"""Exercise the integrated runtime without opening a real desktop window."""

import signal
import socket
from collections.abc import Callable, Generator
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from types import SimpleNamespace
from typing import cast
from unittest.mock import Mock

import pytest

from vencoverlay import runtime
from vencoverlay.backend import Backend, BackendSelection
from vencoverlay.config import Configuration
from vencoverlay.demo import DemoFeed
from vencoverlay.image_cache import AvatarCache, WebcamFrames
from vencoverlay.image_decode import Pixbuf
from vencoverlay.overlay_state import OverlayState
from vencoverlay.paths import resolve_paths
from vencoverlay.protocol import (
    Channel,
    FrameMessage,
    Message,
    Participant,
    SpeakingMessage,
    StateMessage,
    ToastMessage,
)
from vencoverlay.renderer import DrawingAPI, OverlayRenderer
from vencoverlay.runtime import (
    MAX_PENDING_CONTROLS,
    MAX_PENDING_FRAMES,
    MessageInbox,
    RuntimeController,
    run_runtime,
)
from vencoverlay.server import CommunicationServer
from vencoverlay.window import WindowController


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

    def queue_draw(self) -> None:
        self.calls.append("draw")


class Image:
    def get_width(self) -> int:
        return 1

    def get_height(self) -> int:
        return 1


class Clock:
    now = 0.0

    def __call__(self) -> float:
        return self.now


def snapshot(channel: str | None = "voice", *, video: bool = True) -> StateMessage:
    return StateMessage(
        Channel(channel, "Voice", None, None) if channel is not None else None,
        (Participant("a", "Alice", None, False, False, False, False, False, video, False),),
        OverlayState().preferences,
    )


def frame(uid: str = "a", data: bytes = b"jpeg") -> FrameMessage:
    return FrameMessage(uid, 1, 1, data)


def test_inbox_coalesces_frames_preserving_control_order() -> None:
    inbox = MessageInbox()
    state = snapshot()
    speaking = SpeakingMessage("a", True)
    inbox.put(frame())  # No state yet.
    inbox.put(state)
    inbox.put(frame())
    inbox.put(speaking)
    latest = frame(data=b"latest")
    inbox.put(latest)
    assert inbox.drain() == (state, speaking, latest)


def test_inbox_bounds_frames_and_drain_work() -> None:
    inbox = MessageInbox()
    inbox.put(snapshot())
    for i in range(MAX_PENDING_FRAMES + 3):
        inbox.put(frame(str(i)))
    first = inbox.drain()
    assert first == (snapshot(), frame("3"), frame("4"))
    remaining = (*inbox.drain(), *inbox.drain(), *inbox.drain())
    assert remaining == tuple(frame(str(i)) for i in range(5, MAX_PENDING_FRAMES + 3))
    assert not inbox.drain()


def test_inbox_disconnect_and_channel_change_are_barriers() -> None:
    inbox = MessageInbox()
    inbox.put(snapshot())
    inbox.put(frame())
    inbox.put(snapshot("other"))
    assert inbox.drain() == (snapshot(), snapshot("other"))
    inbox.put(frame())
    inbox.disconnect()
    inbox.put(SpeakingMessage("a", True))
    inbox.put(frame())
    inbox.put(snapshot())
    assert inbox.drain() == (None, snapshot())


def test_inbox_recovers_from_control_overload() -> None:
    inbox = MessageInbox()
    inbox.put(snapshot())
    for _ in range(MAX_PENDING_CONTROLS):
        inbox.put(SpeakingMessage("a", True))
    inbox.put(frame())
    assert inbox.drain() == (None,)
    inbox.put(snapshot())
    inbox.put(frame())
    assert inbox.drain() == (snapshot(), frame())


@pytest.fixture
def controller(tmp_path: Path) -> Generator[tuple[RuntimeController, Surface, Clock]]:
    clock = Clock()
    state = OverlayState(clock=clock)
    surface = Surface()
    avatars = AvatarCache[Pixbuf](tmp_path, lambda _data: Image())
    frames = WebcamFrames[Pixbuf](lambda _message: Image(), clock=clock)
    renderer = OverlayRenderer(
        Configuration(),
        state,
        avatars.get,
        frames.get,
        clock=clock,
        native=cast(DrawingAPI, Mock()),
    )
    window = WindowController(surface)
    control = RuntimeController(MessageInbox(), state, avatars, frames, renderer, window, surface)
    try:
        yield control, surface, clock
    finally:
        window.close()
        avatars.close()


def test_runtime_visibility_and_disconnect(
    controller: tuple[RuntimeController, Surface, Clock],
) -> None:
    control, surface, _ = controller
    assert control.tick()
    assert not surface.calls
    control.inbox.put(snapshot())
    assert control.tick()
    assert surface.calls == ["show", "draw"]
    assert control.toggle()
    assert surface.calls[-1] == "hide"
    assert control.toggle()
    assert surface.calls[-2:] == ["show", "draw"]
    control.inbox.disconnect()
    assert control.tick()
    assert surface.calls[-1] == "hide"
    assert control.state.channel is None


def test_runtime_frame_lifecycle(controller: tuple[RuntimeController, Surface, Clock]) -> None:
    control, surface, clock = controller
    assert not control.apply(frame())
    control.inbox.put(snapshot())
    control.inbox.put(frame())
    control.tick()
    assert control.frames.get("a") is not None
    control.inbox.put(snapshot(video=False))
    control.tick()
    assert control.frames.get("a") is None
    assert not control.apply(frame())
    control.inbox.put(snapshot())
    control.inbox.put(frame())
    control.tick()
    clock.now = 5.0
    control.tick()
    assert control.frames.get("a") is None
    assert surface.calls[-1] == "draw"
    control.inbox.put(frame())
    control.tick()
    assert control.frames.get("a") is not None
    control.inbox.put(snapshot("other"))
    control.tick()
    assert control.frames.get("a") is None


def test_runtime_animation_and_toasts(controller: tuple[RuntimeController, Surface, Clock]) -> None:
    control, surface, clock = controller
    state = snapshot()
    control.inbox.put(replace(state, preferences=replace(state.preferences, style="mac")))
    control.inbox.put(SpeakingMessage("a", True))
    control.inbox.put(ToastMessage("join", "Alice joined", None))
    clock.now = 0.1
    control.tick()
    assert control.state.people["a"].glow == 1
    assert control.state.toasts
    clock.now = 5.0
    control.tick()
    assert not control.state.toasts
    assert surface.calls[-1] == "draw"


class FakeLoop:
    PRIORITY_DEFAULT = 0

    def __init__(self) -> None:
        self.sources: dict[int, Callable[[], bool]] = {}
        self.signals: dict[int, Callable[[], bool]] = {}
        self.next_id = 0
        self.ready = True
        self.stopped = False
        self.main_action: Callable[[], None] = lambda: None

    def init_check(self, _argv: list[str] | None) -> tuple[bool, list[str]]:
        return self.ready, []

    def timeout_add(self, _interval: int, callback: Callable[[], bool]) -> int:
        self.next_id += 1
        self.sources[self.next_id] = callback
        return self.next_id

    def unix_signal_add(self, _priority: int, signum: int, callback: Callable[[], bool]) -> int:
        self.signals[signum] = callback
        return self.timeout_add(0, callback)

    def source_remove(self, tag: int) -> bool:
        del self.sources[tag]
        return True

    def main(self) -> None:
        self.main_action()

    def main_quit(self) -> None:
        self.stopped = True


@pytest.fixture
def native_runtime(monkeypatch: pytest.MonkeyPatch) -> tuple[FakeLoop, Mock, Mock]:
    loop = FakeLoop()
    gi = Mock()
    surface = Mock()
    server = Mock()
    server.port = 12345
    modules: dict[str, object] = {
        "gi": gi,
        "gi.repository.Gtk": loop,
        "gi.repository.GLib": loop,
    }

    def import_module(name: str) -> object:
        if name == "gi.repository.Gtk":
            # Every rendering namespace must be pinned before the first GTK import.
            for namespace, version in (
                ("Gtk", "3.0"),
                ("Gdk", "3.0"),
                ("GdkPixbuf", "2.0"),
                ("Pango", "1.0"),
                ("PangoCairo", "1.0"),
            ):
                gi.require_version.assert_any_call(namespace, version)
            gi.require_foreign.assert_called_once_with("cairo")
        return modules[name]

    def server_factory(
        _port: int, on_message: Callable[[Message], None], _on_disconnect: Callable[[], None]
    ) -> Mock:
        on_message(snapshot())
        return server

    monkeypatch.setattr(runtime, "importlib", SimpleNamespace(import_module=import_module))
    monkeypatch.setattr(runtime, "GdkPixbufDecoder", Mock())
    monkeypatch.setattr(runtime, "GtkWindowSurface", Mock(return_value=surface))
    monkeypatch.setattr(runtime, "OverlayRenderer", Mock())
    monkeypatch.setattr(runtime, "CommunicationServer", server_factory)
    monkeypatch.setenv("GDK_BACKEND", "test")
    return loop, surface, server


def test_runtime_startup_and_cleanup(
    native_runtime: tuple[FakeLoop, Mock, Mock], tmp_path: Path
) -> None:
    loop, surface, server = native_runtime

    def main_action() -> None:
        assert loop.sources[1]()
        surface.show.assert_called_once()
        assert loop.signals[signal.SIGUSR1]()
        surface.hide.assert_called_once()
        assert loop.signals[signal.SIGTERM]()
        assert loop.stopped

    loop.main_action = main_action
    assert (
        run_runtime(
            Configuration(), resolve_paths(home=tmp_path), BackendSelection(Backend.X11, "test")
        )
        == 0
    )
    assert not loop.sources
    server.close.assert_called_once()
    surface.destroy.assert_called_once()


def test_runtime_callback_failure_stops_and_cleans_up(
    native_runtime: tuple[FakeLoop, Mock, Mock], tmp_path: Path
) -> None:
    loop, surface, server = native_runtime
    surface.show.side_effect = RuntimeError("test failure")

    def main_action() -> None:
        assert loop.sources[1]()
        assert loop.stopped

    loop.main_action = main_action
    assert (
        run_runtime(
            Configuration(), resolve_paths(home=tmp_path), BackendSelection(Backend.X11, "test")
        )
        == 1
    )
    assert not loop.sources
    server.close.assert_called_once()
    surface.destroy.assert_called_once()


def test_runtime_bind_failure_releases_window(
    native_runtime: tuple[FakeLoop, Mock, Mock],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loop, surface, _ = native_runtime
    monkeypatch.setattr(runtime, "CommunicationServer", Mock(side_effect=OSError("in use")))
    with pytest.raises(OSError, match="in use"):
        run_runtime(
            Configuration(), resolve_paths(home=tmp_path), BackendSelection(Backend.X11, "test")
        )
    assert not loop.sources
    surface.destroy.assert_called_once()


@pytest.mark.integration
def test_real_bridge_to_runtime(controller: tuple[RuntimeController, Surface, Clock]) -> None:
    control, surface, _ = controller
    received = Event()
    disconnected = Event()

    def on_message(message: Message) -> None:
        control.inbox.put(message)
        received.set()

    def on_disconnect() -> None:
        control.inbox.disconnect()
        disconnected.set()

    server = CommunicationServer(0, on_message, on_disconnect)
    try:
        with socket.create_connection(("127.0.0.1", server.port), timeout=2) as client:
            client.sendall(b'{"t":"state","channel":{"id":"voice","name":"Voice"},"users":[]}\n')
            assert received.wait(2)
            assert control.state.channel is None  # Network thread must not mutate state.
            control.tick()
            assert control.state.channel == Channel("voice", "Voice", None, None)
            assert surface.calls == ["show", "draw"]
        assert disconnected.wait(2)
        control.tick()
        assert control.state.channel is None
        assert surface.calls[-1] == "hide"
    finally:
        server.close()


def test_demo_runtime_does_not_bind_or_use_persistent_cache(
    native_runtime: tuple[FakeLoop, Mock, Mock],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loop, surface, _ = native_runtime
    server_factory = Mock(side_effect=AssertionError("Demo must not start the server"))
    monkeypatch.setattr(runtime, "CommunicationServer", server_factory)
    clock = Clock()
    feed = DemoFeed(clock=clock)
    monkeypatch.setattr(runtime, "DemoFeed", lambda: feed)
    paths = resolve_paths(home=tmp_path, environment={})
    temporary: list[Path] = []
    original = runtime.TemporaryDirectory

    def temporary_directory(*, prefix: str) -> TemporaryDirectory[str]:
        directory = original(prefix=prefix, dir=tmp_path)
        temporary.append(Path(directory.name))
        return directory

    monkeypatch.setattr(runtime, "TemporaryDirectory", temporary_directory)

    def main_action() -> None:
        assert loop.sources[1]()
        surface.show.assert_not_called()
        clock.now = 4.0
        # Skip native frame decoding in this simulated GTK test.
        assert loop.sources[1]()
        surface.show.assert_called_once()
        clock.now = 32.0
        assert loop.sources[1]()
        surface.hide.assert_called_once()
        assert loop.signals[signal.SIGTERM]()

    # Mock the actual frame decoder rather than the controller or queue.
    def fake_webcam_decoder(_loader: runtime.GdkPixbufDecoder) -> Callable[[FrameMessage], Pixbuf]:
        def decode(_message: FrameMessage) -> Pixbuf:
            return Image()

        return decode

    monkeypatch.setattr(runtime, "webcam_decoder", fake_webcam_decoder)
    loop.main_action = main_action
    assert (
        run_runtime(Configuration(), paths, BackendSelection(Backend.X11, "test"), demo=True) == 0
    )
    server_factory.assert_not_called()
    surface.destroy.assert_called_once()
    assert not paths.avatar_cache.exists()
    assert len(temporary) == 1
    assert not temporary[0].exists()
    assert not loop.sources
