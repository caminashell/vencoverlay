"""Collect exact third-party source candidates; never authorise publication."""

import argparse
import base64
import hashlib
import io
import os
import re
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import zipfile
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

from scripts import release_licences as licences
from scripts.frontend_artifacts import (
    json_bytes,
    object_dict,
    object_list,
    read_json,
    require,
    sha256,
    string,
)

ROOT = Path(__file__).resolve().parents[1]
INDEX = "build/source-asset.json"
NOTICES = "build/source-notices.zip"
MAX_FILE = 512 * 1024 * 1024
HOSTS = {
    "kojipkgs.fedoraproject.org",
    "files.pythonhosted.org",
    "registry.npmjs.org",
    "codeload.github.com",
}


def download(url: str, destination: Path) -> None:
    require(
        urlsplit(url).scheme == "https" and urlsplit(url).hostname in HOSTS,
        "Unsupported source download URL",
    )
    subprocess.run(
        [
            "curl",
            "--disable",
            "--globoff",
            "--fail",
            "--silent",
            "--show-error",
            "--proto",
            "=https",
            "--max-time",
            "600",
            "--max-filesize",
            str(MAX_FILE),
            "--output",
            str(destination),
            url,
        ],
        check=True,
        timeout=610,
    )
    require(0 < destination.stat().st_size <= MAX_FILE, "Invalid source download size")


def file_hash(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def rpm_sources(native: dict[str, object]) -> set[str]:
    packages = object_dict(native["rpm_packages"])
    for raw in object_list(native["report_entries"]):
        entry = object_dict(raw)
        if entry["kind"] != "module":
            require(
                string(entry["rpm"]) in packages,
                f"Unresolved native source owner: {entry['name']}; rebuild/audit first",
            )
    result: set[str] = set()
    for raw in packages.values():
        details = string(object_dict(raw)["rpm_metadata"]).splitlines()
        require(len(details) >= 2, "Missing source RPM metadata")
        name = licences.safe_name(details[-1])
        require(name.endswith(".src.rpm"), "Invalid source RPM name")
        result.add(name)
    require(bool(result), "No source RPMs in inventory")
    return result


def rpm_url(filename: str) -> str:
    licences.safe_name(filename)
    require(filename.endswith(".src.rpm"), "Invalid source RPM")
    name, version, release = filename.removesuffix(".src.rpm").rsplit("-", 2)
    return f"https://kojipkgs.fedoraproject.org/packages/{name}/{version}/{release}/src/{filename}"


def verify_rpm(path: Path) -> None:
    # Koji build RPMs can be unsigned. Check payload digests and exact header identity.
    subprocess.run(["rpm", "-K", "--nosignature", str(path)], check=True, timeout=120)
    identity = subprocess.check_output(
        ["rpm", "-qp", "--qf", "%{NAME}-%{VERSION}-%{RELEASE}.src.rpm", str(path)],
        text=True,
        timeout=30,
    ).strip()
    require(identity == path.name, f"Source RPM identity mismatch: {path.name}")
    source = subprocess.check_output(
        ["rpm", "-qp", "--qf", "%{SOURCEPACKAGE}", str(path)],
        text=True,
        timeout=30,
    ).strip()
    require(source == "1", f"Not a source RPM: {path.name}")


def python_sources(lock: bytes, native: dict[str, object]) -> dict[str, tuple[str, str]]:
    entries = object_list(object_dict(tomllib.loads(lock.decode()))["package"])
    result: dict[str, tuple[str, str]] = {}
    for name, raw in object_dict(native["python_packages"]).items():
        version = string(object_dict(raw)["version"])
        matches = [
            object_dict(item)
            for item in entries
            if object_dict(item).get("name") == name.lower()
            and object_dict(item).get("version") == version
        ]
        require(len(matches) == 1, f"Missing exact locked source: {name} {version}")
        sdist = object_dict(matches[0]["sdist"])
        url, digest = string(sdist["url"]), string(sdist["hash"])
        require(bool(re.fullmatch(r"sha256:[a-f0-9]{64}", digest)), "Invalid locked hash")
        filename = licences.safe_name(PurePosixPath(urlsplit(url).path).name)
        result[filename] = (url, digest.removeprefix("sha256:"))
    return result


def npm_sources(lock: str, frontend: dict[str, object]) -> dict[str, tuple[str, str]]:
    result: dict[str, tuple[str, str]] = {}
    for root, raw in object_dict(frontend["mapped_npm_packages"]).items():
        info = object_dict(raw)
        name, version = string(info["name"]), string(info["version"])
        key = f"{name}@{version}"
        if name == "gifenc" and "gifenc@https+++codeload.github.com+" in root:
            match = re.search(r"\+([a-f0-9]{40})/node_modules/gifenc$", root)
            require(match is not None, "Missing gifenc Git revision")
            assert match is not None
            url = f"https://codeload.github.com/mattdesl/gifenc/tar.gz/{match[1]}"
            key = f"gifenc@{url}"
        else:
            url = f"https://registry.npmjs.org/{name}/-/{name.split('/')[-1]}-{version}.tgz"
        match = re.search(
            r"(?m)^  ['\"]?"
            + re.escape(key)
            + r"['\"]?:\n"
            + r"    resolution: \{[^\n]*?integrity: sha512-([A-Za-z0-9+/=]+)",
            lock,
        )
        require(match is not None, f"Missing exact pnpm integrity: {key}")
        assert match is not None
        require(len(base64.b64decode(match[1], validate=True)) == 64, "Invalid pnpm SHA-512")
        filename = licences.safe_name(name.replace("/", "+") + "-" + version + ".tgz")
        result[filename] = (url, match[1])
    return result


# Additional preferred source trees for the reviewed frontend package versions.
# Keep the lockfile-verified npm packages as well. A version change needs review.
UPSTREAM_PINS = {
    "@vap+core-0.0.12.tgz": (
        "Vap0r1ze/vapcord",
        "264a6b4f8f88f3bfaa892de4a8b6c2745976039e",
    ),
    "fflate-0.8.3.tgz": (
        "101arrowz/fflate",
        "dcb3714a6c25db3a2748641019c5277413d09714",
    ),
}


def upstream_sources(npm: set[str]) -> dict[str, tuple[str, str]]:
    result: dict[str, tuple[str, str]] = {}
    for package, (repository, revision) in UPSTREAM_PINS.items():
        prefix = package.rsplit("-", 1)[0] + "-"
        matching = {name for name in npm if name.startswith(prefix)}
        if not matching:
            continue
        require(matching == {package}, f"Review upstream source pin: {prefix}")
        root = repository.split("/")[-1] + "-" + revision
        filename = package.removesuffix(".tgz") + "-" + revision + ".tar.gz"
        url = f"https://codeload.github.com/{repository}/tar.gz/{revision}"
        result[filename] = (url, root)
    return result


def verify_upstream_tree(path: Path, root: str) -> None:
    """Check the pinned archive layout and presence of editable TypeScript."""
    has_source = False
    with tarfile.open(path, "r:gz") as archive:
        for member in archive:
            parts = PurePosixPath(member.name).parts
            require(
                bool(parts) and parts[0] == root and ".." not in parts,
                f"Unexpected upstream archive root: {path.name}",
            )
            if member.isfile() and member.size > 0:
                name = parts[-1]
                if name.endswith(".ts") and not name.endswith(".d.ts"):
                    has_source = True
    require(has_source, f"Missing upstream TypeScript source: {path.name}")


def archive_notices(path: Path, prefix: str) -> dict[str, bytes]:
    """Read notices without extracting or running third-party code."""
    files: dict[str, bytes] = {}
    with tarfile.open(path, "r:*") as archive:
        for member in archive:
            name = PurePosixPath(member.name)
            if not member.isfile() or len(name.parts) != 2 or not licences.notice_name(name.name):
                continue
            require(0 < member.size <= 1024 * 1024, "Invalid notice size")
            stream = archive.extractfile(member)
            assert stream is not None
            with stream:
                data = stream.read()
            target = prefix + "/" + licences.safe_name(name.name)
            require(target not in files, "Duplicate source notice")
            files[target] = data
    return files


# Supplemental notices reviewed against the pinned Vencord source and npm metadata.
# A future upstream pin or @vap/core version requires a fresh notice review.
REVIEWED_VENCORD = "d086992fb674299a2fa05ac6432edc3a777389f3"
SUPPLEMENTAL_NOTICES = {
    "notices/npm/@vap+core-0.0.12.tgz/LICENSE": "vap-core-0.0.12-MIT.txt",
    "notices/vendored/discord-intl-MIT.txt": "discord-intl-MIT.txt",
    "notices/vendored/apng-canvas-MIT.txt": "apng-canvas-MIT.txt",
    "notices/vendored/crxToZip-MPL-2.0.txt": "crxToZip-MPL-2.0.txt",
    "notices/THIRD-PARTY-SOURCE.md": "third-party-source.md",
}


def supplemental_notices(
    root: Path, revision: str, npm: dict[str, tuple[str, str]]
) -> dict[str, bytes]:
    require(revision == REVIEWED_VENCORD, "Review supplemental notices for this Vencord pin")
    require("@vap+core-0.0.12.tgz" in npm, "Review the @vap/core notice for this npm inventory")
    result: dict[str, bytes] = {}
    for member, filename in SUPPLEMENTAL_NOTICES.items():
        path = root / "docs/licences" / filename
        require(path.is_file() and not path.is_symlink(), f"Missing reviewed notice: {filename}")
        data = path.read_bytes()
        require(0 < len(data) <= 1024 * 1024, f"Invalid reviewed notice: {filename}")
        result[member] = data
    return result


def collect(root: Path) -> Path:
    evidence = (root / licences.AUDIT).read_bytes()
    binary = (root / "dist/vencoverlay").read_bytes()
    frontend_bytes = (root / "dist/vencoverlay-vencord.zip").read_bytes()
    licences.verify_evidence(evidence, binary, frontend_bytes)
    files = licences.zip_files(evidence)
    native = read_json(files["native/inventory.json"])
    frontend = read_json(files["frontend/inventory.json"])
    rpms = rpm_sources(native)
    python = python_sources((root / "uv.lock").read_bytes(), native)
    with zipfile.ZipFile(io.BytesIO(frontend_bytes)) as archive:
        manifest = read_json(archive.read("manifest.json"))
        version = string(manifest["version"])
        revision = string(manifest["vencord_revision"])
        with zipfile.ZipFile(io.BytesIO(archive.read("source/vencord-source.zip"))) as source:
            npm = npm_sources(source.read("pnpm-lock.yaml").decode(), frontend)
            # Full Apache text already carried by the pinned upstream source.
            apache = source.read("src/api/DataStore/LICENSE")
    supplemental = supplemental_notices(root, revision, npm)
    name = f"vencoverlay-{licences.safe_name(version)}-third-party-source.tar.gz"
    output = root / "dist/releases"
    require(not output.is_symlink() and not output.parent.is_symlink(), "Unsafe output directory")
    output.mkdir(parents=True, exist_ok=True)
    final = output / name
    require(not final.is_symlink(), "Unsafe source output")
    with tempfile.TemporaryDirectory(dir=output, prefix=".sources-") as tmp:
        stage = Path(tmp)
        downloads: dict[str, Path] = {}
        origins: dict[str, str] = {}
        notices: dict[str, bytes] = {"notices/vendored/Apache-2.0.txt": apache}
        for filename in sorted(rpms):
            path = stage / filename
            url = rpm_url(filename)
            print(f"Collecting {filename}", flush=True)
            download(url, path)
            verify_rpm(path)
            downloads[f"rpm/{filename}"] = path
            origins[f"rpm/{filename}"] = url
        for group, plan in (("python", python), ("npm", npm)):
            for filename, (url, expected) in sorted(plan.items()):
                path = stage / filename
                print(f"Collecting {filename}", flush=True)
                download(url, path)
                if group == "python":
                    actual = file_hash(path)
                else:
                    with path.open("rb") as stream:
                        digest = hashlib.file_digest(stream, "sha512").digest()
                        actual = base64.b64encode(digest).decode()
                require(actual == expected, f"Locked source checksum mismatch: {filename}")
                member = f"{group}/{filename}"
                downloads[member] = path
                origins[member] = url
                notices.update(archive_notices(path, f"notices/{group}/{filename}"))
        for filename, (url, archive_root) in sorted(upstream_sources(set(npm)).items()):
            path = stage / filename
            print(f"Collecting {filename}", flush=True)
            download(url, path)
            verify_upstream_tree(path, archive_root)
            member = f"upstream/{filename}"
            downloads[member] = path
            origins[member] = url
            original = archive_notices(path, f"notices/{member}")
            require(bool(original), f"Missing upstream root notice: {filename}")
            notices.update(original)
        # Retain original archive notices if present; never overwrite different text.
        for member, data in supplemental.items():
            require(
                member not in notices or notices[member] == data,
                f"Conflicting reviewed notice: {member}",
            )
            notices[member] = data
        # Candidate evidence remains unchanged; source notices include reviewed additions.
        missing_notices = sorted(
            member
            for member in downloads
            if member.startswith(("python/", "npm/", "upstream/"))
            and not any(n.startswith(f"notices/{member}/") for n in notices)
        )
        records = {
            n: {"sha256": file_hash(p), "bytes": p.stat().st_size, "url": origins[n]}
            for n, p in downloads.items()
        }
        metadata = {
            "version": version,
            "review": licences.REVIEW,
            "evidence_sha256": sha256(evidence),
            "native_sha256": sha256(binary),
            "frontend_sha256": sha256(frontend_bytes),
            "downloads": records,
            "notices": {n: sha256(d) for n, d in notices.items()},
            "missing_source_notices": missing_notices,
            "limitations": (
                "Review the preferred source for modification, any remaining notices, "
                + "Nuitka modifications and LGPL rebuild/relink requirements."
            ),
        }
        candidate = stage / name
        with tarfile.open(candidate, "w:gz") as archive:
            for member, path in sorted(downloads.items()):
                archive.add(path, arcname=member, recursive=False)
            additions = {**notices, "source-manifest.json": json_bytes(metadata)}
            for member, data in sorted(additions.items()):
                entry = tarfile.TarInfo(member)
                entry.mode = 0o644
                entry.size = len(data)
                archive.addfile(entry, io.BytesIO(data))
        verify(candidate, evidence)
        os.replace(candidate, final)
    notice_data = licences.zip_bytes(notices)
    licences.write_atomic(root / NOTICES, notice_data)
    licences.write_atomic(
        root / INDEX,
        json_bytes(
            {
                "filename": name,
                "sha256": file_hash(final),
                "bytes": final.stat().st_size,
                "evidence_sha256": sha256(evidence),
                "review": licences.REVIEW,
                "notices_sha256": sha256(notice_data),
            }
        ),
    )
    print(f"Source asset: {final}")
    print(f"Downloaded sources without root notices: {missing_notices}")
    print("Human review remains required. Do not publish yet.")
    return final


def verify(path: Path, evidence: bytes) -> dict[str, object]:
    records: dict[str, str] = {}
    sizes: dict[str, int] = {}
    manifest: dict[str, object] | None = None
    with tarfile.open(path, "r:gz") as archive:
        for member in archive:
            parts = member.name.split("/")
            require(
                member.isfile()
                and all(p not in {"", ".", ".."} for p in parts)
                and member.name not in records
                and 0 < member.size <= MAX_FILE,
                "Unsafe source archive member",
            )
            stream = archive.extractfile(member)
            assert stream is not None
            with stream:
                if member.name == "source-manifest.json":
                    require(member.size <= 1024 * 1024, "Invalid source manifest size")
                    data = stream.read()
                    manifest = read_json(data)
                    records[member.name] = sha256(data)
                else:
                    digest = hashlib.sha256()
                    while chunk := stream.read(1024 * 1024):
                        digest.update(chunk)
                    records[member.name] = digest.hexdigest()
                sizes[member.name] = member.size
    require(manifest is not None, "Missing source manifest")
    assert manifest is not None
    evidence_manifest = read_json(licences.zip_files(evidence)["inventory.json"])
    require(
        manifest["review"] == licences.REVIEW
        and manifest["evidence_sha256"] == sha256(evidence)
        and manifest["native_sha256"] == evidence_manifest["native_sha256"]
        and manifest["frontend_sha256"] == evidence_manifest["frontend_sha256"],
        "Stale source evidence",
    )
    downloads = object_dict(manifest["downloads"])
    notices = object_dict(manifest["notices"])
    require(
        set(records) == set(downloads) | set(notices) | {"source-manifest.json"}
        and all(n.startswith(("rpm/", "python/", "npm/", "upstream/")) for n in downloads)
        and all(n.startswith("notices/") for n in notices),
        "Source asset inventory mismatch",
    )
    native = read_json(licences.zip_files(evidence)["native/inventory.json"])
    require(
        {f"rpm/{n}" for n in rpm_sources(native)} == {n for n in downloads if n.startswith("rpm/")},
        "Missing exact source RPMs",
    )
    expected_python = {
        f"python/{name.lower()}-{string(object_dict(raw)['version'])}.tar.gz"
        for name, raw in object_dict(native["python_packages"]).items()
    }
    frontend = read_json(licences.zip_files(evidence)["frontend/inventory.json"])
    expected_npm: set[str] = set()
    for raw in object_dict(frontend["mapped_npm_packages"]).values():
        info = object_dict(raw)
        name = string(info["name"]).replace("/", "+")
        version = string(info["version"])
        expected_npm.add(f"npm/{name}-{version}.tgz")
    require(
        expected_python == {n for n in downloads if n.startswith("python/")}
        and expected_npm == {n for n in downloads if n.startswith("npm/")},
        "Missing exact Python or npm sources",
    )
    upstream = upstream_sources({name.removeprefix("npm/") for name in expected_npm})
    require(
        {f"upstream/{name}" for name in upstream}
        == {name for name in downloads if name.startswith("upstream/")},
        "Missing exact upstream source trees",
    )
    for filename, (url, _) in upstream.items():
        member = f"upstream/{filename}"
        require(object_dict(downloads[member]).get("url") == url, "Upstream source URL mismatch")
        require(
            any(name.startswith(f"notices/{member}/") for name in notices),
            f"Missing upstream root notice: {filename}",
        )
    for member, raw in downloads.items():
        record = object_dict(raw)
        require(
            records[member] == record["sha256"] and sizes[member] == record["bytes"],
            f"Source asset checksum mismatch: {member}",
        )
    for member, digest in notices.items():
        require(records[member] == digest, f"Source notice checksum mismatch: {member}")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", type=Path)
    args = parser.parse_args()
    path: object = args.verify
    try:
        if path is None:
            collect(ROOT)
        else:
            assert isinstance(path, Path)
            verify(path, (ROOT / licences.AUDIT).read_bytes())
            print(f"Verified source asset: {path}")
    except (
        ValueError,
        OSError,
        KeyError,
        subprocess.SubprocessError,
        tarfile.TarError,
        zipfile.BadZipFile,
    ) as error:
        print(f"VencOverlay source collection: {error}", file=sys.stderr)
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
