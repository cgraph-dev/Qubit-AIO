"""Hermes-first browser accessibility / responsive checks (Playwright).

Run explicitly with:  pytest -m ui tests/test_ui_browser_8l.py

Excluded from the hermetic default run via pytest.ini. They need a local
chromium and launch a real local WebUI server. A missing Playwright
package or browser is reported as a SKIP (verifier: DEGRADED, not pass).

The old static Qubit pages are retired from normal routes; these checks
target the Hermes React shell that now serves every route.
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.ui

KEY_ROUTES = ["/", "/sessions", "/activity", "/reports", "/operations",
              "/settings", "/memory", "/devices", "/approvals"]

ALLOWED_NOISE = ("ws://", "/api/ws", "/api/events?channel=", "/api/pty",
                 "/api/credentials/pool", "/api/portal",
                 "/api/qubit/update/check", "Failed to load resource",
                 "status of 403", "WebSocket")


@pytest.fixture(scope="module")
def ui_server(tmp_path_factory):
    from voicebridge.config import Config
    from voicebridge.webui import WebUI
    state = tmp_path_factory.mktemp("state")
    p = state / "voice.yaml"
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
    yield f"http://127.0.0.1:{ui._server.server_address[1]}"
    ui.stop()


@pytest.fixture()
def browser():
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"playwright unavailable: {exc}")
    pw = sync_playwright().start()
    try:
        b = pw.chromium.launch(headless=True)
    except Exception as exc:  # pragma: no cover
        pw.stop()
        pytest.skip(f"chromium launch failed: {exc}")
    yield b
    b.close()
    pw.stop()


def _page(browser, base, path="/", viewport=None):
    ctx = browser.new_context(viewport=viewport or {"width": 1280, "height": 800})
    page = ctx.new_page()
    errors = []
    page.on("console", lambda m: errors.append(m.text)
            if m.type == "error" else None)
    page.on("pageerror", lambda exc: errors.append("pageerror: " + str(exc)))
    page.goto(base + path, wait_until="domcontentloaded")
    page.wait_for_selector("nav, aside, [role=navigation]", timeout=30000)
    page.wait_for_timeout(1200)
    return ctx, page, errors


def _fatal(errors):
    return [e for e in errors if not any(s in e for s in ALLOWED_NOISE)]


def test_key_routes_have_no_fatal_console_errors(browser, ui_server):
    for route in KEY_ROUTES:
        ctx, page, errors = _page(browser, ui_server, route)
        try:
            assert not _fatal(errors), (route, _fatal(errors)[:3])
        finally:
            ctx.close()


def test_navigation_landmarks_and_accessible_names(browser, ui_server):
    ctx, page, errors = _page(browser, ui_server, "/sessions")
    try:
        assert page.locator("nav, aside").count() >= 1
        unnamed = page.evaluate(
            "Array.from(document.querySelectorAll('button, a[href]'))"
            ".filter(el => { const t = (el.innerText || el.getAttribute('aria-label')"
            " || el.getAttribute('title') || '').trim();"
            " return !t; }).length")
        assert unnamed == 0, f"{unnamed} controls without accessible names"
        assert page.locator("h1, h2").count() >= 1
    finally:
        ctx.close()


def test_mobile_reflow_no_horizontal_overflow(browser, ui_server):
    for width in (320, 390):
        for route in ("/", "/activity", "/operations"):
            ctx, page, errors = _page(browser, ui_server, route,
                                      viewport={"width": width, "height": 740})
            try:
                overflow = page.evaluate(
                    "document.documentElement.scrollWidth - "
                    "document.documentElement.clientWidth")
                assert overflow <= 2, (route, width, overflow)
            finally:
                ctx.close()


def test_keyboard_focus_reaches_a_control(browser, ui_server):
    ctx, page, errors = _page(browser, ui_server, "/sessions")
    try:
        page.keyboard.press("Tab")
        page.keyboard.press("Tab")
        active = page.evaluate(
            "(() => { const e = document.activeElement;"
            " return {tag: e.tagName, name: (e.innerText || e.getAttribute('aria-label') || '').trim()}; })()")
        assert active["tag"] in ("A", "BUTTON", "INPUT", "SELECT"), active
        assert active["name"], active
        assert not _fatal(errors), _fatal(errors)[:3]
    finally:
        ctx.close()


def test_reduced_motion_still_renders(browser, ui_server):
    ctx = browser.new_context(viewport={"width": 1280, "height": 800})
    page = ctx.new_page()
    try:
        page.emulate_media(reduced_motion="reduce")
        page.goto(ui_server + "/sessions", wait_until="domcontentloaded")
        page.wait_for_selector("nav, aside, [role=navigation]", timeout=30000)
        page.wait_for_timeout(800)
        assert page.locator("nav, aside").count() >= 1
        overflow = page.evaluate(
            "document.documentElement.scrollWidth - "
            "document.documentElement.clientWidth")
        assert overflow <= 2
    finally:
        ctx.close()
