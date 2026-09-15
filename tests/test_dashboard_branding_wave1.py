"""Wave 1 — dashboard workflow parity, route map, and Qubit rebrand.

Covers the 14 open ``dashboard:/...`` pages from ``docs/QUBIT_GAP_LEDGER.md``
(``/analytics``, ``/channels``, ``/chat``, ``/config``, ``/cron``, ``/env``,
``/files``, ``/mcp``, ``/models``, ``/plugins``, ``/profiles``,
``/profiles/new``, ``/skills``, ``/system``):

1. route presence — each baseline page route exists in the vendored SPA
   route map (``App.tsx`` + ``docs/HERMES_FRONTEND_MAP.json``) and the
   served SPA answers it with the real shell;
2. rebrand — the served shell, the Qubit overlay source, and the patched
   product-surface baseline files expose no unrebranded product-facing
   "Hermes"/"OpenClaw" names (legal/provenance and protocol identifiers
   such as ``X-Hermes-Session-Token`` are explicitly exempt);
3. real backing route — every page's primary operation is driven through
   the production dispatcher / HTTP boundary used by CLI and voice, and
   returns truthful success or a typed-degraded gate (never 404/500 and
   never fabricated success).

Hermetic HTTP checks against a real local WebUI server; no browser needed.
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

WEB_SRC = REPO_ROOT / "frontend" / "hermes-web" / "src"
SHARED_SRC = REPO_ROOT / "frontend" / "hermes-shared" / "src"
INDEX_HTML = REPO_ROOT / "frontend" / "hermes-web" / "index.html"
APP_TSX = WEB_SRC / "App.tsx"
OVERLAY = REPO_ROOT / "frontend" / "hermes-qubit"
FRONTEND_MAP = REPO_ROOT / "docs" / "HERMES_FRONTEND_MAP.json"

# page route -> primary real backing Qubit/Hermes-compat GET route.
PAGE_BACKING = {
    "/analytics": "/api/analytics",
    "/channels": "/api/messaging/platforms",
    "/chat": "/api/status",
    "/config": "/api/config",
    "/cron": "/api/cron/jobs",
    "/env": "/api/env",
    "/files": "/api/files",
    "/mcp": "/api/mcp",
    "/models": "/api/model/info",
    "/plugins": "/api/dashboard/plugins",
    "/profiles": "/api/profiles",
    "/profiles/new": "/api/profiles",
    "/skills": "/api/skills",
    "/system": "/api/system/stats",
}

# Protocol identifiers that legitimately keep the upstream name (wire
# compatibility, not product identity).
PROTOCOL_ALLOWED = {"X-Hermes-Session-Token"}

# Product-surface baseline files rebranded under the owner directive; each
# carries a recorded patch in docs/HERMES_SOURCE_COMPARISON.json.
PATCHED_PRODUCT_FILES = [
    "frontend/hermes-web/index.html",
    "frontend/hermes-web/src/App.tsx",
    "frontend/hermes-web/src/components/HermesConsoleModal.tsx",
    "frontend/hermes-web/src/lib/gatewayClient.ts",
    "frontend/hermes-web/src/pages/ChannelsPage.tsx",
    "frontend/hermes-web/src/pages/ChatPage.tsx",
    "frontend/hermes-web/src/pages/ConfigPage.tsx",
    "frontend/hermes-web/src/pages/McpPage.tsx",
    "frontend/hermes-web/src/pages/PluginsPage.tsx",
    "frontend/hermes-web/src/pages/SkillsPage.tsx",
    "frontend/hermes-web/src/pages/SystemPage.tsx",
    "frontend/hermes-web/src/themes/presets.ts",
    "frontend/hermes-shared/src/json-rpc-gateway.ts",
    "frontend/hermes-shared/src/websocket-url.ts",
]
PATCHED_PRODUCT_FILES += [
    "frontend/hermes-web/src/i18n/%s.ts" % n
    for n in ("af", "ar", "de", "en", "es", "fr", "ga", "hu", "it", "ja",
              "ko", "pt", "ru", "tr", "uk", "zh", "zh-hant")
]

_UPSTREAM_NAME = re.compile(r"\bHermes\b|\bOpenClaw\b")
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_LINE_COMMENT = re.compile(r"(?<!:)//[^\n]*")


def _strip_comments(text: str) -> str:
    """Remove JS/TS block + line comments (keeps ``https://`` URL bodies)."""
    text = _BLOCK_COMMENT.sub("", text)
    return _LINE_COMMENT.sub("", text)


def _product_hits(text: str) -> list[str]:
    stripped = _strip_comments(text)
    for allowed in PROTOCOL_ALLOWED:
        stripped = stripped.replace(allowed, "")
    return [m.group(0) for m in _UPSTREAM_NAME.finditer(stripped)]


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


def _get(port, path, token="test-token", hermes=True):
    import urllib.error
    import urllib.request

    req = urllib.request.Request(
        "http://127.0.0.1:%d%s" % (port, path), method="GET")
    if token:
        req.add_header(
            "X-Hermes-Session-Token" if hermes else "X-Api-Token", token)
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            return r.status, r.headers.get("Content-Type", ""), r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Content-Type", ""), e.read()


# ---------------------------------------------------------------------------
# 1. Route presence in the SPA route map + served shell
# ---------------------------------------------------------------------------
def _app_routes() -> set:
    text = APP_TSX.read_text(encoding="utf-8")
    block = re.search(r"const BUILTIN_ROUTES_CORE[^{]*\{(.*?)\n\};",
                      text, re.S)
    assert block, "BUILTIN_ROUTES_CORE not found in App.tsx"
    routes = set(re.findall(r'"([^"]+)":', block.group(1)))
    # /chat is registered conditionally when embedded chat is enabled.
    if '"/chat": ChatRouteSink' in text:
        routes.add("/chat")
    return routes


def test_baseline_page_routes_in_vendored_spa_route_map():
    app_routes = _app_routes()
    map_routes = set(
        json.loads(FRONTEND_MAP.read_text(encoding="utf-8"))["routes"])
    for route in PAGE_BACKING:
        assert route in app_routes, f"{route} missing from App.tsx route map"
        assert route in map_routes, f"{route} missing from HERMES_FRONTEND_MAP"
    # the baseline must not be a smaller shell: the full upstream surface stays
    for route in ("/sessions", "/logs", "/pairing", "/webhooks", "/docs",
                  "/profiles/new"):
        assert route in app_routes, route
        assert route in map_routes, route


def test_served_spa_answers_every_page_route(tmp_path):
    ui, port = _start(tmp_path)
    try:
        for route in PAGE_BACKING:
            status, ctype, body = _get(port, route)
            assert status == 200, (route, status)
            assert "text/html" in ctype, (route, ctype)
            assert b'id="root"' in body, route
    finally:
        ui.stop()


def test_served_spa_shell_is_qubit_branded(tmp_path):
    ui, port = _start(tmp_path)
    try:
        status, ctype, body = _get(port, "/chat")
        assert status == 200 and "text/html" in ctype
        html = body.decode("utf-8")
        assert "<title>Qubit Agent" in html, html[:200]
        for bad in ("Hermes Agent", "OpenClaw"):
            assert bad not in html, f"served shell still exposes {bad!r}"
    finally:
        ui.stop()


# ---------------------------------------------------------------------------
# 2. Rebrand scan — served output, overlay source, patched baseline
# ---------------------------------------------------------------------------
def test_overlay_source_has_no_product_facing_upstream_names():
    hits = []
    for path in sorted(OVERLAY.rglob("*.js")):
        for token in _product_hits(path.read_text(encoding="utf-8")):
            hits.append(f"{path.relative_to(REPO_ROOT)}:{token}")
    assert not hits, f"Qubit overlay still names upstream product: {hits}"


def test_patched_product_surfaces_have_no_upstream_product_names():
    hits = []
    for rel in PATCHED_PRODUCT_FILES:
        path = REPO_ROOT / rel
        assert path.is_file(), rel
        for token in _product_hits(path.read_text(encoding="utf-8")):
            hits.append(f"{rel}:{token}")
    assert not hits, f"product surface still names upstream: {hits}"


def test_vendored_source_has_no_unrebranded_product_names():
    """Ratchet: outside comments and the exempt protocol header, the vendored
    dashboard source carries no remaining "Hermes"/"OpenClaw" product name."""
    hits = []
    roots = [WEB_SRC, SHARED_SRC]
    for root in roots:
        for path in sorted(root.rglob("*.ts")) + sorted(root.rglob("*.tsx")):
            if ".test." in path.name:
                continue
            for token in _product_hits(path.read_text(encoding="utf-8")):
                hits.append(f"{path.relative_to(REPO_ROOT)}:{token}")
    assert not hits, f"unrebranded product names remain: {hits}"


def test_built_spa_bundle_has_no_product_facing_upstream_names():
    """When the canonical dashboard build is present, the generated SPA shell
    and bundles must be Qubit-branded (skipped on a source-only checkout)."""
    dist = REPO_ROOT / "frontend" / "hermes-dist"
    index = dist / "index.html"
    if not index.is_file():
        pytest.skip("frontend/hermes-dist not built in this checkout")
    html = index.read_text(encoding="utf-8")
    assert "<title>Qubit Agent" in html, html[:200]
    banned = ("Hermes Agent", "Hermes Console", "Hermes Teal",
              "Update Hermes", "Hermes gateway", "Hermes plugins",
              "Hermes RPC", "Hermes Desktop",
              "OpenClaw")
    hits = []
    bundle = ""
    for path in sorted((dist / "assets").glob("*.js")):
        text = path.read_text(encoding="utf-8", errors="replace")
        bundle += text
        for phrase in banned:
            if phrase in text:
                hits.append(f"{path.name}:{phrase}")
    assert not hits, f"built SPA still exposes upstream product names: {hits}"
    # the built route map still owns the full baseline page set
    for route in PAGE_BACKING:
        assert f'"{route}"' in bundle, f"built SPA route map missing {route}"


def test_theme_overlay_hides_any_residual_wordmark():
    """The active Qubit theme hides the vendored wordmark via its theming CSS
    (overlay-only; no vendored behaviour change)."""
    theme = json.loads(
        (OVERLAY / "qubit-theme.json").read_text(encoding="utf-8"))
    css = theme["customCSS"]
    assert "display:none" in css
    assert "text-midground.font-bold" in css
    assert theme["name"] == "qubit"


# ---------------------------------------------------------------------------
# 3. Drive the real backing route for every page (production dispatcher)
# ---------------------------------------------------------------------------
def test_every_page_backing_route_is_truthful(tmp_path):
    from voicebridge import hermes_compat as HC
    from voicebridge.config import Config

    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    p = tmp_path / "voice.yaml"
    p.write_text(f"logging:\n  jsonl_path: '{state}'\nwebui:\n  token: t\n")
    cfg = Config.load(p)

    for page, route in PAGE_BACKING.items():
        status, body = HC.dispatch(cfg, "GET", route, {}, {})
        assert status != 404, (page, route, status, str(body)[:200])
        assert status not in (500, 502, 503), \
            (page, route, status, str(body)[:200])
        if status in (200, 202):
            assert isinstance(body, (dict, list, str)), route
        else:
            # typed-degraded: a real gate with a code + Qubit counterpart,
            # never a silent/blank failure.
            assert isinstance(body, dict), (route, body)
            assert body.get("code"), (route, body)
            assert body.get("qubit_counterpart") or body.get("detail"), \
                (route, body)


def test_page_backing_routes_over_http_boundary(tmp_path):
    """Same routes, driven through the authenticated served HTTP boundary
    (server -> compat dispatcher), proving the dashboard's real path."""
    from voicebridge import hermes_compat as HC

    ui, port = _start(tmp_path)
    try:
        for page, route in PAGE_BACKING.items():
            status, ctype, body = _get(port, "/agent" + route)
            assert status != 404, (page, route, status, body[:200])
            assert status != 500, (page, route, status, body[:200])
            if status in (200, 202):
                json.loads(body)  # truthful, parseable success body
            else:
                payload = json.loads(body)
                assert payload.get("code"), (page, route, payload)
    finally:
        ui.stop()


def test_fork_proxied_payloads_are_rebranded(tmp_path):
    """The fork read-proxy must not surface upstream product names."""
    ui, port = _start(tmp_path)
    try:
        status, _ctype, body = _get(port, "/agent/api/messaging/platforms")
        assert status in (200, 202), (status, body[:200])
        payload = json.loads(body)
        blob = json.dumps(payload)
        assert "Hermes" not in blob and "OpenClaw" not in blob, blob[:400]
        assert payload.get("gateway_start_command") == "qubit gateway start", \
            payload
    finally:
        ui.stop()


def test_unknown_backing_route_is_typed_not_500(tmp_path):
    from voicebridge import hermes_compat as HC
    from voicebridge.config import Config

    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    p = tmp_path / "voice.yaml"
    p.write_text(f"logging:\n  jsonl_path: '{state}'\nwebui:\n  token: t\n")
    cfg = Config.load(p)
    code, out = HC.dispatch(cfg, "GET", "/api/no-such-wave1-route", {}, {})
    assert code == 404
    assert out.get("code") == "NOT_FOUND", out
