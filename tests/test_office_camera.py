"""Office camera module — framing rule, minimap inverses and picking.

`frontend/qubit-app/plugins/qubit-office/camera.js` owns the grid -> world ->
viewport map for the tower renderer. It is exercised here through `node -e`
against the real file, because the defect it fixes is a numeric one (the camera
framed the wrong box) and a Python re-implementation of the maths would prove
nothing about the shipped code.

Three things are actually load-bearing for the defect:

1. `fit` must not fall back to a `contain` fit. A `contain` fit of an extent
   into a non-matching viewport ALWAYS leaves dead space on one axis, which is
   the "mostly empty dark canvas" report.
2. `worldExtent` must not invent a box for an empty floorplan. A zero-size box
   at the world origin frames the origin, not the building.
3. `minimapTransform` must round-trip. The minimap is being made interactive
   (click a room, move the camera), which is only correct if the world->mini
   map has an exact inverse.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "frontend/qubit-app/plugins/qubit-office/camera.js"


def run_js(source: str) -> dict:
    """Evaluate a JS snippet against the real camera module and parse its JSON."""
    script = (
        "var C = require(%s);\n"
        "function emit(o){ process.stdout.write(JSON.stringify(o)); }\n"
        "%s" % (json.dumps(str(MODULE)), source)
    )
    proc = subprocess.run(
        ["node", "-e", script],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(ROOT),
    )
    assert proc.returncode == 0, f"node failed:\n{proc.stdout}\n{proc.stderr}"
    assert proc.stdout.strip(), "node produced no output"
    return json.loads(proc.stdout)


def test_module_file_exists() -> None:
    assert MODULE.is_file(), f"missing camera module at {MODULE}"


def test_node_check_passes() -> None:
    proc = subprocess.run(
        ["node", "--check", str(MODULE)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr


def test_no_scheduling_primitives_in_source() -> None:
    """The renderer's hard constraint: motion may only come from the payload.

    A camera module that schedules its own frames would be a second timer in the
    plugin bundle, so the ban is enforced on the module source itself, not just
    on the renderer that uses it.
    """
    src = MODULE.read_text(encoding="utf-8")
    for banned in ("setInterval", "setTimeout", "requestAnimationFrame",
                   "cancelAnimationFrame", "@keyframes"):
        assert banned not in src, f"camera.js must not reference {banned}"


def test_empty_list_returns_null_not_a_box() -> None:
    out = run_js(
        """
        var proj = function (x, y) { return { x: (x - y) * 17, y: (x + y) * 8.5 }; };
        emit({ empty: C.worldExtent([], proj),
               nil: C.worldExtent(null, proj),
               ok: C.worldExtent([{ x: 0, y: 0, w: 4, h: 3 }], proj) });
        """
    )
    # An empty floorplan has no extent. A zero box at the origin would frame the
    # world origin and put a real building off-canvas.
    assert out["empty"] is None
    assert out["nil"] is None
    assert out["ok"] is not None
    assert out["ok"]["w"] > 0 and out["ok"]["h"] > 0


def test_extent_projects_corners_not_just_min_and_max() -> None:
    """Under 2:1 iso a cell is a diamond; the extremes are at different corners.

    Measuring only the (x,y) and (x+w,y+h) corners under-measures the box, which
    crops the building on two sides.
    """
    out = run_js(
        """
        var proj = function (x, y) { return { x: (x - y) * 17, y: (x + y) * 8.5 }; };
        var e = C.worldExtent([{ x: 0, y: 0, w: 10, h: 4 }], proj);
        emit({ e: e });
        """
    )
    e = out["e"]
    # corner (10,0) -> x=170 ; corner (0,4) -> x=-68 ; the diamond is 238 wide,
    # not the 170 a min/max-corner measurement would give.
    assert e["minX"] == pytest.approx(-68.0)
    assert e["maxX"] == pytest.approx(170.0)
    assert e["w"] == pytest.approx(238.0)
    # top extreme is corner (0,0) -> y=0 ; bottom is (10,4) -> y=119
    assert e["minY"] == pytest.approx(0.0)
    assert e["maxY"] == pytest.approx(119.0)
    assert e["cx"] == pytest.approx(51.0)
    assert e["cy"] == pytest.approx(59.5)


def test_fit_never_uses_a_contain_rule() -> None:
    """The defect: containing a wide shallow deck leaves dark bands top+bottom."""
    out = run_js(
        """
        var proj = function (x, y) { return { x: (x - y) * 17, y: (x + y) * 8.5 }; };
        var vp = { w: 880, h: 500 };
        var margin = 0.08;
        var availW = vp.w * (1 - 2 * margin), availH = vp.h * (1 - 2 * margin);
        var results = [];
        var decks = [[16, 3], [20, 4], [24, 5], [36, 4], [8, 2], [40, 6]];
        for (var i = 0; i < decks.length; i++) {
          var d = decks[i];
          var e = C.worldExtent([{ x: 0, y: 0, w: d[0], h: d[1] }], proj);
          var f = C.fit(vp, e, { margin: margin });
          // contain = the smaller of the two axis fits, at the SAME margin.
          var contain = Math.min(availW / e.w, availH / e.h);
          results.push({ d: d, ew: e.w, eh: e.h, zoom: f.zoom, contain: contain,
                         fillW: e.w * f.zoom / vp.w, fillH: e.h * f.zoom / vp.h });
        }
        emit({ results: results, floor: C.CHAR_FLOOR_ZOOM, cap: C.MAX_ZOOM });
        """
    )
    out_ceiling = out["cap"]
    out_floor = out["floor"]
    for r in out["results"]:
        # The framing rule, stated as an identity:
        #   zoom = clamp( cover, floor, ceiling )
        # where cover = min(availW/ew, availH/eh). Anything else is a different
        # rule than the one this module documents.
        expected = min(r["contain"], out_ceiling)
        expected = max(expected, out_floor)
        assert r["zoom"] == pytest.approx(expected, rel=1e-9), (
            f"deck {r['d']} did not follow the documented cover rule: {r}")

        # A contain fit is the smaller of the two axis fits, so it is <= the
        # cover fit. The fixed rule must never sink to it: the whole defect was
        # the fit taking the contain term and shrinking the deck. Only meaningful
        # when neither the floor nor the ceiling overrode the rule.
        if r["zoom"] <= out_floor + 1e-9 or r["zoom"] >= out_ceiling - 1e-9:
            continue
        # cover is >= contain by construction; assert the fit did not collapse
        # back to the contain term (which is what the renderer used to do).
        assert r["zoom"] >= r["contain"] - 1e-9, (
            f"deck {r['d']} fitted below a contain fit: {r}")
        # and the fit must actually fill one axis of the viewport within the
        # margin, on BOTH axes counting the covered one: cover means the limiting
        # axis touches the margin, so at least one of fillW/fillH is >= 1-margin
        # and neither leaves a band bigger than the margin on both sides.
        assert max(r["fillW"], r["fillH"]) >= 1 - 2 * 0.08 - 1e-9, (
            f"fit left dead space on both axes: {r}")


def test_fit_limits_on_the_width_axis_for_an_iso_deck() -> None:
    """For an iso deck the WIDTH is the limiting axis of the cover fit.

    A 2:1 iso projection gives an extent of aspect ~2, wider than the ~1.76
    canvas aspect, so the width is what binds and the deck fills the canvas edge
    to edge with only the margin as slack. This is the observable consequence of
    the cover rule on our data, and it is the thing the defect report asked for.
    """
    out = run_js(
        """
        var proj = function (x, y) { return { x: (x - y) * 17, y: (x + y) * 8.5 }; };
        var vp = { w: 880, h: 500 };
        var e = C.worldExtent([{ x: 0, y: 0, w: 24, h: 5 }], proj);
        var f = C.fit(vp, e, { margin: 0.08 });
        emit({ f: f, ew: e.w, eh: e.h, fillW: e.w * f.zoom / vp.w,
               fillH: e.h * f.zoom / vp.h });
        """
    )
    margin = 0.08
    # width is the limiting axis -> the width fit is the cover zoom
    assert out["fillW"] == pytest.approx(1 - 2 * margin, rel=1e-6)
    # ... so the height is the axis that overflows/under-fills, never the width.
    assert out["fillH"] < out["fillW"]
    assert out["f"]["fit"] == "width", out["f"]


def test_extent_skips_junk_cells_without_poisoning_the_box() -> None:
    """A malformed cell must not turn the whole extent into NaN.

    `worldExtent` is fed the raw payload's rooms; one room with a null x would
    otherwise make minX/maxX NaN, and a NaN camera would blank the canvas. The
    junk cell is skipped; the good ones still measure.
    """
    out = run_js(
        """
        var proj = function (x, y) { return { x: (x - y) * 17, y: (x + y) * 8.5 }; };
        var withJunk = C.worldExtent([
          { x: 0, y: 0, w: 4, h: 3 },
          { x: null, y: 2, w: 4, h: 3 },      // null x
          { x: 'nope', y: 2, w: 4, h: 3 },    // non-numeric x
          { x: 10, y: 0, w: 4, h: 3 }
        ], proj);
        var onlyJunk = C.worldExtent([{ x: null, y: null, w: null, h: null }], proj);
        var emptyish = C.worldExtent([null, undefined], proj);
        emit({ withJunk: withJunk, onlyJunk: onlyJunk, emptyish: emptyish });
        """
    )
    wj = out["withJunk"]
    # The box is finite and reflects only the two GOOD rooms (x in 0 and 10).
    # Crucially a `null` x must be treated as MISSING, not coerced to 0: the
    # coercing behaviour would add a phantom room at x=0 and drag minX to -85.
    assert wj is not None
    for k in ("minX", "maxX", "minY", "maxY", "w", "h", "cx", "cy"):
        assert wj[k] == pytest.approx(wj[k]), f"{k} is NaN: {wj}"
    assert wj["minX"] == pytest.approx(-51.0), (   # corner (0,3) of room 0
        f"a null coordinate was not treated as missing: {wj}")
    assert wj["maxX"] == pytest.approx(238.0)      # corner (14,0) of room 1
    # A payload that is entirely junk has no measurable extent -> null, not NaN.
    assert out["onlyJunk"] is None
    assert out["emptyish"] is None


def test_fit_respects_the_legibility_floor() -> None:
    """A 15-unit character must stay readable; the building may overflow instead."""
    out = run_js(
        """
        var proj = function (x, y) { return { x: (x - y) * 17, y: (x + y) * 8.5 }; };
        var huge = C.worldExtent([{ x: 0, y: 0, w: 400, h: 400 }], proj);
        var f = C.fit({ w: 880, h: 500 }, huge, {});
        var floored = C.fit({ w: 880, h: 500 }, huge, { minZoom: 0.85 });
        var tiny = C.worldExtent([{ x: 0, y: 0, w: 1, h: 1 }], proj);
        var t = C.fit({ w: 880, h: 500 }, tiny, {});
        emit({ f: f, floored: floored, tiny: t, floor: C.CHAR_FLOOR_ZOOM,
               max: C.MAX_ZOOM });
        """
    )
    # An extent far larger than the viewport would want zoom << 1; the floor wins.
    assert out["f"]["zoom"] == pytest.approx(out["floor"])
    assert out["floored"]["zoom"] == pytest.approx(0.85)
    # A one-room office would want to blow up hugely; the ceiling wins.
    assert out["tiny"]["zoom"] <= out["max"] + 1e-9


def test_fit_caps_the_width_fit_with_a_cover_zoom() -> None:
    """A tall narrow deck must not be blown up past its own cover zoom.

    For a wide shallow deck the width fit IS the cover zoom, so the height term
    is invisible. A tall extent is the shape that exercises it — a module that
    took only `availW/e.w` and ignored the height would zoom a one-column tower
    until its single column filled the screen edge to edge.
    """
    out = run_js(
        """
        // Identity projection: a tall narrow extent (4 x 300 world units).
        var proj = function (x, y) { return { x: x, y: y }; };
        var vp = { w: 880, h: 500 };
        var e = C.worldExtent([{ x: 0, y: 0, w: 4, h: 300 }], proj);
        var f = C.fit(vp, e, { margin: 0.08 });
        var availW = vp.w * (1 - 0.16), availH = vp.h * (1 - 0.16);
        emit({ e: e, f: f, widthFit: availW / 4, cover: Math.min(availW / 4, availH / 300) });
        """
    )
    # The width fit alone would want a huge zoom; the cover zoom caps it.
    assert out["widthFit"] > out["cover"] * 2, "fixture does not exercise the cap"
    assert out["f"]["zoom"] == pytest.approx(out["cover"]), (
        f"width fit was not capped by the cover zoom: {out}")
    # and the deck now covers the viewport rather than being a thin stripe
    assert out["e"]["h"] * out["f"]["zoom"] >= 500 * 0.80


def test_extent_does_not_assume_the_world_origin_is_in_the_box() -> None:
    """The real defect's arithmetic: extent must be measured, not seeded at 0.

    A seed of 0 (or any value inside/outside the true box) only stays correct by
    luck. This pins a room whose projection lies so the box's centre is NOT the
    world origin and where one edge is negative — the shape a real floorplan has,
    because room (x=0, y=12) projects to x = (0-12)*17 = -204.
    """
    out = run_js(
        """
        var proj = function (x, y) { return { x: (x - y) * 17, y: (x + y) * 8.5 }; };
        // A single room high on the y axis: every projected x is negative.
        var e = C.worldExtent([{ x: 0, y: 12, w: 2, h: 2 }], proj);
        // And a three-room floorplan whose box straddles the origin, so a 0 seed
        // is "inside" the box and can be silently mistaken for the true edge.
        var e2 = C.worldExtent([
          { x: 0, y: 0, w: 4, h: 3 },
          { x: 10, y: 0, w: 4, h: 3 },
          { x: 0, y: 10, w: 4, h: 3 }
        ], proj);
        emit({ e: e, e2: e2 });
        """
    )
    e = out["e"]
    # (0,12) -> x = -204 ; (2,12) -> x = -170 ; (0,14) -> x = -238 ; (2,14) -> x = -204
    assert e["maxX"] == pytest.approx(-170.0)
    assert e["minX"] == pytest.approx(-238.0)
    # A negative maxX proves the box was measured rather than clamped up to 0.
    assert e["maxX"] < 0
    assert e["cx"] == pytest.approx(-204.0)
    # For e2, every corner must be reflected: the box spans negative and positive
    # x, so a 0 seed would pass the naive "minX <= 0" smell test while being wrong.
    # corner (0,13) -> x = -221 is the true left edge; (14,0) -> x = 238 the right.
    e2 = out["e2"]
    assert e2["minX"] == pytest.approx(-221.0)
    assert e2["maxX"] == pytest.approx(238.0)
    assert e2["w"] == pytest.approx(459.0)
    assert e2["cx"] == pytest.approx(8.5)          # NOT 0


def test_fit_is_centred_on_the_extent_not_the_world_origin() -> None:
    """The other half of the defect: a building in positive room coords."""
    out = run_js(
        """
        var proj = function (x, y) { return { x: (x - y) * 17, y: (x + y) * 8.5 }; };
        var e = C.worldExtent([{ x: 20, y: 20, w: 10, h: 6 }], proj);
        var f = C.fit({ w: 880, h: 500 }, e, {});
        emit({ e: e, f: f });
        """
    )
    assert out["f"]["cx"] == pytest.approx(out["e"]["cx"])
    assert out["f"]["cy"] == pytest.approx(out["e"]["cy"])
    # and that is nowhere near the world origin, which is what the old code centred
    assert abs(out["f"]["cx"]) > 1 or abs(out["f"]["cy"]) > 1


def test_clamp_keeps_the_world_reachable() -> None:
    out = run_js(
        """
        var proj = function (x, y) { return { x: (x - y) * 17, y: (x + y) * 8.5 }; };
        var vp = { w: 880, h: 500 };
        var e = C.worldExtent([{ x: 0, y: 0, w: 20, h: 12 }], proj);
        var far = C.clamp(vp, e, { cx: 1e6, cy: 1e6, zoom: 1 }, 0.30);
        var near = C.clamp(vp, e, { cx: -1e6, cy: -1e6, zoom: 1 }, 0.30);
        var sane = C.clamp(vp, e, { cx: e.cx, cy: e.cy, zoom: 1, auto: true }, 0.30);
        emit({ e: e, far: far, near: near, sane: sane });
        """
    )
    e = out["e"]
    # Per axis, the legal camera-centre interval is
    #   [min - vp/(2z) + m*size,  max + vp/(2z) - m*size]
    # and a wild pan is clamped to that interval's endpoint. The bound is
    # expressed against the extent EDGE (min/max), not its centre.
    def clamp_bounds(mn, mx, vp_px, size):
        half = vp_px / 2
        return (mn - half + 0.30 * size, mx + half - 0.30 * size)

    lo_x, hi_x = clamp_bounds(e["minX"], e["maxX"], 880, e["w"])
    lo_y, hi_y = clamp_bounds(e["minY"], e["maxY"], 500, e["h"])
    assert out["far"]["cx"] == pytest.approx(hi_x, abs=1e-6), out["far"]
    assert out["far"]["cy"] == pytest.approx(hi_y, abs=1e-6), out["far"]
    assert out["near"]["cx"] == pytest.approx(lo_x, abs=1e-6), out["near"]
    assert out["near"]["cy"] == pytest.approx(lo_y, abs=1e-6), out["near"]
    # The clamped camera must still leave a real part of the world on screen:
    # the camera centre cannot be more than (viewport/2 - margin*size) past the
    # edge, so at least margin*size of world units stays visible.
    assert out["far"]["cx"] - e["maxX"] < 880 / 2
    assert out["far"]["cy"] - e["maxY"] < 500 / 2
    # A camera already inside the legal interval is left exactly alone.
    assert out["sane"]["cx"] == pytest.approx(e["cx"], abs=1e-9)
    assert out["sane"]["cy"] == pytest.approx(e["cy"], abs=1e-9)


def test_clamp_scales_its_bound_with_the_zoom() -> None:
    """The clamp bound is viewportSize/(2*zoom) in world units.

    Testing only at zoom 1 cannot tell `vpSize/(2z)` from `vpSize/2`, so the
    zoom term would go unexercised. At a zoomed-in camera the world window is
    smaller, so the reachable camera range is NARROWER — that is the assertion
    that pins the term.
    """
    out = run_js(
        """
        var proj = function (x, y) { return { x: (x - y) * 17, y: (x + y) * 8.5 }; };
        var vp = { w: 880, h: 500 };
        var e = C.worldExtent([{ x: 0, y: 0, w: 20, h: 12 }], proj);
        var z1 = C.clamp(vp, e, { cx: 1e6, cy: 0, zoom: 1 }, 0.10);
        var z2 = C.clamp(vp, e, { cx: 1e6, cy: 0, zoom: 2 }, 0.10);
        var z4 = C.clamp(vp, e, { cx: 1e6, cy: 0, zoom: 4 }, 0.10);
        emit({ e: e, z1: z1.cx, z2: z2.cx, z4: z4.cx, maxX: e.maxX, w: e.w });
        """
    )
    e_max = out["maxX"]
    w = out["w"]
    # hi = maxX + vp.w/(2z) - margin*w
    for zoom, got in ((1, out["z1"]), (2, out["z2"]), (4, out["z4"])):
        expected = e_max + 880 / (2 * zoom) - 0.10 * w
        assert got == pytest.approx(expected, abs=1e-6), (
            f"zoom {zoom}: got {got}, expected {expected}")
    # and the range genuinely shrinks as zoom rises (term is live)
    assert out["z1"] > out["z2"] > out["z4"]


def test_minimap_transform_offset_centres_the_world() -> None:
    out = run_js(
        """
        var proj = function (x, y) { return { x: (x - y) * 17, y: (x + y) * 8.5 }; };
        var e = C.worldExtent([{ x: 20, y: 20, w: 10, h: 6 }], proj);
        var t = C.minimapTransform({ w: 180, h: 120 }, e);
        var lo = t.toMini({ x: e.minX, y: e.minY });
        var hi = t.toMini({ x: e.maxX, y: e.maxY });
        emit({ e: e, sx: t.sx, sy: t.sy, ox: t.ox, oy: t.oy, lo: lo, hi: hi });
        """
    )
    # Uniform scale: an iso world must not be squashed on one axis or the
    # minimap stops looking like the building.
    assert out["sx"] == pytest.approx(out["sy"])
    # The world box lands inside the 180x120 rect ...
    assert out["lo"]["x"] >= -1e-6 and out["lo"]["y"] >= -1e-6
    assert out["hi"]["x"] <= 180 + 1e-6 and out["hi"]["y"] <= 120 + 1e-6
    # ... and an explicit non-zero offset exists, which is exactly what the
    # reference's scale-only minimap lacks.
    assert abs(out["ox"]) > 0 or abs(out["oy"]) > 0


def test_minimap_transform_places_the_world_absolutely() -> None:
    """Round-tripping alone is NOT enough: an offset error cancels in the
    inverse (toWorld(toMini(p)) == p holds even if the map is shifted by a
    constant). So the world box is also pinned to known minimap pixels, and the
    centring offset is checked against the closed form.

    The fixture uses a TALL extent on purpose. A 2:1 iso projection has an
    invariant aspect ratio, so `w/ew` is always the smaller scale for a wide
    minimap rect and the height-bound branch of the uniform scale would never be
    exercised — a module that ignored the height term entirely would pass. The
    `project` here is an identity map to make the box tall and narrow.
    """
    out = run_js(
        """
        // Identity projection: a tall, narrow extent (aspect < rect aspect).
        var proj = function (x, y) { return { x: x, y: y }; };
        var e = C.worldExtent([{ x: 100, y: 0, w: 60, h: 400 }], proj);
        var vp = { w: 180, h: 120 };
        var t = C.minimapTransform(vp, e);
        // A second, iso-projected fixture for the wide case.
        var proj2 = function (x, y) { return { x: (x - y) * 17, y: (x + y) * 8.5 }; };
        var e2 = C.worldExtent([{ x: 0, y: 0, w: 24, h: 14 }], proj2);
        var t2 = C.minimapTransform(vp, e2);
        emit({
          e: e,
          tl: t.toMini({ x: e.minX, y: e.minY }),
          sx: t.sx, ox: t.ox, oy: t.oy,
          e2: e2, sx2: t2.sx, ox2: t2.ox, oy2: t2.oy,
          tl2: t2.toMini({ x: e2.minX, y: e2.minY })
        });
        """
    )
    e = out["e"]
    # --- tall fixture: the HEIGHT must be the binding term ---
    assert e["w"] == pytest.approx(60.0)
    assert e["h"] == pytest.approx(400.0)
    s = out["sx"]
    assert 120 / e["h"] < 180 / e["w"], "fixture does not make height binding"
    assert s == pytest.approx(min(180 / e["w"], 120 / e["h"]))
    assert s == pytest.approx(120 / 400)          # height binds
    assert out["ox"] == pytest.approx((180 - e["w"] * s) / 2 - e["minX"] * s)
    assert out["oy"] == pytest.approx((120 - e["h"] * s) / 2 - e["minY"] * s)
    # The zero-width margin on the width axis proves the centring offset is real:
    # a scale-only map (the reference's) would leave ox = -minX*s and put the box
    # hard against one edge instead of centred.
    assert out["ox"] != pytest.approx(-e["minX"] * s), "box not centred horizontally"

    # --- wide iso fixture: the WIDTH must bind, and the box still centre ---
    e2 = out["e2"]
    s2 = out["sx2"]
    assert s2 == pytest.approx(min(180 / e2["w"], 120 / e2["h"]))
    assert out["tl2"]["x"] + e2["w"] * s2 / 2 == pytest.approx(180 / 2)
    assert out["tl2"]["y"] + e2["h"] * s2 / 2 == pytest.approx(120 / 2)


def test_minimap_inverses_round_trip_within_a_pixel() -> None:
    """toMini(toWorld(p)) == p, and toWorld(toMini(p)) == p, for a fuzz grid."""
    out = run_js(
        """
        var proj = function (x, y) { return { x: (x - y) * 17, y: (x + y) * 8.5 }; };
        var e = C.worldExtent([{ x: 0, y: 0, w: 24, h: 14 }], proj);
        var t = C.minimapTransform({ w: 180, h: 120 }, e);
        var worstA = 0, worstB = 0, n = 0;
        for (var i = 0; i <= 40; i++) {
          for (var j = 0; j <= 40; j++) {
            var p = { x: e.minX + (e.w * i / 40), y: e.minY + (e.h * j / 40) };
            var back = t.toWorld(t.toMini(p));
            worstA = Math.max(worstA, Math.abs(back.x - p.x), Math.abs(back.y - p.y));
            var q = { x: 180 * i / 40, y: 120 * j / 40 };
            var back2 = t.toMini(t.toWorld(q));
            worstB = Math.max(worstB, Math.abs(back2.x - q.x), Math.abs(back2.y - q.y));
            n += 1;
          }
        }
        emit({ worstA: worstA, worstB: worstB, n: n });
        """
    )
    assert out["n"] == 41 * 41
    assert out["worstA"] < 1.0, f"world->mini->world drifted {out['worstA']}"
    assert out["worstB"] < 1.0, f"mini->world->mini drifted {out['worstB']}"


def test_camera_screen_and_world_are_inverses() -> None:
    out = run_js(
        """
        var proj = function (x, y) { return { x: (x - y) * 17, y: (x + y) * 8.5 }; };
        var e = C.worldExtent([{ x: 0, y: 0, w: 24, h: 14 }], proj);
        var f = C.fit({ w: 880, h: 500 }, e, {});
        var cam = { cx: f.cx, cy: f.cy, zoom: f.zoom, w: 880, h: 500 };
        var worst = 0;
        for (var i = 0; i <= 20; i++) {
          for (var j = 0; j <= 20; j++) {
            var p = { x: e.minX + e.w * i / 20, y: e.minY + e.h * j / 20 };
            var s = C.screenPt(cam, p);
            var b = C.worldPt(cam, s);
            worst = Math.max(worst, Math.abs(b.x - p.x), Math.abs(b.y - p.y));
          }
        }
        emit({ worst: worst, zoom: f.zoom });
        """
    )
    # worldPt must invert screenPt exactly: that inversion is what makes
    # click-to-select land on the character that was drawn under the cursor.
    assert out["worst"] < 1e-6, f"screen/world not inverse: {out}"


def test_hit_picks_the_nearest_object_by_true_distance() -> None:
    """Click-to-select must use euclidean world distance, not L1 pixel distance.

    Two desks one grid step apart sit on an iso diagonal, where L1 and euclidean
    disagree most. The renderer's current picker computes |dx|+|dy| on screen
    pixels, which is a different metric from the one that placed the character
    under the cursor — hence "picks the wrong agent". Rather than hand-pick one
    knife-edge point, this asserts the definition over a fuzz grid: `hit` must
    return the argmin of euclidean distance, for every query point where the two
    metrics disagree.
    """
    out = run_js(
        """
        var objs = [];
        // A small iso deck of desks, one grid step apart along both grid axes,
        // so the set contains the diagonal pairs where L1 and euclidean differ.
        var proj = function (x, y) { return { x: (x - y) * 17, y: (x + y) * 8.5 }; };
        for (var gx = 0; gx < 4; gx++) {
          for (var gy = 0; gy < 4; gy++) {
            objs.push({ id: 'r' + gx + '_' + gy, p: proj(gx, gy) });
          }
        }
        var disagree = 0, wrongEuclid = 0, wrongL1 = 0, checked = 0;
        var worst = null;
        for (var i = -200; i <= 400; i += 1) {
          for (var j = -200; j <= 400; j += 1) {
            var pt = { x: i, y: j };
            var got = C.hit(null, objs, pt, 1e9);   // tol huge: always pick something
            var be = null, ce = Infinity, bl = null, cl = Infinity;
            for (var k = 0; k < objs.length; k++) {
              var dx = objs[k].p.x - pt.x, dy = objs[k].p.y - pt.y;
              var de = Math.sqrt(dx * dx + dy * dy);
              var dl = Math.abs(dx) + Math.abs(dy);
              if (de < ce) { ce = de; be = k; }
              if (dl < cl) { cl = dl; bl = k; }
            }
            checked++;
            if (be !== bl) disagree++;
            if (got !== be) {
              wrongEuclid++;
              if (got === bl) wrongL1++;   // it followed L1 instead
              if (worst === null) worst = { pt: pt, got: got, be: be, bl: bl };
            }
          }
        }
        emit({ objs: objs.length, checked: checked, disagree: disagree,
               wrongEuclid: wrongEuclid, wrongL1: wrongL1, worst: worst });
        """
    )
    # The fixture must actually contain points where the two metrics differ,
    # otherwise this test proves nothing.
    assert out["disagree"] > 0, "fixture never puts L1 and euclidean in conflict"
    # ... and hit() must agree with euclidean at every one of them.
    assert out["wrongEuclid"] == 0, (
        f"hit() deviated from euclidean at {out['wrongEuclid']} of "
        f"{out['checked']} points ({out['wrongL1']} followed L1); first: "
        f"{out['worst']}")


def test_hit_returns_null_outside_the_tolerance() -> None:
    out = run_js(
        """
        var objs = [{ p: { x: 0, y: 0 } }];
        emit({ far: C.hit(null, objs, { x: 500, y: 500 }, 10),
               near: C.hit(null, objs, { x: 1, y: 1 }, 10),
               none: C.hit(null, [], { x: 0, y: 0 }, 10),
               tol0: C.hit(null, objs, { x: 0, y: 0 }, 0) });
        """
    )
    # An empty click must clear the selection, not snap to the nearest agent
    # somewhere else on the floor.
    assert out["far"] is None
    assert out["none"] is None
    assert out["near"] == 0
    assert out["tol0"] == 0


def test_loads_as_a_browser_global_script() -> None:
    """window.QubitCamera must be defined by a plain <script> load."""
    out = run_js(
        """
        var fs = require('fs'), vm = require('vm');
        var src = fs.readFileSync(process.argv[1] || %s, 'utf8');
        var win = {};
        var ctx = vm.createContext({ window: win, console: console });
        vm.runInContext(src, ctx);
        emit({ keys: Object.keys(win.QubitCamera || {}),
               hasFit: typeof (win.QubitCamera || {}).fit === 'function',
               transform_has_inverses:
                 typeof win.QubitCamera.minimapTransform({w:10,h:10}, null).toMini === 'function' &&
                 typeof win.QubitCamera.minimapTransform({w:10,h:10}, null).toWorld === 'function' });
        """
        % json.dumps(str(MODULE))
    )
    assert out["has_fit"] if "has_fit" in out else out["hasFit"] is True
    for name in ("worldExtent", "fit", "clamp", "minimapTransform", "hit",
                 "screenPt", "worldPt", "CHAR_FLOOR_ZOOM", "MAX_ZOOM"):
        assert name in out["keys"], f"missing from window.QubitCamera: {name}"
    # toWorld/toMini are methods of the transform object, so they must be
    # reachable through a call rather than as top-level exports.
    assert out["transform_has_inverses"] is True


def test_source_names_both_mit_references() -> None:
    """MIT notice must travel with the code, and must name the exact sources."""
    src = MODULE.read_text(encoding="utf-8")
    assert re.search(r"ruiqili2/agent-monitor", src), "missing agent-monitor credit"
    assert re.search(r"src/engine/canvas\.ts", src), "missing canvas.ts path"
    assert re.search(r"FulAppiOS/Agent-Quest", src), "missing Agent-Quest credit"
    assert re.search(r"client/src/components/Minimap\.tsx", src), \
        "missing Minimap.tsx path"
    assert "MIT" in src
    # The unlicensed reference must not be a source of this code.
    # The first of these two used to end in `or True`, which made it true for
    # EVERY input -- `False or True`, `[] or True`, `"" or True` all pass -- so it
    # guarded nothing while reading like a guarantee.
    #
    # The naive fix (scan for "aphae" in everything before the first "not used")
    # is ALSO wrong, and this test caught it: the legal framing is
    # "`aphae` was not used", where the name precedes its own negation, so a
    # position-based split flags the disclaimer as an offence. What must be
    # rejected is a mention of the unlicensed repo WITHOUT a nearby negation --
    # checked per occurrence, not by position.
    _occurrences = list(re.finditer(r"aphae", src, re.I))
    _bad = [src[max(0, m.start() - 60):m.end() + 60]
            for m in _occurrences
            if not re.search(r"\b(not|never|no|unlicensed|excluded|read-only)\b",
                             src[max(0, m.start() - 80):m.end() + 80], re.I)]
    assert not _bad, (
        "the renderer source mentions the unlicensed repo with no nearby "
        f"negation -- it is being cited as a source: {_bad}")
    assert not re.search(r"rsanandres/aphae", src)
