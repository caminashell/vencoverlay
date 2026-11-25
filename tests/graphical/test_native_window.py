"""Opt-in native GTK smoke test in an isolated process."""

import importlib
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from time import monotonic, sleep
from typing import Protocol, cast

import pytest

from vencoverlay.backend import configure_backend, select_backend
from vencoverlay.config import Configuration
from vencoverlay.demo import AVATAR_URL, FRAME_HEIGHT, FRAME_WIDTH, FRAMES, DemoFeed, demo_avatar
from vencoverlay.image_decode import GdkPixbufDecoder, avatar_decoder, webcam_decoder
from vencoverlay.overlay_state import OverlayState
from vencoverlay.protocol import FrameMessage, StateMessage
from vencoverlay.renderer import CairoContext, OverlayRenderer
from vencoverlay.window import GtkWindowSurface, WindowController


class GI(Protocol):
    def require_version(self, namespace: str, version: str) -> None: ...
    def require_foreign(self, namespace: str) -> None: ...


class Gtk(Protocol):
    def init_check(self, argv: list[str] | None) -> tuple[bool, list[str]]: ...
    def events_pending(self) -> bool: ...
    def main_iteration_do(self, blocking: bool) -> bool: ...


class Widget(Protocol):
    def get_mapped(self) -> bool: ...
    def get_visible(self) -> bool: ...
    def get_app_paintable(self) -> bool: ...
    def get_accept_focus(self) -> bool: ...
    def get_focus_on_map(self) -> bool: ...
    def get_visual(self) -> object: ...


def wait_for(gtk: Gtk, condition: Callable[[], bool]) -> None:
    deadline = monotonic() + 3.0
    while monotonic() < deadline:
        for _ in range(100):
            if not gtk.events_pending():
                break
            gtk.main_iteration_do(False)
        if condition():
            return
        sleep(0.01)
    raise AssertionError("GTK transition or draw timed out")


def native_probe() -> None:
    selection = select_backend()
    configure_backend(selection)
    gi = cast(GI, importlib.import_module("gi"))
    gi.require_version("Gtk", "3.0")
    gi.require_version("Gdk", "3.0")
    gi.require_foreign("cairo")
    gtk = cast(Gtk, importlib.import_module("gi.repository.Gtk"))
    ready, _ = gtk.init_check(None)
    assert ready, "No suitable graphical display"
    now = 0.0
    state = OverlayState(clock=lambda: now)
    feed = DemoFeed(clock=lambda: now)
    now = 4.0
    for message in feed.messages():
        if isinstance(message, StateMessage):
            state.apply(message)
    now = 4.1
    state.tick()
    decoder = GdkPixbufDecoder()
    avatar = avatar_decoder(decoder)(demo_avatar(AVATAR_URL))
    decode_frame = webcam_decoder(decoder)
    frames = tuple(
        decode_frame(FrameMessage("a", FRAME_WIDTH, FRAME_HEIGHT, frame)) for frame in FRAMES
    )
    renderer = OverlayRenderer(
        Configuration(),
        state,
        lambda url: avatar if url == AVATAR_URL else None,
        lambda uid: frames[0] if uid == "a" else None,
    )
    surface = GtkWindowSurface(selection, 0)
    controller = WindowController(surface)
    widget = cast(Widget, surface.window)
    draws = 0
    destroyed = False

    def on_destroy(*_args: object) -> None:
        nonlocal destroyed
        destroyed = True

    def draw(cr: CairoContext, width: int, height: int) -> None:
        nonlocal draws
        assert width > 0 and height > 0
        state.tick()
        renderer.draw(cr, width, height)
        draws += 1

    try:
        surface.window.connect("destroy", on_destroy)
        surface.connect_draw(draw)
        assert not widget.get_visible()
        assert not widget.get_mapped()
        assert widget.get_app_paintable()
        assert not widget.get_accept_focus()
        assert not widget.get_focus_on_map()
        assert widget.get_visual() is not None
        controller.sync(visible=True)
        wait_for(gtk, lambda: widget.get_mapped() and draws > 0)
        previous = draws
        surface.queue_draw()
        wait_for(gtk, lambda: draws > previous)
        assert controller.maintain_stacking()
        controller.sync(visible=False)
        wait_for(gtk, lambda: not widget.get_mapped())
        assert not widget.get_visible()
        controller.sync(visible=True)
        wait_for(gtk, widget.get_mapped)
        controller.sync(visible=False)
        wait_for(gtk, lambda: not widget.get_mapped())
    finally:
        controller.close()
    assert destroyed
    assert controller.closed
    assert not controller.maintain_stacking()


@pytest.mark.graphical
def test_native_window_lifecycle() -> None:
    # GTK/GDK may already have selected a backend in another test. A new
    # process guarantees the production selection occurs before native imports.
    result = subprocess.run(
        [sys.executable, str(Path(__file__).resolve())],
        capture_output=True,
        text=True,
        timeout=25,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


if __name__ == "__main__":
    native_probe()
