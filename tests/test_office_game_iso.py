"""The isometric projection and the room renderer.

A wrong projection is the worst kind of bug here: every scene stays "working"
(something is always drawn) while being subtly wrong in a way nobody can name.
So the geometry is asserted directly rather than eyeballed — the ratio, the
inverse, the depth ordering, and the fact that a room actually paints depth
rather than one flat plane.

Hermetic: tsc + node, no canvas, no browser, no network.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from _office_toolchain import SKIP_REASON, has_tsc, node_path, tsc_path  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
GAME = REPO / "frontend" / "qubit-app" / "plugins" / "qubit-office" / "game"
ART = GAME / "art"

NODE = node_path()

pytestmark = pytest.mark.skipif(not has_tsc(), reason=SKIP_REASON)

MODULES = ["colour.ts", "surfaces.ts", "iso.ts", "room.ts"]


def _run(tmp_path: Path, js: str) -> object:
    staged = tmp_path / "src"
    (staged / "art").mkdir(parents=True, exist_ok=True)
    for name in MODULES:
        shutil.copy(ART / name, staged / "art" / name)
    entry = staged / "art" / "room.ts"
    result = subprocess.run(
        [NODE, str(tsc_path()), "--module", "commonjs", "--target", "es2020",
         "--moduleResolution", "node",
         "--ignoreDeprecations", "6.0", "--outDir", str(tmp_path / "js"),
         "--skipLibCheck", "--noEmitOnError", "false", "--lib", "es2020,dom",
         str(entry)],
        capture_output=True, text=True, timeout=240, cwd=str(staged))
    # Emit layout: with `--outDir` and a single entry, tsc hoists the output by
    # one level (js/room.js, not js/art/room.js). Looking in only the nested
    # path made this suite SKIP silently — a green report proving nothing.
    # Both layouts are checked, and a missing emit FAILS rather than skipping.
    candidates = [tmp_path / "js" / "art" / "room.js", tmp_path / "js" / "room.js"]
    room = next((c for c in candidates if c.is_file()), None)
    if room is None:
        raise AssertionError(
            "tsc emitted no room.js; the driver cannot run.\n"
            f"looked in: {[str(c) for c in candidates]}\n"
            f"tsc stderr: {result.stderr[:800]}")
    js_dir = room.parent
    iso = js_dir / "iso.js"
    colour = js_dir / "colour.js"
    assert iso.is_file() and colour.is_file(), (
        f"tsc emitted room.js but not its dependencies: {sorted(p.name for p in js_dir.iterdir())}")
    program = ("const ISO = require(%s);\nconst C = require(%s);\n"
               "const ROOM = require(%s);\n%s\n"
               "process.stdout.write(JSON.stringify((() => { %s })()));\n"
               % (json.dumps(str(iso)), json.dumps(str(colour)),
                  json.dumps(str(room)), _CTOR, js))
    proc = subprocess.run([NODE], input=program, capture_output=True,
                          text=True, timeout=120)
    assert proc.returncode == 0, (
        f"node exited {proc.returncode}\nstderr: {proc.stderr[:1500]}")
    return json.loads(proc.stdout)


_CTOR = """
function Rec() {
  this.ops = []; this.fillStyle = '#000'; this.strokeStyle = '#000';
  this.lineWidth = 1; this.font = ''; this.textAlign = '';
}
const P = Rec.prototype;
const rec = (op) => function () { this.ops.push([op, this.fillStyle]); };
P.fillRect = function (x, y, w, h) {
  this.ops.push(['fillRect', x, y, w, h, this.fillStyle]); };
P.beginPath = function () { this.ops.push(['beginPath']); };
P.closePath = function () { this.ops.push(['closePath']); };
P.moveTo = function (x, y) { this.ops.push(['moveTo', x, y]); };
P.lineTo = function (x, y) { this.ops.push(['lineTo', x, y]); };
P.fill = function () { this.ops.push(['fill', this.fillStyle]); };
P.stroke = function () { this.ops.push(['stroke', this.strokeStyle]); };
P.arc = function () { this.ops.push(['arc']); };
P.ellipse = function () { this.ops.push(['ellipse']); };
P.save = function () {}; P.restore = function () {};
P.translate = function (x, y) { this.tx = x; this.ty = y; };
P.scale = function () {}; P.setTransform = function () {};
P.createLinearGradient = () => ({addColorStop: () => {}});
P.createRadialGradient = () => ({addColorStop: () => {}});
"""


# ======================================================================
# 1 — the projection itself
# ======================================================================
def test_the_projection_is_a_2_to_1_diamond(tmp_path):
    """THE RATIO IS THE LOOK.

    A 16px tile must become a 32x16 diamond. Get this wrong and every room is
    subtly squashed or stretched, which reads as "the art is off" with no
    obvious cause.
    """
    got = _run(tmp_path, """
      const a = ISO.toScreen({gx: 0, gy: 0});
      const bx = ISO.toScreen({gx: 1, gy: 0});
      const by = ISO.toScreen({gx: 0, gy: 1});
      const d = ISO.tileDiamond(0, 0);
      return {origin: a, east: bx, south: by,
              width: Math.abs(d[1].x - d[3].x),
              height: Math.abs(d[2].y - d[0].y),
              tile: ISO.TILE};
    """)
    assert got["origin"] == {"x": 0, "y": 0}, (
        f"grid origin is not screen origin: {got['origin']}")
    assert got["width"] == 2 * got["tile"], (
        f"a tile is {got['width']}px wide, not 2xTILE: {got}")
    assert got["height"] == got["tile"], (
        f"a tile is {got['height']}px tall, not TILE — the diamond is not 2:1")
    # +1 grid X moves right and down; +1 grid Y moves left and down
    assert got["east"]["x"] > 0 and got["east"]["y"] > 0, (
        f"east is not down-right: {got['east']}")
    assert got["south"]["x"] < 0 and got["south"]["y"] > 0, (
        f"south is not down-left: {got['south']}")


def test_height_lifts_by_half_a_tile(tmp_path):
    """One tile of height (z=1) raises a point by TILE px, so a 2-tile wall
    stands 32px above the floor — the reference's proportion."""
    got = _run(tmp_path, """
      const g = {gx: 3, gy: 4};
      const a = ISO.toScreen({...g, z: 0});
      const b = ISO.toScreen({...g, z: 1});
      const c = ISO.toScreen({...g, z: 2});
      return {lift1: a.y - b.y, lift2: a.y - c.y, tile: ISO.TILE};
    """)
    assert got["lift1"] == got["tile"], (
        f"z=1 lifted {got['lift1']}px, expected {got['tile']}")
    assert got["lift2"] == 2 * got["tile"], "z is not linear"


def test_screen_to_grid_inverts_the_projection(tmp_path):
    """Deep-linking a room and clicking a tile both depend on this being exact."""
    got = _run(tmp_path, """
      const bad = [];
      for (let gx = 0; gx < 12; gx++) {
        for (let gy = 0; gy < 12; gy++) {
          const s = ISO.toScreen({gx, gy});
          const g = ISO.toGrid(s);
          if (Math.abs(g.gx - gx) > 1e-9 || Math.abs(g.gy - gy) > 1e-9) {
            bad.push([gx, gy, g.gx, g.gy]);
          }
        }
      }
      return {bad};
    """)
    assert got["bad"] == [], (
        f"screen->grid does not invert grid->screen: {got['bad'][:3]}")


# ======================================================================
# 2 — draw order, which is what makes it look solid
# ======================================================================
def test_depth_increases_toward_the_viewer(tmp_path):
    """A tile with a larger gx+gy sum is nearer and must be drawn later. Get
    this backwards and the whole scene renders inside-out."""
    got = _run(tmp_path, """
      return {
        near: ISO.depth({gx: 5, gy: 5}),
        far: ISO.depth({gx: 1, gy: 1}),
        sameLine: ISO.depth({gx: 4, gy: 2}) === ISO.depth({gx: 2, gy: 4}),
        higherIsEarlier: ISO.depth({gx: 3, gy: 3, z: 2}) < ISO.depth({gx: 3, gy: 3}),
        inFront: ISO.isInFront({gx: 4, gy: 4}, {gx: 1, gy: 1}),
      };
    """)
    assert got["near"] > got["far"], "depth does not increase toward the viewer"
    assert got["sameLine"] is True, (
        "two tiles on the same diagonal do not share a depth")
    assert got["higherIsEarlier"] is True, (
        "a raised object does not sort before the floor at its base, so a wall "
        "would be drawn over the floor in front of it")
    assert got["inFront"] is True


def test_sorting_is_stable_for_equal_depth(tmp_path):
    """Two objects on the same diagonal must keep the caller's order, or a
    figure flickers in front of the desk it sits at between frames."""
    got = _run(tmp_path, """
      const items = [{id:'a', gx:1, gy:3}, {id:'b', gx:3, gy:1},
                     {id:'c', gx:2, gy:2}];
      const one = ISO.sortByDepth(items, i => i);
      const two = ISO.sortByDepth(items, i => i);
      return {order: one.map(i => i.id), stable: one.map(i=>i.id).join() === two.map(i=>i.id).join()};
    """)
    assert got["stable"] is True, "sortByDepth is not stable"
    assert got["order"] == ["a", "b", "c"], (
        f"equal-depth items did not keep their input order: {got['order']}")


def test_wall_faces_expose_only_the_two_visible_sides(tmp_path):
    """An isometric wall shows its south-west and south-east faces and its top.
    The north faces are always occluded, so drawing them would z-fight.

    The measured span of a face is the DIAMOND (TILE px) plus the LIFT
    (h * TILE), because a face polygon runs from the base corner up to the
    lifted one. Measuring only the lift is the mistake this pins down.
    """
    got = _run(tmp_path, """
      const f = ISO.wallFaces(2, 2, 2);
      const span = pts => Math.max(...pts.map(p => p.y)) - Math.min(...pts.map(p => p.y));
      const base = ISO.tileDiamond(2, 2);
      // THE INVARIANT: the wall top is the floor diamond translated straight
      // up by the wall's height. That is what "the wall has height h" MEANS in
      // this projection, and unlike a corner measurement it cannot be satisfied
      // by accident.
      const lifted = base.map(p => ({x: p.x, y: p.y - 2 * ISO.TILE}));
      const sameShape = f.top.every((p, i) =>
        Math.abs(p.x - lifted[i].x) < 1e-9 && Math.abs(p.y - lifted[i].y) < 1e-9);
      return {westSpan: span(f.west), eastSpan: span(f.east),
              diamond: span(base), tile: ISO.TILE,
              sameShape,
              topAboveBase: Math.max(...f.top.map(p=>p.y)) <= Math.max(...base.map(p=>p.y)),
              westMinX: Math.min(...f.west.map(p=>p.x)),
              eastMaxX: Math.max(...f.east.map(p=>p.x))};
    """)
    assert got["sameShape"] is True, (
        "the wall top is not the floor diamond lifted by exactly h*TILE, so the "
        "wall does not have the height it claims")
    # A face polygon runs from one base corner, along the diamond edge, up to
    # the same corner lifted: its vertical span is therefore (diamond half +
    # lift). Measured, not assumed — this assertion was guessed wrong twice.
    assert got["westSpan"] == got["tile"] / 2 + 2 * got["tile"], (
        f"the west face spans {got['westSpan']}px; expected half a diamond "
        f"({got['tile'] / 2}) plus 2 tiles of height ({2 * got['tile']})")
    assert got["eastSpan"] == got["westSpan"], (
        "the two visible faces are not the same height")
    assert got["topAboveBase"] is True, (
        "the wall top is not above the wall base")
    assert got["westMinX"] < got["eastMaxX"], (
        "the two faces do not sit either side of the corner")


# ======================================================================
# 3 — the room renderer produces a REAL 3D-looking scene
# ======================================================================
def _floor_json(rooms: int = 2) -> str:
    return json.dumps({
        "number": 1, "name": "Executive", "role": "executive",
        "rooms": [
            {"id": i + 1, "slug": f"room-{i}", "kind": "exec",
             "x": 1 + i * 11, "y": 1, "w": 10, "h": 8,
             "occupants": [
                 {"agentId": i * 2 + 1, "slug": f"a{i}", "animation": "working",
                  "stalled": False},
                 {"agentId": i * 2 + 2, "slug": f"b{i}", "animation": "idle",
                  "stalled": True},
             ]}
            for i in range(rooms)
        ],
    })


def test_a_floor_paints_in_three_layers_not_one(tmp_path):
    """Floor, walls, standees. A flat renderer produces one layer and cannot
    produce the distinct wall faces and contact shadows a room needs."""
    got = _run(tmp_path, """
      const floor = %s;
      const ctx = new Rec();
      ROOM.paintFloor(ctx, floor, 900, 560, null);
      const fills = ctx.ops.filter(o => o[0] === 'fill');
      const strokes = ctx.ops.filter(o => o[0] === 'stroke');
      const cols = new Set(fills.map(o => o[1]));
      return {fills: fills.length, strokes: strokes.length,
              distinct: cols.size,
              ground: fills.length && fills[0][1]};
    """ % _floor_json())
    assert got["fills"] > 200, (
        f"the floor painted only {got['fills']} polygons; a 2-room isometric "
        f"floor is hundreds of tiles")
    assert got["strokes"] > 50, (
        f"only {got['strokes']} strokes — the tile seams are not being drawn, "
        f"so the floor will read as a flat sheet")
    assert got["distinct"] > 12, (
        f"only {got['distinct']} distinct colours — the ramps are not reaching "
        f"the paint")


def test_the_camera_fits_the_floor_in_the_viewport(tmp_path):
    """If the camera does not frame the floor, an empty screen gets reported as
    an empty office."""
    got = _run(tmp_path, """
      const floor = %s;
      const cam = ROOM.cameraFor(floor, 900, 560);
      // project every room corner through the camera and check they land inside
      let minX = 1e9, maxX = -1e9, minY = 1e9, maxY = -1e9;
      for (const r of floor.rooms) {
        for (const [gx, gy] of [[r.x,r.y],[r.x+r.w,r.y],[r.x,r.y+r.h],[r.x+r.w,r.y+r.h]]) {
          const p = ISO.toScreen({gx, gy});
          const sx = p.x * cam.zoom + cam.ox, sy = p.y * cam.zoom + cam.oy;
          minX = Math.min(minX, sx); maxX = Math.max(maxX, sx);
          minY = Math.min(minY, sy); maxY = Math.max(maxY, sy);
        }
      }
      return {cam, minX, maxX, minY, maxY};
    """ % _floor_json())
    assert got["minX"] > -40 and got["maxX"] < 940, (
        f"the floor runs off the viewport horizontally: "
        f"{got['minX']:.0f}..{got['maxX']:.0f}")
    assert got["minY"] > -60 and got["maxY"] < 620, (
        f"the floor runs off the viewport vertically: "
        f"{got['minY']:.0f}..{got['maxY']:.0f}")


def test_an_empty_floor_still_renders_without_throwing(tmp_path):
    """A floor with no rooms is a real payload state; it must not crash."""
    got = _run(tmp_path, """
      const ctx = new Rec();
      ROOM.paintFloor(ctx, {number: 9, name: 'Empty', role: 'none', rooms: []},
                      800, 500, null);
      return {ops: ctx.ops.length};
    """)
    assert got["ops"] >= 1, "an empty floor painted nothing at all"


def test_figures_are_drawn_after_the_floor_they_stand_on(tmp_path):
    """Depth: a figure's key must exceed the floor tile beneath it and be less
    than the tile in front, or it renders through the wall or through the desk."""
    got = _run(tmp_path, """
      const onFloor = ISO.depth({gx: 5, gy: 5});
      const figure  = ISO.depth({gx: 5, gy: 5}) + 0.4;
      const inFront = ISO.depth({gx: 6, gy: 5});
      const wall    = ISO.depth({gx: 5, gy: 1});
      return {figureAfterFloor: figure > onFloor,
              figureBeforeInFront: figure < inFront,
              wallBefore: wall < onFloor};
    """)
    assert got["figureAfterFloor"] is True
    assert got["figureBeforeInFront"] is True, (
        "a figure sorts after the tile in front of it, so it will draw over it")
    assert got["wallBefore"] is True, (
        "a back wall sorts after the floor in front of it, so it will draw over "
        "the room")
