"""Cancel/replay and bounded, tenant-scoped audit retention."""
from __future__ import annotations

import pytest

from voicebridge.business.errors import BusinessError
from voicebridge.business.simulation_audit import (
    AuditLedger,
    export_audit,
    project,
    record_projection,
    replay,
)

from _replay_helpers import CLOCK, SEED, TENANT, interaction_history


class _Counter:
    def __init__(self, stop_after: int) -> None:
        self.calls = 0
        self.stop_after = stop_after

    def __call__(self) -> bool:
        self.calls += 1
        return self.calls > self.stop_after


def _variant_history(agent_id: str):
    events = interaction_history()
    events[1]["payload"] = {
        **events[1]["payload"],
        "agent_id": agent_id,
    }
    return events


def test_cancelled_projection_is_partial_and_labeled(history):
    seen = _Counter(2)
    projection = project(
        history, tenant_id=TENANT, seed=SEED, clock=CLOCK, cancel=seen)
    assert projection["state"] == "cancelled"
    assert projection["cancelled"] is True
    assert projection["cancelled_at_index"] == 2
    assert projection["frame_count"] == 2
    assert any("cancelled" in warning for warning in projection["warnings"])


def test_cancelled_replay_is_not_a_match_and_replay_can_restart(history):
    recorded = record_projection(history, tenant_id=TENANT, seed=SEED, clock=CLOCK)
    cancelled = replay(
        history, recorded, tenant_id=TENANT, seed=SEED, clock=CLOCK,
        cancel=_Counter(2))
    assert cancelled["status"] == "cancelled"
    assert cancelled["cancelled"] is True
    assert cancelled["frames_checked"] == 0
    assert cancelled["incidents"] == []
    assert cancelled["external_side_effects"] is False

    restarted = replay(
        history, recorded, tenant_id=TENANT, seed=SEED, clock=CLOCK)
    assert restarted["status"] == "match"
    assert restarted["replayed"]["state"] == "ok"


def test_ledger_retains_a_bounded_window():
    ledger = AuditLedger(max_runs=2, clock=CLOCK)
    for index, agent_id in enumerate(("a1", "a2", "a3"), start=1):
        events = _variant_history(agent_id)
        recorded = record_projection(
            events, tenant_id=TENANT, seed=SEED + index, clock=CLOCK)
        result = replay(
            events, recorded, tenant_id=TENANT, seed=SEED + index, clock=CLOCK)
        ledger.record(result)
    counts = ledger.counts(tenant_id=TENANT)
    assert counts["runs"] == 2
    assert counts["dropped_runs"] == 1
    assert counts["max_runs"] == 2


def test_ledger_record_is_idempotent_for_the_same_run():
    ledger = AuditLedger(max_runs=2, clock=CLOCK)
    events = _variant_history("a1")
    recorded = record_projection(events, tenant_id=TENANT, seed=SEED, clock=CLOCK)
    result = replay(events, recorded, tenant_id=TENANT, seed=SEED, clock=CLOCK)
    first = ledger.record(result)
    second = ledger.record(result)
    assert first["run_id"] == second["run_id"]
    assert ledger.counts(tenant_id=TENANT)["runs"] == 1
    assert ledger.counts(tenant_id=TENANT)["replaced_runs"] == 1


def test_prune_and_bounded_export():
    ledger = AuditLedger(max_runs=5, clock=CLOCK)
    for index, agent_id in enumerate(("a1", "a2", "a3", "a4"), start=1):
        events = _variant_history(agent_id)
        recorded = record_projection(
            events, tenant_id=TENANT, seed=SEED + index, clock=CLOCK)
        ledger.record(replay(
            events, recorded, tenant_id=TENANT, seed=SEED + index, clock=CLOCK))
    assert ledger.prune(retain=2) == 2
    export = export_audit(ledger, tenant_id=TENANT, limit=1)
    assert export["state"] == "ok"
    assert export["counts"]["runs"] == 1
    assert export["retention"]["max_runs"] == 5
    assert len(export["runs"]) == 1
    with pytest.raises(BusinessError):
        ledger.export(tenant_id=TENANT, limit=0)
    with pytest.raises(BusinessError):
        ledger.export(tenant_id=TENANT, limit=6)
