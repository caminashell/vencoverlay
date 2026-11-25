"""GTK window lifecycle, separate from drawing and voice state."""

import importlib
from collections.abc import Callable
from typing import Protocol, cast

from vencoverlay.backend import Backend, BackendSelection
from vencoverlay.renderer import CairoContext


class WindowSurface(Protocol):
    def show(self) -> None: ...
    def hide(self) -> None: ...
    def raise_window(self) -> None: ...
    def destroy(self) -> None: ...


class WindowController:
    """Own mapping transitions on the graphical thread."""

    def __init__(self, surface: WindowSurface) -> None:
        self._surface = surface
        self.visible = False
        self.closed = False

    def sync(self, *, visible: bool) -> None:
        if self.closed or visible == self.visible:
            return
        if visible:
            self._surface.show()
        else:
            self._surface.hide()
        self.visible = visible

    def maintain_stacking(self) -> bool:
        if self.closed:
            return False
        if self.visible:
            self._surface.raise_window()
        return True

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            self.visible = False
            self._surface.destroy()


class _Geometry(Protocol):
    x: int
    y: int
    width: int
    height: int


class _Monitor(Protocol):
    def get_geometry(self) -> _Geometry: ...


class _Display(Protocol):
    def get_n_monitors(self) -> int: ...
    def get_monitor(self, index: int) -> _Monitor | None: ...
    def get_primary_monitor(self) -> _Monitor | None: ...


class _Screen(Protocol):
    def get_rgba_visual(self) -> object | None: ...
    def is_composited(self) -> bool: ...
    def connect(self, signal: str, callback: Callable[..., object]) -> int: ...
    def disconnect(self, handler: int) -> None: ...


class _NativeWindow(Protocol):
    def input_shape_combine_region(self, region: object, x: int, y: int) -> None: ...
    def set_override_redirect(self, enabled: bool) -> None: ...
    def raise_(self) -> None: ...


class _Window(Protocol):
    def set_app_paintable(self, value: bool) -> None: ...
    def set_decorated(self, value: bool) -> None: ...
    def set_skip_taskbar_hint(self, value: bool) -> None: ...
    def set_skip_pager_hint(self, value: bool) -> None: ...
    def set_accept_focus(self, value: bool) -> None: ...
    def set_focus_on_map(self, value: bool) -> None: ...
    def set_title(self, title: str) -> None: ...
    def get_screen(self) -> _Screen: ...
    def set_visual(self, visual: object) -> None: ...
    def get_window(self) -> _NativeWindow | None: ...
    def set_keep_above(self, value: bool) -> None: ...
    def set_type_hint(self, hint: object) -> None: ...
    def move(self, x: int, y: int) -> None: ...
    def resize(self, width: int, height: int) -> None: ...
    def set_default_size(self, width: int, height: int) -> None: ...
    def connect(self, signal: str, callback: Callable[..., object]) -> int: ...
    def show_all(self) -> None: ...
    def hide(self) -> None: ...
    def destroy(self) -> None: ...
    def realize(self) -> None: ...
    def queue_draw(self) -> None: ...
    def get_allocated_width(self) -> int: ...
    def get_allocated_height(self) -> int: ...


class _WindowType(Protocol):
    TOPLEVEL: object
    POPUP: object


class _Gtk(Protocol):
    WindowType: _WindowType

    def Window(self, *, type: object) -> _Window: ...


class _DisplayFactory(Protocol):
    def get_default(self) -> _Display | None: ...


class _Hints(Protocol):
    NOTIFICATION: object


class _Gdk(Protocol):
    Display: _DisplayFactory
    WindowTypeHint: _Hints


class _GI(Protocol):
    def require_version(self, namespace: str, version: str) -> None: ...


class _Cairo(Protocol):
    def Region(self) -> object: ...


class _Edges(Protocol):
    TOP: object
    BOTTOM: object
    LEFT: object
    RIGHT: object


class _Layer(Protocol):
    OVERLAY: object


class _Keyboard(Protocol):
    NONE: object


class _LayerShell(Protocol):
    Edge: _Edges
    Layer: _Layer
    KeyboardMode: _Keyboard

    def is_supported(self) -> bool: ...
    def init_for_window(self, window: _Window) -> None: ...
    def set_layer(self, window: _Window, layer: object) -> None: ...
    def set_monitor(self, window: _Window, monitor: _Monitor) -> None: ...
    def set_anchor(self, window: _Window, edge: object, enabled: bool) -> None: ...
    def set_exclusive_zone(self, window: _Window, zone: int) -> None: ...
    def set_keyboard_mode(self, window: _Window, mode: object) -> None: ...
    def set_namespace(self, window: _Window, namespace: str) -> None: ...


class GtkWindowSurface:
    """Create an initially unmapped GTK3 surface after backend configuration."""

    def __init__(self, selection: BackendSelection, monitor_index: int) -> None:
        gi = cast(_GI, importlib.import_module("gi"))
        gi.require_version("Gtk", "3.0")
        gi.require_version("Gdk", "3.0")
        gtk = cast(_Gtk, importlib.import_module("gi.repository.Gtk"))
        gdk = cast(_Gdk, importlib.import_module("gi.repository.Gdk"))
        self._cairo = cast(_Cairo, importlib.import_module("cairo"))
        display = gdk.Display.get_default()
        if display is None:
            raise RuntimeError("No graphical display available")
        self._display = display
        self._monitor_index = monitor_index
        self._layer: _LayerShell | None = None
        if selection.backend is Backend.LAYER_SHELL:
            gi.require_version("GtkLayerShell", "0.1")
            layer = cast(_LayerShell, importlib.import_module("gi.repository.GtkLayerShell"))
            if not layer.is_supported():
                raise RuntimeError("Compositor does not support GTK Layer Shell")
            self._layer = layer
        self.window = gtk.Window(
            type=gtk.WindowType.POPUP if self._layer is None else gtk.WindowType.TOPLEVEL
        )
        self._screen = self.window.get_screen()
        self._handlers: list[int] = []
        try:
            self._configure(gdk)
        except (RuntimeError, ValueError, TypeError):
            self.destroy()
            raise

    def _configure(self, gdk: _Gdk) -> None:
        window = self.window
        window.set_app_paintable(True)
        window.set_decorated(False)
        window.set_skip_taskbar_hint(True)
        window.set_skip_pager_hint(True)
        window.set_accept_focus(False)
        window.set_focus_on_map(False)
        window.set_title("VencOverlay")
        visual = self._screen.get_rgba_visual()
        if visual is None or not self._screen.is_composited():
            raise RuntimeError("Transparent overlay requires a composited RGBA display")
        window.set_visual(visual)
        layer = self._layer
        if layer is not None:
            layer.init_for_window(window)
            layer.set_layer(window, layer.Layer.OVERLAY)
            for edge in (layer.Edge.TOP, layer.Edge.BOTTOM, layer.Edge.LEFT, layer.Edge.RIGHT):
                layer.set_anchor(window, edge, True)
            layer.set_exclusive_zone(window, -1)
            layer.set_keyboard_mode(window, layer.KeyboardMode.NONE)
            layer.set_namespace(window, "vencoverlay")
        else:
            window.set_keep_above(True)
            window.set_type_hint(gdk.WindowTypeHint.NOTIFICATION)
        self.refit()
        window.connect("realize", self._click_through)
        window.connect("size-allocate", self._click_through)
        for signal in ("monitors-changed", "size-changed"):
            self._handlers.append(self._screen.connect(signal, self.refit))

    def refit(self, *_args: object) -> None:
        count = self._display.get_n_monitors()
        if count == 0:
            return
        monitor = self._display.get_monitor(min(max(0, self._monitor_index), count - 1))
        if monitor is None:
            monitor = self._display.get_primary_monitor()
        if monitor is None:
            return
        geo = monitor.get_geometry()
        if self._layer is None:
            self.window.move(geo.x, geo.y)
        else:
            self._layer.set_monitor(self.window, monitor)
        self.window.set_default_size(geo.width, geo.height)
        self.window.resize(geo.width, geo.height)
        self._click_through()
        self.window.queue_draw()

    def _click_through(self, *_args: object) -> None:
        native = self.window.get_window()
        if native is not None:
            native.input_shape_combine_region(self._cairo.Region(), 0, 0)
            if self._layer is None:
                native.set_override_redirect(True)

    def connect_draw(self, draw: Callable[[CairoContext, int, int], None]) -> None:
        def on_draw(widget: _Window, cr: CairoContext) -> bool:
            draw(cr, widget.get_allocated_width(), widget.get_allocated_height())
            return False

        self.window.connect("draw", on_draw)

    def queue_draw(self) -> None:
        self.window.queue_draw()

    def show(self) -> None:
        self.refit()
        self.window.realize()
        self._click_through()
        self.window.show_all()
        self._click_through()
        self.raise_window()

    def hide(self) -> None:
        self.window.hide()

    def raise_window(self) -> None:
        if self._layer is None:
            native = self.window.get_window()
            if native is not None:
                native.raise_()

    def destroy(self) -> None:
        for handler in self._handlers:
            self._screen.disconnect(handler)
        self._handlers.clear()
        self.window.destroy()
