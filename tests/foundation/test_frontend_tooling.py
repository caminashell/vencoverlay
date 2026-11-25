"""Frontend orchestration tests without downloads or installed Node dependencies."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def run_frontend(root: Path, mode: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "scripts/frontend.sh", mode],
        cwd=root,
        env={**os.environ, "PATH": f"{root / 'bin'}:{os.environ['PATH']}"},
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )


def executable(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!/bin/sh\n{content}\n", encoding="utf-8")
    path.chmod(0o755)


@pytest.fixture
def frontend_root(tmp_path: Path) -> Path:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    for name in ("frontend.sh", "prerequisites.sh"):
        shutil.copy2(ROOT / "scripts" / name, scripts / name)
    executable(tmp_path / "bin/node", 'if [ "$1" = --version ]; then echo v22.16.0; fi')
    executable(
        tmp_path / "bin/git",
        'case "$*" in\n'
        '*"remote get-url origin") echo https://github.com/Vendicated/Vencord.git;;\n'
        '*"rev-parse HEAD") echo d086992fb674299a2fa05ac6432edc3a777389f3;;\n'
        '*"status --porcelain --untracked-files=all") :;;\n'
        '*"show -s --format=%ct "*) echo 1234567890;;\n'
        "*) exit 2;;\nesac",
    )
    return tmp_path


def prepare_checkout(root: Path) -> Path:
    work = root / "build/vencord-dev"
    checkout = work / "source"
    (checkout / ".git").mkdir(parents=True)
    (checkout / "src").mkdir()
    (work / "managed").touch()
    for name in ("package.json", "pnpm-lock.yaml", "pnpm-workspace.yaml"):
        (checkout / name).write_text("{}\n", encoding="utf-8")
    hashes = subprocess.check_output(
        ["sha256sum", "package.json", "pnpm-lock.yaml", "pnpm-workspace.yaml"],
        cwd=checkout,
        text=True,
    )
    (work / "dependencies.ready").write_text(
        hashes + "v22.16.0\nd086992fb674299a2fa05ac6432edc3a777389f3\n12.15.0\n",
        encoding="utf-8",
    )
    executable(work / "toolchain/node_modules/.bin/pnpm", "exit 99")
    executable(checkout / "node_modules/.bin/tsc", 'test "$1" = --noEmit')
    executable(
        checkout / "node_modules/.bin/oxlint",
        'test "$#" = 4 && test "$1" = --deny-warnings '
        '&& test "$2" = src/userplugins/vencoverlay/index.ts '
        '&& test "$3" = src/userplugins/vencoverlay/native.ts '
        '&& test "$4" = src/userplugins/vencoverlay/nested/component.tsx '
        '&& test -f "$2" && test -f "$3" && test -f "$4"',
    )
    plugin = root / "plugin/vencoverlay"
    plugin.mkdir(parents=True)
    for name in ("index.ts", "native.ts"):
        (plugin / name).write_text("export {};\n", encoding="utf-8")
    (plugin / "nested").mkdir()
    (plugin / "nested/component.tsx").write_text("export {};\n", encoding="utf-8")
    return checkout


def test_missing_checkout_does_not_create_files(frontend_root: Path) -> None:
    result = run_frontend(frontend_root, "--check")
    assert result.returncode != 0
    assert "checkout missing" in result.stderr
    assert not (frontend_root / "build").exists()


def test_validation_copies_latest_plugin_and_uses_local_tools(frontend_root: Path) -> None:
    checkout = prepare_checkout(frontend_root)
    assert run_frontend(frontend_root, "--typecheck").returncode == 0
    target = checkout / "src/userplugins/vencoverlay"
    (target / "obsolete.ts").touch()
    assert run_frontend(frontend_root, "--lint").returncode == 0
    assert not (target / "obsolete.ts").exists()
    assert (target / "index.ts").read_text(encoding="utf-8") == "export {};\n"


def test_stale_dependencies_rejected(frontend_root: Path) -> None:
    checkout = prepare_checkout(frontend_root)
    (checkout / "pnpm-lock.yaml").write_text("changed\n", encoding="utf-8")
    result = run_frontend(frontend_root, "--check")
    assert result.returncode != 0
    assert "need synchronising" in result.stderr


def test_unrelated_plugin_is_not_removed(frontend_root: Path) -> None:
    checkout = prepare_checkout(frontend_root)
    unrelated = checkout / "src/userplugins/another-plugin"
    unrelated.mkdir(parents=True)
    result = run_frontend(frontend_root, "--typecheck")
    assert result.returncode != 0
    assert unrelated.is_dir()
    assert "Unexpected userplugins" in result.stderr


def test_typecheck_failure_propagates(frontend_root: Path) -> None:
    checkout = prepare_checkout(frontend_root)
    executable(checkout / "node_modules/.bin/tsc", "exit 7")
    assert run_frontend(frontend_root, "--typecheck").returncode == 7


def test_failed_production_build_restores_checkout_output(frontend_root: Path) -> None:
    checkout = prepare_checkout(frontend_root)
    existing = checkout / "dist"
    existing.mkdir()
    (existing / "previous.js").write_text("previous", encoding="utf-8")
    output = frontend_root / "dist/vencoverlay-vencord.zip"
    output.parent.mkdir()
    output.write_bytes(b"previous release")
    executable(
        frontend_root / "bin/node",
        'case "$1" in\n'
        "--version) echo v22.16.0;;\n"
        "*.mjs) mkdir -p dist; touch dist/partial.js; exit 17;;\n"
        "*) :;;\nesac",
    )
    result = run_frontend(frontend_root, "--build")
    assert result.returncode == 17, result.stderr
    assert (existing / "previous.js").read_text(encoding="utf-8") == "previous"
    assert not (existing / "partial.js").exists()
    assert output.read_bytes() == b"previous release"
    assert not list((frontend_root / "build/vencord-dev").glob("production.*"))


def test_lint_failure_blocks_validation_and_production(frontend_root: Path) -> None:
    checkout = prepare_checkout(frontend_root)
    executable(checkout / "node_modules/.bin/oxlint", "exit 9")
    assert run_frontend(frontend_root, "--lint").returncode == 9
    assert run_frontend(frontend_root, "--build").returncode == 9
    assert not (frontend_root / "dist").exists()
