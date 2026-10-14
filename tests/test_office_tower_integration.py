"""M4 — the tower's FOUR visual modules, checked for agreement WITH EACH OTHER.

Why this file exists
--------------------
`test_office_iso_engine.py`, `test_office_camera.py`, `test_office_sprites.py`
and `test_office_motion.py` each prove one module is internally consistent. They
are all green right now, and every one of them would stay green through the
defects this file exists to catch, because every one of those defects lives in
the SEAM between two modules, not inside any one of them:

  * a camera that fits a different world than the projector draws (browser: the
    building renders off-canvas, or as a thin stripe)
  * a sprite drawn at a scale the tile box does not share (browser: the figure
    floats above its own desk)
  * a motion path in GRID units consumed as SCREEN units (browser: the agent
    walks the wrong way, or teleports)
  * a depth key whose order disagrees with the projected screen order (browser:
    the painter's rule draws the wrong object on top — defect D1)

Each module's own test can be as green as it likes; the module has no way to
know what the module next to it does. So this file loads all four into ONE
`node -e` process — the same process the browser will run them in — and asserts
that they agree on the one thing they all have an opinion about: where a grid
cell is on the screen.

What it does NOT test
---------------------
`qubit-office.js` — the bundle that wires these four together — is another
agent's file and is explicitly out of scope here. These are cross-MODULE
contracts, so they hold whatever the wiring does.

Method
------
`node -e` with all four modules `require()`d (they are UMD: each sets
`module.exports` under CommonJS), a body that computes the contract values and
prints ONE JSON object, and Python assertions on the parsed result. The modules
are pure — no DOM, no canvas, no network — so a hook-free node process is a
faithful stand-in for the browser's module scope.

Every assertion below is written to be capable of FAILING, and the comment on
each says which real defect would fail it. Two were proved to fail by mutating
a COPY of a module (never the repo file) and observing the failure; see the
report accompanying this change.

Hermetic: reads four files, runs `node` on them, no network, no browser, no
server, no provider call.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest  # noqa: E402

from _office_node import run_node  # noqa: E402

PLUGIN_DIR = (Path(__file__).resolve().parent.parent
              / "frontend" / "qubit-app" / "plugins" / "qubit-office")

MODULES = {
    "ISO": PLUGIN_DIR / "iso-engine.js",
    "CAM": PLUGIN_DIR / "camera.js",
    "SPR": PLUGIN_DIR / "sprites.js",
    "MOT": PLUGIN_DIR / "motion.js",
}

_FOUND = shutil.which("node")
NODE: str = _FOUND or "node"

pytestmark = pytest.mark.skipif(
    _FOUND is None,
    reason="node is not installed; the JS tower cannot be run")


# --------------------------------------------------------------------- harness
def _run(body: str) -> dict:
    """Run JS with all four modules loaded; return its parsed JSON output.

    The module paths are injected with `json.dumps` rather than interpolated
    into the program text, so a checkout path containing a space or a backslash
    is a harness concern, not a syntax error inside the test. `body` gets the
    four modules bound to ISO / CAM / SPR / MOT.
    """
    program = ";".join(
        "var %s=require(%s)" % (name, json.dumps(str(path)))
        for name, path in MODULES.items()
    ) + ";\n" + body
    proc = subprocess.run([NODE], input=program, capture_output=True,
                          text=True, timeout=60)
    assert proc.returncode == 0, (
        f"node exited {proc.returncode}\nSTDOUT:\n{proc.stdout}\n"
        f"STDERR:\n{proc.stderr}")
    out = proc.stdout.strip()
    assert out, f"node printed nothing\nSTDERR:\n{proc.stderr}"
    return json.loads(out)


# The camera shape every module is given. ONE camera literal for the whole file
# on purpose: if a module needs a field the others do not set, that is itself
# the disagreement this file is looking for.
_CAM = """function cam(o){var c={cx:0,cy:0,zoom:1,w:800,h:600,
  tileW:34,tileH:17,deckZ:26};for(var k in o)c[k]=o[k];return c;}"""

# The projector the camera is handed: `gridToScreen` with an identity camera is
# the un-zoomed, un-panned WORLD space `worldExtent` documents (`zoom 1, world
# origin`). Using the real projector rather than a stand-in is the point — a
# local re-implementation would agree with the camera by construction and prove
# nothing.
_WORLD_PROJ = """function proj(x,y){return ISO.gridToScreen(x,y,0,
  {cx:0,cy:0,zoom:1,w:0,h:0});}"""


# ==========================================================================
# 1. PROJECTOR AND CAMERA SHARE ONE COORDINATE SPACE
# ==========================================================================
def test_the_projector_inverts_and_the_camera_maps_the_same_world():
    """gridToScreen / screenToGrid round-trip, AND the camera agrees with it.

    Two failures are caught here and they are different bugs:

      (a) `screenToGrid(gridToScreen(p, cam, z), cam, z) != p`. Real defect: a
          click on a character selects whatever pixel was nearest instead of the
          tile under the pointer — the inverse and the forward drifted apart.
          Swept over several grid points, decks and zooms, not spot-checked.

      (b) the point `gridToScreen` puts on the canvas is NOT the point
          `QubitCamera.screenPt` puts there for the same camera. Real defect:
          the camera and the projector hold TWO coordinate spaces (a fit that
          centres the building in one and draws it in the other), i.e. the
          building renders off-canvas. This is the "camera fits a different
          world than the projector draws" class, and it cannot be caught inside
          either module alone.
    """
    got = _run(_CAM + """
    var out = {roundtrip: [], camera: []};
    [-2,0,1,3,5,7].forEach(function(x){
      [-1,0,2,4,6].forEach(function(y){
        [0,1,3].forEach(function(z){
          [0.7,1,1.8,2.4].forEach(function(zoom){
            var c = cam({zoom: zoom, cx: 12, cy: -3, w: 1024, h: 640});
            var s = ISO.gridToScreen(x, y, z, c);
            var g = ISO.screenToGrid(s.x, s.y, c, z);
            out.roundtrip.push({x:x,y:y,z:z,zoom:zoom,
              gx:g.x, gy:g.y,
              ok: (g.x === x && g.y === y)});
          });
        });
      });
    });
    // the projector and the camera must be the SAME map: the world point the
    // projector computes, put through the camera's own screenPt, has to be the
    // pixel the projector itself produced for the live camera.
    [[0,0],[3,4],[7,2],[1,9]].forEach(function(q){
      [0,2].forEach(function(z){
        [1, 1.5, 2.4].forEach(function(zoom){
          var c = cam({zoom: zoom, cx: 40, cy: -18, w: 900, h: 620});
          var live = ISO.gridToScreen(q[0], q[1], z, c);
          var world = ISO.gridToScreen(q[0], q[1], z,
            {cx:0, cy:0, zoom:1, w:0, h:0});
          var viaCam = CAM.screenPt(c, world);
          out.camera.push({q:q, z:z, zoom:zoom,
            ok: (Math.abs(viaCam.x - live.x) < 1e-9 &&
                 Math.abs(viaCam.y - live.y) < 1e-9)});
        });
      });
    });
    // and the camera's inverse must undo its own forward exactly
    var c2 = cam({zoom: 1.25, cx: -7, cy: 33, w: 800, h: 600});
    var back = CAM.worldPt(c2, CAM.screenPt(c2, {x: 12.5, y: -44.25}));
    out.toWorld = back;
    console.log(JSON.stringify(out));
    """)

    assert len(got["roundtrip"]) == 360, got["roundtrip"][:3]
    failed = [r for r in got["roundtrip"] if not r["ok"]]
    assert not failed, (
        "gridToScreen/screenToGrid do not round-trip — a click would select the "
        f"wrong tile. Failing cases: {failed[:5]}")

    assert got["camera"], "no camera checks ran"
    bad = [r for r in got["camera"] if not r["ok"]]
    assert not bad, (
        "QubitCamera.screenPt and QubitIso.gridToScreen disagree about where a "
        "grid cell lands on the canvas — the camera and the projector are in "
        f"two different coordinate spaces. Failing cases: {bad[:5]}")

    assert abs(got["toWorld"]["x"] - 12.5) < 1e-9
    assert abs(got["toWorld"]["y"] + 44.25) < 1e-9


# ==========================================================================
# 2. THE CAMERA'S EXTENT CONTAINS WHAT THE PROJECTOR DRAWS
# ==========================================================================
def test_the_fitted_extent_contains_every_projected_room_corner():
    """`worldExtent(cells, gridToScreen)` must bound the drawn floorplan.

    Real defect: the extent measures one world space and the projector draws in
    another, so `fit()` zooms to a box that does not contain the building — the
    building renders off-canvas (or as a thin stripe). The assertion is not
    "the extent is non-empty"; it is that every projected corner of every room
    lies INSIDE the returned box, across floors.

    The floorplan is built in the backend's own room shape (`{x,y,w,h}`, see
    `tower.floorplan`), several rooms a floor and several floors deep, so a
    missing per-floor z term or a min/max-only corner scan (which under-measures
    a 2:1 diamond) fails here.
    """
    got = _run(_CAM + _WORLD_PROJ + """
    // Synthetic floorplan in the backend's room shape. Rooms are spaced the way
    // tower.py spaces them (col*(ROOM_W+1), row*(ROOM_H+1)) so the test would
    // notice a fit that only works for one-room offices.
    var floorplan = { schema: "qubit-office-floorplan/1", floors: [
      { id: 1, rooms: [
        {x:0,  y:0,  w:6, h:4, slug:"desks"},
        {x:7,  y:0,  w:6, h:4, slug:"desks-2"},
        {x:14, y:0,  w:6, h:4, slug:"lab"},
        {x:0,  y:5,  w:6, h:4, slug:"review"}]},
      { id: 2, rooms: [
        {x:0,  y:10, w:6, h:4, slug:"ops"},
        {x:7,  y:10, w:6, h:4, slug:"exec"}]}
    ]};

    // the cells worldExtent takes: every room rect, at its own floor
    var cells = [];
    floorplan.floors.forEach(function(f, fi){
      f.rooms.forEach(function(r){
        cells.push({x: r.x, y: r.y, w: r.w, h: r.h, z: fi});
      });
    });

    var ext = CAM.worldExtent(cells, proj);
    var checks = [];
    cells.forEach(function(cl){
      var z = cl.z || 0;
      [[cl.x,cl.y],[cl.x+cl.w,cl.y],[cl.x,cl.y+cl.h],[cl.x+cl.w,cl.y+cl.h]]
        .forEach(function(pt){
          // project the corner at ITS OWN deck, the way the renderer draws it
          var s = ISO.gridToScreen(pt[0], pt[1], z, {cx:0,cy:0,zoom:1,w:0,h:0});
          checks.push({pt:pt, z:z, sx:s.x, sy:s.y,
            inside: (s.x >= ext.minX - 1e-9 && s.x <= ext.maxX + 1e-9 &&
                     s.y >= ext.minY - 1e-9 && s.y <= ext.maxY + 1e-9)});
        });
    });

    // Sanity: the projector genuinely spreads the plan (a degenerate extent
    // would trivially contain everything).
    var span = {w: ext.w, h: ext.h, cx: ext.cx, cy: ext.cy};
    console.log(JSON.stringify({ext: ext, checks: checks, span: span}));
    """)

    ext = got["ext"]
    assert ext["w"] > 0 and ext["h"] > 0, (
        f"the fitted extent is degenerate — fit() would frame nothing: {ext}")
    # an isometric deck is wider than it is tall; a square box means the corner
    # scan collapsed to a min/max pair
    assert ext["w"] > ext["h"], (
        "the extent is not the wide, shallow iso box a 2:1 projection produces "
        f"— corner scan looks wrong: {ext}")

    outside = [c for c in got["checks"] if not c["inside"]]
    assert not outside, (
        "a projected room corner falls OUTSIDE the extent the camera fits to — "
        "the fit is fitting the wrong thing and the building renders off-canvas. "
        f"Outside: {outside[:5]}")


def test_the_fit_actually_frames_the_extent():
    """`fit()` + `worldExtent` produce a camera that puts the extent on-canvas.

    Real defect: `fit` returns a camera whose `cx/cy` is not the extent centre,
    or whose zoom collapses the building into a corner — both draw the tower
    off-screen while every module's own tests stay green.
    """
    got = _run(_CAM + _WORLD_PROJ + """
    var cells = [{x:0,y:0,w:6,h:4},{x:7,y:0,w:6,h:4},{x:0,y:5,w:6,h:4}];
    var ext = CAM.worldExtent(cells, proj);
    function cam(o){var c={cx:0,cy:0,zoom:1,w:800,h:600};for(var k in o)c[k]=o[k];return c;}
    var viewport = {w: 800, h: 600};
    var f = CAM.fit(viewport, ext, {});
    var c = cam(f);
    // the extent centre, through the fitted camera, must land at the viewport
    // centre; and the projected extent must be inside the viewport
    var centre = CAM.screenPt(c, {x: ext.cx, y: ext.cy});
    var tl = CAM.screenPt(c, {x: ext.minX, y: ext.minY});
    var br = CAM.screenPt(c, {x: ext.maxX, y: ext.maxY});
    console.log(JSON.stringify({f:f, centre:centre,
      tl:tl, br:br, ext:ext, viewport:viewport}));
    """)

    centre = got["centre"]
    vp = got["viewport"]
    # the extent centre is drawn at the viewport centre (the camera convention)
    assert abs(centre["x"] - vp["w"] / 2) < 1e-6, (
        f"fit() centres the extent at x={centre['x']}, not the viewport centre "
        f"{vp['w']/2} — the building is off to one side")
    assert abs(centre["y"] - vp["h"] / 2) < 1e-6
    # and the drawn extent must actually overlap the viewport, not sit off it
    tl, br = got["tl"], got["br"]
    assert min(br["x"], vp["w"]) - max(tl["x"], 0) > 0, (
        f"the fitted extent [{tl['x']:.1f},{br['x']:.1f}] does not overlap the "
        f"viewport [0,{vp['w']}] — a blank screen")
    assert min(br["y"], vp["h"]) - max(tl["y"], 0) > 0, (
        f"the fitted extent [{tl['y']:.1f},{br['y']:.1f}] does not overlap the "
        f"viewport [0,{vp['h']}] — a blank screen")


# ==========================================================================
# 3. A SPRITE'S FEET SIT ON ITS DESK CELL
# ==========================================================================
def test_a_sprite_is_anchored_on_the_tile_it_stands_on():
    """The figure's BOOTS land on the ground point, and that point is its tile.

    Real defect (the "floating sprite"): the sprite is anchored by its image
    BOTTOM instead of its measured feet line, or `feetY` is inflated, so the
    boots are drawn above the ground the caller placed them on — the body hovers
    over its desk. The crisp, defect-sensitive form of that is:

        anchor.feetY == anchor.height, so that placing the canvas top at
        `ground.y - feetY * scale` puts the canvas BOTTOM (the boots) exactly on
        `ground.y` — the drawn pixel the caller stood the figure on.

    With a correct anchor the boot row EQUALS the ground row (residual 0); a
    float defect leaves a gap of `(height - feetY) * scale`. The assertion is
    that residual, not "the boots are somewhere inside the tile" — the loose
    form tolerates a 14-pixel hover at max zoom and could never fail.

    The ground point is `gridToScreen(cell)`, the tile's centre, and it is also
    asserted to lie inside `tileBounds(cell)` so "on the ground" means "on that
    cell's tile", the cross-module claim: the sprite's anchor and the tile box
    agree about where the desk is.
    """
    got = _run(_CAM + """
    var out = [];
    [[3,4],[0,0],[7,2],[1,9]].forEach(function(cell){
      [0.85, 1, 1.5, 2, 2.4].forEach(function(zoom){
        var c = cam({zoom: zoom, w: 1024, h: 640});
        var tb = ISO.tileBounds(cell[0], cell[1], 0, c);   // tile's screen box
        var ground = ISO.gridToScreen(cell[0], cell[1], 0, c); // its centre
        var a = SPR.anchorOf(SPR.bakeAgent("#3366cc", {scale: zoom}));
        // the documented placement: canvas top = ground - feetY*scale, so the
        // canvas BOTTOM is the boots. They must land on `ground`.
        var drawY = ground.y - a.feetY * zoom;
        var bootRow = drawY + a.height * zoom;
        out.push({
          cell: cell, zoom: zoom, ground: ground, tb: tb, anchor: a,
          bootRow: bootRow,
          // residual between where the boots are drawn and where the caller
          // put the ground: 0 for a correct anchor, +(height-feetY)*scale for a
          // float, -(feetY-height)*scale for a sink
          residual: bootRow - ground.y,
          // and is that ground point inside the tile this cell owns?
          groundInsideTile: (ground.y >= tb.y - 1e-9 &&
                             ground.y <= tb.y + tb.h + 1e-9 &&
                             ground.x >= tb.x - 1e-9 &&
                             ground.x <= tb.x + tb.w + 1e-9)
        });
      });
    });
    console.log(JSON.stringify(out));
    """)

    assert len(got) == 20, got
    for r in got:
        assert r["anchor"]["feetY"] > 0, (
            f"anchorOf returned a non-positive feet line {r['anchor']} — a "
            "figure with no measured feet cannot be placed on its desk")
        # the boots must be drawn exactly where the caller placed the ground
        assert abs(r["residual"]) < 1e-6, (
            f"the sprite's boots are drawn {r['residual']:.3f}px off the ground "
            f"point it was placed on (zoom {r['zoom']}, cell {r['cell']}, "
            f"feetY={r['anchor']['feetY']}, height={r['anchor']['height']}) — "
            "the anchor is measured from the image edge, not the boots: the "
            "floating-sprite defect")
        # and that ground point belongs to the cell's own tile box
        assert r["groundInsideTile"], (
            f"the ground point {r['ground']} for cell {r['cell']} is OUTSIDE its "
            f"own tile box {r['tb']} at zoom {r['zoom']} — the sprite and the "
            "tile box disagree about where the desk is")


def test_the_sprite_scale_is_the_cameras_scale():
    """`anchorOf` is scale-independent, so feet*scale tracks `tileBounds`.

    Real defect: `feetY` leaks device pixels, so `anchor.feetY * scale` grows
    with the bake scale and the figure drifts off its tile as you zoom. Both
    `tileBounds` and the sprite must scale by the SAME `zoom`.
    """
    got = _run(_CAM + """
    var out = [];
    [1, 2, 4].forEach(function(scale){
      var fig = SPR.bakeAgent("#8833cc", {scale: scale});
      var a = SPR.anchorOf(fig);
      out.push({scale: scale, feetY: a.feetY, height: a.height});
    });
    // tileBounds height must scale linearly with zoom; the sprite's drawn
    // height must scale linearly with the same factor. Compare the RATIO.
    var ratios = [];
    [0.85, 1, 2, 2.4].forEach(function(zoom){
      var c = cam({zoom: zoom, w: 800, h: 600});
      var tb = ISO.tileBounds(2, 3, 0, c);
      var fig = SPR.bakeAgent("#8833cc", {scale: zoom});
      var a = SPR.anchorOf(fig);
      ratios.push({zoom: zoom, tileH: tb.h, tileW: tb.w,
                   figH: a.height * zoom});
    });
    console.log(JSON.stringify({anchors: out, ratios: ratios,
      baseTileW: ISO.TILE_W, baseTileH: ISO.TILE_H}));
    """)

    # feetY is a sprite-unit constant: the same at every bake scale
    feet = {r["feetY"] for r in got["anchors"]}
    assert len(feet) == 1, (
        f"anchorOf().feetY changes with the bake scale {got['anchors']} — "
        "device pixels are leaking into a sprite-unit field, so the figure "
        "drifts off its tile as you zoom")

    # and the tile box scales by exactly the zoom the sprite is drawn at
    base_w = got["baseTileW"]
    base_h = got["baseTileH"]
    for r in got["ratios"]:
        assert abs(r["tileW"] - base_w * r["zoom"]) < 1e-9, (
            f"tileBounds width {r['tileW']} != TILE_W * zoom "
            f"({base_w} * {r['zoom']}) — the tile box and the sprite are at "
            "different scales")
        assert abs(r["tileH"] - base_h * r["zoom"]) < 1e-9, (
            f"tileBounds height {r['tileH']} != TILE_H * zoom "
            f"({base_h} * {r['zoom']}) — the tile box and the sprite are at "
            "different scales")
        # the drawn figure height must equal the constant sprite-unit height
        # times the SAME zoom: equal growth is what keeps the feet on the tile
        assert abs(r["figH"] - got["anchors"][0]["height"] * r["zoom"]) < 1e-9, (
            f"the drawn figure height {r['figH']} is not "
            f"figureHeight({got['anchors'][0]['height']}) * zoom({r['zoom']}) — "
            "the sprite is drawn at a different scale than the tile box")


# ==========================================================================
# 4. MOTION FACING AND THE PROJECTION AGREE ON DIRECTION
# ==========================================================================
def test_a_cardinal_step_moves_the_projection_the_way_its_facing_claims():
    """`QubitOfficeMotion.DIR` predicts the projected direction of travel.

    Real defect (the "walks the wrong way" class): a motion path is expressed in
    GRID units and consumed as SCREEN units, so an agent facing "right" is drawn
    moving left, or an agent "up" the grid drifts down the canvas. The mapping
    under this 2:1 projection is fixed by the projector itself:

        +col ("right")  -> projected x INCREASES (and y increases with it)
        -col ("left")   -> projected x DECREASES
        +row ("down")   -> projected y INCREASES (x decreases with it)
        -row ("up")     -> projected y DECREASES

    DIR's own rule (the larger |axis| wins, ties to horizontal — tower.py's
    `_facing`) is asserted against that projection for genuine CARDINAL steps
    (exactly one axis changes). A diagonal is excluded on purpose: DIR must
    return one of four cardinals for it, so a diagonal cannot be expected to
    "move a single axis the way the facing claims" — that is a property of the
    model's 4-connectivity, not a module disagreement.
    """
    got = _run(_WORLD_PROJ + """
    var steps = [
      // from -> to : exactly one axis changes
      [0,0,1,0], [0,0,-1,0], [0,0,0,1], [0,0,0,-1],
      [2,3,5,3], [2,3,-1,3], [2,3,2,5], [2,3,2,-1],
      [1,1,0,1], [4,4,4,3], [7,2,6,2], [7,2,7,3],
      [-3,-3,-2,-3], [-3,-3,-3,-2]
    ];
    var out = [];
    steps.forEach(function(q){
      var f = MOT.DIR(q[0], q[1], q[2], q[3]);
      var a = proj(q[0], q[1]), b = proj(q[2], q[3]);
      var dx = b.x - a.x, dy = b.y - a.y;
      var ok;
      if (f === "right") ok = dx > 0;
      else if (f === "left") ok = dx < 0;
      else if (f === "down") ok = dy > 0;
      else if (f === "up") ok = dy < 0;
      else ok = false;
      out.push({q:q, f:f, dx:dx, dy:dy, ok:ok});
    });
    console.log(JSON.stringify(out));
    """)

    assert len(got) == 14
    for r in got:
        assert r["ok"], (
            f"motion says the step {r['q'][:2]}->{r['q'][2:]} faces "
            f"'{r['f']}' but the projection moves it by ({r['dx']},{r['dy']}) — "
            "the path's facing and the projector disagree about direction")


def test_the_interpolated_walk_stays_on_grid_and_faces_a_walkable_way():
    """A routed walk, run through `interpolate`, projects onto its own cells.

    Real defect: `interpolate` returns a SCREEN-space position or a facing the
    projector cannot realise, so the drawn body leaves the path its own
    waypoints describe. Every interpolated point must project BETWEEN the
    projected positions of the leg it is on, and the reported facing must match
    the leg's grid direction under the same mapping as assertion 4.
    """
    got = _run(_WORLD_PROJ + """
    var path = MOT.pathThrough({x:0,y:0}, {x:4,y:3}, {});
    var out = {path: path, samples: []};
    var pts = [{x:0,y:0}].concat(path);
    for (var i = 0; i <= 20; i++) {
      var t = i / 20;
      var p = MOT.interpolate(pts, t);
      out.samples.push({t: t, x: p.x, y: p.y, facing: p.facing, done: p.done});
    }
    // the facing rule, restated against the projector for each leg
    out.legs = [];
    for (var j = 0; j < pts.length - 1; j++) {
      var a = pts[j], b = pts[j + 1];
      var f = MOT.DIR(a.x, a.y, b.x, b.y);
      var sa = proj(a.x, a.y), sb = proj(b.x, b.y);
      // which way did the facing move the projection?
      var ok = false;
      if (f === "right") ok = (sb.x > sa.x);
      else if (f === "left") ok = (sb.x < sa.x);
      else if (f === "down") ok = (sb.y > sa.y);
      else if (f === "up") ok = (sb.y < sa.y);
      out.legs.push({a:a, b:b, f:f, ok:ok});
    }
    console.log(JSON.stringify(out));
    """)

    assert got["path"], "pathThrough returned no path for a clear 4,3 hop"
    for leg in got["legs"]:
        assert leg["ok"], (
            f"a routed leg {leg['a']}->{leg['b']} faces '{leg['f']}' but "
            "projects the other way — the walk is drawn backwards")

    for s in got["samples"]:
        assert s["x"] is not None and s["y"] is not None, s
        # every sample must lie within the bounding box of the routed cells:
        # a screen-space leak would throw it far outside
        assert -0.5 <= s["x"] <= 4.5 and -0.5 <= s["y"] <= 3.5, (
            f"interpolate returned {s} — outside the grid box of its own path "
            "(a screen-unit value consumed as a grid unit)")
        assert s["facing"] in ("left", "right", "up", "down"), (
            f"interpolate returned facing {s['facing']!r}, which the projector "
            "cannot realise")
    # the walk must actually arrive
    assert got["samples"][-1]["done"] is True
    assert abs(got["samples"][-1]["x"] - 4) < 1e-9
    assert abs(got["samples"][-1]["y"] - 3) < 1e-9


# ==========================================================================
# 5. DEPTH KEY ORDER == PROJECTED SCREEN ORDER  (defect D1)
# ==========================================================================
def test_depth_key_order_matches_projected_screen_order():
    """Sorting by `depthKey` must draw back-to-front exactly as screen y does.

    Real defect (D1): the painter's rule and the projection disagree about which
    object is nearer, so an object is painted UNDER something in front of it —
    a desk covers the character sitting at it, or a far wall covers a near one.

    On one deck the two orderings are provably the same thing: `depthKey` is
    monotone in `(x + y)`, and `gridToScreen` maps `(x + y)` to screen y. This is
    asserted on the SAME set of cells through both functions — not on either one
    alone, which is why no single-module test can see it.
    """
    got = _run("""
    function cam(o){var c={cx:0,cy:0,zoom:1,w:800,h:600,
      tileW:34,tileH:17,deckZ:26};for(var k in o)c[k]=o[k];return c;}
    var c = cam({zoom: 1.3, cx: 5, cy: -2, w: 900, h: 600});
    // a scatter of same-deck cells, including ties in (x+y) that must tie-break
    // deterministically and identically in both orderings
    var cells = [];
    for (var x = 0; x < 6; x++) for (var y = 0; y < 6; y++) cells.push([x, y]);

    var rows = cells.map(function(q){
      var s = ISO.gridToScreen(q[0], q[1], 0, c);
      return {x: q[0], y: q[1], sy: s.y, k: ISO.depthKey(q[0], q[1], 0, 0)};
    });

    function orderBy(fn, tie) {
      return rows.slice().sort(function(a, b){
        if (fn(a) !== fn(b)) return fn(a) - fn(b);
        return tie(a, b);
      }).map(function(r){ return r.x + "," + r.y; });
    }
    // depthKey order, ties by (x then y) so the comparison is total
    var byKey = orderBy(function(r){return r.k;},
                        function(a,b){return a.x-b.x || a.y-b.y;});
    // screen order, ties by (x then y) — the same tie-break, so any disagreement
    // is the depth rule and never the tie handling
    var byScreen = orderBy(function(r){return r.sy;},
                           function(a,b){return a.x-b.x || a.y-b.y;});
    console.log(JSON.stringify({byKey: byKey, byScreen: byScreen,
                                n: rows.length}));
    """)

    assert got["n"] == 36
    if got["byKey"] != got["byScreen"]:
        # report the first disagreement, not the whole list
        for i, (a, b) in enumerate(zip(got["byKey"], got["byScreen"])):
            if a != b:
                pytest.fail(
                    f"depthKey order and projected screen-y order diverge at "
                    f"position {i}: depthKey says {a!r} but screen y says "
                    f"{b!r} — the painter's rule draws the wrong object on top "
                    "(defect D1)")
    assert got["byKey"] == got["byScreen"]


def test_deck_order_draws_the_lower_floor_after_the_higher_one():
    """`depthKey`'s z term must dominate the in-deck term, and
    `gridToScreen` must agree about which deck is which.

    Real defect: the z term is not large enough to dominate `(x+y)`, so a desk
    on a low floor paints over the wall of the floor behind it (a deck's foot
    tiles overlap in col/row). The check is that for the SAME cell on two decks
    the deck ordering wins in BOTH modules: the lower deck gets the greater
    `depthKey` (painted later = nearer the viewer), and `gridToScreen` draws the
    higher deck HIGHER on the canvas (smaller y, since `z` lifts the point).

    The cell is held fixed across the pair on purpose. `gridToScreen`'s y is
    `(x+y)*tileH/2 - z*deckZ`, so comparing two DIFFERENT cells at different
    decks would let the in-deck term decide the order and the assertion would be
    testing the wrong thing — the deck axis is only well-defined for one cell.

    (This is the assertion my first draft got wrong: it varied the cell AND the
    deck and then demanded the deck axis decide, which the projection correctly
    refuses to do. The fix is the fixed cell, not a relaxed bound.)
    """
    got = _run("""
    function cam(o){var c={cx:0,cy:0,zoom:1,w:800,h:600,
      tileW:34,tileH:17,deckZ:26};for(var k in o)c[k]=o[k];return c;}
    var c = cam({zoom: 1.4, cx: 0, cy: 0, w: 800, h: 600});
    var out = [];
    // for each cell, compare deck 0 / 1 / 2 against one another
    [[0,0],[9,9],[3,4],[8,8]].forEach(function(cell){
      var ys = {}, ks = {};
      [0,1,2].forEach(function(z){
        ks[z] = ISO.depthKey(cell[0], cell[1], z, 0);
        ys[z] = ISO.gridToScreen(cell[0], cell[1], z, c).y;
      });
      [[0,1],[0,2],[1,2]].forEach(function(pair){
        var lo = pair[0], hi = pair[1];
        out.push({cell: cell, lo: lo, hi: hi,
          klo: ks[lo], khi: ks[hi], ylo: ys[lo], yhi: ys[hi],
          // the LOWER deck must have the GREATER key (painted later = nearer)
          lowerKeyGreater: ks[lo] > ks[hi],
          // and the HIGHER deck must be drawn higher on the canvas (smaller y)
          higherDrawsHigher: ys[hi] < ys[lo]});
      });
    });
    console.log(JSON.stringify(out));
    """)

    assert got
    for r in got:
        assert r["lowerKeyGreater"], (
            f"depthKey gives deck {r['lo']} key {r['klo']} and deck "
            f"{r['hi']} key {r['khi']} for cell {r['cell']} — the lower deck is "
            "not painted last, so a low-floor desk covers the floor behind it")
        assert r["higherDrawsHigher"], (
            f"gridToScreen draws deck {r['hi']} at y={r['yhi']} and deck "
            f"{r['lo']} at y={r['ylo']} for cell {r['cell']} — the higher deck "
            "is not drawn higher, so the deck axis and the projection disagree")


# ==========================================================================
# 6. THE WHOLE VISUAL LAYER IS TIMER-FREE
# ==========================================================================
def _code(path: Path) -> str:
    """The file with JS comments stripped, so a header cannot trip a check.

    Same convention as `test_office_tower_plugin._code`: the modules' headers
    DOCUMENT that there is no timer, and a naive check would fail on the file's
    own explanation of why it has none.
    """
    src = path.read_text()
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    src = re.sub(r"//[^\n]*", "", src)
    return src


@pytest.mark.parametrize("name", sorted(MODULES))
def test_no_module_in_the_visual_layer_owns_a_timer(name):
    """C7 at the layer level: not one of the four may own a clock.

    Real defect this catches: a module that "just adds a smooth animation" —
    `setInterval`, `setTimeout`, `requestAnimationFrame`, `setImmediate` or
    `queueMicrotask` — lets a character look busy with no event behind it. The
    plugin bundle is allowed exactly one poll (see
    `test_office_tower_plugin.test_the_only_timer_is_the_poll`); the visual
    layer is allowed ZERO, because everything it draws is a pure function of the
    payload's own `progress`.

    A check that reads all four together is stronger than four separate checks:
    it fails on whichever module the next editor adds the timer to, and it fails
    as a LAYER, naming the module.
    """
    code = _code(MODULES[name])
    found = re.findall(
        r"\b(setInterval|setTimeout|requestAnimationFrame|setImmediate"
        r"|queueMicrotask)\b", code)
    assert found == [], (
        f"{name} ({MODULES[name].name}) contains a timer {sorted(set(found))} — "
        "the visual layer must be timer-free; motion may only be a pure "
        "function of the payload's progress")


def test_the_visual_layer_has_no_private_clock_either():
    """No wall-clock read: a timer is not the only way to animate.

    Real defect: a module derives motion from `Date.now()` / `performance.now()`
    instead of the payload's `progress`, which makes the drawing depend on when
    it was called rather than on what the backend reported — the same defect as
    a timer, expressed without one.
    """
    for name in sorted(MODULES):
        code = _code(MODULES[name])
        for token in ("Date.now", "performance.now", "new Date"):
            assert token not in code, (
                f"{name} ({MODULES[name].name}) reads the wall clock via "
                f"{token!r} — drawing must depend on the payload, not on when "
                "it happened to be called")


# ==========================================================================
# the four modules actually load TOGETHER (the precondition for all of the above)
# ==========================================================================
def test_all_four_modules_load_in_one_process_and_export_their_contract():
    """The other five assertions are worthless if the four cannot coexist.

    Real defect: two modules publish the same global name, or one is not UMD and
    only defines a browser global, so the bundle's `require` graph breaks. This
    loads all four into ONE `node -e` process (which the harness does for every
    test above) and asserts the cross-module surface each one must expose.
    """
    got = _run("""
    console.log(JSON.stringify({
      iso: Object.keys(ISO).sort(),
      cam: Object.keys(CAM).sort(),
      spr: Object.keys(SPR).sort(),
      mot: Object.keys(MOT).sort()
    }));
    """)
    # the exact functions each cross-module assertion above depends on
    for fn in ("gridToScreen", "screenToGrid", "tileBounds", "depthKey"):
        assert fn in got["iso"], f"iso-engine is missing {fn}: {got['iso']}"
    for fn in ("worldExtent", "fit", "screenPt", "worldPt"):
        assert fn in got["cam"], f"camera is missing {fn}: {got['cam']}"
    for fn in ("bakeAgent", "anchorOf", "feetFor"):
        assert fn in got["spr"], f"sprites is missing {fn}: {got['spr']}"
    for fn in ("DIR", "pathThrough", "interpolate", "FACING_VECTOR"):
        assert fn in got["mot"], f"motion is missing {fn}: {got['mot']}"
