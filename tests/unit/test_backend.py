"""Graphical backend policy tests without a display server."""

import pytest

from vencoverlay.backend import Backend, BackendError, configure_backend, select_backend


@pytest.mark.parametrize("desktop", ["GNOME", "ubuntu:GNOME", "gnome"])
def test_gnome_wayland(desktop: str) -> None:
    def unexpected_probe() -> bool:
        pytest.fail("GNOME must not probe Layer Shell")

    result = select_backend(
        environment={"XDG_SESSION_TYPE": "wayland", "XDG_CURRENT_DESKTOP": desktop},
        platform="linux",
        probe=unexpected_probe,
    )
    assert result.backend is Backend.X11


@pytest.mark.parametrize("desktop", ["KDE", "COSMIC", "sway"])
@pytest.mark.parametrize("available", [True, False])
def test_other_wayland(desktop: str, available: bool) -> None:
    result = select_backend(
        environment={"XDG_SESSION_TYPE": "wayland", "XDG_CURRENT_DESKTOP": desktop},
        platform="linux",
        probe=lambda: available,
    )
    assert result.backend is (Backend.LAYER_SHELL if available else Backend.X11)


def test_native_x11() -> None:
    result = select_backend(
        environment={"XDG_SESSION_TYPE": "x11", "WAYLAND_DISPLAY": "wayland-0"},
        platform="linux",
        probe=lambda: True,
    )
    assert result.backend is Backend.X11


def test_wayland_inference() -> None:
    result = select_backend(
        environment={"WAYLAND_DISPLAY": "wayland-0"}, platform="linux", probe=lambda: True
    )
    assert result.backend is Backend.LAYER_SHELL


def test_x11_override() -> None:
    result = select_backend(
        environment={"XDG_SESSION_TYPE": "wayland", "VENCOVERLAY_BACKEND": "x11"},
        platform="linux",
        probe=lambda: True,
    )
    assert result.backend is Backend.X11


def test_layer_shell_override() -> None:
    result = select_backend(
        environment={"XDG_SESSION_TYPE": "wayland", "VENCOVERLAY_BACKEND": "layershell"},
        platform="linux",
        probe=lambda: True,
    )
    assert result.backend is Backend.LAYER_SHELL


@pytest.mark.parametrize(
    "environment",
    [
        {"VENCOVERLAY_BACKEND": "invalid"},
        {"VENCOVERLAY_BACKEND": "layershell", "XDG_SESSION_TYPE": "x11"},
        {
            "VENCOVERLAY_BACKEND": "layershell",
            "XDG_SESSION_TYPE": "wayland",
            "XDG_CURRENT_DESKTOP": "GNOME",
        },
        {"VENCOVERLAY_BACKEND": "layershell", "XDG_SESSION_TYPE": "wayland"},
    ],
)
def test_invalid_override(environment: dict[str, str]) -> None:
    with pytest.raises(BackendError):
        select_backend(environment=environment, platform="linux", probe=lambda: False)


def test_unsupported_platform() -> None:
    with pytest.raises(BackendError, match="win32"):
        select_backend(environment={}, platform="win32")


@pytest.mark.parametrize("override, expected", [("x11", "x11"), ("layershell", "wayland")])
def test_configure_backend(override: str, expected: str) -> None:
    environment = {"XDG_SESSION_TYPE": "wayland", "VENCOVERLAY_BACKEND": override}
    selection = select_backend(environment=environment, platform="linux", probe=lambda: True)
    assert "GDK_BACKEND" not in environment
    configure_backend(selection, environment=environment)
    assert environment["GDK_BACKEND"] == expected
