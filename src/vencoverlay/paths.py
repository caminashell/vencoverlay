"""Resolve application paths without creating directories."""

import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ApplicationPaths:
    config_dir: Path
    cache_dir: Path
    data_dir: Path
    state_dir: Path

    @property
    def config_file(self) -> Path:
        return self.config_dir / "config.toml"

    @property
    def avatar_cache(self) -> Path:
        return self.cache_dir / "avatars"


def _absolute_path(environment: Mapping[str, str], key: str, fallback: Path) -> Path:
    value = environment.get(key)
    if value:
        path = Path(value)
        if path.is_absolute():
            return path
    return fallback


def resolve_paths(
    *,
    home: Path | None = None,
    environment: Mapping[str, str] | None = None,
    platform: str | None = None,
) -> ApplicationPaths:
    """Resolve paths for the current platform without filesystem side effects."""
    home = Path.home() if home is None else home
    environment = os.environ if environment is None else environment
    platform = sys.platform if platform is None else platform
    if platform == "win32":
        roaming = _absolute_path(environment, "APPDATA", home / "AppData" / "Roaming")
        local = _absolute_path(environment, "LOCALAPPDATA", home / "AppData" / "Local")
        return ApplicationPaths(
            config_dir=roaming / "vencoverlay",
            cache_dir=local / "vencoverlay" / "cache",
            data_dir=local / "vencoverlay" / "data",
            state_dir=local / "vencoverlay" / "state",
        )
    return ApplicationPaths(
        config_dir=_absolute_path(environment, "XDG_CONFIG_HOME", home / ".config") / "vencoverlay",
        cache_dir=_absolute_path(environment, "XDG_CACHE_HOME", home / ".cache") / "vencoverlay",
        data_dir=_absolute_path(environment, "XDG_DATA_HOME", home / ".local" / "share")
        / "vencoverlay",
        state_dir=_absolute_path(environment, "XDG_STATE_HOME", home / ".local" / "state")
        / "vencoverlay",
    )
