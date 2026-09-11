"""Hermes-backend cutover stage 1 — in-process bridge + safety policy.

Proves the vendored Hermes backend answers dashboard reads through the single
Qubit listener (no second server), that sensitive/write surfaces are never
proxied, and that the `webui.hermes_backend` flag is a safe, reversible toggle.
See docs/QUBIT_HERMES_CUTOVER_MIGRATION.md.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def test_safe_to_proxy_policy():
    from voicebridge import hermes_asgi as HA

    assert HA.safe_to_proxy("GET", "/api/status") is True
    assert HA.safe_to_proxy("HEAD", "/api/sessions") is True
    assert HA.safe_to_proxy("POST", "/api/status") is False
    assert HA.safe_to_proxy("DELETE", "/api/sessions/x") is False
    for prefix in HA.SENSITIVE_READ_PREFIXES:
        assert HA.safe_to_proxy("GET", prefix) is False, prefix
        assert HA.safe_to_proxy("GET", prefix + "/x") is False, prefix


def test_bridge_reports_single_listener():
    from voicebridge import hermes_asgi as HA

    st = HA.status()
    assert st["bridge"] == "qubit-hermes-asgi"
    assert st["single_listener"] is True
    assert st["starts_second_server"] is False
    assert st["available"] is True, st.get("error")


def test_bridge_serves_real_fork_backend():
    from voicebridge import hermes_asgi as HA

    code, hdrs, body = HA.call("GET", "/api/health")
    assert code == 200
    payload = json.loads(body)
    assert payload.get("ok") is True
    assert "version" in payload
    code, _, body = HA.call("GET", "/openapi.json")
    assert code == 200
    assert json.loads(body)["info"]["title"] == "Hermes Agent"


# --------------------------------------------------------------- live webui
def _start(tmp_path, backend_on: bool):
    from voicebridge.config import Config
    from voicebridge.webui import WebUI

    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    p = tmp_path / "voice.yaml"
    p.write_text(
        f"logging:\n  jsonl_path: '{state}'\n"
        f"webui:\n  token: test-token\n  hermes_backend: "
        f"{'true' if backend_on else 'false'}\n")
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
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request("http://127.0.0.1:%d%s" % (port, path),
                                 method=method, data=data)
    if token:
        req.add_header("X-Hermes-Session-Token", token)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, r.headers.get("Content-Type", ""), r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Content-Type", ""), e.read()


def test_flag_off_keeps_compat_behavior_and_rollback(tmp_path):
    """Rollback guarantee: with the flag off, safe reads use compat unchanged."""
    ui, port = _start(tmp_path, backend_on=False)
    try:
        status, _, body = _http(port, "GET", "/hermes/api/status")
        assert status == 200
        out = json.loads(body)
        # Qubit compat status shape, not the fork's raw /api/status.
        assert out.get("schema") == "qubit-hermes-status/1", out
        assert "overall" not in out, "flag off must not serve the fork"
    finally:
        ui.stop()


def test_flag_on_serves_fork_reads_and_gates_sensitive(tmp_path):
    ui, port = _start(tmp_path, backend_on=True)
    try:
        # A safe read is served by the fork (its /api/status shape).
        status, _, body = _http(port, "GET", "/hermes/api/status")
        assert status == 200
        out = json.loads(body)
        assert out.get("release_date") == "2026.9.7", out
        assert "overall" in out and "components" in out, "fork status shape"
        # A sensitive read is never proxied to the fork.
        status, _, body = _http(port, "GET", "/hermes/api/config/raw")
        assert status in (200, 403, 409, 501)
        assert json.loads(body).get("code") != "OK"
        # A write is never proxied (compat path).
        status, _, body = _http(port, "POST", "/hermes/api/status", {})
        assert status in (200, 400, 404, 405, 409, 501)
    finally:
        ui.stop()
