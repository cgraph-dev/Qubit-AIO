"""Reviews: the level above rates the level below (D34/D36/D39/C31).

Two rules, and the second is the one that matters:

* **Nobody rates themselves.** A seat writes *reports* about its own work
  (`office_report`) and those are checked against the ledger. It does not write
  *reviews* of itself, because a rating is the thing the level above supplies.
* **The reviewer is exactly one level above.** The reviewer must be the seat's
  own supervisor — no skipping a level, and no peers rating peers. Two managers
  rating each other is not review; it is a trade, and it is how a bad quarter
  gets a clean write-up.

A review is a rating plus what it rests on. It never blocks work: the org does
not stop to be judged (D22), so filing a review of a seat that is mid-task is
normal and expected.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from . import store
from .errors import BAD_INPUT_CODE, NOT_FOUND_CODE, OfficeError

VERDICTS = ("exceeds", "meets", "below", "failing")


def _seat(conn, agent_id: int):
    row = conn.execute("SELECT * FROM office_agent WHERE id=?",
                       (int(agent_id),)).fetchone()
    if row is None:
        raise OfficeError(NOT_FOUND_CODE, f"no seat #{agent_id}")
    return row


def file_review(cfg: Any, *, subject_agent_id: int, reviewer_agent_id: int,
                verdict: str, kind: str = "weekly", period: str = "",
                body: str = "", confirmed: bool = False,
                requested_by: str = "cli:office") -> Dict[str, Any]:
    """Record a rating of one seat by exactly the level above it."""
    if not confirmed:
        raise OfficeError(store.CONFIRM_CODE,
                          "office review requires --confirm")
    verdict = store.check_enum(verdict, VERDICTS, "verdict")

    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        subject = _seat(conn, subject_agent_id)
        reviewer = _seat(conn, reviewer_agent_id)

        if int(subject["id"]) == int(reviewer["id"]):
            raise OfficeError(
                "OFFICE_SELF_REVIEW",
                f"refused: seat {subject['slug']!r} cannot review itself. A "
                f"seat reports on its own work; the rating comes from above "
                f"(D39/C31).")
        if subject["supervisor_id"] is None:
            raise OfficeError(
                "OFFICE_REVIEW_WRONG_LEVEL",
                f"refused: seat {subject['slug']!r} has no supervisor, so "
                f"there is no level above it to review it (C30).")
        if int(reviewer["id"]) != int(subject["supervisor_id"]):
            raise OfficeError(
                "OFFICE_REVIEW_WRONG_LEVEL",
                f"refused: seat {reviewer['slug']!r} is not exactly one level "
                f"above {subject['slug']!r}. A review must come from the "
                f"seat's own supervisor (C31); a peer rating a peer is a trade, "
                f"not a review.")
        if reviewer["status"] == "terminated":
            raise OfficeError(
                "OFFICE_REVIEW_WRONG_LEVEL",
                f"refused: seat {reviewer['slug']!r} is terminated and cannot "
                f"review anyone.")

        cur = conn.execute(
            "INSERT INTO office_review(ts, subject_agent_id, reviewer_agent_id,"
            " kind, period, verdict, body) VALUES(?,?,?,?,?,?,?)",
            (store.now(), int(subject["id"]), int(reviewer["id"]),
             str(kind or "weekly"), str(period or ""), verdict, str(body or "")))
        conn.commit()
        rid = int(cur.lastrowid or 0)
    finally:
        conn.close()
    store.audit("review.file", requested_by,
                {"id": rid, "subject": subject_agent_id,
                 "reviewer": reviewer_agent_id, "verdict": verdict})
    return {"state": "OK", "id": rid, "subject_slug": subject["slug"],
            "reviewer_slug": reviewer["slug"], "verdict": verdict,
            "blocks_work": False}


def reviews(cfg: Any, *, subject_agent_id: Optional[int] = None,
            limit: int = 20) -> Dict[str, Any]:
    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        sql = ("SELECT r.*, s.slug AS subject_slug, v.slug AS reviewer_slug"
               " FROM office_review r"
               " LEFT JOIN office_agent s ON s.id=r.subject_agent_id"
               " LEFT JOIN office_agent v ON v.id=r.reviewer_agent_id")
        args: List[Any] = []
        if subject_agent_id is not None:
            sql += " WHERE r.subject_agent_id=?"
            args.append(int(subject_agent_id))
        sql += " ORDER BY r.id DESC LIMIT ?"
        args.append(int(limit))
        rows = [dict(r) for r in conn.execute(sql, args)]
        return {"state": "OK", "count": len(rows), "reviews": rows}
    finally:
        conn.close()
