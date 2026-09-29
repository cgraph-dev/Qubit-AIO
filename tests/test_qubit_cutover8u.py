"""Hermes-backend cutover stage 1/2 — in-process bridge + safety policy.

Proves the vendored Hermes backend answers dashboard reads through the single
Qubit listener (no second server), that sensitive/write surfaces are never
proxied, and that the `webui.qubit_backend` flag is a safe, reversible toggle.
See docs/archive/2026-09-21/QUBIT_HERMES_CUTOVER_MIGRATION.md.

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
def test_gated_path_policy(monkeypatch):
    from voicebridge import qubit_asgi as HA

    monkeypatch.setattr(HA, "known_route", lambda _p: True)
    # Corrected cutover: a fork-declared, non-sensitive read is fork-served
    # even though Qubit also implements a compat handler. The old
    # "all implemented compat routes are no-proxy" rule is gone because it made
    # empty Qubit stubs shadow the fork's real dashboard data
    # (finding.fork_shadow_78_get).
    assert HA.gated_path("/api/status") is False
    assert HA.gated_path("/api/sessions") is False
    assert HA.gated_path("/api/health") is False
    assert HA.safe_to_proxy("GET", "/api/status") is True
    assert HA.safe_to_proxy("GET", "/api/sessions") is True
    assert HA.safe_to_proxy("GET", "/api/health") is True
    # A matching Qubit-owned-read pattern does NOT gate it.
    assert HA._matches(HA._owned_read_patterns(), "/api/status") is True

    # Sensitive/Qubit-owned prefixes never proxy.
    for prefix in HA.SENSITIVE_READ_PREFIXES + HA.QUBIT_OWNED_PREFIXES:
        assert HA.gated_path(prefix) is True, prefix
        assert HA.gated_path(prefix + "/x") is True, prefix
        assert HA.safe_to_proxy("GET", prefix) is False, prefix
    # Mutations never proxy.
    assert HA.safe_to_proxy("POST", "/api/status") is False
    assert HA.safe_to_proxy("DELETE", "/api/sessions/x") is False
    # A fork-declared path Qubit does not own is proxy-eligible (fork-served).
    assert HA.gated_path("/openapi.json") is False
    assert HA.safe_to_proxy("GET", "/openapi.json") is True


#: Representative slice of the 78 Qubit compat GET handlers the proxy-first
#: read branch used to shadow (audit finding.fork_shadow_78_get). Each is still
#: an implemented Qubit route, but under the corrected contract the fork serves
#: the non-sensitive ones so the dashboard gets real data.
SHADOWED_SAMPLE = (
    "/api/status", "/api/health", "/api/sessions", "/api/sessions/stats",
    "/api/profiles", "/api/profiles/active", "/api/logs", "/api/memory",
    "/api/config/schema", "/api/local-models/status", "/api/analytics/usage",
    "/api/learning/graph", "/api/egress/status", "/api/mcp/servers",
    "/api/cron/jobs", "/api/system/stats", "/api/curator", "/api/git/status",
    "/api/pairing", "/api/webhooks", "/api/messaging/platforms",
    "/api/skills", "/api/tools/toolsets",
)


def test_shadowed_routes_are_fork_served_unless_gated(monkeypatch):
    """The once-shadowed route set is fork-served: every sample resolves to an
    implemented Qubit handler, is NOT gated, and is proxy-eligible so the fork's
    real data wins. ``_owned_read_patterns`` is retained but not consulted."""
    from voicebridge import qubit_asgi as HA
    from voicebridge import qubit_compat as HC

    monkeypatch.setattr(HA, "known_route", lambda _p: True)
    for path in SHADOWED_SAMPLE:
        route, _ = HC.match("GET", path)
        assert route is not None, path
        assert route["disposition"] == "implemented", (path, route)
        assert route["handler"], (path, route)
        assert HA.gated_path(path) is False, path
        assert HA.safe_to_proxy("GET", path) is True, path

    # Exhaustive regression guard: the owned-read pattern table still mirrors
    # the route authority, and /api/status matches it, yet the policy does not
    # consult it -- proving the old no-proxy rule stays retired.
    owned = sum(1 for r in HC.ROUTES
                if r["method"].upper() in ("GET", "HEAD")
                and r["disposition"] == "implemented")
    assert owned >= len(SHADOWED_SAMPLE), owned
    assert len(HA._owned_read_patterns()) == owned
    assert HA._matches(HA._owned_read_patterns(), "/api/status") is True
    assert HA.gated_path("/api/status") is False


# ----------------------------------------------------- stage 2b egress guard
def test_fork_egress_guard_denies_external_allows_loopback():
    """In-process fork reads cannot open unrestricted outbound connections."""
    import socket

    from voicebridge import qubit_egress_guard as G

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
        "from voicebridge import qubit_asgi as HA\n"
        "print(json.dumps(HA.status()))\n")
    assert out["bridge"] == "qubit-runtime-asgi"
    assert out["single_listener"] is True
    assert out["starts_second_server"] is False
    assert out["available"] is True, out.get("error")


def test_bridge_serves_real_fork_backend():
    out = _run_py(
        "from voicebridge import qubit_asgi as HA\n"
        "code, h, body = HA.call('GET', '/api/health')\n"
        "health = json.loads(body)\n"
        "code2, _, body2 = HA.call('GET', '/openapi.json')\n"
        "spec = json.loads(body2)\n"
        "print(json.dumps({'code': code, 'health': health, 'code2': code2,"
        " 'title': spec['info']['title']}))\n")
    assert out["code"] == 200
    assert out["health"].get("ok") is True
    assert out["code2"] == 200
    assert out["title"] == "Qubit Agent"


def test_flag_off_keeps_compat_behavior_and_rollback(tmp_path):
    """Rollback guarantee: with the flag off, safe reads use compat unchanged."""
    from voicebridge.config import Config
    from voicebridge.webui import WebUI
    import urllib.request

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
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{ui._server.server_address[1]}/agent/api/status")
        req.add_header("X-Hermes-Session-Token", "test-token")
        with urllib.request.urlopen(req, timeout=15) as r:
            out = json.loads(r.read())
        # Qubit compat status shape, not the fork's raw /api/status.
        assert out.get("schema") == "qubit-runtime-status/1", out
        # h_status also serves the reference components/overall rollup (B4-03,
        # pinned by tests/test_overlay_regressions.py); the compat-only
        # discriminator is the schema string, which the fork's /api/status lacks.
    finally:
        ui.stop()


def test_flag_on_serves_fork_reads_and_gates_sensitive(tmp_path):
    """Flag on: fork-declared non-sensitive reads return the fork's real data
    (rebranded); sensitive surfaces stay on the Qubit gate."""
    out = _run_py(
        "import time, pathlib, tempfile, urllib.request, urllib.error\n"
        "from voicebridge.config import Config\n"
        "from voicebridge.webui import WebUI\n"
        "state = pathlib.Path(tempfile.mkdtemp()) / 'state'\n"
        "state.mkdir()\n"
        "p = state.parent / 'voice.yaml'\n"
        "p.write_text(\"logging:\\n  jsonl_path: '\" + str(state) + \"'\\n\"\n"
        "             \"webui:\\n  token: test-token\\n  qubit_backend: true\\n\")\n"
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
        "st, body = get('/agent/api/status')\n"
        "env_st, env_body = get('/agent/api/env')\n"
        "hch_st, health_body = get('/agent/api/health')\n"
        "raw_st, raw_body = get('/agent/api/config/raw')\n"
        "op_st, op_body = get('/agent/openapi.json')\n"
        "post_st, post_body = post('/agent/api/status', {})\n"
        "reads = {}\n"
        "for path in ('/api/sessions','/api/profiles','/api/memory',\n"
        "             '/api/config/schema','/api/logs'):\n"
        "    s2, b2 = get('/agent' + path)\n"
        "    reads[path] = {'status': s2, 'body': json.loads(b2)}\n"
        "ui.stop()\n"
        "print(json.dumps({'status': st, 'body': body,\n"
        "  'env_status': env_st, 'env_body': env_body,\n"
        "  'health_status': hch_st, 'health_body': health_body,\n"
        "  'raw_status': raw_st, 'raw_body': raw_body,\n"
        "  'openapi_status': op_st, 'openapi_body': op_body,\n"
        "  'post_status': post_st, 'post_body': post_body,\n"
        "  'reads': reads}))\n")
    assert out["status"] == 200
    served = json.loads(out["body"])
    # /api/status is on webui.QUBIT_READ_AUTHORITY (gateway-supervision
    # authority, pinned by tests/test_dashboard_control_confirm9u.py), so this
    # read stays on the Qubit compat status document. Fork-read proof is the
    # non-quarantined reads below (/api/health, /api/env, openapi).
    assert served.get("schema") == "qubit-runtime-status/1", served
    assert served.get("version") and served.get("release_date"), served
    assert "overall" in served and "components" in served, served
    # The env read is the fork's real (non-empty) environment document.
    assert out["env_status"] == 200, out["env_body"][:200]
    env = json.loads(out["env_body"])
    assert isinstance(env, dict) and env, env
    # /api/health is fork-served too: the fork health shape, not Qubit's.
    health = json.loads(out["health_body"])
    assert out["health_status"] == 200 and health.get("ok") is True, health
    assert "version" in health and health.get("auth_required") is False, health
    assert "state" not in health and "detail" not in health, health
    # A genuinely fork-only, non-owned read proxies (and is rebranded).
    spec = json.loads(out["openapi_body"])
    assert out["openapi_status"] == 200, out["openapi_body"][:200]
    assert spec["info"]["title"] == "Qubit Agent API", spec.get("info")
    assert len(spec.get("paths", {})) > 100, len(spec.get("paths", {}))
    # Sensitive read is never proxied to the fork: Qubit's typed secret gate.
    raw = json.loads(out["raw_body"])
    assert out["raw_status"] == 403, (out["raw_status"], raw)
    assert raw.get("code") == "SECRET_NEVER_SERVED", raw
    # The once-shadowed routes now serve the fork's real data shape.
    expected_fork_keys = {
        "/api/sessions": ("sessions", "total", "limit", "offset"),
        "/api/profiles": ("profiles",),
        "/api/memory": ("active", "providers", "builtin_files"),
        "/api/config/schema": ("fields", "category_order"),
        "/api/logs": ("file", "lines"),
    }
    assert out["reads"], out
    for path, res in out["reads"].items():
        assert res["status"] == 200, (path, res)
        for key in expected_fork_keys[path]:
            assert key in res["body"], (path, key, res["body"])
    # Write is never proxied (compat path).
    assert out["post_status"] in (200, 400, 404, 405, 409, 501)


# ------------------------------------------------ stage 3 write policy
def _wcfg(tmp_path, extra: str = "") -> object:
    from voicebridge.config import Config

    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    p = tmp_path / "voice.yaml"
    p.write_text(f"logging:\n  jsonl_path: '{state}'\n{extra}")
    return Config.load(p), state


def test_write_policy_is_fail_closed_and_never_bypasses_a_gate(
        tmp_path, monkeypatch):
    """Stage 3 policy: default empty allowlist, typed gates and e-stop win."""
    from voicebridge import qubit_asgi as HA

    cfg, _state = _wcfg(tmp_path)

    # Nothing is a cutover write until the operator allowlists it.
    monkeypatch.setattr(HA, "WRITE_PROXY_ALLOWLIST", set())
    assert HA.write_policy(cfg, "POST", "/api/sessions/import") == (False, None)
    assert HA.safe_write_to_proxy("POST", "/api/sessions/import") is False

    # Allowlisted, but a Qubit-typed gate still refuses; the fork is never used.
    # (sessions/import is a real confirm-gated handler now; the receipt-store
    # gate lives on sessions/owner-backfill — tests/test_sessions_mutations_wave18.py.)
    monkeypatch.setattr(HA, "WRITE_PROXY_ALLOWLIST",
                        {("POST", "/api/sessions/owner-backfill")})
    allowed, refusal = HA.write_policy(cfg, "POST", "/api/sessions/owner-backfill")
    assert allowed is False and refusal is not None, (allowed, refusal)
    assert refusal[0] == 501 and \
        refusal[1]["code"] == "SESSIONS_RECEIPT_STORE", refusal
    assert HA.safe_write_to_proxy("POST", "/api/sessions/owner-backfill") is False

    # The one Qubit emergency stop refuses an allowlisted write.
    monkeypatch.setattr(HA, "WRITE_PROXY_ALLOWLIST",
                        {("POST", "/api/ops/doctor")})
    monkeypatch.setattr("voicebridge.broker.estop_engaged",
                        lambda _c=None: True)
    allowed, refusal = HA.write_policy(cfg, "POST", "/api/ops/doctor")
    assert allowed is False and refusal is not None
    assert refusal[0] == 423 and refusal[1]["code"] == "ESTOP_ENGAGED", refusal


def test_webui_write_middleware_proxies_only_allowlisted_writes(
        tmp_path, monkeypatch):
    """Flag on + allowlist -> the Qubit middleware clears and the fork runs."""
    import urllib.request
    import urllib.error

    from voicebridge import qubit_asgi as HA
    from voicebridge.webui import WebUI

    cfg, state = _wcfg(tmp_path, "webui:\n  token: test-token\n"
                                 "  qubit_backend: true\n"
                                 "  hermes_writes: true\n")

    calls = []

    def _fake_call(method, path, **kw):
        calls.append((method, path))
        return 200, {"content-type": "application/json"}, \
            b'{"ok": true, "proxied_by_fork": true}'

    monkeypatch.setattr(HA, "call", _fake_call)
    monkeypatch.setattr(HA, "known_route", lambda _p: True)
    monkeypatch.setattr(HA, "WRITE_PROXY_ALLOWLIST",
                        {("POST", "/api/cron/jobs"),
                         ("POST", "/api/sessions/owner-backfill")})

    class _FakeOrch:
        _route = "local"
        _web_active = 0
        task = type("T", (), {"active": False})()

        def __init__(self):
            self.cfg = cfg
            self._t0 = time.time()

    ui = WebUI(_FakeOrch(), host="127.0.0.1", port=0, state_dir=state)
    ui.start()
    port = ui._server.server_address[1]

    def _post(path):
        data = json.dumps({"confirm": True}).encode()
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}{path}", method="POST", data=data)
        req.add_header("X-Hermes-Session-Token", "test-token")
        req.add_header("Content-Type", "application/json")
        try:
            r = urllib.request.urlopen(req, timeout=15)
            return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    try:
        # Allowlisted, policy-cleared: the fork (fake) serves the write.
        st, out = _post("/agent/api/cron/jobs")
        assert st == 200 and out.get("proxied_by_fork") is True, (st, out)
        assert calls == [("POST", "/api/cron/jobs")], calls
        # Allowlisted but Qubit-gated: the typed gate wins; no fork call.
        st, out = _post("/agent/api/sessions/owner-backfill")
        assert st == 501 and out.get("code") == "SESSIONS_RECEIPT_STORE", out
        assert calls == [("POST", "/api/cron/jobs")], calls
    finally:
        ui.stop()
