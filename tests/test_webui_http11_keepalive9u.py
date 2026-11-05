"""Dashboard transport: HTTP/1.1 keep-alive, framing and cache validators.

Hermes serves its dashboard over HTTP/1.1 keep-alive; Qubit used the stdlib
HTTP/1.0 default, so every static/API response opened a new TCP connection.
These are the transport contracts a browser relies on:

* repeated static and JSON requests reuse ONE connection (same client port),
* every non-streaming response carries an exact ``Content-Length``,
* HEAD returns the GET headers with no body,
* SSE remains a streaming, close-delimited response (never keep-alive),
* the token-bearing shell stays ``no-store``,
* content-hashed ``/assets/`` stay immutable and carry validators.

A throwaway ``WebUI`` on an ephemeral loopback port is driven with
``http.client`` — no live :8890 service and no network.
"""
from __future__ import annotations

import http.client
import sys
import threading
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

TOKEN = "http11-token"


class _Cfg:
    def get(self, *a, **k):
        if len(a) >= 2 and a[0] == "webui" and a[1] == "host":
            return "127.0.0.1"
        if len(a) >= 2 and a[0] == "webui" and a[1] == "allow_lan":
            return False
        if len(a) >= 2 and a[0] == "webui" and a[1] == "frontend_dir":
            return None
        if len(a) >= 2 and a[0] == "asr" and a[1] == "prewarm":
            return "off"
        return k.get("default")


class _Task:
    active = False
    run_id = None


class _Orch:
    cfg = _Cfg()
    task = _Task()


@pytest.fixture()
def dashboard(tmp_path, monkeypatch):
    from voicebridge.webui import WebUI, WebUIHandler

    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "assets" / "app-abc123.js").write_bytes(
        b"export const x = 1;\n" * 400)
    (dist / "plugins" / "demo").mkdir(parents=True)
    (dist / "plugins" / "demo" / "plugin.js").write_bytes(
        b"export const p = 1;\n" * 50)
    (dist / "index.html").write_text(
        "<html><head></head><body>shell</body></html>", encoding="utf-8")

    monkeypatch.setattr(WebUIHandler, "_qubit_dist", lambda self: dist)
    ui = WebUI(_Orch(), host="127.0.0.1", port=0,
               state_dir=tmp_path / "state")
    ui.api_token = TOKEN
    threading.Thread(target=ui.start, daemon=True).start()
    for _ in range(200):
        if ui._server is not None:
            break
        time.sleep(0.02)
    assert ui._server is not None, "webui did not start"
    yield ui
    ui.stop()


def _base(ui) -> tuple[str, int]:
    port = ui._server.server_address[1]
    return f"http://127.0.0.1:{port}", port


def _request(conn, method, path, headers=None):
    conn.request(method, path, headers=headers or {})
    local_port = conn.sock.getsockname()[1] if conn.sock else None
    resp = conn.getresponse()
    body = resp.read()
    return resp, body, local_port


def test_json_and_static_requests_reuse_one_connection(dashboard):
    _, port = _base(dashboard)
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        ports, versions = set(), set()
        for _ in range(4):
            resp, body, local_port = _request(conn, "GET", "/api/health")
            assert resp.status == 200
            assert resp.version == 11
            assert resp.will_close is False
            assert int(resp.getheader("Content-Length")) == len(body)
            ports.add(local_port)
            versions.add(resp.version)
        for _ in range(4):
            resp, body, local_port = _request(
                conn, "GET", "/assets/app-abc123.js")
            assert resp.status == 200
            assert resp.version == 11
            assert resp.will_close is False
            assert int(resp.getheader("Content-Length")) == len(body)
            ports.add(local_port)
            versions.add(resp.version)
        assert len(ports) == 1, ports
        assert versions == {11}, versions
    finally:
        conn.close()


def test_static_and_json_content_length_is_exact(dashboard):
    _, port = _base(dashboard)
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        resp, body, _ = _request(conn, "GET", "/api/health")
        assert int(resp.getheader("Content-Length")) == len(body)

        resp, body, _ = _request(conn, "GET", "/assets/app-abc123.js")
        assert int(resp.getheader("Content-Length")) == len(body)
    finally:
        conn.close()


def test_head_returns_headers_without_a_body(dashboard):
    _, port = _base(dashboard)
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        get_resp, get_body, _ = _request(conn, "GET", "/api/health")
        head_resp, head_body, _ = _request(conn, "HEAD", "/api/health")
        assert head_resp.status == get_resp.status == 200
        assert head_body == b""
        assert head_resp.getheader("Content-Length") == \
            get_resp.getheader("Content-Length") == str(len(get_body))
        assert head_resp.will_close is False

        get_resp, get_body, _ = _request(conn, "GET", "/assets/app-abc123.js")
        head_resp, head_body, _ = _request(
            conn, "HEAD", "/assets/app-abc123.js")
        assert head_body == b""
        assert head_resp.getheader("Content-Length") == str(len(get_body))

        # /api/activity writes its body outside _send; HEAD must not leak it.
        get_resp, get_body, _ = _request(
            conn, "GET", "/api/activity", {"X-Api-Token": TOKEN})
        head_resp, head_body, _ = _request(
            conn, "HEAD", "/api/activity", {"X-Api-Token": TOKEN})
        assert get_resp.status == head_resp.status == 200
        assert head_body == b""
        assert head_resp.getheader("Content-Length") == str(len(get_body))
    finally:
        conn.close()


def test_hashed_assets_are_immutable_and_revalidate(dashboard):
    _, port = _base(dashboard)
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        resp, _, _ = _request(conn, "GET", "/assets/app-abc123.js")
        assert resp.getheader("Cache-Control") == \
            "public, max-age=31536000, immutable"
        etag = resp.getheader("ETag")
        assert etag
        assert resp.getheader("Last-Modified")

        resp, body, _ = _request(
            conn, "GET", "/assets/app-abc123.js",
            {"If-None-Match": etag})
        assert resp.status == 304
        assert body == b""
        assert resp.getheader("ETag") == etag

        # Stable-name assets keep the existing short cache (not immutable).
        resp, _, _ = _request(conn, "GET", "/plugins/demo/plugin.js")
        assert resp.getheader("Cache-Control") == "public, max-age=3600"
        assert resp.getheader("ETag")
    finally:
        conn.close()


def test_event_sse_is_streaming_close_delimited_and_recovers(dashboard):
    _, port = _base(dashboard)
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        conn.request("GET", "/api/events/stream",
                     headers={"X-Api-Token": TOKEN})
        resp = conn.getresponse()
        assert resp.status == 200
        assert resp.version == 11
        assert resp.getheader("Content-Type", "").startswith(
            "text/event-stream")
        assert resp.getheader("Connection") == "close"
        assert resp.getheader("Content-Length") is None
        assert resp.getheader("Transfer-Encoding") is None
        first = resp.read1(64)
        assert first.startswith(b"retry: 3000")
        resp.close()
        conn.close()
    finally:
        try:
            conn.close()
        except Exception:
            pass

    # The listener survives the closed feed and still frames the next request.
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        resp, body, _ = _request(conn, "GET", "/api/health")
        assert resp.status == 200
        assert int(resp.getheader("Content-Length")) == len(body)
    finally:
        conn.close()


def test_token_bearing_shell_remains_no_store(dashboard):
    _, port = _base(dashboard)
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        resp, body, _ = _request(conn, "GET", "/chat")
        assert resp.status == 200
        assert resp.getheader("Cache-Control") == "no-store"
        assert int(resp.getheader("Content-Length")) == len(body)
        assert TOKEN.encode() in body

        head_resp, head_body, _ = _request(conn, "HEAD", "/chat")
        assert head_resp.status == 200
        assert head_resp.getheader("Cache-Control") == "no-store"
        assert head_body == b""
    finally:
        conn.close()


def test_http10_client_is_told_the_connection_closes(dashboard):
    """Older clients must not be left waiting on a persistent socket."""
    import socket

    _, port = _base(dashboard)
    sock = socket.create_connection(("127.0.0.1", port), timeout=10)
    try:
        sock.sendall(
            b"GET /api/health HTTP/1.0\r\n"
            b"Host: 127.0.0.1\r\n\r\n")
        chunks = []
        while True:
            piece = sock.recv(4096)
            if not piece:
                break
            chunks.append(piece)
    finally:
        sock.close()
    raw = b"".join(chunks)
    head, _, body = raw.partition(b"\r\n\r\n")
    headers = {}
    for line in head.decode("latin-1").split("\r\n")[1:]:
        key, _, value = line.partition(":")
        headers[key.strip().lower()] = value.strip()
    assert head.startswith(b"HTTP/1.")
    assert headers.get("connection", "").lower() == "close"
    assert int(headers["content-length"]) == len(body)
