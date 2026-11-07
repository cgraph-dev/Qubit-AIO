"""Pictures for Jev: read a PNG or JPEG's header, check it against TypeSafe's limits, and
build the ``assets`` entry a request carries.

Images in System One are "Dreamer-ready" (docs.typesafe.ai/images, read 2026-10-15): every
request that holds one needs the ``TypeSafe-Danger-Zone-Images: true`` header, and the format
and limits may change between releases. The limits below are that page's.

The API rejects an image outside them rather than resizing it, and one bad image fails the
whole request. Standard library only, so nothing here can resize, rotate or re-encode a
picture: an image that would be refused is refused here, with a reason, before any request
is made, and the caller takes its fail-open path.
"""
from __future__ import annotations

import base64
import struct
import uuid
from pathlib import Path
from typing import Any, Dict, Tuple, Union

DANGER_ZONE_HEADER = "TypeSafe-Danger-Zone-Images"
MAX_FILE_BYTES = 16 * 1024 * 1024
MIN_SIDE, MAX_SIDE = 256, 1568
MIN_PIXELS, MAX_PIXELS = 78_400, 2_458_624
MAX_REQUEST_BYTES = 32 * 1024 * 1024
MAX_REFERENCE_BYTES = 1024
FORMATS = ("png", "jpg", "jpeg")

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
# JPEG start-of-frame markers. 0xC0/0xC1 are baseline/extended sequential; the API asks for
# baseline, so a progressive (0xC2) or any other coding process is refused.
_SOF_SEQUENTIAL = {0xC0, 0xC1}
_SOF_OTHER = {0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}


class ImageError(ValueError):
    """An image TypeSafe would refuse. ``code`` is short and safe to log."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def inspect(data: bytes) -> Tuple[str, int, int]:
    """``(format, width, height)`` read from the header, or ``ImageError``."""
    if data.startswith(_PNG_SIGNATURE):
        return ("png",) + _png_size(data)
    if data.startswith(b"\xff\xd8"):
        return ("jpeg",) + _jpeg_size(data)
    raise ImageError("unsupported_format")


def _png_size(data: bytes) -> Tuple[int, int]:
    if len(data) < 33 or data[12:16] != b"IHDR":
        raise ImageError("corrupt_png")
    width, height = struct.unpack(">II", data[16:24])
    # An animated PNG announces itself with an acTL chunk before the first IDAT.
    offset = 8
    while offset + 8 <= len(data):
        length, kind = struct.unpack(">I4s", data[offset:offset + 8])
        if kind == b"acTL":
            raise ImageError("animated_png")
        if kind in (b"IDAT", b"IEND"):
            break
        offset += 12 + length
    return width, height


def _jpeg_size(data: bytes) -> Tuple[int, int]:
    offset = 2
    while offset + 4 <= len(data):
        if data[offset] != 0xFF:
            raise ImageError("corrupt_jpeg")
        marker = data[offset + 1]
        if marker == 0xFF:  # fill byte
            offset += 1
            continue
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:  # no length
            offset += 2
            continue
        (length,) = struct.unpack(">H", data[offset + 2:offset + 4])
        if marker == 0xE1 and _exif_rotated(data[offset + 4:offset + 2 + length]):
            raise ImageError("exif_rotated")
        if marker in _SOF_OTHER:
            raise ImageError("progressive_jpeg" if marker == 0xC2 else "unsupported_jpeg")
        if marker in _SOF_SEQUENTIAL:
            if offset + 9 > len(data):
                break
            height, width = struct.unpack(">HH", data[offset + 5:offset + 9])
            return width, height
        if marker == 0xDA:  # scan data before any frame header
            break
        offset += 2 + length
    raise ImageError("corrupt_jpeg")


def _exif_rotated(segment: bytes) -> bool:
    """True when an APP1 Exif segment says the pixels are stored rotated or mirrored.

    The API strips metadata and does not apply EXIF orientation, so a phone photo stored on
    its side reaches the model on its side. Without an image library it cannot be turned
    upright here, so it is refused. Exif this cannot read is treated as upright: the API drops
    it either way, and the pixels are then what is sent.
    """
    if not segment.startswith(b"Exif\x00\x00"):
        return False
    tiff = segment[6:]
    try:
        order = {b"II": "<", b"MM": ">"}[tiff[:2]]
        (ifd,) = struct.unpack(order + "I", tiff[4:8])
        (count,) = struct.unpack(order + "H", tiff[ifd:ifd + 2])
        for index in range(count):
            entry = tiff[ifd + 2 + 12 * index:ifd + 14 + 12 * index]
            tag, kind = struct.unpack(order + "HH", entry[:4])
            if tag == 0x0112 and kind == 3:
                (value,) = struct.unpack(order + "H", entry[8:10])
                return value not in (0, 1)
    except (KeyError, struct.error):
        return False
    return False


def check(data: bytes) -> str:
    """The format (``png`` or ``jpeg``) of an image TypeSafe will accept, or ``ImageError``."""
    if len(data) > MAX_FILE_BYTES:
        raise ImageError("too_large")
    kind, width, height = inspect(data)
    if not (MIN_SIDE <= width <= MAX_SIDE and MIN_SIDE <= height <= MAX_SIDE):
        raise ImageError("side_out_of_range")
    if not MIN_PIXELS <= width * height <= MAX_PIXELS:
        raise ImageError("pixels_out_of_range")
    return kind


def reference() -> str:
    """A fresh reference. References match as plain text, so a UUID never hits a real word."""
    return f"img-{uuid.uuid4()}"


def asset(source: Union[bytes, str, Path]) -> Tuple[str, Dict[str, Any]]:
    """``(reference, asset)`` for one image, checked. Put the reference where the picture goes."""
    data = source if isinstance(source, bytes) else Path(source).expanduser().read_bytes()
    kind = check(data)
    return reference(), {"type": "image", "format": kind,
                         "data_base64": base64.b64encode(data).decode("ascii")}
