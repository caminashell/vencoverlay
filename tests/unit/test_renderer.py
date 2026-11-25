"""Renderer contract tests and native off-screen smoke tests (no GTK window)."""

import base64
import importlib
from collections.abc import Callable
from dataclasses import dataclass, replace
from types import SimpleNamespace
from typing import Protocol, cast
from unittest.mock import Mock

import pytest

from vencoverlay.config import Configuration
from vencoverlay.image_decode import GdkPixbufDecoder, Pixbuf
from vencoverlay.overlay_state import OverlayState
from vencoverlay.protocol import Channel, Participant, StateMessage, ToastMessage
from vencoverlay.renderer import (
    CairoContext,
    DrawingAPI,
    OverlayRenderer,
    block_origin,
    frame_ratio,
    hex_rgb,
    initials,
    name_color,
)


@dataclass(frozen=True)
class Image:
    width: int = 128
    height: int = 128

    def get_width(self) -> int:
        return self.width

    def get_height(self) -> int:
        return self.height


def sample_state(style: str = "discord", names: str = "always") -> OverlayState:
    state = OverlayState(clock=lambda: 1.0)
    preferences = replace(state.preferences, style=style, show_names=names)
    users = (
        Participant("a", "Alice Example", "avatar", False, True, False, False, False, True, True),
        Participant("b", "Bob Example", None, False, False, True, False, False, True, False),
        Participant("c", "Carol Example", None, False, False, False, True, False, False, False),
    )
    state.apply(StateMessage(Channel("voice", "Lounge", None, None), users, preferences))
    for person in state.people.values():
        person.alpha = 1.0
        person.glow = float(person.data.speaking)
    state.apply(ToastMessage("join", "Someone joined", None))
    state.apply(ToastMessage("leave", "Someone left", None))
    return state


class RecordingLayout:
    def __init__(self) -> None:
        self.text = ""

    def set_font_description(self, _description: object) -> None:
        pass

    def set_text(self, text: str, _length: int) -> None:
        self.text = text

    def set_ellipsize(self, _mode: object) -> None:
        pass

    def set_width(self, _width: int) -> None:
        pass

    def set_alignment(self, _alignment: object) -> None:
        pass

    def get_pixel_size(self) -> tuple[int, int]:
        return 40, 14


class RecordingText:
    """Deterministic text metrics and a record of actual text submitted for painting."""

    def __init__(self) -> None:
        self.drawn: list[str] = []

    def create_layout(self, _cr: CairoContext) -> RecordingLayout:
        return RecordingLayout()

    def show_layout(self, _cr: CairoContext, layout: RecordingLayout) -> None:
        self.drawn.append(layout.text)


def fake_drawing(text: RecordingText) -> DrawingAPI:
    return cast(
        DrawingAPI,
        SimpleNamespace(
            cairo=Mock(),
            pango=SimpleNamespace(
                SCALE=1024,
                Weight=Mock(),
                EllipsizeMode=Mock(),
                Alignment=Mock(),
                FontDescription=Mock(),
            ),
            pangocairo=text,
            gdk=Mock(),
        ),
    )


@pytest.mark.parametrize(
    ("position", "expected"),
    [
        ("top-left", (24, 24)),
        ("top-right", (776, 24)),
        ("middle-left", (24, 250)),
        ("middle-right", (776, 250)),
        ("bottom-left", (24, 476)),
        ("bottom-right", (776, 476)),
    ],
)
def test_block_origin(position: str, expected: tuple[int, int]) -> None:
    assert block_origin(position, 1000, 600, 200, 100, 24) == expected
    assert block_origin(position, 1000, 600, 200, 800, 24)[1] == 24


def test_avatar_helpers() -> None:
    assert initials("Alice Example Smith") == "AE"
    assert initials("  ") == "?"
    assert initials("Alice") == "A"
    assert hex_rgb("#ff8000") == (1, 128 / 255, 0)
    assert name_color("Alice") == name_color("Alice")
    assert all(0 <= value <= 1 for value in name_color("Alice"))
    assert name_color("Alice") != name_color("Bob")


def test_frame_ratio() -> None:
    assert frame_ratio(None) == 9 / 16
    assert frame_ratio(Image(320, 180)) == 9 / 16
    assert frame_ratio(Image(100, 200)) == 2


def test_hidden_renderer_only_clears() -> None:
    state = sample_state()
    state.toggle()
    text = RecordingText()
    native = fake_drawing(text)
    paint = Mock()
    context = Mock(spec=CairoContext, paint=paint)
    renderer = OverlayRenderer(
        Configuration(), state, lambda _: None, lambda _: None, native=native
    )
    renderer.draw(cast(CairoContext, context), 1000, 800)
    paint.assert_called_once_with()
    assert text.drawn == []
    state.disconnect()
    renderer.draw(cast(CairoContext, context), 1000, 800)
    assert paint.call_count == 2
    assert text.drawn == []


@pytest.mark.parametrize("style", ["discord", "mac"])
def test_styles_render_typed_state(style: str) -> None:
    state = sample_state(style)
    text = RecordingText()
    native = fake_drawing(text)
    requested: list[str] = []

    def frame(uid: str) -> Pixbuf | None:
        requested.append(uid)
        return Image(320, 180) if uid == "a" else None

    renderer = OverlayRenderer(
        Configuration(),
        state,
        lambda url: Image() if url else None,
        frame,
        clock=lambda: 2.0,
        native=native,
    )
    renderer.draw(cast(CairoContext, Mock(spec=CairoContext)), 1200, 1000)
    assert requested == ["a", "b"]  # one lookup per webcam, shared by layout and paint
    assert {"Alice Example", "Bob Example", "Carol Example", "LIVE"} <= set(text.drawn)
    assert {"Someone joined", "Someone left"} <= set(text.drawn)
    assert "Open the call view in Discord to see this cam" in text.drawn
    assert ("Lounge" in text.drawn) == (style == "mac")
    assert renderer.needs_continuous_redraw == (style == "mac")
    state.preferences = replace(state.preferences, show_webcams=False)
    requested.clear()
    text.drawn.clear()
    renderer.draw(cast(CairoContext, Mock(spec=CairoContext)), 1200, 1000)
    assert not requested
    assert "Open the call view in Discord to see this cam" not in text.drawn


@pytest.mark.parametrize("names", ["always", "speaking", "never"])
def test_discord_name_preferences(names: str) -> None:
    state = sample_state(names=names)
    state.preferences = replace(state.preferences, show_webcams=False)
    text = RecordingText()
    renderer = OverlayRenderer(
        Configuration(), state, lambda _: None, lambda _: None, native=fake_drawing(text)
    )
    renderer.draw(cast(CairoContext, Mock(spec=CairoContext)), 1200, 1000)
    assert ("Alice Example" in text.drawn) == (names != "never")
    assert ("Bob Example" in text.drawn) == (names == "always")
    assert ("Carol Example" in text.drawn) == (names == "always")


def test_participant_selection_respects_animation_and_camera_preferences() -> None:
    state = sample_state("mac")
    state.people["a"].alpha = 0.0
    state.order = ("c", "missing", "b", "a")
    renderer = OverlayRenderer(
        Configuration(), state, lambda _: None, lambda _: None, native=fake_drawing(RecordingText())
    )
    people, cameras = renderer.participants()
    assert [p.data.id for p in people] == ["c", "b"]
    assert [p.data.id for p in cameras] == ["b", "a"]
    state.preferences = replace(state.preferences, show_webcams=False)
    assert renderer.participants()[1] == []
    state.toggle()
    assert not renderer.needs_continuous_redraw


class Surface(Protocol):
    def flush(self) -> None: ...
    def get_data(self) -> memoryview: ...


class CairoModule(Protocol):
    FORMAT_ARGB32: object
    ImageSurface: Callable[[object, int, int], Surface]
    Context: Callable[[Surface], CairoContext]


@pytest.mark.parametrize("style", ["discord", "mac"])
def test_native_offscreen_rendering(style: str) -> None:
    pytest.importorskip("gi")
    pytest.importorskip("cairo")
    native = DrawingAPI.load()
    cairo = cast(CairoModule, importlib.import_module("cairo"))
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1200, 1000)
    context = cairo.Context(surface)
    state = sample_state(style)
    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGNgYGAAAAAEAAH2FzhV"
        "AAAAAElFTkSuQmCC"
    )
    image = GdkPixbufDecoder().decode(png, jpeg=False, max_dimension=128)
    renderer = OverlayRenderer(
        Configuration(),
        state,
        lambda url: image if url else None,
        lambda uid: image if uid == "a" else None,
        clock=lambda: 2.0,
        native=native,
    )
    renderer.draw(context, 1200, 1000)
    surface.flush()
    assert any(surface.get_data())
    state.toggle()
    renderer.draw(context, 1200, 1000)
    surface.flush()
    assert not any(surface.get_data())
