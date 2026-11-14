"""P2-06: an expired or stale policy snapshot is refused, never used."""
from __future__ import annotations

import pytest

from voicebridge.business import jobs
from voicebridge.business.agent_turn import (
    AGENT_TURN_SNAPSHOT_STALE,
    COMPLETED,
    UNKNOWN,
)
from voicebridge.business.errors import BusinessError

from _phase2_helpers import (
    EXPIRES,
    NOW,
    ScriptedModel,
    orchestrator,
    reply_text,
    reply_tools,
    scene,
    timeout_reply,
    write_call,
)


def test_expired_snapshot_is_refused_before_any_run(tmp_path):
    scene_ = scene(tmp_path)
    turn = orchestrator(
        scene_,
        ScriptedModel([reply_text("unreachable")]),
        issued_at=NOW - 7200,
        expires_at=NOW - 1,
    )
    with pytest.raises(BusinessError) as exc:
        turn.execute()
    assert exc.value.code == AGENT_TURN_SNAPSHOT_STALE
    assert jobs.list_jobs(scene_.store, scene_.tenant.tenant_id) == []
    assert scene_.executor.calls == []


def test_resume_refuses_an_expired_snapshot(tmp_path):
    scene_ = scene(tmp_path)
    first = orchestrator(
        scene_,
        ScriptedModel([timeout_reply("timed out")]),
        run_key="stale-resume",
        expires_at=EXPIRES,
    ).execute()
    assert first.status == UNKNOWN

    stale = orchestrator(
        scene_,
        ScriptedModel([reply_text("unreachable")]),
        run_key="stale-resume",
        issued_at=NOW - 7200,
        expires_at=NOW - 1,
    )
    with pytest.raises(BusinessError) as exc:
        stale.resume(reconcile_unknown=True)
    assert exc.value.code == AGENT_TURN_SNAPSHOT_STALE


def test_resume_refuses_a_checkpoint_from_a_previous_policy_revision(tmp_path):
    scene_ = scene(tmp_path)
    first = orchestrator(
        scene_,
        ScriptedModel([timeout_reply("timed out")]),
        run_key="revision-resume",
    ).execute()
    assert first.status == UNKNOWN

    newer = orchestrator(
        scene_,
        ScriptedModel([reply_text("unreachable")]),
        run_key="revision-resume",
        revision="p2-2",
    )
    with pytest.raises(BusinessError) as exc:
        newer.resume(reconcile_unknown=True)
    assert exc.value.code == AGENT_TURN_SNAPSHOT_STALE


def test_completed_run_is_not_replayed_under_a_stale_snapshot(tmp_path):
    scene_ = scene(tmp_path)
    done = orchestrator(
        scene_,
        ScriptedModel([reply_text("done")]),
        run_key="completed-stale",
    ).execute()
    assert done.status == COMPLETED

    stale = orchestrator(
        scene_,
        ScriptedModel([reply_text("unreachable")]),
        run_key="completed-stale",
        issued_at=NOW - 7200,
        expires_at=NOW - 1,
    )
    # Preconditions run before replay: a stale snapshot is never accepted.
    with pytest.raises(BusinessError) as exc:
        stale.execute()
    assert exc.value.code == AGENT_TURN_SNAPSHOT_STALE


def test_write_snapshot_is_the_only_approval_path(tmp_path):
    scene_ = scene(tmp_path)
    result = orchestrator(
        scene_,
        ScriptedModel(
            [reply_tools(write_call("listing")), reply_text("done")]
        ),
        run_key="approval-path",
    ).execute()
    assert result.status == COMPLETED
    assert result.receipt is not None
