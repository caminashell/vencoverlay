"""Verify the application boundary and CLI dispatch."""

import sys

import pytest

from vencoverlay import cli
from vencoverlay.application import run


def test_runtime_placeholder(capsys: pytest.CaptureFixture[str]) -> None:
    assert run() == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "VencOverlay: application runtime not yet implemented.\n"


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
