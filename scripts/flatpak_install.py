"""User-owned deployment of verified VencOverlay artifacts for Discord Flatpak."""

import argparse
import fcntl
import hashlib
import json
import os
import re
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import zipfile
from collections.abc import Callable, Generator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Self

from scripts.frontend_artifacts import (
    NOTICE_PATTERN,
    object_dict,
    object_list,
    read_json,
    require,
    sha256,
    string,
    verify,
)

APP = "com.discordapp.Discord"
PATCHER = "/var/data/vencoverlay/runtime/patcher.js"
BUNDLES = {"patcher.js", "renderer.js", "renderer.css", "preload.js"}
METADATA = {"LICENSE", "VENCOVERLAY-LICENSE", "ATTRIBUTION.txt", "package.json"}
ROOT = Path(__file__).resolve().parents[1]


def command(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, text=True, check=False, timeout=30)


def path_exists(path: Path) -> bool:
    return path.exists() or path.is_symlink()


def safe_path(path: Path) -> None:
    require(path.is_absolute(), f"Expected absolute path: {path}")
    for part in (path, *path.parents):
        require(not part.is_symlink(), f"Refusing symlink: {part}")


def regular(path: Path) -> bytes:
    safe_path(path)
    require(path.is_file(), f"Expected regular file: {path}")
    return path.read_bytes()


def json_data(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()


def xdg(key: str, fallback: Path) -> Path:
    value = Path(os.environ.get(key, ""))
    return value if value.is_absolute() else fallback


@dataclass(frozen=True)
class Paths:
    host: Path
    sandbox: Path
    desktop: Path
    lock: Path

    @classmethod
    def current(cls) -> Self:
        home = Path.home()
        data = xdg("XDG_DATA_HOME", home / ".local/share")
        runtime = Path(os.environ.get("XDG_RUNTIME_DIR", ""))
        require(runtime.is_absolute() and runtime.is_dir(), "XDG_RUNTIME_DIR is required")
        require(runtime.stat().st_uid == os.getuid(), "Runtime directory is not owned by user")
        return cls(
            data / "vencoverlay/flatpak",
            home / ".var/app" / APP / "data/vencoverlay",
            data / "applications" / f"{APP}.desktop",
            runtime / "vencoverlay-discord.lock",
        )


@dataclass(frozen=True)
class Discord:
    scope: str
    deployment: Path
    arch: str
    commit: str

    @property
    def resources(self) -> Path:
        return self.deployment / "files/discord/resources"

    @property
    def exported_desktop(self) -> Path:
        return self.deployment / "export/share/applications" / f"{APP}.desktop"


def detect(scope: str | None) -> Discord:
    found: list[Discord] = []
    for candidate in ("user", "system"):
        if scope is not None and scope != candidate:
            continue
        location = command("flatpak", "info", f"--{candidate}", "--show-location", APP, "stable")
        if location.returncode != 0:
            continue
        ref = command("flatpak", "info", f"--{candidate}", "--show-ref", APP, "stable")
        require(ref.returncode == 0, f"Cannot determine {candidate} Discord ref")
        parts = ref.stdout.strip().split("/")
        require(
            len(parts) == 4 and parts[:2] == ["app", APP] and parts[3] == "stable",
            "Unsupported Discord ref",
        )
        require(parts[2] == "x86_64", "Only the validated x86-64 build is supported")
        location_path = Path(location.stdout.strip())
        require(location_path.is_absolute(), "Invalid Flatpak deployment path")
        revision = command("flatpak", "info", f"--{candidate}", "--show-commit", APP, "stable")
        commit = revision.stdout.strip()
        require(
            revision.returncode == 0 and re.fullmatch(r"[0-9a-f]{64}", commit) is not None,
            "Cannot determine Discord deployment commit",
        )
        found.append(Discord(candidate, location_path.resolve(strict=True), parts[2], commit))
    require(bool(found), "Official Discord stable Flatpak not found")
    require(len(found) == 1, "Both user and system Discord found; select --scope")
    discord = found[0]
    require(discord.resources.is_dir(), "Unsupported Discord resources layout")
    safe_path(discord.resources)
    return discord


def discord_running() -> bool:
    result = command("flatpak", "ps", "--columns=application")
    require(result.returncode == 0, "Unable to check running Flatpak applications")
    return APP in {line.strip() for line in result.stdout.splitlines()}


def loader_bytes() -> bytes:
    """Electron ASAR pickle header and two files, matching upstream loader semantics."""
    # A system-wide loader may be seen by another user. Fall back to the original
    # application if that user has no VencOverlay runtime. Do not mask patcher errors.
    index = (
        "const p=" + json.dumps(PATCHER) + ";if(require('fs').existsSync(p)){require(p)}"
        "else{const j=require('path').join,a=j(__dirname,'..','_app.asar');"
        "require.main.filename=j(a,require(j(a,'package.json')).main);"
        "require('electron').app.setAppPath(a);require(require.main.filename)}\n"
    ).encode()
    package = b'{"name":"discord","main":"index.js"}\n'
    header = json.dumps(
        {
            "files": {
                "index.js": {"size": len(index), "offset": "0"},
                "package.json": {"size": len(package), "offset": str(len(index))},
            }
        },
        separators=(",", ":"),
    ).encode()
    aligned = (len(header) + 3) & ~3
    return (
        struct.pack("<IIII", 4, aligned + 8, aligned + 4, len(header))
        + header.ljust(aligned, b"\0")
        + index
        + package
    )


def file_description(path: Path) -> str:
    if path.is_symlink():
        return "symlink"
    if path.is_file():
        return f"file ({path.stat().st_size:,} bytes)"
    if path.is_dir():
        return "directory"
    return "absent"


def loader_status(resources: Path) -> str:
    app = resources / "app.asar"
    original = resources / "_app.asar"
    unsupported = ("app", "app.asar.unpacked", "_app.asar.unpacked")
    if any(path_exists(resources / name) for name in unsupported):
        return "foreign-or-unsupported"
    if not app.is_file() or app.is_symlink():
        return "foreign-or-unsupported"
    if path_exists(original):
        if (
            original.is_file()
            and not original.is_symlink()
            and app.stat().st_size == len(loader_bytes())
        ):
            if app.read_bytes() == loader_bytes():
                return "managed"
        return "foreign-or-unsupported"
    # A small loader without the original archive must never be treated as clean.
    if app.stat().st_size < 1024 * 1024:
        return "foreign-or-unsupported"
    return "unpatched-candidate"


def file_hash(path: Path) -> str:
    safe_path(path)
    require(path.is_file(), f"Expected regular file: {path}")
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def verified_clean(discord: Discord) -> bool:
    """Only patch an app.asar matching the installed Flatpak OSTree commit."""
    return loader_status(discord.resources) == "unpatched-candidate" and matches_original(
        discord, "app.asar"
    )


def matches_original(discord: Discord, name: str) -> bool:
    if shutil.which("ostree") is None or len(discord.deployment.parents) < 5:
        return False
    repo = discord.deployment.parents[4] / "repo"
    if not repo.is_dir():
        return False
    # Capture into a temporary file rather than buffering the original Discord archive.
    with tempfile.TemporaryFile() as original:
        result = subprocess.run(
            [
                "ostree",
                f"--repo={repo}",
                "cat",
                discord.commit,
                "/files/discord/resources/app.asar",
            ],
            stdout=original,
            stderr=subprocess.PIPE,
            check=False,
            timeout=60,
        )
        if result.returncode != 0:
            return False
        original.seek(0)
        expected = hashlib.file_digest(original, "sha256").hexdigest()
    return file_hash(discord.resources / name) == expected


def adoption_runtime(discord: Discord) -> Path:
    """Recognise only the two-file, literal require loader observed in Vencord."""
    resources = discord.resources
    require(loader_status(resources) == "foreign-or-unsupported", "Not an existing Vencord loader")
    require(
        not any(
            path_exists(resources / name)
            for name in ("app", "app.asar.unpacked", "_app.asar.unpacked")
        ),
        "Unsupported existing loader layout",
    )
    app = resources / "app.asar"
    safe_path(app)
    require(app.is_file() and 16 <= app.stat().st_size <= 4096, "Unsupported ASAR loader size")
    data = regular(app)
    marker, header_size, object_size, json_size = struct.unpack("<IIII", data[:16])
    aligned = (json_size + 3) & ~3
    require(
        marker == 4
        and header_size == aligned + 8
        and object_size == aligned + 4
        and 16 + aligned <= len(data),
        "Invalid ASAR header",
    )
    files = object_dict(read_json(data[16 : 16 + json_size]).get("files"))
    require(set(files) == {"index.js", "package.json"}, "Unsupported ASAR contents")
    index = object_dict(files["index.js"])
    package = object_dict(files["package.json"])
    size, package_size = index.get("size"), package.get("size")
    require(type(size) is int and type(package_size) is int, "Invalid ASAR file sizes")
    assert isinstance(size, int) and isinstance(package_size, int)
    payload = data[16 + aligned :]
    require(
        size > 0
        and package_size > 0
        and size + package_size == len(payload)
        and index == {"size": size, "offset": "0"}
        and package == {"size": package_size, "offset": str(size)},
        "Invalid ASAR entries",
    )
    require(
        read_json(payload[size:]) == {"name": "discord", "main": "index.js"},
        "Unsupported ASAR package",
    )
    match = re.fullmatch(r'require\(("[^"\\\r\n]+")\);?\s*', payload[:size].decode())
    require(match is not None, "Unsupported ASAR loader code")
    assert match is not None
    patcher = Path(string(json.loads(match.group(1))))
    runtime = Path.home() / ".config/Vencord/dist"
    require(patcher == runtime / "patcher.js", "Unsupported existing Vencord runtime path")
    safe_path(runtime)
    require(
        matches_original(discord, "_app.asar"), "Original Discord ASAR failed OSTree verification"
    )
    return runtime


def adoption_backup(runtime: Path) -> Path:
    return runtime.with_name(".vencoverlay-original-dist")


def owned_runtime(runtime: Path) -> dict[str, str]:
    files = inventory(runtime)
    for path in (runtime, *runtime.rglob("*")):
        require(path.stat().st_uid == os.getuid(), f"Runtime not owned by current user: {path}")
    require(BUNDLES <= files.keys(), "Incomplete existing Vencord runtime")
    require(
        runtime.parent.stat().st_uid == os.getuid() and os.access(runtime.parent, os.W_OK),
        "Runtime parent is not writable by its owner",
    )
    return files


def sandbox_runtime_matches(discord: Discord, runtime: Path) -> None:
    result = command(
        "flatpak",
        "run",
        f"--{discord.scope}",
        "--branch=stable",
        f"--arch={discord.arch}",
        "--command=sha256sum",
        APP,
        str(runtime / "patcher.js"),
    )
    require(
        result.returncode == 0 and result.stdout.split()[:1] == [file_hash(runtime / "patcher.js")],
        "Existing runtime not accessible in Discord sandbox",
    )


def plugin_names(data: bytes, category: str) -> set[str]:
    sources = object_list(read_json(data).get("sources"))
    require(bool(sources), "Empty existing source map; cannot assess plugin compatibility")
    pattern = re.compile(rf"(?:^|/)src/{category}/([^/]+)")
    return {match.group(1) for source in sources if (match := pattern.search(string(source)))}


# Only these historically verified upstream revisions have known version order.
# Foreign builds with unrecognised revisions require a matching production build.
KNOWN_VENCORD_REVISIONS = {
    "8d64c8be7e68616f8f116a6fff9480051aa2a0c5": "1.15.15",
    "d086992fb674299a2fa05ac6432edc3a777389f3": "1.15.19",
}


def version_tuple(version: str) -> tuple[int, ...]:
    require(
        re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version) is not None,
        "Unsupported Vencord version; cannot assess downgrade risk",
    )
    return tuple(int(part) for part in version.split("."))


def release_identity(artifact: Path) -> tuple[str, str]:
    verify(artifact)
    with zipfile.ZipFile(artifact) as release:
        manifest = read_json(release.read("manifest.json"))
    return string(manifest["vencord_revision"]), string(manifest["vencord_version"])


def check_version_order(old: tuple[str, str], new: tuple[str, str]) -> None:
    old_revision, old_version = old
    new_revision, new_version = new
    print(f"Existing Vencord: {old_version} ({old_revision[:7]})")
    print(f"Production Vencord: {new_version} ({new_revision[:7]})")
    require(version_tuple(new_version) >= version_tuple(old_version), "Vencord downgrade refused")
    require(
        old_version != new_version or old_revision == new_revision,
        "Different Vencord revisions at the same version; cannot establish ordering",
    )


def check_foreign_version(runtime: Path, production: tuple[str, str]) -> None:
    revisions: set[str] = set()
    for name in ("renderer.js", "patcher.js", "preload.js"):
        first = regular(runtime / name).split(b"\n", 1)[0].rstrip(b"\r")
        match = re.fullmatch(rb"// Vencord ([0-9a-f]{7,40})", first)
        require(match is not None, "Missing or unsupported Vencord build revision")
        assert match is not None
        revisions.add(match.group(1).decode("ascii"))
    require(len(revisions) == 1, "Existing Vencord bundle revisions disagree")
    revision = next(iter(revisions))
    new_revision, new_version = production
    if new_revision.startswith(revision):
        check_version_order((new_revision, new_version), production)
        return
    known = [key for key in KNOWN_VENCORD_REVISIONS if key.startswith(revision)]
    require(
        len(known) == 1,
        "Unknown existing Vencord revision; build against the matching revision before adoption",
    )
    check_version_order((known[0], KNOWN_VENCORD_REVISIONS[known[0]]), production)


def compare_plugins(runtime: Path, artifact: Path, previous_archive: Path | None) -> bool:
    """Source maps are evidence of bundled directories, not of enabled settings."""
    production = release_identity(artifact)
    check_foreign_version(runtime, production)
    if previous_archive is not None:
        check_version_order(release_identity(previous_archive), production)
    with zipfile.ZipFile(artifact) as release:
        new = release.read("debug/renderer.js.map")
    old = regular(runtime / "renderer.js.map")
    native = regular(runtime / "patcher.js.map")
    require(
        not (plugin_names(old, "userplugins") | plugin_names(native, "userplugins")),
        "Existing custom plugins detected; refusing runtime replacement",
    )
    old_plugins = plugin_names(old, "plugins")
    require(bool(old_plugins), "Existing upstream plugins could not be identified")
    new_plugins = plugin_names(new, "plugins")
    require(bool(new_plugins), "Production upstream plugins could not be identified")
    if previous_archive is not None:
        with zipfile.ZipFile(previous_archive) as previous:
            old_plugins |= plugin_names(previous.read("debug/renderer.js.map"), "plugins")
    removed = old_plugins - new_plugins
    print(f"Existing-only upstream plugin directories: {', '.join(sorted(removed)) or 'none'}")
    added = new_plugins - old_plugins
    print(f"Production-only upstream plugin directories: {', '.join(sorted(added)) or 'none'}")
    print("Existing custom plugin directories: none (source map inspection)")
    return bool(removed)


def loader_fingerprint(resources: Path) -> dict[str, str]:
    """Identify loader files before a preparation-only operation."""
    fingerprint: dict[str, str] = {}
    for name in ("app.asar", "_app.asar", "app/index.js", "app/package.json"):
        path = resources / name
        if path_exists(path):
            safe_path(path)
            fingerprint[name] = (
                sha256(json_data(inventory(path))) if path.is_dir() else file_hash(path)
            )
    for name in ("app", "app.asar.unpacked", "_app.asar.unpacked"):
        path = resources / name
        if path_exists(path):
            safe_path(path)
            fingerprint[name] = "directory" if path.is_dir() else "other"
    return fingerprint


def inventory(root: Path, *, ignore_manifest: bool = False) -> dict[str, str]:
    safe_path(root)
    require(root.is_dir(), f"Missing managed directory: {root}")
    files: dict[str, str] = {}
    for path in root.rglob("*"):
        require(not path.is_symlink(), f"Unexpected symlink: {path}")
        if path.is_dir():
            continue
        require(path.is_file(), f"Unexpected special file: {path}")
        name = path.relative_to(root).as_posix()
        if not (ignore_manifest and name == "install-manifest.json"):
            files[name] = file_hash(path)
    return files


def installed(paths: Paths) -> dict[str, object] | None:
    if not path_exists(paths.host):
        require(
            not path_exists(paths.sandbox),
            "Unmanaged sandbox runtime exists; refusing to replace",
        )
        return None
    state = read_json(regular(paths.host / "install-manifest.json"))
    require(
        state.get("schema") == 1 and state.get("product") == "VencOverlay",
        "Unknown installation",
    )
    require(
        state.get("host_files") == inventory(paths.host, ignore_manifest=True),
        "Managed host files changed; refusing mutation",
    )
    if state.get("adopted_runtime") is not None:
        runtime = Path(string(state["adopted_runtime"]))
        require(runtime == Path.home() / ".config/Vencord/dist", "Adopted runtime path changed")
        require(not path_exists(paths.sandbox), "Unexpected managed sandbox runtime")
        require(
            state.get("sandbox_files") == owned_runtime(runtime), "Managed runtime files changed"
        )
        require(
            state.get("original_runtime_files") == owned_runtime(adoption_backup(runtime)),
            "Original runtime backup changed; refusing mutation",
        )
    else:
        require(
            state.get("sandbox_files") == inventory(paths.sandbox),
            "Managed runtime files changed; refusing mutation",
        )
    require(
        state.get("sandbox_path") == str(paths.sandbox)
        and state.get("desktop_path") == str(paths.desktop),
        "Installation paths changed",
    )
    if state.get("desktop_sha256") is not None:
        require(
            sha256(regular(paths.desktop)) == state["desktop_sha256"],
            "Desktop launcher changed; refusing mutation",
        )
    return state


@contextmanager
def exclusive(paths: Paths) -> Generator[None]:
    safe_path(paths.lock)
    fd = os.open(paths.lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        require(
            os.fstat(fd).st_uid == os.getuid() and stat.S_ISREG(os.fstat(fd).st_mode),
            "Unsafe lock file",
        )
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("Installer or managed Discord launcher is active") from exc
        require(not discord_running(), "Quit Discord completely (including the tray) first")
        yield
    finally:
        os.close(fd)


class Transaction:
    """Rename-based rollback for ordinary exceptions; backups survive abrupt interruption."""

    def __init__(self) -> None:
        self.undo: list[Callable[[], None]] = []
        self.backups: list[Path] = []

    def replace(self, target: Path, staged: Path | None) -> None:
        safe_path(target)
        if path_exists(target):
            backup = Path(tempfile.mkdtemp(prefix=".vencoverlay-backup-", dir=target.parent))
            saved = backup / target.name
            os.rename(target, saved)
            self.backups.append(backup)
            self.undo.append(lambda: os.rename(saved, target))
        if staged is not None:
            os.rename(staged, target)
            self.undo.append(lambda: os.rename(target, staged))

    def move(self, source: Path, target: Path) -> None:
        safe_path(source)
        safe_path(target)
        require(not path_exists(target), f"Backup destination already exists: {target}")
        os.rename(source, target)
        self.undo.append(lambda: os.rename(target, source))

    def patch(self, discord: Discord, staged: Path) -> None:
        resources = discord.resources
        app = resources / "app.asar"
        original = resources / "_app.asar"
        require(verified_clean(discord), "Discord loader changed or could not be verified")
        # Preserve the original inode; never rewrite OSTree-backed bytes in place.
        os.link(app, original)

        def restore() -> None:
            if app.is_file() and os.path.samefile(app, original):
                original.unlink()
            elif app.is_file() and app.read_bytes() == loader_bytes():
                os.replace(original, app)
            else:
                raise ValueError(f"Cannot safely restore {app}; original retained at {original}")

        self.undo.append(restore)
        os.replace(staged, app)

    def rollback(self) -> None:
        for action in reversed(self.undo):
            action()
        self.clean()

    def clean(self) -> None:
        for backup in self.backups:
            try:
                shutil.rmtree(backup)
            except OSError as exc:
                print(f"Backup retained at {backup}: {exc}", file=sys.stderr)


@contextmanager
def transaction() -> Generator[Transaction]:
    tx = Transaction()
    try:
        yield tx
    except BaseException:
        try:
            tx.rollback()
        except (OSError, ValueError) as exc:
            print(f"Rollback incomplete; retained backups: {tx.backups}: {exc}", file=sys.stderr)
            raise
        raise
    else:
        tx.clean()


def desktop_contents(discord: Discord, paths: Paths) -> bytes:
    source = regular(discord.exported_desktop).decode()
    wrapper = str(paths.host / "discord-with-overlay")
    require(
        not any(char in wrapper for char in '\\"\n\r$`%'),
        "Unsupported launcher path characters",
    )
    lines: list[str] = []
    count = 0
    for line in source.splitlines():
        if line.startswith("Exec="):
            match = re.fullmatch(
                r"Exec=(?:/usr/bin/)?flatpak run ((?:--[^ ]+ )*)" + re.escape(APP) + r"( .*)?",
                line,
            )
            require(match is not None, f"Unsupported exported Exec: {line}")
            assert match is not None
            options = set(match.group(1).split())
            require(
                options
                <= {
                    "--branch=stable",
                    f"--arch={discord.arch}",
                    f"--command={APP}",
                    "--file-forwarding",
                },
                f"Unsupported exported launch options: {options}",
            )
            # Preserve desktop field codes, actions and Flatpak forwarding markers.
            suffix = match.group(2) or ""
            line = f'Exec="{wrapper}"{suffix}'
            count += 1
        elif line == "DBusActivatable=true":
            line = "DBusActivatable=false"
        lines.append(line)
    require(count > 0, "No exported Discord Exec line")
    return ("\n".join(lines) + "\n").encode()


def launcher(discord: Discord) -> bytes:
    template = regular(ROOT / "scripts/discord-with-overlay.sh").decode()
    return template.replace("@SCOPE@", discord.scope).replace("@ARCH@", discord.arch).encode()


def stage_release(
    discord: Discord, artifact: Path, executable: Path, host: Path, sandbox: Path
) -> str:
    archive = host / "vencoverlay-vencord.zip"
    shutil.copyfile(artifact, archive)
    verify(archive)
    with zipfile.ZipFile(archive) as release:
        manifest = read_json(release.read("manifest.json"))
        version = string(manifest["version"])
        runtime = sandbox / "runtime"
        runtime.mkdir()
        notices = {
            notice.decode()
            for bundle in BUNDLES
            for notice in NOTICE_PATTERN.findall(release.read(f"runtime/{bundle}"))
        }
        for name in release.namelist():
            if not name.startswith("runtime/"):
                continue
            filename = name.removeprefix("runtime/")
            notice = (
                filename.endswith(".LEGAL.txt") and filename.removesuffix(".LEGAL.txt") in BUNDLES
            )
            if filename in BUNDLES | METADATA | notices or notice:
                (runtime / filename).write_bytes(release.read(name))
    binary = host / "vencoverlay"
    shutil.copyfile(executable, binary)
    binary.chmod(0o755)
    result = command(str(binary), "--version")
    require(
        result.returncode == 0 and result.stdout.strip() == f"VencOverlay {version}",
        "Executable version does not match frontend archive",
    )
    (host / "app.asar").write_bytes(loader_bytes())
    wrapper = host / "discord-with-overlay"
    wrapper.write_bytes(launcher(discord))
    wrapper.chmod(0o755)
    return version


def confirm(yes: bool, message: str) -> None:
    print(message)
    if not yes:
        require(input("Type YES to continue: ") == "YES", "Cancelled")


def install(
    paths: Paths,
    discord: Discord,
    artifact: Path,
    executable: Path,
    *,
    prepare: bool,
    replace_launcher: bool,
    yes: bool,
    adopt: bool = False,
    allow_plugin_removal: bool = False,
) -> None:
    with exclusive(paths):
        previous = installed(paths)
        status = loader_status(discord.resources)
        require(not (adopt and prepare), "Adoption cannot be combined with preparation")
        previously_adopted = previous is not None and previous.get("adopted_runtime") is not None
        require(
            not previously_adopted or adopt, "Use --adopt-existing for adopted runtime upgrades"
        )
        runtime = adoption_runtime(discord) if adopt else None
        backup = adoption_backup(runtime) if runtime is not None else None
        original_runtime_files: dict[str, str] | None = None
        reviewed_artifact: str | None = None
        if previous is not None and runtime is None:
            reviewed_artifact = file_hash(artifact)
            check_version_order(
                release_identity(paths.host / "vencoverlay-vencord.zip"),
                release_identity(artifact),
            )
        if runtime is not None:
            require(
                previous is None or previously_adopted,
                "Cannot adopt over another installation mode",
            )
            assert backup is not None
            require(
                previously_adopted or not path_exists(backup),
                "Original runtime backup already exists",
            )
            original_runtime_files = owned_runtime(backup if previously_adopted else runtime)
            sandbox_runtime_matches(discord, runtime)
            reviewed_artifact = file_hash(artifact)
            removed = compare_plugins(
                backup if previously_adopted else runtime,
                artifact,
                paths.host / "vencoverlay-vencord.zip" if previously_adopted else None,
            )
            require(
                not removed or allow_plugin_removal,
                "Upstream plugins would be removed; review differences "
                "and use --allow-plugin-removal",
            )
        if previous is not None:
            require(
                previous.get("deployment") == str(discord.deployment),
                "Discord deployment changed; inspect before upgrading",
            )
            require(previous.get("scope") == discord.scope, "Discord installation scope changed")
            require(
                not prepare or previous.get("desktop_sha256") is None,
                "Cannot prepare over active installation",
            )
        can_patch = (
            not prepare
            and discord.scope == "user"
            and os.access(discord.resources, os.W_OK)
            and verified_clean(discord)
        )
        if prepare:
            require(status != "managed", "Managed loader already present; activate instead")
        if not prepare:
            require(
                status == "managed" or can_patch or runtime is not None,
                "Unknown or protected loader. Use --check; --prepare-only stages files "
                "without patching Discord.",
            )
            require(
                status != "managed" or previous is not None,
                "Unowned managed loader; investigate before installing",
            )
        original_asar_hash: str | None = None
        if not prepare:
            original_asar_hash = file_hash(
                discord.resources / ("_app.asar" if status == "managed" or adopt else "app.asar")
            )
            if (
                status == "managed"
                and previous is not None
                and previous.get("owned_loader") is True
            ):
                require(
                    previous.get("original_asar_sha256") == original_asar_hash,
                    "Original Discord archive has changed; refusing upgrade",
                )
        prepared_loader = (
            previous.get("prepared_loader")
            if previous is not None
            else loader_fingerprint(discord.resources)
        )
        active = not prepare
        original: bytes | None = None
        original_mode: int | None = None
        desktop_before = file_hash(paths.desktop) if active and path_exists(paths.desktop) else None
        if active:
            if previous is not None and previous.get("desktop_sha256") is not None:
                if (paths.host / "original.desktop").is_file():
                    original = regular(paths.host / "original.desktop")
                    mode = previous.get("original_desktop_mode")
                    require(type(mode) is int, "Invalid original desktop mode")
                    assert isinstance(mode, int)
                    original_mode = mode
            elif path_exists(paths.desktop):
                require(
                    replace_launcher,
                    "Existing local Discord launcher; use --replace-launcher to back up "
                    "and replace it",
                )
                original = regular(paths.desktop)
                original_mode = stat.S_IMODE(paths.desktop.stat().st_mode)
        safe_path(artifact)
        safe_path(executable)
        require(
            artifact.is_file() and executable.is_file(),
            "Build artifacts missing; build frontend and executable first",
        )
        confirm(
            yes,
            f"{'Prepare' if prepare else 'Install'} VencOverlay for {discord.scope} Discord."
            f"\nHost: {paths.host}\nRuntime: {runtime or paths.sandbox}\nLoader: {status}"
            + (f"\nOriginal runtime backup: {backup}" if adopt else "")
            + "\nExisting Vencord settings are not modified.",
        )
        runtime_parent = runtime.parent if runtime is not None else paths.sandbox.parent
        for parent in (paths.host.parent, runtime_parent, paths.desktop.parent):
            safe_path(parent)
            parent.mkdir(parents=True, exist_ok=True)
        with ExitStack() as stack:
            host_stage, sandbox_stage, desktop_directory = (
                Path(
                    stack.enter_context(
                        tempfile.TemporaryDirectory(
                            prefix=".vencoverlay-stage-",
                            dir=parent,
                        )
                    )
                )
                for parent in (paths.host.parent, runtime_parent, paths.desktop.parent)
            )
            host, sandbox = host_stage / "host", sandbox_stage / "sandbox"
            host.mkdir()
            sandbox.mkdir()
            version = stage_release(discord, artifact, executable, host, sandbox)
            if reviewed_artifact is not None:
                require(
                    file_hash(host / "vencoverlay-vencord.zip") == reviewed_artifact,
                    "Production artifact changed after compatibility review",
                )
            desktop = desktop_contents(discord, paths) if active else None
            desktop_stage = desktop_directory / "desktop"
            if desktop is not None:
                desktop_stage.write_bytes(desktop)
            if original is not None:
                (host / "original.desktop").write_bytes(original)
            state: dict[str, object] = {
                "schema": 1,
                "product": "VencOverlay",
                "version": version,
                "scope": discord.scope,
                "deployment": str(discord.deployment),
                "sandbox_path": str(paths.sandbox),
                "desktop_path": str(paths.desktop),
                "desktop_sha256": sha256(desktop) if desktop is not None else None,
                "original_desktop_mode": original_mode,
                "prepared_loader": prepared_loader,
                "original_asar_sha256": original_asar_hash,
                "owned_loader": active
                and (can_patch or (previous is not None and previous.get("owned_loader") is True)),
                "host_files": inventory(host),
                "sandbox_files": inventory(sandbox / "runtime" if adopt else sandbox),
                "adopted_runtime": str(runtime) if runtime is not None else None,
                "original_runtime_files": original_runtime_files,
            }
            (host / "install-manifest.json").write_bytes(json_data(state))
            # Recheck the deployment and running state immediately before publishing.
            require(
                detect(discord.scope) == discord and not discord_running(),
                "Discord deployment or running state changed",
            )
            require(installed(paths) == previous, "Existing installation changed during staging")
            if active:
                current_desktop = file_hash(paths.desktop) if path_exists(paths.desktop) else None
                require(
                    current_desktop == desktop_before, "Desktop launcher changed during staging"
                )
            if status == "managed":
                require(
                    loader_status(discord.resources) == "managed"
                    and file_hash(discord.resources / "_app.asar") == original_asar_hash,
                    "Managed Discord loader changed during staging",
                )
            if runtime is not None:
                assert backup is not None
                require(
                    adoption_runtime(discord) == runtime
                    and loader_fingerprint(discord.resources) == prepared_loader,
                    "Existing loader changed during staging",
                )
                require(
                    owned_runtime(backup if previously_adopted else runtime)
                    == original_runtime_files,
                    "Original runtime changed during staging",
                )
                require(
                    previously_adopted or not path_exists(backup),
                    "Original backup appeared during staging",
                )
                with transaction() as tx:
                    tx.replace(paths.host, host)
                    if not previously_adopted:
                        tx.move(runtime, backup)
                    tx.replace(runtime, sandbox / "runtime")
                    tx.replace(paths.desktop, desktop_stage)
            elif active and can_patch:
                with tempfile.TemporaryDirectory(
                    prefix=".vencoverlay-loader-", dir=discord.resources
                ) as patch_stage:
                    loader_path = Path(patch_stage) / "app.asar"
                    loader_path.write_bytes(loader_bytes())
                    with transaction() as tx:
                        tx.replace(paths.host, host)
                        tx.replace(paths.sandbox, sandbox)
                        tx.patch(discord, loader_path)
                        tx.replace(paths.desktop, desktop_stage)
            else:
                with transaction() as tx:
                    tx.replace(paths.host, host)
                    tx.replace(paths.sandbox, sandbox)
                    if desktop is not None:
                        require(
                            loader_status(discord.resources) == "managed",
                            "Managed loader changed",
                        )
                        tx.replace(paths.desktop, desktop_stage)
        print(f"{'Prepared' if prepare else 'Installed'} VencOverlay {version}")
        if prepare:
            print(
                f"Loader template: {paths.host / 'app.asar'}\nSandbox patcher: {PATCHER}"
                "\nNo Discord loader or desktop launcher was changed."
            )
        else:
            print(
                "Start Discord using its desktop launcher; enable VencOverlay in Vencord Plugins."
            )


def uninstall(paths: Paths, yes: bool) -> None:
    with exclusive(paths):
        state = installed(paths)
        if state is None:
            print("VencOverlay Flatpak integration is not installed.")
            return
        resources = Path(string(state["deployment"])) / "files/discord/resources"
        safe_path(resources)
        status = loader_status(resources) if resources.exists() else "missing"
        owned = state.get("owned_loader") is True
        adopted = state.get("adopted_runtime")
        runtime = Path(string(adopted)) if adopted is not None else None
        if status == "managed":
            require(
                owned and state.get("scope") == "user" and os.access(resources, os.W_OK),
                "Restore the externally installed Discord loader before uninstalling runtime files",
            )
            require(
                file_hash(resources / "_app.asar") == state.get("original_asar_sha256"),
                "Original Discord archive has changed; refusing removal",
            )
        elif status != "missing":
            require(
                state.get("prepared_loader") == loader_fingerprint(resources),
                "Unknown loader may reference installed runtime; inspect or restore "
                "before uninstalling",
            )
        # A Flatpak update may leave the previous deployment on disk. Do not remove
        # runtime files if a different active deployment still refers to them.
        current = detect(string(state["scope"]))
        if current.resources != resources:
            require(
                verified_clean(current),
                "New Discord deployment has an unknown or managed loader; inspect "
                "before uninstalling",
            )
        confirm(
            yes,
            "Remove VencOverlay integration? Configuration, cache and Discord settings "
            "will remain.",
        )
        require(not discord_running(), "Discord started during uninstall")
        require(installed(paths) == state, "Installation changed during uninstall")
        require(detect(string(state["scope"])) == current, "Discord deployment changed")
        if runtime is not None and resources.exists():
            require(
                loader_fingerprint(resources) == state.get("prepared_loader"),
                "Adopted loader changed during uninstall",
            )
        with (
            tempfile.TemporaryDirectory(
                prefix=".vencoverlay-stage-", dir=paths.desktop.parent
            ) as stage,
            transaction() as tx,
        ):
            if status == "managed":
                app, original = resources / "app.asar", resources / "_app.asar"
                # Save the current loader, restore original with its original inode.
                tx.replace(app, None)
                os.rename(original, app)
                tx.undo.append(lambda: os.rename(app, original))
            if state.get("desktop_sha256") is not None:
                old = paths.host / "original.desktop"
                replacement = Path(stage) / "desktop" if old.is_file() else None
                if replacement is not None:
                    replacement.write_bytes(regular(old))
                    mode = state.get("original_desktop_mode")
                    require(type(mode) is int, "Invalid original desktop mode")
                    assert isinstance(mode, int)
                    replacement.chmod(mode)
                tx.replace(paths.desktop, replacement)
            if runtime is not None:
                tx.replace(runtime, None)
                tx.move(adoption_backup(runtime), runtime)
            tx.replace(paths.host, None)
            if runtime is None:
                tx.replace(paths.sandbox, None)
        print("VencOverlay integration removed; user configuration retained.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("check", "install", "uninstall"))
    parser.add_argument(
        "--scope", choices=("user", "system"), help="Select Flatpak installation scope"
    )
    parser.add_argument(
        "--prepare-only", action="store_true", help="Stage runtime without patching Discord"
    )
    parser.add_argument(
        "--replace-launcher",
        action="store_true",
        help="Explicitly allow backing up and replacing an existing local desktop entry",
    )
    parser.add_argument(
        "--adopt-existing",
        action="store_true",
        help="Adopt a verified existing Vencord loader and runtime",
    )
    parser.add_argument(
        "--allow-plugin-removal",
        action="store_true",
        help="Accept reported upstream plugin directory removals during adoption",
    )
    parser.add_argument("--yes", action="store_true", help="Skip the interactive YES confirmation")
    parser.add_argument("--artifact", type=Path, default=ROOT / "dist/vencoverlay-vencord.zip")
    parser.add_argument("--executable", type=Path, default=ROOT / "dist/vencoverlay")
    args = object_dict(vars(parser.parse_args()))
    if args["action"] != "install" and (args["prepare_only"] or args["replace_launcher"]):
        parser.error("Preparation and launcher replacement options require installation")
    if args["adopt_existing"] and args["action"] == "uninstall":
        parser.error("Uninstall automatically detects adoption")
    if args["allow_plugin_removal"] and not (
        args["adopt_existing"] and args["action"] == "install"
    ):
        parser.error("Plugin removal consent requires --adopt-existing installation")
    try:
        require(os.getuid() != 0, "Run as your normal user; never sudo this installer")
        require(
            shutil.which("flatpak") is not None and shutil.which("flock") is not None,
            "Flatpak and flock are required",
        )
        paths = Paths.current()
        for path in (paths.host, paths.sandbox, paths.desktop, paths.lock):
            safe_path(path)
        if args["action"] == "uninstall":
            uninstall(paths, bool(args["yes"]))
        else:
            scope = string(args["scope"]) if args["scope"] is not None else None
            discord = detect(scope)
            if args["action"] == "check":
                print(
                    f"Discord scope: {discord.scope}\nDeployment: {discord.deployment}"
                    f"\nResources: {discord.resources}"
                    f"\nLoader: {loader_status(discord.resources)}"
                    f"\napp.asar: {file_description(discord.resources / 'app.asar')}"
                    f"\n_app.asar: {file_description(discord.resources / '_app.asar')}"
                    f"\napp directory: {file_description(discord.resources / 'app')}"
                    f"\nMatches OSTree original: {verified_clean(discord)}"
                    f"\nResources writable: {os.access(discord.resources, os.W_OK)}"
                    f"\nDiscord running: {discord_running()}"
                    f"\nLocal launcher: {paths.desktop if paths.desktop.exists() else 'none'}"
                    f"\nHost install: {paths.host}\nSandbox runtime: {paths.sandbox}"
                    f"\nHost executable present: {(ROOT / 'dist/vencoverlay').is_file()}"
                    f"\nFrontend archive present: "
                    f"{(ROOT / 'dist/vencoverlay-vencord.zip').is_file()}"
                )
                state = installed(paths)
                print(f"Managed installation: {state.get('version') if state else 'none'}")
                if args["adopt_existing"]:
                    runtime = adoption_runtime(discord)
                    sandbox_runtime_matches(discord, runtime)
                    backup = adoption_backup(runtime)
                    adopted = state is not None and state.get("adopted_runtime") is not None
                    require(
                        adopted or not path_exists(backup), "Original runtime backup already exists"
                    )
                    original_runtime = backup if adopted else runtime
                    owned_runtime(original_runtime)
                    artifact = args["artifact"]
                    assert isinstance(artifact, Path)
                    safe_path(artifact.absolute())
                    removed = compare_plugins(
                        original_runtime,
                        artifact.absolute(),
                        paths.host / "vencoverlay-vencord.zip" if adopted else None,
                    )
                    print(f"Adoption runtime: {runtime}\nOriginal runtime backup: {backup}")
                    print("Original _app.asar matches OSTree: True")
                    print(f"Upstream removal consent required: {removed}")
            else:
                artifact = args["artifact"]
                executable = args["executable"]
                require(
                    isinstance(artifact, Path) and isinstance(executable, Path),
                    "Invalid artifact paths",
                )
                assert isinstance(artifact, Path) and isinstance(executable, Path)
                install(
                    paths,
                    discord,
                    artifact.absolute(),
                    executable.absolute(),
                    prepare=bool(args["prepare_only"]),
                    replace_launcher=bool(args["replace_launcher"]),
                    yes=bool(args["yes"]),
                    adopt=bool(args["adopt_existing"]),
                    allow_plugin_removal=bool(args["allow_plugin_removal"]),
                )
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        zipfile.BadZipFile,
        subprocess.SubprocessError,
        EOFError,
        KeyboardInterrupt,
    ) as exc:
        parser.exit(1, f"VencOverlay installation: {exc}\n")


if __name__ == "__main__":
    main()
