"""P2-06: duplicate tool calls and bounded loops."""
from __future__ import annotations

from voicebridge.business import jobs
from voicebridge.business.agent_turn import (
    AGENT_TURN_STEP_LIMIT,
    AGENT_TURN_TOOL_LIMIT,
    COMPLETED,
    FAILED,
)

from _phase2_helpers import (
    READ_TOOL,
    ScriptedModel,
    orchestrator,
    read_call,
    reply_text,
    reply_tools,
    scene,
)


def test_duplicate_tool_call_executes_once(tmp_path):
    scene_ = scene(tmp_path)
    model = ScriptedModel(
        [
            reply_tools(read_call("same"), read_call("same")),
            reply_text("done"),
        ]
    )
    result = orchestrator(scene_, model).execute()

    assert result.status == COMPLETED
    assert result.tool_calls == 2  # both calls were admitted to the loop
    assert scene_.executor.tools == [READ_TOOL]  # only one effect
    assert "agent.turn.tool.deduped" in result.events


def test_distinct_tool_calls_each_execute(tmp_path):
    scene_ = scene(tmp_path)
    model = ScriptedModel(
        [
            reply_tools(read_call("a"), read_call("b")),
            reply_text("done"),
        ]
    )
    result = orchestrator(scene_, model).execute()
    assert result.status == COMPLETED
    assert scene_.executor.tools == [READ_TOOL, READ_TOOL]
    assert "agent.turn.tool.deduped" not in result.events


def test_step_limit_fails_the_turn(tmp_path):
    scene_ = scene(tmp_path)
    # Every reply asks for a different read, so dedupe cannot end the loop.
    replies = [
        reply_tools(read_call(f"record-{index}")) for index in range(10)
    ]
    model = ScriptedModel(replies)
    result = orchestrator(scene_, model, max_steps=2).execute()

    assert result.status == FAILED
    assert result.error_code == AGENT_TURN_STEP_LIMIT
    assert result.steps == 2  # the third step is refused before the call
    assert len(scene_.executor.calls) == 2
    job = jobs.get(scene_.store, scene_.tenant.tenant_id, result.run_id)
    assert job.status == "failed"
    assert job.last_error_code == AGENT_TURN_STEP_LIMIT


def test_tool_call_limit_fails_the_turn(tmp_path):
    scene_ = scene(tmp_path)
    model = ScriptedModel(
        [
            reply_tools(read_call("a"), read_call("b")),
            reply_text("unreachable"),
        ]
    )
    result = orchestrator(scene_, model, max_tool_calls=1).execute()

    assert result.status == FAILED
    assert result.error_code == AGENT_TURN_TOOL_LIMIT
    assert scene_.executor.tools == [READ_TOOL]
    job = jobs.get(scene_.store, scene_.tenant.tenant_id, result.run_id)
    assert job.last_error_code == AGENT_TURN_TOOL_LIMIT


def test_token_budget_fails_the_turn(tmp_path):
    from voicebridge.business.agent_turn import (
        AGENT_TURN_TOKEN_LIMIT,
        ModelReply,
    )

    scene_ = scene(tmp_path)
    model = ScriptedModel(
        [
            ModelReply(
                text="",
                tool_calls=(read_call("a"),),
                usage={"prompt_tokens": 100, "completion_tokens": 100},
            ),
            reply_text("unreachable"),
        ]
    )
    result = orchestrator(scene_, model, max_tokens=50).execute()
    assert result.status == FAILED
    assert result.error_code == AGENT_TURN_TOKEN_LIMIT
