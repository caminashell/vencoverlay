"""Production artifact verification with synthetic compiler output and real Git archives."""

import json
import os
import shutil
import struct
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from scripts import flatpak_install
from scripts.frontend_artifacts import object_dict, object_list, read_json

ROOT = Path(__file__).resolve().parents[2]
PACKAGER = ROOT / "scripts/frontend_artifacts.py"
RENDERERS = ("renderer.js", "vencordDesktopRenderer.js")
MAINS = ("patcher.js", "vencordDesktopMain.js")
PRELOADS = ("preload.js", "vencordDesktopPreload.js")
CSS = ("renderer.css", "vencordDesktopRenderer.css")
MODULES = ("index.ts", "protocol.ts", "settings.ts", "voice.ts", "webcam.ts", "native.ts")
PREFIX = "src/userplugins/vencoverlay/"


def git(checkout: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(checkout), *args], text=True).strip()


@pytest.fixture
def production(tmp_path: Path) -> tuple[Path, Path, str, Path]:
    root = tmp_path / "project"
    checkout = tmp_path / "upstream"
    checkout.mkdir()
    root.mkdir()
    git(checkout, "init", "-q")
    git(checkout, "config", "user.email", "build@example.invalid")
    git(checkout, "config", "user.name", "Build test")
    for name, content in (
        ("LICENSE", "upstream licence\n"),
        ("package.json", '{"version":"1.15.19"}\n'),
        ("pnpm-lock.yaml", "lock\n"),
        ("pnpm-workspace.yaml", "workspace\n"),
        (".gitignore", "dist/\nsrc/userplugins/\n"),
    ):
        (checkout / name).write_text(content, encoding="utf-8")
    git(checkout, "add", ".")
    git(checkout, "commit", "--no-gpg-sign", "-qm", "fixture")
    revision = git(checkout, "rev-parse", "HEAD")
    (root / "LICENSE").write_text("project licence\n", encoding="utf-8")
    (root / "pyproject.toml").write_text('[project]\nversion = "0.1.0.dev0"\n', encoding="utf-8")
    (root / "scripts").mkdir()
    shutil.copy2(ROOT / "scripts/frontend-build.mjs", root / "scripts")
    maintained = root / "plugin/vencoverlay"
    maintained.mkdir(parents=True)
    for module in MODULES:
        (maintained / module).write_text(f"// {module}\nexport {{}};\n", encoding="utf-8")
    shutil.copytree(maintained, checkout / PREFIX)
    dist = checkout / "dist"
    dist.mkdir()
    header = (
        f"// Vencord {revision[:7]}\n"
        "// Standalone: true\n// Platform: Universal\n// Updater Disabled: true\n"
    )
    for name in (*RENDERERS, *MAINS, *PRELOADS, *CSS):
        modules: tuple[str, ...]
        if name in RENDERERS:
            modules = MODULES[:-1]
            code = header + "globalThis.VencOverlayDebug=()=>0;\n"
        elif name in MAINS:
            modules = ("native.ts", "protocol.ts")
            code = header + "exports.VencOverlay=()=>0;\n"
        elif name in PRELOADS:
            modules = ()
            code = header + "exports.a=1;\n"
        else:
            modules = ()
            code = "a{color:red}\n"
        if name == "renderer.js":
            code += "/*! For license information please see renderer.js.LEGAL.txt */\n"
        (dist / name).write_text(code, encoding="utf-8")
        mapping = {
            "version": 3,
            "sources": ["../" + PREFIX + module for module in modules],
            "sourcesContent": [
                (maintained / module).read_text(encoding="utf-8") for module in modules
            ],
            "mappings": "AAAA",
        }
        (dist / (name + ".map")).write_text(json.dumps(mapping), encoding="utf-8")
    (dist / "renderer.js.LEGAL.txt").write_text("linked licence\n", encoding="utf-8")
    (root / "dist").mkdir()
    return root, checkout, revision, root / "dist/vencoverlay-vencord.zip"


def package(production: tuple[Path, Path, str, Path]) -> subprocess.CompletedProcess[str]:
    root, checkout, revision, output = production
    return subprocess.run(
        [
            sys.executable,
            str(PACKAGER),
            "package",
            "--root",
            str(root),
            "--checkout",
            str(checkout),
            "--revision",
            revision,
            "--node",
            "v22.16.0",
            "--pnpm",
            "12.15.0",
            "--timestamp",
            "1234567890000",
            "--output",
            str(output),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )


def verify(output: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(PACKAGER), "verify", str(output)],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )


def test_production_archive_has_separate_runtime_debug_and_source(
    production: tuple[Path, Path, str, Path],
) -> None:
    result = package(production)
    assert result.returncode == 0, result.stderr
    output = production[-1]
    assert verify(output).returncode == 0
    with zipfile.ZipFile(output) as archive:
        names = set(archive.namelist())
        assert "runtime/patcher.js" in names
        assert "runtime/vencordDesktopMain.js" in names
        assert "runtime/renderer.js.LEGAL.txt" in names
        assert "runtime/LICENSE" in names
        assert "debug/renderer.js.map" in names
        assert "source/vencord-source.zip" in names
        assert "manifest.json" in names
        assert not any(name.startswith("runtime/") and name.endswith(".map") for name in names)


@pytest.mark.parametrize(
    ("filename", "error"),
    [
        ("renderer.js", "Missing or unsafe file"),
        ("renderer.js.LEGAL.txt", "Missing linked notice"),
        ("renderer.css.map", "Missing or unsafe file"),
    ],
)
def test_missing_build_output_preserves_previous_release(
    production: tuple[Path, Path, str, Path], filename: str, error: str
) -> None:
    _, checkout, _, output = production
    output.write_bytes(b"previous release")
    (checkout / "dist" / filename).unlink()
    result = package(production)
    assert result.returncode != 0
    assert error in result.stderr
    assert output.read_bytes() == b"previous release"


def test_stale_plugin_map_is_rejected(production: tuple[Path, Path, str, Path]) -> None:
    _, checkout, _, output = production
    mapping = checkout / "dist/renderer.js.map"
    mapping.write_text(
        mapping.read_text(encoding="utf-8").replace("// index.ts", "// outdated"),
        encoding="utf-8",
    )
    result = package(production)
    assert result.returncode != 0
    assert "Stale plugin source" in result.stderr
    assert not output.exists()


def test_invalid_build_banner_is_rejected(production: tuple[Path, Path, str, Path]) -> None:
    _, checkout, _, output = production
    bundle = checkout / "dist/patcher.js"
    bundle.write_bytes(
        bundle.read_bytes().replace(b"Updater Disabled: true", b"Updater Disabled: false")
    )
    result = package(production)
    assert result.returncode != 0
    assert "Unexpected build banner" in result.stderr
    assert not output.exists()


def test_corrupted_artifact_fails_independent_verification(
    production: tuple[Path, Path, str, Path],
) -> None:
    result = package(production)
    assert result.returncode == 0, result.stderr
    output = production[-1]
    with zipfile.ZipFile(output) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
    files["runtime/renderer.js"] += b"// modified"
    with zipfile.ZipFile(output, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    result = verify(output)
    assert result.returncode != 0
    assert "Artifact checksum mismatch" in result.stderr


@pytest.mark.parametrize("minified", [True, False])
def test_production_compiler_configuration_guard(
    request: pytest.FixtureRequest, tmp_path: Path, minified: bool
) -> None:
    if not request.config.getoption("--frontend"):
        pytest.skip("requires explicit --frontend opt-in")
    scripts = tmp_path / "scripts/build"
    scripts.mkdir(parents=True)
    (scripts / "common.mjs").write_text(
        "export const watch=false, IS_DEV=false, IS_REPORTER=false;\n"
        'export const IS_STANDALONE=process.argv.includes("--standalone");\n'
        'export const IS_UPDATER_DISABLED=process.argv.includes("--disable-updater");\n'
        f"export const commonOpts={{bundle:true,minify:{str(minified).lower()},"
        'sourcemap:"external",legalComments:"linked"};\n',
        encoding="utf-8",
    )
    (scripts / "build.mjs").write_text('console.log("fixture compiled");\n', encoding="utf-8")
    result = subprocess.run(
        ["node", str(ROOT / "scripts/frontend-build.mjs")],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    if minified:
        assert result.returncode == 0, result.stderr
        assert "fixture compiled" in result.stdout
    else:
        assert result.returncode != 0
        assert "Unexpected Vencord production build configuration" in result.stderr
        assert "fixture compiled" not in result.stdout


@pytest.fixture
def flatpak_installation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[flatpak_install.Paths, flatpak_install.Discord, Path]:
    deployment = tmp_path / "flatpak/app/com.discordapp.Discord/x86_64/stable/commit"
    discord = flatpak_install.Discord("user", deployment, "x86_64", "a" * 64)
    discord.resources.mkdir(parents=True)
    (discord.resources / "app.asar").write_bytes(b"original" * 200_000)
    discord.exported_desktop.parent.mkdir(parents=True)
    discord.exported_desktop.write_text(
        "[Desktop Entry]\nName=Discord\nDBusActivatable=true\n"
        "Exec=/usr/bin/flatpak run --branch=stable --arch=x86_64 "
        "--command=com.discordapp.Discord --file-forwarding com.discordapp.Discord @@u %U @@\n"
        "[Desktop Action Test]\nExec=/usr/bin/flatpak run --branch=stable "
        "com.discordapp.Discord --start-minimized\n",
        encoding="utf-8",
    )
    paths = flatpak_install.Paths(
        tmp_path / "home/share/vencoverlay/flatpak",
        tmp_path / "home/discord/data/vencoverlay",
        tmp_path / "home/share/applications/com.discordapp.Discord.desktop",
        tmp_path / "vencoverlay-discord.lock",
    )
    paths.desktop.parent.mkdir(parents=True)
    paths.desktop.write_bytes(b"original local desktop\n")
    paths.desktop.chmod(0o751)
    executable = tmp_path / "fake-executable"
    executable.write_text('#!/bin/sh\nprintf "VencOverlay 0.1.0.dev0\\n"\n', encoding="utf-8")
    executable.chmod(0o755)
    monkeypatch.setattr(flatpak_install, "discord_running", lambda: False)

    def detect(_scope: str | None) -> flatpak_install.Discord:
        return discord

    def clean(target: flatpak_install.Discord) -> bool:
        return flatpak_install.loader_status(target.resources) == "unpatched-candidate"

    monkeypatch.setattr(flatpak_install, "detect", detect)
    monkeypatch.setattr(flatpak_install, "verified_clean", clean)
    return paths, discord, executable


def test_install_managed_flatpak_and_restore(
    production: tuple[Path, Path, str, Path],
    flatpak_installation: tuple[flatpak_install.Paths, flatpak_install.Discord, Path],
) -> None:
    assert package(production).returncode == 0
    paths, discord, executable = flatpak_installation
    original = discord.resources / "app.asar"
    original_bytes, inode = original.read_bytes(), original.stat().st_ino
    settings = paths.sandbox.parent / "Vencord/settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_bytes(b'{"plugins":{"Unrelated":true}}')
    flatpak_install.install(
        paths,
        discord,
        production[3],
        executable,
        prepare=False,
        replace_launcher=True,
        yes=True,
    )
    assert flatpak_install.loader_status(discord.resources) == "managed"
    assert (discord.resources / "_app.asar").stat().st_ino == inode
    assert b"@@u %U @@" in paths.desktop.read_bytes()
    assert b"DBusActivatable=false" in paths.desktop.read_bytes()
    assert b"--start-minimized" in paths.desktop.read_bytes()
    assert (paths.host / "original.desktop").read_bytes() == b"original local desktop\n"
    assert (paths.sandbox / "runtime/patcher.js").is_file()
    assert not (paths.sandbox / "runtime/vencordDesktopMain.js").exists()
    assert not list(paths.sandbox.rglob("*.map"))
    assert (paths.host / "vencoverlay-vencord.zip").is_file()
    # Upgrade/repeated install preserves the original local launcher and original ASAR.
    flatpak_install.install(
        paths,
        discord,
        production[3],
        executable,
        prepare=False,
        replace_launcher=False,
        yes=True,
    )
    flatpak_install.uninstall(paths, True)
    assert original.read_bytes() == original_bytes and original.stat().st_ino == inode
    assert not (discord.resources / "_app.asar").exists()
    assert paths.desktop.read_bytes() == b"original local desktop\n"
    assert paths.desktop.stat().st_mode & 0o777 == 0o751
    assert settings.read_bytes() == b'{"plugins":{"Unrelated":true}}'
    assert not paths.host.exists() and not paths.sandbox.exists()
    flatpak_install.uninstall(paths, True)


def test_system_prepare_external_activation_and_restore(
    production: tuple[Path, Path, str, Path],
    flatpak_installation: tuple[flatpak_install.Paths, flatpak_install.Discord, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert package(production).returncode == 0
    paths, user_discord, executable = flatpak_installation
    discord = flatpak_install.Discord(
        "system", user_discord.deployment, user_discord.arch, user_discord.commit
    )

    def detect(_scope: str | None) -> flatpak_install.Discord:
        return discord

    monkeypatch.setattr(flatpak_install, "detect", detect)
    app = discord.resources / "app.asar"
    original = app.read_bytes()
    with pytest.raises(ValueError, match="protected loader"):
        flatpak_install.install(
            paths,
            discord,
            production[3],
            executable,
            prepare=False,
            replace_launcher=True,
            yes=True,
        )
    assert not paths.host.exists()
    flatpak_install.install(
        paths,
        discord,
        production[3],
        executable,
        prepare=True,
        replace_launcher=False,
        yes=True,
    )
    assert app.read_bytes() == original
    assert paths.desktop.read_bytes() == b"original local desktop\n"
    # Simulate an external administrator installing the exact staged loader.
    app.rename(discord.resources / "_app.asar")
    shutil.copyfile(paths.host / "app.asar", app)
    flatpak_install.install(
        paths,
        discord,
        production[3],
        executable,
        prepare=False,
        replace_launcher=True,
        yes=True,
    )
    with pytest.raises(ValueError, match="externally installed"):
        flatpak_install.uninstall(paths, True)
    (discord.resources / "_app.asar").replace(app)
    flatpak_install.uninstall(paths, True)
    assert app.read_bytes() == original
    assert paths.desktop.read_bytes() == b"original local desktop\n"


def test_foreign_loader_and_launcher_are_never_overwritten(
    production: tuple[Path, Path, str, Path],
    flatpak_installation: tuple[flatpak_install.Paths, flatpak_install.Discord, Path],
) -> None:
    assert package(production).returncode == 0
    paths, discord, executable = flatpak_installation
    with pytest.raises(ValueError, match="Existing local Discord launcher"):
        flatpak_install.install(
            paths,
            discord,
            production[3],
            executable,
            prepare=False,
            replace_launcher=False,
            yes=True,
        )
    app = discord.resources / "app.asar"
    app.rename(discord.resources / "_app.asar")
    app.write_bytes(b"foreign Vencord loader")
    with pytest.raises(ValueError, match="protected loader"):
        flatpak_install.install(
            paths,
            discord,
            production[3],
            executable,
            prepare=False,
            replace_launcher=True,
            yes=True,
        )
    flatpak_install.install(
        paths,
        discord,
        production[3],
        executable,
        prepare=True,
        replace_launcher=False,
        yes=True,
    )
    assert app.read_bytes() == b"foreign Vencord loader"
    flatpak_install.uninstall(paths, True)
    assert app.read_bytes() == b"foreign Vencord loader"
    assert paths.desktop.read_bytes() == b"original local desktop\n"


def test_flatpak_install_rolls_back_after_loader_publication(
    production: tuple[Path, Path, str, Path],
    flatpak_installation: tuple[flatpak_install.Paths, flatpak_install.Discord, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert package(production).returncode == 0
    paths, discord, executable = flatpak_installation
    app = discord.resources / "app.asar"
    original = app.read_bytes()
    replace = flatpak_install.Transaction.replace

    def fail_desktop(tx: flatpak_install.Transaction, target: Path, staged: Path | None) -> None:
        if target == paths.desktop:
            raise OSError("simulated launcher failure")
        replace(tx, target, staged)

    monkeypatch.setattr(flatpak_install.Transaction, "replace", fail_desktop)
    with pytest.raises(OSError, match="simulated launcher failure"):
        flatpak_install.install(
            paths,
            discord,
            production[3],
            executable,
            prepare=False,
            replace_launcher=True,
            yes=True,
        )
    assert app.read_bytes() == original
    assert not (discord.resources / "_app.asar").exists()
    assert not paths.host.exists() and not paths.sandbox.exists()
    assert paths.desktop.read_bytes() == b"original local desktop\n"


def test_flatpak_rejects_running_or_unmanaged_installation(
    production: tuple[Path, Path, str, Path],
    flatpak_installation: tuple[flatpak_install.Paths, flatpak_install.Discord, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert package(production).returncode == 0
    paths, discord, executable = flatpak_installation
    monkeypatch.setattr(flatpak_install, "discord_running", lambda: True)
    with pytest.raises(ValueError, match="Quit Discord"):
        flatpak_install.install(
            paths,
            discord,
            production[3],
            executable,
            prepare=True,
            replace_launcher=False,
            yes=True,
        )
    monkeypatch.setattr(flatpak_install, "discord_running", lambda: False)
    paths.sandbox.mkdir(parents=True)
    foreign = paths.sandbox / "unrelated"
    foreign.write_bytes(b"do not remove")
    with pytest.raises(ValueError, match="Unmanaged sandbox"):
        flatpak_install.install(
            paths,
            discord,
            production[3],
            executable,
            prepare=True,
            replace_launcher=False,
            yes=True,
        )
    assert foreign.read_bytes() == b"do not remove"
    assert not paths.host.exists()


def test_flatpak_uninstall_refuses_changed_files(
    production: tuple[Path, Path, str, Path],
    flatpak_installation: tuple[flatpak_install.Paths, flatpak_install.Discord, Path],
) -> None:
    assert package(production).returncode == 0
    paths, discord, executable = flatpak_installation
    flatpak_install.install(
        paths,
        discord,
        production[3],
        executable,
        prepare=False,
        replace_launcher=True,
        yes=True,
    )
    original = discord.resources / "_app.asar"
    original.write_bytes(b"changed original")
    with pytest.raises(ValueError, match="Original Discord archive has changed"):
        flatpak_install.uninstall(paths, True)
    assert paths.host.exists() and paths.sandbox.exists()
    assert flatpak_install.loader_status(discord.resources) == "managed"


def test_managed_loader_contains_original_application_fallback() -> None:
    data = flatpak_install.loader_bytes()
    length, header_size, object_size, json_size = struct.unpack("<IIII", data[:16])
    assert length == 4 and header_size == object_size + 4
    header = read_json(data[16 : 16 + json_size])
    payload = data[8 + header_size :]
    files = object_dict(header["files"])
    entry = object_dict(files["index.js"])
    assert entry["offset"] == "0"
    size = entry["size"]
    assert isinstance(size, int)
    index = payload[:size]
    assert flatpak_install.PATCHER.encode() in index
    assert b"_app.asar" in index and b"setAppPath" in index
    assert read_json(payload[len(index) :]) == {"name": "discord", "main": "index.js"}


def test_flatpak_launcher_owns_overlay_lifetime_and_preserves_arguments(
    flatpak_installation: tuple[flatpak_install.Paths, flatpak_install.Discord, Path],
    tmp_path: Path,
) -> None:
    paths, discord, _ = flatpak_installation
    paths.host.mkdir(parents=True)
    wrapper = paths.host / "discord-with-overlay"
    wrapper.write_bytes(flatpak_install.launcher(discord))
    wrapper.chmod(0o755)
    # Use actual Bash child management and a fake Flatpak executable, not real Discord.
    mock_bin = tmp_path / "bin"
    mock_bin.mkdir()
    mock_flatpak = mock_bin / "flatpak"
    mock_flatpak.write_text(
        "#!/bin/bash\nset -eu\n"
        'if [[ "$1" == ps ]]; then\n'
        '  if [[ -f "$TEST_ROOT/external" ]]; then echo com.discordapp.Discord; fi\n'
        "  exit 0\nfi\n"
        'printf "%s\\n" "$@" > "$TEST_ROOT/args"\n'
        'if [[ ! -f "$TEST_ROOT/external" ]]; then\n'
        "  for ((i=0;i<100;i++)); do\n"
        '    [[ ! -f "$TEST_ROOT/overlay-start" ]] || break\n'
        "    sleep 0.01\n  done\nfi\n",
        encoding="utf-8",
    )
    mock_flatpak.chmod(0o755)
    overlay = paths.host / "vencoverlay"
    overlay.write_text(
        "#!/bin/bash\n"
        "trap 'touch \"$TEST_ROOT/overlay-stop\"; exit 0' TERM\n"
        'touch "$TEST_ROOT/overlay-start"\n'
        "while true; do sleep 0.05; done\n",
        encoding="utf-8",
    )
    overlay.chmod(0o755)
    environment = {
        **os.environ,
        "PATH": f"{mock_bin}:{os.environ['PATH']}",
        "TEST_ROOT": str(tmp_path),
        "XDG_RUNTIME_DIR": str(tmp_path),
    }
    result = subprocess.run(
        [str(wrapper), "@@u", "https://example.invalid/a b", "@@"],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "overlay-start").exists() and (tmp_path / "overlay-stop").exists()
    assert (tmp_path / "args").read_text().splitlines() == [
        "run",
        "--user",
        "--branch=stable",
        "--arch=x86_64",
        "--command=com.discordapp.Discord",
        "--file-forwarding",
        "com.discordapp.Discord",
        "@@u",
        "https://example.invalid/a b",
        "@@",
    ]
    (tmp_path / "overlay-start").unlink()
    (tmp_path / "overlay-stop").unlink()
    (tmp_path / "external").touch()
    result = subprocess.run(
        [str(wrapper), "--start-minimized"],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert not (tmp_path / "overlay-start").exists()
    assert (tmp_path / "args").read_text().splitlines()[-1] == "--start-minimized"


def test_flatpak_scope_detection_is_explicit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for scope in ("user", "system"):
        (tmp_path / scope / "files/discord/resources").mkdir(parents=True)

    def command(*args: str) -> subprocess.CompletedProcess[str]:
        scope = args[2].removeprefix("--")
        value = {
            "--show-location": str(tmp_path / scope),
            "--show-ref": "app/com.discordapp.Discord/x86_64/stable",
            "--show-commit": "a" * 64,
        }[args[3]]
        return subprocess.CompletedProcess(args, 0, value + "\n", "")

    monkeypatch.setattr(flatpak_install, "command", command)
    with pytest.raises(ValueError, match="Both user and system"):
        flatpak_install.detect(None)
    assert flatpak_install.detect("user").deployment == tmp_path / "user"
    assert flatpak_install.detect("system").scope == "system"


def test_flatpak_clean_verification_matches_commit_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "flatpak"
    (root / "repo").mkdir(parents=True)
    deployment = root / "app/com.discordapp.Discord/x86_64/stable/commit"
    discord = flatpak_install.Discord("user", deployment, "x86_64", "a" * 64)
    discord.resources.mkdir(parents=True)
    app = discord.resources / "app.asar"
    original = tmp_path / "original"
    original.write_bytes(b"original" * 200_000)
    shutil.copyfile(original, app)
    mock_bin = tmp_path / "bin"
    mock_bin.mkdir()
    ostree = mock_bin / "ostree"
    ostree.write_text('#!/bin/sh\ncat "$TEST_ORIGINAL"\n', encoding="utf-8")
    ostree.chmod(0o755)
    monkeypatch.setenv("PATH", f"{mock_bin}:{os.environ['PATH']}")
    monkeypatch.setenv("TEST_ORIGINAL", str(original))
    assert flatpak_install.verified_clean(discord)
    with app.open("ab") as stream:
        stream.write(b"modified")
    assert not flatpak_install.verified_clean(discord)


def existing_loader(patcher: Path, *, suffix: str = "") -> bytes:
    index = (f"require({json.dumps(str(patcher))})" + suffix).encode()
    package = b'{\n\t"name": "discord",\n\t"main": "index.js"\n}'
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


@pytest.fixture
def adoption(
    production: tuple[Path, Path, str, Path],
    flatpak_installation: tuple[flatpak_install.Paths, flatpak_install.Discord, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[flatpak_install.Paths, flatpak_install.Discord, Path, Path]:
    paths, user, executable = flatpak_installation
    discord = flatpak_install.Discord("system", user.deployment, user.arch, user.commit)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    runtime = Path.home() / ".config/Vencord/dist"
    runtime.mkdir(parents=True)
    for name in flatpak_install.BUNDLES:
        (runtime / name).write_bytes(
            f"// Vencord {production[2][:7]}\noriginal runtime bytes".encode()
        )
    (runtime / "renderer.js.map").write_text(
        json.dumps(
            {
                "sources": [
                    "../src/plugins/keep/index.ts",
                    "../src/plugins/removed/index.ts",
                ]
            }
        )
    )
    (runtime / "patcher.js.map").write_text(json.dumps({"sources": ["../src/main/patcher.ts"]}))
    (runtime / "package.json").write_bytes(b"{}")
    (runtime / "extra.txt").write_bytes(b"preserve me")
    app = discord.resources / "app.asar"
    app.rename(discord.resources / "_app.asar")
    app.write_bytes(existing_loader(runtime / "patcher.js"))
    original_hash = flatpak_install.file_hash(discord.resources / "_app.asar")

    def matches(target: flatpak_install.Discord, name: str) -> bool:
        return flatpak_install.file_hash(target.resources / name) == original_hash

    def detect(_scope: str | None) -> flatpak_install.Discord:
        return discord

    def command(*args: str) -> subprocess.CompletedProcess[str]:
        if args[0] == "flatpak":
            return subprocess.CompletedProcess(
                args, 0, flatpak_install.file_hash(runtime / "patcher.js") + "  patcher.js\n", ""
            )
        return subprocess.run(args, capture_output=True, text=True, check=False, timeout=30)

    monkeypatch.setattr(flatpak_install, "matches_original", matches)
    monkeypatch.setattr(flatpak_install, "detect", detect)
    monkeypatch.setattr(flatpak_install, "command", command)
    for name in RENDERERS:
        mapping = production[1] / "dist" / (name + ".map")
        value = read_json(mapping.read_bytes())
        value["sources"] = [
            *object_list(value["sources"]),
            "../src/plugins/keep/index.ts",
            "../src/plugins/added/index.ts",
        ]
        value["sourcesContent"] = [*object_list(value["sourcesContent"]), "", ""]
        mapping.write_text(json.dumps(value))
    assert package(production).returncode == 0
    return paths, discord, executable, runtime


def adopt_release(
    production: tuple[Path, Path, str, Path],
    adoption: tuple[flatpak_install.Paths, flatpak_install.Discord, Path, Path],
    *,
    allow: bool = True,
) -> None:
    paths, discord, executable, _ = adoption
    flatpak_install.install(
        paths,
        discord,
        production[3],
        executable,
        prepare=False,
        replace_launcher=True,
        yes=True,
        adopt=True,
        allow_plugin_removal=allow,
    )


def test_adoption_upgrade_and_exact_restoration(
    production: tuple[Path, Path, str, Path],
    adoption: tuple[flatpak_install.Paths, flatpak_install.Discord, Path, Path],
) -> None:
    paths, discord, _, runtime = adoption
    original = flatpak_install.inventory(runtime)
    inode = runtime.stat().st_ino
    (runtime / "extra.txt").chmod(0o640)
    loader = flatpak_install.loader_fingerprint(discord.resources)
    settings = runtime.parent / "settings.json"
    settings.write_bytes(b'{"preserve":true}')
    with pytest.raises(ValueError, match="allow-plugin-removal"):
        adopt_release(production, adoption, allow=False)
    assert flatpak_install.inventory(runtime) == original
    adopt_release(production, adoption)
    backup = flatpak_install.adoption_backup(runtime)
    assert backup.stat().st_ino == inode
    assert flatpak_install.inventory(backup) == original
    assert not paths.sandbox.exists()
    assert not (runtime / "extra.txt").exists()
    assert not list(runtime.glob("*.map"))
    assert flatpak_install.loader_fingerprint(discord.resources) == loader
    adopt_release(production, adoption)
    assert backup.stat().st_ino == inode
    flatpak_install.uninstall(paths, True)
    assert runtime.stat().st_ino == inode
    assert flatpak_install.inventory(runtime) == original
    assert (runtime / "extra.txt").stat().st_mode & 0o777 == 0o640
    assert settings.read_bytes() == b'{"preserve":true}'
    assert not backup.exists() and not paths.host.exists()
    assert paths.desktop.read_bytes() == b"original local desktop\n"
    assert flatpak_install.loader_fingerprint(discord.resources) == loader


def test_adoption_refuses_unknown_loader_and_modified_original(
    production: tuple[Path, Path, str, Path],
    adoption: tuple[flatpak_install.Paths, flatpak_install.Discord, Path, Path],
) -> None:
    paths, discord, _, runtime = adoption
    app = discord.resources / "app.asar"
    app.write_bytes(existing_loader(runtime / "patcher.js", suffix=";alert(1)"))
    with pytest.raises(ValueError, match="Unsupported ASAR loader code"):
        adopt_release(production, adoption)
    app.write_bytes(existing_loader(runtime / "patcher.js"))
    (discord.resources / "_app.asar").write_bytes(b"modified")
    with pytest.raises(ValueError, match="OSTree verification"):
        adopt_release(production, adoption)
    assert not paths.host.exists()


def test_adoption_refuses_custom_plugins_and_existing_backup(
    production: tuple[Path, Path, str, Path],
    adoption: tuple[flatpak_install.Paths, flatpak_install.Discord, Path, Path],
) -> None:
    paths, _, _, runtime = adoption
    mapping = runtime / "patcher.js.map"
    original = mapping.read_bytes()
    mapping.write_text(json.dumps({"sources": ["../src/userplugins/custom/native.ts"]}))
    with pytest.raises(ValueError, match="Existing custom plugins"):
        adopt_release(production, adoption)
    mapping.write_bytes(original)
    backup = flatpak_install.adoption_backup(runtime)
    backup.mkdir()
    with pytest.raises(ValueError, match="backup already exists"):
        adopt_release(production, adoption)
    assert not paths.host.exists()


def test_adoption_rollback_and_modified_backup_protection(
    production: tuple[Path, Path, str, Path],
    adoption: tuple[flatpak_install.Paths, flatpak_install.Discord, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths, _, _, runtime = adoption
    inode = runtime.stat().st_ino
    original = flatpak_install.inventory(runtime)
    replace = flatpak_install.Transaction.replace

    def fail_desktop(tx: flatpak_install.Transaction, target: Path, staged: Path | None) -> None:
        if target == paths.desktop:
            raise OSError("simulated launcher failure")
        replace(tx, target, staged)

    with monkeypatch.context() as patch:
        patch.setattr(flatpak_install.Transaction, "replace", fail_desktop)
        with pytest.raises(OSError, match="simulated launcher failure"):
            adopt_release(production, adoption)
    backup = flatpak_install.adoption_backup(runtime)
    assert runtime.stat().st_ino == inode
    assert flatpak_install.inventory(runtime) == original
    assert not backup.exists() and not paths.host.exists()
    adopt_release(production, adoption)
    (backup / "extra.txt").write_bytes(b"changed backup")
    with pytest.raises(ValueError, match="Original runtime backup changed"):
        flatpak_install.uninstall(paths, True)
    assert paths.host.exists() and runtime.exists() and backup.exists()


def test_adoption_refuses_sandbox_mismatch_and_runtime_changes(
    production: tuple[Path, Path, str, Path],
    adoption: tuple[flatpak_install.Paths, flatpak_install.Discord, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths, _, _, runtime = adoption

    def inaccessible(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, 1, "", "not accessible")

    with monkeypatch.context() as patch:
        patch.setattr(flatpak_install, "command", inaccessible)
        with pytest.raises(ValueError, match="not accessible in Discord sandbox"):
            adopt_release(production, adoption)
    assert not paths.host.exists()
    adopt_release(production, adoption)
    (runtime / "renderer.js").write_bytes(b"changed runtime")
    with pytest.raises(ValueError, match="Managed runtime files changed"):
        adopt_release(production, adoption)
    with pytest.raises(ValueError, match="Managed runtime files changed"):
        flatpak_install.uninstall(paths, True)
    assert flatpak_install.adoption_backup(runtime).exists()


def test_adoption_rejects_loader_changed_after_install(
    production: tuple[Path, Path, str, Path],
    adoption: tuple[flatpak_install.Paths, flatpak_install.Discord, Path, Path],
) -> None:
    paths, discord, _, runtime = adoption
    adopt_release(production, adoption)
    # Semantically equivalent but byte-different loader still requires inspection.
    (discord.resources / "app.asar").write_bytes(
        existing_loader(runtime / "patcher.js", suffix=";")
    )
    with pytest.raises(ValueError, match="Existing loader changed during staging"):
        adopt_release(production, adoption)
    with pytest.raises(ValueError, match="Unknown loader"):
        flatpak_install.uninstall(paths, True)
    assert paths.host.exists() and flatpak_install.adoption_backup(runtime).exists()


def test_adoption_rejects_unknown_runtime(
    production: tuple[Path, Path, str, Path],
    adoption: tuple[flatpak_install.Paths, flatpak_install.Discord, Path, Path],
) -> None:
    paths, _, _, runtime = adoption
    for name in ("renderer.js", "patcher.js", "preload.js"):
        (runtime / name).write_bytes(b"// Vencord ffffffe\nunknown runtime")
    original = flatpak_install.inventory(runtime)
    with pytest.raises(ValueError, match="Unknown existing Vencord revision"):
        adopt_release(production, adoption)
    assert not paths.host.exists()
    assert not flatpak_install.adoption_backup(runtime).exists()
    assert flatpak_install.inventory(runtime) == original


def test_vencord_version_order_rejects_downgrade_and_ambiguous_revision() -> None:
    old = ("8d64c8be7e68616f8f116a6fff9480051aa2a0c5", "1.15.15")
    new = ("d086992fb674299a2fa05ac6432edc3a777389f3", "1.15.19")
    flatpak_install.check_version_order(old, new)
    flatpak_install.check_version_order(new, new)
    with pytest.raises(ValueError, match="downgrade refused"):
        flatpak_install.check_version_order(new, old)
    with pytest.raises(ValueError, match="cannot establish ordering"):
        flatpak_install.check_version_order(new, (old[0], new[1]))


def test_foreign_bundle_revision_disagreement(tmp_path: Path) -> None:
    for name in ("renderer.js", "patcher.js", "preload.js"):
        (tmp_path / name).write_bytes(b"// Vencord d086992\n")
    with pytest.raises(ValueError, match="downgrade refused"):
        flatpak_install.check_foreign_version(
            tmp_path, ("8d64c8be7e68616f8f116a6fff9480051aa2a0c5", "1.15.15")
        )
    (tmp_path / "preload.js").write_bytes(b"// Vencord 8d64c8b\n")
    with pytest.raises(ValueError, match="bundle revisions disagree"):
        flatpak_install.check_foreign_version(
            tmp_path, ("d086992fb674299a2fa05ac6432edc3a777389f3", "1.15.19")
        )
