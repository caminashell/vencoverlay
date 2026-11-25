"""Application path resolution tests."""

from pathlib import Path

from vencoverlay.paths import resolve_paths


def test_linux_defaults(tmp_path: Path) -> None:
    home = tmp_path / "home"
    paths = resolve_paths(home=home, environment={}, platform="linux")
    assert paths.config_file == home / ".config/vencoverlay/config.toml"
    assert paths.avatar_cache == home / ".cache/vencoverlay/avatars"
    assert paths.data_dir == home / ".local/share/vencoverlay"
    assert paths.state_dir == home / ".local/state/vencoverlay"
    assert not home.exists()


def test_xdg_overrides(tmp_path: Path) -> None:
    environment = {
        "XDG_CONFIG_HOME": str(tmp_path / "config"),
        "XDG_CACHE_HOME": str(tmp_path / "cache"),
        "XDG_DATA_HOME": str(tmp_path / "data"),
        "XDG_STATE_HOME": str(tmp_path / "state"),
    }
    paths = resolve_paths(home=tmp_path, environment=environment, platform="linux")
    assert paths.config_dir == tmp_path / "config/vencoverlay"
    assert paths.cache_dir == tmp_path / "cache/vencoverlay"
    assert paths.data_dir == tmp_path / "data/vencoverlay"
    assert paths.state_dir == tmp_path / "state/vencoverlay"


def test_invalid_xdg_overrides(tmp_path: Path) -> None:
    paths = resolve_paths(
        home=tmp_path,
        environment={"XDG_CONFIG_HOME": "relative", "XDG_CACHE_HOME": ""},
        platform="linux",
    )
    assert paths.config_dir == tmp_path / ".config/vencoverlay"
    assert paths.cache_dir == tmp_path / ".cache/vencoverlay"


def test_windows_defaults(tmp_path: Path) -> None:
    paths = resolve_paths(home=tmp_path, environment={}, platform="win32")
    assert paths.config_file == tmp_path / "AppData/Roaming/vencoverlay/config.toml"
    assert paths.avatar_cache == tmp_path / "AppData/Local/vencoverlay/cache/avatars"


def test_windows_overrides(tmp_path: Path) -> None:
    paths = resolve_paths(
        home=tmp_path,
        environment={"APPDATA": str(tmp_path / "roaming"), "LOCALAPPDATA": str(tmp_path / "local")},
        platform="win32",
    )
    assert paths.config_dir == tmp_path / "roaming/vencoverlay"
    assert paths.data_dir == tmp_path / "local/vencoverlay/data"
    assert paths.state_dir == tmp_path / "local/vencoverlay/state"
