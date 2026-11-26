"""Assemble and verify a local Fedora x86-64 installation archive."""

import argparse
import io
import os
import platform
import re
import stat
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import zipfile
from pathlib import Path, PurePosixPath

from scripts import native_artifacts, release_licences, release_sources
from scripts.frontend_artifacts import (
    hashes,
    json_bytes,
    object_dict,
    read_json,
    require,
    sha256,
    string,
)
from scripts.frontend_artifacts import (
    verify as verify_frontend,
)

ROOT = Path(__file__).resolve().parents[1]
TARGET = "fedora-x86_64"
TEST_BASELINE = {
    "distribution": "fedora",
    "version": "43",
    "architecture": "x86_64",
    "desktop": "GNOME Wayland",
    "scope": "Prior Discord integration testing, not certification of this archive",
}
NATIVE = "dist/vencoverlay"
FRONTEND = "dist/vencoverlay-vencord.zip"
SOURCE = "source/vencoverlay-source.zip"
MANIFEST = "release-manifest.json"
SUMS = "SHA256SUMS"
SOURCE_INDEX = "licenses/source-asset.json"
SOURCE_NOTICES = "licenses/source-notices.zip"
OPTIONAL = {release_licences.PACKAGE_AUDIT, SOURCE_INDEX, SOURCE_NOTICES}
EXECUTABLES = {"install.sh", "uninstall.sh", "scripts/discord-with-overlay.sh", NATIVE}
INPUTS = EXECUTABLES | {
    FRONTEND,
    native_artifacts.METADATA,
    "scripts/native_artifacts.py",
    "LICENSE",
    "scripts/flatpak_install.py",
    "scripts/frontend_artifacts.py",
}
PAYLOAD = INPUTS | {"README.md", SOURCE}
MAX_MEMBER = 128 * 1024 * 1024
SOURCE_ROOTS = {
    ".gitignore",
    "LICENSE",
    "README.md",
    "preview.png",
    "pyproject.toml",
    "uv.lock",
    "setup.sh",
    "validate.sh",
    "build.sh",
    "release.sh",
    "install.sh",
    "uninstall.sh",
    "snapshot.sh",
    "stats.sh",
    "version.sh",
}
SOURCE_AREAS = {"src", "plugin", "scripts", "tests", "docs"}
SOURCE_SUFFIXES = {
    ".py",
    ".pyi",
    ".ts",
    ".tsx",
    ".js",
    ".mjs",
    ".cjs",
    ".css",
    ".html",
    ".json",
    ".toml",
    ".yaml",
    ".yml",
    ".md",
    ".txt",
    ".sh",
    ".png",
    ".svg",
    ".lock",
}
EXCLUDED = {"build", "dist", "output", "node_modules", "venv", "htmlcov", "secrets", "credentials"}


def read_file(root: Path, name: str) -> bytes:
    path = root / name
    require(
        not any(part.is_symlink() for part in (path, *path.parents)),
        f"Refusing symlink: {path}",
    )
    require(path.is_file(), f"Missing regular file: {path}")
    require(0 < path.stat().st_size <= MAX_MEMBER, f"Invalid file size: {path}")
    return path.read_bytes()


def project_version(data: bytes) -> str:
    version = string(object_dict(object_dict(tomllib.loads(data.decode()))["project"])["version"])
    require(
        re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:\.dev[0-9]+)?", version) is not None,
        "Unsupported release version format",
    )
    return version


def package_name(version: str) -> str:
    return f"vencoverlay-{version}-{TARGET}"


def git(root: Path, *args: str) -> bytes:
    return subprocess.check_output(["git", "-C", str(root), *args], timeout=30)


def source_allowed(name: str) -> bool:
    parts = PurePosixPath(name).parts
    if not parts or name != PurePosixPath(name).as_posix() or name.startswith("/"):
        return False
    if name in SOURCE_ROOTS:
        return True
    if any(p.startswith(".") or p in EXCLUDED or p.endswith(".egg-info") for p in parts):
        return False
    if any(
        word in name.lower()
        for word in ("secret", "credential", "password", "private_key", "token")
    ):
        return False
    if parts[-1].lower() in {
        "config.toml",
        "config.json",
        "settings.toml",
        "settings.json",
        "state.json",
    }:
        return False
    return (
        len(parts) > 1
        and parts[0] in SOURCE_AREAS
        and PurePosixPath(name).suffix in SOURCE_SUFFIXES
    )


def source_archive(root: Path) -> tuple[bytes, str, bool]:
    revision = git(root, "rev-parse", "HEAD").decode().strip()
    require(re.fullmatch(r"[0-9a-f]{40}", revision) is not None, "Invalid Git revision")
    dirty = bool(git(root, "status", "--porcelain", "--untracked-files=normal"))
    raw = git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
    names = sorted({os.fsdecode(p) for p in raw.split(b"\0") if p})
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in names:
            if not source_allowed(name):
                continue
            path = root / name
            if not path.exists() and not path.is_symlink():
                continue  # Tracked file deleted in the working tree.
            data = read_file(root, name)
            info = zipfile.ZipInfo(name)
            info.create_system = 3
            mode = 0o755 if path.stat().st_mode & 0o111 else 0o644
            info.external_attr = (stat.S_IFREG | mode) << 16
            archive.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED)
    return out.getvalue(), revision, dirty


def check_assembly_environment(environment: dict[str, object]) -> None:
    require(
        environment.get("platform") == "linux"
        and environment.get("distribution") == "fedora"
        and environment.get("architecture") == "x86_64",
        f"Package assembly currently targets {TARGET}; use a Fedora x86-64 host",
    )
    version = environment.get("version")
    require(
        isinstance(version, str) and bool(version.strip()) and version == version.strip(),
        "Missing or invalid Fedora version in assembly environment",
    )


def check_host() -> dict[str, object]:
    release = platform.freedesktop_os_release()
    environment: dict[str, object] = {
        "platform": platform.system().lower(),
        "distribution": release.get("ID"),
        "version": release.get("VERSION_ID"),
        "architecture": platform.machine(),
    }
    check_assembly_environment(environment)
    return environment


def check_native_version(path: Path, version: str) -> None:
    require(bool(path.stat().st_mode & 0o111), f"Executable permission missing: {path}")
    result = subprocess.run(
        [str(path), "--version"], capture_output=True, text=True, check=False, timeout=60
    )
    require(
        result.returncode == 0 and result.stdout.strip() == f"VencOverlay {version}",
        f"Native executable version mismatch or startup failure: {result.stderr.strip()}",
    )


def checksum_text(files: dict[str, bytes]) -> bytes:
    return "".join(f"{sha256(data)}  {name}\n" for name, data in sorted(files.items())).encode()


def check_sources(files: dict[str, bytes], version: str, frontend: dict[str, object]) -> None:
    with zipfile.ZipFile(io.BytesIO(files[SOURCE])) as archive:
        names = archive.namelist()
        require(
            len(names) == len(set(names))
            and all(source_allowed(n) for n in names)
            and archive.testzip() is None,
            "Invalid project source archive",
        )
        required = {
            "pyproject.toml",
            "uv.lock",
            "LICENSE",
            "build.sh",
            "release.sh",
            "src/vencoverlay/cli.py",
            "scripts/release_package.py",
            "scripts/prerequisites.sh",
            "scripts/frontend.sh",
            "scripts/frontend-build.mjs",
            "docs/release-installation.md",
            "setup.sh",
            "validate.sh",
            "version.sh",
        } | (INPUTS - {NATIVE, FRONTEND, native_artifacts.METADATA})
        require(required <= set(names), "Incomplete project source archive")
        require(
            project_version(archive.read("pyproject.toml")) == version, "Source version mismatch"
        )
        require(
            archive.read("docs/release-installation.md") == files["README.md"],
            "Installation guide changed during assembly",
        )
        for name in INPUTS - {NATIVE, FRONTEND, native_artifacts.METADATA}:
            require(
                archive.read(name) == files[name], f"Packaged installer/source mismatch: {name}"
            )
        native = native_artifacts.check_metadata(
            files[native_artifacts.METADATA], files[NATIVE], version
        )
        require(
            native["inputs"]
            == native_artifacts.input_hashes({name: archive.read(name) for name in names}),
            "Native executable built from different source inputs; rebuild with ./build.sh",
        )
        for name, digest in object_dict(frontend["plugin_sha256"]).items():
            require(
                sha256(archive.read(f"plugin/vencoverlay/{name}")) == digest,
                f"Frontend built from different plugin source: {name}",
            )


def verify_payload(files: dict[str, bytes], prefix: str) -> None:
    payload = set(PAYLOAD)
    payload.update(OPTIONAL & set(files))
    if SOURCE_INDEX in files or SOURCE_NOTICES in files:
        require(OPTIONAL <= set(files), "Incomplete companion source evidence")
        index = read_json(files[SOURCE_INDEX])
        require(
            index.get("evidence_sha256") == sha256(files[release_licences.PACKAGE_AUDIT])
            and index.get("notices_sha256") == sha256(files[SOURCE_NOTICES])
            and index.get("review") == release_licences.REVIEW,
            "Companion source evidence mismatch",
        )
        release_licences.zip_files(files[SOURCE_NOTICES])
    require(set(files) == payload | {MANIFEST, SUMS}, "Unexpected release file inventory")
    require(
        files[SUMS] == checksum_text({n: d for n, d in files.items() if n != SUMS}),
        "Release checksum mismatch",
    )
    manifest = read_json(files[MANIFEST])
    version = string(manifest["version"])
    require(
        re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:\.dev[0-9]+)?", version) is not None,
        "Invalid release version",
    )
    require(
        manifest["schema"] == 2
        and manifest["product"] == "VencOverlay"
        and manifest["target"] == TARGET
        and prefix == package_name(version)
        and manifest["client"] == "discord-flatpak-vencord-adoption",
        "Invalid release identity",
    )
    require(
        re.fullmatch(r"[0-9a-f]{40}", string(manifest["source_revision"])) is not None
        and isinstance(manifest["source_dirty"], bool),
        "Invalid source metadata",
    )
    check_assembly_environment(object_dict(manifest["assembly_environment"]))
    require(manifest["test_baseline"] == TEST_BASELINE, "Invalid runtime test baseline")
    require(
        manifest["files"] == hashes({n: files[n] for n in payload}),
        "Release manifest checksum mismatch",
    )
    native = native_artifacts.check_metadata(
        files[native_artifacts.METADATA], files[NATIVE], version
    )
    require(
        manifest["native_build"] == native["build_environment"], "Native build environment mismatch"
    )
    with tempfile.TemporaryDirectory(prefix="vencoverlay-release-verify-") as tmp:
        path = Path(tmp) / "vencoverlay-vencord.zip"
        path.write_bytes(files[FRONTEND])
        verify_frontend(path)
    with zipfile.ZipFile(io.BytesIO(files[FRONTEND])) as archive:
        frontend = read_json(archive.read("manifest.json"))
    require(
        frontend["version"] == version
        and frontend["vencord_version"] == manifest["vencord_version"]
        and frontend["vencord_revision"] == manifest["vencord_revision"],
        "Frontend version mismatch",
    )
    check_sources(files, version, frontend)
    if release_licences.PACKAGE_AUDIT in files:
        require(
            manifest.get("licence_review") == release_licences.REVIEW,
            "Invalid licence review status",
        )
        release_licences.verify_evidence(
            files[release_licences.PACKAGE_AUDIT], files[NATIVE], files[FRONTEND]
        )
    else:
        require(
            manifest.get("licence_review") in {None, "not-collected"}, "Missing licence evidence"
        )


def verify_package(path: Path) -> None:
    files: dict[str, bytes] = {}
    prefix = ""
    with tarfile.open(path, "r:gz") as archive:
        for member in archive:
            parts = member.name.split("/")
            require(
                len(parts) >= 2 and all(p not in {"", ".", ".."} for p in parts),
                "Unsafe archive path",
            )
            if not prefix:
                prefix = parts[0]
            name = "/".join(parts[1:])
            require(
                parts[0] == prefix
                and name in PAYLOAD | {MANIFEST, SUMS} | OPTIONAL
                and name not in files
                and member.isfile(),
                "Unexpected or unsafe archive member",
            )
            require(
                member.mode == (0o755 if name in EXECUTABLES else 0o644),
                f"Unexpected archive permissions: {name}",
            )
            require(0 < member.size <= MAX_MEMBER, f"Invalid archive member size: {name}")
            stream = archive.extractfile(member)
            assert stream is not None
            with stream:
                files[name] = stream.read()
            require(len(files[name]) == member.size, f"Truncated archive member: {name}")
    verify_payload(files, prefix)
    print(f"Verified installation package: {path} ({len(files)} files)")
    print(f"SHA-256: {sha256(path.read_bytes())}")


def package(
    root: Path, *, replace: bool = False, licences: bool = False, sources: bool = False
) -> Path:
    environment = check_host()
    version = project_version(read_file(root, "pyproject.toml"))
    files = {name: read_file(root, name) for name in INPUTS}
    native = native_artifacts.check_metadata(
        files[native_artifacts.METADATA], files[NATIVE], version
    )
    check_native_version(root / NATIVE, version)
    require(read_file(root, NATIVE) == files[NATIVE], "Executable changed during verification")
    for name in EXECUTABLES:
        require(
            bool((root / name).stat().st_mode & 0o111), f"Missing executable permission: {name}"
        )
    files["README.md"] = read_file(root, "docs/release-installation.md")
    files[SOURCE], revision, dirty = source_archive(root)
    with zipfile.ZipFile(io.BytesIO(files[FRONTEND])) as archive:
        frontend = read_json(archive.read("manifest.json"))
    if licences:
        evidence = read_file(root, release_licences.AUDIT)
        release_licences.verify_evidence(evidence, files[NATIVE], files[FRONTEND])
        files[release_licences.PACKAGE_AUDIT] = evidence
    if sources:
        require(licences, "Use --licences with --sources")
        index_data = read_file(root, release_sources.INDEX)
        index = read_json(index_data)
        name = f"vencoverlay-{version}-third-party-source.tar.gz"
        require(index.get("filename") == name, "Companion source version mismatch")
        asset = root / "dist/releases" / name
        require(
            not asset.is_symlink()
            and asset.is_file()
            and release_sources.file_hash(asset) == index.get("sha256")
            and asset.stat().st_size == index.get("bytes"),
            "Missing or changed companion source asset",
        )
        source_manifest = release_sources.verify(asset, files[release_licences.PACKAGE_AUDIT])
        require(source_manifest.get("version") == version, "Source manifest version mismatch")
        files[SOURCE_INDEX] = index_data
        files[SOURCE_NOTICES] = read_file(root, release_sources.NOTICES)
        require(
            source_manifest["notices"]
            == {
                n: sha256(data)
                for n, data in release_licences.zip_files(files[SOURCE_NOTICES]).items()
            },
            "Source notice archive differs from companion source asset",
        )
    manifest = {
        "schema": 2,
        "licence_review": release_licences.REVIEW if licences else "not-collected",
        "native_build": native["build_environment"],
        "product": "VencOverlay",
        "version": version,
        "target": TARGET,
        "assembly_environment": environment,
        "test_baseline": TEST_BASELINE,
        "client": "discord-flatpak-vencord-adoption",
        "source_revision": revision,
        "source_dirty": dirty,
        "vencord_version": frontend["vencord_version"],
        "vencord_revision": frontend["vencord_revision"],
        "files": hashes(files),
    }
    files[MANIFEST] = json_bytes(manifest)
    files[SUMS] = checksum_text(files)
    prefix = package_name(version)
    output = root / "dist/releases"
    require(not output.is_symlink() and not output.parent.is_symlink(), "Unsafe release directory")
    output.mkdir(exist_ok=True)
    final = output / f"{prefix}.tar.gz"
    require(not final.is_symlink(), "Refusing symlinked release output")
    require(
        replace or not final.exists(),
        f"Release already exists: {final}; use --replace for a local rebuild",
    )
    with tempfile.TemporaryDirectory(prefix=".release-", dir=output) as tmp:
        staged = Path(tmp) / final.name
        with tarfile.open(staged, "w:gz", format=tarfile.USTAR_FORMAT) as archive:
            for name, data in sorted(files.items()):
                info = tarfile.TarInfo(f"{prefix}/{name}")
                info.mode = 0o755 if name in EXECUTABLES else 0o644
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))
        verify_package(staged)
        if replace:
            os.replace(staged, final)
        else:
            os.link(staged, final)
    print(f"Package: {final}")
    print(f"Assembly host: Fedora {environment['version']} x86-64")
    print("Runtime test baseline: Fedora 43 GNOME Wayland; other versions unverified.")
    if dirty:
        print("Working tree was not clean; this package is for local testing, not publication.")
    print("No upload performed. Live target compatibility and publication review remain required.")
    print("Licence review: pending; do not publish this package.")
    return final


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--verify", type=Path, metavar="ARCHIVE", help="Verify without extracting or executing"
    )
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Replace an existing local package after verification",
    )
    parser.add_argument(
        "--licences",
        action="store_true",
        help="Include current candidate third-party licence evidence (local testing only)",
    )
    parser.add_argument("--sources", action="store_true", help="Bind companion source asset")
    args = object_dict(vars(parser.parse_args()))
    path = args["verify"]
    replace = bool(args["replace"])
    licences = bool(args["licences"])
    sources = bool(args["sources"])
    if path is not None and (replace or licences or sources):
        parser.error("--replace and --licences are only for package assembly")
    try:
        if path is None:
            package(ROOT, replace=replace, licences=licences, sources=sources)
        else:
            assert isinstance(path, Path)
            verify_package(path)
    except (
        ValueError,
        OSError,
        KeyError,
        RuntimeError,
        subprocess.SubprocessError,
        tarfile.TarError,
        zipfile.BadZipFile,
    ) as error:
        print(f"VencOverlay release: {error}", file=sys.stderr)
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
