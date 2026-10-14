"""Office tower — the renderer's DRAWING behaviour, executed rather than grepped.

Why this file exists
--------------------
`tests/test_office_tower_plugin.py` asserts on the renderer's *text*
(`assert "cs.animation" in src`). That is the right tool for "no mutating verb",
"exactly one timer", "no keyframes" — a future commit that adds a spinner has to
type the word, and a regex catches it. It is the WRONG tool for "did anything get
drawn": every one of those text assertions passes while the canvas renders an
almost-empty frame, which is exactly what shipped. Text is not pixels.

This file runs the renderer's actual drawing code. `node` evaluates
`qubit-office.js` with a minimal SDK stub, the pure drawing helpers are exposed by
rewriting only the file's final `register(...)` line, and every drawing call is
made against a FAKE 2D context that records each `fillRect` / `arc` / `ellipse` /
`moveTo` / `lineTo` / `fillText` / `fill` / `stroke` and the `fillStyle` active at
the time. The assertions are then made on what was drawn.

How the source is executed (the one transform, and why)
------------------------------------------------------
`qubit-office.js` is an IIFE that ends with
`window.__QUBIT_PLUGINS__.register("qubit-office", Office);`. React components
cannot be driven from a stub `hooks` object — `useEffect`/`useRef`/DOM are needed
to reach `redraw()`, and a stub would test the stub, not the renderer. So the
final line is replaced with an assignment of the drawing functions to a global:

    globalThis.__X = { drawTower: drawTower, drawAgent: drawAgent, ... };

That single tail substitution is the only change to the file text; no drawing
function is touched, and `drawTower` — the real entry point, called with the real
payload shape and a real camera — is what most assertions exercise. If the guard
line `window.__QUBIT_PLUGINS__.register("qubit-office", Office)` is ever renamed,
this file fails loudly with a clear message instead of silently skipping.

Every assertion here is verified to be able to fail (see the report): the count
assertions drop to zero on an empty payload, and the colour assertion fails for
the colliding-slug case unless `seatColour` keeps slugs distinct. The
painter's-order assertions assert the depth SORT (the fix for the isometric
smear), so an array-order renderer fails them.

Hermetic: reads one file, runs `node` on it, no network, no browser, no provider.
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

# The IIFE tail this file rewrites to expose the internals. Kept as a literal so
# a renamed registration fails the harness test below with an explicit message.
REGISTER_LINE = 'window.__QUBIT_PLUGINS__.register("qubit-office", Office);'

EXPOSED = (
    "drawTower, drawAgent, drawShadow, drawMarker, drawMinimap, drawPnl, "
    "iso, extent, fitToBuilding, clampPan, "
    "money, moneyRate, seatColour, shade, tint, deckTint, "
    "PALETTE, ANIM_COLOR, MOTION, TYPE_HUE, "
    "TILE_W, TILE_H, DECK_Z, CHAR_FLOOR_ZOOM, MIN_ZOOM, MAX_ZOOM, Office, "
    "FACE_H, FLOOR_STRIDE, FLOOR_GLYPH_H"
).split(", ")

# --------------------------------------------------------------- the harness
# JS prepended to every evaluation: the SDK stub, the shared fake context, and
# the payload builders. One string so every test runs the same machinery.
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

/* A 2D context that records geometry. `path` accumulates the current path the
 * way the real context does, so a `fill()` can be attributed to the primitive
 * (arc/rect/roundRect/ellipse/lineTo) that built it, and every recorded op
 * carries the fillStyle / globalAlpha that were in force. */
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

/* Fake canvases whose getContext returns the SAME recorder, so a helper that
 * takes a canvas (drawMinimap / drawPnl) records where the caller can read it. */
function fakeCanvas(w, h) {
  var o = {width: w, height: h, _ctx: new FakeCtx()};
  o.getContext = function () { return o._ctx; };
  return o;
}

/* ---- op classifiers ---------------------------------------------------- */
function fills(ctx) { return ctx.ops.filter(function (o) { return o.op === "fill"; }); }
function rects(ctx) { return ctx.ops.filter(function (o) { return o.op === "fillRect"; }); }
function fillsWith(ctx, prim) {
  return fills(ctx).filter(function (o) {
    return o.path.some(function (p) { return p[0] === prim; });
  });
}
/* A character body is the roundRect fill; the head is the arc fill; the shadow
 * is the ellipse fill. These are the three primitives drawAgent/drawShadow use,
 * and nothing else in the file draws a roundRect or an arc. */
function bodyFills(ctx) { return fillsWith(ctx, "roundRect"); }
function headFills(ctx) { return fillsWith(ctx, "arc"); }
function shadowFills(ctx) { return fillsWith(ctx, "ellipse"); }
function texts(ctx) {
  return ctx.ops.filter(function (o) { return o.op === "fillText"; })
                .map(function (o) { return o.text; });
}

/* ---- drawing entry points. Payloads are built in Python and passed in as
 * JSON, so there is exactly one definition of the payload shape in this file
 * (the Python side), and the JS here only draws. ---------------------------- */
function drawTowerF(payloadObj) {
  var ctx = new FakeCtx();
  var probe = X.drawTower(ctx, payloadObj,
                          {cam: {cx: 0, cy: 0, zoom: 1, w: 880, h: 500},
                           floor: null});
  return {ctx: ctx, probe: probe};
}
"""


# ---- payload builders (the documented /api/office/tower shape) ------------
# Plain Python dicts: the single source of the payload shape for this file. They
# are serialised to JSON and handed to the drawing call in node.


def make_seat(slug: str, x: float, y: float, slot: int = 0) -> dict:
    return {"slot": slot, "agent_id": "ag-" + slug, "slug": slug,
            "x": x, "y": y}


def make_room(slug: str, x: int, y: int, w: int, h: int,
              seat_slugs: list[str] | None = None) -> dict:
    seats = []
    for i, s in enumerate(seat_slugs or []):
        # spread seats over distinct tiles so no two figures share a point
        seats.append(make_seat(s, x + (i % 3), y + (i // 3), slot=i))
    return {"id": "room-" + slug, "slug": slug, "kind": "open",
            "department_id": 1, "x": x, "y": y, "w": w, "h": h, "seats": seats}


def make_floor(number: int, z: int, role: str, rooms: list[dict]) -> dict:
    return {"id": "floor-%d" % number, "number": number,
            "name": "level %d" % number, "role": role, "theme": role,
            "z": z, "rooms": rooms}


def make_character(slug: str, floor_number: int, anim: str,
                   **extra) -> dict:
    c = {
        "agent_id": "ag-" + slug, "slug": slug, "seat_type": "worker",
        "department_id": 1, "supervisor_id": None, "seated": True,
        "position": {"floor": floor_number, "room": "room", "room_id": "room",
                     "slot": 0, "x": 1.5, "y": 1.5},
        "facing": "down", "walking": False, "at": "desk", "animation": anim,
        "last_event": None, "stalled": False,
    }
    for k, v in extra.items():
        if k == "position":
            c["position"].update(v)
        else:
            c[k] = v
    return c


def make_payload(floors: list[dict], chars: list[dict]) -> dict:
    return {
        "schema": "office/tower", "now": 1700000000.0, "active_window_s": 900,
        "walk_s": 6, "empty": False, "empty_reason": None,
        "hud": {"headcount": len(chars), "revenue_cents": 0, "cost_cents": 0,
                "net_cents": 0, "burn_cents_per_day": 0, "runway_days": None,
                "survival": {"floor_days": None}, "floors": []},
        "floorplan": {"schema": "office/floorplan",
                      "grid": {"cols": 8, "rows": 8, "tile": 1},
                      "floors": floors},
        "characters": chars, "activity": [], "history": [],
        "history_totals": {"revenue_cents": 0, "cost_cents": 0,
                           "net_cents": 0},
        "motion_rule": "A character moves only when an office event says so.",
        "read_only": True, "preview": None,
    }


def _source_with_exposed_internals() -> str:
    """The plugin file with its final register line rewritten to export helpers.

    Returns the full JS text to evaluate. Raises AssertionError with an explicit
    message if the tail this harness depends on has been renamed — a silent skip
    would let the renderer rot behind a green suite.
    """
    src = ENTRY.read_text()
    assert REGISTER_LINE in src, (
        "the harness reads %s and rewrites its final register line (%r). It is "
        "gone, so this file cannot reach the drawing functions. Update "
        "REGISTER_LINE to the new registration call."
        % (ENTRY.name, REGISTER_LINE))
    export = "globalThis.__X = {" + ", ".join(
        "%s: %s" % (name, name) for name in EXPOSED) + "};"
    return src.replace(REGISTER_LINE, export)


_CACHE: dict[str, str] = {}


def _js_result(program: str):
    """Run `program` in node after the harness, return the parsed JSON result.

    `program` must set `globalThis.__RESULT`. Hermetic and repeatable.
    """
    if not _CACHE:
        _CACHE["src"] = _source_with_exposed_internals()
    # Order matters: the harness runs FIRST so `window` (the SDK stub) exists by
    # the time the plugin IIFE reads `window.__QUBIT_PLUGIN_SDK__`.
    full = (
        HARNESS % {"exposed": json.dumps(EXPOSED)}
        + "\nvar __SRC = " + json.dumps(_CACHE["src"]) + ";\n"
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


def _draw(payload_obj: dict, extra_js: str = "") -> dict:
    """Draw `payload_obj` with the real `drawTower` and return the measurement.

    `extra_js` may reference `out` (the {ctx, probe} result), `c` (its ctx) and
    the classifier helpers, and must set `__RESULT`.
    """
    return _js_result(
        "var out = drawTowerF(%s); var c = out.ctx;\n" % json.dumps(payload_obj)
        + extra_js)


# ------------------------------------------------------------------- sanity
def test_the_harness_can_execute_the_renderer_at_all():
    """If this fails, every other test in this file is meaningless."""
    res = _js_result(
        "__RESULT = {money: X.money(1000), hasDrawTower: typeof X.drawTower};")
    assert res["hasDrawTower"] == "function"
    assert res["money"] == "$10.00"


# ------------------------------------------------- 1. every seated character
def test_every_seated_character_is_drawn_exactly_once():
    """N seated characters with valid positions produce N agent figures.

    The text tests could not see this: the page rendered its error-free 'ready'
    state with the canvas drawing zero figures. Each figure is one body fill
    (roundRect), one head fill (arc) and one ground shadow (ellipse), so the
    three counts must all equal N and match the probe the renderer returns.
    """
    for n in (1, 3, 12):
        slugs = ["slug-%02d" % i for i in range(n)]
        chars = [make_character(s, 1, "idle") for s in slugs]
        # seats spread over distinct tiles so no two figures share a point
        v = make_payload([make_floor(1, 0, "operations",
                                     [make_room("open", 0, 0, 6, 6, slugs)])],
                         chars)
        r = _draw(v, (
            "__RESULT = {agents: out.probe.agents.length,"
            "  body: bodyFills(c).length, head: headFills(c).length,"
            "  shadow: shadowFills(c).length};"))
        assert r["agents"] == n, r
        assert r["body"] == n, "a body was not drawn for every seat: %r" % r
        assert r["head"] == n, "a head was not drawn: %r" % r
        assert r["shadow"] == n, "a ground shadow was not drawn: %r" % r


def test_a_seated_character_that_cannot_be_placed_is_not_faked_onto_a_floor():
    """A character whose floor is not on screen is dropped, not drawn anywhere.

    The other half of 'exactly N': the renderer must not invent an agent for a
    character it cannot locate, and must not fail to draw one it can.
    """
    v = make_payload(
        [make_floor(1, 0, "operations",
                    [make_room("open", 0, 0, 4, 4, ["here"])])],
        [make_character("here", 1, "idle"),
         make_character("elsewhere", 99, "idle")])
    r = _draw(v, ("__RESULT = {agents: out.probe.agents.length,"
                  "  body: bodyFills(c).length};"))
    assert r["agents"] == 1, ("the character with no floor on screen was drawn "
                              "anyway: %r" % r)
    assert r["body"] == 1, r


# --------------------------------------------------------- 2. painter's order
def test_floors_are_painted_far_to_near_by_depth_not_array_order():
    """The renderer's depth rule: decks paint by DEPTH, not by array order.

    `drawTower` flattens every deck, desk, label and character into ONE list
    and sorts ascending by the depth key `(x + y) * TILE_H/2 - z * DECK_Z` (see
    `docs/game-office/26-OCCLUSION-AND-DEPTH.md` and `iso-engine.depthKey`), so
    a deck with a LARGER `z` has a SMALLER key and paints FIRST — the far
    floors first, which is what makes the near deck overlap the one behind it.

    This replaces the old assertion, which pinned the previous array-order rule
    (`shown.slice().reverse()`): the array order *was* the defect, because the
    last floor in the payload happened to paint first and the ordering was an
    accident of `tower.py` listing floors by number, not of their screen depth.
    The rule that matters is the sort, so the three assertions below are chosen
    to FAIL on any array-order renderer:

      * an ascending payload paints high-z-first (matches the old behaviour by
        coincidence — kept as the positive control);
      * REVERSING the payload array does NOT change the paint order (the old
        array-order rule reversed with it — this is the discriminating case);
      * `z` decides, not the array: a floor with the larger `z` paints first
        even when it is the payload's FIRST floor.

    Asserted from pixels: each deck is labelled `FLOOR <n> · <role>` on its near
    corner, and those labels appear in draw order.
    """
    labels_js = ("__RESULT = {labels: texts(c).filter(function (t) "
                 "{ return /^FLOOR /.test(t); })};")

    asc = make_payload(
        [make_floor(1, 0, "operations", [make_room("low", 0, 0, 4, 4, [])]),
         make_floor(2, 1, "executive", [make_room("high", 0, 0, 4, 4, [])])],
        [])
    r1 = _draw(asc, labels_js)
    assert r1["labels"] == ["FLOOR 2 · executive", "FLOOR 1 · operations"], (
        "the renderer's depth rule changed: floor 2 (z=1, the far/high deck) "
        "must paint before floor 1 (z=0, the near deck). Got %r"
        % (r1["labels"],))

    # Same two floors, array reversed -> SAME paint order, because the order is
    # the depth sort and not the array. This is the assertion the old array-order
    # rule fails: it would have reversed the labels with the array.
    desc = make_payload(
        [make_floor(2, 1, "executive", [make_room("high", 0, 0, 4, 4, [])]),
         make_floor(1, 0, "operations", [make_room("low", 0, 0, 4, 4, [])])],
        [])
    r2 = _draw(desc, labels_js)
    assert r2["labels"] == ["FLOOR 2 · executive", "FLOOR 1 · operations"], (
        "the paint order followed the payload ARRAY instead of the depth key; "
        "reversing the array must not change which deck paints first. Got %r"
        % (r2["labels"],))

    # `z` decides: floor 1 carries the large z and is the payload's FIRST floor,
    # so a z-unaware renderer paints it last. It must paint first.
    zs = make_payload(
        [make_floor(1, 5, "ops-with-large-z", [make_room("a", 0, 0, 4, 4, [])]),
         make_floor(3, 0, "exec-with-z-zero", [make_room("b", 0, 0, 4, 4, [])])],
        [])
    r3 = _draw(zs, labels_js)
    assert r3["labels"] == ["FLOOR 1 · ops-with-large-z",
                            "FLOOR 3 · exec-with-z-zero"], (
        "the depth key must follow `z`, not the array: floor 1 has the larger z "
        "so it paints first even though it is the payload's first floor. Got %r"
        % (r3["labels"],))


def test_decks_and_characters_are_interleaved_by_depth():
    """A deck NEARER the camera paints AFTER a character standing on a farther
    deck — the sorted painter's order, not 'characters on top of every deck'.

    Two floors, one character on the FAR (higher-z) floor. By the depth key:

      * the far floor's deck plate   (key 0 - 2*26 = -52)     paints FIRST
      * the character on it          (key 25.5 - 2*26 = -26.5) paints next
      * the near floor's deck plate  (key 0 - 1*26 = -26)     paints LAST, over
                                                               the figure's feet

    The two plates have different deck colours (the floors' roles differ), so
    each is located by the colour `deckTint`/`shade` produce. The old
    'characters, on top of every deck' pass would put the character after BOTH
    plates and fail the second assertion; a renderer that reverted to painting
    every deck first would fail the first.
    """
    v = make_payload(
        [make_floor(1, 1, "operations",
                    [make_room("near", 0, 0, 4, 4, ["near-seat"])]),
         make_floor(2, 2, "executive",
                    [make_room("far", 0, 0, 4, 4, ["sit"])])],
        [make_character("sit", 2, "idle", position={"x": 1.5, "y": 1.5})])
    r = _draw(v, (
        "var farPlate = X.shade(X.deckTint('executive', 1), -0.05);"
        "var nearPlate = X.shade(X.deckTint('operations', 1), -0.05);"
        "var farD = -1, nearD = -1, agent = -1;"
        "for (var i = 0; i < c.ops.length; i++) {"
        "  var o = c.ops[i];"
        "  if (o.op === 'fill' && o.fillStyle === farPlate) farD = i;"
        "  if (o.op === 'fill' && o.fillStyle === nearPlate) nearD = i;"
        "  if (agent === -1 && o.op === 'fill' && "
        "      o.path.some(function (p) { return p[0] === 'roundRect'; })) "
        "    agent = i;"
        "}"
        "__RESULT = {farD: farD, nearD: nearD, agent: agent};"))
    assert r["farD"] >= 0, "the far deck plate was not drawn: %r" % r
    assert r["nearD"] >= 0, "the near deck plate was not drawn: %r" % r
    assert r["agent"] >= 0, "no character was drawn: %r" % r
    assert r["farD"] < r["agent"], (
        "the character painted BEFORE the far deck it stands on — a deck "
        "painted over its own occupant: %r" % r)
    assert r["agent"] < r["nearD"], (
        "the character painted AFTER the NEARER deck, so it floats over a deck "
        "in front of it — the 'characters on top of every deck' order that "
        "causes the isometric smear: %r" % r)


# --------------------------------------------------------------- 3/4. money
MONEY_VALUES = [0, 1, 99, 100, 1000, -4940, 0.14286, 142.86]


@pytest.mark.parametrize("value", MONEY_VALUES)
def test_money_always_renders_one_decimal_point(value):
    """`money()` output always matches `-?$<digits with commas>.<2 digits>`.

    The bug this guards: a rate of 0.14286 rendered as `$0.01.4286/day` — two
    decimal points, a number nobody can read. Cents are integers; anything
    fractional is rounded to a whole cent before formatting.
    """
    r = _js_result("__RESULT = {s: X.money(%s)};" % json.dumps(value))
    assert re.match(r"^-?\$[\d,]+\.\d\d$", r["s"]), (
        "money(%r) produced %r, which is not a single-decimal money string"
        % (value, r["s"]))


@pytest.mark.parametrize("value", MONEY_VALUES)
def test_money_rate_renders_one_decimal_point(value):
    """`moneyRate()` — the `/day` formatter — likewise never grows a second `.`

    It formats from the raw number (a rate can be a fraction of a cent per day),
    so it is a separate path from money() and needs its own guard.
    """
    r = _js_result("__RESULT = {s: X.moneyRate(%s)};" % json.dumps(value))
    assert re.match(r"^-?\$[\d,]+\.\d\d$", r["s"]), (
        "moneyRate(%r) produced %r, which is not a single-decimal money string"
        % (value, r["s"]))


def test_money_keeps_signs_and_rounds_fractions_rather_than_truncating():
    """The fix must round, not truncate, and keep the sign.

    `money(0.14286)` is `$0.00` (rounds to zero cents); `money(142.86)` is
    `$1.43` (rounds up to 143 cents). The negative case is the one a naive fix
    drops, and it is asserted with its sign intact.
    """
    r = _js_result(
        "var v = %s;"
        "__RESULT = {m: v.map(function (x) { return X.money(x); }),"
        "  r: v.map(function (x) { return X.moneyRate(x); })};"
        % json.dumps(MONEY_VALUES))
    assert r["m"][4] == "$10.00", r["m"]           # 1000 cents
    assert r["m"][5] == "-$49.40", r["m"]          # -4940 cents keeps its sign
    assert r["m"][6] == "$0.00", r["m"]            # 0.14286 rounds to 0 cents
    assert r["m"][7] == "$1.43", r["m"]            # 142.86 rounds to 143 cents
    assert r["r"][7] == "$1.43", r["r"]            # 1.4286 dollars/day
    for s in r["m"] + r["r"]:
        assert s.count(".") == 1, (
            "a money string grew a second decimal point: %r" % s)


# --------------------------------------------------------------- 5. seat colour
def test_seat_colour_is_stable_for_a_slug():
    """The same slug is the same colour on every call — a seat cannot change
    colour between two polls, or between a redraw and a click."""
    r = _js_result(
        "__RESULT = {a: X.seatColour('inbox-sweeper', 'worker'),"
        "  b: X.seatColour('inbox-sweeper', 'worker'),"
        "  c: X.seatColour('inbox-sweeper', 'worker')};")
    assert r["a"] == r["b"] == r["c"], (
        "seatColour is not a pure function of the slug: %r" % r)
    assert re.match(r"^#[0-9a-f]{6}$", r["a"]), r


def test_two_different_slugs_get_two_different_colours():
    """Distinguishable seats: two slugs in the same room must not share a colour,
    or a room of people draws as one blob.

    This is the defect the text tests could not see and this one currently
    FAILS: the hue is `Math.abs(hash) % PALETTE.length` over a 10-entry palette,
    and `tint(base, TYPE_HUE[seatType], 0.55)` then drags same-type slugs towards
    one hue, so two workers collide. The fix belongs in `seatColour`.
    """
    r = _js_result(
        "var a = 'inbox-sweeper', b = 'ledger-auditor';"
        "__RESULT = {a: X.seatColour(a, 'worker'), b: X.seatColour(b, 'worker'),"
        "  aAgain: X.seatColour(a, 'worker')};")
    assert r["a"] == r["aAgain"], "stability must survive the difference check"
    assert r["a"] != r["b"], (
        "two different worker slugs collapsed to the same colour %r — seats are "
        "not distinguishable" % r["a"])


def test_a_room_of_distinct_slugs_gets_mostly_distinct_colours():
    """At room scale the palette must not degenerate.

    A 12-seat room is the normal case. If many slugs share a colour the drawing
    cannot be read as twelve people. This is a weaker form of the previous
    assertion, on a realistic cohort, which makes the size of the collapse
    visible. Today it fails hard.
    """
    slugs = ["inbox-sweeper", "ledger-auditor", "doc-reader", "patch-writer",
             "mail-triage", "calendar-keeper", "log-watcher", "queue-drainer",
             "report-builder", "index-refresher", "backup-runner",
             "schema-checker"]
    r = _js_result(
        "__RESULT = {colours: %s.map(function (s) "
        "{ return X.seatColour(s, 'worker'); })};" % json.dumps(slugs))
    distinct = len(set(r["colours"]))
    assert distinct >= len(slugs) - 2, (
        "%d distinct slugs produced only %d distinct colours: %r"
        % (len(slugs), distinct, sorted(set(r["colours"]))))


# ----------------------------------------- 6. animation drives what is drawn
def test_an_idle_character_uses_the_idle_colour_and_shows_no_motion_marker():
    """C7 at the pixel: an idle payload draws idle, and nothing that reads as
    work. The state band is the idle colour, no marker glyph is drawn above the
    head, and no working colour appears anywhere in the frame."""
    v = make_payload(
        [make_floor(1, 0, "operations",
                    [make_room("open", 0, 0, 4, 4, ["quiet"])])],
        [make_character("quiet", 1, "idle")])
    r = _draw(v, (
        "var idleBands = rects(c).filter(function (o) "
        "{ return o.fillStyle === X.ANIM_COLOR.idle; }).length;"
        "var workingAnywhere = c.ops.filter(function (o) "
        "{ return o.fillStyle === X.ANIM_COLOR.working; }).length;"
        "__RESULT = {idleBands: idleBands, workingAnywhere: workingAnywhere,"
        "  glyphs: texts(c).filter(function (t) { return /^[!^]$/.test(t); }),"
        "  body: bodyFills(c).length};"))
    assert r["body"] == 1, "the idle character was not drawn at all"
    assert r["idleBands"] == 1, (
        "an idle character must draw exactly one idle state band: %r" % r)
    assert r["workingAnywhere"] == 0, (
        "an idle payload drew the working colour somewhere: %r" % r)
    assert r["glyphs"] == [], (
        "an idle character drew a motion/stall marker: %r" % r)


def test_a_working_character_that_is_not_walking_draws_the_working_band():
    """`animation: 'working'` with `walking: false` shows as working.

    The renderer follows the payload's `animation` field for the band colour,
    independent of the separate `walking` flag. This pins C7: the drawing is a
    function of the payload, never of a local default.
    """
    v = make_payload(
        [make_floor(1, 0, "operations",
                    [make_room("open", 0, 0, 4, 4, ["busy"])])],
        [make_character("busy", 1, "working", walking=False)])
    r = _draw(v, (
        "var workingBands = rects(c).filter(function (o) "
        "{ return o.fillStyle === X.ANIM_COLOR.working; }).length;"
        "var idleAnywhere = c.ops.filter(function (o) "
        "{ return o.fillStyle === X.ANIM_COLOR.idle; }).length;"
        "__RESULT = {workingBands: workingBands, idleAnywhere: idleAnywhere,"
        "  glyphs: texts(c).filter(function (t) { return /^[!^]$/.test(t); })};"))
    assert r["workingBands"] == 1, (
        "a working, non-walking character must draw one working band: %r" % r)
    assert r["idleAnywhere"] == 0, (
        "a working payload drew the idle colour somewhere: %r" % r)
    assert r["glyphs"] == [], (
        "working is not a stall or a summon; no marker belongs here: %r" % r)


def test_the_band_colour_is_the_payloads_animation_not_a_local_default():
    """Two characters, two different `animation` values, two different bands.

    If the renderer ever falls back to a fixed colour, both bands become equal
    and this fails. The colours come from ANIM_COLOR keyed by the payload string.
    """
    v = make_payload(
        [make_floor(1, 0, "operations",
                    [make_room("open", 0, 0, 4, 4, ["one", "two"])])],
        [make_character("one", 1, "idle"),
         make_character("two", 1, "carrying")])
    r = _draw(v, (
        "__RESULT = {idle: rects(c).filter(function (o) "
        "{ return o.fillStyle === X.ANIM_COLOR.idle; }).length,"
        "  carrying: rects(c).filter(function (o) "
        "{ return o.fillStyle === X.ANIM_COLOR.carrying; }).length};"))
    assert r["idle"] == 1 and r["carrying"] == 1, (
        "the bands did not follow the two payload animations: %r" % r)


def test_an_unknown_animation_is_grey_and_never_a_working_colour():
    """A state the renderer does not know must not look active."""
    v = make_payload(
        [make_floor(1, 0, "operations",
                    [make_room("open", 0, 0, 4, 4, ["odd"])])],
        [make_character("odd", 1, "no-such-state")])
    r = _draw(v, (
        "__RESULT = {grey: rects(c).filter(function (o) "
        "{ return o.fillStyle === '#8a8f98'; }).length,"
        "  working: c.ops.filter(function (o) "
        "{ return o.fillStyle === X.ANIM_COLOR.working; }).length};"))
    assert r["grey"] == 1, "an unknown animation must fall back to grey: %r" % r
    assert r["working"] == 0, (
        "an unknown animation fell back to the working colour: %r" % r)


def test_a_blocked_character_is_marked_and_an_idle_one_is_not():
    """Only the states a human must spot get a marker glyph.

    `blocked` draws `!`; `walking_to_manager` draws `^`; `stalled` forces `!`.
    None is drawn for `idle`, so a marker is a statement about the payload and
    not decoration.
    """
    def glyphs(anim, **extra):
        v = make_payload(
            [make_floor(1, 0, "operations",
                        [make_room("open", 0, 0, 4, 4, ["x"])])],
            [make_character("x", 1, anim, **extra)])
        return _draw(v, ("__RESULT = {g: texts(c).filter(function (t) "
                         "{ return /^[!^]$/.test(t); })};"))["g"]

    assert glyphs("idle") == []
    assert glyphs("working") == []
    assert glyphs("blocked") == ["!"]
    assert glyphs("walking_to_manager") == ["^"]
    assert glyphs("idle", stalled=True) == ["!"]


def test_the_walk_bob_only_exists_where_the_payload_reports_a_walk():
    """A body is lifted by the walk bob only when `walking` is true AND the
    payload carries a numeric `progress`. No progress, no bob — this is C7's
    'motion may only come from the payload' at the pixel level."""
    def body_top(walking, progress):
        pos = {"floor": 1, "room": "room", "room_id": "room", "slot": 0,
               "x": 1.5, "y": 1.5}
        if progress is not None:
            pos["progress"] = progress
        v = make_payload(
            [make_floor(1, 0, "operations",
                        [make_room("open", 0, 0, 4, 4, ["x"])])],
            [make_character("x", 1, "walking", walking=walking,
                            position=pos)])
        return _draw(v, (
            "__RESULT = {top: bodyFills(c)[0].path.find(function (p) "
            "{ return p[0] === 'roundRect'; })[2]};"))["top"]

    rest = body_top(False, 0.125)
    # progress 0.125 -> |sin(0.125*pi*4)| = 1 -> a full 3px bob at zoom 1
    bobbed = body_top(True, 0.125)
    assert bobbed < rest, (
        "a walking character with progress was not lifted off its seat: "
        "rest=%r bobbed=%r" % (rest, bobbed))
    # walking but no progress in the payload -> no bob
    assert body_top(True, None) == rest, (
        "the renderer invented a bob with no progress in the payload")
    # walking false with progress -> no bob
    assert body_top(False, 0.5) == rest, (
        "the renderer bobbed a character the payload did not report as walking")


# --------------------------------------------------------------- 7. the camera
def test_iso_is_isometric_and_not_degenerate():
    """`iso()` maps x and y to screen in opposed directions, and the two axes
    are not parallel — otherwise the 'isometric' deck is a line.

    Screen x = (x - y) * TILE_W/2 and screen y = (x + y) * TILE_H/2. Increasing
    x therefore moves right and down; increasing y moves left and down. The two
    direction vectors must not be scalar multiples of each other.
    """
    r = _js_result(
        "var cam = {cx: 0, cy: 0, zoom: 1, w: 880, h: 500};"
        "__RESULT = {p00: X.iso(0, 0, 0, cam), p10: X.iso(1, 0, 0, cam),"
        "  p01: X.iso(0, 1, 0, cam), p20: X.iso(2, 0, 0, cam)};")
    p00, p10, p01, p20 = (r["p00"], r["p10"], r["p01"], r["p20"])
    # increasing x moves right (screen x up) and down (screen y up)
    assert p10["x"] > p00["x"], "increasing x must move the point right: %r" % r
    assert p10["y"] > p00["y"], "increasing x must move the point down: %r" % r
    # increasing y moves left (screen x down) and down (screen y up)
    assert p01["x"] < p00["x"], "increasing y must move the point left: %r" % r
    assert p01["y"] > p00["y"], "increasing y must move the point down: %r" % r
    # monotonic and linear in x
    assert abs((p20["x"] - p10["x"]) - (p10["x"] - p00["x"])) < 1e-9, r
    assert abs((p20["y"] - p10["y"]) - (p10["y"] - p00["y"])) < 1e-9, r
    # the x axis and the y axis are not parallel: cross product != 0
    ax, ay = p10["x"] - p00["x"], p10["y"] - p00["y"]
    bx, by = p01["x"] - p00["x"], p01["y"] - p00["y"]
    cross = ax * by - ay * bx
    assert abs(cross) > 1e-9, (
        "the two projection axes are parallel — the projection is degenerate "
        "(not isometric): a=%r b=%r" % ((ax, ay), (bx, by)))
    # and it really is a 2:1 deck ratio, not an accident of the numbers
    assert abs(abs(ax) / abs(ay) - 2.0) < 1e-9, (
        "the x axis is not the documented 2:1 tile ratio: %r" % ((ax, ay),))


def test_higher_z_draws_higher_on_the_screen():
    """A floor with a larger z is painted further up the canvas."""
    r = _js_result(
        "var cam = {cx: 0, cy: 0, zoom: 1, w: 880, h: 500};"
        "__RESULT = {low: X.iso(2, 2, 0, cam), high: X.iso(2, 2, 3, cam)};")
    assert r["high"]["y"] < r["low"]["y"], (
        "a higher deck must be drawn higher on screen: %r" % r)
    assert r["high"]["x"] == r["low"]["x"], (
        "stacking must not move the deck horizontally: %r" % r)


def test_fit_to_building_centres_the_camera_on_the_actual_extent():
    """The 'mostly-empty canvas' fix: the camera is centred on where the rooms
    really are, not left at the origin with the building jammed in a corner."""
    v = make_payload(
        [make_floor(1, 0, "operations",
                    [make_room("far", 20, 20, 6, 6, []),
                     make_room("further", 24, 22, 4, 4, [])])], [])
    r = _js_result(
        "var floors = %s.floorplan.floors;"
        "var e = X.extent(floors);"
        "var cam = {cx: 0, cy: 0, zoom: 1, w: 880, h: 500};"
        "X.fitToBuilding(cam, floors);"
        "__RESULT = {e: e, cam: cam, isoCentre: X.iso(23, 23, 0, cam)};"
        % json.dumps(v))
    assert r["e"] is not None
    # the camera centre must equal the extent centre, not the origin
    assert abs(r["cam"]["cx"] - r["e"]["cx"]) < 1e-9, r
    assert abs(r["cam"]["cy"] - r["e"]["cy"]) < 1e-9, r
    # the building's own middle must project near the middle of the canvas
    assert abs(r["isoCentre"]["x"] - 440) < 120, (
        "the fitted building is not near the canvas centre: %r" % r)


def test_fit_refuses_to_shrink_the_people_below_legibility():
    """A very wide building may overflow the canvas, but the agents may not
    shrink past the legibility floor — an office whose people are invisible is
    not a drawing of an office."""
    v = make_payload(
        [make_floor(1, 0, "operations",
                    [make_room("wide", 0, 0, 200, 200, [])])], [])
    r = _js_result(
        "var cam = {cx: 0, cy: 0, zoom: 1, w: 880, h: 500};"
        "X.fitToBuilding(cam, %s.floorplan.floors);"
        "__RESULT = {zoom: cam.zoom, floor: X.CHAR_FLOOR_ZOOM,"
        "  max: X.MAX_ZOOM};" % json.dumps(v))
    assert r["zoom"] >= r["floor"] - 1e-9, (
        "fit shrank the zoom to %r, below the character legibility floor %r"
        % (r["zoom"], r["floor"]))
    assert r["zoom"] <= r["max"] + 1e-9, r


# ---------------------------------------------------------- the other drawings
def test_the_minimap_draws_a_room_rect_for_every_room():
    """`drawMinimap` must put a rect on the small canvas per room, and a marker
    per character — the same 'draws nothing' failure applies to it."""
    v = make_payload(
        [make_floor(1, 0, "operations",
                    [make_room("a", 0, 0, 2, 2, ["p"]),
                     make_room("b", 4, 1, 3, 2, [])])],
        [make_character("p", 1, "working")])
    r = _js_result(
        "var cv = fakeCanvas(640, 260);"
        "X.drawMinimap(cv, %s, null);"
        "var f = cv._ctx.ops.filter(function (o) { return o.op === 'fillRect'; });"
        "__RESULT = {background: f.filter(function (o) "
        "{ return o.fillStyle === '#141a23'; }).length,"
        "  roomDims: f.filter(function (o) "
        "{ return o.fillStyle === '#2c3848' || o.fillStyle === '#232c3a'; }).length,"
        "  marker: f.filter(function (o) "
        "{ return o.fillStyle === X.ANIM_COLOR.working; }).length};"
        % json.dumps(v))
    assert r["background"] == 1, "the minimap drew no background: %r" % r
    assert r["roomDims"] == 2, (
        "the minimap did not draw one rect per room: %r" % r)
    assert r["marker"] == 1, (
        "the minimap drew no marker for the character: %r" % r)


def test_the_pnl_chart_draws_a_bar_pair_per_day():
    """`drawPnl` must draw a revenue bar and a cost bar for each history row,
    else the collapsed P&L panel is blank."""
    series = [{"day": "d1", "date": "2026-01-01", "revenue_cents": 1000,
               "cost_cents": 400, "net_cents": 600},
              {"day": "d2", "date": "2026-01-02", "revenue_cents": 500,
               "cost_cents": 900, "net_cents": -400}]
    r = _js_result(
        "var cv = fakeCanvas(860, 130);"
        "X.drawPnl(cv, %s);"
        "var ops = cv._ctx.ops;"
        "var f = ops.filter(function (o) { return o.op === 'fillRect'; });"
        "__RESULT = {rev: f.filter(function (o) "
        "{ return o.fillStyle === 'rgba(111,211,166,0.75)'; }).length,"
        "  cost: f.filter(function (o) "
        "{ return o.fillStyle === 'rgba(224,85,74,0.75)'; }).length,"
        "  netLine: ops.filter(function (o) { return o.op === 'stroke' "
        "&& o.strokeStyle === '#5aa9e6'; }).length};" % json.dumps(series))
    assert r["rev"] == 2 and r["cost"] == 2, (
        "the P&L chart must draw one revenue and one cost bar per day: %r" % r)
    assert r["netLine"] == 1, "the net line was not stroked: %r" % r


def test_the_deck_and_walls_are_actually_filled_not_just_paths():
    """A deck is a filled plate plus two filled wall faces. Counting only path
    construction would pass on a renderer that builds paths and never fills."""
    v = make_payload(
        [make_floor(1, 0, "operations",
                    [make_room("open", 0, 0, 4, 4, ["a"])])], [])
    r = _draw(v, (
        "var base = X.deckTint('operations', 1);"
        "__RESULT = {plate: fills(c).filter(function (o) "
        "{ return o.fillStyle === X.shade(base, -0.05); }).length,"
        "  wallA: fills(c).filter(function (o) "
        "{ return o.fillStyle === X.shade(base, 0.10); }).length,"
        "  wallB: fills(c).filter(function (o) "
        "{ return o.fillStyle === X.shade(base, 0.04); }).length,"
        "  clearRect: c.ops.filter(function (o) "
        "{ return o.op === 'clearRect'; }).length};"))
    assert r["clearRect"] == 1, "the frame was not cleared: %r" % r
    assert r["plate"] == 1, "the deck plate was not filled: %r" % r
    assert r["wallA"] == 1 and r["wallB"] == 1, (
        "the two cutaway wall faces were not filled: %r" % r)


def test_a_seat_shows_whether_it_is_occupied_by_its_alpha():
    """An empty desk is drawn dimmer than an occupied one. A room with empty
    desks must look like a room with empty desks."""
    v = make_payload(
        [make_floor(1, 0, "operations",
                    [{"id": "room-mix", "slug": "mix", "kind": "open",
                      "department_id": 1, "x": 0, "y": 0, "w": 4, "h": 4,
                      "seats": [{"slot": 0, "agent_id": "ag-taken",
                                 "slug": "taken", "x": 0, "y": 0},
                                {"slot": 1, "agent_id": None,
                                 "slug": None, "x": 2, "y": 0}]}])],
        [make_character("taken", 1, "idle")])
    r = _draw(v, (
        "__RESULT = {occupied: rects(c).filter(function (o) "
        "{ return o.alpha === 1 && o.fillStyle === '#3d4b60'; }).length,"
        "  empty: rects(c).filter(function (o) "
        "{ return o.alpha === 0.45 && o.fillStyle === '#2a3443'; }).length};"))
    assert r["occupied"] == 1, (
        "the occupied desk was not drawn at full alpha: %r" % r)
    assert r["empty"] == 1, (
        "the empty desk was not drawn dimmer: %r" % r)


def test_an_empty_payload_draws_the_void_and_nothing_else():
    """The negative control for this whole file: with no floors and no
    characters, only the background fillRect and the clear are drawn. If a
    regression starts inventing geometry, this and the count assertions disagree
    and one of them fails."""
    v = make_payload([], [])
    r = _draw(v, (
        "__RESULT = {agents: out.probe.agents.length,"
        "  body: bodyFills(c).length, head: headFills(c).length,"
        "  shadow: shadowFills(c).length,"
        "  fillRects: rects(c).length,"
        "  background: rects(c).filter(function (o) "
        "{ return o.fillStyle === '#0b1016'; }).length,"
        "  clearRect: c.ops.filter(function (o) "
        "{ return o.op === 'clearRect'; }).length};"))
    assert r["background"] == 1, "the void behind the building was not drawn"
    assert r["clearRect"] == 1, "the frame was not cleared"
    assert r["agents"] == 0 and r["body"] == 0, (
        "an empty payload drew a character: %r" % r)
    assert r["head"] == 0 and r["shadow"] == 0, (
        "an empty payload drew character parts: %r" % r)
    assert r["fillRects"] == 1, (
        "an empty payload drew geometry beyond the background: %r" % r)
