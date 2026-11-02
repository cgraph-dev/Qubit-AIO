"""Office transport: the SSE frames a BROWSER can actually consume, and the
one-shot JSON snapshot.

WHY THIS FILE EXISTS
--------------------
`webui._serve_office_stream` previously answered HTTP/1.1 with
``Content-Type: text/event-stream``, ``Connection: keep-alive`` and NO
``Transfer-Encoding`` or ``Content-Length``. curl streamed it, so a raw probe
looked healthy, but Chromium waited for framing that never arrived: the
EventSource fired ``open`` and then never delivered a ``snapshot`` event, and
the vendored app stayed on "Connecting to the office…" forever.

The fix is chunked transfer encoding. This test drives the real handler method
against a real tower over a tmp office store and parses the chunks a browser
would receive — not a mock of the handler, and not curl's forgiving parser.

Hermetic: tmp XDG dirs only; no network, no live service.
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from voicebridge import webui as W  # noqa: E402

pytestmark = pytest.mark.unit


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _seed(cfg):
    from voicebridge.office import ledger, org as O

    ledger.health(cfg)
    floor = O.floor_create(cfg, 1, "Operations", role="operations",
                           confirmed=True)["id"]
    ceo = O.hire(cfg, "ceo", seat_type="ceo", confirmed=True)["id"]
    dept = O.department_create(cfg, "ops", name="Operations", floor_id=floor,
                               kill_criteria="net negative for 30 days",
                               confirmed=True)["id"]
    room = O.room_create(cfg, dept, "workshop", confirmed=True)["id"]
    return O.hire(cfg, "wk-1", seat_type="worker", supervisor_id=ceo,
                  department_id=dept, room_id=room, confirmed=True)["id"]


class _Handler:
    """The socket bits of a WebUIHandler, captured in memory."""

    def __init__(self, cfg, *, path="/api/stream", token="configured"):
        h = object.__new__(W.WebUIHandler)
        h.path = path
        h.command = "GET"
        h._head_only = False
        h.client_address = ("127.0.0.1", 9000)
        h.headers = {"Host": "127.0.0.1:8890"}
        h.wfile = io.BytesIO()
        h.headers_out = []
        h.close_connection = False
        h.protocol_version = ""

        app = type("A", (), {
            "api_token": token,
            "orch": type("O", (), {"cfg": cfg})(),
            "_server": object(),
        })()
        h.server = type("S", (), {"app": app})()

        h.send_response = lambda code, message=None: h.headers_out.append(
            ("status", code))
        h.send_header = lambda key, value: h.headers_out.append((key, value))
        h.end_headers = lambda: None
        h._office_app_caller_ok = lambda **_: True
        h._office_client_read_ok = lambda: True
        h._json = self._capture_json
        self.h = h
        self.json = None

    def _capture_json(self, obj, code=200):
        self.json = (code, obj)

    def header(self, name):
        for key, value in self.h.headers_out:
            if key.lower() == name.lower():
                return value
        return None


def _chunks(data: bytes):
    """Parse an HTTP chunked body the way a browser's reader would."""
    out, i = [], 0
    while True:
        j = data.index(b"\r\n", i)
        size = int(data[i:j], 16)
        i = j + 2
        if size == 0:
            return out
        out.append(data[i:i + size])
        i += size + 2


def test_stream_is_chunked_and_delivers_a_snapshot_frame(monkeypatch,
                                                         tmp_path):
    cfg = _cfg(tmp_path, monkeypatch)
    _seed(cfg)
    from voicebridge import office_stream as OS

    # One short iteration is enough to prove framing; do not hold the test.
    monkeypatch.setattr(OS, "STREAM_MAX_SECONDS", 0.01)
    monkeypatch.setattr(OS, "SNAPSHOT_INTERVAL_SECONDS", 0.005)
    handler = _Handler(cfg)
    handler.h._serve_office_stream()

    assert handler.header("Transfer-Encoding") == "chunked", handler.headers_out
    assert handler.header("Content-Type") == "text/event-stream; charset=utf-8"
    body = handler.h.wfile.getvalue()
    frames = _chunks(body)
    assert frames, "the stream wrote no chunked frames at all"
    payload = b"".join(frames).decode("utf-8")
    assert payload.startswith("retry:"), payload[:80]
    assert "event: snapshot" in payload, payload[:200]
    snap = json.loads(payload.split("event: snapshot\ndata: ", 1)[1]
                      .split("\n\n", 1)[0])
    assert snap["rooms"] and snap["agents"], snap
    assert snap["meta"]["messages"] is True
    assert snap["meta"]["terminal"] is True
    # The body is terminated, so an idle client's reader completes.
    assert body.endswith(b"0\r\n\r\n"), body[-40:]


def test_snapshot_route_is_one_json_document_not_an_sse_stream(tmp_path,
                                                               monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    _seed(cfg)
    handler = _Handler(cfg, path="/api/snapshot")
    handler.h._serve_office_snapshot()
    assert handler.json is not None, "the snapshot route produced no JSON"
    code, snap = handler.json
    assert code == 200, (code, snap)
    assert snap["ok"] is True
    assert snap["rooms"] and snap["agents"], snap
    assert snap["serverTime"] > 0
    assert snap["meta"]["messages"] is True
