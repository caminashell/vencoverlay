"""Collect build-specific licence evidence; human source/notice review is still required."""

import io
import os
import re
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import zipfile
from importlib.metadata import distribution
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree

from scripts.frontend_artifacts import (
    hashes,
    json_bytes,
    object_list,
    read_archive,
    read_json,
    require,
    sha256,
    string,
    verify,
)

NATIVE_EVIDENCE = "build/native-licences.zip"
AUDIT = "build/release-licences.zip"
PACKAGE_AUDIT = "licenses/third-party-evidence.zip"
REVIEW = "pending-human-review"


def notice_name(name: str) -> bool:
    lower = name.lower()
    return lower.startswith(("license", "licence", "copying", "copyright", "notice")) and not (
        lower.endswith((".py", ".pyc", ".j2"))
    )


def safe_name(name: str) -> str:
    require(bool(re.fullmatch(r"[A-Za-z0-9_.+@^-]+", name)), f"Unsafe component name: {name}")
    require(name not in {".", ".."}, "Unsafe component name")
    return name


def add_notice(files: dict[str, bytes], prefix: str, path: Path) -> None:
    if path.is_file() and path.stat().st_size:
        filename = re.sub(r"[^A-Za-z0-9_.+@^-]", "_", path.name)
        name = f"{prefix}/{safe_name(filename)}"
        data = path.read_bytes()
        if name in files and files[name] != data:
            name = f"{prefix}/{sha256(data)[:12]}-{safe_name(filename)}"
        files[name] = data


def zip_bytes(files: dict[str, bytes]) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(files.items()):
            archive.writestr(name, data)
    return out.getvalue()


def zip_files(data: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return read_archive(archive)


def write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=path.parent) as tmp:
        candidate = Path(tmp) / path.name
        candidate.write_bytes(data)
        os.replace(candidate, path)


def rpm(*args: str) -> str | None:
    if shutil.which("rpm") is None:
        return None
    result = subprocess.run(["rpm", *args], capture_output=True, text=True, check=False, timeout=30)
    return result.stdout.strip() if result.returncode == 0 else None


def report_entries(report: Path) -> list[tuple[str, str, str]]:
    """Retain included entries, not ignored DLL candidates or attempted imports."""
    entries: list[tuple[str, str, str]] = []
    for node in ElementTree.parse(report).iter():
        if node.tag not in {"included_dll", "data_file", "module"}:
            continue
        if node.get("ignored", "no") == "yes":
            continue
        name = node.get("dest_path") or node.get("name") or "(unnamed)"
        source = node.get("source_path") or node.get("source") or node.get("filename") or ""
        entries.append((node.tag, name, source))
    require(any(kind == "module" for kind, _, _ in entries), "No compiled modules in report")
    require(any(kind == "included_dll" for kind, _, _ in entries), "No native DLLs in report")
    return entries


def resolve_source(source: str) -> Path | None:
    for key, value in (
        ("sys.prefix", sys.prefix),
        ("sys.base_prefix", sys.base_prefix),
        ("sys.real_prefix", sys.base_prefix),
    ):
        source = source.replace("${" + key + "}", value)
    path = Path(source)
    return path.resolve() if source and path.is_absolute() and path.is_file() else None


def collect_native(report: Path, binary: Path, output: Path) -> None:
    files: dict[str, bytes] = {}
    python_packages: dict[str, object] = {}
    for name in ("Nuitka", "PyGObject", "pycairo"):
        package = distribution(name)
        prefix = f"native/python/{name}"
        for entry in package.files or ():
            if notice_name(entry.name):
                located = package.locate_file(entry)
                if isinstance(located, Path):
                    add_notice(files, prefix, located)
        python_packages[name] = {
            "version": package.version,
            "licence_expression": package.metadata.get("License-Expression"),
            "declared_licence": package.metadata.get("License"),
            "licence_files": sorted(n for n in files if n.startswith(prefix + "/")),
        }
    entries: list[dict[str, str]] = []
    owners: dict[str, str | None] = {}
    packages: dict[str, object] = {}
    for kind, name, source in report_entries(report):
        owner = None
        path = resolve_source(source)
        if path is not None and kind != "module":
            key = str(path)
            if key not in owners:
                owners[key] = rpm("-qf", "--qf", "%{NAME}-%{VERSION}-%{RELEASE}.%{ARCH}", key)
            owner = owners[key]
        # Never expose absolute build paths in a redistributed evidence bundle.
        entries.append(
            {
                "kind": kind,
                "name": PurePosixPath(name).name if name.startswith("/") else name,
                "rpm": owner
                or ("not-queried-module" if kind == "module" else "unresolved-or-not-rpm"),
            }
        )
        if owner and owner not in packages:
            safe_name(owner)
            details = rpm("-q", "--qf", "%{LICENSE}\n%{SOURCERPM}", owner)
            paths = rpm("-q", "--licensefiles", owner)
            prefix = f"native/rpm/{owner}"
            for licence in (paths or "").splitlines():
                add_notice(files, prefix, Path(licence))
            packages[owner] = {
                "rpm_metadata": details,
                "licence_files": sorted(n for n in files if n.startswith(prefix + "/")),
            }
    # CPython includes compiled standard-library modules even when no module is an RPM DLL.
    # Record interpreter and stdlib owners separately from copied library owners.
    interpreter_path = (
        Path(sys.base_prefix) / "bin" / (f"python{sys.version_info.major}.{sys.version_info.minor}")
    )
    interpreter = rpm("-qf", "--qf", "%{NAME}-%{VERSION}-%{RELEASE}.%{ARCH}", str(interpreter_path))
    stdlib = rpm(
        "-qf",
        "--qf",
        "%{NAME}-%{VERSION}-%{RELEASE}.%{ARCH}",
        str(Path(sysconfig.get_path("stdlib")) / "os.py"),
    )
    for owner in sorted({item for item in (interpreter, stdlib) if item is not None}):
        if owner in packages:
            continue
        safe_name(owner)
        prefix = f"native/rpm/{owner}"
        for licence in (rpm("-q", "--licensefiles", owner) or "").splitlines():
            add_notice(files, prefix, Path(licence))
        packages[owner] = {
            "rpm_metadata": rpm("-q", "--qf", "%{LICENSE}\n%{SOURCERPM}", owner),
            "licence_files": sorted(n for n in files if n.startswith(prefix + "/")),
        }
    files["native/inventory.json"] = json_bytes(
        {
            "executable_sha256": sha256(binary.read_bytes()),
            "report_sha256": sha256(report.read_bytes()),
            "python_version": sys.version.split()[0],
            "interpreter_rpm": interpreter,
            "stdlib_rpm": stdlib,
            "python_packages": python_packages,
            "report_entries": entries,
            "rpm_packages": packages,
            "review": REVIEW,
        }
    )
    write_atomic(output, zip_bytes(files))


def frontend_roots(frontend: dict[str, bytes]) -> set[str]:
    roots: set[str] = set()
    for name, data in frontend.items():
        if not name.startswith("debug/") or not name.endswith(".map"):
            continue
        for raw in object_list(read_json(data)["sources"]):
            source = string(raw)
            if "node_modules/" not in source:
                continue
            parts = source.split("/")
            require(
                parts[:3] == ["..", "node_modules", ".pnpm"],
                f"Review dependency path: {source}",
            )
            require(
                len(parts) >= 7 and parts[4] == "node_modules", f"Review dependency path: {source}"
            )
            end = 7 if parts[5].startswith("@") else 6
            require(
                len(parts) > end and all(p not in {"", ".", ".."} for p in parts[1:]),
                "Unsafe dependency path",
            )
            roots.add("/".join(parts[1:end]))
    return roots


def collect_frontend(frontend: dict[str, bytes], checkout: Path, files: dict[str, bytes]) -> None:
    packages: dict[str, object] = {}
    # Verify the lockfile of the checkout providing the candidate notices.
    with zipfile.ZipFile(io.BytesIO(frontend["source/vencord-source.zip"])) as source:
        require(
            source.read("pnpm-lock.yaml") == (checkout / "pnpm-lock.yaml").read_bytes(),
            "Frontend licence checkout lockfile differs from bundled source",
        )
    roots = frontend_roots(frontend)
    # Check the mapped dependency sources, not merely the declared package version.
    for name, data in frontend.items():
        if not name.startswith("debug/") or not name.endswith(".map"):
            continue
        mapping = read_json(data)
        for raw, content in zip(
            object_list(mapping["sources"]), object_list(mapping["sourcesContent"]), strict=True
        ):
            source_path = string(raw)
            if not source_path.startswith("../node_modules/"):
                continue
            relative = source_path.removeprefix("../")
            require(
                any(relative.startswith(root + "/") for root in roots),
                "Unrecognised mapped dependency",
            )
            path = (checkout / relative).resolve()
            require(path.is_relative_to(checkout.resolve()), "Mapped source outside checkout")
            require(
                path.read_bytes().decode("utf-8").replace("\r\n", "\n")
                == string(content).replace("\r\n", "\n"),
                f"Mapped dependency changed: {relative}",
            )
    for root in sorted(roots):
        directory = (checkout / root).resolve()
        require(directory.is_relative_to(checkout.resolve()), "Dependency outside managed checkout")
        metadata = read_json((directory / "package.json").read_bytes())
        name = string(metadata["name"])
        version = string(metadata["version"])
        prefix = f"frontend/npm/{safe_name(name.replace('/', '+'))}-{safe_name(version)}"
        for path in directory.iterdir():
            if notice_name(path.name):
                add_notice(files, prefix, path)
        packages[root] = {
            "name": name,
            "version": version,
            "licence": metadata.get("license"),
            "repository": metadata.get("repository"),
            "licence_files": sorted(n for n in files if n.startswith(prefix + "/")),
        }
    for name, data in frontend.items():
        if name.startswith("runtime/") and (
            name.endswith(".LEGAL.txt")
            or notice_name(PurePosixPath(name).name)
            or name.endswith("ATTRIBUTION.txt")
        ):
            files[f"frontend/{name}"] = data
    files["frontend/inventory.json"] = json_bytes(
        {
            "mapped_npm_packages": packages,
            "review": REVIEW,
            "limitations": (
                "Review vendored source, generated code " + "and all legal notices separately."
            ),
        }
    )


def verify_evidence(data: bytes, binary: bytes, frontend: bytes) -> None:
    files = zip_files(data)
    manifest = read_json(files.pop("inventory.json"))
    require(manifest.get("review") == REVIEW, "Invalid licence evidence status")
    require(manifest.get("native_sha256") == sha256(binary), "Stale native licence evidence")
    require(manifest.get("frontend_sha256") == sha256(frontend), "Stale frontend licence evidence")
    require(manifest.get("files") == hashes(files), "Licence evidence checksum mismatch")
    require(
        {"native/inventory.json", "frontend/inventory.json", "THIRD-PARTY-NOTICES.md"}
        <= set(files),
        "Incomplete licence evidence",
    )
    require(
        read_json(files["native/inventory.json"]).get("executable_sha256") == sha256(binary),
        "Native inventory mismatch",
    )


def audit(root: Path) -> Path:
    binary = (root / "dist/vencoverlay").read_bytes()
    frontend_path = root / "dist/vencoverlay-vencord.zip"
    verify(frontend_path)
    frontend = frontend_path.read_bytes()
    files = zip_files((root / NATIVE_EVIDENCE).read_bytes())
    native = read_json(files["native/inventory.json"])
    require(native.get("executable_sha256") == sha256(binary), "Stale native evidence; rebuild")
    require(
        native.get("report_sha256") == sha256((root / "build/compilation-report.xml").read_bytes()),
        "Stale compilation report; rebuild",
    )
    collect_frontend(zip_files(frontend), root / "build/vencord-dev/source", files)
    files["THIRD-PARTY-NOTICES.md"] = (
        "# Third-party licence evidence\n\n"
        + "This inventory and the collected notices are candidates for review, not a completed "
        + "redistribution audit. See native/inventory.json and frontend/inventory.json for "
        + "component identities, RPM source names and unresolved entries.\n\n"
        + "Before publication, resolve missing notices, vendored sources, exact corresponding "
        + "source availability and any LGPL relinking or replacement requirements. "
        + "The Nuitka compiler is used with the runtime exception; inspect the included notice.\n"
    ).encode()
    files["inventory.json"] = json_bytes(
        {
            "review": REVIEW,
            "native_sha256": sha256(binary),
            "frontend_sha256": sha256(frontend),
            "files": hashes(files),
        }
    )
    data = zip_bytes(files)
    verify_evidence(data, binary, frontend)
    output = root / AUDIT
    write_atomic(output, data)
    print(f"Licence evidence: {output}")
    print("Human notice and corresponding-source review remains required. Do not publish yet.")
    return output


def main() -> None:
    action, *args = sys.argv[1:]
    if action == "native" and len(args) == 3:
        collect_native(*(Path(arg) for arg in args))
    elif action == "audit" and not args:
        audit(Path(__file__).resolve().parents[1])
    else:
        raise SystemExit("Internal licence evidence command")


if __name__ == "__main__":
    main()
