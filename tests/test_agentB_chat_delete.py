"""Agent B focused proof: a chat-session DELETE must be confirmable over HTTP.

Defect: ``webui._hermes_api`` only parses request bodies for POST/PUT/PATCH
(``voicebridge/webui.py:1182``), so a DELETE body never reaches the compat
handler and ``h_chat_session_delete`` can never observe ``confirm=true`` — the
session is undeletable through the public listener. The handler already
receives the parsed query string, so it must also honor ``?confirm=true``.
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

_DIST = REPO_ROOT / "frontend" / "qubit-dist"
_needs_dist = pytest.mark.skipif(
    not (_DIST / "index.html").is_file(),
    reason="hermes-dist not built (npm build step)")


def _start_webui(tmp_path):
    from voicebridge.config import Config
    from voicebridge.webui import WebUI

    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    p = tmp_path / "voice.yaml"
    p.write_text(f"logging:\n  jsonl_path: '{state}'\n"
                 "webui:\n  token: test-token\n")
    cfg = Config.load(p)

    class _FakeOrch:
        _route = "gateway"
        _web_active = 0
        task = type("T", (), {"active": False})()

        def __init__(self) -> None:
            self.cfg = cfg
            self._t0 = time.time()

    ui = WebUI(_FakeOrch(), host="127.0.0.1", port=0, state_dir=state)
    ui.start()
    return ui, ui._server.server_address[1]


def _http(port, method, path):
    req = urllib.request.Request(
        "http://127.0.0.1:%d%s" % (port, path), method=method)
    req.add_header("X-Hermes-Session-Token", "test-token")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


@_needs_dist
def test_chat_session_delete_confirm_over_http(tmp_path):
    ui, port = _start_webui(tmp_path)
    try:
        status, created = _http(
            port, "POST", "/agent/api/qubit/chat/sessions")
        assert status == 200 and created["ok"] is True, (status, created)
        sid = created["session"]["id"]

        # No confirmation -> typed refusal, session still present.
        denied_status, denied = _http(
            port, "DELETE", f"/agent/api/qubit/chat/sessions/{sid}")
        assert denied_status == 409
        assert denied["code"] == "CONFIRM_REQUIRED"

        # Confirm via query string (DELETE bodies are not parsed server-side).
        ok_status, ok = _http(
            port, "DELETE",
            f"/agent/api/qubit/chat/sessions/{sid}?confirm=true")
        assert ok_status == 200 and ok["ok"] is True, (ok_status, ok)

        gone_status, _ = _http(
            port, "GET", f"/agent/api/qubit/chat/sessions/{sid}")
        assert gone_status == 404
    finally:
        ui.stop()
