"""Production artifact verification with synthetic compiler output and real Git archives."""

import io
import json
import os
import shutil
import struct
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

from scripts import flatpak_install, native_artifacts, release_licences, release_package
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

    def fake_probe(path: Path, version: str) -> None:
        assert path.read_bytes() == executable.read_bytes()
        assert version == "0.1.0.dev0"

    monkeypatch.setattr(native_artifacts, "probe", fake_probe)

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
        adopt=False,
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
        adopt=False,
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
            adopt=False,
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
        adopt=False,
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
        adopt=False,
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
            adopt=False,
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
            adopt=False,
        )
    flatpak_install.install(
        paths,
        discord,
        production[3],
        executable,
        prepare=True,
        replace_launcher=False,
        yes=True,
        adopt=False,
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
            adopt=False,
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
    monkeypatch.setattr(flatpak_install, "DISCORD_EXIT_GRACE_SECONDS", 0.0)
    with pytest.raises(ValueError, match="Quit Discord"):
        flatpak_install.install(
            paths,
            discord,
            production[3],
            executable,
            prepare=True,
            replace_launcher=False,
            yes=True,
            adopt=False,
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
            adopt=False,
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
        adopt=False,
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
    with pytest.raises(ValueError, match="Existing loader changed"):
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


@pytest.fixture
def release_root(production: tuple[Path, Path, str, Path], monkeypatch: pytest.MonkeyPatch) -> Path:
    root, _, _, _ = production
    # Reuse the actual frontend verifier and real upstream source archive.
    assert package(production).returncode == 0
    for name in (
        ".gitignore",
        "README.md",
        "uv.lock",
        "build.sh",
        "release.sh",
        "setup.sh",
        "validate.sh",
        "version.sh",
        "install.sh",
        "uninstall.sh",
        "docs/release-installation.md",
        "scripts/release_package.py",
        "scripts/native_artifacts.py",
        "scripts/release_licences.py",
        "scripts/flatpak_install.py",
        "scripts/frontend_artifacts.py",
        "scripts/discord-with-overlay.sh",
        "scripts/prerequisites.sh",
        "scripts/frontend.sh",
        "src/vencoverlay/cli.py",
    ):
        dest = root / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, dest)
    # A synthetic ELF header is sufficient for testing the archive contract;
    # native execution and target checks are separately exercised on Fedora.
    binary = root / release_package.NATIVE
    header = bytearray(64)
    header[:7] = b"\x7fELF\x02\x01\x01"
    header[16:20] = b"\x03\x00\x3e\x00"
    binary.write_bytes(header)
    binary.chmod(0o755)
    # Simulated compiler provenance, not a real Fedora executable.
    build_env = {
        "platform": "linux",
        "architecture": "x86_64",
        "distribution": "fedora",
        "version": "43",
        "libc": "glibc 2.42",
    }
    monkeypatch.setattr(native_artifacts, "build_environment", lambda: build_env)
    (root / native_artifacts.METADATA).write_bytes(
        native_artifacts.metadata(
            binary.read_bytes(), "0.1.0.dev0", native_artifacts.source_hashes(root)
        )
    )
    git(root, "init", "-q")
    git(root, "config", "user.email", "build@example.invalid")
    git(root, "config", "user.name", "Build test")
    git(root, "add", ".")
    git(root, "commit", "--no-gpg-sign", "-qm", "fixture")

    def native_version(path: Path, version: str) -> None:
        assert path == binary
        assert version == "0.1.0.dev0"

    monkeypatch.setattr(release_package.platform, "system", lambda: "Linux")
    monkeypatch.setattr(release_package.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(
        release_package.platform,
        "freedesktop_os_release",
        lambda: {"ID": "fedora", "VERSION_ID": "43"},
    )
    monkeypatch.setattr(release_package, "check_native_version", native_version)
    return root


def test_installation_archive_is_self_contained(release_root: Path, tmp_path: Path) -> None:
    output = release_package.package(release_root)
    assert output.name == "vencoverlay-0.1.0.dev0-fedora-x86_64.tar.gz"
    release_package.verify_package(output)
    with tarfile.open(output, "r:gz") as archive:
        members = archive.getmembers()
        assert len(members) == 14
        archive.extractall(tmp_path / "extracted", filter="data")
    extracted = tmp_path / "extracted" / output.name.removesuffix(".tar.gz")
    assert not (extracted / "pyproject.toml").exists()
    assert not (extracted / ".git").exists()
    assert (extracted / "install.sh").stat().st_mode & 0o111
    check = subprocess.run(
        ["sha256sum", "-c", "SHA256SUMS"],
        cwd=extracted,
        capture_output=True,
        check=False,
        timeout=30,
    )
    assert check.returncode == 0, check.stderr
    for script in ("install.sh", "uninstall.sh"):
        result = subprocess.run(
            ["bash", script, "--help"],
            cwd=extracted,
            capture_output=True,
            check=False,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr
    manifest = read_json((extracted / release_package.MANIFEST).read_bytes())
    assert manifest["source_dirty"] is False
    assert manifest["assembly_environment"] == {
        "platform": "linux",
        "distribution": "fedora",
        "version": "43",
        "architecture": "x86_64",
    }
    assert manifest["test_baseline"] == release_package.TEST_BASELINE
    assert object_dict(manifest["native_build"])["version"] == "43"
    with zipfile.ZipFile(extracted / release_package.SOURCE) as archive:
        assert "release.sh" in archive.namelist()
        assert "src/vencoverlay/cli.py" in archive.namelist()
        assert not any(name.startswith("dist/") for name in archive.namelist())


@pytest.mark.parametrize("change", ["checksum", "path", "mode", "duplicate", "symlink"])
def test_installation_archive_rejects_tampering(
    release_root: Path, tmp_path: Path, change: str
) -> None:
    output = release_package.package(release_root)
    tampered = tmp_path / "tampered.tar.gz"
    with tarfile.open(output, "r:gz") as original, tarfile.open(tampered, "w:gz") as modified:
        for member in original:
            stream = original.extractfile(member)
            assert stream is not None
            data = stream.read()
            if member.name.endswith("/README.md"):
                if change == "checksum":
                    data = b"modified"
                    member.size = len(data)
                elif change == "path":
                    member.name = "../README.md"
                elif change == "mode":
                    member.mode = 0o777
                elif change == "symlink":
                    member.type = tarfile.SYMTYPE
                    member.linkname = "/etc/passwd"
                    member.size = 0
                elif change == "duplicate":
                    modified.addfile(member, io.BytesIO(data))
            modified.addfile(member, io.BytesIO(data))
    with pytest.raises(ValueError):
        release_package.verify_package(tampered)


def test_release_preserves_previous_archive_on_failure(release_root: Path) -> None:
    output = release_package.package(release_root)
    previous = output.read_bytes()
    with pytest.raises(ValueError, match="Release already exists"):
        release_package.package(release_root)
    (release_root / "plugin/vencoverlay/index.ts").write_text("// changed\n", encoding="utf-8")
    with pytest.raises(ValueError, match="different plugin source"):
        release_package.package(release_root, replace=True)
    assert output.read_bytes() == previous


def test_release_rejects_incompatible_native_artifact(release_root: Path) -> None:
    (release_root / release_package.NATIVE).write_bytes(b"not ELF")
    with pytest.raises(ValueError, match="Linux x86-64 ELF"):
        release_package.package(release_root)


def test_release_native_version_check(tmp_path: Path) -> None:
    binary = tmp_path / "version-probe"
    binary.write_text('#!/bin/sh\nprintf "%s\\n" "VencOverlay 0.1.0.dev0"\n', encoding="utf-8")
    binary.chmod(0o755)
    release_package.check_native_version(binary, "0.1.0.dev0")
    with pytest.raises(ValueError, match="Native executable version mismatch"):
        release_package.check_native_version(binary, "0.1.0.dev1")


@pytest.mark.parametrize("version", ["43", "44", "45", "rawhide"])
def test_release_accepts_fedora_versions(monkeypatch: pytest.MonkeyPatch, version: str) -> None:
    monkeypatch.setattr(release_package.platform, "system", lambda: "Linux")
    monkeypatch.setattr(release_package.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(
        release_package.platform,
        "freedesktop_os_release",
        lambda: {"ID": "fedora", "VERSION_ID": version},
    )
    assert release_package.check_host()["version"] == version


@pytest.mark.parametrize(
    ("system", "machine", "distribution", "version"),
    [
        ("Linux", "aarch64", "fedora", "44"),
        ("Linux", "x86_64", "ubuntu", "26.04"),
        ("Windows", "x86_64", "fedora", "44"),
        ("Linux", "x86_64", "fedora", ""),
        ("Linux", "x86_64", "fedora", "  "),
    ],
)
def test_release_rejects_invalid_assembly_host(
    monkeypatch: pytest.MonkeyPatch,
    system: str,
    machine: str,
    distribution: str,
    version: str,
) -> None:
    monkeypatch.setattr(release_package.platform, "system", lambda: system)
    monkeypatch.setattr(release_package.platform, "machine", lambda: machine)
    monkeypatch.setattr(
        release_package.platform,
        "freedesktop_os_release",
        lambda: {"ID": distribution, "VERSION_ID": version},
    )
    with pytest.raises(ValueError):
        release_package.check_host()


def test_fedora_44_package_keeps_test_baseline(
    release_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        release_package.platform,
        "freedesktop_os_release",
        lambda: {"ID": "fedora", "VERSION_ID": "44"},
    )
    output = release_package.package(release_root)
    assert output.name == "vencoverlay-0.1.0.dev0-fedora-x86_64.tar.gz"
    with tarfile.open(output, "r:gz") as archive:
        stream = archive.extractfile(f"{output.name.removesuffix('.tar.gz')}/release-manifest.json")
        assert stream is not None
        with stream:
            manifest = read_json(stream.read())
    assert object_dict(manifest["assembly_environment"])["version"] == "44"
    assert object_dict(manifest["test_baseline"])["version"] == "43"
    # Offline archive verification must not depend on the verifier's platform.
    monkeypatch.setattr(release_package.platform, "system", lambda: "Windows")
    release_package.verify_package(output)


@pytest.mark.parametrize("field", ["assembly_environment", "test_baseline"])
def test_release_rejects_inconsistent_platform_metadata(release_root: Path, field: str) -> None:
    output = release_package.package(release_root)
    files: dict[str, bytes] = {}
    with tarfile.open(output, "r:gz") as archive:
        for member in archive:
            stream = archive.extractfile(member)
            assert stream is not None
            with stream:
                files[member.name.split("/", 1)[1]] = stream.read()
    manifest = read_json(files[release_package.MANIFEST])
    metadata = object_dict(manifest[field])
    metadata["distribution"] = "ubuntu"
    manifest[field] = metadata
    files[release_package.MANIFEST] = release_package.json_bytes(manifest)
    files[release_package.SUMS] = release_package.checksum_text(
        {name: data for name, data in files.items() if name != release_package.SUMS}
    )
    with pytest.raises(ValueError):
        release_package.verify_payload(files, output.name.removesuffix(".tar.gz"))


def test_release_rejects_stale_native_source(release_root: Path) -> None:
    source = release_root / "src/vencoverlay/cli.py"
    source.write_bytes(source.read_bytes() + b"\n# changed after build\n")
    with pytest.raises(ValueError, match="different source inputs"):
        release_package.package(release_root)


def test_release_rejects_changed_native_bytes(release_root: Path) -> None:
    binary = release_root / native_artifacts.NATIVE
    binary.write_bytes(binary.read_bytes() + b"extra")
    with pytest.raises(ValueError, match="Native executable checksum mismatch"):
        release_package.package(release_root)


def test_native_probe_requires_graphics_success(
    release_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    def run(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args[1])
        if args[1] == "--version":
            return subprocess.CompletedProcess(args, 0, "VencOverlay 0.1.0.dev0\n", "")
        return subprocess.CompletedProcess(args, 1, "", "Cannot initialise GTK")

    monkeypatch.setattr(native_artifacts.subprocess, "run", run)
    with pytest.raises(ValueError, match="Cannot initialise GTK"):
        native_artifacts.probe(release_root / native_artifacts.NATIVE, "0.1.0.dev0")
    assert calls == ["--version", "--check-runtime"]


def test_native_probe_success(release_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def run(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        stdout = (
            "VencOverlay 0.1.0.dev0"
            if args[1] == "--version"
            else "VencOverlay runtime check passed"
        )
        return subprocess.CompletedProcess(args, 0, stdout + "\n", "")

    monkeypatch.setattr(native_artifacts.subprocess, "run", run)
    native_artifacts.probe(release_root / native_artifacts.NATIVE, "0.1.0.dev0")


def test_installer_review_native_source(
    release_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(flatpak_install, "ROOT", release_root)
    artifact = release_root / release_package.FRONTEND
    binary = release_root / native_artifacts.NATIVE
    assert flatpak_install.review_native(artifact, binary) == (
        "0.1.0.dev0",
        flatpak_install.file_hash(binary),
    )
    (release_root / "src/vencoverlay/cli.py").write_text("# changed\n")
    with pytest.raises(ValueError, match="Native build inputs changed"):
        flatpak_install.review_native(artifact, binary)


def test_staged_probe_failure_preserves_installation(
    production: tuple[Path, Path, str, Path],
    flatpak_installation: tuple[flatpak_install.Paths, flatpak_install.Discord, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert package(production).returncode == 0
    paths, discord, executable = flatpak_installation
    original = (discord.resources / "app.asar").read_bytes()

    def fail(_path: Path, _version: str) -> None:
        raise ValueError("Native compatibility check failed")

    monkeypatch.setattr(native_artifacts, "probe", fail)
    with pytest.raises(ValueError, match="Native compatibility check failed"):
        flatpak_install.install(
            paths,
            discord,
            production[3],
            executable,
            prepare=False,
            replace_launcher=True,
            yes=True,
            adopt=False,
        )
    assert (discord.resources / "app.asar").read_bytes() == original
    assert not paths.host.exists() and not paths.sandbox.exists()
    assert paths.desktop.read_bytes() == b"original local desktop\n"


def test_automatic_adoption_and_upgrade(
    production: tuple[Path, Path, str, Path],
    adoption: tuple[flatpak_install.Paths, flatpak_install.Discord, Path, Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    paths, discord, executable, runtime = adoption
    original = flatpak_install.inventory(runtime)
    inode = runtime.stat().st_ino
    assert flatpak_install.installation_state(paths, discord) == ("Not installed", None)
    for expected in ("Install", "Upgrade"):
        flatpak_install.install(
            paths,
            discord,
            production[3],
            executable,
            prepare=False,
            replace_launcher=True,
            yes=True,
            allow_plugin_removal=True,
        )
        output = capsys.readouterr().out
        assert f"{expected} VencOverlay" in output
        assert output.count("Existing Vencord:") == 1
        assert output.count("Production Vencord:") == 1
        assert "Installed Vencord:" not in output
        assert "Loader: verified existing Vencord" in output
        assert flatpak_install.installation_state(paths, discord)[0] == "Installed"
        assert flatpak_install.adoption_backup(runtime).stat().st_ino == inode
    flatpak_install.uninstall(paths, True)
    assert flatpak_install.inventory(runtime) == original
    assert flatpak_install.installation_state(paths, discord) == ("Not installed", None)


def test_prepared_adoption_transition_and_rollback(
    production: tuple[Path, Path, str, Path],
    adoption: tuple[flatpak_install.Paths, flatpak_install.Discord, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths, discord, executable, runtime = adoption
    original = flatpak_install.inventory(runtime)
    flatpak_install.install(
        paths,
        discord,
        production[3],
        executable,
        prepare=True,
        replace_launcher=False,
        yes=True,
    )
    assert flatpak_install.installation_state(paths, discord)[0] == "Prepared"
    host = flatpak_install.inventory(paths.host)
    sandbox = flatpak_install.inventory(paths.sandbox)
    original_desktop = paths.desktop.read_bytes()
    replace = flatpak_install.Transaction.replace

    def fail_desktop(tx: flatpak_install.Transaction, target: Path, staged: Path | None) -> None:
        if target == paths.desktop:
            raise OSError("simulated launcher failure")
        replace(tx, target, staged)

    with monkeypatch.context() as patch:
        patch.setattr(flatpak_install.Transaction, "replace", fail_desktop)
        with pytest.raises(OSError, match="simulated launcher failure"):
            flatpak_install.install(
                paths,
                discord,
                production[3],
                executable,
                prepare=False,
                replace_launcher=True,
                yes=True,
                allow_plugin_removal=True,
            )
    assert flatpak_install.inventory(paths.host) == host
    assert flatpak_install.inventory(paths.sandbox) == sandbox
    assert flatpak_install.inventory(runtime) == original
    assert paths.desktop.read_bytes() == original_desktop
    assert not flatpak_install.adoption_backup(runtime).exists()
    flatpak_install.install(
        paths,
        discord,
        production[3],
        executable,
        prepare=False,
        replace_launcher=True,
        yes=True,
        allow_plugin_removal=True,
    )
    assert flatpak_install.installation_state(paths, discord)[0] == "Installed"
    assert not paths.sandbox.exists()
    assert flatpak_install.inventory(flatpak_install.adoption_backup(runtime)) == original
    flatpak_install.uninstall(paths, True)
    assert flatpak_install.inventory(runtime) == original
    assert paths.desktop.read_bytes() == original_desktop


def test_automatic_install_requires_vencord(
    production: tuple[Path, Path, str, Path],
    flatpak_installation: tuple[flatpak_install.Paths, flatpak_install.Discord, Path],
) -> None:
    assert package(production).returncode == 0
    paths, discord, executable = flatpak_installation
    original = (discord.resources / "app.asar").read_bytes()
    with pytest.raises(ValueError, match="must already have Vencord installed"):
        flatpak_install.install(
            paths,
            discord,
            production[3],
            executable,
            prepare=False,
            replace_launcher=True,
            yes=True,
        )
    assert not paths.host.exists() and not paths.sandbox.exists()
    assert (discord.resources / "app.asar").read_bytes() == original


def test_automatic_adoption_refuses_changed_prepared_loader(
    production: tuple[Path, Path, str, Path],
    adoption: tuple[flatpak_install.Paths, flatpak_install.Discord, Path, Path],
) -> None:
    paths, discord, executable, runtime = adoption
    flatpak_install.install(
        paths,
        discord,
        production[3],
        executable,
        prepare=True,
        replace_launcher=False,
        yes=True,
    )
    (discord.resources / "app.asar").write_bytes(
        existing_loader(runtime / "patcher.js", suffix=";")
    )
    with pytest.raises(
        ValueError, match=r"Installation state: Incomplete.*Prepared loader changed"
    ):
        flatpak_install.installation_state(paths, discord)
    with pytest.raises(ValueError, match="Prepared loader changed"):
        flatpak_install.install(
            paths,
            discord,
            production[3],
            executable,
            prepare=False,
            replace_launcher=True,
            yes=True,
            allow_plugin_removal=True,
        )
    assert paths.host.exists() and paths.sandbox.exists()
    assert not flatpak_install.adoption_backup(runtime).exists()


def test_missing_managed_launcher_is_incomplete(
    production: tuple[Path, Path, str, Path],
    adoption: tuple[flatpak_install.Paths, flatpak_install.Discord, Path, Path],
) -> None:
    paths, discord, _, runtime = adoption
    adopt_release(production, adoption)
    paths.desktop.unlink()
    with pytest.raises(ValueError, match="Installation state: Incomplete"):
        flatpak_install.installation_state(paths, discord)
    assert flatpak_install.adoption_backup(runtime).exists()


def test_automatic_adoption_retains_plugin_removal_consent(
    production: tuple[Path, Path, str, Path],
    adoption: tuple[flatpak_install.Paths, flatpak_install.Discord, Path, Path],
) -> None:
    paths, discord, executable, runtime = adoption
    original = flatpak_install.inventory(runtime)
    with pytest.raises(ValueError, match="allow-plugin-removal"):
        flatpak_install.install(
            paths,
            discord,
            production[3],
            executable,
            prepare=False,
            replace_launcher=True,
            yes=True,
        )
    assert flatpak_install.inventory(runtime) == original
    assert not paths.host.exists()
    assert not flatpak_install.adoption_backup(runtime).exists()


def test_automatic_adoption_refuses_unknown_loader(
    production: tuple[Path, Path, str, Path],
    adoption: tuple[flatpak_install.Paths, flatpak_install.Discord, Path, Path],
) -> None:
    paths, discord, executable, runtime = adoption
    app = discord.resources / "app.asar"
    app.write_bytes(existing_loader(runtime / "patcher.js", suffix=";alert(1)"))
    original = flatpak_install.inventory(runtime)
    with pytest.raises(ValueError, match="Unsupported Vencord loader"):
        flatpak_install.install(
            paths,
            discord,
            production[3],
            executable,
            prepare=False,
            replace_launcher=True,
            yes=True,
            allow_plugin_removal=True,
        )
    assert not paths.host.exists()
    assert not flatpak_install.adoption_backup(runtime).exists()
    assert flatpak_install.inventory(runtime) == original


def test_discord_exit_grace_waits_for_transient_instance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    states = iter((True, True, False))
    delays: list[float] = []
    monkeypatch.setattr(flatpak_install, "discord_running", lambda: next(states))
    monkeypatch.setattr(flatpak_install, "monotonic", lambda: 0.0)
    monkeypatch.setattr(flatpak_install, "sleep", delays.append)
    flatpak_install.require_discord_stopped("Quit Discord")
    assert delays == [0.25, 0.25]


def test_discord_exit_grace_refuses_persistent_instance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = 0.0
    delays: list[float] = []

    def advance(delay: float) -> None:
        nonlocal clock
        delays.append(delay)
        clock += delay

    monkeypatch.setattr(flatpak_install, "discord_running", lambda: True)
    monkeypatch.setattr(flatpak_install, "monotonic", lambda: clock)
    monkeypatch.setattr(flatpak_install, "sleep", advance)
    with pytest.raises(ValueError, match="Quit Discord"):
        flatpak_install.require_discord_stopped("Quit Discord")
    assert sum(delays) == flatpak_install.DISCORD_EXIT_GRACE_SECONDS


def test_discord_exit_grace_does_not_ignore_query_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def failed_command(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, 1, "", "query failed")

    monkeypatch.setattr(flatpak_install, "command", failed_command)
    with pytest.raises(ValueError, match="Unable to check running Flatpak applications"):
        flatpak_install.require_discord_stopped("Quit Discord")


def test_adoption_waits_for_probe_exit_but_rechecks_before_publication(
    production: tuple[Path, Path, str, Path],
    adoption: tuple[flatpak_install.Paths, flatpak_install.Discord, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths, discord, executable, runtime = adoption
    original = flatpak_install.inventory(runtime)
    # Initially stopped, probe instance visible, probe exits, then Discord starts.
    states = iter((False, True, False, True))
    monkeypatch.setattr(flatpak_install, "discord_running", lambda: next(states))
    monkeypatch.setattr(flatpak_install, "monotonic", lambda: 0.0)

    def no_sleep(_delay: float) -> None:
        pass

    monkeypatch.setattr(flatpak_install, "sleep", no_sleep)
    with pytest.raises(ValueError, match="Discord deployment or running state changed"):
        flatpak_install.install(
            paths,
            discord,
            production[3],
            executable,
            prepare=False,
            replace_launcher=True,
            yes=True,
            allow_plugin_removal=True,
        )
    assert not paths.host.exists()
    assert not flatpak_install.adoption_backup(runtime).exists()
    assert flatpak_install.inventory(runtime) == original

    # A transient instance left by the preceding preflight and one left by the
    # installation's own probe both clear before any integration is published.
    states = iter((True, False, True, False, False))
    flatpak_install.install(
        paths,
        discord,
        production[3],
        executable,
        prepare=False,
        replace_launcher=True,
        yes=True,
        allow_plugin_removal=True,
    )
    assert flatpak_install.installation_state(paths, discord)[0] == "Installed"
    assert flatpak_install.inventory(flatpak_install.adoption_backup(runtime)) == original


def test_version_report_distinguishes_original_and_managed_runtime(
    capsys: pytest.CaptureFixture[str],
) -> None:
    original = ("8d64c8be7e68616f8f116a6fff9480051aa2a0c5", "1.15.15")
    current = ("d086992fb674299a2fa05ac6432edc3a777389f3", "1.15.19")
    flatpak_install.report_versions(original, current, current)
    assert capsys.readouterr().out.splitlines() == [
        "Existing Vencord: 1.15.15 (8d64c8b)",
        "Installed Vencord: 1.15.19 (d086992)",
        "Production Vencord: 1.15.19 (d086992)",
    ]


def replace_adopted_runtime(runtime: Path) -> dict[str, str]:
    shutil.rmtree(runtime)
    shutil.copytree(flatpak_install.adoption_backup(runtime), runtime)
    (runtime / "package.json").write_bytes(b'{"external":true}')
    return flatpak_install.inventory(runtime)


def test_repair_adopted_runtime_and_changed_deployment(
    production: tuple[Path, Path, str, Path],
    adoption: tuple[flatpak_install.Paths, flatpak_install.Discord, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths, discord, executable, runtime = adoption
    adopt_release(production, adoption)
    backup = flatpak_install.adoption_backup(runtime)
    original = flatpak_install.inventory(backup)
    original_inode = backup.stat().st_ino
    external = replace_adopted_runtime(runtime)
    deployment = discord.deployment.with_name("new-deployment")
    shutil.copytree(discord.deployment, deployment)
    current = flatpak_install.Discord(discord.scope, deployment, discord.arch, "b" * 64)
    original_asar = current.resources / "_app.asar"
    original_asar.write_bytes(b"new Discord original archive")
    current_hash = flatpak_install.file_hash(original_asar)

    def detect(_scope: str | None) -> flatpak_install.Discord:
        return current

    def matches(target: flatpak_install.Discord, name: str) -> bool:
        return (
            target == current and flatpak_install.file_hash(target.resources / name) == current_hash
        )

    monkeypatch.setattr(flatpak_install, "detect", detect)
    monkeypatch.setattr(flatpak_install, "matches_original", matches)
    with pytest.raises(ValueError, match="Managed runtime files changed"):
        flatpak_install.installation_state(paths, current)
    flatpak_install.install(
        paths,
        current,
        production[3],
        executable,
        prepare=False,
        replace_launcher=False,
        yes=True,
        repair=True,
        allow_plugin_removal=True,
    )
    label, state = flatpak_install.installation_state(paths, current)
    assert label == "Installed" and state is not None
    assert state["deployment"] == str(deployment)
    assert state["original_asar_sha256"] == current_hash
    assert backup.stat().st_ino == original_inode
    assert flatpak_install.inventory(backup) == original
    recovered = list(runtime.parent.glob(".vencoverlay-recovery-dist-*"))
    assert len(recovered) == 1
    assert flatpak_install.inventory(recovered[0]) == external
    flatpak_install.uninstall(paths, True)
    assert flatpak_install.inventory(runtime) == original
    assert flatpak_install.inventory(recovered[0]) == external


def test_repair_rejects_changed_backup_and_custom_plugins(
    production: tuple[Path, Path, str, Path],
    adoption: tuple[flatpak_install.Paths, flatpak_install.Discord, Path, Path],
) -> None:
    paths, discord, executable, runtime = adoption
    adopt_release(production, adoption)
    replace_adopted_runtime(runtime)
    backup_file = flatpak_install.adoption_backup(runtime) / "extra.txt"
    original = backup_file.read_bytes()
    backup_file.write_bytes(b"changed backup")
    with pytest.raises(ValueError, match="Original runtime backup changed"):
        flatpak_install.install(
            paths,
            discord,
            production[3],
            executable,
            prepare=False,
            replace_launcher=False,
            yes=True,
            repair=True,
            allow_plugin_removal=True,
        )
    backup_file.write_bytes(original)
    (runtime / "renderer.js.map").write_text(
        json.dumps({"sources": ["../src/userplugins/custom/index.ts"]})
    )
    external = flatpak_install.inventory(runtime)
    with pytest.raises(ValueError, match="Existing custom plugins detected"):
        flatpak_install.install(
            paths,
            discord,
            production[3],
            executable,
            prepare=False,
            replace_launcher=False,
            yes=True,
            repair=True,
            allow_plugin_removal=True,
        )
    assert flatpak_install.inventory(runtime) == external
    assert not list(runtime.parent.glob(".vencoverlay-recovery-dist-*"))


def test_repair_rolls_back_external_runtime_on_launcher_failure(
    production: tuple[Path, Path, str, Path],
    adoption: tuple[flatpak_install.Paths, flatpak_install.Discord, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths, discord, executable, runtime = adoption
    adopt_release(production, adoption)
    original = flatpak_install.inventory(flatpak_install.adoption_backup(runtime))
    external = replace_adopted_runtime(runtime)
    host = flatpak_install.inventory(paths.host)
    desktop = paths.desktop.read_bytes()
    replace = flatpak_install.Transaction.replace

    def fail_desktop(self: flatpak_install.Transaction, target: Path, staged: Path | None) -> None:
        if target == paths.desktop:
            raise OSError("simulated repair launcher failure")
        replace(self, target, staged)

    monkeypatch.setattr(flatpak_install.Transaction, "replace", fail_desktop)
    with pytest.raises(OSError, match="simulated repair launcher failure"):
        flatpak_install.install(
            paths,
            discord,
            production[3],
            executable,
            prepare=False,
            replace_launcher=False,
            yes=True,
            repair=True,
            allow_plugin_removal=True,
        )
    assert flatpak_install.inventory(paths.host) == host
    assert flatpak_install.inventory(runtime) == external
    assert flatpak_install.inventory(flatpak_install.adoption_backup(runtime)) == original
    assert paths.desktop.read_bytes() == desktop
    assert not list(runtime.parent.glob(".vencoverlay-recovery-dist-*"))


def test_repair_rejects_external_runtime_changed_after_confirmation(
    production: tuple[Path, Path, str, Path],
    adoption: tuple[flatpak_install.Paths, flatpak_install.Discord, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths, discord, executable, runtime = adoption
    adopt_release(production, adoption)
    replace_adopted_runtime(runtime)
    host = flatpak_install.inventory(paths.host)
    desktop = paths.desktop.read_bytes()

    def changed(_yes: bool, _message: str) -> None:
        (runtime / "package.json").write_bytes(b'{"changed_after_review":true}')

    monkeypatch.setattr(flatpak_install, "confirm", changed)
    with pytest.raises(ValueError, match="Managed runtime files changed"):
        flatpak_install.install(
            paths,
            discord,
            production[3],
            executable,
            prepare=False,
            replace_launcher=False,
            yes=True,
            repair=True,
            allow_plugin_removal=True,
        )
    assert flatpak_install.inventory(paths.host) == host
    assert paths.desktop.read_bytes() == desktop
    assert not list(runtime.parent.glob(".vencoverlay-recovery-dist-*"))
    assert (runtime / "package.json").read_bytes() == b'{"changed_after_review":true}'


def desktop_export(discord: flatpak_install.Discord) -> Path:
    root = discord.deployment.parents[4]
    app = root / "app" / flatpak_install.APP
    (app / "current").symlink_to(f"{discord.arch}/stable")
    (discord.deployment.parent / "active").symlink_to(discord.deployment.name)
    export = root / "exports/share/applications" / f"{flatpak_install.APP}.desktop"
    export.parent.mkdir(parents=True, exist_ok=True)
    export.symlink_to(
        f"../../../app/{flatpak_install.APP}/current/active/export/share/applications/"
        f"{flatpak_install.APP}.desktop"
    )
    return export


@pytest.mark.parametrize("scope", ["system", "user"])
def test_refresh_desktop_export_only_touches_link(tmp_path: Path, scope: str) -> None:
    deployment = tmp_path / scope / "flatpak/app/com.discordapp.Discord/x86_64/stable/commit"
    discord = flatpak_install.Discord(scope, deployment, "x86_64", "a" * 64)
    discord.exported_desktop.parent.mkdir(parents=True)
    discord.exported_desktop.write_bytes(b"original desktop")
    export = desktop_export(discord)
    destination = export.readlink()
    os.utime(export, ns=(1_000_000_000, 1_000_000_000), follow_symlinks=False)
    target_before = discord.exported_desktop.stat()
    flatpak_install.refresh_exported_desktop(discord)
    assert export.lstat().st_mtime_ns > 1_000_000_000
    assert export.readlink() == destination
    assert discord.exported_desktop.stat().st_mtime_ns == target_before.st_mtime_ns
    assert discord.exported_desktop.read_bytes() == b"original desktop"


def test_refresh_desktop_export_refuses_wrong_target(
    flatpak_installation: tuple[flatpak_install.Paths, flatpak_install.Discord, Path],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _, discord, _ = flatpak_installation
    export = desktop_export(discord)
    export.unlink()
    unrelated = tmp_path / "unrelated.desktop"
    unrelated.write_bytes(b"unrelated")
    export.symlink_to(unrelated)
    os.utime(export, ns=(1_000_000_000, 1_000_000_000), follow_symlinks=False)
    flatpak_install.refresh_exported_desktop(discord)
    assert export.lstat().st_mtime_ns == 1_000_000_000
    assert "Warning:" in capsys.readouterr().err


@pytest.mark.parametrize("permission_denied", [False, True])
def test_uninstall_refreshes_unshadowed_export(
    production: tuple[Path, Path, str, Path],
    adoption: tuple[flatpak_install.Paths, flatpak_install.Discord, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    permission_denied: bool,
) -> None:
    paths, discord, _, runtime = adoption
    paths.desktop.unlink()
    export = desktop_export(discord)
    os.utime(export, ns=(1_000_000_000, 1_000_000_000), follow_symlinks=False)
    original = flatpak_install.inventory(runtime)
    adopt_release(production, adoption)
    if permission_denied:
        # Refuse the timestamp operation itself, not the transaction.
        def deny_utime(_path: Path, *, follow_symlinks: bool) -> None:
            assert not follow_symlinks
            assert not paths.host.exists() and not paths.desktop.exists()
            raise PermissionError("test denied")

        monkeypatch.setattr(flatpak_install.os, "utime", deny_utime)
    flatpak_install.uninstall(paths, True)
    assert not paths.desktop.exists() and not paths.host.exists()
    assert flatpak_install.inventory(runtime) == original
    output = capsys.readouterr()
    if permission_denied:
        assert "Warning: Could not refresh" in output.err
        assert export.lstat().st_mtime_ns == 1_000_000_000
    else:
        assert "Original Discord desktop export refreshed." in output.out
        assert export.lstat().st_mtime_ns > 1_000_000_000


def test_uninstall_skips_export_refresh_when_restoring_local_launcher(
    production: tuple[Path, Path, str, Path],
    adoption: tuple[flatpak_install.Paths, flatpak_install.Discord, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths, _, _, _ = adoption
    adopt_release(production, adoption)

    def unexpected_refresh(_discord: flatpak_install.Discord) -> None:
        pytest.fail("A restored local launcher still shadows the Flatpak export")

    monkeypatch.setattr(flatpak_install, "refresh_exported_desktop", unexpected_refresh)
    flatpak_install.uninstall(paths, True)
    assert paths.desktop.read_bytes() == b"original local desktop\n"


def licence_fixture(root: Path) -> bytes:
    binary = (root / release_package.NATIVE).read_bytes()
    frontend = (root / release_package.FRONTEND).read_bytes()
    files = {
        "native/inventory.json": release_package.json_bytes(
            {
                "executable_sha256": release_package.sha256(binary),
            }
        ),
        "frontend/inventory.json": b"{}",
        "THIRD-PARTY-NOTICES.md": b"Synthetic evidence, pending review.\n",
    }
    files["inventory.json"] = release_package.json_bytes(
        {
            "review": release_licences.REVIEW,
            "native_sha256": release_package.sha256(binary),
            "frontend_sha256": release_package.sha256(frontend),
            "files": release_package.hashes(files),
        }
    )
    return release_licences.zip_bytes(files)


def test_release_includes_bound_licence_evidence(release_root: Path) -> None:
    data = licence_fixture(release_root)
    evidence = release_root / release_licences.AUDIT
    evidence.parent.mkdir(exist_ok=True)
    evidence.write_bytes(data)
    output = release_package.package(release_root, licences=True)
    release_package.verify_package(output)
    with tarfile.open(output, "r:gz") as archive:
        prefix = output.name.removesuffix(".tar.gz")
        stream = archive.extractfile(f"{prefix}/{release_licences.PACKAGE_AUDIT}")
        assert stream is not None
        with stream:
            assert stream.read() == data
        stream = archive.extractfile(f"{prefix}/{release_package.MANIFEST}")
        assert stream is not None
        with stream:
            assert read_json(stream.read())["licence_review"] == release_licences.REVIEW


def test_licence_evidence_rejects_stale_artifacts(release_root: Path) -> None:
    evidence = licence_fixture(release_root)
    binary = (release_root / release_package.NATIVE).read_bytes()
    frontend = (release_root / release_package.FRONTEND).read_bytes()
    with pytest.raises(ValueError, match="Stale native licence evidence"):
        release_licences.verify_evidence(evidence, binary + b"changed", frontend)
    with pytest.raises(ValueError, match="Stale frontend licence evidence"):
        release_licences.verify_evidence(evidence, binary, frontend + b"changed")
    files = release_licences.zip_files(evidence)
    files["THIRD-PARTY-NOTICES.md"] = b"changed"
    with pytest.raises(ValueError, match="Licence evidence checksum mismatch"):
        release_licences.verify_evidence(release_licences.zip_bytes(files), binary, frontend)


def test_frontend_licence_roots() -> None:
    files = {
        "debug/renderer.js.map": release_package.json_bytes(
            {
                "sources": [
                    "../src/index.ts",
                    "../node_modules/.pnpm/fflate@0.8.3/node_modules/fflate/esm/index.mjs",
                    "../node_modules/.pnpm/@vap+core@0.0.12/node_modules/@vap/core/ipc/index.js",
                ]
            }
        )
    }
    assert release_licences.frontend_roots(files) == {
        "node_modules/.pnpm/fflate@0.8.3/node_modules/fflate",
        "node_modules/.pnpm/@vap+core@0.0.12/node_modules/@vap/core",
    }
    files["debug/renderer.js.map"] = release_package.json_bytes(
        {
            "sources": [
                "../node_modules/.pnpm/example@1/node_modules/example/../../outside.js",
            ]
        }
    )
    with pytest.raises(ValueError, match="Unsafe dependency path"):
        release_licences.frontend_roots(files)


def test_native_licence_report_entries(tmp_path: Path) -> None:
    report = tmp_path / "report.xml"
    report.write_text(
        '<report><module name="gi"/><included_dll name="libgtk.so" '
        'source_path="/usr/lib64/libgtk.so" ignored="no"/>'
        '<included_dll name="ignored.so" ignored="yes"/>'
        '<data_file name="Gtk-3.0.typelib" source="/usr/lib64/Gtk-3.0.typelib"/>'
        "</report>",
        encoding="utf-8",
    )
    assert release_licences.report_entries(report) == [
        ("module", "gi", ""),
        ("included_dll", "libgtk.so", "/usr/lib64/libgtk.so"),
        ("data_file", "Gtk-3.0.typelib", "/usr/lib64/Gtk-3.0.typelib"),
    ]


def test_frontend_licence_collection(
    production: tuple[Path, Path, str, Path],
) -> None:
    _, checkout, _, output = production
    dependency = checkout / "node_modules/.pnpm/fflate@0.8.3/node_modules/fflate"
    dependency.mkdir(parents=True)
    (dependency / "package.json").write_text(
        '{"name":"fflate","version":"0.8.3","license":"MIT"}', encoding="utf-8"
    )
    (dependency / "LICENSE").write_text("Synthetic MIT notice", encoding="utf-8")
    (dependency / "index.js").write_text("export const x=1;", encoding="utf-8")
    mapping_path = checkout / "dist/renderer.js.map"
    mapping = read_json(mapping_path.read_bytes())
    mapping["sources"] = [
        *object_list(mapping["sources"]),
        "../node_modules/.pnpm/fflate@0.8.3/node_modules/fflate/index.js",
    ]
    mapping["sourcesContent"] = [*object_list(mapping["sourcesContent"]), "export const x=1;"]
    mapping_path.write_bytes(release_package.json_bytes(mapping))
    assert package(production).returncode == 0
    frontend = release_licences.zip_files(output.read_bytes())
    files: dict[str, bytes] = {}
    release_licences.collect_frontend(frontend, checkout, files)
    assert files["frontend/npm/fflate-0.8.3/LICENSE"] == b"Synthetic MIT notice"
    (dependency / "index.js").write_text("changed", encoding="utf-8")
    with pytest.raises(ValueError, match="Mapped dependency changed"):
        release_licences.collect_frontend(frontend, checkout, {})


def test_native_licence_collection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from importlib.metadata import Distribution, PathDistribution

    metadata = tmp_path / "example.dist-info"
    metadata.mkdir()
    (metadata / "METADATA").write_text(
        "Name: example\nVersion: 1.0\nLicense: MIT\n", encoding="utf-8"
    )
    (metadata / "LICENSE").write_text("Synthetic Python notice", encoding="utf-8")
    (metadata / "RECORD").write_text("example.dist-info/LICENSE,,\n", encoding="utf-8")

    def distribution(_name: str) -> Distribution:
        return PathDistribution(metadata)

    library = tmp_path / "libgtk.so"
    library.write_bytes(b"synthetic library")
    licence = tmp_path / "COPYING"
    licence.write_text("Synthetic RPM notice", encoding="utf-8")

    def rpm(*args: str) -> str | None:
        if args[0] == "-qf":
            return "gtk3-3.24-1.fc43.x86_64" if args[-1] == str(library) else None
        if "--licensefiles" in args:
            return str(licence)
        return "LGPL-2.0-or-later\ngtk3-3.24-1.fc43.src.rpm"

    monkeypatch.setattr(release_licences, "distribution", distribution)
    monkeypatch.setattr(release_licences, "rpm", rpm)
    report = tmp_path / "report.xml"
    report.write_text(
        '<report><module name="gi"/>'
        f'<included_dll name="libgtk.so" source_path="{library}"/>'
        '<included_dll name="unresolved.so" source_path="/nonexistent"/></report>',
        encoding="utf-8",
    )
    binary = tmp_path / "binary"
    binary.write_bytes(b"synthetic executable")
    output = tmp_path / "native.zip"
    release_licences.collect_native(report, binary, output)
    files = release_licences.zip_files(output.read_bytes())
    assert files["native/python/PyGObject/LICENSE"] == b"Synthetic Python notice"
    assert files["native/rpm/gtk3-3.24-1.fc43.x86_64/COPYING"] == b"Synthetic RPM notice"
    inventory = read_json(files["native/inventory.json"])
    assert str(library) not in files["native/inventory.json"].decode()
    entries = [object_dict(entry) for entry in object_list(inventory["report_entries"])]
    assert entries[-1]["rpm"] == "unresolved-or-not-rpm"
