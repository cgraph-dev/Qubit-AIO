"""Qubit rebrand wave 2 — public routing contract.

Proves the owner-agreed routing table against a real local WebUI listener:

- ``/`` redirects to ``/chat`` (Ruling 3 Option A; never ``/hermes``);
- every public client route serves the SPA shell with the root div;
- ``/hermes`` and ``/hermes/*`` are 404 with no redirect;
- the SPA API prefix ``/agent/api/*`` reaches the compat backend;
- the native Qubit API at ``/api/*`` is unchanged;
- no ``/hermes`` route exists in the served surface.

Hermetic HTTP checks; no browser needed.
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

CANONICAL_ROUTES = (
    "/chat", "/sessions", "/files", "/models", "/analytics", "/logs",
    "/cron", "/skills", "/plugins", "/mcp", "/channels", "/webhooks",
    "/pairing", "/profiles", "/config", "/env", "/system", "/docs",
    "/agents",
)
FAMILY_ROUTES = (
    "/activity", "/reports", "/operations", "/ops", "/approvals",
    "/settings", "/setup", "/projects", "/tasks", "/devices",
    "/messages", "/memory", "/security",
)
# Overlay/plugin tab routes and deep links served by the client router.
PLUGIN_ROUTES = (
    "/achievements", "/kanban", "/sessions/deep-link",
    "/profiles/new",
)
QUBIT_PATHS = (
    "/hermes",
    "/hermes/",
    "/hermes/chat",
    "/hermes/sessions",
    "/hermes/api/status",
    "/hermes/api/ops",
    "/hermes/assets/index.js",
    "/hermes/fonts/x.woff2",
    "/hermes/favicon.ico",
    "/hermes/plugins/fleet.json",
    "/hermes/dashboard-plugins/qubit-chat/manifest.json",
)


def _start(tmp_path):
    from voicebridge.config import Config
    from voicebridge.webui import WebUI

    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    p = tmp_path / "voice.yaml"
    p.write_text(f"logging:\n  jsonl_path: '{state}'\n"
                 f"webui:\n  token: test-token\n  qubit_backend: false\n")
    cfg = Config.load(p)

    class _FakeOrch:
        _route = "local"
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


def _raw(port, path, method="GET", body=None,
         headers=None):
    """Request that does NOT follow redirects."""
    hdrs = dict(headers or {})
    data = json.dumps(body).encode() if body is not None else None
    if data is not None:
        hdrs.setdefault("Content-Type", "application/json")
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request(method, path, body=data, headers=hdrs)
    resp = conn.getresponse()
    out = (resp.status, resp.getheader("Location"),
           resp.getheader("Content-Type", ""), resp.read())
    conn.close()
    return out


def test_root_redirects_to_chat(server):
    status, loc, _, _ = _raw(server, "/")
    assert status == 302, status
    assert loc == "/chat", loc
    assert "hermes" not in (loc or "").lower()


@pytest.mark.parametrize("route", CANONICAL_ROUTES + FAMILY_ROUTES)
def test_public_route_serves_spa_shell(server, route):
    status, loc, ctype, body = _raw(server, route)
    if status == 302:
        # Qubit family alias -> its real Qubit route (never an upstream prefix)
        assert loc and not loc.startswith("/hermes"), (route, loc)
        status, loc, ctype, body = _raw(server, loc)
    if status == 404 and b"not built" in body:
        pytest.skip("dashboard build absent in this checkout")
    assert status == 200, (route, status)
    assert loc is None, (route, loc)
    assert "text/html" in ctype, (route, ctype)
    assert b'id="root"' in body, route


@pytest.mark.parametrize("route", PLUGIN_ROUTES)
def test_plugin_and_deep_link_routes_serve_spa_shell(server, route):
    status, loc, ctype, body = _raw(server, route)
    if status == 404 and b"not built" in body:
        pytest.skip("dashboard build absent in this checkout")
    assert status == 200, (route, status)
    assert "text/html" in ctype, (route, ctype)
    assert b'id="root"' in body, route


@pytest.mark.parametrize("path", QUBIT_PATHS)
def test_no_qubit_route_exists(server, path):
    status, loc, _, body = _raw(server, path)
    assert status == 404, (path, status, body[:120])
    assert loc is None, (path, loc)


def test_agent_prefix_reaches_compat_backend(server):
    status, loc, ctype, body = _raw(
        server, "/agent/api/status",
        headers={"X-Qubit-Session-Token": "test-token"})
    assert status == 200, (status, body[:200])
    assert "application/json" in ctype, ctype
    out = json.loads(body)
    # Flag off -> the compat status shape, not the fork's raw /api/status.
    assert out.get("schema") == "qubit-runtime-status/1", out


def test_agent_prefix_requires_auth(server):
    status, _, _, _ = _raw(server, "/agent/api/status")
    assert status == 401, status


def test_agents_page_is_not_shadowed_by_the_agent_api_prefix(server):
    """``/agents`` is a shell page; only ``/agent`` (+ ``/agent/...``) is API.

    A prior regression treated any path starting with ``/agent`` as the API
    namespace, so the ``/agents`` page hard-loaded 404 while client-side nav
    still worked.
    """
    status, loc, ctype, body = _raw(server, "/agents")
    if status == 404 and b"not built" in body:
        pytest.skip("dashboard build absent in this checkout")
    assert status == 200, (status, body[:120])
    assert "text/html" in ctype, ctype
    assert b'id="root"' in body
    # The API namespace itself is unchanged and still authenticated.
    status, _, _, _ = _raw(server, "/agent/api/status")
    assert status == 401, status


def test_native_api_status_unchanged(server):
    status, loc, ctype, body = _raw(
        server, "/api/status", headers={"X-Api-Token": "test-token"})
    assert status == 200, (status, body[:200])
    out = json.loads(body)
    assert out.get("bridge") == "up", out
    assert out.get("schema") != "qubit-runtime-status/1"


def test_agent_chat_route_reaches_compat_backend(server):
    status, _, ctype, body = _raw(
        server, "/agent/api/qubit/chat/sessions",
        headers={"X-Qubit-Session-Token": "test-token"})
    assert status == 200, (status, body[:200])
    assert "application/json" in ctype, ctype
    assert "sessions" in json.loads(body)
