"""9u — System/Gateway/Update + Skills/MCP/Plugins control audit (backend).

Read-only control audit for the dashboard controls named in the 9u task. Every
case drives the production compatibility dispatcher
(:func:`voicebridge.qubit_compat.dispatch`) in a hermetic XDG/HOME tree — no
browser, no live host service. The gateway lifecycle POSTs are expected to
answer the supervisor's typed 503 ``GATEWAY_SUPERVISION_UNAVAILABLE`` test-guard
refusal (``voicebridge/gateway_supervisor.py:_real_control_disabled``), never a
real service transition.

Nothing here is a completion claim: the route resolving and the wire shape
matching is necessary, not sufficient, for the end-to-end behavior the page
coverage artifact still records as OPEN.
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

DIST = REPO_ROOT / "frontend" / "qubit-dist"

# Every route this audit must resolve to a real, non-404 handler.
READ_ROUTES = [
    ("GET", "/api/status", "h_status"),
    ("GET", "/api/gateway", "h_gateway"),
    ("GET", "/api/qubit/update/check", "h_qubit_update_check"),
    ("GET", "/api/skills", "h_skills"),
    ("GET", "/api/mcp/servers", "h_mcp_servers"),
    ("GET", "/api/dashboard/plugins", "h_plugins"),
]
CONTROL_ROUTES = [
    ("POST", "/api/gateway/start", "h_gateway_start"),
    ("POST", "/api/gateway/stop", "h_gateway_stop"),
    ("POST", "/api/gateway/restart", "h_gateway_restart"),
]


def _cfg(tmp_path, monkeypatch):
    """Hermetic Config in a throwaway XDG/HOME tree (no operator state)."""
    for var in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME"):
        target = tmp_path / var.lower()
        target.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv(var, str(target))
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("QUBIT_HOME", str(home / ".hermes"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\nwebui:\n  token: t\n")
    from voicebridge.config import Config

    return Config.load(p)


def _dispatch(cfg, method, path, body=None, query=None):
    from voicebridge import qubit_compat as HC

    return HC.dispatch(cfg, method, path, query or {}, body or {})


def _matched(method, path):
    from voicebridge import qubit_compat as HC

    return HC.match(method, path)[0], HC


# ---------------------------------------------------------------------------
# Route registration: each control resolves to a real handler, not a stub.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("method,path,handler", READ_ROUTES)
def test_read_route_resolves_to_real_handler(method, path, handler):
    route, HC = _matched(method, path)
    assert route is not None, f"{method} {path} is not registered (404)"
    assert route["disposition"] == "implemented", route
    assert route["handler"] == handler, route
    assert callable(HC.HANDLERS.get(handler)), f"{handler} missing from HANDLERS"


@pytest.mark.parametrize("method,path,handler", CONTROL_ROUTES)
def test_gateway_control_route_is_real_not_the_old_gate(method, path, handler):
    route, HC = _matched(method, path)
    assert route is not None, f"{method} {path} is not registered (404)"
    # The old hard-coded refusal was the GATEWAY_NO_SUPERVISION typed gate; the
    # control now routes to the real supervisor handler.
    assert route["disposition"] == "implemented", route
    assert route["handler"] == handler, route
    assert route.get("gate") in ({}, None), route
    assert callable(HC.HANDLERS.get(handler)), f"{handler} missing from HANDLERS"


def test_update_apply_route_is_registered_never_404():
    """POST /api/qubit/update must resolve, never 404.

    Two known states are accepted because the update_manager wiring is an
    in-flight item: the interim typed `UPDATE_OPERATOR_OWNED` gate, or the real
    `h_qubit_update_apply` handler. Anything else (or no route) is a bug.
    """
    route, _HC = _matched("POST", "/api/qubit/update")
    assert route is not None, "POST /api/qubit/update is not registered"
    assert route["handler"] in ("__gate__", "h_qubit_update_apply"), route
    if route["disposition"] == "gated":
        assert route["gate"]["code"] == "UPDATE_OPERATOR_OWNED", route


# ---------------------------------------------------------------------------
# GET /api/status — gateway fields come from the real supervisor.
# ---------------------------------------------------------------------------
def test_status_carries_gateway_fields(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    status, body = _dispatch(cfg, "GET", "/api/status")
    assert status == 200 and body.get("ok") is True, (status, body)
    assert body.get("schema") == "qubit-runtime-status/1", body.get("schema")
    for key in ("gateway_running", "gateway_state", "gateway_pid",
                "gateway_service_unit", "gateway_detail"):
        assert key in body, key
    assert isinstance(body["gateway_running"], bool), body["gateway_running"]
    assert isinstance(body["gateway_state"], str), body["gateway_state"]
    # The unit is the Qubit-owned service, never an external gateway name.
    unit = body.get("gateway_service_unit") or ""
    assert unit == "" or "qubit" in unit, unit


# ---------------------------------------------------------------------------
# GET /api/gateway — real supervision block (empty signal or typed reason).
# ---------------------------------------------------------------------------
def test_gateway_read_is_real_supervision_not_a_stub(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    status, body = _dispatch(cfg, "GET", "/api/gateway")
    assert status == 200 and body.get("ok") is True, (status, body)
    for key in ("gateway", "state", "supervision", "process"):
        assert key in body, key
    process = body["process"]
    for key in ("state", "service_unit", "scope", "host", "port",
                "available", "running", "active_state", "sub_state",
                "port_in_use", "detail"):
        assert key in process, (key, process)
    assert isinstance(process["available"], bool), process
    assert isinstance(process["running"], bool), process
    supervision = body["supervision"]
    assert isinstance(supervision, str), supervision
    # Empty when the supervisor is available; otherwise the typed reason from
    # the real module (voicebridge.gateway_supervisor.UNAVAILABLE_CODE).
    assert supervision == "" or supervision.startswith(
        "GATEWAY_SUPERVISION_UNAVAILABLE"), supervision


@pytest.mark.parametrize("action", ["start", "stop", "restart"])
def test_gateway_control_returns_typed_test_guard_refusal(action,
                                                          tmp_path, monkeypatch):
    """The control is real and confirm-gated; in a test process the supervisor
    refuses (touching no host service) only after confirmation."""
    cfg = _cfg(tmp_path, monkeypatch)
    # Unconfirmed: a real single-use approval is staged (never the old gate).
    status, body = _dispatch(cfg, "POST", f"/api/gateway/{action}")
    assert status == 409, (action, status, body)
    assert body.get("ok") is False, body
    assert body.get("code") == "GATEWAY_CONFIRM_REQUIRED", body
    # The old unconditional gate code must not be what the control answers.
    assert body.get("code") != "GATEWAY_NO_SUPERVISION", body

    # Confirmed: the supervisor runs and the test guard refuses host control.
    status, body = _dispatch(cfg, "POST", f"/api/gateway/{action}",
                             {"confirm": True})
    assert status == 503, (action, status, body)
    assert body.get("ok") is False, body
    assert body.get("code") == "GATEWAY_SUPERVISION_UNAVAILABLE", body
    assert body.get("state") == "UNAVAILABLE", body
    assert body.get("detail"), body


# ---------------------------------------------------------------------------
# Update check / apply.
# ---------------------------------------------------------------------------
def test_update_check_is_a_real_read_shape(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    status, body = _dispatch(cfg, "GET", "/api/qubit/update/check")
    assert status == 200 and body.get("ok") is True, (status, body)
    for key in ("install_method", "current_version", "behind",
                "update_available", "can_apply", "update_command", "message"):
        assert key in body, (key, body)
    assert body["behind"] is None, body  # unknown, not a fabricated "latest"
    assert body["can_apply"] is False, body
    assert body["install_method"] in ("git-checkout", "package"), body


def test_update_apply_never_fabricates_success(tmp_path, monkeypatch):
    """The apply path is the typed gate today, or a real handler that stages.

    The request is sent **without** ``confirm`` so no update command can run.
    The only allowed interim answers are the typed `UPDATE_OPERATOR_OWNED` gate
    (403) or the real `h_qubit_update_apply` stage (409 `CONFIRM_REQUIRED`,
    since ``update_manager.apply(confirm=False)`` refuses before any git call).
    """
    route, HC = _matched("POST", "/api/qubit/update")
    assert route is not None, "POST /api/qubit/update is not registered"
    cfg = _cfg(tmp_path, monkeypatch)
    if route["disposition"] == "gated":
        status, body = _dispatch(cfg, "POST", "/api/qubit/update")
        assert status == 403 and body.get("code") == "UPDATE_OPERATOR_OWNED", \
            (status, body)
        return
    handler = route["handler"]
    if not callable(HC.HANDLERS.get(handler)):
        pytest.xfail(
            f"route POST /api/qubit/update maps to {handler!r} but it is not "
            "registered in HANDLERS yet (update_manager wiring in flight); "
            "the handler exists at qubit_compat.py and returns 409 for an "
            "unconfirmed apply once registered")
    status, body = _dispatch(cfg, "POST", "/api/qubit/update")
    assert status != 200, (status, body)
    assert body.get("ok") is False, body
    assert status in (409, 403, 503), (status, body)


def test_update_apply_routes_to_update_manager():
    """POST /api/qubit/update maps to the real update_manager apply handler.

    If a future revision reverts this to the blanket `_NO_UPDATE` gate (or the
    handler stops being registered), this test fails rather than silently
    passing.
    """
    route, HC = _matched("POST", "/api/qubit/update")
    assert route is not None, "POST /api/qubit/update is not registered"
    assert route["disposition"] == "implemented", route
    assert route["handler"] == "h_qubit_update_apply", route
    assert callable(HC.HANDLERS.get("h_qubit_update_apply")), route


# ---------------------------------------------------------------------------
# GET /api/skills, /api/mcp/servers, /api/dashboard/plugins — wire shapes.
# ---------------------------------------------------------------------------
def test_skills_read_returns_skillinfo_list(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    status, body = _dispatch(cfg, "GET", "/api/skills")
    assert status == 200, (status, body)
    # SkillInfo[]: a bare JSON array (api.getSkills), empty is typed-empty.
    assert isinstance(body, list), type(body)
    for entry in body:
        assert isinstance(entry, dict), entry
        assert "name" in entry, entry
        assert "enabled" in entry, entry


def test_mcp_servers_read_returns_full_mcpserver_shape(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    status, body = _dispatch(cfg, "GET", "/api/mcp/servers")
    assert status == 200 and body.get("ok") is True, (status, body)
    for key in ("servers", "state", "mount_policy", "detail"):
        assert key in body, (key, body)
    assert isinstance(body["servers"], list), body["servers"]
    for server in body["servers"]:
        for key in ("name", "transport", "enabled", "policy", "source"):
            assert key in server, (key, server)
        assert server["transport"] in ("http", "stdio", "unknown"), server


def test_plugins_read_returns_manifest_array_with_owned_bundles(tmp_path,
                                                                monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    status, body = _dispatch(cfg, "GET", "/api/dashboard/plugins")
    assert status == 200, (status, body)
    # PluginManifestResponse[]: a bare JSON array (api.getPlugins).
    assert isinstance(body, list) and body, type(body)
    names = set()
    for manifest in body:
        for key in ("name", "label", "tab", "entry", "source"):
            assert key in manifest, (key, manifest)
        names.add(manifest["name"])
    # The Qubit-owned overlay bundles the served backend must include.
    assert {"qubit-brand", "qubit-reports"} <= names, sorted(names)


# ---------------------------------------------------------------------------
# Regression: the SPA must recognize the supervisor's real refusal token.
# (Fixed: SystemPage.tsx and App.tsx now treat a non-empty supervision string
# — including GATEWAY_SUPERVISION_UNAVAILABLE — as a disabled control.)
# ---------------------------------------------------------------------------
def test_gateway_refusal_token_agrees_between_spa_and_backend():
    backend = (REPO_ROOT / "voicebridge" / "qubit_compat.py").read_text(
        encoding="utf-8")
    system = (REPO_ROOT / "frontend" / "qubit-web" / "src" / "pages"
              / "SystemPage.tsx").read_text(encoding="utf-8")
    assert "GS.UNAVAILABLE_CODE" in backend
    assert "GATEWAY_SUPERVISION_UNAVAILABLE" in system, \
        "SPA does not recognize the supervisor's real refusal code"


# ---------------------------------------------------------------------------
# Optional browser check (opt-in: -m ui). Hermetic WebUI + built SPA.
# ---------------------------------------------------------------------------
@pytest.mark.ui
@pytest.mark.skipif(
    not (DIST / "index.html").is_file(),
    reason="frontend/qubit-dist not built (npm build step)",
)
def test_gateway_card_browser(tmp_path):
    """Drive the real System page and the real Gateway backend over HTTP.

    The server runs in the pytest process, so the supervisor's test guard
    answers the control POST with the typed 503; the page itself never renders
    the old GATEWAY_NO_SUPERVISION refusal text.
    """
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from voicebridge.config import Config
    from voicebridge.webui import WebUI

    state = tmp_path / "state"
    state.mkdir()
    cfg_path = tmp_path / "voice.yaml"
    cfg_path.write_text(
        f"logging:\n  jsonl_path: '{state}'\nwebui:\n  token: controls9u-token\n")
    cfg = Config.load(cfg_path)

    class _FakeOrch:
        _route = "local"
        _web_active = 0
        task = type("T", (), {"active": False})()

        def __init__(self):
            self.cfg = cfg
            self._t0 = 0.0

    ui = WebUI(_FakeOrch(), host="127.0.0.1", port=0, state_dir=state)
    ui.start()
    base = f"http://127.0.0.1:{ui._server.server_address[1]}"
    token = ui.api_token

    def api(method, path):
        req = urllib.request.Request(
            base + path, method=method,
            headers={"X-Qubit-Session-Token": token,
                     "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                return r.status, json.loads(r.read().decode() or "{}")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode() or "{}")

    try:
        code, gw = api("GET", "/agent/api/gateway")
        assert code == 200 and "supervision" in gw, (code, gw)
        code, ctrl = api("POST", "/agent/api/gateway/restart")
        assert code == 503, (code, ctrl)
        assert ctrl.get("code") == "GATEWAY_SUPERVISION_UNAVAILABLE", ctrl

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                page = browser.new_page(viewport={"width": 1440,
                                                  "height": 900})
                page.goto(base + "/system", wait_until="domcontentloaded",
                          timeout=30000)
                page.get_by_role("heading", name="System").first.wait_for(
                    timeout=20000)
                card = page.get_by_role("heading", name="Gateway").locator(
                    "xpath=ancestor::section[1]")
                card.get_by_role("button", name="Restart",
                                 exact=True).wait_for(timeout=10000)
                assert card.get_by_role("button", name="Start",
                                        exact=True).count() == 1
                assert card.get_by_role("button", name="Stop",
                                        exact=True).count() == 1
                assert "GATEWAY_NO_SUPERVISION" not in card.inner_text()
            finally:
                browser.close()
    finally:
        ui.stop()
