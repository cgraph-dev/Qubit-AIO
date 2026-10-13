"""The org: floors, departments, rooms, seats — and the chain of command.

The structural rules from `docs/game-office/11-ORG-CHARTER.md` live here, and
they are validated on write rather than trusted:

* **One supervisor per seat** (C30/D34). Every seat except the CEO's names a
  superior. ``supervisor_id`` NULL is legal only for the CEO — the owner is
  that seat's supervisor and is not an agent.
* **Span limit 7** (D34). A manager may carry at most ``MAX_SPAN`` direct
  reports; past that the unit splits, because past seven a manager reviews on
  vibes instead of artifacts.
* **No cycles, and the chain terminates at the CEO.** Walking up from any seat
  must reach exactly one CEO inside ``CHAIN_MAX_HOPS``. A dangling or looping
  chain is refused at write time, not discovered during a quarterly review.
* **Money is never stored on a seat.** A balance comes from the ledger
  (:func:`voicebridge.office.ledger.pnl`), so a seat cannot claim money it did
  not earn.
"""
from __future__ import annotations

import re
import sqlite3
from typing import Any, Dict, List, Optional

from . import store
from .errors import (BAD_INPUT_CODE, BAD_STATE_CODE, CAPACITY_CODE,
                     CHAIN_CODE, EXISTS_CODE, NOT_FOUND_CODE, OfficeError)

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


def _slug(value: str, field: str = "slug") -> str:
    text = (value or "").strip().lower()
    if not _SLUG_RE.match(text):
        raise OfficeError(BAD_INPUT_CODE,
                          f"{field} must match ^[a-z0-9][a-z0-9-]{{0,63}}$")
    return text


def _name(value: str, field: str = "name", limit: int = 120) -> str:
    text = (value or "").strip()
    if not text or len(text) > limit:
        raise OfficeError(BAD_INPUT_CODE,
                          f"{field} must be 1..{limit} chars")
    return text


def _get_agent(conn: sqlite3.Connection, agent_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM office_agent WHERE id=?",
                       (agent_id,)).fetchone()
    if row is None:
        raise OfficeError(NOT_FOUND_CODE, f"no seat #{agent_id}")
    return row


# ------------------------------------------------------------------ the chain
def _walk_up(conn: sqlite3.Connection, agent_id: int) -> List[sqlite3.Row]:
    """Return the chain from this seat up to its terminal seat, inclusive."""
    chain: List[sqlite3.Row] = []
    seen = set()
    current: Optional[int] = agent_id
    while current is not None:
        if current in seen:
            raise OfficeError(
                CHAIN_CODE,
                f"reporting cycle detected at seat #{current}: a seat cannot "
                f"be its own ancestor (C30)")
        seen.add(current)
        row = _get_agent(conn, current)
        chain.append(row)
        if len(chain) > store.CHAIN_MAX_HOPS:
            raise OfficeError(
                CHAIN_CODE,
                f"reporting chain exceeds {store.CHAIN_MAX_HOPS} hops; the "
                f"chain must terminate at the CEO (C30)")
        current = row["supervisor_id"]
    return chain


def _check_chain_terminates(conn: sqlite3.Connection,
                            agent_id: int) -> sqlite3.Row:
    chain = _walk_up(conn, agent_id)
    top = chain[-1]
    if top["seat_type"] != "ceo":
        raise OfficeError(
            CHAIN_CODE,
            f"the chain from seat #{agent_id} terminates at "
            f"{top['slug']!r} ({top['seat_type']}), not at a CEO. Every seat "
            f"must have an accountable superior and the chain must reach the "
            f"CEO (C30)")
    return top


def _check_span(conn: sqlite3.Connection, supervisor_id: int) -> None:
    count = conn.execute(
        "SELECT COUNT(*) c FROM office_agent WHERE supervisor_id=? AND"
        " status != 'terminated'", (supervisor_id,)).fetchone()["c"]
    if count >= store.MAX_SPAN:
        sup = _get_agent(conn, supervisor_id)
        raise OfficeError(
            CAPACITY_CODE,
            f"seat {sup['slug']!r} already carries {count} direct reports; the "
            f"span limit is {store.MAX_SPAN} (D34). Split the unit before "
            f"hiring another report.")


def chain(cfg: Any, agent_id: int) -> Dict[str, Any]:
    """The reporting chain for one seat, ground up."""
    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        rows = _walk_up(conn, agent_id)
        return {"state": "OK", "seat": rows[0]["slug"],
                "depth": len(rows),
                "chain": [{"id": r["id"], "slug": r["slug"],
                           "seat_type": r["seat_type"],
                           "supervisor_id": r["supervisor_id"]} for r in rows],
                "terminates_at_ceo": rows[-1]["seat_type"] == "ceo"}
    finally:
        conn.close()


def tree(cfg: Any) -> Dict[str, Any]:
    """The whole org as a parent/child map (read-only)."""
    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        seats = [dict(r) for r in conn.execute(
            "SELECT id, slug, name, seat_type, supervisor_id, department_id,"
            " portfolio, status FROM office_agent ORDER BY id")]
        by_id = {s["id"]: s for s in seats}
        for s in seats:
            s["reports"] = [t["id"] for t in seats
                            if t["supervisor_id"] == s["id"]]
        roots = [s for s in seats if s["supervisor_id"] is None]
        counts: Dict[str, int] = {}
        for s in seats:
            counts[s["seat_type"]] = counts.get(s["seat_type"], 0) + 1
        over = [s["slug"] for s in seats
                if len(s["reports"]) > store.MAX_SPAN]
        return {"state": "OK", "seats": len(seats), "counts": counts,
                "roots": [r["slug"] for r in roots],
                "over_span": over,
                "employees": seats}
    finally:
        conn.close()


# -------------------------------------------------------------------- floors
def floor_create(cfg: Any, number: int, name: str = "",
                 role: str = "operations", *, confirmed: bool = False,
                 requested_by: str = "cli:user") -> Dict[str, Any]:
    if not confirmed:
        raise OfficeError(store.CONFIRM_CODE,
                          "office floor-create requires --confirm")
    role = store.check_enum(role, store.FLOOR_ROLES, "role")
    try:
        number = int(number)
    except (TypeError, ValueError):
        raise OfficeError(BAD_INPUT_CODE, "floor number must be an integer")
    if not 1 <= number <= store.MAX_FLOORS:
        raise OfficeError(BAD_INPUT_CODE,
                          f"floor number must be 1..{store.MAX_FLOORS} (D13)")
    label = name.strip() or f"Floor {number}"
    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        try:
            cur = conn.execute(
                "INSERT INTO office_floor(number, name, role)"
                " VALUES(?,?,?)", (number, label, role))
        except sqlite3.IntegrityError as exc:
            raise OfficeError(EXISTS_CODE,
                              f"floor {number} already exists") from exc
        conn.commit()
        store.audit("floor.create", requested_by,
                    {"number": number, "role": role})
        return {"state": "OK", "id": int(cur.lastrowid or 0),
                "number": number, "name": store.redact(label),
                "role": role}
    finally:
        conn.close()


def floors(cfg: Any) -> Dict[str, Any]:
    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        out = [dict(r) for r in conn.execute(
            "SELECT f.*, (SELECT COUNT(*) FROM office_department d"
            " WHERE d.floor_id = f.id) AS departments"
            " FROM office_floor f ORDER BY f.number")]
        return {"state": "OK", "count": len(out), "floors": out}
    finally:
        conn.close()


# --------------------------------------------------------------- departments
def department_create(cfg: Any, slug: str, *, name: str = "",
                      floor_id: Optional[int] = None,
                      division_kind: str = "product",
                      director_id: Optional[int] = None,
                      template: str = "", charter: str = "",
                      kill_criteria: str = "", confirmed: bool = False,
                      requested_by: str = "cli:user") -> Dict[str, Any]:
    if not confirmed:
        raise OfficeError(store.CONFIRM_CODE,
                          "office department-create requires --confirm")
    clean = _slug(slug)
    kind = store.check_enum(division_kind, store.DIVISION_KINDS,
                            "division_kind")
    label = _name(name or clean, "name")
    if not (kill_criteria or "").strip():
        raise OfficeError(
            BAD_INPUT_CODE,
            "a department needs kill_criteria before it is created; a business "
            "with no declared way to die is not a business (D33)")
    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        if floor_id is not None:
            if conn.execute("SELECT 1 FROM office_floor WHERE id=?",
                            (floor_id,)).fetchone() is None:
                raise OfficeError(NOT_FOUND_CODE, f"no floor #{floor_id}")
        if director_id is not None:
            d = _get_agent(conn, director_id)
            if d["seat_type"] != "director_portfolio":
                raise OfficeError(
                    BAD_STATE_CODE,
                    f"seat {d['slug']!r} is {d['seat_type']}; only a "
                    f"director_portfolio may own a business and its P&L (D35)")
        try:
            cur = conn.execute(
                "INSERT INTO office_department(slug, name, floor_id,"
                " director_id, division_kind, template, charter, status,"
                " kill_criteria, created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (clean, label, floor_id, director_id, kind, template,
                 charter, "proposed", kill_criteria.strip(), store.now()))
        except sqlite3.IntegrityError as exc:
            raise OfficeError(EXISTS_CODE,
                              f"department {clean!r} exists") from exc
        conn.commit()
        store.audit("department.create", requested_by,
                    {"slug": clean, "kind": kind})
        return {"state": "OK", "id": int(cur.lastrowid or 0), "slug": clean,
                "name": store.redact(label), "division_kind": kind,
                "status": "proposed"}
    finally:
        conn.close()


# --------------------------------------------------------------------- rooms
def room_create(cfg: Any, department_id: int, slug: str, *, kind: str = "work",
                role_scope: str = "", capacity: int = 0,
                confirmed: bool = False,
                requested_by: str = "cli:user") -> Dict[str, Any]:
    if not confirmed:
        raise OfficeError(store.CONFIRM_CODE,
                          "office room-create requires --confirm")
    clean = _slug(slug)
    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        if conn.execute("SELECT 1 FROM office_department WHERE id=?",
                        (department_id,)).fetchone() is None:
            raise OfficeError(NOT_FOUND_CODE,
                              f"no department #{department_id}")
        try:
            cur = conn.execute(
                "INSERT INTO office_room(department_id, slug, kind,"
                " role_scope, capacity) VALUES(?,?,?,?,?)",
                (department_id, clean, kind, role_scope or None,
                 int(capacity or 0)))
        except sqlite3.IntegrityError as exc:
            raise OfficeError(EXISTS_CODE,
                              f"room {clean!r} exists in department "
                              f"#{department_id}") from exc
        conn.commit()
        store.audit("room.create", requested_by,
                    {"department_id": department_id, "slug": clean})
        return {"state": "OK", "id": int(cur.lastrowid or 0),
                "department_id": department_id, "slug": clean}
    finally:
        conn.close()


# --------------------------------------------------------------------- seats
def seat_move(cfg: Any, agent_id: int, *, room_id: Optional[int],
              department_id: Optional[int] = None,
              confirmed: bool = False,
              requested_by: str = "cli:owner") -> Dict[str, Any]:
    """Put a seat at a desk, or take it off one (`room_id=None`).

    `room_id` is set at hire time and nothing could change it afterwards, so a
    seat hired before its rooms existed could never be seated at all — which is
    how an org with eleven seats rendered a building with nobody in it. This is
    a placement, not a re-hire: the seat keeps its id, its supervisor, its
    ledger history and its task trail.

    Refuses a room that does not exist rather than silently seating the agent
    nowhere, and takes the room's department as the seat's department when one is
    not given, because a seat sitting in a room but counted in another division
    is a P&L that does not match the floor plan.
    """
    if not confirmed:
        raise OfficeError(store.CONFIRM_CODE, "office seat-move requires --confirm")
    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        seat = conn.execute("SELECT * FROM office_agent WHERE id=?",
                            (int(agent_id),)).fetchone()
        if seat is None:
            raise OfficeError(NOT_FOUND_CODE, f"no seat #{agent_id}")
        dept = department_id
        if room_id is not None:
            room = conn.execute("SELECT * FROM office_room WHERE id=?",
                                (int(room_id),)).fetchone()
            if room is None:
                raise OfficeError(NOT_FOUND_CODE, f"no room #{room_id}")
            if dept is None:
                dept = int(room["department_id"])
        elif department_id is not None:
            if conn.execute("SELECT 1 FROM office_department WHERE id=?",
                            (int(department_id),)).fetchone() is None:
                raise OfficeError(NOT_FOUND_CODE,
                                  f"no department #{department_id}")
        conn.execute(
            "UPDATE office_agent SET room_id=?, department_id=COALESCE(?,"
            " department_id) WHERE id=?",
            (None if room_id is None else int(room_id), dept, int(agent_id)))
        conn.commit()
        store.audit("seat.move", requested_by,
                    {"agent_id": int(agent_id), "room_id": room_id,
                     "department_id": dept})
        return {"state": "OK", "agent_id": int(agent_id),
                "slug": seat["slug"], "room_id": room_id,
                "department_id": dept}
    finally:
        conn.close()


def hire(cfg: Any, slug: str, *, name: str = "", seat_type: str = "worker",
         department_id: Optional[int] = None,
         room_id: Optional[int] = None,
         supervisor_id: Optional[int] = None, portfolio: str = "",
         confirmed: bool = False,
         requested_by: str = "cli:user") -> Dict[str, Any]:
    """Add one seat. The chain rules are enforced here, not at review time."""
    if not confirmed:
        raise OfficeError(store.CONFIRM_CODE,
                          "office hire requires --confirm")
    clean = _slug(slug)
    seat = store.check_enum(seat_type, store.SEAT_TYPES, "seat_type")
    label = _name(name or clean, "name")

    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        if department_id is not None and conn.execute(
                "SELECT 1 FROM office_department WHERE id=?",
                (department_id,)).fetchone() is None:
            raise OfficeError(NOT_FOUND_CODE, f"no department #{department_id}")

        # A seat hired into a room belongs to that room's department. Without
        # this, `hire(room_id=...)` put the seat on the floor plan but left its
        # department NULL, so the seat appeared on one floor while being counted
        # in no division at all — the floor plan and the P&L disagreeing, which
        # is exactly what `seat_move` refuses to do. Hiring was the hole.
        if room_id is not None:
            room = conn.execute("SELECT * FROM office_room WHERE id=?",
                                (int(room_id),)).fetchone()
            if room is None:
                raise OfficeError(NOT_FOUND_CODE, f"no room #{room_id}")
            if department_id is None:
                department_id = int(room["department_id"])
            elif int(department_id) != int(room["department_id"]):
                raise OfficeError(
                    BAD_INPUT_CODE,
                    f"room #{room_id} belongs to department "
                    f"#{room['department_id']} and this seat was sent to "
                    f"department #{department_id}; a desk in one division while "
                    f"counted in another is a P&L that does not match the floor")

        if seat == "ceo":
            if supervisor_id is not None:
                raise OfficeError(
                    BAD_INPUT_CODE,
                    "the CEO's supervisor is the owner, who is not an agent; "
                    "pass no supervisor_id (D2/C30)")
            existing = conn.execute(
                "SELECT slug FROM office_agent WHERE seat_type='ceo' AND"
                " status != 'terminated'").fetchone()
            if existing is not None:
                raise OfficeError(
                    EXISTS_CODE,
                    f"there is already a CEO ({existing['slug']}); the org has "
                    f"exactly one (D2)")
        else:
            if supervisor_id is None:
                raise OfficeError(
                    CHAIN_CODE,
                    f"seat {clean!r} needs a supervisor. Every seat except the "
                    f"CEO has an accountable superior — a seat nobody can rate "
                    f"is a seat nobody owns (C30/D34)")
            _get_agent(conn, supervisor_id)     # exists?
            _check_span(conn, supervisor_id)    # room on that desk?

        if portfolio and seat != "director_portfolio":
            raise OfficeError(BAD_INPUT_CODE,
                              "portfolio is only meaningful on a "
                              "director_portfolio seat")

        try:
            conn.execute(
                "INSERT INTO office_agent(slug, name, seat_type,"
                " department_id, room_id, supervisor_id, portfolio, status,"
                " created_at, created_by) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (clean, label, seat, department_id, room_id, supervisor_id,
                 portfolio or None, "active", store.now(), requested_by))
        except sqlite3.IntegrityError as exc:
            raise OfficeError(EXISTS_CODE,
                              f"seat {clean!r} exists") from exc
        agent_id = int(conn.execute(
            "SELECT id FROM office_agent WHERE slug=?", (clean,)
        ).fetchone()["id"])

        # The new seat must not break the chain (it cannot, when its own
        # supervisor is valid, but the invariant is cheap to assert and this
        # is the only moment it can be asserted for a fresh seat).
        if seat != "ceo":
            _check_chain_terminates(conn, agent_id)
        conn.commit()
        store.audit("agent.hire", requested_by,
                    {"slug": clean, "seat_type": seat,
                     "supervisor_id": supervisor_id})
        return {"state": "OK", "id": agent_id, "slug": clean,
                "name": store.redact(label), "seat_type": seat,
                "supervisor_id": supervisor_id}
    finally:
        conn.close()


def set_supervisor(cfg: Any, agent_id: int, supervisor_id: Optional[int], *,
                   confirmed: bool = False,
                   requested_by: str = "cli:user") -> Dict[str, Any]:
    """Re-point a seat's supervisor, refusing anything that breaks the chain."""
    if not confirmed:
        raise OfficeError(store.CONFIRM_CODE,
                          "office set-supervisor requires --confirm")
    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        row = _get_agent(conn, agent_id)
        if row["seat_type"] == "ceo":
            raise OfficeError(BAD_INPUT_CODE,
                              "the CEO reports to the owner, not to a seat")
        if supervisor_id is None:
            raise OfficeError(CHAIN_CODE,
                              "a seat cannot be left without a supervisor (C30)")
        if supervisor_id == agent_id:
            raise OfficeError(CHAIN_CODE,
                              "a seat cannot supervise itself (C30)")
        _get_agent(conn, supervisor_id)
        # Would this create a loop? Walk up from the proposed supervisor; if
        # we meet the seat being moved, the move is a cycle.
        seen = set()
        cursor: Optional[int] = supervisor_id
        while cursor is not None:
            if cursor == agent_id:
                raise OfficeError(
                    CHAIN_CODE,
                    f"refused: seat #{supervisor_id} already sits below seat "
                    f"#{agent_id}, so this would make a reporting cycle (C30)")
            if cursor in seen:
                raise OfficeError(CHAIN_CODE,
                                  f"existing cycle in the chain at #{cursor}")
            seen.add(cursor)
            cursor = conn.execute(
                "SELECT supervisor_id FROM office_agent WHERE id=?",
                (cursor,)).fetchone()["supervisor_id"]
        conn.execute("UPDATE office_agent SET supervisor_id=? WHERE id=?",
                     (supervisor_id, agent_id))
        _check_chain_terminates(conn, agent_id)
        conn.commit()
        store.audit("agent.set_supervisor", requested_by,
                    {"agent_id": agent_id, "supervisor_id": supervisor_id})
        return {"state": "OK", "agent_id": agent_id,
                "supervisor_id": supervisor_id}
    finally:
        conn.close()
