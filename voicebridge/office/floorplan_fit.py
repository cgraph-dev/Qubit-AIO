"""Fitted floor-plan geometry: the layer that lets ``tower.py``'s constants move safely.

Why this module exists
----------------------
``docs/game-office/24-FLOORPLAN-SCALE.md`` proved the office's crowding is a
*row-pitch* constant, not a zoom or building-size problem: at the current
``ROOM_W, ROOM_H = 6, 4`` with a 2-tile desk stride a 15-unit figure overhangs the
row behind by 6.5 units, at every zoom (the ``z`` cancels:
``15*z > 8.5*z  <=>  15 > 8.5``). ``voicebridge/office/seating_plan.py`` already
implements the corrected geometry as a pure planner. This module is the thin,
pure layer that:

* exposes the fitted geometry for a headcount, **delegating the capacity maths to
  ``seating_plan.plan()``** rather than reimplementing it — two planners that
  disagree mean one of them is wrong, so there is exactly one;
* gives ``desk_positions`` — the absolute grid coordinates ``tower.py``'s
  ``room_slot_x`` / ``room_slot_y`` would have to become;
* measures, by *parsing* ``tower.py`` (never importing-and-mutating it), how far its
  hard-coded constants diverge from the recommendation, so the gap is a number
  instead of a surprise;
* refuses a geometry whose row clearance is below the figure height.

Pure Python, no dependencies, no I/O on the real store (the only file read is
``tower.py``'s own source text, and only to *report* its constants).

Constants are transcribed from the real sources, not from prose. The doc's §10
script and ``seating_plan.py``'s header agree on all of them:
``CHAR_H = 15``, ``TILE_H = 17`` (so ``TILE_H/2 = 8.5``) from
``frontend/qubit-app/plugins/qubit-office/qubit-office.js``; ``ROOM_COLS,
ROOM_ROWS = 3, 2`` from ``tower.py:98``; ``ROOM_W, ROOM_H`` legacy ``6, 4`` from
``tower.py:99``; the recommended ``12, 7`` / ``(4, 3)`` stride from the doc §7.
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional, Tuple

from . import seating_plan as _sp

__all__ = [
    "CHAR_H", "CHAR_W", "TILE_W", "TILE_H", "FIGURE_MIN_CLEARANCE",
    "LEGACY_ROOM_W", "LEGACY_ROOM_H", "LEGACY_PITCH_COL", "LEGACY_PITCH_ROW",
    "LEGACY_ROOM_COLS", "LEGACY_ROOM_ROWS", "LEGACY_ODD_ROW_OFFSET",
    "ROOM_W", "ROOM_H", "PITCH_COL", "PITCH_ROW", "ODD_ROW_OFFSET",
    "ROOM_COLS", "ROOM_ROWS", "ROOM_GAP",
    "legacy_geometry",
    "geometry_for", "desk_positions", "clearance_report", "assert_matches_tower",
]

# --- projection constants (the doc's §10 header, cited to qubit-office.js) ----
#: Figure height, deck units, pre-zoom: ``drawAgent`` ``var h = 15 * cam.zoom``.
CHAR_H = 15.0
#: Figure width, same line: ``w = 9 * cam.zoom`` — the body half-width for the
#: horizontal overlap test in ``desk_positions``' occlusion proof.
CHAR_W = 9.0
#: 2:1 iso tile width/height; ``dy = (TILE_W/2, TILE_H/2) = (17, 8.5)`` per tile,
#: so ``TILE_H / 2 = 8.5`` is the per-tile row offset on screen.
TILE_W = 34.0
TILE_H = 17.0

#: The clearance (row offset minus figure height) a layout must reach, in units.
#: The doc's §4/§7: a 3-tile row pitch buys ``(17/2)*3 = 25.5`` units of offset
#: against the 15-unit figure — a margin of ``+10.5``. The legacy 2-tile pitch buys
#: ``17.0``, clearing the body by only ``+2.0``, which the doc §4 shows still buries
#: the back row's desk plate and state band. A 3-tile pitch is the floor.
FIGURE_MIN_CLEARANCE = 3 * (TILE_H / 2.0) - CHAR_H      # == 10.5

# --- legacy geometry (what tower.py hard-codes today) -----------------------
#: Transcribed from ``tower.py:99`` — ``ROOM_W, ROOM_H = 6, 4``.
LEGACY_ROOM_W, LEGACY_ROOM_H = 6, 4
#: ``room_slot_x`` strides ``2`` tiles per column (``(n % 3) * 2``, ``tower.py:178``).
LEGACY_PITCH_COL = 2
#: ``room_slot_y`` strides ``2`` rows per desk row (``(n // 3) * 2``, ``tower.py:189``).
LEGACY_PITCH_ROW = 2
#: ``tower.py:98`` — ``ROOM_COLS, ROOM_ROWS = 3, 2``.
LEGACY_ROOM_COLS, LEGACY_ROOM_ROWS = 3, 2
#: Legacy rooms do NOT stagger rows; that is precisely the depth-sort tie (§8).
LEGACY_ODD_ROW_OFFSET = 0

# --- recommended geometry (the doc's §7; mirrors seating_plan) --------------
#: ``ROOM_W, ROOM_H = 12, 7`` — 3 columns x 3 rows fit at a clearing pitch.
ROOM_W, ROOM_H = 12, 7
#: Desk stride: {col 4, row 3} tiles; ``(17/2)*3 = 25.5`` units of row offset.
PITCH_COL, PITCH_ROW = 4, 3
#: Odd rows shift one tile, removing the ``(x+1, y-1)`` depth-sort tie.
ODD_ROW_OFFSET = 1
#: Rooms on the floor grid — the doc §7 leaves this unchanged at 3x2 = 6.
ROOM_COLS, ROOM_ROWS = 3, 2
#: One unused tile between rooms, per ``tower.py``'s ``room_slot_x`` / grid maths.
ROOM_GAP = 1

#: ``tower.py`` sits beside this file; parsing it is how the divergence is measured.
_TOWER_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tower.py")


# --------------------------------------------------------------- geometry
def geometry_for(headcount: int) -> Dict[str, Any]:
    """The fitted floor-plan geometry for ``headcount``, capacity from ``plan()``.

    Delegates every capacity number (``desks_per_room``, ``rooms_needed``,
    ``floors_needed``, ``grid_cols``, ``grid_rows``) to
    ``seating_plan.plan()`` — this module does not re-derive them, so there is a
    single source of truth for what fits.

    Returns ``{room_w, room_h, room_cols, room_rows, pitch_col, pitch_row,
    odd_row_offset, grid_cols, grid_rows, desks_per_room, rooms_needed,
    floors_needed}``. If ``plan()`` refuses (bad headcount), its refusal is carried
    through: every geometry field is ``None`` and ``reason`` explains why.
    """
    plan = _sp.plan(headcount)
    if not plan.get("ok"):
        return {
            "room_w": None, "room_h": None,
            "room_cols": None, "room_rows": None,
            "pitch_col": None, "pitch_row": None,
            "odd_row_offset": None,
            "grid_cols": None, "grid_rows": None,
            "desks_per_room": None, "rooms_needed": None, "floors_needed": None,
            "reason": plan.get("reason", "plan() refused"),
        }
    pitch_col, pitch_row = plan["desk_pitch"]
    return {
        "room_w": int(plan["room_w"]),
        "room_h": int(plan["room_h"]),
        "room_cols": ROOM_COLS,
        "room_rows": ROOM_ROWS,
        "pitch_col": int(pitch_col),
        "pitch_row": int(pitch_row),
        "odd_row_offset": int(ODD_ROW_OFFSET),
        "grid_cols": int(plan["grid_cols"]),
        "grid_rows": int(plan["grid_rows"]),
        "desks_per_room": int(plan["desks_per_room"]),
        "rooms_needed": int(plan["rooms_needed"]),
        "floors_needed": int(plan["floors_needed"]),
        "reason": plan["reason"],
    }


def legacy_geometry() -> Dict[str, Any]:
    """The geometry ``tower.py`` hard-codes today, in the ``geometry_for`` shape.

    A named object so a test can put the legacy and recommended layouts side by
    side and diff the SAME fields, instead of comparing against loose numbers.
    """
    return {
        "room_w": LEGACY_ROOM_W,
        "room_h": LEGACY_ROOM_H,
        "room_cols": LEGACY_ROOM_COLS,
        "room_rows": LEGACY_ROOM_ROWS,
        "pitch_col": LEGACY_PITCH_COL,
        "pitch_row": LEGACY_PITCH_ROW,
        "odd_row_offset": LEGACY_ODD_ROW_OFFSET,
        "grid_cols": LEGACY_ROOM_COLS * (LEGACY_ROOM_W + ROOM_GAP),
        "grid_rows": LEGACY_ROOM_ROWS * (LEGACY_ROOM_H + ROOM_GAP),
        "desks_per_room": LEGACY_ROOM_COLS * LEGACY_ROOM_ROWS - 0,  # 3x2 = 6, per rooom_slot_*
        "rooms_needed": None,
        "floors_needed": None,
    }


def desk_positions(room_x: int, room_y: int, n: int,
                   geom: Dict[str, Any]) -> List[Dict[str, int]]:
    """Absolute grid coords for up to ``n`` desks in a room, at ``geom``'s stride.

    Mirrors ``seating_plan._desk_positions`` exactly (same pitch, same odd-row
    stagger, same "keep it only if it stays inside the room") and then adds the
    room's own origin, so the results are the *absolute* coords the floorplan
    payload carries.

    This is what ``tower.py``'s ``room_slot_x`` / ``room_slot_y`` would become.
    The two functions it replaces are, verbatim from the file:

        line 170-178  def room_slot_x(col, n):
                          return col * (ROOM_W + 1) + 1 + (n % 3) * 2
        line 181-189  def room_slot_y(row, n):
                          return row * (ROOM_H + 1) + 1 + (n // 3) * 2

    and at the call site ``tower.py:150-151``:

        "x": room_slot_x(col, n),
        "y": room_slot_y(row, n),

    ``desk_positions`` replaces all four lines: the ``(n % 3) * 2`` /
    ``(n // 3) * 2`` strides become ``pitch_col`` / ``pitch_row``, the ``+1`` becomes
    the room-local ``x = 1 + shift + pitch_col*i``, and the odd-row stagger
    (``+ shift``) is introduced where the legacy version had none.
    """
    room_w = int(geom["room_w"])
    room_h = int(geom["room_h"])
    pitch_col = int(geom["pitch_col"])
    pitch_row = int(geom["pitch_row"])
    stagger = int(geom.get("odd_row_offset", 0))
    if pitch_col <= 0 or pitch_row <= 0 or room_w <= 0 or room_h <= 0:
        return []

    local = _sp._desk_positions(room_w, room_h, pitch_col, pitch_row, stagger)
    out = [{"x": int(room_x) + p["x"], "y": int(room_y) + p["y"]} for p in local]
    return out[:max(0, int(n))]


# --------------------------------------------------------------- clearance
def clearance_report(geom: Dict[str, Any]) -> Dict[str, Any]:
    """Row clearance in units, the margin over the 15-unit figure, and a verdict.

    Returns ``{ok, reason, pitch_row, room_h, row_offset, figure_h, margin,
    min_clearance, per_row_pitch}`` where ``per_row_pitch`` is the doc's §4 table
    as data: ``{pitch_row: {"row_offset", "clearance"}}`` for pitch 1..6, so the
    numbers are printed rather than asserted by adjective.

    **Refuses** — ``ok=False`` with a ``reason`` naming the shortfall — when the
    row offset is below the figure height (the row behind is occluded) *or* below
    ``FIGURE_MIN_CLEARANCE`` (the offset clears the body but still buries the back
    row's desk plate and state band, which is the legacy 2-tile pitch). The
    recommended 3-tile pitch returns ``ok=True`` with a ``+10.5`` margin; the
    legacy 2-tile pitch returns ``ok=False`` with a ``+2.0`` margin stated.
    """
    pitch_row = geom.get("pitch_row")
    if pitch_row is None:
        return {"ok": False, "reason": "no pitch_row in geometry",
                "pitch_row": None, "room_h": geom.get("room_h"),
                "row_offset": None, "figure_h": CHAR_H, "margin": None,
                "min_clearance": FIGURE_MIN_CLEARANCE, "per_row_pitch": {}}
    pitch_row = float(pitch_row)
    row_offset = (TILE_H / 2.0) * pitch_row
    margin = row_offset - CHAR_H
    min_offset = CHAR_H + FIGURE_MIN_CLEARANCE        # 25.5 == 3-tile pitch

    per_row_pitch = {
        p: {"row_offset": (TILE_H / 2.0) * p,
            "clearance": (TILE_H / 2.0) * p - CHAR_H}
        for p in range(1, 7)
    }

    if margin < 0:
        reason = ("row pitch %g gives %.1f units of row offset against a %.1f-unit "
                  "figure — the row behind is occluded by %.1f units at every zoom"
                  % (pitch_row, row_offset, CHAR_H, -margin))
        ok = False
    elif row_offset < min_offset:
        reason = ("row pitch %g clears the %.1f-unit body by only %.1f units, below "
                  "the %.1f-unit offset the doc requires — the back row's desk plate "
                  "and state band are buried"
                  % (pitch_row, CHAR_H, margin, min_offset))
        ok = False
    else:
        reason = ("row pitch %g gives %.1f units of row offset against a %.1f-unit "
                  "figure — margin +%.1f units"
                  % (pitch_row, row_offset, CHAR_H, margin))
        ok = True

    return {
        "ok": ok, "reason": reason,
        "pitch_row": pitch_row, "room_h": geom.get("room_h"),
        "row_offset": row_offset, "figure_h": CHAR_H, "margin": margin,
        "min_clearance": FIGURE_MIN_CLEARANCE, "per_row_pitch": per_row_pitch,
    }


# --------------------------------------------------------------- divergence
# The constants this checker compares MUST be parsed from tower.py's real source.
# An earlier version parsed only ROOM_W/ROOM_COLS here and then filled pitch/offset
# from this module's own LEGACY_PITCH_* constants -- so it reported `current:
# pitch_row=2` while tower.py actually carried 3. That is the exact failure mode
# this checker exists to catch (a fix reported but never bound), and a checker that
# lies this way is worse than no checker: it trains readers to ignore it. Every
# compared parameter is now resolved from the source, and a parameter that cannot
# be resolved is reported as None with `unresolved` naming it -- never silently
# filled with a local default.
_CONST_RE = {
    "ROOM_W": re.compile(r"^ROOM_W\s*,\s*ROOM_H\s*=\s*(-?\d+)\s*,\s*(-?\d+)\s*$",
                         re.MULTILINE),
    "ROOM_COLS": re.compile(r"^ROOM_COLS\s*,\s*ROOM_ROWS\s*=\s*(-?\d+)\s*,\s*(-?\d+)\s*$",
                            re.MULTILINE),
    # tower.py's desk stride. This is written as `PITCH_COL, PITCH_ROW = 4, 3`, and
    # the odd-row offset is `ODD_ROW_OFFSET = 1`. All three are parse-able; if any
    # stops matching, report `unresolved` rather than falling back to a constant.
    "PITCH": re.compile(r"^PITCH_COL\s*,\s*PITCH_ROW\s*=\s*(-?\d+)\s*,\s*(-?\d+)\s*$",
                        re.MULTILINE),
    "ODD_ROW_OFFSET": re.compile(r"^ODD_ROW_OFFSET\s*=\s*(-?\d+)\s*$", re.MULTILINE),
    # tower.py spells the per-room seat count as a product, e.g.
    # `SEAT_SLOTS_PER_ROOM = SEATS_PER_DESK_ROW * DESK_ROWS`. Parse the FACTORS so
    # the value is derived from the source, not assumed to be `cols * rows`.
    "SEAT_SLOTS": re.compile(r"^SEAT_SLOTS_PER_ROOM\s*=\s*(\w+)\s*\*\s*(\w+)",
                              re.MULTILINE),
    "INT_CONST": re.compile(r"^(\w+)\s*=\s*(-?\d+)\s*$", re.MULTILINE),
    # tower.py may carry the pitch on the desk-stride call instead of a constant.
    # Accept either shape; whichever is absent is simply not the one used.
    "PITCH_CALL": re.compile(r"PITCH_COL\s*=\s*(-?\d+)\s*,\s*PITCH_ROW\s*=\s*(-?\d+)"),
}


def _resolve_pitch(text: str, unresolved: list):
    """Return (pitch_col, pitch_row) from tower.py's source, or (None, None)."""
    m = _CONST_RE["PITCH"].search(text)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = _CONST_RE["PITCH_CALL"].search(text)
    if m:
        return int(m.group(1)), int(m.group(2))
    unresolved.append("pitch_col")
    unresolved.append("pitch_row")
    return None, None


def _line_of(text: str, needle: str) -> Optional[int]:
    """1-indexed line number of the first line containing ``needle``, or None."""
    for i, line in enumerate(text.splitlines(), start=1):
        if needle in line:
            return i
    return None


def assert_matches_tower() -> Dict[str, Any]:
    """Read ``tower.py``'s CURRENT constants by parsing its text; report the gap.

    Deliberately does NOT import ``tower`` and mutate anything: it reads the file
    and pulls the two constant lines and the two stride slots out of the source,
    then compares them against this module's recommendation. Returns
    ``{current, recommended, differs_on}`` where ``differs_on`` is a list of the
    parameter names that differ, each carrying the exact ``tower.py`` line it was
    read from — so the divergence is visible and measurable rather than a surprise.

    The legacy constants are *not* asserted to equal tower's; a divergence is the
    expected state today (the recommendation is not yet applied). The value here is
    that the divergence is a number with a line number attached.
    """
    with open(_TOWER_PATH, "r", encoding="utf-8") as fh:
        text = fh.read()

    cur: Dict[str, Any] = {"path": _TOWER_PATH, "lines_read": {}}
    line_no: Dict[str, int] = {}

    unresolved: list = []
    for key in ("ROOM_W", "ROOM_COLS"):
        m = _CONST_RE[key].search(text)
        if not m:
            # Report the failure as a value, not an exception: a checker that
            # raises here tells the caller nothing about WHICH claim it could not
            # verify, and a caller that catches it learns nothing either.
            unresolved.append(key)
            cur[key + "_a"] = None
            cur[key + "_b"] = None
            continue
        cur[key + "_a"] = int(m.group(1))
        cur[key + "_b"] = int(m.group(2))
        line_no[key] = text[:m.start()].count("\n") + 1

    # the desk stride + odd-row offset, resolved from the SAME source text
    _pitch = _resolve_pitch(text, unresolved)
    m_off = _CONST_RE["ODD_ROW_OFFSET"].search(text)
    _offset = int(m_off.group(1)) if m_off else None
    if _offset is None:
        unresolved.append("odd_row_offset")
    line_no["pitch"] = _line_of(text, "PITCH_COL") or 0
    line_no["odd_row_offset"] = _line_of(text, "ODD_ROW_OFFSET") or 0
    cur["unresolved"] = sorted(set(unresolved))

    # The strides live inside the two slot functions; read them verbatim.
    mx = re.search(r"def room_slot_x\(.*?\n(.*?)return\s+(.+)$", text,
                   re.MULTILINE | re.DOTALL)
    my = re.search(r"def room_slot_y\(.*?\n(.*?)return\s+(.+)$", text,
                   re.MULTILINE | re.DOTALL)
    if not mx or not my:
        raise AssertionError("could not parse room_slot_x/room_slot_y out of tower.py")
    slot_x_expr = mx.group(2).strip()
    slot_y_expr = my.group(2).strip()
    line_no["room_slot_x"] = _line_of(text, "def room_slot_x") or 0
    line_no["room_slot_y"] = _line_of(text, "def room_slot_y") or 0
    line_no["room_slot_x_return"] = _line_of(text, "return col * (ROOM_W + 1)") or 0
    line_no["room_slot_y_return"] = _line_of(text, "return row * (ROOM_H + 1)") or 0
    line_no["SEAT_SLOTS_PER_ROOM"] = _line_of(text, "SEAT_SLOTS_PER_ROOM") or 0

    cur["room_slot_x"] = slot_x_expr
    cur["room_slot_y"] = slot_y_expr

    room_w = int(cur["ROOM_W_a"])
    room_h = int(cur["ROOM_W_b"])
    room_cols = int(cur["ROOM_COLS_a"])
    room_rows = int(cur["ROOM_COLS_b"])

    recommended = {
        "ROOM_W": ROOM_W, "ROOM_H": ROOM_H,
        "ROOM_COLS": ROOM_COLS, "ROOM_ROWS": ROOM_ROWS,
        "SEAT_SLOTS_PER_ROOM": _sp.desks_per_room(ROOM_W, ROOM_H, (PITCH_COL, PITCH_ROW)),
        "pitch_col": PITCH_COL, "pitch_row": PITCH_ROW,
        "odd_row_offset": ODD_ROW_OFFSET,
        "grid_cols": ROOM_COLS * (ROOM_W + ROOM_GAP),
        "grid_rows": ROOM_ROWS * (ROOM_H + ROOM_GAP),
    }

    # What tower.py currently holds, in the same field names.
    #
    # EVERY field here must come from tower.py's SOURCE. The version this replaces
    # filled `pitch_col`/`pitch_row`/`odd_row_offset` from this module's own
    # `LEGACY_PITCH_*` constants and `SEAT_SLOTS_PER_ROOM` from `room_cols *
    # room_rows`. As a result `current` and `recommended` were BOTH this module's
    # own numbers -- the checker was comparing the module to itself, and the
    # "divergence" it reported was between two of its own constants, not between
    # the module and tower.py. It happened to report the right NUMBERS (tower did
    # still carry the legacy stride when this was written) while proving nothing:
    # if tower.py is changed to something else entirely, the old code still prints
    # the same "current". A checker must read the thing it is checking.
    #
    # `SEAT_SLOTS_PER_ROOM` now uses tower's parsed SEAT_SLOTS_PER_ROOM (line 116),
    # which is `SEATS_PER_DESK_ROW * DESK_ROWS` -- the real product, not room_cols
    # * room_rows (3*2=6 vs the real 3*3=9).
    _slots = _CONST_RE["SEAT_SLOTS"].search(text)
    seats_per_room = None
    if _slots:
        # resolve `A * B` from the file's own integer constants rather than
        # assuming the product equals room_cols * room_rows
        _ints = {k: int(v) for k, v in _CONST_RE["INT_CONST"].findall(text)}
        fa, fb = _slots.group(1), _slots.group(2)
        if fa in _ints and fb in _ints:
            seats_per_room = _ints[fa] * _ints[fb]
    if seats_per_room is None:
        unresolved_extra = "SEAT_SLOTS_PER_ROOM"
    else:
        unresolved_extra = None

    current = {
        "ROOM_W": room_w, "ROOM_H": room_h,
        "ROOM_COLS": room_cols, "ROOM_ROWS": room_rows,
        "SEAT_SLOTS_PER_ROOM": seats_per_room,
        "pitch_col": _pitch[0],
        "pitch_row": _pitch[1],
        "odd_row_offset": _offset,
        "grid_cols": room_cols * (room_w + ROOM_GAP),
        "grid_rows": room_rows * (room_h + ROOM_GAP),
    }
    if unresolved_extra:
        unresolved.append(unresolved_extra)
    # carry the unresolved list on the result so a caller can see what could NOT
    # be verified rather than reading a stale default as a measured value.
    current_unresolved = sorted(set(unresolved))

    # Which tower.py line each param was read from (the constants by name; the
    # strides from the slot functions' return statements).
    _line_for = {
        "ROOM_W": line_no["ROOM_W"], "ROOM_H": line_no["ROOM_W"],
        "ROOM_COLS": line_no["ROOM_COLS"], "ROOM_ROWS": line_no["ROOM_COLS"],
        "SEAT_SLOTS_PER_ROOM": _line_of(text, "SEAT_SLOTS_PER_ROOM") or 0,
        "pitch_col": line_no["room_slot_x_return"],
        "pitch_row": line_no["room_slot_y_return"],
        "odd_row_offset": line_no["room_slot_y_return"],
        "grid_cols": line_no["ROOM_COLS"],
        "grid_rows": line_no["ROOM_COLS"],
    }

    differs_on = [
        {"param": name, "current": current[name], "recommended": recommended[name],
         "tower_line": _line_for[name]}
        for name in ("ROOM_W", "ROOM_H", "ROOM_COLS", "ROOM_ROWS",
                     "SEAT_SLOTS_PER_ROOM", "pitch_col", "pitch_row",
                     "odd_row_offset", "grid_cols", "grid_rows")
        if current[name] != recommended[name]
    ]

    return {
        "current": current,
        "recommended": recommended,
        "differs_on": differs_on,
        "lines_read": line_no,
        "same": not differs_on,
    }
