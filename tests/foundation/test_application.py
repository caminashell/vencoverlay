"""Verify the application boundary and CLI dispatch."""

import sys
from pathlib import Path

import pytest

from vencoverlay import application, cli
from vencoverlay.application import run
from vencoverlay.backend import BackendSelection
from vencoverlay.config import Configuration


def test_invalid_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    directory = tmp_path / "vencoverlay"
    directory.mkdir()
    (directory / "config.toml").write_text("port = 0\n")
    assert run() == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "port must be 1 to 65535" in captured.err


def test_runtime_status(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("VENCOVERLAY_BACKEND", "x11")

    def fake_runtime(*_args: object) -> int:
        return 7

    monkeypatch.setattr(application, "run_runtime", fake_runtime)
    assert run() == 7


def test_cli_propagates_runtime_status(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["vencoverlay"])
    monkeypatch.setattr(cli, "run", lambda: 7)
    assert cli.main() == 7


@pytest.mark.parametrize("argument", ["--help", "--version", "--unknown"])
def test_cli_does_not_start_runtime(argument: str, monkeypatch: pytest.MonkeyPatch) -> None:
    def unexpected_run() -> int:
        pytest.fail("Informational or invalid arguments must not start the runtime")

    monkeypatch.setattr(sys, "argv", ["vencoverlay", argument])
    monkeypatch.setattr(cli, "run", unexpected_run)
    with pytest.raises(SystemExit):
        cli.main()


def test_runtime_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("VENCOVERLAY_BACKEND", "x11")

    def fail(*_args: object) -> int:
        raise OSError("Address already in use")

    monkeypatch.setattr(application, "run_runtime", fail)
    assert run() == 1
    assert "Address already in use" in capsys.readouterr().err


def test_demo_cli_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    def demo_run(*, demo: bool = False) -> int:
        assert demo
        return 7

    monkeypatch.setattr(sys, "argv", ["vencoverlay", "--demo"])
    monkeypatch.setattr(cli, "run", demo_run)
    assert cli.main() == 7


def test_demo_disables_persistent_logging(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("VENCOVERLAY_BACKEND", "x11")
    directory = tmp_path / "vencoverlay"
    directory.mkdir()
    (directory / "config.toml").write_text("log_to_file = true\n")

    def fake_runtime(config: Configuration, *_args: object, demo: bool = False) -> int:
        assert demo
        assert not config.log_to_file
        return 0

    monkeypatch.setattr(application, "run_runtime", fake_runtime)
    assert run(demo=True) == 0
    assert not (tmp_path / "state").exists()


def test_runtime_check_cli(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def check(selection: BackendSelection) -> None:
        assert selection.gdk_backend == "x11"

    monkeypatch.setenv("VENCOVERLAY_BACKEND", "x11")
    monkeypatch.setattr(sys, "argv", ["vencoverlay", "--check-runtime"])
    monkeypatch.setattr(cli, "check_runtime", check)
    assert cli.main() == 0
    assert capsys.readouterr().out == "VencOverlay runtime check passed\n"


def test_runtime_check_cli_failure(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def check(_selection: BackendSelection) -> None:
        raise RuntimeError("Cannot initialise GTK")

    monkeypatch.setenv("VENCOVERLAY_BACKEND", "x11")
    monkeypatch.setattr(sys, "argv", ["vencoverlay", "--check-runtime"])
    monkeypatch.setattr(cli, "check_runtime", check)
    assert cli.main() == 1
    assert "Cannot initialise GTK" in capsys.readouterr().err
