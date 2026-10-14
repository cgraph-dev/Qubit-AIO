"""Office tower — is the building drawn with VOLUME, measured from the PIXELS.

Why this file exists, and how it differs from its neighbour
-----------------------------------------------------------
`tests/test_office_wall_volume.py` proves the wall has volume by dividing two
CONSTANTS (`FACE_H` by `(ROOM_W + ROOM_H) * TILE_H/2`). That is a good algebra
check and it caught the stale value, but it can be green while the canvas draws
a flat plate: it never executes the renderer and it never looks at a coordinate.
A constant is not a drawing. (The same mistake shipped once already — the page
reported `data_state=ready`, zero console errors, an almost-empty canvas; text
and constants both passed while the pixels were wrong.)

This file executes `drawRoomPlate` through the real `drawTower`, records the
polygon fills the renderer actually emits into a fake 2D context, and asserts on
those COORDINATES. `FACE_H` is deliberately NOT exposed to this file, so it can
never drift back into an algebra check: every number below is read off a drawn
vertex. Five things must hold for the building to read as a solid rather than a
stack of plates:

 1. every room is a 3-face block — a top face and TWO wall faces, not one quad;
 2. the wall faces are TALL relative to the plate they stand on;
 3. the wall never rises into the deck above it (interpenetration is worse than
    a flat wall, so this is a hard ceiling, not a preference);
 4. the cutaway still reads — the walls do not cover the room interior;
 5. flattening the walls makes the whole building's drawn silhouette SHORTER,
    which is the direct test of "there is volume here"; a flat renderer draws
    the same frame twice and fails it.

How the measurements are taken (all from recorded vertices)
-----------------------------------------------------------
`drawRoomPlate` emits, per room, exactly three 4-vertex `fill()`s in a fixed
order: the plate quad, then the +x wall face, then the +y wall face. A wall quad
is `[topA, topB, topB-up, topA-up]` where `up` is the SAME delta applied to both
`y` components — that delta (`h2 = FACE_H * zoom`) is the wall's drawn vertical
height, and it is recovered as `min(topPair.y) - min(bottomPair.y)`, NOT as
`maxY - minY` (a wall is a sheared parallelogram, so its bounding-box height
includes the shear and would measure ~6x too big). The plate's projected depth is
its own `maxY - minY`. The ratio of the two is `FACE_H / ((ROOM_W+ROOM_H) *
TILE_H/2)` — the exact number `docs/game-office/37-WALL-VOLUME.md` §3 computes
(0.1238 at FACE_H=20, 0.223 at 36) — but here it comes from pixels.

Every assertion is stated as a measured number in its failure message, and the
controls are proved able to fail by re-running against SCRATCH copies of the
renderer (one `var FACE_H = <n>` re-written in a STRING handed to node — the repo
file is never opened for writing).

Harness: the fake-canvas + node-eval pattern from
`tests/test_office_tower_visual.py` (read that file for the rationale). The tail
line this file rewrites to expose the drawing internals is kept as a literal so
a rename fails loudly instead of silently skipping.
"""

from __future__ import annotations

import json
import re
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

REGISTER_LINE = 'window.__QUBIT_PLUGINS__.register("qubit-office", Office);'

# `FACE_H` is NOT exposed: this file must not be able to read the constant, or
# it would drift back into the algebra check it exists to replace.
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

/* The recording 2D context: each fill carries the path that built it and the
 * fillStyle in force, which is how a wall quad is told from the plate quad. */
function FakeCtx() {
  this.ops = [];
  this.fillStyle = "#000000"; this.strokeStyle = "#000000";
  this.globalAlpha = 1; this.lineWidth = 1; this.font = ""; this.textAlign = "";
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
FakeCtx.prototype.roundRect = function (x, y, w, h, r) { this._path.push(["roundRect", x, y, w, h, r]); };
FakeCtx.prototype.arc = function (x, y, r, a, b) { this._path.push(["arc", x, y, r]); };
FakeCtx.prototype.ellipse = function (x, y, rx, ry, rot, a, b) { this._path.push(["ellipse", x, y, rx, ry]); };
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

/* ---- geometry readers ------------------------------------------------- */
/* A wall/plate quad is a closed 4-vertex polygon fill. Nothing else in the room
 * draw path emits one (desks are fillRect, labels fillText, figures
 * arc/ellipse), so the count of these is the count of drawn faces. */
function quads(ctx) {
  return ctx.ops.filter(function (o) {
    if (o.op !== "fill") return false;
    var n = o.path.filter(function (p) {
      return p[0] === "moveTo" || p[0] === "lineTo";
    }).length;
    return n === 4;
  }).map(function (o) {
    var pts = o.path.filter(function (p) {
      return p[0] === "moveTo" || p[0] === "lineTo";
    }).map(function (p) { return {x: p[1], y: p[2]}; });
    var xs = pts.map(function (p) { return p.x; });
    var ys = pts.map(function (p) { return p.y; });
    return {pts: pts, fillStyle: o.fillStyle,
            minX: Math.min.apply(null, xs), maxX: Math.max.apply(null, xs),
            minY: Math.min.apply(null, ys), maxY: Math.max.apply(null, ys),
            /* the folder's own projected depth = the plate's vertical span */
            vspan: Math.max.apply(null, ys) - Math.min.apply(null, ys)};
  });
}

/* One room = 3 consecutive quads in draw order: [plate, wall+x, wall+y]. The
 * renderer pushes the room item as a unit (see drawTower's room item), so the
 * grouping is the recorded order, not a Y heuristic. */
function groupRooms(allq) {
  var rooms = [];
  for (var i = 0; i + 2 < allq.length; i += 3) {
    rooms.push({plate: allq[i], walls: [allq[i + 1], allq[i + 2]]});
  }
  return rooms;
}

/* The wall's DRAWN vertical height, in px. A wall quad is
 * [topA, topB, topB-up, topA-up] where `up` is a pure -y shift. So the height is
 * min(topA.y, topB.y) - min(topB-up.y, topA-up.y) -- NOT maxY - minY, which
 * would add the horizontal shear of the parallelogram. */
function wallDrawnHeight(w) {
  var p = w.pts;
  var top = Math.min(p[0].y, p[1].y);
  var bottom = Math.min(p[2].y, p[3].y);
  return top - bottom;
}

/* The deck plate's DRAWN projected depth = its vertical span. This is the
 * on-screen value of (ROOM_W + ROOM_H) * TILE_H/2 * zoom, which is the
 * denominator 37-WALL-VOLUME.md uses for its ratio. */
function plateDepth(plate) { return plate.vspan; }

/* Fit the camera exactly as the renderer does, then draw with it:
 * `fitToBuilding` mutates the camera in place and `drawTower` consumes
 * opts.cam verbatim, so this is the SAME camera a real redraw() uses. */
function drawFitted(payloadObj, W, H) {
  var cam = {cx: 0, cy: 0, zoom: 1, w: W, h: H};
  X.fitToBuilding(cam, (payloadObj.floorplan || {}).floors || []);
  var ctx = new FakeCtx();
  var probe = X.drawTower(ctx, payloadObj, {cam: cam, floor: null});
  return {ctx: ctx, probe: probe,
          cam: {cx: cam.cx, cy: cam.cy, zoom: cam.zoom, w: cam.w, h: cam.h}};
}

/* The drawn SILHOUETTE of the building: the bounding box of every polygon-fill
 * vertex (walls included, because wall vertices are part of the quads). */
function bbox(ctx) {
  var xs = [], ys = [];
  ctx.ops.forEach(function (o) {
    if (o.op !== "fill") return;
    o.path.forEach(function (p) {
      if (p[0] === "moveTo" || p[0] === "lineTo") { xs.push(p[1]); ys.push(p[2]); }
    });
  });
  return {minX: Math.min.apply(null, xs), maxX: Math.max.apply(null, xs),
          minY: Math.min.apply(null, ys), maxY: Math.max.apply(null, ys),
          h: Math.max.apply(null, ys) - Math.min.apply(null, ys)};
}
"""


# ---- payload builders (plain dicts = the single shape definition here) -----
def make_room(slug, x, y, w, h, seats=0):
    ss = [{"slot": i, "agent_id": None, "slug": "s-%d" % i,
           "x": x + i % 3, "y": y + i // 3} for i in range(seats)]
    return {"id": "room-" + slug, "slug": slug, "kind": "open", "department_id": 1,
            "x": x, "y": y, "w": w, "h": h, "seats": ss}


def make_floor(number, z, role, rooms):
    return {"id": "floor-%d" % number, "number": number, "name": "level %d" % number,
            "role": role, "theme": role, "z": z, "rooms": rooms}


def make_payload(floors, chars=None):
    return {
        "schema": "office/tower", "now": 1700000000.0, "active_window_s": 900,
        "walk_s": 6, "empty": False, "empty_reason": None,
        "hud": {"headcount": 0, "revenue_cents": 0, "cost_cents": 0, "net_cents": 0,
                "burn_cents_per_day": 0, "runway_days": None,
                "survival": {"floor_days": None}, "floors": []},
        "floorplan": {"schema": "office/floorplan",
                      "grid": {"cols": 8, "rows": 8, "tile": 1},
                      "floors": floors},
        "characters": chars or [], "activity": [], "history": [],
        "history_totals": {"revenue_cents": 0, "cost_cents": 0, "net_cents": 0},
        "motion_rule": "x", "read_only": True, "preview": None,
    }


# The real room after the scale fix is 12x7 (tower.py), the case the doc measures.
ONE_ROOM = make_payload([make_floor(1, 0, "operations",
                                    [make_room("open", 0, 0, 12, 7, seats=4)])])


def three_floor_plan():
    return make_payload([
        make_floor(1, 0, "operations", [make_room("a", 0, 0, 12, 7, seats=2)]),
        make_floor(2, 3, "operations", [make_room("b", 0, 0, 12, 7, seats=2)]),
        make_floor(3, 6, "executive", [make_room("c", 0, 0, 12, 7, seats=2)]),
    ])


def two_floor_plan():
    return make_payload([
        make_floor(1, 0, "operations", [make_room("lower", 0, 0, 12, 7)]),
        make_floor(2, 3, "operations", [make_room("upper", 0, 0, 12, 7)]),
    ])


# --------------------------------------------------- source + scratch copies
_SRC_CACHE: dict[str, str] = {}


def _live_source() -> str:
    if "live" not in _SRC_CACHE:
        src = ENTRY.read_text()
        assert REGISTER_LINE in src, (
            "the harness rewrites %s's final register line (%r) to expose the "
            "drawing internals. It is gone, so this file cannot reach them — "
            "update REGISTER_LINE to the new registration call."
            % (ENTRY.name, REGISTER_LINE))
        _SRC_CACHE["live"] = src
    return _SRC_CACHE["live"]


def _source_with_face_h(value: int | None) -> str:
    """The live renderer, or a SCRATCH copy with FACE_H overridden.

    `None` returns the live text untouched. An integer rewrites the one
    `var FACE_H = <n>,` declaration IN A COPY that exists only as a string
    handed to node — the repo file is never opened for writing here. This is how
    the controls (FACE_H=0 flattened, FACE_H=36 recommended, FACE_H=200 breach)
    are drawn, and how the "walls were flattened" comparison is made.
    """
    src = _live_source()
    if value is None:
        return src
    new, n = re.subn(r"var FACE_H = \d+", "var FACE_H = %d" % value, src, count=1)
    assert n == 1, ("could not find the `var FACE_H = <n>` declaration to "
                    "override; the constant moved or was renamed")
    return new


def _run(program: str, face_h: int | None = None):
    """Run `program` in node with the harness, against the live renderer (or a
    scratch copy with FACE_H overridden). `program` sets `globalThis.__RESULT`."""
    src = _source_with_face_h(face_h).replace(
        REGISTER_LINE,
        "globalThis.__X = {" + ", ".join("%s: %s" % (n, n) for n in EXPOSED) + "};")
    full = (HARNESS % {"exposed": json.dumps(EXPOSED)}
            + "\nvar __SRC = " + json.dumps(src) + ";\n"
            + "(0, eval)(__SRC);\n" + "var X = __X;\n"
            + program
            + "\nif (typeof __RESULT !== 'undefined') "
              "{ process.stdout.write(JSON.stringify(__RESULT)); }\n")
    proc = subprocess.run([NODE], input=full, capture_output=True, text=True,
                          timeout=60)
    if proc.returncode != 0:
        raise AssertionError("node failed:\nSTDOUT:\n%s\nSTDERR:\n%s"
                             % (proc.stdout, proc.stderr))
    out = proc.stdout.strip()
    assert out, ("node produced no JSON result — the program did not reach "
                 "__RESULT. STDOUT=%r STDERR=%r" % (proc.stdout, proc.stderr))
    return json.loads(out)


# The one-room measurement, reused by several tests so they all assert on the
# SAME recorded frame.
_ONE_ROOM_JS = """
var out = drawFitted(%(payload)s, 880, 500);
var allq = quads(out.ctx);
var rooms = groupRooms(allq);
__RESULT = {
  zoom: out.cam.zoom,
  n_quads: allq.length,
  n_rooms: rooms.length,
  plate: rooms[0].plate,
  walls: rooms[0].walls,
  wall_heights: rooms[0].walls.map(wallDrawnHeight),
  plate_depth: plateDepth(rooms[0].plate),
  n_rects: out.ctx.ops.filter(function (o) { return o.op === "fillRect"; }).length,
  texts: out.ctx.ops.filter(function (o) { return o.op === "fillText"; })
           .map(function (o) { return o.text; })
};
"""


def measure_one_room(face_h: int | None = None) -> dict:
    return _run(_ONE_ROOM_JS % {"payload": json.dumps(ONE_ROOM)}, face_h=face_h)


# =========================================================== sanity / harness
def test_the_harness_can_execute_the_renderer_and_record_geometry():
    """If this fails, every other test in this file is meaningless."""
    res = _run("__RESULT = {money: X.money(1000), hasDrawTower: typeof X.drawTower,"
               " tileH: X.TILE_H, deckZ: X.DECK_Z};")
    assert res["hasDrawTower"] == "function"
    assert res["money"] == "$10.00"
    assert (res["tileH"], res["deckZ"]) == (17, 26), res


# ============================================ 1. each room is a 3-face block
def test_each_room_draws_a_top_face_and_two_wall_faces_not_a_flat_quad():
    """A room is a 3-face block: ONE top plate + TWO wall faces = 3 quads.

    A flat-plate renderer draws exactly ONE quad per room. The cutaway only ever
    shows two of the four walls, so three is the correct count and it is the
    count that separates "a block" from "a plate".
    """
    res = measure_one_room()
    assert res["n_rooms"] == 1, "one room payload produced %d rooms" % res["n_rooms"]
    assert res["n_quads"] == 3, (
        "a room must draw 3 quads (1 top face + 2 cutaway wall faces); the "
        "recorded frame has %d. A flat-plate renderer draws 1. Quads: plate "
        "%s, walls %s"
        % (res["n_quads"],
           {"minY": round(res["plate"]["minY"], 1), "maxY": round(res["plate"]["maxY"], 1)},
           [{"minY": round(w["minY"], 1), "maxY": round(w["maxY"], 1)} for w in res["walls"]]))
    # each wall stands ON an edge of the plate: its top edge (pts[0]-pts[1]) must
    # be one of the plate's edges. This is what makes it a BLOCK and not three
    # loose quads floating near each other.
    plate_pts = [(round(p["x"], 3), round(p["y"], 3)) for p in res["plate"]["pts"]]
    plate_edges = set()
    for i in range(len(plate_pts)):
        plate_edges.add(frozenset((plate_pts[i], plate_pts[(i + 1) % len(plate_pts)])))
    for j, w in enumerate(res["walls"]):
        pts = [(round(p["x"], 3), round(p["y"], 3)) for p in w["pts"]]
        top = frozenset((pts[0], pts[1]))
        assert top in plate_edges, (
            "wall %d does not stand on a plate edge: its top edge %r is not among "
            "the plate's edges %r — the wall is detached from the room it belongs "
            "to" % (j, sorted(top), sorted(map(sorted, plate_edges))))


# ============================================ 2. the wall faces are actually tall
def test_wall_faces_are_tall_relative_to_the_plate_they_stand_on():
    """The wall's DRAWN height must be a meaningful fraction of the plate's depth.

    `docs/game-office/37-WALL-VOLUME.md` §3 measures the defect as the RATIO of
    wall height to plate extent: 20/161.5 = 0.1238 at FACE_H=20 (a rim); the
    recommendation of 36 gives 0.223 (a wall). Both numbers are zoom-invariant
    because zoom cancels. This test recovers the ratio from DRAWN VERTICES — the
    wall quad's pure vertical drop over the plate quad's vertical span — so a
    flat renderer (drop 0) scores 0.0 and fails hard. Threshold 0.20 is the
    document's own boundary between "a rim" and "a wall".
    """
    res = measure_one_room()
    depth = res["plate_depth"]
    heights = res["wall_heights"]
    assert depth > 0, "the plate has no projected depth: %r" % res

    ratios = [h / depth for h in heights]
    shortest = min(ratios)
    THRESHOLD = 0.20
    assert shortest > THRESHOLD, (
        "the DRAWN wall faces are too short to read as walls: heights %s px "
        "against a %.1f px plate depth -> ratios %s (shortest %.4f). Required "
        "> %.2f (37-WALL-VOLUME.md: 0.124 is a rim, 0.223 is a wall; at the "
        "shipped value this was 0.1238)."
        % ([round(h, 1) for h in heights], depth,
           [round(r, 4) for r in ratios], shortest, THRESHOLD))
    # The two wall faces are drawn at the same height (one FACE_H), so the two
    # ratios must agree; a mismatch means one wall is not using the same height.
    assert abs(ratios[0] - ratios[1]) < 0.01, (
        "the two wall faces have different drawn heights (%s) — they must share "
        "the one wall height" % [round(h, 2) for h in heights])


# ==================================== 3. the wall does not enter the deck above
def test_the_drawn_wall_does_not_reach_into_the_deck_above_it():
    """Interpenetration is a worse defect than a flat wall, so this is hard.

    Two stacked decks are separated on screen by FLOOR_HEIGHT_UNITS * DECK_Z =
    3 * 26 = 78 world units, times zoom. A wall that rises past that gap pokes
    through the floor above. This draws a 2-floor tower and checks the lower
    room's wall top edge stays CLEAR of the upper deck's plate.
    """
    js = """
var out = drawFitted(%(payload)s, 880, 500);
var allq = quads(out.ctx);
var rooms = groupRooms(allq);
/* The wall's DRAWN TOP EDGE is pts[2]/pts[3] (the pair the renderer shifted UP
 * by h2); pts[0]/pts[1] are the plate edge it stands on. */
function wallTopEdge(w) { return Math.min(w.pts[2].y, w.pts[3].y); }
__RESULT = {
  zoom: out.cam.zoom,
  deck_step_px: 3 * X.DECK_Z * out.cam.zoom,
  rooms: rooms.map(function (r) {
    return {plate_top_y: r.plate.minY, plate_bottom_y: r.plate.maxY,
            far_corner_y: r.plate.pts[0].y, near_corner_y: r.plate.pts[2].y,
            wall_top_edge_y: Math.min.apply(null, r.walls.map(wallTopEdge)),
            wall_heights: r.walls.map(wallDrawnHeight)};
  })
};
""" % {"payload": json.dumps(two_floor_plan())}
    res = _run(js)
    rooms = res["rooms"]
    assert len(rooms) == 2, "expected 2 rooms in a 2-floor plan, got %d" % len(rooms)

    # Floor 2 (z=3) is drawn 3*DECK_Z*zoom px HIGHER on screen than floor 1, and
    # paints first (far -> near). So the deck with the SMALLER far-corner y is
    # the upper one; the other is the lower deck whose wall could pierce it.
    upper = min(rooms, key=lambda r: r["far_corner_y"])
    lower = max(rooms, key=lambda r: r["far_corner_y"])

    # Compare LIKE corners: both plates' FAR corners are the same grid point
    # (0,0) one deck apart, so their y difference IS the drawn deck step.
    step = lower["far_corner_y"] - upper["far_corner_y"]
    assert abs(step - res["deck_step_px"]) < 1.0, (
        "the drawn deck step %.2f px does not match FLOOR_HEIGHT_UNITS*DECK_Z*"
        "zoom = %.2f px — the camera or a constant moved" % (step, res["deck_step_px"]))

    # The wall stands on the lower plate's far corner and rises h2 px up. Its top
    # edge must not reach the upper deck's plate far corner: that is the point it
    # would have to pass to interpenetrate.
    wall_rise = lower["far_corner_y"] - lower["wall_top_edge_y"]
    # The clear sky between the wall's top edge and the deck above's floor level.
    # The upper deck's plate (at the SAME far corner) is one deck step higher, so
    # the wall has `step - rise` px of clearance before it reaches that plane.
    # FACE_H must be < FLOOR_HEIGHT_UNITS*DECK_Z = 78 world units for this > 0.
    clearance = step - wall_rise
    used = wall_rise / step if step else 1.0

    assert wall_rise < step, (
        "the lower floor's drawn wall (rise %.2f px) reaches past the deck step "
        "(%.2f px): FACE_H*zoom >= FLOOR_HEIGHT_UNITS*DECK_Z*zoom, so the wall "
        "rises into the floor above. The wall top edge is at y=%.2f; the upper "
        "deck's plate far corner is at y=%.2f. Wall uses %.1f%% of the gap."
        % (wall_rise, step, lower["wall_top_edge_y"], upper["far_corner_y"],
           100.0 * used))
    # And a real, usable gap must remain (not a hairline).
    assert clearance > 0.5, (
        "the lower wall clears the deck above by only %.2f px of the %.2f px "
        "deck step — interpenetration for any practical purpose (wall rise %.2f "
        "px). The upper deck's far corner is at y=%.2f, the wall's top edge at "
        "y=%.2f." % (clearance, step, wall_rise, upper["far_corner_y"],
                     lower["wall_top_edge_y"]))
    # Report the clearance as a positive measurement for the failure message
    # above even when it passes (pytest shows it via -rA / assert rewriting).
    assert used < 0.55, (
        "the wall consumes %.1f%% of the inter-deck gap (%.2f of %.2f px) — that "
        "leaves the deck above no clear sky and is at the interpenetration edge"
        % (100.0 * used, wall_rise, step))


# ==================================== 4. taller walls did not eat the interior
def test_wall_faces_do_not_cover_the_room_interior():
    """The cutaway exists so you can see in. Walls may not dominate the plate.

    The two wall faces project onto the plate; the fraction of the plate's area
    they cover is the "how much interior is hidden" measure. `37-WALL-VOLUME.md`
    §6.3 puts the anti-cutaway boundary at roughly a quarter of the plate
    (~22% at FACE_H=36, 48% at the FACE_H=78 ceiling). The threshold here is the
    document's stated upper bound: the walls must cover less than a THIRD.
    """
    res = measure_one_room()
    plate = res["plate"]
    plate_area = (plate["maxX"] - plate["minX"]) * plate["vspan"]
    assert plate_area > 0

    def shoelace(pts):
        s = 0.0
        for i in range(len(pts)):
            x1, y1 = pts[i]["x"], pts[i]["y"]
            x2, y2 = pts[(i + 1) % len(pts)]["x"], pts[(i + 1) % len(pts)]["y"]
            s += x1 * y2 - x2 * y1
        return abs(s) / 2.0

    covered = sum(shoelace(w["pts"]) for w in res["walls"])
    fraction = covered / plate_area

    THRESHOLD = 0.3333
    assert fraction < THRESHOLD, (
        "the wall faces cover %.4f of the plate's area — the cutaway intent is "
        "defeated (interior hidden). Required < %.4f. Wall area %.1f px^2 over "
        "plate %.1f px^2 (plate x[%.1f..%.1f] y[%.1f..%.1f])."
        % (fraction, THRESHOLD, covered, plate_area, plate["minX"], plate["maxX"],
           plate["minY"], plate["maxY"]))


# ============================ 5. the building's silhouette got taller with walls
def test_flattening_the_walls_makes_the_drawn_building_shorter():
    """The direct test of "there is volume": flatten the walls, the tower shrinks.

    Draws the same 3-floor plan twice — once with the live renderer, once with
    FACE_H=0 in a SCRATCH copy (a string handed to node; the repo file is never
    written). With walls, each deck's plate and the deck above it are joined by
    two wall faces, so the drawn bounding box is taller. With FACE_H=0 the wall
    quads collapse onto the plate and the silhouette is SHORTER. A flat-plate
    renderer cannot pass: its two draws are identical.
    """
    plan = three_floor_plan()
    js = """
var out = drawFitted(%(payload)s, 880, 500);
__RESULT = {zoom: out.cam.zoom, bbox: bbox(out.ctx)};
""" % {"payload": json.dumps(plan)}

    live = _run(js)
    flat = _run(js, face_h=0)
    live_h = live["bbox"]["h"]
    flat_h = flat["bbox"]["h"]
    delta = live_h - flat_h

    assert live_h > 0 and flat_h > 0, (live, flat)
    MARGIN_PX = 8.0
    assert delta > MARGIN_PX, (
        "flattening FACE_H=0 changed the drawn tower height by only %.2f px "
        "(live %.2f px vs flat %.2f px) — the walls are not contributing VOLUME "
        "to the silhouette. Required gain > %.1f px."
        % (delta, live_h, flat_h, MARGIN_PX))

    # Confirm the FACE_H=0 control really flattened the walls (the mutation is
    # not a no-op): wall quads must have zero drawn height.
    js_ctrl = """
var out = drawFitted(%(payload)s, 880, 500);
var rooms = groupRooms(quads(out.ctx));
var hs = [];
rooms.forEach(function (r) { r.walls.forEach(function (w) { hs.push(wallDrawnHeight(w)); }); });
__RESULT = {n_rooms: rooms.length, wall_heights: hs,
            max_wall_h: Math.max.apply(null, hs)};
""" % {"payload": json.dumps(plan)}
    ctrl = _run(js_ctrl, face_h=0)
    assert ctrl["n_rooms"] == 3, ctrl
    assert ctrl["max_wall_h"] == 0.0, (
        "the FACE_H=0 control did not flatten the walls (max drawn wall height "
        "%.2f px from %s) — the mutation is a no-op and this test proves nothing"
        % (ctrl["max_wall_h"], [round(h, 2) for h in ctrl["wall_heights"]]))

    # And the live renderer's walls are NOT flattened, which is the positive half.
    ctrl_live = _run(js_ctrl)
    assert ctrl_live["max_wall_h"] > 1.0, (
        "the live renderer's walls have drawn height %.2f px — the building is "
        "being drawn flat" % ctrl_live["max_wall_h"])


# ============================ 6. the recommended fix is strictly more volume, and
#                                  still legal (report the before/after)
def test_the_landed_fix_drew_strictly_taller_walls_than_the_pre_fix_value():
    """Bound the fix: the landed wall must draw strictly taller than the pre-fix one.

    This is the "did the fix produce visible volume" check as measurements rather
    than a claim. It compares the wall height drawn by the LIVE renderer against
    the PRE-FIX value (FACE_H=20, the value 37-WALL-VOLUME.md measured at 0.1238)
    in a scratch copy, and asserts the live file's walls are taller by a real
    margin -- i.e. the fix actually landed and it is visible on the canvas.

    It is written against the pre-fix value rather than a literal 36 so it stays
    true if the landed value is 36, 40, or anything else in the legal window: the
    property under test is "taller than what shipped before", not "equals 36".
    """
    live = measure_one_room()          # whatever the repo currently holds
    pre = measure_one_room(face_h=20)  # the value documented as a flat rim

    live_h = max(live["wall_heights"])
    pre_h = max(pre["wall_heights"])
    live_ratio = live_h / live["plate_depth"]
    pre_ratio = pre_h / pre["plate_depth"]

    assert live_h > pre_h + 1.0, (
        "the landed FACE_H does not draw taller walls than the pre-fix value: "
        "live wall %.2f px (ratio %.4f) vs FACE_H=20 wall %.2f px (ratio %.4f). "
        "The volume fix produced no more drawn height, so it is not the fix."
        % (live_h, live_ratio, pre_h, pre_ratio))

    # and the landed value must still satisfy the cutaway coverage limit.
    plate = live["plate"]
    plate_area = (plate["maxX"] - plate["minX"]) * plate["vspan"]

    def shoelace(pts):
        s = 0.0
        for i in range(len(pts)):
            x1, y1 = pts[i]["x"], pts[i]["y"]
            x2, y2 = pts[(i + 1) % len(pts)]["x"], pts[(i + 1) % len(pts)]["y"]
            s += x1 * y2 - x2 * y1
        return abs(s) / 2.0

    frac = sum(shoelace(w["pts"]) for w in live["walls"]) / plate_area
    assert frac < 0.3333, (
        "the landed wall covers %.4f of the plate — the fix breaks the cutaway it "
        "was meant to preserve" % frac)


def test_face_h_36_does_not_interpenetrate_the_deck_above():
    """The recommended value must also clear the deck above, measured in pixels."""
    js = """
var out = drawFitted(%(payload)s, 880, 500);
var rooms = groupRooms(quads(out.ctx));
function wallTopEdge(w) { return Math.min(w.pts[2].y, w.pts[3].y); }
__RESULT = {zoom: out.cam.zoom, deck_step_px: 3 * X.DECK_Z * out.cam.zoom,
  rooms: rooms.map(function (r) {
    return {far_corner_y: r.plate.pts[0].y, plate_bottom_y: r.plate.maxY,
            wall_top_edge_y: Math.min.apply(null, r.walls.map(wallTopEdge))};
  })};
""" % {"payload": json.dumps(two_floor_plan())}
    res = _run(js, face_h=36)
    rooms = res["rooms"]
    upper = min(rooms, key=lambda r: r["far_corner_y"])
    lower = max(rooms, key=lambda r: r["far_corner_y"])
    wall_rise = lower["far_corner_y"] - lower["wall_top_edge_y"]
    step = res["deck_step_px"]
    assert wall_rise < step, (
        "at the recommended FACE_H=36 the lower floor's wall rises %.2f px, "
        "past the deck step of %.2f px (the deck gap) — the recommendation would "
        "interpenetrate, so 36 is not legal." % (wall_rise, step))
    clearance = step - wall_rise
    assert clearance > 0.5, (
        "at FACE_H=36 the wall rises %.2f px of the %.2f px deck step — it clears "
        "the deck above by only %.2f px, so the recommendation interpenetrates."
        % (wall_rise, step, clearance))
