"""ChatSidebar event feed transport contract.

Qubit keeps a single event path: the read-only SSE feed at
``GET /api/events/stream`` (``voicebridge.webui:WebUIHandler._events_stream``,
route ``webui.py:1728``). The dashboard SPA consumes it with ``fetch`` + a
``ReadableStream`` reader because ``EventSource`` cannot attach the auth
header (``X-Api-Token`` / ``X-Qubit-Session-Token``). These tests assert the
server side of that contract: fail-closed auth, the ``text/event-stream``
framing, journal replay via ``Last-Event-ID`` / ``?last_seq=``, and
heartbeats. The SPA side is covered by
``frontend/qubit-web/src/lib/events-sse.test.ts`` and
``frontend/qubit-web/src/components/ChatSidebar.test.tsx``.
"""
from __future__ import annotations

import json
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from voicebridge.events import make_event


@pytest.fixture()
def sse_server(tmp_path):
    """WebUI instance on an ephemeral port with token auth enabled."""
    class _Cfg:
        def get(self, *a, **k):
            return {"token": "sekrit", "host": "127.0.0.1"}.get(a[-1]) if a else None

        def section(self, name):
            return {}

    class _Task:
        active = False
        run_id = None

    class _Orch:
        cfg = _Cfg()
        task = _Task()

    from voicebridge.webui import WebUI

    ui = WebUI(_Orch(), host="127.0.0.1", port=0, state_dir=tmp_path)
    ui.api_token = "sekrit"
    threading.Thread(target=ui.start, daemon=True).start()
    for _ in range(100):
        if ui._server is not None:
            break
        time.sleep(0.05)
    assert ui._server is not None, "webui did not start"
    yield ui
    ui.stop()


def _request(base_dir: str, path: str, headers: dict | None = None,
             timeout: float = 10.0):
    req = urllib.request.Request(base_dir + path, headers=headers or {})
    try:
        # Not a context manager: the caller keeps the streaming body open and
        # closes it after reading (a `with` would close the reader immediately).
        resp = urllib.request.urlopen(req, timeout=timeout)
        return resp.status, dict(resp.headers), resp
    except urllib.error.HTTPError as exc:
        code, headers = exc.code, dict(exc.headers)
        exc.read()
        exc.close()
        return code, headers, b""


def _read_until(resp, needle: bytes, deadline_s: float = 5.0) -> bytes:
    deadline = time.time() + deadline_s
    buf = b""
    while needle not in buf and time.time() < deadline:
        chunk = resp.read1(4096)
        if not chunk:
            break
        buf += chunk
    return buf


def _ids(buf: str) -> list[int]:
    out = []
    for line in buf.splitlines():
        if line.startswith("id:"):
            out.append(int(line[3:].strip()))
    return out


def _data_events(buf: str) -> list[dict]:
    events = []
    for line in buf.splitlines():
        if line.startswith("data:"):
            raw = line[5:].strip()
            if raw:
                events.append(json.loads(raw))
    return events


def test_events_stream_fails_closed_without_header_token(sse_server):
    port = sse_server._server.server_address[1]
    base = f"http://127.0.0.1:{port}"

    status, _, _ = _request(base, "/api/events/stream")
    assert status == 401

    status, _, _ = _request(base, "/api/events/stream?token=sekrit")
    assert status == 401


def test_events_stream_serves_event_stream_with_header_token(sse_server):
    ui = sse_server
    port = ui._server.server_address[1]
    base = f"http://127.0.0.1:{port}"
    ui._events_hub.publish(make_event("STEP_STARTED", step_name="first"))

    status, headers, resp = _request(
        base, "/api/events/stream", {"X-Api-Token": "sekrit"}
    )
    assert status == 200
    assert headers.get("Content-Type", "").startswith("text/event-stream")
    assert headers.get("Cache-Control") == "no-store"

    buf = _read_until(resp, b"STEP_STARTED")
    resp.close()
    text = buf.decode()
    assert text.startswith("retry: 3000")
    events = _data_events(text)
    assert any(e.get("type") == "STEP_STARTED" for e in events)


def test_events_stream_last_event_id_header_replays_continuation(sse_server):
    ui = sse_server
    port = ui._server.server_address[1]
    base = f"http://127.0.0.1:{port}"
    for i in range(3):
        ui._events_hub.publish(make_event("STEP_STARTED", step_name=f"s{i}"))

    # Last-Event-ID is the SSE-reconnect header the fetch reader mirrors with
    # ?last_seq=. The server must replay strictly after the cursor.
    status, _, resp = _request(
        base,
        "/api/events/stream",
        {"X-Api-Token": "sekrit", "Last-Event-ID": "2"},
    )
    assert status == 200
    buf = _read_until(resp, b"s2")
    resp.close()
    text = buf.decode()
    assert _ids(text) == [3]
    names = [e.get("stepName") for e in _data_events(text)]
    assert names == ["s2"]


def test_events_stream_last_seq_query_replays_continuation(sse_server):
    ui = sse_server
    port = ui._server.server_address[1]
    base = f"http://127.0.0.1:{port}"
    for i in range(3):
        ui._events_hub.publish(make_event("STEP_STARTED", step_name=f"q{i}"))

    status, _, resp = _request(
        base, "/api/events/stream?last_seq=1", {"X-Api-Token": "sekrit"}
    )
    assert status == 200
    buf = _read_until(resp, b"q2")
    resp.close()
    text = buf.decode()
    assert _ids(text) == [2, 3]
    assert [e.get("stepName") for e in _data_events(text)] == ["q1", "q2"]


def test_events_stream_heartbeats_when_idle(sse_server):
    ui = sse_server
    port = ui._server.server_address[1]
    base = f"http://127.0.0.1:{port}"

    status, _, resp = _request(
        base, "/api/events/stream", {"X-Api-Token": "sekrit"}, timeout=30.0
    )
    assert status == 200
    # No backlog and no live events: the writer emits a `: ping` comment after
    # its ~15s drain timeout.
    buf = _read_until(resp, b": ping", deadline_s=25.0)
    resp.close()
    assert b": ping" in buf
