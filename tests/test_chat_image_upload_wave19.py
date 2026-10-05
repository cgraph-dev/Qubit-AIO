"""Browser clipboard image paste is a real, working capability (wave 19).

``POST /api/chat/image-upload`` was a typed ``CHAT_IMAGE_OVERLAY`` gate on the
compat surface, while the dashboard paste path
(``frontend/qubit-web/src/pages/ChatPage.tsx`` -> ``uploadChatImage`` in
``src/lib/chatImagePaste.ts``) posts straight to it. A gate there meant every
pasted screenshot failed with an unhandled 403 and the user saw "no image".

The endpoint is not an unsafe upstream surface: the vendored fork already
serves it (``qubit_cli/web_routers/files.py::upload_chat_image``) validating
MIME, size-capping, sniffing magic bytes and writing into
``QUBIT_HOME/images/`` so the embedded TUI can read the returned path.

These tests pin, through the real production functions:
* the compat table no longer shadows the route with a typed refusal;
* the real handler rejects a non-image payload and a spoofed extension rather
  than trusting the client MIME;
* a valid PNG round-trips to a readable file under ``QUBIT_HOME/images/``.
"""
from __future__ import annotations

import base64
import pathlib
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# 1x1 transparent PNG.
_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8"
    "z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


# ── the compat handler the dashboard actually calls ──────────────────────

def _call(body):
    """Drive the REAL dispatcher, so an unregistered handler name fails here."""
    from voicebridge import qubit_compat as HC
    return HC.dispatch(None, "POST", "/api/chat/image-upload", {}, body)


def test_handler_name_is_registered_in_handlers():
    """A route whose handler name is absent from HANDLERS 500s live."""
    from voicebridge import qubit_compat as HC

    route, _ = HC.match("POST", "/api/chat/image-upload")
    assert route["handler"] in HC.HANDLERS, (
        f"{route['handler']} is not in HANDLERS - the route would answer "
        "NO_HANDLER at runtime"
    )


def _data_url(raw: bytes, mime: str = "image/png") -> str:
    return f"data:{mime};base64," + base64.b64encode(raw).decode()


def test_handler_serves_the_route_not_a_gate():
    """The route must resolve to a real handler, never a typed refusal."""
    from voicebridge import qubit_compat as HC

    route, _ = HC.match("POST", "/api/chat/image-upload")
    assert route is not None, "route is not registered at all"
    assert route["disposition"] == "implemented", route
    assert route["handler"] == "h_chat_image_upload", route


def test_handler_rejects_non_data_url():
    code, out = _call({"data_url": "not-a-data-url"})
    assert code == 400 and out["code"] == "BAD_IMAGE", out


def test_handler_rejects_non_image_mime():
    code, out = _call({"data_url": _data_url(b"hello", "text/plain")})
    assert code == 400 and out["code"] == "BAD_IMAGE", out


def test_handler_rejects_spoofed_png():
    """Bytes that are not a PNG must fail the magic-byte sniff."""
    code, out = _call({"data_url": _data_url(b"definitely-not-a-png")})
    assert code == 400 and out["code"] == "BAD_IMAGE", out


def test_handler_rejects_declared_mime_mismatch():
    """A GIF claiming image/png is a spoof, not a passthrough."""
    fake_gif = b"GIF89a" + b"\x00" * 20
    code, out = _call({"data_url": _data_url(fake_gif, "image/png")})
    assert code == 400 and out["code"] == "BAD_IMAGE", out


def test_handler_writes_a_real_png(tmp_path, monkeypatch):
    """A valid PNG lands under <home>/images/ and is byte-identical."""
    import qubit_constants

    monkeypatch.setattr(qubit_constants, "get_qubit_home", lambda: tmp_path)
    code, out = _call({"data_url": _data_url(_PNG), "filename": "shot.png"})

    assert code == 200 and out["ok"] is True, out
    written = pathlib.Path(out["path"])
    assert written.parent == tmp_path / "images", written
    assert written.read_bytes() == _PNG
    assert written.name.endswith(".png")
    assert out["bytes"] == len(_PNG)
