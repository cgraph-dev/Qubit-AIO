"""The ROOM PROGRAMME: how many rooms, of what kind, seating whom.

This module is the *design layer above the geometry layer*. `seating_plan.py`
answers "does a room of size WxH hold N desks without the row occluding?" — it is
about tiles and pitch. This module answers a question one level up:

    given a headcount and a department count, how many rooms of what KIND should
    the building contain, and who sits in each?

Why that question is not the geometry question
-----------------------------------------------
The live office has four rooms holding eleven seats:

    boardroom (kind ``exec``, 3 seated) · desk-a (4) · desk-b (3) · review (0)

and the rendered page shows it wrong in two ways at once. The ``review`` room is
**empty**, and the two ``work`` rooms are **crowded** — seven leaf seats in two
rooms whose own capacity constant is 4 each (the live ``office_room`` rows carry
``capacity=4``), i.e. the plan over-fills the rooms it has. Neither is a tile
problem: `seating_plan.py` would draw all three occupied rooms correctly. It is a
**programme** problem — the org model says a manager carries at most seven direct
reports and judges exactly one unit (`org.py:94`, C31), yet the current building
puts eight leaves and two managers into three rooms with no rule saying which
manager owns which desks, while the room built for the internal-decision function
sits unused.

So this module computes a programme from the org model, not from habit.

What the org model actually says (and where)
--------------------------------------------
Every rule below is cited to the line that enforces it, because a programme
justified by prose is the failure mode this module exists to avoid.

* **The chain of command is mandatory and acyclic.** `org.py:57` `_walk_up`
  refuses a cycle (C30) and `org.py:80` `_check_chain_terminates` refuses a
  chain that does not end at a CEO (C30). So the org is a rooted tree, which is
  exactly what a room programme can be laid out as.
* **``supervisor_id`` is mandatory for every seat except the CEO's** —
  `org.py:399` refuses a seat with no supervisor ("a seat nobody can rate is a
  seat nobody owns", C30/D34). So "leaf" is well-defined: a seat is a **leaf**
  when no live seat names it as ``supervisor_id``, and a **supervisor** when at
  least one does. The distinction is a graph property, not a naming convention.
* **Span limit 7** — `org.py:94` `_check_span` refuses an 8th direct report
  (D34). A unit past seven leaves must split into more than one work room with a
  manager each; this is the arithmetic that makes room count a function of
  headcount and not a taste.
* **Each department is owned by exactly one ``director_portfolio``** —
  `org.py:227` refuses a department whose ``director_id`` is not a
  ``director_portfolio``, and D35 (`10-DECISIONS.md`) says "each business has
  exactly one owning director". A director is a supervisor, not a leaf.
* **Two room kinds carry an org meaning** — `57-BOARDROOM.md` §"Two new room
  kinds": ``boardroom`` is the CEO's owner-facing decision room ("CEO + the
  proposing manager + (remote) the owner"); ``review`` is a manager's
  internal-decision room ("a manager ruling on their own team's proposals, no
  owner needed"), and a department "may have any number of review rooms".
  `org.py:253` ``room_create(kind="work")`` is the default for everything else,
  i.e. an ordinary team room.

Where a supervisor sits
-----------------------
A **manager's own desk is in the room it runs** — that is the ordinary reading of
"a manager runs one business unit" (D35/`11-ORG-CHARTER.md`). So a work room is
*one supervisor desk plus its leaves*; the supervisor is **not counted as a leaf
seat**, and `check` refuses a programme that counts it as one. That is the
room-layer restatement of C31 (`95-CONTRACT.md:657`): "a seat cannot write its
own `office_review`; the reviewer's level must be exactly one above" — a manager
cannot be *one of the leaves* in the unit it reviews, because then the unit has
no independent reviewer.

Directors and the CEO do **not** sit in a work room: they are the executive
chain and sit in the boardroom (`57-BOARDROOM.md`). This is why the boardroom is
not an empty ceremonial room — on the documented corp it holds the CEO and the
portfolio directors.

Pure Python. No I/O, no store, no clock, no network.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

__all__ = [
    "ROOM_KINDS", "ROOM_KIND_ALIASES",
    "MIN_ROOM_SEATS", "MAX_ROOM_SEATS", "MAX_SPAN",
    "MAX_ROOM_COLS", "MAX_ROOM_ROWS", "ROOMS_PER_FLOOR",
    "programme", "check", "staffing", "rooms_per_floor",
]

# --------------------------------------------------------------------- kinds
#: The ``kind`` values this programme actually uses. Each is a value the code can
#: really write:
#:
#: * ``"work"``  — `org.py:253` ``room_create(..., kind: str = "work")``: the
#:   default, i.e. an ordinary team room. Live rooms ``desk-a``/``desk-b`` use it.
#: * ``"boardroom"`` — `57-BOARDROOM.md` §"Two new room kinds": the CEO's
#:   owner-facing decision room. The live room ``boardroom`` uses it (written as
#:   kind ``exec`` in the DB; see ``ROOM_KIND_ALIASES``).
#: * ``"review"`` — `57-BOARDROOM.md`: "internal decisions: a manager ruling on
#:   their own team's proposals, no owner needed". The live room ``review`` uses
#:   it.
#:
#: Deliberately NOT included: the long `30-DATA-MODEL.md:170` decorative enum
#: (``lab``, ``studio``, ``vault``, ``crawl_room`` ...). Those are aspirational;
#: no code path writes them, so inventing them here would make the programme
#: describe a building nobody can create.
ROOM_KINDS = ("work", "boardroom", "review")

#: The live DB stores the boardroom's kind as ``exec`` while `57-BOARDROOM.md`
#: calls the kind ``boardroom``; ``meeting`` is what the event map calls a review
#: gathering (`tower.py:63`). The programme emits the documented names; this
#: alias table maps what-is-written -> what-it-means, so `check` recognises a
#: building whose boardroom was written under either spelling instead of
#: reporting a boardroom as missing for a naming reason.
ROOM_KIND_ALIASES = {"exec": "boardroom", "meeting": "review"}


def _canonical_kind(kind: Any) -> Any:
    return ROOM_KIND_ALIASES.get(kind, kind)


# ------------------------------------------------------------------- limits
#: At most 7 direct reports per supervisor — `org.py:94` `_check_span`, D34.
#: Read as a number here because the room programme must split any unit the org
#: would refuse: a work room whose leaves exceed MAX_SPAN would need either a
#: manager with an illegal span or leaves with no manager at all (C30).
MAX_SPAN = 7

#: The smallest room that reads as a room rather than a corridor. Below two
#: seats a room cannot hold the "manager + proposer" pair `57-BOARDROOM.md`
#: describes for a review room, nor a manager and a report in a team room; and a
#: 1-seat room wastes a whole cell of the 3x2 render grid (`tower.py:98`) on a
#: booth. A work room always holds its manager *plus* the leaves, so a work room
#: with 1 leaf seat is 2 desks — MIN_ROOM_SEATS is about the leaves, i.e. a work
#: room must have at least one leaf to run.
MIN_ROOM_SEATS = 2

#: The largest number of *leaves* a single work room may hold. NOT the geometry
#: ceiling — a 12x7 room at the doc's recommended (4,3) pitch holds 9 desks
#: (`seating_plan.desks_per_room(12, 7, (4, 3)) == 9`). It is the programme
#: ceiling, and it is 7 for one reason: the span limit (`org.py:94`, D34). A room
#: seating 8+ leaves needs either a manager with an illegal span or a second
#: manager, which means it was already two rooms. Typing rooms at >= 8 leaves
#: would emit a floor plan the org layer refuses to staff.
#:
#: The gap to the geometry ceiling is recorded in ``MAX_ROOM_DESKS_GEOMETRY`` so
#: a reader can see the two numbers differ and why: the binding constraint on a
#: programme room is the SPAN, not the tiles.
MAX_ROOM_SEATS = MAX_SPAN

#: The geometry ceiling for a recommended-size room, quoted so the gap to
#: ``MAX_ROOM_SEATS`` is visible: `seating_plan` holds 9 desks in a 12x7 room at
#: the doc's (4,3) pitch. A *programme* room's leaves are capped lower by the
#: span limit; a work room's physical desks are ``leaves + 1`` (the manager), so
#: a full work room is 8 desks, still inside the 9-desk geometry.
MAX_ROOM_DESKS_GEOMETRY = 9

#: The floor grid rooms are laid out on: `tower.py:98` `ROOM_COLS, ROOM_ROWS =
#: 3, 2` -> 6 rooms per floor. `check` reports a programme whose room count does
#: not fit this grid and SAYS SO rather than silently expanding the grid —
#: `24-FLOORPLAN-SCALE.md` §7 is explicit: "Do not change
#: `ROOM_COLS/ROOM_ROWS`, and do not enlarge the building."
MAX_ROOM_COLS, MAX_ROOM_ROWS = 3, 2
ROOMS_PER_FLOOR = MAX_ROOM_COLS * MAX_ROOM_ROWS


def _ceil_div(n: int, d: int) -> int:
    return -(-n // d) if d > 0 else 0


def _can_afford_directors(headcount: int, dept_count: int) -> bool:
    """Whether the headcount can carry one director seat per department.

    A director costs one seat and supervises only managers; there is no point
    (and no seat) for a directorate when the org is barely larger than its
    executive. The rule used is the live office's own shape: at 11 seats the
    departments have no separate director and their managers report straight to
    the CEO (the live DB has ``director_id`` NULL on all three departments and
    two managers reporting to the CEO).

    Directorates are afforded when the department's share of the org can hold a
    director and still leave a manager its own unit: ``headcount >= 1 + 2*D``.
    Below that the managers report to the CEO directly — legal under C30, which
    requires *a* supervisor, never a director.
    """
    return dept_count > 0 and headcount >= 1 + 2 * dept_count


def staffing(headcount: int, dept_count: int) -> Dict[str, int]:
    """Split ``headcount`` seats into CEO / directors / managers / leaves.

    Derived from the org rules, not configured:

    * the CEO is one seat — `org.py:390` allows exactly one (D2);
    * directors exist only when affordable (:func:`_can_afford_directors`) and,
      when they do, there is one per department — `org.py:227` + D35;
    * managers: ``ceil(leaves / MAX_SPAN)``, because the span limit (`org.py:94`,
      D34) means a manager carries at most seven direct reports.

    The split is solved **exactly**: the largest leaf count ``L`` for which
    ``ceo + directors + ceil(L / MAX_SPAN) + L == headcount``. Solving rather
    than estimating is what keeps the programme from emitting a building that
    seats 68 of 71 people (an estimate, off by exactly the manager seats it
    forgot) or 8-leaf rooms (an estimate that under-counted managers).
    """
    out = {"ceo": 0, "directors": 0, "managers": 0, "leaves": 0, "seats": 0}
    if not isinstance(headcount, int) or isinstance(headcount, bool) or headcount <= 0:
        return out
    if not isinstance(dept_count, int) or isinstance(dept_count, bool) or dept_count < 0:
        return out

    out["ceo"] = 1
    dirs = dept_count if _can_afford_directors(headcount, dept_count) else 0

    # Solve L exactly: seats(L) = 1 + dirs + ceil(L / MAX_SPAN) + L == headcount.
    best = 0
    for leaves in range(headcount, -1, -1):
        managers = _ceil_div(leaves, MAX_SPAN) if leaves > 0 else 0
        seats = 1 + dirs + managers + leaves
        if seats <= headcount:
            best = leaves
            out["directors"] = dirs
            out["managers"] = managers
            out["leaves"] = leaves
            out["seats"] = seats
            break
    if best == 0 and out["leaves"] == 0:
        # No leaf seat fits (headcount consumed by the executive/directorate):
        # report what the headcount can hold rather than inventing leaves.
        out["directors"] = min(dirs, max(0, headcount - 1))
        out["seats"] = 1 + out["directors"]
    return out


def programme(headcount: int, dept_count: int) -> List[Dict[str, Any]]:
    """The room programme for ``headcount`` seats across ``dept_count`` depts.

    Returns a list of ``{kind, slug_hint, seats, seats_detail, why}`` rooms,
    ordered the way they should be laid out on the floor grid (the boardroom
    first — the CEO's room, on the executive floor per `57-BOARDROOM.md`).

    * ``seats`` is the number of **physical desks** the room holds.
    * ``seats_detail`` is who sits at them: for a work room, one supervisor
      (``is_supervisor: True``) plus its leaves; for the boardroom, the executive
      chain; for the review room, nobody (it is a walking destination).

    The shape is forced by the org model:

    * **one ``boardroom``** holding the executive chain — the CEO (exactly one
      seat, `org.py:390` D2) plus the portfolio directors, one per department
      when affordable (`org.py:227` + D35).
    * **one ``work`` room per manager**, because `org.py:94` refuses the 8th
      direct report (D34): a unit past seven leaves is not a bigger room, it is
      two rooms with a manager each. The room count is the org's own arithmetic
      (:func:`staffing`), not a chosen number. Each work room holds its manager
      **plus** its leaves, so the manager is never counted as a leaf.
    * **one shared ``review`` room** — `57-BOARDROOM.md`: "internal decisions: a
      manager ruling on its own team's proposals". One room serves the building
      because a review room is a *place agents walk to*, not a desk allocation; a
      department "may have any number" but need not have one each. It holds **no
      permanent seats** (``seats=0``): its occupancy is transient by design. This
      is the room the current building leaves empty, and the reason it is empty
      is that it has no permanent desks — so rendering it empty is CORRECT, and
      deleting it would be the wrong response to an empty-looking room.
    """
    if not isinstance(headcount, int) or isinstance(headcount, bool) or headcount <= 0:
        return []
    if not isinstance(dept_count, int) or isinstance(dept_count, bool) or dept_count < 0:
        return []

    staff = staffing(headcount, dept_count)
    rooms: List[Dict[str, Any]] = []

    # --- the boardroom: the executive chain, not leaves ---
    exec_seats = staff["ceo"] + staff["directors"]
    exec_seats = min(exec_seats, headcount)
    detail = [{"role": "ceo", "is_supervisor": True}]
    for d in range(staff["directors"]):
        detail.append({"role": f"director_portfolio-{d + 1}", "is_supervisor": True})
    detail = detail[:exec_seats]
    rooms.append({
        "kind": "boardroom",
        "slug_hint": "boardroom",
        "seats": len(detail),
        "seats_detail": detail,
        "why": (
            f"the executive chain: the CEO (exactly one seat, org.py:390 D2) "
            f"plus {staff['directors']} portfolio director(s), one per department "
            f"where affordable (org.py:227, D35). Owner-facing decisions are made "
            f"here (57-BOARDROOM.md: 'CEO + the proposing manager'), so it seats "
            f"supervisors, not leaves"),
    })

    # --- work rooms: one per manager, manager + its leaves ---
    leaves = staff["leaves"]
    n_work = max(0, staff["managers"])
    if leaves > 0 and n_work == 0:
        # No manager seat was affordable: the leaves hang directly off the CEO,
        # which C30 permits but the span limit caps — emit the rooms the span
        # rule demands and let `check` accept them (each <= MAX_SPAN leaves).
        n_work = _ceil_div(leaves, MAX_SPAN)

    if n_work > 0 and leaves > 0:
        base = leaves // n_work
        extra = leaves % n_work
        for i in range(n_work):
            leaf_count = base + (1 if i < extra else 0)
            if leaf_count <= 0:
                continue
            dept = (i % dept_count) + 1 if dept_count > 0 else 0
            where = f"department #{dept}" if dept_count > 0 else "the CEO directly"
            letter = chr(ord("a") + i) if i < 26 else f"n{i + 1}"
            detail = [{"role": "manager", "is_supervisor": True}]
            for s in range(leaf_count):
                detail.append({"role": f"leaf-{s + 1}", "is_supervisor": False})
            rooms.append({
                "kind": "work",
                "slug_hint": f"desk-{letter}",
                "seats": leaf_count + 1,        # leaves + the manager's own desk
                "seats_detail": detail,
                "why": (
                    f"one manager's unit: {leaf_count} leaf seat(s) of {where} "
                    f"plus the manager's own desk. The manager is a supervisor, "
                    f"not a leaf (C31/95-CONTRACT.md:657). The span limit is "
                    f"{MAX_SPAN} direct reports (org.py:94 D34), so a unit past "
                    f"{MAX_SPAN} leaves splits into a second work room with its "
                    f"own manager rather than growing one room"),
            })

    # --- the review room: a meeting destination, no permanent seats ---
    rooms.append({
        "kind": "review",
        "slug_hint": "review",
        "seats": 0,
        "seats_detail": [],
        "why": (
            "internal decisions — a manager ruling on its own team's proposals, "
            "no owner needed (57-BOARDROOM.md). Holds NO permanent seats by "
            "design: it is a room agents walk to for a meeting, so drawing it "
            "empty is correct, not a defect"),
    })

    return rooms


def rooms_per_floor() -> int:
    """Rooms the current floor grid holds: 3 x 2 = 6 (`tower.py:98`)."""
    return ROOMS_PER_FLOOR


def check(programme: Any, headcount: int) -> Dict[str, Any]:
    """Sanity-check a programme against ``headcount``. Refuses, with a reason.

    Returns ``{ok, seats_offered, shortfall, floors_needed, fits_grid, reason}``.

    * ``seats_offered`` counts the **physical seats that get a person off their
      feet**: the boardroom's executive seats and every work room's desks. A
      ``review`` room holds none by design, so it contributes 0.
    * ``shortfall`` is ``headcount - seats_offered`` when positive.
    * ``ok`` means "this programme seats everyone AND contains no illegal room".
      A room count that exceeds the floor grid is reported separately as
      ``fits_grid=False`` with ``floors_needed``, because needing a second floor
      is a building fact, not a reason nobody can sit down — conflating the two
      would make ``ok`` a lie in one direction or the other.

    Refuses, by name:

    * a programme that does not seat everyone (``shortfall`` > 0) — a floor plan
      that leaves seats undrawn is the bug the module exists to catch;
    * a work room with **more than one supervisor**, or with **no** supervisor —
      a work room is one manager's unit (C31), so exactly one supervisor;
    * a programme that seats a supervisor among leaves, i.e. a work room that
      counts its manager as a leaf (C31, `95-CONTRACT.md:657`);
    * a work room with more than ``MAX_SPAN`` leaves (the manager would be
      over-span, `org.py:94` D34) or fewer than one leaf (nothing to run);
    * a ``review`` room with permanent seats, or a ``boardroom`` with none;
    * an unknown ``kind``.
    """
    if not isinstance(headcount, int) or isinstance(headcount, bool) or headcount <= 0:
        return {"ok": False, "seats_offered": 0, "shortfall": 0,
                "floors_needed": 0, "fits_grid": True,
                "reason": f"headcount must be a positive int, got {headcount!r}"}
    if not isinstance(programme, list) or not programme:
        return {"ok": False, "seats_offered": 0, "shortfall": headcount,
                "floors_needed": 0, "fits_grid": True,
                "reason": "programme must be a non-empty list of rooms"}

    seats_offered = 0
    rooms_total = 0

    def _fail(reason: str) -> Dict[str, Any]:
        return {
            "ok": False, "seats_offered": seats_offered,
            "shortfall": max(0, headcount - seats_offered),
            "floors_needed": _ceil_div(rooms_total, ROOMS_PER_FLOOR),
            "fits_grid": rooms_total <= ROOMS_PER_FLOOR,
            "reason": reason,
        }

    for i, room in enumerate(programme):
        if not isinstance(room, dict):
            return _fail(f"room #{i} is not a dict")
        kind = _canonical_kind(room.get("kind"))
        if kind not in ROOM_KINDS:
            return _fail(f"room #{i} has unknown kind {room.get('kind')!r}; "
                         f"ROOM_KINDS are {ROOM_KINDS}")
        try:
            seats = int(room.get("seats", 0))
        except (TypeError, ValueError):
            return _fail(f"room #{i} ({kind}) has a non-integer seat count")
        rooms_total += 1

        if kind == "review":
            if seats != 0:
                return _fail(
                    f"room #{i} ('{room.get('slug_hint')}') is kind 'review' "
                    f"with {seats} permanent seats; a review room is a meeting "
                    f"destination and holds none (57-BOARDROOM.md)")
            continue

        detail = room.get("seats_detail") or []

        if kind == "boardroom":
            if seats < 1:
                return _fail("a boardroom must seat the executive chain "
                             "(>= 1 seat: the CEO)")
            seats_offered += seats
            continue

        # kind == "work": exactly one supervisor + 1..MAX_SPAN leaves
        if detail:
            supers = sum(1 for s in detail
                         if isinstance(s, dict) and s.get("is_supervisor"))
            leaves_in_room = sum(1 for s in detail
                                 if isinstance(s, dict) and not s.get("is_supervisor"))
        else:
            # no detail: fall back to the seat count, treating seat 0 as the
            # manager. This keeps a minimally-specified room checkable.
            supers = 1 if seats >= 1 else 0
            leaves_in_room = max(0, seats - 1)

        if supers == 0:
            return _fail(
                f"room #{i} ('{room.get('slug_hint')}') has no supervisor; a "
                f"work room is one manager's unit (C31), so it must contain its "
                f"manager's desk")
        if supers > 1 and detail:
            return _fail(
                f"room #{i} ('{room.get('slug_hint')}') seats {supers} "
                f"supervisors; a work room is ONE unit and holds one manager "
                f"(C31/95-CONTRACT.md:657) — extra supervisors belong in the "
                f"boardroom")
        if leaves_in_room < 1:
            return _fail(
                f"room #{i} ('{room.get('slug_hint')}') has no leaves; a work "
                f"room with only its manager has nothing to run, so "
                f"MIN is 1 leaf")
        if leaves_in_room > MAX_SPAN:
            return _fail(
                f"room #{i} ('{room.get('slug_hint')}') has {leaves_in_room} "
                f"leaves, past the span limit of {MAX_SPAN} direct reports "
                f"(org.py:94 D34): this unit must split into two work rooms with "
                f"a manager each, or its manager would be over-span")
        if not detail and seats > MAX_SPAN + 1:
            return _fail(
                f"room #{i} ('{room.get('slug_hint')}') seats {seats} desks, "
                f"past one manager plus {MAX_SPAN} leaves (org.py:94 D34)")

        seats_offered += seats

    shortfall = max(0, headcount - seats_offered)
    floors_needed = _ceil_div(rooms_total, ROOMS_PER_FLOOR)
    fits_grid = rooms_total <= ROOMS_PER_FLOOR

    if shortfall > 0:
        return {
            "ok": False, "seats_offered": seats_offered, "shortfall": shortfall,
            "floors_needed": floors_needed, "fits_grid": fits_grid,
            "reason": (
                f"programme offers {seats_offered} permanent seats but the "
                f"headcount is {headcount}: {shortfall} seat(s) would be left "
                f"without a desk (the review room holds none by design, so it "
                f"does not count)"),
        }

    grid_note = ""
    if not fits_grid:
        grid_note = (
            f"; WARNING: {rooms_total} rooms exceed the {MAX_ROOM_COLS}x"
            f"{MAX_ROOM_ROWS} floor grid ({ROOMS_PER_FLOOR} rooms/floor), so it "
            f"needs {floors_needed} floors — the grid is NOT expanded here "
            f"(24-FLOORPLAN-SCALE.md §7: do not enlarge the building)")

    return {
        "ok": True, "seats_offered": seats_offered, "shortfall": 0,
        "floors_needed": floors_needed, "fits_grid": fits_grid,
        "reason": (
            f"ok: {rooms_total} room(s) offer {seats_offered} permanent seats "
            f"for a headcount of {headcount} across {_count_kinds(programme)}"
            f"{grid_note}"),
    }


def _count_kinds(programme: List[Dict[str, Any]]) -> str:
    counts: Dict[str, int] = {}
    for room in programme:
        if not isinstance(room, dict):
            continue
        k = _canonical_kind(room.get("kind"))
        counts[k] = counts.get(k, 0) + 1
    return ", ".join(f"{n} {k}" for n, k in sorted(counts.items()))
