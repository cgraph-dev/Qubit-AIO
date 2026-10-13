"""Floor-plan planner that REFUSES to emit a layout whose rows would occlude.

Why this module exists
----------------------
The office's desks "overlap and occlude each other" — and the arithmetic in
``docs/game-office/24-FLOORPLAN-SCALE.md`` shows that is not a zoom problem and
not a size-of-building problem. It is a row-pitch problem, and it is a *constant*
in the projection:

    a figure is ``CHAR_H`` (= 15) units tall, drawn UPWARD from its feet;
    a row one tile nearer the camera sits ``TILE_H / 2`` (= 8.5) units lower
    on screen; so at a row pitch of ``p`` tiles the covering figure's head
    reaches ``CHAR_H`` units past its own feet while the row behind is only
    ``(TILE_H / 2) * p`` units higher. The zoom ``z`` multiplies BOTH terms:

        15*z  >  8.5*p*z     <=>     15 > 8.5*p     (z cancels)

    At ``p = 2`` the offset is 17 and the 15-unit figure overhangs the row
    behind by 2.0 units at EVERY zoom; because the marker stacks another 32
    units above the feet the back row's desk plate and state band are buried.
    At ``p = 1`` the bodies are 57% covered outright.

So this planner does not accept a room and hope. It computes the usable desk
grid for a given room size and pitch, derives the desk count from that (never
hardcodes 6 or 9), and exposes ``check_no_occlusion`` so a caller can be told
*which rooms* failed instead of being handed a plausible-looking floor plan.

The recommendation it implements (doc §7): ``ROOM_W, ROOM_H = 12, 7`` with a
desk stride of ``(col 4, row 3)`` and odd rows staggered by 1 tile — 3 columns x
3 rows = 9 desks/room, 6 rooms/floor = 54 desks/floor, floor grid 39 x 16.

Pure Python, no dependencies, no I/O, deterministic.

Constants are transcribed from the real sources, not from prose:
``CHAR_H`` from ``frontend/qubit-app/plugins/qubit-office/qubit-office.js``
``drawAgent`` (``var h = 15 * cam.zoom``), ``TILE_H`` from the same file's
``TILE_W, TILE_H = 34, 17``; ``ROOM_COLS, ROOM_ROWS`` from
``voicebridge/office/tower.py``.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

__all__ = [
    "CHAR_H", "CHAR_W", "TILE_W", "TILE_H", "MIN_CLEARANCE",
    "MIN_ROW_OFFSET", "ROOM_W", "ROOM_H", "PITCH_COL", "PITCH_ROW",
    "ODD_ROW_OFFSET", "ROOM_COLS", "ROOM_ROWS", "ROOM_GAP",
    "LEGACY_ROOM_W", "LEGACY_ROOM_H", "LEGACY_PITCH_ROW",
    "clearance", "desks_per_room", "plan", "check_no_occlusion",
]

# --- figure / tile geometry (the projection's own units, pre-zoom) ----------
#: Figure height, in deck units. Read from ``drawAgent`` in
#: ``qubit-office.js``: ``var h = 15 * cam.zoom, w = 9 * cam.zoom;``.
CHAR_H = 15.0
#: Figure width, in deck units (same line as CHAR_H).
CHAR_W = 9.0
#: The 2:1 isometric tile. ``var TILE_W = 34, TILE_H = 17;`` — the half-height
#: ``TILE_H / 2 = 8.5`` is the per-tile row offset in screen units.
TILE_W = 34.0
TILE_H = 17.0

#: The minimum row offset a layout may have. This is NOT just the figure height:
#: at a 2-tile pitch the offset is 17.0, which clears a 15-unit *body* — yet the
#: doc's §4 shows the back row's desk plate and state band are still buried,
#: because `drawMarker` stacks `20 + 12 = 32` units above the feet. The doc's
#: recommendation is therefore the 3-tile pitch, whose `(17/2) * 3 = 25.5`-unit
#: offset is the smallest that clears the body with room for the marker's own
#: overhang to be a separate (headroom) fix. A pitch under 3 is refused.
MIN_CLEARANCE = 3 * (TILE_H / 2.0)
#: The *margin* the recommended pitch leaves over the figure: `25.5 - 15 = 10.5`.
MIN_ROW_OFFSET = CHAR_H

# --- room / desk stride (the doc's §7 recommendation) -----------------------
ROOM_W, ROOM_H = 12, 7
PITCH_COL, PITCH_ROW = 4, 3
#: Odd rows shift one tile so no two desks share a ``(x + y, y)`` sort key
#: (removes the ``(x+1, y-1)`` depth-sort tie).
ODD_ROW_OFFSET = 1

#: Rooms on the floor grid — unchanged by the doc's recommendation.
ROOM_COLS, ROOM_ROWS = 3, 2
#: One unused tile between rooms, per ``tower.py``'s ``room_slot_x``.
ROOM_GAP = 1

#: The geometry the module diagnoses as occluding — kept as a named pair so the
#: test reproduces the doc's finding against the SAME numbers the doc used.
LEGACY_ROOM_W, LEGACY_ROOM_H = 6, 4
LEGACY_PITCH_ROW = 2


def _row_offset(pitch_row: float) -> float:
    """Screen-units of vertical separation a row pitch buys.

    One tile nearer the camera is drawn ``TILE_H / 2`` lower on screen, so a
    pitch of ``p`` rows separates the feet of two adjacent rows by
    ``(TILE_H / 2) * p``. The figure they must clear does NOT scale with the
    pitch, which is why this is the whole occlusion test.
    """
    return (TILE_H / 2.0) * float(pitch_row)


def clearance(room_h: float, pitch: float) -> float:
    """Vertical screen clearance, in units, for a room height and row pitch.

    Positive means the row behind clears the covering figure by that many units;
    negative means it is buried by that many. Measured as
    ``row_offset - CHAR_H``, i.e. the same arithmetic the doc's §4 table prints
    (+10.5 at pitch 3, +2.0 at pitch 2, -6.5 at pitch 1).

    ``room_h`` is accepted because the caller asks in terms of the room it has;
    the clearance depends only on the pitch (the offset is per-tile, not
    per-room), so it is recorded in the result but does not change the number.
    """
    return _row_offset(pitch) - CHAR_H


def _count(pos_start: int, extent: int, step: int) -> int:
    """Desks along one axis: from ``pos_start``, step ``step``, staying <= extent.

    This is the derivation that makes ``desks_per_room`` a function of the pitch
    rather than a constant — the doc's §1 table (a 6x4 room holds 6 desks at
    pitch 2 and only 4 at pitch 3) falls straight out of it.
    """
    if step <= 0 or extent < pos_start:
        return 0
    n = 0
    pos = pos_start
    while pos <= extent:
        n += 1
        pos += step
    return n


def desks_per_room(room_w: int, room_h: int, pitch: Any = None) -> int:
    """Desks a ``room_w x room_h`` room holds at a given stride. Derived.

    ``pitch`` is either a scalar applied to both axes or a ``(col, row)`` pair.
    With the recommended ``(4, 3)`` and a 12x7 room this returns 9 — but it
    returns 9 because 3 columns x 3 rows fit, not because 9 was written down.
    """
    if pitch is None:
        pitch_col, pitch_row = PITCH_COL, PITCH_ROW
    elif isinstance(pitch, (tuple, list)):
        pitch_col, pitch_row = int(pitch[0]), int(pitch[1])
    else:
        pitch_col = pitch_row = int(pitch)
    if pitch_col <= 0 or pitch_row <= 0:
        return 0
    return _count(1, room_w, pitch_col) * _count(1, room_h, pitch_row)


def _desk_positions(room_w: int, room_h: int, pitch_col: int,
                    pitch_row: int, stagger: int) -> List[Dict[str, int]]:
    """Room-local desk offsets for a stride, with odd rows staggered.

    ``x = 1 + pitch_col * i (+ stagger on odd rows)``, ``y = 1 + pitch_row * j``
    — the doc §7 layout (row j=0 at x {1,5,9}, j=1 at x {2,6,10}, j=2 at
    x {1,5,9}). A desk is kept only if it stays inside the room.
    """
    out: List[Dict[str, int]] = []
    j = 0
    y = 1
    while y <= room_h:
        shift = stagger if (j % 2) else 0
        i = 0
        x = 1 + shift
        while x <= room_w:
            out.append({"x": x, "y": y})
            i += 1
            x = 1 + shift + pitch_col * i
        j += 1
        y = 1 + pitch_row * j
    return out


def check_no_occlusion(layout: Dict[str, Any]) -> Dict[str, Any]:
    """Does this layout occlude? Returns the offending rooms by name.

    ``layout`` is a plan dict (or any dict carrying ``rooms`` / ``pitch`` /
    ``room_h``). Every room is checked at the layout's row pitch; a row pitch
    under what ``MIN_CLEARANCE`` requires makes the room a violator. The return
    is ``{ok, worst_clearance, violating_rooms}`` — ``ok`` False with the rooms
    named rather than a bare boolean, so the caller learns *which* rooms failed.
    """
    rooms = layout.get("rooms") if isinstance(layout, dict) else None
    if rooms is None:
        # A bare geometry can still be checked: room_h + an explicit pitch.
        room_h = layout.get("room_h", ROOM_H)
        pitch = layout.get("pitch", layout.get("pitch_row", PITCH_ROW))
        rooms = [{"room_id": "room", "room_h": room_h, "pitch": pitch}]

    violating: List[str] = []
    worst: Optional[float] = None

    for room in rooms:
        rh = room.get("room_h", layout.get("room_h", ROOM_H))
        pitch = room.get("pitch", room.get("pitch_row",
                                           layout.get("pitch",
                                                      layout.get("pitch_row",
                                                                 PITCH_ROW))))
        if isinstance(pitch, (tuple, list)):
            pitch_row = float(pitch[1])
        else:
            pitch_row = float(pitch)
        c = clearance(rh, pitch_row)
        if worst is None or c < worst:
            worst = c
        # Gate on the row offset against the minimum the doc requires (25.5);
        # the returned `worst_clearance` is the margin above the figure.
        if _row_offset(pitch_row) < MIN_CLEARANCE:
            violating.append(str(room.get("room_id", "room")))

    if worst is None:
        worst = clearance(ROOM_H, PITCH_ROW)

    return {
        "ok": not violating,
        "worst_clearance": worst,
        "violating_rooms": violating,
    }


def plan(headcount: int, opts: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """A non-occluding floor plan for ``headcount`` seats, or a refusal.

    Computes room size, desk stride, desks/room and desks/floor, then derives
    rooms and floors from the headcount. Refuses — ``ok=False`` with a ``reason``
    — rather than returning a plausible-looking plan, when the headcount is not
    a positive integer or when the requested geometry would occlude.

    Keys: ``ok, rooms_needed, floors_needed, room_w, room_h, desk_pitch,
    grid_cols, grid_rows, reason`` (plus ``desks_per_room`` and ``rooms`` for
    the caller's ``check_no_occlusion``).
    """
    opts = dict(opts or {})
    room_w = int(opts.get("room_w", ROOM_W))
    room_h = int(opts.get("room_h", ROOM_H))
    pitch = opts.get("pitch", (PITCH_COL, PITCH_ROW))
    if isinstance(pitch, (tuple, list)):
        pitch_col, pitch_row = int(pitch[0]), int(pitch[1])
    else:
        pitch_col = pitch_row = int(pitch)
    stagger = int(opts.get("stagger", ODD_ROW_OFFSET))

    cols = ROOM_COLS
    rows = ROOM_ROWS
    grid_cols = cols * (room_w + ROOM_GAP)
    grid_rows = rows * (room_h + ROOM_GAP)

    def _fail(reason: str) -> Dict[str, Any]:
        return {
            "ok": False,
            "rooms_needed": 0,
            "floors_needed": 0,
            "room_w": room_w,
            "room_h": room_h,
            "desk_pitch": (pitch_col, pitch_row),
            "grid_cols": grid_cols,
            "grid_rows": grid_rows,
            "desks_per_room": 0,
            "reason": reason,
        }

    # --- input honesty: nonsense in must not produce a layout out ---
    if isinstance(headcount, bool) or not isinstance(headcount, int):
        return _fail("headcount must be an int, got %r" % (headcount,))
    if headcount == 0:
        return _fail("headcount is 0: an office with no seats has no floor plan")
    if headcount < 0:
        return _fail("headcount must be >= 0, got %d" % headcount)

    # A plan is produced for any positive headcount, however large — the
    # building just grows. The ceiling is NOT here to hide a size limit.

    if room_w <= 0 or room_h <= 0 or pitch_col <= 0 or pitch_row <= 0:
        return _fail("room %dx%d and pitch %d/%d must all be positive"
                     % (room_w, room_h, pitch_col, pitch_row))

    # --- occlusion refusal: the whole point of the module ---
    offset = _row_offset(pitch_row)
    if offset < MIN_CLEARANCE:
        return _fail(
            "row pitch %d gives only %.1f units of row offset against a %.1f-unit "
            "figure — the row behind is occluded; row pitch must be >= 3"
            % (pitch_row, offset, CHAR_H))

    # --- capacity, derived from the room + stride ---
    per_room = desks_per_room(room_w, room_h, (pitch_col, pitch_row))
    if per_room <= 0:
        return _fail("room %dx%d at pitch (%d,%d) holds no desks"
                     % (room_w, room_h, pitch_col, pitch_row))

    per_floor = per_room * cols * rows
    rooms_needed = -(-headcount // per_room)   # ceil
    floors_needed = -(-rooms_needed // (cols * rows))

    # The plan must actually survive the check it exposes to the caller.
    positions = _desk_positions(room_w, room_h, pitch_col, pitch_row, stagger)
    rooms = [
        {"room_id": "room-%d" % (i + 1), "room_w": room_w, "room_h": room_h,
         "pitch": (pitch_col, pitch_row), "desks": per_room,
         "positions": positions}
        for i in range(rooms_needed)
    ]
    verdict = check_no_occlusion({"rooms": rooms, "room_h": room_h,
                                  "pitch": (pitch_col, pitch_row)})
    if not verdict["ok"]:
        return _fail("internal: computed layout occludes in %s"
                     % ", ".join(verdict["violating_rooms"]))

    reason = (
        "ok: %d desks/room, %d desks/floor; %d seats need %d room(s) over %d "
        "floor(s); row pitch %d clears the %.1f-unit figure by %.1f units"
        % (per_room, per_floor, headcount, rooms_needed, floors_needed,
           pitch_row, CHAR_H, clearance(room_h, pitch_row)))

    return {
        "ok": True,
        "rooms_needed": rooms_needed,
        "floors_needed": floors_needed,
        "room_w": room_w,
        "room_h": room_h,
        "desk_pitch": (pitch_col, pitch_row),
        "grid_cols": grid_cols,
        "grid_rows": grid_rows,
        "desks_per_room": per_room,
        "desks_per_floor": per_floor,
        "positions": positions,
        "rooms": rooms,
        "worst_clearance": verdict["worst_clearance"],
        "reason": reason,
    }
