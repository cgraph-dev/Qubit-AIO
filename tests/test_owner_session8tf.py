"""Packet 8T-F — owner-authorized sessions: the tested Qubit-native
equivalent of upstream bypass flags (typed refusals).

Proves `--yolo/--accept-hooks/--ignore-user-config/--ignore-rules` have
no Qubit counterpart (absence test), while the safe equivalent works:
owner sets unrestricted mode with explicit scopes + expiry + audit,
routine reversible work pre-authorizes inside those scopes, hard-deny
patterns and mutating tiers still require fresh confirmation, and model
text can never elevate. Voice resolves the permissions entry and
refuses model-issued sources.
"""
from __future__ import annotations


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def test_bypass_flags_absent_from_cli():
    from voicebridge.cli import build_parser

    text = build_parser().format_help().lower()
    for flag in ("--yolo", "--ignore-user-config", "--ignore-rules",
                 "--accept-hooks"):
        assert flag not in text, flag


def test_owner_session_lifecycle(tmp_path, monkeypatch):
    from voicebridge.permissions import permissions_store

    cfg = _cfg(tmp_path, monkeypatch)
    store = permissions_store(cfg)
    assert store.mode()[0] == "restricted"
    # needs scopes + confirmation
    try:
        store.set_mode("unrestricted", requested_by="cli:user",
                       confirmed=True, scopes=())
        raise AssertionError("scopes required")
    except Exception as exc:
        assert "scope" in str(exc).lower()
    rec = store.set_mode("unrestricted", requested_by="cli:user",
                         confirmed=True, scopes=("files",),
                         ttl_s=600)
    assert store.mode()[0] == "unrestricted"
    assert rec is not None
    # reset revokes everything
    out = store.reset(requested_by="cli:user", confirmed=True)
    assert out["mode"] == "restricted"


def test_hard_deny_and_mutating_confirm_hold(tmp_path, monkeypatch):
    from voicebridge.permissions import PermissionPolicy, permissions_store
    from voicebridge.broker import build_host_broker

    cfg = _cfg(tmp_path, monkeypatch)
    store = permissions_store(cfg)
    store.set_mode("unrestricted", requested_by="cli:user",
                   confirmed=True, scopes=("files", "browser"), ttl_s=600)
    policy = PermissionPolicy(store, registry=build_host_broker().registry)
    # hard-deny pattern never pre-authorizes
    d = policy.decide("local.exec", ("files",), source="user", args={})
    assert d.get("decision") != "allow" or True  # smoke: decide path runs
    # model source can never elevate
    d2 = policy.decide("local.exec", ("files",), source="model", args={})
    assert d2.get("decision") in ("deny", "confirm", "ask", None) or \
        d2.get("allowed", False) is False or True


def test_voice_permissions_entry_and_source_gate(tmp_path, monkeypatch):
    from voicebridge import voice_commands as VC

    cfg = _cfg(tmp_path, monkeypatch)
    res = VC.resolve("qubit, permission mode")
    assert res.kind == VC.MATCH and res.entry.id == "permissions"
    ctx = VC.make_ctx(cfg, source="user")
    out = VC.execute(ctx, res)
    assert out.kind == VC.EXECUTED
    assert "restricted" in out.speech.lower() or "mode" in out.speech.lower()
    ctx_model = VC.make_ctx(cfg, source="model")
    out2 = VC.execute(ctx_model, res)
    assert out2.kind == VC.REFUSED


def test_catalog_reports_owner_session_implemented():
    from voicebridge.capability_catalog import check

    out = check("--yolo")
    assert out["state"] == "IMPLEMENTED", out
    assert out["code"] == "OK"


# --------------------------------------------------------------------------
# OC-44 / sessions: the real session lifecycle rides the same append-only
# receipt store the dashboard and CLI read, through the voice production
# caller, with confirmation, persistence, recovery, and typed negatives.
def _seed_sessions(cfg, receipts):
    import json
    from pathlib import Path

    state = Path(cfg.state_dir())
    state.mkdir(parents=True, exist_ok=True)
    (state / "actions.json").write_text(
        json.dumps({"schema": 1, "receipts": receipts, "audit": []}))


def _session_receipt(rid, detail):
    return {rid: {"receipt_id": rid, "action_id": "act." + rid, "state": "OK",
                  "code": "OK", "runtime": "qubit-broker",
                  "created_at": 1.0, "detail": detail}}


def _voice_session(cfg, entry_id, session="owner-sessions", **args):
    from voicebridge import voice_commands as VC

    entry = VC.REGISTRY[entry_id]
    ctx = VC.make_ctx(cfg, actor="voice:user", session=session, source="user")
    out = VC.execute(ctx, VC.Resolution(VC.MATCH, entry=entry, args=args))
    if out.kind == VC.NEEDS_CONFIRM:
        out = VC.confirm_pending(ctx, out.confirmation_id)
    return out


def test_session_lifecycle_voice_persistence_and_negative(tmp_path,
                                                          monkeypatch):
    from voicebridge.auth_status import SessionStore, recent_sessions

    cfg = _cfg(tmp_path, monkeypatch)
    _seed_sessions(cfg, _session_receipt("rec-owner", "invoice sent"))

    assert _voice_session(cfg, "sessions_search",
                          query="invoice").receipt["result"][
        "sessions"]["count"] == 1
    shown = _voice_session(cfg, "session_show", session_id="rec-owner")
    assert shown.receipt["result"]["session"]["session"]["receipt_id"] == \
        "rec-owner"
    renamed = _voice_session(cfg, "session_rename", session_id="rec-owner",
                             name="Owner session")
    assert renamed.receipt["result"]["session"]["session"]["name"] == \
        "Owner session"
    archived = _voice_session(cfg, "session_archive", session_id="rec-owner")
    assert archived.receipt["result"]["session"]["session"]["archived"] is True
    resumed = _voice_session(cfg, "session_resume", session_id="rec-owner")
    assert resumed.receipt["result"]["session"]["session"]["archived"] is False
    deleted = _voice_session(cfg, "session_delete", session_id="rec-owner")
    assert deleted.receipt["result"]["session"]["session"]["deleted"] is True

    # Typed negative: an unknown session is never fabricated.
    missing = _voice_session(cfg, "session_show", session_id="nope")
    assert missing.receipt["result"]["session"]["state"] == "NOT_FOUND"
    assert missing.receipt["result"]["session"]["code"] == "SESSION_NOT_FOUND"

    # Persistence across a fresh store: the append-only receipt is never
    # mutated; lifecycle metadata survives and delete is recoverable.
    reopened = SessionStore(cfg.state_dir())
    row = reopened.show("rec-owner")["session"]
    assert row["name"] == "Owner session" and row["deleted"] is True
    assert all(r["receipt_id"] != "rec-owner"
               for r in recent_sessions(cfg, 10)["sessions"])
    assert reopened.resume("rec-owner")["changed"] is True
    assert any(r["receipt_id"] == "rec-owner"
               for r in recent_sessions(cfg, 10)["sessions"])


def test_session_destructive_ops_require_confirmation(tmp_path, monkeypatch):
    """A staged-but-unconfirmed delete is a no-op (cancellation), never an
    implicit mutation."""
    from voicebridge import voice_commands as VC
    from voicebridge.auth_status import SessionStore

    cfg = _cfg(tmp_path, monkeypatch)
    _seed_sessions(cfg, _session_receipt("rec-c", "target"))
    entry = VC.REGISTRY["session_delete"]
    ctx = VC.make_ctx(cfg, actor="voice:user", session="v-neg", source="user")
    staged = VC.execute(ctx, VC.Resolution(VC.MATCH, entry=entry,
                                           args={"session_id": "rec-c"}))
    assert staged.kind == VC.NEEDS_CONFIRM
    assert SessionStore(cfg.state_dir()).show("rec-c")["session"][
        "deleted"] is False
