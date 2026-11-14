"""P8-03 deployment rings: order, gates, production approval, rollback.

Hermetic: throwaway SQLite file under ``tmp_path`` for the audit trail; ring
state is per-controller in memory.
"""
from __future__ import annotations

import pytest

from voicebridge.business.errors import BusinessError
from voicebridge.business import disaster_recovery as dr

from _dr_helpers import seed_store

INTERNAL_GATES = {"backup_verified": True, "dr_drill_passed": True}
SYNTHETIC_GATES = {"internal_soak": True}
PILOT_GATES = {"synthetic_soak": True, "canary_healthy": True}
PRODUCTION_GATES = {
    "pilot_soak": True,
    "dr_drill_passed": True,
    "rollback_rehearsed": True,
}


def _to_pilot(rings: dr.DeploymentRings) -> None:
    rings.promote("internal", revision="rev-1", gates=INTERNAL_GATES)
    rings.promote("synthetic", revision="rev-1", gates=SYNTHETIC_GATES)
    rings.promote("pilot", revision="rev-1", gates=PILOT_GATES)
    assert rings.current_ring() == "pilot"


def test_rings_promote_one_step_at_a_time(tmp_path):
    seed = seed_store(tmp_path / "live")
    rings = dr.DeploymentRings(seed.store, tenant_id=seed.tenant_id)

    with pytest.raises(BusinessError) as first:
        rings.promote("synthetic", revision="rev-1", gates=SYNTHETIC_GATES)
    assert first.value.code == dr.DR_RING_ORDER_CODE
    assert rings.current_ring() is None

    rings.promote("internal", revision="rev-1", gates=INTERNAL_GATES)
    with pytest.raises(BusinessError) as skip:
        rings.promote("pilot", revision="rev-1", gates=PILOT_GATES)
    assert skip.value.code == dr.DR_RING_ORDER_CODE
    assert rings.current_ring() == "internal"

    with pytest.raises(BusinessError) as unknown:
        rings.promote("staging", revision="rev-1", gates={})
    assert unknown.value.code == dr.DR_RING_UNKNOWN_CODE


def test_promotion_gates_block_with_the_failed_gate_named(tmp_path):
    seed = seed_store(tmp_path / "live")
    rings = dr.DeploymentRings(seed.store, tenant_id=seed.tenant_id)

    with pytest.raises(BusinessError) as exc:
        rings.promote(
            "internal",
            revision="rev-1",
            gates={"backup_verified": True, "dr_drill_passed": False},
        )
    assert exc.value.code == dr.DR_RING_PROMOTION_BLOCKED_CODE
    assert "dr_drill_passed" in str(exc.value)
    assert rings.current_ring() is None

    with pytest.raises(BusinessError) as missing:
        rings.promote("internal", revision="rev-1", gates={})
    assert missing.value.code == dr.DR_RING_PROMOTION_BLOCKED_CODE


def test_production_requires_explicit_owner_approval(tmp_path):
    seed = seed_store(tmp_path / "live")
    rings = dr.DeploymentRings(seed.store, tenant_id=seed.tenant_id)
    _to_pilot(rings)

    with pytest.raises(BusinessError) as no_owner:
        rings.promote("production", revision="rev-1", gates=PRODUCTION_GATES)
    assert no_owner.value.code == dr.DR_PRODUCTION_APPROVAL_REQUIRED_CODE
    assert rings.current_ring() == "pilot"

    with pytest.raises(BusinessError) as flag_only:
        rings.promote(
            "production",
            revision="rev-1",
            gates=PRODUCTION_GATES,
            owner_approved=True,
            owner="",
        )
    assert flag_only.value.code == dr.DR_PRODUCTION_APPROVAL_REQUIRED_CODE

    record = rings.promote(
        "production",
        revision="rev-1",
        gates=PRODUCTION_GATES,
        owner_approved=True,
        owner="owner",
    )
    assert rings.current_ring() == "production"
    assert record["ring"] == "production"
    assert record["owner_approved"] is True
    assert record["owner"] == "owner"
    assert rings.deployed()["production"] == "rev-1"


def test_ring_scoped_rollout_cannot_skip_a_promotion(tmp_path):
    seed = seed_store(tmp_path / "live")
    rings = dr.DeploymentRings(seed.store, tenant_id=seed.tenant_id)

    with pytest.raises(BusinessError) as not_entered:
        rings.rollout("rev-2", ring="internal")
    assert not_entered.value.code == dr.DR_RING_ORDER_CODE

    rings.promote("internal", revision="rev-1", gates=INTERNAL_GATES)
    record = rings.rollout("rev-2", ring="internal")
    assert record["previous_revision"] == "rev-1"
    assert rings.deployed()["internal"] == "rev-2"
    # A rollout on one ring does not move the promotion cursor.
    assert rings.current_ring() == "internal"

    with pytest.raises(BusinessError) as skip:
        rings.rollout("rev-2", ring="pilot")
    assert skip.value.code == dr.DR_RING_ORDER_CODE


def test_production_rollout_needs_owner_approval(tmp_path):
    seed = seed_store(tmp_path / "live")
    rings = dr.DeploymentRings(seed.store, tenant_id=seed.tenant_id)
    _to_pilot(rings)
    rings.promote(
        "production",
        revision="rev-1",
        gates=PRODUCTION_GATES,
        owner_approved=True,
        owner="owner",
    )

    with pytest.raises(BusinessError) as exc:
        rings.rollout("rev-2", ring="production")
    assert exc.value.code == dr.DR_PRODUCTION_APPROVAL_REQUIRED_CODE

    record = rings.rollout("rev-2", ring="production", owner_approved=True)
    assert record["revision"] == "rev-2"


def test_unhealthy_canary_rolls_the_ring_back(tmp_path):
    seed = seed_store(tmp_path / "live")
    rings = dr.DeploymentRings(seed.store, tenant_id=seed.tenant_id)
    rings.promote("internal", revision="rev-1", gates=INTERNAL_GATES)
    rings.rollout("rev-2", ring="internal")

    result = rings.canary(
        "rev-2", ring="internal", percent=10, healthy=False
    )
    assert result["ok"] is False
    assert result["canary"]["healthy"] is False
    assert result["rollback"]["action"] == "rollback"
    assert result["rollback"]["revision"] == "rev-1"
    assert rings.deployed()["internal"] == "rev-1"

    healthy = rings.canary("rev-1", ring="internal", percent=25, healthy=True)
    assert healthy["ok"] is True
    assert healthy["rollback"] is None


def test_rollback_without_a_previous_revision_is_a_typed_refusal(tmp_path):
    seed = seed_store(tmp_path / "live")
    rings = dr.DeploymentRings(seed.store, tenant_id=seed.tenant_id)
    rings.promote("internal", revision="rev-1", gates=INTERNAL_GATES)

    with pytest.raises(BusinessError) as exc:
        rings.rollback("internal")
    assert exc.value.code == dr.DR_RING_ROLLBACK_CODE

    # An explicit destination is always accepted and audited.
    record = rings.rollback("internal", to_revision="rev-0")
    assert record["revision"] == "rev-0"
    assert rings.deployed()["internal"] == "rev-0"

    with pytest.raises(BusinessError) as nothing:
        rings.rollback("synthetic")
    assert nothing.value.code == dr.DR_RING_ROLLBACK_CODE
