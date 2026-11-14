"""P2-06: an approval bound to another payload can never execute the call."""
from __future__ import annotations

from voicebridge.business import approvals, jobs, ledger, policy
from voicebridge.business.agent_turn import (
    AGENT_TURN_APPROVAL_DENIED,
    AGENT_TURN_APPROVAL_UNAVAILABLE,
    FAILED,
)

from _phase2_helpers import (
    NOW,
    WRITE_ACTION,
    WRITE_RESOURCE,
    WRITE_TOOL,
    ScriptedModel,
    orchestrator,
    reply_text,
    reply_tools,
    scene,
    write_call,
)


def _approved_for(scene_, listing: str) -> str:
    intent = policy.normalized_intent(
        tool=WRITE_TOOL,
        resource=WRITE_RESOURCE,
        action=WRITE_ACTION,
        parameters={"listing": listing},
        currency="USD",
        amount_minor=0,
        data_class="internal",
        environment="production",
    )
    row, _ = approvals.request(
        scene_.store,
        tenant_id=scene_.tenant.tenant_id,
        business_id=scene_.business.business_id,
        action=f"tool:{WRITE_TOOL}",
        requested_by=scene_.agent.principal_id,
        idempotency_key=f"confused-{listing}",
        payload=intent,
        expires_at=NOW + 3600,
    )
    row = approvals.decide(
        scene_.store,
        tenant_id=scene_.tenant.tenant_id,
        approval_id=row.approval_id,
        decision="approved",
        decided_by=scene_.owner.principal_id,
        reason="owner approved a different intent",
        now=NOW,
    )
    assert row.status == "approved"
    return row.approval_id


def test_approval_for_payload_a_cannot_execute_payload_b(tmp_path):
    scene_ = scene(tmp_path)
    wrong = _approved_for(scene_, "other-listing")
    model = ScriptedModel(
        [
            reply_tools(write_call("approved-listing")),
            reply_text("unreachable"),
        ]
    )
    turn = orchestrator(
        scene_,
        model,
        approval_requester=lambda **_: wrong,
        approval_decider=lambda **_: "approved",
    )
    result = turn.execute()

    assert result.status == FAILED
    assert result.error_code == "BUSINESS_APPROVAL_REQUIRED"
    assert scene_.executor.calls == []
    assert result.artifact_id is None
    assert scene_.store.list_artifacts(
        scene_.tenant.tenant_id, business_id=scene_.business.business_id
    ) == []
    assert ledger.list_entries(
        scene_.store,
        scene_.tenant.tenant_id,
        business_id=scene_.business.business_id,
    ) == []
    job = jobs.get(scene_.store, scene_.tenant.tenant_id, result.run_id)
    assert job.status == "failed"
    assert job.last_error_code == "BUSINESS_APPROVAL_REQUIRED"


def test_rejected_approval_is_a_typed_failure(tmp_path):
    scene_ = scene(tmp_path)

    def requester(**kwargs):
        intent = kwargs["intent"]
        row, _ = approvals.request(
            scene_.store,
            tenant_id=scene_.tenant.tenant_id,
            business_id=scene_.business.business_id,
            action=f"tool:{WRITE_TOOL}",
            requested_by=scene_.agent.principal_id,
            idempotency_key="rejected-approval",
            payload=intent,
            expires_at=NOW + 3600,
        )
        approvals.decide(
            scene_.store,
            tenant_id=scene_.tenant.tenant_id,
            approval_id=row.approval_id,
            decision="rejected",
            decided_by=scene_.owner.principal_id,
            reason="owner said no",
            now=NOW,
        )
        return row.approval_id

    model = ScriptedModel(
        [reply_tools(write_call("blocked")), reply_text("unreachable")]
    )
    result = orchestrator(
        scene_,
        model,
        approval_requester=requester,
        approval_decider=lambda **_: "rejected",
    ).execute()

    assert result.status == FAILED
    assert result.error_code == AGENT_TURN_APPROVAL_DENIED
    assert scene_.executor.calls == []


def test_missing_approval_wiring_fails_closed(tmp_path):
    scene_ = scene(tmp_path)
    model = ScriptedModel(
        [reply_tools(write_call("blocked")), reply_text("unreachable")]
    )
    turn = orchestrator(scene_, model, approval_requester=None)
    # Explicitly remove the decider too: a write must never auto-approve.
    turn.approval_decider = None
    result = turn.execute()

    assert result.status == FAILED
    assert result.error_code == AGENT_TURN_APPROVAL_UNAVAILABLE
    assert scene_.executor.calls == []
