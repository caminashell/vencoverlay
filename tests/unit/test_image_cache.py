import os
from pathlib import Path
from time import monotonic, sleep, time

from vencoverlay.image_cache import AvatarCache, WebcamFrames
from vencoverlay.protocol import FrameMessage


def wait_for_avatar(cache: AvatarCache[bytes]) -> None:
    deadline = monotonic() + 2
    while monotonic() < deadline:
        if cache.poll():
            return
        sleep(0.005)
    raise AssertionError("Avatar worker did not complete")


def test_avatar_cache(tmp_path: Path) -> None:
    calls: list[str] = []

    def fetch(url: str) -> bytes:
        calls.append(url)
        return b"image"

    cache = AvatarCache(tmp_path, bytes, fetch=fetch)
    try:
        assert cache.get("https://example.com/a") is None
        assert cache.get("https://example.com/a") is None
        wait_for_avatar(cache)
        assert cache.get("https://example.com/a") == b"image"
        assert len(calls) == 1
    finally:
        cache.close()
    cache = AvatarCache(tmp_path, bytes, fetch=fetch)
    try:
        assert cache.get("https://example.com/a") is None
        wait_for_avatar(cache)
        assert cache.get("https://example.com/a") == b"image"
        assert len(calls) == 1
    finally:
        cache.close()


def test_avatar_failure(tmp_path: Path) -> None:
    def fetch(url: str) -> bytes:
        raise ValueError(url)

    cache = AvatarCache(tmp_path, bytes, fetch=fetch)
    try:
        assert cache.get("https://example.com/a") is None
        wait_for_avatar(cache)
        assert cache.get("https://example.com/a") is None
        assert not cache.poll()
    finally:
        cache.close()


def test_webcam_lifecycle() -> None:
    now = 0.0
    frames = WebcamFrames(lambda message: message.jpeg, clock=lambda: now, capacity=1)
    assert not frames.apply(FrameMessage("a", 1, 1, b"a"), set())
    assert frames.apply(FrameMessage("a", 1, 1, b"a"), {"a"})
    assert frames.apply(FrameMessage("a", 1, 1, b"b"), {"a"})
    assert frames.get("a") == b"b"
    now = 4.0
    assert frames.get("a") is None
    assert frames.prune({"a"})
    assert frames.apply(FrameMessage("a", 1, 1, b"a"), {"a"})
    assert frames.prune(set())
    assert frames.apply(FrameMessage("a", 1, 1, b"a"), {"a"})
    frames.clear()
    assert frames.get("a") is None


def test_disk_eviction(tmp_path: Path) -> None:
    old = tmp_path / ("a" * 64 + ".image")
    expired = tmp_path / ("b" * 64 + ".image")
    unrelated = tmp_path / "notes.txt"
    link = tmp_path / ("c" * 64 + ".image")
    for path in (old, expired, unrelated):
        path.write_bytes(b"1234")
    link.symlink_to(unrelated)
    os.utime(old, (time() - 10, time() - 10))
    os.utime(expired, (0, 0))
    cache = AvatarCache(tmp_path, bytes, fetch=lambda _: b"5678", disk_capacity=4)
    try:
        cache.get("https://example.com/new")
        wait_for_avatar(cache)
        assert not old.exists()
        assert not expired.exists()
        assert unrelated.read_bytes() == b"1234"
        assert link.is_symlink()
        assert cache.get("https://example.com/new") == b"5678"
    finally:
        cache.close()
