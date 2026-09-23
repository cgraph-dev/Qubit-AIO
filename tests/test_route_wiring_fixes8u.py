"""Focused route-wiring regressions for the isolated dashboard sweep (8U).

Each test pins one defect fixed in ``voicebridge/webui.py`` /
``voicebridge/qubit_compat.py`` against the LIVE listener (or the in-process
dispatcher where the transport is not under test):

1. every ``/agent/`` write (not just ``/agent/api/``) reaches the compat
   dispatcher (POST/PUT/PATCH/DELETE);
2. ``HEAD /agent/api/files/stream`` returns the typed gate, never a bare 404;
3. the SPA e-stop alias ``/agent/api/power`` reaches the one power authority;
4. cron GET and POST read/write the SAME schedules store (no split-brain);
5. the skill/toolset/cron confirmation contracts carry a POPULATED,
   settle-able ``confirmation_id`` and DELETE reads ``confirm`` from body/query.

Hermetic: tmp XDG state only; no network. Security gates stay asserted.
"""
from __future__ import annotations

import hashlib
import http.client
import json
import time


def _cfg(tmp_path, monkeypatch, backend: bool = True):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    p = tmp_path / "voice.yaml"
    p.write_text(
        f"logging:\n  jsonl_path: '{state}'\n"
        f"webui:\n  token: test-token\n"
        f"  qubit_backend: {'true' if backend else 'false'}\n")
    return C.Config.load(p), state


class _FakeOrch:
    _route = "local"
    _web_active = 0
    task = type("T", (), {"active": False})()

    def __init__(self, cfg):
        self.cfg = cfg
        self._t0 = time.time()


def _live(tmp_path, monkeypatch, backend: bool = True):
    from voicebridge.webui import WebUI

    cfg, state = _cfg(tmp_path, monkeypatch, backend)
    ui = WebUI(_FakeOrch(cfg), host="127.0.0.1", port=0, state_dir=state)
    ui.start()
    return ui, ui._server.server_address[1]


def _req(port, method, path, body=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
    headers = {"X-Hermes-Session-Token": "test-token"}
    data = json.dumps(body) if body is not None else None
    if data is not None:
        headers["Content-Type"] = "application/json"
    conn.request(method, path, body=data, headers=headers)
    resp = conn.getresponse()
    out = (resp.status, resp.read())
    conn.close()
    return out


def _json_body(raw):
    return json.loads(raw.decode("utf-8")) if raw else None


# ------------------------------------------------------------------ defect 1
def test_all_agent_writes_reach_compat_not_bare_404(tmp_path, monkeypatch):
    ui, port = _live(tmp_path, monkeypatch)
    try:
        # POST /agent/auth/logout is registered in qubit_compat; before the fix
        # only /agent/api/ was routed and this was a bare 404.
        st, raw = _req(port, "POST", "/agent/auth/logout", {})
        assert st == 200, (st, raw[:200])
        assert _json_body(raw)["ok"] is True

        # The other fork-declared /agent/auth/* writes now reach their handlers
        # too (typed envelopes, never a bare 404).
        st, raw = _req(port, "POST", "/agent/auth/password-login", {})
        assert st == 401 and _json_body(raw)["code"] == "AUTH_INVALID_CREDENTIALS"
        st, raw = _req(port, "POST", "/agent/auth/native/token", {})
        assert st == 400 and _json_body(raw)["code"] == "AUTH_MISSING_CODE"
        st, raw = _req(port, "POST", "/agent/auth/native/refresh", {})
        assert st == 400 and _json_body(raw)["code"] == "AUTH_MISSING_REFRESH"

        # PUT /agent/... reaches the compat handler (typed refusal, not 404).
        st, raw = _req(port, "PUT", "/agent/api/profiles/ghost/description",
                       {"description": "x"})
        assert st not in (404,) and b"not found" not in raw, (st, raw[:200])

        # PATCH /agent/... reaches the compat persona handler.
        st, raw = _req(port, "PATCH", "/agent/api/profiles/ghost", {"name": "y"})
        assert st not in (404,) and b"not found" not in raw, (st, raw[:200])

        # DELETE /agent/... reaches the compat cron handler.
        st, raw = _req(port, "DELETE", "/agent/api/cron/jobs/999")
        assert st not in (404,) and b"not found" not in raw, (st, raw[:200])
    finally:
        ui.stop()


# ------------------------------------------------------------------ defect 2
def test_head_agent_route_returns_typed_gate_no_body(tmp_path, monkeypatch):
    ui, port = _live(tmp_path, monkeypatch)
    try:
        st, raw = _req(port, "HEAD", "/agent/api/files/stream")
        assert st == 403, (st, raw[:200])
        assert raw == b"", raw[:200]  # HEAD carries headers only
        # The GET sibling still answers the same typed gate.
        st, raw = _req(port, "GET", "/agent/api/files/stream")
        assert st == 403 and _json_body(raw)["code"] == "FILES_BINARY_STREAM"
    finally:
        ui.stop()


# ------------------------------------------------------------------ defect 3
def test_spa_power_alias_is_the_same_estop_authority(tmp_path, monkeypatch):
    ui, port = _live(tmp_path, monkeypatch)
    try:
        # Confirm-gated: without confirmation the latch is NOT engaged.
        st, raw = _req(port, "POST", "/agent/api/power", {"action": "reset"})
        out = _json_body(raw)
        assert st == 200 and out["state"] == "denied"
        assert out["code"] == "CONFIRMATION_REQUIRED", out

        # Latched through the SPA alias.
        st, raw = _req(port, "POST", "/agent/api/power",
                       {"action": "emergency-stop", "confirmed": True})
        assert st == 200 and _json_body(raw)["state"] == "OK", raw[:200]

        # The one e-stop authority now refuses a mutating compat route.
        st, raw = _req(port, "POST", "/agent/api/skills",
                       {"name": "x", "content": "# x", "license": "MIT"})
        assert st == 403 and _json_body(raw)["code"] == "ESTOP_LATCHED", raw[:200]

        # Lifted through the SAME alias; mutations resume.
        st, raw = _req(port, "POST", "/agent/api/power",
                       {"action": "reset", "confirmed": True})
        assert st == 200 and _json_body(raw)["state"] == "OK", raw[:200]

        # Read path is served by the compat handler, not a bare 404.
        st, raw = _req(port, "GET", "/agent/api/power")
        assert st == 200 and _json_body(raw)["state"] == "OK", raw[:200]
    finally:
        ui.stop()


# ------------------------------------------------------------------ defect 4
def test_cron_read_and_write_share_one_store(tmp_path, monkeypatch):
    ui, port = _live(tmp_path, monkeypatch)
    try:
        st, raw = _req(port, "POST", "/agent/api/cron/jobs",
                       {"confirm": True, "name": "sweep-job", "prompt": "do x",
                        "when": "every 60 minutes"})
        assert st == 200, (st, raw[:200])

        # Before the fix GET was fork-proxied and returned [] while the create
        # landed in voicebridge.schedules. The read now observes the write.
        st, raw = _req(port, "GET", "/agent/api/cron/jobs")
        assert st == 200, (st, raw[:200])
        jobs = _json_body(raw)
        assert isinstance(jobs, list) and len(jobs) == 1, jobs
        assert jobs[0]["name"] == "sweep-job", jobs[0]
    finally:
        ui.stop()


# ------------------------------------------------------------------ defect 5
def test_skill_create_confirm_contract_is_settleable(tmp_path, monkeypatch):
    ui, port = _live(tmp_path, monkeypatch)
    try:
        st, raw = _req(port, "POST", "/agent/api/skills",
                       {"name": "demo", "content": "# Demo\n", "license": "MIT"})
        out = _json_body(raw)
        assert st == 409 and out["code"] == "CONFIRM_REQUIRED", (st, out)
        assert out["confirmation_id"], out  # populated, not ""

        st, raw = _req(port, "POST", "/agent/api/ops/confirm",
                       {"confirmation_id": out["confirmation_id"]})
        done = _json_body(raw)
        assert st == 200 and done["state"] == "executed", (st, done)
        assert done["skill_id"] == "demo", done

        st, raw = _req(port, "GET", "/agent/api/skills")
        names = [s["name"] for s in _json_body(raw)]
        assert "demo" in names, names
    finally:
        ui.stop()


def test_skill_hub_install_confirm_contract_is_settleable(tmp_path, monkeypatch):
    ui, port = _live(tmp_path, monkeypatch)
    try:
        man = tmp_path / "local-skill.json"
        man.write_text(json.dumps({
            "skill_id": "local-tool", "name": "local-tool",
            "version": "1.0.0", "description": "reviewed",
            "source": "qubit-local", "revision": "1", "license": "MIT",
            "content_hash": hashlib.sha256(b"# tool\n").hexdigest(),
            "capabilities": ["skill.demo"], "scopes": ["skill:read"],
            "tier": "reversible", "rollback": "remove the skill"}))
        st, raw = _req(port, "POST", "/agent/api/skills/hub/install",
                       {"identifier": str(man)})
        out = _json_body(raw)
        assert st == 409 and out["code"] == "CONFIRM_REQUIRED", (st, out)
        assert out["confirmation_id"], out

        st, raw = _req(port, "POST", "/agent/api/ops/confirm",
                       {"confirmation_id": out["confirmation_id"]})
        done = _json_body(raw)
        assert st == 200 and done["state"] == "executed", (st, done)
        assert done["skill_id"] == "local-tool", done
    finally:
        ui.stop()


def test_toolset_toggle_confirm_contract_persists(tmp_path, monkeypatch):
    ui, port = _live(tmp_path, monkeypatch)
    try:
        st, raw = _req(port, "PUT", "/agent/api/tools/toolsets/web",
                       {"enabled": False})
        out = _json_body(raw)
        assert st == 409 and out["code"] == "CONFIRM_REQUIRED", (st, out)
        assert out["confirmation_id"], out

        st, raw = _req(port, "POST", "/agent/api/ops/confirm",
                       {"confirmation_id": out["confirmation_id"]})
        done = _json_body(raw)
        assert st == 200 and done["state"] == "executed", (st, done)
        assert done["enabled"] is False, done

        # The read path shows the persisted state (was a hard-coded True no-op).
        st, raw = _req(port, "GET", "/agent/api/tools/toolsets")
        rows = {r["name"]: r["enabled"] for r in _json_body(raw)}
        assert rows.get("web") is False, rows
    finally:
        ui.stop()


def test_cron_delete_reads_confirm_from_query_and_body(tmp_path, monkeypatch):
    ui, port = _live(tmp_path, monkeypatch)
    try:
        def _create(name):
            st, raw = _req(port, "POST", "/agent/api/cron/jobs",
                           {"confirm": True, "name": name, "prompt": "p",
                            "when": "every 60 minutes"})
            assert st == 200, (st, raw[:200])
            return _json_body(raw)["id"]

        # Query-string settle.
        jid = _create("delete-query")
        st, raw = _req(port, "DELETE", f"/agent/api/cron/jobs/{jid}?confirm=true")
        assert st == 200 and _json_body(raw)["ok"] is True, (st, raw[:200])

        # JSON-body settle (the body must be parsed for DELETE).
        jid = _create("delete-body")
        st, raw = _req(port, "DELETE", f"/agent/api/cron/jobs/{jid}",
                       {"confirm": True})
        assert st == 200 and _json_body(raw)["ok"] is True, (st, raw[:200])

        st, raw = _req(port, "GET", "/agent/api/cron/jobs")
        assert _json_body(raw) == [], _json_body(raw)
    finally:
        ui.stop()


# ----------------------------------------------------------- gates stay on
def test_security_gates_are_unchanged(tmp_path, monkeypatch):
    ui, port = _live(tmp_path, monkeypatch)
    try:
        st, raw = _req(port, "GET", "/agent/api/config/raw")
        assert st == 403 and _json_body(raw)["code"] == "SECRET_NEVER_SERVED"

        # Unauthenticated /agent writes still 401.
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
        conn.request("POST", "/agent/auth/logout", body=b"{}",
                     headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        assert resp.status == 401, resp.status
        resp.read()
        conn.close()
    finally:
        ui.stop()
