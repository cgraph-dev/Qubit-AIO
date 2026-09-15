"""Scoped autonomy profiles (Workstream F).

Proves the owner-created, persisted autonomy contract end to end:

  * create + in-scope authorization allows;
  * out-of-scope capability, disallowed target, owner mismatch, wrong binding
    are denied (a caller can never widen scope from request data);
  * expired / revoked / paused / out-of-window profiles deny;
  * operation/network/provider budget and concurrency ceilings deny;
  * the fail-closed emergency stop denies and is never bypassed;
  * profiles persist across a store reload; and
  * an allowed unattended operation records an audit receipt.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from voicebridge import autonomy, autonomy_ops, config as C

BASE = 1_700_000_000.0  # 2023-11-14T22:13:20Z, deterministic test clock


def _cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _mk(cfg, **overrides):
    kwargs = dict(
        owner="owner",
        capabilities=("files.read", "notes.write"),
        targets=("docs/*",),
        max_concurrency=4,
    )
    kwargs.update(overrides)
    return autonomy.create(cfg, **kwargs)


# ---------------------------------------------------------------- happy path
def test_create_and_authorize_in_scope_allows(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    prof = _mk(cfg)
    d = autonomy.authorize(cfg, prof.profile_id, "files.read",
                           "docs/readme.md", now=BASE)
    assert d.allowed is True
    assert d.code == autonomy.AutonomyDenial.ALLOWED.value
    assert prof.state == autonomy.AutonomyState.ACTIVE.value
    assert autonomy.show(cfg, prof.profile_id) is not None
    assert len(autonomy.list_profiles(cfg, owner="owner")) == 1


# ------------------------------------------------------------- scope denials
def test_out_of_scope_capability_denied(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    prof = _mk(cfg)
    d = autonomy.authorize(cfg, prof.profile_id, "shell.exec",
                           "docs/readme.md", now=BASE)
    assert d.allowed is False
    assert d.code == autonomy.AutonomyDenial.CAPABILITY_NOT_IN_SCOPE.value


def test_target_not_allowed_denied(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    prof = _mk(cfg)
    d = autonomy.authorize(cfg, prof.profile_id, "files.read",
                           "/etc/shadow", now=BASE)
    assert d.allowed is False
    assert d.code == autonomy.AutonomyDenial.TARGET_NOT_ALLOWED.value


def test_scope_widening_attempt_denied(tmp_path, monkeypatch):
    """Request data can never widen the owner-authored scope."""
    cfg = _cfg(tmp_path, monkeypatch)
    prof = _mk(cfg)
    # capability not granted -> denied even though the caller asks for it
    assert not autonomy.authorize(
        cfg, prof.profile_id, "admin.all", "docs/x", now=BASE).allowed
    # target outside the grant -> denied
    assert not autonomy.authorize(
        cfg, prof.profile_id, "files.read", "secrets/key", now=BASE).allowed
    # a caller that is not the owner -> denied
    d = autonomy.authorize(cfg, prof.profile_id, "files.read",
                           "docs/x", now=BASE, owner="intruder")
    assert d.allowed is False
    assert d.code == autonomy.AutonomyDenial.OWNER_MISMATCH.value


def test_binding_mismatch_denied(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    prof = _mk(cfg, session="sess-a", device="dev-a")
    d = autonomy.authorize(cfg, prof.profile_id, "files.read", "docs/x",
                           now=BASE, session="sess-b")
    assert d.allowed is False
    assert d.code == autonomy.AutonomyDenial.BINDING_MISMATCH.value


# --------------------------------------------------------- lifecycle denials
def test_expired_denied(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    prof = _mk(cfg, expires_at=BASE + 10)
    d = autonomy.authorize(cfg, prof.profile_id, "files.read",
                           "docs/x", now=BASE + 20)
    assert d.allowed is False
    assert d.code == autonomy.AutonomyDenial.EXPIRED.value


def test_revoked_denied(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    prof = _mk(cfg)
    autonomy.revoke(cfg, prof.profile_id, owner="owner")
    d = autonomy.authorize(cfg, prof.profile_id, "files.read",
                           "docs/x", now=BASE)
    assert d.allowed is False
    assert d.code == autonomy.AutonomyDenial.REVOKED.value


def test_paused_denied_then_resume_allows(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    prof = _mk(cfg)
    autonomy.pause(cfg, prof.profile_id, owner="owner")
    d = autonomy.authorize(cfg, prof.profile_id, "files.read",
                           "docs/x", now=BASE)
    assert d.allowed is False
    assert d.code == autonomy.AutonomyDenial.PAUSED.value
    autonomy.resume(cfg, prof.profile_id, owner="owner")
    assert autonomy.authorize(cfg, prof.profile_id, "files.read",
                              "docs/x", now=BASE).allowed


def test_out_of_window_denied(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    prof = _mk(cfg, window_start="00:00", window_end="00:01")
    # BASE is well outside the one-minute window
    d = autonomy.authorize(cfg, prof.profile_id, "files.read",
                           "docs/x", now=BASE)
    assert d.allowed is False
    assert d.code == autonomy.AutonomyDenial.OUT_OF_WINDOW.value


def test_no_profile_denied(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    d = autonomy.authorize(cfg, "aut_missing", "files.read", "docs/x",
                           now=BASE)
    assert d.allowed is False
    assert d.code == autonomy.AutonomyDenial.NO_PROFILE.value


def test_owner_mismatch_on_admin_mutation(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    prof = _mk(cfg)
    with pytest.raises(autonomy.AutonomyError) as exc:
        autonomy.revoke(cfg, prof.profile_id, owner="intruder")
    assert exc.value.code == "OWNER_MISMATCH"
    assert autonomy.show(cfg, prof.profile_id).state == \
        autonomy.AutonomyState.ACTIVE.value


# ---------------------------------------------------------------- budgets
def test_operation_budget_exceeded_denied(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    prof = _mk(cfg, budget=autonomy.AutonomyBudget(max_operations=1),
               max_concurrency=5)
    first = autonomy_ops.authorize_operation(
        cfg, prof.profile_id, "files.read", "docs/x", now=BASE)
    assert first.allowed is True
    second = autonomy_ops.authorize_operation(
        cfg, prof.profile_id, "files.read", "docs/x", now=BASE)
    assert second.allowed is False
    assert second.code == autonomy.AutonomyDenial.BUDGET_EXCEEDED.value


def test_cost_budget_exceeded_denied(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    prof = _mk(cfg, budget=autonomy.AutonomyBudget(max_cost=1.0),
               max_concurrency=5)
    d = autonomy.authorize(cfg, prof.profile_id, "files.read", "docs/x",
                           now=BASE, cost=2.5)
    assert d.allowed is False
    assert d.code == autonomy.AutonomyDenial.BUDGET_EXCEEDED.value


def test_concurrency_exceeded_denied(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    prof = _mk(cfg, max_concurrency=1)
    store = autonomy.autonomy_store(cfg)
    store.record_operation(prof.profile_id, active_delta=1, now=BASE)
    d = autonomy.authorize(cfg, prof.profile_id, "files.read", "docs/x",
                           now=BASE)
    assert d.allowed is False
    assert d.code == autonomy.AutonomyDenial.CONCURRENCY_EXCEEDED.value


# ----------------------------------------------------------------- e-stop
def test_estop_denies_authorize(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    prof = _mk(cfg)
    monkeypatch.setattr(autonomy, "_estop_engaged", lambda cfg: True)
    d = autonomy.authorize(cfg, prof.profile_id, "files.read", "docs/x",
                           now=BASE)
    assert d.allowed is False
    assert d.code == autonomy.AutonomyDenial.ESTOP_ENGAGED.value


def test_estop_denies_authorize_operation(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    prof = _mk(cfg)
    monkeypatch.setattr(autonomy_ops, "estop_engaged", lambda cfg: True)
    d = autonomy_ops.authorize_operation(
        cfg, prof.profile_id, "files.read", "docs/x", now=BASE)
    assert d.allowed is False
    assert d.code == autonomy.AutonomyDenial.ESTOP_ENGAGED.value


def test_hard_deny_class_denied_by_ops(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    prof = _mk(cfg, capabilities=("system.shutdown",), targets=("docs/*",))
    d = autonomy_ops.authorize_operation(
        cfg, prof.profile_id, "system.shutdown", "docs/x", now=BASE)
    assert d.allowed is False
    assert d.code == autonomy_ops.DENY_HARD_DENY


# ------------------------------------------------------------- persistence
def test_persistence_across_reload(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    prof = _mk(cfg, profile_id="aut_persist")
    path = autonomy.autonomy_store(cfg).path
    assert path == Path(cfg.state_dir()) / autonomy.STORE_NAME
    assert path.exists()
    reloaded = autonomy.AutonomyStore(path)
    got = reloaded.get("aut_persist")
    assert got is not None
    assert got.owner == "owner"
    assert got.capabilities == ("files.read", "notes.write")
    # authorization works over the fresh store handle
    d = autonomy.authorize(cfg, "aut_persist", "files.read", "docs/x",
                           now=BASE)
    assert d.allowed is True


# ------------------------------------------------------------- audit receipt
def test_unattended_operation_records_audit(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    prof = _mk(cfg)
    events = []

    def _fake_log_event(event, **fields):
        events.append((event, fields))

    monkeypatch.setattr(autonomy_ops, "log_event", _fake_log_event)
    d = autonomy_ops.authorize_operation(
        cfg, prof.profile_id, "files.read", "docs/x", now=BASE,
        source="scheduler")
    assert d.allowed is True
    assert d.audit_id
    recorded = [f for (e, f) in events if e == "autonomy.operation"]
    assert recorded, f"no audit receipt emitted: {events}"
    assert recorded[0]["allowed"] is True
    assert recorded[0]["capability"] == "files.read"
    # usage was persisted for the profile
    usage = autonomy.autonomy_store(cfg).usage(prof.profile_id)
    assert usage.operations == 1
