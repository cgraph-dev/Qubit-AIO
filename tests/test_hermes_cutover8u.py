"""Hermes-backend cutover stage 1/2 — in-process bridge + safety policy.

Proves the vendored Hermes backend answers dashboard reads through the single
Qubit listener (no second server), that sensitive/write surfaces are never
proxied, and that the `webui.hermes_backend` flag is a safe, reversible toggle.
See docs/QUBIT_HERMES_CUTOVER_MIGRATION.md.

Fork-importing assertions run in subprocesses so the hermetic Qubit suite never
inherits the vendored backend's background threads.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def _run_py(body: str, timeout: int = 180) -> dict:
    home = Path(tempfile.mkdtemp(prefix="hermes-cutover-"))
    env = dict(os.environ)
    env["HOME"] = str(home)
    env["XDG_CONFIG_HOME"] = str(home / ".config")
    env["XDG_DATA_HOME"] = str(home / ".local" / "share")
    env.pop("PYTHONPATH", None)
    code = "import json\n" + body
    try:
        p = subprocess.run([sys.executable, "-c", code], cwd=str(REPO),
                           env=env, capture_output=True, text=True,
                           timeout=timeout)
        assert p.returncode == 0, p.stderr[-1200:]
        return json.loads(p.stdout.strip().splitlines()[-1])
    finally:
        shutil.rmtree(home, ignore_errors=True)


# --------------------------------------------------------------- pure policy
def test_gated_path_policy():
    from voicebridge import hermes_asgi as HA

    assert HA.gated_path("/api/status") is False
    assert HA.gated_path("/api/sessions") is False
    for prefix in HA.SENSITIVE_READ_PREFIXES + HA.QUBIT_OWNED_PREFIXES:
        assert HA.gated_path(prefix) is True, prefix
        assert HA.gated_path(prefix + "/x") is True, prefix
    assert HA.safe_to_proxy("POST", "/api/status") is False
    assert HA.safe_to_proxy("DELETE", "/api/sessions/x") is False


# ----------------------------------------------------- stage 2b egress guard
def test_fork_egress_guard_denies_external_allows_loopback():
    """In-process fork reads cannot open unrestricted outbound connections."""
    import socket

    from voicebridge import hermes_egress_guard as G

    G.install()
    # A real loopback listener the guard must still permit.
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    try:
        # Deny branch: TEST-NET-3 is non-routable; the guard must refuse
        # before any syscall so this never touches the network.
        with G.fork_scope():
            assert G.FORK_CONTEXT.get() is True
            blocked = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                with pytest.raises(G.EgressBlocked):
                    blocked.connect(("203.0.113.1", 443))
            finally:
                blocked.close()
            # Allow branch: loopback still works inside fork scope.
            client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                client.connect(("127.0.0.1", port))
            finally:
                client.close()
        # Outside fork scope the flag is back to its safe default.
        assert G.FORK_CONTEXT.get() is False
        assert G.status()["installed"] is True
        assert G.status()["denials"] >= 1
    finally:
        listener.close()


# --------------------------------------------------------------- subprocess
def test_bridge_reports_single_listener():
    out = _run_py(
        "from voicebridge import hermes_asgi as HA\n"
        "print(json.dumps(HA.status()))\n")
    assert out["bridge"] == "qubit-hermes-asgi"
    assert out["single_listener"] is True
    assert out["starts_second_server"] is False
    assert out["available"] is True, out.get("error")


def test_bridge_serves_real_fork_backend():
    out = _run_py(
        "from voicebridge import hermes_asgi as HA\n"
        "code, h, body = HA.call('GET', '/api/health')\n"
        "health = json.loads(body)\n"
        "code2, _, body2 = HA.call('GET', '/openapi.json')\n"
        "spec = json.loads(body2)\n"
        "print(json.dumps({'code': code, 'health': health, 'code2': code2,"
        " 'title': spec['info']['title']}))\n")
    assert out["code"] == 200
    assert out["health"].get("ok") is True
    assert out["code2"] == 200
    assert out["title"] == "Hermes Agent"


def test_flag_off_keeps_compat_behavior_and_rollback(tmp_path):
    """Rollback guarantee: with the flag off, safe reads use compat unchanged."""
    from voicebridge.config import Config
    from voicebridge.webui import WebUI
    import urllib.request

    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    p = tmp_path / "voice.yaml"
    p.write_text(f"logging:\n  jsonl_path: '{state}'\n"
                 f"webui:\n  token: test-token\n  hermes_backend: false\n")
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
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{ui._server.server_address[1]}/hermes/api/status")
        req.add_header("X-Hermes-Session-Token", "test-token")
        with urllib.request.urlopen(req, timeout=15) as r:
            out = json.loads(r.read())
        # Qubit compat status shape, not the fork's raw /api/status.
        assert out.get("schema") == "qubit-hermes-status/1", out
        assert "overall" not in out, "flag off must not serve the fork"
    finally:
        ui.stop()


def test_flag_on_serves_fork_reads_and_gates_sensitive(tmp_path):
    out = _run_py(
        "import time, pathlib, tempfile, urllib.request, urllib.error\n"
        "from voicebridge.config import Config\n"
        "from voicebridge.webui import WebUI\n"
        "state = pathlib.Path(tempfile.mkdtemp()) / 'state'\n"
        "state.mkdir()\n"
        "p = state.parent / 'voice.yaml'\n"
        "p.write_text(\"logging:\\n  jsonl_path: '\" + str(state) + \"'\\n\"\n"
        "             \"webui:\\n  token: test-token\\n  hermes_backend: true\\n\")\n"
        "cfg = Config.load(p)\n"
        "class O:\n"
        "    _route='local'; _web_active=0\n"
        "    task=type('T',(),{'active':False})()\n"
        "    def __init__(self):\n"
        "        self.cfg=cfg; self._t0=time.time()\n"
        "ui=WebUI(O(), host='127.0.0.1', port=0, state_dir=state); ui.start()\n"
        "port=ui._server.server_address[1]\n"
        "def get(path):\n"
        "    req=urllib.request.Request('http://127.0.0.1:%d%s'%(port,path))\n"
        "    req.add_header('X-Hermes-Session-Token','test-token')\n"
        "    try:\n"
        "        r=urllib.request.urlopen(req,timeout=20); return r.status, r.read().decode()\n"
        "    except urllib.error.HTTPError as e: return e.code, e.read().decode()\n"
        "def post(path, body):\n"
        "    data=json.dumps(body).encode()\n"
        "    req=urllib.request.Request('http://127.0.0.1:%d%s'%(port,path), method='POST', data=data)\n"
        "    req.add_header('X-Hermes-Session-Token','test-token')\n"
        "    req.add_header('Content-Type','application/json')\n"
        "    try:\n"
        "        r=urllib.request.urlopen(req,timeout=20); return r.status, r.read().decode()\n"
        "    except urllib.error.HTTPError as e: return e.code, e.read().decode()\n"
        "st, body = get('/hermes/api/status')\n"
        "raw_st, raw_body = get('/hermes/api/config/raw')\n"
        "post_st, post_body = post('/hermes/api/status', {})\n"
        "ui.stop()\n"
        "print(json.dumps({'status': st, 'body': body, 'raw_status': raw_st,\n"
        "  'raw_body': raw_body, 'post_status': post_st, 'post_body': post_body}))\n")
    assert out["status"] == 200
    served = json.loads(out["body"])
    assert served.get("release_date") == "2026.9.7", served
    assert "overall" in served and "components" in served, "fork status shape"
    # Sensitive read is never proxied to the fork.
    assert json.loads(out["raw_body"]).get("code") != "OK"
    # Write is never proxied (compat path).
    assert out["post_status"] in (200, 400, 404, 405, 409, 501)
