"""Logging lifecycle and output tests."""

import logging
from pathlib import Path

import pytest

from vencoverlay.config import Configuration
from vencoverlay.logging_setup import application_logging
from vencoverlay.paths import resolve_paths


def test_console_logging(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    paths = resolve_paths(home=tmp_path, environment={}, platform="linux")
    logger = logging.getLogger("vencoverlay.test")
    with application_logging(Configuration(), paths):
        logger.debug("hidden")
        logger.info("visible")
    output = capsys.readouterr().err
    assert "hidden" not in output
    assert "Z INFO     vencoverlay.test: visible" in output
    assert not paths.state_dir.exists()


def test_file_logging(tmp_path: Path) -> None:
    paths = resolve_paths(home=tmp_path, environment={}, platform="linux")
    with application_logging(Configuration(log_level="DEBUG", log_to_file=True), paths):
        logging.getLogger("vencoverlay.test").debug("persistent")
    assert "persistent" in (paths.state_dir / "vencoverlay.log").read_text(encoding="utf-8")


def test_restore_logging_on_error(tmp_path: Path) -> None:
    paths = resolve_paths(home=tmp_path, environment={}, platform="linux")
    logger = logging.getLogger("vencoverlay")
    previous = (logger.handlers[:], logger.level, logger.propagate)
    with pytest.raises(RuntimeError, match="failure"), application_logging(Configuration(), paths):
        raise RuntimeError("failure")
    assert (logger.handlers, logger.level, logger.propagate) == previous


def test_file_setup_failure(tmp_path: Path) -> None:
    paths = resolve_paths(home=tmp_path, environment={}, platform="linux")
    paths.state_dir.parent.mkdir(parents=True)
    paths.state_dir.touch()
    logger = logging.getLogger("vencoverlay")
    previous = (logger.handlers[:], logger.level, logger.propagate)
    with pytest.raises(OSError), application_logging(Configuration(log_to_file=True), paths):
        pytest.fail("Logging setup should fail")
    assert (logger.handlers, logger.level, logger.propagate) == previous
