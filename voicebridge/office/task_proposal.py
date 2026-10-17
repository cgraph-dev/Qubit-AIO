"""Task proposals: how a seat finds work for itself without approving it.

The work loop (``work.py`` / ``dispatch.py``) can *execute* work, but every task
in it was created by the owner. Eleven seats and a working loop still make a
purely reactive org: it can do anything you ask and nothing you don't. M5 calls
the missing half *"the ratchet"* — the org proposes, the level above disposes.

This module is that half, and it adds no second queue (C3). A proposal is a
*record* in the same store, in a new table, in a state that the dispatcher
cannot see. Approval is the only thing that turns a proposal into a task, and it
turns it into an ordinary one — created through :func:`work.create`, so a
proposal that survives approval is indistinguishable from a task the owner
typed. Nothing downstream needs to know a proposal existed.

Three rules, each enforced here rather than hoped for:

* **A seat may never approve its own proposal.** The approver is *exactly one
  level above* the proposer (C31/D38) — the same rule
  :func:`escalation.resolve` enforces at ``escalation.py:328`` and
  :func:`org._walk_up` walks at ``org.py:57``. A seat reviewing its own idea is
  the one thing the chain of command exists to prevent.
* **A rejection carries a reason**, because a rejection with no instruction is
  just a delay — the rule ``work.review`` already sets at ``work.py:204``.
* **Pending proposals are capped per seat and per org**, so a seat that can
  queue unlimited ideas produces a backlog, not a business — the no-backlog
  rule ``docs/game-office/14-RND-FLOOR.md:84`` already states as "at most 12
  open items."

----

**Why proposals are not rows in ``office_task`` with ``status='proposed'``.**

They cannot be, without a destructive migration. ``office_task.status`` carries
a ``CHECK`` constraint (``store.py:326-328``) enumerating exactly
``('open','running','review','fixing','accepted','rejected','abandoned')``.
SQLite cannot widen a ``CHECK`` in place, and ``CREATE TABLE IF NOT EXISTS`` is
a no-op on a table that already exists, so adding ``'proposed'`` would require
bumping ``store.SCHEMA`` and rebuilding ``office_task`` — dropping and
recreating a table that holds live work. That is a real cost paid on every
deploy, to express a state the dispatcher must not see anyway.

The minimal honest alternative is a separate table in the **same store**, which
is additive, non-destructive, and satisfies the same requirement structurally:
the dispatcher's ``SELECT ... WHERE status IN ('open','fixing','running')``
(``dispatch.py:159-160``) cannot reach a proposal, because a proposal is not a
row in ``office_task`` at all. If a future milestone does widen the CHECK, this
module's surface (``propose`` / ``proposals`` / ``approve`` / ``reject``) does
not change — only the storage underneath it.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from . import store
from . import work as W
from .errors import (BAD_INPUT_CODE, BAD_STATE_CODE, NOT_FOUND_CODE,
                     OfficeError)

#: Pending-proposal caps. Unbounded proposal intake is an org that manufactures
#: backlog instead of work, so the cap is part of the design, not a nicety.
#:
#: * ``per_seat_pending`` — one seat may hold at most this many proposals in
#:   ``proposed``. A seat with a fistful of outstanding ideas is not committing
#:   to any of them; the cap pushes it to land or drop what it already floated.
#: * ``org_pending`` — the whole org may hold at most this many. This is the
#:   number ``docs/game-office/14-RND-FLOOR.md:84`` already fixes for owner
#:   intake ("the floor holds **at most 12 open items**"), applied to the org's
#:   *own* intake: one no-backlog rule for the whole building, not two.
PROPOSAL_LIMITS = {
    "per_seat_pending": 3,
    "org_pending": 12,
}

#: Stable refusal codes the caller can branch on.
SELF_APPROVAL_CODE = "OFFICE_PROPOSAL_SELF_APPROVAL"
WRONG_LEVEL_CODE = "OFFICE_PROPOSAL_WRONG_LEVEL"
CAP_CODE = "OFFICE_PROPOSAL_CAP"
NEEDS_REASON_CODE = "OFFICE_PROPOSAL_NEEDS_REASON"
#: The proposer cannot be a terminated seat, and neither can the approver.
BAD_STATE = BAD_STATE_CODE

STATES = ("proposed", "approved", "rejected")

# The proposal table. Additive and idempotent: created here, never a migration
# of an existing table. It lives in the same ``office.db`` as everything else
# (C3 — one store), and it is deliberately the *only* new thing this module
# touches.
_PROPOSAL_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS office_task_proposal(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL NOT NULL,
  proposer_id INTEGER NOT NULL,
  title TEXT NOT NULL,
  brief TEXT NOT NULL DEFAULT '',
  exit_check TEXT NOT NULL DEFAULT '',
  rationale TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'proposed'
    CHECK(status IN ('proposed','approved','rejected')),
  approver_id INTEGER,
  decided_at REAL,
  reason TEXT NOT NULL DEFAULT '',
  task_id INTEGER);
"""


def ensure(conn) -> None:
    """Install the proposal table. Idempotent; additive-only."""
    conn.executescript(_PROPOSAL_TABLE_SQL)


def _seat(conn, agent_id: int) -> Dict[str, Any]:
    row = conn.execute("SELECT * FROM office_agent WHERE id=?",
                       (int(agent_id),)).fetchone()
    if row is None:
        raise OfficeError(NOT_FOUND_CODE, f"no seat #{agent_id}")
    return dict(row)


def _proposal(conn, proposal_id: int) -> Dict[str, Any]:
    ensure(conn)
    row = conn.execute("SELECT * FROM office_task_proposal WHERE id=?",
                       (int(proposal_id),)).fetchone()
    if row is None:
        raise OfficeError(NOT_FOUND_CODE, f"no proposal #{proposal_id}")
    return dict(row)


def _pending_count(conn, *, proposer_id: Optional[int] = None) -> int:
    ensure(conn)
    if proposer_id is None:
        row = conn.execute(
            "SELECT COUNT(*) n FROM office_task_proposal WHERE status='proposed'"
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT COUNT(*) n FROM office_task_proposal"
            " WHERE status='proposed' AND proposer_id=?",
            (int(proposer_id),)).fetchone()
    return int(row["n"])


def approve_level(cfg: Any, proposer_id: int) -> Dict[str, Any]:
    """Who, if any seat, may approve this proposer's proposal.

    The approver is **exactly one level above** the proposer, read from the
    live chain rather than assumed from ``seat_type`` (C31: "the reviewer's
    level must be exactly one above the reviewed seat's"). This is the same
    rule the escalation path enforces — :func:`escalation.resolve` requires
    ``by_agent == supervisor_id`` (``escalation.py:328``), and
    :func:`org._walk_up` (``org.py:57``) is what defines "one level above".

    A **director** proposes to the **CEO**, its own supervisor. It does *not*
    jump to the owner: that would skip the CEO and break the one-level rule the
    whole chain is built on, and 14-RND-FLOOR.md's "may not hold a gate" rule
    is about the seat's incentives, not about who signs the idea. A **CEO**
    proposal has no seat above it — the owner is not an agent (D2) — so
    ``approver_id`` is ``None`` and only the owner may approve it; a seat
    attempting it is refused as the wrong level.
    """
    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        seat = _seat(conn, proposer_id)
        if seat["status"] == "terminated":
            raise OfficeError(BAD_STATE_CODE,
                              f"seat {seat['slug']!r} is terminated")
        supervisor = seat["supervisor_id"]
        return {
            "state": "OK",
            "proposer_slug": seat["slug"],
            "seat_type": seat["seat_type"],
            # None means "the owner", the one approver outside the org (D2).
            "approver_id": None if supervisor is None else int(supervisor),
            "approver": "owner" if supervisor is None else f"seat:{supervisor}",
        }
    finally:
        conn.close()


def propose(cfg: Any, proposer_id: int, title: str, brief: str = "",
            exit_check: str = "", rationale: str = "",
            confirmed: bool = True,
            requested_by: str = "office:agent") -> Dict[str, Any]:
    """A seat floats a unit of work. It is **not** dispatchable.

    A proposal states *why* (``rationale``) as well as *what*, because the
    approver's whole job is to judge the why — a proposal with no reasoning is
    a wish, and the level above cannot check a wish. The exit condition is
    accepted here but is **required at approval**, not creation: a seat may
    float an idea before it knows how to prove completion, but nothing becomes
    dispatchable work without a machine-checkable exit condition (C17).

    Refused when the seat is over its pending cap, with the cap named.
    """
    if not confirmed:
        raise OfficeError(store.CONFIRM_CODE, "office propose requires --confirm")
    clean = str(title or "").strip()
    if not clean:
        raise OfficeError(BAD_INPUT_CODE, "a proposal needs a title")
    why = str(rationale or "").strip()
    if not why:
        raise OfficeError(
            BAD_INPUT_CODE,
            "a proposal needs a rationale: the level above cannot approve a "
            "wish. Say what the work is for and why now.")

    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        ensure(conn)
        seat = _seat(conn, proposer_id)
        if seat["status"] == "terminated":
            raise OfficeError(BAD_STATE_CODE,
                              f"seat {seat['slug']!r} is terminated")
        seat_pending = _pending_count(conn, proposer_id=int(proposer_id))
        if seat_pending >= int(PROPOSAL_LIMITS["per_seat_pending"]):
            raise OfficeError(
                CAP_CODE,
                f"seat {seat['slug']!r} already holds {seat_pending} pending "
                f"proposals, at its own cap of "
                f"{PROPOSAL_LIMITS['per_seat_pending']}. Land or withdraw one "
                f"before floating another: a seat with a fistful of open ideas "
                f"has committed to none of them.")
        org_pending = _pending_count(conn)
        if org_pending >= int(PROPOSAL_LIMITS["org_pending"]):
            raise OfficeError(
                CAP_CODE,
                f"the org already holds {org_pending} pending proposals, at "
                f"its shared cap of {PROPOSAL_LIMITS['org_pending']}. This is "
                f"the no-backlog rule of 14-RND-FLOOR.md, applied to the org's "
                f"own intake.")
        cur = conn.execute(
            "INSERT INTO office_task_proposal(ts, proposer_id, title, brief,"
            " exit_check, rationale, status) VALUES(?,?,?,?,?,?,'proposed')",
            (store.now(), int(proposer_id), clean[:200], str(brief),
             str(exit_check or "").strip(), why))
        conn.commit()
        pid = int(cur.lastrowid or 0)
    finally:
        conn.close()
    store.audit("proposal.create", requested_by,
                {"id": pid, "proposer_id": proposer_id, "title": clean[:80]})
    return {"state": "OK", "id": pid, "status": "proposed",
            "proposer_id": int(proposer_id), "title": clean,
            "dispatchable": False,
            "meaning": ("a proposal; it is not in the queue and cannot be "
                        "claimed until the level above approves it")}


def proposals(cfg: Any, *, status: Optional[str] = "proposed",
              proposer_id: Optional[int] = None) -> Dict[str, Any]:
    """Pending proposals (by default), newest last."""
    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        ensure(conn)
        sql = ("SELECT p.*, a.slug AS proposer_slug,"
               " r.slug AS approver_slug FROM office_task_proposal p"
               " LEFT JOIN office_agent a ON a.id=p.proposer_id"
               " LEFT JOIN office_agent r ON r.id=p.approver_id")
        where, args = [], []
        if status:
            where.append("p.status=?"); args.append(status)
        if proposer_id is not None:
            where.append("p.proposer_id=?"); args.append(int(proposer_id))
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY p.id"
        rows = [dict(r) for r in conn.execute(sql, args)]
    finally:
        conn.close()
    return {"state": "OK", "count": len(rows), "proposals": rows}


def approve(cfg: Any, proposal_id: int, *, approver_id: Optional[int],
            confirmed: bool = True,
            requested_by: str = "cli:owner") -> Dict[str, Any]:
    """Move a proposal into the dispatchable queue as an ordinary task.

    The approver must be **exactly one level above** the proposer. A seat may
    never approve its own proposal — refused with ``OFFICE_PROPOSAL_SELF_APPROVAL``
    — and a seat that is not the proposer's supervisor is refused with
    ``OFFICE_PROPOSAL_WRONG_LEVEL``. The CEO's proposal has no seat above it, so
    ``approver_id`` must be ``None`` (the owner) and any seat is refused.

    Approval delegates creation to :func:`work.create`, so the resulting task is
    indistinguishable from one the owner typed: it appears in
    :func:`work.tasks`, it carries the same C17 exit-check requirement, and the
    dispatcher picks it up with no knowledge that a proposal existed.
    """
    if not confirmed:
        raise OfficeError(store.CONFIRM_CODE, "office approve requires --confirm")
    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        ensure(conn)
        prop = _proposal(conn, proposal_id)
        if prop["status"] != "proposed":
            raise OfficeError(
                BAD_STATE_CODE,
                f"proposal #{proposal_id} is {prop['status']}; only a pending "
                f"proposal can be approved")
        proposer = _seat(conn, prop["proposer_id"])
        proposer_id = int(proposer["id"])
        supervisor = proposer["supervisor_id"]

        # ---- self-approval: the rule this whole module exists to hold --------
        if approver_id is not None and int(approver_id) == proposer_id:
            raise OfficeError(
                SELF_APPROVAL_CODE,
                f"refused: seat {proposer['slug']!r} proposed #{proposal_id} "
                f"and may not approve it. A seat that approves its own work has "
                f"no chain above it (C31).")

        # ---- the level above, and only the level above -----------------------
        if supervisor is None:
            # the CEO: the owner is the only approver, and is not a seat (D2)
            if approver_id is not None:
                raise OfficeError(
                    WRONG_LEVEL_CODE,
                    f"refused: proposal #{proposal_id} comes from "
                    f"{proposer['slug']!r}, the CEO. The seat above the CEO is "
                    f"the owner, who is not an agent (D2); no seat may approve "
                    f"this — pass no approver_id.")
        elif approver_id is None:
            raise OfficeError(
                WRONG_LEVEL_CODE,
                f"refused: proposal #{proposal_id} comes from "
                f"{proposer['slug']!r} and must be approved by its supervisor "
                f"#{supervisor} (C31), not by the owner directly.")
        elif int(approver_id) != int(supervisor):
            approver = _seat(conn, int(approver_id))
            _seat(conn, int(supervisor))
            raise OfficeError(
                WRONG_LEVEL_CODE,
                f"refused: proposal #{proposal_id} was raised by "
                f"{proposer['slug']!r}, whose level above is seat #{supervisor}; "
                f"this seat is {approver['slug']!r} (#{approver_id}). Only the "
                f"level immediately above may approve (C31).")

        exit_check = str(prop["exit_check"] or "").strip()
    finally:
        conn.close()

    # ---- the C17 gate applies at approval, not just creation -----------------
    # `work.create` refuses an empty exit_check with OFFICE_TASK_NEEDS_EXIT_CHECK.
    # Re-raising here keeps the refusal named for the proposal surface and stops
    # a taskless "approve" from half-committing.
    if not exit_check:
        raise OfficeError(
            "OFFICE_TASK_NEEDS_EXIT_CHECK",
            f"refused: proposal #{proposal_id} has no exit_check, so it cannot "
            f"become work. 'Done' is never the model's own verdict (C17); a "
            f"proposal whose completion only its proposer can judge would "
            f"always succeed.")

    # Materialize an ordinary task. This is the only path from a proposal to the
    # queue, and it deliberately goes through `work.create` so nothing
    # downstream can tell the two apart.
    task = W.create(
        cfg, prop["title"], brief=prop["brief"], exit_check=exit_check,
        created_by=None, confirmed=True, requested_by=requested_by)
    task_id = int(task["id"])

    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        ensure(conn)
        conn.execute(
            "UPDATE office_task_proposal SET status='approved', approver_id=?,"
            " decided_at=?, task_id=? WHERE id=?",
            (None if approver_id is None else int(approver_id),
             store.now(), task_id, int(proposal_id)))
        conn.commit()
    finally:
        conn.close()
    # The proposal carries its own trail onto the task it became, so the record
    # of *who thought of this and why* survives into the work.
    W._trail(cfg, task_id, actor=f"seat:{prop['proposer_id']}",
             kind="proposed",
             body=prop["rationale"],
             payload={"proposal_id": int(proposal_id),
                      "proposer_id": int(prop["proposer_id"]),
                      "approved_by": approver_id})
    store.audit("proposal.approve", requested_by,
                {"id": int(proposal_id), "task_id": task_id,
                 "approver_id": approver_id})
    return {"state": "OK", "id": task_id, "task_id": task_id,
            "title": prop["title"], "status": "open",
            "proposal_id": int(proposal_id), "proposal_status": "approved",
            "approved_by": approver_id,
            "dispatchable": True}


def reject(cfg: Any, proposal_id: int, *, approver_id: Optional[int],
           reason: str, confirmed: bool = True,
           requested_by: str = "cli:owner") -> Dict[str, Any]:
    """Send a proposal back with a reason. **A reason is mandatory.**

    The same rule ``work.review`` sets at ``work.py:204``: a rejection without
    an instruction is just a delay. The proposal is not deleted — it stays as
    ``rejected`` with the reason attached, so the proposer reads *why* and the
    trail is append-only in spirit (C1). Only the level above may reject, so the
    self/wrong-level checks are identical to :func:`approve`.
    """
    if not confirmed:
        raise OfficeError(store.CONFIRM_CODE, "office reject requires --confirm")
    text = str(reason or "").strip()
    if not text:
        raise OfficeError(
            NEEDS_REASON_CODE,
            "a rejection must say why; a rejection without a reason is just a "
            "delay (the same rule as work.review, C31).")

    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        ensure(conn)
        prop = _proposal(conn, proposal_id)
        if prop["status"] != "proposed":
            raise OfficeError(
                BAD_STATE_CODE,
                f"proposal #{proposal_id} is {prop['status']}; only a pending "
                f"proposal can be rejected")
        proposer = _seat(conn, prop["proposer_id"])
        proposer_id = int(proposer["id"])
        supervisor = proposer["supervisor_id"]

        if approver_id is not None and int(approver_id) == proposer_id:
            raise OfficeError(
                SELF_APPROVAL_CODE,
                f"refused: seat {proposer['slug']!r} proposed #{proposal_id} "
                f"and may not decide it — including rejecting it. The level "
                f"above decides (C31).")
        if supervisor is None:
            if approver_id is not None:
                raise OfficeError(
                    WRONG_LEVEL_CODE,
                    f"refused: proposal #{proposal_id} comes from the CEO; only "
                    f"the owner, who is not an agent (D2), may decide it.")
        elif approver_id is None or int(approver_id) != int(supervisor):
            approver = ("owner" if approver_id is None
                        else _seat(conn, int(approver_id))["slug"])
            raise OfficeError(
                WRONG_LEVEL_CODE,
                f"refused: proposal #{proposal_id} was raised by "
                f"{proposer['slug']!r}, whose level above is seat #{supervisor}; "
                f"the decision came from {approver!r}. Only the level "
                f"immediately above may decide (C31).")

        conn.execute(
            "UPDATE office_task_proposal SET status='rejected', approver_id=?,"
            " decided_at=?, reason=? WHERE id=?",
            (None if approver_id is None else int(approver_id),
             store.now(), text[:1000], int(proposal_id)))
        conn.commit()
    finally:
        conn.close()
    store.audit("proposal.reject", requested_by,
                {"id": int(proposal_id), "approver_id": approver_id})
    return {"state": "OK", "id": int(proposal_id), "status": "rejected",
            "proposal_status": "rejected", "reason": text,
            "decided_by": approver_id, "dispatchable": False}


def pending(cfg: Any, proposer_id: Optional[int] = None) -> Dict[str, Any]:
    """Count pending proposals, against the caps, for a status surface."""
    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        ensure(conn)
        seat = None if proposer_id is None else _pending_count(
            conn, proposer_id=int(proposer_id))
        org = _pending_count(conn)
    finally:
        conn.close()
    return {"state": "OK", "org_pending": org,
            "org_cap": int(PROPOSAL_LIMITS["org_pending"]),
            "seat_pending": seat,
            "seat_cap": int(PROPOSAL_LIMITS["per_seat_pending"]),
            "limits": dict(PROPOSAL_LIMITS)}
