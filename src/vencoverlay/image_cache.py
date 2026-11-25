"""Bounded asynchronous avatar retrieval and latest-frame storage."""

import hashlib
import logging
import os
import re
import stat
import tempfile
from collections import OrderedDict
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from threading import Lock
from time import monotonic, time
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from vencoverlay.image_decode import MAX_AVATAR_BYTES
from vencoverlay.protocol import FrameMessage

logger = logging.getLogger(__name__)
MAX_DISK_BYTES = 64 * 1024 * 1024
AVATAR_TTL = 86400
FRAME_TTL = 4.0


def download_avatar(url: str) -> bytes:
    """Retrieve a bounded HTTP(S) response."""
    if urlsplit(url).scheme not in {"http", "https"}:
        raise ValueError("Unsupported avatar URL scheme")
    request = Request(url, headers={"User-Agent": "VencOverlay"})
    with urlopen(request, timeout=10) as response:
        data: bytes = response.read(MAX_AVATAR_BYTES + 1)
    if len(data) > MAX_AVATAR_BYTES:
        raise ValueError("Avatar exceeds size limit")
    return data


class AvatarCache[T]:
    """Owner-thread cache; workers fetch and decode, poll publishes results."""

    def __init__(
        self,
        directory: Path,
        decode: Callable[[bytes], T],
        *,
        fetch: Callable[[str], bytes] = download_avatar,
        capacity: int = 128,
        max_pending: int = 16,
        disk_capacity: int = MAX_DISK_BYTES,
    ) -> None:
        if capacity < 1 or max_pending < 1 or disk_capacity < 1:
            raise ValueError("Cache limits must be positive")
        self._directory = directory
        self._disk_capacity = disk_capacity
        self._disk_lock = Lock()
        self._decode = decode
        self._fetch = fetch
        self._capacity = capacity
        self._max_pending = max_pending
        self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="vencoverlay-avatar")
        self._pending: dict[str, Future[T]] = {}
        self._images: OrderedDict[str, tuple[T | None, float]] = OrderedDict()
        self._closed = False

    def get(self, url: str | None) -> T | None:
        if not url or self._closed:
            return None
        cached = self._images.get(url)
        if cached is not None:
            image, expiry = cached
            if monotonic() < expiry:
                self._images.move_to_end(url)
                return image
            del self._images[url]
        if url not in self._pending and len(self._pending) < self._max_pending:
            self._pending[url] = self._pool.submit(self._load, url)
        return None

    def _load(self, url: str) -> T:
        path = self._directory / (hashlib.sha256(url.encode()).hexdigest() + ".image")
        with self._disk_lock:
            try:
                self._prune_disk()
                if path.is_symlink():
                    raise ValueError("Cache entry is a symbolic link")
                info = path.lstat()
                if stat.S_ISREG(info.st_mode) and time() - info.st_mtime < AVATAR_TTL:
                    with path.open("rb") as stream:
                        data = stream.read(MAX_AVATAR_BYTES + 1)
                    if len(data) <= MAX_AVATAR_BYTES:
                        return self._decode(data)
            except (OSError, ValueError):
                pass
        data = self._fetch(url)
        if len(data) > MAX_AVATAR_BYTES:
            raise ValueError("Avatar exceeds size limit")
        image = self._decode(data)
        with self._disk_lock:
            try:
                self._directory.mkdir(parents=True, exist_ok=True)
                if path.is_symlink():
                    return image
                with tempfile.NamedTemporaryFile(dir=self._directory, delete=False) as stream:
                    temporary = Path(stream.name)
                    try:
                        stream.write(data)
                        stream.close()
                        os.replace(temporary, path)
                    finally:
                        temporary.unlink(missing_ok=True)
                self._prune_disk()
            except OSError:
                logger.debug("Unable to persist avatar cache entry")
        return image

    def _prune_disk(self) -> None:
        """Called under the disk lock; only manage our regular cache files."""
        if not self._directory.exists():
            return
        entries: list[tuple[float, Path, int]] = []
        now = time()
        for path in self._directory.iterdir():
            if not re.fullmatch(r"[0-9a-f]{64}\.image", path.name):
                continue
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode):
                continue
            if now - info.st_mtime >= AVATAR_TTL or info.st_size > MAX_AVATAR_BYTES:
                path.unlink()
            else:
                entries.append((info.st_mtime, path, info.st_size))
        total = sum(size for _, _, size in entries)
        for _, path, size in sorted(entries):
            if total <= self._disk_capacity:
                break
            path.unlink()
            total -= size

    def poll(self) -> bool:
        """Publish completed requests on the owner thread; report redraw need."""
        changed = False
        for url, future in tuple(self._pending.items()):
            if not future.done():
                continue
            del self._pending[url]
            try:
                image = future.result()
            except Exception:  # noqa: BLE001 - isolate network and decoder failures
                logger.debug("Avatar retrieval failed")
                self._images[url] = (None, monotonic() + 30)
            else:
                self._images[url] = (image, monotonic() + AVATAR_TTL)
            while len(self._images) > self._capacity:
                self._images.popitem(last=False)
            changed = True
        return changed

    def close(self) -> None:
        self._closed = True
        self._pool.shutdown(wait=True, cancel_futures=True)
        self._pending.clear()
        self._images.clear()


class WebcamFrames[T]:
    """Owner-thread latest-frame cache with bounded capacity and expiry."""

    def __init__(
        self,
        decode: Callable[[FrameMessage], T],
        *,
        clock: Callable[[], float] = monotonic,
        capacity: int = 32,
    ) -> None:
        if capacity < 1:
            raise ValueError("Frame capacity must be positive")
        self._decode = decode
        self._clock = clock
        self._capacity = capacity
        self._frames: OrderedDict[str, tuple[T, float]] = OrderedDict()

    def apply(self, message: FrameMessage, participants: set[str]) -> bool:
        if message.id not in participants:
            return False
        try:
            image = self._decode(message)
        except ValueError:
            return False
        self._frames[message.id] = (image, self._clock())
        self._frames.move_to_end(message.id)
        while len(self._frames) > self._capacity:
            self._frames.popitem(last=False)
        return True

    def get(self, participant: str) -> T | None:
        frame = self._frames.get(participant)
        if frame is None or self._clock() - frame[1] >= FRAME_TTL:
            return None
        return frame[0]

    def prune(self, participants: set[str]) -> bool:
        now = self._clock()
        expired = [
            key
            for key, (_, born) in self._frames.items()
            if key not in participants or now - born >= FRAME_TTL
        ]
        for key in expired:
            del self._frames[key]
        return bool(expired)

    def clear(self) -> None:
        self._frames.clear()
