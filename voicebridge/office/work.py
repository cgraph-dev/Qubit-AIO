"""The work queue: tasks, who does them, and who says they are done.

This is what makes "deploy N agents" mean something. A seat with no task is a
seated spectator, and nine of those are slower than one of you.

Three rules from the plan, enforced here rather than hoped for:

* **A task needs a machine-checkable exit condition** (`exit_check`), because
  "done" is never the model's own verdict (C17). A task without one cannot be
  created — an agent asked to judge its own completion will always comply.
* **The reviewer is exactly one level above** the assignee (C31). A worker does
  not accept its own work and peers do not sign each other off.
* **A rejection is a fix instruction, not a restart.** `reject()` records what
  must change and returns the task to the same seat with one attempt spent. The
  trail is append-only; a rejection cannot be edited away.

Turns are bounded by **count**, not by dollars, when the provider is
subscription-backed: a flat-rate gateway has no marginal dollar cost, so a dollar
cap cannot bound it and pretending otherwise would be theatre (D16/M5).
"""
from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional

from . import store
from .errors import BAD_INPUT_CODE, BAD_STATE_CODE, NOT_FOUND_CODE, OfficeError

OPEN_STATES = ("open", "running", "review", "fixing")
CLOSED_STATES = ("accepted", "rejected", "abandoned")


def today(ts: Optional[float] = None) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(
        ts if ts is not None else store.now()))


def _seat(conn, agent_id: int) -> Dict[str, Any]:
    row = conn.execute("SELECT * FROM office_agent WHERE id=?",
                       (int(agent_id),)).fetchone()
    if row is None:
        raise OfficeError(NOT_FOUND_CODE, f"no seat #{agent_id}")
    return dict(row)


def _task(conn, task_id: int) -> Dict[str, Any]:
    row = conn.execute("SELECT * FROM office_task WHERE id=?",
                       (int(task_id),)).fetchone()
    if row is None:
        raise OfficeError(NOT_FOUND_CODE, f"no task #{task_id}")
    return dict(row)


def _trail(cfg: Any, task_id: int, *, actor: str, kind: str, body: str = "",
           payload: Optional[Dict[str, Any]] = None) -> None:
    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        conn.execute(
            "INSERT INTO office_task_event(ts, task_id, actor, kind, body,"
            " payload) VALUES(?,?,?,?,?,?)",
            (store.now(), int(task_id), str(actor), str(kind), str(body),
             json.dumps(payload or {}, sort_keys=True, default=str)))
        conn.commit()
    finally:
        conn.close()


def create(cfg: Any, title: str, *, brief: str = "", exit_check: str = "",
           department_id: Optional[int] = None, created_by: Optional[int] = None,
           turn_budget: int = 1, max_attempts: int = 3,
           confirmed: bool = False, requested_by: str = "cli:owner"
           ) -> Dict[str, Any]:
    """Open a task. Refuses without an exit condition the machine can check."""
    if not confirmed:
        raise OfficeError(store.CONFIRM_CODE, "office task-create requires --confirm")
    clean = str(title or "").strip()
    if not clean:
        raise OfficeError(BAD_INPUT_CODE, "a task needs a title")
    check = str(exit_check or "").strip()
    if not check:
        raise OfficeError(
            "OFFICE_TASK_NEEDS_EXIT_CHECK",
            "refused: a task must state how its completion is checked. "
            "'Done' is never the model's own verdict (C17); a task whose "
            "completion only the assignee can judge is a task that will always "
            "succeed.")
    if int(turn_budget) < 1:
        raise OfficeError(BAD_INPUT_CODE, "turn_budget must be >= 1")

    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        cur = conn.execute(
            "INSERT INTO office_task(ts, title, brief, exit_check,"
            " department_id, created_by, status, turn_budget, max_attempts,"
            " created_at) VALUES(?,?,?,?,?,?,'open',?,?,?)",
            (store.now(), clean[:200], str(brief), check, department_id,
             created_by, int(turn_budget), int(max_attempts), store.now()))
        conn.commit()
        tid = int(cur.lastrowid or 0)
    finally:
        conn.close()
    _trail(cfg, tid, actor=f"seat:{created_by or 'owner'}", kind="created",
           body=clean, payload={"exit_check": check, "brief": brief})
    store.audit("task.create", requested_by, {"id": tid, "title": clean[:80]})
    return {"state": "OK", "id": tid, "title": clean, "status": "open",
            "exit_check": check}


def claim(cfg: Any, task_id: int, *, agent_id: int, confirmed: bool = False,
          requested_by: str = "office:dispatch") -> Dict[str, Any]:
    """Assign a task to a seat, enforcing the chain."""
    if not confirmed:
        raise OfficeError(store.CONFIRM_CODE, "office task-claim requires --confirm")
    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        task = _task(conn, task_id)
        seat = _seat(conn, agent_id)
        # `running` is claimable on purpose: a task whose turn was refused or
        # whose check failed stays in progress and must be picked up again.
        if task["status"] not in ("open", "fixing", "running"):
            raise OfficeError(
                BAD_STATE_CODE,
                f"task #{task_id} is {task['status']}; only a task in open, "
                f"fixing or running can be worked")
        if seat["status"] == "terminated":
            raise OfficeError(BAD_STATE_CODE,
                              f"seat {seat['slug']!r} is terminated")
        # the reviewer must be exactly one level above the assignee (C31)
        reviewer = seat["supervisor_id"]
        if reviewer is None:
            raise OfficeError(
                BAD_STATE_CODE,
                f"seat {seat['slug']!r} has no supervisor, so nothing can "
                f"review its work (C30)")
        conn.execute(
            "UPDATE office_task SET assignee_id=?, reviewer_id=?, status='running'"
            " WHERE id=?", (int(agent_id), int(reviewer), int(task_id)))
        conn.commit()
    finally:
        conn.close()
    _trail(cfg, task_id, actor=f"seat:{agent_id}", kind="claimed",
           body=f"assigned to {seat['slug']}; reviewer {reviewer}")
    return {"state": "OK", "id": int(task_id), "assignee_id": int(agent_id),
            "reviewer_id": int(reviewer), "status": "running"}


def record_turn(cfg: Any, task_id: int, *, run_id: str = "", artifact: str = "",
                outcome: str = "", payload: Optional[Dict[str, Any]] = None,
                ) -> Dict[str, Any]:
    """Note a turn against the task, and whether it satisfied the exit check."""
    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        task = _task(conn, task_id)
        conn.execute(
            "UPDATE office_task SET turns_used=turns_used+1, run_id=?,"
            " artifact=?, status=? WHERE id=?",
            (str(run_id), str(artifact),
             "review" if outcome == "satisfied" else task["status"],
             int(task_id)))
        conn.commit()
    finally:
        conn.close()
    _trail(cfg, task_id, actor="office:turn", kind="turn",
           body=outcome or "ran", payload={"run_id": run_id,
                                           "artifact": artifact,
                                           **(payload or {})})
    return {"state": "OK", "id": int(task_id), "outcome": outcome,
            "turns_used": int(task["turns_used"]) + 1}


def review(cfg: Any, task_id: int, *, verdict: str, note: str = "",
           by_agent: Optional[int] = None, confirmed: bool = False,
           requested_by: str = "cli:office") -> Dict[str, Any]:
    """The level above accepts or sends it back with a fix instruction.

    A rejection is not a restart: the task returns to its assignee with the note
    attached and one attempt spent. The note is the instruction — "fix X" — and
    it is what the assignee reads next round.
    """
    if not confirmed:
        raise OfficeError(store.CONFIRM_CODE, "office task-review requires --confirm")
    verdict = store.check_enum(verdict, ("accept", "reject"), "verdict")
    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        task = _task(conn, task_id)
        if task["status"] != "review":
            raise OfficeError(
                BAD_STATE_CODE,
                f"task #{task_id} is {task['status']}; only a task in `review` "
                f"can be judged")
        if by_agent is not None and task["reviewer_id"] is not None \
                and int(by_agent) != int(task["reviewer_id"]):
            raise OfficeError(
                "OFFICE_REVIEW_WRONG_LEVEL",
                f"refused: task #{task_id} was assigned reviewer "
                f"#{task['reviewer_id']} and this seat is #{by_agent}. Only the "
                f"level above may accept the work (C31).")
        if verdict == "reject" and not str(note or "").strip():
            raise OfficeError(
                BAD_INPUT_CODE,
                "a rejection must say what to change; a rejection without an "
                "instruction is just a delay")

        if verdict == "accept":
            conn.execute("UPDATE office_task SET status='accepted', verdict=?,"
                         " review_note=?, closed_at=? WHERE id=?",
                         (verdict, str(note), store.now(), int(task_id)))
        else:
            attempts = int(task["attempts"]) + 1
            exhausted = attempts >= int(task["max_attempts"])
            conn.execute(
                "UPDATE office_task SET status=?, attempts=?, verdict=?,"
                " review_note=?, closed_at=? WHERE id=?",
                ("abandoned" if exhausted else "fixing", attempts, verdict,
                 str(note), store.now() if exhausted else None, int(task_id)))
        conn.commit()
    finally:
        conn.close()
    _trail(cfg, task_id, actor=f"seat:{by_agent or 'reviewer'}",
           kind="verdict", body=f"{verdict}: {note}",
           payload={"verdict": verdict, "note": note})
    return {"state": "OK", "id": int(task_id), "verdict": verdict,
            "status": "accepted" if verdict == "accept" else "fixing",
            "attempts": int(task["attempts"]) + (0 if verdict == "accept" else 1),
            "max_attempts": int(task["max_attempts"]),
            "note": str(note)}


def requeue(cfg: Any, task_id: int, *, reason: str = "",
            requested_by: str = "office:dispatch") -> Dict[str, Any]:
    """Return a task to the queue after a turn that never happened.

    A refused turn (no admission, no budget, no provider) leaves the task
    untouched in reality, so it must not be left looking claimed. Only states
    that mean "in progress" are requeued — an accepted or abandoned task is
    left exactly as the reviewer ruled.
    """
    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        task = _task(conn, task_id)
        if task["status"] not in ("running", "fixing"):
            return {"state": "OK", "id": int(task_id),
                    "status": task["status"], "requeued": False,
                    "reason": f"status {task['status']} is not in progress"}
        conn.execute("UPDATE office_task SET status='open', assignee_id=NULL,"
                     " reviewer_id=NULL WHERE id=?", (int(task_id),))
        conn.commit()
    finally:
        conn.close()
    _trail(cfg, task_id, actor="office:dispatch", kind="requeued",
           body=reason or "returned to the queue")
    return {"state": "OK", "id": int(task_id), "status": "open",
            "requeued": True}


def tasks(cfg: Any, *, status: Optional[str] = None,
          assignee_id: Optional[int] = None, limit: int = 50) -> Dict[str, Any]:
    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        sql = ("SELECT t.*, a.slug AS assignee_slug, r.slug AS reviewer_slug"
               " FROM office_task t"
               " LEFT JOIN office_agent a ON a.id=t.assignee_id"
               " LEFT JOIN office_agent r ON r.id=t.reviewer_id")
        where, args = [], []
        if status:
            where.append("t.status=?"); args.append(status)
        if assignee_id is not None:
            where.append("t.assignee_id=?"); args.append(int(assignee_id))
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY t.id LIMIT ?"
        args.append(int(limit))
        rows = [dict(r) for r in conn.execute(sql, args)]
    finally:
        conn.close()
    return {"state": "OK", "count": len(rows), "tasks": rows}


def trail(cfg: Any, task_id: int, *, limit: int = 100) -> Dict[str, Any]:
    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM office_task_event WHERE task_id=? ORDER BY id LIMIT ?",
            (int(task_id), int(limit)))]
    finally:
        conn.close()
    for row in rows:
        try:
            row["payload"] = json.loads(row["payload"])
        except Exception:                                  # noqa: BLE001
            pass
    return {"state": "OK", "count": len(rows), "events": rows}


# ------------------------------------------------------------ turn budgeting
def set_turn_cap(cfg: Any, *, agent_id: int, turns: int, day: Optional[str] = None,
                 confirmed: bool = False,
                 requested_by: str = "cli:owner") -> Dict[str, Any]:
    """The bound that means something on a flat-rate provider (M5)."""
    if not confirmed:
        raise OfficeError(store.CONFIRM_CODE, "office turn-cap requires --confirm")
    if int(turns) < 0:
        raise OfficeError(BAD_INPUT_CODE, "turns must be >= 0")
    key = day or today()
    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        _seat(conn, agent_id)
        conn.execute(
            "INSERT INTO office_turn_budget(day, agent_id, turns_cap, turns_used)"
            " VALUES(?,?,?,0) ON CONFLICT(day, agent_id) DO UPDATE SET"
            " turns_cap=excluded.turns_cap", (key, int(agent_id), int(turns)))
        conn.commit()
    finally:
        conn.close()
    return {"state": "OK", "day": key, "agent_id": int(agent_id),
            "turns_cap": int(turns)}


def turn_budget(cfg: Any, *, agent_id: int, day: Optional[str] = None
                ) -> Dict[str, Any]:
    key = day or today()
    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        row = conn.execute(
            "SELECT * FROM office_turn_budget WHERE day=? AND agent_id=?",
            (key, int(agent_id))).fetchone()
    finally:
        conn.close()
    if row is None:
        # no cap set is NOT the same as unlimited-and-unbounded: it means the
        # seat is governed by the dollar caps only, which on a flat-rate
        # provider means not at all. Say so rather than imply safety.
        return {"state": "OK", "day": key, "agent_id": int(agent_id),
                "turns_cap": None, "turns_used": 0, "unbounded": True}
    return {"state": "OK", "day": key, "agent_id": int(agent_id),
            "turns_cap": int(row["turns_cap"]),
            "turns_used": int(row["turns_used"]),
            "remaining": int(row["turns_cap"]) - int(row["turns_used"]),
            "unbounded": False}


def spend_turn(cfg: Any, *, agent_id: int, day: Optional[str] = None,
               ) -> Dict[str, Any]:
    """Consume one turn, refusing past the cap."""
    key = day or today()
    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        row = conn.execute(
            "SELECT * FROM office_turn_budget WHERE day=? AND agent_id=?",
            (key, int(agent_id))).fetchone()
        if row is None:
            return {"state": "OK", "allowed": True, "bounded": False,
                    "turns_used": 0, "turns_cap": None}
        used, cap = int(row["turns_used"]), int(row["turns_cap"])
        if used >= cap:
            return {"state": "OK", "allowed": False, "bounded": True,
                    "code": "OFFICE_TURN_BUDGET_EXHAUSTED",
                    "turns_used": used, "turns_cap": cap,
                    "detail": (f"seat #{agent_id} has used {used} of {cap} "
                               f"turns today")}
        conn.execute("UPDATE office_turn_budget SET turns_used=turns_used+1"
                     " WHERE day=? AND agent_id=?", (key, int(agent_id)))
        conn.commit()
        return {"state": "OK", "allowed": True, "bounded": True,
                "turns_used": used + 1, "turns_cap": cap}
    finally:
        conn.close()


def budgets(cfg: Any, *, day: Optional[str] = None) -> Dict[str, Any]:
    key = day or today()
    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        rows = [dict(r) for r in conn.execute(
            "SELECT b.*, a.slug FROM office_turn_budget b"
            " LEFT JOIN office_agent a ON a.id=b.agent_id WHERE b.day=?"
            " ORDER BY b.agent_id", (key,))]
    finally:
        conn.close()
    return {"state": "OK", "day": key, "count": len(rows), "budgets": rows}
