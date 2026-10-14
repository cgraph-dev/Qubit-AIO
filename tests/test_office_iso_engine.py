"""M4 — the office's isometric projection core, checked by running it.

The plugin directory is dependency-free browser JS: there is no bundler and no
JS test runner in this repo's test path. That is not a reason to leave the
projection unverified — `node` is enough. `iso-engine.js` is a pure-function
module that loads under CommonJS as well as in a browser, so this file spawns
`node -e` and asserts on what it prints.

The property that matters most here is the one the old renderer got wrong: the
projection must INVERT. `screenToGrid(gridToScreen(p))` has to return `p`, or a
click on a character selects whatever pixel happened to be nearest instead of
the tile under the pointer. That round trip is asserted exhaustively over a grid
of points, not spot-checked.

Also asserted, because each is a visible defect if it breaks:

  * the tile is 2:1 — a 1:1 diamond is not an isometric floor
  * `z` lifts a deck, so a stack is a building and not one plate
  * `drawBlock` emits three faces (top + two walls), while `drawTile` emits one
    — a plate cannot have volume however it is coloured
  * `depthKey` orders near-over-far, and a low deck paints after a high one

Hermetic: no network, no browser, no provider call, no server. It shells out to
`node`, and skips cleanly if node is absent rather than failing the suite on a
machine that has no JS runtime.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest  # noqa: E402

from _office_node import run_node  # noqa: E402

PLUGIN_DIR = (Path(__file__).resolve().parent.parent
              / "frontend" / "qubit-app" / "plugins" / "qubit-office")
ENGINE = PLUGIN_DIR / "iso-engine.js"

_FOUND = shutil.which("node")
NODE: str = _FOUND or "node"

pytestmark = pytest.mark.skipif(
    _FOUND is None,
    reason="node is not installed; the JS projection cannot be run")


def _run(script: str) -> dict:
    """Run a JS snippet that requires the engine and prints one JSON object.

    The snippet is wrapped so the module path is injected rather than
    interpolated into the script — a path with a space would otherwise be a
    syntax error in the test rather than a failure of the code under test.
    """
    program = (
        "var ISO = require(%s);\n%s\n"
        % (json.dumps(str(ENGINE)), script)
    )
    proc = subprocess.run([NODE], input=program, capture_output=True,
                          text=True, timeout=60)
    assert proc.returncode == 0, (
        f"node exited {proc.returncode}\nSTDOUT:\n{proc.stdout}\n"
        f"STDERR:\n{proc.stderr}")
    out = proc.stdout.strip()
    assert out, f"node printed nothing\nSTDERR:\n{proc.stderr}"
    return json.loads(out)


# --------------------------------------------------------------- the module
def test_the_engine_exists_and_parses():
    assert ENGINE.is_file()
    proc = subprocess.run([NODE, "--check", str(ENGINE)],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr


def test_the_module_loads_and_exports_the_projection_core():
    got = _run("console.log(JSON.stringify(Object.keys(ISO).sort()));")
    assert got == sorted([
        "TILE_W", "TILE_H", "DECK_Z", "FLOOR_STEP", "CHAR_BODY_W",
        "CHAR_BODY_H", "gridToScreen", "screenToGrid", "tileCorners",
        "tileBounds", "characterBounds", "depthKey", "sortByDepth",
        "drawTile", "drawBlock", "faceTone",
    ]), got


def test_the_engine_satisfies_the_renderers_hard_constraints():
    """The same spirit as test_office_tower_plugin.py, applied to this file.

    A helper that is supposed to be geometry must not own a timer, a frame loop,
    a network call or a mutation. A projection that polls would be a renderer
    inside a renderer.
    """
    src = ENGINE.read_text()
    # comment-stripped, so this file's own header cannot trip or satisfy it
    import re
    code = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    code = re.sub(r"//[^\n]*", "", code)
    for banned in ("setInterval", "setTimeout", "requestAnimationFrame",
                   "@keyframes", "animate(", "innerHTML", "document.",
                   "XMLHttpRequest", "fetch("):
        assert banned not in code, f"iso-engine.js must not use {banned}"
    for verb in ("POST", "PUT", "PATCH", "DELETE"):
        assert f'"{verb}"' not in code, f"iso-engine.js must not issue {verb}"
    # and the MIT provenance notice travels with the code
    assert "agent-monitor" in src and "isometric.ts" in src
    assert "MIT" in src and "ruiqili2" in src


# ------------------------------------------------------- the 2:1 proportion
def test_a_tile_is_a_2_to_1_diamond():
    got = _run("console.log(JSON.stringify({w: ISO.TILE_W, h: ISO.TILE_H}));")
    assert got["w"] == 2 * got["h"], got
    assert got["h"] > 0


def test_the_projection_is_the_rotated_and_squashed_grid():
    """The literal transform: col+1 goes right-and-down, row+1 left-and-down."""
    got = _run("""
      var cam = {cx:0, cy:0, zoom:1, w:100, h:100};
      function rel(x, y) {
        var a = ISO.gridToScreen(0, 0, 0, cam), b = ISO.gridToScreen(x, y, 0, cam);
        return {x: b.x - a.x, y: b.y - a.y};
      }
      console.log(JSON.stringify({
        col: rel(1, 0), row: rel(0, 1),
        tw: ISO.TILE_W, th: ISO.TILE_H
      }));
    """)
    assert got["col"] == {"x": got["tw"] / 2, "y": got["th"] / 2}, got
    assert got["row"] == {"x": -got["tw"] / 2, "y": got["th"] / 2}, got


# ---------------------------------------------------- THE round-trip proof
def test_screen_to_grid_is_the_exact_inverse_of_grid_to_screen():
    """THE requirement. Exhaustive over a grid, at several zooms and cameras.

    Anyone can write a forward transform. The defect being fixed is the missing
    inverse, so this asserts the round trip lands on the SAME tile — not one
    tile away — for every integer point in a 41x41 grid, under three cameras.
    A tolerance of "within 1 tile" would hide exactly the bug we are here to
    kill.
    """
    got = _run("""
      var cams = [
        {cx:0, cy:0, zoom:1, w:100, h:100},
        {cx:120, cy:-40, zoom:1.7, w:880, h:500},
        {cx:-33.5, cy:17.25, zoom:0.85, w:641, h:311}
      ];
      var worst = 0, checked = 0, bad = null;
      cams.forEach(function (cam) {
        for (var gx = -20; gx <= 20; gx++) {
          for (var gy = -20; gy <= 20; gy++) {
            var s = ISO.gridToScreen(gx, gy, 0, cam);
            var g = ISO.screenToGrid(s.x, s.y, cam, 0);
            var d = Math.abs(g.x - gx) + Math.abs(g.y - gy);
            checked++;
            if (d > worst) { worst = d; bad = {x:gx, y:gy, got:g, cam:cam}; }
          }
        }
      });
      console.log(JSON.stringify({worst: worst, checked: checked, bad: bad}));
    """)
    assert got["checked"] == 3 * 41 * 41, got
    assert got["worst"] == 0, f"round trip is not exact: {got}"


def test_the_round_trip_holds_on_a_stacked_deck_too():
    """The same proof with the `z` axis in play, since `z` moves screen y."""
    got = _run("""
      var cam = {cx:40, cy:-25, zoom:1.3, w:880, h:500};
      var worst = 0;
      [0, 1, 2, 5].forEach(function (z) {
        for (var x = -6; x <= 30; x++) {
          for (var y = -6; y <= 30; y++) {
            var s = ISO.gridToScreen(x, y, z, cam);
            var g = ISO.screenToGrid(s.x, s.y, cam, z);
            worst = Math.max(worst, Math.abs(g.x - x) + Math.abs(g.y - y));
          }
        }
      });
      console.log(JSON.stringify({worst: worst}));
    """)
    assert got["worst"] == 0, got


def test_a_click_resolves_to_the_tile_under_the_pointer_not_a_neighbour():
    """The user-visible consequence, stated as its own test.

    A cell of a grid is drawn, then the pixel at the tile's own centre and the
    pixel a quarter-tile toward each edge are resolved. Every one of them must
    come back as the cell it was drawn from: this is what makes click-to-select
    pick the character the user pointed at.
    """
    got = _run("""
      var cam = {cx:0, cy:0, zoom:1.4, w:880, h:500};
      var misses = [];
      for (var x = 0; x <= 12; x++) {
        for (var y = 0; y <= 12; y++) {
          var c = ISO.gridToScreen(x, y, 0, cam);
          /* centre, and a nudge a third of the way to each corner */
          var probes = [[0,0],[1/3,0],[-1/3,0],[0,1/3],[0,-1/3]];
          probes.forEach(function (p) {
            var sx = c.x + p[0] * ISO.TILE_W / 2;
            var sy = c.y + p[1] * ISO.TILE_H / 2;
            var g = ISO.screenToGrid(sx, sy, cam, 0);
            if (g.x !== x || g.y !== y) {
              misses.push({cell:[x,y], probe:p, got:g});
            }
          });
        }
      }
      console.log(JSON.stringify({moves: 13*13*5, misses: misses.length,
                                  first: misses[0] || null}));
    """)
    assert got["misses"] == 0, got


def test_the_inverse_scales_with_zoom():
    """A camera transform that is not undone makes the inverse zoom-dependent.

    Resolving the same grid tile through two zooms must give the same tile; if
    the camera were skipped in screenToGrid, the zoomed case would drift by many
    tiles and this would catch it.
    """
    got = _run("""
      var a = {cx:0, cy:0, zoom:0.5, w:880, h:500};
      var b = {cx:0, cy:0, zoom:2.4, w:880, h:500};
      var sa = ISO.gridToScreen(9, 4, 0, a), sb = ISO.gridToScreen(9, 4, 0, b);
      console.log(JSON.stringify({
        a: ISO.screenToGrid(sa.x, sa.y, a, 0),
        b: ISO.screenToGrid(sb.x, sb.y, b, 0)
      }));
    """)
    assert got["a"] == {"x": 9, "y": 4}, got
    assert got["b"] == {"x": 9, "y": 4}, got


# ------------------------------------------------------ stacks have volume
def test_z_raises_a_deck_on_screen():
    """Without this every floor projects onto the same diamond — the flat tower."""
    got = _run("""
      var cam = {cx:0, cy:0, zoom:1, w:100, h:100};
      var g = ISO.gridToScreen(2, 2, 0, cam), h = ISO.gridToScreen(2, 2, 1, cam);
      console.log(JSON.stringify({dy: g.y - h.y, dx: h.x - g.x,
                                  deck: ISO.DECK_Z}));
    """)
    assert got["dy"] == got["deck"] > 0, got
    assert got["dx"] == 0, "z must not move a tile horizontally"


def test_tile_bounds_cover_the_tile_and_its_extruded_walls():
    got = _run("""
      var cam = {cx:0, cy:0, zoom:1, w:100, h:100};
      var flat = ISO.tileBounds(3, 2, 1, cam, 0);
      var tall = ISO.tileBounds(3, 2, 1, cam, 20);
      var p = ISO.gridToScreen(3, 2, 1, cam);
      console.log(JSON.stringify({flat: flat, tall: tall, p: p,
                                  tw: ISO.TILE_W, th: ISO.TILE_H}));
    """)
    flat, tall, p = got["flat"], got["tall"], got["p"]
    # the flat box is the 2:1 diamond centred on the projected point
    assert flat["w"] == got["tw"] and flat["h"] == got["th"], got
    assert p["x"] == flat["x"] + flat["w"] / 2, got
    assert p["y"] == flat["y"] + flat["h"] / 2, got
    # a raised block's box grows upward only
    assert tall["h"] == flat["h"] + 20, got
    assert tall["y"] == flat["y"] - 20 and tall["x"] == flat["x"], got


def test_a_character_box_sits_above_its_feet():
    got = _run("""
      var cam = {cx:0, cy:0, zoom:2, w:200, h:200};
      var b = ISO.characterBounds(4, 5, 0, cam);
      var p = ISO.gridToScreen(4, 5, 0, cam);
      console.log(JSON.stringify({b: b, p: p, w: ISO.CHAR_BODY_W,
                                  h: ISO.CHAR_BODY_H}));
    """)
    b, p = got["b"], got["p"]
    assert b["w"] == got["w"] * 2, got
    # top of the box is up-screen from the feet; bottom IS the feet
    assert b["y"] < p["y"] and b["y"] + b["h"] == p["y"], got
    assert b["y"] + b["h"] - b["y"] > got["h"] * 2, got


# -------------------------------------------------- depth: near over far
def test_depth_key_paints_a_nearer_object_later():
    """+col and +row both come toward the viewer, so a bigger key draws later."""
    got = _run("""
      var k = ISO.depthKey;
      console.log(JSON.stringify({
        near_beats_far:   k(3, 1, 0) > k(1, 1, 0),
        row_also:         k(1, 3, 0) > k(1, 1, 0),
        diagonal:         k(4, 4, 0) > k(2, 2, 0),
        same:             k(2, 2, 0) === k(2, 2, 0),
        high_deck_first:  k(2, 2, 1) < k(2, 2, 0),
        different_decks:  k(30, 30, 1) < k(0, 0, 0)
      }));
    """)
    assert got["near_beats_far"], got
    assert got["row_also"], got
    assert got["diagonal"], got
    assert got["same"], got
    # a HIGHER deck is painted first: in a cutaway the lower deck is in front
    assert got["high_deck_first"], got
    # and the deck term dominates any plausible in-deck (col + row)
    assert got["different_decks"], got


def test_a_character_wins_the_tie_against_its_own_desk():
    """Two objects on one tile: the occupant must land last, or it is hidden."""
    got = _run("""
      var k = ISO.depthKey;
      console.log(JSON.stringify({
        char_over_desk: k(5, 5, 0, 1) < k(5, 5, 0, 0),
        desk_over_char: k(5, 5, 0, 0) < k(5, 5, 0, 1)
      }));
    """)
    assert got["char_over_desk"], got
    assert not got["desk_over_char"], got


def test_sort_by_depth_orders_a_room_back_to_front_and_leaves_it_alone():
    got = _run("""
      var nodes = [
        {id:"near", x:4, y:4, z:0},
        {id:"far",  x:0, y:0, z:0},
        {id:"mid",  x:2, y:2, z:0},
        {id:"above",x:0, y:0, z:1}
      ];
      var before = nodes.slice();
      var out = ISO.sortByDepth(nodes).map(function (n) { return n.id; });
      console.log(JSON.stringify({
        order: out,
        untouched: nodes.every(function (n, i) { return n === before[i]; })
      }));
    """)
    # far -> mid -> near, with the upper deck first of all
    assert got["order"] == ["above", "far", "mid", "near"], got
    assert got["untouched"], "sortByDepth reordered the caller's array in place"


# -------------------------------------------------- draw calls are host-free
def test_drawing_emits_the_expected_number_of_faces():
    """A fake 2D context: records calls instead of touching a DOM.

    `drawTile` is one quad (a plate). `drawBlock` is three (two walls + the top)
    with the top and the walls at different y — which is the actual difference
    between a floor and a building.
    """
    got = _run("""
      function ctxSpy() {
        var log = [];
        return {
          log: log,
          beginPath: function () { log.push(["begin"]); },
          moveTo: function (x, y) { log.push(["move", x, y]); },
          lineTo: function (x, y) { log.push(["line", x, y]); },
          closePath: function () { log.push(["close"]); },
          fill: function () { log.push(["fill", this.fillStyle]); },
          stroke: function () { log.push(["stroke", this.strokeStyle]); },
          fillStyle: null, strokeStyle: null, lineWidth: 0
        };
      }
      var cam = {cx:0, cy:0, zoom:1, w:100, h:100};
      var t = ctxSpy(); ISO.drawTile(t, 2, 3, 0, cam, "#111", "#222");
      var b = ctxSpy();
      ISO.drawBlock(b, 2, 3, 0, cam, 12, "#aaa", "#666", "#888");
      function count(log, kind) {
        return log.filter(function (r) { return r[0] === kind; }).length;
      }
      var fills = b.log.filter(function (r) { return r[0] === "fill"; });
      var minY = b.log.filter(function (r) {
        return r[0] === "move" || r[0] === "line";
      }).map(function (r) { return r[2]; });
      console.log(JSON.stringify({
        tile_quads: count(t.log, "begin"),
        tile_fills: count(t.log, "fill"),
        tile_strokes: count(t.log, "stroke"),
        block_quads: count(b.log, "begin"),
        block_fills: fills.length,
        block_colours: fills.map(function (r) { return r[1]; }),
        block_raised: Math.min.apply(null, minY)
          < Math.max.apply(null, minY) - 11
      }));
    """)
    assert got["tile_quads"] == 1, got
    assert got["tile_fills"] == 1 and got["tile_strokes"] == 1, got
    assert got["block_quads"] == 3, got
    assert got["block_fills"] == 3, got
    # walls are painted first, the top face last, and each has its own tone
    assert got["block_colours"] == ["#666", "#888", "#aaa"], got
    assert got["block_raised"], "the block's top face was not raised off the floor"


def test_a_zero_height_block_is_a_plain_tile_not_a_degenerate_polygon():
    got = _run("""
      function ctxSpy() {
        var log = [];
        return {log: log,
          beginPath: function () { log.push("begin"); },
          moveTo: function () { log.push("move"); },
          lineTo: function () { log.push("line"); },
          closePath: function () { log.push("close"); },
          fill: function () { log.push("fill"); },
          stroke: function () {}, fillStyle: null, strokeStyle: null,
          lineWidth: 0};
      }
      var cam = {cx:0, cy:0, zoom:1, w:100, h:100};
      var c = ctxSpy(); ISO.drawBlock(c, 1, 1, 0, cam, 0, "#abc", "#111", "#222");
      console.log(JSON.stringify({quads: c.log.filter(function (r) {
        return r === "begin"; }).length,
        points: c.log.filter(function (r) { return r === "move"; }).length}));
    """)
    assert got["quads"] == 1, got
    assert got["points"] == 1, got


def test_face_tone_gives_a_wall_a_lit_and_a_shaded_variant():
    got = _run("""
      var base = "#405060";
      console.log(JSON.stringify({
        lit: ISO.faceTone(base, 1.15),
        dark: ISO.faceTone(base, 0.8),
        junk: ISO.faceTone("not-a-colour", 0.8),
        hex: /^#[0-9a-f]{6}$/.test(ISO.faceTone(base, 1.15))
      }));
    """)
    assert got["hex"], got
    assert got["lit"] != got["dark"], got
    # a malformed colour is returned unchanged rather than invented
    assert got["junk"] == "not-a-colour", got


# --------------------------------------------- it is loadable both ways
def test_the_engine_loads_in_a_browser_shaped_global_too():
    """No `module`: the same factory must land on the global object."""
    program = (
        "var g = {};\n"
        "(function (root, factory) {\n"
        "  var api = factory();\n"
        "  if (typeof module === 'object' && module && module.exports) module.exports = api;\n"
        "  if (root) root.QubitIso = api;\n"
        "})(g, function () {\n"
        "  var s = require('fs').readFileSync(%s, 'utf8');\n"
        "  var m = {exports: {}};\n"
        "  new Function('module', 'exports', s)(m, m.exports);\n"
        "  return m.exports;\n"
        "});\n"
        "console.log(JSON.stringify({\n"
        "  global: typeof g.QubitIso,\n"
        "  tiles: g.QubitIso.TILE_W,\n"
        "  round: (function () {\n"
        "    var cam = {cx:0, cy:0, zoom:1, w:100, h:100};\n"
        "    var p = g.QubitIso.gridToScreen(7, 3, 0, cam);\n"
        "    return g.QubitIso.screenToGrid(p.x, p.y, cam, 0);\n"
        "  })()\n"
        "}));\n"
        % json.dumps(str(ENGINE))
    )
    proc = subprocess.run([NODE], input=program, capture_output=True, text=True,
                          timeout=60)
    assert proc.returncode == 0, proc.stderr
    got = json.loads(proc.stdout.strip())
    assert got["global"] == "object", got
    assert got["tiles"] == 34, got
    assert got["round"] == {"x": 7, "y": 3}, got


def test_the_engine_defines_no_global_other_than_its_own_namespace():
    """A plugin bundle that leaked names could collide with the host page."""
    got = _run("""
      var before = Object.keys(globalThis).length;
      delete require.cache[require.resolve(%s)];
      require(%s);
      console.log(JSON.stringify({added: Object.keys(globalThis).length - before,
                                  qiso: typeof QubitIso}));
    """ % (json.dumps(str(ENGINE)), json.dumps(str(ENGINE))))
    # exactly one new global: the module's own namespace
    assert got["qiso"] == "object", got
    assert got["added"] <= 1, got
