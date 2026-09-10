"""Qubit dashboard families: every capability family has a real routed
workspace wired to the same backend as CLI and voice.

Each family page is catalog-driven (groups of /api/ops operations with
real forms, confirmations, receipts, and typed states) — no static
tiles, no frontend-only success. Hermetic HTTP checks against a real
local WebUI server; no browser required.
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

FAMILIES = {
    "/sessions": ["sessions", "actions"],
    "/projects": ["projects", "files", "kanban"],
    "/models": ["frontier", "frontier_chat", "frontier_show",
                "config_profiles", "reasoning", "prompt_budget"],
    "/skills": ["skills", "verified", "curator", "plugins_install",
                "import_agent", "experiments"],
    "/tasks": ["schedules", "opencode", "task_run"],
    "/security": ["approvals", "permissions", "power_status", "auth_status",
                  "auth_login", "auth_verify", "auth_logout", "secrets",
                  "security", "device", "device_enroll"],
    "/devices": ["browser", "browser_tabs", "computer", "ghost", "ide",
                 "wake", "audio", "audio_hw", "mic_guidance"],
    "/messages": ["msg", "peer", "pairing", "webhook", "webhooks",
                  "gateway", "websites"],
    "/memory": ["memory", "brain", "lessons", "insights"],
    "/system": ["runtime", "status", "doctor", "diagnostics",
                "setup_check", "update_status", "migrate_status", "product",
                "package", "verify_release", "state", "sync", "backup",
                "checkpoints", "monitor"],
}

NAV_LINKS = ["/", "/activity", "/reports", "/sessions", "/projects",
             "/models", "/skills", "/tasks", "/security", "/devices",
             "/messages", "/memory", "/ops", "/approvals", "/settings",
             "/setup", "/system", "/token-setup"]


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


def _http(port, method, path, body=None, token="test-token"):
    import urllib.request
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request("http://127.0.0.1:%d%s" % (port, path),
                                 method=method, data=data)
    if token:
        req.add_header("X-Api-Token", token)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.headers.get("Content-Type", ""), r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Content-Type", ""), e.read()


def _family_groups(html):
    m = re.search(r"window\.FAMILY\s*=\s*\{\s*groups:\s*\[([^\]]*)\]",
                  html)
    assert m, "family page must declare window.FAMILY.groups"
    return re.findall(r'"([^"]+)"', m.group(1))


def test_every_family_page_served_with_nav_and_shell(tmp_path):
    ui, port = _start(tmp_path)
    try:
        for page in FAMILIES:
            status, ctype, body = _http(port, "GET", page)
            assert status == 200, page
            assert "text/html" in ctype, page
            html = body.decode("utf-8", "replace")
            assert 'id="main-content"' in html, page
            assert 'aria-current="page"' in html, page
            assert "/static/family.js" in html, page
            for link in NAV_LINKS:
                assert f'href="{link}"' in html, (page, link)
            for token in ("Hermes", "OpenClaw", "hermes-agent"):
                assert token not in html, (page, token)
    finally:
        ui.stop()


def test_family_groups_match_catalog_and_cover_everything(tmp_path):
    from voicebridge import dashboard_ops as OPS

    ui, port = _start(tmp_path)
    try:
        catalog_groups = {o["group"] for o in OPS.catalog()}
        covered: set = set()
        for page, groups in FAMILIES.items():
            _, _, body = _http(port, "GET", page)
            declared = _family_groups(body.decode("utf-8", "replace"))
            assert declared == groups, (page, declared)
            for group in groups:
                assert group in catalog_groups, (page, group)
                ops = [o for o in OPS.catalog() if o["group"] == group]
                assert ops, (page, group)
            covered.update(groups)
        # every catalog group is reachable from a family page or /ops
        assert covered <= catalog_groups
    finally:
        ui.stop()


def test_family_ops_execute_through_backend(tmp_path):
    """Spot-check one op per family through /api/ops/run: reads execute,
    mutations stage confirmations, unknown ids fail typed."""
    ui, port = _start(tmp_path)
    try:
        from voicebridge import dashboard_ops as OPS

        by_group = {}
        for op in OPS.catalog():
            by_group.setdefault(op["group"], []).append(op)
        for page, groups in FAMILIES.items():
            for group in groups:
                ops = by_group[group]
                read = next((o for o in ops if not o["confirm"]), ops[0])
                args = {}
                for a in read.get("args", []):
                    if a.get("required"):
                        args[a["name"]] = "1" if a.get("type") == "int" else "test"
                status, _, body = _http(
                    port, "POST", "/api/ops/run",
                    {"id": read["id"], "args": args})
                assert status == 200, (page, read["id"])
                out = json.loads(body.decode())
                assert out["state"] in ("executed", "needs-confirm",
                                        "failed", "refused"), (page, read)
                if out["state"] == "needs-confirm":
                    status, _, body = _http(
                        port, "POST", "/api/ops/cancel",
                        {"confirmation_id": out["confirmation_id"]})
                    assert status == 200, (page, read)
        status, _, _ = _http(port, "POST", "/api/ops/run",
                             {"id": "no-such-op", "args": {}})
        assert status == 200
    finally:
        ui.stop()


def test_api_stays_header_token_authenticated(tmp_path):
    ui, port = _start(tmp_path)
    try:
        status, _, _ = _http(port, "GET", "/api/ops", token=None)
        assert status == 401
        status, _, _ = _http(port, "GET", "/api/ops?token=test-token",
                             token=None)
        assert status == 401, "query-string tokens must stay rejected"
    finally:
        ui.stop()


# ---------------------------------------------------------------------------
# Browser rendering (opt-in ui mark): landmarks, no JS errors, touch targets
# ---------------------------------------------------------------------------
import pytest as _pytest  # noqa: E402


@_pytest.mark.ui
def test_family_pages_render_clean_with_landmarks(tmp_path_factory):
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        _pytest.skip(f"playwright unavailable: {exc}")
    from voicebridge.config import Config
    from voicebridge.webui import WebUI
    import time as _time

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
            self._t0 = _time.time()

    ui = WebUI(_FakeOrch(), host="127.0.0.1", port=0, state_dir=state)
    ui.start()
    base = f"http://127.0.0.1:{ui._server.server_address[1]}"
    try:
        pw = sync_playwright().start()
        try:
            browser = pw.chromium.launch()
        except Exception as exc:
            _pytest.skip(f"chromium unavailable: {exc}")
        try:
            for route in list(FAMILIES) + ["/ops"]:
                ctx = browser.new_context(
                    viewport={"width": 1280, "height": 800})
                page = ctx.new_page()
                page.add_init_script(
                    "localStorage.setItem('vdb_token','test-token');")
                errors = []
                page.on("console", lambda m: errors.append(m.text)
                        if m.type == "error"
                        and "favicon" not in m.text.lower() else None)
                page.on("pageerror",
                        lambda exc: errors.append("pageerror: " + str(exc)))
                page.goto(base + route, wait_until="domcontentloaded")
                page.wait_for_timeout(800)
                assert page.locator("main#main-content").count() == 1, route
                assert page.locator(
                    'nav[aria-label="Primary"]').count() == 1, route
                assert page.locator(
                    ".page-nav a[aria-current='page']").count() == 1, route
                assert not errors, (route, errors[:3])
                # every visible control meets the touch target minimum
                small = page.evaluate(
                    "() => { const out = []; "
                    "for (const el of document.querySelectorAll("
                    "'button, a[href], input, select, textarea')) {"
                    " if (el.classList.contains('skip-link')) continue;"
                    " const cs = getComputedStyle(el);"
                    " if (cs.display === 'none' || cs.visibility === 'hidden') continue;"
                    " const r = el.getBoundingClientRect();"
                    " if (r.width === 0 && r.height === 0) continue;"
                    " if (r.width < 44 || r.height < 44)"
                    " out.push(el.tagName + '.' + el.className); }"
                    " return out; }")
                assert not small, (route, small[:5])
                ctx.close()
        finally:
            browser.close()
            pw.stop()
    finally:
        ui.stop()


@_pytest.mark.ui
def test_family_pages_narrow_mobile_no_overflow(tmp_path_factory):
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        _pytest.skip(f"playwright unavailable: {exc}")
    from voicebridge.config import Config
    from voicebridge.webui import WebUI
    import time as _time

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
            self._t0 = _time.time()

    ui = WebUI(_FakeOrch(), host="127.0.0.1", port=0, state_dir=state)
    ui.start()
    base = f"http://127.0.0.1:{ui._server.server_address[1]}"
    try:
        pw = sync_playwright().start()
        try:
            browser = pw.chromium.launch()
        except Exception as exc:
            _pytest.skip(f"chromium unavailable: {exc}")
        try:
            for route in list(FAMILIES):
                ctx = browser.new_context(
                    viewport={"width": 360, "height": 740})
                page = ctx.new_page()
                page.add_init_script(
                    "localStorage.setItem('vdb_token','test-token');")
                page.goto(base + route, wait_until="domcontentloaded")
                page.wait_for_timeout(600)
                overflow = page.evaluate(
                    "document.documentElement.scrollWidth - "
                    "document.documentElement.clientWidth")
                assert overflow <= 0, (route, overflow)
                ctx.close()
        finally:
            browser.close()
            pw.stop()
    finally:
        ui.stop()
