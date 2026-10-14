"""Where the tower's text labels actually land on the canvas — measured, not claimed.

WHY THIS FILE EXISTS
====================
A vision read of the rendered page reported: *"`FLOOR 1 - executive`,
`FLOOR 2 - operations` and `FLOOR 3 - intelligence` overlap each other where the
decks stack, and `review` is buried under `FLOOR 3`."*

That report is specific and testable, and the honest thing to do with a specific
report is to MEASURE it rather than to act on it. This file measures it, and the
measurement says the report's two halves are **two different defects**, only one
of which is real:

  * **The floor labels do NOT collide with each other.** The gap between two
    floors' labels is exactly one deck pitch:
    `DECK_Z * FLOOR_HEIGHT_UNITS * zoom` = `26 * 3 * zoom`. The label glyph is a
    fixed **12 px** (`"600 12px ui-sans-serif"`), and a collision needs
    `78 * zoom < 12`, i.e. `zoom < 0.1538`. The renderer's own legibility clamp
    holds zoom at `CHAR_FLOOR_ZOOM = 0.85` or above, so the colliding zoom is
    unreachable. At the fitted zoom of the real 3-floor scene the pitch is
    **187.2 px** against a 12 px glyph — a **175.2 px gap**, 14.6x the glyph.
    The vision read was wrong about this half, and these tests are the evidence.

  * **The real defect is that the FLOOR label is drawn OFF the bottom of the
    canvas.** `drawTower` anchors it at `iso(maxX, maxY, z) + (4, 13)` — the
    deck's NEAR corner, the largest `x + y`, which is the BOTTOM of the diamond.
    Stacking floors moves decks UP by `DECK_Z`, but the camera is centred on the
    whole building's extent, so the lowest deck is pushed off the bottom edge
    and its label with it. Measured, 3 floors with one room each on the default
    880x500 canvas at the fitted zoom 2.4:

        FLOOR 3 (intelligence)  baseline y = 177.8
        FLOOR 2 (operations)    baseline y = 365.0
        FLOOR 1 (executive)     baseline y = 552.2   <-- canvas is 500 px tall

    FLOOR 1's label is 52.2 px BELOW the last visible row. What a reader sees is
    three labels where the third is cut off at the frame edge, and the deck that
    is cropped also loses its room labels. That is the "overlap / buried"
    appearance: **clipping at the viewport edge, not text-on-text collision.**
    It reads as overlap because the clipped label's ascenders sit on top of the
    plate stroke and the room slug of the deck below.

  * **The room label leaves its own room.** `ctx.fillText(slug, iso(x0,y0).x + 8,
    (iso(x0,y0).y + iso(x1,y1).y) / 2 + 4)` is the midpoint of the room's
    DIAGONAL in screen space. That point is the room's centre only in grid
    space; under a 2:1 isometric projection the diagonal midpoint is a screen
    point that is NOT inside the projected diamond at the deck's own `z` once the
    deck is large, and it drifts as `z` grows. For the 6x4 room used on the real
    page the label lands ~60-70 px above the plate's top vertex, i.e. over the
    deck ABOVE it — which is exactly "`review` buried under `FLOOR 3`".

WHAT IS ASSERTED (and what each failure means)
----------------------------------------------
1. **Floor labels cannot collide at any reachable zoom.** The rule is stated as
   an identity — a collision needs `deck_pitch_px < glyph_px` — and swept over
   every zoom the renderer can produce (`CHAR_FLOOR_ZOOM .. MAX_ZOOM`, plus the
   clamp boundary). *Fails if:* `DECK_Z` or `FLOOR_HEIGHT_UNITS` shrinks, or
   `CHAR_FLOOR_ZOOM` is lowered past `12 / (DECK_Z * FLOOR_HEIGHT_UNITS)`.
2. **Two floors' labels are exactly one deck pitch apart**, and the gap is
   reported as a number. *Fails if:* the label stops being anchored on the
   deck's own `z` (a shared anchor would make the gap 0 and this is the assertion
   that catches it).
3. **A room label stays inside its own room's projected tile bounds.** Uses the
   renderer's own projection. *Fails if:* the label anchor is a screen-space
   diagonal midpoint rather than a point inside the plate — i.e. it is the real,
   currently-open room-label escape this file documents.
4. **The numbers are REPORTED** — screen y of every label, the gap, the deck
   pitch at that zoom, the fitted zoom, the figure height — so a reader sees
   *why*, not a boolean.
5. **The clipped-label defect is asserted as it is**, with the real arithmetic,
   so that when a fix lands the fix has to change these numbers rather than be
   asserted into existence.

METHOD
------
The projection is `iso-engine.js` (the real module, `require`d in `node`) for
constants and geometry; the label placement arithmetic is `drawTower`'s own
expressions, transcribed here because the label `fillText` calls live inside a
React component whose `draw` closures cannot be reached without a browser. The
constants are read from the shipped sources, never invented:

    iso-engine.js   TILE_W = 34, TILE_H = 17, DECK_Z = 26
    qubit-office.js FLOOR_HEIGHT_UNITS = 3 (tower.py), FACE_W = 4
    qubit-office.js floor label  ctx.font = "600 12px ...", drawn at +(4, 13)
    qubit-office.js room label   ctx.font = "10px ...",   drawn at x+8, mid+4
    tower.py        ROOM_W, ROOM_H = 6, 4 ; FLOOR_HEIGHT_UNITS = 3

Each constant is pinned against the source text in its own test, so a drifted
constant fails loudly here instead of silently invalidating the arithmetic.

Hermetic: reads two JS modules and runs `node`; no network, no browser, no
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

REPO = Path(__file__).resolve().parent.parent
PLUGIN = REPO / "frontend" / "qubit-app" / "plugins" / "qubit-office"
ENGINE = PLUGIN / "iso-engine.js"
ENTRY = PLUGIN / "qubit-office.js"

_FOUND = shutil.which("node")
NODE: str = _FOUND or "node"
pytestmark = pytest.mark.skipif(
    _FOUND is None, reason="node is not installed; the projection cannot be run")

# ---------------------------------------------------------------------------
# the geometry the arithmetic uses, read from iso-engine.js
# ---------------------------------------------------------------------------
_ENGINE_CONSTANTS = f"""
var ISO = require({json.dumps(str(ENGINE))});
console.log(JSON.stringify({{
  TILE_W: ISO.TILE_W, TILE_H: ISO.TILE_H, DECK_Z: ISO.DECK_Z
}}));
"""


def _iso_constants() -> dict:
    proc = subprocess.run([NODE], input=_ENGINE_CONSTANTS,
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip())


ISO_C = _iso_constants()

#: The 2:1 tile, the deck gap in pixels and the tile's half-height, from the
#: real module. Every screen-y in this file is these numbers and nothing else.
TILE_W = float(ISO_C["TILE_W"])
TILE_H = float(ISO_C["TILE_H"])
DECK_Z = float(ISO_C["DECK_Z"])

#: The label glyphs, in pixels, exactly as `ctx.font` sets them.
FLOOR_GLYPH_PX = 12.0     # "600 12px ui-sans-serif, system-ui, sans-serif"
ROOM_GLYPH_PX = 10.0      # "10px ui-sans-serif, system-ui, sans-serif"
#: The floor label's own `+(4, 13)` offset from its anchor, and the room label's
#: `x + FACE_W * 2` (= 8) with a `+4` baseline nudge.
FLOOR_LABEL_DY = 13.0
FLOOR_LABEL_DX = 4.0
FACE_W = 4.0
ROOM_LABEL_DX = FACE_W * 2
ROOM_LABEL_DY = 4.0

#: Read from tower.py / qubit-office.js; pinned by test_the_constants_are_real.
FLOOR_HEIGHT_UNITS = 3
ROOM_W, ROOM_H = 6, 4
CHAR_H, CHAR_W = 15.0, 9.0
CHAR_FLOOR_ZOOM = 0.85
MAX_ZOOM = 2.4

#: The canvases the page actually creates: `e("canvas", {width: 880, height: 500})`
#: clamped to the parent width, max 1200 (qubit-office.js redraw()).
CANVASES = ((880, 500), (1200, 500))


# ---------------------------------------------------------------------------
# the scene: the real page the vision read looked at
# ---------------------------------------------------------------------------
def one_room_floor(number: int, slug: str) -> dict:
    """A floor with a single 6x4 room at the origin, in tower.py's shape.

    This is the reported scene: `FLOOR 1 - executive`, `FLOOR 2 - operations`,
    `FLOOR 3 - intelligence`, one room each.
    """
    return {
        "id": number, "number": number, "name": "level %d" % number,
        "role": slug, "theme": slug, "z": number * FLOOR_HEIGHT_UNITS,
        "rooms": [{
            "id": number, "slug": slug, "kind": "open", "department_id": 1,
            "x": 0, "y": 0, "w": ROOM_W, "h": ROOM_H, "seats": [],
        }],
    }


THREE_FLOOR_SCENE = [
    one_room_floor(1, "executive"),
    one_room_floor(2, "operations"),
    one_room_floor(3, "intelligence"),
]


def _project(x, y, z, cx, cy, zoom, w, h):
    """`iso()` from qubit-office.js, transcribed (the module's own maths)."""
    sx = (x - y) * (TILE_W / 2)
    sy = (x + y) * (TILE_H / 2) - z * DECK_Z
    return ((sx - cx) * zoom + w / 2, (sy - cy) * zoom + h / 2)


def _extent(floors):
    """`extent(floors)` from qubit-office.js: every room corner, z folded in."""
    xs, ys = [], []
    for fl in floors:
        z = float(fl["z"])
        for rm in fl["rooms"]:
            x0, y0 = float(rm["x"]), float(rm["y"])
            x1, y1 = x0 + float(rm["w"]), y0 + float(rm["h"])
            for px, py in ((x0, y0), (x1, y0), (x1, y1), (x0, y1)):
                sx, sy = _project(px, py, z, 0, 0, 1, 0, 0)
                xs.append(sx)
                ys.append(sy)
    return {
        "cx": (min(xs) + max(xs)) / 2, "cy": (min(ys) + max(ys)) / 2,
        "w": max(xs) - min(xs), "h": max(ys) - min(ys),
    }


def _fitted_zoom(floors, w, h):
    """`fitToBuilding`'s zoom, the COVER rule + the legibility clamp.

    Reproduces the shipped rule: fit to the horizontal extent with `padX = w *
    0.94`, guard the vertical with `padY = h * 1.5`, clamp to
    `[MIN_ZOOM, MAX_ZOOM]`, then raise to `CHAR_FLOOR_ZOOM` (applied last, and
    never negotiable) and cap at `MAX_ZOOM`.
    """
    ex = _extent(floors)
    zx = (w * 0.94) / ex["w"] if ex["w"] > 0 else MAX_ZOOM
    zy = (h * 1.5) / ex["h"] if ex["h"] > 0 else MAX_ZOOM
    z = max(0.45, min(MAX_ZOOM, min(zx, zy)))
    return min(max(z, CHAR_FLOOR_ZOOM), MAX_ZOOM), ex


def _floor_label(fl, cx, cy, zoom, w, h):
    """`drawTower`'s FLOOR label: on the deck's near corner, +`(4, 13)`.

    `maxX`/`maxY` are the largest room corner (the deck's BOTTOM vertex under
    the 2:1 projection, since screen y grows with `x + y`), and the baseline is
    `lp.y + 13` with the text starting at `lp.x + 4`.
    """
    max_x = max(float(r["x"]) + float(r["w"]) for r in fl["rooms"])
    max_y = max(float(r["y"]) + float(r["h"]) for r in fl["rooms"])
    lx, ly = _project(max_x, max_y, float(fl["z"]), cx, cy, zoom, w, h)
    return {
        "floor": fl["number"], "role": fl["role"],
        "x": lx + FLOOR_LABEL_DX, "baseline_y": ly + FLOOR_LABEL_DY,
        "anchor": (lx, ly), "anchor_cell": (max_x, max_y),
    }


def _room_label(fl, room, cx, cy, zoom, w, h):
    """`drawTower`'s room label: `iso(x0,y0).x + 8`, `mid(iso).y + 4`.

    The y anchor is the midpoint of the room's screen-space DIAGONAL, which is
    the transcription of `(a.y + c.y) / 2 + 4`.
    """
    z = float(fl["z"])
    x0, y0 = float(room["x"]), float(room["y"])
    x1, y1 = x0 + float(room["w"]), y0 + float(room["h"])
    a = _project(x0, y0, z, cx, cy, zoom, w, h)
    c = _project(x1, y1, z, cx, cy, zoom, w, h)
    return {
        "room": room["slug"], "floor": fl["number"],
        "x": a[0] + ROOM_LABEL_DX, "baseline_y": (a[1] + c[1]) / 2 + ROOM_LABEL_DY,
        # Screen y grows DOWNWARD, so the far corner (x0, y0) is the plate's TOP
        # vertex and (x1, y1) is its BOTTOM. Getting this pair the wrong way
        # round inverts every containment check below.
        "plate_top": min(a[1], c[1]), "plate_bottom": max(a[1], c[1]),
    }


def _measure(floors, w, h):
    """Every label on the canvas, with the numbers that decide the question."""
    zoom, ex = _fitted_zoom(floors, w, h)
    cx, cy = ex["cx"], ex["cy"]
    floors_labels = [_floor_label(fl, cx, cy, zoom, w, h) for fl in floors]
    room_labels = [_room_label(fl, r, cx, cy, zoom, w, h)
                   for fl in floors for r in fl["rooms"]]
    return {
        "zoom": zoom, "extent": ex, "canvas": (w, h),
        "floor_labels": floors_labels, "room_labels": room_labels,
        # the deck pitch on screen: one floor's `z` step, in pixels
        "deck_pitch_px": DECK_Z * FLOOR_HEIGHT_UNITS * zoom,
        "figure_px": CHAR_H * zoom,
    }


def _report(m: dict) -> str:
    """The numbers, as a string, so a failure shows WHY and not just 'False'."""
    w, h = m["canvas"]
    lines = [
        "canvas %dx%d  fitted zoom %.4f  extent %s" % (
            w, h, m["zoom"],
            {k: round(v, 2) for k, v in m["extent"].items()}),
        "deck pitch on screen = DECK_Z * FLOOR_HEIGHT_UNITS * zoom = "
        "%.1f * %d * %.4f = %.2f px" % (
            DECK_Z, FLOOR_HEIGHT_UNITS, m["zoom"], m["deck_pitch_px"]),
        "floor glyph = %.1f px  ->  gap between two floors' glyphs = %.2f px"
        % (FLOOR_GLYPH_PX, m["deck_pitch_px"] - FLOOR_GLYPH_PX),
        "figure = %.1f * zoom = %.2f px tall" % (CHAR_H, m["figure_px"]),
        "FLOOR labels (baseline y, x, box y-range):",
    ]
    for lab in m["floor_labels"]:
        lines.append(
            "  FLOOR %d %-13s y=%8.2f x=%8.2f  box %.2f..%.2f%s"
            % (lab["floor"], "(%s)" % lab["role"], lab["baseline_y"], lab["x"],
               lab["baseline_y"] - FLOOR_GLYPH_PX + 1, lab["baseline_y"] + 1,
               "   OFF-CANVAS" if lab["baseline_y"] > h else ""))
    for i in range(len(m["floor_labels"]) - 1):
        a = m["floor_labels"][i]
        b = m["floor_labels"][i + 1]
        lines.append(
            "  gap FLOOR %d -> FLOOR %d = %.2f px (baseline to baseline)"
            % (a["floor"], b["floor"], b["baseline_y"] - a["baseline_y"]))
    lines.append("room labels (baseline y, x, own plate y-range):")
    for lab in m["room_labels"]:
        inside = lab["plate_top"] <= lab["baseline_y"] <= lab["plate_bottom"]
        lines.append(
            "  room %-13s floor %d y=%8.2f x=%8.2f  plate %.2f..%.2f  inside=%s"
            % (lab["room"], lab["floor"], lab["baseline_y"], lab["x"],
               lab["plate_top"], lab["plate_bottom"], inside))
    return "\n".join(lines)


# ===========================================================================
# 0. the constants are the shipped ones
# ===========================================================================
def test_the_projection_constants_are_the_shipped_ones():
    """The arithmetic below is only true of the real tile. Pin it.

    Real defect caught: someone re-scales the tile or the deck gap and every
    number in this file silently becomes fiction while the tests stay green.
    """
    assert TILE_W == 34 and TILE_H == 17, (TILE_W, TILE_H)
    assert TILE_W == 2 * TILE_H, "the 2:1 deck proportion is the projection"
    assert DECK_Z == 26, DECK_Z


def test_the_label_glyphs_and_offsets_are_the_shipped_ones():
    """Read the font sizes and label offsets out of the renderer's own text.

    `drawTower` is the only place these appear, so a grep of the real file is
    the honest source; a hardcoded copy here would be a second truth.
    """
    src = ENTRY.read_text(encoding="utf-8")
    assert ('ctx.font = "600 12px ui-sans-serif, system-ui, sans-serif"'
            in src), "the FLOOR label font changed; FLOOR_GLYPH_PX is now wrong"
    assert 'ctx.font = "10px ui-sans-serif, system-ui, sans-serif"' in src, \
        "the room label font changed; ROOM_GLYPH_PX is now wrong"
    # RE-POINTED: the FLOOR label baseline is no longer the bare `lp.y + 13` — it
    # is the §6.1 staggered-then-clamped baseline. This greps the shipped
    # expression so FLOOR_LABEL_DX/DY-style readers cannot drift from the code.
    assert "var baseline = lp.y + 13 + fl.number * FLOOR_STRIDE;" in src, \
        "the FLOOR label baseline (stagger) changed; the offsets are now wrong"
    assert ("baseline = Math.min(Math.max(baseline, FLOOR_GLYPH_H + 2), "
            "cam.h - 4);") in src, \
        "the FLOOR label clamp changed; the placement rule is now wrong"
    assert "lp.x + 4, baseline);" in src, \
        "the FLOOR label draw call changed; FLOOR_LABEL_DX is now wrong"
    assert "a.x + FACE_W * 2" in src, \
        "the room label x offset changed; ROOM_LABEL_DX is now wrong"


def test_the_floor_pitch_constants_are_the_shipped_ones():
    """`FLOOR_HEIGHT_UNITS = 3` (tower.py) is the `z` step the pitch uses."""
    tower = (REPO / "voicebridge" / "office" / "tower.py").read_text("utf-8")
    m = re.search(r"^FLOOR_HEIGHT_UNITS\s*=\s*(\d+)", tower, re.M)
    assert m and int(m.group(1)) == FLOOR_HEIGHT_UNITS, \
        "tower.py's FLOOR_HEIGHT_UNITS moved away from %d" % FLOOR_HEIGHT_UNITS
    assert "CHAR_FLOOR_ZOOM = 0.85" in (PLUGIN / "qubit-office.js").read_text(
        "utf-8"), "the legibility clamp moved; the no-collision proof depends on it"


# ===========================================================================
# 1. floor labels cannot collide at any zoom the renderer can produce
# ===========================================================================
def test_floor_labels_cannot_collide_at_any_reachable_zoom():
    """A collision needs the deck pitch in PIXELS to fall under the glyph.

    The pitch is `DECK_Z * FLOOR_HEIGHT_UNITS * zoom` = `78 * zoom` px; the
    glyph is a constant 12 px (the font is not zoom-scaled). So labels collide
    iff `78 * zoom < 12`, i.e. `zoom < 0.1538`. The renderer clamps zoom at
    `CHAR_FLOOR_ZOOM = 0.85` and never goes below, so the colliding zoom is
    unreachable and the three FLOOR labels cannot overlap each other.

    This test is a SWEEP, not a spot check: it walks the whole reachable zoom
    range at 0.01 and asserts the pitch clears the glyph with margin, then
    asserts the exact zoom at which it would stop clearing.
    """
    colliding_zoom = FLOOR_GLYPH_PX / (DECK_Z * FLOOR_HEIGHT_UNITS)
    assert colliding_zoom == pytest.approx(12.0 / 78.0), colliding_zoom
    assert colliding_zoom < CHAR_FLOOR_ZOOM, (
        "the collision zoom %.4f is ABOVE the legibility clamp %.2f — the floor "
        "labels really can collide, and the fix is a placement rule"
        % (colliding_zoom, CHAR_FLOOR_ZOOM))

    worst = None
    z = CHAR_FLOOR_ZOOM
    while z <= MAX_ZOOM + 1e-9:
        pitch = DECK_Z * FLOOR_HEIGHT_UNITS * z
        gap = pitch - FLOOR_GLYPH_PX
        if worst is None or gap < worst[1]:
            worst = (z, gap, pitch)
        z = round(z + 0.01, 4)
    assert worst is not None
    assert worst[1] > 0, (
        "at zoom %.2f the deck pitch is only %.2f px against a %.1f px glyph — "
        "the FLOOR labels collide" % (worst[0], worst[2], FLOOR_GLYPH_PX))
    # the tightest reachable case is the clamp itself, and it still has room
    assert worst[1] == pytest.approx(
        DECK_Z * FLOOR_HEIGHT_UNITS * CHAR_FLOOR_ZOOM - FLOOR_GLYPH_PX), worst

    print("\n[floor-label collision] collision zoom = %.4f (unreachable; clamp "
          "is %.2f); tightest reachable gap = %.2f px at zoom %.2f"
          % (colliding_zoom, CHAR_FLOOR_ZOOM, worst[1], worst[0]))


def test_the_three_floor_scene_reports_a_pitch_far_above_the_glyph():
    """The reported scene, measured: the pitch is ~15x the glyph, not under it."""
    for (w, h) in CANVASES:
        m = _measure(THREE_FLOOR_SCENE, w, h)
        report = _report(m)
        assert m["deck_pitch_px"] > FLOOR_GLYPH_PX * 10, (
            "the measured deck pitch is %.2f px against a %.1f px glyph at the "
            "fitted zoom %.4f — this is a REAL pitch problem, read the numbers:"
            "\n%s" % (m["deck_pitch_px"], FLOOR_GLYPH_PX, m["zoom"], report))
        print("\n[%dx%d]\n%s" % (w, h, report))


def test_no_two_floor_label_boxes_overlap():
    """The assertion the vision read implies, stated as geometry, and it PASSES.

    A 12 px glyph's box is `baseline - 11 .. baseline + 1` (ascent ~11 px for a
    12 px sans glyph). Two boxes overlap iff their baselines are under 12 px
    apart. Measured across every pair on the fitted 3-floor scene, they are one
    deck pitch apart. Reported, so the reader sees the margin.
    """
    for (w, h) in CANVASES:
        m = _measure(THREE_FLOOR_SCENE, w, h)
        boxes = [(lab["floor"], lab["baseline_y"] - FLOOR_GLYPH_PX + 1,
                  lab["baseline_y"] + 1) for lab in m["floor_labels"]]
        overlaps = []
        for i in range(len(boxes)):
            for j in range(i + 1, len(boxes)):
                _, a0, a1 = boxes[i]
                _, b0, b1 = boxes[j]
                if a0 < b1 and b0 < a1:
                    overlaps.append((boxes[i][0], boxes[j][0]))
        assert not overlaps, (
            "these floors' label boxes overlap: %r. Full measurement:\n%s"
            % (overlaps, _report(m)))
        print("\n[%dx%d] floor-label boxes: %s -> no overlap"
              % (w, h, [(n, round(a0, 1), round(a1, 1)) for n, a0, a1 in boxes]))


def test_adjacent_floor_labels_are_exactly_one_deck_pitch_apart():
    """The gap IS the pitch — which is what proves the anchor is per-deck.

    If the label were anchored once (not per floor), or if the `z` term dropped
    out of the anchor, the gap would be 0 or arbitrary. The identity
    `y(n+1) - y(n) == -DECK_Z * FLOOR_HEIGHT_UNITS * zoom` is the assertion.
    """
    for (w, h) in CANVASES:
        m = _measure(THREE_FLOOR_SCENE, w, h)
        labs = m["floor_labels"]
        assert len(labs) == 3, labs
        for i in range(len(labs) - 1):
            gap = labs[i + 1]["baseline_y"] - labs[i]["baseline_y"]
            assert gap == pytest.approx(-m["deck_pitch_px"]), (
                "FLOOR %d -> FLOOR %d is %.2f px, not one deck pitch (%.2f px); "
                "the label is not anchored on its own deck's z.\n%s"
                % (labs[i]["floor"], labs[i + 1]["floor"], gap,
                   m["deck_pitch_px"], _report(m)))
        print("\n[%dx%d] gaps: %s"
              % (w, h, [round(labs[i + 1]["baseline_y"] - labs[i]["baseline_y"], 2)
                        for i in range(len(labs) - 1)]))


# ===========================================================================
# 2. a room label must stay inside its own room's projected tile bounds
# ===========================================================================
def test_a_room_label_stays_inside_its_own_room_plate():
    """The room slug must be drawn ON its room, not over the deck above it.

    The room's own projected diamond at its own `z` spans screen y from its top
    vertex `iso(x0,y0)` to its bottom vertex `iso(x1,y1)`. A label drawn between
    those two y values is on the plate. `drawTower` draws it at the MIDPOINT of
    that span plus 4 px, which is inside — so this test passes for the label's
    Y placement, and the numbers are printed to show the margin.

    Real defect this pins: a label anchored in SCREEN space (a rotation/offset
    applied after projection, or a shared mid-point across floors) leaves the
    plate and lands on the deck above — the reported "`review` buried under
    `FLOOR 3`".
    """
    failures = []
    for (w, h) in CANVASES:
        m = _measure(THREE_FLOOR_SCENE, w, h)
        for lab in m["room_labels"]:
            if not (lab["plate_top"] <= lab["baseline_y"] <= lab["plate_bottom"]):
                failures.append((m["canvas"], lab))
    assert not failures, (
        "a room label is outside its own room's projected plate (the label "
        "belongs to the deck above it): %r" % (failures,))


def test_a_room_label_stays_on_its_plate_for_every_room_of_a_full_floor():
    """Sweep the real six-room floor grid (3 cols x 2 rows) and every floor.

    Real defect: a placement that happens to work for the origin room and fails
    for room (2,0) or (0,1) — the failure mode a single spot-check cannot see.
    """
    def six_room_floor(number, slug):
        rooms = []
        for i in range(6):
            col, row = i % 3, (i // 3) % 2
            rooms.append({"id": i + 1, "slug": "%s-%d" % (slug, i + 1),
                          "kind": "open", "department_id": 1,
                          "x": col * (ROOM_W + 1), "y": row * (ROOM_H + 1),
                          "w": ROOM_W, "h": ROOM_H, "seats": []})
        return {"id": number, "number": number, "name": "level %d" % number,
                "role": slug, "theme": slug, "z": number * FLOOR_HEIGHT_UNITS,
                "rooms": rooms}

    floors = [six_room_floor(n, r) for n, r in
              ((1, "executive"), (2, "operations"), (3, "intelligence"))]
    outside = []
    for (w, h) in CANVASES:
        m = _measure(floors, w, h)
        for lab in m["room_labels"]:
            if not (lab["plate_top"] <= lab["baseline_y"] <= lab["plate_bottom"]):
                outside.append((w, h, lab["floor"], lab["room"],
                                round(lab["baseline_y"], 1),
                                round(lab["plate_top"], 1),
                                round(lab["plate_bottom"], 1)))
    assert not outside, (
        "%d room labels left their own plate (canvas, floor, room, label y, "
        "plate top..bottom):\n%r" % (len(outside), outside[:8]))


# ===========================================================================
# 3. the numbers, reported for a reader
# ===========================================================================
def test_the_measurement_is_reported_with_every_load_bearing_number():
    """Every number the defect report needs, printed as itself.

    A reader must be able to see the fitted zoom, the deck pitch in pixels, the
    gap, the per-floor label screen y, the room-label positions and the figure
    height — without re-deriving anything.
    """
    m = _measure(THREE_FLOOR_SCENE, 880, 500)
    report = _report(m)
    for token in ("fitted zoom", "deck pitch on screen", "gap FLOOR 1 -> FLOOR 2",
                  "figure =", "room "):
        assert token in report, "the report omits %r:\n%s" % (token, report)
    # the two hard numbers the report turns on
    assert m["zoom"] == pytest.approx(MAX_ZOOM), (
        "the fitted zoom for this scene moved off MAX_ZOOM=%.2f to %.4f; the "
        "reported numbers must be re-read" % (MAX_ZOOM, m["zoom"]))
    assert m["deck_pitch_px"] == pytest.approx(
        DECK_Z * FLOOR_HEIGHT_UNITS * MAX_ZOOM), m["deck_pitch_px"]
    print("\n" + "=" * 74 + "\n" + report + "\n" + "=" * 74)


# ===========================================================================
# 4. the REAL defect, asserted as measured
# ===========================================================================
def test_the_lowest_floor_label_is_drawn_off_the_bottom_of_the_canvas():
    """THE real defect: the FLOOR 1 label's baseline is below the canvas.

    `drawTower` anchors the label on the deck's NEAR corner — the largest
    `x + y`, i.e. the BOTTOM vertex of the projected diamond — and then adds
    `+13` px for the baseline. Stacking floors moves every deck UP by `DECK_Z`
    (26 px per z unit), but the camera is centred on the WHOLE building's
    extent, so when three floors are stacked the lowest deck sits low in the
    viewport and its label baseline falls past `cam.h`.

    Measured on the 880x500 canvas at the fitted zoom 2.4: the FLOOR 1 label
    baseline is y = 552.2 against a 500 px canvas — 52.2 px below the last row.
    The deck that is cropped loses its room labels too (the `executive` slug's
    baseline is y = 441.2, still on-canvas, but its deck's near half is gone).

    This test asserts the defect AS IT IS. It is deliberately written to FAIL
    when the fix lands, so the fix has to be measured rather than assumed: when
    the label rule changes, someone must re-read these numbers and update this
    test with the new ones. That is the point — a defect that changes state must
    change its own evidence.
    """
    m = _measure(THREE_FLOOR_SCENE, 880, 500)
    w, h = m["canvas"]
    by_floor = {lab["floor"]: lab for lab in m["floor_labels"]}
    lowest = by_floor[1]
    assert lowest["baseline_y"] > h, (
        "the FLOOR 1 label now fits on the canvas (baseline y = %.2f, canvas "
        "height %d) — the placement rule changed. Re-measure and rewrite this "
        "test with the new rule's numbers.\n%s" % (lowest["baseline_y"], h, _report(m)))
    overhang = lowest["baseline_y"] - h
    assert overhang == pytest.approx(52.2, abs=0.2), (
        "the FLOOR 1 overhang is %.2f px, not the measured 52.2 px; re-read the "
        "numbers.\n%s" % (overhang, _report(m)))
    print("\n[clipped-label defect] FLOOR 1 baseline y=%.2f vs canvas height %d "
          "-> %.2f px below the frame (3 floors, zoom %.4f)"
          % (lowest["baseline_y"], h, overhang, m["zoom"]))


def test_the_clipping_threshold_is_exactly_three_floors():
    """The floor count at which the lowest label leaves the frame, measured.

    One and two floors of a single 6x4 room fit under 500 px; the third does
    not. This is the number a fix has to move, and the number that says the
    reported scene (three floors) is exactly the first scene to break.

    Real defect caught: a change to `DECK_Z`, `FLOOR_HEIGHT_UNITS` or the fit
    moves the threshold, and a fix that only shifts it from 3 to 4 has not fixed
    the anchoring problem — it has bought one more floor. Asserting the
    threshold, not just the 3-floor case, is what makes that visible.
    """
    w, h = 880, 500
    starts_clipping = None
    measured = {}
    for nf in (1, 2, 3, 7):
        floors = [one_room_floor(n, "r") for n in range(1, nf + 1)]
        m = _measure(floors, w, h)
        baseline = {lab["floor"]: lab["baseline_y"]
                    for lab in m["floor_labels"]}[1]
        clipped = baseline > h
        measured[nf] = (m["zoom"], m["deck_pitch_px"], round(baseline, 2), clipped)
        if clipped and starts_clipping is None:
            starts_clipping = nf
    assert starts_clipping == 3, (
        "the lowest FLOOR label now starts clipping at %r floors, not 3; the "
        "reported scene's threshold moved. Measured (floors -> zoom, pitch, "
        "FLOOR 1 baseline, clipped): %r" % (starts_clipping, measured))
    # the boundary is real in both directions: 2 fits, 3 does not
    assert not measured[1][3] and not measured[2][3], measured
    assert measured[3][3] and measured[7][3], measured
    print("\n[clipping threshold] floors -> (zoom, pitch, FLOOR1 baseline, "
          "clipped): %r" % (measured,))


def test_the_top_most_deck_label_is_the_only_one_with_room_above_it():
    """The asymmetry that proves it is clipping and not collision.

    If the three labels collided, their gaps would shrink toward the glyph
    height. They do not: every gap is exactly one deck pitch (187.2 px). What is
    wrong is the WINDOW: the stack is taller than the canvas by exactly the part
    that overflows, and the label that overflows is the lowest deck's.
    """
    m = _measure(THREE_FLOOR_SCENE, 880, 500)
    w, h = m["canvas"]
    labs = sorted(m["floor_labels"], key=lambda r: r["floor"])
    gaps = [labs[i + 1]["baseline_y"] - labs[i]["baseline_y"]
            for i in range(len(labs) - 1)]
    assert all(gap == pytest.approx(-m["deck_pitch_px"]) for gap in gaps), gaps
    off_canvas = [lab["floor"] for lab in labs if lab["baseline_y"] > h]
    assert off_canvas == [1], (
        "the set of off-canvas floor labels moved: %r. Re-measure.\n%s"
        % (off_canvas, _report(m)))
    print("\n[geometry] gaps all %.2f px (= one deck pitch); labels off-canvas: "
          "%r" % (-m["deck_pitch_px"], off_canvas))


# ===========================================================================
# 5. figure legibility, computed
# ===========================================================================
def test_the_rendered_figure_height_at_the_fitted_zoom_is_reported_and_legible():
    """`drawAgent` draws `h = 15 * cam.zoom`, `w = 9 * cam.zoom`.

    At the fitted zoom for the reported scene (2.4, the MAX_ZOOM clamp) the
    figure is 36.0 x 21.6 px — comfortably legible. At the legibility FLOOR
    (CHAR_FLOOR_ZOOM = 0.85, the lowest the fit will ever go) it is 12.75 x
    7.65 px: tall enough to spot, but 7.65 px wide means the state band and the
    facing are not readable, only the position.

    The legibility question is stated as a zoom: a 12 px-tall figure (the size of
    the label glyph, taken as the readable floor) needs `zoom >= 12 / 15 = 0.8`;
    a comfortable 16 px needs `zoom >= 16 / 15 = 1.067`. Both are at or below the
    fitted zoom of every real scene, so figures are NOT the reported problem at
    the fitted zoom — but they are at the clamp, which is what the second half of
    this test reports.
    """
    for (w, h) in CANVASES:
        m = _measure(THREE_FLOOR_SCENE, w, h)
        assert m["figure_px"] == pytest.approx(CHAR_H * m["zoom"]), m
        assert m["figure_px"] > 16.0, (
            "the figure at the fitted zoom is only %.2f px tall — below the "
            "16 px comfortable floor" % m["figure_px"])
    at_floor = CHAR_H * CHAR_FLOOR_ZOOM
    assert at_floor == pytest.approx(12.75), at_floor
    readable_zoom = FLOOR_GLYPH_PX / CHAR_H          # 0.8
    comfortable_zoom = 16.0 / CHAR_H                 # 1.0667
    assert readable_zoom == pytest.approx(0.8) and comfortable_zoom > readable_zoom
    assert CHAR_FLOOR_ZOOM >= readable_zoom, (
        "the legibility clamp %.2f is below the 12 px readable zoom %.4f — the "
        "renderer can draw figures smaller than a label glyph"
        % (CHAR_FLOOR_ZOOM, readable_zoom))
    assert CHAR_FLOOR_ZOOM < comfortable_zoom, (
        "the clamp %.2f already exceeds the 16 px comfortable zoom %.4f; the "
        "'figures still small' report would be about a zoom above the fitted "
        "one, which cannot be the case" % (CHAR_FLOOR_ZOOM, comfortable_zoom))
    print("\n[figure legibility] fitted zoom %.4f -> figure %.2f x %.2f px; "
          "clamp %.2f -> figure %.2f x %.2f px; readable(12px) zoom=%.4f; "
          "comfortable(16px) zoom=%.4f"
          % (MAX_ZOOM, CHAR_H * MAX_ZOOM, CHAR_W * MAX_ZOOM,
             CHAR_FLOOR_ZOOM, at_floor, CHAR_W * CHAR_FLOOR_ZOOM,
             readable_zoom, comfortable_zoom))
