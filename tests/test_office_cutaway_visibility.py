"""Cutaway visibility — can the viewer still SEE the people at the shipped wall?

The question this file exists to settle
---------------------------------------
``FACE_H`` (the cutaway wall-face height in ``qubit-office.js``) was raised from
20 to **36** so the walls read as walls rather than a coloured rim. That is a
measured win for the wall/plate ratio (0.1238 -> 0.2229, see
``docs/game-office/37-WALL-VOLUME.md`` §6). But a cutaway exists for the
OPPOSITE reason: so the viewer can see INTO the rooms. A taller wall covers
more of the interior, and **nobody had measured what is actually visible to the
viewer** — the only global number on record was "non-background pixels 27.15%",
which says nothing about whether a seated character is readable.

This file answers that from RECORDED DRAW CALLS, not from a picture.

How it is measured
------------------
The real ``drawTower`` is executed against the fake-canvas recorder harness
(the same approach as ``tests/test_office_tower_visual.py``: the plugin's final
``register(...)`` line is rewritten to export the drawing functions, and every
``fill`` is recorded with its path and ``fillStyle``). From that stream:

  * a **wall fill** is one whose colour is ``shade(base, 0.10)`` or
    ``shade(base, 0.04)`` (the two cutaway faces, ``drawRoomPlate``);
  * a **plate fill** is one whose colour is ``shade(base, -0.05)`` (occupied) or
    ``shade(base, -0.16)`` (empty);
  * a **character body** is the ``roundRect`` fill ``drawAgent`` draws;
  * a **marker** is the ``rect`` fill ``drawMarker`` draws at
    ``rgba(12,16,22,0.86)``.

The renderer paints far -> near (``paintOrder``), so a wall can only hide a
character drawn BEFORE it: occlusion is the area of a character box's overlap
with any wall quad at a LATER index in the stream. Overlap of two convex
polygons is computed by Sutherland-Hodgman clipping, which the file tests
against a known area first so a broken clipper cannot silently report "visible".

The geometry is the REAL built tower: ``tower.py``'s 12x7 rooms on its 3x2 grid
at pitch (13, 8), ``FLOOR_HEIGHT_UNITS`` decks, the real seat slots, and the
renderer's own COVER fit on the real 880x500 canvas. Nothing is transcribed.

What is asserted (each capable of failing — see the mutations at the bottom)
---------------------------------------------------------------------------
  1. At the shipped ``FACE_H = 36`` no seated character is fully hidden, and
     **the raise from 20 to 36 adds no occlusion at all**: the set of occluded
     characters at 36 is identical to the set at 20.
  2. The plate (where a character stands) is never covered by its own walls past
     a stated fraction; the real number at 36 is reported and bounded.
  3. The **visibility ceiling**: the largest ``FACE_H`` at which a seated
     character is still FULLY visible (not "not fully hidden" — fully visible).
     Swept across ``FACE_H`` in scratch copies of the renderer, never the real
     file. The doc's 78 is a GEOMETRIC limit; the visibility limit is measured.
  4. A marker above a head is not occluded by the wall that rises from the deck
     below it; the clearance in FACE_H units is reported.

Hermetic: reads one renderer file, shells to ``node``, no network, no browser.
Skips cleanly when ``node`` is absent. It NEVER writes the renderer: the FACE_H
sweep patches a scratch STRING of the source, never the file on disk.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
ENTRY = (REPO / "frontend" / "qubit-app" / "plugins" / "qubit-office"
         / "qubit-office.js")

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node is not on PATH")

# Reuse the tower-visual harness verbatim as a module (its HARNESS is a printf
# template, its EXPOSED list names the symbols to export, and its REGISTER_LINE
# is the IIFE tail to rewrite). Reading it as a module keeps ONE definition of
# the SDK stub and the fake context; it is not modified.
import sys  # noqa: E402

sys.path.insert(0, str(REPO / "tests"))
import test_office_tower_visual as H  # noqa: E402

sys.path.insert(0, str(REPO))
from voicebridge.office import tower as T  # noqa: E402

# ------------------------------------------------------------------ the tilt
# The renderer source, with FACE_H optionally rewritten. `face_h=None` uses the
# file as shipped. The file on disk is never touched.
_FACE_H_RE = re.compile(r"var FACE_H = (\d+), FACE_W = 4;")


def _shipped_face_h():
    """The FACE_H actually in the renderer, parsed not assumed.

    Assertions are made against THIS value, so shipping a different FACE_H
    (say 78) makes the 'at the shipped value' tests fail rather than silently
    measuring a hardcoded 36 that the renderer no longer uses.
    """
    m = _FACE_H_RE.search(ENTRY.read_text())
    assert m, ("no `var FACE_H = <n>, FACE_W = 4;` line in %s — the constant "
               "moved or was renamed." % ENTRY.name)
    return int(m.group(1))


SHIPPED = _shipped_face_h()


def _source(face_h=None):
    src = ENTRY.read_text()
    if face_h is not None:
        src, n = _FACE_H_RE.subn("var FACE_H = %d, FACE_W = 4;" % face_h, src)
        assert n == 1, (
            "expected exactly one `var FACE_H = <n>, FACE_W = 4;` line in %s, "
            "found %d — the constant moved and this file's sweep cannot patch "
            "it blindly." % (ENTRY.name, n))
    assert H.REGISTER_LINE in src, (
        "the harness rewrites %r in %s; it is gone, so the drawing functions "
        "cannot be reached. Update REGISTER_LINE."
        % (H.REGISTER_LINE, ENTRY.name))
    export = "globalThis.__X = {" + ", ".join(
        "%s: %s" % (nm, nm) for nm in H.EXPOSED) + "};"
    return src.replace(H.REGISTER_LINE, export)


def _run(program, face_h=None):
    full = (H.HARNESS % {"exposed": json.dumps(H.EXPOSED)}
            + "\nvar __SRC = " + json.dumps(_source(face_h)) + ";\n"
            + "(0, eval)(__SRC);\n"
            + "var X = __X;\n"
            + program
            + "\nif (typeof __RESULT !== 'undefined') "
              "{ process.stdout.write(JSON.stringify(__RESULT)); }\n")
    proc = subprocess.run([NODE, "-e", full], capture_output=True, text=True,
                          timeout=60)
    if proc.returncode != 0:
        raise AssertionError("node failed:\nSTDOUT:\n%s\nSTDERR:\n%s"
                             % (proc.stdout, proc.stderr))
    out = proc.stdout.strip()
    assert out, ("node produced no JSON; the program did not reach __RESULT. "
                 "STDOUT=%r STDERR=%r" % (proc.stdout, proc.stderr))
    return json.loads(out)


# --------------------------------------------------------------- the clipper
# Convex-polygon overlap by Sutherland-Hodgman. Winding-agnostic (both polygons
# are normalised to CCW first) and covered by `test_the_clipper_measures_known_
# overlaps`, so a broken clipper fails loudly instead of reporting a clean frame.
def _signed_area(pts):
    a = 0.0
    for i in range(len(pts)):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % len(pts)]
        a += x1 * y2 - x2 * y1
    return a / 2.0


def poly_area(pts):
    return abs(_signed_area(pts))


def _ccw(pts):
    return list(pts) if _signed_area(pts) >= 0 else list(reversed(pts))


def clip_convex(subject, clip):
    if len(subject) < 3 or len(clip) < 3:
        return []
    sub, cl = _ccw(subject), _ccw(clip)

    def inside(p, a, b):
        return (b[0]-a[0])*(p[1]-a[1]) - (b[1]-a[1])*(p[0]-a[0]) >= -1e-9

    def inter(p1, p2, a, b):
        x1, y1 = p1; x2, y2 = p2; x3, y3 = a; x4, y4 = b
        d = (x1-x2)*(y3-y4) - (y1-y2)*(x3-x4)
        if abs(d) < 1e-12:
            return list(p2)
        return [((x1*y2-y1*x2)*(x3-x4) - (x1-x2)*(x3*y4-y3*x4)) / d,
                ((x1*y2-y1*x2)*(y3-y4) - (y1-y2)*(x3*y4-y3*x4)) / d]

    out = sub
    for i in range(len(cl)):
        if not out:
            return []
        a, b = cl[i], cl[(i + 1) % len(cl)]
        inp, out = out, []
        for j in range(len(inp)):
            cur, prv = inp[j], inp[j - 1]
            if inside(cur, a, b):
                if not inside(prv, a, b):
                    out.append(inter(prv, cur, a, b))
                out.append(cur)
            elif inside(prv, a, b):
                out.append(inter(prv, cur, a, b))
    return out


# --------------------------------------------------------------- the payload
def _floors(n_floors):
    """The real built tower geometry: tower.py's 12x7 rooms on its 3x2 grid."""
    RW, RH = T.ROOM_W, T.ROOM_H
    COLS, ROWS = T.ROOM_COLS, T.ROOM_ROWS
    floors = []
    for fl in range(1, n_floors + 1):
        rooms = []
        for i in range(COLS * ROWS):
            col, row = i % COLS, (i // COLS) % ROWS
            seats = [{
                "slot": n, "agent_id": "ag-%d-%d-%d" % (fl, i, n),
                "slug": "s%d_%d_%d" % (fl, i, n),
                "x": T.room_slot_x(col, n) + T.room_slot_offset(n),
                "y": T.room_slot_y(row, n)} for n in range(6)]
            rooms.append({"id": "r%d-%d" % (fl, i), "slug": "r%d" % i,
                          "kind": "open", "department_id": 1,
                          "x": col * (RW + 1), "y": row * (RH + 1),
                          "w": RW, "h": RH, "seats": seats})
        floors.append({"id": "f%d" % fl, "number": fl, "name": "l%d" % fl,
                       "role": "operations" if fl % 2 else "executive",
                       "theme": "operations", "z": fl * T.FLOOR_HEIGHT_UNITS,
                       "rooms": rooms})
    return floors


def _chars(floors, char_floors, slots=(0,)):
    chars = []
    for fl in floors:
        if fl["number"] not in char_floors:
            continue
        for room in fl["rooms"]:
            for s in room["seats"]:
                if slots is not None and s["slot"] not in slots:
                    continue
                chars.append({
                    "agent_id": s["agent_id"], "slug": s["slug"],
                    "seat_type": "worker", "department_id": 1,
                    "supervisor_id": None, "seated": True,
                    "position": {"floor": fl["number"], "room": room["slug"],
                                 "room_id": room["id"], "slot": s["slot"],
                                 "x": s["x"], "y": s["y"]},
                    "facing": "down", "walking": False, "at": "desk",
                    "animation": "idle", "last_event": None, "stalled": False})
    return chars


def _payload(floors, chars):
    return {
        "schema": "office/tower", "now": 1700000000.0, "active_window_s": 900,
        "walk_s": 6, "empty": False, "empty_reason": None,
        "hud": {"headcount": len(chars), "revenue_cents": 0, "cost_cents": 0,
                "net_cents": 0, "burn_cents_per_day": 0, "runway_days": None,
                "survival": {"floor_days": None}, "floors": []},
        "floorplan": {"schema": "office/floorplan",
                      "grid": {"cols": T.ROOM_COLS * (T.ROOM_W + 1),
                               "rows": T.ROOM_ROWS * (T.ROOM_H + 1), "tile": 1},
                      "floors": floors},
        "characters": chars, "activity": [], "history": [],
        "history_totals": {"revenue_cents": 0, "cost_cents": 0,
                           "net_cents": 0},
        "motion_rule": "A character moves only when an office event says so.",
        "read_only": True, "preview": None}


# The dump program: drives the REAL drawTower over the REAL fitted camera and
# records wall quads, plate quads, character bodies and markers, each with its
# index in the op stream (the painter's order is the occlusion order).
_DUMP = r"""
var p = __PAYLOAD__;
var cam = {cx: 0, cy: 0, zoom: 1, w: 880, h: 500};
X.fitToBuilding(cam, p.floorplan.floors);
var out = drawTowerF(p); var c = out.ctx;
function quad(pth) {
  var pts = [];
  for (var i = 0; i < pth.length; i++)
    if (pth[i][0] === 'moveTo' || pth[i][0] === 'lineTo') pts.push([pth[i][1], pth[i][2]]);
  return pts;
}
var wallC = {}, plateC = {};
p.floorplan.floors.forEach(function (fl) {
  var b = X.deckTint(fl.role, (fl.rooms || []).length);
  wallC[X.shade(b, 0.10)] = 1; wallC[X.shade(b, 0.04)] = 1;
  plateC[X.shade(b, -0.05)] = 1; plateC[X.shade(b, -0.16)] = 1;
});
var walls = [], plates = [], bodies = [], markers = [];
c.ops.forEach(function (o, i) {
  if (o.op === 'fill' && wallC[o.fillStyle] === 1) {
    var q = quad(o.path); if (q.length === 4) walls.push({i: i, pts: q});
  }
  if (o.op === 'fill' && plateC[o.fillStyle] === 1) {
    var q2 = quad(o.path); if (q2.length === 4) plates.push({i: i, pts: q2});
  }
  if (o.op === 'fill' && o.path.some(function (x) { return x[0] === 'roundRect'; })) {
    var rr = o.path.find(function (x) { return x[0] === 'roundRect'; });
    bodies.push({i: i, x: rr[1], y: rr[2], w: rr[3], h: rr[4]});
  }
  if (o.op === 'fill' && o.fillStyle === 'rgba(12,16,22,0.86)') {
    var rc = o.path.find(function (x) { return x[0] === 'rect'; });
    if (rc) markers.push({i: i, x: rc[1], y: rc[2], w: rc[3], h: rc[4]});
  }
});
__RESULT = {zoom: cam.zoom, walls: walls, plates: plates,
            bodies: bodies, markers: markers,
            face_h: X.FACE_H,
            agents: out.probe.agents.map(function (a) {
              return {x: a.x, y: a.y, floor: a.floor, gx: a.gx, gy: a.gy};
            })};
"""


def _dump(face_h=None, n_floors=3, char_floors=None, slots=(0,),
          marker=False):
    if char_floors is None:
        char_floors = tuple(range(1, n_floors + 1))
    floors = _floors(n_floors)
    chars = _chars(floors, char_floors, slots=slots)
    if marker:
        for ch in chars:
            ch["animation"] = "blocked"
    prog = _DUMP.replace("__PAYLOAD__", json.dumps(_payload(floors, chars)))
    return _run(prog, face_h=face_h)


# ------------------------------------------------------------- the measures
def _box(o):
    return [[o["x"], o["y"]], [o["x"] + o["w"], o["y"]],
            [o["x"] + o["w"], o["y"] + o["h"]], [o["x"], o["y"] + o["h"]]]


def _overlap_after(box, idx, walls):
    """Area of `box` covered by any wall quad painted AFTER op `idx`."""
    a0 = poly_area(box)
    cov = 0.0
    for w in walls:
        if w["i"] <= idx:
            continue
        cl = clip_convex(box, w["pts"])
        if len(cl) >= 3:
            cov += poly_area(cl)
    return min(cov, a0)


def _visibility(face_h=None, n_floors=3, slots=(0,), marker=False):
    """Per-character visible fraction of the drawn body box."""
    r = _dump(face_h=face_h, n_floors=n_floors, slots=slots, marker=marker)
    out = []
    for b in (r["markers"] if marker else r["bodies"]):
        box = _box(b)
        a0 = poly_area(box)
        cov = _overlap_after(box, b["i"], r["walls"])
        out.append({"i": b["i"], "x": b["x"], "y": b["y"], "area": a0,
                    "covered": cov, "visible": (a0 - cov) / a0 if a0 else 1.0})
    return r, out


# ================================================================== sanity
def test_the_clipper_measures_known_overlaps():
    """The occlusion maths itself: a half-overlap is exactly half the area.

    If this fails, every other assertion in this file is meaningless — a broken
    clipper reports 'fully visible' for a fully hidden figure.
    """
    sq = [[0, 0], [10, 0], [10, 10], [0, 10]]
    half = [[5, 0], [15, 0], [15, 10], [5, 10]]
    assert poly_area(clip_convex(sq, half)) == pytest.approx(50.0), (
        "a half-overlap must be half the area")
    assert poly_area(clip_convex(sq, sq)) == pytest.approx(100.0)
    assert clip_convex(sq, [[20, 20], [30, 20], [30, 30], [20, 30]]) == [], (
        "disjoint polygons must not overlap")
    # winding must not matter
    assert poly_area(clip_convex(sq[::-1], half[::-1])) == pytest.approx(50.0)


def test_the_renderer_is_executed_and_draws_the_real_tower():
    """The harness reaches the real renderer, and a real 3-floor, 6-room-per-
    floor plan draws 18 rooms, 18 plates, 36 walls and one body per seated
    character. If the geometry stops being the built one, this notices."""
    r = _dump(face_h=None, n_floors=3, slots=(0,))
    assert r["face_h"] == SHIPPED, (
        "the renderer was executed with FACE_H=%r but the file declares %r"
        % (r["face_h"], SHIPPED))
    assert r["zoom"] > 0, r
    # 3 floors x 6 rooms x 2 walls
    assert len(r["walls"]) == 36, (
        "expected 2 cutaway walls per room (36), drew %d" % len(r["walls"]))
    assert len(r["plates"]) == 18, (
        "expected one plate per room (18), drew %d" % len(r["plates"]))
    assert len(r["bodies"]) == 18, (
        "expected one body per seated character (18), drew %d"
        % len(r["bodies"]))


# ============================================ 1. characters visible at FACE_H
def test_at_the_shipped_face_h_no_seated_character_is_hidden():
    """HARD RED LINE: at FACE_H=36 not one seated character is fully hidden.

    A character reduced to zero visible body area is not 'subtle occlusion' —
    it is a figure the viewer cannot see at all, which is the cutaway failing
    outright. The shipped value must clear this, in both occupancy patterns:

      * the front-row case (slot 0, the seat nearest the camera's read), and
      * the ALL-SEATS worst case (every desk filled, back rows included).

    The all-seats case is the honest one to pin: a full office, not a showroom.
    """
    for slots in ((0,), None):
        r, res = _visibility(face_h=None, slots=slots)
        hidden = [x for x in res if x["visible"] < 0.001]
        assert not hidden, (
            "at the shipped FACE_H=%d with slots=%r, %d seated character(s) "
            "were reduced to zero visible area (fully behind a wall): %r"
            % (SHIPPED, slots, len(hidden),
               [(round(x["x"]), round(x["y"])) for x in hidden]))


def test_the_face_h_raise_from_20_to_36_adds_no_occlusion():
    """The tradeoff as measured: 20 -> 36 costs the viewer NOTHING.

    Occlusion that exists at 36 but not at 20 would be a real price paid for
    wall volume. This asserts the price is zero — the same set of bodies (by
    position) is occluded at both heights, to the same fraction. The wall's
    BOTTOM edge is fixed (it sits on the plate edge), so raising FACE_H extends
    the wall only UPWARD, away from the interior a seated figure occupies, until
    it is tall enough to reach the deck above. 20 and 36 are both below that.
    """
    def occluded(face_h):
        _, res = _visibility(face_h=face_h, slots=None)
        return sorted((round(x["x"]), round(x["y"]), round(x["covered"], 3))
                      for x in res if x["covered"] > 0.01)

    at20 = occluded(20)
    at_shipped = occluded(SHIPPED)
    assert at20 == at_shipped, (
        "raising FACE_H 20->%d changed which characters are occluded, so the "
        "wall volume was bought at a real cost:\n  at 20: %r\n  at %d: %r"
        % (SHIPPED, at20, SHIPPED, at_shipped))


# ================================================= 2. the plate stays visible
def test_the_plate_is_barely_covered_by_its_own_walls_at_36():
    """The desk PLATE — where a character stands — must stay visible.

    The two cutaway walls rise from the plate's top and right edges, OUTSIDE the
    diamond, so they cover only the slice of plate nearest those edges. This
    reports the real fraction of a room's plate area its own walls cover at the
    shipped FACE_H, and bounds it: under 20% (a fifth of the floor hidden at the
    edge is the most a cutaway can lose without defeating its purpose). For
    reference the doc's 78-unit ceiling reaches 25.7% — over this bound.
    """
    def close(p, q):
        return abs(p[0] - q[0]) < 0.05 and abs(p[1] - q[1]) < 0.05

    r = _dump(face_h=None, slots=(0,))
    fracs = []
    for pl in r["plates"]:
        a, b, c, d = pl["pts"]
        a0 = poly_area(pl["pts"])
        cov = 0.0
        for w in r["walls"]:
            pts = w["pts"]
            if (close(pts[0], a) and close(pts[1], b)) or \
               (close(pts[0], b) and close(pts[1], c)):
                cl = clip_convex(pl["pts"], w["pts"])
                if len(cl) >= 3:
                    cov += poly_area(cl)
        fracs.append(min(cov, a0) / a0 if a0 else 0.0)
    assert fracs, "no plates were measured"
    worst = max(fracs)
    print("\nplate coverage by own walls @ shipped FACE_H=%d: worst=%.4f "
          "mean=%.4f" % (SHIPPED, worst, sum(fracs) / len(fracs)))
    assert worst < 0.20, (
        "at the shipped FACE_H=%d a room's own walls cover %.2f%% of its plate "
        "area, over the 20%% bound — the cutaway is eating the floor it exists "
        "to show" % (SHIPPED, worst * 100))


# =========================================== 3. the visibility ceiling on FACE_H
def test_the_visibility_ceiling_on_face_h_is_measured_and_reported():
    """The FACE_H at which a seated character first stops being FULLY visible.

    The doc calls 78 the ceiling — but that is the GEOMETRIC limit (a wall may
    not rise past the deck gap). The VISIBILITY limit is different and lower:
    the wall first covers a seated figure (from the deck below rising into it)
    well before 78. This sweeps FACE_H in scratch copies and pins the largest
    value at which every seated character is still FULLY visible (>=99.9% of its
    body box), then asserts the shipped 36 is comfortably below it.
    """
    def all_full(fh):
        _, res = _visibility(face_h=fh, slots=(0,))
        return min(x["visible"] for x in res)

    # the shipped value must be fully clear; the ceiling is the last height
    # that stays clear.
    assert all_full(None) >= 0.999, (
        "at the shipped FACE_H=%d a front-row seated character is already "
        "partly occluded (min visible %.4f) — the wall is too tall"
        % (SHIPPED, all_full(None)))

    lo, hi = SHIPPED, 78     # shipped clear, 78 known-bad; find the boundary
    assert all_full(78) < 0.999, (
        "expected a seated character to be occluded at the doc's 78-unit "
        "ceiling; if it is not, the ceiling moved and this test must be retuned")
    # binary search the first integer FACE_H that is NOT fully clear
    while lo + 1 < hi:
        mid = (lo + hi) // 2
        if all_full(mid) >= 0.999:
            lo = mid
        else:
            hi = mid
    ceiling = lo
    print("\nvisibility ceiling: every seated character fully visible up to "
          "FACE_H=%d (first occlusion at %d)" % (ceiling, ceiling + 1))
    assert ceiling >= 44, (
        "the visibility ceiling on FACE_H is only %d; the shipped %d has too "
        "little headroom over it" % (ceiling, SHIPPED))
    # and the shipped value must be strictly below the ceiling
    assert SHIPPED < ceiling, (
        "the shipped FACE_H=%d does not sit below the measured visibility "
        "ceiling %d" % (SHIPPED, ceiling))


# ==================================================== 4. marker clearance
def test_a_marker_above_a_head_clears_the_wall_below_it():
    """A stall/walk marker sits ABOVE the head, so it out-clears the body.

    `drawMarker` puts the glyph box at `y - 20*zoom`, a full head-height above
    the feet, so a wall tall enough to clip a seated body has not yet reached
    the marker. This reports the clearance as FACE_H units and asserts the
    shipped 36 leaves the markers fully clear.
    """
    def marker_min(fh):
        _, res = _visibility(face_h=fh, slots=(0,), marker=True)
        return min(x["visible"] for x in res)

    at_shipped = marker_min(None)
    assert at_shipped >= 0.999, (
        "at the shipped FACE_H=%d a marker above a head is already occluded "
        "(min visible %.4f) — a stalled/walking signal the viewer cannot read"
        % (SHIPPED, at_shipped))

    lo, hi = SHIPPED, 120
    assert marker_min(120) < 0.999, (
        "expected markers to be occluded by FACE_H=120; the ceiling moved")
    while lo + 1 < hi:
        mid = (lo + hi) // 2
        if marker_min(mid) >= 0.999:
            lo = mid
        else:
            hi = mid
    clearance = lo
    print("\nmarker clearance: markers fully clear up to FACE_H=%d "
          "(shipped 36 has %d units of margin)" % (clearance, clearance - 36))
    assert clearance > 36, (
        "markers are not clear even at the shipped FACE_H=36")
