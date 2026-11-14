"""P2-06: provider timeout is unknown; resume only from a safe checkpoint."""
from __future__ import annotations

import pytest

from voicebridge.business import durable, jobs
from voicebridge.business.agent_turn import (
    AGENT_TURN_MODEL_TIMEOUT,
    AGENT_TURN_RECONCILIATION_REQUIRED,
    COMPLETED,
    UNKNOWN,
)
from voicebridge.business.errors import BusinessError

from _phase2_helpers import (
    ScriptedModel,
    orchestrator,
    read_call,
    reply_text,
    reply_tools,
    scene,
    timeout_reply,
)


def test_model_timeout_is_unknown_never_completed(tmp_path):
    scene_ = scene(tmp_path)
    model = ScriptedModel([timeout_reply("provider did not answer")])
    result = orchestrator(scene_, model).execute()

    assert result.status == UNKNOWN
    assert result.error_code == AGENT_TURN_MODEL_TIMEOUT
    assert result.artifact_id is None
    job = jobs.get(scene_.store, scene_.tenant.tenant_id, result.run_id)
    assert job.status == "unknown"
    assert job.last_error_code == "JOB_OUTCOME_UNKNOWN"
    assert scene_.store.list_artifacts(
        scene_.tenant.tenant_id, business_id=scene_.business.business_id
    ) == []
    assert scene_.executor.calls == []

    # An unknown outcome is never silently retried by execute().
    with pytest.raises(BusinessError) as exc:
        orchestrator(
            scene_, ScriptedModel([reply_text("unreachable")])
        ).execute()
    assert exc.value.code == AGENT_TURN_RECONCILIATION_REQUIRED

    # Even resume() refuses until reconciliation is explicit.
    with pytest.raises(BusinessError) as exc:
        orchestrator(
            scene_, ScriptedModel([reply_text("unreachable")])
        ).resume()
    assert exc.value.code == AGENT_TURN_RECONCILIATION_REQUIRED


def test_restart_resumes_from_a_validated_checkpoint(tmp_path):
    scene_ = scene(tmp_path)
    first_model = ScriptedModel(
        [reply_tools(read_call("demo-1")), timeout_reply("process died")]
    )
    first = orchestrator(scene_, first_model, run_key="resume-run").execute()
    assert first.status == UNKNOWN
    assert scene_.executor.tools == ["office.records.read"]

    checkpoints = durable.list_checkpoints(
        scene_.store, scene_.tenant.tenant_id, first.run_id
    )
    assert checkpoints[-1].state["stage"] == "unknown"
    # The transcript and the tool result survive the checkpoint.
    transcript = checkpoints[-1].state["messages"]
    assert any(
        item.get("role") == "tool"
        and "demo-1" in str(item.get("content", ""))
        for item in transcript
    )

    second_model = ScriptedModel([reply_text("Finished after restart.")])
    resumed = orchestrator(
        scene_, second_model, run_key="resume-run"
    ).resume(reconcile_unknown=True)

    assert resumed.status == COMPLETED
    assert resumed.text == "Finished after restart."
    assert resumed.artifact_id is not None
    assert scene_.executor.tools == ["office.records.read"]  # no duplicate read
    job = jobs.get(scene_.store, scene_.tenant.tenant_id, resumed.run_id)
    assert job.status == "completed"
    # The run resumed on the same durable record, with monotonic checkpoints.
    assert first.checkpoints == (1, 2, 3, 4)
    assert resumed.checkpoints == (1, 2, 3, 4, 5)
