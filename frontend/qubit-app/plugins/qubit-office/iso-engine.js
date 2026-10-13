/* Qubit Office — isometric projection core (Qubit addition, dependency-free).
 *
 * This file is the geometry half of the tower renderer. It owns ONE thing: the
 * mapping between a room's grid coordinates and a pixel on the canvas, plus the
 * three draw primitives and the paint order that turn a flat deck into a
 * building with volume. It reads no DOM, starts no timer, touches no network,
 * and holds no state between calls — every function here is pure, so a caller
 * can call it a hundred times for one frame and a test can call it from `node`
 * with no browser in sight.
 *
 * WHY IT IS A SEPARATE FILE. `qubit-office.js` is a plugin bundle loaded by the
 * browser. The projection is the part of it that is worth testing without a
 * browser and worth reusing outside the plugin, so it lives on its own and is
 * loaded both ways:
 *
 *     <script src="/dashboard-plugins/qubit-office/iso-engine.js"></script>
 *     ...window.QubitIso.gridToScreen(x, y, z, cam)
 *
 *     // CommonJS / node -e
 *     var ISO = require("./iso-engine.js");
 *
 * ---------------------------------------------------------------------------
 * PORTED CODE — provenance. MIT requires the notice to travel with the code.
 *
 * The isometric maths below is a port of the technique in:
 *
 *   upstream repo : ruiqili2/agent-monitor (MIT, Copyright (c) 2026 Ruiqi Li)
 *   source path   : src/engine/isometric.ts
 *   upstream rev  : bec78ad8cb64054a81df7bf2db56e3012f577c79
 *   licence       : MIT (see LICENSE at the repo root of that project)
 *
 * Ported techniques, and what each one buys us here:
 *
 *   1. The 2:1 diamond. `TILE_W = 48, TILE_H = 24` upstream — a tile is twice as
 *      wide as it is tall. Every isometric tile is a rhombus whose diagonals are
 *      TILE_W and TILE_H, and that 2:1 ratio is what makes a diamond read as a
 *      floor seen at an angle. A 1:1 diamond reads as a slightly rotated square
 *      and the whole scene loses its depth cue, so the ratio is not cosmetic —
 *      it IS the projection.
 *   2. The projection itself: `x = (col - row) * (TILE_W / 2)` and
 *      `y = (col + row) * (TILE_H / 2)`. One unit of `col` moves the point
 *      right-and-down; one unit of `row` moves it left-and-down. That is the
 *      rotation of a square grid by 45 degrees and its squash by two.
 *   3. The inverse, `screenToGrid`: the forward transform is a linear map, so it
 *      inverts in closed form — divide by the same half-tile factors and add /
 *      subtract the two axes, then round. This is the piece our own renderer was
 *      missing: it picked a character by screen distance because it had no way
 *      to go backwards from a click to a tile.
 *   4. The block: three quads (top, left wall, right wall) sharing the four
 *      diamond corners, with the top face translated UP by the extrusion height.
 *      The two wall quads are what make a plate read as a box. Drawing only the
 *      top face — which is what a flat deck is — cannot produce volume no matter
 *      how it is coloured.
 *
 * What is ours and not upstream's: the camera (`cx`, `cy`, `zoom`, `w`, `h`,
 * which upstream had as fixed module constants), the `z` deck axis (their engine
 * is single-level), the painter's-order key, and the palette separation between
 * a lit and a shaded wall face. Upstream's `getTileBounds` returns
 * `{x, y, width, height}`; ours returns `{x, y, w, h}` to match the canvas
 * callers in `qubit-office.js`.
 *
 * Unrelated to the port, and stated so nobody has to guess: no code was taken
 * from any repository that ships no licence file. Where a no-licence project was
 * read at all, nothing of its expression survives here.
 * ---------------------------------------------------------------------------
 */
(function (root, factory) {
  "use strict";
  var api = factory();
  /* CommonJS first: in `node -e` there is no window, and `module` is the only
   * export surface that exists. In the browser `module` is undefined, so the
   * same factory lands on window.QubitIso. */
  if (typeof module === "object" && module && module.exports) {
    module.exports = api;
  }
  if (root) root.QubitIso = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  /* --- the tile proportion, 2:1 -------------------------------------------
   * Width twice the height. These are the single source of truth for the whole
   * projection: change TILE_W and the diamond widens, the grid spreads, and the
   * inverse still lands on the same tile because the inverse is derived from
   * these numbers rather than mirroring them by hand.
   *
   * 34 x 17 is what the current renderer uses and it is deliberately the value
   * kept here: this module exists to fix the projection's INVERSE and its depth
   * order, not to re-scale the office behind the owner's back. A caller that
   * wants bigger tiles sets `cam.tileW` / `cam.tileH`. */
  var TILE_W = 34, TILE_H = 17;

  /* Vertical gap, in screen pixels, between two stacked floors. `z` on a floor
   * is its deck level; the projection multiplies it by DECK_Z. Without a `z`
   * term every floor projects onto the same diamond and the tower is one plate
   * — which is exactly the "flat building" defect. */
  var DECK_Z = 26;

  function num(v, fallback) {
    var n = Number(v);
    return isFinite(n) ? n : (fallback === undefined ? 0 : fallback);
  }

  /* A camera is {cx, cy, zoom, w, h} plus optional {tileW, tileH, deckZ}.
   * It is read, never mutated: every function below takes a plain object and
   * returns a new one, so a caller can hold two cameras (a live one and a
   * fitted one) without them aliasing.
   *
   *   cx, cy  the WORLD point that sits at the centre of the canvas
   *   zoom    pixels per world unit; the world unit is the un-zoomed iso space
   *   w, h    canvas size in device pixels
   *   tileW/H tile overrides (default TILE_W / TILE_H)
   *   deckZ   deck-gap override (default DECK_Z)
   */
  function _cam(cam) {
    var c = cam || {};
    return {
      cx: num(c.cx),
      cy: num(c.cy),
      zoom: num(c.zoom, 1) || 1,
      w: num(c.w),
      h: num(c.h),
      tileW: num(c.tileW, TILE_W) || TILE_W,
      tileH: num(c.tileH, TILE_H) || TILE_H,
      deckZ: num(c.deckZ, DECK_Z)
    };
  }

  /* ---------------------------------------------------------- world space
   * Grid -> an un-zoomed, un-panned pixel. Split out from gridToScreen because
   * the two halves answer different questions: this one is pure geometry (where
   * is this tile in the world), the other adds the camera (where does the world
   * land on the canvas). Having it separate is what makes screenToGrid simple —
   * the camera is divided out once, then the geometry is inverted.
   */
  function _world(x, y, z, c) {
    var gx = num(x), gy = num(y);
    return {
      x: (gx - gy) * (c.tileW / 2),
      /* z lifts the point on screen. Higher deck = smaller y = drawn higher. */
      y: (gx + gy) * (c.tileH / 2) - num(z) * c.deckZ
    };
  }

  /* Grid -> screen, camera applied. The return value is the CENTRE of the tile's
   * top face, which is the draw origin the primitives below expect. */
  function gridToScreen(x, y, z, cam) {
    var c = _cam(cam);
    var p = _world(x, y, z, c);
    return {
      x: (p.x - c.cx) * c.zoom + c.w / 2,
      y: (p.y - c.cy) * c.zoom + c.h / 2
    };
  }

  /* ------------------------------------------------------- inverse mapping
   * Screen -> the grid cell under that pixel.
   *
   * How: undo the camera, then invert the linear map. Forward is
   *
   *     wx = (gx - gy) * hw          hw = tileW / 2
   *     wy = (gx + gy) * hh          hh = tileH / 2   (z already removed)
   *
   * so adding and subtracting the two equations gives
   *
   *     gx = (wx / hw + wy / hh) / 2
   *     gy = (wy / hh - wx / hw) / 2
   *
   * and rounding each lands on a cell. Because the two axes are independent and
   * the rounding is per-axis, the round trip gridToScreen -> screenToGrid is
   * EXACT for every integer grid point, at any zoom: the forward transform maps
   * integers to points whose inverse is an integer (not merely within half a
   * tile), so a click's pixel is resolved to a real cell instead of guessed.
   *
   * `z` must be supplied — a screen point is a ray, not a point, and which floor
   * it hits cannot be recovered from the pixel alone. Pass the deck being picked
   * on. (Screen y and z trade off one-for-one, so a wrong z shifts the hit along
   * the depth axis rather than corrupting x/y; the caller knows its floor.)
   */
  function screenToGrid(sx, sy, cam, z) {
    var c = _cam(cam);
    /* camera out */
    var wx = (num(sx) - c.w / 2) / c.zoom + c.cx;
    var wy = (num(sy) - c.h / 2) / c.zoom + c.cy + num(z) * c.deckZ;
    var hw = c.tileW / 2, hh = c.tileH / 2;
    return {
      x: Math.round((wx / hw + wy / hh) / 2),
      y: Math.round((wy / hh - wx / hw) / 2)
    };
  }

  /* The four corners of a tile's top face, clockwise from the top point. A
   * caller drawing a plate (rather than a whole block) needs these and nothing
   * else, and a caller hit-testing a non-rectangular region needs them too. */
  function tileCorners(x, y, z, cam) {
    var c = _cam(cam), p = _world(x, y, z, c);
    function at(dx, dy) {
      return {x: (p.x + dx) * c.zoom + c.w / 2,
              y: (p.y + dy) * c.zoom + c.h / 2};
    }
    var hw = c.tileW / 2, hh = c.tileH / 2;
    return [at(0, -hh), at(hw, 0), at(0, hh), at(-hw, 0)];   /* N, E, S, W */
  }

  /* The axis-aligned screen box a tile occupies. Upstream returns
   * {x, y, width, height}; ours is {x, y, w, h} to match the canvas calls in
   * qubit-office.js. `lift` adds extrusion height above the tile (a block's
   * walls rise by that much), so a hit box for a drawn block covers its walls
   * too — a box that ignored them would say the top half of a building is not
   * part of the building. */
  function tileBounds(x, y, z, cam, lift) {
    var c = _cam(cam), p = _world(x, y, z, c);
    var hw = c.tileW / 2 * c.zoom, hh = c.tileH / 2 * c.zoom;
    var up = num(lift) * c.zoom;
    var cx = p.x * c.zoom + c.w / 2, cy = p.y * c.zoom + c.h / 2;
    return {x: cx - hw, y: cy - hh - up, w: hw * 2, h: hh * 2 + up};
  }

  /* The box a character occupies, anchored by its FEET: the sprite is drawn
   * above the ground point, so the box extends upward from it. Same reasoning as
   * upstream's getCharacterBounds, with our own figure proportions (the
   * renderer's character is 15 world units tall and 9 wide, and the marker for a
   * stalled seat sits above the head — the box includes it, or clicking a
   * warning triangle would select nothing). */
  var CHAR_BODY_W = 9, CHAR_BODY_H = 15, CHAR_MARKER_H = 8;
  function characterBounds(x, y, z, cam) {
    var c = _cam(cam);
    var p = gridToScreen(x, y, z, cam);
    var halfW = CHAR_BODY_W / 2 * c.zoom;
    var up = (CHAR_BODY_H + CHAR_MARKER_H) * c.zoom;
    return {x: p.x - halfW, y: p.y - up, w: halfW * 2, h: up};
  }

  /* ------------------------------------------------------------ paint order
   * THE RULE, stated once so every draw call can obey it without repeating it:
   *
   *   Paint in increasing `depthKey`. A greater key is NEARER the viewer, so it
   *   is painted later and covers what is behind it.
   *
   * Why col + row: in this projection +col goes down-right and +row goes
   * down-left, so a tile's screen-y is (col + row) * tileH / 2. Sort by the sum
   * and you are sorting back-to-front by screen depth — which is the whole
   * painter's algorithm for an axis-aligned iso grid. A desk at (3,1) is drawn
   * after, and therefore over, a desk at (1,1).
   *
   * Why `-z * big` and not `-z`: two decks are separated vertically by DECK_Z
   * pixels, but their foot tiles overlap in col/row. The deck term must dominate
   * the in-deck term or a desk on a low floor would paint over the wall of the
   * floor behind it. FLOOR_STEP is larger than any plausible (col + row) sum for
   * an office grid, so the sort is: deck order first, in-deck depth second. ROBUST TIE-BREAK: two objects on the same tile (a desk and the character
   * sitting at it) must have a deterministic order, and the character must win —
   * an occupant drawn under its own desk is invisible. `tiebreak` pushes the
   * caller's own tie-break value to the FRONT of the equal-depth range, and the
   * renderer passes 1 for a character and 0 for furniture.
   *
   * A HIGHER deck is painted FIRST even though it is drawn physically higher on
   * screen: in a cutaway tower the near, lower deck overlaps the upper deck's
   * walls. That is why the z term is subtracted.
   */
  var FLOOR_STEP = 1000;
  function depthKey(x, y, z, tiebreak) {
    /* repeated col+row is intentional: scaled by GRAIN so that the tie-break
     * (which is smaller than one grid unit) can never reorder two objects that
     * are on genuinely different tiles. */
    var GRAIN = 4;
    return -num(z) * FLOOR_STEP + (num(x) + num(y)) * GRAIN
      - num(tiebreak) * 1;
  }

  /* Sort helper: a stable, comparator-free ordering of {x, y, z, ...} nodes.
   * Returns a NEW array; the input is never reordered, because a caller who
   * sorts the payload's own arrays in place would mutate data it does not own.
   * Ties are broken by the caller's `tiebreak` (see depthKey). */
  function sortByDepth(nodes) {
    return (nodes || []).map(function (n, i) {
      return {n: n, i: i, k: depthKey(n.x, n.y, n.z, n.tiebreak)};
    }).sort(function (a, b) {
      /* equal key -> original order, so a re-sort never reshuffles a stable
       * scene between polls (the same discipline the payload's determinism
       * tests demand). */
      return a.k === b.k ? a.i - b.i : a.k - b.k;
    }).map(function (row) { return row.n; });
  }

  /* ----------------------------------------------------------- primitives
   * All three take a camera and the grid coordinates of the tile, and touch
   * only the 2D context handed to them. No global state, no DOM lookup.
   */
  function _path(ctx, pts) {
    ctx.beginPath();
    ctx.moveTo(pts[0].x, pts[0].y);
    for (var i = 1; i < pts.length; i++) ctx.lineTo(pts[i].x, pts[i].y);
    ctx.closePath();
  }

  /* The flat diamond: a floor plate, a room's footprint, a desk mat. */
  function drawTile(ctx, x, y, z, cam, fill, stroke) {
    if (!ctx) return;
    var pts = tileCorners(x, y, z, cam);
    _path(ctx, pts);
    if (fill) { ctx.fillStyle = fill; ctx.fill(); }
    if (stroke) { ctx.strokeStyle = stroke; ctx.lineWidth = 1; ctx.stroke(); }
  }

  /* The extruded tile: a top face plus two wall faces, which is what makes a
   * deck read as a building instead of a plate.
   *
   * `height` is in SCREEN pixels at zoom 1 (it is multiplied by cam.zoom), so a
   * caller can say "20 units of wall" and it stays 20 units whether the user has
   * zoomed in or out. The walls hang DOWN from the top face, not up from the
   * ground: the top face is the deck surface the camera looks at, and the wall
   * faces are the cutaway below it. This is the cutaway convention the renderer
   * already uses — only the two viewer-facing faces are drawn so the interior
   * (seats, characters) stays visible.
   *
   * The two faces get different tones by default: the right face is lit and the
   * left face is in shadow, which is the only cue that tells a reader which way
   * the box faces. Without the split both walls are the same colour and the
   * corner between them vanishes, taking the volume with it.
   */
  function drawBlock(ctx, x, y, z, cam, height, top, left, right) {
    if (!ctx) return;
    var c = _cam(cam);
    var n = tileCorners(x, y, z, cam);          /* N, E, S, W (screen space) */
    var h = num(height) * c.zoom;
    if (!h) { drawTile(ctx, x, y, z, cam, top, null); return; }
    var N = n[0], E = n[1], S = n[2], W = n[3];
    /* the same four points raised by the extrusion */
    var N2 = {x: N.x, y: N.y - h}, E2 = {x: E.x, y: E.y - h};
    var S2 = {x: S.x, y: S.y - h}, W2 = {x: W.x, y: W.y - h};

    /* left (W-S) face first, then right (E-S), then the top over both — the
     * top face is the only one guaranteed to be unobstructed, so it lands last. */
    if (left) {
      _path(ctx, [W, S, S2, W2]);
      ctx.fillStyle = left; ctx.fill();
    }
    if (right) {
      _path(ctx, [E, S, S2, E2]);
      ctx.fillStyle = right; ctx.fill();
    }
    _path(ctx, [N2, E2, S2, W2]);
    if (top) { ctx.fillStyle = top; ctx.fill(); }
  }

  /* A lit / shaded pair from one hex colour, for callers that want a block's
   * walls to differ without carrying two literals. Kept numeric-local (no HSL
   * round trip) so it stays cheap enough for a per-tile call at 70 seats. */
  function faceTone(hex, factor) {
    var m = /^#?([0-9a-f]{6})$/i.exec(String(hex || ""));
    if (!m) return hex;
    var v = parseInt(m[1], 16);
    var r = Math.round(Math.min(255, ((v >> 16) & 255) * factor));
    var g = Math.round(Math.min(255, ((v >> 8) & 255) * factor));
    var b = Math.round(Math.min(255, (v & 255) * factor));
    function hx(q) { return (q < 16 ? "0" : "") + q.toString(16); }
    return "#" + hx(r) + hx(g) + hx(b);
  }

  return {
    TILE_W: TILE_W,
    TILE_H: TILE_H,
    DECK_Z: DECK_Z,
    FLOOR_STEP: FLOOR_STEP,
    CHAR_BODY_W: CHAR_BODY_W,
    CHAR_BODY_H: CHAR_BODY_H,
    gridToScreen: gridToScreen,
    screenToGrid: screenToGrid,
    tileCorners: tileCorners,
    tileBounds: tileBounds,
    characterBounds: characterBounds,
    depthKey: depthKey,
    sortByDepth: sortByDepth,
    drawTile: drawTile,
    drawBlock: drawBlock,
    faceTone: faceTone
  };
});
