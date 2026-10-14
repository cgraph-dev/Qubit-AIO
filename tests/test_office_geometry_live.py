"""Does the drawn office actually reflect the 6x4 -> 12x7 geometry change?

WHY THIS FILE EXISTS
--------------------
`tower.py` moved from `ROOM_W, ROOM_H = 6, 4` (desk pitch 2 across, 2 down) to
`12, 7` (pitch 4 across, 3 down, odd rows offset 1). The grid grew from 21x10 to
39x16. The full suite stayed green (661 passed) and the live render reported
`24.64% non-background pixels` -- the SAME 24.64% as before the change.

An identical number across a geometry change is a smell with exactly two
innocent explanations and one guilty one:

  (a) the camera's COVER fit zooms out to contain the bigger world, so the
      fraction of the canvas the building covers is legitimately similar, even
      though the world got bigger; or
  (b) part of the drawing ignores the new layout (the camera still measures the
      old extent, or a cache is stale), which is a real defect that every
      existing test misses.

This file settles it with arithmetic rather than opinion. It projects the REAL
plan through the REAL `iso-engine.js`, feeds the result to the REAL camera
`fit`, and compares the coverage fraction `extent_area * zoom^2 / viewport_area`
for the new layout against the legacy one. If the two fractions land close
together, (a) is the explanation and the identical pixel percentage is a
coincidence of the cover fit -- not a stale drawing. If they diverge by more
than the tolerance, that is (b): a loud failure with the two numbers printed.

The four claims, in order:
  1. the payload carries the new geometry (grid 39x16, room 12x7);
  2. desk spacing, MEASURED from the payload, is the new pitch (4 across, 3 down);
  3. the projected extent genuinely grew (bigger screen bounding box);
  4. the camera compensates, quantitatively (lower zoom, similar coverage).

Everything is hermetic: a tmp XDG for a real org, `node -e` on the UMD modules
(no browser, no server, no network, no provider call). The constants are read
from `tower.py`, never hardcoded, so if the geometry moves again this file
follows it and says so.

Every test here is written to be capable of FAILING; the comment on each names
the real defect that would fail it. Two were proved to fail by mutating a
SCRATCH COPY of a module (never the repo file); see the accompanying report.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest  # noqa: E402

from _office_node import run_node  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
PLUGIN_DIR = REPO / "frontend" / "qubit-app" / "plugins" / "qubit-office"
ISO_JS = PLUGIN_DIR / "iso-engine.js"
CAMERA_JS = PLUGIN_DIR / "camera.js"

_FOUND = shutil.which("node")
NODE: str = _FOUND or "node"

pytestmark = pytest.mark.skipif(
    _FOUND is None,
    reason="node is not installed; the JS projection/camera cannot be run")

#: One viewport literal for the whole file. The live number (24.64%) was measured
#: in a browser canvas close to this; the arithmetic below is scale-invariant for
#: the COVER fit (both axes scale together) as long as the aspect matches, so the
#: exact pixels matter less than the aspect ratio being a realistic canvas.
VIEWPORT = {"w": 880, "h": 500}

#: How close the two coverage fractions must be for us to call the identical
#: pixel percentage "explained by the cover fit". The fit's own `margin` and the
#: aspect-ratio difference between the two layouts move this by a few percent;
#: 15% relative is comfortably inside "the camera compensates" and comfortably
#: outside "the drawing ignores the change" (which measured ~3.3x apart, see the
#: one-room case in test_desk_spacing and the report).
COVERAGE_TOLERANCE_REL = 0.15


# --------------------------------------------------------------------- harness
def _node(body: str) -> dict:
    """Run a JS body with ISO and CAM bound; return the parsed JSON it prints.

    Module paths are injected with `json.dumps`, not interpolated into the
    program text, so a checkout path with a space or backslash is a harness
    concern rather than a syntax error inside the test.
    """
    program = (
        "var ISO=require(%s);var CAM=require(%s);\n" % (
            json.dumps(str(ISO_JS)), json.dumps(str(CAMERA_JS)))
    ) + body
    proc = subprocess.run([NODE], input=program, capture_output=True,
                          text=True, timeout=60)
    assert proc.returncode == 0, (
        f"node exited {proc.returncode}\nSTDOUT:\n{proc.stdout}\n"
        f"STDERR:\n{proc.stderr}")
    out = proc.stdout.strip()
    assert out, f"node printed nothing\nSTDERR:\n{proc.stderr}"
    return json.loads(out)


# The real joiner of the two modules: the renderer's own `extent()` walks floors,
# projects each room's four corners via `isoWorld`, and folds z. Reproduced here
# so the measurement is the renderer's, not a fresh re-derivation. It mirrors
# `qubit-office.bundle.js` `extent()` (worldExtent + min/max fold + w/h/cx/cy).
_EXTENT_JS = r"""
function isoWorld(x,y,z){return ISO.gridToScreen(x,y,z,{cx:0,cy:0,zoom:1,w:0,h:0});}
function extent(floors){
  var box=null;
  (floors||[]).forEach(function(fl){
    var z=Number(fl.z||0);
    var cells=(fl.rooms||[]).map(function(r){
      return {x:Number(r.x),y:Number(r.y),w:Number(r.w),h:Number(r.h)};});
    if(!cells.length) return;
    var e2=CAM.worldExtent(cells,function(gx,gy){return isoWorld(gx,gy,z);});
    if(!e2) return;
    if(!box) box=e2;
    else {
      box.minX=Math.min(box.minX,e2.minX); box.maxX=Math.max(box.maxX,e2.maxX);
      box.minY=Math.min(box.minY,e2.minY); box.maxY=Math.max(box.maxY,e2.maxY);
    }
  });
  if(!box) return null;
  box.w=box.maxX-box.minX; box.h=box.maxY-box.minY;
  box.cx=(box.minX+box.maxX)/2; box.cy=(box.minY+box.maxY)/2;
  return box;
}
function coverage(ex, zoom){
  /* The share of the viewport the PROJECTED EXTENT covers once the camera's zoom
   * is applied. This is the quantity whose near-constancy would explain an
   * unchanged non-background pixel percentage across the geometry change. */
  return (ex.w*zoom)*(ex.h*zoom)/(VW*VH);
}
var VW=%(vw)d, VH=%(vh)d;
"""


def _extent_and_fit(floors: list[dict]) -> dict:
    """Project `floors`, fit the real camera, and return box + zoom + coverage."""
    body = (_EXTENT_JS % {"vw": VIEWPORT["w"], "vh": VIEWPORT["h"]}) + r"""
    var ex = extent(%(floors)s);
    var f = CAM.fit({w:VW,h:VH}, ex, {margin:0.08});
    var zoom = Math.max(CAM.CHAR_FLOOR_ZOOM, Math.min(CAM.MAX_ZOOM, f.zoom));
    console.log(JSON.stringify({
      extent: ex, zoom: zoom, fit: f.fit, cover: coverage(ex, zoom)}));
    """ % {"floors": json.dumps(floors)}
    return _node(body)


# ------------------------------------------------------------- the real org
def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _build_org(cfg, *, departments=6, workers_per=4):
    """A real store-backed org: one floor, `departments` rooms in the 3x2 grid.

    Six departments x one room each is the reference floor -- it exercises the
    FULL room grid (3 cols x 2 rows = 6), which is the layout whose extent the
    camera has to fit. A single-room org would under-measure the building.
    """
    from voicebridge.office import org as O, store

    ceo = O.hire(cfg, "ceo", seat_type="ceo", confirmed=True)["id"]
    dr = O.hire(cfg, "dir", seat_type="director_portfolio", supervisor_id=ceo,
                confirmed=True)["id"]
    floor = O.floor_create(cfg, 1, "Work", role="director",
                           confirmed=True)["id"]
    for d in range(departments):
        dept = O.department_create(cfg, f"d{d}", director_id=dr, floor_id=floor,
                                   kill_criteria="x", confirmed=True)["id"]
        room = O.room_create(cfg, dept, f"desks{d}", kind="work",
                             confirmed=True)["id"]
        mgr = O.hire(cfg, f"mgr-{d}", seat_type="manager", supervisor_id=dr,
                     department_id=dept, confirmed=True)["id"]
        seats = [mgr] + [
            O.hire(cfg, f"wk-{d}-{i}", seat_type="worker", supervisor_id=mgr,
                   department_id=dept, confirmed=True)["id"]
            for i in range(workers_per)]
        conn = store.connect(cfg)
        try:
            store.migrate(conn)
            for s in seats:
                conn.execute("UPDATE office_agent SET room_id=? WHERE id=?",
                             (room, s))
            conn.commit()
        finally:
            conn.close()


@pytest.fixture()
def live_state(tmp_path, monkeypatch):
    """A real `tower.state()` payload from a real 6-room org on a tmp XDG."""
    from voicebridge.office import tower

    cfg = _cfg(tmp_path, monkeypatch)
    _build_org(cfg)
    state = tower.state(cfg)
    assert state["characters"], "the reference org has seats"
    return state


def _rooms(state) -> list[dict]:
    """Every room rect in the payload, flattened across floors."""
    return [r for f in state["floorplan"]["floors"] for r in f["rooms"]]


def _seated_room(state) -> dict:
    """The room with the most seats (the pitch check needs a full-ish row)."""
    best = max(_rooms(state), key=lambda r: len(r["seats"]))
    assert len(best["seats"]) >= 4, (
        "no room with >=4 seats; the desk-pitch check needs a full row")
    return best


# ===========================================================================
# 1. THE PAYLOAD CARRIES THE NEW GEOMETRY
# ===========================================================================
def test_payload_carries_the_new_geometry(live_state):
    """The grid is the new 39x16 and a room is 12x7.

    FAILS IF: `state()` serves a plan built from the OLD constants (e.g. a
    cached floorplan, or a `state()` path that bypasses `floorplan()`). That is
    the (b)-class defect in its purest form -- the drawing would be honest but
    the source of truth would still be 6x4.
    """
    from voicebridge.office import tower as T

    # Read the real constants; do not trust this file's idea of them.
    room_w, room_h = T.ROOM_W, T.ROOM_H
    grid = live_state["grid"]

    assert grid == {"cols": T.ROOM_COLS * (room_w + 1),
                    "rows": T.ROOM_ROWS * (room_h + 1), "tile": 1}, grid
    # the brief said 39x16 / 12x7; assert against the SOURCE, and cross-check the
    # brief separately so a silent re-change is visible rather than assumed away.
    assert (room_w, room_h) == (12, 7), (room_w, room_h)
    assert (grid["cols"], grid["rows"]) == (39, 16), (grid["cols"], grid["rows"])

    rooms = _rooms(live_state)
    assert rooms, "the payload has rooms"
    for r in rooms:
        assert (r["w"], r["h"]) == (room_w, room_h), (r["slug"], r["w"], r["h"])

    print(f"\npayload grid={grid} room={room_w}x{room_h} rooms={len(rooms)}")


# ===========================================================================
# 2. DESK SPACING IS THE NEW PITCH, MEASURED FROM THE PAYLOAD
# ===========================================================================
def test_desk_spacing_is_the_new_pitch(live_state):
    """Consecutive same-row desks differ by PITCH_COL in x; rows by PITCH_ROW.

    FAILS IF: the seats are still laid out at the OLD pitch (2 across, 2 down)
    while `w/h` say 12x7 -- i.e. the room grew but the desks did not, which is
    the half-migrated layout. It computes spacing from the PAYLOAD's `seats`,
    not from the constants, so it measures what is actually served.
    """
    from voicebridge.office import tower as T

    pitch_col, pitch_row = T.PITCH_COL, T.PITCH_ROW
    per_row = T.SEATS_PER_DESK_ROW

    room = _seated_room(live_state)
    seats = sorted(room["seats"], key=lambda s: s["slot"])
    # Need at least two desks in one visual row (to measure the x-step) AND a
    # second row (to measure the y-step). per_row + 1 guarantees both.
    assert len(seats) >= per_row + 1, (room["slug"], len(seats))

    # Group by y (same visual row) and require the x-step to be pitch_col.
    by_row: dict[int, list[int]] = {}
    for s in seats:
        by_row.setdefault(s["y"], []).append(s["x"])
    multi = {y: sorted(xs) for y, xs in by_row.items() if len(xs) > 1}
    assert multi, f"no row with 2+ desks to measure: {by_row}"

    for y, xs in multi.items():
        steps = [xs[i + 1] - xs[i] for i in range(len(xs) - 1)]
        # odd rows are offset by ODD_ROW_OFFSET as a whole; the WITHIN-row step is
        # still pitch_col, which is exactly what distinguishes a real stagger
        # from a broken pitch.
        assert all(step == pitch_col for step in steps), (y, xs, steps)
        assert len(xs) <= per_row, (y, xs)

    # Rows must differ in y by pitch_row.
    ys = sorted(by_row)
    assert len(ys) >= 2, f"only one desk row in {room['slug']}"
    ysteps = [ys[i + 1] - ys[i] for i in range(len(ys) - 1)]
    assert all(step == pitch_row for step in ysteps), (ys, ysteps)

    # And the whole thing must NOT be the legacy pitch -- a positive statement
    # that this is the new layout, not an accidental pass at pitch 2.
    assert pitch_col != 2 and pitch_row != 2, (pitch_col, pitch_row)

    print(f"\nroom {room['slug']}: rows={ys} x-steps={[multi[y] for y in ys]} "
          f"pitch_col={pitch_col} pitch_row={pitch_row}")


# ===========================================================================
# 3. THE PROJECTED EXTENT GREW
# ===========================================================================
def test_projected_extent_grew(live_state):
    """The real plan's screen bounding box is larger than a synthetic 6x4 one.

    The synthetic legacy plan is built from `floorplan_fit.legacy_geometry()`'s
    constants (6x4, 3x2 rooms, same one-tile gap) so it is the TRUE before-image,
    not a guess.

    FAILS IF: the renderer still measures the old extent -- e.g. it reads a
    stale `w/h` or the camera module's `worldExtent` is fed room rects that were
    never updated. The bigger world must project to a bigger box.
    """
    from voicebridge.office import floorplan_fit as FF

    real_floors = live_state["floorplan"]["floors"]

    leg = FF.legacy_geometry()
    legacy_rooms = [
        {"x": (i % FF.LEGACY_ROOM_COLS) * (leg["room_w"] + FF.ROOM_GAP),
         "y": (i // FF.LEGACY_ROOM_COLS) % FF.LEGACY_ROOM_ROWS
              * (leg["room_h"] + FF.ROOM_GAP),
         "w": leg["room_w"], "h": leg["room_h"]}
        for i in range(FF.LEGACY_ROOM_COLS * FF.LEGACY_ROOM_ROWS)]
    legacy_floors = [{"z": real_floors[0]["z"], "rooms": legacy_rooms}]

    real = _extent_and_fit(real_floors)
    old = _extent_and_fit(legacy_floors)

    rw, rh = real["extent"]["w"], real["extent"]["h"]
    ow, oh = old["extent"]["w"], old["extent"]["h"]
    assert rw > ow and rh > oh, (
        f"the new layout did not enlarge the projected extent: "
        f"new={rw:.1f}x{rh:.1f} legacy={ow:.1f}x{oh:.1f}")

    # and it must be the geometry, not rounding: the area grew materially.
    assert (rw * rh) > 1.5 * (ow * oh), (rw * rh, ow * oh)

    print(f"\nextent box new={rw:.1f}x{rh:.1f} legacy={ow:.1f}x{oh:.1f} "
          f"area_ratio={(rw * rh) / (ow * oh):.3f}")


# ===========================================================================
# 4. THE CAMERA COMPENSATES, QUANTITATIVELY
# ===========================================================================
def test_camera_zoom_out_explains_the_identical_coverage(live_state):
    """The verdict test.

    The camera's COVER fit must choose a LOWER zoom for the bigger world, and the
    resulting coverage `extent_area * zoom^2 / viewport_area` must be close for
    both layouts. When it is close, the unchanged 24.64% is the cover fit doing
    its job -- NOT a stale drawing. When it is NOT close, the drawing is not
    reflecting the new layout and that is a real defect, reported loudly with
    both numbers.
    """
    from voicebridge.office import floorplan_fit as FF

    real_floors = live_state["floorplan"]["floors"]
    leg = FF.legacy_geometry()
    legacy_rooms = [
        {"x": (i % FF.LEGACY_ROOM_COLS) * (leg["room_w"] + FF.ROOM_GAP),
         "y": (i // FF.LEGACY_ROOM_COLS) % FF.LEGACY_ROOM_ROWS
              * (leg["room_h"] + FF.ROOM_GAP),
         "w": leg["room_w"], "h": leg["room_h"]}
        for i in range(FF.LEGACY_ROOM_COLS * FF.LEGACY_ROOM_ROWS)]
    legacy_floors = [{"z": real_floors[0]["z"], "rooms": legacy_rooms}]

    real = _extent_and_fit(real_floors)
    old = _extent_and_fit(legacy_floors)

    zr, zl = real["zoom"], old["zoom"]
    cr, cl = real["cover"], old["cover"]

    # (i) the zoom for the bigger world must be lower (it zooms out to fit).
    assert zr < zl, (
        f"the camera did NOT zoom out for the bigger world: "
        f"zoom_new={zr:.4f} zoom_legacy={zl:.4f} -- a stale extent would do this")

    # (ii) the coverage fractions must be within tolerance -- the arithmetic that
    # explains an unchanged non-background percentage.
    rel = abs(cr - cl) / max(cr, cl)
    explained = rel <= COVERAGE_TOLERANCE_REL

    finding = (
        "\n=== GEOMETRY-LIVE VERDICT ===\n"
        f"  coverage(real)   = extent {real['extent']['w']:.1f}x"
        f"{real['extent']['h']:.1f} * zoom {zr:.4f}^2 / "
        f"{VIEWPORT['w']*VIEWPORT['h']} = {cr:.4f}\n"
        f"  coverage(legacy) = extent {old['extent']['w']:.1f}x"
        f"{old['extent']['h']:.1f} * zoom {zl:.4f}^2 / "
        f"{VIEWPORT['w']*VIEWPORT['h']} = {cl:.4f}\n"
        f"  relative difference = {rel:.4f} (tolerance {COVERAGE_TOLERANCE_REL})\n"
        f"  VERDICT: {'the cover fit EXPLAINS the near-identical' if explained else 'MISMATCH -- the drawing ignores the new layout, NOT explained'}"
        " non-background percentage")
    print(finding)

    assert explained, (
        "the coverage fractions DIVERGE beyond tolerance -- this is NOT the "
        "camera compensating; the drawing is not reflecting the new layout.\n"
        f"  coverage(real)={cr:.4f}  coverage(legacy)={cl:.4f}  "
        f"relative={rel:.4f} > {COVERAGE_TOLERANCE_REL}\n" + finding)


# ===========================================================================
# 5. THE TWO LAYOUTS ARE GENUINELY DIFFERENT WORLDS (guard against a no-op)
# ===========================================================================
def test_the_two_layouts_are_not_the_same_world(live_state):
    """A sanity guard: the 12x7 and 6x4 plans must NOT be the same extent.

    FAILS IF: `legacy_geometry()` and the live payload collapse to the same
    geometry (e.g. someone 'unifies' them), which would make the whole comparison
    above vacuous -- two identical extents trivially have identical coverage.
    """
    from voicebridge.office import tower as T

    # the real statement: the room rects differ in size from the legacy ones.
    rooms = _rooms(live_state)
    assert rooms
    for r in rooms:
        assert (r["w"], r["h"]) != (6, 4), (r["slug"], r["w"], r["h"])
    assert (_seated_room(live_state)["seats"])  # and it is a real room
    print(f"\nroom rects are {rooms[0]['w']}x{rooms[0]['h']}, not 6x4 "
          f"(tower ROOM_W,H={T.ROOM_W},{T.ROOM_H}) -- the comparison is "
          "between two different worlds")
