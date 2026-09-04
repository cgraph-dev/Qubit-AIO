"""Packet 8L opt-in browser accessibility/responsive checks (Playwright).

Run explicitly with:  pytest -m ui tests/test_ui_browser_8l.py

These tests are excluded from the hermetic default run via pytest.ini. They
need a local chromium (``playwright install chromium``) and launch a real local
WebUI server. A missing Playwright package or browser is reported as a SKIP,
which the verifier must treat as DEGRADED, never as a pass.

Coverage: keyboard focus order (skip link), visible focus, WCAG contrast,
reduced motion, forced colors, 44 px touch targets, 320 px reflow without
horizontal overflow, safe rendering of hostile item text, and no JavaScript
console errors on Talk/Activity/Reports/Approvals.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

pytestmark = pytest.mark.ui


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
    port = ui._server.server_address[1]
    base = f"http://127.0.0.1:{port}"
    # Seed the activity feed with a real item plus a hostile text probe to prove
    # the migrated page renders untrusted text as text, never as live HTML.
    ui.feed.push({"kind": "user", "text": "Hello, bridge.", "route": "web"})
    ui.feed.push({"kind": "answered",
                  "text": '<img src=x onerror="window.__xss=1"><script>alert(1)</script>',
                  "summary": "Hostile probe", "route": "local"})
    yield base
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


def _page(browser, base, path="/", viewport=None, authed=True):
    page = browser.new_page(viewport=viewport or {"width": 1280, "height": 800})
    if authed:
        page.add_init_script("localStorage.setItem('vdb_token','test-token');")
    errors = []
    page.on("console", lambda m: errors.append(m.text)
            if m.type == "error"
            and "favicon" not in m.text.lower()
            and not m.text.startswith("Failed to load resource:") else None)
    page.on("pageerror", lambda exc: errors.append("pageerror: " + str(exc)))
    page.goto(base + path, wait_until="domcontentloaded")
    return page, errors


def _enumerate_targets(page):
    """Return every visible interactive element's size. Skip the off-screen
    skip link; hidden elements (zero box) are skipped automatically."""
    return page.evaluate(
        "(() => {"
        " const sel = 'button, a[href], input, select, textarea, summary,"
        "             [role=button], [tabindex]:not([tabindex=\"-1\"])';"
        " const out = [];"
        " for (const el of document.querySelectorAll(sel)) {"
        "   if (el.classList.contains('skip-link')) continue;"
        "   if (el.closest('[hidden]')) continue;"
        "   const cs = getComputedStyle(el);"
        "   if (cs.visibility === 'hidden' || cs.display === 'none') continue;"
        "   const r = el.getBoundingClientRect();"
        "   if (r.width === 0 || r.height === 0) continue;"
        "   out.push({tag: el.tagName.toLowerCase(), id: el.id || '',"
        "             cls: typeof el.className === 'string' ? el.className : '',"
        "             w: Math.round(r.width * 10) / 10,"
        "             h: Math.round(r.height * 10) / 10,"
        "             text: (el.textContent || '').trim().slice(0, 28)});"
        " }"
        " return out; })()")


def _assert_44px(targets, where):
    bad = [t for t in targets if t["w"] < 44 or t["h"] < 44]
    assert not bad, (f"{where}: targets below 44x44: "
                     f"{[t for t in bad]}")


def _wait_status(page, text, elid="st"):
    page.wait_for_function(
        "(o) => { const el = document.getElementById(o.elid); "
        "return el && el.textContent.indexOf(o.needle) !== -1; }",
        arg={"elid": elid, "needle": text}, timeout=8000)


def _anim_ms(page):
    return page.evaluate(
        "(() => { const v = getComputedStyle(document.querySelector('.wrap'))"
        ".animationDuration; return parseFloat(v) * (v.indexOf('ms') !== -1 ? 1 : 1000); })()")


def test_skip_link_focus_and_target_size(browser, ui_server):
    page, errors = _page(browser, ui_server, "/")
    try:
        _wait_status(page, "bridge up")
        assert page.locator(".skip-link").count() == 1
        page.keyboard.press("Tab")
        active = page.evaluate(
            "(() => { const e = document.activeElement; "
            "return {id: e.id, className: e.className, tag: e.tagName}; })()")
        assert "skip-link" in active["className"], "first Tab must reach skip link"
        # Pressing Enter on the skip link moves focus into #main-content.
        page.keyboard.press("Enter")
        page.wait_for_timeout(50)
        target = page.evaluate("document.activeElement.id")
        assert target == "main-content" or page.evaluate(
            "document.getElementById('main-content').contains(document.activeElement)")
        # Primary control target size >= 44 px.
        h = page.locator("#ptt").bounding_box()["height"]
        assert h >= 44, f"PTT target too small: {h}px"
        assert not errors, errors
    finally:
        page.close()


def test_visible_focus_with_keyboard(browser, ui_server):
    page, _ = _page(browser, ui_server, "/")
    try:
        _wait_status(page, "bridge up")
        page.keyboard.press("Tab")  # skip link
        page.keyboard.press("Tab")  # first nav control
        focus = page.evaluate(
            "(() => { const e = document.activeElement; "
            "const cs = getComputedStyle(e); "
            "return {outlineWidth: cs.outlineWidth, outlineStyle: cs.outlineStyle}; })()")
        width = float(focus["outlineWidth"].replace("px", "") or 0)
        assert width > 0 and focus["outlineStyle"] != "none", \
            f"no visible focus on keyboard focus: {focus}"
    finally:
        page.close()


def test_mobile_reflow_and_touch_targets(browser, ui_server):
    page, errors = _page(browser, ui_server, "/",
                         viewport={"width": 320, "height": 640})
    try:
        _wait_status(page, "bridge up")
        overflow = page.evaluate(
            "document.documentElement.scrollWidth - document.documentElement.clientWidth")
        assert overflow <= 0, f"horizontal overflow at 320px: {overflow}px"
        for sel in [".page-nav a[aria-current='page']", "#ptt", "#textsend"]:
            h = page.locator(sel).first.bounding_box()["height"]
            assert h >= 44, f"{sel} too small at 320px: {h}px"
        assert not errors, errors
    finally:
        page.close()


def test_reduced_motion(browser, ui_server):
    page, _ = _page(browser, ui_server, "/")
    try:
        _wait_status(page, "bridge up")
        default = _anim_ms(page)
        assert default >= 100, f"expected normal motion, got {default}ms"
        page.close()
        page, _ = _page(browser, ui_server, "/")
        page.emulate_media(reduced_motion="reduce")
        _wait_status(page, "bridge up")
        reduced = _anim_ms(page)
        assert reduced < 50, f"reduced motion not applied: {reduced}ms"
    finally:
        page.close()


def test_forced_colors_surface(browser, ui_server):
    page, errors = _page(browser, ui_server, "/")
    try:
        page.emulate_media(forced_colors="active", color_scheme="dark")
        _wait_status(page, "bridge up")
        btn = page.locator("#textsend").bounding_box()
        assert btn["height"] >= 44
        assert page.evaluate(
            "getComputedStyle(document.getElementById('textsend')).display") != "none"
        assert not errors, errors
    finally:
        page.close()


def test_text_contrast(browser, ui_server):
    page, _ = _page(browser, ui_server, "/")
    try:
        _wait_status(page, "bridge up")
        ratio = page.evaluate(
            "(() => { const lum = c => { const [r,g,b] = c.slice(4,-1).split(',').map(Number)"
            ".map(v=>v/255).map(v=>v<=0.03928?v/12.92:Math.pow((v+0.055)/1.055,2.4));"
            " return 0.2126*r+0.7152*g+0.0722*b; };"
            " const cs = getComputedStyle(document.body);"
            " const L1 = lum(cs.color), L2 = lum(cs.backgroundColor);"
            " return (Math.max(L1,L2)+0.05)/(Math.min(L1,L2)+0.05); })()")
        assert ratio >= 4.5, f"body text contrast too low: {ratio:.2f}"
    finally:
        page.close()


def test_activity_render_and_hostile_text_is_escaped(browser, ui_server):
    page, errors = _page(browser, ui_server, "/activity")
    try:
        _wait_status(page, "live", "conn")
        assert page.locator(".item").count() >= 2
        # The hostile probe text must render as text and never execute.
        assert page.evaluate("window.__xss") is None
        assert page.evaluate(
            "!![...document.querySelectorAll('.item .text')].find(el => "
            "el.textContent.indexOf('onerror') !== -1)")
        assert page.evaluate("document.querySelectorAll('.item img').length") == 0
        assert not errors, errors
    finally:
        page.close()


def test_reports_and_approvals_render_without_errors(browser, ui_server):
    page, errors = _page(browser, ui_server, "/reports")
    try:
        _wait_status(page, "live", "conn")
        assert page.locator(".toolbar").is_visible()
        assert page.locator("#feed").count() == 1
        page.close()
        page, errors = _page(browser, ui_server, "/approvals")
        page.wait_for_function(
            "document.getElementById('list').textContent.indexOf('Nothing') !== -1",
            timeout=8000)
        assert not errors, errors
    finally:
        page.close()


def test_settings_page_loads_and_reports_real_health(browser, ui_server):
    page, errors = _page(browser, ui_server, "/settings")
    try:
        page.wait_for_function(
            "(o) => { const el = document.getElementById('health'); "
            "return el && el.textContent.indexOf(o.needle) !== -1; }",
            arg={"needle": "bridge up"}, timeout=8000)
        assert page.locator("#onboarding").is_visible()
        assert page.locator(".page-nav").is_visible()
        assert page.locator("button.warn").count() >= 1  # Advanced toggle
        overflow = page.evaluate(
            "document.documentElement.scrollWidth - document.documentElement.clientWidth")
        assert overflow <= 0, f"settings horizontal overflow at 1280px: {overflow}px"
        assert not errors, errors
    finally:
        page.close()


def test_settings_mobile_reflow(browser, ui_server):
    page, _ = _page(browser, ui_server, "/settings",
                    viewport={"width": 320, "height": 640})
    try:
        page.wait_for_function(
            "(o) => { const el = document.getElementById('health'); "
            "return el && el.textContent.indexOf(o.needle) !== -1; }",
            arg={"needle": "bridge up"}, timeout=8000)
        overflow = page.evaluate(
            "document.documentElement.scrollWidth - document.documentElement.clientWidth")
        assert overflow <= 0, f"settings horizontal overflow at 320px: {overflow}px"
    finally:
        page.close()


def test_every_route_has_one_semantic_main_landmark(browser, ui_server):
    for route in ["/", "/activity", "/reports", "/settings",
                  "/approvals", "/token-setup"]:
        page, errors = _page(browser, ui_server, route)
        try:
            assert page.locator("main").count() == 1, route
            main = page.locator("main").first
            assert main.get_attribute("id") == "main-content", route
            assert main.get_attribute("tabindex") == "-1", route
            # the landmark is not a div/h1
            assert page.locator('div[id="main-content"]').count() == 0, route
            assert page.locator('h1[id="main-content"]').count() == 0, route
            assert page.locator('a.skip-link[href="#main-content"]').count() == 1, route
            assert not errors, (route, errors)
        finally:
            page.close()


def test_all_visible_targets_desktop_44px(browser, ui_server):
    page, errors = _page(browser, ui_server, "/")
    try:
        _wait_status(page, "bridge up")
        # Open the toggled Talk panels so their controls are also measured.
        page.click("#permslink")
        page.click("#reportslink")
        page.click("#gearlink")
        page.wait_for_timeout(300)
        _assert_44px(_enumerate_targets(page), "Talk desktop")
        assert not errors, errors
    finally:
        page.close()
    for route in ["/activity", "/reports", "/settings", "/approvals"]:
        page, errors = _page(browser, ui_server, route)
        try:
            if route == "/activity":
                _wait_status(page, "live", "conn")
            elif route == "/reports":
                _wait_status(page, "live", "conn")
            elif route == "/settings":
                page.wait_for_function(
                    "(o) => { const el = document.getElementById('health'); "
                    "return el && el.textContent.indexOf(o.needle) !== -1; }",
                    arg={"needle": "bridge up"}, timeout=8000)
            else:
                page.wait_for_timeout(300)
            _assert_44px(_enumerate_targets(page), f"{route} desktop")
            assert not errors, (route, errors)
        finally:
            page.close()


def test_all_visible_targets_mobile_44px(browser, ui_server):
    for route in ["/", "/activity", "/reports", "/settings"]:
        page, errors = _page(browser, ui_server, route,
                             viewport={"width": 320, "height": 640})
        try:
            if route in ("/activity", "/reports"):
                _wait_status(page, "live", "conn")
            elif route == "/settings":
                page.wait_for_function(
                    "(o) => { const el = document.getElementById('health'); "
                    "return el && el.textContent.indexOf(o.needle) !== -1; }",
                    arg={"needle": "bridge up"}, timeout=8000)
            else:
                _wait_status(page, "bridge up")
            overflow = page.evaluate(
                "document.documentElement.scrollWidth - document.documentElement.clientWidth")
            assert overflow <= 0, f"{route} horizontal overflow at 320px: {overflow}px"
            _assert_44px(_enumerate_targets(page), f"{route} mobile")
            assert not errors, (route, errors)
        finally:
            page.close()


def test_auth_required_pair_hint_link_target(browser, ui_server):
    """A 401 (no token / expired pairing) must render the pairing hint, and its
    link must meet the 44px target (dynamically generated control)."""
    page, errors = _page(browser, ui_server, "/", authed=False)
    try:
        page.wait_for_selector("#pair-hint", timeout=8000)
        page.wait_for_timeout(200)
        assert page.locator("#pair-hint a[href='/token-setup']").count() == 1
        _assert_44px(_enumerate_targets(page), "pair-hint desktop")
        assert not errors, errors
    finally:
        page.close()


def test_offline_state_is_typed_not_false_success(browser, ui_server):
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    try:
        # Point the shared helper at a dead port: every API/stream call fails
        # as a network error; the UI must report it and never claim 'bridge up'.
        page.add_init_script(
            "localStorage.setItem('vdb_api','http://127.0.0.1:1');")
        page.goto(ui_server + "/", wait_until="domcontentloaded")
        page.wait_for_function(
            "() => { const el = document.getElementById('st'); "
            "return el && el.textContent.indexOf('connecting') === -1; }",
            timeout=8000)
        st = page.evaluate("document.getElementById('st').textContent")
        assert "bridge up" not in st, st
        assert "bridge" in st, st  # typed offline/degraded copy, not blank
    finally:
        page.close()


def test_bounded_activity_dom(browser, ui_server):
    page, _ = _page(browser, ui_server, "/activity")
    try:
        _wait_status(page, "live", "conn")
        first = page.locator(".item").count()
        page.wait_for_timeout(4500)  # span several 2s refresh cycles
        second = page.locator(".item").count()
        assert first == second <= 5, (first, second)
        page.wait_for_timeout(4500)
        third = page.locator(".item").count()
        assert third == second <= 5, (second, third)
    finally:
        page.close()