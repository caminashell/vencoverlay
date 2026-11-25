"""Select the Linux graphical backend before initialising GTK."""

import importlib
import os
import sys
from collections.abc import Callable, Mapping, MutableMapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, cast


class Backend(StrEnum):
    X11 = "x11"
    LAYER_SHELL = "layershell"


class BackendError(RuntimeError):
    """The requested graphical backend cannot be selected."""


@dataclass(frozen=True, slots=True)
class BackendSelection:
    backend: Backend
    reason: str

    @property
    def gdk_backend(self) -> str:
        return "wayland" if self.backend is Backend.LAYER_SHELL else "x11"


class _GI(Protocol):
    def require_version(self, namespace: str, version: str) -> None: ...


def layer_shell_available() -> bool:
    """Check the Layer Shell typelib without initialising a GTK display."""
    try:
        gi = cast(_GI, importlib.import_module("gi"))
        gi.require_version("GtkLayerShell", "0.1")
    except (ImportError, ValueError, OSError):
        return False
    return True


def select_backend(
    *,
    environment: Mapping[str, str] | None = None,
    platform: str | None = None,
    probe: Callable[[], bool] = layer_shell_available,
) -> BackendSelection:
    """Choose a backend without changing the environment or opening a display."""
    environment = os.environ if environment is None else environment
    platform = sys.platform if platform is None else platform
    if platform != "linux":
        raise BackendError(f"Graphical backend not implemented for {platform}")
    override = environment.get("VENCOVERLAY_BACKEND", "auto").strip().lower()
    if override not in ("auto", "x11", "layershell"):
        raise BackendError("VENCOVERLAY_BACKEND must be auto, x11 or layershell")
    session = environment.get("XDG_SESSION_TYPE", "").lower()
    wayland = session == "wayland" or (not session and bool(environment.get("WAYLAND_DISPLAY")))
    gnome = "GNOME" in environment.get("XDG_CURRENT_DESKTOP", "").upper()
    if override == "x11":
        return BackendSelection(Backend.X11, "Explicit X11 override")
    if override == "layershell":
        if not wayland or gnome:
            raise BackendError("Layer Shell requires a supported non-GNOME Wayland session")
        if not probe():
            raise BackendError("GTK Layer Shell typelib unavailable")
        return BackendSelection(Backend.LAYER_SHELL, "Explicit Layer Shell override")
    if not wayland:
        return BackendSelection(Backend.X11, "X11 or unspecified session")
    if gnome:
        return BackendSelection(Backend.X11, "GNOME Wayland requires XWayland")
    if probe():
        return BackendSelection(Backend.LAYER_SHELL, "GTK Layer Shell typelib available")
    return BackendSelection(Backend.X11, "GTK Layer Shell unavailable; XWayland fallback")


def configure_backend(
    selection: BackendSelection, *, environment: MutableMapping[str, str] | None = None
) -> None:
    """Apply the selected GDK backend before GTK/GDK display initialisation."""
    environment = os.environ if environment is None else environment
    environment["GDK_BACKEND"] = selection.gdk_backend
