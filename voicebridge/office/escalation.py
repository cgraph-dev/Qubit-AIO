"""Escalation: how a seat asks the level above for something.

There is **one journal** in this product — the shared approval journal at
``<state_dir>/approvals.jsonl`` that the dashboard, the voice bridge and the CLI
already settle. This module does not add a second queue. An escalation mints a
record on *that* journal and keeps its `confirmation_id`, so an escalation from
an office seat appears on the same surface as any other confirmation, is
approved the same way, and settles through the same code path.

What an escalation adds on top of the journal is **direction**: the journal
knows what is waiting, and this knows *who raised it* and *who must decide*.
That is what makes the daily tick escalate to the seat's actual manager
(`supervisor_id`) instead of dying on a notification nobody owns (D34).

An escalation is never auto-approved. A seat that could approve its own
escalation would have no chain above it.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import store
from .errors import BAD_INPUT_CODE, BAD_STATE_CODE, NOT_FOUND_CODE, OfficeError

KINDS = ("envelope", "runway", "blocked", "deadline", "raise", "owner")
OPEN = "open"
RESOLVED = "resolved"
ABANDONED = "abandoned"
CAPABILITY_PREFIX = "office.escalation"


def journal_for(cfg: Any, *, ttl_s: float = 7 * 86400.0):
    """The *shared* journal. Same path the dashboard and CLI already use."""
    from voicebridge.approvals import ApprovalJournal

    return ApprovalJournal(Path(cfg.state_dir()) / "approvals.jsonl",
                           default_ttl_s=ttl_s)


def _seat(conn, agent_id: int):
    row = conn.execute("SELECT * FROM office_agent WHERE id=?",
                       (agent_id,)).fetchone()
    if row is None:
        raise OfficeError(NOT_FOUND_CODE, f"no seat #{agent_id}")
    return row


def raise_to_supervisor(cfg: Any, *, agent_id: int, kind: str, summary: str,
                        payload: Optional[Dict[str, Any]] = None,
                        ttl_s: float = 7 * 86400.0,
                        requested_by: str = "office:tick") -> Dict[str, Any]:
    """Raise exactly one item to the seat's own supervisor.

    Returns the escalation **and** its `confirmation_id` — if the journal could
    not be written, the escalation is not created either, because an escalation
    nobody can settle is worse than none: it looks like someone owns it.
    """
    kind = store.check_enum(kind, KINDS, "escalation kind")
    text = str(summary or "").strip()
    if not text:
        raise OfficeError(BAD_INPUT_CODE, "an escalation needs a summary")
    body = json.dumps(payload or {}, sort_keys=True, default=str)

    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        seat = _seat(conn, agent_id)
        if seat["status"] == "terminated":
            raise OfficeError(BAD_STATE_CODE,
                              f"seat {seat['slug']!r} is terminated")
        if seat["seat_type"] == "ceo":
            raise OfficeError(
                BAD_STATE_CODE,
                "the CEO escalates to the owner, who is not an agent (D2); "
                "file a report instead (D24)")
        supervisor = seat["supervisor_id"]
        if supervisor is None:
            raise OfficeError(
                BAD_STATE_CODE,
                f"seat {seat['slug']!r} has no supervisor to raise to (C30); "
                f"the chain of command is incomplete for this seat")
    finally:
        conn.close()

    # Write the journal FIRST. If this raises, nothing else happened.
    journal = journal_for(cfg, ttl_s=ttl_s)
    approval = journal.create(
        CAPABILITY_PREFIX,
        f"[office/{kind}] {seat['slug']}: {text}",
        ttl_s=ttl_s, agent_id=int(agent_id), agent_slug=seat["slug"],
        supervisor_id=int(supervisor), escalation_kind=kind)

    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        cur = conn.execute(
            "INSERT INTO office_escalation(ts, agent_id, supervisor_id, kind,"
            " summary, payload, confirmation_id, status)"
            " VALUES(?,?,?,?,?,?,?,'open')",
            (store.now(), int(agent_id), int(supervisor), kind, text, body,
             approval.id))
        conn.commit()
        eid = int(cur.lastrowid or 0)
    finally:
        conn.close()

    _announce_raised(cfg, requested_by,
                     {"id": eid, "agent_id": agent_id, "kind": kind,
                      "confirmation_id": approval.id}, eid, kind)
    return {"state": "OK", "id": eid, "agent_id": int(agent_id),
            "supervisor_id": int(supervisor), "kind": kind,
            "confirmation_id": approval.id, "summary": text,
            "expires_at": approval.expires_at}


CAPABILITY_QUESTION = "office.question"


def ask_owner(cfg: Any, *, agent_id: int, question: str,
              options: Optional[List[str]] = None, default_action: str = "",
              blocking: bool = False, ttl_s: float = 30 * 86400.0,
              requested_by: str = "office:agent") -> Dict[str, Any]:
    """Ask the owner. **An open question is not a stop.**

    The org does not need the owner to decide before it can work — it needs the
    owner to decide before it *spends real money on that specific thing*. So a
    question is a record with a stated default: the seat says what it will do
    while it waits, does that, and the answer arrives whenever the owner gets to
    it. `blocking=True` is for the rare case where no default is honest, and the
    seat stops until it is answered — the caller must say which.

    This is the difference between "the org is blocked on three questions" and
    "the org is running and has three questions outstanding". The second one is
    a company.
    """
    text = str(question or "").strip()
    if not text:
        raise OfficeError(BAD_INPUT_CODE, "a question needs to say something")
    if not blocking and not str(default_action or "").strip():
        raise OfficeError(
            BAD_INPUT_CODE,
            "a non-blocking question must state what the seat will do while it "
            "waits (default_action). Asking and then sitting still is a stop "
            "with extra steps.")

    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        seat = _seat(conn, agent_id)
        if seat["status"] == "terminated":
            raise OfficeError(BAD_STATE_CODE,
                              f"seat {seat['slug']!r} is terminated")
    finally:
        conn.close()

    journal = journal_for(cfg, ttl_s=ttl_s)
    approval = journal.create(
        CAPABILITY_QUESTION, f"[owner] {seat['slug']}: {text}", ttl_s=ttl_s,
        agent_id=int(agent_id), agent_slug=seat["slug"], blocking=bool(blocking),
        options=list(options or []), default_action=str(default_action),
        addressed_to="owner")

    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        cur = conn.execute(
            "INSERT INTO office_escalation(ts, agent_id, supervisor_id, kind,"
            " summary, payload, confirmation_id, status)"
            " VALUES(?,?,NULL,'owner',?,?,?,'open')",
            (store.now(), int(agent_id), text,
             json.dumps({"options": list(options or []),
                         "default_action": str(default_action),
                         "blocking": bool(blocking),
                         "addressed_to": "owner"},
                        sort_keys=True),
             approval.id))
        conn.commit()
        qid = int(cur.lastrowid or 0)
    finally:
        conn.close()
    store.audit("question.ask", requested_by,
                {"id": qid, "agent_id": agent_id, "blocking": bool(blocking)})
    return {"state": "OK", "id": qid, "agent_id": int(agent_id),
            "question": text, "options": list(options or []),
            "default_action": str(default_action), "blocking": bool(blocking),
            "confirmation_id": approval.id,
            "addressed_to": "owner",
            "meaning": ("the seat stops until this is answered"
                        if blocking else
                        "the seat is working on the stated default meanwhile")}


def questions(cfg: Any, *, status: Optional[str] = "open") -> Dict[str, Any]:
    """Everything the org is currently asking its owner."""
    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        sql = ("SELECT e.*, a.slug AS agent_slug FROM office_escalation e"
               " LEFT JOIN office_agent a ON a.id=e.agent_id WHERE e.kind='owner'")
        args: List[Any] = []
        if status:
            sql += " AND e.status=?"
            args.append(status)
        sql += " ORDER BY e.id"
        rows = []
        for r in conn.execute(sql, args):
            row = dict(r)
            try:
                row.update(json.loads(row.get("payload") or "{}"))
            except Exception:                              # noqa: BLE001
                pass
            rows.append(row)
    finally:
        conn.close()
    return {"state": "OK", "count": len(rows), "questions": rows}


def answer_question(cfg: Any, question_id: int, *, answer: str,
                    answered_by: str = "owner", confirmed: bool = False,
                    requested_by: str = "cli:owner") -> Dict[str, Any]:
    """The owner's answer. Closes the question and stores what was decided.

    The answer is appended to the question's own record, so the seat that asked
    can read the decision and the reason without a second channel.
    """
    if not confirmed:
        raise OfficeError(store.CONFIRM_CODE,
                          "office answer requires --confirm")
    text = str(answer or "").strip()
    if not text:
        raise OfficeError(BAD_INPUT_CODE, "an answer needs to say something")
    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        row = conn.execute("SELECT * FROM office_escalation WHERE id=?"
                           " AND kind='owner'", (int(question_id),)).fetchone()
        if row is None:
            raise OfficeError(NOT_FOUND_CODE, f"no question #{question_id}")
        if row["status"] != OPEN:
            raise OfficeError(BAD_STATE_CODE,
                              f"question #{question_id} is already"
                              f" {row['status']}")
        payload = {}
        try:
            payload = json.loads(row["payload"] or "{}")
        except Exception:                                  # noqa: BLE001
            pass
        payload["answer"] = text[:1000]
        payload["answered_by"] = str(answered_by)
        conn.execute("UPDATE office_escalation SET status='resolved',"
                     " payload=? WHERE id=?",
                     (json.dumps(payload, sort_keys=True), int(question_id)))
        conn.commit()
        confirmation_id = row["confirmation_id"]
    finally:
        conn.close()

    if confirmation_id:
        try:
            journal = journal_for(cfg)
            journal.respond(confirmation_id, "approve")
            journal.finish(confirmation_id)
        except Exception:                                  # noqa: BLE001
            pass                       # the office record is the source
    store.audit("question.answer", requested_by,
                {"id": int(question_id), "answered_by": answered_by})
    return {"state": "OK", "id": int(question_id), "status": "resolved",
            "answer": text, "answered_by": answered_by}


def escalations(cfg: Any, *, status: Optional[str] = None,
                agent_id: Optional[int] = None) -> Dict[str, Any]:
    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        sql = ("SELECT e.*, a.slug AS agent_slug, s.slug AS supervisor_slug"
               " FROM office_escalation e"
               " LEFT JOIN office_agent a ON a.id=e.agent_id"
               " LEFT JOIN office_agent s ON s.id=e.supervisor_id")
        where, args = [], []
        if status:
            where.append("e.status=?"); args.append(status)
        if agent_id is not None:
            where.append("e.agent_id=?"); args.append(int(agent_id))
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY e.id DESC"
        rows = [dict(r) for r in conn.execute(sql, args)]
        return {"state": "OK", "count": len(rows), "escalations": rows}
    finally:
        conn.close()


def resolve(cfg: Any, escalation_id: int, decision: str, *,
            by_agent: int, confirmed: bool = False,
            requested_by: str = "cli:office") -> Dict[str, Any]:
    """Settle an escalation through the shared journal, one level up only.

    `approve` and `deny` land as `resolved`; a denial is a decision, not a
    silence, and the seat is told either way.
    """
    if not confirmed:
        raise OfficeError(store.CONFIRM_CODE,
                          "office escalation-resolve requires --confirm")
    if decision not in ("approve", "deny"):
        raise OfficeError(BAD_INPUT_CODE, "decision must be approve or deny")

    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        row = conn.execute("SELECT * FROM office_escalation WHERE id=?",
                           (escalation_id,)).fetchone()
        if row is None:
            raise OfficeError(NOT_FOUND_CODE, f"no escalation #{escalation_id}")
        if row["status"] != OPEN:
            raise OfficeError(BAD_STATE_CODE,
                              f"escalation #{escalation_id} is already"
                              f" {row['status']}")
        if int(by_agent) != int(row["supervisor_id"]):
            raise OfficeError(
                "OFFICE_ESCALATION_WRONG_LEVEL",
                f"refused: escalation #{escalation_id} was raised to seat "
                f"#{row['supervisor_id']} and this seat is #{by_agent}. Only "
                f"the seat it was raised to may settle it (D34).")
        confirmation_id = row["confirmation_id"]
    finally:
        conn.close()

    # Settle on the shared journal so the dashboard and the voice surface see
    # exactly the same outcome the office does.
    if confirmation_id:
        journal = journal_for(cfg)
        journal.respond(confirmation_id, decision)

    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        conn.execute("UPDATE office_escalation SET status='resolved',"
                     " decided_by=?, decided_at=? WHERE id=?",
                     (int(by_agent), store.now(), escalation_id))
        conn.commit()
        store.audit("escalation.resolve", requested_by,
                    {"id": escalation_id, "decision": decision})
        return {"state": "OK", "id": escalation_id, "status": RESOLVED,
                "decision": decision, "decided_by": int(by_agent),
                "confirmation_id": confirmation_id}
    finally:
        conn.close()


def sync(cfg: Any) -> Dict[str, Any]:
    """Close escalations the **shared surface** already settled.

    This is what keeps one queue instead of two: if the owner approves the
    confirmation in the dashboard, the office must not keep insisting it is
    open. It reads the journal; it never writes a decision of its own.
    """
    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        open_rows = [dict(r) for r in conn.execute(
            "SELECT id, confirmation_id, kind FROM office_escalation WHERE"
            " status='open' AND confirmation_id IS NOT NULL")]
    finally:
        conn.close()
    if not open_rows:
        return {"state": "OK", "synced": 0, "checked": 0}

    journal = journal_for(cfg)
    synced = []
    for row in open_rows:
        item = journal.get(row["confirmation_id"])
        if item is None:
            continue
        if item.state in ("complete", "denied", "aborted"):
            conn = store.connect(cfg)
            try:
                store.migrate(conn)
                conn.execute("UPDATE office_escalation SET status=? WHERE id=?",
                             (RESOLVED if item.decision == "approve"
                              else ABANDONED, row["id"]))
                conn.commit()
            finally:
                conn.close()
            synced.append({"id": row["id"], "state": item.state,
                           "decision": item.decision})
    return {"state": "OK", "synced": len(synced), "checked": len(open_rows),
            "closed": synced}


def _announce_raised(cfg: Any, requested_by: str, detail: Dict[str, Any],
                     eid: int, kind: str) -> None:
    """Audit a raise, and emit the ``escalation`` event it caused (A5, C7).

    A real escalation was raised: the tower draws that as the walk to the
    manager's desk. So the row is emitted by the code that raised it — never by
    the renderer guessing — beside the journal row ``raise_to_supervisor``
    already writes. One journal, no second queue (``90-AGENT-CONTRACT`` §2.5).

    Defined at the end of the module on purpose: this is a call-site-only
    addition, and keeping it out of the function bodies above preserves the
    exact 1-based line numbers other modules cite (``escalation.py:321`` is the
    level rule in :func:`resolve`).
    """
    store.audit("escalation.raise", requested_by, detail)
    try:
        from . import agent_memory as _mem

        agent_id = detail.get("agent_id")
        _mem.emit_event(cfg, "escalation",
                        agent_id=(int(agent_id)
                                  if agent_id is not None else None),
                        payload={"escalation_id": int(eid),
                                 "kind": str(kind),
                                 "confirmation_id": detail.get("confirmation_id")},
                        visible="bubble")
    except Exception:                                   # noqa: BLE001
        pass
