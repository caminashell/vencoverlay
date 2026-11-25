"""Verify the offline demonstration timeline and embedded image limits."""

import pytest

from vencoverlay.demo import AVATAR_URL, FRAMES, DemoFeed, demo_avatar
from vencoverlay.image_decode import checked_dimensions
from vencoverlay.protocol import FrameMessage, StateMessage, ToastMessage


class Clock:
    now = 0.0

    def __call__(self) -> float:
        return self.now


def test_demo_cycle_and_events() -> None:
    clock = Clock()
    feed = DemoFeed(clock=clock)
    initial = feed.messages()
    assert len(initial) == 1
    assert isinstance(initial[0], StateMessage)
    assert initial[0].channel is None
    assert not feed.messages()
    clock.now = 4.0
    first = feed.messages()
    assert isinstance(first[0], StateMessage)
    assert first[0].channel is not None
    assert first[0].preferences.style == "discord"
    assert len(first[0].users) == 4
    assert first[0].users[0].speaking
    assert first[0].users[1].live
    assert first[0].users[2].mute
    assert first[0].users[3].deaf
    assert isinstance(first[1], FrameMessage)
    assert not feed.messages()
    clock.now = 4.25
    next_frame = feed.messages()
    assert len(next_frame) == 1
    assert isinstance(next_frame[0], FrameMessage)
    assert next_frame[0].jpeg != first[1].jpeg
    clock.now = 8.0
    joined = feed.messages()
    assert ToastMessage("join", "Ellis joined", None) in joined
    clock.now = 14.0
    assert ToastMessage("leave", "Ellis left", None) in feed.messages()
    clock.now = 18.0
    mac = feed.messages()
    assert isinstance(mac[0], StateMessage)
    assert mac[0].preferences.style == "mac"
    clock.now = 32.0
    idle = feed.messages()
    assert len(idle) == 1
    assert isinstance(idle[0], StateMessage)
    assert idle[0].channel is None
    assert not idle[0].users
    clock.now = 40.0
    restarted = feed.messages()
    assert isinstance(restarted[0], StateMessage)
    assert restarted[0].channel is not None
    assert restarted[0].preferences.style == "discord"


def test_demo_images_match_protocol_limits() -> None:
    assert checked_dimensions(demo_avatar(AVATAR_URL), jpeg=False) == (64, 64)
    for frame in FRAMES:
        assert checked_dimensions(frame, jpeg=True) == (160, 90)
    with pytest.raises(ValueError, match="Unknown demo avatar"):
        demo_avatar("https://example.invalid/avatar.png")
