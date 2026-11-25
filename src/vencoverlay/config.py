"""Load and validate local VencOverlay configuration."""

import tomllib
from dataclasses import dataclass
from pathlib import Path


class ConfigurationError(ValueError):
    """The configuration could not be read or validated."""


@dataclass(frozen=True, slots=True)
class Configuration:
    port: int = 6889
    monitor: int = 0
    margin: int = 24
    font: str = "Inter"
    log_level: str = "INFO"
    log_to_file: bool = False

    def __post_init__(self) -> None:
        _integer(self.port, "port", 1, 65535)
        _integer(self.monitor, "monitor", 0)
        _integer(self.margin, "margin", 0)
        _font(self.font)
        _log_level(self.log_level)
        _boolean(self.log_to_file, "log_to_file")


def _integer(value: object, name: str, minimum: int, maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigurationError(f"{name} must be an integer")
    if value < minimum or (maximum is not None and value > maximum):
        bounds = f"{minimum} to {maximum}" if maximum is not None else f"at least {minimum}"
        raise ConfigurationError(f"{name} must be {bounds}")
    return value


def _font(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigurationError("font must be a non-empty string")
    return value


def _log_level(value: object) -> str:
    if not isinstance(value, str) or value not in {
        "DEBUG",
        "INFO",
        "WARNING",
        "ERROR",
        "CRITICAL",
    }:
        raise ConfigurationError("log_level must be DEBUG, INFO, WARNING, ERROR or CRITICAL")
    return value


def _boolean(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise ConfigurationError(f"{name} must be a boolean")
    return value


def load_config(path: Path) -> Configuration:
    """Read TOML, using defaults only for absent files or omitted settings."""
    defaults = Configuration()
    try:
        with path.open("rb") as stream:
            values: dict[str, object] = tomllib.load(stream)
    except FileNotFoundError:
        return defaults
    except (OSError, ValueError) as exc:
        raise ConfigurationError(f"{path}: {exc}") from exc

    try:
        unknown = values.keys() - {"port", "monitor", "margin", "font", "log_level", "log_to_file"}
        if unknown:
            raise ConfigurationError(f"Unknown settings: {', '.join(sorted(unknown))}")
        return Configuration(
            port=_integer(values.get("port", defaults.port), "port", 1, 65535),
            monitor=_integer(values.get("monitor", defaults.monitor), "monitor", 0),
            margin=_integer(values.get("margin", defaults.margin), "margin", 0),
            font=_font(values.get("font", defaults.font)),
            log_level=_log_level(values.get("log_level", defaults.log_level)),
            log_to_file=_boolean(values.get("log_to_file", defaults.log_to_file), "log_to_file"),
        )
    except ConfigurationError as exc:
        raise ConfigurationError(f"{path}: {exc}") from exc
