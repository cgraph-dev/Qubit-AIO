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
            "X-Hermes-Session-Token": token,
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read().decode("utf-8") or "{}"
            return resp.status, json.loads(raw)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8") or "{}"
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, {"_raw": raw}


def _row_for(page, text: str, aria: str):
    """The innermost row div that contains ``text`` and the ``aria`` button."""
    el = page.get_by_text(text, exact=True).first
    return el.locator(
        f'xpath=ancestor::div[.//button[@aria-label="{aria}"]][1]')


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
        row = _row_for(page, "phase4-seed", "Rename session")
        row.get_by_role("button", name="Rename session").click()
        box = page.get_by_role("textbox", name="Session title")
        box.fill("phase4-renamed")
        box.press("Enter")

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
    try:
        page.goto(base + "/cron", wait_until="domcontentloaded", timeout=30000)
        row = _row_for(page, "phase4-cron", "Pause")
        row.get_by_role("button", name="Pause").click()

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
    _api(base, token, "POST", f"/agent/api/cron/jobs/{jid}/pause")

    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    page = ctx.new_page()
    try:
        page.goto(base + "/cron", wait_until="domcontentloaded", timeout=30000)
        row = _row_for(page, "phase4-cron-resume", "Resume")
        row.wait_for(timeout=20000)
        resume = row.get_by_role("button", name="Resume")
        assert resume.count() >= 1
        # The Resume control fires the real backend resume and the canonical
        # store reflects it.
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


def test_skills_list_and_toggle_requires_confirmation(ui_server):
    """Skills list is real; a toggle stages a typed confirmation on the journal."""
    base, token = ui_server
    code, skills = _api(base, token, "GET", "/agent/api/skills")
    assert code == 200 and isinstance(skills, list) and skills, skills
    target = skills[0]
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
    assert canceled.get("state") == "executed", canceled


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


def test_config_save_persists_non_secret_edit(browser, ui_server):
    """Config Save persists a non-secret edit through the real SPA control.

    The SPA PUTs the whole tree it read, including the raw secret-shaped leaves
    the served runtime returns (``browser.camofox.session_key``,
    ``terminal.docker_shared_container_key``, ``voice.record_key``). The backend
    skips the unchanged secret leaves instead of refusing the whole save, so the
    real edit lands in the canonical store. A changed secret leaf is still
    refused.
    """
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
        page.get_by_placeholder("Search...").fill("browser.headed")
        switch = page.get_by_role("switch", name="Headed")
        switch.wait_for(timeout=10000)
        assert switch.get_attribute("aria-checked") == "false", "seed not false"
        switch.click()
        with page.expect_response(
                lambda r: r.url.endswith("/agent/api/config")
                and r.request.method == "PUT") as info:
            page.get_by_role("button", name="Save").first.click()
        assert info.value.status == 409, info.value.status
        dialog = page.get_by_role("alertdialog")
        dialog.wait_for(timeout=10000)
        dialog.get_by_role("button", name="Save").click()

        deadline = time.time() + 15
        value = None
        while time.time() < deadline:
            c, cfg = _api(base, token, "GET", "/agent/api/config")
            value = (cfg.get("browser") or {}).get("headed")
            if value is True:
                break
            time.sleep(0.3)
        assert value is True, value
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
    code, profs = _api(base, token, "GET", "/agent/api/profiles")
    assert code == 200 and profs.get("profiles"), profs
    names = [p["name"] for p in profs["profiles"]]
    code, act = _api(base, token, "GET", "/agent/api/profiles/active")
    assert code == 200 and act.get("active"), act
    original = act["active"]
    target = next((n for n in names if n != original), None)
    assert target, names

    def read_active():
        c, d = _api(base, token, "GET", "/agent/api/profiles/active")
        return d.get("active") if c == 200 else None

    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    page = ctx.new_page()
    try:
        page.goto(base + "/profiles", wait_until="domcontentloaded",
                  timeout=30000)
        page.get_by_role("heading", name="Profiles").first.wait_for(timeout=20000)
        # The profile card renders "<name> (<display>)" as one text node, so
        # an exact-text row lookup does not match. Find the Actions button
        # whose card text starts with the target name instead.
        actions = page.get_by_role("button", name="Actions")
        actions.first.wait_for(timeout=20000)
        action_index = None
        for i in range(actions.count()):
            card_text = actions.nth(i).evaluate(
                "e => { let n = e; for (let k = 0; k < 6 && n; k++) {"
                " n = n.parentElement;"
                " if (n && n.innerText && n.innerText.length > 30) break; }"
                " return n ? n.innerText : ''; }")
            if card_text.startswith(f"{target} ("):
                action_index = i
                break
        assert action_index is not None, (target, actions.count())
        actions.nth(action_index).click()
        item = page.get_by_role("menuitem", name="Set as active")
        item.wait_for(timeout=10000)
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
    ``_compat_confirm``); the human confirms and the re-issued POST registers
    the Brain principal. Persisted read-back is the canonical
    ``voicebridge.brain`` registry — the same store the CLI
    ``qubit brain principal-add`` and voice write.
    """
    from voicebridge import brain as B

    base, token = ui_server
    cfg = _TEST_CTX["cfg"]
    name = "phase4-created"
    assert name not in {p["id"] for p in B.principal_list(cfg)["principals"]}, \
        "seed leaked from a prior run"

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
        page.get_by_role("button", name="Create", exact=True).click()
        dialog = page.get_by_role("dialog", name="New Profile")
        dialog.wait_for(timeout=10000)
        dialog.get_by_role("textbox", name="Name").fill(name)
        # The staged 409 is followed by the confirmed re-issue; wait for the
        # 200 (the shared round-trip settles on the human's window.confirm).
        # The settle re-issue carries ``?confirmation_id=`` so match the path.
        with page.expect_response(
                lambda r: r.url.split("?")[0].endswith("/agent/api/profiles")
                and r.request.method == "POST"
                and r.status == 200) as info:
            dialog.get_by_role("button", name="Create", exact=True).click()
        assert info.value.status == 200, info.value.status
        assert info.value.json().get("ok") is True, info.value.json()

        def principal_present() -> bool:
            return name in {p["id"] for p in B.principal_list(cfg)["principals"]}

        assert _poll(principal_present), "principal not registered"
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
        assert payload.get("ok") is True, payload
        assert payload.get("platform"), payload
        assert payload.get("state"), payload

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
    _api(base, token, "POST", "/agent/api/webhooks/enable", {"enabled": False})
    assert _api(base, token, "GET", "/agent/api/webhooks")[1].get(
        "enabled") is False

    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    page = ctx.new_page()
    try:
        page.goto(base + "/webhooks", wait_until="domcontentloaded",
                  timeout=30000)
        page.get_by_role("heading", name="Webhooks").first.wait_for(timeout=20000)
        with page.expect_response(
                lambda r: r.url.endswith("/api/webhooks/enable")
                and r.request.method == "POST") as info:
            page.get_by_role("button", name="Enable webhooks").click()
        assert info.value.status == 200, info.value.status
        assert info.value.json().get("enabled") is True, info.value.json()

        # Canonical read-back reflects the write (was the fork's false store).
        got = _poll(lambda: (
            lambda c, d: d if c == 200 and d.get("enabled") else None
        )(*_api(base, token, "GET", "/agent/api/webhooks")))
        assert got and got.get("enabled") is True, got
        # The create control is reachable now (was disabled by the split-brain).
        new_sub = page.get_by_role("button", name="New subscription")
        new_sub.wait_for(timeout=10000)
        assert new_sub.is_enabled()
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
    _api(base, token, "POST", "/agent/api/webhooks/enable", {"enabled": True})

    # Direct canonical request/response + persisted read-back.
    code, created = _api(base, token, "POST", "/agent/api/webhooks",
                         {"id": direct, "url": "http://127.0.0.1:9/hook",
                          "events": ["task.completed"],
                          "secret_ref": "env:PHASE4_HOOK"})
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
        page.get_by_role("button", name="New subscription").wait_for(
            timeout=20000)
        page.get_by_role("button", name="New subscription").click()
        dialog = page.get_by_role("dialog", name="New subscription")
        dialog.wait_for(timeout=10000)
        dialog.get_by_label("Name").fill(ui_name)
        dialog.get_by_label("Delivery URL").fill("http://127.0.0.1:9/ui-hook")
        dialog.get_by_label("Events").fill("task.completed")
        dialog.get_by_label("Signing secret reference").fill("env:PHASE4_UI")
        with page.expect_response(
                lambda r: r.url.split("?")[0].endswith("/api/webhooks")
                and r.request.method == "POST" and r.status == 200) as info:
            dialog.get_by_role("button", name="Create", exact=True).click()
        assert info.value.status == 200, info.value.status
        payload = info.value.json()
        assert payload.get("name") == ui_name and payload.get("url") == \
            "http://127.0.0.1:9/ui-hook", payload
        page.get_by_role("button", name="Done").click()

        got = _poll(lambda: (lambda c, d: d if c == 200 and ui_name in {
            s["name"] for s in d.get("subscriptions", [])} else None)(
                *_api(base, token, "GET", "/agent/api/webhooks")))
        assert got, got

        # Delete through the row control (dialog is the human confirmation).
        page.reload(wait_until="domcontentloaded")
        row = _row_for(page, ui_name, "Delete")
        row.wait_for(timeout=20000)
        row.get_by_role("button", name="Delete").click()
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

    A server is seeded through the canonical registry with ``confirm`` +
    ``transport: http`` (the shape the backend really requires), then the SPA's
    ``Test connection`` control runs ``POST /api/mcp/servers/{name}/test`` and
    the canonical ``GET /api/mcp/servers`` read-back shows the server.

    The SPA's Add Server control now sends ``transport`` (``mcp-server-create``)
    and runs the shared confirm round-trip; Enable and Delete settle their
    staged confirmations the same way. Every step is verified by a read-back
    from the same canonical registry.
    """
    base, token = ui_server
    code, seeded = _api(base, token, "POST", "/agent/api/mcp/servers",
                        {"name": "phase4-mcp", "transport": "http",
                         "url": "https://example.com/mcp", "confirm": True})
    assert code == 200, (code, seeded)

    # A transport-less create is still honestly refused (typed validation is
    # not weakened): the empty transport defaults to stdio and needs a command.
    code, body = _api(base, token, "POST", "/agent/api/mcp/servers",
                      {"name": "phase4-mcp-bad",
                       "url": "https://example.com/mcp", "confirm": True})
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
            row.get_by_role("button", name="Test connection").click()
        assert info.value.status == 200, info.value.status
        payload = info.value.json()
        assert payload.get("ok") is True and payload.get("name") == "phase4-mcp", payload
        assert payload.get("network_probe") is False, payload

        # Add Server through the real dialog: stages then settles, persists.
        page.get_by_role("button", name="Add Server").click()
        dialog = page.get_by_role("dialog", name="Add MCP server")
        dialog.wait_for(timeout=10000)
        dialog.get_by_label("Name").fill(ui_name)
        dialog.get_by_label("URL").fill("https://example.com/mcp")
        with page.expect_response(
                lambda r: r.url.split("?")[0].endswith("/api/mcp/servers")
                and r.request.method == "POST" and r.status == 200) as info:
            dialog.get_by_role("button", name="Add", exact=True).click()
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
            row2.get_by_role("button", name="Enable").click()
        assert info.value.status == 200, info.value.status
        got = _poll(lambda: (lambda c, d: d if c == 200 else None)(
            *_api(base, token, "GET", "/agent/api/mcp/servers")))
        server = next((s for s in got.get("servers", [])
                       if s.get("name") == ui_name), None)
        assert server and server.get("enabled") is True, server

        # Delete through the row control (dialog is the human confirmation).
        row3 = _row_for(page, ui_name, "Delete")
        row3.get_by_role("button", name="Delete").click()
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
    vault reference and the canonical ``GET /api/env`` read-back shows it set.
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
        card.get_by_label("Variable name").fill(key)
        card.get_by_role("button", name="Add", exact=True).click()
        value = page.get_by_placeholder("Enter value...")
        value.wait_for(timeout=10000)
        value.fill("vault:phase4-ref")
        with page.expect_response(
                lambda r: r.url.endswith("/api/env")
                and r.request.method == "PUT") as info:
            page.get_by_role("button", name="Save", exact=True).click()
        assert info.value.status == 200, info.value.status
        assert info.value.json().get("ok") is True, info.value.json()

        code, env = _api(base, token, "GET", "/agent/api/env")
        assert code == 200, (code, env)
        assert env.get(key, {}).get("is_set") is True, env.get(key)

        card.get_by_role("button", name="Clear", exact=True).click()
        dialog = page.get_by_role("alertdialog")
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


def test_system_doctor_action_and_gateway_gating(browser, ui_server):
    """System: a real ops action + the typed supervision gate.

    ``Run doctor`` POSTs ``/api/ops/doctor`` and the canonical
    ``GET /api/actions/doctor/status`` read-back records the executed action.
    The Start/Stop/Restart process controls are disabled and the backend
    confirms the ``GATEWAY_NO_SUPERVISION`` typed gate.
    """
    base, token = ui_server
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    page = ctx.new_page()
    try:
        page.goto(base + "/system", wait_until="domcontentloaded", timeout=30000)
        page.get_by_role("button", name="Run doctor").wait_for(timeout=20000)
        with page.expect_response(
                lambda r: r.url.endswith("/api/ops/doctor")
                and r.request.method == "POST") as info:
            page.get_by_role("button", name="Run doctor").click()
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
        main_text = page.inner_text("main")
        assert "GATEWAY_NO_SUPERVISION" not in main_text
        assert page.get_by_role("button", name="Restart", exact=True).count() == 1
        code, body = _api(base, token, "POST", "/agent/api/gateway/restart")
        assert code == 503 and body.get("code") == \
            "GATEWAY_SUPERVISION_UNAVAILABLE", (code, body)
    finally:
        ctx.close()


def test_devices_probe_action_status(browser, ui_server):
    """Devices: a device Check control runs the shared broker op.

    ``Check acp`` POSTs ``/api/ops/run {id: acp}`` and the canonical
    ``GET /api/actions/acp/status`` read-back records it. Pinned-runtime
    inventory is read from ``GET /api/ops/runtime``.
    """
    base, token = ui_server
    code, runtime = _api(base, token, "GET", "/agent/api/ops/runtime")
    assert code == 200 and runtime.get("ok") is True, runtime

    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    page = ctx.new_page()
    try:
        page.goto(base + "/devices", wait_until="domcontentloaded",
                  timeout=30000)
        page.get_by_label("Check acp", exact=True).wait_for(timeout=20000)
        with page.expect_response(
                lambda r: r.url.endswith("/api/ops/run")
                and r.request.method == "POST") as info:
            page.get_by_label("Check acp", exact=True).click()
        assert info.value.status == 200, info.value.status
        payload = info.value.json()
        # /api/ops/run returns the raw broker envelope (no "name"); the
        # action-status read-back below carries the resolved op id.
        assert payload.get("ok") is True, payload
        assert payload.get("state") == "executed", payload
        assert payload.get("result", {}).get("check", {}).get("state"), payload
        sent = json.loads(info.value.request.post_data)
        assert sent.get("id") == "acp", sent

        status = _poll(lambda: (
            lambda c, d: d if c == 200 and d.get("state") == "executed" else None
        )(*_api(base, token, "GET", "/agent/api/actions/acp/status")))
        assert status and status.get("name") == "acp", status
    finally:
        ctx.close()


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


def test_qubit_chat_mobile_composer_not_squeezed(browser, ui_server):
    """The /qubit-chat composer textarea must not collapse at 390px.

    Before the fix the flex row with Image/Voice/Send buttons squeezed the
    textarea to ~29px wide at 390px. The composer now wraps the action buttons
    below the full-width textarea on mobile while the desktop row is unchanged.
    """
    base, _token = ui_server
    mob = browser.new_context(viewport={"width": 390, "height": 844})
    page = mob.new_page()
    try:
        page.goto(base + "/qubit-chat", wait_until="domcontentloaded",
                  timeout=30000)
        ta = page.get_by_label("Message Qubit")
        ta.wait_for(timeout=20000)
        page.wait_for_timeout(1200)
        box = ta.bounding_box()
        assert box, "composer textarea has no box"
        assert box["width"] >= 200, box   # was ~29px before the fix
        assert box["height"] >= 36, box
    finally:
        mob.close()

    desk = browser.new_context(viewport={"width": 1440, "height": 900})
    page = desk.new_page()
    try:
        page.goto(base + "/qubit-chat", wait_until="domcontentloaded",
                  timeout=30000)
        ta = page.get_by_label("Message Qubit")
        ta.wait_for(timeout=20000)
        page.wait_for_timeout(1000)
        box = ta.bounding_box()
        assert box and box["width"] >= 400, box  # desktop row unchanged
    finally:
        desk.close()
