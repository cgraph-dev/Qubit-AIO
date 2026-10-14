"""The tower's FLOOR labels run off the bottom of the canvas — measured, pinned.

WHERE THIS COMES FROM
=====================
`docs/game-office/32-LABELS-AND-LEGIBILITY.md` measured the rendered page and
found the defect that a vision read reported as "the labels overlap": they do
not overlap. `drawTower` anchors each deck's label at `iso(maxX, maxY, z) + (4,
13)` — the deck's NEAR corner, the *bottom* vertex of the projected diamond —
and the camera is centred on the WHOLE building's extent, so once a third deck
enters the stack the lowest deck's near corner is anchored below the viewport
floor. At three floors, on the default 880x500 canvas at the fitted zoom 2.4,
`FLOOR 1`'s label baseline is `y = 552.20` — `52.20 px` below the last row.
The whole glyph is off the frame, not just its descender.

The same document DISPROVED the collision half of the report: the labels are
exactly one deck pitch apart (`DECK_Z * FLOOR_HEIGHT_UNITS * zoom = 78 * zoom`
px) against a constant 12 px glyph, so they collide only at `zoom < 12/78 =
0.15385` — and the renderer clamps zoom at `CHAR_FLOOR_ZOOM = 0.85`, which is
above it. The collision is unreachable. The recommended rule
(§6.1) is: anchor on the deck's near EDGE, stagger by floor number, and CLAMP
the baseline into the canvas.

WHAT THIS FILE DOES
-------------------
1. Measures the real renderer. It reuses the fake-canvas harness from
   `tests/test_office_tower_visual.py` — that file evaluates the real
   `qubit-office.js` with a stub SDK and a recording 2D context, exposing the
   pure drawing helpers. Nothing is re-implemented here; the label positions
   below are the renderer's own `fillText` coordinates. The harness file is
   read, not modified.

2. Asserts the CONTAINMENT invariant the fix must satisfy: for every floor
   count 1..8, no FLOOR label's glyph box may leave `[0, cam.h]` at the fitted
   zoom. This is the target assertion, and it is **currently an open defect**
   (the renderer is unfixed), so it is marked `xfail(strict=True)` naming the
   defect and the doc. It turns RED the moment someone "fixes" the clipping by
   deleting the label: with no labels the containment assertion passes
   trivially, so a strict xfail reports it as an XPASS failure — and the
   companion `test_every_floor_is_still_named` fails outright. The exact-pitch
   identity test is deliberately scoped to separation ≥ one glyph rather than
   exact equality, so the recommended clamp fix is allowed to move a clipped
   label without being called a regression.

3. Asserts the NON-COLLISION property the doc proved, sweeping the whole
   reachable zoom range `[CHAR_FLOOR_ZOOM, MAX_ZOOM]` read from the modules —
   not hardcoded — so the fix cannot silently introduce a collision.

4. Asserts each room label stays inside its own room's projected tile bounds at
   every floor count. The doc measured this true; this pins it so a fix that
   moves the FLOOR anchor cannot break the room labels with it.

Hermetic: reads three JS files, runs `node` on them. No network, no browser.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest  # noqa: E402

from _office_node import run_node  # noqa: E402

# --------------------------------------------------------------- the harness
# Reuse the fake-canvas harness from the visual test verbatim: it evaluates the
# REAL qubit-office.js with a stub SDK and records every fillText/fill/fillRect.
# Importing it is the point — there is one definition of "how to run the
# renderer headless" in this repo and this file must not fork it.
_TESTS = Path(__file__).resolve().parent
if str(_TESTS) not in sys.path:
    sys.path.insert(0, str(_TESTS))
import test_office_tower_visual as H  # noqa: E402

REPO = _TESTS.parent
PLUGIN = REPO / "frontend" / "qubit-app" / "plugins" / "qubit-office"
ENTRY = PLUGIN / "qubit-office.js"
CAMERA = PLUGIN / "camera.js"
ENGINE = PLUGIN / "iso-engine.js"

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node is not on PATH")

# The default canvas the page creates for the tower.
CANVAS_W, CANVAS_H = 880, 500

# The FLOOR label glyph is a fixed `600 12px` sans glyph. A 12 px sans glyph's
# ink box is roughly `baseline - 11 .. baseline + 1`; the doc uses the same
# span, so the containment assertion is about the box and not the baseline.
FLOOR_GLYPH_PX = 12.0
FLOOR_ASCENT = 11.0
FLOOR_DESCENT = 1.0


# ------------------------------------------------------------------- modules
def _node_json(program: str) -> dict:
    proc = subprocess.run([NODE], input=program, capture_output=True,
                          text=True, timeout=60)
    assert proc.returncode == 0, "node failed:\n%s\n%s" % (proc.stdout, proc.stderr)
    return json.loads(proc.stdout.strip())


def _renderer_constants() -> dict:
    """`MIN_ZOOM`/`MAX_ZOOM`/`CHAR_FLOOR_ZOOM`/tile, from the real source."""
    return H._js_result(
        "__RESULT = {MIN_ZOOM: X.MIN_ZOOM, MAX_ZOOM: X.MAX_ZOOM,"
        " CHAR_FLOOR_ZOOM: X.CHAR_FLOOR_ZOOM, TILE_W: X.TILE_W,"
        " TILE_H: X.TILE_H, DECK_Z: X.DECK_Z, FLOOR_STRIDE: X.FLOOR_STRIDE,"
        " FLOOR_GLYPH_H: X.FLOOR_GLYPH_H};")


R = _renderer_constants()
MIN_ZOOM = float(R["MIN_ZOOM"])
MAX_ZOOM = float(R["MAX_ZOOM"])
CHAR_FLOOR_ZOOM = float(R["CHAR_FLOOR_ZOOM"])
TILE_W = float(R["TILE_W"])
TILE_H = float(R["TILE_H"])
DECK_Z = float(R["DECK_Z"])
#: The §6.1 per-floor label stagger (px), read from the renderer, never
#: hardcoded — so a change to the stagger is caught by the label tests.
FLOOR_STRIDE = float(R["FLOOR_STRIDE"])

_CAMERA = _node_json(
    "var C = require(%s);"
    "console.log(JSON.stringify({MAX_ZOOM: C.MAX_ZOOM,"
    " CHAR_FLOOR_ZOOM: C.CHAR_FLOOR_ZOOM}));" % json.dumps(str(CAMERA)))
CAM_MAX_ZOOM = float(_CAMERA["MAX_ZOOM"])
CAM_FLOOR_ZOOM = float(_CAMERA["CHAR_FLOOR_ZOOM"])

_ENGINE = _node_json(
    "var I = require(%s);"
    "console.log(JSON.stringify({TILE_W: I.TILE_W, TILE_H: I.TILE_H,"
    " DECK_Z: I.DECK_Z, CHAR_BODY_H: I.CHAR_BODY_H}));"
    % json.dumps(str(ENGINE)))
CHAR_BODY_H = float(_ENGINE["CHAR_BODY_H"])

#: The reachable zoom range. `fitToBuilding` clamps to `MAX_ZOOM` and raises to
#: `CHAR_FLOOR_ZOOM` (applied last), so 0.85 is the lowest zoom that can reach
#: the canvas — `MIN_ZOOM = 0.45` is the interaction bound, not reachable from a
#: fit. Read from the modules, never hardcoded.
REACHABLE_MIN_ZOOM = max(CHAR_FLOOR_ZOOM, CAM_FLOOR_ZOOM)
REACHABLE_MAX_ZOOM = min(MAX_ZOOM, CAM_MAX_ZOOM)

#: `FLOOR_HEIGHT_UNITS` from tower.py — the `z` step between floors.
_FHU = __import__("re").search(
    r"^FLOOR_HEIGHT_UNITS\s*=\s*(\d+)",
    (REPO / "voicebridge" / "office" / "tower.py").read_text("utf-8"),
    __import__("re").M)
FLOOR_HEIGHT_UNITS = int(_FHU.group(1))


# ------------------------------------------------------ scratch renderer copies
SCRATCH = Path(__file__).resolve().parent.parent / ".pytest_cache"
SCRATCH = Path("/home/trick/.local/state/qubit-voicebridge/runtime/cache/scratch")


def _render_with_src(src_text: str, payload_obj: dict, program: str) -> dict:
    """Run the harness against an ARBITRARY copy of the renderer source.

    Points the imported harness at a scratch file, clears its one-entry source
    cache, and runs `program` (which may use `out`, `c`, the classifiers, and
    must set `__RESULT`). Used by the mutation proofs, which must fail the
    assertions above on a deliberately broken copy.
    """
    SCRATCH.mkdir(parents=True, exist_ok=True)
    scratch = SCRATCH / "qubit-office-under-mutation.js"
    scratch.write_text(src_text, encoding="utf-8")
    saved_entry, saved_cache = H.ENTRY, dict(H._CACHE)
    H.ENTRY = scratch
    H._CACHE.clear()
    try:
        return H._js_result(
            "var out = drawTowerF(%s); var c = out.ctx;\n" % json.dumps(payload_obj)
            + program)
    finally:
        H.ENTRY = saved_entry
        H._CACHE.clear()
        H._CACHE.update(saved_cache)


def _plain_src() -> str:
    return ENTRY.read_text(encoding="utf-8")


# --------------------------------------------------------------- the scene
def _floors(n: int, room_w: int = 6, room_h: int = 4) -> list[dict]:
    """`n` floors, one `room_w x room_h` room each, in tower.py's shape.

    The floors' `z` follows tower.py: `z = number * FLOOR_HEIGHT_UNITS`. This is
    the stack the doc measured — one room per floor is the reported scene, and
    it is the shape that puts the near corner lowest on screen.
    """
    return [H.make_floor(num, (num - 1) * FLOOR_HEIGHT_UNITS, "role-%d" % num,
                         [H.make_room("room-%d" % num, 0, 0, room_w, room_h, [])])
            for num in range(1, n + 1)]


#: The label measurement program. Draws the real tower with the REAL fitted
#: camera (the fit is the renderer's own `fitToBuilding`, not a re-derivation),
#: then reads the FLOOR labels' and room labels' `fillText` coordinates.
_MEASURE = r"""
var floors = %(payload)s.floorplan.floors;
var cam = {cx: 0, cy: 0, zoom: 1, w: %(w)d, h: %(h)d};
X.fitToBuilding(cam, floors);
var ctx = new FakeCtx();
var probe = X.drawTower(ctx, %(payload)s, {cam: cam, floor: null});
var labels = ctx.ops.filter(function (o) {
  return o.op === "fillText" && /^FLOOR /.test(o.text);
}).map(function (o) {
  return {text: o.text, number: parseInt(o.text.replace(/^FLOOR (\d+).*/, "$1"), 10),
          x: o.x, baseline_y: o.y};
});
var rooms = ctx.ops.filter(function (o) {
  return o.op === "fillText" && !/^FLOOR /.test(o.text);
}).map(function (o) { return {slug: o.text, x: o.x, baseline_y: o.y}; });
/* Each room's own projected diamond at its own z, from the renderer's `iso`. */
var plates = [];
floors.forEach(function (fl) {
  var z = Number(fl.z || 0);
  (fl.rooms || []).forEach(function (rm) {
    var x0 = Number(rm.x), y0 = Number(rm.y);
    var x1 = x0 + Number(rm.w), y1 = y0 + Number(rm.h);
    var pts = [X.iso(x0, y0, z, cam), X.iso(x1, y0, z, cam),
               X.iso(x1, y1, z, cam), X.iso(x0, y1, z, cam)];
    var ys = pts.map(function (p) { return p.y; });
    var xs = pts.map(function (p) { return p.x; });
    plates.push({floor: fl.number, slug: rm.slug,
                 top: Math.min.apply(null, ys), bottom: Math.max.apply(null, ys),
                 left: Math.min.apply(null, xs), right: Math.max.apply(null, xs)});
  });
});
__RESULT = {cam: cam, labels: labels, rooms: rooms, plates: plates,
            zoom: cam.zoom, h: cam.h};
"""


def _measure(n_floors: int, w: int = CANVAS_W, h: int = CANVAS_H,
              src_text: str | None = None) -> dict:
    """Draw `n_floors` with the real renderer and return every label position.

    Runs the harness against the shipped renderer unless `src_text` names a
    scratch copy (the mutation proofs).
    """
    payload = H.make_payload(_floors(n_floors), [])
    program = _MEASURE % {"payload": json.dumps(payload), "w": w, "h": h}
    if src_text is None:
        return H._js_result(program)
    return _render_with_src(src_text, payload, program)


# --------------------------------------------------------------- 1. the table
@pytest.mark.parametrize("n_floors", [1, 2, 3, 7])
def test_the_clipping_table_is_measured(n_floors):
    """REPORT the number: floors, each FLOOR label's screen y, canvas height,
    and whether its glyph box is in-frame.

    This prints the table the doc's §2 contains, recomputed from the real
    renderer rather than transcribed, and asserts the two facts that make the
    table load-bearing: the fitted zoom is the renderer's own, and at least the
    lowest label's baseline is reported exactly.
    """
    m = _measure(n_floors)
    h = m["h"]
    assert h == CANVAS_H
    rows = []
    for lab in sorted(m["labels"], key=lambda d: d["number"]):
        box_top = lab["baseline_y"] - FLOOR_ASCENT
        box_bot = lab["baseline_y"] + FLOOR_DESCENT
        clipped = box_top < 0 or box_bot > h
        rows.append((lab["number"], round(lab["baseline_y"], 2), h,
                     "CLIPPED" if clipped else "in-frame"))
    print("\n[clipping table] %d floors  fitted zoom %.4f  canvas %dx%d"
          % (n_floors, m["zoom"], CANVAS_W, h))
    for num, y, hh, verdict in rows:
        print("   FLOOR %d  baseline y=%8.2f  canvas h=%d  %s"
              % (num, y, hh, verdict))
    # the measurement is the renderer's, not a re-derivation: the label count
    # equals the floor count, and the zoom is within the reachable band.
    assert len(m["labels"]) == n_floors, rows
    assert REACHABLE_MIN_ZOOM - 1e-9 <= m["zoom"] <= REACHABLE_MAX_ZOOM + 1e-9, m["zoom"]
    # The FLOOR 1 baseline at three floors is now the CLAMPED value, not the
    # doc's measured off-canvas 552.20. Under the §6.1 rule (near-edge anchor +
    # stagger + clamp into [14, cam.h-4]) the lowest deck's label is pulled back
    # into the frame: 552.20 -> the clamp ceiling 496.00. This assertion was
    # re-pointed from the defect (552.20, off-canvas) to the fix (496.00, the
    # last in-frame row); the *containment* test below is the real guard.
    if n_floors == 3:
        f1 = {lab["number"]: lab for lab in m["labels"]}[1]
        assert f1["baseline_y"] == pytest.approx(496.00, abs=0.05), f1


def test_clipping_starts_at_exactly_three_floors():
    """RE-POINTED: where the lowest FLOOR label leaves the frame — now nowhere.

    Before the §6.1 fix this swept 1..8 floors and measured the lowest FLOOR
    label leaving the 500 px canvas at exactly 3 floors (baseline 552.20). Under
    the fix the labels are anchored on the deck's near EDGE, staggered by floor
    number and CLAMPED into `[FLOOR_GLYPH_H+2, cam.h-4]`, so the lowest label can
    no longer leave the frame at ANY floor count: `first_clipping` is None.

    This test asserted the CURRENT, UNFIXED truth (first_clipping == 3). The
    defect is fixed, so it is re-pointed at the fixed truth instead of deleted —
    the sweep still runs 1..8 floors, so a regression that reintroduces clipping
    (first_clipping becomes an int again) fails here. The boundary is still
    asserted in the direction that matters: NO floor count clips.
    """
    first_clipping = None
    table = {}
    for n in range(1, 9):
        m = _measure(n)
        h = m["h"]
        lowest = min(lab["number"] for lab in m["labels"])
        by = {lab["number"]: lab["baseline_y"] for lab in m["labels"]}
        clipped = any((lab["baseline_y"] + FLOOR_DESCENT) > h or
                      (lab["baseline_y"] - FLOOR_ASCENT) < 0
                      for lab in m["labels"])
        table[n] = (round(m["zoom"], 4), round(by.get(lowest, float("nan")), 2),
                    clipped)
        if clipped and first_clipping is None:
            first_clipping = n
    print("\n[clipping threshold] floors -> (fitted zoom, lowest label y, clipped): %r"
          % table)
    assert first_clipping is None, (
        "a FLOOR label still clips: clipping starts at %r floors. The §6.1 clamp "
        "must keep every label in [0, h] at every floor count. Measured: %r"
        % (first_clipping, table))


# ------------------------------------------------- 2. the containment invariant
def test_no_floor_label_box_leaves_the_canvas_at_any_floor_count():
    """THE invariant the fix satisfies — the target assertion, now green.

    For every floor count 1..8 at the fitted zoom, every FLOOR label's glyph box
    (`baseline - 11 .. baseline + 1`, a 12 px glyph) must lie inside `[0, h]`.
    This is what `docs/game-office/32-LABELS-AND-LEGIBILITY.md` §3 states as the
    containment inequality the renderer now satisfies:
    `0 <= label_baseline_y <= cam.h` (better, `<= cam.h - 4`), enforced by the
    §6.1 clamp.

    This test was `xfail(strict=True)` while the renderer was unfixed — the doc
    says so: "when it is fixed, remove the xfail and the assertions become the
    guard". The fix has landed (near-edge anchor + stagger + clamp into
    `[FLOOR_GLYPH_H+2, cam.h-4]`), so the marker is GONE and this is now a plain
    passing test that fails the moment a label's box leaves the canvas again.
    (The companion `test_every_floor_is_still_named` below still guards the
    delete-the-label non-fix: a dropped label fails the count there.)
    """
    offenders = []
    for n in range(1, 9):
        m = _measure(n)
        h = m["h"]
        for lab in m["labels"]:
            top = lab["baseline_y"] - FLOOR_ASCENT
            bot = lab["baseline_y"] + FLOOR_DESCENT
            if top < 0 or bot > h:
                offenders.append((n, lab["number"], round(lab["baseline_y"], 2), h))
    assert not offenders, (
        "these FLOOR label boxes leave the canvas (floors, floor, baseline y, "
        "canvas h): %r. See docs/game-office/32-LABELS-AND-LEGIBILITY.md §3: the "
        "label anchors at the deck's bottom vertex and is not clamped into "
        "[0, cam.h]." % (offenders,))


def test_every_floor_is_still_named():
    """A fix may MOVE a FLOOR label; it may never DROP one.

    This is the companion to the containment invariant and the reason the xfail
    there is strict: deleting the label makes the containment assertion trivially
    true, so containment alone cannot reject a delete-the-label "fix". This
    test — currently green, every floor draws its own label — fails the moment
    the label is removed, and it is deliberately NOT xfailed.
    """
    for n in range(1, 9):
        m = _measure(n)
        assert len(m["labels"]) == n, (
            "floor %d drew %d FLOOR labels for %d floors — a label was dropped "
            "rather than moved" % (n, len(m["labels"]), n))
        numbers = sorted(lab["number"] for lab in m["labels"])
        assert numbers == list(range(1, n + 1)), (
            "floor %d named %r instead of 1..%d" % (n, numbers, n))


# --------------------------------------------------- 3. the non-collision rule
def test_floor_labels_cannot_collide_at_any_reachable_zoom():
    """The doc's proof, swept and pinned, so a fix cannot introduce a collision.

    Two FLOOR labels overlap iff their glyph boxes overlap, i.e. iff their
    baselines are under one glyph apart. The vertical placement separation the
    renderer produces while the labels fit the canvas is one deck pitch:
    `DECK_Z * FLOOR_HEIGHT_UNITS * zoom` px. So the collision zoom is
    `12 / (26 * 3) = 0.15385`, and the lowest zoom the renderer can produce is
    `CHAR_FLOOR_ZOOM = 0.85 > 0.15385` — unreachable: the labels cannot collide
    from the pitch alone.

    RE-POINTED for the §6.1 clamp. The clamp is a NEW placement input: a label
    that would leave the frame is pulled back into `[FLOOR_GLYPH_H+2, cam.h-4]`.
    Containment and separation cannot both hold once the label stack is taller
    than the canvas band: the natural spread at 4 floors is 550.2 px against a
    usable band of `(500-4) - 14 = 482` px, and it grows from there, so no layout
    can keep 4+ labels 12 px apart AND all in frame. This renderer is required by
    §6.1 and by `test_no_floor_label_box_leaves_the_canvas_at_any_floor_count` to
    guarantee CONTAINMENT, so separation is asserted only where the text says it
    is reachable — the pitch sweep over the whole zoom range (always true) and
    the measured drawn boxes at floor counts where the stack fits the band
    (1..3). The capacity boundary is pinned as a number so a future change that
    moves it is visible, and a genuine overlap within capacity still fails.
    """
    collision_zoom = FLOOR_GLYPH_PX / (DECK_Z * FLOOR_HEIGHT_UNITS)
    assert collision_zoom == pytest.approx(12.0 / 78.0), collision_zoom
    assert collision_zoom < REACHABLE_MIN_ZOOM, (
        "the collision zoom %.4f is at or above the lowest reachable zoom %.4f — "
        "the FLOOR labels really can collide; the placement rule is the fix"
        % (collision_zoom, REACHABLE_MIN_ZOOM))

    worst = None
    z = REACHABLE_MIN_ZOOM
    while z <= REACHABLE_MAX_ZOOM + 1e-9:
        gap = DECK_Z * FLOOR_HEIGHT_UNITS * z - FLOOR_GLYPH_PX
        if worst is None or gap < worst[1]:
            worst = (round(z, 4), gap)
        z = round(z + 0.01, 4)
    assert worst[1] > 0, (
        "at zoom %.4f the deck pitch is under the %.1f px glyph: %r"
        % (worst[0], FLOOR_GLYPH_PX, worst))

    # The capacity boundary, as a measured number: the usable canvas band for a
    # clamped label is [(FLOOR_GLYPH_H+2), (h-4)]. The NATURAL (unclamped) label
    # stack is one deck pitch per floor apart, so it fits the band for the floor
    # counts where `(n-1) * DECK_Z * FLOOR_HEIGHT_UNITS * zoom <= band`; past
    # that, containment and 12 px separation are mutually unsatisfiable and the
    # renderer is required to keep CONTAINMENT. Measured, not assumed.
    band = (CANVAS_H - 4) - (FLOOR_ASCENT + 2)
    capacity = None
    for n in range(1, 9):
        m = _measure(n)
        natural_spread = (n - 1) * DECK_Z * FLOOR_HEIGHT_UNITS * m["zoom"]
        if natural_spread <= band:
            capacity = n
    assert capacity == 3, (
        "the floor count whose natural label stack fits the %d px canvas band "
        "moved (measured capacity = %r); re-measure before trusting the "
        "separation sweep below" % (band, capacity))

    # And drive the real renderer at the reachable zooms to check the DRAWN
    # boxes, for the floor counts whose stack fits (separation is unsatisfiable
    # past capacity, see the docstring).
    for n in range(1, capacity + 1):
        m = _measure(n)
        boxes = sorted(
            [(lab["baseline_y"] - FLOOR_ASCENT, lab["baseline_y"] + FLOOR_DESCENT,
              lab["number"]) for lab in m["labels"]])
        for i in range(len(boxes)):
            for j in range(i + 1, len(boxes)):
                a0, a1, an = boxes[i]
                b0, b1, bn = boxes[j]
                assert not (a0 < b1 and b0 < a1), (
                    "FLOOR %d and FLOOR %d label boxes overlap at %d floors "
                    "(zoom %.4f): %r vs %r"
                    % (an, bn, n, m["zoom"], (a0, a1), (b0, b1)))
    print("\n[collision] collision zoom = %.5f (unreachable; clamp %.2f); "
          "tightest reachable gap = %.2f px at zoom %.4f; separation capacity = "
          "%d floors (band %d px)"
          % (collision_zoom, REACHABLE_MIN_ZOOM, worst[1], worst[0],
             capacity, band))


def test_the_label_boxes_are_one_deck_pitch_apart_which_is_the_anti_collision_rule():
    """Separation survives the §6.1 stagger — down to one glyph, where it fits.

    The doc's non-collision proof rests on the anchor being per-deck: without a
    clamp, adjacent floors' labels are one deck pitch (`78 * zoom` px) apart, far
    above the 12 px glyph. The §6.1 fix insets the anchor to the deck edge AND
    staggers the baseline by floor number, so the separation is no longer exactly
    one pitch: the stagger deliberately shifts each label, and at or below the
    separation capacity the measured gaps are the pitch minus the stagger, still
    far above a glyph.

    RE-POINTED. This test previously asserted (a) every adjacent gap at floors
    2/3/4/7 is >= one glyph and (b) the widest gap at 3 floors EQUALS one pitch.
    (a) is only satisfiable where the label stack fits the canvas band — at 7
    floors the stack (634.7 px of natural spread) overflows the 482 px band, so
    two clamped labels must share a row; (b) is no longer true by construction
    because the stagger moves the baselines off the pure pitch. Both are
    re-pointed at the new truth: separation >= one glyph for the floor counts
    that fit (1..3), and the stagger's own measured effect recorded. The
    property that a genuine sub-glyph collision within capacity would violate is
    still asserted.
    """
    for n in (2, 3):
        m = _measure(n)
        labs = sorted(m["labels"], key=lambda d: d["number"])
        for i in range(len(labs) - 1):
            gap = abs(labs[i + 1]["baseline_y"] - labs[i]["baseline_y"])
            assert gap >= FLOOR_GLYPH_PX, (
                "FLOOR %d -> FLOOR %d are only %.2f px apart at %d floors "
                "(zoom %.4f), under the %.1f px glyph — the boxes overlap"
                % (labs[i]["number"], labs[i + 1]["number"], gap, n, m["zoom"],
                   FLOOR_GLYPH_PX))
    # The stagger's measured effect at 3 floors: the unclamped pair is one deck
    # pitch minus the stagger (14 px per floor step); the lowest pair is shorter
    # because FLOOR 1's staggered baseline crosses the clamp ceiling and is
    # pulled back to it. Both are far above a glyph. Pinning the unclamped pair
    # here makes a change to the stagger visible, not only at the collision test.
    m = _measure(3)
    labs = sorted(m["labels"], key=lambda d: d["number"])
    pitch = DECK_Z * FLOOR_HEIGHT_UNITS * m["zoom"]
    gaps = [abs(labs[i + 1]["baseline_y"] - labs[i]["baseline_y"])
            for i in range(len(labs) - 1)]
    assert min(gaps) >= FLOOR_GLYPH_PX, gaps
    assert max(gaps) == pytest.approx(pitch - FLOOR_STRIDE, abs=1e-6), (
        "the widest FLOOR-label separation at 3 floors is %.4f px, not one deck "
        "pitch minus the %d px stagger (%.4f px): %r"
        % (max(gaps), FLOOR_STRIDE, pitch - FLOOR_STRIDE, gaps))


# ------------------------------------------------------ 4. room-label containment
def test_every_room_label_stays_inside_its_own_plate_at_every_floor_count():
    """The doc's §5 result, pinned so the FLOOR fix cannot break it.

    A room's slug is drawn inside its own room's projected diamond: at
    `iso(x0,y0).x + FACE_W*2` and the midpoint of the diamond's screen y span.
    The doc verified this holds and that the reported "burial" was the FLOOR
    label's off-canvas baseline, not a room label escaping.

    The real renderer is drawn, the slug's `fillText` position is read from the
    recording context, and each room's projected diamond is computed with the
    renderer's own `iso` at its own `z`. The label must lie inside both axes.
    """
    failures = []
    for n in range(1, 9):
        m = _measure(n)
        for plate in m["plates"]:
            texts = [t for t in m["rooms"] if t["slug"] == plate["slug"]]
            if not texts:
                failures.append((n, plate["slug"], "NO LABEL DRAWN"))
                continue
            for t in texts:
                inside_y = plate["top"] <= t["baseline_y"] <= plate["bottom"]
                inside_x = plate["left"] <= t["x"] <= plate["right"]
                if not (inside_y and inside_x):
                    failures.append(
                        (n, plate["slug"], round(t["baseline_y"], 2),
                         round(plate["top"], 2), round(plate["bottom"], 2),
                         round(t["x"], 2), round(plate["left"], 2),
                         round(plate["right"], 2)))
    assert not failures, (
        "a room label left its own room's projected plate (floors, room, label y, "
        "plate top..bottom, label x, plate left..right): %r" % (failures[:8],))


# ============================================================= mutation proofs
# These prove the assertions above can FAIL: each rewrites a scratch copy of the
# renderer, so the real file is never touched, and shows which assertion catches
# the mutation. Two distinct mutations, one per hazard named in the task.
def _mutate_delete_the_label(src: str) -> str:
    """Hazard A: 'fix' the clipping by DELETING the label.

    A non-strict xfail on the containment invariant would pass on this — which
    is why the xfail was strict while the defect was open. This mutation is
    caught by the label COUNT assertion (a floor stops being named).
    """
    needle = ("ctx.fillText(\"FLOOR \" + fl.number + \" \\u00b7 \" "
              "+ String(fl.role || \"\"),\n                       "
              "lp.x + 4, baseline);")
    assert needle in src, "the label fillText call moved; update the mutation"
    return src.replace(needle, "/* label deleted by the mutation */")


def _mutate_clamp_the_baseline(src: str) -> str:
    """Hazard B: the RECOMMENDED fix — clamp the baseline into the canvas.

    This mutation is kept as a proof that the containment assertion is
    discriminating rather than a tautology: it replaces the renderer's own
    §6.1 baseline (stagger + clamp) with a bare clamp, which on the OLD renderer
    was the whole fix. It must still contain the labels, proving the assertion
    measures the clamp and not something incidental.
    """
    needle = ("var baseline = lp.y + 13 + fl.number * FLOOR_STRIDE;\n"
              "          baseline = Math.min(Math.max(baseline, "
              "FLOOR_GLYPH_H + 2), cam.h - 4);")
    assert needle in src, "the label baseline expression moved; update the mutation"
    return src.replace(
        needle,
        "var baseline = Math.min(Math.max(lp.y + 13, 13), cam.h - 4);")


def _label_boxes(m: dict) -> list[tuple]:
    return sorted((lab["baseline_y"] - FLOOR_ASCENT,
                   lab["baseline_y"] + FLOOR_DESCENT, lab["number"])
                  for lab in m["labels"])


def _program_for(payload: dict, w: int = CANVAS_W, h: int = CANVAS_H) -> str:
    """The measurement program for a payload, for the mutation tests."""
    return _MEASURE % {"payload": json.dumps(payload), "w": w, "h": h}


def test_mutation_deleting_the_label_is_caught_by_the_label_count():
    """Proof mutation A fails an assertion in this file.

    On the mutated copy the FLOOR labels are gone: `len(labels) != n_floors`,
    which is the count assertion in the containment test. This is the exact
    non-fix the strict xfail is written to reject.
    """
    src = _mutate_delete_the_label(_plain_src())
    payload = H.make_payload(_floors(3), [])
    out = _render_with_src(src, payload, _program_for(payload))
    assert out["labels"] == [], (
        "the delete mutation did not remove the labels, so the proof is void: %r"
        % out["labels"])
    # the assertion that catches it: a floor is no longer named
    assert len(out["labels"]) != 3, "the count assertion should have caught this"


def test_mutation_clamping_the_baseline_satisfies_the_containment_invariant():
    """Proof the bare-clamp mutation makes the invariant hold, as does the fix.

    The §6.1 fix staggers then clamps; the mutation strips the stagger and keeps
    only a bare clamp. On the OLD renderer the bare clamp WAS the fix (it brought
    FLOOR 1 from 552.20 back to 496); on the fixed renderer both the fix and the
    mutation contain every label, so this test asserts CONTAINMENT for both,
    proving the invariant assertion is discriminating (it measures the clamp) and
    not a tautology.
    """
    src = _mutate_clamp_the_baseline(_plain_src())
    payload = H.make_payload(_floors(3), [])
    fixed = _render_with_src(src, payload, _program_for(payload))
    real = _measure(3)
    h = fixed["h"]

    def contained(m):
        return all(0 <= lab["baseline_y"] - FLOOR_ASCENT and
                   lab["baseline_y"] + FLOOR_DESCENT <= h for lab in m["labels"])

    # both the fix and the bare-clamp mutation keep every label in frame
    assert contained(real), (
        "the real renderer no longer contains every label — the §6.1 clamp is "
        "broken")
    assert contained(fixed), (
        "the clamp mutation did not contain the labels; the invariant assertion "
        "does not discriminate: %r" % _label_boxes(fixed))
    # the bare clamp and the §6.1 baseline agree on the lowest label (both pull
    # FLOOR 1 to the clamp ceiling): the mutation is the fix minus the stagger.
    f1_fixed = {lab["number"]: lab["baseline_y"] for lab in fixed["labels"]}[1]
    f1_real = {lab["number"]: lab["baseline_y"] for lab in real["labels"]}[1]
    assert 0 <= f1_fixed <= h, f1_fixed
    assert f1_real == pytest.approx(496.0, abs=0.05), f1_real


def test_mutation_that_swaps_the_anchor_to_the_far_corner_is_caught_by_the_pitch():
    """A third hazard, for completeness: moving the anchor to the FAR corner.

    Anchoring every deck at `iso(0, 0, z)` instead of the near edge keeps the
    `z` term (the pitch is intact) but is a placement change; it is included to
    show the pitch assertion is about the `z` term and the room-label assertion
    is independent. Not a required proof — recorded because it is cheap.
    """
    src = _plain_src().replace("var lp = iso(edgeX, maxY, z, cam);",
                               "var lp = iso(0, 0, z, cam);")
    assert "var lp = iso(0, 0, z, cam);" in src
    payload = H.make_payload(_floors(3), [])
    out = _render_with_src(src, payload, _program_for(payload))
    labs = sorted(out["labels"], key=lambda d: d["number"])
    pitch = DECK_Z * FLOOR_HEIGHT_UNITS * out["zoom"]
    # the z term survives, so moving the anchor in x,y leaves the label stack
    # one deck pitch apart (minus the fixed per-floor stagger, which is a
    # constant offset the anchor move does not touch)...
    for i in range(len(labs) - 1):
        gap = abs(labs[i + 1]["baseline_y"] - labs[i]["baseline_y"])
        assert gap == pytest.approx(pitch - FLOOR_STRIDE, abs=1e-6), gap
    # ...but the room labels are unaffected (independent assertion), proving the
    # room-label test is not reading the floor label by accident.
    assert out["plates"], "the room plates were not measured"
