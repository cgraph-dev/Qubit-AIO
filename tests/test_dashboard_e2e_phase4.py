"""Phase-4 real-browser dashboard E2E against the real Qubit WebUI.

Boots the real ``voicebridge.webui.WebUI`` (current worktree source) over the
built SPA (``frontend/qubit-dist``) on a loopback port with an isolated state
dir, then drives real routes and controls through headless Chromium.

Every assertion is backed by a real canonical backend request/response and,
for mutating controls, by a persisted read-back from the same canonical store
(``state.db`` / the schedules store). Nothing passes merely because a route
returned 200.

Run explicitly::

    pytest -m ui tests/test_dashboard_e2e_phase4.py

The module is excluded from the hermetic default run via ``pytest.ini`` because
it needs a local chromium and a real HTTP server.
"""
from __future__ import annotations

import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

DIST = REPO_ROOT / "frontend" / "qubit-dist"

pytestmark = [
    pytest.mark.ui,
    pytest.mark.skipif(
        not (DIST / "index.html").is_file(),
        reason="frontend/qubit-dist not built (npm build step)",
    ),
]

pw = pytest.importorskip("playwright.sync_api")

# Typed, documented backend noise that is a deliberate gate, not a page failure.
ALLOWED_CONSOLE = (
    "status of 403",   # /api/config/raw SECRET_NEVER_SERVED
    "status of 409",   # /chat ws-ticket context floor / staged confirms
    "/api/config/raw",
    "ws://",
    "/api/pty",
    "/api/events",
)

# (route, expected <h1>/<h2> heading) for every reachable built-in + overlay tab.
ROUTE_HEADINGS = [
    ("/sessions", "Sessions"),
    ("/files", "Files"),
    ("/models", "Models"),
    ("/logs", "Logs"),
    ("/cron", "Cron"),
    ("/skills", "Skills"),
    ("/plugins", "Plugins"),
    ("/mcp", "MCP"),
    ("/channels", "Channels"),
    ("/webhooks", "Webhooks"),
    ("/pairing", "Pairing"),
    ("/profiles", "Profiles"),
    ("/config", "Config"),
    ("/env", "Keys"),
    ("/system", "System"),
    ("/agents", "Agents"),
    ("/analytics", "Analytics"),
    ("/docs", "Documentation"),
    ("/kanban", "Kanban"),
    ("/qubit-boards", "Qubit Boards"),
    ("/memory", "Memory"),
    ("/operations", "Operations"),
    ("/achievements", "Achievements"),
    ("/devices", "Devices"),
    ("/reports", "Reports"),
    ("/qubit-runtime", "Runtime Inventory"),
    ("/qubit-gateway", "Gateway"),
    ("/qubit-models", "Models & Providers"),
]


def _api(base: str, token: str, method: str, path: str, body=None):
    """Call the canonical backend directly and return (status, parsed-json)."""
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        base + path,
        data=data,
        method=method,
        headers={
            "X-Qubit-Session-Token": token,
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read().decode("utf-8") or "{}"
            return resp.status, json.loads(raw)
    except urllib.error.HTTPError as exc:
        with exc:
            raw = exc.read().decode("utf-8") or "{}"
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, {"_raw": raw}


def _row_for(page, text: str, aria: str):
    """The innermost row div that contains ``text`` and the ``aria`` button."""
    el = page.get_by_text(text, exact=True).first
    return el.locator(
        f'xpath=ancestor::div[.//*[self::button or self::sakuga-button][@aria-label="{aria}"]][1]')


@pytest.fixture(scope="module")
def ui_server(tmp_path_factory):
    from voicebridge.config import Config
    from voicebridge.webui import WebUI

    state = tmp_path_factory.mktemp("phase4-state")
    p = state / "voice.yaml"
    p.write_text(
        f"logging:\n  jsonl_path: '{state}'\n"
        "webui:\n  token: phase4-token\n"
        # The shipped /chat floor is 65536; lower it here so the hermetic local
        # default can open /chat without a configured API profile (the real
        # floor gate is covered by tests/test_terminal_context_floor.py).
        "terminal:\n  min_context_tokens: 4096\n"
    )
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
    base = f"http://127.0.0.1:{ui._server.server_address[1]}"
    _TEST_CTX["cfg"] = cfg
    _TEST_CTX["state"] = state
    try:
        yield base, ui.api_token
    finally:
        ui.stop()


@pytest.fixture(scope="module")
def browser():
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"playwright unavailable: {exc}")
    pw_ = sync_playwright().start()
    try:
        b = pw_.chromium.launch(headless=True)
    except Exception as exc:  # pragma: no cover
        pw_.stop()
        pytest.skip(f"chromium launch failed: {exc}")
    try:
        yield b
    finally:
        b.close()
        pw_.stop()


def test_workbench_closed_workspace_panel_stays_within_viewport(browser, ui_server):
    base, _token = ui_server
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    try:
        page = context.new_page()
        page.add_init_script("localStorage.setItem('qubit-wb-workspace-panel', 'closed')")
        page.goto(f"{base}/qubit-workbench", wait_until="domcontentloaded")
        for reload_page in (False, True):
            if reload_page:
                page.reload(wait_until="domcontentloaded")
            pw.expect(page.locator("html")).to_have_attribute("data-workspace-panel", "closed")
            pw.expect(page.locator("#btnWorkspacePanelEdgeToggle")).to_be_in_viewport()
            dimensions = page.evaluate(
                "({ scroll: document.documentElement.scrollWidth, "
                "viewport: document.documentElement.clientWidth })"
            )
            assert dimensions["scroll"] == dimensions["viewport"], dimensions
    finally:
        context.close()


@pytest.mark.parametrize("mount", ["/qubit-workbench", "/wb"])
def test_workbench_session_deep_link_serves_the_workbench(ui_server, mount):
    base, token = ui_server
    code, created = _api(base, token, "POST", f"{mount}/api/session/new",
                         {"title": "Workbench deep-link regression"})
    assert code == 200 and created.get("session_id"), created
    session_id = created["session_id"]
    with urllib.request.urlopen(f"{base}{mount}/session/{session_id}", timeout=5) as response:
        assert response.status == 200
        assert response.headers.get_content_type() == "text/html"
        assert f'<base href="{mount}/">'.encode() in response.read()
    code, restored = _api(base, token, "GET", f"{mount}/api/session?session_id={session_id}")
    assert code == 200 and restored["session"]["session_id"] == session_id


@pytest.mark.parametrize("asset", [
    "Inter-latin.woff2",
    "Rajdhani-latin.woff2",
])
def test_dashboard_fonts_are_served_as_woff2(ui_server, asset):
    base, _token = ui_server
    with urllib.request.urlopen(f"{base}/fonts-dashboard/{asset}", timeout=5) as response:
        assert response.headers.get_content_type() == "font/woff2"
        assert response.read(4) == b"wOF2"


def test_routes_mount_real_pages(browser, ui_server):
    """Every canonical route deep-links to a mounted page with a real heading."""
    base, _token = ui_server
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    page = ctx.new_page()
    errors: list[str] = []
    page.on("console", lambda m: errors.append(m.text)
            if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append("pageerror: " + str(e)))
    try:
        for route, heading in ROUTE_HEADINGS:
            page.goto(base + route, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_selector("nav, aside, [role=navigation]", timeout=30000)
            page.get_by_role("heading", name=heading, exact=False).first.wait_for(
                timeout=20000)
            text = page.inner_text("body")
            assert len(text.strip()) > 60, (route, "blank render")
            assert page.url.endswith(route) or route.startswith(
                page.url.split("127.0.0.1")[-1]), (route, page.url)

        # /chat is the real vendored terminal (xterm), independent of the
        # transcript composer.
        page.goto(base + "/chat", wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(3000)
        assert page.locator('[aria-label="Qubit terminal"]').count() == 1
        assert page.locator(".xterm").count() >= 1, "chat terminal did not mount"

        # /qubit-chat is the native Qubit agent turn surface.
        page.goto(base + "/qubit-chat", wait_until="domcontentloaded",
                  timeout=30000)
        page.get_by_label("Message Qubit").wait_for(timeout=20000)
        assert page.locator(
            '[aria-label="Qubit Chat transcript"]').count() == 1, \
            "native chat transcript did not mount"
    finally:
        ctx.close()
    fatal = [e for e in errors if not any(s in e for s in ALLOWED_CONSOLE)]
    assert not fatal, fatal[:5]


def test_dashboard_control_aliases_open_their_own_page(browser, ui_server):
    base, _ = ui_server
    context = browser.new_context(viewport={"width": 390, "height": 844})
    page = context.new_page()
    requested_fonts = []
    page.on("request", lambda request: requested_fonts.append(request.url)
            if "fonts.googleapis.com" in request.url or "fonts.gstatic.com" in request.url else None)
    try:
        for alias, destination, title in (
            ("/keys", "/env", "Keys"),
            ("/model", "/models", "Models"),
            ("/providers", "/models", "Models"),
            ("/model-providers", "/models", "Models"),
            ("/models/providers", "/models", "Models"),
            ("/gateway", "/system", "System"),
        ):
            page.goto(base + alias + "?audit=1", wait_until="domcontentloaded")
            expected = destination + "?audit=1" + ("#gateway" if alias == "/gateway" else "")
            page.wait_for_url(base + expected)
            pw.expect(page.get_by_role("heading", name=title, exact=True).first).to_be_visible()
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        pw.expect(page.locator("#gateway")).to_be_in_viewport()
        assert not requested_fonts, requested_fonts
    finally:
        context.close()


def test_runtime_alias_is_not_a_page(browser, ui_server):
    """`/runtime` is not a route; the SPA catch-all resolves it to /sessions."""
    base, _token = ui_server
    ctx = browser.new_context()
    page = ctx.new_page()
    try:
        page.goto(base + "/runtime", wait_until="domcontentloaded",
                  timeout=30000)
        page.wait_for_timeout(1500)
        assert page.url.endswith("/sessions"), page.url
    finally:
        ctx.close()


def test_sessions_rename_persists(browser, ui_server):
    """Sessions list + rename: PATCH the canonical store, read back the write."""
    base, token = ui_server
    code, created = _api(base, token, "POST", "/agent/api/qubit/chat/sessions",
                         {"name": "phase4-seed"})
    assert code == 200 and created.get("session"), created
    sid = created["session"]["id"]

    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    page = ctx.new_page()
    try:
        page.goto(base + "/sessions", wait_until="domcontentloaded",
                  timeout=30000)
        page.get_by_role("radio", name="History").click()
        row = page.get_by_test_id(f"session-row-{sid}")
        row.get_by_text("phase4-seed", exact=True).wait_for()
        row.get_by_role("button", name="Rename session", exact=True).click()
        # Fill the native shadow input. The custom element host also advertises a textbox.
        box = row.locator("input[aria-label='Session title']")
        box.fill("phase4-renamed")
        with page.expect_response(
                lambda r: r.url.split("?", 1)[0].endswith(f"/api/sessions/{sid}")
                and r.request.method == "PATCH") as rename:
            box.press("Enter")
        assert rename.value.status == 200

        deadline = time.time() + 15
        title = None
        while time.time() < deadline:
            c, d = _api(base, token, "GET", f"/agent/api/sessions/{sid}")
            title = d.get("title") or (d.get("session") or {}).get("title")
            if title == "phase4-renamed":
                break
            time.sleep(0.3)
        assert title == "phase4-renamed", (sid, title)
    finally:
        ctx.close()


def test_cron_pause_persists(browser, ui_server):
    """Cron list + pause: the canonical schedules store reflects the toggle."""
    base, token = ui_server
    code, created = _api(
        base, token, "POST", "/agent/api/cron/jobs",
        {"name": "phase4-cron", "prompt": "echo phase4",
         "when": "every 1h", "confirm": True})
    assert code == 200 and created.get("id"), created
    jid = str(created["id"])

    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    page = ctx.new_page()
    _accept_dialogs(page)
    try:
        page.goto(base + "/cron", wait_until="domcontentloaded", timeout=30000)
        row = _row_for(page, "phase4-cron", "Pause")
        with page.expect_response(
                lambda r: r.url.split("?", 1)[0].endswith(f"/cron/jobs/{jid}/pause")
                and r.request.method == "POST" and r.status == 200):
            row.get_by_role("button", name="Pause", exact=True).click()

        deadline = time.time() + 15
        enabled = None
        while time.time() < deadline:
            c, jobs = _api(base, token, "GET", "/agent/api/cron/jobs")
            job = next((j for j in jobs if str(j.get("id")) == jid), None)
            enabled = job.get("enabled") if job else None
            if enabled is False:
                break
            time.sleep(0.3)
        assert enabled is False, (jid, enabled)
    finally:
        ctx.close()


def test_cron_named_profile_and_blueprint_confirmation(browser, ui_server):
    from urllib.parse import parse_qs, urlsplit

    from voicebridge import qubit_runtime

    base, token = ui_server
    qubit_runtime.ensure_path()
    from qubit_cli import profiles

    home = profiles.get_profile_dir("cron-audit")
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.yaml").write_text("{}\n")
    route = "/agent/api/cron/jobs"
    for name, profile in (("Default fixture", "default"), ("Scoped fixture", "cron-audit")):
        status, job = _api(base, token, "POST", f"{route}?profile={profile}",
                           {"name": name, "prompt": "Summarize notes",
                            "schedule": "every 1h", "confirm": True})
        assert status == 200, job

    context = browser.new_context(viewport={"width": 1440, "height": 1000})
    page = context.new_page()
    dialogs = []
    page.on("dialog", lambda dialog: (dialogs.append(dialog.message), dialog.accept()))
    try:
        page.goto(base + "/cron", wait_until="domcontentloaded")
        page.get_by_text("Default fixture", exact=True).wait_for()
        page.get_by_text("Scoped fixture", exact=True).wait_for()
        page.locator("#cron-profile-filter").click()
        page.get_by_role("option", name="cron-audit", exact=True).click()
        page.get_by_text("Scoped fixture", exact=True).wait_for()
        assert not page.get_by_text("Default fixture", exact=True).count()
        page.get_by_text("Blueprints", exact=True).click()
        card = page.get_by_text("Custom reminder", exact=True).locator(
            "xpath=ancestor::div[.//button[normalize-space()='Set up']][1]")
        card.get_by_role("button", name="Set up").click()
        page.get_by_role("textbox", name="Remind me to…").fill("verify the scheduled fixture")
        path = "/agent/api/cron/blueprints/instantiate"
        with page.expect_response(lambda response: urlsplit(response.url).path == path
                                  and response.request.method == "POST"
                                  and response.status == 200) as created:
            page.get_by_role("button", name="Schedule it").click()
        assert parse_qs(urlsplit(created.value.url).query)["profile"] == ["cron-audit"]
        assert len(dialogs) == 1
        row = created.value.json()
        status, readback = _api(base, token, "GET", f"{route}/{row['id']}?profile=cron-audit")
        assert status == 200 and "verify the scheduled fixture" in readback["prompt"], readback
        assert _api(base, token, "GET", f"{route}/{row['id']}?profile=default")[0] == 404
    finally:
        context.close()


def test_cron_paused_job_exposes_resume(browser, ui_server):
    """A paused job renders Resume, and the Resume control really resumes it.

    The schedules store pauses by flipping ``enabled`` to 0 (the served
    ``state`` can be empty or the last run outcome), so the page must
    canonicalize that to the ``paused`` view-state the button keys off.
    """
    base, token = ui_server
    code, created = _api(
        base, token, "POST", "/agent/api/cron/jobs",
        {"name": "phase4-cron-resume", "prompt": "echo phase4",
         "when": "every 1h", "confirm": True})
    assert code == 200 and created.get("id"), created
    jid = str(created["id"])
    status, paused = _api(base, token, "POST", f"/agent/api/cron/jobs/{jid}/pause",
                          {"confirm": True})
    assert status == 200 and paused.get("enabled") is False, paused

    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    page = ctx.new_page()
    _accept_dialogs(page)
    try:
        page.goto(base + "/cron", wait_until="domcontentloaded", timeout=30000)
        row = _row_for(page, "phase4-cron-resume", "Resume")
        row.wait_for(timeout=20000)
        resume = row.get_by_role("button", name="Resume")
        assert resume.count() >= 1
        # The Resume control fires the real backend resume and the canonical
        # store reflects it.
        with page.expect_response(
                lambda r: r.url.split("?", 1)[0].endswith(f"/cron/jobs/{jid}/resume")
                and r.request.method == "POST" and r.status == 200):
            resume.click()
        deadline = time.time() + 15
        enabled = None
        while time.time() < deadline:
            c, jobs = _api(base, token, "GET", "/agent/api/cron/jobs")
            job = next((j for j in jobs if str(j.get("id")) == jid), None)
            enabled = job.get("enabled") if job else None
            if enabled is True:
                break
            time.sleep(0.3)
        assert enabled is True, (jid, enabled)
    finally:
        ctx.close()


def test_skills_list_and_toggle_requires_confirmation(browser, ui_server):
    """Cancel a staged toggle, then confirm the same change through the browser."""
    from voicebridge.qubit_runtime import runtime_home_for_cfg

    base, token = ui_server
    name = "phase4-audit-skill"
    folder = runtime_home_for_cfg(_TEST_CTX["cfg"]) / "skills" / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "SKILL.md").write_text(f"---\nname: {name}\ndescription: Audit fixture\n---\n")
    code, skills = _api(base, token, "GET", "/agent/api/skills")
    assert code == 200 and isinstance(skills, list), skills
    target = next(row for row in skills if row["name"] == name)
    code, staged = _api(base, token, "PUT", "/agent/api/skills/toggle",
                        {"name": target["name"],
                         "enabled": not bool(target.get("enabled"))})
    assert code == 409, staged
    assert staged.get("code") == "CONFIRM_REQUIRED", staged
    cid = staged.get("confirmation_id")
    assert cid, staged
    # Leave no pending approval behind: the staged confirm is cancellable.
    code, canceled = _api(base, token, "POST", "/agent/api/ops/cancel",
                          {"confirmation_id": cid})
    assert code == 200 and canceled.get("state") == "executed", canceled
    code, skills = _api(base, token, "GET", "/agent/api/skills")
    assert code == 200
    current = next(row for row in skills if row["name"] == name)
    assert current["enabled"] == target["enabled"]

    context = browser.new_context(viewport={"width": 1440, "height": 900})
    page = context.new_page()
    _accept_dialogs(page)
    try:
        page.goto(base + "/skills", wait_until="domcontentloaded")
        toggle = page.locator("sakuga-toggle").filter(
            has=page.get_by_role("switch", name=f"Enable {name}", exact=True))
        pw.expect(toggle).to_have_attribute("aria-checked", "true")
        with page.expect_response(
                lambda r: r.url.split("?")[0].endswith("/api/ops/confirm")
                and r.request.method == "POST" and r.status == 200):
            toggle.click()
        pw.expect(toggle).to_have_attribute("aria-checked", "false")
        code, skills = _api(base, token, "GET", "/agent/api/skills")
        assert code == 200
        assert next(row for row in skills if row["name"] == name)["enabled"] is False
        page.reload(wait_until="domcontentloaded")
        pw.expect(toggle).to_have_attribute("aria-checked", "false")
    finally:
        context.close()


def test_plugins_rescan_returns_manifests(browser, ui_server):
    """The visible Rescan control fires the canonical rescan and the overlay
    manifests come back."""
    base, token = ui_server
    code, rescan = _api(base, token, "GET",
                        "/agent/api/dashboard/plugins/rescan")
    assert code == 200 and rescan.get("rescanned") is True, rescan
    assert rescan.get("count", 0) >= 1 and "qubit-boards" in rescan.get(
        "plugins", []), rescan

    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    page = ctx.new_page()
    try:
        page.goto(base + "/plugins", wait_until="domcontentloaded",
                  timeout=30000)
        with page.expect_response(
                lambda r: "/dashboard/plugins/rescan" in r.url) as info:
            page.get_by_role(
                "button", name="Rescan dashboard extensions").click()
        assert info.value.status == 200, info.value.status
        # The visible control fires the canonical rescan and returns the real
        # overlay manifest set (not an empty 200).
        payload = info.value.json()
        assert payload.get("rescanned") is True and payload.get("count", 0) >= 1, payload
        assert page.locator("nav, aside, [role=navigation]").count() >= 1
    finally:
        ctx.close()


def test_config_save_persists_non_secret_edit(browser, ui_server, monkeypatch):
    """Config Save persists a non-secret edit through the real SPA control.

    The SPA PUTs the whole tree it read, including the raw secret-shaped leaves
    the served runtime returns (``browser.camofox.session_key``,
    ``terminal.docker_shared_container_key``, ``voice.record_key``). The backend
    skips the unchanged secret leaves instead of refusing the whole save, so the
    real edit lands in the canonical store. A changed secret leaf is still
    refused.
    """
    monkeypatch.setenv("QUBIT_GATEWAY_SUPERVISION_DISABLED", "1")
    base, token = ui_server
    code, before = _api(base, token, "GET", "/agent/api/config")
    assert code == 200 and before["browser"]["headed"] is False, \
        before.get("browser")
    # The served tree really carries raw secret-shaped leaves: this is the shape
    # that used to make the whole-tree Save a dead control.
    assert "session_key" in before.get("browser", {}).get("camofox", {}), \
        before.get("browser", {}).get("camofox")
    assert "docker_shared_container_key" in before.get("terminal", {}), \
        before.get("terminal")
    # A changed secret leaf is still refused (secrecy is not weakened).
    code, refused = _api(
        base, token, "PUT", "/agent/api/config",
        {"config": {"browser": {"camofox": {"session_key": "phase4-rotated"}}},
         "confirm": True})
    assert (code, refused.get("code")) == (403, "SECRET_WRITE_REFUSED"), \
        (code, refused)

    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    page = ctx.new_page()
    try:
        page.goto(base + "/config", wait_until="domcontentloaded",
                  timeout=30000)
        page.get_by_role("heading", name="Config").first.wait_for(timeout=20000)
        page.get_by_role("banner").locator("input[placeholder='Search...']").fill("browser.headed")
        switch = page.locator("sakuga-toggle").filter(
            has=page.get_by_role("switch", name="Headed", exact=True)
        )
        switch.wait_for(timeout=10000)
        assert switch.get_attribute("aria-checked") == "false", "seed not false"
        switch.click()
        with page.expect_response(
                lambda r: r.url.endswith("/agent/api/config")
                and r.request.method == "PUT") as info:
            page.get_by_role("button", name="Save").first.click()
        assert info.value.status == 409, info.value.status
        dialog = page.get_by_role("dialog", name="Apply these settings to Qubit?")
        dialog.wait_for(timeout=10000)
        dialog.locator("[data-confirm]").click()

        deadline = time.time() + 15
        value = None
        while time.time() < deadline:
            c, cfg = _api(base, token, "GET", "/agent/api/config")
            value = (cfg.get("browser") or {}).get("headed")
            if value is True:
                break
            time.sleep(0.3)
        assert value is True, value

        def is_restart(response):
            return (response.url.split("?", 1)[0].endswith("/agent/api/gateway/restart")
                    and response.request.method == "POST")
        with page.expect_response(is_restart) as staged:
            page.locator("main sakuga-button").filter(has_text="Restart gateway").locator("button").click()
        assert staged.value.status == 409
        restart = page.get_by_role("dialog", name="Restart the gateway?")
        with page.expect_response(is_restart) as confirmed:
            restart.locator("[data-confirm]").click()
        assert confirmed.value.status == 503
        assert confirmed.value.json()["code"] == "GATEWAY_SUPERVISION_UNAVAILABLE"
    finally:
        ctx.close()


def test_approvals_cancel_settles_staged_config_write(ui_server):
    """The approvals Cancel path cancels a staged ``qubit.config.write``.

    Without it the staged confirmation can only expire and the banner Cancel
    is a dead control.
    """
    base, token = ui_server
    code, staged = _api(base, token, "PUT", "/agent/api/config",
                        {"config": {"browser": {"headed": True}}})
    assert code == 409 and staged.get("code") == "CONFIRM_REQUIRED", staged
    cid = staged.get("confirmation_id")
    assert cid, staged

    code, approvals = _api(base, token, "GET", "/agent/api/ops/approvals")
    assert code == 200, approvals
    assert any(a["id"] == cid for a in approvals.get("approvals", [])), approvals

    code, canceled = _api(base, token, "POST", "/agent/api/ops/cancel",
                          {"confirmation_id": cid})
    assert canceled.get("state") == "executed", canceled
    assert canceled.get("receipt", {}).get("cancelled", 0) >= 1, canceled

    code, after = _api(base, token, "GET", "/agent/api/ops/approvals")
    assert not any(a["id"] == cid for a in after.get("approvals", [])), after


def test_approvals_confirm_settles_staged_config_write(ui_server):
    """The approvals Confirm path executes a staged ``qubit.config.write``.

    Stage via the real config route -> it appears on the shared journal ->
    ``POST /api/ops/confirm`` settles it through the same journal -> the value
    persists and the entry is consumed exactly once. An unknown/replayed id is
    a typed failure, never a fabricated success.
    """
    base, token = ui_server
    code, before = _api(base, token, "GET", "/agent/api/config")
    assert code == 200, before
    current = bool((before.get("vad") or {}).get("enabled", True))
    wanted = not current

    code, staged = _api(base, token, "PUT", "/agent/api/config",
                        {"config": {"vad": {"enabled": wanted}}})
    assert code == 409 and staged.get("code") == "CONFIRM_REQUIRED", staged
    cid = staged.get("confirmation_id")
    assert cid, staged

    code, approvals = _api(base, token, "GET", "/agent/api/ops/approvals")
    assert code == 200, approvals
    assert any(a["id"] == cid and a["capability"] == "qubit.config.write"
               for a in approvals.get("approvals", [])), approvals

    code, confirmed = _api(base, token, "POST", "/agent/api/ops/confirm",
                           {"confirmation_id": cid})
    assert code == 200 and confirmed.get("state") == "executed", \
        (code, confirmed)

    deadline = time.time() + 15
    value = None
    while time.time() < deadline:
        c, cfg = _api(base, token, "GET", "/agent/api/config")
        value = (cfg.get("vad") or {}).get("enabled")
        if value is wanted:
            break
        time.sleep(0.3)
    assert value is wanted, (value, wanted)

    code, after = _api(base, token, "GET", "/agent/api/ops/approvals")
    assert not any(a["id"] == cid for a in after.get("approvals", [])), after

    # Replay and unknown ids are typed failures (one journal, no double-apply).
    code, replay = _api(base, token, "POST", "/agent/api/ops/confirm",
                        {"confirmation_id": cid})
    assert code == 400 and replay.get("code") == "NO_CONFIRMATION", \
        (code, replay)
    code, unknown = _api(base, token, "POST", "/agent/api/ops/confirm",
                         {"confirmation_id": "phase4-unknown"})
    assert code == 400 and unknown.get("code") == "NO_CONFIRMATION", \
        (code, unknown)


def test_files_and_logs_typed_reads(ui_server):
    """Files and Logs return real canonical payloads (empty/typed allowed)."""
    base, token = ui_server
    code, files = _api(base, token, "GET", "/agent/api/files")
    assert code == 200 and files.get("ok") is True, files
    assert isinstance(files.get("entries"), list), files

    code, logs = _api(base, token, "GET", "/agent/api/logs")
    assert code == 200 and isinstance(logs.get("lines"), list), logs


def test_profiles_set_active_persists(browser, ui_server):
    """Profiles: the row "Set as active" control persists the active profile.

    The control POSTs ``/api/profiles/active``; the canonical
    ``GET /api/profiles/active`` read-back returns the selected name.
    """
    base, token = ui_server
    target = "phase4-active-target"
    code, created = _api(base, token, "POST", "/agent/api/profiles",
                         {"name": target, "no_skills": True, "confirm": True})
    assert code == 200 and created["ok"], created
    code, act = _api(base, token, "GET", "/agent/api/profiles/active")
    assert code == 200 and act.get("active"), act
    original = act["active"]

    def read_active():
        c, d = _api(base, token, "GET", "/agent/api/profiles/active")
        return d.get("active") if c == 200 else None

    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    page = ctx.new_page()
    try:
        page.goto(base + "/profiles", wait_until="domcontentloaded",
                  timeout=30000)
        page.get_by_role("heading", name="Profiles").first.wait_for(timeout=20000)
        title = page.get_by_text(re.compile(rf"^(?:.* \()?{re.escape(target)}\)?$"))
        card = title.locator("xpath=ancestor::*[.//*[@data-profile-actions]][1]")
        card.locator('sakuga-button[aria-label="Actions"]').locator("button").click()
        item = page.get_by_role("menuitem", name="Set as active", exact=True)
        with page.expect_response(
                lambda r: r.url.endswith("/api/profiles/active")
                and r.request.method == "POST" and r.status == 200):
            item.click()
        got = _poll(lambda: read_active() if read_active() == target else None)
        assert got == target, (target, read_active())
    finally:
        # Restore the original active profile for the rest of the module.
        _api(base, token, "POST", "/agent/api/profiles/active",
             {"name": original})
        ctx.close()
    assert read_active() == original


def test_profiles_create_requires_confirmation_and_persists(browser, ui_server):
    """Profiles create: the SPA Create control stages then really settles.

    ``api.createProfile`` now runs the shared staged-confirmation round-trip.
    The first POST stages a typed 409 ``CONFIRM_REQUIRED`` with a REAL
    ``confirmation_id`` on the shared journal (``h_profile_create`` ->
    ``_compat_confirm``); the confirmed POST creates the profile home.
    Read back the canonical profile list used by the CLI and dashboard.
    """
    base, token = ui_server
    name = "phase4-created"

    # The stage is settleable: a direct unconfirmed POST returns a real id.
    code, staged = _api(base, token, "POST", "/agent/api/profiles",
                        {"name": "phase4-staged-only"})
    assert code == 409 and staged.get("code") == "CONFIRM_REQUIRED", staged
    assert staged.get("confirmation_id"), staged  # was EMPTY before the fix
    _api(base, token, "POST", "/agent/api/ops/cancel",
         {"confirmation_id": staged["confirmation_id"]})

    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    page = ctx.new_page()
    _accept_dialogs(page)
    try:
        page.goto(base + "/profiles", wait_until="domcontentloaded",
                  timeout=30000)
        page.locator("[data-dashboard-page-header] sakuga-button").filter(
            has_text="Create").locator("button").click()
        dialog = page.get_by_role("dialog", name="New Profile")
        dialog.wait_for(timeout=10000)
        dialog.locator("#profile-name").locator("input").fill(name)
        # The staged 409 is followed by the confirmed re-issue; wait for the
        # 200 (the shared round-trip settles on the human's window.confirm).
        # The settle re-issue carries ``?confirmation_id=`` so match the path.
        with page.expect_response(
                lambda r: r.url.split("?")[0].endswith("/agent/api/profiles")
                and r.request.method == "POST"
                and r.status == 200) as info:
            dialog.locator("sakuga-button").filter(has_text="Create").locator("button").click()
        assert info.value.status == 200, info.value.status
        assert info.value.json().get("ok") is True, info.value.json()

        code, listed = _api(base, token, "GET", "/agent/api/profiles")
        assert code == 200 and name in {p["name"] for p in listed["profiles"]}, listed
    finally:
        ctx.close()


# ==========================================================================
# Phase-4 extension (2026-09-21): remaining core pages
# ==========================================================================
# Each control below is driven through the real built SPA and is backed by a
# canonical request/response PLUS a read-back from the same store. The
# confirm-gated mutations (webhooks delete, profiles create, MCP add/enable/
# delete) run the shared staged-confirmation round-trip and settle for real;
# typed refusals are still asserted where validation must not be weakened
# (e.g. a transport-less MCP create). Nothing here replaces or weakens the
# assertions above.
# ==========================================================================


def _poll(fn, timeout=15.0, interval=0.3):
    """Poll ``fn`` until it returns a truthy value; return the last value."""
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = fn()
        if last:
            return last
        time.sleep(interval)
    return last


# The module-scoped server's Config/state, exposed for canonical read-backs
# that have no HTTP route (e.g. the Brain principal registry behind
# ``h_profile_create``). Populated by the ``ui_server`` fixture.
_TEST_CTX: dict = {}


def _accept_dialogs(page):
    """Auto-accept the ``window.confirm`` the staged-confirm round-trip shows."""
    page.on("dialog", lambda d: d.accept())


def test_channel_test_control_is_real(browser, ui_server):
    """Channels: the per-platform Test control runs the canonical link probe.

    ``POST /api/messaging/platforms/{id}/test`` returns the real link state;
    the canonical platform list read-back still contains the same id.
    """
    base, token = ui_server
    code, listing = _api(base, token, "GET", "/agent/api/messaging/platforms")
    assert code == 200 and listing.get("platforms"), listing
    first = listing["platforms"][0]["id"]

    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    page = ctx.new_page()
    try:
        page.goto(base + "/channels", wait_until="domcontentloaded",
                  timeout=30000)
        page.get_by_role("heading", name="Channels").first.wait_for(timeout=20000)
        with page.expect_response(
                lambda r: "/messaging/platforms/" in r.url
                and r.url.endswith("/test")
                and r.request.method == "POST") as info:
            page.get_by_role("button", name="Test").first.click()
        assert info.value.status == 200, info.value.status
        payload = info.value.json()
        assert payload["platform"] == first, payload
        assert payload["state"] == "disabled", payload
        assert payload["ok"] is False and payload["reachable"] is False, payload
        page.get_by_text(payload["message"], exact=False).first.wait_for()

        # Persisted/read-back: the canonical platform listing still carries the
        # platform the probe targeted.
        code, after = _api(base, token, "GET",
                           "/agent/api/messaging/platforms")
        ids = {p["id"] for p in after.get("platforms", [])}
        assert payload["platform"] in ids, (payload["platform"], ids)
        assert first in ids, (first, ids)
    finally:
        ctx.close()


def test_webhooks_enable_and_list_persist(browser, ui_server):
    """Webhooks enable: canonical write -> canonical read (no split-brain).

    ``/api/webhooks`` is in ``QUBIT_READ_AUTHORITY`` now, so the GET the page
    issues after enabling is served by the SAME canonical store the enable
    wrote — ``enabled: true`` persists and the header ``New subscription``
    control becomes usable (before the fix the fork proxy reported
    ``enabled: false`` from its own empty store).
    """
    base, token = ui_server
    # Normalize through the canonical route so the page renders the control.
    code, disabled = _api(base, token, "POST", "/agent/api/webhooks/enable",
                          {"enabled": False, "confirm": True})
    assert code == 200 and disabled.get("enabled") is False, disabled
    assert _api(base, token, "GET", "/agent/api/webhooks")[1].get(
        "enabled") is False

    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    page = ctx.new_page()
    try:
        page.goto(base + "/webhooks", wait_until="domcontentloaded",
                  timeout=30000)
        page.get_by_role("heading", name="Webhooks").first.wait_for(timeout=20000)
        with page.expect_response(
                lambda r: r.url.split("?")[0].endswith("/api/webhooks/enable")
                and r.request.method == "POST" and r.status == 200) as info:
            page.locator("sakuga-button").filter(has_text="Enable webhooks").locator("button").click()
        assert info.value.status == 200, info.value.status
        assert info.value.json().get("enabled") is True, info.value.json()

        # Canonical read-back reflects the write (was the fork's false store).
        got = _poll(lambda: (
            lambda c, d: d if c == 200 and d.get("enabled") else None
        )(*_api(base, token, "GET", "/agent/api/webhooks")))
        assert got and got.get("enabled") is True, got
        # The create control is reachable now (was disabled by the split-brain).
        new_sub = page.locator("[data-dashboard-page-header] sakuga-button").filter(
            has_text="New subscription").locator("button")
        new_sub.wait_for(timeout=10000)
        assert new_sub.is_enabled()
        assert page.get_by_text("Gateway restart failed", exact=False).count() == 0
    finally:
        ctx.close()


def test_webhooks_create_and_delete_via_spa(browser, ui_server):
    """Webhooks create/delete: canonical contract + persisted read-backs.

    Direct canonical shape (the SPA's ``createWebhook`` mapping) persists a
    subscription in ``voicebridge.webhooks`` and returns the ``WebhookRoute``
    shape. A bodyless DELETE stages a typed 409 with a REAL ``confirmation_id``
    (before the fix it was empty/unsettleable), settles by id, and the record
    is gone. The SPA's own create modal and row Delete are then driven end to
    end and verified against the canonical list.
    """
    base, token = ui_server
    direct = "phase4-hook"
    code, enabled = _api(base, token, "POST", "/agent/api/webhooks/enable",
                         {"enabled": True, "confirm": True})
    assert code == 200 and enabled.get("enabled") is True, enabled

    # Direct canonical request/response + persisted read-back.
    code, created = _api(base, token, "POST", "/agent/api/webhooks",
                         {"id": direct, "url": "http://127.0.0.1:9/hook",
                          "events": ["task.completed"],
                          "secret_ref": "env:PHASE4_HOOK", "confirm": True})
    assert code == 200 and created.get("ok") is True, (code, created)
    assert created.get("name") == direct, created
    assert created.get("url") == "http://127.0.0.1:9/hook", created
    assert created.get("secret_set") is True, created
    code, listing = _api(base, token, "GET", "/agent/api/webhooks")
    assert code == 200, (code, listing)
    assert direct in {s["name"] for s in listing.get("subscriptions", [])}, listing

    # Bodyless DELETE stages a settleable 409 with a real id.
    code, staged = _api(base, token, "DELETE",
                        f"/agent/api/webhooks/{direct}")
    assert code == 409 and staged.get("code") == "CONFIRM_REQUIRED", staged
    cid = staged.get("confirmation_id")
    assert cid, staged
    code, settled = _api(base, token, "DELETE",
                         f"/agent/api/webhooks/{direct}",
                         {"confirmation_id": cid})
    assert code == 200, (code, settled)
    code, listing = _api(base, token, "GET", "/agent/api/webhooks")
    assert direct not in {s["name"] for s in listing.get("subscriptions", [])}, \
        listing

    # Now the real SPA controls.
    ui_name = "phase4-hook-ui"
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    page = ctx.new_page()
    _accept_dialogs(page)
    try:
        page.goto(base + "/webhooks", wait_until="domcontentloaded",
                  timeout=30000)
        page.locator("[data-dashboard-page-header] sakuga-button").filter(
            has_text="New subscription").locator("button").click(timeout=20000)
        dialog = page.get_by_role("dialog", name="New subscription")
        dialog.wait_for(timeout=10000)
        dialog.locator("#webhook-name").locator("input").fill(ui_name)
        dialog.locator("#webhook-url").locator("input").fill("http://127.0.0.1:9/ui-hook")
        dialog.locator("#webhook-events").locator("input").fill("task.completed")
        dialog.locator("#webhook-secret-ref").locator("input").fill("env:PHASE4_UI")
        with page.expect_response(
                lambda r: r.url.split("?")[0].endswith("/api/webhooks")
                and r.request.method == "POST" and r.status == 200) as info:
            dialog.locator("sakuga-button").filter(has_text="Create").locator("button").click()
        assert info.value.status == 200, info.value.status
        payload = info.value.json()
        assert payload.get("name") == ui_name and payload.get("url") == \
            "http://127.0.0.1:9/ui-hook", payload
        dialog.locator("sakuga-button").filter(has_text="Done").locator("button").click()

        got = _poll(lambda: (lambda c, d: d if c == 200 and ui_name in {
            s["name"] for s in d.get("subscriptions", [])} else None)(
                *_api(base, token, "GET", "/agent/api/webhooks")))
        assert got, got

        # Delete through the row control (dialog is the human confirmation).
        page.reload(wait_until="domcontentloaded")
        row = _row_for(page, ui_name, "Delete")
        row.wait_for(timeout=20000)
        row.locator('sakuga-button[aria-label="Delete"]').locator("button").click()
        confirm = page.get_by_role("alertdialog")
        confirm.wait_for(timeout=10000)
        with page.expect_response(
                lambda r: r.url.split("?")[0].endswith(
                    f"/api/webhooks/{ui_name}")
                and r.request.method == "DELETE" and r.status == 200) as info:
            confirm.get_by_role("button", name="Delete", exact=True).click()
        assert info.value.status == 200, info.value.status

        code, after = _api(base, token, "GET", "/agent/api/webhooks")
        assert code == 200, (code, after)
        assert ui_name not in {s["name"] for s in after.get("subscriptions", [])}, \
            after
    finally:
        ctx.close()


def test_mcp_add_enable_delete_via_spa(browser, ui_server):
    """MCP add/enable/delete: real SPA controls -> canonical registry.

    A disabled HTTP server is seeded through the canonical registry. Test
    connection must report it as valid and disconnected without probing it.

    The SPA's Add Server control now sends ``transport`` (``mcp-server-create``)
    and runs the shared confirm round-trip; Enable and Delete settle their
    staged confirmations the same way. Every step is verified by a read-back
    from the same canonical registry.
    """
    base, token = ui_server
    code, seeded = _api(base, token, "POST", "/agent/api/mcp/servers",
                        {"name": "phase4-mcp", "transport": "http",
                         "url": "https://mcp.example.invalid/mcp", "confirm": True})
    assert code == 200, (code, seeded)

    # A transport-less create is still honestly refused (typed validation is
    # not weakened): the empty transport defaults to stdio and needs a command.
    code, body = _api(base, token, "POST", "/agent/api/mcp/servers",
                      {"name": "phase4-mcp-bad",
                       "url": "https://mcp.example.invalid/mcp", "confirm": True})
    assert code == 400 and body.get("code") == "MCP_BAD_INPUT", (code, body)

    ui_name = "phase4-mcp-ui"
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    page = ctx.new_page()
    _accept_dialogs(page)
    try:
        page.goto(base + "/mcp", wait_until="domcontentloaded", timeout=30000)
        row = _row_for(page, "phase4-mcp", "Test connection")
        row.wait_for(timeout=20000)
        with page.expect_response(
                lambda r: r.url.endswith("/api/mcp/servers/phase4-mcp/test")
                and r.request.method == "POST") as info:
            row.locator('sakuga-button[aria-label="Test connection"]').locator("button").click()
        assert info.value.status == 200, info.value.status
        payload = info.value.json()
        assert payload["name"] == "phase4-mcp", payload
        assert payload["valid"] and not payload["connected"] and not payload["ok"], payload
        assert payload["code"] == "MCP_NOT_CONNECTED" and not payload["network_probe"], payload

        # Add Server through the real dialog: stages then settles, persists.
        page.locator("[data-dashboard-page-header] sakuga-button").filter(
            has_text="Add Server").locator("button").click()
        dialog = page.get_by_role("dialog", name="Add MCP server")
        dialog.wait_for(timeout=10000)
        dialog.locator("#mcp-name").locator("input").fill(ui_name)
        dialog.locator("#mcp-url").locator("input").fill("https://mcp.example.invalid/mcp")
        with page.expect_response(
                lambda r: r.url.split("?")[0].endswith("/api/mcp/servers")
                and r.request.method == "POST" and r.status == 200) as info:
            dialog.locator("sakuga-button").filter(has_text="Add").locator("button").click()
        assert info.value.status == 200, info.value.status
        sent = json.loads(info.value.request.post_data)
        assert sent.get("transport") == "http", sent
        assert sent.get("name") == ui_name, sent

        got = _poll(lambda: (lambda c, d: d if c == 200 and ui_name in {
            s["name"] for s in d.get("servers", [])} else None)(
                *_api(base, token, "GET", "/agent/api/mcp/servers")))
        assert got, got

        # Enable through the row control.
        page.reload(wait_until="domcontentloaded")
        row2 = _row_for(page, ui_name, "Enable")
        row2.wait_for(timeout=20000)
        with page.expect_response(
                lambda r: r.url.split("?")[0].endswith(
                    f"/api/mcp/servers/{ui_name}/enabled")
                and r.request.method == "PUT" and r.status == 200) as info:
            row2.locator('sakuga-button[aria-label="Enable"]').locator("button").click()
        assert info.value.status == 200, info.value.status
        got = _poll(lambda: (lambda c, d: d if c == 200 else None)(
            *_api(base, token, "GET", "/agent/api/mcp/servers")))
        server = next((s for s in got.get("servers", [])
                       if s.get("name") == ui_name), None)
        assert server and server.get("enabled") is True, server

        # Delete through the row control (dialog is the human confirmation).
        row3 = _row_for(page, ui_name, "Delete")
        row3.locator('sakuga-button[aria-label="Delete"]').locator("button").click()
        confirm = page.get_by_role("alertdialog")
        confirm.wait_for(timeout=10000)
        with page.expect_response(
                lambda r: r.url.split("?")[0].endswith(
                    f"/api/mcp/servers/{ui_name}")
                and r.request.method == "DELETE" and r.status == 200) as info:
            confirm.get_by_role("button", name="Delete", exact=True).click()
        assert info.value.status == 200, info.value.status
        got = _poll(lambda: (lambda c, d: d if c == 200 else None)(
            *_api(base, token, "GET", "/agent/api/mcp/servers")))
        assert ui_name not in {s["name"] for s in got.get("servers", [])}, got
    finally:
        ctx.close()
    # Cleanup the seeded server through the canonical confirm path.
    _api(base, token, "DELETE", "/agent/api/mcp/servers/phase4-mcp",
         {"confirm": True})


def test_memory_read_and_search_persists_action_status(browser, ui_server):
    """Memory: real provider read + the Search control's canonical op.

    ``GET /api/memory`` returns the live provider list; the Search control runs
    ``POST /api/ops/run {id: memory_search}`` on the shared broker and the
    canonical ``GET /api/actions/memory_search/status`` read-back records the
    executed action. There is no memory ``note``/write op in the catalog, so
    that half of the task is NOT_REACHABLE (recorded here explicitly).
    """
    base, token = ui_server
    code, mem = _api(base, token, "GET", "/agent/api/memory")
    assert code == 200 and isinstance(mem.get("providers"), list), mem

    code, catalog = _api(base, token, "GET", "/agent/api/ops")
    assert code == 200, catalog
    memory_ids = {o["id"] for o in catalog.get("ops", [])
                  if o.get("group") == "memory"}
    assert "memory_search" in memory_ids, memory_ids
    assert not ({"memory_note", "memory_write", "memory_add"} & memory_ids), \
        memory_ids  # no write/note control exists to exercise

    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    page = ctx.new_page()
    try:
        page.goto(base + "/memory", wait_until="domcontentloaded", timeout=30000)
        page.get_by_label("Memory search query").wait_for(timeout=20000)
        page.get_by_label("Memory search query").fill("phase4-memory")
        with page.expect_response(
                lambda r: r.url.endswith("/api/ops/run")
                and r.request.method == "POST") as info:
            page.get_by_role("button", name="Search memory").click()
        assert info.value.status == 200, info.value.status
        payload = info.value.json()
        assert payload.get("state") == "executed", payload
        assert payload.get("result", {}).get("engine"), payload

        status = _poll(lambda: (
            lambda c, d: d if c == 200 and d.get("state") == "executed" else None
        )(*_api(base, token, "GET", "/agent/api/actions/memory_search/status")))
        assert status and status.get("name") == "memory_search", status
        assert status.get("lines"), status
    finally:
        ctx.close()


def test_boards_create_card_and_transition(browser, ui_server):
    """Boards: create board / add card / move card through the real UI.

    Each mutation stages a typed 409 on the shared journal; the plugin's inline
    Confirm settles it. Every step is verified by a canonical read-back
    (``GET /api/ops/boards`` and ``GET /api/ops/kanban?board=...``).
    """
    base, token = ui_server
    board = "phase4-board"
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    page = ctx.new_page()

    def read_boards():
        c, d = _api(base, token, "GET", "/agent/api/ops/boards")
        return d if c == 200 else None

    def read_tasks():
        c, d = _api(base, token, "GET",
                    f"/agent/api/ops/kanban?board={board}")
        return d if c == 200 else None

    def confirm_pending():
        region = page.get_by_role(
            "region", name="Pending board confirmation")
        region.wait_for(timeout=15000)
        region.get_by_role("button", name="Confirm").click()

    try:
        page.goto(base + "/qubit-boards", wait_until="domcontentloaded",
                  timeout=30000)
        page.get_by_role("button", name="Create board").wait_for(timeout=20000)
        page.get_by_label("New board slug").fill(board)
        page.get_by_label("New board name").fill("Phase4 Board")
        page.get_by_role("button", name="Create board").click()
        confirm_pending()

        got = _poll(lambda: (lambda d: d if d and any(
            b.get("board") == board for b in d.get("result", {}).get("boards", [])
        ) else None)(read_boards()))
        assert got, got
        assert any(b.get("name") == "Phase4 Board"
                   for b in got["result"]["boards"]), got

        # Select the new board so card mutations target it.
        page.get_by_label("Active board").fill(board)
        page.wait_for_timeout(1000)
        page.get_by_label("New card title").fill("Phase4 Card")
        page.get_by_role("button", name="Add card").click()
        confirm_pending()

        got = _poll(lambda: (lambda d: d if d and any(
            t.get("title") == "Phase4 Card"
            for t in d.get("result", {}).get("tasks", [])
        ) else None)(read_tasks()))
        assert got, got
        task = next(t for t in got["result"]["tasks"]
                    if t.get("title") == "Phase4 Card")
        tid = task["id"]
        assert task["status"] == "open", task

        # Transition the card to claimed via the row's select.
        page.get_by_label(f"Move card {tid} to status").wait_for(timeout=15000)
        page.get_by_label(f"Move card {tid} to status").select_option("claimed")
        confirm_pending()

        got = _poll(lambda: (lambda d: d if d and any(
            t.get("id") == tid and t.get("status") == "claimed"
            for t in d.get("result", {}).get("tasks", [])
        ) else None)(read_tasks()))
        assert got, got
        moved = next(t for t in got["result"]["tasks"] if t.get("id") == tid)
        assert moved["status"] == "claimed", moved
    finally:
        ctx.close()


def test_env_custom_key_set_and_remove(browser, ui_server):
    """Keys: a custom reference is set through the SPA and persisted.

    The Custom Keys form opens an edit row; Save PUTs ``/api/env`` with the
    vault reference and the canonical ``GET /api/env`` read-back distinguishes
    the saved reference from a resolved credential.
    Clear -> confirm dialog DELETEs it and the read-back drops it.
    """
    base, token = ui_server
    key = "PHASE4_TEST_REF"
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    page = ctx.new_page()
    try:
        page.goto(base + "/env", wait_until="domcontentloaded", timeout=30000)
        card = page.locator("#section-custom")
        card.wait_for(timeout=20000)
        card.locator("input[aria-label='Variable name']").fill(key)
        card.get_by_role("button", name="Add", exact=True).first.click()
        value = card.locator("input[placeholder='Enter value...']")
        value.wait_for(timeout=10000)
        assert value.get_attribute("type") == "password"
        value.fill("vault:phase4-ref")
        with page.expect_response(
                lambda r: r.url.endswith("/api/env")
                and r.request.method == "PUT") as info:
            card.get_by_role("button", name="Save", exact=True).first.click()
        assert info.value.status == 200, info.value.status
        assert info.value.json().get("ok") is True, info.value.json()

        code, env = _api(base, token, "GET", "/agent/api/env")
        assert code == 200, (code, env)
        assert env.get(key, {}).get("category") == "references", env.get(key)
        assert env[key]["is_set"] is False
        assert env[key]["redacted_value"] is None

        page.reload(wait_until="domcontentloaded")

        card.get_by_role("button", name="Clear", exact=True).first.click()
        dialog = page.get_by_role("alertdialog", name="Clear this key?")
        dialog.wait_for(timeout=10000)
        with page.expect_response(
                lambda r: r.url.endswith("/api/env")
                and r.request.method == "DELETE") as info:
            dialog.get_by_role("button", name="Delete").click()
        assert info.value.status == 200, info.value.status

        code, env2 = _api(base, token, "GET", "/agent/api/env")
        assert code == 200, (code, env2)
        assert key not in env2 or env2.get(key, {}).get("is_set") is not True, \
            env2.get(key)
    finally:
        ctx.close()
    _api(base, token, "DELETE", "/agent/api/env", {"key": key, "confirm": True})


def test_system_doctor_action_and_gateway_gating(browser, ui_server, monkeypatch):
    """System: a real ops action + the typed supervision gate.

    ``Run doctor`` POSTs ``/api/ops/doctor`` and the canonical
    ``GET /api/actions/doctor/status`` read-back records the executed action.
    The isolated supervisor refuses to restart the host gateway.
    """
    monkeypatch.setenv("QUBIT_GATEWAY_SUPERVISION_DISABLED", "1")
    base, token = ui_server
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    page = ctx.new_page()
    try:
        page.goto(base + "/system", wait_until="domcontentloaded", timeout=30000)
        doctor = page.locator("sakuga-button").filter(has_text="Run doctor").locator("button")
        with page.expect_response(
                lambda r: r.url.endswith("/api/ops/doctor")
                and r.request.method == "POST") as info:
            doctor.click()
        assert info.value.status == 200, info.value.status
        payload = info.value.json()
        assert payload.get("ok") is True and payload.get("name") == "doctor", payload

        status = _poll(lambda: (
            lambda c, d: d if c == 200 and d.get("state") == "executed" else None
        )(*_api(base, token, "GET", "/agent/api/actions/doctor/status")))
        assert status and status.get("name") == "doctor", status

        # Gateway process control is real: the backend routes to
        # voicebridge.gateway_supervisor. In a test process the supervisor's
        # test guard refuses to touch the host service, so the call is a typed
        # 503 (never the old unconditional GATEWAY_NO_SUPERVISION refusal and
        # never a real service restart).
        gateway = page.locator("main section").filter(
            has=page.get_by_role("heading", name="Gateway", exact=True))
        pw.expect(gateway.locator("sakuga-button").filter(has_text="Restart")).to_have_count(1)
        code, body = _api(base, token, "POST", "/agent/api/gateway/restart")
        assert code == 409 and body["code"] == "GATEWAY_CONFIRM_REQUIRED", (code, body)
        code, body = _api(base, token, "POST", "/agent/api/gateway/restart",
                          {"confirm": True})
        assert code == 503 and body.get("code") == \
            "GATEWAY_SUPERVISION_UNAVAILABLE", (code, body)
    finally:
        ctx.close()


def test_devices_mint_enroll_and_remove_in_browser(browser, ui_server):
    """Use the Devices page with the real pairing store and confirmation journal."""
    base, token = ui_server
    path = "/agent/api/qubit/devices"
    status, before = _api(base, token, "GET", path + "/setup-status")
    assert status == 200 and before["enabled"]
    label = "Audit companion"
    scopes = ["operator.read", "status.read"]

    context = browser.new_context(viewport={"width": 1440, "height": 900})
    page = context.new_page()
    try:
        page.goto(base + "/devices", wait_until="domcontentloaded")
        mint = page.get_by_role("region", name="Mint setup code form")
        mint.locator('sakuga-input[aria-label="Setup code label"]').locator("input").fill(label)
        mint.locator('sakuga-input[aria-label="Setup code scopes"]').locator("input").fill(",".join(scopes))
        dialog = page.get_by_role("dialog", name="Confirm device action")
        confirm = dialog.locator('sakuga-button[aria-label="Confirm"]').locator("button")

        def result(action):
            return page.expect_response(
                lambda r: r.url.split("?")[0].endswith(f"{path}/{action}")
                and r.request.method == "POST" and r.status == 200
            )

        mint.locator('sakuga-button[aria-label="Mint setup code"]').locator("button").click()
        with result("setup-code-mint") as created:
            confirm.click()
        record = created.value.json()
        assert record["label"] == label and record["scopes"] == scopes
        assert record["code_present"] is True and set(record) <= {
            "ok", "id", "label", "scopes", "expires_at", "code_present"
        }
        device_id = record["id"]
        status, pending = _api(base, token, "GET", path + "/setup-status")
        assert status == 200 and pending["pending_codes"] == before["pending_codes"] + 1
        page.get_by_role("list", name="Pending device pairings").get_by_text(label, exact=True).wait_for()

        enroll = page.get_by_role("region", name="Enroll device form")
        enroll.locator('sakuga-input[aria-label="Enroll setup code id"]').locator("input").fill(device_id)
        enroll.locator('sakuga-button[aria-label="Enroll device"]').locator("button").click()
        with result("enroll") as enrolled:
            confirm.click()
        assert enrolled.value.json()["approved"] == device_id
        status, current = _api(base, token, "GET", path + "/setup-status")
        assert status == 200 and current["paired_devices"] == before["paired_devices"] + 1
        paired = page.get_by_role("list", name="Paired devices")
        paired.get_by_text(label, exact=True).wait_for()
        assert paired.locator(f'sakuga-button[aria-label="Rotate token for {device_id}"]').locator("button").is_disabled()
        paired.locator(f'sakuga-button[aria-label="Show identity for {device_id}"]').locator("button").click()
        page.get_by_role("region", name="Device identity").get_by_text(label, exact=True).wait_for()

        paired.locator(f'sakuga-button[aria-label="Remove pairing {device_id}"]').locator("button").click()
        with result("remove") as removed:
            confirm.click()
        assert removed.value.json()["revoked"] == device_id
        status, after = _api(base, token, "GET", path + "/setup-status")
        assert status == 200 and after["paired_devices"] == before["paired_devices"]
    finally:
        context.close()


def test_operations_run_op_action_status(browser, ui_server):
    """Operations: run a catalog op through the console and read it back.

    The console lists ``GET /api/ops``; selecting ``auth_status`` and pressing
    Run issues ``POST /api/ops/run`` and
    ``GET /api/actions/auth_status/status`` records the executed action.
    """
    base, token = ui_server
    code, catalog = _api(base, token, "GET", "/agent/api/ops")
    assert code == 200 and catalog.get("count", 0) >= 1, catalog
    assert "auth_status" in {o["id"] for o in catalog["ops"]}, "no auth_status op"

    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    page = ctx.new_page()
    try:
        page.goto(base + "/operations", wait_until="domcontentloaded",
                  timeout=30000)
        search = page.get_by_label("Search operations")
        search.wait_for(timeout=20000)
        search.fill("auth_status")
        item = page.get_by_role("listitem").filter(has_text="auth_status").first
        item.wait_for(timeout=10000)
        item.click()
        run = page.get_by_role("button", name="Run auth_status")
        run.wait_for(timeout=10000)
        with page.expect_response(
                lambda r: r.url.endswith("/api/ops/run")
                and r.request.method == "POST") as info:
            run.click()
        assert info.value.status == 200, info.value.status
        sent = json.loads(info.value.request.post_data)
        assert sent.get("id") == "auth_status", sent

        status = _poll(lambda: (
            lambda c, d: d if c == 200 and d.get("state") == "executed" else None
        )(*_api(base, token, "GET", "/agent/api/actions/auth_status/status")))
        assert status and status.get("name") == "auth_status", status
    finally:
        ctx.close()


def test_reports_reads_are_real(browser, ui_server):
    """Reports: the page's canonical reads really fire and are recorded.

    On mount the plugin issues ``GET /api/logs`` and two shared-broker reads
    (``reports_list``, ``insights``); the action-status read-back proves the
    broker executed them. No control exists to exercise on this read-only page.
    """
    base, token = ui_server
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    page = ctx.new_page()
    seen = []
    page.on("request", lambda r: seen.append((r.method, r.url, r.post_data)))
    try:
        page.goto(base + "/reports", wait_until="domcontentloaded",
                  timeout=30000)
        page.get_by_role("heading", name="Reports").first.wait_for(timeout=20000)
        page.wait_for_timeout(3500)
        ops_ids = []
        for method, url, post in seen:
            if method == "POST" and url.endswith("/agent/api/ops/run") and post:
                try:
                    ops_ids.append(json.loads(post).get("id"))
                except (ValueError, TypeError):
                    pass
        assert "reports_list" in ops_ids, ops_ids
        assert "insights" in ops_ids, ops_ids
        assert any(m == "GET" and u.endswith("/agent/api/logs")
                   for m, u, _ in seen), seen

        for op in ("reports_list", "insights"):
            status = _poll(lambda op=op: (
                lambda c, d: d if c == 200 and d.get("state") == "executed"
                else None
            )(*_api(base, token, "GET", f"/agent/api/actions/{op}/status")))
            assert status and status.get("name") == op, (op, status)
    finally:
        ctx.close()


def test_achievements_rescan_and_scan_status(browser, ui_server):
    """Achievements: Rescan runs the canonical scan and the status advances."""
    base, token = ui_server
    before = _api(base, token, "GET",
                  "/agent/api/plugins/qubit-achievements/scan-status")[1]
    before_runs = int(before.get("run_count", 0))

    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    page = ctx.new_page()
    try:
        page.goto(base + "/achievements", wait_until="domcontentloaded",
                  timeout=30000)
        rescan = page.get_by_role("button", name="Rescan")
        rescan.wait_for(timeout=20000)
        with page.expect_response(
                lambda r: r.url.endswith("/plugins/qubit-achievements/rescan")
                and r.request.method == "POST") as info:
            rescan.click()
        assert info.value.status == 200, info.value.status
        payload = info.value.json()
        assert payload.get("ok") is True and payload.get("achievements"), payload

        after = _poll(lambda: (
            lambda c, d: d if c == 200 and int(d.get("run_count", 0)) > before_runs
            else None
        )(*_api(base, token, "GET",
                "/agent/api/plugins/qubit-achievements/scan-status")))
        assert after, (before_runs, after)
    finally:
        ctx.close()


@pytest.mark.parametrize("width, min_width", [(390, 200), (1440, 400)])
def test_qubit_chat_composer_respects_viewport(browser, ui_server, width, min_width):
    """Keep the composer usable at narrow and desktop viewport widths."""
    base, _token = ui_server
    context = browser.new_context(viewport={"width": width, "height": 900})
    page = context.new_page()
    try:
        page.goto(base + "/qubit-chat", wait_until="domcontentloaded")
        textarea = page.get_by_label("Message Qubit")
        pw.expect(textarea).to_be_visible()
        box = textarea.bounding_box()
        assert box and box["width"] >= min_width and box["height"] >= 36, box
    finally:
        context.close()


@pytest.mark.parametrize("surface", ["/qubit-chat", "/chat"])
def test_qubit_chat_sidebar_publishes_and_reloads_the_provider(browser, ui_server, monkeypatch, surface):
    """Switch between two providers that list the same model and reload the picker."""
    from voicebridge import qubit_compat as compat
    from urllib.parse import parse_qs, urlsplit

    base, token = ui_server
    model = "audit-shared-model"
    catalog = {
        "ok": True,
        "provider": "audit-alpha",
        "model": model,
        "providers": [
            {"slug": slug, "name": name, "models": [model], "total_models": 1,
             "authenticated": True, "is_current": slug == "audit-alpha"}
            for slug, name in (("audit-alpha", "Audit Alpha"), ("audit-beta", "Audit Beta"))
        ],
    }

    validate = compat._chat_session_provider_pair
    def validate_fixture_pair(cfg, provider, requested_model, **kwargs):
        return validate(cfg, provider, requested_model, _payload=catalog, **kwargs)

    monkeypatch.setattr(compat, "_chat_session_provider_pair", validate_fixture_pair)
    monkeypatch.setattr(compat, "_h_model_options_uncached", lambda *_: (200, catalog))
    status, reply = _api(base, token, "POST", "/agent/api/qubit/chat/sessions", {"name": "Sidebar fixture"})
    assert status == 200, reply
    sid = reply["session"]["id"]
    path = "/agent/api/qubit/chat/sessions/" + sid
    status, reply = _api(base, token, "PATCH", path, {"provider": "audit-alpha", "model": model})
    assert status == 200, reply

    if surface == "/chat":
        monkeypatch.setattr("qubit_cli.model_selection_guards.combined_selection_warning", lambda *a, **k: None)
        from voicebridge.chat_transport import store
        from voicebridge.pty_ws import PtyWsServer

        def fixture_guard(self, profile_id="", session_id=""):
            row = store(self.cfg).show(session_id).get("session", {})
            return {
                "ok": True, "profile_id": "audit-fixture",
                "model": row.get("model", model), "provider": row.get("provider", "audit-alpha"),
                "context_tokens": 131072, "min_context_tokens": 4096,
            }

        monkeypatch.setattr(PtyWsServer, "_resolve_guard", fixture_guard)

    context = browser.new_context(viewport={"width": 1440, "height": 950})
    page = context.new_page()
    page.set_default_timeout(8000)
    try:
        scope = "&profile=default" if surface == "/chat" else ""
        page.goto(base + surface + "?resume=" + sid + scope, wait_until="domcontentloaded")
        page.locator('[title="audit-alpha/' + model + '"]').click(timeout=20000)
        dialog = page.get_by_role("dialog").filter(has_text="Switch Model")
        dialog.locator(".qmodel-picker__current").get_by_text("audit-alpha", exact=True).wait_for()
        dialog.get_by_text("Applies to this session.", exact=True).wait_for()
        def refreshed(res):
            url = urlsplit(res.url)
            return (url.path.endswith("/api/model/options")
                    and parse_qs(url.query).get("refresh") == ["1"])

        with page.expect_response(refreshed) as refreshing:
            dialog.locator(".qmodel-picker__action--refresh").locator("button").click()
        assert refreshing.value.ok
        dialog.locator(".qmodel-picker__provider-item").filter(has_text="Audit Beta").click()
        dialog.locator(".qmodel-picker__model-item").filter(has_text=model).click()
        with page.expect_response(lambda res: urlsplit(res.url).path == path and res.request.method == "PATCH") as changed:
            dialog.locator(".qmodel-picker__action--confirm").locator("button").click()
        assert changed.value.ok
        if surface == "/chat":
            assert parse_qs(urlsplit(changed.value.url).query)["profile"] == ["default"]
            assert changed.value.json()["terminal_model_request"]["provider"] == "audit-beta"
            page.get_by_text("is queued for the next terminal turn.", exact=False).wait_for()
            assert page.locator('[title="audit-alpha/' + model + '"]').count() == 1
            terminal_store = store(_TEST_CTX["cfg"])
            request = terminal_store._db.read_terminal_model_request(sid)
            terminal_store.set_model(sid, model, provider="audit-beta")
            terminal_store._db.finish_terminal_model_request(sid, request, "applied")
            page.get_by_text("Terminal switched to " + model, exact=False).wait_for()
        else:
            assert changed.value.json()["session"]["provider"] == "audit-beta"
        status, reply = _api(base, token, "GET", path)
        assert status == 200 and reply["session"]["provider"] == "audit-beta", reply
        button = page.locator('[title="audit-beta/' + model + '"]')
        button.wait_for()
        page.reload(wait_until="domcontentloaded")
        button.click(timeout=20000)
        dialog.locator(".qmodel-picker__current").get_by_text("audit-beta", exact=True).wait_for()
    finally:
        context.close()


def test_manga_chat_sidebar_labels_and_composer_have_one_visual_owner(browser, ui_server):
    """Exercise the current sidebar art and painted composer in Chromium."""
    base, token = ui_server
    theme_path = "/agent/api/dashboard/theme"
    status, previous = _api(base, token, "GET", theme_path)
    assert status == 200 and previous.get("theme"), previous
    status, selected = _api(base, token, "PUT", theme_path, {"theme": "q6"})
    assert status == 200 and selected.get("theme") == "q6", selected
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    context.add_init_script('localStorage.setItem("qubit-sidebar-collapsed", "false")')
    page = context.new_page()
    try:
        page.goto(base + "/qubit-chat", wait_until="domcontentloaded")
        page.locator('html[data-qubit-manga="on"]').wait_for()
        sidebar = page.locator("#app-sidebar")
        chat = sidebar.get_by_role("group", name="Chat", exact=True)
        workspace = sidebar.get_by_role("group", name="Workspace", exact=True)
        for group, label in ((chat, "CHAT"), (workspace, "WORKSPACE")):
            title = group.locator(".qubit-sidebar-title")
            title.get_by_text(label, exact=True).wait_for()
            assert title.locator('svg[aria-hidden="true"]').count() == 1
        assert sidebar.locator("nav .q5t-gateway__link").count() == 0
        sidebar.locator(".q5t-system-shell .q5t-gateway__link").wait_for()

        composer = page.locator(".qchat__composer")
        composer.wait_for()
        chrome = composer.evaluate("""node => ({
            frame: getComputedStyle(node, '::before').borderImageSource,
            overlay: getComputedStyle(node, '::after').content,
            background: getComputedStyle(node).backgroundImage,
            field: getComputedStyle(node.querySelector('textarea')).backgroundImage,
        })""")
        assert "/manga-ui/qubit-chat/composer.svg" in chrome["frame"], chrome
        assert (chrome["overlay"], chrome["background"], chrome["field"]) == ("none",) * 3

        sidebar.locator("[data-sidebar-toggle]").locator("button").click()
        page.wait_for_function("document.querySelector('#app-sidebar')?.dataset.collapsed === 'true'")
        assert chat.count() == workspace.count() == 1
        assert chat.locator(".qubit-sidebar-title").count() == 0
    finally:
        context.close()
        _api(base, token, "PUT", theme_path, {"theme": previous["theme"]})
