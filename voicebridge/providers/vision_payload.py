"""Vision payload translation — one image, two wire shapes.

The vision surface must be able to send the SAME image to a local Ollama model
and to an OpenAI-style ``/chat/completions`` endpoint. The two endpoints speak
different shapes for an attached image:

* Ollama ``/api/chat`` inlines a bare base64 string on the message:

  .. code-block:: json

     {"role": "user", "content": "<text>", "images": ["<b64>"]}

* OpenAI-style ``/chat/completions`` (OpenRouter, OpenCode Zen/Go, DeepSeek,
  Nous, custom) requires a content-block list:

  .. code-block:: json

     {"role": "user", "content": [
       {"type": "text", "text": "<text>"},
       {"type": "image_url",
        "image_url": {"url": "data:<mime>;base64,<b64>"}}]}

Before this module the image was ALWAYS sent as the Ollama ``images`` shape
(``voicebridge/vision.py``); no translation existed anywhere, so a remote vision
profile would have received a message with an unknown ``images`` key and no
usable image. This module is the single, dependency-free translation boundary.

It is deliberately pure: no I/O, no network, no config. Callers own the bytes
and the decision of which shape to build; this module only builds it.
"""
from __future__ import annotations

import base64
from typing import Any, Dict, List, Optional, Tuple

#: Recognised image MIME types (mirrors the vision validation whitelist).
ALLOWED_MIMES = ("image/png", "image/jpeg", "image/gif", "image/webp",
                 "image/bmp")

# MIME lookups are case-insensitive on the wire, so normalise to lower-case.
_MIME_ALIASES = {
    "image/jpg": "image/jpeg",       # tolerated: some encoders emit the 3-letter form
}

#: Default prompt when the caller supplies none (identical to the vision
#: surface's own default so the local and remote paths ask the same question).
DEFAULT_VISION_PROMPT = (
    "Describe this image factually: main subjects, text visible, setting.")


def normalize_mime(mime: str) -> str:
    """Return a canonical MIME type, raising ValueError for unsupported ones."""
    value = str(mime or "").strip().lower()
    value = _MIME_ALIASES.get(value, value)
    if value not in ALLOWED_MIMES:
        raise ValueError(f"unsupported image mime {mime!r}")
    return value


def sniff_mime(raw: bytes) -> Optional[str]:
    """Best-effort MIME from magic bytes (None when unrecognised).

    Mirrors ``voicebridge.vision.detect_format`` but returns a MIME string so
    the data URL is well-formed even when the caller did not supply one.
    """
    if not raw:
        return None
    if raw.startswith(bytes((0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A))):
        return "image/png"
    if raw.startswith(bytes((0xFF, 0xD8, 0xFF))):
        return "image/jpeg"
    if raw.startswith(b"GIF87a") or raw.startswith(b"GIF89a"):
        return "image/gif"
    if raw.startswith(b"BM"):
        return "image/bmp"
    if raw.startswith(b"RIFF") and len(raw) >= 12 and raw[8:12] == b"WEBP":
        return "image/webp"
    return None


def data_url(image_b64: str, mime: str = "image/png") -> str:
    """Build a ``data:<mime>;base64,<b64>`` URL from an already-b64 image.

    ``image_b64`` may already carry the ``data:`` prefix; it is returned
    unchanged in that case (idempotent, so a caller can pass either form).
    """
    b64 = str(image_b64 or "").strip()
    if b64.startswith("data:"):
        return b64
    return f"data:{normalize_mime(mime)};base64,{b64}"


def image_b64_from_raw(raw: bytes, mime: str = "") -> Tuple[str, str]:
    """Return ``(b64, mime)`` for raw bytes, sniffing the MIME when unset."""
    resolved = normalize_mime(mime) if mime else (
        sniff_mime(raw) or "image/png")
    return base64.b64encode(raw).decode("ascii"), resolved


# ---------------------------------------------------------------------------
# Message builders
# ---------------------------------------------------------------------------
def ollama_messages(text: str, image_b64: str,
                    *, role: str = "user") -> List[Dict[str, Any]]:
    """The local Ollama ``/api/chat`` shape: a bare ``images`` list."""
    return [{"role": role, "content": text, "images": [str(image_b64)]}]


def openai_messages(text: str, image_b64: str, mime: str = "image/png",
                    *, role: str = "user") -> List[Dict[str, Any]]:
    """The OpenAI-style ``/chat/completions`` shape: a content-block list."""
    return [{
        "role": role,
        "content": [
            {"type": "text", "text": text},
            {"type": "image_url",
             "image_url": {"url": data_url(image_b64, mime)}},
        ],
    }]


def build_vision_messages(shape: str, text: str, image_b64: str,
                          mime: str = "image/png") -> List[Dict[str, Any]]:
    """Dispatch to the right builder by wire ``shape``.

    ``shape`` is ``"ollama"`` (or ``"local"``) for the inlined-base64 message
    and ``"openai"`` (or ``"api"``/``"frontier"``) for the content-block list.
    An unknown shape raises ``ValueError`` — never a silent default, so a
    routing bug surfaces instead of sending the wrong body.
    """
    key = str(shape or "").strip().lower()
    if key in ("ollama", "local"):
        return ollama_messages(text, image_b64)
    if key in ("openai", "api", "frontier"):
        return openai_messages(text, image_b64, mime)
    raise ValueError(f"unknown vision payload shape {shape!r}")


def message_has_image(message: Dict[str, Any]) -> bool:
    """True when a single chat message carries an attached image.

    Recognises BOTH shapes so a caller can assert an image is actually present
    before egress (the pre-send guard the popup relies on).
    """
    if not isinstance(message, dict):
        return False
    if message.get("images"):
        return True
    content = message.get("content")
    if isinstance(content, list):
        return any(isinstance(block, dict)
                   and block.get("type") == "image_url" for block in content)
    return False


__all__ = [
    "ALLOWED_MIMES", "DEFAULT_VISION_PROMPT", "normalize_mime", "sniff_mime",
    "data_url", "image_b64_from_raw", "ollama_messages", "openai_messages",
    "build_vision_messages", "message_has_image",
]
