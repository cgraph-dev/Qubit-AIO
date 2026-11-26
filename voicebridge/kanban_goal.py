"""Explicit, bounded Goal mode runs on the native Kanban store."""
from __future__ import annotations

import json
from contextlib import contextmanager
from hashlib import sha256
from uuid import uuid4

from . import kanban as KB


@contextmanager
def _write(cfg):
    conn = KB._connect(cfg)
    try:
        KB._migrate(conn)
        conn.execute("BEGIN IMMEDIATE")
        with conn:
            yield conn
    finally:
        conn.close()


def signature(task):
    fields = [task[key] for key in ("title", "body", "goal_mode", "goal_max_turns")]
    return sha256(json.dumps(fields, ensure_ascii=False).encode()).hexdigest()


def _start(cfg, task_id, token, actor, expected):
    with _write(cfg) as conn:
        row = KB._get_task(conn, task_id)
        task = KB._task_dict(row)
        if expected is not None and expected != signature(task):
            raise KB.KanbanError(KB.STATE_CODE, "Goal settings changed. Request a fresh approval.")
        if not row["goal_mode"]:
            raise KB.KanbanError(KB.BAD_INPUT_CODE, f"Goal mode is disabled for task #{task_id}")
        if row["goal_run_token"]:
            raise KB.KanbanError(KB.GOAL_ACTIVE_CODE, f"task #{task_id} already has an active goal run")
        if row["status"] not in ("open", "blocked", "claimed"):
            raise KB.KanbanError(KB.STATE_CODE, f"task #{task_id} is {row['status']}")
        KB._require_parents_done(conn, task_id)
        conn.execute(
            "UPDATE tasks SET status='claimed', goal_run_token=?, goal_turns_used=0,"
            " assignee=coalesce(nullif(assignee,''),?), updated_at=? WHERE id=?",
            (token, actor, KB._now(), task_id),
        )
        KB._event(conn, task_id, "goal_started")
        return task


def _reserve_turn(cfg, task_id, token):
    with _write(cfg) as conn:
        row = KB._get_task(conn, task_id)
        if row["goal_run_token"] != token:
            return False
        KB._require_parents_done(conn, task_id)
        if row["goal_turns_used"] >= row["goal_max_turns"]:
            return False
        conn.execute("UPDATE tasks SET goal_turns_used=goal_turns_used+1 WHERE id=?", (task_id,))
        return True


def _still_current(cfg, task_id, token):
    conn = KB._connect(cfg)
    try:
        row = conn.execute("SELECT goal_run_token FROM tasks WHERE id=?", (task_id,)).fetchone()
        return row is not None and row["goal_run_token"] == token
    finally:
        conn.close()


def _finish(cfg, task_id, token, status, detail, reply=""):
    if status == "done" and KB.estop_engaged(cfg):
        status, detail = "blocked", "Emergency stop is latched."
    detail = KB.redact_spec(detail)[:400]
    summary = KB.redact_spec(reply)[:KB.MAX_SUMMARY_LEN] if status == "done" else ""
    with _write(cfg) as conn:
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None or row["goal_run_token"] != token:
            return {"state": "STOPPED", "goal_status": "stopped",
                    "detail": "This run no longer owns the task."}
        if status == "done":
            try:
                KB._require_parents_done(conn, task_id)
            except KB.KanbanError as exc:
                status, detail, summary = "blocked", exc.detail, ""
        conn.execute(
            "UPDATE tasks SET status=?, goal_run_token=NULL, updated_at=?,"
            " completion_summary=coalesce(nullif(?,''), completion_summary) WHERE id=?",
            (status, KB._now(), summary, task_id),
        )
        KB._event(conn, task_id, "complete" if status == "done" else "block", detail)
        KB._event(conn, task_id, "goal_finished", detail)
        return {"state": "OK", "goal_status": status, "detail": detail,
                "task": KB._task_dict(KB._get_task(conn, task_id))}


def run(cfg, task_id: int, *, requested_by: str, confirmed: bool = False, expected=None):
    """Run a goal only after approval and only while this run owns the card."""
    KB._canvas_guard(cfg, "run-goal", confirmed)
    from filelock import FileLock, Timeout

    task_id = KB.dependency_id(task_id)
    directory = KB._db_path(cfg).parent
    directory.mkdir(parents=True, exist_ok=True)
    try:
        with FileLock(directory / f"kanban-goal-{task_id}.lock", timeout=0):
            return _run_owned(cfg, task_id, requested_by, expected)
    except Timeout as exc:
        raise KB.KanbanError(KB.GOAL_ACTIVE_CODE, f"task #{task_id} already has an active goal run") from exc


def _run_owned(cfg, task_id, requested_by, expected):
    from qubit_cli.goals import judge_goal

    from .orchestrator import VoiceOrchestrator

    token = uuid4().hex
    task = _start(cfg, task_id, token, requested_by, expected)
    goal = "\n\n".join(part for part in (task["title"], task["body"]) if part)
    session = f"kanban-goal-{task_id}-{token}"
    prompt = f"Work on this confirmed Kanban task. Report what you did and the evidence.\n\n{goal}"
    detail = "Goal turn budget exhausted."
    orch = None
    try:
        orch = VoiceOrchestrator(cfg, no_hotkey=True, headless=True)
        for _ in range(task["goal_max_turns"]):
            if KB.estop_engaged(cfg):
                detail = "Emergency stop is latched."
                break
            if not _reserve_turn(cfg, task_id, token):
                break
            result = orch.run_worker_task(prompt, session=session)
            if not _still_current(cfg, task_id, token):
                break
            if result.get("state") != "OK":
                detail = "Worker stopped: " + str(result.get("code", "WORKER_ERROR"))
                break
            if KB.estop_engaged(cfg):
                detail = "Emergency stop is latched."
                break
            reply = str(result.get("reply", ""))
            verdict, reason, malformed, _, offline = judge_goal(goal, reply)
            if offline:
                detail = "Goal judge unavailable."
                break
            if verdict == "done" and not malformed and reply.strip():
                return _finish(cfg, task_id, token, "done", "Goal judge accepted the evidence.", reply)
            if verdict == "blocked" and not malformed:
                detail = "Goal judge blocked the task: " + str(reason)
                break
            prompt = "Continue the same confirmed task. Report the remaining work and the evidence."
    except KB.KanbanError as exc:
        detail = exc.detail
    except Exception as exc:
        detail = f"Goal run stopped: {type(exc).__name__}"
    finally:
        if orch is not None:
            try:
                orch.stop()
            except Exception:
                pass
    return _finish(cfg, task_id, token, "blocked", detail)
