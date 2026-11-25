import base64
import struct

import pytest

from vencoverlay.image_decode import GdkPixbufDecoder, checked_dimensions, webcam_decoder
from vencoverlay.protocol import FrameMessage

# Valid 1x1 PNG fixture.
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGNgYGAAAAAEAAH2FzhVAAAAAElFTkSuQmCC"
)
WIDE_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAQAAAACACAIAAABr1yBdAAAAdklEQVR4nO3BMQEAAADCoPVPbQ0P"
    "oAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAeDCAjwABfclWBwAAAABJRU5E"
    "rkJggg=="
)
JPEG = base64.b64decode(
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0a"
    "HBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/2wBDAQkJCQwLDBgNDRgyIRwhMjIyMjIy"
    "MjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjL/wAARCAAQACADASIA"
    "AhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQA"
    "AAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3"
    "ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWm"
    "p6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEA"
    "AwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSEx"
    "BhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElK"
    "U1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3"
    "uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwD5/ooo"
    "oAKKKKAP/9k="
)


def test_png_dimensions() -> None:
    assert checked_dimensions(PNG, jpeg=False) == (1, 1)
    with pytest.raises(ValueError):
        checked_dimensions(PNG[:16] + struct.pack(">II", 257, 1) + PNG[24:], jpeg=False)


def test_invalid_header() -> None:
    with pytest.raises(ValueError):
        checked_dimensions(b"bad", jpeg=True)


def test_native_png() -> None:
    pytest.importorskip("gi")
    decoder = GdkPixbufDecoder()
    image = decoder.decode(PNG, jpeg=False, max_dimension=128)
    assert (image.get_width(), image.get_height()) == (1, 1)
    with pytest.raises(ValueError):
        decoder.decode(PNG[:33], jpeg=False, max_dimension=128)


def test_frame_mismatch() -> None:
    class Loader:
        def decode(self, data: bytes, *, jpeg: bool, max_dimension: int) -> bytes:
            return data

    jpeg = bytes.fromhex("ffd8ffc0000b080001000101011100ffd9")
    decode = webcam_decoder(Loader())
    assert decode(FrameMessage("a", 1, 1, jpeg)) == jpeg
    with pytest.raises(ValueError):
        decode(FrameMessage("a", 2, 1, jpeg))


def test_native_scaling_and_jpeg() -> None:
    pytest.importorskip("gi")
    decoder = GdkPixbufDecoder()
    image = decoder.decode(WIDE_PNG, jpeg=False, max_dimension=128)
    assert (image.get_width(), image.get_height()) == (128, 64)
    image = webcam_decoder(decoder)(FrameMessage("a", 32, 16, JPEG))
    assert (image.get_width(), image.get_height()) == (32, 16)
