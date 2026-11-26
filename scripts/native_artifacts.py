"""Native build metadata and target-side executable checks (stdlib only)."""

import os
import platform
import subprocess
import sys
from pathlib import Path

from scripts.frontend_artifacts import hashes, json_bytes, object_dict, read_json, require

NATIVE = "dist/vencoverlay"
METADATA = "dist/vencoverlay-native.json"
INPUTS = {
    "build.sh",
    "pyproject.toml",
    "uv.lock",
    "scripts/prerequisites.sh",
    "scripts/native_artifacts.py",
    "scripts/release_licences.py",
}


def check_elf(data: bytes) -> None:
    require(
        len(data) >= 64
        and data[:7] == b"\x7fELF\x02\x01\x01"
        and int.from_bytes(data[16:18], "little") in {2, 3}
        and int.from_bytes(data[18:20], "little") == 62,
        "Expected a Linux x86-64 ELF executable",
    )


def input_hashes(files: dict[str, bytes]) -> dict[str, dict[str, str | int]]:
    selected = {
        name: data
        for name, data in files.items()
        if name in INPUTS or (name.startswith("src/vencoverlay/") and name.endswith(".py"))
    }
    require(
        INPUTS <= set(selected) and "src/vencoverlay/cli.py" in selected,
        "Incomplete native build inputs",
    )
    return hashes(selected)


def source_hashes(root: Path) -> dict[str, dict[str, str | int]]:
    names = INPUTS | {
        path.relative_to(root).as_posix() for path in (root / "src/vencoverlay").rglob("*.py")
    }
    files: dict[str, bytes] = {}
    for name in names:
        path = root / name
        require(path.is_file() and not path.is_symlink(), f"Missing or unsafe build input: {name}")
        files[name] = path.read_bytes()
    return input_hashes(files)


def build_environment() -> dict[str, str]:
    release = platform.freedesktop_os_release()
    return {
        "platform": platform.system().lower(),
        "architecture": platform.machine(),
        "distribution": release.get("ID", "unknown"),
        "version": release.get("VERSION_ID", "unknown"),
        "libc": " ".join(platform.libc_ver()),
    }


def metadata(binary: bytes, version: str, inputs: dict[str, dict[str, str | int]]) -> bytes:
    check_elf(binary)
    return json_bytes(
        {
            "schema": 1,
            "version": version,
            "build_environment": build_environment(),
            "executable": hashes({NATIVE: binary})[NATIVE],
            "inputs": inputs,
        }
    )


def check_metadata(data: bytes, binary: bytes, version: str) -> dict[str, object]:
    check_elf(binary)
    record = read_json(data)
    require(
        record.get("schema") == 1 and record.get("version") == version,
        "Native build metadata version mismatch; rebuild with ./build.sh",
    )
    require(
        record.get("executable") == hashes({NATIVE: binary})[NATIVE],
        "Native executable checksum mismatch; rebuild or obtain a trusted package",
    )
    environment = object_dict(record.get("build_environment"))
    require(
        environment.get("platform") == "linux"
        and environment.get("architecture") == "x86_64"
        and all(
            isinstance(environment.get(key), str) for key in ("distribution", "version", "libc")
        ),
        "Invalid native build environment",
    )
    require(bool(object_dict(record.get("inputs"))), "Missing native build input hashes")
    return record


def probe(path: Path, version: str) -> None:
    """Execute only a trusted artifact, without starting the normal overlay."""
    require(
        platform.system() == "Linux" and platform.machine() == "x86_64",
        "This executable requires Linux x86-64",
    )
    check_elf(path.read_bytes())
    require(os.access(path, os.X_OK), f"Executable permission missing: {path}")
    # Avoid development interpreter/library overrides, retain the desktop session.
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in {"PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"}
    }
    for argument, expected in (
        ("--version", f"VencOverlay {version}"),
        ("--check-runtime", "VencOverlay runtime check passed"),
    ):
        try:
            result = subprocess.run(
                [str(path), argument],
                capture_output=True,
                text=True,
                check=False,
                timeout=60,
                env=environment,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise ValueError(f"Cannot execute VencOverlay ({argument}): {error}") from error
        require(
            result.returncode == 0 and result.stdout.strip() == expected,
            f"Native compatibility check failed ({argument}): "
            f"{result.stderr.strip() or result.stdout.strip() or result.returncode}",
        )


def main() -> None:
    # Internal build.sh helper. Capture before compilation, compare before publishing.
    root = Path(__file__).resolve().parents[1]
    action, *args = sys.argv[1:]
    if action == "snapshot" and len(args) == 1:
        Path(args[0]).write_bytes(json_bytes(source_hashes(root)))
    elif action == "record" and len(args) == 4:
        binary, snapshot, version, output = args
        current = source_hashes(root)
        require(
            read_json(Path(snapshot).read_bytes()) == current,
            "Native build inputs changed during compilation; rebuild",
        )
        Path(output).write_bytes(metadata(Path(binary).read_bytes(), version, current))
    else:
        raise SystemExit("Internal native build metadata command")


if __name__ == "__main__":
    main()
