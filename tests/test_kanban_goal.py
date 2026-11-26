from __future__ import annotations

import json
from contextlib import closing
from threading import Event
from time import monotonic
from types import SimpleNamespace

import pytest

from voicebridge import kanban as KB, kanban_goal as KG


class Cfg:
    def __init__(self, path):
        self.path = path

    def state_dir(self):
        return self.path

    def get(self, *_args, default=None, **_kwargs):
        return default


def _goal(cfg, turns=2):
    return KB.create(cfg, "Confirm the fixture", body="Check the fixture evidence.",
                     goal_mode=True, goal_max_turns=turns, confirmed=True)["task"]["id"]


def _worker_fixture(monkeypatch, replies, verdicts, on_turn=None):
    from qubit_cli import goals
    from voicebridge import orchestrator

    calls = []
    responses = iter(replies)
    judgements = iter(verdicts)

    class Worker:
        def __init__(self, _cfg, *, no_hotkey, headless):
            assert headless and no_hotkey

        def run_worker_task(self, text, *, session, timeout_s):
            assert timeout_s == KG.WORKER_TURN_TIMEOUT_S
            calls.append((text, session))
            if on_turn:
                on_turn()
            return {"state": "OK", "reply": next(responses)}

        def stop(self):
            pass

    def judge(goal, reply):
        assert "Check the fixture evidence." in goal
        assert reply
        return next(judgements)

    monkeypatch.setattr(orchestrator, "VoiceOrchestrator", Worker)
    monkeypatch.setattr(goals, "judge_goal", judge)
    return calls


def test_approved_goal_reuses_session_and_saves_evidence(tmp_path, monkeypatch):
    cfg = Cfg(tmp_path)
    task_id = _goal(cfg)
    calls = _worker_fixture(monkeypatch, ["Working", "Fixture passed."], [
        ("continue", "Need evidence", False, None, False),
        ("done", "Verified", False, None, False),
    ])
    with pytest.raises(KB.KanbanError) as exc:
        KG.run(cfg, task_id, requested_by="tester")
    assert exc.value.code == KB.CONFIRM_CODE and not calls
    out = KG.run(cfg, task_id, requested_by="tester", confirmed=True)
    assert out["goal_status"] == "done"
    task = KB.show(cfg, task_id)["task"]
    assert task["goal_turns_used"] == 2 and task["completion_summary"] == "Fixture passed."
    assert task["goal_mode"] and not task["goal_running"]
    assert len(calls) == 2 and calls[0][1] == calls[1][1]
    with pytest.raises(KB.KanbanError):
        KG.run(cfg, task_id, requested_by="tester", confirmed=True)
    snapshot = KB.export(cfg)["snapshot"]
    assert "goal_run_token" not in snapshot["tasks"][0]
    KB.import_snapshot(cfg, snapshot, confirmed=True)
    assert KB.show(cfg, task_id)["task"]["goal_max_turns"] == 2


def test_goal_cap_blocks_before_an_extra_turn(tmp_path, monkeypatch):
    cfg = Cfg(tmp_path)
    task_id = _goal(cfg, turns=1)
    calls = _worker_fixture(monkeypatch, ["Still working"], [
        ("continue", "Still working", False, None, False),
    ])
    out = KG.run(cfg, task_id, requested_by="tester", confirmed=True)
    assert out["goal_status"] == "blocked" and "budget" in out["detail"]
    assert out["task"]["goal_turns_used"] == len(calls) == 1
    assert not out["task"]["goal_running"]


def test_goal_worker_deadline_interrupts_and_releases_claim(tmp_path, monkeypatch):
    from qubit_cli import goals
    from voicebridge import agent, orchestrator

    cfg = Cfg(tmp_path)
    task_id = _goal(cfg)
    interrupted = Event()
    entered = Event()
    stopped = Event()
    reasons = []

    class Runtime:
        def run_conversation(self, _text):
            entered.set()
            assert interrupted.wait(5), "worker was never interrupted"
            return {"final_response": "Late reply"}

        def hard_interrupt(self, *, tool_reason):
            reasons.append(tool_reason)
            interrupted.set()

    class Worker:
        def __init__(self, _cfg, *, no_hotkey, headless):
            assert no_hotkey and headless

        def run_worker_task(self, text, *, session, timeout_s):
            result = agent._run_vendored_turn(Runtime(), text, monotonic() + timeout_s)
            assert result is None, "a reply after expiry must not reach the judge"
            return agent._worker_timeout(session)

        def stop(self):
            stopped.set()

    def judge(*_args):
        pytest.fail("the judge must not run after the worker deadline")

    monkeypatch.setattr(KG, "WORKER_TURN_TIMEOUT_S", 0.1)
    monkeypatch.setattr(orchestrator, "VoiceOrchestrator", Worker)
    monkeypatch.setattr(goals, "judge_goal", judge)

    out = KG.run(cfg, task_id, requested_by="tester", confirmed=True)
    assert entered.is_set() and stopped.is_set()
    assert reasons == ["worker deadline"]
    assert out["detail"] == "Worker turn exceeded its 0.1-second time limit."
    saved = KB.show(cfg, task_id)
    assert saved["task"]["status"] == "blocked" and not saved["task"]["goal_running"]
    assert saved["task"]["goal_turns_used"] == 1 and not saved["task"]["completion_summary"]
    with closing(KB._connect(cfg)) as db:
        events = db.execute("SELECT kind FROM events WHERE task_id=? ORDER BY id", (task_id,))
        assert [row[0] for row in events] == ["create", "goal_started", "block", "goal_finished"]


def test_goal_cli_keeps_worker_progress_out_of_json(tmp_path, monkeypatch, capsys):
    from voicebridge.cli import _cmd_kanban

    cfg = Cfg(tmp_path)
    task_id = _goal(cfg, turns=1)
    _worker_fixture(monkeypatch, ["Fixture passed."], [
        ("done", "Verified", False, None, False),
    ], lambda: print("Worker progress"))
    args = SimpleNamespace(kanban_command="run-goal", task_id=task_id, confirm=True)

    assert _cmd_kanban(cfg, args) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out)["goal_status"] == "done"
    assert "Worker progress" in captured.err


def test_reclaimed_goal_cannot_settle_or_overlap(tmp_path, monkeypatch):
    cfg = Cfg(tmp_path)
    task_id = _goal(cfg)

    def reclaim_during_turn():
        with pytest.raises(KB.KanbanError) as exc:
            KB.edit(cfg, task_id, title="Changed", confirmed=True)
        assert exc.value.code == KB.GOAL_ACTIVE_CODE
        KB.reclaim(cfg, task_id, confirmed=True)
        with pytest.raises(KB.KanbanError) as exc:
            KG.run(cfg, task_id, requested_by="second", confirmed=True)
        assert exc.value.code == KB.GOAL_ACTIVE_CODE

    calls = _worker_fixture(monkeypatch, ["Stale worker response"], [], reclaim_during_turn)
    assert KG.run(cfg, task_id, requested_by="tester", confirmed=True)["state"] == "STOPPED"
    task = KB.show(cfg, task_id)["task"]
    assert task["status"] == "open" and not task["goal_running"]
    assert task["completion_summary"] == "" and len(calls) == 1


@pytest.mark.parametrize("interrupt", [False, True])
def test_stopping_a_goal_preserves_the_spent_turn(tmp_path, monkeypatch, interrupt):
    cfg = Cfg(tmp_path)
    task_id = _goal(cfg)
    stopped = False

    def stop_during_turn():
        nonlocal stopped
        stopped = True
        if interrupt:
            raise KeyboardInterrupt

    monkeypatch.setattr(KB, "estop_engaged", lambda _cfg: stopped)
    calls = _worker_fixture(monkeypatch, ["Reply after stop"], [], stop_during_turn)
    out = KG.run(cfg, task_id, requested_by="tester", confirmed=True)
    assert out["goal_status"] == "blocked" and out["task"]["goal_turns_used"] == len(calls) == 1
    assert not out["task"]["goal_running"]
    assert out["detail"] == ("Goal run interrupted." if interrupt else "Emergency stop is latched.")


def test_voice_approval_binds_goal_text_and_turn_budget(tmp_path, monkeypatch):
    from voicebridge import voice_commands as VC

    cfg = Cfg(tmp_path)
    task_id = _goal(cfg)
    phrase = f"Qubit, run Kanban goal {task_id}"
    ctx = VC.make_ctx(cfg)
    staged = VC.voice_say(cfg, phrase)
    assert staged["state"] == VC.NEEDS_CONFIRM and "up to 2 turns" in staged["speech"]
    KB.edit(cfg, task_id, body="New goal", confirmed=True)
    denied = VC.confirm_pending(ctx, staged["confirmation_id"])
    assert denied.kind == VC.FAILED and denied.receipt["code"] == KB.STATE_CODE
    assert KB.show(cfg, task_id)["task"]["goal_turns_used"] == 0


def test_dashboard_create_preserves_goal_settings_and_approval(tmp_path):
    from voicebridge import qubit_compat as QC

    cfg = Cfg(tmp_path)
    form = {"title": "Fixture", "goal_mode": True, "goal_max_turns": 3}
    status, staged = QC.h_plugin_kanban_tasks_create(cfg, {}, form, {})
    assert status == 409
    cid = staged["confirmation_id"]
    status, changed = QC.h_plugin_kanban_tasks_create(
        cfg, {}, {**form, "confirmation_id": cid, "goal_max_turns": 4}, {})
    assert status == 409 and changed["confirmation_id"] != cid
    assert KB.list_tasks(cfg)["tasks"] == []
    status, saved = QC.h_plugin_kanban_tasks_create(cfg, {}, {**form, "confirmation_id": cid}, {})
    assert status == 200 and saved["task"]["goal_mode"]
    assert saved["task"]["goal_max_turns"] == 3
