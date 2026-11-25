"""Loopback transport for the Vencord bridge."""

import logging
import socket
from collections.abc import Callable
from threading import Event, Thread

from vencoverlay.protocol import MAX_MESSAGE_BYTES, Message, ProtocolError, parse_message

logger = logging.getLogger(__name__)


class CommunicationServer:
    """Own one client at a time; callbacks execute on the server worker.

    Callbacks must return promptly and must not call close(). The runtime is
    responsible for dispatching messages to its graphical thread.
    """

    def __init__(
        self,
        port: int,
        on_message: Callable[[Message], None],
        on_disconnect: Callable[[], None],
    ) -> None:
        self._on_message = on_message
        self._on_disconnect = on_disconnect
        self._stop = Event()
        self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        started = False
        try:
            self._listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._listener.bind(("127.0.0.1", port))
            self._listener.listen(1)
            self._listener.settimeout(0.2)
            self.port: int = self._listener.getsockname()[1]
            self._worker = Thread(target=self._serve, name="vencoverlay-server")
            self._worker.start()
            started = True
        finally:
            if not started:
                self._listener.close()

    def close(self) -> None:
        """Stop receiving, wait for callbacks to finish and release the port."""
        self._stop.set()
        self._worker.join()
        self._listener.close()

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                client, _ = self._listener.accept()
            except TimeoutError:
                continue
            except OSError:
                logger.exception("Listener failed")
                return
            with client:
                if self._stop.is_set():
                    return
                logger.debug("Bridge connected")
                try:
                    client.settimeout(0.2)
                    self._receive(client)
                except OSError:
                    logger.debug("Bridge connection ended")
                finally:
                    self._on_disconnect()
                    logger.debug("Bridge disconnected")

    def _receive(self, client: socket.socket) -> None:
        pending = bytearray()
        while not self._stop.is_set():
            try:
                chunk = client.recv(65536)
            except TimeoutError:
                continue
            if not chunk:
                return
            pending.extend(chunk)
            while not self._stop.is_set():
                end = pending.find(b"\n")
                if end < 0:
                    if len(pending) > MAX_MESSAGE_BYTES:
                        logger.warning("Closing bridge connection: message too large")
                        return
                    break
                if end > MAX_MESSAGE_BYTES:
                    logger.warning("Closing bridge connection: message too large")
                    return
                raw = bytes(pending[:end])
                del pending[: end + 1]
                try:
                    message = parse_message(raw)
                except ProtocolError:
                    logger.debug("Ignoring invalid bridge message")
                    continue
                self._on_message(message)
