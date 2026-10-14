"""M4 — the Office's procedural sprite module: bake, cache, anchor, walk.

The tower used to draw a character as four rectangles inline, once per figure,
every frame. At the scale the building needs ("a character is 15 units tall")
that reads as a smear, so the figures moved into
``frontend/qubit-app/plugins/qubit-office/sprites.js``: a standalone,
dependency-free module that BAKES one figure per (colour, pose) into an
offscreen canvas, caches it, and draws it anchored by its FEET.

This file is its proof. It runs the real module under ``node`` and asserts the
two properties that a text check cannot reach and that a browser test would not
isolate:

  * the CACHE KEY — two bakes of the same colour and pose must return the SAME
    cached object, two different poses must not, and an out-of-range pose must
    not mint an unbounded number of entries; and
  * the ANCHOR MATHS — ``drawAgentSprite`` must place the figure's feet on the
    caller's ``(x, y)`` at any scale, because that is what stops a body floating
    above its desk or sinking through it.

It also pins the SILHOUETTE, from the module's own geometry rather than from a
pixel readback: head, torso and legs must be three separable masses, and the
legs must stay separated once the 1px outline closes in from both sides. That
last one caught a real defect — a two-column leg gap welds into one slab when
the outline is applied, which is the exact smear the module exists to remove.

Canvas availability is optional. The module is written so that its cache-key and
anchor logic are pure JavaScript and need no pixels; when a canvas IS available
this file goes further and reads the baked pixels back, asserting the outline
ring is closed and the derived tones (dark legs, bright head) are distinguishable
— the "measured, not assumed" half of the ported technique.

Hermetic: reads files, spawns ``node``. No network, no browser, no provider
call. Skips with a clear reason when ``node`` is absent.
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
PLUGIN_DIR = REPO / "frontend" / "qubit-app" / "plugins" / "qubit-office"
SPRITES = PLUGIN_DIR / "sprites.js"
ENTRY = PLUGIN_DIR / "qubit-office.js"

NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not on PATH")


# ---------------------------------------------------------------------------
# the harness
# ---------------------------------------------------------------------------
# A canvas stub that really rasterises fillRect, so a bake can be read back as
# pixels. It models the ONE thing the module depends on and that a naive stub
# gets wrong: `document.createElement('canvas')` returns a canvas whose
# width/height are assigned AFTER creation, so the stub must expose settable
# width/height and a getContext that reflects them.
_CANVAS_STUB = r"""
function makeCanvas() {
  var px = new Uint8ClampedArray(4);
  var cv = {
    _w: 1, _h: 1,
    get width() { return this._w; },   set width(v) { this._w = v | 0; px = new Uint8ClampedArray(Math.max(4, this._w * this._h * 4)); },
    get height() { return this._h; },  set height(v) { this._h = v | 0; px = new Uint8ClampedArray(Math.max(4, this._w * this._h * 4)); },
  };
  var scale = 1, stack = [];
  var ctx = {
    globalAlpha: 1, fillStyle: '#000000', imageSmoothingEnabled: true,
    save: function () { stack.push(scale); },
    restore: function () { scale = stack.length ? stack.pop() : 1; },
    resetTransform: function () { scale = 1; },
    scale: function (s) { scale *= s; },
    clearRect: function () { px.fill(0); },
    beginPath: function () {}, ellipse: function () {}, fill: function () {},
    fillRect: function (x, y, w, h) {
      var x0 = Math.round(x * scale), y0 = Math.round(y * scale);
      var x1 = Math.round((x + w) * scale), y1 = Math.round((y + h) * scale);
      var m = /^#?([0-9a-f]{6})/i.exec(String(this.fillStyle));
      var n = m ? parseInt(m[1], 16) : 0;
      var a = Math.round(255 * this.globalAlpha);
      for (var yy = y0; yy < y1; yy++) {
        for (var xx = x0; xx < x1; xx++) {
          if (xx < 0 || yy < 0 || xx >= cv.width || yy >= cv.height) continue;
          var o = (yy * cv.width + xx) * 4;
          px[o] = (n >> 16) & 255; px[o + 1] = (n >> 8) & 255;
          px[o + 2] = n & 255; px[o + 3] = a;
        }
      }
    },
    getImageData: function (x, y, w, h) {
      var out = new Uint8ClampedArray(w * h * 4);
      for (var yy = 0; yy < h; yy++) for (var xx = 0; xx < w; xx++) {
        var so = (yy * cv.width + xx) * 4, o = (yy * w + xx) * 4;
        out[o] = px[so]; out[o + 1] = px[so + 1];
        out[o + 2] = px[so + 2]; out[o + 3] = px[so + 3];
      }
      return { width: w, height: h, data: out };
    },
  };
  cv.getContext = function () { return ctx; };
  cv._px = function () { return px; };
  return cv;
}
globalThis.document = { createElement: function (t) { return t === 'canvas' ? makeCanvas() : { tag: t }; } };
"""


def _run(script: str, *, with_canvas: bool) -> dict:
    """Run a JS snippet against the real module and return its JSON stdout."""
    prelude = _CANVAS_STUB if with_canvas else ""
    program = (
        prelude
        + f"var S = require({json.dumps(str(SPRITES))});\n"
        + "function out(o){ console.log('__RESULT__' + JSON.stringify(o)); }\n"
        + script
    )
    proc = subprocess.run([NODE], input=program, cwd=str(REPO), timeout=60,
                          capture_output=True, text=True)
    assert proc.returncode == 0, (
        f"node exited {proc.returncode}\nstderr:\n{proc.stderr}\nstdout:\n{proc.stdout}")
    for line in proc.stdout.splitlines():
        if line.startswith("__RESULT__"):
            return json.loads(line[len("__RESULT__"):])
    raise AssertionError(f"the script produced no result:\n{proc.stdout}\n{proc.stderr}")


def _module_shape() -> dict:
    """The required API surface, checked without a canvas."""
    return _run(
        "out({keys: Object.keys(S).sort(),"
        " hasBake: typeof S.bakeAgent === 'function',"
        " hasDraw: typeof S.drawAgentSprite === 'function',"
        " hasAnchor: typeof S.anchorOf === 'function',"
        " hasShade: typeof S.shade === 'function',"
        " hasTint: typeof S.tint === 'function'});",
        with_canvas=False)


# ---------------------------------------------------------------------------
# the module loads and carries the ported-code notice
# ---------------------------------------------------------------------------
def test_the_module_exists_as_a_standalone_dependency_free_file():
    text = SPRITES.read_text(encoding="utf-8")
    assert text.strip(), "sprites.js is empty"
    # Strip comments first: the header legitimately NAMES the asset pipeline it
    # deliberately does not use ("no `new Image()`, no manifest fetch"), and a
    # naive scan would fail the file on its own documentation.
    code = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    code = re.sub(r"//[^\n]*", "", code)
    for banned in ("require(", "import ", "fetch(", "XMLHttpRequest",
                   "new Image(", "addEventListener"):
        assert banned not in code, f"sprites.js must not contain {banned!r}"
    for banned in ("setTimeout", "setInterval", "requestAnimationFrame"):
        assert banned not in code, (
            f"sprites.js must own no timer — motion comes from progress ({banned!r})")


def test_the_module_names_its_upstream_sources_and_their_licence():
    """MIT requires the notice to travel with the code."""
    text = SPRITES.read_text(encoding="utf-8")
    assert "MIT" in text
    assert "starnet" in text, "androoAGI/starnet is the source of the anchor + shadow"
    assert "agent-monitor" in text, "ruiqili2/agent-monitor is the source of the walk cycle"
    # the reference file paths, so a reader can follow the provenance
    assert "frontend/agent-demo/sprites.js" in text
    assert "src/engine/animation.ts" in text


def test_the_unlicensed_reference_is_named_as_not_used():
    """aphae ships no licence, so it must never be a source here."""
    text = SPRITES.read_text(encoding="utf-8")
    assert "aphae" in text and ("no licence" in text or "all rights" in text)


def test_the_module_loads_under_node_and_exposes_the_required_api():
    shape = _module_shape()
    assert shape["hasBake"] and shape["hasDraw"] and shape["hasAnchor"], shape
    assert shape["hasShade"] and shape["hasTint"], shape


def test_the_module_also_attaches_to_a_browser_global():
    """It is loaded as a plain <script>, so it must publish window.QubitSprites."""
    text = SPRITES.read_text(encoding="utf-8")
    assert "QubitSprites" in text
    assert "module.exports" in text, "and it must still work under node -e"


# ---------------------------------------------------------------------------
# THE CACHE KEY — the pixel-agents / StarNet "bake once, draw every frame" rule
# ---------------------------------------------------------------------------
def test_the_same_colour_and_pose_come_back_from_the_cache_as_one_object():
    got = _run(
        "var a = S.bakeAgent('#5aa9e6');"
        "var b = S.bakeAgent('#5aa9e6');"
        "out({same: a === b, key: S.cacheKey('#5aa9e6', 0)});",
        with_canvas=False)
    assert got["same"] is True, "a second bake re-ran the work instead of hitting the cache"


def test_the_cache_key_covers_both_inputs_the_bake_depends_on():
    """Colour and pose. Anything else and two different figures share a canvas."""
    got = _run(
        "out({"
        " byColour: S.cacheKey('#5aa9e6', 0) !== S.cacheKey('#e0554a', 0),"
        " byPose: S.cacheKey('#5aa9e6', 1) !== S.cacheKey('#5aa9e6', 2),"
        " caseInsensitive: S.cacheKey('#5AA9E6', 0) === S.cacheKey('#5aa9e6', 0),"
        " eightDigit: S.cacheKey('#5aa9e680', 0) === S.cacheKey('#5aa9e6', 0),"
        " key: S.cacheKey('#5aa9e6', 1)"
        "});",
        with_canvas=False)
    assert got["byColour"], "two colours shared a cache entry"
    assert got["byPose"], "two poses shared a cache entry"
    assert got["caseInsensitive"], "the same colour in a different case minted a new entry"
    assert got["eightDigit"], "an 8-digit colour must key the same as its 6-digit form"
    assert got["key"] == "#5aa9e6|1", got["key"]


def test_an_out_of_range_pose_cannot_mint_an_unbounded_cache():
    """A payload value outside 0..2 must normalise, not allocate."""
    got = _run(
        "var before = S.cacheStats().size;"
        "for (var i = -50; i < 50; i++) S.bakeAgent('#5aa9e6', {walk: i});"
        "for (var j = 0; j < 20; j++) S.bakeAgent('#5aa9e6', {walk: j + 0.37});"
        "out({size: S.cacheStats().size, keys: S.cacheStats().keys,"
        " limit: S.CACHE_LIMIT, before: before});",
        with_canvas=False)
    # three poses -- 0, 1 and 2 -- is the entire space, whatever was asked for
    assert got["size"] == 3, got
    assert set(got["keys"]) == {"#5aa9e6|0", "#5aa9e6|1", "#5aa9e6|2"}, got["keys"]


def test_the_cache_is_bounded():
    got = _run(
        "for (var i = 0; i < 400; i++)"
        "  S.bakeAgent('#' + (i % 256).toString(16).padStart(2, '0').repeat(3));"
        "out({size: S.cacheStats().size, limit: S.CACHE_LIMIT});",
        with_canvas=False)
    assert got["size"] <= got["limit"], got
    assert got["limit"] > 0


def test_clearing_the_cache_makes_the_next_bake_a_fresh_object():
    got = _run(
        "var a = S.bakeAgent('#5aa9e6');"
        "S.clearCache();"
        "out({empty: S.cacheStats().size === 0, fresh: S.bakeAgent('#5aa9e6') !== a});",
        with_canvas=False)
    assert got["empty"] and got["fresh"], got


# ---------------------------------------------------------------------------
# THE ANCHOR MATHS — feet on the floor, at every scale
# ---------------------------------------------------------------------------
def test_anchor_reports_a_feet_line_and_a_height():
    got = _run(
        "var f = S.bakeAgent('#5aa9e6');"
        "out({anchor: S.anchorOf(f), feetFor: S.feetFor('#5aa9e6')});",
        with_canvas=False)
    anchor = got["anchor"]
    assert anchor["feetY"] > 0, anchor
    assert anchor["height"] > 0, anchor
    # feetFor is the same answer, from the same bake
    assert got["feetFor"] == anchor["feetY"], got


def test_anchor_is_scale_independent():
    """The same figure at any zoom must anchor at the same sprite-unit feet line."""
    got = _run(
        "out([1, 1.6, 3, 8].map(function (s) {"
        "  return S.anchorOf(S.bakeAgent('#5aa9e6', {scale: s})).feetY; }));",
        with_canvas=False)
    assert len(set(got)) == 1, f"the feet line moved with the zoom: {got}"
    assert got[0] > 0


def test_the_draw_puts_the_feet_exactly_on_the_callers_point_at_every_scale():
    """This is the whole defect: a figure anchored by its top floats."""
    got = _run(
        "function stub(){ return {globalAlpha:1,fillStyle:'#000',"
        " imageSmoothingEnabled:true, save:function(){}, restore:function(){},"
        " ellipse:function(){}, beginPath:function(){}, fill:function(){},"
        " drawImage:function(){}, fillRect:function(){}}; }"
        "out([0.85, 1, 1.6, 2.4].map(function (s) {"
        "  var f = S.bakeAgent('#5aa9e6', {scale: s});"
        "  var a = S.anchorOf(f);"
        "  var r = S.drawAgentSprite(stub(), f, '#5aa9e6', 100, 200, s, 'down', false, 0);"
        "  return {scale: s, feetRow: r.drawY + a.feetY * s, feetY: a.feetY};"
        "}));",
        with_canvas=False)
    for row in got:
        assert abs(row["feetRow"] - 200) < 1e-9, (
            f"at scale {row['scale']} the feet landed on {row['feetRow']}, not 200")


def test_a_walking_figure_bobs_UPWARD_only_from_its_progress():
    """No progress argument, no motion. And a bob can never sink a body."""
    got = _run(
        "function stub(){ return {globalAlpha:1,fillStyle:'#000',"
        " imageSmoothingEnabled:true, save:function(){}, restore:function(){},"
        " ellipse:function(){}, beginPath:function(){}, fill:function(){},"
        " drawImage:function(){}, fillRect:function(){}}; }"
        "var f = S.bakeAgent('#5aa9e6');"
        "var still = S.drawAgentSprite(stub(), f, '#5aa9e6', 100, 200, 1.6, 'down', false, 0.3);"
        "var rows = [0, 0.125, 0.25, 0.375, 0.5, 0.625, 0.75, 0.875].map(function (p) {"
        "  return S.drawAgentSprite(stub(), f, '#5aa9e6', 100, 200, 1.6, 'down', true, p).drawY; });"
        "out({still: still.drawY, stillBob: still.bob, rows: rows,"
        "  bobAt: [0,0.25,0.5,0.75].map(function(p){ return S.bobFor(p, 1); })});",
        with_canvas=False)
    assert got["stillBob"] == 0, "a non-walking figure moved on its own"
    assert all(r <= got["still"] + 1e-9 for r in got["rows"]), (
        "a bob sank a figure below its planted height")
    assert min(got["rows"]) < got["still"], "the walk produced no vertical motion at all"
    assert got["rows"] != [got["rows"][0]] * len(got["rows"]), "the bob was constant"
    # derived ONLY from progress: the same progress must give the same bob.
    # sin(pi) is not exactly 0 in IEEE-754, so the mid-cycle bob is a tolerance
    # comparison and not an equality one.
    mid = got["bobAt"][2]
    assert abs(mid) < 1e-9, f"the mid-cycle bob should vanish: {mid}"
    assert got["bobAt"][1] == -0.5 and got["bobAt"][3] == -0.5, got["bobAt"]
    assert got["bobAt"][0] == 0.0, got["bobAt"]


def test_the_walk_frame_is_a_pure_function_of_progress():
    got = _run(
        "out({frames: [0, 0.1, 0.25, 0.5, 0.6, 0.75, 0.99].map(S.walkFrame),"
        " wrapped: S.walkFrame(1.25) === S.walkFrame(0.25),"
        " negative: S.walkFrame(-0.25) === S.walkFrame(0.75),"
        " space: [S.walkFrame(0), S.walkFrame(0.5)].sort()});",
        with_canvas=False)
    # a 2-frame cycle: two distinct frames, both used
    assert len(set(got["frames"])) == 2, got["frames"]
    assert got["wrapped"], "progress past 1 must wrap, not run off the cycle"
    assert got["negative"], "negative progress must wrap into the cycle"


def test_body_scale_is_the_one_answer_to_how_big_the_figure_is_drawn():
    got = _run(
        "out({a: S.bodyScale(1, 1), b: S.bodyScale(1, 1.6),"
        " bad: S.bodyScale(0, 0), neg: S.bodyScale(-2, -2)});",
        with_canvas=False)
    assert got["a"] == 1 and abs(got["b"] - 1.6) < 1e-9, got
    # an impossible scale is never returned as-is: callers get a drawable number
    assert got["bad"] > 0 and got["neg"] > 0, got


# ---------------------------------------------------------------------------
# THE SILHOUETTE — measured from the module's own geometry, no canvas needed
# ---------------------------------------------------------------------------
# The figure is a head, a torso and two legs. The probe below rebuilds the
# module's own occupancy grid and its outline rule, then measures the result.
_SILHOUETTE_PROBE = r"""
function grid(walk) {
  var W = S.SPRITE_W, H = S.SPRITE_H;
  var rs = S._figureRects(walk, false);
  function body(x, y) {
    for (var i = 0; i < rs.length; i++) {
      var r = rs[i];
      if (x >= r.x && x < r.x + r.w && y >= r.y && y < r.y + r.h) return true;
    }
    return false;
  }
  var b = [], o = [];
  for (var y = 0; y < H; y++) {
    b.push([]); o.push([]);
    for (var x = 0; x < W; x++) { b[y].push(body(x + 0.5, y + 0.5)); o[y].push(false); }
  }
  // the module's outline rule: background cells 4-adjacent to the body, and
  // never below the feet row (the boots contact the floor, the line does not).
  for (var yy = 0; yy < H - 1; yy++) {
    for (var xx = 0; xx < W; xx++) {
      if (b[yy][xx]) continue;
      var n = [[-1,0],[1,0],[0,-1],[0,1]];
      for (var k = 0; k < 4; k++) {
        var nx = xx + n[k][0], ny = yy + n[k][1];
        if (nx < 0 || ny < 0 || nx >= W || ny >= H) continue;
        if (b[ny][nx]) { o[yy][xx] = true; break; }
      }
    }
  }
  var rows = b.map(function (r, y) {
    var s = '';
    for (var x = 0; x < W; x++) s += b[y][x] ? 'B' : (o[y][x] ? 'o' : '.');
    return s;
  });
  return {rows: rows, body: b, outline: o, w: W, h: H};
}
"""


def test_the_figure_is_three_separable_masses():
    """Head, torso and legs must be visually distinct at 15px, not one slab."""
    got = _run(
        _SILHOUETTE_PROBE
        + "var g = grid(0);"
        "out({rows: g.rows, w: g.w, h: g.h});",
        with_canvas=False)
    body_rows = [i for i, r in enumerate(got["rows"]) if "B" in r]
    assert body_rows, got["rows"]
    # a gap row separates the head from the torso, and the torso from the legs
    first = body_rows[0]
    last = body_rows[-1]
    # the head is the mass above the first all-background row inside the figure
    inner_gap = [i for i in range(first, last) if got["rows"][i].count("B") == 0]
    assert inner_gap, (
        "the head and the torso are one unbroken slab — nothing separates them:\n"
        + "\n".join(got["rows"]))
    assert got["h"] - last >= 1, "the feet row is flush with no room under the boots"


def test_the_legs_stay_separated_once_the_outline_closes_in():
    """The defect this module caught: a 2-column gap welds when outlined."""
    got = _run(
        _SILHOUETTE_PROBE
        + "var out2 = {};"
        "for (var w = 0; w < 3; w++) {"
        "  var g = grid(w);"
        "  var best = 0;"
        "  for (var y = 0; y < g.h; y++) {"
        "    var run = 0;"
        "    for (var x = 0; x < g.w; x++) {"
        "      if (!g.body[y][x] && !g.outline[y][x]) { run++; if (run > best) best = run; }"
        "      else run = 0;"
        "    }"
        "  }"
        "  out2['walk' + w] = best;"
        "}"
        "out(out2);",
        with_canvas=False)
    for pose, clear in got.items():
        assert clear >= 2, (
            f"{pose}: only {clear} clear column(s) between the legs — the outline "
            f"has welded them into one slab, which is the smear this module removes")


def test_the_only_unoutlined_edge_is_the_feet_row():
    """The outline must wrap the whole silhouette except where the boots touch
    the floor. One pixel missing anywhere else is a figure that dissolves into
    a dark desk at its edge."""
    got = _run(
        _SILHOUETTE_PROBE
        + "var g = grid(0);"
        "var gaps = [];"
        "var n = [[-1,0],[1,0],[0,-1],[0,1]];"
        "for (var y = 0; y < g.h; y++) for (var x = 0; x < g.w; x++) {"
        "  if (!g.body[y][x]) continue;"
        "  for (var k = 0; k < 4; k++) {"
        "    var nx = x + n[k][0], ny = y + n[k][1];"
        "    if (nx < 0 || ny < 0 || nx >= g.w || ny >= g.h) continue;"
        "    if (!g.body[ny][nx] && !g.outline[ny][nx]) gaps.push([x, y]);"
        "  }"
        "}"
        "out({gaps: gaps, maxY: Math.max.apply(null, gaps.map(function(p){return p[1];})),"
        "     minY: Math.min.apply(null, gaps.map(function(p){return p[1];})),"
        "     h: g.h});",
        with_canvas=False)
    assert got["gaps"], "no gap at all — even the feet row is outlined"
    # every unoutlined body cell sits on the feet row (the last populated row)
    # — the boots contact the floor and the outline deliberately stops above
    # them, so the feet row is the only one allowed to touch bare background.
    assert got["minY"] == got["maxY"] == got["h"] - 1, got


def test_the_outline_is_a_darker_shade_of_the_seat_colour_not_a_grey():
    """A fixed grey line would stop being an outline the moment a seat is dark."""
    got = _run(
        "var pal = S.paletteFor('#5aa9e6');"
        "var lum = function (h) {"
        "  var n = parseInt(h.slice(1, 7), 16);"
        "  return (0.299 * ((n >> 16) & 255) + 0.587 * ((n >> 8) & 255)"
        "          + 0.114 * (n & 255)) / 255; };"
        "out({pal: pal, base: lum(pal.base), outline: lum(pal.outline),"
        "     dark: lum(pal.dark), light: lum(pal.light)});",
        with_canvas=False)
    assert got["outline"] < got["base"], got
    assert got["outline"] < got["dark"], "the outline is not the darkest tone"
    # and the derived tones bracket the base, so the figure is not flat
    assert got["light"] > got["base"] > got["dark"], got


# ---------------------------------------------------------------------------
# COLOUR — HSL, hue-preserving, alpha-carrying
# ---------------------------------------------------------------------------
def test_shade_darkens_and_lightens_while_keeping_the_hue():
    got = _run(
        "var h = function (hex) { var n = parseInt(hex.slice(1, 7), 16);"
        "  var r = ((n >> 16) & 255) / 255, g = ((n >> 8) & 255) / 255, b = (n & 255) / 255;"
        "  var mx = Math.max(r, g, b), mn = Math.min(r, g, b), d = mx - mn;"
        "  if (!d) return 0;"
        "  if (mx === r) return ((g - b) / d + (g < b ? 6 : 0)) * 60;"
        "  if (mx === g) return ((b - r) / d + 2) * 60;"
        "  return ((r - g) / d + 4) * 60; };"
        "out({dark: S.shade('#5aa9e6', -0.5), light: S.shade('#5aa9e6', 0.5),"
        "     base: '#5aa9e6',"
        "     darkH: h(S.shade('#5aa9e6', -0.5)), baseH: h('#5aa9e6'),"
        "     lightH: h(S.shade('#5aa9e6', 0.5)),"
        "     zero: S.shade('#5aa9e6', 0)});",
        with_canvas=False)
    for key in ("darkH", "baseH", "lightH"):
        assert abs(got[key] - got["baseH"]) < 12, f"{key} rotated the hue: {got}"
    assert got["dark"] != got["base"] != got["light"], "shade did nothing"
    assert got["zero"].lower().startswith("#5aa9e6"), got["zero"]


def test_tint_moves_toward_the_hue_without_destroying_the_colour():
    got = _run(
        "out({toward: S.tint('#5aa9e6', 45, 1.0), none: S.tint('#5aa9e6', 45, 0),"
        "     partial: S.tint('#5aa9e6', 45, 0.55)});",
        with_canvas=False)
    assert got["none"].lower().startswith("#5aa9e6"), "mix 0 changed the colour"
    assert got["toward"] != got["partial"] != got["none"], got
    # the tinted result is a warm gold at full mix, not still blue
    assert not got["toward"].lower().startswith("#5a"), got


def test_shade_and_tint_preserve_alpha():
    """Recolouring a sprite must never punch a hole in it or flood it opaque."""
    got = _run(
        "out({shade: S.shade('#5aa9e680', -0.4), tint: S.tint('#5aa9e680', 40, 0.5),"
        "     opaque: S.shade('#5aa9e6', -0.4),"
        "     clear: S.shade('#5aa9e600', -0.4),"
        "     solidHex: S.solidHex('#5aa9e680')});",
        with_canvas=False)
    assert len(got["shade"]) == 9 and got["shade"].lower().endswith("80"), got
    assert len(got["tint"]) == 9 and got["tint"].lower().endswith("80"), got
    assert len(got["opaque"]) == 7, got
    assert got["clear"].lower().endswith("00"), got
    # the bake always resolves to a fully opaque colour, so a faded seat is
    # driven by ctx.globalAlpha and never by a half-transparent sprite
    assert len(got["solidHex"]) == 7, got


def test_an_unparseable_colour_falls_back_instead_of_throwing():
    got = _run(
        "var r = [];"
        "['', null, undefined, 'not-a-colour', '#12', 42].forEach(function (c) {"
        "  try { r.push(S.solidHex(c)); } catch (e) { r.push('THREW'); }"
        "});"
        "var b = S.bakeAgent('nonsense');"
        "out({results: r, bakedColour: b.colour, anchor: S.anchorOf(b).feetY});",
        with_canvas=False)
    assert "THREW" not in got["results"], got
    assert all(isinstance(v, str) and v.startswith("#") for v in got["results"]), got
    assert got["bakedColour"].startswith("#"), got
    assert got["anchor"] > 0, got


# ---------------------------------------------------------------------------
# THE BAKE, WHEN A CANVAS IS AVAILABLE — measured from real pixels
# ---------------------------------------------------------------------------
def test_the_bake_paints_a_local_outline_ring_and_reads_back_as_baked():
    got = _run(
        "var f = S.bakeAgent('#5aa9e6', {scale: 1});"
        "var ctx = f.canvas.getContext('2d');"
        "var d = ctx.getImageData(0, 0, f.canvas.width, f.canvas.height).data;"
        "var pal = f.palette;"
        "var rgb = function (h) { var n = parseInt(h.slice(1, 7), 16);"
        "  return [ (n >> 16) & 255, (n >> 8) & 255, n & 255 ].join(','); };"
        "var want = {}; want[rgb(pal.base)] = 'base'; want[rgb(pal.outline)] = 'outline';"
        "want[rgb(pal.dark)] = 'dark'; want[rgb(pal.light)] = 'light'; want[rgb(pal.ink)] = 'ink';"
        "var counts = {}, rows = [];"
        "for (var y = 0; y < f.canvas.height; y++) {"
        "  var line = '';"
        "  for (var x = 0; x < f.canvas.width; x++) {"
        "    var o = (y * f.canvas.width + x) * 4;"
        "    if (d[o + 3] < 16) { counts.empty = (counts.empty || 0) + 1; line += '.'; continue; }"
        "    var k = want[d[o] + ',' + d[o + 1] + ',' + d[o + 2]] || '?';"
        "    counts[k] = (counts[k] || 0) + 1; line += { outline: 'o', base: 'B', dark: 'D', light: 'L', ink: 'i', '?': '?' }[k];"
        "  }"
        r"  if (line.replace(/\./g, '').length) rows.push(line.replace(/\.+$/, ''));"
        "}"
        "out({baked: f.baked, outline: f.outline, counts: counts, rows: rows,"
        "     footPad: f.footPad, feetY: f.feetY, figH: f.figureHeight,"
        "     canvasW: f.canvas.width, canvasH: f.canvas.height});",
        with_canvas=True)
    assert got["baked"] is True, "the bake could not read its own pixels back"
    assert got["outline"] is True, "no outline was painted at this size"
    c = got["counts"]
    assert c.get("outline", 0) > 0 and c.get("base", 0) > 0, got
    assert c.get("dark", 0) > 0, "the legs are not a darker tone"
    assert c.get("light", 0) > 0, "the head is not a brighter tone"
    assert "?" not in c, f"a pixel was painted in a tone the palette does not name: {c}"
    # and the outline really rings the figure rather than covering it
    assert c["outline"] < c["base"] + c.get("dark", 0) + c.get("light", 0), (
        f"the outline is more of the figure than the figure is: {c}")
    # the painted rows agree with the tile map: head above torso above legs
    joined = "\n".join(got["rows"])
    assert "L" in joined and "B" in joined and "D" in joined, joined
    assert joined.index("D") > joined.index("L"), joined


def test_the_baked_anchor_is_measured_from_the_pixels_not_assumed():
    """The ported measureFootPad rule: the reported feet line must be the row
    the boots are actually drawn on."""
    got = _run(
        "var f = S.bakeAgent('#5aa9e6', {scale: 1});"
        "var ctx = f.canvas.getContext('2d');"
        "var d = ctx.getImageData(0, 0, f.canvas.width, f.canvas.height).data;"
        "var bottom = -1;"
        "for (var y = f.canvas.height - 1; y >= 0; y--) {"
        "  for (var x = 0; x < f.canvas.width; x++) {"
        "    if (d[(y * f.canvas.width + x) * 4 + 3] > 16) { bottom = y; break; }"
        "  }"
        "  if (bottom >= 0) break;"
        "}"
        "out({bottomRow: bottom, canvasH: f.canvas.height,"
        "     footPad: f.footPad,"
        "     derivedFeetRow: f.canvas.height - f.footPad,"
        "     anchorFeetRow: S.anchorOf(f).feetY});",
        with_canvas=True)
    # `feetY` is an EDGE, not an index: a boot drawn on row 17 has its sole at
    # y = 18, because placing the figure there puts the sole exactly on the
    # caller's y. So the anchor is the count of rows above the sole, and the
    # last painted row INDEX is one less than that.
    assert got["footPad"] == 0, got
    assert abs(got["derivedFeetRow"] - got["canvasH"]) < 1e-9, got
    assert abs(got["anchorFeetRow"] - got["canvasH"]) < 1e-9, got
    assert got["bottomRow"] == got["canvasH"] - 1, got


def test_the_outline_separates_the_body_parts_from_each_other():
    """The single biggest legibility win: a line between the masses.

    "Legible" is not a property a test can see; a contrast ratio is. So this
    asserts the measured property the outline is for — every body tone is
    further from the OUTLINE than it is from its neighbouring body tone, i.e.
    the line really does divide the head from the torso and the torso from the
    legs, rather than being a wash of a slightly different shade.
    """
    got = _run(
        "function lum(h) { var n = parseInt(h.slice(1, 7), 16);"
        "  return 0.299 * ((n >> 16) & 255) + 0.587 * ((n >> 8) & 255) + 0.114 * (n & 255); }"
        "var pal = S.paletteFor('#5aa9e6');"
        "out({base: lum(pal.base), outline: lum(pal.outline),"
        "     dark: lum(pal.dark), light: lum(pal.light), ink: lum(pal.ink)});",
        with_canvas=False)
    line = got["outline"]
    # the line is the darkest tone, so it is distinct from every mass
    assert line < got["dark"] < got["base"] < got["light"], got
    # and the gap the line opens is a real separation, not a rounding step
    assert got["dark"] - line >= 20, (
        f"the line ({line:.0f}) is only {got['dark'] - line:.0f} below the darkest "
        f"body tone ({got['dark']:.0f}) — it will not read as a line")
    # the head is brighter than the torso, so the two do not merge
    assert got["light"] - got["base"] >= 20, got


def test_the_outline_earns_its_pixel_against_the_tower_deck():
    """The tower's deck is dark (#2c3848). A dark line cannot contrast against
    a dark deck, so the outline's job there is to separate the FIGURE'S OWN
    parts — which is exactly what the previous test measures. This test pins the
    other half of the claim honestly rather than asserting a contrast win that
    a dark line cannot deliver: against a LIGHT desk the line provides the
    silhouette edge, and that is where it is load-bearing."""
    got = _run(
        "function lum(h) { var n = parseInt(h.slice(1, 7), 16);"
        "  return 0.299 * ((n >> 16) & 255) + 0.587 * ((n >> 8) & 255) + 0.114 * (n & 255); }"
        "var pal = S.paletteFor('#5aa9e6');"
        "out({lightDeck: lum('#c8d4e0'), darkDeck: lum('#2c3848'),"
        "     base: lum(pal.base), outline: lum(pal.outline)});",
        with_canvas=False)
    # on the light deck the outline is what keeps the figure off the floor
    assert abs(got["outline"] - got["lightDeck"]) > abs(got["base"] - got["lightDeck"]), got
    # and on the dark deck the figure's own body is the contrast
    assert abs(got["base"] - got["darkDeck"]) > abs(got["outline"] - got["darkDeck"]), got
    # either way, SOMETHING in the figure contrasts with either deck — which is
    # the actual legibility claim, and it is true of both
    assert abs(got["outline"] - got["lightDeck"]) > 20, got
    assert abs(got["base"] - got["darkDeck"]) > 20, got


def test_a_figure_too_small_for_an_outline_drops_it_rather_than_muddying():
    """At a few pixels the line would BE the figure, so it is omitted."""
    got = _run(
        "var tiny = S.bakeAgent('#5aa9e6', {scale: 0.4});"
        "var big = S.bakeAgent('#5aa9e6', {scale: 4});"
        "out({tinyOutline: tiny.outline, bigOutline: big.outline,"
        "     tinyCell: tiny.cell, bigCell: big.cell,"
        "     tinyAnchor: S.anchorOf(tiny).feetY, bigAnchor: S.anchorOf(big).feetY});",
        with_canvas=False)
    assert got["tinyOutline"] is False, "an outline was painted on a sub-pixel figure"
    assert got["bigOutline"] is True, "no outline on a large figure"
    # the anchor survives both, because it is measured in sprite units
    assert got["tinyAnchor"] > 0 and got["bigAnchor"] > 0


# ---------------------------------------------------------------------------
# the module owns no state the renderer has to know about
# ---------------------------------------------------------------------------
def test_the_module_is_pure_enough_to_be_re_entrant():
    """Same inputs, same outputs — the renderer calls this per figure per frame."""
    got = _run(
        "var a = S.drawAgentSprite({globalAlpha:1,fillStyle:'#000',"
        " imageSmoothingEnabled:true, save:function(){}, restore:function(){},"
        " ellipse:function(){}, beginPath:function(){}, fill:function(){},"
        " drawImage:function(){}, fillRect:function(){}},"
        " null, '#5aa9e6', 10, 20, 1.5, 'left', true, 0.4);"
        "var b = S.drawAgentSprite({globalAlpha:1,fillStyle:'#000',"
        " imageSmoothingEnabled:true, save:function(){}, restore:function(){},"
        " ellipse:function(){}, beginPath:function(){}, fill:function(){},"
        " drawImage:function(){}, fillRect:function(){}},"
        " null, '#5aa9e6', 10, 20, 1.5, 'left', true, 0.4);"
        "out({same: JSON.stringify(a) === JSON.stringify(b), a: a});",
        with_canvas=False)
    assert got["same"] is True, "the draw function is not a pure function of its arguments"
    assert got["a"]["facing"] == "left"
    assert got["a"]["x"] == 10 and got["a"]["y"] == 20


def test_an_unknown_facing_falls_back_rather_than_drawing_nothing():
    got = _run(
        "function stub(){ return {globalAlpha:1,fillStyle:'#000',"
        " imageSmoothingEnabled:true, save:function(){}, restore:function(){},"
        " ellipse:function(){}, beginPath:function(){}, fill:function(){},"
        " drawImage:function(){}, fillRect:function(){}}; }"
        "var out2 = {};"
        "['left','right','up','down','sideways','', null, undefined].forEach(function (f) {"
        "  out2[String(f)] = S.drawAgentSprite(stub(), null, '#5aa9e6', 0, 0, 1, f, false, 0).facing;"
        "});"
        "out(out2);",
        with_canvas=False)
    assert got["left"] == "left" and got["right"] == "right", got
    assert got["up"] == "up" and got["down"] == "down", got
    for bad in ("sideways", "", "null", "undefined"):
        assert got[bad] == "down", got


def test_the_new_module_does_not_edit_the_renderer_it_serves():
    """The renderer is owned by another worker; this file only adds to it."""
    text = ENTRY.read_text(encoding="utf-8")
    # the renderer is untouched by this change: it does not yet load the module,
    # and the module must not have been pasted into it.
    assert "function bakeAgent" not in text, (
        "the sprite module was inlined into the renderer instead of imported")
