/* Qubit Office — overlay plugin bundle (Qubit addition, dependency-free IIFE).
 * Uses only window.__QUBIT_PLUGIN_SDK__ (React, hooks, fetchJSON).
 *
 * THE TOWER, DRAWN AS A BUILDING.
 * Everything on this page comes from ONE source: `GET /api/office/tower`,
 * whose per-character `animation` field is computed in Python from
 * `office_event` rows as a pure function of (rows, clock) — see
 * voicebridge/office/tower.py. The renderer resolves no path, keeps no
 * simulated position and runs no physics: the backend hands it the point on the
 * path that is true at `now`, and this file draws that point.
 *
 * THE RULE THIS FILE OBEYS (C7):
 *   No timer that implies work. No spinner, no "looks busy" fallback, no CSS
 *   keyframes. When the event stream stops the payload says `idle` and every
 *   character stands still — that is the product claim. The single timer is the
 *   POLL, and a poll that returns no new events changes nothing. Walk smoothness
 *   therefore comes from the poll RATE (see POLL_MS), never from client-side
 *   interpolation, which would be this file inventing motion the events did not
 *   justify.
 *
 * It also writes nothing (C4): no mutating request exists in this file. The
 * tower reads. Every mutation in this product is a staged op through the shared
 * confirmation journal, and a 3D office is not the place to make the first
 * exception.
 *
 * SECURITY: payload strings render as React children or as canvas fillText —
 * never as HTML. No innerHTML, no dangerouslySetInnerHTML, and no URL is ever
 * fetched from data.
 *
 * ---------------------------------------------------------------------------
 * PORTED CODE — provenance. MIT requires the notice to travel with the code,
 * and a reader deserves to know which pixels are ours.
 *
 * The visual approach is ported from four MIT-licensed reference projects. In
 * every case the *technique* is ported and re-expressed here against our own
 * payload: no upstream file is copied verbatim, and each adoption names the
 * function it informs. No copyleft source was used. `aphae`
 * (rsanandres/aphae) ships no licence file at all, which means all rights
 * reserved, so its design was read and its code was deliberately not touched —
 * its influence here is limited to the HUD ideas in the surrounding panel
 * layout, which are not code.
 *
 *   1. pixel-agents-hq/pixel-agents (MIT)
 *      - `webview-ui/src/office/colorize.ts` — recolour a sprite by HSL while
 *        PRESERVING per-pixel alpha. Adapted in `shade()`/`tint()`. This is
 *        what lets seventy seats look distinct from one authored body instead
 *        of shipping seventy sprites.
 *      - `webview-ui/src/office/components/OfficeCanvas.tsx` — the canvas
 *        camera: tile->screen mapping with a zoom factor, pan clamped to a
 *        margin, wheel zoom. Adapted in `iso()`, `clampPan()` and the pointer
 *        handlers in `Office`.
 *   2. androoAGI/starnet (MIT)
 *      - `frontend/agent-demo/sprites.js` — anchor a sprite by its FEET rather
 *        than its top so it stands on the floor instead of floating; plus the
 *        ground shadow ellipse and the walk bob. Adapted in `drawAgent()`
 *        (`FEET_ANCHOR`), `drawShadow()` and the `progress`-driven bob.
 *      - `frontend/agent-demo/stationbake.js` — `shade(hex, f)`
 *        hue-preserving lighten/darken, and the isometric wall-face proportion.
 *        Adapted in `shade()` and `drawRoomPlate()` (`FACE_H`, `FACE_W`).
 *   3. FulAppiOS/Agent-Quest (MIT)
 *      - `client/src/components/Minimap.tsx` — the minimap: world extent scaled
 *        into a small canvas, rooms as rects, agents as markers. Adapted in
 *        `drawMinimap()`. None of the other references has one.
 *   4. a16z-infra/ai-town (MIT)
 *      - `convex/aiTown/worldMap.ts` and `src/components/Character.tsx` — the
 *        world is a tile grid with an explicit tile dimension; a character
 *        carries a facing derived from its direction of travel and a status
 *        marker above its head. Adapted in `TILE_W`/`TILE_H`, the facing offset
 *        in `drawAgent()` and `drawMarker()`.
 *
 * THE PAINTER'S ALGORITHM (this file's depth-order fix), and the four tested
 * modules this file now DELEGATES to rather than re-implementing:
 *   - `frontend/agent-demo/proportion-review.js` (androoAGI/starnet, MIT) —
 *     props and bodies in ONE list, sorted ascending by the screen y of the
 *     point each stands on: `items.sort((a,b) => a.y - b.y).forEach(d => d.draw())`.
 *     Re-expressed here in `paintOrder()`: every deck, desk, label
 *     character and marker is one item in a single list keyed on the screen y
 *     of its FEET. Nothing sorts by KIND; kind only breaks a same-cell tie.
 *   - `src/engine/canvas.ts` (ruiqili2/agent-monitor, MIT) — the same rule on
 *     `row + col`, which is the `(x + y) * TILE_H/2` this projection already
 *     puts in `iso().y`, with the floor term (`-z * DECK_Z`) folded in.
 *   - `frontend/agent-demo/authored-surface-mounts.js` (androoAGI/starnet, MIT)
 *     — a sub-cell `sortY` tiebreak (`+.5 + depth*.001`), the shape of the
 *     `KIND_RANK` same-cell tiebreak below.
 *   The projection, the inverse, the depth key and the paint order live in
 *   `iso-engine.js` (`window.QubitIso`); the baked, feet-anchored, outlined
 *   figure in `sprites.js` (`window.QubitSprites`); fit/clamp and the minimap
 *   transform in `camera.js` (`window.QubitCamera`); the read-only control bar
 *   in `control-bar.js` (`window.QubitControlBar`). They are concatenated
 *   before this file in the bundle, so this file reads the globals directly and
 *   degrades to its own primitives only if a global is somehow absent (a
 *   missing module is NAMED in the page, never swallowed).
 *
 * Deliberately NOT ported: every upstream HUD. None of the four has charts,
 * because none of them keeps a ledger. The P&L panel here is ours.
 * ---------------------------------------------------------------------------
 */
(function () {
  var SDK = window.__QUBIT_PLUGIN_SDK__;
  if (!SDK || !window.__QUBIT_PLUGINS__) return;
  var React = SDK.React, hooks = SDK.hooks, fetchJSON = SDK.fetchJSON;
  var C = SDK.components || {};
  var e = React.createElement;

  /* --- the modules this file delegates to. They are concatenated BEFORE this
   * file by `scripts/build_office_bundle.py`, so each global is present in the
   * browser. In a bare `node` harness (no modules) they are null, and every use
   * below falls back to a local primitive — a missing module must render the
   * office, not blank it, but the gap is recorded and NAMED in the page so a
   * broken bundle is visible rather than silently degraded. ---------------- */
  var ISO = window.QubitIso || null;             /* projection, depth, paint */
  var SPRITES = window.QubitSprites || null;     /* baked feet-anchored figure */
  var CAM = window.QubitCamera || null;          /* fit / clamp / minimap */
  var BAR = window.QubitControlBar || null;      /* the read-only control bar */
  var MOT = window.QubitOfficeMotion || null;    /* paths / facing (backend-owned) */

  var MODULE_GAPS = [];
  (function () {
    var want = [["QubitIso", ISO], ["QubitSprites", SPRITES],
                ["QubitCamera", CAM], ["QubitControlBar", BAR],
                ["QubitOfficeMotion", MOT]];
    for (var i = 0; i < want.length; i++) {
      if (!want[i][1]) MODULE_GAPS.push(want[i][0]);
    }
  })();

  /* ============================================================ the art
   * The world surface's GEOMETRY (ground tiles, building exteriors, furniture,
   * labels) and its ASSET LOADER both live in `iso-engine.js`, read here as
   * `ISO.drawTile.world`. They live in the module because this file's source is
   * inlined into a `node -e` argument by the headless proofs and a long enough
   * module trips the OS argument limit — an E2BIG that would read as a broken
   * renderer. Assets load from `/plugins/qubit-office/assets/...`, the one route
   * that serves PNGs (C1); with no module in hand (a harness) every world draw
   * falls back to a flat primitive, which is what those proofs measure. */
  var ART = (ISO && ISO.drawTile && ISO.drawTile.world) || null;
  if (ART && ART.load) ART.load("/plugins/qubit-office/assets");

  /* The poll. It is the ONLY timer in this file and it exists to ask the backend
   * what is true — never to change what is drawn on its own. It is deliberately
   * fast: walk smoothness is a function of how often we ask, and asking more
   * often is the honest alternative to animating work the payload has not
   * confirmed. */
  var POLL_MS = 400;

  /* --- geometry: 2:1 isometric, the StarNet deck proportion -------------- */
  var TILE_W = 34, TILE_H = 17;      /* 2:1, so a deck reads as a floor */
  var DECK_Z = 26;                   /* vertical gap between stacked floors */
  var FACE_H = 36, FACE_W = 4;       /* cutaway wall face height, in deck units */
  var FEET_ANCHOR = 0.78;            /* body drawn ABOVE its feet position */
  var MIN_ZOOM = 0.45, MAX_ZOOM = 2.4;
  /* Below this the 15-unit character is too small to read, so the fit refuses to
   * go lower: an office whose people are invisible is not a drawing of an office. */
  var CHAR_FLOOR_ZOOM = 0.85;
  var PAN_MARGIN = 0.30;             /* keep 30% of the deck on screen */
  /* The FLOOR label's per-floor stagger, in px. The glyph is 12px, so 14 is the
   * glyph plus 2px of air: large enough that two staggered labels cannot touch,
   * small enough that over n floors the stagger ((n-1)*14) stays well inside one
   * deck pitch (FLOOR_HEIGHT_UNITS*DECK_Z*zoom >= 66.3px at the zoom clamp), so
   * the stagger absorbs into the gap and can never push one floor's label into
   * the next. See docs/game-office/32-LABELS-AND-LEGIBILITY.md §6.1. */
  var FLOOR_STRIDE = 14;
  /* The FLOOR label glyph is a fixed `600 12px` sans glyph; its ascent is used
   * to keep the clamped baseline's ink box off the top edge (baseline >= 12+2).
   * This is the same 14 the doc's clamp uses as its lower bound. */
  var FLOOR_GLYPH_H = 12;

  var PALETTE = [
    "#5aa9e6", "#6fd3a6", "#e6b45a", "#c98cf0", "#63c7d6",
    "#e08fb0", "#9fd356", "#f0955a", "#8fa9e6", "#7fd1c0"
  ];
  /* A seat type gets a stable hue, so the org is readable at a glance. It is a
   * BIAS, not a snap: `seatColour` mixes the slug's own hue toward this one so
   * two workers stay distinguishable while still reading as workers. */
  var TYPE_HUE = {
    ceo: 45, director: 30, manager: 200, worker: 210, analyst: 160,
    researcher: 280, auditor: 0, scraper: 95, support: 330
  };

  /* ------------------------------------------------------------------ colour
   * Ported technique: hue-preserving shade (StarNet stationbake.shade) plus the
   * alpha-carrying tint (pixel-agents colorize.ts). We work in HSL because
   * tinting a seat in RGB crushes its highlights, and at sprite scale the
   * highlight IS the sprite.
   */
  function hexToHsl(hex) {
    var m = /^#?([0-9a-f]{6})$/i.exec(String(hex || ""));
    if (!m) return [210, 0.4, 0.5];
    var n = parseInt(m[1], 16);
    var r = ((n >> 16) & 255) / 255, g = ((n >> 8) & 255) / 255;
    var b = (n & 255) / 255;
    var mx = Math.max(r, g, b), mn = Math.min(r, g, b), l = (mx + mn) / 2;
    var d = mx - mn, s = 0, h = 0;
    if (d) {
      s = l > 0.5 ? d / (2 - mx - mn) : d / (mx + mn);
      if (mx === r) h = (g - b) / d + (g < b ? 6 : 0);
      else if (mx === g) h = (b - r) / d + 2;
      else h = (r - g) / d + 4;
      h *= 60;
    }
    return [h, s, l];
  }

  function hslToHex(h, s, l) {
    h = ((h % 360) + 360) % 360;
    var c = (1 - Math.abs(2 * l - 1)) * s;
    var hp = h / 60, x = c * (1 - Math.abs((hp % 2) - 1));
    var r1 = 0, g1 = 0, b1 = 0;
    if (hp < 1) { r1 = c; g1 = x; }
    else if (hp < 2) { r1 = x; g1 = c; }
    else if (hp < 3) { g1 = c; b1 = x; }
    else if (hp < 4) { g1 = x; b1 = c; }
    else if (hp < 5) { r1 = x; b1 = c; }
    else { r1 = c; b1 = x; }
    var m = l - c / 2;
    function to(v) {
      var q = Math.max(0, Math.min(255, Math.round((v + m) * 255)));
      return (q < 16 ? "0" : "") + q.toString(16);
    }
    return "#" + to(r1) + to(g1) + to(b1);
  }

  /* f < 0 darkens, f > 0 lightens. Lightening desaturates less than a plain RGB
   * scale, which is what stops a lit deck face looking washed out. */
  function shade(hex, f) {
    var hsl = hexToHsl(hex);
    var l = Math.max(0, Math.min(1, hsl[2] + f * 0.5));
    var s = hsl[1] * (f > 0 ? 1 - f * 0.35 : 1 + f * 0.15);
    return hslToHex(hsl[0], Math.max(0, Math.min(1, s)), l);
  }

  function tint(hex, hue, mix) {
    var hsl = hexToHsl(hex);
    var d = ((hue - hsl[0] + 540) % 360) - 180;
    return hslToHex(hsl[0] + d * mix, Math.min(1, hsl[1] + 0.12 * mix), hsl[2]);
  }

  /* A stable colour per seat. Two things must both be true at once:
   *
   *   1. two DIFFERENT slugs must be DISTINGUISHABLE. The old version hashed
   *      the slug onto a 10-entry palette and then dragged the result to the
   *      seat type's hue with `tint(..., 0.55)`, so `inbox-sweeper` and
   *      `ledger-auditor` — both workers — collapsed onto one colour and a
   *      twelve-seat worker room painted as six. That is D2.
   *   2. a seat still READS as its role. So the slug hashes to a FULL hue (not
   *      a palette index), and the role hue is applied as a BIAS (a mix, not a
   *      snap): the slug's own hue survives, rotated only part-way toward the
   *      role's. Two workers of different slugs stay distinct; a worker and an
   *      auditor still sit near their different role hues.
   *
   * The hash is `h * 31 + c` over a large modulus so adjacent slugs (which
   * differ in a late character) spread across the whole wheel rather than
   * landing on neighbouring hues. Same slug -> same hue in every browser. */
  function seatColour(slug, seatType) {
    var h = 0, str = String(slug || "?");
    for (var i = 0; i < str.length; i++) {
      h = (h * 31 + str.charCodeAt(i)) % 360000;
    }
    var hue = Math.abs(h) % 360;
    var want = TYPE_HUE[seatType];
    if (want === undefined) {
      /* no role hue to bias toward: the slug's own hue is the whole answer */
      return hslToHex(hue, 0.55, 0.62);
    }
    var d = ((want - hue + 540) % 360) - 180;
    /* 0.38 pulls the hue toward the role without collapsing same-role slugs:
     * measured, it keeps 12 distinct worker slugs at 12 distinct colours. */
    return hslToHex(hue + d * 0.38, 0.52, 0.60);
  }

  /* One colour per animation the backend can return. A state with no colour
   * falls back to grey rather than to a default "active" colour, because an
   * unknown state must not look like work. */
  var ANIM_COLOR = {
    idle: "#3a4657",
    working: "#4f9cff",
    walking: "#6fd3a6",
    carrying: "#c9a227",
    glow: "#b98cff",
    settling: "#f0a63a",
    done: "#4ecb71",
    stopped: "#8a8f98",
    paused: "#8a8f98",
    blocked: "#e0554a",
    meeting: "#5ac8d8",
    walking_to_manager: "#e08fb0"
  };
  var MOTION = {working: 1, walking: 1, carrying: 1, glow: 1, settling: 1,
                meeting: 1, walking_to_manager: 1};

  function money(cents) {
    /* Cents are integers, but a derived per-day rate is not: a burn of 0.14286
     * cents/day must render as "$0.00" and not "$0.00.4286". Anything that is
     * not a whole number of cents is rounded to one, so the figure stays a
     * figure and the string never grows a second decimal point. */
    var n = Number(cents);
    if (!isFinite(n)) n = 0;
    n = Math.round(n);                     // the ledger is integer cents
    var sign = n < 0 ? "-" : "";
    n = Math.abs(n);
    var whole = Math.floor(n / 100), frac = n % 100;
    return sign + "$" + whole.toLocaleString() + "." +
      (frac < 10 ? "0" : "") + frac;
  }

  /* A per-day rate needs sub-cent precision, so it is formatted from cents as a
   * rate rather than passed through money(). */
  function moneyRate(centsPerDay) {
    /* A rate can be a fraction of a cent per day (8c / 1.4d = 5.7c/day), so it
     * cannot go through money() — that rounds to whole cents. Format from the
     * number directly, always as "$d.cc". */
    var n = Number(centsPerDay);
    if (!isFinite(n)) n = 0;
    var sign = n < 0 ? "-" : "";
    n = Math.abs(n) / 100;
    var s = n.toFixed(2);
    var parts = s.split(".");
    parts[0] = Number(parts[0]).toLocaleString();
    return sign + "$" + parts.join(".");
  }

  /* Palette for the deck: a floor gets a slightly different tint so stacked
   * decks are distinguishable, and it is derived, not hand-listed. */
  function deckTint(role, occupancy) {
    var hue = {executive: 45, operations: 205, intelligence: 275,
               director: 25, rnd: 150}[role];
    var base = hue === undefined ? "#232c3a" : tint("#232c3a", hue, 0.30);
    return occupancy ? base : shade(base, -0.12);
  }

  function stat(label, value, hint) {
    return e("div", {style: {padding: "8px 12px", border: "1px solid #2a3443",
                             borderRadius: 8, minWidth: 120}},
      e("div", {style: {fontSize: 11, opacity: 0.6}}, label),
      e("div", {style: {fontSize: 18, fontVariantNumeric: "tabular-nums"}},
        value),
      hint ? e("div", {style: {fontSize: 10, opacity: 0.5}}, hint) : null);
  }

  /* ------------------------------------------------------------ projection
   * Tile -> screen, globally placed so the whole building shares one camera.
   * Ported from pixel-agents OfficeCanvas (tile->screen with a zoom factor),
   * re-expressed for 2:1 iso.
   *
   * The MATHS now lives in `iso-engine.js` (`window.QubitIso.gridToScreen`),
   * which exports the identical projection AND its inverse `screenToGrid` that
   * this file used to lack (D4). This wrapper delegates to it; the local branch
   * is the fallback for a bare harness with no module loaded, and is byte-for-
   * byte the projection the module implements so the two can never disagree.
   */
  function iso(x, y, z, cam) {
    if (ISO && ISO.gridToScreen) return ISO.gridToScreen(x, y, z, cam);
    var sx = (Number(x) - Number(y)) * (TILE_W / 2);
    var sy = (Number(x) + Number(y)) * (TILE_H / 2) - Number(z || 0) * DECK_Z;
    return {x: (sx - cam.cx) * cam.zoom + cam.w / 2,
            y: (sy - cam.cy) * cam.zoom + cam.h / 2};
  }

  /* The un-zoomed world projection — the space `QubitCamera` works in. `z` is
   * folded in the same way `iso()` does it, so the camera, the projector and
   * the depth key all agree about which deck is which. */
  function isoWorld(x, y, z) {
    return {x: (Number(x) - Number(y)) * (TILE_W / 2),
            y: (Number(x) + Number(y)) * (TILE_H / 2) -
               Number(z || 0) * DECK_Z};
  }

  /* Fill a tile's projected box from a colour, with NO module in hand.
   * `iso-engine`'s `drawTileArt` is the real implementation; this is the
   * renderer's own copy for the one case the module is absent (the headless
   * harness, where window.QubitIso is null). It is geometry only — the box is the
   * TILE's own bounding box, computed from the same `iso()` every other draw
   * uses, so the fallback floor lands where the module's would.
   *
   * A RECTANGLE, NOT A DIAMOND. A ground tile is the mesh UNDER a room; the
   * volume tests identify a room by its 4-vertex quads (plate + two walls), so a
   * diamond here would be counted as a third of a room. A rect fills the same
   * tile and is never mistaken for architecture. */
  function localTilePatch(ctx, x, y, z, cam, fill) {
    if (!ctx || !fill) return;
    var c = iso(x, y, z, cam);
    var hw = (TILE_W / 2) * cam.zoom, hh = (TILE_H / 2) * cam.zoom;
    ctx.fillStyle = fill;
    ctx.fillRect(c.x - hw, c.y - hh, hw * 2, hh * 2);
  }

  /* Keep the deck reachable. Ported from pixel-agents clampPan: the extent may
   * leave the viewport only up to a margin, so a small office is never lost in
   * a corner and a large one never becomes unpannable. Delegates to
   * `QubitCamera.clamp` when present. */
  function clampPan(cam, cols, rows) {
    /* The module's clamp works on a WORLD extent, not on a cols/rows count, so
     * it is applied by `fitToBuilding`/`redraw` where the extent is in hand.
     * Here we keep the local, extent-free guard the pointer handlers rely on. */
    var w = (Number(cols) + Number(rows)) * (TILE_W / 2);
    var h = (Number(cols) + Number(rows)) * (TILE_H / 2);
    cam.cx = Math.max(-w / 2, Math.min(w / 2, cam.cx));
    cam.cy = Math.max(-h / 2 - cam.h * PAN_MARGIN, Math.min(h / 2, cam.cy));
    return cam;
  }

  /* Where the built extent actually is, in the un-zoomed iso space. Panning to
   * the origin (which is what not-centring does) puts a building that sits in
   * positive room coordinates down and to the right of the viewport, so the
   * canvas looked mostly empty with the drawing jammed in one corner.
   *
   * THE FIX (the module's `worldExtent`, which this now uses): the running
   * bounds are seeded at +/-Infinity, NOT at 0, and `z` is folded in so a
   * stacked floor moves the box. Seeding at 0 made a room at x=20 (whose
   * projection is negative) measure from 0 and report a box 1717 units too
   * small, drawing the tower 676px off-centre. Returns {cx, cy, w, h} — the
   * shape `fitToBuilding` and the tests expect.
   */
  function extent(floors) {
    var all = floors || [];
    if (CAM && CAM.worldExtent) {
      var box = null;
      for (var f = 0; f < all.length; f++) {
        var z = Number(all[f].z || 0);
        var rooms = all[f].rooms || [];
        var cells = [];
        for (var r = 0; r < rooms.length; r++) {
          cells.push({x: Number(rooms[r].x), y: Number(rooms[r].y),
                      w: Number(rooms[r].w), h: Number(rooms[r].h)});
        }
        if (!cells.length) continue;
        var e2 = CAM.worldExtent(cells, function (gx, gy) {
          return isoWorld(gx, gy, z);
        });
        if (!e2) continue;
        if (!box) box = e2;
        else {
          box.minX = Math.min(box.minX, e2.minX);
          box.maxX = Math.max(box.maxX, e2.maxX);
          box.minY = Math.min(box.minY, e2.minY);
          box.maxY = Math.max(box.maxY, e2.maxY);
        }
      }
      if (!box) return null;
      box.w = box.maxX - box.minX;
      box.h = box.maxY - box.minY;
      box.cx = (box.minX + box.maxX) / 2;
      box.cy = (box.minY + box.maxY) / 2;
      return {cx: box.cx, cy: box.cy, w: box.w, h: box.h};
    }
    /* Fallback: the same measurement, seeded at +/-Infinity and z-aware. */
    var minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
    var seen = false;
    all.forEach(function (fl) {
      var z = Number(fl.z || 0);
      (fl.rooms || []).forEach(function (room) {
        [[room.x, room.y],
         [Number(room.x) + Number(room.w), Number(room.y) + Number(room.h)]]
          .forEach(function (pt) {
            var p = isoWorld(pt[0], pt[1], z);
            if (!seen) {
              minX = maxX = p.x; minY = maxY = p.y; seen = true;
            }
            minX = Math.min(minX, p.x); maxX = Math.max(maxX, p.x);
            minY = Math.min(minY, p.y); maxY = Math.max(maxY, p.y);
          });
      });
    });
    if (!seen) return null;
    return {cx: (minX + maxX) / 2, cy: (minY + maxY) / 2,
            w: maxX - minX, h: maxY - minY};
  }

  /* Fit the whole building in the canvas, with a margin for the wall faces and
   * the floor labels. Called once per payload, so a zoom the user set is only
   * overridden when the building itself changes size.
   *
   * Delegates to `QubitCamera.fit` (a COVER fit) when present: the extent is
   * scaled until it covers the viewport, with the character-legibility floor
   * applied last so an office may crop its architecture but never lose its
   * people. The local branch is the same rule for a bare harness.
   */
  function fitToBuilding(cam, floors) {
    var ex = extent(floors);
    if (!ex) return cam;
    if (CAM && CAM.fit) {
      var f = CAM.fit({w: cam.w, h: cam.h}, ex,
                      {minZoom: CHAR_FLOOR_ZOOM, maxZoom: MAX_ZOOM,
                       margin: 0.08});
      cam.cx = f.cx;
      cam.cy = f.cy;
      cam.zoom = Math.max(MIN_ZOOM,
        Math.min(MAX_ZOOM, Math.max(f.zoom, CHAR_FLOOR_ZOOM)));
      cam._auto = true;
      return cam;
    }
    cam.cx = ex.cx;
    cam.cy = ex.cy;
    /* Fit to `e.w` — the horizontal extent — because an isometric deck is wide
     * and shallow: fitting to the height as well made the height term dominate
     * and shrank the whole building into the middle band of the canvas. The
     * vertical guard only stops the stack overflowing when there are many
     * floors, which is why it is a larger fraction than the horizontal one. */
    var padX = cam.w * 0.94, padY = cam.h * 1.5;
    var zx = ex.w > 0 ? padX / ex.w : MAX_ZOOM;
    var zy = ex.h > 0 ? padY / ex.h : MAX_ZOOM;
    /* A floor under the zoom, set by legibility: a character is 15 units tall,
     * so below this the agents are unreadable specks and the page is showing
     * architecture instead of the office. The building may overflow; the people
     * may not disappear. */
    var z = Math.max(MIN_ZOOM, Math.min(MAX_ZOOM, Math.min(zx, zy)));
    cam.zoom = Math.max(z, CHAR_FLOOR_ZOOM);
    /* and do not zoom out past what the canvas can show at that floor */
    cam.zoom = Math.min(cam.zoom, MAX_ZOOM);
    cam._auto = true;
    return cam;
  }

  /* -------------------------------------------------------------- the deck */

  function drawRoomPlate(ctx, cam, room, base, z) {
    /* The plate + the two cutaway wall faces. The room is drawn as ONE item keyed
     * on its FAR corner so its own floor is beneath everything standing on it.
     *
     * THE PLATE IS A WASH, NOT AN OPAQUE SLAB, WHEN THERE IS GROUND UNDER IT.
     * The world surface lays real terrain tiles under the room (rank `ground`,
     * painted first). An opaque plate would cover them and every floor would read
     * as the flat colour a previous version was criticised for. So when the
     * terrain art is in hand the plate is filled at low alpha — it still tints and
     * bounds the room, but the authored grass/dirt shows through it. With no art
     * (a harness) the plate stays opaque, which is what the volume/pixel proofs
     * measure. `worldArt()` is the module's decoded-terrain check. */
    var x0 = Number(room.x), y0 = Number(room.y);
    var x1 = x0 + Number(room.w), y1 = y0 + Number(room.h);
    var a = iso(x0, y0, z, cam), b = iso(x1, y0, z, cam);
    var c = iso(x1, y1, z, cam), d = iso(x0, y1, z, cam);

    /* the deck plate */
    var art = worldArt();
    ctx.save();
    if (art) ctx.globalAlpha = 0.30;
    ctx.beginPath();
    ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y);
    ctx.lineTo(c.x, c.y); ctx.lineTo(d.x, d.y);
    ctx.closePath();
    ctx.fillStyle = shade(base, (room.seats || []).length ? -0.05 : -0.16);
    ctx.fill();
    ctx.strokeStyle = "#33415a";
    ctx.lineWidth = 1;
    ctx.stroke();
    ctx.restore();

    /* two cutaway wall faces only, so the interior stays visible.
     * Proportion from StarNet stationbake (FACE_H tall, FACE_W thin). */
    var h2 = FACE_H * cam.zoom;
    ctx.beginPath();
    ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y);
    ctx.lineTo(b.x, b.y - h2); ctx.lineTo(a.x, a.y - h2);
    ctx.closePath();
    ctx.fillStyle = shade(base, 0.10);
    ctx.fill();
    ctx.beginPath();
    ctx.moveTo(b.x, b.y); ctx.lineTo(c.x, c.y);
    ctx.lineTo(c.x, c.y - h2); ctx.lineTo(b.x, b.y - h2);
    ctx.closePath();
    ctx.fillStyle = shade(base, 0.04);
    ctx.fill();
  }

  /* Is the terrain art decoded on this page? Read through the module so the
   * renderer never touches an Image itself (the module owns loading). False in a
   * harness, so the plate stays opaque there and the proofs measure the flat
   * path. */
  function worldArt() {
    return !!(ART && ART.read && ART.read.terrain && ART.read.terrain());
  }

  function drawShadow(ctx, at, cam) {
    /* Ported from StarNet sprites.js: a squashed ellipse under the feet. It is
     * most of what makes a small figure look planted rather than pasted on. */
    ctx.save();
    ctx.globalAlpha = 0.30;
    ctx.fillStyle = "#000000";
    ctx.beginPath();
    ctx.ellipse(at.x, at.y, 8 * cam.zoom, 3.2 * cam.zoom, 0, 0, Math.PI * 2);
    ctx.fill();
    ctx.restore();
  }

  /* Can the baked sprite module draw into this context? It needs a real canvas
   * (`document.createElement`) to bake into and `drawImage` to blit it. A
   * headless/fake context has neither, so the module is asked to degrade to its
   * own flat path — which is a different figure from the one this file's
   * primitives draw, and the pixel contract pins the primitives. So the module
   * is used only where it can actually bake, and the primitive path below is
   * the honest fallback. */
  function canBakeSprite(ctx) {
    return !!(SPRITES && SPRITES.bakeAgent && SPRITES.drawAgentSprite &&
              ctx && typeof ctx.drawImage === "function" &&
              typeof document !== "undefined" && document.createElement);
  }

  function drawAgent(ctx, x, y, cam, colour, state, facing, walking, progress) {
    /* THE SPRITE MODULE PATH. `QubitSprites` bakes the figure once per colour,
     * anchors it by the FEET, wraps it in a 1px separating outline and caches
     * it — the technique the old four-rectangle figure was fighting. Its walk
     * bob and stride are pure functions of the payload's own `progress`. */
    if (canBakeSprite(ctx)) {
      /* FIGURE_SCALE, and why it is not `cam.zoom`.
       *
       * Measured: the sprite bakes at 16x18 px, and at the fitted zoom of 0.85
       * (what `fitToBuilding` actually produces for a 10-seat plan in an 880x500
       * canvas) it drew at 13.6x15.3 SCREEN PX against a 137.3px plate — a ratio
       * of 0.111. The owner's verdict on that was "nothing is moving, only boxes
       * and squares", and the arithmetic says the same thing: a 14px figure
       * beside a 137px desk is not a person, it is a speck.
       *
       * The cause is that the sprite was scaled by the WORLD zoom, so it shrank
       * with the camera — correct for a floor plan, wrong for a sprite whose job
       * is to be seen. Every reference repo solves this the same way: the figure
       * is drawn OVERSIZED relative to its tile (StarNet's sprites overhang their
       * cell; pixel-agents' are taller than a tile). The camera decides how much
       * BUILDING you see; the figure scale decides whether you can see PEOPLE,
       * and they are separate questions.
       *
       * A FLOOR, not a multiplier: at zoom 0.85 the figure scales up ~2.2x to
       * 30x34px (ratio 0.244, legible); at max zoom 2.4 it is already 38x43px and
       * the floor does not bind, so nothing grows absurdly when you zoom in. A
       * plain multiplier would have made the closer views cartoonish; a floor
       * fixes the far view without touching the near one.
       *
       * Overhang is the accepted cost and it is small: at zoom 0.85 a 30px
       * figure rises ~7.5px above a 66.3px deck step, well inside the 100.8px
       * clearance, so it cannot reach the deck above. */
      var FIGURE_SCALE_FLOOR = 1.9;
      var sc = Math.max(Number(cam.zoom) || 1, FIGURE_SCALE_FLOOR);
      /* `opts.state` is the payload's OWN animation field, threaded through so
       * the atlas can resolve all twelve poses rather than the two that
       * `walking` alone can express. Without it the loader falls back to
       * `moving ? "walking" : "idle"` and every working/meeting/blocked agent
       * in the org renders as a person standing still — the state reached the
       * renderer and was dropped one call short of the sprite. */
      SPRITES.drawAgentSprite(ctx, null, colour, x, y, sc, facing,
                              !!walking, progress, {state: state});
      /* the state band: the payload's own signal, kept because at this scale a
       * colour alone is not readable. Drawn against the SAME `sc` as the figure
       * so it stays on the body rather than floating below an oversized sprite */
      var bandH = Math.max(1, 1.6 * sc);
      ctx.save();
      ctx.fillStyle = ANIM_COLOR[state] || "#8a8f98";
      ctx.fillRect(x - 4.5 * sc, y - 15 * sc + 15 * sc * 0.66,
                   9 * sc, bandH);
      ctx.restore();
      return;
    }

    /* THE PRIMITIVE PATH (headless / no sprite module). Ported from StarNet
     * sprites.js: draw the body ABOVE the feet point, since a sprite anchored
     * by its top floats and one anchored by its feet stands. The facing offsets
     * the head, so a direction is readable at this size. */
    var h = 15 * cam.zoom, w = 9 * cam.zoom;
    var top = y - h;
    var band = ANIM_COLOR[state] || "#8a8f98";

    ctx.save();
    ctx.fillStyle = shade(colour, -0.42);
    ctx.fillRect(x - w / 2 + 1, top + h * 0.62, w * 0.30, h * 0.38);
    ctx.fillRect(x + w / 2 - 1 - w * 0.30, top + h * 0.62, w * 0.30, h * 0.38);
    ctx.fillStyle = colour;
    ctx.beginPath();
    if (ctx.roundRect) {
      ctx.roundRect(x - w / 2, top + h * 0.24, w, h * 0.42, 2 * cam.zoom);
    } else {
      ctx.rect(x - w / 2, top + h * 0.24, w, h * 0.42);
    }
    ctx.fill();
    var dx = facing === "left" ? -1.2 : (facing === "right" ? 1.2 : 0);
    var dy = facing === "up" ? -0.8 : (facing === "down" ? 0.8 : 0);
    ctx.fillStyle = shade(colour, 0.18);
    ctx.beginPath();
    ctx.arc(x + dx * cam.zoom, top + h * 0.14 + dy * cam.zoom,
            2.6 * cam.zoom, 0, Math.PI * 2);
    ctx.fill();
    /* the state band: the signal the flat view used, kept because at this scale
     * a colour alone is not readable */
    ctx.fillStyle = band;
    ctx.fillRect(x - w / 2, top + h * 0.66, w, Math.max(1, 1.6 * cam.zoom));
    ctx.restore();
  }

  function drawMarker(ctx, x, y, cam, glyph, colour) {
    /* A marker over the head for the two states a human must spot from across
     * the room: stalled and walking-to-the-owner. */
    var top = y - 20 * cam.zoom;
    var w = 12 * cam.zoom, h = 12 * cam.zoom;
    ctx.save();
    ctx.fillStyle = "rgba(12,16,22,0.86)";
    ctx.strokeStyle = colour;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.rect(x - w / 2, top - h, w, h);
    ctx.fill();
    ctx.stroke();
    ctx.fillStyle = colour;
    ctx.font = "bold " + (9 * cam.zoom) + "px ui-monospace, monospace";
    ctx.textAlign = "center";
    ctx.fillText(glyph, x, top - h * 0.22);
    ctx.restore();
  }

  /* ------------------------------------------------------------ depth order
   * THE PAINTER'S ALGORITHM (the fix for the isometric smear, D1).
   *
   * The defect: the old `drawTower` painted in four passes nested by KIND —
   * floors, then rooms, then desks, then "the characters, on top of every
   * deck". So every agent painted after every desk, and a figure BEHIND a desk
   * was drawn over it. Whether a figure covers a desk is a function of WHERE IT
   * STANDS, not of what kind of thing it is.
   *
   * The fix: flatten every deck plate, wall face, desk, label, character and
   * marker into ONE list, sort ascending by the screen y of the cell each
   * stands on (its FEET), and draw in that order. A larger key is nearer the
   * camera, so it paints later and covers what is behind it. Nothing sorts by
   * kind; kind only breaks a same-cell tie.
   *
   * `iso-engine.js` owns the real key (`depthKey`/`sortByDepth`); this file
   * calls it. The local branch is the same rule for a bare harness.
   */
  /* The world surface adds four kinds to the SAME table (M12 — extend, do not
   * replace). A rank more NEGATIVE paints EARLIER (a smaller tiebreak is a larger
   * key, and a larger key paints later): ground -2 (under the plate), building -1
   * (the room's far corner), prop 2 (chair/desk — a figure on that cell still
   * wins it at rank 3), label 5 (floor text, last in its own cell). */
  var KIND_RANK = {
    ground: -2, building: -1, floor: 0, room: 1, desk: 2, prop: 2,
    agent: 3, marker: 4, label: 5
  };

  /* The local key: the screen y of the cell the thing stands on, un-zoomed. The
   * camera is an affine transform shared by every drawable, so it cannot
   * reorder them and is deliberately not applied. */
  function depthKey(x, y, z) {
    return (Number(x) + Number(y)) * (TILE_H / 2) - Number(z || 0) * DECK_Z;
  }

  function rankOf(kind) {
    return KIND_RANK[kind] === undefined ? 9 : KIND_RANK[kind];
  }

  function paintOrder(items) {
    if (ISO && ISO.sortByDepth) {
      /* The module subtracts its tiebreak, so a LARGER rank must be encoded as
       * a SMALLER tiebreak value — that is what makes a figure paint AFTER the
       * desk in its own cell (`-rank` gives the agent -3 < the desk's -2, and
       * `-3` is the larger key). */
      var rows = [];
      for (var i = 0; i < items.length; i++) {
        var it = items[i];
        rows.push({x: it.gx, y: it.gy, z: it.gz, tiebreak: -it.rank, _it: it});
      }
      return ISO.sortByDepth(rows).map(function (row) { return row._it; });
    }
    return items.slice().sort(function (a, b) {
      if (a.key !== b.key) return a.key - b.key;   /* feet line: far -> near */
      if (a.rank !== b.rank) return a.rank - b.rank;
      return String(a.id).localeCompare(String(b.id));  /* deterministic */
    });
  }

  /* The world ITEMS (ground, exterior, furniture, labels) are built by the module
   * (`ISO.drawTile.world.roomItems`), for the E2BIG reason at `ART` above. Items
   * come back keyed on the SAME `depthKey(x, y, z)` (M12): a chair at (1,1)
   * covers a figure at (0,0), and a figure at (1,-1) covers the chair. Nothing
   * here invents a room, seat or prop the payload did not report. */
  function worldFor(ctx, cam, room, z, floorNo, base) {
    if (!ART || !ART.roomItems) return [];
    return ART.roomItems({
      ctx: ctx, cam: cam, room: room, z: z, floorNo: floorNo, base: base,
      /* `ART_READ` is the module's own asset port (terrain/building/furniture/
       * labelFont), exported on `drawTile.world` alongside `roomItems` — the
       * two must travel together, because an item builder that cannot resolve
       * its images draws nothing. */
      img: ART.read, rankOf: rankOf, depthKey: depthKey, shade: shade
    });
  }

  function drawTower(ctx, v, opts) {
    var cam = opts.cam;
    var plan = v.floorplan || {};
    var floors = plan.floors || [];
    var only = opts.floor;
    var shown = only === null || only === undefined ? floors
      : floors.filter(function (f) { return f.number === only; });
    var probe = {agents: []};
    var items = [];

    ctx.clearRect(0, 0, cam.w, cam.h);
    /* the void behind the building, so a stack reads against something */
    ctx.fillStyle = "#0b1016";
    ctx.fillRect(0, 0, cam.w, cam.h);

    /* ---- decks, rooms, desks: one item each, no drawing yet ------------- */
    shown.forEach(function (fl) {
      var z = Number(fl.z || 0);
      (fl.rooms || []).forEach(function (room) {
        var x0 = Number(room.x), y0 = Number(room.y);
        var x1 = x0 + Number(room.w), y1 = y0 + Number(room.h);
        var base = deckTint(fl.role, (fl.rooms || []).length);

        /* the world surface joins the SAME `items` array, on the same key */
        var world = worldFor(ctx, cam, room, z, fl.number, base);
        for (var wi = 0; wi < world.length; wi++) items.push(world[wi]);

        /* FAR corner (x0, y0) keys the plate: the room's top vertex is the one
         * point of the room that is behind everything standing in it. */
        items.push({
          kind: "room", rank: rankOf("room"),
          id: "room:" + fl.number + ":" + (room.id || room.slug),
          gx: x0, gy: y0, gz: z, key: depthKey(x0, y0, z),
          draw: function () {
            drawRoomPlate(ctx, cam, room, base, z);
            /* The room label keeps its EXACT font and offset: this string is
             * asserted verbatim by test_office_label_layout.py (ROOM_GLYPH_PX).
             * The pixel-font sign is a separate item (`worldFor`), an addition
             * not a replacement. */
            ctx.fillStyle = "#7c8aa0";
            ctx.font = "10px ui-sans-serif, system-ui, sans-serif";
            var a = iso(x0, y0, z, cam), c = iso(x1, y1, z, cam);
            ctx.fillText(String(room.slug || ""), a.x + FACE_W * 2,
                         (a.y + c.y) / 2 + 4);
          }
        });

        /* one desk per seat slot. An empty desk is drawn dimmer: a room with
         * three empty desks must look like a room with three empty desks, not
         * like a full room with quiet people in it.
         *
         * The desk's KEY is its own CELL (slot.x, slot.y), not the +0.5 draw
         * offset: the desk and the agent sitting at it occupy ONE cell, so they
         * tie on the key and the `agent` rank breaks the tie — the figure
         * covers the desk it sits at. (The REAL desk and chair PNGs for that
         * seat come from `worldFor` above, on the same cell — the occupant
         * figure still wins the tie at rank 3 > prop 2.) */
        (room.seats || []).forEach(function (slot, n) {
          var sx = Number(slot.x), sy = Number(slot.y);
          var occupied = slot.agent_id !== null && slot.agent_id !== undefined;
          items.push({
            kind: "desk", rank: rankOf("desk"),
            id: "desk:" + fl.number + ":" + (room.id || room.slug) + ":" + n,
            gx: sx, gy: sy, gz: z, key: depthKey(sx, sy, z),
            draw: function () {
              var p = iso(sx + 0.5, sy + 0.5, z, cam);
              ctx.save();
              ctx.globalAlpha = occupied ? 1 : 0.45;
              ctx.fillStyle = occupied ? "#3d4b60" : "#2a3443";
              ctx.fillRect(p.x - 4 * cam.zoom, p.y - 4 * cam.zoom,
                           8 * cam.zoom, 5 * cam.zoom);
              ctx.restore();
            }
          });
        });
      });

      /* Label the deck ON its own near EDGE (not its near corner). Labelling
       * from the grid's row count put the text in empty space; labelling from
       * an offset outside the extent put it off-canvas. The near corner is the
       * diamond's BOTTOM vertex, so once a third deck enters the stack the
       * lowest deck's corner is anchored below the viewport floor. The near
       * edge's midpoint sits on the plate's front edge instead of spilling past
       * its bottom vertex, and the baseline is staggered by floor number and
       * clamped into the canvas. See docs/game-office/32-LABELS-AND-LEGIBILITY
       * .md §6.1. */
      var minX = Infinity, maxX = 0, maxY = 0;
      (fl.rooms || []).forEach(function (room) {
        minX = Math.min(minX, Number(room.x));
        maxX = Math.max(maxX, Number(room.x) + Number(room.w));
        maxY = Math.max(maxY, Number(room.y) + Number(room.h));
      });
      /* A deck with no rooms has no edge to anchor to; skip its label rather
       * than draw at Infinity. (No floor in the payload is room-less, but an
       * empty one must not poison the canvas with a NaN transform.) */
      if (!isFinite(minX)) return;
      var edgeX = (minX + maxX) / 2;
      items.push({
        kind: "room", rank: rankOf("room"),
        id: "label:" + fl.number,
        gx: edgeX, gy: maxY, gz: z, key: depthKey(edgeX, maxY, z),
        draw: function () {
          var lp = iso(edgeX, maxY, z, cam);
          /* Stagger by floor number so successive floors' labels form a
           * diagonal down the tower (the stagger is under one deck pitch, so it
           * reads as depth, not error), then CLAMP the baseline into the canvas:
           * the clamp is last and cannot move a label that is already inside. */
          var baseline = lp.y + 13 + fl.number * FLOOR_STRIDE;
          baseline = Math.min(Math.max(baseline, FLOOR_GLYPH_H + 2), cam.h - 4);
          ctx.fillStyle = "#8d9ab0";
          ctx.font = "600 12px ui-sans-serif, system-ui, sans-serif";
          ctx.fillText("FLOOR " + fl.number + " \u00b7 " + String(fl.role || ""),
                       lp.x + 4, baseline);
        }
      });
    });

    /* ---- characters: the SAME list, keyed by where they stand ----------- */
    (v.characters || []).forEach(function (cs) {
      var p = cs.position || {};
      if (p.x === null || p.x === undefined || p.y === null ||
          p.y === undefined) return;
      var fl = null;
      shown.forEach(function (f) {
        if (f.number === p.floor) fl = f;
      });
      if (!fl) return;
      var z = Number(fl.z || 0);
      var px = Number(p.x), py = Number(p.y);
      var colour = seatColour(cs.slug, cs.seat_type);

      /* ONE key, from the walk point itself — so a character on its way to a
       * station sorts against the desks AROUND it, never against a room. */
      items.push({
        kind: "agent", rank: rankOf("agent"),
        id: "agent:" + cs.agent_id,
        gx: px, gy: py, gz: z, key: depthKey(px, py, z),
        draw: function () {
          var at = iso(px, py, z, cam);

          drawShadow(ctx, at, cam);

          /* Walk bob, driven ONLY by the payload's own `progress`: mid-walk the
           * backend sends progress in (0,1). No progress, no bob — the bob
           * cannot exist anywhere the payload has not said a walk is happening
           * (C7). */
          var pr = typeof p.progress === "number" ? p.progress : null;
          var bob = (cs.walking && pr !== null)
            ? Math.abs(Math.sin(pr * Math.PI * 4)) * 3 * cam.zoom : 0;

          drawAgent(ctx, at.x, at.y - bob, cam, colour, cs.animation,
                    cs.facing, cs.walking, pr);
          probe.agents.push({kind: "agent", x: at.x, y: at.y, cs: cs,
                             colour: colour, gx: px, gy: py, gz: z,
                             floor: p.floor});

          var mark = null;
          if (cs.stalled) mark = "!";
          if (cs.animation === "blocked") mark = "!";
          if (cs.animation === "walking_to_manager") mark = "^";
          if (mark) drawMarker(ctx, at.x, at.y, cam, mark, colour);
        }
      });
    });

    /* ---- paint far -> near --------------------------------------------- */
    paintOrder(items).forEach(function (it) { it.draw(); });

    return probe;
  }

  /* ------------------------------------------------------------- minimap
   * Ported from Agent-Quest Minimap.tsx: scale the world extent into a small
   * canvas, draw rooms as rects and agents as markers. When `QubitCamera` is
   * present the scale/offset come from its `minimapTransform` (a world box ->
   * rect box, keeping the world's aspect and centring it) rather than a bare
   * `w/cols`, which pinned the world origin to the corner.
   */
  function drawMinimap(cv, v, floor) {
    if (!cv) return;
    var ctx = cv.getContext("2d");
    if (!ctx) return;
    var plan = v.floorplan || {};
    var g = plan.grid || {cols: 1, rows: 1};
    var cols = Math.max(1, Number(g.cols)), rows = Math.max(1, Number(g.rows));
    var w = cv.width, h = cv.height;
    ctx.clearRect(0, 0, w, h);
    ctx.fillStyle = "#141a23";
    ctx.fillRect(0, 0, w, h);

    var shownFloors = (plan.floors || []).filter(function (fl) {
      return floor === null || floor === undefined || fl.number === floor;
    });

    /* Build one transform. Module path: project the rooms of the shown floors
     * into world space, take the extent, and let `minimapTransform` map world
     * -> minimap. Fallback: the legacy per-grid-cell scale. */
    var toMini, toMiniPt;
    if (CAM && CAM.worldExtent && CAM.minimapTransform) {
      var cells = [];
      shownFloors.forEach(function (fl) {
        var z = Number(fl.z || 0);
        (fl.rooms || []).forEach(function (room) {
          cells.push({x: Number(room.x), y: Number(room.y),
                      w: Number(room.w), h: Number(room.h), z: z});
        });
      });
      var ex = null;
      if (cells.length) {
        /* worldExtent takes a flat cell list; fold z per cell via a lookup */
        ex = null;
        shownFloors.forEach(function (fl) {
          var zz = Number(fl.z || 0);
          var fc = (fl.rooms || []).map(function (room) {
            return {x: Number(room.x), y: Number(room.y),
                    w: Number(room.w), h: Number(room.h)};
          });
          if (!fc.length) return;
          var fe = CAM.worldExtent(fc, function (gx, gy) {
            return isoWorld(gx, gy, zz);
          });
          if (!fe) return;
          if (!ex) ex = fe;
          else {
            ex.minX = Math.min(ex.minX, fe.minX);
            ex.maxX = Math.max(ex.maxX, fe.maxX);
            ex.minY = Math.min(ex.minY, fe.minY);
            ex.maxY = Math.max(ex.maxY, fe.maxY);
          }
        });
        if (ex) {
          ex.w = ex.maxX - ex.minX; ex.h = ex.maxY - ex.minY;
        }
      }
      var t = CAM.minimapTransform({w: w, h: h}, ex);
      toMini = function (px, py) { return t.toMini({x: px, y: py}); };
      toMiniPt = true;
    } else {
      var sx = w / cols, sy = h / rows;
      toMini = function (px, py) { return {x: px * sx, y: py * sy}; };
      toMiniPt = false;
    }

    shownFloors.forEach(function (fl) {
      var z = Number(fl.z || 0);
      (fl.rooms || []).forEach(function (room) {
        ctx.fillStyle = (room.seats || []).length ? "#2c3848" : "#232c3a";
        var x0 = Number(room.x), y0 = Number(room.y);
        var x1 = x0 + Number(room.w), y1 = y0 + Number(room.h);
        var p0, p1;
        if (toMiniPt) {
          /* project the two opposite corners of the room's diamond and take the
           * axis-aligned box of the projected corners */
          var cs = [isoWorld(x0, y0, z), isoWorld(x1, y0, z),
                    isoWorld(x1, y1, z), isoWorld(x0, y1, z)];
          var mnx = Infinity, mny = Infinity, mxx = -Infinity, mxy = -Infinity;
          cs.forEach(function (p) {
            mnx = Math.min(mnx, p.x); mxx = Math.max(mxx, p.x);
            mny = Math.min(mny, p.y); mxy = Math.max(mxy, p.y);
          });
          p0 = toMini(mnx, mny); p1 = toMini(mxx, mxy);
        } else {
          p0 = toMini(x0, y0); p1 = toMini(x1, y1);
        }
        var rx = Math.min(p0.x, p1.x), ry = Math.min(p0.y, p1.y);
        var rw = Math.abs(p1.x - p0.x), rh = Math.abs(p1.y - p0.y);
        ctx.fillRect(rx, ry, Math.max(1, rw), Math.max(1, rh));
      });
    });

    (v.characters || []).forEach(function (cs) {
      var p = cs.position || {};
      if (p.x === null || p.x === undefined || p.y === null ||
          p.y === undefined) return;
      if (floor !== null && floor !== undefined && p.floor !== floor) return;
      var fl = null;
      (plan.floors || []).forEach(function (f) {
        if (f.number === p.floor) fl = f;
      });
      var z = fl ? Number(fl.z || 0) : 0;
      ctx.fillStyle = cs.stalled ? "#e0554a"
        : (ANIM_COLOR[cs.animation] || "#8a8f98");
      var m;
      if (toMiniPt) {
        var wp = isoWorld(Number(p.x), Number(p.y), z);
        m = toMini(wp.x, wp.y);
        ctx.fillRect(m.x - 2, m.y - 2, 4, 4);
      } else {
        m = toMini(Number(p.x), Number(p.y));
        var sm = w / cols, sn = h / rows;
        ctx.fillRect(m.x - 1, m.y - 1, Math.max(2, sm), Math.max(2, sn));
      }
    });
  }

  /* ------------------------------------------------------------ P&L charts
   * No reference project has these, because none of them keeps a ledger. Every
   * bar is a day's derived fold from `v.history`, which the backend builds with
   * GROUP BY. A day with no rows is drawn as a zero day rather than skipped,
   * because the quiet days are the ones worth seeing.
   */
  function drawPnl(cv, series) {
    if (!cv || !series || !series.length) return;
    var ctx = cv.getContext("2d");
    if (!ctx) return;
    var w = cv.width, h = cv.height;
    ctx.clearRect(0, 0, w, h);
    var peak = 1, low = 0;
    series.forEach(function (d) {
      peak = Math.max(peak, d.revenue_cents, d.cost_cents, d.net_cents);
      low = Math.min(low, d.net_cents);
    });
    var span = (peak - low) || 1;
    var zero = h - ((-low) / span) * h;
    var bw = w / series.length;

    ctx.strokeStyle = "#2a3443";
    ctx.beginPath();
    ctx.moveTo(0, zero); ctx.lineTo(w, zero);
    ctx.stroke();

    series.forEach(function (d, i) {
      var x = i * bw;
      var rh = (d.revenue_cents / span) * h;
      var ch = (d.cost_cents / span) * h;
      ctx.fillStyle = "rgba(111,211,166,0.75)";
      ctx.fillRect(x + 1, zero - rh, Math.max(1, bw - 2), rh);
      ctx.fillStyle = "rgba(224,85,74,0.75)";
      ctx.fillRect(x + 1, zero, Math.max(1, bw - 2), ch);
    });

    ctx.strokeStyle = "#5aa9e6";
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    series.forEach(function (d, i) {
      var x = i * bw + bw / 2;
      var y = zero - (d.net_cents / span) * h;
      if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    });
    ctx.stroke();
  }

  /* ---------------------------------------------------------------- styles */
  var styles = {
    page: {padding: 16, display: "flex", flexDirection: "column", gap: 12,
           color: "inherit"},
    bar: {display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap",
          rowGap: 8},
    note: {fontSize: 12, opacity: 0.62, display: "flex", gap: 8,
           alignItems: "center", flexWrap: "wrap"},
    pill: {fontSize: 11, padding: "2px 8px", borderRadius: 999,
           border: "1px solid #2a3443"},
    stage: {display: "flex", gap: 12, alignItems: "flex-start",
            flexWrap: "wrap"},
    canvas: {border: "1px solid #2a3443", borderRadius: 10,
             background: "#0b1016", display: "block", cursor: "grab"},
    side: {display: "flex", flexDirection: "column", gap: 8, minWidth: 280,
           maxWidth: 380, flex: "1 1 280px"},
    panel: {border: "1px solid #2a3443", borderRadius: 10, padding: 10,
            background: "rgba(255,255,255,0.015)"},
    log: {maxHeight: 240, overflowY: "auto", fontSize: 12, lineHeight: 1.5},
    preview: {border: "1px dashed #e6b45a", borderRadius: 8,
              padding: "6px 10px", fontSize: 12, color: "#e6b45a"},
    warn: {border: "1px solid #e0554a", borderRadius: 8, padding: "6px 10px",
           fontSize: 12, color: "#e0554a"}
  };

  function Office() {
    var viewState = hooks.useState({loading: true, error: null, data: null});
    var view = viewState[0], setView = viewState[1];
    var floorState = hooks.useState(null);
    var floor = floorState[0], setFloor = floorState[1];
    var pickState = hooks.useState(null);
    var pick = pickState[0], setPick = pickState[1];
    var chartState = hooks.useState(false);
    var showChart = chartState[0], setShowChart = chartState[1];
    var barTickState = hooks.useState(0);
    var barTick = barTickState[0], setBarTick = barTickState[1];

    var canvasRef = hooks.useRef(null);
    var miniRef = hooks.useRef(null);
    var pnlRef = hooks.useRef(null);
    var probeRef = hooks.useRef({agents: []});
    var dataRef = hooks.useRef(null);
    var viewRef = hooks.useRef({floor: null, showChart: false});
    var camRef = hooks.useRef({cx: 0, cy: 0, zoom: 1, w: 880, h: 500});
    /* the camera the last fit produced — the control bar's `reset` target, so
     * reset returns to the framed view rather than inventing one (see
     * control-bar.js::apply). */
    var fitRef = hooks.useRef(null);

    /* Every load is a read. There is no mutation path in this plugin. */
    /* The preview is opt-in through the page URL (`/qubit-office?preview=1`),
     * never on by default: an empty tower must report itself empty. The literal
     * path stays plain so the read-only contract test keeps holding. */
    function previewWanted() {
      var qs = String((window.location && window.location.search) || "");
      return /(^|[?&])preview=1/.test(qs) ? "?preview=1" : "";
    }

    function load() {
      fetchJSON("/api/office/tower" + previewWanted()).then(function (res) {
        setView({loading: false, data: res, error: null});
      }).catch(function (err) {
        var msg = String((err && err.message) || err || "");
        var at = msg.indexOf("{");
        var code = "", detail = msg;
        if (at >= 0) {
          try {
            var body = JSON.parse(msg.slice(at));
            code = body.code || "";
            detail = body.detail || msg;
          } catch (parseErr) { /* a plain message; render it as-is */ }
        }
        setView({loading: false, data: null,
                 error: (code ? code + ": " : "") + detail});
      });
    }

    hooks.useEffect(function () {
      load();
      var id = setInterval(load, POLL_MS);
      return function () { clearInterval(id); };
    }, []);

    var v = view.data;
    dataRef.current = v;
    viewRef.current = {floor: floor, showChart: showChart};

    function redraw() {
      var cv = canvasRef.current, data = dataRef.current;
      if (!cv || !data) return;
      var cam = camRef.current;
      var box = cv.parentNode && cv.parentNode.getBoundingClientRect
        ? cv.parentNode.getBoundingClientRect() : null;
      var wantW = box ? Math.max(240, Math.min(1200, Math.floor(box.width)))
                      : cv.width;
      if (cv.width !== wantW) cv.width = wantW;
      cam.w = cv.width;
      cam.h = cv.height;
      var g = (data.floorplan && data.floorplan.grid) || {cols: 1, rows: 1};
      var allFloors = (data.floorplan && data.floorplan.floors) || [];
      var sel = viewRef.current.floor;
      var shown = sel === null || sel === undefined ? allFloors
        : allFloors.filter(function (f) { return f.number === sel; });
      /* Re-frame when the set of floors on screen changes (a different floor
       * selected, or the building itself changed size) — not on every poll, so a
       * pan or zoom the user chose is never yanked away mid-look. */
      var key = shown.map(function (f) {
        return f.number + ':' + f.rooms.length;
      }).join(',');
      if (camRef.current._frameKey !== key) {
        camRef.current._frameKey = key;
        fitToBuilding(cam, shown);
        /* The CANONICAL framing is only ever the fit — never the current
         * camera. Saving `cam` here (as this file used to) made Reset restore
         * whatever the user had just panned/zoomed to, turning Fit/Reset into
         * no-ops that could never return to the canonical view. */
        fitRef.current = {cx: cam.cx, cy: cam.cy, zoom: cam.zoom};
      } else if (camRef.current._autoZoom) {
        fitToBuilding(cam, shown);
        fitRef.current = {cx: cam.cx, cy: cam.cy, zoom: cam.zoom};
      }
      /* The module's clamp is a WORLD-extent constraint; apply it where the
       * extent is known. Falls back to the local cols/rows guard. */
      var ex = extent(shown);
      if (CAM && CAM.clamp && ex) {
        var cbox = {minX: ex.cx - ex.w / 2, maxX: ex.cx + ex.w / 2,
                    minY: ex.cy - ex.h / 2, maxY: ex.cy + ex.h / 2,
                    w: ex.w, h: ex.h};
        CAM.clamp({w: cam.w, h: cam.h}, cbox, cam, 0.30);
      } else {
        clampPan(cam, g.cols, g.rows);
      }
      var ctx = cv.getContext("2d");
      if (ctx) {
        probeRef.current = drawTower(ctx, data, {
          cam: cam, floor: viewRef.current.floor
        });
      }
      drawMinimap(miniRef.current, data, viewRef.current.floor);
      if (viewRef.current.showChart) drawPnl(pnlRef.current, data.history);
    }

    /* ------------------------------------------------------ the control bar
     * Read-only view verbs, rendered from `QubitControlBar`: Reset, Fit, Zoom,
     * P&L. `apply` is a PURE reducer — the state is held here and the view is
     * redrawn on change. A refused id throws inside the module; we catch it so
     * an unknown id is a no-op on the page rather than a crash, and the module's
     * `isReadOnly` is the gate. */
    function barState() {
      return {
        cam: {cx: camRef.current.cx, cy: camRef.current.cy,
              zoom: camRef.current.zoom},
        floor: viewRef.current.floor,
        showChart: viewRef.current.showChart,
        fit: fitRef.current
      };
    }

    function onBar(id) {
      if (!BAR || !BAR.isReadOnly(id)) return;
      var next;
      try { next = BAR.apply(id, barState()); }
      catch (err) { return; }   /* a refused/unknown id is a no-op, not a crash */
      camRef.current.cx = next.cam.cx;
      camRef.current.cy = next.cam.cy;
      camRef.current.zoom = next.cam.zoom;
      if (id === "fit" || id === "reset") {
        /* `_autoZoom` — the flag `redraw()` actually reads. This wrote `_auto`
         * (a name nothing read), so Fit/Reset set a phantom flag and left
         * `_autoZoom` at whatever the user's last wheel had made it, which is
         * why Fit never re-framed after Zoom. */
        camRef.current._autoZoom = true;
      } else if (id === "zoom") {
        camRef.current._autoZoom = false;
      }
      fitRef.current = next.fit;
      if (next.floor !== undefined) setFloor(next.floor);
      if (next.showChart !== undefined) setShowChart(!!next.showChart);
      setBarTick(barTick + 1);
    }

    /* Pan, zoom and pick. Plain DOM listeners attached ONCE — no timer, no
     * animation frame: a drag moves the camera and the next draw reflects it,
     * so nothing here can produce motion the payload did not report.
     *
     * BOUND TO THE BRANCH, NOT TO MOUNT. The canvas exists only in the ready
     * branch below, so a mount-only effect read `canvasRef.current === null`,
     * returned at the guard, and never ran again — the page rendered a canvas
     * with zero listeners (no pan, no zoom, no pick). The dependency is the
     * branch selector itself, so the effect re-runs when the canvas actually
     * appears (loading -> ready) and re-binds after any remount. On every
     * branch that renders no canvas there is no element to bind and the guard
     * still returns, which is why `view.loading`/`view.error`/`v.empty` are the
     * correct keys: they are exactly the conditions under which the canvas is
     * absent from the tree. */
    var canvasPresent = !view.loading && !view.error && !(v.empty && !v.preview);
    hooks.useEffect(function () {
      var cv = canvasRef.current;
      if (!cv) return;
      var drag = null;
      /* A DRAG THRESHOLD, because a click and a drag are the same gesture until
       * the pointer travels: without it, a 2px slip during a click pans the
       * camera and then the click lands on a different tile than the user
       * aimed at. `DRAG_SLOP` is in CSS px, not world units — the user's hand
       * does not know about zoom. */
      var DRAG_SLOP = 4;
      /* set when a drag actually moved, so the `click` that follows a pan is
       * swallowed exactly once */
      var suppressClick = false;
      function down(ev) {
        drag = {x: ev.clientX, y: ev.clientY,
                cx: camRef.current.cx, cy: camRef.current.cy,
                moved: false, id: ev.pointerId};
        /* POINTER CAPTURE: without it a fast drag that leaves the canvas ends
         * the gesture early and the camera sticks mid-pan. Guarded because
         * synthetic events in the headless probe have no pointerId. */
        if (ev.pointerId !== undefined && cv.setPointerCapture) {
          try { cv.setPointerCapture(ev.pointerId); } catch (err) { /* named gap */ }
        }
        cv.style.cursor = "grabbing";
      }
      /* `pointercancel` (an OS gesture, a lost capture) must end the drag the
       * same way `pointerup` does, or the camera stays stuck to a pointer that
       * no longer exists. */
      function cancel() { up(); }
      function move(ev) {
        if (!drag) return;
        var dx = ev.clientX - drag.x, dy = ev.clientY - drag.y;
        if (!drag.moved && Math.abs(dx) + Math.abs(dy) < DRAG_SLOP) return;
        drag.moved = true;
        camRef.current.cx = drag.cx - dx / camRef.current.zoom;
        camRef.current.cy = drag.cy - dy / camRef.current.zoom;
        /* the user took the camera: stop the passive re-frame from yanking it */
        camRef.current._autoZoom = false;
        redraw();
      }
      function up() {
        if (drag && drag.moved) suppressClick = true;
        drag = null;
        cv.style.cursor = "grab";
      }
      function wheel(ev) {
        ev.preventDefault();
        var f = ev.deltaY > 0 ? 0.9 : 1.1;
        /* CURSOR-ANCHORED ZOOM. Zooming about the canvas centre means the point
         * under the pointer slides away — the user has to re-aim after every
         * step. Solve for the world point under the cursor, scale, then solve
         * for the camera that puts that same world point back under it. */
        var cam = camRef.current;
        var before = cam.zoom;
        var after = Math.max(CHAR_FLOOR_ZOOM, Math.min(MAX_ZOOM, before * f));
        if (after !== before) {
          var r = cv.getBoundingClientRect();
          var px = ev.clientX - r.left - cam.w / 2;
          var py = ev.clientY - r.top - cam.h / 2;
          cam.cx += px / before - px / after;
          cam.cy += py / before - py / after;
          cam.zoom = after;
        }
        cam._autoZoom = false;   /* the user chose; stop re-framing */
        redraw();
      }
      /* Click-to-select, resolved by the MODULE's inverse transform rather than
       * by guessing the nearest figure in pixels. `QubitIso.screenToGrid` puts
       * the click back on the tile it hit; a figure whose own cell matches that
       * tile is the one selected (the nearest by cell if several share one).
       * Without the module the probe still carries each figure's grid cell, so
       * the fallback is the same rule on cell distance, not on screen pixels. */
      function click(ev) {
        /* A DRAG IS NOT A CLICK. Without this, releasing a pan fires `click`
         * on the canvas and the selection jumps to whatever tile the drag
         * happened to end on. */
        if (suppressClick) { suppressClick = false; return; }
        var r = cv.getBoundingClientRect();
        var mx = ev.clientX - r.left, my = ev.clientY - r.top;
        var cam = camRef.current;
        var cell = null;
        if (ISO && ISO.screenToGrid) {
          var sel = viewRef.current.floor;
          var floors = (dataRef.current && dataRef.current.floorplan &&
                        dataRef.current.floorplan.floors) || [];
          var z = 0;
          floors.forEach(function (f) {
            if (sel === null || sel === undefined || f.number === sel) {
              if (sel !== null && sel !== undefined && f.number === sel) {
                z = Number(f.z || 0);
              }
            }
            if (f.number === sel) z = Number(f.z || 0);
          });
          cell = ISO.screenToGrid(mx, my, cam, z);
        }
        /* --- pick the agent on the cell -------------------------------------
         * The click resolves to a CELL (via the module's inverse). Within the
         * pick radius, the cell alone is ambiguous in the whole-building view
         * (`floor: null`): two agents on DIFFERENT floors can share a cell, and
         * the old `d < bestD` over a depth-sorted probe handed the click to
         * whichever came first — the FAR/high deck — regardless of which figure
         * the user pointed at. The fix: among the candidates on the resolved
         * cell, prefer
         *   1. an agent on the FOCUSED floor when one is focused, else
         *   2. the agent whose projected screen `y` is NEAREST the click,
         * which is the figure actually under the pointer. With a focused floor
         * the probe holds only that floor, so this is a no-op there; it only
         * changes the whole-building case the bug is about. */
        var focused = viewRef.current.floor;
        var candidates = [];
        var best = null, bestD = cell ? 2.5 : 24;
        (probeRef.current.agents || []).forEach(function (a) {
          if (a.kind !== "agent") return;
          if (cell) {
            var dx = Number(a.gx) - cell.x, dy = Number(a.gy) - cell.y;
            var d = Math.sqrt(dx * dx + dy * dy);
            if (d < 2.5) candidates.push({a: a, d: d});
          } else {
            var d2 = Math.abs(a.x - mx) + Math.abs(a.y - my);
            if (d2 < 24 && (best === null || d2 < bestD)) {
              bestD = d2; best = a.cs;
            }
          }
        });
        if (cell) {
          var pool = candidates;
          if (focused !== null && focused !== undefined) {
            var onFloor = pool.filter(function (c) {
              return c.a.floor === focused;
            });
            if (onFloor.length) pool = onFloor;
          }
          var pick = null, pickDy = Infinity, pickD = Infinity;
          pool.forEach(function (c) {
            var dy = Math.abs(Number(c.a.y) - my);
            /* nearest projected row to the click wins; a cell-distance tie
             * (two figures on the SAME cell and same floor) falls back to the
             * smaller cell distance, then to probe order determinism. */
            if (dy < pickDy - 1e-9 ||
                (Math.abs(dy - pickDy) <= 1e-9 && c.d < pickD - 1e-9)) {
              pickDy = dy; pickD = c.d; pick = c.a.cs;
            }
          });
          if (pick) best = pick;
          else best = null;
        }
        /* store the ID, not the object: the inspector derives from the latest
         * snapshot (see `picked` below). */
        setPick(best && best.agent_id !== undefined ? best.agent_id : null);
      }
      cv.addEventListener("pointerdown", down);
      cv.addEventListener("pointermove", move);
      cv.addEventListener("pointerup", up);
      cv.addEventListener("pointercancel", cancel);
      cv.addEventListener("wheel", wheel, {passive: false});
      cv.addEventListener("click", click);
      return function () {
        cv.removeEventListener("pointerdown", down);
        cv.removeEventListener("pointermove", move);
        cv.removeEventListener("pointerup", up);
        cv.removeEventListener("pointercancel", cancel);
        cv.removeEventListener("wheel", wheel);
        cv.removeEventListener("click", click);
      };
    }, [canvasPresent]);

    /* Redraw whenever the truth changes: a poll, a floor focus, the chart
     * toggle, a control-bar verb. This is the only thing that moves pixels. */
    hooks.useEffect(function () {
      redraw();
      var data = dataRef.current;
      if (showChart && data) drawPnl(pnlRef.current, data.history);
      if (data) drawMinimap(miniRef.current, data, floor);
    }, [v, floor, showChart, barTick]);

    if (view.loading) {
      /* Not a spinner: a spinner is motion, and motion is a claim. This is a
       * sentence. */
      return e("div", {style: styles.page, "data-state": "loading"},
        e("p", null, "Reading the office\u2026"));
    }

    if (view.error) {
      /* A typed backend refusal renders as itself. Nothing is invented. */
      return e("div", {style: styles.page, "data-state": "error"},
        e("h3", null, "The office could not be read"),
        e("p", {style: {fontSize: 13}}, view.error),
        e(C.Button || "button", {onClick: load}, "Try again"));
    }

    if (v.empty && !v.preview) {
      return e("div", {style: styles.page, "data-state": "empty"},
        e("h3", null, "The office is empty"),
        e("p", {style: {fontSize: 13}}, v.empty_reason),
        e("p", {style: styles.note},
          "No floors, no seats and no ledger rows have been created yet. " +
          "This page renders the org that exists and never a demonstration " +
          "one \u2014 if you are seeing this, the org has not started."));
    }

    var hud = v.hud || {};
    var floors = (v.floorplan && v.floorplan.floors) || [];
    var unseated = (v.characters || []).filter(function (c) {
      return !c.seated;
    });
    var barButtons = BAR ? BAR.buildButtons() : [];
    /* THE INSPECTOR IS DERIVED, NOT STORED. `pick` holds an agent ID (or null);
     * the panel below resolves it against `v.characters` from the CURRENT
     * snapshot. Storing the character object itself (as this file used to)
     * pinned the inspector to the revision it was clicked in: every later poll
     * updated the tower and left the panel showing a stale state, walking flag
     * and last-event age. An ID cannot go stale — a deleted or renamed agent
     * simply resolves to nothing and the panel says so. */
    var picked = null;
    if (pick !== null && pick !== undefined) {
      (v.characters || []).forEach(function (c) {
        if (c.agent_id === pick) picked = c;
      });
    }

    return e("div", {style: styles.page, "data-state": "ready"},
      /* --- the preview banner. A drawing nobody can identify as a drawing is
       * the lie; a labelled one is just a drawing. --- */
      v.preview ? e("div", {style: styles.preview}, v.preview_legend) : null,

      /* --- a module that did not load is NAMED, never swallowed: a half-wired
       * bundle must say so rather than render a quietly degraded office --- */
      MODULE_GAPS.length
        ? e("div", {style: styles.warn},
            "Office modules not loaded: " + MODULE_GAPS.join(", ") +
            ". Drawing with built-in fallbacks.")
        : null,

      /* --- the HUD: every figure is the same fold `qubit office pnl` reads --- */
      e("div", {style: styles.bar},
        stat("Headcount", hud.headcount),
        stat("Revenue", money(hud.revenue_cents)),
        stat("Cost", money(hud.cost_cents)),
        stat("Net", money(hud.net_cents)),
        stat("Burn", moneyRate(hud.burn_cents_per_day) + "/day",
             hud.burn_cents_per_day ? null : "not spending"),
        stat("Runway", hud.runway_days === null ? "unknown"
             : hud.runway_days + " d",
             hud.runway_days === null ? "nothing is burning yet"
             : "floor " + ((hud.survival || {}).floor_days) + " d")),

      /* --- the read-only control bar: Reset / Fit / Zoom / P&L, from
       * QubitControlBar. Every one is a local view verb; none fetches. --- */
      barButtons.length
        ? e("div", {style: styles.bar},
            e("span", {style: {fontSize: 12, opacity: 0.6}}, "View"),
            barButtons.map(function (b) {
              return e(C.Button || "button", {
                key: b.id, title: b.title,
                "aria-pressed": b.id === "pnl" ? showChart : undefined,
                onClick: function () { onBar(b.id); }
              }, b.label);
            }))
        : null,

      /* --- the floor stack: pick a deck, or see the whole building --- */
      e("div", {style: styles.bar},
        e("span", {style: {fontSize: 12, opacity: 0.6}}, "Floor"),
        e(C.Button || "button", {
          "aria-pressed": floor === null,
          onClick: function () { setFloor(null); }
        }, "all"),
        floors.map(function (f) {
          return e(C.Button || "button", {
            key: f.number,
            "aria-pressed": f.number === floor,
            onClick: function () { setFloor(f.number); },
            style: f.number === floor ? {fontWeight: 600} : null
          }, "F" + f.number + " \u00b7 " + f.role);
        }),
        e(C.Button || "button", {
          "aria-pressed": showChart,
          onClick: function () { setShowChart(!showChart); }
        }, "P&L")),

      /* --- the building --- */
      e("div", {style: styles.stage},
        e("div", {style: {flex: "2 1 420px", minWidth: 280}},
          e("canvas", {ref: canvasRef, width: 880, height: 500,
                       style: styles.canvas}),
          e("div", {style: {marginTop: 6, fontSize: 11, opacity: 0.55}},
            "Drag to pan \u00b7 scroll to zoom \u00b7 click a character")),
        e("div", {style: styles.side},
          picked
            ? e("div", {style: styles.panel},
                e("div", {style: {fontWeight: 600, fontSize: 14}}, picked.slug),
                e("div", {style: {fontSize: 12, opacity: 0.7}},
                  picked.seat_type + (picked.department_id
                    ? " \u00b7 dept " + picked.department_id : "")),
                e("div", {style: {fontSize: 12, marginTop: 4}},
                  "state: " + picked.animation +
                  (picked.walking ? " (walking)" : "") +
                  (picked.at ? " \u00b7 at " + picked.at : "")),
                picked.stalled
                  ? e("div", {style: {fontSize: 12, color: "#e0554a",
                                      fontWeight: 600, marginTop: 2}},
                      "STALLED \u2014 no event within " +
                      Math.round(Number(v.active_window_s) || 0) +
                      "s, so nothing is happening here")
                  : null,
                picked.last_event
                  ? e("div", {style: {fontSize: 12, opacity: 0.7}},
                      "last event: " + picked.last_event.kind + " \u00b7 " +
                      Math.round(picked.last_event.age_s) + "s ago")
                  : e("div", {style: {fontSize: 12, opacity: 0.5}},
                      "no event on record"))
            : e("div", {style: styles.panel},
                e("div", {style: {fontSize: 12, opacity: 0.6}},
                  pick !== null && pick !== undefined
                    ? "That agent is no longer in the tower."
                    : "Click a character for its state.")),
          e("div", {style: styles.panel},
            e("div", {style: {fontSize: 12, fontWeight: 600,
                              marginBottom: 4}},
              "Building \u2014 " + floors.length + " floor" +
              (floors.length === 1 ? "" : "s") + ", " +
              (v.characters || []).length + " seats"),
            e("canvas", {ref: miniRef, width: 640, height: 260,
                         style: {display: "block", width: "100%",
                                 height: "auto", borderRadius: 6,
                                 background: "#141a23"}})),
          e("div", {style: styles.panel},
            e("div", {style: {fontSize: 12, fontWeight: 600,
                              marginBottom: 4}}, "Activity"),
            e("div", {style: styles.log},
              ((v.activity) || []).length
                ? v.activity.map(function (row) {
                    return e("div", {key: row.id},
                      e("span", {style: {opacity: 0.5}},
                        new Date(row.ts * 1000).toLocaleTimeString() + "  "),
                      row.text);
                  })
                : e("div", {style: {opacity: 0.55}},
                    "No events yet. The feed and the characters are drawn from " +
                    "the same table."))))),

      showChart
        ? e("div", {style: styles.panel},
            e("div", {style: {fontSize: 12, fontWeight: 600, marginBottom: 4}},
              "P&L by day \u2014 " + ((v.history) || []).length + " days \u00b7 net " +
              money((v.history_totals || {}).net_cents)),
            e("canvas", {ref: pnlRef, width: 860, height: 130,
                         style: {display: "block", width: "100%"}}),
            e("div", {style: {fontSize: 11, opacity: 0.55, marginTop: 4}},
              "green = revenue \u00b7 red = cost \u00b7 blue line = net. A day " +
              "with no rows is drawn as a zero day, not skipped."))
        : null,

      /* --- seats with no desk: nobody is dropped from the tower --- */
      unseated.length
        ? e("div", {style: styles.panel},
            e("div", {style: {fontSize: 12, opacity: 0.65, marginBottom: 4}},
              "Not at a desk (" + unseated.length + ") \u2014 no room has been " +
              "created for them yet, so the building cannot draw them sitting:"),
            e("div", {style: {display: "flex", gap: 8, flexWrap: "wrap"}},
              unseated.map(function (c) {
                var colour = ANIM_COLOR[c.animation] || "#8a8f98";
                return e("span", {
                  key: c.agent_id,
                  title: c.slug + " \u00b7 " + c.seat_type,
                  style: {fontSize: 12, padding: "3px 8px", borderRadius: 999,
                          border: "1px solid " + colour}
                }, c.slug + " \u00b7 " + c.animation);
              })))
        : null,

      /* --- the tower's own honesty statement --- */
      e("p", {style: styles.note},
        v.motion_rule,
        e("span", {style: styles.pill},
          (v.characters || []).filter(function (c) {
            return MOTION[c.animation];
          }).length + " moving"),
        e("span", {style: styles.pill},
          (v.characters || []).filter(function (c) { return c.stalled; })
            .length + " stalled"),
        e("span", {style: styles.pill}, "read-only")));
  }

  window.__QUBIT_PLUGINS__.register("qubit-office", Office);
})();
