"""Qubit rebrand wave 2 — fail-closed public-surface scan.

The owner gate: the normal Qubit product must never expose Hermes or OpenClaw
as a product/assistant name, route prefix, URL, title, nav label, or dashboard
path. This scan fails on every upstream-name occurrence on the served/rendered
public surface that is not on the explicit allowlist recorded in
``docs/archive/2026-09-21/QUBIT_REBRAND_ALLOWLIST.md``.

Hermetic HTTP checks against a real local WebUI server; no browser needed.
No skips / xfail except when the dashboard build is absent from the checkout.
"""
from __future__ import annotations

import re
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

DIST = REPO_ROOT / "frontend" / "qubit-dist"

# Internal compatibility / wire-protocol identifiers (not product names) —
# keep in sync with docs/archive/2026-09-21/QUBIT_REBRAND_ALLOWLIST.md.
ALLOWED_PUBLIC_TOKENS = (
    "__HERMES_BASE_PATH__",
    "__HERMES_SESSION_TOKEN__",
    "X-Hermes-Session-Token",
)

# The upstream mount must not exist at all.
UPSTREAM_PATHS = (
    "/hermes",
    "/hermes/",
    "/hermes/chat",
    "/hermes/logs",
    "/hermes/api/status",
    "/hermes/assets/index.js",
    "/hermes/plugins/fleet.json",
    "/hermes/dashboard-plugins/qubit-chat/manifest.json",
    "/hermes/hooks/demo",
    "/hermes/channels/demo",
)

_UPSTREAM = re.compile(r"\bHermes\b|\bOpenClaw\b", re.IGNORECASE)
_URL_ATTR = re.compile(r"""(?:href|src|action)\s*=\s*["']([^"']+)["']""")

# Product-facing phrases forbidden in the built bundle (nav labels, titles).
_BANNED_BUNDLE_PHRASES = (
    "Hermes Agent", "Hermes Console", "Hermes Teal", "Update Hermes",
    "Hermes gateway", "Hermes plugins", "Hermes RPC", "Hermes Desktop",
    "OpenClaw",
    # Company/portal product branding must read Qubit Labs, never Nous.
    "Nous Portal", "Nous Research", "NousResearch", "Nous Blue",
)


def _scan(text: str) -> list[str]:
    for token in ALLOWED_PUBLIC_TOKENS:
        text = text.replace(token, "")
    return [m.group(0) for m in _UPSTREAM.finditer(text)]


def _start(tmp_path):
    from voicebridge.config import Config
    from voicebridge.webui import WebUI

    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    p = tmp_path / "voice.yaml"
    p.write_text(f"logging:\n  jsonl_path: '{state}'\n"
                 f"webui:\n  token: test-token\n  qubit_backend: false\n")
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


def _get(port, path):
    import urllib.error
    import urllib.request

    req = urllib.request.Request(
        "http://127.0.0.1:%d%s" % (port, path), method="GET")
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            return r.status, r.headers.get("Content-Type", ""), r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Content-Type", ""), e.read()


@pytest.fixture
def server(tmp_path):
    ui, port = _start(tmp_path)
    try:
        yield port
    finally:
        ui.stop()


def test_public_route_table_has_no_upstream_names():
    from voicebridge.webui import (
        QUBIT_ROUTE_ALIASES,
        QUBIT_WS_GATE_CODES,
        SPA_ROUTE_PATHS,
    )

    offenders = []
    for route in SPA_ROUTE_PATHS:
        if _UPSTREAM.search(route):
            offenders.append(route)
    for src, tgt in QUBIT_ROUTE_ALIASES.items():
        if _UPSTREAM.search(src) or _UPSTREAM.search(tgt):
            offenders.append(f"{src}->{tgt}")
    for api_path in QUBIT_WS_GATE_CODES:
        if _UPSTREAM.search(api_path):
            offenders.append(api_path)
    assert not offenders, f"upstream names in public route table: {offenders}"
    assert not any(r.startswith("/hermes") for r in SPA_ROUTE_PATHS)


def test_served_shell_for_every_public_route_is_clean(server):
    from voicebridge.webui import SPA_ROUTE_PATHS

    for route in SPA_ROUTE_PATHS:
        status, ctype, body = _get(server, route)
        if status == 404 and b"not built" in body:
            pytest.skip("dashboard build absent in this checkout")
        assert status == 200, route
        assert "text/html" in ctype, (route, ctype)
        html = body.decode("utf-8", "replace")
        hits = _scan(html)
        assert not hits, f"{route}: upstream name on served surface: {hits}"


def test_served_shell_has_no_upstream_url(server):
    status, _, body = _get(server, "/chat")
    if status == 404:
        pytest.skip("dashboard build absent in this checkout")
    html = body.decode("utf-8", "replace")
    bad = [u for u in _URL_ATTR.findall(html) if "hermes" in u.lower()
           or "openclaw" in u.lower()]
    assert not bad, f"served shell points at upstream URLs: {bad}"


def test_dashboard_route_redirects_to_chat_and_never_upstream(server):
    import http.client

    conn = http.client.HTTPConnection("127.0.0.1", server, timeout=8)
    conn.request("GET", "/")
    resp = conn.getresponse()
    loc = resp.getheader("Location")
    status = resp.status
    resp.read()
    conn.close()
    assert status == 302
    assert loc == "/chat", loc
    assert "hermes" not in (loc or "").lower()


@pytest.mark.parametrize("path", UPSTREAM_PATHS)
def test_upstream_paths_are_404_and_clean(server, path):
    status, _, body = _get(server, path)
    assert status == 404, (path, status, body[:120])
    hits = _scan(body.decode("utf-8", "replace"))
    assert not hits, f"{path}: upstream name in 404 body: {hits}"


def test_built_spa_bundle_has_no_product_facing_upstream_names():
    index = DIST / "index.html"
    if not index.is_file():
        pytest.skip("frontend/qubit-dist not built in this checkout")
    hits = []
    for path in sorted((DIST / "assets").glob("*.js")):
        text = path.read_text(encoding="utf-8", errors="replace")
        for token in ALLOWED_PUBLIC_TOKENS:
            text = text.replace(token, "")
        for phrase in _BANNED_BUNDLE_PHRASES:
            if phrase in text:
                hits.append(f"{path.name}:{phrase}")
    assert not hits, f"built SPA still exposes upstream product names: {hits}"
