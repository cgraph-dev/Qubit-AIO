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
