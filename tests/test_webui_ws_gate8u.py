"""Packet 8U: Hermes WebSocket refusal gate (audit 2026-09-11 §1D).

The vendored Hermes dashboard opens six WebSocket endpoints. Qubit serves a
single SSE event path, so each must answer with a TYPED HTTP 426 JSON envelope
carrying the existing GATED codes (``PTY_NO_SHELL`` / ``WS_SINGLE_PATH``) —
never a bare connection error, a silent hang, or an uncaught exception.

Hermetic live-server checks (no browser, no WS library): a real ``WebUI`` is
booted on loopback and raw ``http.client`` requests carry the upgrade headers.
Also proves normal HTTP paths — including non-upgrade GETs to the same paths —
are unchanged. No skips / xfail.
"""
from __future__ import annotations

import http.client
import json
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

# (path, expected typed code) for the six upstream WebSocket endpoints.
# The public SPA reaches the API through the Qubit-owned ``/agent`` prefix;
# the bare ``/api`` path is the internal compat form.
WS_API_PATHS = [
    ("/api/pty", "PTY_NO_SHELL"),
    ("/api/console", "PTY_NO_SHELL"),
    ("/api/ws", "WS_SINGLE_PATH"),
    ("/api/events", "WS_SINGLE_PATH"),
    ("/api/pub", "WS_SINGLE_PATH"),
    ("/api/audio/speak-stream", "WS_SINGLE_PATH"),
    # Vendored Kanban plugin live-update socket: typed refusal, not a bare 401.
    ("/api/plugins/kanban/events", "WS_SINGLE_PATH"),
]

WS_PATHS = [("/agent" + path, code) for path, code in WS_API_PATHS]


def _start(tmp_path):
    from voicebridge.config import Config
    from voicebridge.webui import WebUI
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    p = tmp_path / "voice.yaml"
    p.write_text(f"logging:\n  jsonl_path: '{state}'\nwebui:\n  token: test-token\n")
    cfg = Config.load(p)

    class _FakeOrch:
        _route = "gateway"
        _web_active = 0
        task = type("T", (), {"active": False})()

        def __init__(self):
            self.cfg = cfg
            self._t0 = time.time()

    ui = WebUI(_FakeOrch(), host="127.0.0.1", port=0, state_dir=state)
    ui.start()
    return ui, ui._server.server_address[1]


@pytest.fixture
def server(tmp_path):
    ui, port = _start(tmp_path)
    try:
        yield port
    finally:
        ui.stop()


def _upgrade(port, path, token=None):
    """Raw WebSocket-upgrade GET; returns (status, content_type, body)."""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    headers = {
        "Upgrade": "websocket",
        "Connection": "Upgrade",
        "Sec-WebSocket-Key": "dGhlIHNhbXBsZSBub25jZQ==",
        "Sec-WebSocket-Version": "13",
    }
    if token:
        headers["X-Api-Token"] = token
    conn.request("GET", path, headers=headers)
    resp = conn.getresponse()
    status, ctype, body = resp.status, resp.getheader("Content-Type", ""), resp.read()
    conn.close()
    return status, ctype, body


def _http(port, method, path, body=None, token="test-token"):
    data = json.dumps(body).encode() if body is not None else None
    headers = {}
    if token:
        headers["X-Api-Token"] = token
    if data is not None:
        headers["Content-Type"] = "application/json"
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request(method, path, body=data, headers=headers)
    resp = conn.getresponse()
    status, ctype, payload = resp.status, resp.getheader("Content-Type", ""), resp.read()
    conn.close()
    return status, ctype, payload


def test_ws_gate_mapping_is_exactly_the_gated_endpoints():
    from voicebridge.webui import HERMES_WS_GATE_CODES, hermes_ws_gate_code
    assert len(HERMES_WS_GATE_CODES) == len(WS_API_PATHS) == 7
    assert set(HERMES_WS_GATE_CODES) == {p for p, _ in WS_API_PATHS}
    for path, code in WS_API_PATHS:
        assert hermes_ws_gate_code(path) == code, path
        assert hermes_ws_gate_code("/agent" + path) == code, path
        assert hermes_ws_gate_code("/agent" + path + "/") == code, path
        assert hermes_ws_gate_code("/api/status") is None
        # The upstream mount is gone: an upstream path is not a public gate.
        assert hermes_ws_gate_code("/hermes" + path) is None


@pytest.mark.parametrize("path,code", WS_PATHS)
def test_each_ws_upgrade_returns_426_typed(server, path, code):
    status, ctype, body = _upgrade(server, path)
    assert status == 426, (path, status, body[:200])
    assert "application/json" in ctype, (path, ctype)
    out = json.loads(body)
    assert out["code"] == code, (path, out)
    assert out["state"] == "UNAVAILABLE", (path, out)
    assert out["ws"] == "single-sse", (path, out)
    assert out["ok"] is False, (path, out)
    assert out["detail"], (path, out)
    assert out["owner"] == "qubit-policy", (path, out)
    assert out["rollback"], (path, out)


def test_ws_upgrade_needs_no_browser_token(server):
    # A browser WebSocket() cannot attach X-Hermes-Session-Token; the gate
    # must answer before auth (otherwise the client only sees a bare 401).
    status, _, body = _upgrade(server, "/agent/api/pty", token=None)
    assert status == 426, (status, body[:200])
    assert json.loads(body)["code"] == "PTY_NO_SHELL"


def test_plain_api_alias_ws_is_gated(server):
    status, _, body = _upgrade(server, "/api/pty")
    assert status == 426, (status, body[:200])
    assert json.loads(body)["code"] == "PTY_NO_SHELL"


def test_upstream_ws_path_is_gone(server):
    # No public /hermes route: an upgrade to it is a plain 404, never a gate
    # response that would confirm an upstream mount exists.
    status, _, body = _upgrade(server, "/hermes/api/pty")
    assert status == 404, (status, body[:200])


def test_normal_get_to_real_route_still_works(server):
    # Cutover stage 2: safe reads are served by the vendored backend, so
    # /agent/api/status carries the real status shape (version/overall),
    # not the compat wrapper's `ok`. See docs/QUBIT_HERMES_CUTOVER_MIGRATION.md.
    status, ctype, body = _http(server, "GET", "/agent/api/status")
    assert status == 200, (status, body[:200])
    assert "application/json" in ctype
    served = json.loads(body)
    assert served.get("version") and "overall" in served, served
    status, _, body = _http(server, "GET", "/api/status")
    assert status == 200, (status, body[:200])


def test_non_ws_get_to_same_paths_unaffected(server):
    # No Upgrade header -> the existing handler answers, never the 426 gate.
    expected = {
        "/agent/api/pty": 403,
        "/agent/api/console": 403,
        "/agent/api/ws": 403,
        "/agent/api/events": 200,
        "/agent/api/pub": 404,
        "/agent/api/audio/speak-stream": 403,
    }
    for path, want in expected.items():
        status, _, body = _http(server, "GET", path)
        assert status == want, (path, status, body[:200])
        assert status != 426, path


def test_non_ws_gated_codes_unchanged(server):
    status, _, body = _http(server, "GET", "/agent/api/pty")
    assert status == 403 and json.loads(body)["code"] == "PTY_NO_SHELL"
    status, _, body = _http(server, "GET", "/agent/api/ws")
    assert status == 403 and json.loads(body)["code"] == "WS_SINGLE_PATH"
    status, _, body = _http(server, "GET", "/agent/api/events")
    assert status == 200 and "events" in json.loads(body)


def test_no_ws_response_is_uncaught_or_500(server):
    for path, _ in WS_PATHS:
        status, _, body = _upgrade(server, path)
        assert status == 426, (path, status, body[:200])
        assert status != 500, path
