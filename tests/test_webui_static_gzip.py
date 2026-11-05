"""Served dashboard assets: gzip on the wire, identical bytes in the tab.

Every dashboard JS/CSS chunk is served through `WebHandler._file`. Before
compression, I confirmed the wire form by hand: `curl -H 'Accept-Encoding:
gzip'` on `/assets/index-*.js` returned 139,702 bytes — byte-for-byte the file
on disk, no `Content-Encoding`, no `Vary`. A hard refresh therefore re-transfered
the whole bundle uncompressed.

What must hold:

* a gzip-capable client gets `Content-Encoding: gzip` and a body that
  decompresses to EXACTLY the file on disk (the UI cannot change),
* a client without `Accept-Encoding: gzip` gets the raw bytes and no
  `Content-Encoding` (a browser that cannot compress must never be handed a
  compressed body),
* binary/media formats are passed through — gzipping an mp4 or a woff2 wastes
  CPU and breaks nothing but helps nobody,
* tiny bodies stay raw (the header overhead cancels the win),
* a missing file is still a 404, not a crash inside the compressor.
"""
from __future__ import annotations

import gzip
import io
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from voicebridge import webui as ui  # noqa: E402


class _FakeApp:
    api_token = "gzip-test-token"
    allowed_origins: list[str] = []


class _FakeServer:
    app = _FakeApp()


class _FakeWfile:
    def __init__(self) -> None:
        self.buf = io.BytesIO()

    def write(self, data: bytes) -> None:
        self.buf.write(data)

    def getvalue(self) -> bytes:
        return self.buf.getvalue()


def _handler(base: Path, accept_encoding: str | None = None):
    handler = object.__new__(ui.WebUIHandler)
    handler.server = _FakeServer()
    handler._head_only = False
    handler.headers = (
        {"Accept-Encoding": accept_encoding} if accept_encoding else {}
    )
    handler._qubit_dist = lambda: base  # type: ignore[method-assign]
    handler.wfile = _FakeWfile()
    captured: dict[str, object] = {}

    def send_response(code):
        captured["code"] = code

    def send_header(name, value):
        captured.setdefault("headers", {})[name] = value

    def end_headers():
        pass

    handler.send_response = send_response  # type: ignore[method-assign]
    handler.send_header = send_header  # type: ignore[method-assign]
    handler.end_headers = end_headers  # type: ignore[method-assign]
    return handler, captured


def _asset_tree(tmp_path: Path, name: str, body: bytes) -> Path:
    (tmp_path / "assets").mkdir(parents=True, exist_ok=True)
    (tmp_path / "assets" / name).write_bytes(body)
    return tmp_path


def test_js_asset_is_gzipped_for_a_capable_client(tmp_path):
    body = b"export const x = 1;\n" * 500
    base = _asset_tree(tmp_path, "index-abc123.js", body)
    handler, captured = _handler(base, "gzip, deflate, br")

    handler._serve_qubit_static("/assets/index-abc123.js")

    headers = captured["headers"]
    assert captured["code"] == 200
    assert headers["Content-Encoding"] == "gzip"
    assert headers["Vary"] == "Accept-Encoding"
    assert headers["Content-Type"] == "application/javascript"
    wire = handler.wfile.getvalue()
    assert int(headers["Content-Length"]) == len(wire)
    assert len(wire) < len(body)
    # The decoded body is byte-identical to the file: the UI cannot change.
    assert gzip.decompress(wire) == body


def test_js_asset_is_raw_without_gzip_support(tmp_path):
    body = b"export const y = 2;\n" * 500
    base = _asset_tree(tmp_path, "index-def456.js", body)
    handler, captured = _handler(base)

    handler._serve_qubit_static("/assets/index-def456.js")

    headers = captured["headers"]
    assert "Content-Encoding" not in headers
    assert int(headers["Content-Length"]) == len(body)
    assert handler.wfile.getvalue() == body


def test_small_asset_stays_raw(tmp_path):
    body = b"body{}\n"
    base = _asset_tree(tmp_path, "tiny-abc.css", body)
    handler, captured = _handler(base, "gzip")

    handler._serve_qubit_static("/assets/tiny-abc.css")

    assert "Content-Encoding" not in captured["headers"]
    assert handler.wfile.getvalue() == body


def test_woff2_font_is_never_gzipped(tmp_path):
    body = b"\x00\x01\x00\x00" * 2000
    base = _asset_tree(tmp_path, "font-abc.woff2", body)
    handler, captured = _handler(base, "gzip")

    handler._serve_qubit_static("/assets/font-abc.woff2")

    assert "Content-Encoding" not in captured["headers"]
    assert handler.wfile.getvalue() == body


def test_video_keeps_range_semantics_and_is_not_gzipped(tmp_path):
    body = b"\x00" * 4096
    base = _asset_tree(tmp_path, "clip.mp4", body)
    handler, captured = _handler(base, "gzip")

    handler._serve_qubit_static("/assets/clip.mp4")

    headers = captured["headers"]
    assert headers["Content-Type"] == "video/mp4"
    assert headers["Accept-Ranges"] == "bytes"
    assert "Content-Encoding" not in headers
    assert handler.wfile.getvalue() == body


def test_missing_asset_is_404(tmp_path):
    base = _asset_tree(tmp_path, "present.js", b"x")
    handler, captured = _handler(base, "gzip")

    handler._serve_qubit_static("/assets/absent.js")

    assert captured["code"] == 404


def test_compressor_never_writes_headers_itself(tmp_path):
    """Regression (dashboard stuck on "Still loading", ERR_INVALID_HTTP_RESPONSE).

    ``BaseHTTPRequestHandler.send_header`` before ``send_response`` writes the
    header bytes onto the socket ahead of the HTTP status line, so the browser
    sees an HTTP/0.9 reply and rejects every asset. The compressor must only
    return bytes; ``_send`` emits Content-Encoding/Vary inside the header block.
    """
    body = b"export const z = 3;\n" * 500
    base = _asset_tree(tmp_path, "index-ghi789.js", body)
    handler, _ = _handler(base, "gzip")

    def forbidden(name, value):
        raise AssertionError(f"gzip wrote {name!r} before the status line")

    handler.send_header = forbidden  # type: ignore[method-assign]
    wire, gzipped = handler._gzip_if_wanted(body, base / "assets" / "index-ghi789.js")

    assert gzipped is True
    assert gzip.decompress(wire) == body


def test_hashed_assets_are_immutable_and_revalidate_free(tmp_path):
    """Vite content-hashes every /assets/ name, so a changed file gets a new
    URL. Serving them as immutable lets a reload or a revisited lazy route use
    the browser cache instead of re-downloading the bundle."""
    body = b"export const cached = 1;\n" * 500
    base = _asset_tree(tmp_path, "index-jkl012.js", body)
    handler, captured = _handler(base, "gzip")

    handler._serve_qubit_static("/assets/index-jkl012.js")

    assert captured["headers"]["Cache-Control"] == \
        "public, max-age=31536000, immutable"


def test_stable_name_assets_get_a_short_cache(tmp_path):
    """Plugin/media files keep stable names, so they must not be immutable: an
    update has to become visible without a cache-busting rename."""
    (tmp_path / "plugins" / "demo").mkdir(parents=True)
    body = b"export const x = 1;\n" * 200
    (tmp_path / "plugins" / "demo" / "plugin.js").write_bytes(body)
    handler, captured = _handler(tmp_path, "gzip")

    handler._serve_qubit_static("/plugins/demo/plugin.js")

    assert captured["headers"]["Cache-Control"] == "public, max-age=3600"


def test_generic_send_defaults_to_no_store(tmp_path):
    """The dashboard shell carries the one-shot token; it must not be cached by
    accident when a caller does not opt into a cache policy."""
    handler, captured = _handler(tmp_path)

    handler._send(200, b"hello", "text/plain")

    assert captured["headers"]["Cache-Control"] == "no-store"


def test_gzip_output_is_reused_for_an_unchanged_file(tmp_path, monkeypatch):
    body = b"export const reused = 1;\n" * 500
    base = _asset_tree(tmp_path, "index-mno345.js", body)
    handler, _ = _handler(base, "gzip")
    calls = {"n": 0}
    real_compress = ui.gzip.compress

    def counting_compress(data, level=6):
        calls["n"] += 1
        return real_compress(data, level)

    monkeypatch.setattr(ui.gzip, "compress", counting_compress)

    first, first_gz = handler._gzip_if_wanted(
        body, base / "assets" / "index-mno345.js")
    second, second_gz = handler._gzip_if_wanted(
        body, base / "assets" / "index-mno345.js")

    assert first_gz is True and second_gz is True
    assert first is second
    assert calls["n"] == 1
    assert gzip.decompress(second) == body


def _index_handler(base: Path, accept_encoding: str = "gzip"):
    handler, captured = _handler(base, accept_encoding)
    handler.client_address = ("127.0.0.1", 0)
    handler.headers = {"Host": "127.0.0.1",
                       "Accept-Encoding": accept_encoding}
    return handler, captured


def test_dashboard_shell_is_gzipped_and_never_cached(tmp_path):
    body = ("<html><head><!-- " + ("pad " * 500) +
            " --></head><body>shell</body></html>").encode()
    (tmp_path / "index.html").write_bytes(body)
    handler, captured = _index_handler(tmp_path)

    handler.server.app.api_token = "token-one"
    handler._serve_qubit_index()

    headers = captured["headers"]
    assert headers["Content-Encoding"] == "gzip"
    assert headers["Vary"] == "Accept-Encoding"
    assert headers["Cache-Control"] == "no-store"
    decoded = gzip.decompress(handler.wfile.getvalue()).decode()
    assert "token-one" in decoded


def test_dashboard_shell_does_not_serve_a_cached_injection(tmp_path):
    """The shell is generated per request (session token + theme). Reusing a
    gzip body keyed by the file would hand a stale credential to the next tab."""
    body = ("<html><head><!-- " + ("pad " * 500) +
            " --></head><body>shell</body></html>").encode()
    (tmp_path / "index.html").write_bytes(body)
    handler, _ = _index_handler(tmp_path)

    handler.server.app.api_token = "token-one"
    handler._serve_qubit_index()
    first = gzip.decompress(handler.wfile.getvalue()).decode()

    handler.server.app.api_token = "token-two"
    handler.wfile = _FakeWfile()
    handler._serve_qubit_index()
    second = gzip.decompress(handler.wfile.getvalue()).decode()

    assert "token-one" in first
    assert "token-two" in second
    assert "token-one" not in second
