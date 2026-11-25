"""Check the installed application package and entry point."""

import subprocess
import sys
import tomllib
from importlib.metadata import distribution
from pathlib import Path
from typing import cast

import pytest

from vencoverlay import cli
from vencoverlay.cli import main

ROOT = Path(__file__).resolve().parents[2]


def test_package_import_is_headless() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            "import sys; import vencoverlay; import vencoverlay.cli; "
            "assert 'gi' not in sys.modules; assert 'cairo' not in sys.modules",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    assert result.stdout == ""
    assert result.stderr == ""


def test_installed_metadata() -> None:
    with (ROOT / "pyproject.toml").open("rb") as stream:
        project = tomllib.load(stream)
    metadata: object = project["project"]
    assert isinstance(metadata, dict)
    expected = cast(dict[object, object], metadata).get("version")
    assert isinstance(expected, str)
    installed = distribution("vencoverlay")
    assert installed.version == expected
    assert any(
        entry.group == "console_scripts"
        and entry.name == "vencoverlay"
        and entry.value == "vencoverlay.cli:main"
        for entry in installed.entry_points
    )


def test_entry_point_dispatches_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["vencoverlay"])
    monkeypatch.setattr(cli, "run", lambda: 0)
    assert main() == 0


def test_version_reporting(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "argv", ["vencoverlay", "--version"])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 0
    captured = capsys.readouterr()
    assert captured.out == f"VencOverlay {distribution('vencoverlay').version}\n"
    assert captured.err == ""


@pytest.mark.parametrize("argument", ["--help", "-h"])
def test_help(
    argument: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "argv", ["vencoverlay", argument])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 0
    captured = capsys.readouterr()
    assert "usage: vencoverlay" in captured.out
    assert "--version" in captured.out
    assert captured.err == ""


@pytest.mark.parametrize("argument", ["--unknown", "--vers"])
def test_invalid_argument(
    argument: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "argv", ["vencoverlay", argument])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "unrecognized arguments" in captured.err


def test_module_entry_point() -> None:
    result = subprocess.run(
        [sys.executable, "-I", "-m", "vencoverlay", "--version"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    assert result.stdout == f"VencOverlay {distribution('vencoverlay').version}\n"
    assert result.stderr == ""
