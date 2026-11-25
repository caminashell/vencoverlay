"""Dispatch bridge messages on the graphical thread and own runtime resources."""

import importlib
import logging
import signal
from collections import deque
from collections.abc import Callable
from contextlib import ExitStack
from os import getpid
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Lock
from typing import Protocol, cast

from vencoverlay.backend import BackendSelection, configure_backend
from vencoverlay.config import Configuration
from vencoverlay.demo import DemoFeed, demo_avatar
from vencoverlay.image_cache import AvatarCache, WebcamFrames
from vencoverlay.image_decode import GdkPixbufDecoder, Pixbuf, avatar_decoder, webcam_decoder
from vencoverlay.overlay_state import ANIMATION_INTERVAL_MS, OverlayState
from vencoverlay.paths import ApplicationPaths
from vencoverlay.protocol import FrameMessage, Message, StateMessage
from vencoverlay.renderer import CairoContext, OverlayRenderer
from vencoverlay.server import CommunicationServer
from vencoverlay.window import GtkWindowSurface, WindowController

logger = logging.getLogger(__name__)
MAX_PENDING_CONTROLS = 32
MAX_PENDING_FRAMES = 8
MAX_EVENTS_PER_TICK = 32
MAX_FRAMES_PER_TICK = 2


class MessageInbox:
    """Single-producer inbox, with a bounded owner-thread drain.

    None is a disconnect barrier. Under control overload discard pending state
    and wait for a fresh state snapshot rather than silently losing transitions.
    Frames are expendable and only the latest pending frame per user is kept.
    """

    def __init__(self) -> None:
        self._lock = Lock()
        self._pending: deque[Message | None] = deque()
        self._channel: str | None = None
        self._synchronised = False

    def disconnect(self) -> None:
        with self._lock:
            self._reset()

    def _reset(self) -> None:
        self._pending.clear()
        self._pending.append(None)
        self._channel = None
        self._synchronised = False

    def put(self, message: Message) -> None:
        with self._lock:
            if isinstance(message, FrameMessage):
                if not self._synchronised or self._channel is None:
                    return
                self._pending = deque(
                    item
                    for item in self._pending
                    if not isinstance(item, FrameMessage) or item.id != message.id
                )
                frames = [item for item in self._pending if isinstance(item, FrameMessage)]
                if len(frames) >= MAX_PENDING_FRAMES:
                    self._pending.remove(frames[0])
            else:
                controls = sum(not isinstance(item, FrameMessage) for item in self._pending)
                if controls >= MAX_PENDING_CONTROLS:
                    self._reset()
                    logger.warning("Bridge dispatch overloaded; waiting for fresh state")
                if isinstance(message, StateMessage):
                    channel = message.channel.id if message.channel is not None else None
                    if channel != self._channel:
                        self._pending = deque(
                            item for item in self._pending if not isinstance(item, FrameMessage)
                        )
                    self._channel = channel
                    self._synchronised = True
                elif not self._synchronised or self._channel is None:
                    return
            self._pending.append(message)

    def drain(self) -> tuple[Message | None, ...]:
        with self._lock:
            batch: list[Message | None] = []
            frames = 0
            while self._pending and len(batch) < MAX_EVENTS_PER_TICK:
                if isinstance(self._pending[0], FrameMessage):
                    if frames >= MAX_FRAMES_PER_TICK:
                        break
                    frames += 1
                batch.append(self._pending.popleft())
            return tuple(batch)


class RuntimeSurface(Protocol):
    def queue_draw(self) -> None: ...


class RuntimeController:
    """Coordinate existing state, image services and window on the GTK thread."""

    def __init__(
        self,
        inbox: MessageInbox,
        state: OverlayState,
        avatars: AvatarCache[Pixbuf],
        frames: WebcamFrames[Pixbuf],
        renderer: OverlayRenderer,
        window: WindowController,
        surface: RuntimeSurface,
    ) -> None:
        self.inbox = inbox
        self.state = state
        self.avatars = avatars
        self.frames = frames
        self.renderer = renderer
        self.window = window
        self.surface = surface

    def apply(self, message: Message | None) -> bool:
        if message is None:
            self.state.disconnect()
            self.frames.clear()
            return True
        if isinstance(message, FrameMessage):
            person = self.state.people.get(message.id)
            if person is None or not person.data.video:
                return False
            return self.frames.apply(message, {message.id})
        previous_channel = self.state.channel
        self.state.apply(message)
        if isinstance(message, StateMessage):
            current_channel = self.state.channel
            if (
                current_channel is None
                or previous_channel is None
                or previous_channel.id != current_channel.id
            ):
                self.frames.clear()
            else:
                self.frames.prune(self.video_participants())
        return True

    def video_participants(self) -> set[str]:
        return {uid for uid, person in self.state.people.items() if person.data.video}

    def tick(self) -> bool:
        changed = False
        for message in self.inbox.drain():
            changed = self.apply(message) or changed
        changed = self.state.tick() or changed
        changed = self.avatars.poll() or changed
        changed = self.frames.prune(self.video_participants()) or changed
        self.window.sync(visible=self.state.visible)
        if self.state.visible and (changed or self.renderer.needs_continuous_redraw):
            self.surface.queue_draw()
        return True

    def toggle(self) -> bool:
        self.state.toggle()
        self.window.sync(visible=self.state.visible)
        if self.state.visible:
            self.surface.queue_draw()
        return True


class _GI(Protocol):
    def require_version(self, namespace: str, version: str) -> None: ...
    def require_foreign(self, namespace: str) -> None: ...


class _Gtk(Protocol):
    def init_check(self, argv: list[str] | None) -> tuple[bool, list[str]]: ...
    def main(self) -> None: ...
    def main_quit(self) -> None: ...


class _GLib(Protocol):
    PRIORITY_DEFAULT: int

    def timeout_add(self, interval: int, callback: Callable[[], bool]) -> int: ...
    def unix_signal_add(self, priority: int, signum: int, callback: Callable[[], bool]) -> int: ...
    def source_remove(self, tag: int) -> bool: ...


def run_runtime(
    config: Configuration,
    paths: ApplicationPaths,
    selection: BackendSelection,
    *,
    demo: bool = False,
) -> int:
    """Acquire resources in order and release them on every exit path."""
    configure_backend(selection)
    gi = cast(_GI, importlib.import_module("gi"))
    # Pin all drawing namespaces before importing GTK: compiled GI modules
    # may load their dependencies earlier than the individual adapters do.
    for namespace, version in (
        ("Gtk", "3.0"),
        ("Gdk", "3.0"),
        ("GdkPixbuf", "2.0"),
        ("Pango", "1.0"),
        ("PangoCairo", "1.0"),
    ):
        gi.require_version(namespace, version)
    gi.require_foreign("cairo")
    gtk = cast(_Gtk, importlib.import_module("gi.repository.Gtk"))
    glib = cast(_GLib, importlib.import_module("gi.repository.GLib"))
    ready, _ = gtk.init_check(None)
    if not ready:
        raise RuntimeError(f"Cannot initialise GTK using {selection.gdk_backend}")
    with ExitStack() as cleanup:
        inbox = MessageInbox()
        state = OverlayState()
        decoder = GdkPixbufDecoder()
        if demo:
            directory = Path(cleanup.enter_context(TemporaryDirectory(prefix="vencoverlay-demo-")))
            avatars = AvatarCache(directory, avatar_decoder(decoder), fetch=demo_avatar)
        else:
            avatars = AvatarCache(paths.avatar_cache, avatar_decoder(decoder))
        cleanup.callback(avatars.close)
        frames = WebcamFrames(webcam_decoder(decoder))
        surface = GtkWindowSurface(selection, config.monitor)
        window = WindowController(surface)
        cleanup.callback(window.close)
        renderer = OverlayRenderer(config, state, avatars.get, frames.get)
        runtime = RuntimeController(inbox, state, avatars, frames, renderer, window, surface)
        status = 0

        def stop() -> bool:
            gtk.main_quit()
            return True

        def guarded(callback: Callable[[], bool]) -> Callable[[], bool]:
            def invoke() -> bool:
                nonlocal status
                try:
                    return callback()
                except Exception:
                    logger.exception("Graphical runtime callback failed")
                    status = 1
                    return stop()

            return invoke

        def draw(cr: CairoContext, width: int, height: int) -> None:
            nonlocal status
            try:
                renderer.draw(cr, width, height)
            except Exception:
                logger.exception("Overlay drawing failed")
                status = 1
                stop()

        surface.connect_draw(draw)
        update: Callable[[], bool] = runtime.tick
        if demo:
            feed = DemoFeed()

            def demo_tick() -> bool:
                for message in feed.messages():
                    inbox.put(message)
                return runtime.tick()

            update = demo_tick
            logger.info(
                "Offline demo mode (%s); PID %d; no communication port opened",
                selection.reason,
                getpid(),
            )
        else:
            server = CommunicationServer(config.port, inbox.put, inbox.disconnect)
            cleanup.callback(server.close)
            logger.info("Listening on 127.0.0.1:%d (%s)", server.port, selection.reason)
        for interval, callback in (
            (ANIMATION_INTERVAL_MS, update),
            (500, window.maintain_stacking),
        ):
            cleanup.callback(glib.source_remove, glib.timeout_add(interval, guarded(callback)))
        for signum, callback in (
            (signal.SIGINT, stop),
            (signal.SIGTERM, stop),
            (signal.SIGUSR1, runtime.toggle),
        ):
            cleanup.callback(
                glib.source_remove,
                glib.unix_signal_add(glib.PRIORITY_DEFAULT, signum, guarded(callback)),
            )
        gtk.main()
        logger.info("Stopping VencOverlay")
        return status
