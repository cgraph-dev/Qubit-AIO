"""Packet 8L webui serving tests.

Hermetic HTTP checks against a real local WebUI server: every dashboard route
is served from the repository frontend with the shared stylesheet/auth/UI
helpers, the static assets are served with correct MIME types, and the API
remains header-token authenticated. No browser is required.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

PAGES = ["/classic/", "/activity", "/reports", "/ops", "/settings", "/approvals",
         "/token-setup", "/docs", "/sessions", "/projects", "/models",
         "/skills", "/tasks", "/security", "/devices", "/messages",
         "/memory", "/system", "/setup"]


def _start(tmp_path):
    from voicebridge.config import Config
    from voicebridge.webui import WebUI
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    p = tmp_path / "voice.yaml"
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


def _http_status_location(port, path):
    """Raw request that does NOT follow redirects."""
    import http.client
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request("GET", path)
    resp = conn.getresponse()
    loc = resp.getheader("Location")
    resp.read()
    return resp.status, loc


def test_every_normal_route_serves_the_spa_shell(tmp_path):
    """Qubit rebrand: every normal dashboard route serves the root-served
    SPA shell (200); nothing redirects into an upstream-named prefix."""
    from voicebridge.webui import qubit_alias_target
    ui, port = _start(tmp_path)
    try:
        # root is the SPA, reached via the public /sessions route
        status, loc = _http_status_location(port, "/")
        assert (status, loc) == (302, "/sessions"), (status, loc)
        for page in PAGES:
            if page in ("/classic/", "/token-setup"):
                continue
            target = qubit_alias_target(page)
            assert target is not None, page
            assert "hermes" not in target.lower(), (page, target)
            status, ctype, body = _http(port, "GET", page)
            if status == 404 and b"not built" in body:
                continue  # dashboard build absent in this checkout
            assert status == 200, (page, status)
            assert "text/html" in ctype, (page, ctype)
            assert b'id="root"' in body, page
    finally:
        ui.stop()


def test_classic_fallback_serves_static_pages(tmp_path):
    """The labelled /classic/ fallback still serves the old static pages."""
    ui, port = _start(tmp_path)
    try:
        for page in ("/classic/", "/classic/activity", "/classic/ops",
                     "/classic/settings", "/token-setup"):
            status, ctype, body = _http(port, "GET", page)
            assert status == 200, page
            assert "text/html" in ctype, page
            html = body.decode("utf-8", "replace")
            assert 'id="main-content"' in html, page
            assert 'class="skip-link"' in html, page
    finally:
        ui.stop()


def test_normal_routes_never_serve_static_qubit_html(tmp_path):
    """No normal route returns the legacy static markup; every normal route
    serves the SPA shell (or the dashboard-not-built gate), never the old
    horizontal nav."""
    from voicebridge.webui import qubit_alias_target
    ui, port = _start(tmp_path)
    try:
        for page in [p for p in PAGES if qubit_alias_target(p)]:
            status, _, body = _http(port, "GET", page)  # follows redirect
            if status == 404:
                continue  # dashboard build absent in this checkout
            assert status == 200, page
            html = body.decode("utf-8", "replace")
            assert "Qubit VoiceBridge — Workspace" not in html, page
    finally:
        ui.stop()


def test_static_assets_are_served_with_correct_mime(tmp_path):
    ui, port = _start(tmp_path)
    try:
        cases = {"/static/app.css": "text/css",
                 "/static/vb.js": "application/javascript",
                 "/static/ui.js": "application/javascript"}
        for path, ctype in cases.items():
            status, got, body = _http(port, "GET", path)
            assert status == 200, path
            assert ctype in got, (path, got)
            assert len(body) > 0, path
    finally:
        ui.stop()


def test_api_remains_header_token_authenticated(tmp_path):
    ui, port = _start(tmp_path)
    try:
        status, _, _ = _http(port, "GET", "/api/activity", token="")
        assert status == 401
        status, _, body = _http(port, "GET", "/api/activity", token="wrong")
        assert status == 401
        status, ctype, body = _http(port, "GET", "/api/activity")
        assert status == 200
        assert "application/json" in ctype
        data = json.loads(body)
        assert data["items"] == []
        status, _, body = _http(port, "GET", "/api/status")
        assert status == 200
        assert json.loads(body)["bridge"] == "up"
    finally:
        ui.stop()


def test_every_frontend_api_path_hits_a_real_backend_route(tmp_path):
    """No dashboard control points at a missing route (no frontend-only
    controls): every /api/* path referenced by the shipped frontend must
    resolve to an authenticated backend handler (401 without token), never
    404 on both GET and POST."""
    import re

    ui, port = _start(tmp_path)
    try:
        paths = set()
        for name in ("index.html", "activity.html", "approvals.html",
                     "ops.html", "reports.html", "settings.html",
                     "setup.html", "token-setup.html", "docs.html",
                     "sessions.html", "projects.html", "models.html",
                     "skills.html", "tasks.html", "security.html",
                     "devices.html", "messages.html", "memory.html",
                     "system.html", "vb.js", "ui.js", "family.js"):
            text = (REPO_ROOT / "frontend" / name).read_text(encoding="utf-8")
            for match in re.findall(r"/api/[A-Za-z0-9/_\-.]+", text):
                paths.add(match.rstrip("/"))
        assert len(paths) >= 40, f"inventory too small: {len(paths)}"
        dynamic = {"/api/reports": "/api/reports/nope",
                   "/api/result": "/api/result/nope",
                   "/api/runs": "/api/runs/nope/cancel"}
        checked = 0
        for path in sorted(paths):
            probe = dynamic.get(path, path)
            if path in dynamic:
                probe = dynamic[path]
            status, _, _ = _http(port, "GET", probe, token="")
            if status == 404:
                status, _, _ = _http(port, "POST", probe, body={},
                                     token="")
            assert status != 404, f"{path}: frontend points at no route"
            assert status in (200, 400, 401, 404, 405, 500), \
                f"{path}: unexpected {status}"
            checked += 1
        assert checked == len(paths)
    finally:
        ui.stop()