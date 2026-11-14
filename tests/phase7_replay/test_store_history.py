"""Authoritative history loading, zero writes, and tenant isolation."""
from __future__ import annotations

import pytest

from voicebridge.business import eventlog
from voicebridge.business.errors import BusinessError
from voicebridge.business.simulation_audit import (
    TENANT_MISMATCH_CODE,
    AuditLedger,
    export_audit,
    load_history,
    project,
    record_projection,
    replay,
)

from _replay_helpers import (
    CLOCK,
    OTHER_TENANT,
    SEED,
    TENANT,
    append_history,
    interaction_history,
)


def _counts(store):
    with store.read() as conn:
        return {
            table: int(conn.execute(
                f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in (
                "bos_event",
                "bos_audit_event",
                "bos_job",
                "bos_artifact",
            )
        }


def test_load_history_reads_the_authoritative_log_without_writing(store):
    business_store, tenant, business = store
    append_history(business_store, tenant.tenant_id, business.business_id)
    before = _counts(business_store)
    history = load_history(business_store, tenant.tenant_id)
    assert [event["type"] for event in history] == [
        "office.interaction.started",
        "office.interaction.effect",
        "office.interaction.fx",
        "office.interaction.completed",
    ]
    recorded = record_projection(
        history, tenant_id=tenant.tenant_id, seed=SEED, clock=CLOCK)
    result = replay(
        history, recorded, tenant_id=tenant.tenant_id, seed=SEED, clock=CLOCK)
    assert result["status"] == "match"
    assert result["replayed"]["frame_count"] == 4
    assert _counts(business_store) == before


def test_load_history_refuses_an_unknown_tenant(store):
    business_store, _tenant, _business = store
    with pytest.raises(BusinessError) as caught:
        load_history(business_store, "ten_missing")
    assert caught.value.code == "BUSINESS_NOT_FOUND"


def test_project_refuses_mixed_tenant_history(history):
    other = interaction_history(tenant=OTHER_TENANT)
    with pytest.raises(BusinessError) as caught:
        project(history + other, tenant_id=TENANT, seed=SEED, clock=CLOCK)
    assert caught.value.code == TENANT_MISMATCH_CODE


def test_project_derived_tenant_still_rejects_mixed_history(history):
    other = interaction_history(tenant=OTHER_TENANT)
    with pytest.raises(BusinessError) as caught:
        project(history + other, seed=SEED, clock=CLOCK)
    assert caught.value.code == TENANT_MISMATCH_CODE


def test_replay_refuses_a_foreign_recorded_projection(history):
    recorded = record_projection(
        history, tenant_id=TENANT, seed=SEED, clock=CLOCK)
    with pytest.raises(BusinessError) as caught:
        replay(
            history, recorded, tenant_id=OTHER_TENANT, seed=SEED, clock=CLOCK)
    assert caught.value.code == TENANT_MISMATCH_CODE


def test_ledger_export_never_crosses_tenants():
    ledger = AuditLedger(max_runs=8, clock=CLOCK)
    runs = {}
    for tenant in (TENANT, OTHER_TENANT):
        events = interaction_history(tenant=tenant)
        recorded = record_projection(
            events, tenant_id=tenant, seed=SEED, clock=CLOCK)
        result = replay(
            events, recorded, tenant_id=tenant, seed=SEED, clock=CLOCK)
        runs[tenant] = ledger.record(result)["run_id"]

    export = export_audit(ledger, tenant_id=TENANT)
    assert export["tenant_id"] == TENANT
    assert export["tenant_scoped"] is True
    assert [row["run_id"] for row in export["runs"]] == [runs[TENANT]]
    assert all(row["tenant_id"] == TENANT for row in export["runs"])
    assert ledger.get(runs[OTHER_TENANT], tenant_id=TENANT) is None
    assert ledger.get(runs[TENANT], tenant_id=TENANT) is not None
    assert ledger.counts(tenant_id=TENANT)["runs"] == 1
    assert ledger.counts(tenant_id=OTHER_TENANT)["runs"] == 1


def test_load_history_is_aggregate_scoped(store):
    business_store, tenant, business = store
    append_history(business_store, tenant.tenant_id, business.business_id)
    other = eventlog.append_event(
        business_store,
        tenant.tenant_id,
        "aggregate-other",
        "office.interaction.started",
        {"interaction_id": "int-other", "agent_id": "agent-other"},
        actor="agent-other",
        idempotency_key="sim-other",
        correlation_id="corr-other",
        now=1_700_000_100.0,
    )
    scoped = load_history(
        business_store, tenant.tenant_id, aggregate_id=business.business_id)
    assert all(event["aggregate_id"] == business.business_id
               for event in scoped)
    assert other.event_id not in [event["event_id"] for event in scoped]
