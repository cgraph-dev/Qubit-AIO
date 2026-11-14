"""A mismatched replay is a typed incident, never a successful replay."""
from __future__ import annotations

import copy

from voicebridge.business.simulation_audit import (
    DIVERGENCE_CODE,
    INCIDENT_SCHEMA,
    record_projection,
    replay,
)

from conftest import CLOCK, SEED, TENANT


def _recorded(history):
    return record_projection(history, tenant_id=TENANT, seed=SEED, clock=CLOCK)


def test_state_divergence_carries_event_ids_expected_actual_seed_and_clock(
    history,
):
    recorded = _recorded(history)
    tampered = copy.deepcopy(recorded)
    tampered["frames"][1]["state_digest"] = "0" * 64
    result = replay(
        history, tampered, tenant_id=TENANT, seed=SEED, clock=CLOCK)
    assert result["status"] == "divergent"
    assert result["incident_count"] == 1
    incident = result["divergence"]
    assert incident["schema"] == INCIDENT_SCHEMA
    assert incident["code"] == DIVERGENCE_CODE
    assert incident["kind"] == "state_digest"
    assert incident["event_id"] == "ten_sim_a-e2"
    assert incident["event_ids"] == ["ten_sim_a-e2"]
    assert incident["expected"] == "0" * 64
    assert incident["actual"] == recorded["frames"][1]["state_digest"]
    assert incident["seed"] == SEED
    assert incident["clock"] == CLOCK
    assert incident["frame_index"] == 1
    assert incident["causation_id"] == "ten_sim_a-e1"
    assert incident["correlation_id"] == "corr-coffee"


def test_effects_divergence_is_its_own_incident(history):
    recorded = _recorded(history)
    tampered = copy.deepcopy(recorded)
    tampered["frames"][1]["effects_digest"] = "e" * 64
    result = replay(history, tampered, tenant_id=TENANT, seed=SEED, clock=CLOCK)
    assert result["status"] == "divergent"
    assert result["divergence"]["kind"] == "effects_digest"
    assert result["divergence"]["event_id"] == "ten_sim_a-e2"


def test_frame_digest_divergence_is_its_own_incident(history):
    recorded = _recorded(history)
    tampered = copy.deepcopy(recorded)
    tampered["frames"][2]["digest"] = "d" * 64
    result = replay(history, tampered, tenant_id=TENANT, seed=SEED, clock=CLOCK)
    assert result["status"] == "divergent"
    assert result["divergence"]["kind"] == "frame_digest"


def test_fx_divergence_is_its_own_incident(history):
    recorded = _recorded(history)
    tampered = copy.deepcopy(recorded)
    tampered["frames"][2]["fx_digest"] = "f" * 64
    result = replay(history, tampered, tenant_id=TENANT, seed=SEED, clock=CLOCK)
    assert result["status"] == "divergent"
    assert result["divergence"]["kind"] == "fx_digest"
    assert result["divergence"]["event_id"] == "ten_sim_a-e3"


def test_seed_divergence_is_detected(history):
    recorded = _recorded(history)
    recorded["seed"] = SEED + 1
    result = replay(history, recorded, tenant_id=TENANT, seed=SEED, clock=CLOCK)
    assert result["status"] == "divergent"
    kinds = [incident["kind"] for incident in result["incidents"]]
    assert "seed" in kinds


def test_frame_count_divergence_is_detected(history):
    recorded = _recorded(history)
    recorded["frames"] = recorded["frames"][:-1]
    result = replay(history, recorded, tenant_id=TENANT, seed=SEED, clock=CLOCK)
    assert result["status"] == "divergent"
    assert "frame_count" in [
        incident["kind"] for incident in result["incidents"]]


def test_final_state_divergence_is_detected(history):
    recorded = _recorded(history)
    recorded["final_state"]["agents"]["agent-ada"]["needs"]["energy"] = 0
    result = replay(history, recorded, tenant_id=TENANT, seed=SEED, clock=CLOCK)
    assert result["status"] == "divergent"
    kinds = [incident["kind"] for incident in result["incidents"]]
    assert "final_state" in kinds


def test_event_order_divergence_is_detected(history):
    recorded = _recorded(history)
    recorded["event_ids"] = list(reversed(recorded["event_ids"]))
    result = replay(history, recorded, tenant_id=TENANT, seed=SEED, clock=CLOCK)
    assert result["status"] == "divergent"
    assert "event_order" in [
        incident["kind"] for incident in result["incidents"]]


def test_incidents_are_bounded(history):
    recorded = _recorded(history)
    tampered = copy.deepcopy(recorded)
    for frame in tampered["frames"]:
        frame["state_digest"] = "1" * 64
        frame["effects_digest"] = "2" * 64
        frame["fx_digest"] = "3" * 64
    result = replay(
        history, tampered, tenant_id=TENANT, seed=SEED, clock=CLOCK,
        max_incidents=3)
    assert result["status"] == "divergent"
    assert len(result["incidents"]) == 3
    assert result["incident_count"] > 3


def test_divergent_run_is_retained_as_divergent(history):
    from voicebridge.business.simulation_audit import AuditLedger

    recorded = _recorded(history)
    tampered = copy.deepcopy(recorded)
    tampered["frames"][0]["fx_digest"] = "a" * 64
    result = replay(history, tampered, tenant_id=TENANT, seed=SEED, clock=CLOCK)
    ledger = AuditLedger(max_runs=4, clock=CLOCK)
    row = ledger.record(result)
    assert row["status"] == "divergent"
    assert row["incident_count"] == 1
    assert ledger.counts(tenant_id=TENANT)["divergent_runs"] == 1
