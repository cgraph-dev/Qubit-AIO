"""P4-06 — baseline, quarantine, lifecycle and out-of-band kill switch."""
from __future__ import annotations

import pytest

from voicebridge.business import AgentControlService
from voicebridge.business import runs
from voicebridge.business.errors import BusinessError
from voicebridge.business.safety import (
    KILL_SWITCH_SURFACES,
    SAFETY_KILL_SWITCH_INCOMPLETE_CODE,
    SAFETY_QUARANTINED_CODE,
    BehaviorBaseline,
    BehaviorSample,
    OutOfBandKillSwitch,
    SafetyService,
    lease_stopper,
    runtime_stopper,
)
from _p4_helpers import NOW, READ, Recorder, policy, runtime, scope, snapshot, store


def _agent(bos, tenant, business, *, key="agent-1", model="qubit-brain"):
    control = AgentControlService(bos)
    agent, _ = control.create(
        tenant.tenant_id,
        business.business_id,
        role_id="role-ops",
        department="operations",
        display_name="Ops",
        human_owner="owner",
        provider="local",
        model=model,
        idempotency_key=key,
        permissions={"tools": [READ]},
        safety={"autonomy": "L2"},
    )
    return control, agent


def test_insufficient_baseline_does_not_invent_a_verdict():
    baseline = BehaviorBaseline()
    report = baseline.classify(BehaviorSample(turns=5, tool_calls=5))
    assert report.insufficient_baseline is True
    assert report.severity == "ok"
    assert report.anomalies == ()


def test_anomalous_spend_and_unknown_outcomes_are_critical():
    baseline = BehaviorBaseline(
        [
            BehaviorSample(turns=2, tool_calls=2, spend_minor=2, duration_seconds=60),
            BehaviorSample(turns=2, tool_calls=2, spend_minor=2, duration_seconds=60),
            BehaviorSample(turns=2, tool_calls=2, spend_minor=2, duration_seconds=60),
        ]
    )
    report = baseline.classify(
        BehaviorSample(
            turns=40,
            tool_calls=40,
            unknown_outcomes=30,
            spend_minor=9_000,
            duration_seconds=60,
        )
    )
    assert report.severity == "critical"
    metrics = {item.metric for item in report.anomalies}
    assert {"spend_minor_per_minute", "unknown_outcome_rate"} <= metrics


def test_tool_errors_and_denials_are_flagged():
    baseline = BehaviorBaseline(
        [BehaviorSample(turns=10, tool_calls=10, duration_seconds=60) for _ in range(3)]
    )
    report = baseline.classify(
        BehaviorSample(
            turns=10, tool_calls=10, tool_errors=8, denied_calls=6,
            duration_seconds=60,
        )
    )
    metrics = {item.metric for item in report.anomalies}
    assert {"tool_error_rate", "denied_call_rate"} <= metrics
    assert report.severity in ("warn", "critical")


def test_quarantine_pauses_the_agent_and_blocks_capability_calls(tmp_path):
    bos = store(tmp_path)
    tenant, business = scope(bos)
    control, agent = _agent(bos, tenant, business)
    safety = SafetyService(bos, control=control, clock=lambda: NOW)
    rec = Recorder()
    rt = runtime(
        tmp_path, bos, adapters={READ: rec},
        snap=snapshot(tenant.tenant_id, subject=agent.agent_id),
    )
    rt.set_guard(safety.guard_for_runtime())

    record = safety.quarantine(
        tenant.tenant_id,
        agent.agent_id,
        "anomalous spend",
        actor="operator",
        idempotency_key="q-1",
    )
    assert record.paused is True
    assert record.status_before == "active"
    assert record.status_after == "paused"
    assert record.audit_id is not None
    assert control.get(tenant.tenant_id, agent.agent_id).status == "paused"

    with pytest.raises(BusinessError) as exc:
        safety.ensure_can_act(tenant.tenant_id, agent.agent_id)
    assert exc.value.code == SAFETY_QUARANTINED_CODE

    with pytest.raises(BusinessError) as call_exc:
        rt.call(
            tenant_id=tenant.tenant_id,
            business_id=business.business_id,
            subject_id=agent.agent_id,
            actor=agent.agent_id,
            capability_id=READ,
            policy=policy(READ),
            idempotency_key="q-call",
        )
    assert call_exc.value.code == SAFETY_QUARANTINED_CODE
    assert rec.calls == []

    repeated = safety.quarantine(
        tenant.tenant_id,
        agent.agent_id,
        "again",
        actor="operator",
        idempotency_key="q-2",
    )
    assert repeated.replayed is True


def test_pause_resume_stop_revoke_and_archive(tmp_path):
    bos = store(tmp_path)
    tenant, business = scope(bos)
    control, agent = _agent(bos, tenant, business)
    safety = SafetyService(bos, control=control)

    paused = safety.pause(
        tenant.tenant_id, agent.agent_id, idempotency_key="pause-1"
    )
    assert paused.agent.status == "paused"
    resumed = safety.resume(
        tenant.tenant_id, agent.agent_id, idempotency_key="resume-1"
    )
    assert resumed.agent.status == "active"

    turn = control.begin_turn(
        tenant.tenant_id, agent.agent_id, idempotency_key="turn-1"
    )
    stopped = safety.stop(
        tenant.tenant_id, agent.agent_id, idempotency_key="stop-1",
        turn_id=turn.turn_id,
    )
    assert stopped["turn"]["status"] == "interrupted"

    credential_calls = []
    lease_calls = []

    def revoke_credentials(tenant_id, agent_id):
        credential_calls.append((tenant_id, agent_id))
        return {"revoked": ["secret:gmail"]}

    def revoke_leases(tenant_id, agent_id):
        lease_calls.append((tenant_id, agent_id))
        return {"released": ["lease:1"]}

    revoker = SafetyService(
        bos,
        control=control,
        credential_revoker=revoke_credentials,
        lease_revoker=revoke_leases,
    )
    revoked = revoker.revoke(
        tenant.tenant_id, agent.agent_id, idempotency_key="revoke-1"
    )
    assert revoked.agent.status == "revoked"
    assert revoked.revocations["credentials"]["ok"] is True
    assert revoked.revocations["leases"]["ok"] is True
    assert credential_calls == [(tenant.tenant_id, agent.agent_id)]
    assert lease_calls == [(tenant.tenant_id, agent.agent_id)]

    archived = safety.archive(
        tenant.tenant_id, agent.agent_id, idempotency_key="archive-1"
    )
    assert archived.agent.status == "archived"


def test_revoke_records_a_failing_callback_without_lying(tmp_path):
    bos = store(tmp_path)
    tenant, business = scope(bos)
    control, agent = _agent(bos, tenant, business)

    def boom(tenant_id, agent_id):
        raise RuntimeError("vault unreachable")

    safety = SafetyService(bos, control=control, credential_revoker=boom)
    receipt = safety.revoke(
        tenant.tenant_id, agent.agent_id, idempotency_key="revoke-boom"
    )
    assert receipt.revocations["credentials"]["ok"] is False
    assert "vault unreachable" in receipt.revocations["credentials"]["error"]
    assert receipt.agent.status == "revoked"


def test_kill_switch_invokes_every_stopper_once_and_is_idempotent(tmp_path):
    bos = store(tmp_path)
    tenant, _ = scope(bos)
    counts = {name: 0 for name in KILL_SWITCH_SURFACES}

    def make(name):
        def stop():
            counts[name] += 1
            return {"surface": name}
        return stop

    switch = OutOfBandKillSwitch(
        bos, {name: make(name) for name in KILL_SWITCH_SURFACES},
        clock=lambda: NOW,
    )
    report = switch.engage(
        tenant_id=tenant.tenant_id, reason="rogue agent", actor="operator"
    )
    assert report.status == "STOPPED"
    assert set(report.surfaces_stopped) == set(KILL_SWITCH_SURFACES)
    assert all(counts[name] == 1 for name in KILL_SWITCH_SURFACES)
    assert report.audit_id is not None

    again = switch.engage(
        tenant_id=tenant.tenant_id, reason="again", actor="operator"
    )
    assert again.already_engaged is True
    assert all(counts[name] == 1 for name in KILL_SWITCH_SURFACES)
    audits = bos.list_audit(tenant.tenant_id, action="safety.kill_switch")
    assert len(audits) == 1


def test_a_failing_stopper_is_recorded_without_skipping_the_rest(tmp_path):
    bos = store(tmp_path)
    tenant, _ = scope(bos)
    called = []

    def scheduler():
        called.append("scheduler")
        raise RuntimeError("scheduler offline")

    def ok(name):
        def stop():
            called.append(name)
            return {"stopped": name}
        return stop

    switch = OutOfBandKillSwitch(
        bos,
        {
            "scheduler": scheduler,
            "gateway": ok("gateway"),
            "leases": ok("leases"),
            "egress": ok("egress"),
        },
        clock=lambda: NOW,
    )
    report = switch.engage(
        tenant_id=tenant.tenant_id, reason="partial outage", actor="operator"
    )
    assert report.status == "PARTIAL"
    assert report.surfaces_failed == ("scheduler",)
    assert set(report.surfaces_stopped) == {"gateway", "leases", "egress"}
    assert called == ["scheduler", "gateway", "leases", "egress"]
    scheduler_row = next(item for item in report.surfaces if item.surface == "scheduler")
    assert scheduler_row.ok is False
    assert "scheduler offline" in scheduler_row.error


def test_kill_switch_refuses_an_incomplete_surface_set(tmp_path):
    bos = store(tmp_path)
    with pytest.raises(BusinessError) as exc:
        OutOfBandKillSwitch(bos, {"scheduler": lambda: None})
    assert exc.value.code == SAFETY_KILL_SWITCH_INCOMPLETE_CODE


def test_operator_kill_switch_needs_no_agent(tmp_path):
    bos = store(tmp_path)
    tenant = bos.create_tenant("Only Tenant")
    report = OutOfBandKillSwitch(
        bos,
        {name: (lambda: True) for name in KILL_SWITCH_SURFACES},
        clock=lambda: NOW,
    ).engage(tenant_id=tenant.tenant_id, reason="operator drill")
    assert report.status == "STOPPED"
    assert report.actor == "operator"


def test_runtime_and_lease_stoppers_stop_real_surfaces(tmp_path):
    bos = store(tmp_path)
    tenant, business = scope(bos)
    rec = Recorder()
    rt = runtime(
        tmp_path, bos, adapters={READ: rec}, snap=snapshot(tenant.tenant_id)
    )
    lease = runs.acquire_lease(
        bos, tenant.tenant_id, "workspace", "ws-1", holder="agent:test"
    )
    switch = OutOfBandKillSwitch(
        bos,
        {
            "scheduler": lambda: {"stopped": True},
            "gateway": runtime_stopper(rt),
            "leases": lease_stopper(bos, tenant.tenant_id),
            "egress": lambda: {"stopped": True},
        },
        clock=lambda: NOW,
    )
    report = switch.engage(tenant_id=tenant.tenant_id, reason="stop everything")
    assert report.status == "STOPPED"
    assert rt.halted is True
    assert runs.get_lease(bos, tenant.tenant_id, lease.lease_id).released_at is not None

    from voicebridge.business.capability_runtime import CAPABILITY_RUNTIME_HALTED_CODE

    with pytest.raises(BusinessError) as exc:
        rt.call(
            tenant_id=tenant.tenant_id,
            business_id=business.business_id,
            subject_id="agent:test",
            actor="agent:test",
            capability_id=READ,
            policy=policy(READ),
            idempotency_key="after-kill",
        )
    assert exc.value.code == CAPABILITY_RUNTIME_HALTED_CODE
