"""P2-06: one low-risk real turn records success end to end (hermetic)."""
from __future__ import annotations

import json

from voicebridge.business import approvals, durable, jobs
from voicebridge.business.agent_turn import COMPLETED, SAFE_RESUME_STAGES

from _phase2_helpers import (
    NOW,
    READ_TOOL,
    WRITE_TOOL,
    ScriptedModel,
    orchestrator,
    read_call,
    reply_text,
    reply_tools,
    scene,
    write_call,
)


def test_read_and_approved_write_turn_records_success(tmp_path):
    scene_ = scene(tmp_path)
    model = ScriptedModel(
        [
            reply_tools(read_call()),
            reply_tools(write_call("p2-listing")),
            reply_text("Published the listing."),
        ]
    )
    turn = orchestrator(scene_, model)
    result = turn.execute()

    assert result.status == COMPLETED, result.as_dict()
    assert result.text == "Published the listing."
    assert result.error_code is None
    assert result.receipt is not None
    assert result.receipt["provider"] == "fake-etsy"
    assert result.receipt["status"] == "accepted"
    assert result.receipt["listing"] == "p2-listing"
    assert scene_.executor.tools == [READ_TOOL, WRITE_TOOL]

    # The durable run record is the bos_job, not a second engine.
    job = jobs.get(
        scene_.store, scene_.tenant.tenant_id, result.run_id
    )
    assert job.status == "completed"
    assert job.output["artifact_id"] == result.artifact_id
    assert job.output["receipt"]["request_id"] == "fake-request-1"

    # Artifact is content-addressed and tied to the run/task.
    artifacts = scene_.store.list_artifacts(
        scene_.tenant.tenant_id, business_id=scene_.business.business_id
    )
    assert len(artifacts) == 1
    artifact = artifacts[0]
    assert artifact.artifact_id == result.artifact_id
    assert artifact.sha256 == result.artifact_sha256
    assert artifact.kind == "agent_turn_result"
    body = json.loads(artifact.body)
    assert body["run_id"] == result.run_id
    assert body["task_id"] == scene_.task.task_id
    assert body["status"] == COMPLETED
    assert body["receipts"][0]["request_id"] == "fake-request-1"

    # A checkpoint exists before and after each effect, plus a terminal one.
    assert result.checkpoints == (1, 2, 3, 4, 5, 6)
    checkpoints = durable.list_checkpoints(
        scene_.store, scene_.tenant.tenant_id, result.run_id
    )
    stages = [row.state["stage"] for row in checkpoints]
    assert stages == [
        "turn_started",
        "tool_pending",
        "tool_effect_recorded",
        "tool_pending",
        "tool_effect_recorded",
        "completed",
    ]
    assert set(stages).issubset(SAFE_RESUME_STAGES | {"tool_pending"})
    assert checkpoints[-1].artifact_ids == [artifact.artifact_id]

    # Event log is append-only and records the tool/approval/completion chain.
    events = result.events
    assert "agent.turn.tool.completed" in events
    assert "agent.turn.approval.granted" in events
    assert "agent.turn.receipt.recorded" in events
    assert "agent.turn.completed" in events

    # The approval was bound to the exact intent and approved by the owner.
    approval_rows = approvals.list_approvals(scene_.store, scene_.tenant.tenant_id)
    assert len(approval_rows) == 1
    assert approval_rows[0].status == "approved"
    assert approval_rows[0].action == f"tool:{WRITE_TOOL}"
    assert approval_rows[0].expires_at == NOW + 3600

    # One OpenTelemetry trace explains the run; content capture stays off.
    assert result.trace is not None
    assert len(result.trace["traces"]) == 1
    spans = result.trace["spans"]
    names = {span.name for span in spans}
    assert {"invoke_agent", "chat", "execute_tool", "approval"} <= names
    assert "external_receipt" in names
    assert all(span.capture_content is False for span in spans)
    assert result.tool_calls == 2
    assert result.steps == 3
    assert result.tokens == 0

    # Idempotent execute: the terminal run is replayed, not re-executed.
    replayed = orchestrator(
        scene_,
        ScriptedModel([reply_text("should not be called")]),
    ).execute()
    assert replayed.status == COMPLETED
    assert replayed.replayed is True
    assert replayed.artifact_id == artifact.artifact_id
