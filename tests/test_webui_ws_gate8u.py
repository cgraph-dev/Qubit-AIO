"""Packet 8U: Hermes WebSocket gate + the one supported Qubit PTY socket.

Qubit serves a single SSE event path, so the upstream dashboard's refuse-only
WebSocket endpoints answer with a TYPED HTTP 426 JSON envelope carrying the
existing GATED codes (``PTY_NO_SHELL`` / ``WS_SINGLE_PATH``) — never a bare
connection error, a silent hang, or an uncaught exception.

The single exception is Qubit's own terminal socket at ``/agent/api/pty``: it is
a real, supported WebSocket (owner PTY-policy exception, docs/archive/2026-09-21/QUBIT_PTY_POLICY_EXCEPTION_2026-09-17.md)
that requires a short-lived scoped ticket, so an unauthenticated upgrade is a
typed 401 ``TICKET_REQUIRED`` rather than a 426 refusal. Raw ``/api/pty`` HTTP
(no upgrade) and every other upstream socket stay refused.

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

# (path, expected typed code) for the upstream WebSocket endpoints that stay
# refused. The public SPA reaches the API through the Qubit-owned ``/agent``
# prefix; the bare ``/api`` path is the internal compat form.
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

# The refused upgrade endpoints (all but the supported Qubit PTY terminal).
WS_PATHS = [("/agent" + path, code) for path, code in WS_API_PATHS
            if path != "/api/pty"]

# Qubit's supported terminal socket: a real WS that requires a scoped ticket.
PTY_WS_PATHS = ("/agent/api/pty", "/api/pty")


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
    from voicebridge.webui import QUBIT_WS_GATE_CODES, qubit_ws_gate_code
    assert len(QUBIT_WS_GATE_CODES) == len(WS_API_PATHS) == 7
    assert set(QUBIT_WS_GATE_CODES) == {p for p, _ in WS_API_PATHS}
    for path, code in WS_API_PATHS:
        assert qubit_ws_gate_code(path) == code, path
        assert qubit_ws_gate_code("/agent" + path) == code, path
        assert qubit_ws_gate_code("/agent" + path + "/") == code, path
        assert qubit_ws_gate_code("/api/status") is None
        # The upstream mount is gone: an upstream path is not a public gate.
        assert qubit_ws_gate_code("/hermes" + path) is None


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
    # A browser WebSocket() cannot attach X-Hermes-Session-Token; the refusal
    # gate must answer before auth (otherwise the client only sees a bare 401).
    # /console is a still-refused socket, so it proves the pre-auth gate.
    status, _, body = _upgrade(server, "/agent/api/console", token=None)
    assert status == 426, (status, body[:200])
    assert json.loads(body)["code"] == "PTY_NO_SHELL"


def test_plain_api_alias_ws_is_gated(server):
    status, _, body = _upgrade(server, "/api/console")
    assert status == 426, (status, body[:200])
    assert json.loads(body)["code"] == "PTY_NO_SHELL"


@pytest.mark.parametrize("path", PTY_WS_PATHS)
def test_pty_upgrade_requires_a_scoped_ticket(server, path):
    # The Qubit terminal socket is a REAL supported WebSocket, not a 426 refusal.
    # An upgrade without a minted ticket is a typed 401 TICKET_REQUIRED that names
    # the owner and rollback; it never opens a PTY and is never a bare failure.
    status, ctype, body = _upgrade(server, path)
    assert status == 401, (path, status, body[:200])
    assert "application/json" in ctype, (path, ctype)
    out = json.loads(body)
    assert out["code"] == "TICKET_REQUIRED", (path, out)
    assert out["state"] == "UNAVAILABLE", (path, out)
    assert out["ok"] is False, (path, out)
    assert out["owner"] == "qubit-policy", (path, out)
    assert out["rollback"], (path, out)


def test_upstream_ws_path_is_gone(server):
    # No public /hermes route: an upgrade to it is a plain 404, never a gate
    # response that would confirm an upstream mount exists.
    status, _, body = _upgrade(server, "/hermes/api/pty")
    assert status == 404, (status, body[:200])


def test_normal_get_to_real_route_still_works(server):
    # /api/status is a Qubit-authoritative read (the sidebar/System gateway card
    # must read the Qubit supervisor, not the vendored fork's own gateway), so
    # the SPA `/agent/api/status` serves the Qubit compat status document, not
    # the fork's raw status. The WS gate must not interfere with a normal GET.
    # See docs/archive/2026-09-21/QUBIT_HERMES_CUTOVER_MIGRATION.md.
    status, ctype, body = _http(server, "GET", "/agent/api/status")
    assert status == 200, (status, body[:200])
    assert "application/json" in ctype
    served = json.loads(body)
    assert served.get("schema") == "qubit-runtime-status/1", served
    assert served.get("version") and served.get("release_date"), served
    assert "overall" in served and "components" in served, served
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
    # The supported PTY terminal must also be a typed envelope (401), never 500.
    for path in PTY_WS_PATHS:
        status, _, body = _upgrade(server, path)
        assert status == 401, (path, status, body[:200])
        assert status != 500, path
