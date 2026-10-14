"""M4 — is the cutaway wall face tall enough to read as volume?

The evidence this file exists to settle
---------------------------------------
The rooms were enlarged from ``6x4`` to ``12x7`` tiles (``tower.py:110``). The
cutaway wall face height ``FACE_H`` was NOT changed (``qubit-office.js:137``).
Because the wall face is a FIXED pixel height while the plate it stands on grew
with the room, the wall's share of the plate it borders shrank even though the
number never moved. This is the "flat plates, not a building with volume"
report, and it is arithmetic, not taste.

What is measured, and against what
----------------------------------
Every constant here is PARSED from the real sources, never transcribed from
this docstring:

  * ``TILE_W``, ``TILE_H``, ``DECK_Z``, ``FACE_H`` — ``qubit-office.js``
  * ``FLOOR_HEIGHT_UNITS``, ``ROOM_W``, ``ROOM_H``, ``ROOM_COLS``, ``ROOM_ROWS``
    — ``voicebridge/office/tower.py``
  * the fitted ``zoom`` — RUN, not assumed: ``camera.js``'s own ``fit`` is
    required and called with the real 880x500 canvas and the real 3-floor
    extent, so the zoom is the one the renderer would actually frame.

Three independent numbers are produced:

  1. the projected wall face height vs the projected room-plate depth, and their
     ratio — the "is it a wall or a line" test;
  2. the projected deck separation (``FLOOR_HEIGHT_UNITS * DECK_Z * zoom``) vs
     the wall height — the "do two floors merge" test;
  3. the interpenetration ceiling (a wall may not rise past the deck gap) — the
     self-consistency test.

The ratio is ZOOM-INVARIANT: both the wall height and the plate depth are
multiplied by ``cam.zoom``, so the ratio is pure geometry —
``FACE_H / ((ROOM_W + ROOM_H) * TILE_H / 2)``. That is asserted too, because it
is what makes the defect a scale-free framing error rather than a window-size
accident.

Hermetic: no network, no browser, no server. Shells out to ``node`` (required
for the real ``fit``), and skips cleanly when node is absent.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest  # noqa: E402

from _office_node import run_node  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
PLUGIN_DIR = ROOT / "frontend" / "qubit-app" / "plugins" / "qubit-office"
RENDERER = PLUGIN_DIR / "qubit-office.js"
CAMERA = PLUGIN_DIR / "camera.js"
TOWER = ROOT / "voicebridge" / "office" / "tower.py"

_FOUND = shutil.which("node")
NODE: str = _FOUND or "node"

pytestmark = pytest.mark.skipif(
    _FOUND is None,
    reason="node is not installed; the fitted zoom cannot be computed")


# ----------------------------------------------------------- parse the truth
_DECL_RE = re.compile(
    r"(?:var\s+|const\s+|let\s+)?([A-Z_][A-Z0-9_]*|(?:[A-Z_][A-Z0-9_]*\s*,\s*)+"
    r"[A-Z_][A-Z0-9_]*)\s*=\s*"
    r"(\d+(?:\.\d+)?(?:\s*,\s*\d+(?:\.\d+)?)*)")


def _parse_const(name: str, src: str) -> float:
    """Read ``NAME = 20`` or ``NAME, OTHER = 20, 17`` from a JS or Python source.

    Two declaration shapes are used across the sources this test measures:

      * a chained declarator — ``var FACE_H = 20, FACE_W = 4;`` (JS) — which is
        two independent ``name = value`` pairs;
      * a tuple assignment — ``ROOM_W, ROOM_H = 12, 7`` (Python) — where the
        N-th name holds the N-th value.

    ``_DECL_RE`` captures a comma-joined LHS and a comma-joined numeric RHS.
    When the LHS has exactly one name it is a scalar pair. When it has several
    names AND the same number of RHS values, the name's index selects its value
    (the tuple form). A multi-name LHS with a SINGLE value (``var A = 1, B = 2``)
    is handled by the caller's own scan, so this function tries every match.

    Deliberately strict: a missing constant is an error, not a silent default.
    If a source renames or removes one, this test FAILS loudly rather than
    measure a number nobody uses.
    """
    for hit in _DECL_RE.finditer(src):
        lhs = [p.strip() for p in hit.group(1).split(",")]
        rhs = [float(p) for p in hit.group(2).split(",")]
        if name in lhs:
            assert lhs.index(name) < len(rhs), (
                f"{name} is declared but has no numeric value in {hit.group(0)!r}")
            return rhs[lhs.index(name)]
    raise AssertionError(
        f"{name} not found in the source it is expected to live in")


@pytest.fixture(scope="module")
def renderer_consts() -> dict:
    src = RENDERER.read_text()
    return {
        "TILE_W": _parse_const("TILE_W", src),
        "TILE_H": _parse_const("TILE_H", src),
        "DECK_Z": _parse_const("DECK_Z", src),
        "FACE_H": _parse_const("FACE_H", src),
        "MIN_ZOOM": _parse_const("MIN_ZOOM", src),
        "MAX_ZOOM": _parse_const("MAX_ZOOM", src),
        "CHAR_FLOOR_ZOOM": _parse_const("CHAR_FLOOR_ZOOM", src),
    }


@pytest.fixture(scope="module")
def tower_consts() -> dict:
    src = TOWER.read_text()
    return {
        "FLOOR_HEIGHT_UNITS": _parse_const("FLOOR_HEIGHT_UNITS", src),
        "ROOM_W": _parse_const("ROOM_W", src),
        "ROOM_H": _parse_const("ROOM_H", src),
        "ROOM_COLS": _parse_const("ROOM_COLS", src),
        "ROOM_ROWS": _parse_const("ROOM_ROWS", src),
    }


def _run(script: str) -> dict:
    """Run JS that requires ``camera.js`` and prints one JSON object."""
    program = (
        "var C = require(%s);\n%s\n" % (json.dumps(str(CAMERA)), script)
    )
    proc = subprocess.run([NODE], input=program, capture_output=True,
                          text=True, timeout=60)
    assert proc.returncode == 0, (
        f"node exited {proc.returncode}\nSTDOUT:\n{proc.stdout}\n"
        f"STDERR:\n{proc.stderr}")
    out = proc.stdout.strip()
    assert out, f"node printed nothing\nSTDERR:\n{proc.stderr}"
    return json.loads(out)


# --------------------------------------------------- the fitted zoom (RUN)
def _fitted_zoom(rc: dict, tc: dict, viewport_w: float, viewport_h: float,
                 floor_numbers: list[int]) -> dict:
    """Compute the REAL fitted zoom from ``camera.js``'s own ``fit``.

    The extent is measured the way ``qubit-office.js::extent`` measures it: the
    four corners of every room on every shown floor, projected by the real
    ``isoWorld`` (``sy = (x + y) * TILE_H/2 - z * DECK_Z``), with ``z`` folded
    in. The 880x500 canvas and a ``margin`` of 0.08 are the renderer's own
    (:1339, :442). Nothing here is guessed: the zoom printed is what the app
    frames.
    """
    rooms = []
    for i in range(int(tc["ROOM_COLS"] * tc["ROOM_ROWS"])):
        col = i % int(tc["ROOM_COLS"])
        row = (i // int(tc["ROOM_COLS"])) % int(tc["ROOM_ROWS"])
        rooms.append([col * (tc["ROOM_W"] + 1), row * (tc["ROOM_H"] + 1)])
    zs = [n * tc["FLOOR_HEIGHT_UNITS"] for n in floor_numbers]
    script = """
      var TILE_W = %(tw)s, TILE_H = %(th)s, DECK_Z = %(dz)s;
      var ROOM_W = %(rw)s, ROOM_H = %(rh)s;
      var rooms = %(rooms)s, zs = %(zs)s;
      function isoW(x, y, z) {
        return {x: (x - y) * (TILE_W / 2),
                y: (x + y) * (TILE_H / 2) - z * DECK_Z};
      }
      var minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
      zs.forEach(function (z) {
        rooms.forEach(function (r) {
          [r[0], r[0] + ROOM_W].forEach(function (px) {
            [r[1], r[1] + ROOM_H].forEach(function (py) {
              var p = isoW(px, py, z);
              if (p.x < minX) minX = p.x; if (p.x > maxX) maxX = p.x;
              if (p.y < minY) minY = p.y; if (p.y > maxY) maxY = p.y;
            });
          });
        });
      });
      var extent = {minX: minX, maxX: maxX, minY: minY, maxY: maxY,
                    w: maxX - minX, h: maxY - minY,
                    cx: (minX + maxX) / 2, cy: (minY + maxY) / 2};
      /* THE renderer's rule, quoted: CAM.fit(viewport, extent,
       *   {minZoom: CHAR_FLOOR_ZOOM, maxZoom: MAX_ZOOM, margin: 0.08})
       * then clamped again to MIN_ZOOM..MAX_ZOOM (qubit-office.js:440-446). */
      var f = C.fit({w: %(vw)s, h: %(vh)s}, extent,
                    {minZoom: %(floor_z)s, maxZoom: %(max_z)s, margin: 0.08});
      var zoom = Math.max(%(min_z)s,
                   Math.min(%(max_z)s, Math.max(f.zoom, %(floor_z)s)));
      console.log(JSON.stringify({zoom: zoom, cover: f.zoom, which: f.fit,
                                  extent: {w: extent.w, h: extent.h}}));
    """ % {
        "tw": rc["TILE_W"], "th": rc["TILE_H"], "dz": rc["DECK_Z"],
        "rw": tc["ROOM_W"], "rh": tc["ROOM_H"],
        "rooms": json.dumps(rooms), "zs": json.dumps(zs),
        "vw": viewport_w, "vh": viewport_h,
        "floor_z": rc["CHAR_FLOOR_ZOOM"], "max_z": rc["MAX_ZOOM"],
        "min_z": rc["MIN_ZOOM"],
    }
    return _run(script)


# ============================================================ (1) the ratio
def test_wall_face_is_a_stated_fraction_of_the_plate_it_stands_on(
        renderer_consts, tower_consts):
    """THE finding. Wall-face pixel height vs room-plate pixel depth.

    The wall face is ``FACE_H * zoom`` px (``drawRoomPlate``, :497). The room
    plate is ``(ROOM_W + ROOM_H) * TILE_H/2 * zoom`` px deep on screen (the iso
    ``sy`` spans ``w`` columns down-right and ``h`` rows down-left).

    RE-POINTED at the fixed truth. This test originally asserted the DEFECT —
    that ``FACE_H = 20`` leaves the wall under a fifth of the plate (ratio
    0.1238) — so it was green only while the bug was present. ``FACE_H`` is now
    36 (``docs/game-office/37-WALL-VOLUME.md`` §6.1), which lifts the ratio to
    0.223 (wall 30.60px vs plate 137.28px at the fitted zoom 0.85): the wall now
    clears the 0.20 "rim vs wall" line and reads as a surface, while staying
    under the 0.25 "wall hides the near desks" anti-cutaway ceiling (§6.3). The
    assertions below pin the FIXED window, so a regression back to a thin wall
    (ratio < 0.20) or an over-tall wall (ratio >= 0.25) fails.
    """
    rc, tc = renderer_consts, tower_consts
    zoom = _fitted_zoom(rc, tc, 880, 500, [1, 2, 3])["zoom"]

    wall_px = rc["FACE_H"] * zoom
    depth_px = (tc["ROOM_W"] + tc["ROOM_H"]) * (rc["TILE_H"] / 2) * zoom
    ratio = wall_px / depth_px

    print(f"\n  fitted zoom (3 floors, 880x500) = {zoom}")
    print(f"  wall face  = FACE_H({rc['FACE_H']}) * zoom({zoom}) = {wall_px:.2f} px")
    print(f"  room depth = (w{tc['ROOM_W']:.0f}+h{tc['ROOM_H']:.0f}) * "
          f"TILE_H/2({rc['TILE_H']/2}) * zoom = {depth_px:.2f} px")
    print(f"  ratio wall/depth = {ratio:.4f}")

    # The fixed window (37-WALL-VOLUME.md §6.1, §6.3): a wall must be a
    # meaningful fraction of the plate (>= 1/5, the rim/wall line) but must not
    # eat the interior (< 1/4, past which the near desks tuck behind their wall).
    RIM = 0.20
    ANTI_CUTAWAY = 0.25
    assert ratio >= RIM, (
        f"wall/depth ratio {ratio:.4f} is under the {RIM} rim threshold — the "
        f"wall is a line again, not volume (wall {wall_px:.2f}px vs depth "
        f"{depth_px:.2f}px at zoom {zoom}). See 37-WALL-VOLUME.md §3, §6.1.")
    assert ratio < ANTI_CUTAWAY, (
        f"wall/depth ratio {ratio:.4f} clears the {ANTI_CUTAWAY} anti-cutaway "
        f"ceiling — the wall starts hiding the near desks, which is the opposite "
        f"of the cutaway's intent (37-WALL-VOLUME.md §6.3).")
    # and the magnitude of the wall's share, as a number
    print(f"  the wall face is {depth_px / wall_px:.2f}x shorter than the plate, "
          f"{100 * ratio:.1f}% of it")


def test_the_ratio_is_zoom_invariant_so_the_defect_is_geometry_not_window_size(
        renderer_consts, tower_consts):
    """Both terms carry ``* zoom``, so the ratio is pure geometry.

    This matters: it means the defect is NOT a window-size accident that a
    bigger canvas would fix. It is the ratio of two constants,
    ``FACE_H / ((ROOM_W + ROOM_H) * TILE_H / 2)``, and only moving ``FACE_H``
    changes it.
    """
    rc, tc = renderer_consts, tower_consts
    def ratio_at(zoom):
        return (rc["FACE_H"] * zoom) / (
            (tc["ROOM_W"] + tc["ROOM_H"]) * (rc["TILE_H"] / 2) * zoom)
    r1, r2 = ratio_at(0.85), ratio_at(2.4)
    assert r1 == pytest.approx(r2), (r1, r2)
    closed_form = rc["FACE_H"] / ((tc["ROOM_W"] + tc["ROOM_H"]) * rc["TILE_H"] / 2)
    assert r1 == pytest.approx(closed_form)
    print(f"\n  ratio is {closed_form:.4f} at every zoom "
          f"(FACE_H / ((w+h)*TILE_H/2))")


def test_the_enlarged_room_made_the_wall_proportionally_thinner(
        renderer_consts, tower_consts):
    """The regression the report names: 6x4 -> 12x7 at unchanged FACE_H.

    Same ``FACE_H``, same ``TILE_H``; only the room grew. The plate got
    ``(12+7)/(6+4) = 1.9`` times deeper, so the wall's share fell by that
    factor — the number did not move but the wall got proportionally thinner.
    """
    rc, tc = renderer_consts, tower_consts
    old_depth = (6 + 4) * (rc["TILE_H"] / 2)
    new_depth = (tc["ROOM_W"] + tc["ROOM_H"]) * (rc["TILE_H"] / 2)
    shrink = old_depth / new_depth
    assert shrink < 1.0
    print(f"\n  plate depth: {old_depth:.1f} -> {new_depth:.1f} world units "
          f"({new_depth / old_depth:.2f}x)")
    print(f"  wall share of the plate fell {new_depth / old_depth:.2f}x "
          f"({old_depth / new_depth:.3f} of its former ratio)")
    assert pytest.approx(new_depth / old_depth, abs=1e-9) == 1.9


# ================================================== (2) do the floors merge
def test_deck_separation_keeps_the_wall_out_of_the_deck_behind_it(
        renderer_consts, tower_consts):
    """Two stacked decks are ``FLOOR_HEIGHT_UNITS * DECK_Z`` world units apart.

    ``tower.py:179`` sets ``z = floor.number * FLOOR_HEIGHT_UNITS`` and the
    projection multiplies ``z`` by ``DECK_Z``, so the real per-floor screen step
    is ``FLOOR_HEIGHT_UNITS * DECK_Z * zoom`` — NOT ``DECK_Z * zoom``. The wall
    rises ``FACE_H * zoom`` px above its plate; if that exceeded the deck step,
    a floor's wall would cover the deck behind it and the floors would merge
    (a different defect from "flat").
    """
    rc, tc = renderer_consts, tower_consts
    zoom = _fitted_zoom(rc, tc, 880, 500, [1, 2, 3])["zoom"]

    deck_step_px = tc["FLOOR_HEIGHT_UNITS"] * rc["DECK_Z"] * zoom
    wall_px = rc["FACE_H"] * zoom

    print(f"\n  deck step = FLOOR_HEIGHT_UNITS({tc['FLOOR_HEIGHT_UNITS']:.0f}) * "
          f"DECK_Z({rc['DECK_Z']:.0f}) * zoom({zoom}) = {deck_step_px:.2f} px")
    print(f"  wall rises {wall_px:.2f} px -> clearance "
          f"{deck_step_px - wall_px:.2f} px "
          f"({100 * wall_px / deck_step_px:.1f}% of the gap)")
    # the floors do NOT merge — the wall uses well under the gap
    assert wall_px < deck_step_px, (
        f"wall {wall_px:.2f}px >= deck step {deck_step_px:.2f}px: floors merge")
    # reported, so the headroom available to a taller wall is a number
    assert deck_step_px - wall_px > 0


# ============================================== (3) self-consistency ceiling
def test_a_taller_wall_fits_under_the_interpenetration_ceiling(
        renderer_consts, tower_consts):
    """The ceiling on ``FACE_H``: it may not exceed the per-floor deck step.

    Both the wall height (``FACE_H * zoom``) and the deck step
    (``FLOOR_HEIGHT_UNITS * DECK_Z * zoom``) carry ``* zoom``, so the ceiling is
    ``FACE_H <= FLOOR_HEIGHT_UNITS * DECK_Z`` in world units — zoom cancels.
    The current value sits far below it, which is exactly why it can be raised
    without any risk of interpenetration.
    """
    rc, tc = renderer_consts, tower_consts
    ceiling = tc["FLOOR_HEIGHT_UNITS"] * rc["DECK_Z"]
    assert rc["FACE_H"] <= ceiling, (
        f"FACE_H {rc['FACE_H']} exceeds the interpenetration ceiling {ceiling}")
    headroom = ceiling - rc["FACE_H"]
    print(f"\n  interpenetration ceiling FACE_H <= "
          f"FLOOR_HEIGHT_UNITS*DECK_Z = {ceiling:.0f} (zoom-invariant)")
    print(f"  current FACE_H {rc['FACE_H']:.0f} leaves {headroom:.0f} units "
          f"of headroom ({100 * rc['FACE_H'] / ceiling:.1f}% used)")


def test_a_recommended_face_h_reaches_volume_and_stays_legal(
        renderer_consts, tower_consts):
    """The recommendation is testable, not a bare assertion.

    A wall at 1/4 of the plate is the target the arithmetic supports (see
    ``docs/game-office/37-WALL-VOLUME.md``). Assert that a ``FACE_H`` that
    reaches that ratio is (a) above today's value and (b) still under the
    interpenetration ceiling, so the recommendation can never be a value that
    would merge the floors.
    """
    rc, tc = renderer_consts, tower_consts
    target_ratio = 0.25
    plate_depth_world = (tc["ROOM_W"] + tc["ROOM_H"]) * (rc["TILE_H"] / 2)
    recommended = round(target_ratio * plate_depth_world)   # ~40 for 12x7
    ceiling = tc["FLOOR_HEIGHT_UNITS"] * rc["DECK_Z"]

    print(f"\n  plate depth = {plate_depth_world:.1f} world units")
    print(f"  to reach ratio {target_ratio}: FACE_H ~= {recommended}")
    print(f"  ceiling = {ceiling:.0f} -> "
          f"{'LEGAL' if recommended <= ceiling else 'ILLEGAL'}")
    assert recommended > rc["FACE_H"], "the recommendation must be taller"
    assert recommended <= ceiling, (
        f"recommended FACE_H {recommended} exceeds ceiling {ceiling}")
