/* Qubit Office — camera, minimap and picking. Standalone, dependency-free.
 *
 * This module owns ONE concern: the map between grid space, "world" screen
 * space (the un-zoomed projection), and the pixels of a viewport. Nothing here
 * draws, fetches, timers or animates. Load it as a plain browser script
 * (defines `window.QubitCamera`) or require() it under node for tests; it has
 * no DOM dependency beyond the `window` check at the bottom.
 *
 * ---------------------------------------------------------------------------
 * PORTED CODE — provenance. Both sources are MIT; the *technique* is ported and
 * re-expressed here against our own coordinate convention. No upstream file is
 * copied verbatim and no upstream variable naming survives. `aphae` was NOT
 * used: it ships no licence file (all rights reserved).
 *
 *   1. ruiqili2/agent-monitor (MIT) — `src/engine/canvas.ts`, and the coupled
 *      `src/engine/isometric.ts`. Two ideas taken:
 *        (a) the camera is a *world-space* origin (that repo's MAP_OFFSET_X/Y),
 *            not a viewport-relative one, and the projection is an invertible
 *            affine map. Their `screenToGrid` is the exact algebraic inverse of
 *            `gridToScreen` — solving `sx = (col-row)*W/2`, `sy = (col+row)*H/2`
 *            back out gives `col = (sx/(W/2) + sy/(H/2))/2` and
 *            `row = (sy/(H/2) - sx/(W/2))/2`. That is why this module can offer
 *            `toWorld`/`toMini` inverses instead of guessing: the projection is
 *            linear, so it has a closed-form inverse and it is a bug to
 *            approximate it.
 *        (b) the extent is accumulated over drawables in SCREEN space
 *            (`prepareRenderData` builds bounds in projected coords), which is
 *            what makes an isometric world measurable without knowing anything
 *            about the deck's shape.
 *   2. FulAppiOS/Agent-Quest (MIT) — `client/src/components/Minimap.tsx`. Its
 *      minimap is `sx = w / GAME_W`, `sy = h / GAME_H`, then every world rect
 *      drawn as `x * sx`. That is a pure scale, no offset, and it is exactly
 *      what makes a minimap *look* vestigial: it silently pins the world's
 *      origin to the minimap's corner, so a world that does not sit at (0,0)
 *      lands off-rect. The correct generalisation — world box -> rect box with
 *      an independent x and y scale — is what `minimapTransform` implements,
 *      together with the inverse the reference does not need (its minimap is
 *      not clickable) but ours is, because clicking the map should move the
 *      building under the camera.
 * ---------------------------------------------------------------------------
 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.QubitCamera = factory();
})(typeof window !== "undefined" ? window : this, function () {
  "use strict";

  var MAX_ZOOM = 2.4;
  /* Legibility floor: a character is 15 world units tall, and below ~0.85 the
   * figures are specks. `fit` will therefore happily let the building overflow
   * the viewport rather than zoom past this. Architecture may be cropped;
   * people may not vanish. */
  var CHAR_FLOOR_ZOOM = 0.85;

  /* Strict numeric coercion. `Number(null)` is 0 and `Number("")` is 0, both
   * finite — so a plain Number()+isFinite test silently turns a null field into
   * a real coordinate at the origin. The payload legitimately carries null
   * (`position.x === null` is checked elsewhere in the renderer), so a null must
   * be REJECTED as missing, not laundered into 0. */
  function _num(v, d) {
    if (typeof v === "number") return isFinite(v) ? v : d;
    if (typeof v === "string") {
      var s = v.trim();
      if (!s) return d;                      /* "" and "   " are missing */
      var n = Number(s);
      return isFinite(n) ? n : d;
    }
    return d;                                /* null, undefined, bool, object */
  }

  function _clamp(v, lo, hi) {
    return v < lo ? lo : (v > hi ? hi : v);
  }

  /* ------------------------------------------------------------- projection
   * The caller supplies `project(x, y) -> {x, y}` = the iso projection of a
   * grid point into WORLD screen space (zoom 1, world origin). Having the
   * caller own the projection is deliberate: the camera never needs to know
   * TILE_W/TILE_H, so the two can evolve independently and this module can be
   * tested against the linear map directly.
   */
  function _proj(project, x, y) {
    var p = project(x, y);
    if (!p || typeof p.x !== "number" || typeof p.y !== "number" ||
        !isFinite(p.x) || !isFinite(p.y)) {
      throw new TypeError("project(x,y) must return finite {x,y}");
    }
    return p;
  }

  /* worldExtent(cells, project) -> {minX,maxX,minY,maxY,w,h,cx,cy} | null
   *
   * `cells` is a list of axis-aligned grid rects {x,y,w,h}. We project the four
   * CORNERS, not just the min and max corner: under a 2:1 iso projection the
   * cell (x..x+w, y..y+h) maps to a diamond whose extreme left/right/top/bottom
   * are at different corners, so the min/max pair alone under-measures the box
   * and the fit would crop the building on two sides.
   *
   * Empty list -> null. Returning a zero-size box at the origin is the bug that
   * makes an empty office look like a tiny office parked in the corner. The
   * running bounds are seeded at +/-Infinity, NOT at 0: a 0 seed silently
   * becomes the box edge whenever the true edge does not cross 0, which is the
   * normal case (room (0,12) projects to x = -204).
   *
   * A cell with a non-numeric x or y is SKIPPED, not coerced. `Number(null)` is
   * 0, so a naive coercion would turn `"x": null` from the payload into a real
   * room at the world origin and drag the box with it; a null coordinate is
   * missing data and contributes nothing.
   */
  function worldExtent(cells, project) {
    if (typeof project !== "function") {
      throw new TypeError("worldExtent(cells, project) needs a project function");
    }
    var list = cells || [];
    var minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
    var seen = 0;
    for (var i = 0; i < list.length; i++) {
      var c = list[i];
      if (!c) continue;
      var x = _num(c.x, NaN), y = _num(c.y, NaN);
      var w = _num(c.w, 0), h = _num(c.h, 0);
      if (isNaN(x) || isNaN(y)) continue;   /* skip junk cells, do not poison the box with NaN */
      var xs = [x, x + w], ys = [y, y + h];
      for (var a = 0; a < 2; a++) {
        for (var b = 0; b < 2; b++) {
          var p = _proj(project, xs[a], ys[b]);
          if (p.x < minX) minX = p.x;
          if (p.x > maxX) maxX = p.x;
          if (p.y < minY) minY = p.y;
          if (p.y > maxY) maxY = p.y;
          seen++;
        }
      }
    }
    if (!seen) return null;
    return {
      minX: minX, maxX: maxX, minY: minY, maxY: maxY,
      w: maxX - minX, h: maxY - minY,
      cx: (minX + maxX) / 2, cy: (minY + maxY) / 2
    };
  }

  /* fit(viewport, extent, opts) -> {cx, cy, zoom}
   *
   * THE FRAMING RULE. Think about the two ways a fit can fail:
   *
   *   "contain"  — zoom = min(vw/e.w, vh/e.h). Both dimensions fit, so there is
   *                guaranteed dead space on the axis that is not the constraint.
   *                For an isometric deck (very wide, shallow) the HEIGHT term
   *                always wins and the building is shrunk to a thin stripe with
   *                dark bands above and below it. That is the exact defect.
   *                A contain fit is the right rule only when BOTH the extent's
   *                aspect and the viewport's aspect are close to each other; it
   *                is degenerate for a degenerate-shaped subject.
   *   "cover"    — zoom = max(vw/e.w, vh/e.h). The limiting axis fills the
   *                viewport exactly, the overflow on the other axis is cropped
   *                by the viewport rect. No dead space is ever introduced by
   *                the fit itself.
   *
   * So the rule is a COVER fit: the extent is scaled until it covers the
   * viewport, the limiting axis touches the margin and the other axis is
   * cropped by the viewport rect. No dead space is ever introduced by the fit
   * itself, which is the whole point — dead space is what the defect report is.
   *
   *   zoom = max( min( availW/e.w, availH/e.h ), minZoom )
   *
   * The practical shape of this rule for an isometric deck: a 2:1 iso
   * projection gives an extent whose aspect ratio is ~2 (wide and shallow) while
   * a typical canvas is ~1.76, so the WIDTH is the limiting axis and the deck
   * fills the canvas edge to edge. That is why a cover fit looks like "fit the
   * width" on this data — but it is the general cover expression, not a width
   * special case, and it also does the right thing for a tall narrow floorplan
   * (there the height limits and the width crops, instead of the whole thing
   * being shrunk to fit the width with slack above and below).
   *
   * NOTE, deliberately: `min(availW/e.w, availH/e.h)` is already a cover fit, so
   * there is no separate "width term" to take a minimum with — writing
   * `min(widthFit, cover)` would be dead code, since cover is by construction
   * <= either single-axis fit. An earlier draft of this module carried that
   * redundant term and a comment claiming it mattered; it did not.
   *
   * Then the legibility floor and the sanity ceiling, applied on top:
   *   zoom = min(zoom, maxZoom);  zoom = max(zoom, minZoom)
   * The floor is applied LAST and is not negotiable: architecture may be
   * cropped (cover already crops), but a 15-unit character may not vanish.
   *
   * `margin` is a fraction of viewport WIDTH/HEIGHT (0.08 = 8% of the
   * viewport on each side), NOT world units and NOT a multiplier on the extent:
   * a margin that scales with the extent is not a margin, it is a second fit.
   */
  function fit(viewport, extent, opts) {
    var o = opts || {};
    var vw = Math.max(1, _num(viewport && viewport.w, 1));
    var vh = Math.max(1, _num(viewport && viewport.h, 1));
    var minZoom = _num(o.minZoom, CHAR_FLOOR_ZOOM);
    var maxZoom = _num(o.maxZoom, MAX_ZOOM);
    var margin = _clamp(_num(o.margin, 0.08), 0, 0.45);
    if (!extent) return {cx: 0, cy: 0, zoom: minZoom, fit: "empty"};

    var availW = vw * (1 - 2 * margin);
    var availH = vh * (1 - 2 * margin);
    var ew = Math.max(1e-6, extent.w), eh = Math.max(1e-6, extent.h);

    /* Cover zoom: the smallest zoom at which the extent covers the viewport.
     * Whichever axis needs less magnification is the limiting one. */
    var zCover = Math.min(availW / ew, availH / eh);
    var limited = (availW / ew < availH / eh) ? "width" : "height";
    var z = Math.min(zCover, maxZoom);
    z = Math.max(z, minZoom);
    return {cx: extent.cx, cy: extent.cy, zoom: z,
             fit: (z <= minZoom + 1e-9) ? "floor" :
                  (z >= maxZoom - 1e-9 ? "ceiling" : limited)};
  }

  /* clamp(viewport, extent, cam, marginFrac) -> cam (mutated, returned)
   *
   * The pan constraint. The camera centre is a world point drawn at the centre
   * of the viewport. The constraint is that a camera position is legal iff the
   * viewport rect still overlaps the extent's projected rect by at least
   * `marginFrac` of the extent — i.e. the building may be pushed to the edge,
   * but a piece of it always stays on screen and the user can always drag it
   * back. That overlap condition, converted to camera-centre bounds, gives an
   * interval per axis:
   *
   *   lo = e.min - halfVp + margin*e.size
   *   hi = e.max + halfVp - margin*e.size
   *
   * (halfVp = viewport extent in world units / 2 = viewportSize / (2*zoom)).
   * When the extent is smaller than the viewport the interval is wide and the
   * building can sit anywhere including fully inside; when it is larger the
   * interval correctly refuses to let a blank region be the whole screen.
   * The clamp only applies where the interval is non-degenerate — on the
   * pinned axis (lo > hi, i.e. the subject is far smaller than the viewport),
   * clamping would fight the fit, so we centre instead.
   */
  function clamp(viewport, extent, cam, marginFrac) {
    if (!cam) return cam;
    if (!extent) return cam;
    var m = _clamp(_num(marginFrac, 0.30), 0, 0.9);
    var vw = Math.max(1, _num(viewport && viewport.w, 1));
    var vh = Math.max(1, _num(viewport && viewport.h, 1));
    var z = Math.max(1e-6, _num(cam.zoom, 1));

    function axis(size, vpSize, mn, mx, c, auto) {
      var half = vpSize / (2 * z);
      var lo = mn - half + m * size;
      var hi = mx + half - m * size;
      if (hi < lo) return (mn + mx) / 2;          /* subject far smaller than the viewport */
      if (lo > hi) { var t = lo; lo = hi; hi = t; }
      /* A passive fit centre should not be nudged: rounding could otherwise
       * shift a freshly framed building by a sub-pixel and make the fit look
       * off-centre. User pans still get clamped. */
      if (auto && c >= lo && c <= hi) return c;
      return _clamp(c, lo, hi);
    }
    cam.cx = axis(extent.w, vw, extent.minX, extent.maxX, _num(cam.cx, 0), !!cam.auto);
    cam.cy = axis(extent.h, vh, extent.minY, extent.maxY, _num(cam.cy, 0), !!cam.auto);
    return cam;
  }

  /* minimapTransform(viewport, extent) -> {sx, sy, ox, oy, w, h, toWorld, toMini}
   *
   * The scale/offset that maps WORLD coords into the minimap rect. Agent-Quest
   * uses `sx = w/GAME_W` with no offset, which pins the world origin to the
   * minimap corner — the vestigial look. The general form keeps the world box's
   * aspect (the true uniform scale on the limiting axis) and centres it in the
   * rect with an explicit offset, so a small office is a small map centred in
   * the panel rather than a smear in one corner.
   *
   * toMini(toWorld(p)) is an identity to within a rounding pixel — the test
   * asserts exactly that over a fuzz grid, which is what proves the two axes
   * share one scale and one offset.
   */
  function minimapTransform(viewport, extent) {
    var w = Math.max(1, _num(viewport && viewport.w, 1));
    var h = Math.max(1, _num(viewport && viewport.h, 1));
    var e = extent || {minX: 0, minY: 0, w: 1, h: 1};
    var ew = Math.max(1e-6, _num(e.w, 1)), eh = Math.max(1e-6, _num(e.h, 1));
    var s = Math.min(w / ew, h / eh);
    var ox = (w - ew * s) / 2 - _num(e.minX, 0) * s;
    var oy = (h - eh * s) / 2 - _num(e.minY, 0) * s;

    var t = {
      sx: s, sy: s, ox: ox, oy: oy, w: w, h: h,
      toMini: function (p) {
        return {x: _num(p && p.x, 0) * s + ox, y: _num(p && p.y, 0) * s + oy};
      },
      toWorld: function (p) {
        return {x: (_num(p && p.x, 0) - ox) / s, y: (_num(p && p.y, 0) - oy) / s};
      }
    };
    return t;
  }

  /* hit(extent, objects, pt, tol) -> index | null
   *
   * Nearest-object picking in WORLD space. `objects` is anything with a `.p`
   * of {x,y} in world coords; the test keeps it to plain point records.
   * We use true euclidean distance on the world point, not the L1 (|dx|+|dy|)
   * distance the renderer currently uses on screen pixels: isometric world
   * distance is not screen distance, and L1 is worst exactly along the iso
   * diagonals — which is where two adjacent desks sit. So the wrong agent gets
   * picked for a click that visually belongs to the nearer one.
   *
   * `tol` is the pick radius in WORLD units (divide the pixel radius by zoom at
   * the call site). Ties break to the lower index so a click is deterministic.
   * Returns null when nothing is inside the radius — an empty click must clear
   * the selection, not snap to the nearest agent anywhere on the floor.
   */
  function hit(extent, objects, pt, tol) {
    if (!pt) return null;
    var list = objects || [];
    var maxD = Math.max(0, _num(tol, 0));
    var px = _num(pt.x, 0), py = _num(pt.y, 0);
    var best = null, bestD = maxD;
    for (var i = 0; i < list.length; i++) {
      var o = list[i];
      var p = o ? (o.p || o) : null;
      if (!p) continue;
      var dx = _num(p.x, NaN) - px, dy = _num(p.y, NaN) - py;
      if (isNaN(dx) || isNaN(dy)) continue;
      var d = Math.sqrt(dx * dx + dy * dy);
      if (d <= bestD && (best === null || d < bestD)) { bestD = d; best = i; }
    }
    return best;
  }

  /* screenPt(cam, worldPt) / worldPt(cam, screenPt)
   * The pair the caller actually uses: the camera is a translation in world
   * space followed by a scale. Keeping both here is what lets click-to-select
   * invert the exact transform that placed the pixel. */
  function screenPt(cam, p) {
    var z = _num(cam && cam.zoom, 1);
    return {
      x: (_num(p && p.x, 0) - _num(cam && cam.cx, 0)) * z + _num(cam && cam.w, 0) / 2,
      y: (_num(p && p.y, 0) - _num(cam && cam.cy, 0)) * z + _num(cam && cam.h, 0) / 2
    };
  }

  function worldPt(cam, p) {
    var z = Math.max(1e-6, _num(cam && cam.zoom, 1));
    return {
      x: (_num(p && p.x, 0) - _num(cam && cam.w, 0) / 2) / z + _num(cam && cam.cx, 0),
      y: (_num(p && p.y, 0) - _num(cam && cam.h, 0) / 2) / z + _num(cam && cam.cy, 0)
    };
  }

  return {
    MAX_ZOOM: MAX_ZOOM,
    CHAR_FLOOR_ZOOM: CHAR_FLOOR_ZOOM,
    worldExtent: worldExtent,
    fit: fit,
    clamp: clamp,
    minimapTransform: minimapTransform,
    hit: hit,
    screenPt: screenPt,
    worldPt: worldPt
  };
});
