"""Live serving tests for the vendored dashboard on Qubit-owned public paths.

Boots the real Qubit WebUI on loopback and proves:
- /chat (and every root client route) serves the built dashboard shell with
  token + API-prefix injection; no /hermes/ path is served;
- /agent/api/* compat endpoints answer with the Qubit token header
  (X-Qubit-Session-Token) and reject without it;
- overlay plugin bundles + manifests are served at /dashboard-plugins/*;
- SPA deep links fall back to the shell;
- the classic Qubit pages still serve (no regression).
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
needs_dist = pytest.mark.skipif(
    not (DIST / "index.html").is_file(),
    reason="hermes-dist not built (npm build step)")


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


def _http(port, method, path, body=None, token="test-token",
          qubit_token=False):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request("http://127.0.0.1:%d%s" % (port, path),
                                 method=method, data=data)
    if token and qubit_token:
        req.add_header("X-Qubit-Session-Token", token)
    elif token:
        req.add_header("X-Api-Token", token)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.headers.get("Content-Type", ""), r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Content-Type", ""), e.read()


@needs_dist
def test_qubit_shell_serves_with_injection(tmp_path):
    ui, port = _start(tmp_path)
    try:
        status, ctype, body = _http(port, "GET", "/chat")
        assert status == 200, status
        assert "text/html" in ctype
        text = body.decode()
        assert "__QUBIT_BASE_PATH__" in text and "__QUBIT_API_PREFIX__" in text
        assert "/agent" in text and "/hermes/" not in text
        assert "__QUBIT_SESSION_TOKEN__" in text
        assert "Hermes" in text or "hermes" in text or "assets/" in text
    finally:
        ui.stop()


@needs_dist
def test_qubit_spa_deep_link_falls_back(tmp_path):
    ui, port = _start(tmp_path)
    try:
        for deep in ("/sessions", "/skills", "/plugins"):
            status, _, body = _http(port, "GET", deep)
            assert status == 200, (deep, status)
            assert b"__QUBIT_BASE_PATH__" in body, deep
    finally:
        ui.stop()


@needs_dist
def test_qubit_api_auth_and_status(tmp_path):
    ui, port = _start(tmp_path)
    try:
        status, _, _ = _http(port, "GET", "/agent/api/status", token=None)
        assert status == 401, status
        status, ctype, body = _http(port, "GET", "/agent/api/status",
                                    qubit_token=True)
        assert status == 200, (status, body[:200])
        out = json.loads(body)
        # /api/status is a QUBIT_READ_AUTHORITY path, so the SPA prefix serves
        # the Qubit compat h_status document (schema "qubit-runtime-status/1"),
        # not the fork proxy's status. Pinned by
        # tests/test_dashboard_controls9u.py::test_status_carries_gateway_fields.
        assert out.get("schema") == "qubit-runtime-status/1", out
        assert out.get("version") and out.get("release_date"), out
        assert "overall" in out and "components" in out, out
        status, _, body = _http(port, "GET", "/agent/api/dashboard/themes",
                                qubit_token=True)
        assert status == 200
        assert json.loads(body)["active"] == "qubit"
    finally:
        ui.stop()


@needs_dist
def test_qubit_plugins_served(tmp_path):
    ui, port = _start(tmp_path)
    try:
        status, _, body = _http(port, "GET", "/agent/api/dashboard/plugins",
                                qubit_token=True)
        assert status == 200
        plugins = json.loads(body)
        names = [m["name"] for m in plugins]
        assert names == ["qubit-approvals", "qubit-boards", "qubit-brand",
                          "qubit-devices", "qubit-gateway", "qubit-memory", "qubit-models",
                          "qubit-ops", "qubit-reports", "qubit-runtime"], names
        for name in names:
            mf = [m for m in plugins if m["name"] == name][0]
            status, ctype, js = _http(
                port, "GET", f"/dashboard-plugins/{name}/{mf['entry']}")
            assert status == 200, (name, status)
            assert "application/javascript" in ctype
            assert b"__HERMES_PLUGINS__" in js, name
    finally:
        ui.stop()


@needs_dist
def test_qubit_chat_validation_live(tmp_path):
    ui, port = _start(tmp_path)
    try:
        status, _, body = _http(port, "POST", "/agent/api/qubit/chat",
                                body={"text": "  ", "session": "t"},
                                qubit_token=True)
        assert status == 400, (status, body[:200])
        assert json.loads(body)["code"] == "MALFORMED"
        status, _, body = _http(port, "GET", "/agent/api/pty",
                                qubit_token=True)
        assert status == 403
        assert json.loads(body)["code"] == "PTY_NO_SHELL"
    finally:
        ui.stop()


@needs_dist
def test_classic_pages_unaffected(tmp_path):
    ui, port = _start(tmp_path)
    try:
        for page in ("/", "/ops", "/settings", "/api/ops"):
            status, _, _ = _http(port, "GET", page)
            assert status == 200, (page, status)
    finally:
        ui.stop()
