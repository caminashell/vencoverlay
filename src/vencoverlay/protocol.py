"""Typed messages for the existing Vencord bridge wire protocol."""

import json
import math
import re
from base64 import b64decode
from dataclasses import dataclass
from typing import cast

MAX_MESSAGE_BYTES = 8 * 1024 * 1024
MAX_FRAME_WIDTH = 960
MAX_FRAME_HEIGHT = 1920
MAX_FRAME_BYTES = 2 * 1024 * 1024


class ProtocolError(ValueError):
    """A message cannot be accepted by the overlay."""


@dataclass(frozen=True, slots=True)
class Channel:
    id: str
    name: str
    guild: str | None
    icon: str | None


@dataclass(frozen=True, slots=True)
class Participant:
    id: str
    name: str
    avatar: str | None
    me: bool
    speaking: bool
    mute: bool
    deaf: bool
    server_muted: bool
    video: bool
    live: bool


@dataclass(frozen=True, slots=True)
class Preferences:
    position: str
    scale: float
    opacity: float
    accent: str
    style: str
    show_names: str
    avatar_size: str
    only_speaking: bool
    show_webcams: bool
    webcam_width: int


@dataclass(frozen=True, slots=True)
class StateMessage:
    channel: Channel | None
    users: tuple[Participant, ...]
    preferences: Preferences


@dataclass(frozen=True, slots=True)
class SpeakingMessage:
    id: str
    on: bool


@dataclass(frozen=True, slots=True)
class ToastMessage:
    kind: str
    text: str
    avatar: str | None


@dataclass(frozen=True, slots=True)
class FrameMessage:
    id: str
    width: int
    height: int
    jpeg: bytes


Message = StateMessage | SpeakingMessage | ToastMessage | FrameMessage


def _object(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ProtocolError("Expected a JSON object")
    return cast(dict[str, object], value)


def _string(value: object) -> str:
    if not isinstance(value, str):
        raise ProtocolError("Expected a string")
    return value


def _nullable(value: object) -> str | None:
    return None if value is None else _string(value)


def _bool(value: object) -> bool:
    if not isinstance(value, bool):
        raise ProtocolError("Expected a boolean")
    return value


def _integer(value: object, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ProtocolError("Integer outside allowed range")
    return value


def _number(value: object, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProtocolError("Expected a number")
    if not low <= value <= high or not math.isfinite(value):
        raise ProtocolError("Number outside allowed range")
    return float(value)


def _choice(value: object, choices: tuple[str, ...]) -> str:
    result = _string(value)
    if result not in choices:
        raise ProtocolError("Unsupported option")
    return result


def _channel(value: object) -> Channel | None:
    if value is None:
        return None
    data = _object(value)
    return Channel(
        _string(data.get("id")),
        _string(data.get("name")),
        _nullable(data.get("guild")),
        _nullable(data.get("icon")),
    )


def _participant(value: object) -> Participant:
    data = _object(value)
    return Participant(
        _string(data.get("id")),
        _string(data.get("name")),
        _nullable(data.get("avatar")),
        _bool(data.get("me")),
        _bool(data.get("speaking")),
        _bool(data.get("mute")),
        _bool(data.get("deaf")),
        _bool(data.get("serverMuted")),
        _bool(data.get("video")),
        _bool(data.get("live")),
    )


def _preferences(value: object) -> Preferences:
    data = _object(value)
    accent = _string(data.get("accent", "#23a55a"))
    if not re.fullmatch(r"#?[0-9a-fA-F]{6}", accent):
        raise ProtocolError("Invalid accent colour")
    return Preferences(
        _choice(
            data.get("position", "top-left"),
            (
                "top-left",
                "top-right",
                "bottom-left",
                "bottom-right",
                "middle-left",
                "middle-right",
            ),
        ),
        _number(data.get("scale", 1), 0.6, 1.8),
        _number(data.get("opacity", 0.85), 0.3, 1),
        accent,
        _choice(data.get("style", "discord"), ("discord", "mac")),
        _choice(data.get("showNames", "always"), ("always", "speaking", "never")),
        _choice(data.get("avatarSize", "large"), ("large", "small")),
        _bool(data.get("onlySpeaking", False)),
        _bool(data.get("showWebcams", True)),
        _integer(data.get("webcamWidth", 280), 160, 480),
    )


def parse_message(raw: bytes) -> Message:
    """Parse one UTF-8 JSON message; image decoding belongs to the image service."""
    if len(raw) > MAX_MESSAGE_BYTES:
        raise ProtocolError("Message too large")
    try:
        value: object = json.loads(raw.decode("utf-8"))
    except (ValueError, RecursionError) as exc:
        raise ProtocolError("Invalid JSON message") from exc
    data = _object(value)
    kind = data.get("t")
    if kind == "state":
        users = data.get("users")
        if not isinstance(users, list):
            raise ProtocolError("Expected a participant array")
        participants = tuple(_participant(user) for user in cast(list[object], users))
        if len({user.id for user in participants}) != len(participants):
            raise ProtocolError("Duplicate participant ID")
        return StateMessage(
            _channel(data.get("channel")), participants, _preferences(data.get("config", {}))
        )
    if kind == "speaking":
        return SpeakingMessage(_string(data.get("id")), _bool(data.get("on")))
    if kind == "toast":
        return ToastMessage(
            _choice(data.get("kind"), ("join", "leave")),
            _string(data.get("text")),
            _nullable(data.get("avatar")),
        )
    if kind == "frame":
        try:
            jpeg = b64decode(_string(data.get("data")), validate=True)
        except ValueError as exc:
            raise ProtocolError("Invalid frame encoding") from exc
        if not jpeg or len(jpeg) > MAX_FRAME_BYTES:
            raise ProtocolError("Invalid frame size")
        return FrameMessage(
            _string(data.get("id")),
            _integer(data.get("w"), 1, MAX_FRAME_WIDTH),
            _integer(data.get("h"), 1, MAX_FRAME_HEIGHT),
            jpeg,
        )
    raise ProtocolError("Unsupported message type")
