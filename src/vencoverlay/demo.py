"""Offline, deterministic demonstration of the real overlay message flow."""

from base64 import b64decode
from collections.abc import Callable
from dataclasses import replace
from time import monotonic

from vencoverlay.overlay_state import OverlayState
from vencoverlay.protocol import (
    Channel,
    FrameMessage,
    Message,
    Participant,
    StateMessage,
    ToastMessage,
)

CYCLE_SECONDS = 36
FRAME_WIDTH = 160
FRAME_HEIGHT = 90
FRAME_RATE = 5
CHANNEL = Channel("demo", "Lounge", "VencOverlay Demo", None)
AVATAR_URL = "demo:avatar"

# Original synthetic images, embedded so demo mode needs no network or assets.
AVATAR = b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAEAAAABACAIAAAAlC+aJAAABPElEQVR4nO2ZMQ7CMAxFW8TZ2Jk4BQsHYeEScEsG"
    "JIRakjr+P7WT+I1IOP/VTkvKfD49p5Y5WAdACQFrQsCaY6W698dl/eHt+qIvNHNvo39zryGa0ASE0X+haHD2gCK9"
    "+lsLCAJIDtwBFcATgBUgAcoMgHX0Aqz0YLXmH2RKAe7lR2qO2gE/aARqzI+68pAdcEUIWBMC1mgEapwM1ZWH7IAr"
    "lAI1pkhXc9QOTOwmqKtBHWA5IHXQEcIdwAqEPYAkwP05m1iXgzKBA78bpZ/LdFbFAvXOk1+KTMr2wA7pS1eRdmCf"
    "6AskrRB1wCS9cN1tAav0wtV7/zFne/klGXICHtJ/yCTpfYT8kxTwMz8fUnn67UArhIA1IWBNCFgTAtYkBer9i6Ej"
    "laffDrRCTsDPFGWSbHTAg0M+Q/Mj9AYw0GMFluCyyQAAAABJRU5ErkJggg=="
)

FRAMES = (
    b64decode(
        "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAsICAoIBwsKCQoNDAsNERwSEQ8PESIZGhQcKSQrKigkJyctMkA3"
        "LTA9MCcnOEw5PUNFSElIKzZPVU5GVEBHSEX/2wBDAQwNDREPESESEiFFLicuRUVFRUVFRUVFRUVFRUVFRUVF"
        "RUVFRUVFRUVFRUVFRUVFRUVFRUVFRUVFRUVFRUVFRUX/wAARCABaAKADASIAAhEBAxEB/8QAHwAAAQUBAQEB"
        "AQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKB"
        "kaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1"
        "dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl"
        "5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcF"
        "BAQAAQJ3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5"
        "OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0"
        "tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwDj9P0+XUp2"
        "ihZFZV3ZckDGQOw96NQ0+XTZ1imZGZl3ZQkjGSO49qNP1CXTZ2lhVGZl24cEjGQex9qNQ1CXUp1lmVFZV24Q"
        "EDGSe59639/n8g/c+x/v/hYq0UUVoc4UVu6X4S1DUoknOy3gfBDSHllPcAf1xnIrWf4fMEYx6iGfB2hocAn3"
        "O44qeZIlziupxlFaWq6DfaMQbqMGNjhZUOVJxnHqPxx0NZtPcad9gooopjCiiigAooooAKKKKACiiigAoooo"
        "AKKKKACuv8FaGJn/ALTuUBRCVhVlPLDHz+nHQdec9MVyFesaDCkGg2KxrtUwq5Gc8sMn9SaibsjOo7I0aKKK"
        "xOYa6LIjJIoZGBDKwyCPQ15f4j0Y6PqTIo/0eXLxEA4Az93J7j/A969SrlPH0KNpdtMV/eJNsU56AqSf/QR+"
        "VXB2ZpTdnY4GiiitjpCiiigAooooAKKKKACiiigAooooAKKKKACvS/CGoC+0OKNn3TW37pgcA4/h4HbGBn2N"
        "eaVf0fVZtGv1uYVDcbXQ/wAS9xnt0HNTJXRE48yPWqKyNL8S6fqcSYmSGdsAwyNg7jxgZ+9+Ht0rVd1jRnkY"
        "KiglmY4AHqaxtY5mmtx1cP481APLb2Eb58v97KBgjJ4X3Bxnj3Fams+MbSxUx2JS7nPGVb5E44Oe/bge/Irz"
        "2aZ7ieSaVt0kjF2OMZJOTVwj1ZrTg73YyiiitTcKKKKACiiigAooooAKKKKACiiigAoq9/Zv/TX/AMd/+vR/"
        "Zv8A01/8d/8Ar0AWdH0pbpTPcAmLOFXkbvfPpXRJGkSBI1VFHQKMAVFZxiKzhRQMBB0GM8danpnn1JuTCiii"
        "mQFQ3FrDdJtnjVwOmeo+h7VNRQCdtjkNSsDYXGzJaNhlGI/T61TrqdatxcWqcgMr8NjJ6HisT+zf+mv/AI7/"
        "APXqWd9OTlG7KNFXv7N/6a/+O/8A16P7N/6a/wDjv/16DQo0Ve/s3/pr/wCO/wD16P7N/wCmv/jv/wBegCjR"
        "V7+zf+mv/jv/ANej+zf+mv8A47/9egCjRV7+zf8Apr/47/8AXo/s3/pr/wCO/wD16AKNFXv7N/6a/wDjv/16"
        "P7N/6a/+O/8A16AL1FFFIDbg/wCPeL/dH8qkqOD/AI94v90fyqSrPNluwooooEFFFFAFPUv+Pdf97+hrMrT1"
        "L/j3X/e/oazKlnbQ+AKKKKRsFFFFABRRRQAUUUUAFFFFABRRRQBpWFwGQRMfmXpnuKu1gVsWbFrVCxJJzyfr"
        "VI460FF3XUnooopmAUUVQ1JiBGASAc5Hr0pDjHmdiK+uBK4RDlF/U1UooqT0IxUVZBRRRQUFFFFABRRRQAUU"
        "UUAFFFFAH//Z"
    ),
    b64decode(
        "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAsICAoIBwsKCQoNDAsNERwSEQ8PESIZGhQcKSQrKigkJyctMkA3"
        "LTA9MCcnOEw5PUNFSElIKzZPVU5GVEBHSEX/2wBDAQwNDREPESESEiFFLicuRUVFRUVFRUVFRUVFRUVFRUVF"
        "RUVFRUVFRUVFRUVFRUVFRUVFRUVFRUVFRUVFRUVFRUX/wAARCABaAKADASIAAhEBAxEB/8QAHwAAAQUBAQEB"
        "AQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKB"
        "kaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1"
        "dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl"
        "5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcF"
        "BAQAAQJ3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5"
        "OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0"
        "tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwDj9P0+XUp2"
        "ihZFZV3ZckDGQOw96NQ0+XTZ1imZGZl3ZQkjGSO49qNP1CXTZ2lhVGZl24cEjGQex9qNQ1CXUp1lmVFZV24Q"
        "EDGSe59639/n8g/c+x/v/hYq0UUVoc4UUUUAFFFFABRRRQAUUUUAFFKiNI6pGpZ2ICqoySfQV1Fl4EvpwGvJ"
        "o7YEH5R87A5744/Wk2luJyS3OWorr7jwBOqA219HI+eRIhQY+oJrmL2wudOuDBeRGKQAHBwcg9wRwaE09hKS"
        "exXoooplBRRRQAUUUUAFFFFABRRRQAUUUUAFFFFABRRVrTIUuNUs4ZV3RyTIjDOMgsAaQHf+FNDGl2AmmQfa"
        "5wGYlSCinGE5/X3+groKKKwbucbd3cKp6pp0Oq2EtrMB8w+ViM7G7MPp/wDWq5RSDY8cvLSWxu5bacYkiYqe"
        "uD7jPY9RUNdJ45hSLXlZFw0sKu5z1OSP5AVzddCd0dcXdXCiiimMKKKKACiiigAooooAKKKKACiiigAp8Mz2"
        "88c0TbZI2DqcZwQcimUUAexWd3FfWkVzAcxyqGHTI9jjuOhqevM/DviV9E3wyRebayNuIHDKeASPXgdPYciv"
        "QLLVbHUQDZ3UcpIJ2g4YAHGSp5FYSi0csoOJcoqK4uYLVA9zNHChOA0jBQT6c1xniHxilxBLZaaN0ci7XnYY"
        "yCOQFI/DJ9+OhpJNijFy2MXxPqA1LXJ5I33wx4ijPGMDrgjqCckH3rIoordaHUlZWCiiimMKKvf2b/01/wDH"
        "f/r0f2b/ANNf/Hf/AK9AFGir39m/9Nf/AB3/AOvR/Zv/AE1/8d/+vQBRoq9/Zv8A01/8d/8Ar0f2b/01/wDH"
        "f/r0AUaKvf2b/wBNf/Hf/r0f2b/01/8AHf8A69AFGir39m/9Nf8Ax3/69H9m/wDTX/x3/wCvQBRrpNM0aKOF"
        "ZblA8rDO1hwvtj1+tZ1tpwF1DucMu8ZUrweeldRQjnrza0QUUUVRyBRRRQBTvdMt71WLKFlI4kUc/j61yc8L"
        "28zxSDDIcGu4rC1myE14HDBCUGcL15PNJnRQm78pg0Ve/s3/AKa/+O//AF6P7N/6a/8Ajv8A9ekdZeooopAF"
        "FFFABRRRQAUUUUAFFFFAEkH/AB8Rf7w/nW3WJB/x8Rf7w/nW3VI5MRugooopnOFFFFABWZqX/Hwv+7/U1p1m"
        "al/x8L/u/wBTSZtQ+Mp0UUVJ2hRRRQAUUUUAFFFFABRRRQAUUUUAKCQQQcEd62LedZ4wwPzD7w9DWNSqxU5U"
        "kEdxTRlUgpI3qKKKo4QooooAa7qilmOAOprGnlM0zP27D0FS3zE3TAkkDGB6cCq1SzsowSXMFFFFI3P/2Q=="
    ),
)


def demo_avatar(url: str) -> bytes:
    """Return local synthetic data only, never perform a network request."""
    if url != AVATAR_URL:
        raise ValueError("Unknown demo avatar")
    return AVATAR


class DemoFeed:
    """Produce snapshots, join/leave events and synthetic video on the owner thread."""

    def __init__(self, *, clock: Callable[[], float] = monotonic) -> None:
        self._clock = clock
        self._started = clock()
        self._previous: StateMessage | None = None
        self._last_frame = -1
        self._preferences = OverlayState().preferences

    def messages(self) -> tuple[Message, ...]:
        elapsed = max(0.0, self._clock() - self._started)
        second = int(elapsed) % CYCLE_SECONDS
        active = 4 <= second < 32
        phase = (second - 4) % 14
        style = "discord" if second < 18 else "mac"
        preferences = replace(self._preferences, style=style)
        users: tuple[Participant, ...] = ()
        if active:
            speaker = (phase // 2) % 3
            users = (
                Participant(
                    "a",
                    "Alex",
                    AVATAR_URL,
                    True,
                    speaker == 0,
                    False,
                    False,
                    False,
                    True,
                    False,
                ),
                Participant(
                    "b",
                    "Blake",
                    None,
                    False,
                    speaker == 1,
                    False,
                    False,
                    False,
                    True,
                    True,
                ),
                Participant(
                    "c",
                    "Casey",
                    None,
                    False,
                    False,
                    True,
                    False,
                    False,
                    False,
                    False,
                ),
                Participant(
                    "d",
                    "Drew",
                    None,
                    False,
                    False,
                    True,
                    True,
                    False,
                    False,
                    False,
                ),
            )
            if 4 <= phase < 10:
                users += (
                    Participant(
                        "e",
                        "Ellis",
                        None,
                        False,
                        speaker == 2,
                        False,
                        False,
                        False,
                        False,
                        False,
                    ),
                )
        snapshot = StateMessage(CHANNEL if active else None, users, preferences)
        messages: list[Message] = []
        previous = self._previous
        if previous != snapshot:
            messages.append(snapshot)
            if active and previous is not None and previous.channel == CHANNEL:
                old_ids = {user.id for user in previous.users}
                new_ids = {user.id for user in users}
                for user in users:
                    if user.id not in old_ids:
                        messages.append(ToastMessage("join", f"{user.name} joined", user.avatar))
                for user in previous.users:
                    if user.id not in new_ids:
                        messages.append(ToastMessage("leave", f"{user.name} left", user.avatar))
            self._previous = snapshot
        frame_index = int(elapsed * FRAME_RATE)
        if active and frame_index != self._last_frame:
            messages.append(
                FrameMessage("a", FRAME_WIDTH, FRAME_HEIGHT, FRAMES[frame_index % len(FRAMES)])
            )
            self._last_frame = frame_index
        return tuple(messages)
