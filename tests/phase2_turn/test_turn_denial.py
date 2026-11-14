"""P2-06: denial/unknown tool leaves no side effect and marks the turn failed."""
from __future__ import annotations

from voicebridge.business import approvals, durable, jobs, ledger
from voicebridge.business.agent_turn import (
    AGENT_TURN_TOOL_DENIED,
    AGENT_TURN_TOOL_UNKNOWN,
    FAILED,
)
from voicebridge.business.errors import BusinessError

from _phase2_helpers import (
    ScriptedModel,
    orchestrator,
    reply_text,
    reply_tools,
    scene,
    write_call,
)


def _assert_no_side_effect(scene_, result):
    assert result.status == FAILED
    assert result.artifact_id is None
    assert result.receipt is None
    assert result.receipts == ()
    artifacts = scene_.store.list_artifacts(
        scene_.tenant.tenant_id, business_id=scene_.business.business_id
    )
    assert artifacts == []
    entries = ledger.list_entries(
        scene_.store,
        tenant_id=scene_.tenant.tenant_id,
        business_id=scene_.business.business_id,
    )
    assert entries == []
    assert scene_.executor.calls == []
    job = jobs.get(scene_.store, scene_.tenant.tenant_id, result.run_id)
    assert job.status == "failed"
    assert job.last_error_code == result.error_code
    # A failure checkpoint is a recovery point, not a receipt/artifact.
    checkpoints = durable.list_checkpoints(
        scene_.store, scene_.tenant.tenant_id, result.run_id
    )
    assert [row.state["stage"] for row in checkpoints] == [
        "turn_started",
        "failed",
    ]
    assert all(row.tool_receipts == [] for row in checkpoints)


def test_snapshot_denied_tool_leaves_no_side_effect(tmp_path):
    scene_ = scene(tmp_path, write=False)
    model = ScriptedModel(
        [reply_tools(write_call("denied-listing")), reply_text("unreachable")]
    )
    turn = orchestrator(scene_, model, write=False, approval_requester=lambda **_: "x")
    result = turn.execute()

    assert result.error_code == AGENT_TURN_TOOL_DENIED
    _assert_no_side_effect(scene_, result)
    # Denial is decided before the gateway: no invocation, no approval.
    assert approvals.list_approvals(
        scene_.store, scene_.tenant.tenant_id
    ) == []


def test_unknown_tool_is_a_typed_failure(tmp_path):
    scene_ = scene(tmp_path)
    model = ScriptedModel(
        [
            reply_tools(
                type(write_call())(
                    tool="office.not-a-capability",
                    arguments={"x": 1},
                )
            ),
            reply_text("unreachable"),
        ]
    )
    result = orchestrator(scene_, model).execute()

    assert result.status == FAILED
    assert result.error_code == AGENT_TURN_TOOL_UNKNOWN
    _assert_no_side_effect(scene_, result)


def test_policy_refusal_still_marks_the_run_failed(tmp_path):
    """A gateway policy refusal is never retried into a completed turn."""
    scene_ = scene(tmp_path)
    model = ScriptedModel(
        [reply_tools(write_call()), reply_text("unreachable")]
    )

    def broken_requester(**_):
        raise BusinessError("BUSINESS_APPROVAL_REQUIRED", "no approver")

    turn = orchestrator(scene_, model, approval_requester=broken_requester)
    result = turn.execute()
    assert result.status == FAILED
    assert result.error_code == "BUSINESS_APPROVAL_REQUIRED"
    _assert_no_side_effect(scene_, result)
