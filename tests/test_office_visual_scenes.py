"""Office tower — SCENE-level visual tests: is the drawing CORRECT for the scene?

Why this file exists (and how it differs from test_office_tower_visual.py)
--------------------------------------------------------------------------
`tests/test_office_tower_visual.py` proves the renderer's drawing code EXECUTES
against a fake 2D context (text is not pixels). `tests/test_office_tower_plugin.py`
proves the file's text. Both are necessary and both are, on their own, a trap:
they check that the renderer does *something*, self-consistently, with whatever
it was handed. They cannot see that the *scene* is drawn wrong.

This file asserts what a viewer would see. Each test builds a realistic tower
payload in the REAL schema (mirrored from `voicebridge/office/tower.py::state`
and `floorplan`, including `z = number * FLOOR_HEIGHT_UNITS` and the
`room_slot_x` / `room_slot_y` desk grid) and asserts the DEPTH-ORDER, the BOB,
the COLOUR and the MARKER of that scene. A scene test fails when the picture is
wrong even though every unit still "returns" the right shape.

The rules this file encodes ("depthKey", from `docs/game-office/26-OCCLUSION-AND-DEPTH.md`)
--------------------------------------------------------------------------------------
    k(item) = (x + y) * TILE_H/2 - z * DECK_Z      # the screen y of the FEET
    larger k  ==  nearer the camera  ==  painted LATER
    on an equal k: desk (2) paints BEFORE agent (3)  -- the figure covers its desk

Scene 2 (agent behind a desk) is the case the old four-pass renderer got
backwards: every character was painted after every desk, so an agent behind a
desk painted over it. Scene 1 ((x,y) behind (x+1,y+1)) is the same defect
between two characters.

How the renderer is executed
----------------------------
IDENTICAL method to `tests/test_office_tower_visual.py`: the plugin IIFE cannot
be driven by a stub `hooks` (it needs React/DOM to reach `redraw()`), so the
final `register(...)` line is rewritten to export the drawing functions, and the
whole file text is `eval`ed in `node` against a hand-written fake 2D context
that records every draw call and the `fillStyle`/`alpha` in force. That file is
NOT modified — the harness below is a copy, so the two suites stay independent
(a change to the older harness cannot silently weaken this one). The one
transform is that tail substitution; no drawing function is touched.

Hermetic: reads one file, runs `node` on it, no network, no browser, no provider.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest  # noqa: E402

from _office_node import run_node  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
ENTRY = (REPO / "frontend" / "qubit-app" / "plugins" / "qubit-office"
         / "qubit-office.js")

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node is not on PATH")

# The IIFE tail rewritten to expose the internals. Kept literal so a renamed
# registration fails loudly instead of silently skipping.
REGISTER_LINE = 'window.__QUBIT_PLUGINS__.register("qubit-office", Office);'

EXPOSED = (
    "drawTower, drawAgent, drawShadow, drawMarker, drawMinimap, drawPnl, "
    "iso, extent, fitToBuilding, clampPan, "
    "money, moneyRate, seatColour, shade, tint, deckTint, "
    "PALETTE, ANIM_COLOR, MOTION, TYPE_HUE, "
    "TILE_W, TILE_H, DECK_Z, CHAR_FLOOR_ZOOM, MIN_ZOOM, MAX_ZOOM, Office"
).split(", ")

# --------------------------------------------------------------- the harness
HARNESS = r"""
var __exposed = %(exposed)s;
var captured = {};
globalThis.window = {
  __QUBIT_PLUGIN_SDK__: {
    React: {createElement: function () { return {type: arguments[0]}; }},
    hooks: {
      useState: function (v) { return [v, function () {}]; },
      useEffect: function () {}, useRef: function (v) { return {current: v}; },
      useCallback: function (f) { return f; }
    },
    fetchJSON: function () { return Promise.resolve({}); }
  },
  __QUBIT_PLUGINS__: {register: function (n, f) { captured.n = n; captured.f = f; }},
  location: {search: ""}
};

/* Records geometry and the fillStyle/alpha in force at each call. */
function FakeCtx() {
  this.ops = [];
  this.fillStyle = "#000000";
  this.strokeStyle = "#000000";
  this.globalAlpha = 1;
  this.lineWidth = 1;
  this.font = "";
  this.textAlign = "";
  this._path = [];
}
FakeCtx.prototype.save = function () { this.ops.push({op: "save"}); };
FakeCtx.prototype.restore = function () { this.ops.push({op: "restore"}); };
FakeCtx.prototype.clearRect = function (x, y, w, h) {
  this.ops.push({op: "clearRect", x: x, y: y, w: w, h: h});
};
FakeCtx.prototype.beginPath = function () { this._path = []; };
FakeCtx.prototype.closePath = function () { this._path.push(["closePath"]); };
FakeCtx.prototype.moveTo = function (x, y) { this._path.push(["moveTo", x, y]); };
FakeCtx.prototype.lineTo = function (x, y) { this._path.push(["lineTo", x, y]); };
FakeCtx.prototype.fillRect = function (x, y, w, h) {
  this.ops.push({op: "fillRect", x: x, y: y, w: w, h: h,
                 fillStyle: this.fillStyle, alpha: this.globalAlpha});
};
FakeCtx.prototype.rect = function (x, y, w, h) { this._path.push(["rect", x, y, w, h]); };
FakeCtx.prototype.roundRect = function (x, y, w, h, r) {
  this._path.push(["roundRect", x, y, w, h, r]);
};
FakeCtx.prototype.arc = function (x, y, r, a, b) { this._path.push(["arc", x, y, r]); };
FakeCtx.prototype.ellipse = function (x, y, rx, ry, rot, a, b) {
  this._path.push(["ellipse", x, y, rx, ry]);
};
FakeCtx.prototype.fill = function () {
  this.ops.push({op: "fill", fillStyle: this.fillStyle,
                 alpha: this.globalAlpha, path: this._path.slice()});
};
FakeCtx.prototype.stroke = function () {
  this.ops.push({op: "stroke", strokeStyle: this.strokeStyle,
                 lineWidth: this.lineWidth, path: this._path.slice()});
};
FakeCtx.prototype.fillText = function (t, x, y) {
  this.ops.push({op: "fillText", text: String(t), x: x, y: y,
                 fillStyle: this.fillStyle, font: this.font});
};

function fakeCanvas(w, h) {
  var o = {width: w, height: h, _ctx: new FakeCtx()};
  o.getContext = function () { return o._ctx; };
  return o;
}

/* ---- op classifiers. drawAgent draws a roundRect body + an arc head + a
 * fillRect state band; drawShadow an ellipse. Nothing else in the file draws a
 * roundRect or an arc, and no other fillRect uses the desk colours. ------- */
function fills(ctx) { return ctx.ops.filter(function (o) { return o.op === "fill"; }); }
function rects(ctx) { return ctx.ops.filter(function (o) { return o.op === "fillRect"; }); }
function fillsWith(ctx, prim) {
  return fills(ctx).filter(function (o) {
    return o.path.some(function (p) { return p[0] === prim; });
  });
}
function bodyFills(ctx) { return fillsWith(ctx, "roundRect"); }
function headFills(ctx) { return fillsWith(ctx, "arc"); }
function shadowFills(ctx) { return fillsWith(ctx, "ellipse"); }
function texts(ctx) {
  return ctx.ops.filter(function (o) { return o.op === "fillText"; })
                .map(function (o) { return o.text; });
}
/* The op index of the i-th body fill, so two figures' paint order is comparable. */
function bodyIndex(ctx, i) {
  var seen = 0;
  for (var n = 0; n < ctx.ops.length; n++) {
    var o = ctx.ops[n];
    if (o.op === "fill" && o.path.some(function (p) { return p[0] === "roundRect"; })) {
      if (seen === i) return n;
      seen++;
    }
  }
  return -1;
}
/* Screen-x of the i-th body, to identify a figure independently of draw order. */
function bodyX(ctx, i) {
  var b = bodyFills(ctx);
  if (i >= b.length) return null;
  var pr = b[i].path.find(function (p) { return p[0] === "roundRect"; });
  return pr[1];
}
/* Desk fills, split by occupancy colour, with their op index. */
function deskOps(ctx) {
  var out = [];
  ctx.ops.forEach(function (o, n) {
    if (o.op === "fillRect" &&
        (o.fillStyle === "#3d4b60" || o.fillStyle === "#2a3443")) {
      out.push({index: n, x: o.x, y: o.y, style: o.fillStyle, alpha: o.alpha});
    }
  });
  return out;
}

/* ---- drawing entry points -------------------------------------------------
 * Payloads are built in Python and passed in as JSON, so there is exactly one
 * definition of the payload shape in this file (the Python side). ------- */
function drawTowerF(payloadObj) {
  var ctx = new FakeCtx();
  var probe = X.drawTower(ctx, payloadObj,
                          {cam: {cx: 0, cy: 0, zoom: 1, w: 880, h: 500},
                           floor: null});
  return {ctx: ctx, probe: probe};
}
"""


# ------------------------------------------------------- payload builders
# Mirrors voicebridge/office/tower.py::floorplan / state. `z = number * 3`,
# desks on the room_slot_x/room_slot_y grid, characters carry position.progress
# ONLY while walking (tower.py:612-613).
FLOOR_HEIGHT_UNITS = 3
ROOM_W, ROOM_H = 6, 4


def slot_x(col: int, n: int) -> int:
    """tower.py::room_slot_x — the room-local desk column."""
    return col * (ROOM_W + 1) + 1 + (n % 3) * 2


def slot_y(row: int, n: int) -> int:
    """tower.py::room_slot_y — every third desk starts a new row."""
    return row * (ROOM_H + 1) + 1 + (n // 3) * 2


def make_seat(slug: str, x: float, y: float, slot: int = 0,
              agent_id: str | None = None) -> dict:
    return {"slot": slot, "agent_id": agent_id if agent_id is not None
            else "ag-" + slug, "slug": slug, "x": x, "y": y}


def make_room(slug: str, x: int, y: int, w: int = ROOM_W, h: int = ROOM_H,
              seats: list[dict] | None = None, room_id: int = 1) -> dict:
    return {"id": room_id, "slug": slug, "kind": "open", "department_id": 1,
            "x": x, "y": y, "w": w, "h": h, "seats": seats or []}


def make_floor(number: int, role: str, rooms: list[dict],
               z: int | None = None) -> dict:
    """A floor exactly as tower.py builds one: `z = number * FLOOR_HEIGHT_UNITS`."""
    return {"id": number, "number": number, "name": "level %d" % number,
            "role": role, "theme": role,
            "z": FLOOR_HEIGHT_UNITS * number if z is None else z,
            "rooms": rooms}


def make_character(slug: str, floor_number: int, anim: str = "idle",
                   x: float = 1.5, y: float = 1.5, **extra) -> dict:
    """A character in the real shape. `progress` is only ever passed explicitly."""
    c = {
        "agent_id": "ag-" + slug, "slug": slug, "seat_type": "worker",
        "department_id": 1, "supervisor_id": None, "seated": True,
        "position": {"floor": floor_number, "room": "open", "room_id": 1,
                     "slot": 0, "x": x, "y": y},
        "facing": "down", "walking": False, "at": "desk", "animation": anim,
        "last_event": None, "stalled": False,
    }
    for k, v in extra.items():
        if k == "position":
            c["position"].update(v)
        else:
            c[k] = v
    return c


def make_payload(floors: list[dict], chars: list[dict],
                 empty: bool = False) -> dict:
    return {
        "schema": "qubit-office-tower/1", "now": 1700000000.0,
        "active_window_s": 120.0, "walk_s": 6.0,
        "empty": empty,
        "empty_reason": ("no seats, no floors and no ledger rows: this org has "
                         "not started" if empty else ""),
        "hud": {"headcount": len(chars), "revenue_cents": 0, "cost_cents": 0,
                "net_cents": 0, "burn_cents_per_day": 0, "runway_days": None,
                "survival": {"floor_days": None}, "floors": []},
        "floorplan": {"schema": "qubit-office-floorplan/1",
                      "grid": {"cols": 21, "rows": 10, "tile": 1},
                      "floors": floors},
        "characters": chars, "activity": [], "history": [],
        "history_totals": {"revenue_cents": 0, "cost_cents": 0, "net_cents": 0},
        "grid": {"cols": 21, "rows": 10, "tile": 1},
        "motion_rule": ("animation is derived from office_event rows only; no "
                        "event within active_window_s means `idle` (C7)"),
        "read_only": True, "preview": False,
    }


def _source_with_exposed_internals() -> str:
    src = ENTRY.read_text()
    assert REGISTER_LINE in src, (
        "the scene harness reads %s and rewrites its final register line (%r). "
        "It is gone, so this file cannot reach the drawing functions. Update "
        "REGISTER_LINE to the new registration call."
        % (ENTRY.name, REGISTER_LINE))
    export = "globalThis.__X = {" + ", ".join(
        "%s: %s" % (name, name) for name in EXPOSED) + "};"
    return src.replace(REGISTER_LINE, export)


_CACHE: dict[str, str] = {}


def _js_result(program: str, src_override: str | None = None):
    """Run `program` in node after the harness; return parsed JSON `__RESULT`."""
    if src_override is not None:
        src = src_override
    else:
        if not _CACHE:
            _CACHE["src"] = _source_with_exposed_internals()
        src = _CACHE["src"]
    full = (
        HARNESS % {"exposed": json.dumps(EXPOSED)}
        + "\nvar __SRC = " + json.dumps(src) + ";\n"
        + "(0, eval)(__SRC);\n"
        + "var X = __X;\n"
        + program
        + "\nif (typeof __RESULT !== 'undefined') "
          "{ process.stdout.write(JSON.stringify(__RESULT)); }\n"
    )
    proc = subprocess.run([NODE], input=full, capture_output=True, text=True,
                          timeout=60)
    if proc.returncode != 0:
        raise AssertionError("node failed:\nSTDOUT:\n%s\nSTDERR:\n%s"
                             % (proc.stdout, proc.stderr))
    out = proc.stdout.strip()
    assert out, ("node produced no JSON result; the program did not reach "
                 "__RESULT. STDOUT=%r STDERR=%r" % (proc.stdout, proc.stderr))
    return json.loads(out)


def _draw(payload_obj: dict, extra_js: str) -> dict:
    """Draw `payload_obj` with the real `drawTower`; `extra_js` sets __RESULT.

    It may use `out` ({ctx, probe}), `c` (the ctx) and the classifier helpers.
    """
    return _js_result(
        "var out = drawTowerF(%s); var c = out.ctx;\n" % json.dumps(payload_obj)
        + extra_js)


# =========================================================== sanity
def test_the_scene_harness_can_execute_the_renderer_at_all():
    """If this fails every other test in this file is meaningless."""
    res = _js_result(
        "__RESULT = {hasDrawTower: typeof X.drawTower, "
        "tileH: X.TILE_H, deckZ: X.DECK_Z};")
    assert res["hasDrawTower"] == "function"
    # The depth rule this file asserts against is the projection's own numbers.
    assert res["tileH"] == 17 and res["deckZ"] == 26, res


# =============================== SCENE 1 — two seats, one behind the other
def test_scene_one_seat_behind_another_paints_the_far_one_first():
    """THE occlusion case: in 2:1 iso, cell (x, y) is BEHIND cell (x+1, y+1).

    Two agents on one floor, one at cell (1, 1) (far) and one at cell (3, 3)
    (near). The near agent must be painted AFTER the far one, or the far figure
    is drawn over a person standing in front of it — the "smear" the owner is
    looking at. The figures are identified by screen-x (independent of draw
    order), so this cannot pass by drawing them in the wrong slots.

    Defect that makes it fail: any renderer that paints characters in array /
    insertion order instead of by depth. With the far agent listed FIRST the bug
    is invisible; this payload lists the NEAR agent first, which is exactly the
    order a naive pass preserves and which draws the far figure on top.
    """
    far = make_character("far", 1, "idle", x=1, y=1)
    near = make_character("near", 1, "idle", x=3, y=3)
    floors = [make_floor(1, "operations", [make_room("open", 0, 0)])]
    # NEAR is first in the array: the naive renderer draws near, then far on top.
    v = make_payload(floors, [near, far])
    r = _draw(v, (
        "var cam = {cx: 0, cy: 0, zoom: 1, w: 880, h: 500};"
        "/* identify a figure by its FOOT LINE (at.y), independent of paint order */"
        "var farFoot = X.iso(1, 1, 3, cam).y;"
        "var nearFoot = X.iso(3, 3, 3, cam).y;"
        "var farIdx = -1, nearIdx = -1;"
        "var bodies = bodyFills(c);"
        "for (var i = 0; i < bodies.length; i++) {"
        "  var pr = bodies[i].path.find(function (p) "
        "    { return p[0] === 'roundRect'; });"
        # a figure's foot line = top(y) + h - h*FEET_ANCHOR; h = 15 at zoom 1
        "  var foot = pr[2] + 15 - 15 * 0.24;"
        "  if (Math.abs(foot - farFoot) < 3) farIdx = bodyIndex(c, i);"
        "  if (Math.abs(foot - nearFoot) < 3) nearIdx = bodyIndex(c, i);"
        "}"
        "__RESULT = {bodies: bodies.length, near: nearIdx, far: farIdx};"))
    assert r["bodies"] == 2, "both agents must be drawn: %r" % r
    assert r["far"] != -1 and r["near"] != -1, (
        "could not locate both figures on screen: %r" % r)
    assert r["near"] > r["far"], (
        "the NEAR agent (cell 3,3) was painted before/at the FAR agent (cell "
        "1,1): near=%r far=%r. In 2:1 iso the far figure must paint first, or "
        "the near one is occluded — this is D1." % (r["near"], r["far"]))


# ============================ SCENE 2 — an agent standing behind a desk
def test_scene_agent_behind_a_desk_paints_under_the_nearer_desk():
    """The desk a figure stands at is under it; a desk NEARER the camera is over it.

    Two desks in one room, at cells (1,1) (far) and (3,3) (near). The agent
    stands at the FAR desk, cell (1,1). Correct painting, by the depthKey rule
    (larger `(x+y)*TILE_H/2 - z*DECK_Z` paints later):

      * the FAR desk  (key at 1.5+1.5)  -> paints FIRST
      * the AGENT     (key at 1+1)      -> paints after its own desk, so it sits
                                           on it rather than behind it
      * the NEAR desk (key at 3.5+3.5)  -> paints LAST, over the agent's lower body

    Asserted by op index: farDesk < agentBody < nearDesk.

    Defect that makes it fail: the old "characters are a separate pass on top of
    every deck" order, where the agent paints after BOTH desks and floats over
    the nearer desk. This is the exact case §1 of 26-OCCLUSION-AND-DEPTH.md calls
    "the figure floats over the desk in front of it".
    """
    seats = [make_seat("far-desk", slot_x(0, 0), slot_y(0, 0), 0),
             make_seat("near-desk", slot_x(0, 5), slot_y(0, 5), 5)]
    floors = [make_floor(1, "operations", [make_room("open", 0, 0, seats=seats)])]
    # The agent stands at the FAR desk's own cell (1, 1).
    v = make_payload(floors, [make_character("sit", 1, "idle", x=1, y=1)])
    # Desks are located by BOTH screen x and y. Locating by x alone is
    # ambiguous: `iso(1.5,1.5)` and `iso(3.5,3.5)` share a screen x (both lie on
    # the x==y diagonal), so an x-only filter matches whichever desk is drawn
    # first and cannot tell the two apart. The seats' own cells are
    # `(slot_x, slot_y)` = (1,1) and (5,3), drawn at `iso(cell+0.5)`.
    r = _draw(v, (
        "var cam = {cx: 0, cy: 0, zoom: 1, w: 880, h: 500};"
        "function deskAt(cx, cy) {"
        "  var p = X.iso(cx + 0.5, cy + 0.5, 3, cam);"
        "  return deskOps(c).filter(function (o) {"
        "    return Math.abs(o.x - (p.x - 4)) < 1 &&"
        "           Math.abs(o.y - (p.y - 4)) < 1; })[0];"
        "}"
        "var farD = deskAt(1, 1);"
        "var nearD = deskAt(5, 3);"
        "var agent = bodyIndex(c, 0);"
        "__RESULT = {desks: deskOps(c).length,"
        "  farDesk: farD ? farD.index : -1,"
        "  nearDesk: nearD ? nearD.index : -1,"
        "  agent: agent};"))
    assert r["desks"] == 2, "both desks must be drawn: %r" % r
    assert r["farDesk"] != -1 and r["nearDesk"] != -1, (
        "could not locate both desks: %r" % r)
    assert r["farDesk"] < r["agent"], (
        "the agent at cell (1,1) painted BEFORE its own desk at cell (1.5,1.5) "
        "— the desk covers the person sitting at it: farDesk=%r agent=%r"
        % (r["farDesk"], r["agent"]))
    assert r["agent"] < r["nearDesk"], (
        "the agent standing BEHIND the nearer desk (cell 5,3) painted after "
        "it, so it floats over a desk in front of it: agent=%r nearDesk=%r. "
        "Larger key must paint later." % (r["agent"], r["nearDesk"]))


# ================================ SCENE 3 — a three-floor tower
def test_scene_three_floors_the_z_term_lifts_upper_floors_off_lower_ones():
    """Floors stack: a higher deck (larger z) has a SMALLER key, so it paints first.

    `z = number * FLOOR_HEIGHT_UNITS` (tower.py:161) and the key subtracts
    `z * DECK_Z`, so a floor 3 character (`z=9`, key reduced by 234) sorts
    strictly before a floor 1 character at the same column — the sort key
    genuinely handles the z term, it is not an array-order accident.

    Proven with an OVERLAPPING case: a floor 3 character at (1,1) and a floor 1
    character at (1,1) project to the SAME screen x (the z term moves y only),
    so if the renderer ignored z they would be ambiguous by insertion order. The
    floors are listed [3, 2, 1] in the array, and the floor 1 character is listed
    BEFORE the floor 3 one in `characters` — so neither array order can produce
    the correct result. Only the z term can.
    """
    ch3 = make_character("top", 3, "idle", x=1, y=1)
    ch1 = make_character("base", 1, "idle", x=1, y=1)
    floors = [make_floor(3, "operations", [make_room("r3", 0, 0, room_id=3)]),
              make_floor(2, "director", [make_room("r2", 0, 0, room_id=2)]),
              make_floor(1, "executive", [make_room("r1", 0, 0, room_id=1)])]
    # floor-1 character FIRST in the array; floor-3 second. Array order says
    # base paints first (wrong); only the z-aware key says top paints first.
    v = make_payload(floors, [ch1, ch3])
    r = _draw(v, (
        "var cam = {cx: 0, cy: 0, zoom: 1, w: 880, h: 500};"
        "var topY = Math.round(X.iso(1, 1, 9, cam).y);"
        "var baseY = Math.round(X.iso(1, 1, 3, cam).y);"
        "var bodies = bodyFills(c);"
        "var ix = function (wantY) { for (var i = 0; i < bodies.length; i++) {"
        "  var pr = bodies[i].path.find(function (p) "
        "    { return p[0] === 'roundRect'; });"
        "  if (Math.abs((pr[2] + 0.5) - wantY) < 12) return bodyIndex(c, i); }"
        "  return -1; };"
        "/* body top = footY - h + h*0.24 ; h = 15 at zoom 1 -> footY - 11.4 */"
        "var topFoot = X.iso(1, 1, 9, cam).y, baseFoot = X.iso(1, 1, 3, cam).y;"
        "var topIdx = -1, baseIdx = -1;"
        "for (var i = 0; i < bodies.length; i++) {"
        "  var pr = bodies[i].path.find(function (p) "
        "    { return p[0] === 'roundRect'; });"
        "  var foot = pr[2] + 15 - 15*0.24;"
        "  if (Math.abs(foot - topFoot) < 6) topIdx = bodyIndex(c, i);"
        "  if (Math.abs(foot - baseFoot) < 6) baseIdx = bodyIndex(c, i);"
        "}"
        "__RESULT = {bodies: bodies.length, top: topIdx, base: baseIdx,"
        "  sameScreenX: topY === topY};"))
    assert r["bodies"] == 2, "both floors' characters must be drawn: %r" % r
    assert r["top"] != -1 and r["base"] != -1, (
        "could not locate both characters by foot line: %r" % r)
    assert r["top"] < r["base"], (
        "the floor-3 character (z=9) was NOT painted before the floor-1 "
        "character (z=3): top=%r base=%r. The sort key must keep the z term — "
        "smaller (x+y)*TILE_H/2 - z*DECK_Z paints first." % (r["top"], r["base"]))


# ================================ SCENE 4 — a walking agent mid-path
def test_scene_bob_only_where_the_payload_reports_a_live_walk():
    """A figure bobs only when the payload says walking AND carries `progress`.

    `position.progress` exists only while a walk is in progress (tower.py:612:
    "a stale `progress` on a stopped character would be a leftover value
    pretending to be live"). Three cases, all asserted on the body's top edge:

      (a) walking=True  + progress present -> the body is lifted (bob applied)
      (b) walking=False + progress present -> NO bob (stale value ignored)
      (c) walking=True  + progress ABSENT  -> NO bob (missing value not invented)
    """
    def body_top(walking, progress):
        pos = {"floor": 1, "room": "open", "room_id": 1, "slot": 0,
               "x": 1, "y": 1}
        if progress is not None:
            pos["progress"] = progress
        v = make_payload(
            [make_floor(1, "operations", [make_room("open", 0, 0)])],
            [make_character("walker", 1, "walking", walking=walking,
                            position=pos)])
        return _draw(v, (
            "__RESULT = {top: bodyFills(c)[0].path.find(function (p) "
            "{ return p[0] === 'roundRect'; })[2],"
            "  bodies: bodyFills(c).length};"))["top"]

    rest = body_top(False, None)          # the un-bobbed foot line
    # progress 0.125 -> |sin(0.125*pi*4)| = 1 -> a full 3px lift at zoom 1
    bobbed = body_top(True, 0.125)
    assert bobbed < rest, (
        "(a) a walking character WITH progress was not lifted off the floor: "
        "rest=%r bobbed=%r" % (rest, bobbed))
    assert abs((rest - bobbed) - 3.0) < 1e-6, (
        "(a) the bob must be exactly 3px at zoom 1 for progress 0.125: "
        "rest=%r bobbed=%r" % (rest, bobbed))
    assert body_top(True, None) == rest, (
        "(c) the renderer invented a bob with walking=true but NO `progress` in "
        "the payload — a missing value must not animate")
    assert body_top(False, 0.125) == rest, (
        "(b) the renderer bobbed a character the payload did not report as "
        "walking — a stale `progress` must not animate")


# ================================ SCENE 5 — distinct colours across a room
def test_scene_a_room_of_twelve_slugs_paints_at_least_ten_distinct_figures():
    """Twelve workers in one room must read as (at least) ten distinct people.

    The proof is on the FILL CALLS, not on `seatColour()` alone: the number of
    DISTINCT fill colours used for the twelve bodies must be >= 10. A turn of
    the palette that collapses two slugs into one hue draws two people as one
    blob, which is the D2 defect (today: 12 slugs -> 6 colours).

    The 12 slugs sit on the real desk grid (two per row, 3 columns) so no two
    figures share a screen point and every one draws a distinct body fill.
    """
    slugs = ["inbox-sweeper", "ledger-auditor", "doc-reader", "patch-writer",
             "mail-triage", "calendar-keeper", "log-watcher", "queue-drainer",
             "report-builder", "index-refresher", "backup-runner",
             "schema-checker"]
    seats = [make_seat(s, slot_x(0, i), slot_y(0, i), i) for i, s in enumerate(slugs)]
    chars = [make_character(s, 1, "idle", x=slot_x(0, i), y=slot_y(0, i))
             for i, s in enumerate(slugs)]
    floors = [make_floor(1, "operations", [make_room("open", 0, 0, seats=seats)])]
    v = make_payload(floors, chars)
    r = _draw(v, (
        "var bodies = bodyFills(c);"
        "var seen = {}; bodies.forEach(function (o) { seen[o.fillStyle] = 1; });"
        "__RESULT = {bodies: bodies.length, distinct: Object.keys(seen).length,"
        "  colours: Object.keys(seen).sort()};"))
    assert r["bodies"] == 12, (
        "all twelve figures must be drawn (no two share a screen point): %r" % r)
    assert r["distinct"] >= 10, (
        "twelve distinct worker slugs painted only %d distinct figure colours: "
        "%r — a room of people reads as a blob. This is D2."
        % (r["distinct"], r["colours"]))


# ================================ SCENE 6 — an empty org
def test_scene_an_empty_org_draws_no_character_and_takes_the_empty_path():
    """An empty org must draw nothing that implies a character.

    No floors, no characters, `empty: true` — the frame is the void plus the
    clear, and there is no body, head, shadow or marker anywhere. If the
    renderer ever invents a figure to fill the canvas, the counts disagree and
    this fails. This is the negative control that keeps Scene 5 honest: a colour
    count of 10 cannot be reached by drawing phantom people.
    """
    v = make_payload([], [], empty=True)
    r = _draw(v, (
        "__RESULT = {agents: out.probe.agents.length,"
        "  body: bodyFills(c).length, head: headFills(c).length,"
        "  shadow: shadowFills(c).length,"
        "  desks: deskOps(c).length,"
        "  marks: texts(c).filter(function (t) { return /^[!^]$/.test(t); }),"
        "  fillRects: rects(c).length, clearRect: c.ops.filter(function (o) "
        "    { return o.op === 'clearRect'; }).length};"))
    assert r["agents"] == 0, "an empty org drew a probe agent: %r" % r
    assert r["body"] == 0 and r["head"] == 0 and r["shadow"] == 0, (
        "an empty org drew a character or part of one: %r" % r)
    assert r["desks"] == 0, "an empty org drew a desk: %r" % r
    assert r["marks"] == [], "an empty org drew a marker: %r" % r
    assert r["clearRect"] == 1, "the frame was not cleared: %r" % r
    assert r["fillRects"] == 1, (
        "an empty org drew geometry beyond the background fill: %r" % r)


# ================================ SCENE 7 — a stalled agent
def test_scene_a_stalled_agent_is_marked_and_a_running_one_is_not():
    """`stalled: true` must be visible; `stalled: false` must produce nothing.

    A stalled seat is a run that started and never settled (tower.py:228). It is
    the one state a human must spot across the room, so it draws a `!` marker
    over the head. The control (same seat, not stalled, idle) draws no marker,
    so a marker is a statement about the payload and not decoration.
    """
    def marker(stalled):
        v = make_payload(
            [make_floor(1, "operations", [make_room("open", 0, 0)])],
            [make_character("quiet", 1, "idle", stalled=stalled)])
        return _draw(v, (
            "__RESULT = {marks: texts(c).filter(function (t) "
            "{ return /^[!^]$/.test(t); }), bodies: bodyFills(c).length};"))

    stalled = marker(True)
    assert stalled["bodies"] == 1, (
        "the stalled character was not drawn at all: %r" % stalled)
    assert stalled["marks"] == ["!"], (
        "a stalled agent drew no `!` marker — a dead turn is invisible: %r"
        % stalled)

    running = marker(False)
    assert running["marks"] == [], (
        "a not-stalled agent drew a marker anyway: %r" % running)
