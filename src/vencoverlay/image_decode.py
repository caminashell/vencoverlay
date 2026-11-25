"""Bounded native PNG and JPEG decoding."""

import importlib
import struct
from collections.abc import Callable
from typing import Protocol, cast

from vencoverlay.protocol import MAX_FRAME_BYTES, MAX_FRAME_HEIGHT, MAX_FRAME_WIDTH, FrameMessage

MAX_AVATAR_BYTES = 512 * 1024


class ImageLoader[T](Protocol):
    def decode(self, data: bytes, *, jpeg: bool, max_dimension: int) -> T: ...


def image_dimensions(data: bytes, *, jpeg: bool) -> tuple[int, int]:
    """Read dimensions before handing compressed data to a native decoder."""
    if not jpeg:
        if len(data) >= 33 and data[:16] == b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR":
            return struct.unpack(">II", data[16:24])
        raise ValueError("Invalid PNG header")
    if data[:2] != b"\xff\xd8":
        raise ValueError("Invalid JPEG header")
    pos = 2
    while pos < len(data):
        if data[pos] != 255:
            break
        while pos < len(data) and data[pos] == 255:
            pos += 1
        if pos + 3 > len(data):
            break
        marker = data[pos]
        pos += 1
        if marker in (0, 1, 216, 217, 218) or 208 <= marker <= 215:
            break
        length = int.from_bytes(data[pos : pos + 2], "big")
        if length < 2 or pos + length > len(data):
            break
        if marker in (192, 193, 194):
            if length < 8:
                break
            return (
                int.from_bytes(data[pos + 5 : pos + 7], "big"),
                int.from_bytes(data[pos + 3 : pos + 5], "big"),
            )
        pos += length
    raise ValueError("Invalid or unsupported JPEG header")


def checked_dimensions(data: bytes, *, jpeg: bool) -> tuple[int, int]:
    limit = MAX_FRAME_BYTES if jpeg else MAX_AVATAR_BYTES
    if not data or len(data) > limit:
        raise ValueError("Invalid image size")
    width, height = image_dimensions(data, jpeg=jpeg)
    max_width, max_height = (MAX_FRAME_WIDTH, MAX_FRAME_HEIGHT) if jpeg else (256, 256)
    if not (1 <= width <= max_width and 1 <= height <= max_height):
        raise ValueError("Image dimensions exceed limits")
    return width, height


class Pixbuf(Protocol):
    def get_width(self) -> int: ...
    def get_height(self) -> int: ...


class _Loader(Protocol):
    def set_size(self, width: int, height: int) -> None: ...
    def write(self, data: bytes) -> bool: ...
    def close(self) -> bool: ...
    def get_pixbuf(self) -> Pixbuf | None: ...


class _LoaderFactory(Protocol):
    def new_with_type(self, image_type: str) -> _Loader: ...


class _PixbufModule(Protocol):
    PixbufLoader: _LoaderFactory


class _GI(Protocol):
    def require_version(self, namespace: str, version: str) -> None: ...


class _GLib(Protocol):
    Error: type[Exception]


class GdkPixbufDecoder:
    """Load GI lazily; native errors become recoverable ValueErrors."""

    def __init__(self) -> None:
        gi = cast(_GI, importlib.import_module("gi"))
        gi.require_version("GdkPixbuf", "2.0")
        self._native = cast(_PixbufModule, importlib.import_module("gi.repository.GdkPixbuf"))
        self._error = cast(_GLib, importlib.import_module("gi.repository.GLib")).Error

    def decode(self, data: bytes, *, jpeg: bool, max_dimension: int) -> Pixbuf:
        width, height = checked_dimensions(data, jpeg=jpeg)
        if max_dimension < 1:
            raise ValueError("Invalid output limit")
        ratio = min(1.0, max_dimension / max(width, height))
        target = (max(1, round(width * ratio)), max(1, round(height * ratio)))
        try:
            loader = self._native.PixbufLoader.new_with_type("jpeg" if jpeg else "png")
            loader.set_size(*target)
            try:
                if not loader.write(data):
                    raise ValueError("Image write failed")
            finally:
                closed = loader.close()
            if not closed:
                raise ValueError("Image close failed")
            result = loader.get_pixbuf()
        except self._error as exc:
            raise ValueError("Native image decoding failed") from exc
        if result is None or (result.get_width(), result.get_height()) != target:
            raise ValueError("Unexpected decoded image dimensions")
        return result


def avatar_decoder[T](loader: ImageLoader[T]) -> Callable[[bytes], T]:
    def decode(data: bytes) -> T:
        checked_dimensions(data, jpeg=False)
        return loader.decode(data, jpeg=False, max_dimension=128)

    return decode


def webcam_decoder[T](loader: ImageLoader[T]) -> Callable[[FrameMessage], T]:
    def decode(message: FrameMessage) -> T:
        if checked_dimensions(message.jpeg, jpeg=True) != (message.width, message.height):
            raise ValueError("Frame dimensions do not match declaration")
        return loader.decode(message.jpeg, jpeg=True, max_dimension=1920)

    return decode
