"""Qubit dashboard families — Hermes-first.

Every capability family has a real workspace inside the vendored Hermes
React shell: either an upstream Hermes page or a Qubit overlay plugin
(operations console, devices, memory, reports, approvals). This suite
proves:

- every normal Qubit route alias maps to a real Hermes workspace;
- the shared /api/ops broker backs the operations console (catalog +
  run/confirm) on the same registry as CLI and voice;
- no catalog group is left without an operation;
- every family route renders the Hermes shell landmark set.

Hermetic HTTP checks against a real local WebUI server; no browser is
required. Browser checks are opt-in under ``-m ui``.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

# Qubit route -> the Hermes workspace it must render (shell target).
FAMILY_TARGETS = {
    "/activity": "/hermes/logs",
    "/reports": "/hermes/reports",
    "/sessions": "/hermes/sessions",
    "/projects": "/hermes/files",
    "/models": "/hermes/models",
    "/skills": "/hermes/skills",
    "/tasks": "/hermes/cron",
    "/security": "/hermes/qubit-approvals",
    "/devices": "/hermes/devices",
    "/messages": "/hermes/channels",
    "/memory": "/hermes/memory",
    "/operations": "/hermes/operations",
    "/ops": "/hermes/operations",
    "/approvals": "/hermes/qubit-approvals",
    "/settings": "/hermes/config",
    "/setup": "/hermes/system",
    "/system": "/hermes/system",
    "/chat": "/hermes/chat",
    "/files": "/hermes/files",
    "/models": "/hermes/models",
    "/analytics": "/hermes/analytics",
    "/logs": "/hermes/logs",
    "/cron": "/hermes/cron",
    "/plugins": "/hermes/plugins",
    "/mcp": "/hermes/mcp",
    "/channels": "/hermes/channels",
    "/webhooks": "/hermes/webhooks",
    "/pairing": "/hermes/pairing",
    "/profiles": "/hermes/profiles",
    "/config": "/hermes/config",
    "/env": "/hermes/env",
    "/docs": "/hermes/docs",
}

REQUIRED_ROUTES = [
    "/", "/hermes/", "/chat", "/sessions", "/files", "/models", "/analytics",
    "/logs", "/cron", "/skills", "/plugins", "/mcp", "/channels", "/webhooks",
    "/pairing", "/profiles", "/config", "/env", "/system", "/docs",
    "/activity", "/reports", "/operations", "/ops", "/approvals",
    "/settings", "/setup", "/projects", "/tasks", "/devices", "/messages",
    "/memory", "/security",
]


def _start(tmp_path):
    from voicebridge.config import Config
    from voicebridge.webui import WebUI
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    p = tmp_path / "voice.yaml"
    p.write_text(f"logging:\n  jsonl_path: '{state}'\nwebui:\n  token: test-token\n")
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


def _http(port, method, path, body=None, token="test-token", hermes=False):
    import urllib.error
    import urllib.request
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request("http://127.0.0.1:%d%s" % (port, path),
                                 method=method, data=data)
    if token:
        req.add_header("X-Hermes-Session-Token" if hermes else "X-Api-Token",
                       token)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            return r.status, r.headers.get("Content-Type", ""), r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Content-Type", ""), e.read()


def _raw(port, path):
    """Request that does NOT follow redirects."""
    import http.client
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=8)
    conn.request("GET", path)
    resp = conn.getresponse()
    loc = resp.getheader("Location")
    status = resp.status
    resp.read()
    return status, loc


def test_every_family_alias_maps_to_a_real_hermes_workspace():
    from voicebridge import hermes_compat as HC
    from voicebridge.webui import HERMES_ROUTE_ALIASES
    plugin_paths = {"/hermes" + m["tab"]["path"]
                    for m in HC._overlay_manifests()}
    builtin = {
        "/hermes/chat", "/hermes/sessions", "/hermes/files", "/hermes/models",
        "/hermes/analytics", "/hermes/logs", "/hermes/cron", "/hermes/skills",
        "/hermes/plugins", "/hermes/mcp", "/hermes/channels",
        "/hermes/webhooks", "/hermes/pairing", "/hermes/profiles",
        "/hermes/config", "/hermes/env", "/hermes/system", "/hermes/docs",
    }
    known = builtin | plugin_paths
    for alias, target in FAMILY_TARGETS.items():
        assert HERMES_ROUTE_ALIASES.get(alias) == target, (alias, target)
        assert target in known, (alias, target, sorted(known))
    for route in REQUIRED_ROUTES:
        if route in ("/", "/hermes/"):
            continue
        assert route in HERMES_ROUTE_ALIASES, route


def test_every_normal_route_redirects_into_the_shell(tmp_path):
    from voicebridge.webui import hermes_alias_target
    ui, port = _start(tmp_path)
    try:
        status, loc = _raw(port, "/")
        assert (status, loc) == (302, "/hermes/")
        for route in REQUIRED_ROUTES:
            if route in ("/", "/hermes/"):
                continue
            status, loc = _raw(port, route)
            assert status == 302, (route, status)
            assert loc == hermes_alias_target(route), (route, loc)
    finally:
        ui.stop()


def test_ops_catalog_matches_registry_and_runs(tmp_path):
    from voicebridge import dashboard_ops as OPS
    ui, port = _start(tmp_path)
    try:
        status, ctype, body = _http(port, "GET", "/hermes/api/ops",
                                    hermes=True)
        assert status == 200, (status, body[:200])
        out = json.loads(body)
        assert out["count"] == len(OPS.catalog())
        groups = {o["group"] for o in out["ops"]}
        assert groups == {o["group"] for o in OPS.catalog()}
        # run one op per group through the shared broker
        for group in sorted(groups):
            candidates = [o for o in out["ops"] if o["group"] == group]
            read = next((o for o in candidates if not o["confirm"]),
                        candidates[0])
            status, _, body = _http(
                port, "POST", "/hermes/api/ops/run",
                {"id": read["id"], "args": {}}, hermes=True)
            assert status in (200, 400, 409), (group, read["id"], status)
            out2 = json.loads(body)
            assert out2.get("state") in ("executed", "failed", "refused",
                                         "needs-confirm",
                                         "NEEDS_CONFIRMATION"), (group, out2)
            if out2.get("confirmation_id"):
                _http(port, "POST", "/hermes/api/ops/cancel",
                      {"confirmation_id": out2["confirmation_id"]},
                      hermes=True)
        # unknown op is typed, never a fabricated success
        status, _, body = _http(port, "POST", "/hermes/api/ops/run",
                                {"id": "no-such-op", "args": {}}, hermes=True)
        assert status == 400
        assert json.loads(body)["code"] == "UNKNOWN_OP"
    finally:
        ui.stop()


def test_ops_api_stays_header_token_authenticated(tmp_path):
    ui, port = _start(tmp_path)
    try:
        status, _, _ = _http(port, "GET", "/hermes/api/ops", token=None)
        assert status == 401
        status, _, _ = _http(port, "GET", "/hermes/api/ops?token=test-token",
                             token=None)
        assert status == 401, "query-string tokens must stay rejected"
    finally:
        ui.stop()


# ---------------------------------------------------------------------------
# Browser rendering (opt-in ui mark): Hermes shell landmarks, no JS errors
# ---------------------------------------------------------------------------
@pytest.mark.ui
def test_family_routes_render_hermes_shell(tmp_path_factory):
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        pytest.skip(f"playwright unavailable: {exc}")
    from voicebridge.config import Config
    from voicebridge.webui import WebUI

    state = tmp_path_factory.mktemp("fam-ui")
    p = state / "voice.yaml"
    p.write_text(f"logging:\n  jsonl_path: '{state}'\nwebui:\n  token: test-token\n")
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
    base = f"http://127.0.0.1:{ui._server.server_address[1]}"
    try:
        pw = sync_playwright().start()
        try:
            browser = pw.chromium.launch()
        except Exception as exc:
            pytest.skip(f"chromium unavailable: {exc}")
        try:
            for route in FAMILY_TARGETS:
                ctx = browser.new_context(
                    viewport={"width": 1280, "height": 800})
                page = ctx.new_page()
                errors = []
                page.on("console", lambda m: errors.append(m.text)
                        if m.type == "error"
                        and "favicon" not in m.text.lower()
                        and not m.text.startswith("Failed to load resource:")
                        and "status of 403" not in m.text
                        and "401" not in m.text else None)
                page.on("pageerror",
                        lambda exc: errors.append("pageerror: " + str(exc)))
                page.goto(base + route, wait_until="domcontentloaded")
                page.wait_for_timeout(1200)
                html = page.content()
                assert "app-sidebar" in html or 'role="navigation"' in html, route
                assert 'id="main-content"' not in html, route
                fatal = [e for e in errors
                         if not any(s in e for s in (
                             "ws://", "/api/ws", "/api/pty", "WebSocket",
                             "/api/credentials/pool", "/api/portal",
                             "/api/hermes/update/check"))]
                assert not fatal, (route, fatal[:3])
                ctx.close()
        finally:
            browser.close()
            pw.stop()
    finally:
        ui.stop()


@pytest.mark.ui
def test_family_routes_narrow_mobile_no_overflow(tmp_path_factory):
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        pytest.skip(f"playwright unavailable: {exc}")
    from voicebridge.config import Config
    from voicebridge.webui import WebUI

    state = tmp_path_factory.mktemp("fam-mob")
    p = state / "voice.yaml"
    p.write_text(f"logging:\n  jsonl_path: '{state}'\nwebui:\n  token: test-token\n")
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
    base = f"http://127.0.0.1:{ui._server.server_address[1]}"
    try:
        pw = sync_playwright().start()
        try:
            browser = pw.chromium.launch()
        except Exception as exc:
            pytest.skip(f"chromium unavailable: {exc}")
        try:
            for route in ("/", "/activity", "/operations", "/memory"):
                ctx = browser.new_context(
                    viewport={"width": 360, "height": 740})
                page = ctx.new_page()
                page.goto(base + route, wait_until="domcontentloaded")
                page.wait_for_timeout(1000)
                overflow = page.evaluate(
                    "document.documentElement.scrollWidth - "
                    "document.documentElement.clientWidth")
                assert overflow <= 2, (route, overflow)
                ctx.close()
        finally:
            browser.close()
            pw.stop()
    finally:
        ui.stop()
