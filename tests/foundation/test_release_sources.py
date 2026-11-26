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
    frontend = {"mapped_npm_packages": {"root": {"name": "test", "version": "1.0"}}}
    identity = {"native_sha256": "native", "frontend_sha256": "frontend"}
    evidence = release_licences.zip_bytes(
        {
            "native/inventory.json": release_sources.json_bytes(native),
            "frontend/inventory.json": release_sources.json_bytes(frontend),
            "inventory.json": release_sources.json_bytes(identity),
        }
    )
    payload = {
        "rpm/gtk3-3.24-1.fc43.src.rpm": b"fake rpm",
        "python/pycairo-1.0.tar.gz": b"fake python source",
        "npm/test-1.0.tgz": b"fake npm tarball",
        "notices/LICENSE": b"fixture notice",
    }
    metadata = {
        **identity,
        "review": release_licences.REVIEW,
        "evidence_sha256": release_sources.sha256(evidence),
        "downloads": {
            n: {"sha256": release_sources.sha256(d), "bytes": len(d)}
            for n, d in payload.items()
            if not n.startswith("notices/")
        },
        "notices": {"notices/LICENSE": release_sources.sha256(payload["notices/LICENSE"])},
    }
    payload["source-manifest.json"] = release_sources.json_bytes(metadata)
    path = tmp_path / "sources.tar.gz"

    def write() -> None:
        with tarfile.open(path, "w:gz") as archive:
            for name, data in payload.items():
                info = tarfile.TarInfo(name)
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))

    write()
    assert release_sources.verify(path, evidence) == metadata
    with pytest.raises(ValueError, match="Stale source evidence"):
        release_sources.verify(path, evidence + b"changed")
    payload["notices/LICENSE"] = b"changed notice"
    write()
    with pytest.raises(ValueError, match="Source notice checksum mismatch"):
        release_sources.verify(path, evidence)
