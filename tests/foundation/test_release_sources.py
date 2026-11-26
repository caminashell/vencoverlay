"""Tests for release source collection plans and notice extraction."""

import base64
import io
import sys
import tarfile
from pathlib import Path

import pytest

from scripts import release_licences, release_sources


def test_resolve_nuitka_real_prefix(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "base_prefix", str(tmp_path))
    path = tmp_path / "Gtk-3.0.typelib"
    path.write_bytes(b"fixture")
    assert release_licences.resolve_source("${sys.real_prefix}/Gtk-3.0.typelib") == path


def test_exact_rpm_plan() -> None:
    native: dict[str, object] = {
        "report_entries": [{"kind": "data_file", "name": "Gtk", "rpm": "gtk3"}],
        "rpm_packages": {"gtk3": {"rpm_metadata": "LGPL\ngtk3-3.24-1.fc43.src.rpm"}},
    }
    assert release_sources.rpm_sources(native) == {"gtk3-3.24-1.fc43.src.rpm"}
    assert release_sources.rpm_url("gtk3-3.24-1.fc43.src.rpm") == (
        "https://kojipkgs.fedoraproject.org/packages/gtk3/3.24/1.fc43/src/"
        + "gtk3-3.24-1.fc43.src.rpm"
    )
    native["report_entries"] = [
        {"kind": "data_file", "name": "Gtk", "rpm": "unresolved-or-not-rpm"}
    ]
    with pytest.raises(ValueError, match="Unresolved native source owner"):
        release_sources.rpm_sources(native)


def test_exact_python_plan() -> None:
    digest = "a" * 64
    lock = (
        '[[package]]\nname = "pycairo"\nversion = "1.0"\n'
        + 'sdist = { url = "https://files.pythonhosted.org/pycairo-1.0.tar.gz", '
        + f'hash = "sha256:{digest}" }}\n'
    ).encode()
    native: dict[str, object] = {"python_packages": {"pycairo": {"version": "1.0"}}}
    assert release_sources.python_sources(lock, native) == {
        "pycairo-1.0.tar.gz": ("https://files.pythonhosted.org/pycairo-1.0.tar.gz", digest)
    }
    with pytest.raises(ValueError, match="Missing exact locked source"):
        release_sources.python_sources(lock.replace(b'"1.0"', b'"2.0"'), native)


def test_exact_npm_plan() -> None:
    frontend: dict[str, object] = {
        "mapped_npm_packages": {"root": {"name": "@vap/core", "version": "0.0.12"}}
    }
    digest = base64.b64encode(b"a" * 64).decode()
    lock = f"  '@vap/core@0.0.12':\n    resolution: {{integrity: sha512-{digest}}}\n"
    assert release_sources.npm_sources(lock, frontend) == {
        "@vap+core-0.0.12.tgz": ("https://registry.npmjs.org/@vap/core/-/core-0.0.12.tgz", digest)
    }
    with pytest.raises(ValueError, match="Missing exact pnpm integrity"):
        release_sources.npm_sources(lock.replace("0.0.12", "0.0.13"), frontend)


def test_notices_read_without_extracting(tmp_path: Path) -> None:
    path = tmp_path / "source.tar.gz"
    with tarfile.open(path, "w:gz") as archive:
        for name in ("package/LICENSE", "package/src/code.py"):
            info = tarfile.TarInfo(name)
            info.size = 6
            archive.addfile(info, io.BytesIO(b"notice"))
    assert release_sources.archive_notices(path, "notices") == {"notices/LICENSE": b"notice"}
    assert not (tmp_path / "package").exists()


def test_companion_archive_integrity(tmp_path: Path) -> None:
    native = {
        "report_entries": [{"kind": "included_dll", "name": "lib", "rpm": "gtk3"}],
        "rpm_packages": {"gtk3": {"rpm_metadata": "LGPL\ngtk3-3.24-1.fc43.src.rpm"}},
        "python_packages": {"pycairo": {"version": "1.0"}},
    }
    frontend = {"mapped_npm_packages": {"root": {"name": "fflate", "version": "0.8.3"}}}
    identity = {"native_sha256": "native", "frontend_sha256": "frontend"}
    evidence = release_licences.zip_bytes(
        {
            "native/inventory.json": release_sources.json_bytes(native),
            "frontend/inventory.json": release_sources.json_bytes(frontend),
            "inventory.json": release_sources.json_bytes(identity),
        }
    )
    upstream = release_sources.upstream_sources({"fflate-0.8.3.tgz"})
    filename, (url, _) = next(iter(upstream.items()))
    upstream_member = f"upstream/{filename}"
    upstream_notice = f"notices/{upstream_member}/LICENSE"
    payload = {
        "rpm/gtk3-3.24-1.fc43.src.rpm": b"fake rpm",
        "python/pycairo-1.0.tar.gz": b"fake python source",
        "npm/fflate-0.8.3.tgz": b"fake npm tarball",
        upstream_member: b"fake upstream source",
        upstream_notice: b"upstream licence",
        "notices/LICENSE": b"fixture notice",
    }
    downloads: dict[str, dict[str, str | int]] = {
        n: {"sha256": release_sources.sha256(d), "bytes": len(d)}
        for n, d in payload.items()
        if not n.startswith("notices/")
    }
    downloads[upstream_member]["url"] = url
    notices = {n: release_sources.sha256(d) for n, d in payload.items() if n.startswith("notices/")}
    metadata = {
        **identity,
        "review": release_licences.REVIEW,
        "evidence_sha256": release_sources.sha256(evidence),
        "downloads": downloads,
        "notices": notices,
    }
    path = tmp_path / "sources.tar.gz"

    def write() -> None:
        payload["source-manifest.json"] = release_sources.json_bytes(metadata)
        with tarfile.open(path, "w:gz") as archive:
            for name, data in payload.items():
                info = tarfile.TarInfo(name)
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))

    write()
    assert release_sources.verify(path, evidence) == metadata
    with pytest.raises(ValueError, match="Stale source evidence"):
        release_sources.verify(path, evidence + b"changed")
    downloads[upstream_member]["url"] = "https://example.com/wrong"
    write()
    with pytest.raises(ValueError, match="Upstream source URL mismatch"):
        release_sources.verify(path, evidence)
    downloads[upstream_member]["url"] = url
    source = payload.pop(upstream_member)
    record = downloads.pop(upstream_member)
    write()
    with pytest.raises(ValueError, match="Missing exact upstream source trees"):
        release_sources.verify(path, evidence)
    payload[upstream_member] = source
    downloads[upstream_member] = record
    notice = payload.pop(upstream_notice)
    digest = notices.pop(upstream_notice)
    write()
    with pytest.raises(ValueError, match="Missing upstream root notice"):
        release_sources.verify(path, evidence)
    payload[upstream_notice] = notice
    notices[upstream_notice] = digest
    payload["notices/LICENSE"] = b"changed notice"
    write()
    with pytest.raises(ValueError, match="Source notice checksum mismatch"):
        release_sources.verify(path, evidence)


def test_upstream_source_plan() -> None:
    vap = "264a6b4f8f88f3bfaa892de4a8b6c2745976039e"
    fflate = "dcb3714a6c25db3a2748641019c5277413d09714"
    assert release_sources.upstream_sources({"@vap+core-0.0.12.tgz", "fflate-0.8.3.tgz"}) == {
        f"@vap+core-0.0.12-{vap}.tar.gz": (
            f"https://codeload.github.com/Vap0r1ze/vapcord/tar.gz/{vap}",
            f"vapcord-{vap}",
        ),
        f"fflate-0.8.3-{fflate}.tar.gz": (
            f"https://codeload.github.com/101arrowz/fflate/tar.gz/{fflate}",
            f"fflate-{fflate}",
        ),
    }
    assert release_sources.upstream_sources({"gifenc-1.0.3.tgz"}) == {}
    with pytest.raises(ValueError, match="Review upstream source pin"):
        release_sources.upstream_sources({"fflate-0.8.4.tgz"})


def test_upstream_tree_validation(tmp_path: Path) -> None:
    path = tmp_path / "upstream.tar.gz"

    def write(name: str) -> None:
        with tarfile.open(path, "w:gz") as archive:
            member = tarfile.TarInfo(name)
            member.size = 6
            archive.addfile(member, io.BytesIO(b"source"))

    write("fflate-revision/src/index.ts")
    release_sources.verify_upstream_tree(path, "fflate-revision")
    write("fflate-revision/src/index.d.ts")
    with pytest.raises(ValueError, match="Missing upstream TypeScript"):
        release_sources.verify_upstream_tree(path, "fflate-revision")
    write("fflate-other/src/index.ts")
    with pytest.raises(ValueError, match="Unexpected upstream archive root"):
        release_sources.verify_upstream_tree(path, "fflate-revision")
