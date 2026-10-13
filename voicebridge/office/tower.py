"""The tower: the office as a renderable view, derived from the store and events.

This module exists to make **C7 enforceable**. "The tower renders only from
events" is a rule about the *renderer*, and a rule that lives only in a browser
is a rule nobody can test. So the animation state of every character is computed
here, in Python, as a **pure function of the `office_event` rows and a clock
value** — and the browser is handed the answer. The plugin then has nothing to
animate from but those numbers.

Three consequences, and they are the point:

* **No timers, anywhere.** `state()` takes `now` as an argument. Freeze the event
  stream and advance the clock, and every character is `idle` — which is what
  `tests/test_office_tower_no_phantom_motion.py` asserts. There is no code path
  that produces motion without an event row behind it.
* **A stalled turn looks stalled, not busy.** If a run started and never settled,
  the character's animation is `idle` (no phantom motion) and the *state* says
  `stalled` — so the dead turn is visible as a fact rather than as a character
  pretending to work. This is the M4 proof about killing a session mid-turn.
* **An empty org renders empty.** With no rows there is no floorplan to build, so
  `empty` is `True` and the plugin is told to draw an empty state. It is never
  handed a demo office, because it has nothing to draw one from (D14).

Everything the HUD shows is the same fold `qubit office pnl` reads (C2), so
"the HUD matches the CLI to the cent" is true by construction rather than by
careful maintenance.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from . import store

SCHEMA = "qubit-office-tower/1"
FLOORPLAN_SCHEMA = "qubit-office-floorplan/1"

#: How long after an event a character is still drawn as doing that thing.
#: After this the state falls back to `idle` — the event is history, not motion.
ACTIVE_WINDOW_S = 120.0

#: The event -> animation map. Two sources, both already in the docs: the run
#: events the runner emits, and the capability events `50-INTEGRATION.md` maps.
#: An unknown kind maps to `working` rather than to nothing, because an event
#: that happened must still be visible — but it is never invented.
_ANIMATION = {
    # the runner's own events
    "run_started": "working",
    "run_settled": "settling",
    "run_unpriced": "paused",
    "run_failed": "stopped",
    "task_done": "done",
    # NOT `idle`. A turn that ran and produced work which is not yet verified has
    # WORKED -- `idle` is for a seat whose last event is outside the window, and
    # using it here made a real turn end in apparent stillness, so the office
    # looked dead however much work it did. The owner's report ("nothing is
    # moving") was measured against exactly this row. `working` is the honest
    # animation for unverified output: the model ran, the check did not pass.
    "task_unverified": "working",
    "consent_denied": "blocked",
    # `50-INTEGRATION.md` capability map
    "tool_call": "walking",
    "note_written": "carrying",
    "memory_written": "glow",
    "skill_used": "working",
    "mcp_call": "walking",
    "ledger_write": "settling",
    "escalation": "walking_to_manager",
    "meeting": "meeting",
}

#: How long a walk takes, in seconds. The server computes the whole path; the
#: renderer only draws the point the server says is true for `now`.
WALK_S = 6.0

#: Named spots inside a room. An agent walks to one of these when its event says
#: it is doing the thing that happens there — so the movement is a *rendering of
#: an event*, never decoration. A room is `ROOM_W x ROOM_H`; these are offsets.
STATIONS = {
    "workbench": (1, 2),     # `tool_call`, `skill_used`
    "vault": (4, 1),         # `ledger_write`
    "comms": (4, 2),         # `mcp_call`
    "shelf": (2, 3),         # `note_written`, `memory_written`
}
#: Which station an event kind walks to. Kinds absent here stay at the desk.
_EVENT_STATION = {
    "tool_call": "workbench",
    "skill_used": "workbench",
    "ledger_write": "vault",
    "mcp_call": "comms",
    "note_written": "shelf",
    "memory_written": "shelf",
}
#: `escalation` walks to the supervisor instead of a station in its own room.

#: The animations that must NEVER be produced by the passage of time alone.
MOTION = ("working", "walking", "carrying", "glow", "settling", "meeting",
          "walking_to_manager")

#: Minutes between a floor level. Floors stack; floor 0 is the executive floor.
FLOOR_HEIGHT_UNITS = 3

#: Grid the rooms are laid out on. Fixed so a floorplan is deterministic.
#:
#: Sized from `docs/game-office/24-FLOORPLAN-SCALE.md`, which worked the numbers
#: out against the projection: an agent figure is 15 units tall, and at 2:1
#: isometric a desk row 2 tiles apart is only 8.5 units of screen separation.
#: `15 > 8.5` at EVERY zoom, so a 2-tile row pitch buries the back row at any
#: magnification — the crowding was geometric, not a rendering fault. A 3-tile
#: row pitch gives 25.5 units against the 15-unit figure (+10.5 of clearance),
#: which is the margin that makes a room readable.
#:
#: `voicebridge/office/floorplan_fit.py` measures this geometry and refuses an
#: occluding one; `voicebridge/office/seating_plan.py` holds the capacity maths.
ROOM_COLS, ROOM_ROWS = 3, 2
ROOM_W, ROOM_H = 12, 7

#: 3 desks per row x 3 rows: stride 4 across, 3 down, with odd rows offset by 1
#: tile so two desks never share a cell in the isometric projection.
SEATS_PER_DESK_ROW = 3
DESK_ROWS = 3
SEAT_SLOTS_PER_ROOM = SEATS_PER_DESK_ROW * DESK_ROWS
PITCH_COL, PITCH_ROW = 4, 3
ODD_ROW_OFFSET = 1


# ---------------------------------------------------------------- floorplan
def floorplan(cfg: Any) -> Dict[str, Any]:
    """Build the building from the store. Procedural: no asset, no file needed.

    Rooms are laid out on a fixed grid so the same store always yields the same
    building, and a seat's slot inside its room is its position in seat order —
    so an agent does not drift between renders.
    """
    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        floors = [dict(r) for r in conn.execute(
            "SELECT * FROM office_floor ORDER BY number")]
        rooms = [dict(r) for r in conn.execute(
            "SELECT * FROM office_room ORDER BY department_id, id")]
        seats = [dict(r) for r in conn.execute(
            "SELECT id, slug, seat_type, department_id, room_id, supervisor_id,"
            " status FROM office_agent WHERE status != 'terminated'"
            " ORDER BY id")]
        depts = {r["id"]: dict(r) for r in conn.execute(
            "SELECT id, slug, floor_id FROM office_department")}
    finally:
        conn.close()

    # a room with no department has nowhere to be; and a floor with no rooms is
    # still a floor (an empty floor is a real, reportable state)
    rooms_by_floor: Dict[int, List[Dict[str, Any]]] = {}
    for room in rooms:
        dept = depts.get(room["department_id"])
        if dept is None or dept["floor_id"] is None:
            continue
        rooms_by_floor.setdefault(int(dept["floor_id"]), []).append(room)

    seats_by_room: Dict[int, List[Dict[str, Any]]] = {}
    for seat in seats:
        if seat["room_id"] is not None:
            seats_by_room.setdefault(int(seat["room_id"]), []).append(seat)

    out_floors = []
    for floor in floors:
        floor_rooms = []
        for i, room in enumerate(rooms_by_floor.get(int(floor["id"]), [])):
            col, row = i % ROOM_COLS, (i // ROOM_COLS) % ROOM_ROWS
            slots = []
            for n, seat in enumerate(seats_by_room.get(int(room["id"]), [])):
                slots.append({"slot": n, "agent_id": int(seat["id"]),
                              "slug": seat["slug"],
                              "x": room_slot_x(col, n) + room_slot_offset(n),
                              "y": room_slot_y(row, n)})
            floor_rooms.append({
                "id": int(room["id"]), "slug": room["slug"],
                "kind": room["kind"], "department_id": int(room["department_id"]),
                "x": col * (ROOM_W + 1), "y": row * (ROOM_H + 1),
                "w": ROOM_W, "h": ROOM_H, "seats": slots})
        out_floors.append({
            "id": int(floor["id"]), "number": int(floor["number"]),
            "name": store.redact(floor["name"] or ""),
            "role": floor["role"], "theme": floor.get("theme") or "",
            "z": int(floor["number"]) * FLOOR_HEIGHT_UNITS,
            "rooms": floor_rooms})

    return {"schema": FLOORPLAN_SCHEMA,
            "grid": {"cols": ROOM_COLS * (ROOM_W + 1), "rows": ROOM_ROWS * (ROOM_H + 1),
                     "tile": 1},
            "floors": out_floors}


def room_slot_x(col: int, n: int) -> int:
    """Desk column inside a room: 3 desks per row, `PITCH_COL` tiles apart.

    Note this is the *room-local* column; `room_slot_y` walks the rows. Together
    they lay a room's desks out in a grid, because a single row of desks is not
    a room — it is a shelf, and every desk on it overlaps the last one when drawn
    in isometric projection.
    """
    return col * (ROOM_W + 1) + 1 + (n % SEATS_PER_DESK_ROW) * PITCH_COL


def room_slot_y(row: int, n: int) -> int:
    """Desk row inside a room: every third desk starts a new row.

    The stride is `PITCH_ROW` (3), not 2, and that is the whole point: at 2:1
    isometric a 2-tile row pitch is 8.5 screen units against a 15-unit figure, so
    `15 > 8.5` at every zoom and the back row is buried at any magnification. A
    3-tile pitch is 25.5 units, clearing the figure by +10.5. Odd rows are
    offset by one tile in x (`ODD_ROW_OFFSET`) so two desks never land on the
    same cell once projected.
    """
    r = n // SEATS_PER_DESK_ROW
    return row * (ROOM_H + 1) + 1 + r * PITCH_ROW


def room_slot_offset(n: int) -> int:
    """The extra x nudge for a staggered row.

    Without it, `(x, y)` and `(x+2, y+2)`-style neighbours can share an
    isometric cell: the projection collapses `x+y`, so two desks can project to
    the same point from different grid coordinates. The odd-row offset breaks
    that tie.
    """
    return ODD_ROW_OFFSET if (n // SEATS_PER_DESK_ROW) % 2 else 0


# ------------------------------------------------------------------ events
def _animation_for(kind: str) -> str:
    return _ANIMATION.get(str(kind or ""), "working")


def activity_from_events(event_rows: List[Dict[str, Any]], *,
                         now: float, window: float = ACTIVE_WINDOW_S
                         ) -> Dict[int, Dict[str, Any]]:
    """Turn recent event rows into per-agent animation state.

    **This is the whole of C7.** The function reads no clock of its own, holds
    no state, and starts no timer: given the same rows and the same `now`, it
    returns the same thing. When the rows stop arriving and `now` moves past the
    window, every entry becomes `idle`.
    """
    out: Dict[int, Dict[str, Any]] = {}
    for row in event_rows:
        if row.get("agent_id") is None:
            continue
        aid = int(row["agent_id"])
        prev = out.get(aid)
        if prev is not None and float(prev["ts"]) >= float(row["ts"]):
            continue
        age = float(now) - float(row["ts"])
        fresh = age <= window
        out[aid] = {
            "agent_id": aid,
            "kind": row["kind"],
            "ts": float(row["ts"]),
            "age_s": round(age, 1),
            "fresh": fresh,
            # no row, or a stale row -> idle. There is deliberately no branch
            # that produces motion from anything but a fresh event.
            "animation": _animation_for(row["kind"]) if fresh else "idle",
            # a run that started and never settled is reported as a STATE, so a
            # dead turn is visible without the character pretending to work
            "stalled": bool(not fresh and row["kind"] == "run_started"),
        }
    return out


# ------------------------------------------------------------------ walking
def _station_coords(room: Dict[str, Any]) -> Dict[str, Dict[str, int]]:
    """Absolute grid coords of the named spots inside a room."""
    out = {}
    for name, (dx, dy) in STATIONS.items():
        out[name] = {"x": int(room["x"]) + dx, "y": int(room["y"]) + dy}
    return out


def _desk_of(seat_id: int, plan: Dict[str, Any]) -> Optional[Dict[str, int]]:
    for floor in plan["floors"]:
        for room in floor["rooms"]:
            for slot in room["seats"]:
                if int(slot["agent_id"]) == int(seat_id):
                    return {"x": slot["x"], "y": slot["y"],
                            "floor": floor["number"], "room_id": room["id"]}
    return None


def _room_of(room_id: Optional[int],
             plan: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if room_id is None:
        return None
    for floor in plan["floors"]:
        for room in floor["rooms"]:
            if int(room["id"]) == int(room_id):
                return room
    return None


def walk_for(seat_id: int, *, plan: Dict[str, Any], room_id: Optional[int],
             event_kind: str, ts: float, now: float,
             escalation_target: Optional[Dict[str, int]] = None
             ) -> Dict[str, Any]:
    """Where this character is *now*, as a function of its event and the clock.

    This is the honest way to draw movement under C7. The server does not tell
    the renderer "this one is walking"; it tells the renderer the **point on the
    path that is true at `now`**, computed here. The renderer has no path, no
    speed and no timer — so it cannot produce motion the events did not justify.

    Consequence, and it is the whole point: freeze the event stream and advance
    the clock, and every character is standing still at a station, because that
    is where the last event left it.
    """
    desk = _desk_of(seat_id, plan)
    if desk is None:
        return {"x": None, "y": None, "walking": False, "facing": "down"}

    age = float(now) - float(ts)
    # the destination this event implies, if any
    dest: Optional[Dict[str, int]] = None
    if event_kind == "escalation":
        dest = escalation_target
    else:
        station = _EVENT_STATION.get(str(event_kind))
        if station:
            room = _room_of(room_id, plan)
            if room is not None:
                dest = _station_coords(room).get(station)

    if dest is None or age < 0:
        return {"x": desk["x"], "y": desk["y"], "walking": False,
                "facing": "down", "at": "desk"}
    if age >= WALK_S:
        return {"x": int(dest["x"]), "y": int(dest["y"]), "walking": False,
                "facing": _facing(desk, dest), "at": "station"}
    # mid-walk: the interpolated point, computed here and nowhere else
    t = age / WALK_S
    x = int(round(desk["x"] + (int(dest["x"]) - desk["x"]) * t))
    y = int(round(desk["y"] + (int(dest["y"]) - desk["y"]) * t))
    return {"x": x, "y": y, "walking": True, "facing": _facing(desk, dest),
            "at": "walking", "progress": round(t, 3)}


def _facing(frm: Dict[str, int], to: Dict[str, int]) -> str:
    dx = int(to["x"]) - int(frm["x"])
    dy = int(to["y"]) - int(frm["y"])
    if abs(dx) >= abs(dy):
        return "right" if dx >= 0 else "left"
    return "down" if dy >= 0 else "up"


# --------------------------------------------------------------------- HUD
def hud(cfg: Any, *, now: Optional[float] = None) -> Dict[str, Any]:
    """Headcount, burn, runway, and per-floor P&L.

    Every money figure here is the same fold `qubit office pnl` reads (C2), so
    the HUD cannot drift from the CLI. `burn_cents_per_day` and `runway_days`
    come from `survival`, which is the same module the daily tick uses — one
    definition, two readers.
    """
    from . import ledger as L
    from . import survival as SV

    stamp = now if now is not None else store.now()
    org = L.pnl(cfg, scope="org")

    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        headcount = int(conn.execute(
            "SELECT COUNT(*) c FROM office_agent WHERE status != 'terminated'"
        ).fetchone()["c"])
        floors = [dict(r) for r in conn.execute(
            "SELECT * FROM office_floor ORDER BY number")]
        dept_rows = [dict(r) for r in conn.execute(
            "SELECT id, slug, floor_id FROM office_department")]
        per_dept = {r["department_id"]: dict(r) for r in conn.execute(
            "SELECT * FROM office_v_department_pnl")}
    finally:
        conn.close()

    by_floor: List[Dict[str, Any]] = []
    for floor in floors:
        ids = [int(d["id"]) for d in dept_rows
               if d["floor_id"] is not None
               and int(d["floor_id"]) == int(floor["id"])]
        rev = sum(int(per_dept.get(i, {}).get("revenue_cents", 0)) for i in ids)
        cost = sum(int(per_dept.get(i, {}).get("cost_cents", 0)) for i in ids)
        by_floor.append({
            "floor_id": int(floor["id"]), "number": int(floor["number"]),
            "role": floor["role"], "departments": len(ids),
            "revenue_cents": rev, "cost_cents": cost, "net_cents": rev - cost})

    burn = _org_burn_cents_per_day(cfg, now=stamp)
    runway = (int(org["net_cents"]) / burn) if burn > 0 else None
    return {
        "headcount": headcount,
        "revenue_cents": int(org["revenue_cents"]),
        "cost_cents": int(org["cost_cents"]),
        "net_cents": int(org["net_cents"]),
        "burn_cents_per_day": round(burn, 4),
        # None, not "infinity": a number the ledger cannot support is not a
        # number, and the HUD must be able to say so.
        "runway_days": (round(runway, 2) if runway is not None else None),
        "runway_is_derived": runway is not None,
        "floors": by_floor,
        "survival": {"floor_days": SV.SURVIVAL_FLOOR_DAYS},
        "pnl_source": "office_v_org_pnl (the same view `qubit office pnl` reads)",
    }


def _org_burn_cents_per_day(cfg: Any, *, now: Optional[float] = None) -> float:
    """Metered inference spend per day — the same window `survival` uses (D16)."""
    from . import survival as SV

    end = now if now is not None else store.now()
    start = end - SV.BURN_WINDOW_DAYS * 86400.0
    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        row = conn.execute(
            "SELECT COALESCE(SUM(amount_cents),0) c FROM office_ledger"
            " WHERE unmetered=0 AND kind='inference' AND ts >= ? AND ts <= ?",
            (start, end)).fetchone()
        total = int(row["c"])
    finally:
        conn.close()
    return total / SV.BURN_WINDOW_DAYS


# ------------------------------------------------------------------ history
def history(cfg: Any, *, days: int = 30, now: Optional[float] = None
            ) -> Dict[str, Any]:
    """A daily series for the charts. Derived, like every other money figure.

    Per-day revenue, cost and net over the window, from the ledger by
    `GROUP BY`. A day with no rows is reported as a zero day rather than
    omitted — a chart that skips empty days draws a company that was never
    quiet, and quiet days are the ones worth seeing.
    """
    end = now if now is not None else store.now()
    days = max(1, min(int(days), 180))
    start = end - days * 86400.0
    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        rows = conn.execute(
            "SELECT CAST(ts / 86400 AS INTEGER) AS day,"
            " COALESCE(SUM(CASE WHEN unmetered=0 AND kind='revenue'"
            "   THEN amount_cents ELSE 0 END),0) AS revenue,"
            " COALESCE(SUM(CASE WHEN unmetered=0 AND"
            "   kind IN ('inference','fees','capex') THEN amount_cents"
            "   ELSE 0 END),0) AS cost"
            " FROM office_ledger WHERE ts >= ? GROUP BY day ORDER BY day",
            (start,)).fetchall()
    finally:
        conn.close()

    got = {int(r["day"]): (int(r["revenue"]), int(r["cost"])) for r in rows}
    first = int(start // 86400)
    series = []
    for d in range(first, first + days):
        rev, cost = got.get(d, (0, 0))
        series.append({"day": d, "date": time.strftime(
            "%Y-%m-%d", time.localtime(d * 86400)),
            "revenue_cents": rev, "cost_cents": cost, "net_cents": rev - cost})
    return {"state": "OK", "days": days, "series": series,
            "totals": {"revenue_cents": sum(x["revenue_cents"] for x in series),
                       "cost_cents": sum(x["cost_cents"] for x in series),
                       "net_cents": sum(x["net_cents"] for x in series)}}


# ----------------------------------------------------------------- activity
_EVENT_SENTENCE = {
    "run_started": "started a turn",
    "run_settled": "finished a turn",
    "run_unpriced": "finished a turn with no cost reported",
    "run_failed": "had a turn fail",
    "task_done": "completed a task",
    "task_unverified": "claimed a task with nothing to check it",
    "consent_denied": "was refused a permission",
    "tool_call": "used a tool",
    "note_written": "wrote a note",
    "memory_written": "wrote a memory",
    "skill_used": "used a skill",
    "mcp_call": "called an MCP server",
    "ledger_write": "moved money",
    "escalation": "raised an escalation",
    "meeting": "went into a meeting",
}


def activity(cfg: Any, *, limit: int = 40) -> Dict[str, Any]:
    """The event feed, as sentences. Same rows the animation is drawn from."""
    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        rows = [dict(r) for r in conn.execute(
            "SELECT e.id, e.ts, e.kind, e.agent_id, a.slug AS agent_slug"
            " FROM office_event e LEFT JOIN office_agent a ON a.id=e.agent_id"
            " ORDER BY e.id DESC LIMIT ?", (max(1, min(int(limit), 200)),))]
    finally:
        conn.close()
    out = []
    for r in rows:
        who = r["agent_slug"] or "the org"
        what = _EVENT_SENTENCE.get(r["kind"], r["kind"])
        out.append({"id": int(r["id"]), "ts": float(r["ts"]),
                    "kind": r["kind"], "agent_id": r["agent_id"],
                    "agent_slug": r["agent_slug"],
                    "text": f"{who} {what}"})
    return {"state": "OK", "count": len(out), "activity": out}


# ------------------------------------------------------------------ preview
#: A PREVIEW office: what the page draws when the org has not started, so the
#: UI can be explored with no agents. It is **never** served as live data.
#: `state()` returns it only under the explicit `preview=True` argument, and the
#: payload carries `preview: True` plus a legend. D14 forbids showing work that
#: is not happening; it does not forbid showing a labelled drawing of what the
#: office will look like, and aphae's "live data or built-in demo data" is the
#: same pattern. What would be a lie is a preview nobody can identify.
PREVIEW_LEGEND = ("PREVIEW — a drawing of the office, not live data. No agent "
                  "is running and no number here came from the ledger.")


def preview_state(cfg: Any) -> Dict[str, Any]:
    """A small, obviously-fake office for the empty page.

    Deterministic, so the preview does not shuffle between reloads, and
    deliberately named after roles rather than people, so nobody mistakes a
    preview character for a seat that exists.
    """
    rooms = [{"slug": "exec", "kind": "boardroom"},
             {"slug": "desks", "kind": "work"},
             {"slug": "desks-2", "kind": "work"},
             {"slug": "review", "kind": "review"},
             {"slug": "lab", "kind": "work"}]
    floors = []
    for number, role in ((1, "executive"), (2, "director"), (3, "operations")):
        floor_rooms = []
        for i, room in enumerate(rooms):
            if i >= len(rooms):
                break
            col, row = i % ROOM_COLS, (i // ROOM_COLS) % ROOM_ROWS
            floor_rooms.append({
                "id": number * 10 + i, "slug": room["slug"],
                "kind": room["kind"], "department_id": None,
                "x": col * (ROOM_W + 1), "y": row * (ROOM_H + 1),
                "w": ROOM_W, "h": ROOM_H, "seats": []})
        floors.append({"id": number, "number": number,
                       "name": ["Executive", "Directorate", "Operations"][number - 1],
                       "role": role, "theme": "", "z": number * FLOOR_HEIGHT_UNITS,
                       "rooms": floor_rooms})
    roles = ["CEO", "Director", "Manager", "Worker", "Analyst"]
    characters = []
    for i, role in enumerate(roles):
        room = floors[0]["rooms"][i % len(floors[0]["rooms"])]
        characters.append({
            "agent_id": -(i + 1), "slug": f"preview-{role.lower()}",
            "seat_type": role.lower(), "department_id": None,
            "supervisor_id": None, "seated": True,
            "position": {"floor": 1, "floor_role": "executive",
                         "room": room["slug"], "room_id": room["id"],
                         "x": int(room["x"]) + 1 + (i % 3) * 2,
                         "y": int(room["y"]) + 1, "slot": i},
            "animation": "idle", "last_event": None, "stalled": False,
            "facing": "down", "walking": False, "at": "desk"})
    plan = {"schema": FLOORPLAN_SCHEMA,
            "grid": {"cols": ROOM_COLS * (ROOM_W + 1),
                     "rows": ROOM_ROWS * (ROOM_H + 1), "tile": 1},
            "floors": floors}
    return {"preview": True, "preview_legend": PREVIEW_LEGEND,
            "characters": characters, "floorplan": plan}


# ------------------------------------------------------------------- state
def state(cfg: Any, *, now: Optional[float] = None,
          preview: bool = False) -> Dict[str, Any]:
    """Everything the tower renders. Reads only; writes nothing (C4).

    `preview=True` fills an *empty* org with a labelled drawing so the page is
    explorable with nothing running. It is off by default and it never attaches
    itself to live rows — a preview mixed into real data is the lie D14 forbids.
    """
    stamp = now if now is not None else store.now()
    plan = floorplan(cfg)
    h = hud(cfg, now=stamp)

    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        rows = [dict(r) for r in conn.execute(
            "SELECT id, kind, ts, agent_id FROM office_event"
            " ORDER BY id DESC LIMIT 500")]
        ledger_rows = int(conn.execute(
            "SELECT COUNT(*) c FROM office_ledger").fetchone()["c"])
    finally:
        conn.close()

    activity = activity_from_events(rows, now=stamp)

    # place every live seat at its real desk, and give it the animation its own
    # events justify and nothing more
    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        seats = [dict(r) for r in conn.execute(
            "SELECT id, slug, seat_type, department_id, room_id, status,"
            " supervisor_id FROM office_agent WHERE status != 'terminated'"
            " ORDER BY id")]
        placed = {}
        for floor in plan["floors"]:
            for room in floor["rooms"]:
                for slot in room["seats"]:
                    placed[int(slot["agent_id"])] = {
                        "floor": floor["number"], "floor_role": floor["role"],
                        "room": room["slug"], "room_id": room["id"],
                        "x": slot["x"], "y": slot["y"], "slot": slot["slot"]}
    finally:
        conn.close()

    characters = []
    for seat in seats:
        aid = int(seat["id"])
        pos = placed.get(aid)
        act = activity.get(aid)
        # Where this character is *now* is computed here, from its event and the
        # clock — never sent to the renderer as "it is walking". The renderer
        # receives one point and no path, so it cannot invent motion.
        walk = walk_for(aid, plan=plan, room_id=seat["room_id"],
                        event_kind=(act or {}).get("kind", ""),
                        ts=(act or {}).get("ts", 0.0), now=stamp,
                        escalation_target=_escalation_target(
                            seat, seats, placed))
        position = dict(pos or {"floor": None, "room": None, "room_id": None,
                                "slot": None, "x": None, "y": None})
        if walk.get("x") is not None:
            position["x"] = walk["x"]
            position["y"] = walk["y"]
        # `progress` is where along the desk->station path the character is, and
        # it is the ONLY thing the renderer may use to bob a walking figure (C7:
        # motion comes from the payload, never from a client clock). It was being
        # computed by `walk_for` and then dropped here, so the renderer's bob was
        # keyed on a field that never arrived and drew nothing. Carry it only
        # while a walk is actually in progress: a stale `progress` on a stopped
        # character would be a leftover value pretending to be live.
        if walk.get("walking") and walk.get("progress") is not None:
            position["progress"] = walk["progress"]
        # a walking character is drawn walking; a stopped one shows whatever its
        # event says it is doing, which is `idle` when the event is stale
        animation = (act or {}).get("animation", "idle")
        if walk.get("walking"):
            animation = "walking"
        characters.append({
            "agent_id": aid, "slug": seat["slug"], "seat_type": seat["seat_type"],
            "department_id": seat["department_id"],
            "supervisor_id": seat["supervisor_id"],
            "seated": pos is not None,
            "position": position,
            "facing": walk.get("facing", "down"),
            "walking": bool(walk.get("walking")),
            "at": walk.get("at", "desk"),
            "animation": animation,
            "last_event": ({"kind": act["kind"], "age_s": act["age_s"]}
                           if act else None),
            "stalled": bool((act or {}).get("stalled")),
        })

    empty = (not seats and not plan["floors"] and ledger_rows == 0)
    feed = activity_feed(cfg, limit=60)
    hist = history(cfg, now=stamp)
    out = {
        "schema": SCHEMA,
        "now": stamp,
        "active_window_s": ACTIVE_WINDOW_S,
        "walk_s": WALK_S,
        "empty": empty,
        "empty_reason": ("no seats, no floors and no ledger rows: this org has "
                         "not started" if empty else ""),
        "hud": h,
        "floorplan": plan,
        "characters": characters,
        "activity": feed["activity"],
        "history": hist["series"],
        "history_totals": hist["totals"],
        "grid": plan["grid"],
        # The renderer is told, explicitly, what the rule is. A plugin that
        # animates from a timer is not misreading the payload.
        "motion_rule": ("animation is derived from office_event rows only; no "
                        "event within active_window_s means `idle` (C7)"),
        "read_only": True,
        "preview": False,
    }
    if preview and empty:
        # The page gets something to draw only when the org has not started, and
        # only when it is asked for, and it is labelled. Never mixed with live
        # rows: `characters` above is empty in exactly this case.
        fake = preview_state(cfg)
        out["preview"] = True
        out["preview_legend"] = fake["preview_legend"]
        out["floorplan"] = fake["floorplan"]
        out["characters"] = fake["characters"]
        out["grid"] = fake["floorplan"]["grid"]
    return out


def _escalation_target(seat: Dict[str, Any], seats: List[Dict[str, Any]],
                       placed: Dict[int, Dict[str, Any]]
                       ) -> Optional[Dict[str, int]]:
    """Where an `escalation` event sends this character: its manager's desk."""
    sup = seat.get("supervisor_id")
    if sup is None:
        return None
    target = placed.get(int(sup))
    if target is None:
        return None
    return {"x": int(target["x"]), "y": int(target["y"])}


def activity_feed(cfg: Any, *, limit: int = 40) -> Dict[str, Any]:
    return activity(cfg, limit=limit)


def motion_states(cfg: Any, *, now: Optional[float] = None) -> Dict[str, Any]:
    """Just the motion, for the phantom-motion check (`tests/test_office_tower_...`)."""
    out = state(cfg, now=now)
    moving = [c for c in out["characters"] if c["animation"] in MOTION]
    return {"state": "OK", "total": len(out["characters"]),
            "moving": len(moving),
            "animations": {c["slug"]: c["animation"] for c in out["characters"]},
            "moving_slugs": [c["slug"] for c in moving],
            "empty": out["empty"]}
