"""Package and verify the pinned Vencord production distribution."""

import argparse
import hashlib
import io
import json
import os
import re
import subprocess
import tempfile
import tomllib
import zipfile
from pathlib import Path, PurePosixPath
from typing import cast

JS = (
    "patcher.js",
    "renderer.js",
    "preload.js",
    "vencordDesktopMain.js",
    "vencordDesktopRenderer.js",
    "vencordDesktopPreload.js",
)
CSS = ("renderer.css", "vencordDesktopRenderer.css")
REQUIRED = (*JS, *CSS)
RENDERER_MODULES = {"index.ts", "protocol.ts", "settings.ts", "voice.ts", "webcam.ts"}
NATIVE_MODULES = {"native.ts", "protocol.ts"}
PLUGIN_PREFIX = "src/userplugins/vencoverlay/"
METADATA = ("LICENSE", "VENCOVERLAY-LICENSE", "ATTRIBUTION.txt", "package.json")
SOURCE = "source/vencord-source.zip"
PRODUCTION = {"minified": True, "standalone": True, "updater_disabled": True}
NOTICE_PATTERN = re.compile(rb"For license information please see ([^\s*]+\.LEGAL\.txt)")
ATTRIBUTION = b"""VencOverlay custom Vencord distribution (GPL-3.0-or-later).
Vencord: Vendicated and contributors, https://github.com/Vendicated/Vencord
VencOverlay builds on Vesktop Overlay by Akii-G (akiloyutu):
https://github.com/Akii-G/vesktop-overlay
Maintained source: https://github.com/caminashell/vencoverlay
See LICENSE, VENCOVERLAY-LICENSE and the linked .LEGAL.txt files.
Corresponding plugin and pinned upstream source are in source/vencord-source.zip.
"""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def object_dict(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError("Expected a JSON object")
    return {string(key): item for key, item in cast(dict[object, object], value).items()}


def object_list(value: object) -> list[object]:
    if not isinstance(value, list):
        raise ValueError("Expected a JSON array")
    return cast(list[object], value)


def string(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("Expected a string")
    return value


def read_json(data: bytes) -> dict[str, object]:
    value: object = json.loads(data)
    return object_dict(value)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_regular(path: Path) -> bytes:
    require(path.is_file() and not path.is_symlink(), f"Missing or unsafe file: {path}")
    data = path.read_bytes()
    require(bool(data), f"Empty file: {path}")
    return data


def json_bytes(value: object) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()


def hashes(files: dict[str, bytes]) -> dict[str, dict[str, str | int]]:
    return {
        name: {"sha256": sha256(data), "bytes": len(data)} for name, data in sorted(files.items())
    }


def validate_map(data: bytes, expected: dict[str, bytes], name: str) -> None:
    mapping = read_json(data)
    require(mapping.get("version") == 3, f"Invalid source map: {name}")
    sources = object_list(mapping.get("sources"))
    contents = object_list(mapping.get("sourcesContent"))
    require(len(sources) == len(contents), f"Incomplete source map: {name}")
    found: set[str] = set()
    for source, content in zip(sources, contents, strict=True):
        source = string(source)
        if source.startswith("../" + PLUGIN_PREFIX):
            module = source.removeprefix("../" + PLUGIN_PREFIX)
        elif source.startswith(PLUGIN_PREFIX):
            module = source.removeprefix(PLUGIN_PREFIX)
        else:
            continue
        if module in expected:
            require(
                isinstance(content, str) and content.encode() == expected[module],
                f"Stale plugin source in {name}: {module}",
            )
            found.add(module)
    require(
        found == expected.keys(),
        f"Plugin modules missing from {name}: {sorted(expected.keys() - found)}",
    )


def check_payload(files: dict[str, bytes], plugin: dict[str, bytes], revision: str) -> None:
    allowed = {f"runtime/{name}" for name in (*REQUIRED, *METADATA)}
    allowed.update(f"debug/{name}.map" for name in REQUIRED)
    allowed.add(SOURCE)
    for name in files:
        require(
            name in allowed
            or (
                name.startswith("runtime/") and name.count("/") == 1 and name.endswith(".LEGAL.txt")
            ),
            f"Unexpected distribution file: {name}",
        )
    for name in REQUIRED:
        data = files.get(f"runtime/{name}", b"")
        require(bool(data), f"Missing runtime file: {name}")
        if name in JS:
            header = data.splitlines()[:4]
            require(
                header
                == [
                    f"// Vencord {revision[:7]}".encode(),
                    b"// Standalone: true",
                    b"// Platform: Universal",
                    b"// Updater Disabled: true",
                ],
                f"Unexpected build banner: {name}",
            )
        for notice in NOTICE_PATTERN.findall(data):
            reference = notice.decode()
            require(
                PurePosixPath(reference).name == reference
                and bool(files.get(f"runtime/{reference}")),
                f"Missing linked notice: {reference}",
            )
        map_name = f"debug/{name}.map"
        require(bool(files.get(map_name)), f"Missing source map: {map_name}")
        modules: set[str] = set()
        if name in ("renderer.js", "vencordDesktopRenderer.js"):
            modules = RENDERER_MODULES
            require(b"VencOverlayDebug" in data, f"Plugin missing from {name}")
        elif name in ("patcher.js", "vencordDesktopMain.js"):
            modules = NATIVE_MODULES
            require(b"VencOverlay" in data, f"Native plugin missing from {name}")
        validate_map(files[map_name], {module: plugin[module] for module in modules}, map_name)
    for name in METADATA:
        require(bool(files.get(f"runtime/{name}")), f"Missing distribution metadata: {name}")
    require(
        read_json(files["runtime/package.json"]) == {"type": "commonjs"},
        "Invalid runtime package.json",
    )


def read_archive(archive: zipfile.ZipFile) -> dict[str, bytes]:
    names = archive.namelist()
    require(len(names) == len(set(names)), "Duplicate archive entries")
    for info in archive.infolist():
        path = PurePosixPath(info.filename)
        require(
            not path.is_absolute()
            and ".." not in path.parts
            and "\\" not in info.filename
            and path.as_posix() == info.filename,
            f"Unsafe archive path: {info.filename}",
        )
        require(
            (info.external_attr >> 16) & 0o170000 != 0o120000,
            f"Symlink archive entry: {info.filename}",
        )
    return {info.filename: archive.read(info) for info in archive.infolist() if not info.is_dir()}


def verify(path: Path) -> None:
    with zipfile.ZipFile(path) as archive:
        files = read_archive(archive)
    manifest = read_json(files.pop("manifest.json"))
    require(manifest["schema"] == 1 and manifest["product"] == "VencOverlay", "Invalid manifest")
    require(manifest["production"] == PRODUCTION, "Not a production distribution")
    require(manifest["files"] == hashes(files), "Artifact checksum mismatch")
    revision = string(manifest["vencord_revision"])
    require(re.fullmatch(r"[0-9a-f]{40}", revision) is not None, "Invalid revision")
    with zipfile.ZipFile(io.BytesIO(files[SOURCE])) as source:
        # The outer SHA covers the entire corresponding source zip.
        names = source.namelist()
        require(
            len(names) == len(set(names)) and source.testzip() is None, "Invalid source archive"
        )
        require(source.comment.decode() == revision, "Source archive revision mismatch")
        plugin = {
            name: source.read(PLUGIN_PREFIX + name) for name in RENDERER_MODULES | NATIVE_MODULES
        }
        require(source.read("LICENSE") == files["runtime/LICENSE"], "Upstream licence mismatch")
        require(
            source.read("VENCOVERLAY-LICENSE") == files["runtime/VENCOVERLAY-LICENSE"],
            "Project licence mismatch",
        )
        require(
            read_json(source.read("package.json"))["version"] == manifest["vencord_version"],
            "Upstream version mismatch",
        )
        for name in (
            "pnpm-lock.yaml",
            "pnpm-workspace.yaml",
            "VENCOVERLAY-BUILD.md",
            "vencoverlay-build.mjs",
        ):
            require(bool(source.read(name)), f"Missing corresponding source: {name}")
    require(
        manifest["plugin_sha256"] == {name: sha256(data) for name, data in sorted(plugin.items())},
        "Plugin source checksum mismatch",
    )
    check_payload(files, plugin, revision)
    print(
        f"Verified: {path} ({len(files)} files, VencOverlay {manifest['version']}, "
        f"Vencord {manifest['vencord_version']})"
    )


def package(
    root: Path, checkout: Path, revision: str, node: str, pnpm: str, timestamp: str, output: Path
) -> None:
    dist = checkout / "dist"
    require(dist.is_dir() and not dist.is_symlink(), "Missing or unsafe build output")
    plugin_path = checkout / PLUGIN_PREFIX
    plugin = {name: read_regular(plugin_path / name) for name in RENDERER_MODULES | NATIVE_MODULES}
    maintained = root / "plugin/vencoverlay"
    require(
        all(read_regular(maintained / name) == data for name, data in plugin.items()),
        "Maintained plugin changed during build",
    )
    files = {f"runtime/{name}": read_regular(dist / name) for name in REQUIRED}
    files.update({f"debug/{name}.map": read_regular(dist / (name + ".map")) for name in REQUIRED})
    for path in sorted(dist.glob("*.LEGAL.txt")):
        files[f"runtime/{path.name}"] = read_regular(path)
    files["runtime/LICENSE"] = read_regular(checkout / "LICENSE")
    files["runtime/VENCOVERLAY-LICENSE"] = read_regular(root / "LICENSE")
    files["runtime/ATTRIBUTION.txt"] = ATTRIBUTION
    files["runtime/package.json"] = json_bytes({"type": "commonjs"})
    check_payload(files, plugin, revision)
    with (root / "pyproject.toml").open("rb") as stream:
        project = object_dict(tomllib.load(stream))
    version = string(object_dict(project["project"])["version"])
    upstream_version = string(read_json(read_regular(checkout / "package.json"))["version"])
    require(
        not output.parent.is_symlink() and output.parent.is_dir(), "Unsafe publication directory"
    )
    with tempfile.TemporaryDirectory(
        prefix=".vencoverlay-release-", dir=output.parent
    ) as temporary:
        stage = Path(temporary)
        source = stage / "vencord-source.zip"
        subprocess.run(
            ["git", "-C", str(checkout), "archive", "--format=zip", f"--output={source}", revision],
            check=True,
        )
        instructions = f"""# VencOverlay {version} corresponding Vencord source

Upstream Vencord {upstream_version}, commit {revision}.
Custom plugin: `{PLUGIN_PREFIX}`. See the included licence files.
Build with Node {node} and pnpm {pnpm} (or a compatible Node >=22).

```sh
pnpm install --frozen-lockfile
VENCORD_HASH={revision[:7]} VENCORD_REMOTE=Vendicated/Vencord \\
    SOURCE_DATE_EPOCH={timestamp} node vencoverlay-build.mjs
```

The timestamp is in milliseconds as expected by this Vencord revision.
The wrapper builds the standalone desktop targets with the updater disabled.
This is a source archive, not an installer; do not run it inside Discord.
"""
        with zipfile.ZipFile(source, "a", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(plugin_path.rglob("*")):
                require(not path.is_symlink(), "Symlink in staged plugin")
                if path.is_file():
                    archive.write(path, PLUGIN_PREFIX + path.relative_to(plugin_path).as_posix())
            archive.writestr("VENCOVERLAY-LICENSE", files["runtime/VENCOVERLAY-LICENSE"])
            archive.writestr("VENCOVERLAY-BUILD.md", instructions)
            archive.writestr(
                "vencoverlay-build.mjs", read_regular(root / "scripts/frontend-build.mjs")
            )
        files[SOURCE] = source.read_bytes()
        manifest = {
            "schema": 1,
            "product": "VencOverlay",
            "version": version,
            "vencord_revision": revision,
            "vencord_version": upstream_version,
            "node": node,
            "pnpm": pnpm,
            "build_timestamp_ms": int(timestamp),
            "production": PRODUCTION,
            "plugin_sha256": {name: sha256(data) for name, data in sorted(plugin.items())},
            "files": hashes(files),
        }
        candidate = stage / output.name
        with zipfile.ZipFile(
            candidate, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
        ) as archive:
            for name, data in sorted(files.items()):
                archive.writestr(name, data)
            archive.writestr("manifest.json", json_bytes(manifest))
        verify(candidate)
        os.replace(candidate, output)
    print(f"Published: {output} ({output.stat().st_size:,} bytes)")
    print(f"SHA-256: {sha256(output.read_bytes())}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    verify_parser = commands.add_parser("verify")
    verify_parser.add_argument("archive")
    pack = commands.add_parser("package")
    for name in ("root", "checkout", "output"):
        pack.add_argument(f"--{name}", required=True)
    for name in ("revision", "node", "pnpm", "timestamp"):
        pack.add_argument(f"--{name}", required=True)
    args = object_dict(vars(parser.parse_args()))
    try:
        if args["command"] == "verify":
            verify(Path(string(args["archive"])))
        else:
            package(
                Path(string(args["root"])),
                Path(string(args["checkout"])),
                string(args["revision"]),
                string(args["node"]),
                string(args["pnpm"]),
                string(args["timestamp"]),
                Path(string(args["output"])),
            )
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        zipfile.BadZipFile,
        subprocess.CalledProcessError,
    ) as exc:
        parser.exit(1, f"Artifact verification failed: {exc}\n")


if __name__ == "__main__":
    main()
