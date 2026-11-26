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
from time import monotonic, sleep
from typing import Self
from uuid import uuid4

from scripts import native_artifacts
from scripts.frontend_artifacts import (
    NOTICE_PATTERN,
    hashes,
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
DISCORD_EXIT_GRACE_SECONDS = 5.0
DISCORD_EXIT_POLL_SECONDS = 0.25


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
    require(
        bool(found),
        "Official Discord stable Flatpak not found; install Discord and Vencord first. "
        "Vesktop installation is not supported yet.",
    )
    require(len(found) == 1, "Both user and system Discord found; select --scope")
    discord = found[0]
    require(discord.resources.is_dir(), "Unsupported Discord resources layout")
    safe_path(discord.resources)
    return discord


def discord_running() -> bool:
    result = command("flatpak", "ps", "--columns=application")
    require(result.returncode == 0, "Unable to check running Flatpak applications")
    return APP in {line.strip() for line in result.stdout.splitlines()}


def require_discord_stopped(message: str) -> None:
    """Allow brief Flatpak instance cleanup, but never ignore a listed instance."""
    deadline = monotonic() + DISCORD_EXIT_GRACE_SECONDS
    if not discord_running():
        return
    print("Discord Flatpak instance detected; waiting briefly for it to exit.")
    while True:
        remaining = deadline - monotonic()
        require(remaining > 0, message)
        sleep(min(DISCORD_EXIT_POLL_SECONDS, remaining))
        if not discord_running():
            return


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
    verify(artifact, quiet=True)
    with zipfile.ZipFile(artifact) as release:
        manifest = read_json(release.read("manifest.json"))
    return string(manifest["vencord_revision"]), string(manifest["vencord_version"])


def check_version_order(old: tuple[str, str], new: tuple[str, str]) -> None:
    old_revision, old_version = old
    new_revision, new_version = new
    require(version_tuple(new_version) >= version_tuple(old_version), "Vencord downgrade refused")
    require(
        old_version != new_version or old_revision == new_revision,
        "Different Vencord revisions at the same version; cannot establish ordering",
    )


def report_versions(
    original: tuple[str, str],
    production: tuple[str, str],
    previous: tuple[str, str] | None = None,
) -> None:
    old_revision, old_version = original
    new_revision, new_version = production
    print(f"Existing Vencord: {old_version} ({old_revision[:7]})")
    if previous is not None and previous != original:
        revision, version = previous
        print(f"Installed Vencord: {version} ({revision[:7]})")
    print(f"Production Vencord: {new_version} ({new_revision[:7]})")


def check_foreign_version(runtime: Path, production: tuple[str, str]) -> tuple[str, str]:
    revisions: set[str] = set()
    for name in ("renderer.js", "patcher.js", "preload.js"):
        first = regular(runtime / name).split(b"\n", 1)[0].rstrip(b"\r")
        match = re.fullmatch(rb"// Vencord ([0-9a-f]{7,40})", first)
        require(match is not None, "Missing or unsupported Vencord build revision")
        assert match is not None
        revisions.add(match.group(1).decode("ascii"))
    require(len(revisions) == 1, "Existing Vencord bundle revisions disagree")
    revision = next(iter(revisions))
    new_revision, _ = production
    if new_revision.startswith(revision):
        check_version_order(production, production)
        return production
    known = [key for key in KNOWN_VENCORD_REVISIONS if key.startswith(revision)]
    require(
        len(known) == 1,
        "Unknown existing Vencord revision; build against the matching revision before adoption",
    )
    original = (known[0], KNOWN_VENCORD_REVISIONS[known[0]])
    check_version_order(original, production)
    return original


def compare_plugins(runtime: Path, artifact: Path, previous_archive: Path | None) -> bool:
    """Source maps are evidence of bundled directories, not of enabled settings."""
    production = release_identity(artifact)
    original = check_foreign_version(runtime, production)
    previous = release_identity(previous_archive) if previous_archive is not None else None
    if previous is not None:
        check_version_order(previous, production)
    report_versions(original, production, previous)
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


def installed(
    paths: Paths, *, reviewed_external: dict[str, str] | None = None
) -> dict[str, object] | None:
    """Validate ownership, including an exact reviewed runtime during explicit repair."""
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
            (state.get("sandbox_files") if reviewed_external is None else reviewed_external)
            == owned_runtime(runtime),
            "Managed runtime files changed",
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


def installation_state(paths: Paths, discord: Discord) -> tuple[str, dict[str, object] | None]:
    """Report integration ownership, not whether the plugin is currently enabled."""
    try:
        state = installed(paths)
        if state is None:
            require(loader_status(discord.resources) != "managed", "Unowned managed loader")
            return "Not installed", None
        require(
            state.get("deployment") == str(discord.deployment)
            and state.get("scope") == discord.scope,
            "Discord deployment or scope changed; inspect before upgrading",
        )
        if state.get("desktop_sha256") is None:
            require(
                state.get("adopted_runtime") is None and state.get("owned_loader") is False,
                "Invalid prepared installation ownership",
            )
            require(
                loader_status(discord.resources) == "managed"
                or state.get("prepared_loader") == loader_fingerprint(discord.resources),
                "Prepared loader changed; inspect before completing installation",
            )
            return "Prepared", state
        if state.get("adopted_runtime") is not None:
            require(
                state.get("prepared_loader") == loader_fingerprint(discord.resources),
                "Existing loader changed; inspect before upgrading",
            )
        else:
            require(loader_status(discord.resources) == "managed", "Managed loader is missing")
            require(
                file_hash(discord.resources / "_app.asar") == state.get("original_asar_sha256"),
                "Original Discord archive has changed",
            )
        return "Installed", state
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ValueError(f"Installation state: Incomplete — {exc}") from exc


def select_adoption(
    discord: Discord, previous: dict[str, object] | None, *, prepare: bool, adopt: bool | None
) -> bool:
    """Automatic normal installation requires a recognised existing Vencord patch."""
    if adopt is not None:
        return adopt
    if prepare:
        return False
    if previous is not None and previous.get("adopted_runtime") is not None:
        return True
    status = loader_status(discord.resources)
    if status == "managed" and previous is not None:
        return False
    if status == "unpatched-candidate":
        raise ValueError(
            "Discord must already have Vencord installed. Install Vencord from "
            "https://vencord.dev/download/, verify it works, quit Discord and retry."
        )
    # The adoption path performs the full loader review; do not repeat OSTree
    # verification here merely to choose the candidate mode.
    return True


def review_adoption_runtime(discord: Discord) -> Path:
    try:
        return adoption_runtime(discord)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ValueError(
            f"Unsupported Vencord loader: {exc}. Install a compatible Vencord patch "
            "or inspect the existing installation; no files were replaced."
        ) from exc


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
        require_discord_stopped("Quit Discord completely (including the tray) first")
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
    discord: Discord,
    artifact: Path,
    executable: Path,
    host: Path,
    sandbox: Path,
    reviewed_executable: str | None = None,
) -> str:
    archive = host / "vencoverlay-vencord.zip"
    shutil.copyfile(artifact, archive)
    verify(archive, quiet=True)
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
    if reviewed_executable is not None:
        require(
            file_hash(binary) == reviewed_executable,
            "Staged executable changed after compatibility review",
        )
    native_artifacts.probe(binary, version)
    print("Native runtime compatibility: passed (unmapped window)")
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
    adopt: bool | None = None,
    allow_plugin_removal: bool = False,
    reviewed_executable: str | None = None,
    repair: bool = False,
) -> None:
    with exclusive(paths):
        external_files: dict[str, str] | None = None
        recovery: Path | None = None
        if repair:
            require(not prepare and adopt is not False, "Repair requires Vencord adoption")
            repair_runtime = review_adoption_runtime(discord)
            external_files = owned_runtime(repair_runtime)
            previous = installed(paths, reviewed_external=external_files)
            require(
                previous is not None
                and previous.get("adopted_runtime") == str(repair_runtime)
                and previous.get("owned_loader") is False
                and previous.get("desktop_sha256") is not None
                and previous.get("scope") == discord.scope,
                "Repair requires an existing adopted installation in the same scope",
            )
            assert previous is not None
            changed = previous.get("sandbox_files") != external_files
            require(
                changed
                or previous.get("deployment") != str(discord.deployment)
                or previous.get("prepared_loader") != loader_fingerprint(discord.resources),
                "No repair required; run ./install.sh for a normal upgrade",
            )
            if changed:
                recovery = repair_runtime.with_name(f".vencoverlay-recovery-dist-{uuid4().hex}")
            state_label = "Incomplete (explicit repair)"
            adopt = True
        else:
            state_label, previous = installation_state(paths, discord)
        print(f"Installation state: {state_label}")
        status = loader_status(discord.resources)
        adopt = select_adoption(discord, previous, prepare=prepare, adopt=adopt)
        require(not (adopt and prepare), "Adoption cannot be combined with preparation")
        require(not allow_plugin_removal or adopt, "Plugin removal consent requires adoption")
        previously_adopted = previous is not None and previous.get("adopted_runtime") is not None
        require(
            not previously_adopted or adopt,
            "Use automatic adoption or --adopt-existing for adopted runtime upgrades",
        )
        prepared = state_label == "Prepared"
        runtime = review_adoption_runtime(discord) if adopt else None
        backup = adoption_backup(runtime) if runtime is not None else None
        original_runtime_files: dict[str, str] | None = None
        reviewed_artifact: str | None = None
        if previous is not None and runtime is None:
            reviewed_artifact = file_hash(artifact)
            existing_identity = release_identity(paths.host / "vencoverlay-vencord.zip")
            production_identity = release_identity(artifact)
            check_version_order(existing_identity, production_identity)
            report_versions(existing_identity, production_identity)
        if runtime is not None:
            require(
                previous is None or previously_adopted or prepared,
                "Cannot adopt over another active installation mode",
            )
            assert backup is not None
            if prepared:
                assert previous is not None
                require(
                    previous.get("prepared_loader") == loader_fingerprint(discord.resources),
                    "Prepared loader changed; inspect before completing installation",
                )
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
            if recovery is not None:
                # A replaced runtime must itself pass the foreign-bundle review. Keep
                # the original uninstall baseline unchanged; do not silently downgrade
                # a newer externally installed Vencord on eventual removal.
                production = release_identity(artifact)
                require(
                    check_foreign_version(runtime, production)
                    == check_foreign_version(backup, production),
                    "External Vencord revision differs from the original backup; "
                    "repair requires manual review",
                )
                print("Reviewing externally replaced Vencord runtime:")
                removed = compare_plugins(runtime, artifact, None) or removed
            require(
                not removed or allow_plugin_removal,
                "Upstream plugins would be removed; review differences "
                "and use --allow-plugin-removal",
            )
        if previous is not None:
            require(
                repair or previous.get("deployment") == str(discord.deployment),
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
            if previous is not None and not repair
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
        if repair:
            action = "Repair"
        elif prepare:
            action = "Prepare"
        elif prepared:
            action = "Complete installation of"
        elif previous is not None:
            action = "Upgrade"
        else:
            action = "Install"
        confirm(
            yes,
            f"{action} VencOverlay for {discord.scope} Discord."
            f"\nHost: {paths.host}\nRuntime: {runtime or paths.sandbox}"
            f"\nLoader: {'verified existing Vencord' if adopt else status}"
            + (f"\nOriginal runtime backup: {backup}" if adopt else "")
            + (f"\nExternally replaced runtime retained at: {recovery}" if recovery else "")
            + ("\nUninstall will still restore the original backup." if repair else "")
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
            if reviewed_executable is not None:
                require(
                    file_hash(executable) == reviewed_executable,
                    "Executable changed after compatibility review",
                )
            version = stage_release(
                discord, artifact, executable, host, sandbox, reviewed_executable
            )
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
            # The adoption sandbox probe may leave an instance briefly listed.
            # Still require a fresh stopped-state check before publishing.
            require_discord_stopped("Discord deployment or running state changed")
            # Recheck the deployment and running state immediately before publishing.
            require(
                detect(discord.scope) == discord and not discord_running(),
                "Discord deployment or running state changed",
            )
            require(
                installed(paths, reviewed_external=external_files) == previous,
                "Existing installation changed during staging",
            )
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
                    elif recovery is not None:
                        tx.move(runtime, recovery)
                    tx.replace(runtime, sandbox / "runtime")
                    if prepared:
                        tx.replace(paths.sandbox, None)
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
        if recovery is not None:
            print(f"External runtime preserved at: {recovery}")
        print(f"{'Prepared' if prepare else 'Installed'} VencOverlay {version}")
        if prepare:
            print(
                f"Loader template: {paths.host / 'app.asar'}\nSandbox patcher: {PATCHER}"
                "\nNo Discord loader or desktop launcher was changed."
                "\nInstallation state: Prepared (inactive). Run ./install.sh to complete "
                "integration when the loader is compatible."
            )
        else:
            print(
                "Installation state: Installed.\n"
                "Start Discord using its desktop launcher; enable VencOverlay in Vencord Plugins."
            )


def refresh_exported_desktop(discord: Discord) -> None:
    """Best-effort notification for the now-unshadowed Flatpak desktop entry."""
    try:
        deployment = discord.deployment
        require(
            deployment.is_absolute()
            and len(deployment.parents) >= 5
            and deployment.parts[-5:-1] == ("app", APP, discord.arch, "stable"),
            "Unrecognised Flatpak deployment layout",
        )
        export = deployment.parents[4] / "exports/share/applications" / f"{APP}.desktop"
        safe_path(export.parent)
        require(export.is_symlink(), f"Expected Flatpak desktop export symlink: {export}")
        require(
            export.resolve(strict=True) == discord.exported_desktop,
            f"Flatpak desktop export does not reference the verified deployment: {export}",
        )
        regular(discord.exported_desktop)
        # Equivalent to touch -h: never change the target's timestamps or contents.
        os.utime(export, follow_symlinks=False)
    except (OSError, ValueError, RuntimeError) as exc:
        print(
            f"Warning: Could not refresh the original Discord application icon: {exc}. "
            "Uninstallation succeeded; your desktop may need to refresh its application list.",
            file=sys.stderr,
        )
    else:
        print("Original Discord desktop export refreshed.")


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
        if state.get("desktop_sha256") is not None and not path_exists(paths.desktop):
            refresh_exported_desktop(current)


def review_native(artifact: Path, executable: Path) -> tuple[str, str]:
    """Review trusted local production inputs before installation or execution."""
    safe_path(artifact)
    require(
        artifact.is_file() and executable.is_file(),
        "Production artifacts missing; obtain a complete package or build from source",
    )
    verify(artifact)
    with zipfile.ZipFile(artifact) as release:
        version = string(read_json(release.read("manifest.json"))["version"])
    binary = regular(executable)
    sidecar = executable.with_name(executable.name + "-native.json")
    require(
        sidecar.is_file(), "Native build metadata missing; rebuild or obtain a complete package"
    )
    sidecar_data = regular(sidecar)
    record = native_artifacts.check_metadata(sidecar_data, binary, version)
    if (ROOT / "release-manifest.json").is_file():
        require(
            artifact == ROOT / "dist/vencoverlay-vencord.zip"
            and executable == ROOT / native_artifacts.NATIVE,
            "Use the packaged artifacts when installing from a release",
        )
        manifest = read_json(regular(ROOT / "release-manifest.json"))
        entries = object_dict(manifest["files"])
        require(
            manifest.get("schema") == 2 and manifest.get("version") == version,
            "Release manifest version mismatch",
        )
        for path, data in (
            (artifact, regular(artifact)),
            (executable, binary),
            (sidecar, sidecar_data),
        ):
            name = path.relative_to(ROOT).as_posix()
            require(
                entries.get(name) == hashes({name: data})[name],
                f"Release artifact checksum mismatch: {name}",
            )
    elif (ROOT / "pyproject.toml").is_file() and executable == ROOT / native_artifacts.NATIVE:
        require(
            record["inputs"] == native_artifacts.source_hashes(ROOT),
            "Native build inputs changed; rebuild with ./build.sh",
        )
    print(f"Native executable checksum verified: {sha256(binary)}")
    print(f"Native build environment: {record['build_environment']}")
    target = native_artifacts.build_environment()
    print(f"Native target environment: {target}")
    if target["distribution"] != "fedora" or target["version"] != "43":
        print("Warning: this target OS version is outside the Fedora 43 live test baseline.")
    return version, sha256(binary)


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
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument(
        "--patch-clean",
        action="store_true",
        help="Advanced: explicitly patch a verified writable per-user Discord deployment",
    )
    modes.add_argument(
        "--adopt-existing",
        action="store_true",
        help="Explicitly adopt a verified Vencord runtime (normally detected automatically)",
    )
    modes.add_argument(
        "--repair", action="store_true", help="Repair a verified previously adopted runtime"
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
    if (args["adopt_existing"] or args["patch_clean"]) and args["action"] == "uninstall":
        parser.error("Uninstall automatically detects adoption")
    if args["allow_plugin_removal"] and (
        args["action"] != "install" or args["prepare_only"] or args["patch_clean"]
    ):
        parser.error("Plugin removal consent requires Vencord adoption installation")
    if args["repair"] and (
        args["action"] != "install" or args["prepare_only"] or args["replace_launcher"]
    ):
        parser.error("Repair requires installation without preparation or launcher replacement")
    if args["prepare_only"] and (args["adopt_existing"] or args["patch_clean"]):
        parser.error("Preparation cannot be combined with an active installation mode")
    adopt = True if args["adopt_existing"] else False if args["patch_clean"] else None
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
            artifact = args["artifact"]
            executable = args["executable"]
            assert isinstance(artifact, Path) and isinstance(executable, Path)
            if args["action"] == "check":
                print(
                    f"Discord scope: {discord.scope}\nDeployment: {discord.deployment}"
                    f"\nResources: {discord.resources}"
                    f"\napp.asar: {file_description(discord.resources / 'app.asar')}"
                    f"\n_app.asar: {file_description(discord.resources / '_app.asar')}"
                    f"\napp directory: {file_description(discord.resources / 'app')}"
                    f"\nUnpatched app.asar matches OSTree: {verified_clean(discord)}"
                    f"\nResources writable: {os.access(discord.resources, os.W_OK)}"
                    f"\nDiscord running: {discord_running()}"
                    f"\nLocal launcher: {paths.desktop if paths.desktop.exists() else 'none'}"
                    f"\nHost install: {paths.host}\nSandbox runtime: {paths.sandbox}"
                    f"\nCandidate executable present: {executable.is_file()}"
                    f"\nCandidate frontend archive present: {artifact.is_file()}"
                )
                label, state = installation_state(paths, discord)
                print(f"Installation state: {label}")
                print(f"Managed version: {state.get('version') if state else 'none'}")
                adopt = select_adoption(discord, state, prepare=False, adopt=adopt)
                require(
                    state is None or state.get("adopted_runtime") is None or adopt,
                    "Use automatic adoption or --adopt-existing for adopted runtime upgrades",
                )
                require(
                    artifact.is_file() and executable.is_file(),
                    "Production artifacts missing; obtain a complete package or build from source",
                )
                version, digest = review_native(artifact.absolute(), executable.absolute())
                native_artifacts.probe(executable.absolute(), version)
                require(
                    file_hash(executable.absolute()) == digest,
                    "Executable changed during compatibility check",
                )
                print("Native runtime compatibility: passed (unmapped window)")
                if adopt:
                    runtime = review_adoption_runtime(discord)
                    if label == "Prepared":
                        assert state is not None
                        require(
                            state.get("prepared_loader") == loader_fingerprint(discord.resources),
                            "Prepared loader changed; inspect before completing installation",
                        )
                    require(
                        state is None
                        or label == "Prepared"
                        or state.get("adopted_runtime") is not None,
                        "Cannot adopt over another active installation mode",
                    )
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
                    print("Loader: verified existing Vencord")
                    print(f"Adoption runtime: {runtime}\nOriginal runtime backup: {backup}")
                    print("Original _app.asar matches OSTree: True")
                    print(f"Upstream removal consent required: {removed}")
                else:
                    require(
                        loader_status(discord.resources) == "managed"
                        or (
                            discord.scope == "user"
                            and os.access(discord.resources, os.W_OK)
                            and verified_clean(discord)
                        ),
                        "Unknown or protected loader; install Vencord first",
                    )
                    print(f"Loader: {loader_status(discord.resources)}")
                if label == "Prepared":
                    print(
                        "Prepared files are inactive. Quit Discord and run ./install.sh "
                        "to complete installation."
                    )
            else:
                artifact = args["artifact"]
                executable = args["executable"]
                require(
                    isinstance(artifact, Path) and isinstance(executable, Path),
                    "Invalid artifact paths",
                )
                assert isinstance(artifact, Path) and isinstance(executable, Path)
                _, digest = review_native(artifact.absolute(), executable.absolute())
                install(
                    paths,
                    discord,
                    artifact.absolute(),
                    executable.absolute(),
                    prepare=bool(args["prepare_only"]),
                    replace_launcher=bool(args["replace_launcher"]),
                    yes=bool(args["yes"]),
                    adopt=adopt,
                    allow_plugin_removal=bool(args["allow_plugin_removal"]),
                    reviewed_executable=digest,
                    repair=bool(args["repair"]),
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
