from dataclasses import replace

import pytest

from vencoverlay.overlay_state import OverlayState
from vencoverlay.protocol import Channel, Participant, SpeakingMessage, StateMessage, ToastMessage


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def user(uid: str = "a") -> Participant:
    return Participant(uid, uid, None, False, False, False, False, False, False, False)


def join(state: OverlayState, *users: Participant) -> None:
    state.apply(StateMessage(Channel("1", "Voice", None, None), users, state.preferences))


def test_order_and_animation_preserved() -> None:
    clock = Clock()
    state = OverlayState(clock=clock)
    join(state, user(), user("b"))
    clock.now = 0.05
    assert state.tick()
    person = state.people["a"]
    assert person.alpha == pytest.approx(0.4)
    join(state, user("b"), replace(user(), name="Updated"))
    assert state.order == ("b", "a")
    assert state.people["a"] is person
    assert person.data.name == "Updated"
    join(state, user("b"))
    assert "a" not in state.people


def test_speaking_hold_and_video() -> None:
    clock = Clock()
    state = OverlayState(clock=clock)
    state.preferences = replace(state.preferences, only_speaking=True)
    join(state, user())
    person = state.people["a"]
    assert not state.wants_visible(person, clock.now)
    state.apply(SpeakingMessage("missing", True))
    state.apply(SpeakingMessage("a", True))
    clock.now = 0.05
    assert state.tick()
    assert person.glow == pytest.approx(0.7)
    state.apply(SpeakingMessage("a", False))
    assert state.wants_visible(person, 1.49)
    assert not state.wants_visible(person, 1.5)
    clock.now = 2.0
    assert state.tick()
    assert person.glow == pytest.approx(0.35)
    join(state, replace(user(), video=True))
    assert state.wants_visible(person, clock.now)


def test_toast_limit_and_expiry() -> None:
    clock = Clock()
    state = OverlayState(clock=clock)
    state.apply(ToastMessage("join", "ignored", None))
    assert not state.toasts
    join(state)
    for i in range(4):
        state.apply(ToastMessage("join", str(i), None))
    assert [toast.message.text for toast in state.toasts] == ["1", "2", "3"]
    clock.now = 4.49
    assert state.tick()
    clock.now = 4.5
    assert state.tick()
    assert not state.toasts
    assert not state.tick()


def test_visibility_and_disconnect() -> None:
    state = OverlayState()
    assert not state.visible
    join(state, user())
    assert state.visible
    state.toggle()
    assert not state.visible
    state.apply(ToastMessage("join", "a", None))
    state.apply(StateMessage(None, (), state.preferences))
    assert not state.people
    assert not state.order
    assert not state.toasts
    join(state, user())
    assert not state.visible
    state.toggle()
    assert state.visible
    state.disconnect()
    assert not state.visible


def test_channel_change_clears_old_state() -> None:
    state = OverlayState()
    join(state, user())
    old = state.people["a"]
    state.apply(ToastMessage("join", "a", None))
    state.apply(StateMessage(Channel("2", "Other", None, None), (user(),), state.preferences))
    assert state.people["a"] is not old
    assert not state.toasts


def test_animation_settles() -> None:
    clock = Clock()
    state = OverlayState(clock=clock)
    join(state, replace(user(), speaking=True))
    for i in range(1, 20):
        clock.now = i * 0.1
        state.tick()
    assert state.people["a"].alpha == 1.0
    assert state.people["a"].glow == 1.0
    assert not state.tick()
