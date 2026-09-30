"""QBPV Agent 4 (D) — gateway RPC session-sharing coverage.

The pinned v2026.9.4 gateway added session-sharing / session-repository
methods (``src/gateway/server-methods/sessions-sharing.ts``). Qubit runs no
second gateway or cloud repository, so these are exposed through the one
in-process ``MethodRegistry`` delegating to the real
``voicebridge/session_share.py`` backend with the shared confirmation/e-stop
gates. This proves the methods dispatch and stay honest (token shown once,
typed refusals, no fabricated remote artifacts).
"""
from __future__ import annotations

import json
from pathlib import Path


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _seed(cfg, rid="rec-a", detail="invoice alpha sent"):
    Path(cfg.state_dir()).mkdir(parents=True, exist_ok=True)
    receipts = {rid: {"receipt_id": rid, "action_id": "act.send",
                      "state": "OK", "code": "OK", "runtime": "qubit-broker",
                      "created_at": 1.0, "detail": detail}}
    (Path(cfg.state_dir()) / "actions.json").write_text(
        json.dumps({"schema": 1, "receipts": receipts, "audit": []}))


def _reg():
    from voicebridge.gateway.rpc import default_registry

    return default_registry()


def test_registry_exposes_session_sharing_methods():
    from voicebridge.gateway.rpc import (
        default_registry, GatewayError, METHOD_BAD_PARAMS)

    reg = default_registry()
    assert {"sessions.sharing", "session.repository_artifacts"} <= set(
        reg.names())
    try:
        reg.handle(None, "sessions.sharing", {"action": "nope"})
        raise AssertionError("expected typed bad params")
    except GatewayError as exc:
        assert exc.code == METHOD_BAD_PARAMS


def test_sessions_sharing_publish_confirm_and_token_once(
        tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    _seed(cfg)
    reg = _reg()

    status = reg.handle(cfg, "sessions.sharing", {"action": "status"})
    assert status["state"] == "OK"

    denied = reg.handle(cfg, "sessions.sharing",
                        {"action": "publish", "session_id": "rec-a"})
    assert denied["state"] == "CONFIRMATION_REQUIRED", denied

    out = reg.handle(cfg, "sessions.sharing",
                     {"action": "publish", "session_id": "rec-a",
                      "ttl_s": 120, "confirmed": True})
    assert out["state"] == "OK", out
    token = out["token"]
    assert token
    listed = json.dumps(reg.handle(cfg, "sessions.sharing",
                                   {"action": "list"}))
    assert token not in listed and "token_hash" not in listed

    resolved = reg.handle(cfg, "sessions.sharing",
                          {"action": "resolve",
                           "share_id": out["share"]["id"], "token": token})
    assert resolved["state"] == "OK"
    assert resolved["transcript"]["session_id"] == "rec-a"
    assert token not in json.dumps(resolved)

    bad = reg.handle(cfg, "sessions.sharing",
                     {"action": "resolve", "share_id": out["share"]["id"],
                      "token": "wrong"})
    assert bad["state"] in ("DENIED", "REVOKED", "EXPIRED"), bad


def test_sessions_sharing_revoke_is_typed(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    _seed(cfg, rid="rec-b", detail="invoice beta drafted")
    reg = _reg()
    out = reg.handle(cfg, "sessions.sharing",
                     {"action": "publish", "session_id": "rec-b",
                      "ttl_s": 600, "confirmed": True})
    sid = out["share"]["id"]
    unconfirmed = reg.handle(cfg, "sessions.sharing",
                             {"action": "revoke", "share_id": sid})
    assert unconfirmed["state"] == "CONFIRMATION_REQUIRED", unconfirmed
    revoked = reg.handle(cfg, "sessions.sharing",
                         {"action": "revoke", "share_id": sid,
                          "confirmed": True})
    assert revoked["state"] == "OK" and revoked["changed"] is True
    dead = reg.handle(cfg, "sessions.sharing",
                      {"action": "resolve", "share_id": sid,
                       "token": out["token"]})
    assert dead["state"] == "REVOKED", dead


def test_session_repository_artifacts_is_read_only_and_typed_unavailable(
        tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    _seed(cfg)
    reg = _reg()
    out = reg.handle(cfg, "session.repository_artifacts", {})
    assert out["state"] == "OK" and out["mode"] == "local-shares"
    assert out["cloud"]["code"] == "SESSION_REPOSITORY_UNAVAILABLE"
    assert out["authority"].startswith("read-only")
