"""Application-thread-owned voice state and animation, independent of rendering."""

from collections.abc import Callable
from dataclasses import dataclass, replace
from time import monotonic

from vencoverlay.protocol import (
    Channel,
    Participant,
    Preferences,
    SpeakingMessage,
    StateMessage,
    ToastMessage,
)

ANIMATION_INTERVAL_MS = 16
SPEAKING_HOLD_SECONDS = 1.5
TOAST_LIFETIME_SECONDS = 4.5
MAX_TOASTS = 3


@dataclass(slots=True)
class AnimatedParticipant:
    data: Participant
    glow: float = 0.0
    alpha: float = 0.0
    last_spoke: float | None = None


@dataclass(frozen=True, slots=True)
class ActiveToast:
    message: ToastMessage
    born: float


def approach(current: float, target: float, dt: float, speed: float) -> float:
    """Apply the original easing curve."""
    return current + (target - current) * min(1.0, dt * speed)


class OverlayState:
    """Maintain voice state; callers must dispatch updates on the owning thread."""

    def __init__(self, *, clock: Callable[[], float] = monotonic) -> None:
        self._clock = clock
        self._last_tick = clock()
        self.channel: Channel | None = None
        self.preferences = Preferences(
            "top-left", 1.0, 0.85, "#30d158", "discord", "always", "large", False, True, 280
        )
        self.people: dict[str, AnimatedParticipant] = {}
        self.order: tuple[str, ...] = ()
        self.toasts: list[ActiveToast] = []
        self.visible_toggle = True

    @property
    def visible(self) -> bool:
        return self.visible_toggle and self.channel is not None

    def toggle(self) -> None:
        self.visible_toggle = not self.visible_toggle

    def disconnect(self) -> None:
        self.channel = None
        self.people.clear()
        self.order = ()
        self.toasts.clear()

    def apply(self, message: StateMessage | SpeakingMessage | ToastMessage) -> None:
        now = self._clock()
        if isinstance(message, StateMessage):
            self.preferences = message.preferences
            if message.channel is None:
                self.disconnect()
                return
            if self.channel is None or self.channel.id != message.channel.id:
                self.disconnect()
            self.channel = message.channel
            people: dict[str, AnimatedParticipant] = {}
            for user in message.users:
                person = self.people.get(user.id)
                if person is None:
                    person = AnimatedParticipant(user)
                person.data = user
                if user.speaking:
                    person.last_spoke = now
                people[user.id] = person
            self.people = people
            self.order = tuple(user.id for user in message.users)
        elif isinstance(message, SpeakingMessage):
            person = self.people.get(message.id)
            if person is not None:
                person.data = replace(person.data, speaking=message.on)
                if message.on:
                    person.last_spoke = now
        elif self.channel is not None:
            self.toasts.append(ActiveToast(message, now))
            self.toasts = self.toasts[-MAX_TOASTS:]

    def wants_visible(self, person: AnimatedParticipant, now: float) -> bool:
        return (
            not self.preferences.only_speaking
            or person.data.speaking
            or person.data.video
            or (person.last_spoke is not None and now - person.last_spoke < SPEAKING_HOLD_SECONDS)
        )

    def tick(self) -> bool:
        """Advance animation and return whether a redraw is needed."""
        now = self._clock()
        dt = max(0.0, min(0.1, now - self._last_tick))
        self._last_tick = now
        previous_count = len(self.toasts)
        self.toasts = [toast for toast in self.toasts if now - toast.born < TOAST_LIFETIME_SECONDS]
        changed = bool(self.toasts) or len(self.toasts) != previous_count
        for person in self.people.values():
            glow = 1.0 if person.data.speaking else 0.0
            alpha = 1.0 if self.wants_visible(person, now) else 0.0
            old = (person.glow, person.alpha)
            person.glow = approach(person.glow, glow, dt, 14 if glow else 5)
            person.alpha = approach(person.alpha, alpha, dt, 8)
            if abs(person.glow - glow) <= 0.01:
                person.glow = glow
            if abs(person.alpha - alpha) <= 0.01:
                person.alpha = alpha
            changed = changed or old != (person.glow, person.alpha)
        return changed
