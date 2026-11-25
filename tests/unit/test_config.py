"""Configuration loading and validation tests."""

from pathlib import Path

import pytest

from vencoverlay.config import Configuration, ConfigurationError, load_config


def test_missing_file(tmp_path: Path) -> None:
    path = tmp_path / "config" / "config.toml"
    assert load_config(path) == Configuration()
    assert not path.parent.exists()


def test_partial_configuration(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    content = 'monitor = 2\nfont = "Sans"\n'
    path.write_text(content, encoding="utf-8")
    assert load_config(path) == Configuration(monitor=2, font="Sans")
    assert path.read_text(encoding="utf-8") == content


@pytest.mark.parametrize(
    "content",
    [
        "port = 0",
        "port = 65536",
        "port = true",
        'port = "6889"',
        "monitor = -1",
        "margin = -1",
        "margin = 1.5",
        'font = " "',
        "font = 1",
        "unknown = 1",
        "[overlay]\nmonitor = 0",
        "port = [",
    ],
)
def test_invalid_configuration(tmp_path: Path, content: str) -> None:
    path = tmp_path / "config.toml"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ConfigurationError, match=r"config\.toml"):
        load_config(path)


def test_directory_is_not_configuration(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError):
        load_config(tmp_path)


def test_direct_validation() -> None:
    with pytest.raises(ConfigurationError, match="port"):
        Configuration(port=0)


def test_logging_configuration(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('log_level = "DEBUG"\nlog_to_file = true\n', encoding="utf-8")
    assert load_config(path) == Configuration(log_level="DEBUG", log_to_file=True)


@pytest.mark.parametrize("content", ['log_level = "TRACE"', "log_level = 1", "log_to_file = 1"])
def test_invalid_logging_configuration(tmp_path: Path, content: str) -> None:
    path = tmp_path / "config.toml"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ConfigurationError, match="log_"):
        load_config(path)
