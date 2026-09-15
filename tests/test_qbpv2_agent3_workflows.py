"""Agent 3 (wave 2) — live dashboard workflow crawl (desktop + narrow mobile).

Boots the real Qubit WebUI on the built hermes-dist and drives every dashboard
navigation item with headless Chromium. For each route it asserts: HTTP 200, a
real rendered workspace (not an empty shell), no uncaught page error, no
disallowed console error, every backend call is answered (2xx or a documented
policy gate), and zero visible upstream product names. Narrow-mobile coherence
is checked on the primary workspaces.
"""
from __future__ import annotations

import re
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

DIST = REPO_ROOT / "frontend" / "hermes-dist"
pytestmark = pytest.mark.skipif(
    not (DIST / "index.html").is_file(),
    reason="hermes-dist not built (npm build step)")

pw = pytest.importorskip("playwright.sync_api")

UPSTREAM = re.compile(r"\b(Hermes|OpenClaw)\b")

# Canonical + overlay + family-alias routes: the full navigation surface.
ROUTES = [
    "/chat", "/sessions", "/files", "/analytics", "/models", "/logs",
    "/cron", "/skills", "/plugins", "/mcp", "/channels", "/webhooks",
    "/pairing", "/profiles", "/config", "/env", "/system", "/docs",
    "/achievements", "/kanban", "/qubit-boards", "/reports", "/operations",
    "/memory", "/devices",
    "/projects", "/tasks", "/activity", "/messages", "/approvals",
    "/settings", "/setup",
]
MOBILE_ROUTES = ["/chat", "/sessions", "/achievements", "/qubit-boards",
                 "/operations", "/models", "/skills", "/reports"]

# Policy-gated endpoints the dashboard intentionally cannot read (documented
# Qubit refusals), plus WS handshake noise from the single-event-path rule.
ALLOWED_403 = (
    "/api/config/raw", "/api/providers/oauth", "/api/credentials/pool",
    "/api/portal", "/api/qubit/update/check", "/api/dashboard/plugin-providers",
)
ALLOWED_WS = ("/api/plugins/kanban/events", "/api/ws", "/api/events",
              "/api/pty", "/api/console", "/api/pub")


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


def _visible_text(page):
    return page.evaluate(
        """() => {
          const out = [];
          const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
          let n;
          while ((n = w.nextNode())) {
            let el = n.parentElement, ok = true;
            while (el && el !== document.body) {
              const cs = getComputedStyle(el);
              if (cs.display === 'none' || cs.visibility === 'hidden') { ok = false; break; }
              el = el.parentElement;
            }
            if (ok && n.textContent.trim()) out.push(n.textContent.trim());
          }
          return out.join('\\n');
        }"""
    )


def _drive(port, routes, viewport):
    results = []
    with pw.sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport=viewport)
        for route in routes:
            page = ctx.new_page()
            page_errors, console_errors, api = [], [], []

            page.on("pageerror", lambda exc: page_errors.append(str(exc)[:300]))
            page.on("console", lambda msg: (
                console_errors.append(msg.text[:300])
                if msg.type == "error" else None))
            page.on("response", lambda resp: (
                api.append((resp.status,
                            resp.url.split("127.0.0.1", 1)[-1][:140]))
                if ("/api/" in resp.url or "/agent/" in resp.url) else None))
            page.on("requestfailed", lambda req: (
                api.append(("FAILED",
                            req.url.split("127.0.0.1", 1)[-1][:140]))
                if ("/api/" in req.url or "/agent/" in req.url) else None))

            nav = page.goto(f"http://127.0.0.1:{port}{route}", timeout=30000)
            page.wait_for_timeout(1800)
            text = _visible_text(page)
            results.append({
                "route": route, "status": nav.status if nav else None,
                "page_errors": page_errors, "console_errors": console_errors,
                "api": api, "text": text,
                "leaked": sorted(set(UPSTREAM.findall(text))),
            })
            page.close()
        ctx.close()
        browser.close()
    return results


def test_every_nav_route_is_a_rendered_workspace(tmp_path):
    ui, port = _start(tmp_path)
    try:
        rows = _drive(port, ROUTES, {"width": 1440, "height": 900})
    finally:
        ui.stop()

    for r in rows:
        assert r["status"] == 200, (r["route"], r["status"])
        assert not r["page_errors"], (r["route"], r["page_errors"])
        # A real workspace, not a blank/dead shell.
        assert len(r["text"].strip()) > 60, (r["route"], r["text"][:80])
        # No upstream product identity in normal UI.
        assert not r["leaked"], (r["route"], r["leaked"])
        # Every backend call answered: 2xx, or an allowlisted policy gate.
        for status, url in r["api"]:
            if status == "FAILED":
                assert any(a in url for a in ALLOWED_WS), (r["route"], url)
                continue
            if status in (200, 204, 302):
                continue
            if status == 403:
                assert any(a in url for a in ALLOWED_403), (r["route"], url)
                continue
            raise AssertionError(f"{r['route']}: unexpected API {status} {url}")

    # No disallowed console errors (WS handshake refusals + gated 403s are the
    # documented by-design noise; a 404 is NOT allowed).
    for r in rows:
        for msg in r["console_errors"]:
            lowered = msg.lower()
            assert ("websocket" in lowered and "426" in lowered) or \
                ("403" in lowered and "forbidden" in lowered), (r["route"], msg)


def test_narrow_mobile_workspaces_are_coherent(tmp_path):
    ui, port = _start(tmp_path)
    try:
        rows = _drive(port, MOBILE_ROUTES, {"width": 390, "height": 844})
    finally:
        ui.stop()
    for r in rows:
        assert r["status"] == 200, (r["route"], r["status"])
        assert not r["page_errors"], (r["route"], r["page_errors"])
        assert len(r["text"].strip()) > 60, (r["route"], r["text"][:80])
        assert not r["leaked"], (r["route"], r["leaked"])
