"""Wire protocol regression tests."""

import json

import pytest

from vencoverlay.protocol import (
    MAX_MESSAGE_BYTES,
    FrameMessage,
    ProtocolError,
    SpeakingMessage,
    StateMessage,
    ToastMessage,
    parse_message,
)


def test_state() -> None:
    raw = b"""{"t":"state","channel":{"id":"1","name":"Call","guild":null,"icon":null},
    "users":[{"id":"2","name":"User","avatar":null,"me":true,"speaking":false,
    "mute":false,"deaf":false,"serverMuted":false,"video":true,"live":false}],
    "config":{"style":"mac","showNames":"speaking","position":"middle-right",
    "scale":1.2,"opacity":0.9,"accent":"#23a55a","avatarSize":"small",
    "onlySpeaking":true,"showWebcams":true,"webcamWidth":480}}"""
    message = parse_message(raw)
    assert isinstance(message, StateMessage)
    assert message.channel is not None
    assert message.channel.id == "1"
    assert message.users[0].video
    assert message.preferences.style == "mac"
    assert message.preferences.webcam_width == 480


def test_disconnected() -> None:
    message = parse_message(b'{"t":"state","channel":null,"users":[],"config":{}}')
    assert isinstance(message, StateMessage)
    assert message.channel is None
    assert not message.users
    assert message.preferences.scale == 1


def test_speaking() -> None:
    assert parse_message(b'{"t":"speaking","id":"2","on":true}') == SpeakingMessage("2", True)


def test_toast() -> None:
    assert parse_message(b'{"t":"toast","kind":"leave","text":"User left","avatar":null}') == (
        ToastMessage("leave", "User left", None)
    )


def test_frame() -> None:
    assert parse_message(b'{"t":"frame","id":"2","w":640,"h":480,"data":"YWJj"}') == (
        FrameMessage("2", 640, 480, b"abc")
    )


@pytest.mark.parametrize(
    "raw",
    [
        b"not JSON",
        b"\xff",
        b"[]",
        b'{"t":"unknown"}',
        b'{"t":"speaking","id":"2","on":1}',
        b'{"t":"speaking","id":2,"on":true}',
        b'{"t":"toast","kind":"other","text":"x"}',
        b'{"t":"frame","id":"2","w":true,"h":480,"data":"YWJj"}',
        b'{"t":"frame","id":"2","w":640,"h":480,"data":"!"}',
        b'{"t":"frame","id":"2","w":640,"h":480,"data":""}',
        b'{"t":"state","users":{}}',
    ],
)
def test_invalid_message(raw: bytes) -> None:
    with pytest.raises(ProtocolError):
        parse_message(raw)


@pytest.mark.parametrize(
    "config",
    [
        {"scale": True},
        {"scale": "1"},
        {"scale": 2},
        {"opacity": float("nan")},
        {"accent": "invalid"},
        {"position": "centre"},
        {"webcamWidth": 0},
    ],
)
def test_invalid_preferences(config: dict[str, object]) -> None:
    raw = json.dumps({"t": "state", "channel": None, "users": [], "config": config})
    with pytest.raises(ProtocolError):
        parse_message(raw.encode())


def test_message_limit() -> None:
    with pytest.raises(ProtocolError, match="Message too large"):
        parse_message(b" " * (MAX_MESSAGE_BYTES + 1))


@pytest.mark.parametrize("width,height", [(961, 540), (960, 1921)])
def test_frame_dimensions_limit(width: int, height: int) -> None:
    raw = json.dumps({"t": "frame", "id": "a", "w": width, "h": height, "data": "YWJj"})
    with pytest.raises(ProtocolError):
        parse_message(raw.encode())
