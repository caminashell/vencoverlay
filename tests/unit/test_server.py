"""Exercise the loopback transport without the installed overlay."""

import socket
from collections.abc import Generator
from queue import Queue

import pytest

from vencoverlay.protocol import Message, SpeakingMessage
from vencoverlay.server import CommunicationServer

WIRE = b'{"t":"speaking","id":"1","on":true}\n'


@pytest.fixture
def bridge() -> Generator[tuple[CommunicationServer, Queue[Message | None]]]:
    events: Queue[Message | None] = Queue()
    server = CommunicationServer(0, events.put, lambda: events.put(None))
    try:
        yield server, events
    finally:
        server.close()


def test_fragmented_and_combined_messages(
    bridge: tuple[CommunicationServer, Queue[Message | None]],
) -> None:
    server, events = bridge
    with socket.create_connection(("127.0.0.1", server.port), timeout=2) as client:
        client.sendall(WIRE[:8])
        client.sendall(WIRE[8:] + WIRE)
        assert events.get(timeout=2) == SpeakingMessage("1", True)
        assert events.get(timeout=2) == SpeakingMessage("1", True)
    assert events.get(timeout=2) is None


def test_invalid_message_and_reconnect(
    bridge: tuple[CommunicationServer, Queue[Message | None]],
) -> None:
    server, events = bridge
    with socket.create_connection(("127.0.0.1", server.port), timeout=2) as client:
        client.sendall(b"invalid\n" + WIRE)
        assert events.get(timeout=2) == SpeakingMessage("1", True)
    assert events.get(timeout=2) is None
    with socket.create_connection(("127.0.0.1", server.port), timeout=2) as client:
        client.sendall(WIRE)
        assert events.get(timeout=2) == SpeakingMessage("1", True)


def test_oversized_message(
    bridge: tuple[CommunicationServer, Queue[Message | None]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("vencoverlay.server.MAX_MESSAGE_BYTES", 64)
    server, events = bridge
    with socket.create_connection(("127.0.0.1", server.port), timeout=2) as client:
        client.sendall(b"x" * 65)
        assert events.get(timeout=2) is None
        assert client.recv(1) == b""


def test_close_active_connection(
    bridge: tuple[CommunicationServer, Queue[Message | None]],
) -> None:
    server, events = bridge
    with socket.create_connection(("127.0.0.1", server.port), timeout=2) as client:
        client.sendall(WIRE)
        assert events.get(timeout=2) == SpeakingMessage("1", True)
        server.close()
        assert events.get(timeout=2) is None
        assert client.recv(1) == b""


def test_bind_failure(
    bridge: tuple[CommunicationServer, Queue[Message | None]],
) -> None:
    server, events = bridge
    with pytest.raises(OSError):
        CommunicationServer(server.port, events.put, lambda: events.put(None))
