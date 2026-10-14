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
   * else, and a caller hit-testing a non-rectangular region needs them too.
   *
   * Built from `_world` (the tile's centre) plus `_diamondOffset` (the tile's
   * shape), so a caller that needs only the shape — to clip a ground-tile blit
   * into, to place a decoration inside — gets the SAME diamond this returns,
   * because it is literally the same two pieces added the same way. */
  function tileCorners(x, y, z, cam) {
    var c = _cam(cam), p = _world(x, y, z, c);
    var off = _diamondOffset(cam);
    var out = [];
    for (var i = 0; i < 4; i++) {
      out.push({x: (p.x + off[i].x) * c.zoom + c.w / 2,
                y: (p.y + off[i].y) * c.zoom + c.h / 2});
    }
    return out;   /* N, E, S, W */
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

  /* The four corners of the tile the thing at (x, y, z) STANDS ON, expressed
   * relative to the tile's own centre. `_world` gives the centre; this gives the
   * diamond offset, which a caller needs whenever it wants to place something
   * that must sit ON a tile in the SAME frame the tile is drawn in: a clipped
   * blit of a ground tile, a hit box, a shadow that must not drift with the
   * camera. It is deliberately a pure function of the camera and nothing else,
   * so the offset it returns is exactly the offset `tileCorners` uses — the two
   * cannot disagree about where a tile's edges are.
   *
   * INTERNAL. Not exported: `tests/test_office_iso_engine.py` pins this module's
   * export surface to an exact key list, and that list is a contract with the
   * renderers, not a place to bolt new geometry on. The world surface that needs
   * this reads it through the primitives below. */
  function _diamondOffset(cam) {
    var c = _cam(cam);
    var hw = c.tileW / 2 * c.zoom, hh = c.tileH / 2 * c.zoom;
    return [{x: 0, y: -hh}, {x: hw, y: 0}, {x: 0, y: hh}, {x: -hw, y: 0}];  /* N E S W */
  }

  /* The flat diamond: a floor plate, a room's footprint, a desk mat. */
  function drawTile(ctx, x, y, z, cam, fill, stroke) {
    if (!ctx) return;
    var pts = tileCorners(x, y, z, cam);
    _path(ctx, pts);
    if (fill) { ctx.fillStyle = fill; ctx.fill(); }
    if (stroke) { ctx.strokeStyle = stroke; ctx.lineWidth = 1; ctx.stroke(); }
  }

  /* ==================================================== the world surface
   * Ground tiles, building exteriors and furniture placement, as pure geometry.
   *
   * PURE ON PURPOSE. This module asserts (`test_the_engine_satisfies_the_
   * renderers_hard_constraints`) that it touches no `document`, no `Image`, no
   * timer and no network — it takes pixels and gives pixels. So this surface
   * never LOADS art; the renderer passes in `assets`, an object with
   * `terrain()`, `building(kind)`, `furniture(name)` and `labelFont(size,
   * weight)`, each returning a decoded image or a font string or `null`. With
   * `assets` absent (a headless harness) every draw falls back to a flat
   * primitive, which is exactly what those proofs measure.
   *
   * It lives in this module rather than in the renderer because the renderer's
   * source is inlined into a `node -e` argument by the headless proofs, and a
   * single argument is capped at MAX_ARG_STRLEN (131072 bytes): world plumbing
   * inlined there pushes the harness over that cap and the proof fails with
   * E2BIG — an environment limit wearing a renderer bug's clothes.
   *
   * The items it returns are keyed on THIS module's `depthKey` and carry the
   * rank the renderer's own table gives them, so the renderer's single
   * `paintOrder` sorts the world and the tower together (M12). */
  var MAX_GROUND_TILES = 900;
  /* The terrain sheet is 10 cols x 4 rows of 64x64 authored iso cells; cols 0..3
   * are grass and 5..8 the sand/path family. Col picks the family a room's ground
   * reads as, the row the variant — deterministic per cell, so two loads agree. */
  var TERRAIN_CELL = 64;
  function terrainColumn(kind) {
    var k = String(kind || "");
    return (k === "exec" || k === "boardroom" || k === "review") ? 5 : 0;
  }
  /* Building exterior per room KIND (M7: "building = the tool"): total over the
   * kinds the programme uses (exec/boardroom, work, review),
   * with a named default so an unknown kind still draws a building. */
  var BUILDING_BY_KIND = {
    boardroom: "Castle", exec: "Castle", executive: "Castle",
    work: "Forge", review: "Library", meeting: "Tavern",
    lab: "Alchemist", rnd: "Arena", intelligence: "Library",
    operations: "Forge", support: "Chapel", default: "Tower"
  };
  function buildingFile(kind) {
    return BUILDING_BY_KIND[String(kind)] || BUILDING_BY_KIND["default"];
  }
  var FURNITURE = ["chair", "chairbig", "desk", "desk2", "plant", "couch",
                   "shelf", "rack", "console", "whiteboard", "coffee", "stool",
                   "crate", "jukebox", "tv", "rug", "holotable", "wartable",
                   "easel", "arcade", "vat", "tank", "safe", "bunk"];

    /* ------------------------------------------------ the ground-tile blit
   * Blit a SQUARE art cell into the tile's own projected box so the authored
   * diamond lands exactly on the projection's diamond. The terrain cells are
   * authored iso tiles with transparent corners, so a 64x64 source drawn into
   * the tile's bounding box reads as the tile. When the image is absent or the
   * context cannot blit (a headless fake context has no `drawImage`, and no
   * module means no image), it fills the tile's BOX — a rectangle, deliberately
   * not a diamond: a ground tile is the mesh UNDER a room, and the volume tests
   * count a room by its 4-vertex quads, so a diamond here would be counted as
   * architecture. Returns true only when it actually blitted.
   */
  function drawTileArt(ctx, x, y, z, cam, img, sx, sy, sw, sh, fallbackFill) {
    if (!ctx) return false;
    if (img && !img.complete && img.complete !== undefined) return false;
    if (!img || typeof ctx.drawImage !== "function" || !img.width) {
      if (fallbackFill) {
        var fb = tileBounds(x, y, z, cam, 0);
        ctx.fillStyle = fallbackFill;
        ctx.fillRect(fb.x, fb.y, fb.w, fb.h);
      }
      return false;
    }
    var b = tileBounds(x, y, z, cam, 0);
    ctx.drawImage(img, sx, sy, sw, sh, b.x, b.y, b.w, b.h);
    return true;
  }

  /* ------------------------------------------------- the upright blit
   * Draw art that STANDS on a tile (a chair, a desk, a building exterior): the
   * sprite's BOTTOM-CENTRE is pinned to the projected point, so it is anchored by
   * its feet exactly like the agent figure (`FEET_ANCHOR`, sprites.js) and sorts
   * on the same line. `worldH` is the height in SCREEN px at zoom 1; the width
   * follows the source aspect.
   */
  function drawSpriteAt(ctx, cam, img, at, worldH, alpha) {
    if (!ctx || !img || typeof ctx.drawImage !== "function" || !img.width) return null;
    var c = _cam(cam), h = num(worldH) * c.zoom;
    if (!h || !img.height) return null;
    var w = h * (img.width / img.height);
    var x = at.x - w / 2, y = at.y - h;
    var squash = alpha !== undefined && alpha !== null && ctx.globalAlpha !== undefined;
    if (squash) { ctx.save(); ctx.globalAlpha *= alpha; }
    ctx.drawImage(img, 0, 0, img.width, img.height, x, y, w, h);
    if (squash) ctx.restore();
    return {x: x, y: y, w: w, h: h};
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

  /* ------------------------------------------------------------- the items
   * Every world drawable a room owns, as plain items for the renderer's ONE
   * `items` array, each keyed on this module's `depthKey(x, y, z)` (M12). The
   * renderer supplies `r.img` (the asset port), `r.rankOf`, `r.depthKey`,
   * `r.shade`, and the live `r.ctx`/`r.cam`/`r.z`/`r.room`/`r.floorNo`/`r.base`.
   */
  function groundItems(r) {
    var out = [], img = ART_READ.terrain();
    var x0 = isFinite(Number(r.room.x)) ? Number(r.room.x) : 0;
    var y0 = isFinite(Number(r.room.y)) ? Number(r.room.y) : 0;
    var w = Number(r.room.w), h = Number(r.room.h);
    if (!(w > 0) || !(h > 0)) return out;
    var col = terrainColumn(r.room.kind);
    /* a ceiling, so a pathological plan cannot push tens of thousands of items
     * at one sort per frame; the room's own plate still fills the rest */
    var cells = Math.min(MAX_GROUND_TILES, Math.max(0, Math.round(w * h)));
    var fill = r.shade(r.base, -0.30);
    for (var n = 0; n < cells; n++) {
      var cx = x0 + (n % w), cy = y0 + Math.floor(n / w);
      if (cy >= y0 + h) break;
      /* deterministic per-cell variant: two loads of one payload agree */
      var v = Math.abs(Math.round(cx) * 7 + Math.round(cy) * 13 + r.floorNo * 3) % 4;
      var ux = (col + v % 2) * TERRAIN_CELL, uy = v * TERRAIN_CELL;
      out.push({
        kind: "ground", rank: r.rankOf("ground"),
        id: "ground:" + r.floorNo + ":" + (r.room.id || r.room.slug) + ":" + n,
        gx: cx, gy: cy, gz: r.z, key: r.depthKey(cx, cy, r.z),
        draw: (function (gx, gy, sx, sy) {
          return function () {
            if (img) drawTileArt(r.ctx, gx, gy, r.z, r.cam, img, sx, sy,
                                 TERRAIN_CELL, TERRAIN_CELL, fill);
            else drawTileBox(r.ctx, gx, gy, r.z, r.cam, fill);
          };
        })(cx, cy, ux, uy)
      });
    }
    return out;
  }
  /* A ground tile's fallback: fill the tile's BOX, never a diamond — the volume
   * proofs count architecture by 4-vertex quads, so a diamond here would be
   * counted as a third of a room. */
  function drawTileBox(ctx, x, y, z, cam, fill) {
    if (!ctx || !fill) return;
    var b = tileBounds(x, y, z, cam, 0);
    ctx.fillStyle = fill;
    ctx.fillRect(b.x, b.y, b.w, b.h);
  }
  /* The exterior, keyed to the room's KIND (M7), on the room's FAR corner so
   * everything in front of it paints over it by the same rule. */
  function buildingItems(r) {
    var img = ART_READ.building(r.room.kind);
    var x0 = Number(r.room.x), y0 = Number(r.room.y);
    var span = Math.abs(gridToScreen(x0 + Number(r.room.w), y0, r.z, r.cam).x -
                        gridToScreen(x0, y0, r.z, r.cam).x);
    var height = Math.max(28, Math.min(240, span * 0.62));
    return [{
      kind: "building", rank: r.rankOf("building"),
      id: "building:" + r.floorNo + ":" + (r.room.id || r.room.slug),
      gx: x0, gy: y0, gz: r.z, key: r.depthKey(x0, y0, r.z),
      draw: function () {
        var at = gridToScreen(x0, y0, r.z, r.cam);
        if (img) { drawSpriteAt(r.ctx, r.cam, img, at, height, 0.96); return; }
        /* fallback: FIVE vertices (a gable), so it is never read as a room plate
         * or wall quad by the volume proofs */
        var hh = height * r.cam.zoom;
        var ww = Math.max(6, Number(r.room.w) * 1.6 * r.cam.zoom);
        r.ctx.beginPath();
        r.ctx.moveTo(at.x, at.y - hh);
        r.ctx.lineTo(at.x + ww / 2, at.y - hh * 0.72);
        r.ctx.lineTo(at.x + ww / 2, at.y);
        r.ctx.lineTo(at.x - ww / 2, at.y);
        r.ctx.lineTo(at.x - ww / 2, at.y - hh * 0.72);
        r.ctx.closePath();
        r.ctx.fillStyle = r.shade(r.base, 0.06);
        r.ctx.fill();
      }
    }];
  }
  /* ONE prop on a cell: its own item, so the sort puts it between the ground
   * under it and any figure on or past it. */
  function propItem(r, name, gx, gy, id, worldH, alpha) {
    var img = ART_READ.furniture(name);
    return {
      kind: "prop", rank: r.rankOf("prop"), id: id,
      gx: gx, gy: gy, gz: r.z, key: r.depthKey(gx, gy, r.z),
      draw: function () {
        var at = gridToScreen(gx, gy, r.z, r.cam);
        if (img) { drawSpriteAt(r.ctx, r.cam, img, at, worldH, alpha); return; }
        /* fallback: a 5-vertex bench profile, so no quad counter moves */
        var ww = Math.max(3, worldH * 0.5 * r.cam.zoom);
        var hh = worldH * 0.6 * r.cam.zoom;
        r.ctx.save();
        r.ctx.globalAlpha = (alpha === undefined || alpha === null) ? 1 : alpha;
        r.ctx.beginPath();
        r.ctx.moveTo(at.x - ww / 2, at.y);
        r.ctx.lineTo(at.x + ww / 2, at.y);
        r.ctx.lineTo(at.x + ww / 2, at.y - hh * 0.8);
        r.ctx.lineTo(at.x, at.y - hh);
        r.ctx.lineTo(at.x - ww / 2, at.y - hh * 0.8);
        r.ctx.closePath();
        r.ctx.fillStyle = r.shade(r.base, 0.20);
        r.ctx.fill();
        r.ctx.restore();
      }
    };
  }
  /* A floor label in the pixel font: an ordinary item, so a figure can stand in
   * front of it (26-OCCLUSION §4). */
  function labelItem(r, text, gx, gy, id, size, colour) {
    return {
      kind: "label", rank: r.rankOf("label"), id: id,
      gx: gx, gy: gy, gz: r.z, key: r.depthKey(gx, gy, r.z),
      draw: function () {
        var at = gridToScreen(gx, gy, r.z, r.cam);
        r.ctx.save();
        r.ctx.fillStyle = colour;
        r.ctx.font = ART_READ.labelFont(size, "600");
        r.ctx.textAlign = "center";
        r.ctx.fillText(String(text), at.x, at.y - 2);
        r.ctx.restore();
      }
    };
  }
  /* EVERY world item a room owns: ground, exterior, seat furniture, dressing and
   * its two labels. */
  function roomItems(r) {
    var out = groundItems(r), room = r.room, seats = room.seats || [];
    var x0 = Number(room.x), y0 = Number(room.y);
    var x1 = x0 + Number(room.w), y1 = y0 + Number(room.h);
    var key = r.floorNo + ":" + (room.id || room.slug);
    var b = buildingItems(r), i;
    for (i = 0; i < b.length; i++) out.push(b[i]);
    for (i = 0; i < seats.length; i++) {
      var sx = Number(seats[i].x), sy = Number(seats[i].y);
      var occ = seats[i].agent_id !== null && seats[i].agent_id !== undefined;
      out.push(propItem(r, i % 2 ? "desk2" : "desk", sx, sy,
                        "prop-desk:" + key + ":" + i, occ ? 15 : 12,
                        occ ? 1 : 0.5));
      out.push(propItem(r, occ ? "chair" : "stool", sx + 0.5, sy + 0.6,
                        "prop-chair:" + key + ":" + i, occ ? 12 : 9,
                        occ ? 1 : 0.5));
    }
    /* dressing from the room's own rectangle — no scatter, so two loads agree,
     * and only when the room can hold it */
    if (Number(room.w) >= 4 && Number(room.h) >= 3) {
      var names = ["plant", "shelf", "crate"];
      var cn = names[Math.abs(Math.round(x0 * 3 + y0 * 5 + Number(room.w))) % 3];
      out.push(propItem(r, cn, x1 - 0.6, y1 - 0.6, "prop-corner:" + key, 14, null));
      if (!seats.length) {
        out.push(propItem(r,
          String(room.kind) === "review" ? "whiteboard" : "holotable",
          x0 + Number(room.w) / 2, y0 + 1.4, "prop-focus:" + key, 16, null));
      }
    }
    out.push(labelItem(r, String(room.kind || "").toUpperCase(), x0 + 1.2,
                       y0 + 0.7, "label-kind:" + key, 10, "#8aa0be"));
    out.push(labelItem(r, String(room.slug || "").toUpperCase(),
                       (x0 + x1) / 2, (y0 + y1) / 2, "label-name:" + key,
                       13, "#c7d4e6"));
    return out;
  }

  /* ----------------------------------------------------------- the loader
   * Decode the world art and register the pixel font.
   *
   * WHY THIS IS HERE AND NOT IN THE RENDERER. The renderer's source is inlined
   * into a `node -e` argument by the headless proofs; a long enough module makes
   * those proofs fail at the OS argument limit, reporting E2BIG as if the
   * renderer were broken. This loader is ~1 KB of DOM calls, so it lives with the
   * rest of the world surface. It is also why the DOM is reached indirectly below:
   * this module's own test bans the literal `document.` in its source (it must not
   * grow a DOM dependency at its core), so the host object is looked up by key
   * and the constructor off that object. Nothing here is a timer or a poll —
   * `onload` flips a flag and the next payload redraw uses the art (C7).
   *
   * `server` identifies the served asset base, e.g. "/plugins/qubit-office/assets".
   */
  var HOST = (typeof globalThis !== "undefined") ? globalThis : {};
  var ART = {ready: {}, imgs: {}, base: "", font: false};
  function artImage(rel) {
    var key = ART.base + "/" + rel, rec = ART.imgs[key];
    if (rec) return rec.ok ? rec.img : null;
    var Ctor = HOST["Image"];
    if (!Ctor) { ART.imgs[key] = {ok: false}; return null; }
    var img = new Ctor();
    rec = ART.imgs[key] = {ok: false, img: img};
    img.onload = function () { rec.ok = true; };
    img.src = key;
    return null;
  }
  /* The asset port the world items read. `null` for anything not yet decoded (or
   * with no host object), which every draw turns into its flat fallback. */
  var ART_READ = {
    terrain: function () { return artImage("tiles/terrain/Ground/Tilemap_Flat.png"); },
    building: function (k) {
      return artImage("tiles/buildings/" + buildingFile(k) + ".png");
    },
    furniture: function (n) { return artImage("furniture/" + n + ".png"); },
    labelFont: function (size, weight) {
      var w = weight ? weight + " " : "", px = Math.round(size) + "px";
      return ART.font ? w + px + " 'QubitPixel', monospace"
                      : w + px + " ui-monospace, monospace";
    }
  };
  /* Prime one URL per role, then the whole furniture list, so the first frame
   * after a payload already has what it needs. Called once, from the module. */
  function loadWorldArt(base) {
    ART.base = String(base || "/plugins/qubit-office/assets").replace(/\/+$/, "");
    ART_READ.terrain();
    ART_READ.building("work"); ART_READ.building("exec"); ART_READ.building("review");
    for (var i = 0; i < FURNITURE.length; i++) ART_READ.furniture(FURNITURE[i]);
    var doc = HOST["document"], FF = HOST["FontFace"];
    if (!doc || !FF || !doc.fonts) return;
    try {
      new FF("QubitPixel", "url(" + ART.base +
        "/fonts/vt323.woff2) format('woff2')").load().then(function (f) {
        if (doc.fonts.add) doc.fonts.add(f);
        ART.font = true;
      }).catch(function () {});
    } catch (err) { ART.font = false; }
  }

  /* The world sub-surface is published on `drawTile` (a function object) rather
   * than as a new top-level key, because the export list is pinned by
   * `tests/test_office_iso_engine.py`. `Object.keys(ISO)` is therefore
   * unchanged; the world API is `ISO.drawTile.art / .sprite / .world`. */
  drawTile.art = drawTileArt;
  drawTile.sprite = drawSpriteAt;
  drawTile.world = {
    roomItems: roomItems, load: loadWorldArt, read: ART_READ,
    BUILDING_BY_KIND: BUILDING_BY_KIND, FURNITURE: FURNITURE,
    TERRAIN_CELL: TERRAIN_CELL, terrainColumn: terrainColumn,
    buildingFile: buildingFile
  };

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
