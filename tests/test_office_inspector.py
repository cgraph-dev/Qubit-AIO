"""Office — click-to-select picks the RIGHT agent, and what the inspector shows.

Why this file exists
====================
P5 in `docs/game-office/28-HUD-PANELS.md` is the *selected-agent inspector*:
the panel that appears when a figure on the tower is clicked. The panel's own
contents were already speced. What was **not** tested anywhere is the thing the
panel depends on: that the click actually resolves to the agent the user
pointed at.

The renderer used to pick a character by **screen pixel distance** — it had no
way to go from a click back to a tile, so it guessed. That is wrong in
isometric: two cells that differ by (+1, -1) project to the *same* screen row,
and the 2:1 collapse of `x + y` makes the pixel that looks like it belongs to
one figure sit closer to an other. `iso-engine.js` now ships the exact algebraic
inverse, `screenToGrid`, and `qubit-office.js` resolves a click to a **cell**
and selects the agent whose own cell matches. That is the whole point of the
inverse, and it is the most likely thing to be subtly wrong — inverting the two
axes, mishandling `z`, or comparing the wrong distance all produce a renderer
that *looks* right and selects the wrong person.

This file drives the renderer's own `click()` against the real `drawTower`
probe and the real `screenToGrid`, so what is tested is the shipped code path,
not a re-implementation of it:

  * `qubit-office.js` is executed under `node` with a stub SDK, its final
    `register(...)` line rewritten to expose the drawing entry point (the same
    single-transform harness `tests/test_office_tower_visual.py` uses), and the
    resulting `drawTower` is run on a payload to produce the **real probe** —
    the list of `{kind:"agent", gx, gy, x, y, cs, ...}` records click-to-select
    reads.
  * the renderer's `click(ev)` function is cut out of `qubit-office.js` by brace
    matching and evaluated against that probe, so the selection rule under test
    is the renderer's, verbatim. If the function is renamed or restructured the
    extraction fails loudly rather than silently testing nothing.
  * `screenToGrid` is the real module (`iso-engine.js`, loaded under CommonJS).

Every test here can fail, and two are proven to fail under mutation (a scratch
copy of `iso-engine.js` with the inverse's axes swapped, and a scratch copy of
`qubit-office.js` with the cell comparison weakened) — see the report.

FIXED (renderer change, pinned here)
------------------------------------
The selection rule matched on the **cell only**, not the floor. With a floor
focused (the renderer's normal click state) that was unambiguous, because
`redraw()` filters the probe to that floor. With the WHOLE building shown
(`floor: null`, which resolves `z = 0` in `click()`) two figures on different
floors that share a cell were indistinguishable, and the strict `d < bestD`
comparison handed the click to whichever came first in the depth-sorted probe —
the FAR/high deck, not the figure under the pointer.

The renderer now breaks the cell tie on the floor: among the candidates on the
resolved cell it prefers (1) an agent on the focused floor, else (2) the agent
whose projected screen `y` is nearest the click. `test_a_whole_building_cell_collision_resolves_to_the_clicked_floor`
pins the fixed behaviour (it previously pinned the bug); the mutation proof
`test_MUTATION_a_weakened_cell_comparison_selects_the_wrong_agent` shows the
comparison is still load-bearing.

--------------------------------------------------------------------------------
WHAT THE INSPECTOR CAN SHOW vs WHAT IS A DOCUMENTED GAP
--------------------------------------------------------------------------------
Per P5 in `docs/game-office/28-HUD-PANELS.md` and the payload `characters[]`
shape. Fields marked *present* exist in the payload and are asserted below to be
either read by the renderer or carried on the probe; the rest are gaps the doc
already names, listed here so nobody renders an invented value.

Present (the panel may render these, from `characters[]`):
  * Identity        : `slug`, `agent_id`, `seat_type`, `department_id`
                      (`department_id` is null when unassigned — render the role
                      alone, never "dept null").
  * State           : `animation` (+ " (walking)" when `walking`), `stalled`.
  * Location        : `position.floor`, `position.room`, `at`.
  * Last event      : `last_event.kind`, `last_event.age_s` — `last_event` is
                      NULLABLE; a seat with no event shows nothing, not a
                      fabricated event.
  * Context scalars : `active_window_s` (for the stalled line and the freshness
                      meter), `read_only`, `motion_rule`, `schema`.
  * Org roll-ups    : `hud.headcount`, `hud.revenue_cents`, `hud.cost_cents`,
                      `hud.net_cents`, `hud.burn_cents_per_day`,
                      `hud.runway_days` (nullable), `hud.survival.floor_days`,
                      `hud.floors[].{number,role,departments,revenue_cents,
                      cost_cents,net_cents}`.

Documented gaps (NOT in the payload — named in 28-HUD-PANELS.md §5; the panel
must render nothing for each rather than invent a value):
  * N1  character DISPLAY NAME (e.g. "Alice") — only `slug`/`seat_type` exist.
  * N2  per-character MEMORY / RELATIONSHIP / GOAL lines — absent entirely.
  * N3  per-character TASK / current feature title — absent.
  * N4  per-character SPEND / balance — absent (provider spend is cap-attested).
  * N5  org MODEL TIER (best/cheap per seat) — absent.
  * N6  SUPERVISOR NAME — only the numeric `supervisor_id` exists; no label.
  (N7–N11 — activity tab split, active/awaiting roll-up, build cost, per-agent
  daily output, and `position.progress` — are documented gaps too, but they are
  not inspector readouts.)

Also present but read from `iso-engine.js` rather than the payload: each probe
agent carries its grid cell (`gx`, `gy`) and its projected pixel (`x`, `y`), so
the inspector's selection has both the identity and the geometry it needs.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
PLUGIN = REPO / "frontend" / "qubit-app" / "plugins" / "qubit-office"
ENTRY = PLUGIN / "qubit-office.js"
ISO = PLUGIN / "iso-engine.js"

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node is not on PATH")

REGISTER_LINE = 'window.__QUBIT_PLUGINS__.register("qubit-office", Office);'

#: The camera every probe is drawn with. ONE literal for the file: if the
#: renderer and the inverse disagree about a camera field, this is where it
#: shows, and a per-test camera would hide it.
CAM = {"cx": 0, "cy": 0, "zoom": 1, "w": 880, "h": 500}

#: The pick radius the renderer uses for a resolved cell (qubit-office.js
#: `var best = null, bestD = cell ? 2.5 : 24;`). Kept as a literal so a change
#: to the renderer's tolerance is caught by the boundary test below.
PICK_HALF = 2.5


# --------------------------------------------------------------------------- #
# The scratch-copy machinery: the ONLY way this file mutates the code under test
# is by copying a source file into the scratch dir and rewriting that copy. The
# repository sources are never written to.
# --------------------------------------------------------------------------- #

def _scratch_dir(tmp_path: Path) -> Path:
    d = tmp_path / "office-inspector-scratch"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _copy_with_replacement(src: Path, dst: Path, old: str, new: str) -> Path:
    """A scratch copy of `src` at `dst` with `old` replaced by `new` once.

    Refuses to write if `old` is absent (so a mutation that no longer matches
    the source fails the test rather than silently making an unmutated copy).
    """
    text = src.read_text()
    assert old in text, (
        "mutation anchor %r is not in %s — the mutation would be a no-op and "
        "the test would not prove anything" % (old, src.name))
    dst.write_text(text.replace(old, new, 1))
    return dst


# --------------------------------------------------------------------------- #
# payload builders — the documented /api/office/tower shape
# --------------------------------------------------------------------------- #

def make_seat(slug: str, x: int, y: int, slot: int = 0) -> dict:
    return {"slot": slot, "agent_id": "ag-" + slug, "slug": slug, "x": x, "y": y}


def make_room(slug: str, x: int, y: int, w: int, h: int,
              seats: list[dict]) -> dict:
    return {"id": "room-" + slug, "slug": slug, "kind": "open",
            "department_id": 1, "x": x, "y": y, "w": w, "h": h, "seats": seats}


def make_floor(number: int, z: int, role: str, rooms: list[dict]) -> dict:
    return {"id": "floor-%d" % number, "number": number,
            "name": "level %d" % number, "role": role, "theme": role,
            "z": z, "rooms": rooms}


def make_character(slug: str, floor_number: int, x: int, y: int,
                   anim: str = "idle", **extra) -> dict:
    """A character seated at its own cell (x, y) on `floor_number`."""
    c = {
        "agent_id": "ag-" + slug, "slug": slug, "seat_type": "worker",
        "department_id": 1, "supervisor_id": None, "seated": True,
        "position": {"floor": floor_number, "room": "room", "room_id": "room",
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


def make_payload(floors: list[dict], chars: list[dict]) -> dict:
    return {
        "schema": "office/tower", "now": 1700000000.0, "active_window_s": 900,
        "walk_s": 6, "empty": False, "empty_reason": None,
        "hud": {"headcount": len(chars), "revenue_cents": 0, "cost_cents": 0,
                "net_cents": 0, "burn_cents_per_day": 0, "runway_days": None,
                "survival": {"floor_days": None}, "floors": []},
        "floorplan": {"schema": "office/floorplan",
                      "grid": {"cols": 16, "rows": 16, "tile": 1},
                      "floors": floors},
        "characters": chars, "activity": [], "history": [],
        "history_totals": {"revenue_cents": 0, "cost_cents": 0, "net_cents": 0},
        "motion_rule": "A character moves only when an office event says so.",
        "read_only": True, "preview": None,
    }


# --------------------------------------------------------------------------- #
# The harness: run the REAL renderer (drawTower) and the REAL click() in node.
# --------------------------------------------------------------------------- #

def _extract_click(src: str) -> str:
    """The renderer's own `click(ev)` function, by brace matching.

    Raises AssertionError naming the symbol if it is gone — a silent skip would
    let the selection logic rot behind a green suite.
    """
    anchor = "function click(ev) {"
    assert anchor in src, (
        "%s no longer defines %r; this file extracts the renderer's literal "
        "click handler and cannot proceed. Update the anchor."
        % (ENTRY.name, anchor))
    i = src.index(anchor)
    depth = 0
    for j in range(i, len(src)):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[i:j + 1]
    raise AssertionError("unbalanced braces extracting click() from %s"
                         % ENTRY.name)


def _renderer_program(payload_obj: dict, clicks: list[dict]) -> dict:
    """Drive drawTower + the renderer's click() for a list of clicks.

    `clicks` is a list of {"agents_floor": <floor number or null>,
    "mx": <pixel x>, "my": <pixel y>}. Returns, per click, the selected
    character's slug (or None) and a per-agent hit table computed from the
    real probe.
    """
    src = ENTRY.read_text()
    assert REGISTER_LINE in src, (
        "the harness rewrites %r to expose drawTower; it is gone." % REGISTER_LINE)
    mod_src = src.replace(
        REGISTER_LINE,
        "globalThis.__X = {drawTower: drawTower, iso: iso, Office: Office};")
    click_body = _extract_click(src)

    program = _PROGRAM % {
        "iso": json.dumps(str(ISO)),
        "mod": json.dumps(mod_src),
        "click": click_body,
        "payload": json.dumps(payload_obj),
        "clicks": json.dumps(clicks),
    }
    proc = subprocess.run([NODE, "-e", program], capture_output=True,
                          text=True, timeout=60)
    assert proc.returncode == 0, (
        "node failed driving the renderer:\nSTDOUT:\n%s\nSTDERR:\n%s"
        % (proc.stdout, proc.stderr))
    out = proc.stdout.strip()
    assert out.startswith("{"), (
        "node produced no JSON (the renderer may have thrown):\n%s\n%s"
        % (proc.stdout, proc.stderr))
    return json.loads(out)


# The node program. `%`-substituted once; the JS braces are doubled.
_PROGRAM = r"""
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
  __QUBIT_PLUGINS__: {register: function () {}},
  location: {search: ""}
};
globalThis.QubitIso = require(%(iso)s);

/* A 2D context that records nothing it does not have to: drawTower only needs
 * the calls to exist and not throw. */
function FakeCtx() {
  this.fillStyle = "#000"; this.strokeStyle = "#000"; this.globalAlpha = 1;
  this.lineWidth = 1; this.font = ""; this.textAlign = ""; this.textBaseline = "";
}
["save", "restore", "beginPath", "closePath", "moveTo", "lineTo", "fillRect",
 "strokeRect", "rect", "roundRect", "arc", "ellipse", "fill", "stroke",
 "fillText", "strokeText", "clearRect", "setTransform", "translate", "scale",
 "rotate", "clip", "measureText", "createLinearGradient", "setLineDash"
].forEach(function (m) {
  FakeCtx.prototype[m] = function () { return {width: 10}; };
});

var __SRC = %(mod)s;
(0, eval)(__SRC);
var X = globalThis.__X;

/* --- the renderer's own click(), evaluated verbatim ---------------------- */
/* `click` closes over the renderer module's `var ISO = window.QubitIso`, so
 * the binding is recreated here from the same global the renderer reads. */
var ISO = globalThis.QubitIso;
var camRef = {current: {cx: 0, cy: 0, zoom: 1, w: 880, h: 500}};
var probeRef = {current: {agents: []}};
var dataRef = {current: null};
var viewRef = {current: {floor: null}};
var __selected = [];
var setPick = function (p) { __selected.push(p); };
var cv = {getBoundingClientRect: function () { return {left: 0, top: 0}; }};
/* `click` consults `suppressClick` — set by a completed drag so the click that
 * follows a pan is swallowed. Every click this harness drives is a bare click
 * with no preceding drag, so `false` is the faithful value, not a stub. */
var suppressClick = false;
%(click)s

var payload = %(payload)s;
var clicks = %(clicks)s;

/* The whole-building geometry, drawn once with floor: null — used only to show
 * where every figure lands and to expose the raw probe. */
var allCtx = new FakeCtx();
var allProbe = X.drawTower(allCtx, payload, {cam: camRef.current, floor: null});
var table = allProbe.agents.map(function (a) {
  return {slug: a.cs.slug, gx: a.gx, gy: a.gy, gz: a.gz,
          x: a.x, y: a.y, floor: a.floor, kind: a.kind};
});

var results = clicks.map(function (cl) {
  /* Mirror the renderer's own redraw(): what is DRAWN is filtered by the
   * focused floor, and the resulting probe is what a click reads. Drawing
   * whole-building while clicking with a focused floor would test a state the
   * renderer never occupies. */
  var ctx = new FakeCtx();
  var probe = X.drawTower(ctx, payload,
    {cam: camRef.current, floor: cl.agents_floor});
  var agents = probe.agents;
  var z = 0;
  (payload.floorplan.floors || []).forEach(function (f) {
    if (f.number === cl.agents_floor) z = Number(f.z || 0);
  });
  probeRef.current = {agents: agents};
  viewRef.current = {floor: cl.agents_floor};
  dataRef.current = payload;
  __selected = [];
  click({clientX: cl.mx, clientY: cl.my});
  var got = __selected.length ? __selected[__selected.length - 1] : null;
  /* The renderer stores an agent ID and DERIVES the character from the current
   * snapshot (so the inspector cannot go stale). This resolves it the same
   * way, against the payload being drawn — reading `got.slug` here would only
   * work for the old store-the-object behaviour. */
  var pickedSlug = null;
  if (got !== null && got !== undefined) {
    (payload.characters || []).forEach(function (c) {
      if (c.agent_id === got) pickedSlug = c.slug;
    });
  }
  return {mx: cl.mx, my: cl.my, agents_floor: cl.agents_floor,
          z: z,
          drawn: agents.map(function (a) { return a.cs.slug; }),
          picked: pickedSlug,
          picked_id: got,
          cell: globalThis.QubitIso.screenToGrid(cl.mx, cl.my,
                                                 camRef.current, z)};
});

console.log(JSON.stringify({table: table, results: results}));
"""


def _project(x: float, y: float, z: float) -> dict:
    """The real projector, from the module — not re-derived in Python."""
    program = (
        "var ISO = require(%s);\n"
        "console.log(JSON.stringify(ISO.gridToScreen(%s, %s, %s, %s)));"
        % (json.dumps(str(ISO)), json.dumps(x), json.dumps(y), json.dumps(z),
           json.dumps(CAM)))
    proc = subprocess.run([NODE, "-e", program], capture_output=True, text=True,
                          timeout=60)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip())


def _cell_of(px: float, py: float, z: float) -> dict:
    program = (
        "var ISO = require(%s);\n"
        "var c = ISO.screenToGrid(%s, %s, %s, %s);\n"
        "console.log(JSON.stringify(c));"
        % (json.dumps(str(ISO)), json.dumps(px), json.dumps(py),
           json.dumps(CAM), json.dumps(z)))
    proc = subprocess.run([NODE, "-e", program], capture_output=True, text=True,
                          timeout=60)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip())


# --------------------------------------------------------------------------- #
# The fixture: agents at KNOWN, distinct screen positions on ONE floor.
# --------------------------------------------------------------------------- #

#: cell -> slug. Chosen so no two share a screen row or column after the 2:1
#: collapse, and so each pair's projected pixels are provably distinct.
ONE_FLOOR_AGENTS = [
    ("alpha", 2, 2),
    ("bravo", 6, 1),
    ("charlie", 9, 5),
    ("delta", 1, 8),
]


def _one_floor_fixture() -> tuple[dict, list[dict]]:
    """Payload + the click points, on a single floor with z=0."""
    seats = [make_seat(s, x, y, slot=i)
             for i, (s, x, y) in enumerate(ONE_FLOOR_AGENTS)]
    chars = [make_character(s, 1, x, y) for (s, x, y) in ONE_FLOOR_AGENTS]
    floors = [make_floor(1, 0, "operations",
                         [make_room("open", 0, 0, 16, 16, seats)])]
    v = make_payload(floors, chars)
    clicks = []
    for (slug, x, y) in ONE_FLOOR_AGENTS:
        p = _project(x, y, 0)
        clicks.append({"agents_floor": 1, "mx": p["x"], "my": p["y"],
                       "want": slug})
    return v, clicks


# ========================================================================== #
# 1. The harness runs the renderer at all
# ========================================================================== #
def test_the_harness_selects_the_agent_whose_own_cell_was_clicked():
    """The central requirement, on a single floor.

    For every agent the click is placed at the pixel `gridToScreen` says the
    agent is drawn at, and the renderer must select THAT agent. The per-agent
    hit table is asserted cell-for-cell so a near-miss is visible.
    """
    v, clicks = _one_floor_fixture()
    out = _renderer_program(v, clicks)

    # the probe really carries every agent, with its own cell
    by_slug = {a["slug"]: a for a in out["table"]}
    assert set(by_slug) == {s for (s, _, _) in ONE_FLOOR_AGENTS}, out["table"]

    hit_table = []
    for cl, res in zip(clicks, out["results"]):
        hit_table.append((cl["want"], res["picked"]))
    wrong = [(want, got) for (want, got) in hit_table if want != got]
    assert not wrong, (
        "clicking an agent's own projected pixel selected a different agent:\n"
        "  want -> got: %r\n  full table: %r\n  probe: %r"
        % (wrong, hit_table, out["table"]))


def test_a_click_in_empty_space_selects_nothing_not_the_nearest_agent():
    """An empty click must CLEAR the selection.

    The failure this guards is worse than a no-op: an inspector that snaps to
    the nearest figure anywhere on the floor invents a selection, i.e. it claims
    the user selected someone they did not. The click point here is far from
    every figure's cell (and far from every projected pixel), so the resolved
    cell is outside the pick radius and the result must be null.
    """
    v, clicks = _one_floor_fixture()
    empty = {"agents_floor": 1, "mx": 20.0, "my": 20.0}
    out = _renderer_program(v, clicks + [empty])
    res = out["results"][-1]
    assert res["picked"] is None, (
        "a click in empty space invented a selection: %r (probe: %r)"
        % (res, out["table"]))

    # also: a click just off the whole building, and a click on a tile that is
    # inside the room but has no agent on it.
    elsewhere = [{"agents_floor": 1, "mx": 4000.0, "my": 4000.0},
                 {"agents_floor": 1, "mx": 12.0, "my": 300.0}]
    out2 = _renderer_program(v, elsewhere)
    for r in out2["results"]:
        assert r["picked"] is None, (
            "an off-building click invented a selection: %r" % r)


# ========================================================================== #
# 2. The cross-floor hard case: equal (x + y), different floors
# ========================================================================== #
def _cross_floor_fixture() -> tuple[dict, list[dict]]:
    """Two agents whose un-z'd projections coincide, on DIFFERENT floors.

    `gridToScreen` is `x = (col-row)*TW/2`, `y = (col+row)*TH/2 - z*DECK_Z`.
    Ignore the `z` term -- "un-z'd" -- and the two figures below project to the
    SAME point, because they share the cell (2,3):

        "ground" on floor 1 (z=0)  -> pixel (423, 292.5)
        "upper"  on floor 2 (z=5)  -> pixel (423, 162.5)

    Only the deck term (`5 * DECK_Z = 130px`) separates them. If the renderer
    inverted the click with the WRONG floor's z -- or if it had to guess a floor
    from the pixel -- the same click would resolve to a different cell and the
    wrong figure would win. A third figure, "upper2", sits on the far floor at
    (5,2), which shares `x + y = 7` with cells on floor 1, so the far floor is
    not a single-figure degenerate.

    The click on each figure is made with THAT figure's floor focused, which is
    the only state in which the renderer knows which `z` to invert with (the
    focused floor is what `drawTower` drew, and what the click handler reads for
    `z`). The assertion is that each click selects the agent on the floor whose
    projected point is actually under the pointer -- not the other one, and not
    null.
    """
    # floor 1 (z=0): "ground" at (2,3)
    f1_seats = [make_seat("ground", 2, 3, 0)]
    # floor 2 (z=5): same CELL (2,3) -> same un-z'd projection, different pixel
    #                 plus "upper2" at (5,2) so the far floor is non-degenerate.
    f2_seats = [make_seat("upper", 2, 3, 0), make_seat("upper2", 5, 2, 1)]
    chars = [make_character("ground", 1, 2, 3),
             make_character("upper", 2, 2, 3),
             make_character("upper2", 2, 5, 2)]
    floors = [make_floor(1, 0, "operations",
                         [make_room("ground", 0, 0, 16, 16, f1_seats)]),
              make_floor(2, 5, "executive",
                         [make_room("upper", 0, 0, 16, 16, f2_seats)])]
    v = make_payload(floors, chars)

    pz0 = _project(2, 3, 0)      # "ground" pixel
    pz5 = _project(2, 3, 5)      # "upper" pixel  (same x, y - 130)
    p_up2 = _project(5, 2, 5)    # "upper2" pixel

    clicks = [
        {"agents_floor": 1, "mx": pz0["x"], "my": pz0["y"], "want": "ground"},
        {"agents_floor": 2, "mx": pz5["x"], "my": pz5["y"], "want": "upper"},
        {"agents_floor": 2, "mx": p_up2["x"], "my": p_up2["y"],
         "want": "upper2"},
    ]
    return v, clicks


def test_a_click_on_a_figure_selects_the_agent_on_that_figures_floor():
    """The cross-floor case: an un-z'd coincidence resolves to the right figure.

    Two agents share the cell (2,3) on two floors. Their un-z'd projections are
    the same point; with z applied they are 130px apart vertically. Clicking the
    pixel of the upper figure with the upper floor focused must select "upper";
    clicking the lower figure's pixel with the lower floor focused must select
    "ground". A renderer that inverted with the wrong z, or resolved the click
    by screen distance, selects the wrong one here.

    The figures are drawn with the floor focused (the renderer's own `redraw`
    filters `shown` by `viewRef.current.floor`), so the probe holds only that
    floor and the click's `z` is unambiguous -- which is exactly the state a
    click is made in.
    """
    v, clicks = _cross_floor_fixture()

    # whole-building geometry first: the two same-cell figures share a screen x
    # and differ only in y, proving the coincidence the test is about.
    out_all = _renderer_program(v, clicks)
    table = {a["slug"]: a for a in out_all["table"]}
    assert table["ground"]["x"] == table["upper"]["x"], out_all["table"]
    assert table["ground"]["gy"] == table["upper"]["gy"] == 3, out_all["table"]
    assert table["ground"]["y"] != table["upper"]["y"], (
        "the deck term did not lift the upper figure: %r" % out_all["table"])

    # and the selection, per focused floor, is the correct figure.
    for res, cl in zip(out_all["results"], clicks):
        assert res["picked"] == cl["want"], (
            "click on %r's projected pixel selected %r (floor focused: %r). "
            "This is the cross-floor case: an un-z'd coincidence must resolve "
            "to the agent on the floor whose point is really there.\n"
            "  result: %r\n  probe: %r"
            % (cl["want"], res["picked"], cl["agents_floor"], res,
               out_all["table"]))


def test_a_whole_building_cell_collision_resolves_to_the_clicked_floor():
    """The whole-building cross-floor cell collision is now FIXED, and pinned.

    With the WHOLE building shown (`floor: null`) the probe carries both floors'
    figures. `upper` and `ground` share the cell (2,3) but sit on different
    floors, so they have DIFFERENT projected pixels (130 px apart vertically).

    This test previously asserted the BUG: the renderer's comparison was
    `d < bestD` (strict) over a depth-sorted probe, so the FIRST figure in probe
    order won an exact cell tie — the FAR/high deck — regardless of which figure
    the user pointed at. The click rule matched on grid CELL only, ignoring `z`.

    The fix makes the rule floor-aware: among the candidates on the resolved
    cell, the renderer prefers (1) an agent on the focused floor, else (2) the
    agent whose projected screen `y` is NEAREST the click. So a whole-building
    click on `ground`'s own pixel now selects `ground`, and a click on `upper`'s
    pixel selects `upper` — the figure actually under the pointer.
    """
    v, clicks = _cross_floor_fixture()
    # both floors are in the probe, and they collide on the cell
    ground_click = dict(clicks[0]); ground_click["agents_floor"] = None
    out = _renderer_program(v, [ground_click])
    table = {a["slug"]: a for a in out["table"]}
    assert table["ground"]["gx"] == table["upper"]["gx"] == 2
    assert table["ground"]["gy"] == table["upper"]["gy"] == 3
    # the whole-building click resolved to the shared cell (the click's z is 0
    # in whole-building view, so `ground`'s own pixel is the one that lands on it)
    assert out["results"][0]["cell"] == {"x": 2, "y": 3}, out["results"][0]
    # ...and the screen-`y`-nearest tie-break now picks the figure under the
    # pointer (`ground`, whose projected y = 292.5 is the click), NOT the
    # far/high deck (`upper`, probe order first but y = 162.5).
    assert out["results"][0]["picked"] == "ground", (
        "a whole-building click on the shared cell selected %r; the floor-aware "
        "tie-break must pick the figure whose projected y is nearest the click "
        "(`ground` at 292.5, not `upper` at 162.5). result: %r"
        % (out["results"][0]["picked"], out["results"][0]))

    # and the two figures genuinely differ in projected y (the property the
    # tie-break reads): the same cell, different decks, 130 px apart.
    assert table["ground"]["y"] != table["upper"]["y"], out["table"]
    assert abs(table["ground"]["y"] - table["upper"]["y"]) > 12.0, out["table"]


def test_the_wrong_floor_resolves_to_a_different_cell_so_the_test_is_load_bearing():
    """Negative control for the cross-floor test: the two floors' z's differ.

    If `screenToGrid` ignored `z`, the same pixel would resolve to the same
    cell whatever floor was focused and the cross-floor test above would pass
    for the wrong reason. This asserts the two inversions genuinely give
    different cells, so the case is real.
    """
    pz5 = _project(2, 3, 5)
    at_right_z = _cell_of(pz5["x"], pz5["y"], 5)
    at_wrong_z = _cell_of(pz5["x"], pz5["y"], 0)
    assert at_right_z == {"x": 2, "y": 3}, at_right_z
    assert at_wrong_z != at_right_z, (
        "z did not affect the inverse — the cross-floor case would be vacuous:"
        " right=%r wrong=%r" % (at_right_z, at_wrong_z))


# ========================================================================== #
# 3. The tolerance boundary is real and finite
# ========================================================================== #
def test_a_click_just_inside_a_figures_cell_selects_it_and_just_outside_does_not():
    """The pick tolerance is real and finite.

    A resolved click selects an agent when its cell is within the renderer's
    pick radius (`bestD = 2.5` in a CELL metric) and selects nothing beyond it.
    A LONE agent sits at cell (2,2) (isolated so no neighbour can steal the
    pick); a pixel is walked away from it in a straight screen line and the
    renderer is asked at each step. The selection must be present while the
    resolved cell is within the radius and must VANISH once it leaves — not
    snap to another agent, not stay stuck. That is what stops the tolerance
    being infinite (inventing a selection) or the click being unselectable.
    """
    lone = make_payload(
        [make_floor(1, 0, "operations",
                    [make_room("open", 0, 0, 16, 16,
                               [make_seat("solo", 2, 2, 0)])])],
        [make_character("solo", 1, 2, 2)])
    px = _project(2, 2, 0)["x"]
    py = _project(2, 2, 0)["y"]

    step = 2.0            # world pixels per step (screen at zoom 1)
    picks = []
    for n in range(0, 21):
        r = _renderer_program(
            lone, [{"agents_floor": 1, "mx": px + n * step, "my": py + n * step}])
        picks.append((n, r["results"][0]["cell"], r["results"][0]["picked"]))

    assert picks[0][2] == "solo", picks           # the click ON the figure
    selected = [p for p in picks if p[2] == "solo"]
    cleared = [p for p in picks if p[2] is None]

    # both sides of the boundary must exist, or the tolerance is degenerate
    assert selected, "no click near the figure selected it: %r" % picks
    assert cleared, (
        "the pick tolerance never cleared — it is unbounded, so a click "
        "anywhere would invent a selection: %r" % picks)

    first_clear = cleared[0][0]
    last_select = max(p[0] for p in selected)
    assert first_clear == last_select + 1, (
        "the boundary is not a single flip: last select at %r, first clear at "
        "%r — %r" % (last_select, first_clear, picks))
    # it is a genuine, near boundary: the flip happens well inside the walk,
    # not at the end (an 'eventually clears' tolerance would be unbounded in
    # practice).
    assert first_clear < len(picks) - 1, picks
    # once cleared, it stays cleared as the click moves further away
    for n, cell, picked in picks[first_clear:]:
        assert picked is None, (
            "the selection came back at step %d (cell %r) after clearing — the "
            "tolerance is not monotone: %r" % (n, cell, picks))


def test_a_click_on_a_tile_that_has_no_agent_selects_nothing():
    """The other half of the boundary: a bare tile inside the room is not a pick.

    Every cell in the room is a clickable tile; only cells with an agent on them
    may select. A click resolved to an agentless cell beyond the radius clears,
    so the tolerance is about the AGENT's cell, not about the tile being
    drawable.
    """
    v, _ = _one_floor_fixture()
    # cell (12, 12) is inside the 16x16 room but no fixture agent is near it
    p = _project(12, 12, 0)
    out = _renderer_program(v, [{"agents_floor": 1, "mx": p["x"], "my": p["y"]}])
    res = out["results"][0]
    assert res["cell"] == {"x": 12, "y": 12}, res
    assert res["picked"] is None, (
        "a click on an agentless tile selected an agent: %r (probe: %r)"
        % (res, out["table"]))


def test_the_pick_radius_the_boundary_test_assumes_is_still_the_renderers():
    """Pin the renderer's pick radius literal so the boundary test is anchored.

    The boundary test above is only meaningful if the tolerance it exercises is
    the renderer's. This asserts the literal is still present, and fails if it
    changed shape — so the boundary test cannot drift away from the code.
    """
    src = ENTRY.read_text()
    m = re.search(r"bestD\s*=\s*cell\s*\?\s*([0-9.]+)\s*:\s*([0-9.]+)", src)
    assert m, (
        "the renderer's pick-radius expression changed shape; the boundary "
        "test in this file no longer describes it. Update the regex.")
    assert float(m.group(1)) == PICK_HALF, (
        "the renderer's cell pick radius changed from %r to %r — update "
        "PICK_HALF and re-check the boundary test" % (PICK_HALF, m.group(1)))


# ========================================================================== #
# 4. The probe carries the fields the inspector needs
# ========================================================================== #
def test_the_selected_character_carries_the_fields_an_inspector_can_show():
    """What a click hands the panel: the character object plus geometry.

    The probe's `cs` is the payload character verbatim, and it carries every
    field P5 documents as present. This asserts the PRESENT set, and asserts the
    documented gap fields are ABSENT so a renderer cannot read a value that does
    not exist.
    """
    v, clicks = _one_floor_fixture()
    out = _renderer_program(v, [clicks[0]])
    res = out["results"][0]
    assert res["picked"] == "alpha", res

    # the probe's character object is the payload character verbatim; read it
    # from the payload (the probe copies the same object).
    char = [c for c in v["characters"] if c["slug"] == "alpha"][0]
    present = {"agent_id", "slug", "seat_type", "department_id",
               "supervisor_id", "seated", "position", "facing", "walking",
               "at", "animation", "last_event", "stalled"}
    assert present <= set(char), sorted(set(char))
    assert {"floor", "room", "x", "y"} <= set(char["position"]), char["position"]

    # the documented GAPS must not be present as fields on the character
    for gap in ("display_name", "name", "memory", "goal", "task",
                "task_id", "spend_cents", "balance_cents", "model_tier",
                "supervisor_slug", "supervisor_name"):
        assert gap not in char, (
            "%r is a documented gap (28-HUD-PANELS.md §5) but appeared on the "
            "character — if a backend field landed, update the gaps list in "
            "this file's docstring" % gap)


def test_the_probe_gives_each_agent_its_own_cell_and_pixel():
    """Selection needs both: the cell (to match) and the pixel (to draw).

    A probe that carried only the pixel would force the screen-distance guess
    back; one that carried only the cell could not place the figure. Both are
    asserted distinct and correct for the fixture.
    """
    v, clicks = _one_floor_fixture()
    out = _renderer_program(v, clicks)
    seen_cells, seen_pixels = set(), set()
    for a in out["table"]:
        assert a["kind"] == "agent", a
        assert a["gz"] == 0, a
        cell = (a["gx"], a["gy"])
        px = (a["x"], a["y"])
        assert cell not in seen_cells, "two agents share a cell: %r" % a
        assert px not in seen_pixels, "two agents share a pixel: %r" % out["table"]
        seen_cells.add(cell)
        seen_pixels.add(px)


# ========================================================================== #
# 5. Mutation proofs — a scratch copy is broken; the tests above must fail.
# ========================================================================== #
def test_MUTATION_an_inverted_inverse_breaks_the_click_selection(tmp_path):
    """PROOF the selection tests above can fail: invert the inverse's axes.

    A scratch copy of `iso-engine.js` is made with `screenToGrid`'s two output
    axes swapped (the exact off-by-a-transpose bug `screenToGrid`'s own
    docstring warns about), and the REAL renderer is run with that engine
    injected. The baseline (real engine) selects every agent; the mutated one
    must NOT. Both halves are asserted here, so this test fails if the mutation
    stops changing anything (anchor moved, or the selection tests stopped
    exercising `screenToGrid`).
    """
    d = _scratch_dir(tmp_path)
    mutated = _copy_with_replacement(
        ISO, d / "iso-engine.js",
        "return {\n      x: Math.round((wx / hw + wy / hh) / 2),\n"
        "      y: Math.round((wy / hh - wx / hw) / 2)\n    };",
        "return {\n      x: Math.round((wy / hh - wx / hw) / 2),\n"
        "      y: Math.round((wx / hw + wy / hh) / 2)\n    };")

    v, clicks = _one_floor_fixture()

    base = _renderer_program(v, clicks)
    base_picks = [(cl["want"], r["picked"])
                  for cl, r in zip(clicks, base["results"])]
    assert all(w == g for (w, g) in base_picks), (
        "the baseline did not select the clicked agent, so the mutation proof "
        "is meaningless: %r" % base_picks)

    out = _renderer_program_with_iso(mutated, v, clicks)
    picks = [(cl["want"], r["picked"]) for cl, r in zip(clicks, out["results"])]
    assert any(want != got for (want, got) in picks), (
        "swapping the inverse's axes did NOT change the selection — the "
        "selection tests are not exercising screenToGrid. picks=%r" % picks)
    # concrete divergence captured for the report
    print("\nMUTATION A (axes swapped) one-floor picks: %r" % picks)


def test_MUTATION_a_weakened_cell_comparison_selects_the_wrong_agent(tmp_path):
    """PROOF the whole-building selection test can fail: revert to probe order.

    The renderer breaks a cell tie on the floor: among the candidates on the
    resolved cell it prefers an agent on the focused floor, else the one whose
    projected screen `y` is nearest the click. A scratch copy of
    `qubit-office.js` is made whose tie-break is neutralised — the nearest-`y`
    comparison is replaced by an unconditional overwrite, so the LAST candidate
    in probe order wins (a stand-in for the old "first probe entry" rule, which
    the depth-sorted probe makes the far/high deck). Exercised on the
    WHOLE-BUILDING case (two same-cell figures in one probe — the only case where
    the tie-break has a choice): the click must stop selecting the figure under
    the pointer; the unmutated renderer must select it. Both halves asserted.
    """
    d = _scratch_dir(tmp_path)
    mutated = _copy_with_replacement(
        ENTRY, d / "qubit-office.js",
        "if (dy < pickDy - 1e-9 ||\n"
        "                (Math.abs(dy - pickDy) <= 1e-9 && c.d < pickD - 1e-9)) {\n"
        "              pickDy = dy; pickD = c.d; pick = c.a.cs;\n"
        "            }",
        "if (pick === null) { pickDy = dy; pickD = c.d; pick = c.a.cs; }")
    click_body = _extract_click(mutated.read_text())

    v, clicks = _cross_floor_fixture()
    # the whole-building click on the shared cell: the case with a real choice
    wb = dict(clicks[0]); wb["agents_floor"] = None

    base = _renderer_program_raw(ENTRY, _extract_click(ENTRY.read_text()),
                                 v, [wb])
    assert base["results"][0]["picked"] == "ground", (
        "the baseline did not select the figure under the pointer, so the "
        "mutation proof is meaningless: %r" % base["results"][0])

    out = _renderer_program_raw(mutated, click_body, v, [wb])
    assert out["results"][0]["picked"] != "ground", (
        "neutralising the nearest-y tie-break did NOT change the whole-building "
        "selection — the test is not exercising the tie-break. result=%r"
        % out["results"][0])
    print("\nMUTATION B (tie-break neutralised) whole-building pick: %r"
          % out["results"][0]["picked"])


# --------------------------------------------------------------------------- #
# harness variants that take a SCRATCH engine / scratch renderer
# --------------------------------------------------------------------------- #

def _run_node_with(iso_path: Path, mod_src: str, click_body: str,
                   payload_obj: dict, clicks: list[dict]) -> dict:
    program = _PROGRAM % {
        "iso": json.dumps(str(iso_path)),
        "mod": json.dumps(mod_src),
        "click": click_body,
        "payload": json.dumps(payload_obj),
        "clicks": json.dumps(clicks),
    }
    proc = subprocess.run([NODE, "-e", program], capture_output=True, text=True,
                          timeout=60)
    assert proc.returncode == 0, (
        "node failed:\nSTDOUT:\n%s\nSTDERR:\n%s" % (proc.stdout, proc.stderr))
    out = proc.stdout.strip()
    assert out.startswith("{"), out + proc.stderr
    return json.loads(out)


def _renderer_program_with_iso(iso_path: Path, payload_obj: dict,
                               clicks: list[dict]) -> dict:
    """The real renderer, but a SCRATCH iso-engine.js injected in its place."""
    src = ENTRY.read_text()
    mod_src = src.replace(
        REGISTER_LINE,
        "globalThis.__X = {drawTower: drawTower, iso: iso, Office: Office};")
    return _run_node_with(iso_path, mod_src, _extract_click(src),
                          payload_obj, clicks)


def _renderer_program_raw(entry_path: Path, click_body: str,
                          payload_obj: dict, clicks: list[dict]) -> dict:
    """A SCRATCH renderer source, with the click body taken from that copy."""
    src = entry_path.read_text()
    mod_src = src.replace(
        REGISTER_LINE,
        "globalThis.__X = {drawTower: drawTower, iso: iso, Office: Office};")
    return _run_node_with(ISO, mod_src, click_body, payload_obj, clicks)
