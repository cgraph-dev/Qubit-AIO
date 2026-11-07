"""The Office agents panel: seats grouped by division, from the roster on disk.

WHAT THIS IS, AND WHAT IT REFUSES TO BE
---------------------------------------
The Office agents panel lists every SEAT the building plan places, grouped by
the division the seat belongs to, with the persona's real name and the count
per division. It is a projection of two inputs and nothing else:

  * ``agency_org.plan_building()`` — the seats, their types and their rooms;
  * ``agency_org.load()``          — the personas and their division labels.

No field here is invented. Every key the panel serves is copied from one of
those two structures, and :func:`panel` REFUSES (``ok: false``) rather than
filling a gap: an empty division label or a seat whose persona is not in the
roster is a real inconsistency in the data, and reporting it is more useful
than a plausible-looking row.

TWO NUMBERS THAT LOOK ALIKE AND ARE NOT
---------------------------------------
``plan_building()["seats"]`` folds the executive chain into the roster's own
divisions, so three seats (the CEO and the functional directors) carry
``division == ""`` — they sit in the boardroom, above every division. Counting
seats per division therefore yields NINETEEN buckets, one of which has the
empty string as its slug (3 seats), while the roster has EIGHTEEN divisions.
The panel keeps that distinction explicit: the empty-slug bucket is rendered
as the boardroom tier, never silently merged into a division and never dropped,
so the per-division counts sum to the seat total.

A function promoted out of its division (the second and third functional
directors are ``academic`` personas holding boardroom seats) still reports the
division it came FROM — that is what ``seat["division"]`` says, and the panel
does not re-derive it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

SCHEMA = "qubit-office-agents-panel/1"

#: The boardroom's division key in a seat record: the executive chain is placed
#: above the divisions, so its seats carry no division slug.
BOARDROOM_KEY = ""

#: The label the boardroom bucket is shown under. It is the office's own name
#: for that tier (``room_layout`` calls the room ``boardroom``), not a division
#: label — no division on disk has the empty slug.
BOARDROOM_LABEL = "Boardroom (executive tier)"

#: Seat types in the order the plan builds them: the executive chain first,
#: then the teams. A stable order is what makes two renders comparable.
SEAT_ORDER = ("ceo", "director_functional", "director_portfolio",
              "manager", "worker")


def _empty(detail: str) -> Dict[str, Any]:
    return {"schema": SCHEMA, "ok": False, "code": "AGENCY_ORG_UNAVAILABLE",
            "detail": detail, "divisions": [], "seats": [], "counts": {}}


def panel(root: Optional[Path] = None) -> Dict[str, Any]:
    """The panel payload: divisions, their seats, and the real counts.

    Returns::

        {
          "schema", "ok",
          "counts": {headcount, divisions, roster_divisions, rooms,
                     floors, boardroom_seats, seat_types, per_division},
          "divisions": [ {slug, label, kind, floor, headcount,
                          seats: [ {slug, name, seat_type, supervisor,
                                    room, emoji, color} ]} ],
          "seats": [ every seat, in plan order ],
          "detail",
        }

    ``divisions`` is sorted by slug with the boardroom bucket LAST (it has no
    slug, so sorting it among the divisions would put it first for no reason a
    reader could see).
    """
    from . import agency_org as A

    try:
        plan = A.plan_building(root)
        roster = A.load(root)
    except Exception as exc:  # noqa: BLE001 — the route must answer, not crash
        return _empty(f"the agency roster could not be read: {str(exc)[:200]}")

    if not plan.get("ok"):
        check = plan.get("check") or {}
        return _empty("the building plan refused this roster: "
                      + str(check.get("reason") or "no reason given"))

    seats: List[Dict[str, Any]] = plan.get("seats") or []
    roster_divs: Dict[str, Dict[str, Any]] = roster.get("divisions") or {}
    # The roster's own slug -> label, for a seat whose division has one.
    labels = {slug: (d.get("label") or slug) for slug, d in roster_divs.items()}

    rows: List[Dict[str, Any]] = []
    for seat in seats:
        division = seat.get("division") or BOARDROOM_KEY
        row = {
            "slug": seat.get("slug") or "",
            "name": seat.get("name") or "",
            "seat_type": seat.get("seat_type") or "",
            "supervisor": seat.get("supervisor"),
            "division": division,
            "room": seat.get("room") or "",
            # A promoted seat carries `-N` on its slug, so it is not a roster
            # key. The roster slug is the one the persona is ON DISK under.
            "persona_slug": (str(seat.get("slug") or "").rsplit("-", 1)[0]
                             if str(seat.get("slug") or "").rsplit("-", 1)[-1]
                             .isdigit() else (seat.get("slug") or "")),
            "emoji": seat.get("persona_emoji") or "",
            "color": seat.get("persona_color") or "",
        }
        rows.append(row)

    # A seat with no name or no slug is a broken record, not a blank cell.
    for row in rows:
        if not row["slug"] or not row["name"]:
            return _empty("the plan placed a seat with no persona slug or name; "
                          "refusing to render a nameless seat")
    # Every seat's division must be either the boardroom bucket or a division
    # the roster actually has — otherwise the grouping would silently create a
    # division that does not exist.
    unknown = sorted({r["division"] for r in rows} - set(labels)
                     - {BOARDROOM_KEY})
    if unknown:
        return _empty("seats reference division(s) the roster does not have: "
                      + ", ".join(unknown))
    # Every seat must name a persona the roster actually holds. `plan_building`
    # promotes an already-rostered persona into a second executive seat by
    # suffixing its slug (`-2`), so the roster key is not the seat slug for
    # those; the check is on the PERSONA, and a seat with no matching persona
    # is a broken roster, never a row to render.
    roster_slugs = {p.get("slug") for p in (roster.get("personas") or [])}
    missing = sorted({r["persona_slug"] for r in rows} - roster_slugs)
    if missing:
        return _empty("seats name persona(s) the roster does not hold: "
                      + ", ".join(missing))

    floors = {f.get("division"): f.get("number")
              for f in (plan.get("floors") or [])}
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault(row["division"], []).append(row)

    # Plan order within a group, so the executive chain reads first.
    def _rank(row: Dict[str, Any]) -> int:
        try:
            return SEAT_ORDER.index(row["seat_type"])
        except ValueError:
            return len(SEAT_ORDER)

    divisions: List[Dict[str, Any]] = []
    for slug in sorted(groups):
        if slug == BOARDROOM_KEY:
            continue
        members = sorted(groups[slug], key=_rank)
        divisions.append({
            "slug": slug,
            "label": labels.get(slug, slug),
            "kind": (roster_divs.get(slug) or {}).get("kind") or "",
            "floor": floors.get(slug),
            "headcount": len(members),
            "seats": members,
        })
    if BOARDROOM_KEY in groups:
        members = sorted(groups[BOARDROOM_KEY], key=_rank)
        # The boardroom's tier is a room, not a floor: `plan_building` puts it
        # on `exec_floor`, which is asserted by `room_layout.check`. It is not
        # copied here because the plan does not expose it as a floor record.
        divisions.append({
            "slug": BOARDROOM_KEY,
            "label": BOARDROOM_LABEL,
            "kind": "executive",
            "floor": None,
            "headcount": len(members),
            "seats": members,
        })

    per_division = {d["slug"]: d["headcount"] for d in divisions}
    seat_types: Dict[str, int] = {}
    for row in rows:
        seat_types[row["seat_type"]] = seat_types.get(row["seat_type"], 0) + 1

    return {
        "schema": SCHEMA,
        "ok": True,
        "counts": {
            "headcount": len(rows),
            "divisions": len([d for d in divisions if d["slug"]]),
            "roster_divisions": len(roster_divs),
            "boardroom_seats": len(groups.get(BOARDROOM_KEY, [])),
            "rooms": plan.get("rooms_needed", 0),
            "floors": len(plan.get("floors") or []),
            "seat_types": dict(sorted(seat_types.items())),
            "per_division": per_division,
        },
        "divisions": divisions,
        "seats": rows,
        "detail": ("every seat the building plan places, grouped by the "
                   "division recorded on the seat; the boardroom tier holds "
                   "the seats the plan places above the divisions"),
    }
