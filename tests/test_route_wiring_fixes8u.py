"""Focused route-wiring regressions for the isolated dashboard sweep (8U).

Each test pins one defect fixed in ``voicebridge/webui.py`` /
``voicebridge/qubit_compat.py`` against the LIVE listener (or the in-process
dispatcher where the transport is not under test):

1. every ``/agent/`` write (not just ``/agent/api/``) reaches the compat
   dispatcher (POST/PUT/PATCH/DELETE);
2. ``HEAD /agent/api/files/stream`` reaches the real download handler (its
   typed ``path required`` refusal when no path is given), never a bare 404;
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
    headers = {"X-Qubit-Session-Token": "test-token"}
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
        # D5: /api/files/stream is a real brokered binary download now, so a
        # request with no ``path`` is a typed 400 (never a bare 404); HEAD
        # carries headers only.
        st, raw = _req(port, "HEAD", "/agent/api/files/stream")
        assert st == 400, (st, raw[:200])
        assert raw == b"", raw[:200]  # HEAD carries headers only
        st, raw = _req(port, "GET", "/agent/api/files/stream")
        assert st == 400 and _json_body(raw)["code"] == "MALFORMED"
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

        # The one e-stop authority now refuses a mutating compat route. The
        # compat confirm gate is the single mutation gate and emits
        # EMERGENCY_STOP (dashboard_ops uses ESTOP_LATCHED only on its own
        # registry lane); both are the same latched e-stop authority.
        st, raw = _req(port, "POST", "/agent/api/skills",
                       {"name": "x", "content": "# x", "license": "MIT"})
        assert st == 403 and _json_body(raw)["code"] == "EMERGENCY_STOP", raw[:200]

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
                       {"name": "demo",
                        "content": "---\nname: demo\ndescription: demo skill\n---\n# Demo\n",
                        "license": "MIT"})
        out = _json_body(raw)
        assert st == 409 and out["code"] == "CONFIRM_REQUIRED", (st, out)
        assert out["confirmation_id"], out  # populated, not ""

        st, raw = _req(port, "POST", "/agent/api/ops/confirm",
                       {"confirmation_id": out["confirmation_id"]})
        done = _json_body(raw)
        assert st == 200 and done["state"] == "executed", (st, done)

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


def test_named_settings_and_keys_round_trip_over_http(tmp_path, monkeypatch):
    root = tmp_path / "runtime"
    home = root / "profiles" / "work"
    home.mkdir(parents=True)
    (root / "config.yaml").write_text("agent:\n  max_turns: 10\n")
    (home / "config.yaml").write_text("agent:\n  max_turns: 20\n")
    monkeypatch.setenv("QUBIT_HOME", str(root))
    ui, port = _live(tmp_path, monkeypatch)

    def request(method, path, body=None):
        status, raw = _req(port, method, "/agent/api/" + path, body)
        return status, _json_body(raw)

    try:
        scoped = "config?profile=work"
        code, row = request("GET", scoped)
        assert code == 200 and row["agent"]["max_turns"] == 20
        patch = {"config": {"agent": {"max_turns": 30}}}
        code, staged = request("PUT", scoped, patch)
        assert code == 409 and staged["confirmation_id"]
        code, done = request("PUT", scoped, {**patch, "confirmation_id": staged["confirmation_id"]})
        assert code == 200 and done["applied_to_home"], done
        code, scoped_config = request("GET", scoped)
        assert code == 200 and scoped_config["agent"]["max_turns"] == 30
        assert request("GET", "config?profile=default")[1]["agent"]["max_turns"] == 10

        keys = "env?profile=work"
        raw_key = {"key": "LOCAL_FIXTURE_CUSTOM", "value": "fixture-only value", "confirm": True}
        code, status = request("PUT", keys, raw_key)
        assert code == 200 and "fixture-only value" not in str(status)
        code, scoped_keys = request("GET", keys)
        assert code == 200 and scoped_keys["LOCAL_FIXTURE_CUSTOM"]["is_set"]
        assert "fixture-only value" not in str(scoped_keys)
        assert "LOCAL_FIXTURE_CUSTOM" not in request("GET", "env?profile=default")[1]
        code, _ = request("DELETE", keys, {"key": "LOCAL_FIXTURE_CUSTOM", "confirm": True})
        assert code == 200
        assert "LOCAL_FIXTURE_CUSTOM" not in request("GET", keys)[1]
    finally:
        ui.stop()


def test_profile_gateway_and_log_reads_over_http(tmp_path, monkeypatch):
    from voicebridge import gateway_supervisor as GS
    from voicebridge import qubit_runtime

    root = tmp_path / "runtime"
    work = root / "profiles" / "work"
    shared = root / "profiles" / "shared"
    for home in (work, shared):
        (home / "logs").mkdir(parents=True)
        (home / "config.yaml").write_text("{}\n")
        (home / "logs" / "gateway.log").write_text(f"{home.name} log fixture\n")
    monkeypatch.setenv("QUBIT_HOME", str(root))
    qubit_runtime.ensure_path()
    from gateway import status as native_status
    from qubit_cli import web_server_gateway as native_routing

    monkeypatch.setattr(native_status, "multiplexer_liveness_for_profile",
                        lambda home: (77, {}) if home == shared else None)
    monkeypatch.setattr(native_routing, "_has_own_gateway", lambda _home: False)
    monkeypatch.setattr(GS, "status", lambda cfg, *, unit_override="", home=None:
                        {"state": "OK", "service_unit": unit_override,
                         "running": True, "available": True, "pid": 31})
    requests = []

    def fake_control(_cfg, action, *, confirmed, actor, unit_override="", home=None):
        requests.append((action, confirmed, unit_override, home))
        if not confirmed:
            return {"ok": False, "code": GS.CONFIRM_CODE, "state": "CONFIRM_REQUIRED"}
        return {"ok": True, "state": "RESTARTING"}

    monkeypatch.setattr(GS, "control", fake_control)
    ui, port = _live(tmp_path, monkeypatch)
    try:
        status, body = _req(port, "GET", "/agent/api/gateway?profile=work")
        assert status == 200 and _json_body(body)["process"]["service_unit"] == "qubit-gateway-work.service"
        status, body = _req(port, "GET", "/agent/api/gateway?profile=shared")
        assert status == 200 and _json_body(body)["process"]["pid"] == 77
        status, _ = _req(port, "POST", "/agent/api/gateway/restart?profile=shared", {"confirm": True})
        assert status == 503 and requests == []
        route = "/agent/api/gateway/restart?profile=work"
        assert _req(port, "POST", route, {"confirm": "false"})[0] == 409
        assert _req(port, "POST", route, {"confirm": True})[0] == 200
        assert requests == [
            ("restart", False, "qubit-gateway-work.service", work),
            ("restart", True, "qubit-gateway-work.service", work),
        ]
        status, body = _req(port, "GET", "/agent/api/logs?file=gateway&profile=work")
        assert status == 200 and _json_body(body)["lines"] == ["work log fixture\n"]
        status, body = _req(port, "GET", "/agent/api/logs?file=gateway&profile=shared")
        assert status == 200 and _json_body(body)["lines"] == ["shared log fixture\n"]
    finally:
        ui.stop()


def test_profile_mcp_and_skills_over_http(tmp_path, monkeypatch):
    import yaml

    root = tmp_path / "runtime"
    work = root / "profiles" / "work"
    work.mkdir(parents=True)
    for home in (root, work):
        (home / "config.yaml").write_text("{}\n")
    monkeypatch.setenv("QUBIT_HOME", str(root))

    ui, port = _live(tmp_path, monkeypatch)

    def request(method, path, payload=None):
        status, raw = _req(port, method, f"/agent/api/{path}", payload)
        return status, _json_body(raw)

    def approve(staged, sensitive=None):
        status, body = staged
        assert status == 409 and body["confirmation_id"], body
        payload = {"confirmation_id": body["confirmation_id"]}
        if sensitive is not None:
            payload["sensitive"] = sensitive
        status, body = request("POST", "ops/confirm", payload)
        assert status == 200 and body.get("state") == "executed", body

    try:
        mcp = "mcp/servers?profile=work"
        endpoint = "http://127.0.0.1:9/fixture"
        server = {"name": "fixture-mcp", "transport": "http", "url": endpoint,
                  "enabled": False}
        approve(request("POST", mcp, server), sensitive={"profile": "default"})
        assert request("GET", mcp)[1]["servers"][0]["url"] == endpoint
        original = yaml.safe_load((work / "config.yaml").read_text())
        original["mcp_servers"]["fixture-mcp"]["timeout"] = 23
        (work / "config.yaml").write_text(yaml.safe_dump(original))
        new_endpoint = f"{endpoint}-updated"
        assert request("PUT", mcp, {"name": "fixture-mcp", "url": new_endpoint,
                                    "confirm": True})[0] == 200
        native = yaml.safe_load((work / "config.yaml").read_text())["mcp_servers"]["fixture-mcp"]
        assert native["url"] == new_endpoint
        assert native["timeout"] == 23 and native["enabled"] is False
        assert request("GET", "mcp/servers?profile=default")[1]["servers"] == []

        name = "audit-profile-local-fixture"
        skill = f"---\nname: {name}\ndescription: Local test fixture\n---\n\nRead a sample note.\n"
        approve(request("POST", "skills?profile=work", {"name": name, "content": skill}))
        assert any(row["name"] == name for row in request("GET", "skills?profile=work")[1])
        assert all(row["name"] != name for row in request("GET", "skills?profile=default")[1])
        content_path = f"skills/content?profile=work&name={name}"
        assert request("GET", content_path)[1]["content"] == skill
        edited = skill.replace("Read", "Review")
        assert request("PUT", content_path, {"name": name, "content": edited,
                                              "confirm": True})[0] == 200
        assert request("GET", content_path)[1]["content"] == edited
        approve(request("PUT", "skills/toggle?profile=work", {"name": name, "enabled": False}))
        assert name in yaml.safe_load((work / "config.yaml").read_text())["skills"]["disabled"]
        assert yaml.safe_load((root / "config.yaml").read_text()) == {}
        assert request("DELETE", "skills?profile=work", {"name": name, "confirm": True})[0] == 200
        assert request("GET", content_path)[0] == 404
    finally:
        ui.stop()


def test_sessions_history_and_confirmations_follow_the_owning_profile(tmp_path, monkeypatch):
    from voicebridge import qubit_runtime

    root = tmp_path / "runtime"
    work = root / "profiles" / "work"
    work.mkdir(parents=True)
    monkeypatch.setenv("QUBIT_HOME", str(root))
    qubit_runtime.ensure_path()
    from qubit_state import SessionDB

    for home in (root, work):
        (home / "config.yaml").write_text("{}\n")
        db = SessionDB(home / "state.db")
        try:
            db.create_session("same-id", "cli", display_name=home.name)
            db.append_message("same-id", "user", f"message for {home.name}")
            db.create_session("empty-id", "cli")
            db.end_session("empty-id", "done")
            db.create_session("bulk-id", "cli")
            db.append_message("bulk-id", "user", f"bulk record for {home.name}")
            if home == work:
                db.create_session("work-child", "cli", parent_session_id="same-id")
        finally:
            db.close()

    ui, port = _live(tmp_path, monkeypatch)

    def request(method, path, body=None):
        code, raw = _req(port, method, f"/agent/api/{path}", body)
        return code, _json_body(raw)

    try:
        code, named = request("GET", "sessions?profile=work")
        assert code == 200
        assert named["sessions"] and {r["profile"] for r in named["sessions"]} == {"work"}
        assert "empty-id" in {r["id"] for r in named["sessions"]}
        assert request("GET", "sessions/same-id/messages?profile=work")[1]["messages"][0]["content"] == "message for work"
        assert request("GET", "sessions/same-id/export?profile=work")[1]["messages"][0]["content"] == "message for work"
        assert request("GET", "sessions/same-id/latest-descendant?profile=work")[1]["session_id"] == "work-child"
        assert request("GET", "sessions/same-id/latest-descendant?profile=current")[1]["session_id"] == "same-id"
        assert request("PATCH", "sessions/same-id?profile=current", {"profile": "work", "name": "conflict"})[0] == 400
        assert request("PATCH", "sessions/same-id", {"profile": "work", "name": "Renamed"})[0] == 200
        assert request("GET", "sessions/same-id?profile=work")[1]["title"] == "Renamed"
        assert request("GET", "sessions/same-id?profile=current")[1]["title"] == "runtime"

        assert request("GET", "sessions/empty/count?profile=work")[1]["count"] == 1
        code, pending = request("DELETE", "sessions/empty?profile=work")
        assert code == 409 and pending["confirmation_id"]
        code, result = request("POST", "ops/confirm", {
            "confirmation_id": pending["confirmation_id"], "sensitive": {"profile": "current"},
        })
        assert code == 200 and result["state"] == "executed", result
        assert request("GET", "sessions/empty/count?profile=work")[1]["count"] == 0
        assert request("GET", "sessions/empty/count?profile=current")[1]["count"] == 1

        assert request("DELETE", "sessions/same-id?profile=work&confirm=false")[0] == 409
        assert request("DELETE", "sessions/same-id?profile=work&confirm=true")[0] == 200
        assert request("GET", "sessions/same-id?profile=work")[0] == 404
        assert request("GET", "sessions/same-id?profile=current")[0] == 200
        assert request("PATCH", "qubit/chat/sessions/same-id?profile=work", {"archived": False})[0] == 200
        assert request("GET", "sessions/same-id?profile=work")[0] == 200

        code, pending = request("POST", "sessions/bulk-delete", {"profile": "work", "ids": ["bulk-id"]})
        assert code == 409 and pending["confirmation_id"]
        code, result = request("POST", "ops/confirm", {
            "confirmation_id": pending["confirmation_id"],
            "sensitive": {"profile": "current", "ids": ["same-id"]},
        })
        assert code == 200 and result["state"] == "executed", result
        assert request("GET", "sessions/bulk-id?profile=work")[0] == 404
        assert request("GET", "sessions/bulk-id?profile=current")[0] == 200
        assert request("GET", "sessions/same-id?profile=work")[0] == 200
    finally:
        ui.stop()


def test_qubit_chat_lifecycle_and_worker_boundary_use_the_selected_profile(tmp_path, monkeypatch):
    import asyncio
    from types import SimpleNamespace

    from voicebridge import chat_transport as CT, qubit_compat as HC, qubit_runtime
    from voicebridge.chat_stores import CommentStore, FileDraftStore

    root = tmp_path / "runtime"
    work = root / "profiles" / "work"
    work.mkdir(parents=True)
    monkeypatch.setenv("QUBIT_HOME", str(root))
    qubit_runtime.ensure_path()
    from qubit_state import SessionDB
    from tui_gateway import owner_forward

    for home in (root, work):
        (home / "config.yaml").write_text("model:\n  default: fixture-model\n")
        db = SessionDB(home / "state.db")
        try:
            db.create_session("same-chat", "cli", display_name=f"Chat in {home.name}")
            db.append_message("same-chat", "user", f"transcript in {home.name}")
        finally:
            db.close()
    FileDraftStore(work).put("same-chat", "fixture.txt", "named draft")
    CommentStore(work).add_comment("same-chat", "named comment")
    def unavailable(_home):
        raise owner_forward.OwnerNotRunning("no fixture gateway")

    monkeypatch.setattr(owner_forward, "discover_owner", unavailable)
    events = []
    monkeypatch.setattr(HC, "_publish_chat_frame", lambda _cfg, name, value: events.append((name, value)))

    def unexpected_execution(*_args, **_kwargs):
        raise AssertionError("The listener worker must not accept a named-profile turn")

    for name in ("stream_preflight", "cancel", "turn", "retry_last"):
        monkeypatch.setattr(CT, name, unexpected_execution)

    ui, port = _live(tmp_path, monkeypatch)

    def request(method, path, payload=None):
        status, raw = _req(port, method, f"/agent/api/qubit/chat{path}", payload)
        return status, _json_body(raw)

    try:
        base = "/sessions"
        named = "?profile=work"
        assert request("GET", base + named)[1]["sessions"][0]["name"] == "Chat in work"
        assert request("GET", base + "/same-chat")[1]["session"]["name"] == "Chat in runtime"
        assert request("GET", base + "/same-chat" + named)[1]["session"]["name"] == "Chat in work"
        assert request("GET", base + "/same-chat/messages" + named)[1]["messages"][0]["content"] == "transcript in work"
        status, export = request("GET", base + "/same-chat/export" + named)
        assert status == 200 and export["messages"][0]["content"] == "transcript in work"
        assert request("GET", base + "/same-chat/drafts" + named)[1]["drafts"][0]["path"] == "fixture.txt"
        assert request("GET", base + "/same-chat/drafts")[1]["drafts"] == []
        assert request("GET", base + "/same-chat/comments" + named)[1]["comments"][0]["body"] == "named comment"
        assert request("GET", base + "/same-chat/comments")[1]["comments"] == []
        export["session"]["id"] = "import-fixture"
        status, imported = request("POST", base + "/import" + named, {"envelope": export})
        assert status == 200 and imported["imported"] == 1, imported
        assert request("GET", base + "/import-fixture")[0] == 404
        assert request("GET", base + "/import-fixture/messages" + named)[1]["total"] == 1

        status, created = request("POST", base + named, {"name": "Named test"})
        assert status == 200 and created["session"]["model"] == "fixture-model", created
        sid = created["session"]["id"]
        db = SessionDB(work / "state.db")
        try:
            assert db.get_session(sid)["source"] == "gui"
        finally:
            db.close()
        assert not events
        assert request("GET", f"{base}/{sid}")[0] == 404
        assert request("GET", f"{base}/{sid}{named}")[0] == 200
        assert request("DELETE", f"{base}/{sid}{named}&confirm=false")[0] == 409
        assert request("DELETE", f"{base}/{sid}{named}&confirm=true")[0] == 200
        assert request("GET", f"{base}/{sid}{named}")[0] == 404
        assert not events

        for endpoint in ("", "/stream", "/retry", "/image"):
            status, out = request("POST", endpoint + named, {"text": "fixture", "session": "same-chat"})
            assert status == 409 and out["code"] == "CHAT_PROFILE_WORKER_UNAVAILABLE", out
        assert request("POST", "/cancel" + named, {"session": "same-chat"})[1]["code"] == "CHAT_NO_ACTIVE_TURN"
        assert request("GET", base + "/same-chat/messages")[1]["total"] == 1
        assert request("GET", base + "/same-chat/messages" + named)[1]["total"] == 1

        # Create while the dashboard sees no owner, then open using the real gateway authority.
        status, draft = request("POST", base + named, {"name": "Offline draft"})
        assert status == 200
        draft_id = draft["session"]["id"]
        from gateway import run
        from gateway.config import GatewayConfig
        from gateway.session import SessionStore
        from gateway.session_authority import initialize_session_authority
        from gateway.session_controls import AuthorityConnection
        from qubit_constants import reset_qubit_home_override, set_qubit_home_override

        monkeypatch.setattr(run, "_load_gateway_config",
                            lambda: {"model": {"provider": "openrouter", "default": "fixture-model"}})

        async def open_draft():
            token = set_qubit_home_override(work)
            store = SessionStore(work / "sessions", GatewayConfig())
            try:
                db = store._db
                db.create_session("old-dashboard", "dashboard")
                db.append_message("old-dashboard", "user", "preserved history")
                runner = SimpleNamespace(session_store=store, _session_db=db, adapters={}, _draining=False,
                                         _adapter_for_source=lambda source: None,
                                         _cached_agent_for=lambda route: None)
                authority = await initialize_session_authority(runner, profile_id="work", instance_id="fixture")
                identity = {"user_id": "owner", "provider": "local", "profile_id": "work",
                            "instance_id": "fixture", "native_bootstrap": True}
                owner = AuthorityConnection(authority, object(), identity)
                try:
                    reply = await owner.dispatch({"id": 1, "method": "session.resume",
                                                  "params": {"session_id": draft_id}})
                    assert reply.get("result", {}).get("stored_session_id") == draft_id, reply
                    assert db.get_session(draft_id)["chat_id"] == draft_id
                    history = await owner.dispatch({"id": 2, "method": "session.resume",
                                                    "params": {"session_id": "old-dashboard"}})
                    assert history["result"]["messages"][0]["content"] == "preserved history"
                    refused = await owner.dispatch({"id": 3, "method": "prompt.submit",
                                                    "params": {"session_id": "old-dashboard", "text": "later",
                                                               "submission_id": "fixture"}})
                    assert refused["error"]["data"]["reason"] == "session_read_only"
                    assert db.get_session("old-dashboard")["chat_id"] is None
                finally:
                    await owner.close()
            finally:
                store.close_all_db_handles()
                reset_qubit_home_override(token)

        asyncio.run(open_draft())
    finally:
        ui.stop()


def test_named_qubit_chat_stream_uses_its_gateway_admission(tmp_path, monkeypatch):
    import asyncio
    from contextlib import asynccontextmanager

    from voicebridge import chat_transport as CT, profile_chat, qubit_runtime

    root = tmp_path / "runtime"
    work = root / "profiles" / "work"
    work.mkdir(parents=True)
    monkeypatch.setenv("QUBIT_HOME", str(root))
    qubit_runtime.ensure_path()
    from qubit_state import SessionDB
    from tui_gateway import owner_forward

    for home in (root, work):
        (home / "config.yaml").write_text("{}\n")
        db = SessionDB(home / "state.db")
        db.create_session("same-chat", "gui")
        db.close()

    calls = []

    def discover(home):
        assert home == work
        return "fixture-owner"

    class Client:
        def __init__(self):
            self.events = asyncio.Queue()
            self.waiting = True

        async def rpc(self, method, **kwargs):
            calls.append((method, kwargs))
            if method == "session.create":
                assert kwargs["source"] == "gui" and kwargs["request_id"]
                db = SessionDB(work / "state.db")
                db.create_session("native-chat", "gui")
                db.close()
                return {"stored_session_id": "native-chat"}
            if method == "session.resume":
                return {"session_id": "owner-live-handle", "stored_session_id": "same-chat"}
            if method == "prompt.submit":
                return {"admission_id": "ours", "status": "queued"}
            if method == "prompt.receipt" and self.waiting:
                self.waiting = False
                for admission_id, kind, payload in (
                    ("foreign", "message.delta", {"text": "foreign delta"}),
                    ("ours", "message.delta", {"text": "named delta"}),
                    ("ours", "message.complete", {}),
                ):
                    self.events.put_nowait({"params": {"admission_id": admission_id,
                                                       "type": kind, "payload": payload}})
                return {"status": "queued"}
            if method == "prompt.receipt":
                assert kwargs == {"session_id": "owner-live-handle", "admission_id": "ours",
                                  "include_result": True}
                db = SessionDB(work / "state.db")
                db.append_message("same-chat", "user", "fixture question")
                db.append_message("same-chat", "assistant", "named reply")
                db.close()
                return {"status": "terminal", "outcome": "completed",
                        "result": {"final_response": "named reply"}}
            raise AssertionError(method)

    @asynccontextmanager
    async def connect(home, endpoint):
        assert home == work and endpoint == "fixture-owner"
        yield Client()

    def unexpected_legacy_preflight(*_args, **_kwargs):
        raise AssertionError("Named turns must bypass the listener's worker")

    monkeypatch.setattr(owner_forward, "discover_owner", discover)
    monkeypatch.setattr(owner_forward, "connect_owner", connect)
    monkeypatch.setattr(CT, "stream_preflight", unexpected_legacy_preflight)
    monkeypatch.setattr(profile_chat, "_RECEIPT_INTERVAL", 0.01)
    monkeypatch.setattr(profile_chat, "_HEARTBEAT_INTERVAL", 0)
    ui, port = _live(tmp_path, monkeypatch)
    try:
        sessions = "/agent/api/qubit/chat/sessions"
        status, raw = _req(port, "POST", sessions + "?profile=work", {})
        assert status in (200, 201) and b"native-chat" in raw
        assert _req(port, "GET", sessions + "/native-chat?profile=work")[0] == 200
        assert _req(port, "GET", sessions + "/native-chat")[0] == 404
        assert [method for method, _ in calls] == ["session.create"]
        calls.clear()
        status, raw = _req(port, "POST", "/agent/api/qubit/chat/stream?profile=work",
                           {"session": "same-chat", "text": "fixture question"})
        assert status == 200
        assert b": keep-alive\n\n" in raw and b"event: heartbeat" not in raw
        assert b"event: delta" in raw and b"named delta" in raw and b"foreign delta" not in raw
        assert b"event: done" in raw and b"named reply" in raw
        assert [method for method, _ in calls] == ["session.resume", "prompt.submit", "prompt.receipt", "prompt.receipt"]
        path = "/agent/api/qubit/chat/sessions/same-chat/messages"
        assert _json_body(_req(port, "GET", path + "?profile=work")[1])["total"] == 2
        assert _json_body(_req(port, "GET", path)[1])["total"] == 0
    finally:
        ui.stop()


def test_profile_chat_cancel_targets_only_the_admitted_turn(tmp_path, monkeypatch):
    import asyncio
    from contextlib import asynccontextmanager

    from voicebridge import profile_chat, qubit_runtime

    qubit_runtime.ensure_path()
    from tui_gateway import owner_forward

    calls = []

    class Client:
        def __init__(self):
            self.events = asyncio.Queue()

        async def rpc(self, method, **kwargs):
            calls.append((method, kwargs))
            if method == "session.resume":
                return {"session_id": "live", "stored_session_id": "same-chat"}
            if method == "prompt.submit":
                return {"admission_id": "selected", "status": "queued"}
            if method == "prompt.cancel":
                return {"status": "terminal", "outcome": "cancelled"}
            raise AssertionError(method)

    @asynccontextmanager
    async def connect(home, endpoint):
        assert home == tmp_path and endpoint == "owner"
        yield Client()

    monkeypatch.setattr(owner_forward, "connect_owner", connect)
    monkeypatch.setattr(owner_forward, "discover_owner", lambda _home: "owner")
    from contextlib import closing

    with closing(profile_chat.events(tmp_path, "owner", "same-chat", "query")) as events:
        assert next(events)["event"] == "start"
        assert profile_chat.cancel(tmp_path / "sibling", "same-chat")[0] == 404
        assert profile_chat.cancel(tmp_path, "same-chat")[0] == 200
        assert calls[-1] == ("prompt.cancel", {"session_id": "live", "admission_id": "selected"})
    assert profile_chat.cancel(tmp_path, "same-chat")[0] == 404


def test_profile_chat_disconnect_does_not_resubmit(tmp_path, monkeypatch):
    from contextlib import asynccontextmanager

    from voicebridge import profile_chat, qubit_runtime

    qubit_runtime.ensure_path()
    from qubit_cli.gateway_client import GatewayClientError
    from tui_gateway import owner_forward

    submits = []

    class Client:
        async def rpc(self, method, **kwargs):
            if method == "session.resume":
                return {"session_id": "live", "stored_session_id": "chat"}
            assert method == "prompt.submit"
            submits.append(kwargs)
            raise GatewayClientError("fixture disconnect after submission")

    @asynccontextmanager
    async def connect(home, endpoint):
        assert home == tmp_path and endpoint == "owner"
        yield Client()

    monkeypatch.setattr(owner_forward, "connect_owner", connect)
    events = list(profile_chat.events(tmp_path, "owner", "chat", "question"))
    assert len(submits) == 1 and submits[0]["submission_id"]
    assert events[0]["code"] == "CHAT_ADMISSION_UNCONFIRMED"
    assert profile_chat.cancel(tmp_path, "chat")[0] == 404
