"""8U — one confirmation authority (A7) and MCP listener disposition (A12).

A7: the durable shared human-confirmation journal
(:class:`voicebridge.approvals.ApprovalJournal`, ``approvals.jsonl``) is the
single authoritative confirmation store. ``CapabilityBroker``, the worker
dispatch guard, and the fork-forward guard consume it through
``broker.confirmation_store_for``; broker confirmations no longer create a
second writable store in ``actions.json``. A record minted through one surface
is consumable by the other with the full single-use / expiry / target-digest
contract.

A12: the MCP loopback endpoint is disabled by default and binds no socket
unless an operator opts in through a human-confirmed attach grant; it is a
bounded loopback helper, never a second product listener/gateway.

Hermetic: tmp XDG state only, no network, no live services.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


# ===========================================================================
# A7 — one confirmation authority
# ===========================================================================
def _journal_lines(cfg):
    path = Path(cfg.state_dir()) / "approvals.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines()
            if line.strip()]


def _actions_confirmations(cfg):
    path = Path(cfg.state_dir()) / "actions.json"
    if not path.exists():
        return {}
    raw = json.loads(path.read_text())
    return raw.get("confirmations", {}) if isinstance(raw, dict) else {}


def test_broker_confirmations_are_journaled_not_a_second_store(tmp_path,
                                                              monkeypatch):
    """The broker no longer keeps a duplicate writable store in actions.json."""
    from voicebridge.actions import PersistentConfirmationStore
    from voicebridge.broker import build_host_broker

    cfg = _cfg(tmp_path, monkeypatch)
    broker = build_host_broker(cfg=cfg)
    assert isinstance(broker.engine.confirmations, PersistentConfirmationStore)

    rec = broker.issue_confirmation("local.empty_trash", {"scope": "u"},
                                    issued_by="user", ttl_s=60)
    records = _journal_lines(cfg)
    assert any(r["id"] == rec.id and r["capability"] == "local.empty_trash"
               for r in records), records
    # The authoritative journal carries the bound digest, not raw args.
    wire = next(r for r in records if r["id"] == rec.id)
    assert wire["meta"]["args_digest"]
    assert "scope" not in json.dumps(wire.get("meta", {}))
    # No duplicate confirmation record was written to actions.json.
    assert _actions_confirmations(cfg) == {}


def test_confirmation_minted_by_one_broker_is_consumed_by_another(
        tmp_path, monkeypatch):
    from voicebridge.broker import build_host_broker
    from voicebridge.capabilities import ActionRequest

    cfg = _cfg(tmp_path, monkeypatch)
    mint = build_host_broker(cfg=cfg)
    rec = mint.engine.confirmations.issue("local.empty_trash", {},
                                          issued_by="user", ttl_s=60)
    later = build_host_broker(cfg=cfg)
    decision = later.engine.decide(ActionRequest(
        capability="local.empty_trash", args={}, source="user",
        confirmation_id=rec.id))
    assert decision.allowed and decision.code == "ALLOWED"
    # Single-use is preserved: the same id replays as a typed denial.
    replay = later.engine.decide(ActionRequest(
        capability="local.empty_trash", args={}, source="user",
        confirmation_id=rec.id))
    assert replay.code == "REPLAYED_CONFIRMATION"


def test_journal_minted_confirmation_is_consumable_by_the_broker(
        tmp_path, monkeypatch):
    """Mint through the human journal; consume through the broker policy core."""
    from voicebridge.approvals import ApprovalJournal
    from voicebridge.broker import build_host_broker
    from voicebridge.capabilities import ActionRequest, _args_digest

    cfg = _cfg(tmp_path, monkeypatch)
    journal = ApprovalJournal(Path(cfg.state_dir()) / "approvals.jsonl")
    approval = journal.create("local.empty_trash", "stage",
                              ttl_s=60, args_digest=_args_digest({}),
                              issued_by="user")
    broker = build_host_broker(cfg=cfg)
    decision = broker.engine.decide(ActionRequest(
        capability="local.empty_trash", args={}, source="user",
        confirmation_id=approval.id))
    assert decision.allowed and decision.code == "ALLOWED"
    # The durable journal record reflects the broker's settle-once consumption.
    reloaded = ApprovalJournal(Path(cfg.state_dir()) / "approvals.jsonl")
    assert reloaded.get(approval.id).state == "complete"


def test_dispatch_guard_shares_the_one_authority(tmp_path, monkeypatch):
    from voicebridge.actions import PersistentConfirmationStore
    from voicebridge.broker import confirmation_store_for
    from voicebridge.dispatch_guard import build_worker_engine

    cfg = _cfg(tmp_path, monkeypatch)
    engine, mode = build_worker_engine("confirm", cfg)
    assert mode == "confirm"
    assert isinstance(engine.confirmations, PersistentConfirmationStore)
    # The guard's engine and the broker resolve the same journal authority.
    assert _actions_confirmations(cfg) == {}
    assert confirmation_store_for(cfg) is not None


def test_confirmation_semantics_are_preserved(tmp_path, monkeypatch):
    """Expiry and target binding still fail closed through the journal."""
    from voicebridge.broker import build_host_broker
    from voicebridge.capabilities import ActionRequest

    cfg = _cfg(tmp_path, monkeypatch)
    broker = build_host_broker(cfg=cfg)
    store = broker.engine.confirmations

    rec = store.issue("local.empty_trash", {"scope": "a"}, issued_by="user")
    mismatch = broker.engine.decide(ActionRequest(
        capability="local.empty_trash", args={"scope": "b"}, source="user",
        confirmation_id=rec.id))
    assert mismatch.code == "TARGET_MISMATCH"

    clock = {"now": 1000.0}
    tick = lambda: clock["now"]  # noqa: E731 - test clock
    store._clock = tick
    store._journal._clock = tick
    rec2 = store.issue("local.empty_trash", {"scope": "a"}, issued_by="user")
    clock["now"] += 10_000.0
    expired = broker.engine.decide(ActionRequest(
        capability="local.empty_trash", args={"scope": "a"}, source="user",
        confirmation_id=rec2.id))
    assert expired.code == "EXPIRED_CONFIRMATION"


# ===========================================================================
# A12 — MCP loopback is opt-in, not a second product listener
# ===========================================================================
def _no_bind(monkeypatch):
    """Fail if any loopback server is constructed (i.e. a socket would bind)."""
    import voicebridge.plugin_runtime as PR

    class _Boom:
        def __init__(self, *a, **k):
            raise AssertionError("no socket may bind without operator opt-in")

    monkeypatch.setattr(PR, "_LoopbackServer", _Boom)


def test_mcp_serve_disabled_without_operator_grant_binds_nothing(
        tmp_path, monkeypatch):
    from voicebridge import plugin_runtime as PR

    cfg = _cfg(tmp_path, monkeypatch)
    _no_bind(monkeypatch)
    with pytest.raises(PR.PluginRuntimeError) as ei:
        PR.mcp_serve(cfg)
    assert ei.value.code == PR.MCP_REFUSED_CODE


def test_mcp_serve_estop_refuses_before_any_bind(tmp_path, monkeypatch):
    from voicebridge import plugin_runtime as PR
    from voicebridge.power import power_control_store

    cfg = _cfg(tmp_path, monkeypatch)
    _no_bind(monkeypatch)
    store = power_control_store(cfg)
    store.set(emergency_stop=True, global_disable=False, issued_by="cli:user")
    try:
        with pytest.raises(PR.PluginRuntimeError) as ei:
            PR.mcp_serve(cfg)
        assert ei.value.code == PR.MCP_ESTOP_CODE
    finally:
        store.set(emergency_stop=False, global_disable=False,
                  issued_by="cli:user")


def test_mcp_serve_refuses_non_loopback_host(tmp_path, monkeypatch):
    from voicebridge import plugin_runtime as PR

    cfg = _cfg(tmp_path, monkeypatch)
    _no_bind(monkeypatch)
    monkeypatch.setattr(PR, "MCP_HOST", "0.0.0.0")
    with pytest.raises(PR.PluginRuntimeError) as ei:
        PR.mcp_serve(cfg)
    assert ei.value.code == PR.MCP_REFUSED_CODE


def test_mcp_serve_opt_in_after_confirmed_attach(tmp_path, monkeypatch):
    from voicebridge import plugin_runtime as PR

    cfg = _cfg(tmp_path, monkeypatch)
    manifest = {"id": "acme.echo", "kind": "tool", "entrypoint": "acme:main",
                "digest": "a" * 64, "label": "Acme", "version": "1.0.0",
                "capabilities": ["run"]}
    PR.bind_handler(cfg, manifest=manifest, handler=lambda a: {"ok": True},
                    confirmed=True)
    PR.mcp_attach(cfg, "loopapp", confirmed=True)
    host = PR.mcp_serve(cfg)
    try:
        assert host.host == "127.0.0.1" and host.port > 0
        view = host.as_dict()
        assert view["second_listener"] is False
        assert view["serve_disposition"] == PR.MCP_SERVE_DISPOSITION
    finally:
        host.stop()


def test_mcp_doctor_reports_disposition(tmp_path, monkeypatch):
    from voicebridge import plugin_runtime as PR

    cfg = _cfg(tmp_path, monkeypatch)
    doc = PR.mcp_doctor(cfg)
    assert doc["second_listener"] is False
    assert doc["serve_disposition"] == PR.MCP_SERVE_DISPOSITION
    assert doc["serve_enabled"] is False
