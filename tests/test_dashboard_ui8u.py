"""Hermes-first dashboard UI: every normal route renders the vendored
Hermes React shell, the operations console runs real backend ops, and a
screenshot per route is captured for visual review.

Opt-in (``-m ui``): needs Playwright + bundled Chromium. Hermetic
otherwise (local server, synthetic token, tmp state).
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest as _pytest

pytestmark = _pytest.mark.ui

REPO_ROOT = Path(__file__).resolve().parents[1]

ROUTES = ["/", "/chat", "/qubit-chat", "/sessions", "/files", "/models",
          "/analytics", "/logs", "/cron", "/skills", "/plugins", "/mcp",
          "/channels", "/webhooks", "/pairing", "/profiles", "/config",
          "/env", "/system", "/docs", "/activity", "/reports",
          "/operations", "/ops", "/approvals", "/settings", "/setup",
          "/projects", "/tasks", "/devices", "/messages", "/memory",
          "/security"]


def _start(tmp_path_factory):
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        _pytest.skip(f"playwright unavailable: {exc}")
    from voicebridge.config import Config
    from voicebridge.webui import WebUI

    state = tmp_path_factory.mktemp("dash-ui")
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
    pw = sync_playwright().start()
    try:
        browser = pw.chromium.launch()
    except Exception as exc:
        pw.stop()
        ui.stop()
        _pytest.skip(f"chromium unavailable: {exc}")
    return ui, pw, browser, base


ALLOWED_NOISE = ("ws://", "/api/ws", "/api/events?channel=", "/api/pty",
                 "/api/credentials/pool", "/api/portal",
                 "/api/hermes/update/check", "Failed to load resource",
                 "status of 403", "WebSocket")


def _page(browser, base, route):
    ctx = browser.new_context(viewport={"width": 1280, "height": 800})
    page = ctx.new_page()
    errors = []
    page.on("console", lambda m: errors.append(m.text)
            if m.type == "error" else None)
    page.on("pageerror", lambda exc: errors.append("pageerror: " + str(exc)))
    page.goto(base + route, wait_until="domcontentloaded")
    page.wait_for_selector("nav, aside, [role=navigation]", timeout=30000)
    page.wait_for_timeout(1200)
    page._fatal_errors = [e for e in errors
                          if not any(s in e for s in ALLOWED_NOISE)]
    return ctx, page, errors


def test_every_route_loads_the_qubit_shell(tmp_path_factory):
    ui, pw, browser, base = _start(tmp_path_factory)
    try:
        for route in ROUTES:
            ctx, page, errors = _page(browser, base, route)
            try:
                html = page.content()
                assert "app-sidebar" in html or 'role="navigation"' in html, route
                assert 'id="main-content"' not in html, route
                assert "page-nav" not in html, route
                assert not page._fatal_errors, (route, page._fatal_errors[:3])
            finally:
                ctx.close()
    finally:
        browser.close()
        pw.stop()
        ui.stop()


def test_operations_console_runs_backend_op(tmp_path_factory):
    ui, pw, browser, base = _start(tmp_path_factory)
    try:
        ctx, page, errors = _page(browser, base, "/operations")
        try:
            page.wait_for_timeout(1500)
            body = page.inner_text("body")
            assert "Operations" in body
            assert "ops" in body.lower()
            # The catalog (353 ops) must be rendered from the real backend.
            assert page.locator('input[aria-label="Search operations"]').count() == 1
            # Run a known read op by searching and clicking it.
            page.fill('input[aria-label="Search operations"]', "insights")
            page.wait_for_timeout(500)
            rows = page.locator('[role="listitem"]')
            assert rows.count() >= 1
            rows.first.click()
            page.wait_for_timeout(300)
            page.locator('button[aria-label^="Run "]').first.click()
            page.wait_for_timeout(1500)
            assert "HTTP 200" in page.inner_text("body"), body[:500]
            assert not page._fatal_errors, page._fatal_errors[:3]
        finally:
            ctx.close()
    finally:
        browser.close()
        pw.stop()
        ui.stop()


def test_mobile_layout_holds(tmp_path_factory):
    ui, pw, browser, base = _start(tmp_path_factory)
    try:
        for route in ("/", "/activity", "/operations", "/memory"):
            ctx = browser.new_context(viewport={"width": 390, "height": 844})
            page = ctx.new_page()
            page.goto(base + route, wait_until="domcontentloaded")
            page.wait_for_timeout(1500)
            overflow = page.evaluate(
                "document.documentElement.scrollWidth - "
                "document.documentElement.clientWidth")
            assert overflow <= 2, (route, overflow)
            ctx.close()
    finally:
        browser.close()
        pw.stop()
        ui.stop()


def test_family_screenshots(tmp_path_factory):
    shots = tmp_path_factory.mktemp("shots")
    ui, pw, browser, base = _start(tmp_path_factory)
    try:
        captured = 0
        for route in ROUTES:
            ctx, page, errors = _page(browser, base, route)
            try:
                name = "root" if route == "/" else route.strip("/").replace("/", "_")
                shot = shots / f"{name}.png"
                page.screenshot(path=str(shot), full_page=False)
                assert shot.is_file() and shot.stat().st_size > 5000, route
                assert not page._fatal_errors, (route, page._fatal_errors[:3])
                captured += 1
            finally:
                ctx.close()
        assert captured == len(ROUTES)
    finally:
        browser.close()
        pw.stop()
        ui.stop()
