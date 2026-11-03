"""Served dashboard media — the ambient video backdrop must actually play.

The backdrop is a looping video at ``/media/qubit-backdrop.mp4``. Two things
have to hold or the browser silently refuses it, and both were missing when the
asset shipped:

* the ``/media/`` prefix must route to the static handler (it only knew
  ``/assets/``, ``/fonts/``, ``/fonts-terminal/`` and ``/plugins/``), and
* the response must carry ``video/mp4`` AND advertise byte ranges. HTML5 video
  scrubs and loops through ``Range`` requests; a server that reads the whole
  file into memory and answers every range with a bare 200 makes the element
  refetch the file on each seek.
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from voicebridge import webui as ui  # noqa: E402


class _FakeApp:
    api_token = "media-test-token"


class _FakeServer:
    app = _FakeApp()


class _FakeWfile:
    def __init__(self) -> None:
        self.buf = io.BytesIO()
        self.chunks: list[bytes] = []

    def write(self, data: bytes) -> None:
        self.chunks.append(bytes(data))
        self.buf.write(data)

    def getvalue(self) -> bytes:
        return self.buf.getvalue()


def _handler(base: Path, range_header: str | None = None):
    handler = object.__new__(ui.WebUIHandler)
    handler.server = _FakeServer()
    handler._head_only = False
    handler.headers = {"Range": range_header} if range_header else {}
    handler._qubit_dist = lambda: base  # type: ignore[method-assign]
    captured: dict[str, object] = {}
    handler.wfile = _FakeWfile()

    def send_response(code):
        captured["code"] = code

    def send_header(name, value):
        captured.setdefault("headers", {})[name] = value

    def end_headers():
        pass

    def _send(code, body, ctype="text/plain"):
        captured["code"] = code
        captured["body"] = body
        captured["ctype"] = ctype

    handler.send_response = send_response  # type: ignore[method-assign]
    handler.send_header = send_header  # type: ignore[method-assign]
    handler.end_headers = end_headers  # type: ignore[method-assign]
    handler._send = _send  # type: ignore[method-assign]
    return handler, captured


def _media_tree(tmp_path: Path) -> Path:
    (tmp_path / "media").mkdir(parents=True, exist_ok=True)
    (tmp_path / "media" / "qubit-backdrop.mp4").write_bytes(b"\x00" * 4096)
    return tmp_path


def test_media_asset_is_served_as_video_with_range_support(tmp_path):
    base = _media_tree(tmp_path)
    handler, captured = _handler(base, "bytes=0-1023")

    handler._serve_qubit_static("/media/qubit-backdrop.mp4")

    assert captured.get("code") == 206
    headers = captured["headers"]
    assert headers["Content-Type"] == "video/mp4"
    assert headers["Accept-Ranges"] == "bytes"
    assert headers["Content-Range"] == "bytes 0-1023/4096"
    assert headers["Content-Length"] == "1024"
    assert len(handler.wfile.getvalue()) == 1024


def test_media_asset_without_range_sends_the_whole_body(tmp_path):
    base = _media_tree(tmp_path)
    handler, captured = _handler(base)

    handler._serve_qubit_static("/media/qubit-backdrop.mp4")

    headers = captured["headers"]
    assert captured.get("code") == 200
    assert headers["Content-Type"] == "video/mp4"
    assert headers["Accept-Ranges"] == "bytes"
    assert headers["Content-Length"] == "4096"
    assert handler.wfile.getvalue() == b"\x00" * 4096


def test_suffix_range_serves_the_tail(tmp_path):
    base = _media_tree(tmp_path)
    handler, captured = _handler(base, "bytes=-512")

    handler._serve_qubit_static("/media/qubit-backdrop.mp4")

    assert captured["code"] == 206
    assert captured["headers"]["Content-Range"] == "bytes 3584-4095/4096"
    assert len(handler.wfile.getvalue()) == 512


def test_unsatisfiable_range_is_416(tmp_path):
    base = _media_tree(tmp_path)
    handler, captured = _handler(base, "bytes=9000-9100")

    handler._serve_qubit_static("/media/qubit-backdrop.mp4")

    assert captured["code"] == 416
    assert captured["headers"]["Content-Range"] == "bytes */4096"


def test_missing_media_is_404_not_a_crash(tmp_path):
    base = _media_tree(tmp_path)
    handler, captured = _handler(base)

    handler._serve_qubit_static("/media/nope.mp4")

    assert captured.get("code") == 404
