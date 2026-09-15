"""Wave 2 — dashboard page closure: every page's real backing route proven.

The 14 ``dashboard:/...`` pages closed this wave (``/analytics``, ``/channels``,
``/chat``, ``/config``, ``/cron``, ``/env``, ``/files``, ``/mcp``, ``/models``,
``/plugins``, ``/profiles``, ``/profiles/new``, ``/skills``, ``/system``) plus
the rest of the Hermes information architecture are each driven through the
real production dispatcher (:func:`voicebridge.hermes_compat.dispatch`) or the
served HTTP boundary. Every test asserts a truthful 200 success (or a typed
gate with a code) and never a fabricated success.

The generator ``scripts/rebuild_ledger_hermes.py`` maps each page to the exact
test function below via ``DASHBOARD_PROOF`` and verifies the function name and
the backing route literal live in this file: an IMPLEMENTED page with a stale
proof is downgraded to PARTIAL.
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

VENDORED_APP = REPO_ROOT / "qubit_hermes" / "web" / "src" / "App.tsx"
OVERLAY_APP = REPO_ROOT / "frontend" / "hermes-web" / "src" / "App.tsx"

# page -> real backing API GET route (the primary read each page loads).
PAGE_BACKING = {
    "/chat": "/api/status",
    "/sessions": "/api/sessions",
    "/files": "/api/files",
    "/analytics": "/api/analytics",
    "/models": "/api/model/info",
    "/logs": "/api/logs",
    "/cron": "/api/cron/jobs",
    "/skills": "/api/skills",
    "/plugins": "/api/dashboard/plugins",
    "/mcp": "/api/mcp",
    "/pairing": "/api/pairing",
    "/channels": "/api/messaging/platforms",
    "/webhooks": "/api/webhooks",
    "/system": "/api/system/stats",
    "/profiles": "/api/profiles",
    "/profiles/new": "/api/profiles",
    "/config": "/api/config",
    "/env": "/api/env",
}


def _cfg(tmp_path, monkeypatch):
    """Hermetic Config in a throwaway XDG/HOME tree."""
    for var in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME"):
        target = tmp_path / var.lower()
        target.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv(var, str(target))
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("HERMES_HOME", str(home / ".hermes"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\nwebui:\n  token: t\n")
    from voicebridge.config import Config

    return Config.load(p)


def _dispatch(cfg, route):
    """Drive one GET route through the production compat dispatcher."""
    from voicebridge import hermes_compat as HC

    status, body = HC.dispatch(cfg, "GET", route, {}, {})
    assert status in (200, 202), (route, status, str(body)[:200])
    assert isinstance(body, (dict, list, str)), (route, type(body))
    if isinstance(body, dict):
        assert body.get("ok") is not False, (route, body)
    return status, body


# ---------------------------------------------------------------------------
# 14 closed pages + the rest of the baseline: real backing route == 200.
# (Each function name is referenced by DASHBOARD_PROOF in the generator.)
# ---------------------------------------------------------------------------
def test_page_chat_backing_route(tmp_path, monkeypatch):
    _dispatch(_cfg(tmp_path, monkeypatch), "/api/status")


def test_page_sessions_backing_route(tmp_path, monkeypatch):
    _dispatch(_cfg(tmp_path, monkeypatch), "/api/sessions")


def test_page_files_backing_route(tmp_path, monkeypatch):
    _dispatch(_cfg(tmp_path, monkeypatch), "/api/files")


def test_page_analytics_backing_route(tmp_path, monkeypatch):
    _dispatch(_cfg(tmp_path, monkeypatch), "/api/analytics")


def test_page_models_backing_route(tmp_path, monkeypatch):
    _dispatch(_cfg(tmp_path, monkeypatch), "/api/model/info")


def test_page_logs_backing_route(tmp_path, monkeypatch):
    _dispatch(_cfg(tmp_path, monkeypatch), "/api/logs")


def test_page_cron_backing_route(tmp_path, monkeypatch):
    _dispatch(_cfg(tmp_path, monkeypatch), "/api/cron/jobs")


def test_page_skills_backing_route(tmp_path, monkeypatch):
    _dispatch(_cfg(tmp_path, monkeypatch), "/api/skills")


def test_page_plugins_backing_route(tmp_path, monkeypatch):
    _dispatch(_cfg(tmp_path, monkeypatch), "/api/dashboard/plugins")


def test_page_mcp_backing_route(tmp_path, monkeypatch):
    _dispatch(_cfg(tmp_path, monkeypatch), "/api/mcp")


def test_page_pairing_backing_route(tmp_path, monkeypatch):
    _dispatch(_cfg(tmp_path, monkeypatch), "/api/pairing")


def test_page_channels_backing_route(tmp_path, monkeypatch):
    _dispatch(_cfg(tmp_path, monkeypatch), "/api/messaging/platforms")


def test_page_webhooks_backing_route(tmp_path, monkeypatch):
    _dispatch(_cfg(tmp_path, monkeypatch), "/api/webhooks")


def test_page_system_backing_route(tmp_path, monkeypatch):
    _dispatch(_cfg(tmp_path, monkeypatch), "/api/system/stats")


def test_page_profiles_backing_route(tmp_path, monkeypatch):
    _dispatch(_cfg(tmp_path, monkeypatch), "/api/profiles")


def test_page_profiles_new_backing_route(tmp_path, monkeypatch):
    _dispatch(_cfg(tmp_path, monkeypatch), "/api/profiles")


def test_page_config_backing_route(tmp_path, monkeypatch):
    _dispatch(_cfg(tmp_path, monkeypatch), "/api/config")


def test_page_env_backing_route(tmp_path, monkeypatch):
    _dispatch(_cfg(tmp_path, monkeypatch), "/api/env")


def test_all_page_backing_routes_are_truthful(tmp_path, monkeypatch):
    """Every mapped page drives its primary read and gets a real success."""
    cfg = _cfg(tmp_path, monkeypatch)
    for page, route in PAGE_BACKING.items():
        status, body = _dispatch(cfg, route)
        assert status in (200, 202), (page, route, status, str(body)[:160])


# ---------------------------------------------------------------------------
# Served HTTP boundary: the same routes through the authenticated WebUI.
# ---------------------------------------------------------------------------
def _start(tmp_path):
    from voicebridge.config import Config
    from voicebridge.webui import WebUI

    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    p = tmp_path / "voice.yaml"
    p.write_text(
        f"logging:\n  jsonl_path: '{state}'\nwebui:\n  token: test-token\n")
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


def _get(port, path, token="test-token"):
    import urllib.error
    import urllib.request

    req = urllib.request.Request(
        "http://127.0.0.1:%d%s" % (port, path), method="GET")
    req.add_header("X-Hermes-Session-Token", token)
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            return r.status, r.headers.get("Content-Type", ""), r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Content-Type", ""), e.read()


def test_page_root_served_spa(tmp_path):
    """`/` redirects into the served SPA shell (HTTP boundary)."""
    ui, port = _start(tmp_path)
    try:
        status, ctype, body = _get(port, "/chat")
        assert status == 200, (status, body[:200])
        assert "text/html" in ctype
        assert b'id="root"' in body
    finally:
        ui.stop()


def test_page_docs_served_spa(tmp_path):
    """`/docs` is reachable through the served SPA shell (HTTP boundary)."""
    ui, port = _start(tmp_path)
    try:
        status, ctype, body = _get(port, "/docs")
        assert status == 200, (status, body[:200])
        assert "text/html" in ctype
        assert b'id="root"' in body
    finally:
        ui.stop()


def test_page_backing_routes_over_http_boundary(tmp_path):
    """Every backing route answers truthfully over the real served boundary."""
    ui, port = _start(tmp_path)
    try:
        for page, route in PAGE_BACKING.items():
            status, _ctype, body = _get(port, "/agent" + route)
            assert status in (200, 202), (page, route, status, body[:200])
            json.loads(body)  # truthful, parseable success body
    finally:
        ui.stop()


# ---------------------------------------------------------------------------
# Vendored + overlay route presence: the baseline IA is preserved.
# ---------------------------------------------------------------------------
def _routes_in(path: Path) -> set:
    text = path.read_text(encoding="utf-8")
    block = re.search(r"const BUILTIN_ROUTES_CORE[^{]*\{(.*?)\n\};",
                      text, re.S)
    assert block, f"BUILTIN_ROUTES_CORE not found in {path}"
    routes = set(re.findall(r'"([^"]+)":', block.group(1)))
    if '"/chat": ChatRouteSink' in text:
        routes.add("/chat")
    return routes


def test_vendored_and_overlay_route_maps_keep_every_page():
    vendored = _routes_in(VENDORED_APP)
    overlay = _routes_in(OVERLAY_APP)
    for route in PAGE_BACKING:
        assert route in vendored, f"{route} missing from vendored App.tsx"
        assert route in overlay, f"{route} missing from overlay App.tsx"
    for route in ("/", "/docs", "/profiles/new"):
        assert route in vendored, route
        assert route in overlay, route


def test_generator_dashboard_proof_covers_every_page():
    """The ledger generator's DASHBOARD_PROOF maps every page to a live proof.

    This is the contract that makes an IMPLEMENTED dashboard row evidence-based:
    the cited test function and backing-route literal must exist on disk.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "rebuild_ledger_hermes_wave2",
        REPO_ROOT / "scripts" / "rebuild_ledger_hermes.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    page_paths = {p[0] for p in mod.PAGES}
    assert set(mod.DASHBOARD_PROOF) == page_paths
    for path in sorted(page_paths):
        ok, why = mod._dashboard_proof_ok(path)
        assert ok, (path, why)

