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
          handoff: bool = False, reason: str = "",
          requested_by: str = "office:dispatch") -> Dict[str, Any]:
    """Assign a task to a seat, enforcing the chain.

    **No work changes hands without a record.** `running` is claimable on
    purpose: a task whose turn was refused or whose check failed stays in
    progress and must be picked up again (27-WORK-LOOP.md). But "picked up
    again" is a *recovery by the seat that already owns it*, not a licence for
    a peer to take it. `pick_worker` returns the current `assignee_id` for a
    `running`/`fixing` task for exactly this reason (dispatch.py).

    Before the ownership check existed, a second seat could claim a peer's
    in-flight task outright: the trail recorded two clean `claimed` events and
    no handoff, the assignee flipped silently, and the first seat's turn became
    untracked work -- the trail could no longer tell a reader that the first
    seat's work had been discarded. A double-`claimed` is unfalsifiable, so the
    fix is to make the transfer *say so*.

    So a task that already has an assignee in progress (`running`/`fixing`) may
    be re-claimed by:

    * **the current assignee** -- the ordinary recovery path, allowed silently
      (nothing changed hands); or
    * **any other seat, only as an explicit handoff** -- pass ``handoff=True``
      and a ``reason``. The transfer updates the assignee and appends a
      ``reassigned`` trail event naming BOTH seats, so the previous owner's
      work is on the record rather than erased. A cross-seat claim without
      ``handoff`` is a typed refusal (``OFFICE_TASK_NOT_YOURS``).

    An `open` task has no owner, so anyone entitled to it still claims it
    normally. The reviewer is always recomputed from the (new) assignee's
    supervisor, so a handoff cannot leave a stale reviewer one level up (C31).
    """
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

        # --- ownership: in-progress work does not change hands unrecorded ---
        prev_assignee = task["assignee_id"]
        needs_handoff = (prev_assignee is not None
                         and int(prev_assignee) != int(agent_id))
        if needs_handoff:
            prev = _seat(conn, prev_assignee)["slug"]
            if not handoff:
                raise OfficeError(
                    "OFFICE_TASK_NOT_YOURS",
                    f"refused: task #{task_id} is {task['status']} and already "
                    f"assigned to {prev!r} (#{prev_assignee}). Re-claiming "
                    f"another seat's in-progress work is a handoff, not a "
                    f"claim -- pass handoff with a reason so the transfer "
                    f"names both seats, or have the current assignee release "
                    f"it. A silent take would leave {prev!r}'s turn untracked.")
            if not str(reason or "").strip():
                raise OfficeError(
                    BAD_INPUT_CODE,
                    f"a handoff must say why task #{task_id} is moving from "
                    f"{prev!r} to {seat['slug']!r}; a handoff without a reason "
                    f"is just a silent claim")

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
        prev_slug = (_seat(conn, prev_assignee)["slug"]
                     if prev_assignee is not None else None)
    finally:
        conn.close()
    if needs_handoff:
        _trail(cfg, task_id, actor=f"seat:{agent_id}", kind="reassigned",
               body=(f"handed off from seat:{prev_assignee} ({prev_slug}) to "
                     f"seat:{agent_id} ({seat['slug']}): {str(reason).strip()}"),
               payload={"from_agent_id": int(prev_assignee),
                        "to_agent_id": int(agent_id),
                        "reason": str(reason).strip(),
                        "reviewer_id": int(reviewer)})
    _trail(cfg, task_id, actor=f"seat:{agent_id}", kind="claimed",
           body=f"assigned to {seat['slug']}; reviewer {reviewer}")
    return {"state": "OK", "id": int(task_id), "assignee_id": int(agent_id),
            "reviewer_id": int(reviewer), "status": "running",
            "handoff": bool(needs_handoff),
            "previous_assignee_id": int(prev_assignee) if needs_handoff else None}


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

    ``by_agent=None`` means **the owner**, never "anyone". The owner is not a
    seat (D2) and sits above the top seat, so an omitted identity accepts
    exactly one thing: work whose assignee has no supervisor. Everywhere else
    the owner is refused, and so is any seat that is not the assignee's
    immediate supervisor — including the assignee itself. An approver flag that
    defaults to ``None`` reads as "anyone may" unless the callee interprets it,
    and that is how a worker ends up accepting its own work (C31). Mirrors
    :func:`voicebridge.office.task_proposal.approve`, which derives the required
    approver from the proposer's supervisor.
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
        # ---- C31: exactly one level above, and only that level -------------
        # The required approver is derived from the ASSIGNEE's supervisor, the
        # same way `task_proposal.approve` derives it from the proposer's
        # (task_proposal.py:316-339) — `reviewer_id` is a convenience copy of
        # that, not the authority.
        #
        # `by_agent=None` means THE OWNER, never "anyone". The owner is not a
        # seat (D2) and sits above the CEO, so an omitted identity approves
        # exactly one case: work whose assignee has no supervisor (the top
        # seat). Everywhere else it is refused, and so is the owner "helpfully"
        # reached for a task that a seat should have judged. An approver flag
        # that defaults to None reads as "anyone may" unless the callee
        # interprets it, and that is precisely how a worker ends up accepting
        # its own work.
        assignee = _seat(conn, int(task["assignee_id"])) \
            if task["assignee_id"] is not None else None
        supervisor = assignee["supervisor_id"] if assignee is not None else None
        assignee_slug = assignee["slug"] if assignee is not None else None
        if by_agent is not None and assignee is not None \
                and int(by_agent) == int(assignee["id"]):
            raise OfficeError(
                "OFFICE_REVIEW_WRONG_LEVEL",
                f"refused: seat {assignee_slug!r} did task #{task_id} and may "
                f"not accept it. A seat that signs off its own work has no "
                f"chain above it (C31).")
        if supervisor is None:
            # the top seat: the owner is the only approver, and is not a seat
            if by_agent is not None:
                raise OfficeError(
                    "OFFICE_REVIEW_WRONG_LEVEL",
                    f"refused: task #{task_id} was done by "
                    f"{assignee_slug or 'an unassigned seat'}, the top seat. "
                    f"The approver above it is the owner, who is not an agent "
                    f"(D2); no seat may accept this work — omit --by.")
        elif by_agent is None:
            raise OfficeError(
                "OFFICE_REVIEW_WRONG_LEVEL",
                f"refused: task #{task_id} was done by {assignee_slug!r} and "
                f"must be accepted by its level above, seat #{supervisor} "
                f"(C31) — not by the owner directly. Omitting --by means the "
                f"owner, not anyone.")
        elif int(by_agent) != int(supervisor):
            raise OfficeError(
                "OFFICE_REVIEW_WRONG_LEVEL",
                f"refused: task #{task_id} was done by {assignee_slug!r}, "
                f"whose level above is seat #{supervisor}; this approver is "
                f"seat #{by_agent}. Only the level immediately above may "
                f"accept the work (C31).")
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


def abandon(cfg: Any, task_id: int, *, reason: str,
            by_agent: Optional[int] = None, confirmed: bool = False,
            requested_by: str = "cli:office") -> Dict[str, Any]:
    """Close a task that will never be worked, and say why.

    ``abandoned`` has always been a terminal state in the schema, but the only
    way to reach it was a *rejection* that exhausted ``max_attempts`` — which
    costs a review turn and needs a reviewer. So a queue full of rows that no
    turn will ever claim had no honest exit: a tick stub filed and then refused,
    a proposal approved after its reason expired, a task whose exit check can no
    longer be met. Leaving them ``open`` is the worst of the three outcomes,
    because ``open`` means *work in flight*: the board reports throughput it
    does not have, and ``dispatch`` keeps handing the row to a seat that spends
    a turn discovering there is nothing to do.

    The verb is deliberately narrow:

    * a **reason is required** — the row is evidence, and an abandonment without
      one is indistinguishable from a deletion;
    * **work that happened is refused**: a task carrying an artifact, or whose
      run booked a non-zero amount, is not unworkable and must go through
      ``review``;
    * an already-closed task is **left alone**, so a real verdict is never
      rewritten and a second call is a no-op.
    """
    if not confirmed:
        raise OfficeError(store.CONFIRM_CODE,
                          "office task-abandon requires --confirm")
    clean = str(reason or "").strip()
    if not clean:
        raise OfficeError(
            BAD_INPUT_CODE,
            "an abandonment must say why: the row is evidence, and a closure "
            "without a reason is indistinguishable from a deletion")
    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        task = _task(conn, task_id)
        if task["status"] in CLOSED_STATES:
            return {"state": "OK", "id": int(task_id), "status": task["status"],
                    "abandoned": False,
                    "reason": f"task #{task_id} is already {task['status']}"}
        artifact = str(task.get("artifact") or "").strip()
        run_id = str(task.get("run_id") or "").strip()
        booked = None
        if run_id:
            got = conn.execute(
                "SELECT amount_cents FROM office_ledger WHERE run_id=? LIMIT 1",
                (run_id,)).fetchone()
            booked = int(got["amount_cents"] or 0) if got is not None else None
        if artifact or booked:
            produced = artifact or f"{booked} cents booked on {run_id}"
            raise OfficeError(
                BAD_STATE_CODE,
                f"refused: task #{task_id} produced work ({produced}). It is "
                f"not unworkable, so it goes through review — abandoning it "
                f"would erase a result the org paid for")
        conn.execute("UPDATE office_task SET status='abandoned', verdict=?,"
                     " review_note=?, closed_at=? WHERE id=?",
                     ("abandon", clean, store.now(), int(task_id)))
        conn.commit()
    finally:
        conn.close()
    _trail(cfg, task_id, actor=f"seat:{by_agent or 'owner'}", kind="abandoned",
           body=clean, payload={"reason": clean, "by_agent": by_agent})
    store.audit("task.abandon", requested_by,
                {"id": int(task_id), "reason": clean[:160]})
    return {"state": "OK", "id": int(task_id), "status": "abandoned",
            "abandoned": True, "reason": clean}


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
