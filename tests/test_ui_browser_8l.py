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
    _seed_8l_state(state)
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


def _seed_8l_state(state) -> None:
    """Seed backend state used by the Packet 8L browser tests: one canonical
    report bundle (for authenticated export/html/asset flows) and hostile
    backend-controlled values (integration_id, skill_id, profile_id) to prove
    the Settings UI renders them as text and never executes them."""
    import hashlib
    import json
    from voicebridge.communications.contracts import (  # noqa: E402
        CommunicationItem, EvidenceAssetRecord, EvidenceStatus, Platform,
        ReadState, ReportManifest, ReportStatus,
    )
    from voicebridge.communications.reports import ReportStore  # noqa: E402

    store = ReportStore(state / "reports")
    items = [CommunicationItem.create(
        platform=Platform.GMAIL, account_scope="a",
        sender="Synthetic Sender", subject="Synthetic subject",
        preview="synthetic preview", is_new=True, read_state=ReadState.UNREAD,
        safe_url="https://mail.google.com/mail/u/0/#inbox",
        source_item_id="m1", adapter="gmail", adapter_version="8b-gmail-2")]
    manifest = ReportManifest.create(
        window_start=1_700_000_000.0, window_end=1_700_000_500.0,
        status=ReportStatus.SUCCESS, new_count=1, platforms=("gmail",),
        accounts=("a",), item_identities=tuple(i.identity for i in items),
        summary="Synthetic report summary", report_id="report_8l1")
    # a real 1x1 PNG so the authenticated blob renders as a decodable image
    import base64
    data = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
        "+A8AAQUBAScY42YAAAAASUVORK5CYII=")
    asset = EvidenceAssetRecord.create(
        report_id="report_8l1", item_identity=items[0].identity,
        mime="image/png", byte_count=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        redaction_status=EvidenceStatus.AVAILABLE)
    store.render(manifest, items, [asset], asset_bytes={asset.asset_id: data})

    hostile = {
        "integration_id": '<img src=x onerror="window.__xss=1">',
        "origin": "https://example.com",
        "url": "https://example.com/page",
        "display_name": 'Hostile <script>alert(1)</script>',
        "status": "READY",
        "enabled": False,
        "requested_capabilities": [], "requested_scopes": [], "grant_ids": [],
        "sensitivity": "personal", "approval_policy": "auto",
        "auth_required": False,
        "manifest_link": "https://example.com/manifest",
        "created_at": 1_700_000_000.0, "updated_at": 1_700_000_000.0,
    }
    (state / "websites.json").write_text(json.dumps({
        "schema": 1, "integrations": {"hostile_ws": hostile},
        "removed": {}, "audit": []}, indent=2))
    (state / "permissions.json").write_text(json.dumps({
        "mode": "restricted", "mode_scopes": [], "grants": [], "audit": [],
        # skill_id is backend-validated (hostile ids are rejected by the store),
        # so a VALID skill is seeded here; hostile skill_id RENDERING is tested
        # separately by driving wsPopulateSkills directly in the browser test.
        "skills": {"render-skill": {
            "skill_id": "render-skill",
            "manifest": {
                "skill_id": "render-skill", "version": "1.0.0",
                "name": "Render skill", "source": "https://example.com/skill",
                "revision": "abc123", "license": "MIT",
                "content_hash": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
                "capabilities": ["read.reports"], "scopes": ["https://example.com"],
                "tier": "read_only", "sensitivity": "personal",
                "approval_policy": "auto", "timeout_s": 30,
                "resource_limits": {}, "audit_events": ["skill.used"],
                "compatibility": "linux", "rollback": "disabled",
            },
            "status": "active", "installed_at": 0.0,
        }}}, indent=2))
    (state / "profiles.json").write_text(json.dumps({"profiles": [{
        "id": '<img src=x onerror="window.__xss=3">', "kind": "brain",
        "source": "https://example.com/s", "revision": "r1",
        "license": "MIT", "hardware": "any",
    }]}, indent=2))


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
    ctx = browser.new_context(
        viewport=viewport or {"width": 1280, "height": 800},
        accept_downloads=True)
    page = ctx.new_page()
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
        # The Talk route replaces .page-nav with the .command-rail by
        # design (workspace-shell); the current-section indicator there
        # is .rail-action-active. Same assertion intent: the operator can
        # always tell where they are, at touch size.
        for sel in [".rail-action-active", "#ptt", "#textsend"]:
            box = page.locator(sel).first.bounding_box()
            assert box is not None, f"{sel} not visible at 320px"
            assert box["height"] >= 44, f"{sel} too small at 320px"
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


def test_settings_hostile_values_never_execute(browser, ui_server):
    """Packet 8L XSS repair: hostile backend values (integration_id, origin,
    display_name, manifest_link, status, skill_id, profile_id) are rendered as
    literal text through DOM/textContent/safe-HTML — never as live HTML or
    inline handlers."""
    page, errors = _page(browser, ui_server, "/settings")
    try:
        page.wait_for_function(
            "(o) => { const el = document.getElementById('health'); "
            "return el && el.textContent.indexOf(o.needle) !== -1; }",
            arg={"needle": "bridge up"}, timeout=8000)
        # hostile profile_id must appear as literal option text in brain-sel
        page.wait_for_function(
            "() => { const s = document.getElementById('brain-sel'); "
            "return s && s.options.length > 0; }", timeout=8000)
        brain_text = page.evaluate(
            "[...document.getElementById('brain-sel').options].map(o => o.textContent).join('|')")
        assert "<img" in brain_text and "onerror" in brain_text, \
            "hostile profile_id must render as literal text in the select"
        # drive the skill/action option builders directly with hostile values
        page.evaluate(
            "(p) => { "
            " window.fillOptions(document.getElementById('act-sel'), "
            "   [{value: p.action_id, label: p.action_label}]);"
            " window.wsPopulateSkills([{skill_id: p.skill_id,"
            "   status:'active', version: p.version}]); }",
            {"action_id": '<img src=x onerror=window.__xss=4>',
             "action_label": 'hostile <script>action</script> "quotes" \\ backslash',
             "skill_id": '<img src=x onerror=window.__xss=5>',
             "version": '<script>1</script>'})
        act_text = page.evaluate(
            "[...document.getElementById('act-sel').options].map(o => o.value + '::' + o.textContent).join('|')")
        assert "onerror=window.__xss=4" in act_text, "hostile action_id must stay literal text"
        assert '"quotes"' in act_text and "backslash" in act_text, \
            "hostile action label quotes/backslashes must stay literal text"
        skill_text = page.evaluate(
            "[...document.getElementById('ws-skill-beginner').options].map(o => o.textContent).join('|')")
        assert "onerror=window.__xss=5" in skill_text, \
            "hostile skill_id must stay literal text in the options"
        # render the seeded hostile website integration via the real backend
        page.locator("button:has-text('Refresh list')").first.click()
        page.wait_for_function(
            "() => { const el = document.getElementById('ws-list'); "
            "return el && el.children.length > 0; }", timeout=8000)
        ws_text = page.evaluate(
            "document.getElementById('ws-list').innerText")
        # the displayed hostile values (display_name/status/origin/manifest_link)
        # render as literal text, never as live markup
        assert "Hostile" in ws_text and "<script>" in ws_text
        assert "onerror" not in ws_text
        # no hostile payload may have executed
        assert page.evaluate("window.__xss") is None, "hostile JS executed!"
        # no live <img>/<script> may exist anywhere from the hostile data
        assert page.evaluate(
            "document.querySelectorAll('#ws-list img, #ws-list script, "
            "#brain-sel img, #ws-skill-beginner img, #act-sel img').length") == 0
        assert not errors, errors
    finally:
        page.close()


def test_report_operations_authenticated(browser, ui_server):
    """Packet 8L report-operations repair: Open report / Export / Print /
    screenshot all fetch through the authenticated binary helper (header token),
    never as plain <a href>/<img src>/window.open URLs that would 401."""
    page, errors = _page(browser, ui_server, "/")
    unauth = []
    page.on("response", lambda r: unauth.append(r.url)
            if r.status == 401 and "/api/reports/" in r.url else None)
    try:
        _wait_status(page, "bridge up")
        page.click("#reportslink")
        page.wait_for_function(
            "() => { const el = document.getElementById('reports-list'); "
            "return el && el.textContent.indexOf('report_8l1') !== -1; }",
            timeout=8000)
        # Export -> authenticated blob download
        with page.expect_download() as dl:
            page.locator("#reports-list button[title='Download bundle (.zip)']").first.click()
        download = dl.value
        assert download.suggested_filename == "report_8l1.zip"
        assert download.path() is not None
        assert download.path().stat().st_size > 0
        # Open report detail -> authenticated blob asset thumbnail
        page.locator("#reports-list button[title='Open report detail']").first.click()
        page.wait_for_function(
            "() => { const imgs = document.querySelectorAll('#reportspanel img'); "
            "return imgs.length > 0; }", timeout=8000)
        page.wait_for_function(
            "() => { const imgs = document.querySelectorAll('#reportspanel img'); "
            "return imgs.length > 0 && imgs[0].naturalWidth > 0; }", timeout=8000)
        assert page.evaluate(
            "document.querySelector('#reportspanel img').src.indexOf('blob:') === 0"), \
            "screenshot asset must be an authenticated blob object URL"
        # Print -> authenticated blob open in a new tab
        with page.expect_popup() as popup_info:
            page.locator("#reportspanel button[title='Open print view']").first.click()
        popup = popup_info.value
        popup.wait_for_load_state("domcontentloaded")
        assert popup.url.startswith("blob:"), f"print view must be a blob URL, got {popup.url[:60]}"
        popup.close()
        assert not unauth, f"report operations hit 401: {unauth}"
        assert page.locator("#pair-hint").count() == 0
        assert not errors, errors
    finally:
        page.close()


def test_api_origin_attacker_rejected(browser, ui_server):
    """Packet 8L API-origin repair: ?api=https://attacker.example must be
    ignored (never persisted), no X-Api-Token request may be sent there, and
    the page keeps working against the same-origin bridge."""
    page, errors = _page(browser, ui_server, "/?api=https://attacker.example")
    attacker = []
    page.on("request", lambda r: attacker.append(r.url)
            if r.url.startswith("https://attacker.example") else None)
    try:
        _wait_status(page, "bridge up")
        assert not attacker, f"requests sent to attacker origin: {attacker}"
        assert page.evaluate("localStorage.getItem('vdb_api')") is None or \
            page.evaluate("localStorage.getItem('vdb_api')") == "", \
            "attacker ?api= must not be persisted to vdb_api"
        # token is still present and header-only
        assert page.evaluate("localStorage.getItem('vdb_token')") == "test-token"
        assert not errors, errors
    finally:
        page.close()


def test_pairing_success_saves_token(browser, ui_server):
    """Packet 8L pairing repair: a valid candidate token is saved only after a
    successful authenticated /api/status."""
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    try:
        page.goto(ui_server + "/", wait_until="domcontentloaded")
        page.click("#gearlink")
        page.fill("#set-token", "test-token")
        page.click("#set-save")
        page.wait_for_function(
            "() => localStorage.getItem('vdb_token') === 'test-token'",
            timeout=8000)
    finally:
        page.close()


def test_pairing_invalid_network_preserve_previous_token(browser, ui_server):
    """Packet 8L pairing repair: a 401 candidate and an unreachable bridge must
    preserve the previous working token; a later valid retry succeeds."""
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    dialogs = []
    page.on("dialog", lambda d: (dialogs.append(d.message), d.accept()))
    try:
        page.add_init_script(
            "localStorage.setItem('vdb_token','prev-valid');")
        page.goto(ui_server + "/", wait_until="domcontentloaded")
        page.click("#gearlink")
        # 401: wrong token must NOT overwrite the stored token
        page.fill("#set-token", "definitely-wrong")
        page.click("#set-save")
        page.wait_for_function(
            "() => { const el = document.getElementById('set-status'); "
            "return el && el.textContent.indexOf('rejected') !== -1; }",
            timeout=8000)
        assert page.evaluate("localStorage.getItem('vdb_token')") == "prev-valid"
        # network failure: loopback http to a dead port, confirm() accepted
        page.fill("#set-api", "http://127.0.0.1:1")
        page.click("#set-save")
        page.wait_for_function(
            "() => { const el = document.getElementById('set-status'); "
            "return el && el.textContent.indexOf('unavailable') !== -1; }",
            timeout=8000)
        assert dialogs, "remote-origin pairing must ask for confirmation"
        assert page.evaluate("localStorage.getItem('vdb_token')") == "prev-valid"
        assert page.evaluate("localStorage.getItem('vdb_api')") in (None, ""), \
            "unreachable bridge must not persist a new API origin"
        # retry with the correct token and same-origin -> saved
        page.fill("#set-api", "")
        page.fill("#set-token", "test-token")
        page.click("#set-save")
        page.wait_for_function(
            "() => localStorage.getItem('vdb_token') === 'test-token'",
            timeout=8000)
    finally:
        page.close()

def test_reports_page_open_html_authenticated(browser, ui_server):
    """Packet 8L standalone-Reports repair: clicking "Open report" fetches the
    HTML through the authenticated binary helper — the request must carry the
    X-Api-Token header, return non-401, and open via a blob object URL (the
    token is never placed in the URL)."""
    page, errors = _page(browser, ui_server, "/reports")
    requests = []
    page.on("request", lambda r: requests.append(r)
            if "/api/reports/" in r.url and "/html" in r.url else None)
    try:
        _wait_status(page, "live", "conn")
        page.wait_for_function(
            "() => { const f = document.getElementById('feed'); "
            "return f && f.textContent.indexOf('report_8l1') !== -1; }",
            timeout=8000)
        with page.expect_popup() as popup_info:
            page.locator("#feed button:has-text('Open report (new tab)')").first.click()
        popup = popup_info.value
        popup.wait_for_load_state("domcontentloaded")
        assert popup.url.startswith("blob:"), \
            f"report HTML must open as a blob URL, got {popup.url[:60]}"
        popup.close()
        assert requests, "no authenticated /html request was issued"
        for req in requests:
            assert req.headers.get("x-api-token") == "test-token", \
                "report HTML request must carry the X-Api-Token header"
            assert "token=" not in req.url, "token must never appear in a URL"
        assert page.locator("#pair-hint").count() == 0
        assert not errors, errors
    finally:
        page.close()


def test_reports_page_typed_401_when_unpaired(browser, ui_server):
    """Packet 8L: a missing/wrong token must yield a typed 401 pairing message
    (no popup, no crash), proving the shared helper's authRequired path."""
    page, errors = _page(browser, ui_server, "/reports", authed=False)
    try:
        page.wait_for_selector("#pair-hint", timeout=8000)
        # drive the authenticated open directly with no token
        page.evaluate("window.openReportHtml('report_8l1')")
        page.wait_for_function(
            "() => { const el = document.getElementById('msg'); "
            "return el && el.textContent.indexOf('Not paired') !== -1; }",
            timeout=8000)
        assert page.locator("#pair-hint").count() >= 1
        assert not errors, errors
    finally:
        page.close()


def test_reports_hostile_values_safe_and_ids_encoded(browser, ui_server):
    """Packet 8L: hostile backend-rendered report values (report_id, status,
    summary) stay literal text and never execute; hostile report ids are
    percent-encoded in the authenticated request URL."""
    page, errors = _page(browser, ui_server, "/reports")
    seen = []
    page.on("request", lambda r: seen.append(r.url)
            if "/api/reports/" in r.url and "/html" in r.url else None)
    try:
        _wait_status(page, "live", "conn")
        hostile_id = '<img src=x onerror="window.__xss=9">\\"backslash"'
        page.evaluate(
            "(p) => { document.getElementById('feed')"
            ".appendChild(window.reportCard(p)); }",
            {"report_id": hostile_id,
             "status": '<script>window.__xss=10</script>',
             "summary": 'Hostile <script>alert(1)</script> "quotes" \\ backslash',
             "created_at": 1700000000.0, "new_count": 1,
             "platforms": ["gmail"], "assets_available": 0, "assets_total": 0})
        text = page.evaluate("document.getElementById('feed').innerText")
        assert "Hostile" in text and "<script>" in text and '"quotes"' in text, \
            "hostile report values must stay literal text"
        assert page.evaluate("window.__xss") is None, "hostile JS executed!"
        assert page.evaluate(
            "document.querySelectorAll('#feed img, #feed script').length") == 0
        # hostile report id must be percent-encoded, never raw, in the URL
        page.evaluate("(id) => window.openReportHtml(id)", hostile_id)
        page.wait_for_function(
            "() => { const el = document.getElementById('msg'); "
            "return el && el.textContent.length > 0; }", timeout=8000)
        assert seen, "no authenticated /html request issued for hostile id"
        url = seen[-1]
        assert "<" not in url and ">" not in url and '"' not in url, \
            f"hostile id must be percent-encoded in the URL: {url}"
        assert "%3C" in url and "%3E" in url, \
            f"hostile id must be percent-encoded in the URL: {url}"
        assert "token=" not in url, f"token must never appear in a URL: {url}"
        assert page.evaluate("window.__xss") is None
        assert not errors, errors
    finally:
        page.close()
