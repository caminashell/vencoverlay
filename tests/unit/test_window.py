from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from vencoverlay import window
from vencoverlay.backend import Backend, BackendSelection
from vencoverlay.window import GtkWindowSurface, WindowController


class Surface:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def show(self) -> None:
        self.calls.append("show")

    def hide(self) -> None:
        self.calls.append("hide")

    def raise_window(self) -> None:
        self.calls.append("raise")

    def destroy(self) -> None:
        self.calls.append("destroy")


def test_initially_hidden() -> None:
    surface = Surface()
    controller = WindowController(surface)
    controller.sync(visible=False)
    assert controller.maintain_stacking()
    assert surface.calls == []


def test_mapping_transitions() -> None:
    surface = Surface()
    controller = WindowController(surface)
    controller.sync(visible=True)
    controller.sync(visible=True)
    controller.sync(visible=False)
    controller.sync(visible=False)
    controller.sync(visible=True)
    assert surface.calls == ["show", "hide", "show"]


def test_raise_only_when_visible() -> None:
    surface = Surface()
    controller = WindowController(surface)
    controller.sync(visible=True)
    assert controller.maintain_stacking()
    controller.sync(visible=False)
    assert controller.maintain_stacking()
    assert surface.calls == ["show", "raise", "hide"]


def test_shutdown_is_idempotent() -> None:
    surface = Surface()
    controller = WindowController(surface)
    controller.sync(visible=True)
    controller.close()
    controller.close()
    controller.sync(visible=True)
    assert not controller.maintain_stacking()
    assert not controller.visible
    assert surface.calls == ["show", "destroy"]


@pytest.mark.parametrize(
    ("selection", "inset"),
    [
        (BackendSelection(Backend.X11, "GNOME Wayland", avoid_monitor_sized_window=True), 2),
        (BackendSelection(Backend.X11, "Native X11"), 0),
        (BackendSelection(Backend.X11, "Other XWayland"), 0),
        (BackendSelection(Backend.LAYER_SHELL, "Layer Shell"), 0),
    ],
)
def test_monitor_geometry(
    monkeypatch: pytest.MonkeyPatch, selection: BackendSelection, inset: int
) -> None:
    widget = Mock()
    monitor = Mock()
    monitor.get_geometry.return_value = SimpleNamespace(x=-2560, y=120, width=2560, height=1440)
    display = Mock()
    display.get_n_monitors.return_value = 2
    display.get_monitor.return_value = monitor
    layer = Mock()
    layer.is_supported.return_value = True
    modules: dict[str, object] = {
        "gi": Mock(),
        "gi.repository.Gtk": SimpleNamespace(Window=Mock(return_value=widget), WindowType=Mock()),
        "gi.repository.Gdk": SimpleNamespace(
            Display=SimpleNamespace(get_default=lambda: display), WindowTypeHint=Mock()
        ),
        "gi.repository.GtkLayerShell": layer,
        "cairo": Mock(),
    }

    def import_module(name: str) -> object:
        return modules[name]

    monkeypatch.setattr(window, "importlib", SimpleNamespace(import_module=import_module))
    surface = GtkWindowSurface(selection, 1)
    display.get_monitor.assert_called_once_with(1)
    widget.set_default_size.assert_called_once_with(2560 - inset, 1440 - inset)
    widget.resize.assert_called_once_with(2560 - inset, 1440 - inset)
    if selection.backend is Backend.LAYER_SHELL:
        widget.move.assert_not_called()
        layer.set_monitor.assert_called_once_with(widget, monitor)
    else:
        widget.move.assert_called_once_with(-2560, 120)
    # Refit on monitor change and again on show: use fresh geometry, not the old size.
    monitor.get_geometry.return_value = SimpleNamespace(x=0, y=0, width=1920, height=1080)
    surface.refit()
    surface.show()
    widget.set_default_size.assert_called_with(1920 - inset, 1080 - inset)
    widget.resize.assert_called_with(1920 - inset, 1080 - inset)
    if selection.backend is not Backend.LAYER_SHELL:
        widget.move.assert_called_with(0, 0)
    surface.destroy()
