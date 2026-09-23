"""Hermes API parity wave 1 — real registered handlers, no 404/stub.

Closes the wave-1 open compat rows by dispatching each target route through
``voicebridge.qubit_compat.match`` / ``dispatch`` and asserting the real
non-refusal success path (real reads, persisted writes, and the loopback
native-auth handshake). No mock replaces the production caller: schedules,
pairing, webhooks, profiles, sessions, plugins, messaging, MCP inventory,
gateway drain, the event journal publish, and the dashboard-auth store are all
the same modules the CLI/voice/dashboard use.

The tests are hermetic (temp ``XDG_*`` + ``voicebridge.config.Config``) and
never touch the network.
"""
from __future__ import annotations

import base64
import hashlib
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


_PROVIDER = "acme"
_PROVIDER_ENV = "ACME_API_KEY"


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    text = (
        "logging:\n  jsonl: false\n"
        "intent:\n  frontier:\n    enabled: false\n    providers:\n"
        f"      {_PROVIDER}:\n        key_ref: \"env:{_PROVIDER_ENV}\"\n"
        "mcp:\n  servers:\n    wave1-mcp:\n      command: echo\n"
    )
    p = tmp_path / "voice.yaml"
    p.write_text(text)
    return C.Config.load(p)


def _seed_schedule(cfg):
    from voicebridge import schedules as S

    created = S.create(cfg, "wave1-job", "summarize notes",
                       "every 60 minutes", requested_by="t", confirmed=True)
    assert created.get("state") == "OK", created
    return int(created["job"]["id"])


def _seed_skill(cfg, skill_id="wave1.plugin"):
    from voicebridge.permissions import SkillManifest, Tier, permissions_store

    manifest = SkillManifest(
        skill_id=skill_id, version="1.0.0", name="Wave1 Plugin",
        description="synthetic wave-1 plugin",
        source="https://example.test/skills", revision="abc123",
        license="MIT", content_hash="a" * 64,
        capabilities=("web.observe.metadata",), scopes=("web",),
        tier=Tier.READ_ONLY, sensitivity="internal", approval_policy="auto",
        timeout_s=30.0, resource_limits={"max_bytes": 4096},
        audit_events=("skill.run",), compatibility="linux",
        rollback="remove restores prior manifest state")
    permissions_store(cfg).install_skill(manifest, requested_by="t",
                                         confirmed=True)
    return skill_id


def _seed_receipt(cfg, rid="wave1-rec"):
    state = Path(cfg.state_dir())
    state.mkdir(parents=True, exist_ok=True)
    (state / "actions.json").write_text(json.dumps(
        {"schema": 1,
         "receipts": {rid: {"receipt_id": rid, "action_id": "act.wave1",
                            "state": "OK", "code": "OK",
                            "runtime": "qubit-broker", "created_at": 1.0,
                            "detail": "wave1 receipt detail"}},
         "audit": []}))
    return rid


def _seed_profile(cfg, name="wave1"):
    """Seed a real reference profile — the one registry ``/api/profiles`` serves."""
    from voicebridge import qubit_compat as HC

    P = HC._profiles_module(cfg)
    if not P.profile_exists(name):
        P.create_profile(name, no_skills=True)
    return name


def _seed_account(cfg, account_id="wave1-msg"):
    from voicebridge import messaging as M

    M.account_add(cfg, account_id, "slack", "Wave1 Slack",
                  key_ref="env:SLACK_BOT_TOKEN", requested_by="t",
                  confirmed=True)
    return account_id


def _seed_webhook(cfg, sub_id="wave1-hook"):
    from voicebridge import webhooks as W

    W.add(cfg, sub_id, "http://127.0.0.1:9/hook", ["message"],
          "env:WAVE1_HOOK", requested_by="t", confirmed=True)
    return sub_id


# --------------------------------------------------------------------------
# (method, path, handler) — resolution + registration evidence.
# --------------------------------------------------------------------------
WAVE1_ROUTES = [
    ("WEBSOCKET", "/api/pub", "h_pub"),
    ("POST", "/api/pub", "h_pub"),
    ("POST", "/api/cron/jobs/1/pause", "cron_pause"),
    ("POST", "/api/cron/jobs/1/resume", "cron_resume"),
    ("POST", "/api/cron/jobs/1/trigger", "cron_trigger"),
    ("GET", "/api/auth/providers", "h_auth_providers"),
    ("GET", "/login", "h_login_page"),
    ("GET", "/auth/login", "h_auth_login"),
    ("GET", "/auth/native/authorize", "h_auth_native_authorize"),
    ("GET", "/auth/callback", "h_auth_callback"),
    ("POST", "/auth/password-login", "h_auth_password_login"),
    ("POST", "/auth/logout", "h_auth_logout"),
    ("POST", "/auth/native/token", "h_auth_native_token"),
    ("POST", "/auth/native/refresh", "h_auth_native_refresh"),
    ("DELETE", "/api/dashboard/agent-plugins/wave1.plugin", "agent_remove"),
    ("GET", "/api/dashboard/plugins/rescan", "plugins_rescan"),
    ("PUT", "/api/dashboard/font", "font_set"),
    ("PUT", "/api/dashboard/theme", "h_theme_set"),
    ("POST", "/api/dashboard/plugins/wave1/plugin/visibility",
     "h_plugin_visibility_set"),
    ("POST", "/api/gateway/drain", "h_gateway_drain"),
    ("GET", "/api/mcp/servers", "h_mcp_servers"),
    ("GET", "/api/mcp/catalog", "h_mcp_catalog_live"),
    ("POST", "/api/mcp/servers/wave1-mcp/test", "h_mcp_test_local"),
    ("POST", "/api/messaging/platforms/wave1-msg/test", "h_msg_test_live"),
    ("PUT", "/api/messaging/platforms/wave1-msg", "msg_update"),
    ("POST", "/api/pairing/approve", "pairing_approve"),
    ("POST", "/api/pairing/clear-pending", "pairing_clear_pending"),
    ("POST", "/api/pairing/revoke", "pairing_revoke"),
    ("POST", "/api/webhooks", "webhooks_add"),
    ("POST", "/api/webhooks/enable", "webhooks_enable"),
    ("PUT", "/api/webhooks/wave1-hook/enabled", "webhooks_enabled"),
    ("POST", "/api/profiles/active", "profiles_active_set"),
    ("PUT", "/api/profiles/wave1/description", "profile_desc"),
    ("PUT", "/api/profiles/wave1/model", "profile_model"),
    ("PUT", "/api/profiles/wave1/soul", "profile_soul_set"),
    ("GET", "/api/sessions/empty/count", "sess_empty"),
    ("GET", "/api/sessions/wave1-rec", "h_session_show"),
    ("GET", "/api/sessions/wave1-rec/export", "h_session_show"),
    ("GET", "/api/sessions/wave1-rec/latest-descendant", "h_session_show"),
    ("GET", "/api/sessions/wave1-rec/messages", "h_session_messages"),
    ("PATCH", "/api/sessions/wave1-rec", "h_session_patch"),
    # Ambiguity closure: real agent-plugin permission toggles, the local
    # memory-provider read, the session resume transition, and the debug-share
    # diagnostics op -- each a real registered handler, not a stub.
    ("POST", "/api/dashboard/agent-plugins/wave1-toggle.plugin/enable",
     "agent_enable"),
    ("POST", "/api/dashboard/agent-plugins/wave1-toggle.plugin/disable",
     "agent_disable"),
    ("GET", "/api/memory/providers/qubit-local", "mem_prov"),
    ("POST", "/api/sessions/wave1-rec/resume", "h_session_resume"),
    ("POST", "/api/ops/debug-share", "debug_share"),
]


def test_wave1_routes_resolve_to_registered_real_handlers():
    from voicebridge import qubit_compat as HC

    for method, path, handler in WAVE1_ROUTES:
        route, _params = HC.match(method, path)
        assert route is not None, (method, path)
        assert route["handler"] == handler, (method, path, route["handler"])
        assert route["disposition"] == "implemented", (method, path, route)
        assert callable(HC.HANDLERS.get(handler)), (method, path, handler)


# --------------------------------------------------------------------------
# (method, path, query, body) — dispatched and asserted 200.
# --------------------------------------------------------------------------
WAVE1_DISPATCH = [
    ("POST", "/api/cron/jobs/1/pause", {}, {"confirm": True}),
    ("POST", "/api/cron/jobs/1/resume", {}, {"confirm": True}),
    ("POST", "/api/cron/jobs/1/trigger", {}, {}),
    ("GET", "/api/auth/providers", {}, {}),
    ("GET", "/login", {}, {}),
    ("GET", "/auth/login", {"provider": _PROVIDER}, {}),
    ("DELETE", "/api/dashboard/agent-plugins/wave1.plugin",
     {}, {"confirm": True}),
    ("GET", "/api/dashboard/plugins/rescan", {}, {}),
    ("PUT", "/api/dashboard/font", {}, {"font": "Inter"}),
    ("PUT", "/api/dashboard/theme", {}, {"theme": "qubit"}),
    ("POST", "/api/dashboard/plugins/wave1-plugin/visibility",
     {}, {"visible": False}),
    ("POST", "/api/gateway/drain", {}, {"action": "drain"}),
    ("POST", "/api/gateway/drain", {}, {"action": "cancel"}),
    ("GET", "/api/mcp/servers", {}, {}),
    ("GET", "/api/mcp/catalog", {}, {}),
    ("POST", "/api/mcp/servers/wave1-mcp/test", {}, {}),
    ("POST", "/api/messaging/platforms/wave1-msg/test", {}, {}),
    ("PUT", "/api/messaging/platforms/wave1-msg", {},
     {"enabled": False, "confirm": True}),
    ("POST", "/api/pairing/clear-pending", {}, {"confirm": True}),
    ("POST", "/api/webhooks", {},
     {"id": "wave1-hook", "url": "http://127.0.0.1:9/hook",
      "events": ["message"], "secret_ref": "env:WAVE1_HOOK",
      "confirm": True}),
    ("POST", "/api/webhooks/enable", {}, {"enabled": True, "confirm": True}),
    ("PUT", "/api/webhooks/wave1-hook/enabled", {},
     {"enabled": True, "confirm": True}),
    ("POST", "/api/profiles/active", {}, {"active": "wave1"}),
    ("PUT", "/api/profiles/wave1/description",
     {}, {"confirm": True, "value": "wave1 description"}),
    ("PUT", "/api/profiles/wave1/model",
     {}, {"confirm": True, "provider": "opencode-go",
          "model": "deepseek-v4-flash"}),
    ("PUT", "/api/profiles/wave1/soul",
     {}, {"confirm": True, "value": "wave1 soul"}),
    ("GET", "/api/sessions/empty/count", {}, {}),
    ("GET", "/api/sessions/wave1-rec", {}, {}),
    ("GET", "/api/sessions/wave1-rec/export", {}, {}),
    ("GET", "/api/sessions/wave1-rec/latest-descendant", {}, {}),
    ("GET", "/api/sessions/wave1-rec/messages", {}, {}),
    ("PATCH", "/api/sessions/wave1-rec", {}, {"name": "wave1 renamed"}),
    ("POST", "/api/dashboard/agent-plugins/wave1-toggle.plugin/enable",
     {}, {"confirm": True}),
    ("POST", "/api/dashboard/agent-plugins/wave1-toggle.plugin/disable",
     {}, {"confirm": True}),
    ("GET", "/api/memory/providers/qubit-local", {}, {}),
    ("POST", "/api/sessions/wave1-rec/resume", {}, {}),
    ("POST", "/api/ops/debug-share", {}, {}),
    ("WEBSOCKET", "/api/pub", {}, {"channel": "wave1", "data": "hello"}),
    ("POST", "/api/pub", {}, {"channel": "wave1", "data": "hello"}),
]


def test_wave1_dispatch_non_refusal_success(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC

    monkeypatch.setenv("SLACK_BOT_TOKEN", "not-a-real-token")
    monkeypatch.setenv(_PROVIDER_ENV, "not-a-real-key")
    cfg = _cfg(tmp_path, monkeypatch)
    _seed_schedule(cfg)
    _seed_skill(cfg)
    _seed_skill(cfg, "wave1-toggle.plugin")
    _seed_receipt(cfg)
    _seed_profile(cfg)
    _seed_account(cfg)

    for method, path, q, b in WAVE1_DISPATCH:
        code, out = HC.dispatch(cfg, method, path, q, b)
        assert code == 200, (method, path, code, str(out)[:200])
        assert isinstance(out, dict) and out.get("ok") is not False, \
            (method, path, out)


# --------------------------------------------------------------------------
# Persistence / state transitions the ledger expects behind the 200.
# --------------------------------------------------------------------------
def test_cron_pause_resume_persist_state(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC
    from voicebridge import schedules as S

    cfg = _cfg(tmp_path, monkeypatch)
    job_id = _seed_schedule(cfg)

    code, _out = HC.dispatch(cfg, "POST", f"/api/cron/jobs/{job_id}/pause",
                             {}, {"confirm": True})
    assert code == 200
    assert S.show(cfg, job_id)["job"]["enabled"] is False

    code, _out = HC.dispatch(cfg, "POST", f"/api/cron/jobs/{job_id}/resume",
                             {}, {"confirm": True})
    assert code == 200
    assert S.show(cfg, job_id)["job"]["enabled"] is True


def test_pairing_approve_then_revoke_over_compat(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC
    from voicebridge import pairing as P

    cfg = _cfg(tmp_path, monkeypatch)
    created = P.create(cfg, "wave1", ["chat"], requested_by="t",
                       confirmed=True)
    assert created["state"] == "OK"

    code, out = HC.dispatch(cfg, "POST", "/api/pairing/approve", {},
                            {"code": created["code"], "confirm": True})
    assert code == 200 and out["ok"] is True, (code, out)

    code, out = HC.dispatch(cfg, "POST", "/api/pairing/revoke", {},
                            {"code_id": created["code_id"], "confirm": True})
    assert code == 200 and out["ok"] is True, (code, out)
    code, out = HC.dispatch(cfg, "POST", "/api/pairing/clear-pending", {},
                            {"confirm": True})
    assert code == 200 and out["ok"] is True, (code, out)
    records = {c["id"]: c for c in P.list_codes(cfg)["codes"]}
    assert records[created["code_id"]]["revoked"] is True, records


def test_webhook_add_enable_and_subscription_toggle(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC
    from voicebridge import webhooks as W

    cfg = _cfg(tmp_path, monkeypatch)
    _seed_webhook(cfg)

    code, out = HC.dispatch(cfg, "POST", "/api/webhooks/enable", {},
                            {"enabled": False, "confirm": True})
    assert code == 200 and out["ok"] is True, (code, out)
    assert W.is_enabled(cfg) is False

    code, out = HC.dispatch(cfg, "PUT",
                            "/api/webhooks/wave1-hook/enabled", {},
                            {"enabled": False, "confirm": True})
    assert code == 200 and out["ok"] is True, (code, out)
    sub = W.show(cfg, "wave1-hook")["subscription"]
    assert sub["enabled"] is False, sub


def test_dashboard_font_theme_and_visibility_persist(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)

    code, _out = HC.dispatch(cfg, "PUT", "/api/dashboard/font", {},
                             {"font": "Inter"})
    assert code == 200
    code, out = HC.dispatch(cfg, "GET", "/api/dashboard/font", {}, {})
    assert code == 200 and out["font"] == "Inter", out

    code, _out = HC.dispatch(cfg, "PUT", "/api/dashboard/theme", {},
                             {"theme": "qubit"})
    assert code == 200
    code, out = HC.dispatch(cfg, "GET", "/api/dashboard/themes", {}, {})
    assert code == 200 and out["active"] == "qubit", out

    code, out = HC.dispatch(
        cfg, "POST", "/api/dashboard/plugins/wave1/plugin/visibility", {},
        {"visible": False})
    assert code == 200 and out["visible"] is False, (code, out)
    hidden = json.loads(
        (Path(cfg.state_dir()) / "qubit_hidden.json").read_text())
    assert "wave1/plugin" in hidden.get("hidden", []), hidden


def test_gateway_drain_marker_roundtrip(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    marker = Path(cfg.state_dir()) / "gateway_drain.json"

    code, out = HC.dispatch(cfg, "POST", "/api/gateway/drain", {},
                            {"action": "drain"})
    assert code == 200 and out["draining"] is True, (code, out)
    assert marker.is_file()
    assert "requested_at" in json.loads(marker.read_text())

    code, out = HC.dispatch(cfg, "POST", "/api/gateway/drain", {},
                            {"action": "cancel"})
    assert code == 200 and out["was_draining"] is True, (code, out)
    assert not marker.exists()


def test_mcp_inventory_reads_are_real_and_never_connect(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)

    code, out = HC.dispatch(cfg, "GET", "/api/mcp/servers", {}, {})
    assert code == 200
    assert [s["name"] for s in out["servers"]] == ["wave1-mcp"], out

    code, out = HC.dispatch(cfg, "GET", "/api/mcp/catalog", {}, {})
    assert code == 200
    assert [e["name"] for e in out["entries"]] == ["wave1-mcp"], out
    assert out["diagnostics"], out

    code, out = HC.dispatch(cfg, "POST", "/api/mcp/servers/wave1-mcp/test",
                            {}, {})
    assert code == 200 and out["connected"] is False, (code, out)
    assert out["network_probe"] is False
    assert out["tools"] == []

    code, out = HC.dispatch(cfg, "POST", "/api/mcp/servers/absent/test",
                            {}, {})
    assert code == 404 and out["ok"] is False, (code, out)


def test_messaging_platform_test_reads_real_link_state(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    monkeypatch.setenv("SLACK_BOT_TOKEN", "present")
    _seed_account(cfg)

    code, out = HC.dispatch(cfg, "POST",
                            "/api/messaging/platforms/wave1-msg/test", {}, {})
    assert code == 200 and out["ok"] is True, (code, out)
    assert out["state"] in ("linked", "degraded"), out
    assert out["reachable"] is True, out


def test_event_publish_appends_to_single_journal(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    code, out = HC.dispatch(cfg, "POST", "/api/pub", {},
                            {"channel": "wave1", "data": "hello"})
    assert code == 200 and out["published"] is True, (code, out)
    assert isinstance(out["seq"], int) and out["seq"] >= 1, out
    journal = Path(cfg.state_dir()) / "typed-events.jsonl"
    assert journal.is_file()
    assert "hello" in journal.read_text()


def test_session_routes_read_and_patch_the_receipt(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC
    from voicebridge.auth_status import session_store

    cfg = _cfg(tmp_path, monkeypatch)
    _seed_receipt(cfg)

    code, out = HC.dispatch(cfg, "GET", "/api/sessions/wave1-rec", {}, {})
    assert code == 200 and out["id"] == "wave1-rec", (code, out)

    code, out = HC.dispatch(cfg, "PATCH", "/api/sessions/wave1-rec", {},
                            {"name": "wave1 renamed"})
    assert code == 200 and out["ok"] is True, (code, out)
    assert session_store(cfg).meta("wave1-rec")["name"] == "wave1 renamed"


# --------------------------------------------------------------------------
# Qubit-owned dashboard auth (loopback identity + RFC 8252 native handshake).
# --------------------------------------------------------------------------
def _pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii")


def _authorize(cfg, HC, verifier):
    challenge = _pkce_challenge(verifier)
    code, out = HC.dispatch(
        cfg, "GET", "/auth/native/authorize",
        {"provider": "loopback", "code_challenge": challenge,
         "code_challenge_method": "S256",
         "redirect_uri": "http://127.0.0.1:8765/callback",
         "state": "client-state"}, {})
    assert code == 200, (code, out)
    return out


def test_auth_native_authorize_local_loopback_success(
        tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    verifier = "verifier-native-abcdefghijklmnopqrstuvwxyz"
    code, out = HC.dispatch(
        cfg, "GET", "/auth/native/authorize",
        {"provider": "loopback", "code_challenge": _pkce_challenge(verifier),
         "code_challenge_method": "S256",
         "redirect_uri": "http://127.0.0.1:8765/callback",
         "state": "client-state"}, {})
    assert code == 200 and out["ok"] is True, (code, out)
    assert out["authorize_url"].startswith("http://127.0.0.1:8765/callback?")
    assert out["code"] and out["broker_state"]


def test_dashboard_auth_native_loopback_flow(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)

    code, out = HC.dispatch(cfg, "GET", "/api/auth/providers", {}, {})
    assert code == 200
    names = {p["name"] for p in out["providers"]}
    assert _PROVIDER in names, out
    assert out["mode"] == "loopback-token", out

    code, out = HC.dispatch(cfg, "GET", "/login", {}, {})
    assert code == 200 and out["loopback"] is True, (code, out)

    code, out = HC.dispatch(cfg, "GET", "/auth/login",
                            {"provider": _PROVIDER}, {})
    assert code == 200 and out["handoff"]["state"] == "OK", (code, out)

    v1 = "verifier-one-abcdefghijklmnopqrstuvwxyz"
    auth1 = _authorize(cfg, HC, v1)
    assert auth1["authorize_url"].startswith("http://127.0.0.1:8765/callback?")
    code, out = HC.dispatch(
        cfg, "GET", "/auth/callback",
        {"code": auth1["code"], "state": auth1["broker_state"]}, {})
    assert code == 200 and out["ok"] is True, (code, out)
    assert out["session"]["access_token"], out

    v2 = "verifier-two-abcdefghijklmnopqrstuvwxyz"
    auth2 = _authorize(cfg, HC, v2)
    code, out = HC.dispatch(
        cfg, "POST", "/auth/native/token",
        {}, {"code": auth2["code"], "code_verifier": v2})
    assert code == 200 and out["ok"] is True, (code, out)
    access, refresh = out["access_token"], out["refresh_token"]
    assert access and refresh

    code, out = HC.dispatch(
        cfg, "POST", "/auth/native/refresh", {},
        {"refresh_token": refresh, "provider": "loopback"})
    assert code == 200 and out["ok"] is True, (code, out)
    access, refresh = out["access_token"], out["refresh_token"]

    code, out = HC.dispatch(
        cfg, "POST", "/auth/password-login", {},
        {"provider": "loopback", "token": access})
    assert code == 200 and out["ok"] is True, (code, out)
    rotated = out["refresh_token"]

    code, out = HC.dispatch(cfg, "POST", "/auth/logout", {}, {})
    assert code == 200 and out["logged_out"] is True, (code, out)

    code, out = HC.dispatch(
        cfg, "POST", "/auth/native/refresh", {}, {"refresh_token": rotated})
    assert code == 401, (code, out)


def test_dashboard_auth_native_rejects_non_loopback_redirect(
        tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    code, out = HC.dispatch(
        cfg, "GET", "/auth/native/authorize",
        {"code_challenge": _pkce_challenge("v"),
         "code_challenge_method": "S256",
         "redirect_uri": "https://evil.example/callback"}, {})
    assert code == 400 and out["code"] == "AUTH_BAD_REDIRECT", (code, out)

    code, out = HC.dispatch(
        cfg, "GET", "/auth/native/authorize",
        {"code_challenge": _pkce_challenge("v"),
         "code_challenge_method": "plain",
         "redirect_uri": "http://127.0.0.1:8765/callback"}, {})
    assert code == 400 and out["code"] == "AUTH_BAD_CHALLENGE_METHOD", \
        (code, out)

    code, out = HC.dispatch(
        cfg, "POST", "/auth/native/token", {},
        {"code": "nope", "code_verifier": "wrong"})
    assert code == 400 and out["code"] == "AUTH_BAD_CODE", (code, out)
