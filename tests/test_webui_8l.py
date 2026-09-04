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

PAGES = ["/", "/activity", "/reports", "/settings", "/approvals", "/token-setup"]


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


def test_every_dashboard_page_is_served_from_shared_frontend(tmp_path):
    ui, port = _start(tmp_path)
    try:
        for page in PAGES:
            status, ctype, body = _http(port, "GET", page)
            assert status == 200, page
            assert "text/html" in ctype, page
            html = body.decode("utf-8", "replace")
            assert 'id="main-content"' in html, page
            assert 'class="skip-link"' in html, page
            assert '/static/app.css' in html, page
            assert '/static/vb.js' in html, page
            if page != "/token-setup":
                assert '/static/ui.js' in html, page
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


def test_migrated_pages_serve_no_raw_dynamic_html(tmp_path):
    ui, port = _start(tmp_path)
    try:
        for page in ["/activity", "/approvals", "/reports"]:
            status, _, body = _http(port, "GET", page)
            assert status == 200
            html = body.decode("utf-8", "replace")
            assert ".innerHTML =" not in html, page
            assert "fetch(" not in html, page
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