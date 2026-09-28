"""Workstream F: scoped autonomy is enforced on the real broker execution path.

Before this wiring ``voicebridge/autonomy_ops.py`` had zero production callers,
so an owner-authored autonomy profile enforced nothing. These tests prove the
broker's single capability-execution seam now consults that authority:

  * an ACTIVE, in-scope profile pre-authorizes execution through the broker
    (no ad-hoc confirmation), emitting exactly one audit receipt;
  * an out-of-scope capability/target under an ACTIVE profile is a typed
    refusal carrying the autonomy code, and nothing executes;
  * with no applicable profile the prior per-action confirmation behavior is
    byte-for-byte unchanged (destructive two-step, mutating auto-confirm);
  * a latched e-stop refuses regardless of an otherwise-covering profile;
  * request data (capability/target) can never widen the owner-authored scope;
  * an unarmed broker (no ``cfg`` seam) never reads any autonomy store.

Everything is hermetic: tmp state tree, synthetic tools, no hardware/network.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from voicebridge import autonomy, autonomy_ops, config as C
from voicebridge.broker import CapabilityBroker, ToolBinding, build_host_broker
from voicebridge.capabilities import CapabilitySpec, Tier

BASE = 1_700_000_000.0  # deterministic clock (2023-11-14T22:13:20Z)


def _cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _profile(cfg, **overrides):
    kwargs = dict(
        owner="owner",
        capabilities=("t.write",),
        targets=("docs/*",),
        max_concurrency=4,
    )
    kwargs.update(overrides)
    return autonomy.create(cfg, **kwargs)


def _broker(cfg, calls=None):
    """Armed broker (cfg seam) with synthetic read-only + mutating tools."""
    calls = calls if calls is not None else []
    b = build_host_broker(cfg=cfg)

    def _ro(_args):
        calls.append("ro")
        return "read"

    b.register(ToolBinding(
        spec=CapabilitySpec(name="t.read", version="1.0.0", tier=Tier.READ_ONLY,
                            summary="synthetic read", scopes=("s",)),
        side_effects="none", fn=_ro))
    b.register(ToolBinding(
        spec=CapabilitySpec(name="t.write", version="1.0.0", tier=Tier.MUTATING,
                            summary="synthetic write", scopes=("s",)),
        side_effects="writes", fn=lambda a: "wrote"))
    b.register(ToolBinding(
        spec=CapabilitySpec(name="t.admin", version="1.0.0", tier=Tier.MUTATING,
                            summary="synthetic admin", scopes=("s",)),
        side_effects="writes", fn=lambda a: "admin"))
    return b


# --------------------------------------------------- active profile: allow
def test_active_profile_preauthorizes_in_scope_capability(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    prof = _profile(cfg)
    events: list = []
    monkeypatch.setattr(autonomy_ops, "log_event",
                        lambda e, **f: events.append((e, f)))
    b = _broker(cfg)
    # An unattended (automation) mutating run that the engine would otherwise
    # refuse as CONFIRMATION_SOURCE_FORBIDDEN is pre-authorized by the profile.
    res = b.execute("t.write", {"q": "hello"}, source="automation",
                    target="docs/a.txt")
    assert res.ok and res.output == "wrote"
    assert not res.denied
    receipts = [f for e, f in events if e == "autonomy.operation"]
    assert len(receipts) == 1
    assert receipts[0]["allowed"] is True
    assert receipts[0]["capability"] == "t.write"
    assert receipts[0]["profile_id"] == prof.profile_id
    usage = autonomy.autonomy_store(cfg).usage(prof.profile_id)
    assert usage.operations == 1


# ------------------------------------------------- active profile: refusals
def test_out_of_scope_capability_refused_with_autonomy_code(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    _profile(cfg)
    calls: list = []
    b = _broker(cfg, calls)
    res = b.execute("t.read", {}, source="automation", target="docs/a.txt")
    assert not res.ok
    assert res.denied == autonomy.AutonomyDenial.CAPABILITY_NOT_IN_SCOPE.value
    assert calls == []


def test_out_of_scope_target_refused_with_autonomy_code(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    _profile(cfg)
    calls: list = []
    b = _broker(cfg, calls)
    res = b.execute("t.write", {}, source="automation", target="secrets/key")
    assert not res.ok
    assert res.denied == autonomy.AutonomyDenial.TARGET_NOT_ALLOWED.value
    assert calls == []


def test_scope_widening_attempt_cannot_expand_profile(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    prof = _profile(cfg)
    b = _broker(cfg)
    # A model/message asking for a capability the owner never granted.
    r1 = b.execute("t.admin", {}, source="model", target="docs/a.txt")
    assert r1.denied == autonomy.AutonomyDenial.CAPABILITY_NOT_IN_SCOPE.value
    # A target outside the owner-authored grant.
    r2 = b.execute("t.write", {}, source="automation", target="../../etc/shadow")
    assert r2.denied == autonomy.AutonomyDenial.TARGET_NOT_ALLOWED.value
    # The persisted profile is unchanged by any request.
    stored = autonomy.show(cfg, prof.profile_id)
    assert stored.capabilities == ("t.write",)
    assert stored.targets == ("docs/*",)


# --------------------------------------------- no profile: prior behavior
def test_no_profile_preserves_destructive_two_step(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    b = _broker(cfg)
    spec = CapabilitySpec(name="t.trash", version="1.0.0", tier=Tier.DESTRUCTIVE,
                          summary="synthetic trash", scopes=("s",))
    b.register(ToolBinding(spec=spec, side_effects="deletes", fn=lambda a: "gone"))
    # First call: typed confirmation request, nothing runs.
    r1 = b.execute("t.trash", {"scope": "u"}, source="user")
    assert r1.denied == "CONFIRMATION_REQUIRED"
    # Spoken "yes" -> user-issued single-use confirmation -> executes once.
    rec = b.issue_confirmation("t.trash", {"scope": "u"}, issued_by="user")
    r2 = b.execute("t.trash", {"scope": "u"}, source="user",
                   confirmation_id=rec.id)
    assert r2.ok and r2.output == "gone"


def test_no_profile_preserves_mutating_auto_confirmation(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    b = _broker(cfg)
    # Non-destructive MUTATING user request auto-issues+consumes a confirmation
    # exactly as before the autonomy seam existed.
    res = b.execute("t.write", {"q": "x"}, source="user")
    assert res.ok and res.output == "wrote"
    # A non-user source is still refused (no profile to pre-authorize it).
    res2 = b.execute("t.write", {"q": "x"}, source="automation")
    assert res2.denied == "CONFIRMATION_SOURCE_FORBIDDEN"


def test_unarmed_broker_never_reads_autonomy_store(tmp_path, monkeypatch):
    """No cfg seam -> a stored profile must not change broker behavior."""
    cfg = _cfg(tmp_path, monkeypatch)
    _profile(cfg)
    unarmed = _broker_no_cfg()
    res = unarmed.execute("t.write", {"q": "x"}, source="automation")
    assert res.denied == "CONFIRMATION_SOURCE_FORBIDDEN"


def _broker_no_cfg():
    b = CapabilityBroker()
    b.register(ToolBinding(
        spec=CapabilitySpec(name="t.write", version="1.0.0", tier=Tier.MUTATING,
                            summary="synthetic write", scopes=("s",)),
        side_effects="writes", fn=lambda a: "wrote"))
    return b


# ------------------------------------------------------------------ e-stop
def test_estop_refuses_even_with_covering_profile(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    _profile(cfg)
    monkeypatch.setattr(autonomy_ops, "estop_engaged", lambda cfg: True)
    monkeypatch.setattr(autonomy, "_estop_engaged", lambda cfg: True)
    events: list = []
    monkeypatch.setattr(autonomy_ops, "log_event",
                        lambda e, **f: events.append((e, f)))
    b = _broker(cfg)
    res = b.execute("t.write", {}, source="automation", target="docs/a.txt")
    assert not res.ok
    assert res.denied == autonomy.AutonomyDenial.ESTOP_ENGAGED.value
    assert any(e == "autonomy.denied" for e, _ in events)


# ---------------------------------------------------------- audit discipline
def test_one_audit_line_per_autonomy_decision(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    _profile(cfg)
    events: list = []
    monkeypatch.setattr(autonomy_ops, "log_event",
                        lambda e, **f: events.append((e, f)))
    b = _broker(cfg)
    b.execute("t.write", {"q": "ok"}, source="automation", target="docs/a.txt")
    b.execute("t.read", {}, source="automation", target="docs/a.txt")
    autonomy_events = [e for e, _ in events if e.startswith("autonomy.")]
    assert len(autonomy_events) == 2
    assert sorted(autonomy_events) == ["autonomy.denied", "autonomy.operation"]
