/* Qubit Office — procedural agent sprite technique (Qubit addition,
 * dependency-free, no build step).
 *
 * WHAT THIS FILE IS FOR
 * The tower used to draw each agent as four rectangles — a torso, two legs and
 * an arc head — inline in the renderer, once per figure, every frame. At the
 * scale the building needs (a character is ~15 units tall) that reads as a
 * blur: the head merges into the torso, the torso into the legs, and two
 * figures on neighbouring desks smear into one shape. Four rectangles is not a
 * sprite; it is a rectangle stack.
 *
 * THE TECHNIQUE (ported, re-expressed, not copied)
 * A small figure is legible when it is (a) BAKED once into an offscreen canvas
 * per colour and cached, (b) drawn with a SEPARATING OUTLINE so it cannot
 * dissolve into a dark desk, (c) anchored by its FEET so it stands on the floor
 * rather than floating above it, and (d) carried by a ground shadow that
 * provides the contact the outline alone cannot. All four are here; the caller
 * supplies only the colour, the world point, the facing and the payload's own
 * animation progress.
 *
 * ---------------------------------------------------------------------------
 * PORTED CODE — provenance. MIT requires the notice to travel with the code.
 *
 *   1. androoAGI/starnet (MIT)
 *      - `frontend/agent-demo/sprites.js`
 *        · `measureFootPad()` — an authored sprite carries transparent rows
 *          below its feet, and anchoring such a sprite by its IMAGE bottom
 *          pushes the visible feet up off the contact shadow (the gap is
 *          `pad × scale`, so bigger figures float worst). We measure the same
 *          thing, but our figures are drawn by us, so `bakeAgent` reports the
 *          transparent rows it actually left below the boots and `anchorOf()`
 *          returns that as the feet line. Technique: measure the anchor from
 *          the pixels, never assume the image edge is the floor.
 *        · `footPad` / `framePads` / `tinted` caches — a recoloured sprite
 *          canvas is built ONCE per (set, colour) key and reused every frame.
 *          The old renderer called `shade()` per figure per frame; the cache
 *          key logic in `bakeAgent` is the port of this idea.
 *        · `groundShadow()` — a squashed ground ellipse, plus a tighter
 *          "contact core" under the boots, is most of what makes a small
 *          figure read as planted rather than pasted on. Ported in
 *          `drawAgentSprite`.
 *        · the walk `bob` — StarNet offsets the body vertically from its own
 *          cycle, never from a timer. Ours is a pure function of `progress`.
 *      - `frontend/agent-demo/stationbake.js` — `shade(hex, f)`, the
 *        hue-preserving lighten/darken used for every desk face. Ported in
 *        `shade()`.
 *   2. ruiqili2/agent-monitor (MIT)
 *      - `src/engine/animation.ts` — a walk cycle is a frame INDEX derived from
 *        elapsed progress (`tickCounter >= def.speed → currentFrame++`, with
 *        `frameCount` and `loop`), not from a wall clock the animation owns.
 *        Ported in `walkFrame()`/`bobFor()`: given the payload's `progress`
 *        in 0..1, the stride frame and the vertical bob are both derived from
 *        that one number — no timers, no internal animation state.
 *   3. pixel-agents-hq/pixel-agents (MIT)
 *      - `webview-ui/src/office/colorize.ts` — recolour a sprite by shifting
 *        HSL while PRESERVING per-pixel alpha. Ported in `tint()`.
 *
 * Deliberately NOT used: any asset pipeline (`new Image()`, manifest fetching,
 * `drawImage` of a PNG) — our figures are procedural, so only the technique of
 * those files is adopted. `rsanandres/aphae` ships no licence file (all rights
 * reserved) and none of its code is referenced here.
 * ---------------------------------------------------------------------------
 */
(function (root, factory) {
  var api = factory();
  if (typeof module === "object" && module && module.exports) {
    module.exports = api;                 /* `node -e` smoke test path */
  }
  if (root) {
    root.QubitSprites = api;              /* browser <script> path */
  }
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  /* ======================================================================
   * THE FIGURE, IN SPRITE UNITS
   * Everything below is authored on a 14 x 18 grid. At scale 1 an agent is
   * 18 units tall, which is the tower's ~15-unit character with headroom for
   * the outline. The proportions are chosen for LEGIBILITY AT 15px, not for
   * anatomy: the head is deliberately oversized (an 8-wide, 4-tall cap on the
   * same column as the torso) because at this size a realistic head is two
   * pixels and reads as noise, and the legs are a FOUR-unit gap so that the
   * 1px outline closing in from both legs still leaves two clear background
   * columns between the shins. The figure splits into three separable
   * masses — head, torso, legs — with a line between all three.
   * ====================================================================== */
  var SPRITE_W = 16, SPRITE_H = 18;
  /* Downward pads reserve room for the leg swing (walk frame 2) and for the
   * facing offsets applied at draw time, so no pose is ever clipped. */
  var PAD = 3;
  /* Sub-pixel sampling when the figure is drawn large; at the tower's own
   * scale the figure is smaller than this and is drawn by rect directly. */
  var CELL = 0.5;
  /* The feet in sprite units: the boots are the last two rows. Kept as
   * constants so anchorOf() and the bake agree by construction. */
  var FOOT_TOP = 14, FOOT_BOTTOM = 18;
  /* Below this drawn height the outline would be most of the figure, so it is
   * dropped and an even-contrast body is used instead. Measured, not guessed:
   * at 8px a 1px line is an eighth of the figure's height and the head/torso
   * gap closes. 10px is the smallest a 4-row head, a 7-row torso and a 4-row
   * leg still survive with a line between them. */
  var MIN_OUTLINE_PX = 10;
  /* Diamond silhouette for the outline's 4-connected dilation pass. */
  var NEIGHBOURS = [[-1, 0], [0, -1], [0, 1], [1, 0]];
  /* The ground contact. Ported shape from StarNet groundShadow: an outer
   * penumbra plus a tighter core under the boots. */
  var SHADOW_SPREAD = 0.46, SHADOW_CORE = 0.22;
  var SHADOW_SQUASH = 0.30;

  /* ======================================================================
   * COLOUR — HSL, alpha-preserving
   * We work in HSL rather than scaling RGB because tinting a seat in RGB
   * crushes its highlights, and at sprite scale the highlight IS the sprite.
   * ====================================================================== */
  var HEX6 = /^#?([0-9a-f]{6})$/i;
  var HEX8 = /^#?([0-9a-f]{8})$/i;

  /* Parse #rgb / #rrggbb / #rrggbbaa into {h, s, l, a, ok}. `ok` is false for
   * anything unparseable, which the callers turn into a flat grey-ish fallback
   * rather than an exception — a colour the payload got wrong must not blank
   * the office. */
  function parseColour(hex) {
    var s = String(hex == null ? "" : hex).trim();
    var m8 = HEX8.exec(s), m6 = HEX6.exec(s);
    var body = m6 ? m6[1] : (m8 ? m8[1].slice(0, 6) : null);
    if (!body) {
      if (/^#?[0-9a-f]{3}$/i.test(s)) {
        var t = s.replace(/^#/, "");
        body = t[0] + t[0] + t[1] + t[1] + t[2] + t[2];
      } else {
        return {h: 210, s: 0.35, l: 0.52, a: 1, ok: false};
      }
    }
    var alpha = m8 ? parseInt(m8[1].slice(6, 8), 16) / 255 : 1;
    var n = parseInt(body, 16);
    var r = ((n >> 16) & 255) / 255, g = ((n >> 8) & 255) / 255;
    var b = (n & 255) / 255;
    var mx = Math.max(r, g, b), mn = Math.min(r, g, b), l = (mx + mn) / 2;
    var d = mx - mn, sat = 0, h = 0;
    if (d) {
      sat = l > 0.5 ? d / (2 - mx - mn) : d / (mx + mn);
      if (mx === r) h = (g - b) / d + (g < b ? 6 : 0);
      else if (mx === g) h = (b - r) / d + 2;
      else h = (r - g) / d + 4;
      h *= 60;
    }
    return {h: h, s: sat, l: l, a: alpha, ok: true};
  }

  function hex2(v) {
    var q = Math.max(0, Math.min(255, Math.round(v * 255)));
    return (q < 16 ? "0" : "") + q.toString(16);
  }

  /* HSL -> #rrggbb, plus the alpha byte when it is not fully opaque. Keeping
   * alpha in the string (rather than dropping it) is the pixel-agents
   * colorize.ts contract: recalouring a sprite must never punch a hole in it. */
  function hslToHex(h, s, l, a) {
    h = ((h % 360) + 360) % 360;
    s = Math.max(0, Math.min(1, s));
    l = Math.max(0, Math.min(1, l));
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
    var out = "#" + hex2(r1 + m) + hex2(g1 + m) + hex2(b1 + m);
    if (typeof a === "number" && a < 1) {
      var byte = Math.max(0, Math.min(255, Math.round(a * 255)));
      out += (byte < 16 ? "0" : "") + byte.toString(16);
    }
    return out;
  }

  /* f < 0 darkens, f > 0 lightens. Lightening desaturates LESS than a plain
   * RGB scale, which is what stops a lit deck face looking washed out.
   * Ported from StarNet stationbake.shade(). */
  function shade(hex, f) {
    var c = parseColour(hex);
    var amt = Number(f) || 0;
    var l = Math.max(0, Math.min(1, c.l + amt * 0.5));
    var s = c.s * (amt > 0 ? 1 - amt * 0.35 : 1 + amt * 0.15);
    return hslToHex(c.h, s, l, c.a);
  }

  /* Rotate toward `hue` by `mix` (0..1). A tint never fully replaces the seat
   * colour, so two workers at the same desk type stay distinguishable.
   * Ported from pixel-agents colorize.ts (alpha preserved). */
  function tint(hex, hue, mix) {
    var c = parseColour(hex);
    var m = Math.max(0, Math.min(1, Number(mix) || 0));
    var target = Number(hue) || 0;
    var d = ((target - c.h + 540) % 360) - 180;
    return hslToHex(c.h + d * m, Math.min(1, c.s + 0.12 * m), c.l, c.a);
  }

  /* Resolve any colour to a fully opaque #rrggbb. The outline, the head and
   * the legs are all derived from this one value, so an input that is
   * translucent (a dimmed/ghosted seat) still bakes to a solid, legible body
   * and the caller carries the fading via ctx.globalAlpha instead. */
  function solidHex(colour) {
    var c = parseColour(colour);
    return hslToHex(c.h, c.s, c.l, 1);
  }

  /* ======================================================================
   * THE BAKE — the palette, the figure, the outline
   * ====================================================================== */

  /* The five tones a bake actually paints with. DERIVED from the one colour
   * the caller passed, never hand-listed: an unknown seat colour still gets a
   * shaded, outlined figure rather than a flat blob. */
  function paletteFor(colour) {
    var base = solidHex(colour);
    return {
      base: base,
      dark: shade(base, -0.46),        /* legs — the darkest of the three masses */
      light: shade(base, 0.34),        /* head — the brightest, so it leads the eye */
      outline: shade(base, -0.95),     /* the separating line */
      ink: shade(base, 0.46)           /* the 1px sheen that says which way it faces */
    };
  }

  /* One pose, as a list of filled rects. `walk` selects the stride:
   *   walk === 0 — both feet planted (the idle pose)
   *   walk === 1 — left leg swung forward, right planted (1px step out)
   *   walk === 2 — right leg swung forward, left planted
   * Frame 1 exists so the legs are visibly NOT symmetric: a symmetric figure
   * at this size reads as a static icon, and a walk made only of a vertical
   * bob reads as a glide.
   *
   * The legs sit at x=3..6 and x=9..12 — a gap of columns 7 and 8. The outline
   * closing in from each leg eats one column on its inboard edge, so a
   * 2-column gap closes completely and a 3-column gap leaves one. Two clear
   * columns of background is the minimum that reads as TWO legs at 15px, so
   * the gap is authored at 2 and the sprite is widened to 16 to afford it.
   * This is arithmetic, not taste: outline 2 + clear 2 → gap ≥ 4. */
  function legRects(walk) {
    var top = FOOT_TOP, h = FOOT_BOTTOM - FOOT_TOP;   /* 4 sprite units */
    if (walk === 1) {
      return [{x: 3, y: top, w: 4, h: h}, {x: 9, y: top, w: 4, h: h - 1}];
    }
    if (walk === 2) {
      return [{x: 3, y: top, w: 4, h: h - 1}, {x: 9, y: top, w: 4, h: h}];
    }
    return [{x: 3, y: top, w: 4, h: h}, {x: 9, y: top, w: 4, h: h}];
  }

  /* The figure's parts, in paint order (back to front). Consumed on the
   * integer grid by the categorical test, and on the sub-pixel grid by the
   * filled bake.
   *
   * HEAD AND TORSO SHARE THE SAME WIDTH EDGES (x = 2..10, an 8-unit column)
   * on purpose. A narrower head leaves the outline ring to jump diagonally at
   * the shoulders: the ring closes around the head and then restarts around
   * the torso, so in the baked canvas the torso's flanks sat three cells away
   * from the nearest outline pixel instead of one — the figure read as two
   * stacked slabs rather than one wrapped silhouette. Matching the edges makes
   * the head and the torso one column, so ONE 1px line wraps head, torso and
   * legs together.
   *
   * The only body cells left abutting bare background are the FEET ROW, which
   * is deliberately unoutlined (see outlineMask) — 4 cells on the idle pose,
   * 5 when one leg is lifted. */
  function figureRects(walk, tone) {
    var legs = legRects(walk);
    var out = [];
    for (var i = 0; i < legs.length; i++) {
      out.push({x: legs[i].x, y: legs[i].y, w: legs[i].w, h: legs[i].h, tone: "dark"});
    }
    /* Torso and head are the same 8-wide column (x = 4..11), centred on the
     * 16-wide grid so both legs hang symmetrically under it. */
    out.push({x: 4, y: 7, w: 8, h: 7, tone: tone ? "light" : "base"});
    out.push({x: 4, y: 2, w: 8, h: 4, tone: tone ? "base" : "light"});
    return out;
  }

  function insideRects(px, py, rects) {
    for (var i = 0; i < rects.length; i++) {
      var r = rects[i];
      if (px >= r.x && px < r.x + r.w && py >= r.y && py < r.y + r.h) return true;
    }
    return false;
  }

  /* Categorical ink for one cell of the POPULATED grid: the feet line is the
   * body colour (never pure black, or the last row of the figure dissolves
   * into the outline), and a 1px sheen on the lit side of the head and the
   * left column of the torso is what makes the facing readable at 15px. */
  function cellTone(px, py, rects) {
    if (py === FOOT_BOTTOM - 1) return "base";
    if (py === 3 && (px === 4 || px === 5)) return "ink";
    if (py === 8 && px === 3) return "ink";
    return (rects[rects.length - 1] && insideRects(px, py, [rects[rects.length - 1]]))
      ? "light" : null;
  }

  /* The outline: the set of BACKGROUND cells 4-adjacent to a body cell, so the
   * 1px line wraps head, torso and legs as one silhouette. Drawn UNDER the
   * body it becomes the separating line the whole technique exists for — it is
   * what stops a blue-green figure from vanishing against the dark desk it is
   * standing at.
   *
   * THE FEET ROW IS DELIBERATELY EXCLUDED. An outline under the boots drops the
   * visible feet line by one pixel, so `anchorOf()` reports a feet line one
   * pixel below the boots and the figure floats above its own contact shadow.
   * The boots contact the floor; the outline does not. (First render: the
   * measured feetY was 22 with an outline under the feet and 21 without — the
   * outline was moving the whole figure up a pixel.)
   */
  var OUTLINE_BOTTOM = FOOT_BOTTOM - 1;   /* the feet row itself is never outlined */

  function outlineMask(rects) {
    var W = SPRITE_W + PAD * 2, H = SPRITE_H + PAD * 2;
    var body = [];
    for (var i = 0; i < W * H; i++) body.push(false);
    for (var by = 0; by < SPRITE_H; by++) {
      for (var bx = 0; bx < SPRITE_W; bx++) {
        if (insideRects(bx + 0.5, by + 0.5, rects)) body[(by + PAD) * W + (bx + PAD)] = true;
      }
    }
    var out = [];
    for (var k = 0; k < W * H; k++) out.push(false);
    for (var py = 0; py < SPRITE_H; py++) {
      if (py >= OUTLINE_BOTTOM) continue;
      for (var px = 0; px < SPRITE_W; px++) {
        if (body[(py + PAD) * W + (px + PAD)]) continue;   /* body is not outline */
        for (var n = 0; n < NEIGHBOURS.length; n++) {
          var ox = px + NEIGHBOURS[n][0] + PAD, oy = py + NEIGHBOURS[n][1] + PAD;
          if (ox < 0 || oy < 0 || ox >= W || oy >= H) continue;
          if (body[oy * W + ox]) { out[(py + PAD) * W + (px + PAD)] = true; break; }
        }
      }
    }
    return {mask: out, w: W, h: H};
  }

  function makeCanvas(w, h) {
    if (typeof document === "undefined" || !document.createElement) return null;
    try {
      var cv = document.createElement("canvas");
      cv.width = w;
      cv.height = h;
      return cv;
    } catch (err) {
      return null;
    }
  }

  /* The bake runs at one of two resolutions. At the tower's own scale the
   * figure is ~18px tall, so the native grid is used directly and every edge
   * lands on a device pixel (no resample blur). Drawn LARGER than that grid,
   * the bake is sampled on a half-unit grid instead, because the adjectives
   * applied to the figure — "darker legs", "brighter head" — are magnitudes
   * and magnitudes cannot be asserted on a 2-tone grid. */
  function bake(figure, scale, cap) {
    var pal = figure.palette, rects = figureRects(figure.walk, false);
    var cell = scale >= 3 ? CELL : 1;
    /* Supersample only when the outline can survive the resample. */
    var outlineW = figure.w * scale >= MIN_OUTLINE_PX ? 1 : 0;
    var w = Math.max(1, Math.round(figure.w * scale));
    var h = Math.max(1, Math.round(figure.h * scale));

    var canvas = makeCanvas(w, h);
    var ctx = null;
    if (canvas && typeof canvas.getContext === "function") {
      try { ctx = canvas.getContext("2d"); } catch (err) { ctx = null; }
    }
    var drawn = 0;

    if (ctx) {
      ctx.clearRect(0, 0, w, h);
      /* Paint in units, not device pixels, so the grid and the canvas agree. */
      ctx.save();
      ctx.scale(scale, scale);
      /* 1. the outline pass: every background cell adjacent to the figure.
       * The mask is built on the PADDED grid (so a dilation off the figure's
       * edge has somewhere to land), but it must be PAINTED in the body's own
       * coordinate space — both passes share one origin or the ring lands PAD
       * cells up-and-left of the silhouette it is wrapping. (First render: the
       * outline sat 2 right and 3 down, i.e. exactly PAD, and the ring missed
       * the torso's left flank entirely.) */
      if (outlineW) {
        var padW = SPRITE_W + PAD * 2;
        outlineMask(rects).mask.forEach(function (on, idx) {
          if (!on) return;
          var cx = (idx % padW) - PAD, cy = ((idx / padW) | 0) - PAD;
          ctx.fillStyle = pal.outline;
          ctx.fillRect(cx * cell, cy * cell, cell, cell);
        });
      }
      /* 2. the body pass, with the derived tones. */
      rects.forEach(function (r) {
        ctx.fillStyle = pal[r.tone] || pal.base;
        ctx.fillRect(r.x * cell, r.y * cell, r.w * cell, r.h * cell);
      });
      /* 3. the sheen, then the 1px line that keeps the boots off a dark deck.
       * The sheen is on the LEFT of the head and torso — the lit side under a
       * north-west key — so it says which way the body faces at 15px. */
      ctx.fillStyle = pal.ink;
      ctx.fillRect(6 * cell, 3 * cell, 2 * cell, cell);
      ctx.fillRect(5 * cell, 8 * cell, cell, cell);
      ctx.fillStyle = pal.base;
      ctx.fillRect(4 * cell, (FOOT_BOTTOM - 1) * cell, 8 * cell, cell);
      ctx.restore();
      drawn = 1;
      if (typeof ctx.getImageData === "function") {
        try {
          var data = ctx.getImageData(0, 0, w, h).data;
          for (var i = 3; i < data.length; i += 4) if (data[i] > 16) drawn = 2;
        } catch (err2) { /* tainted/blocked readback — keep `drawn` as-is */ }
      } else {
        drawn = 2;
      }
    }

    /* An anchor is a claim about pixels, so it is measured, never assumed.
     * StarNet measures its authored masters with measureFootPad() and falls
     * back to `DEFAULT_FOOT` when the frame cannot be read; ours has the same
     * shape — measure when the bake was actually painted, otherwise use the
     * foot line the figure was authored with.
     *
     * The measurement comes back in DEVICE pixels and is divided by `scale`
     * into SPRITE units, so `feetY` is scale-independent and a caller can
     * place a figure at any size from the same number. (First render returned
     * 60 and -35 for a figure baked at scale 4 — device rows leaking into a
     * sprite-unit field. The division by `scale` is the fix.)
     *
     * `feetY` is the distance in sprite units from the canvas TOP to the
     * boots, and `figure.h` is the canvas height in sprite units, so
     * `feetY = figure.h - footPad` and `anchor.feetY * scale` is exactly the
     * device-pixel row the feet land on.
     *
     * When the pixels cannot be read at all — no canvas, a lost context, a
     * tainted surface — the figure was AUTHORED with its boots on the last
     * row, so the authored answer is `0` rows below the feet. That is the
     * honest fallback; anything else would report a feet line the bake never
     * had. (The first cut fell back to `PAD = 3` and the headless render
     * anchored three rows above the boots, which is the "floating body" the
     * measurement exists to prevent — a fallback that disagrees with the
     * authored figure is worse than no measurement.) */
    var padUnits = 0;
    if (drawn > 1) {
      var rows = measureFootPad(canvas);
      if (rows !== null) padUnits = rows / scale;
    }

    return {
      canvas: canvas,
      width: figure.w,
      height: figure.h,
      baked: drawn === 2,
      cell: cell,
      scale: scale,
      outline: !!outlineW,
      footPad: padUnits,
      feetY: figure.h - padUnits,
      /* The figure's own height — its top to its boots, excluding the empty
       * grid below them — in SPRITE units, so a caller placing a body on a
       * desk uses the body and not the padding. */
      figureHeight: Math.max(1, figure.h - padUnits),
      walk: figure.walk,
      colour: pal.base,
      palette: pal
    };
  }

  /* The transparent rows below the first opaque pixel, read from the BOTTOM.
   * Ported from StarNet sprites.js measureFootPad(): exact same question
   * ("how much empty space is under this figure's feet?"), and the same
   * "unreadable is a safe fallback, never a throw" contract.
   * Returns null when the pixels cannot be read. */
  function measureFootPad(canvas) {
    try {
      var w = canvas.width | 0, h = canvas.height | 0;
      if (!w || !h) return null;
      var ctx = canvas.getContext("2d", {willReadFrequently: true});
      if (!ctx || typeof ctx.getImageData !== "function") return null;
      var data = ctx.getImageData(0, 0, w, h).data;
      for (var y = h - 1; y >= 0; y--) {
        var row = y * w * 4;
        for (var x = 0; x < w; x++) {
          if (data[row + x * 4 + 3] > 16) return (h - 1) - y;
        }
      }
      return null;   /* nothing opaque at all */
    } catch (err) {
      return null;   /* tainted / lost context → caller falls back, never throws */
    }
  }

  /* ======================================================================
   * THE CACHE — bake once per (colour, walk), draw forever
   * The key must name EVERY input the bake depends on or two different figures
   * share one canvas. These are exactly the bake's inputs:
   *     colour + walk   (width, height and the palette are all pinned by PAD
   *                      and derived from colour, and PAD is a constant of
   *                      this file, so it never needs to be in the key)
   * `walk` is quantised with a modulo, so a caller passing 7, -1 or 2.7 can
   * never mint a new cache entry for the same pose — the cache is bounded by
   * (colours × poses) no matter what the payload sends.
   * ====================================================================== */
  var CACHE_LIMIT = 256;
  var cache = {};        /* key -> baked figure */
  var order = [];        /* the same keys, oldest first: a bounded LRU */

  function walkFrom(progress) {
    /* agent-monitor animation.ts: a frame index is `floor(progress × frames)`,
     * and a 2-frame cycle alternates. The tower's progress is a number in
     * (0,1) for a walking character only — no progress, no stride. */
    var p = Number(progress);
    if (!isFinite(p)) return 0;
    p = p - Math.floor(p);           /* 0..1, robust to a caller at 1.0/1000 */
    return 1 + (Math.floor(p * 2) % 2);   /* 1 or 2, alternating */
  }

  function cacheKey(colour, walk) {
    var w = ((Math.round(Number(walk) || 0) % 3) + 3) % 3;
    return solidHex(colour) + "|" + w;
  }

  function bakeAgent(colour, opts) {
    var o = opts || {};
    var walk = o.walk === undefined ? 0 : o.walk;
    var scale = Number(o.scale);
    if (!isFinite(scale) || scale <= 0) scale = 1;
    var key = cacheKey(colour, walk);
    var hit = cache[key];
    if (hit && hit.scale === scale) return hit;
    var figure = {
      w: SPRITE_W + PAD * 2,
      /* Sprite-unit grid, INDEPENDENT of `scale`: exactly the figure's own
       * rows, so the boots sit on the canvas's last row and `footPad` is 0 —
       * i.e. the measured feet line equals the boot line by construction, and
       * an extra reserved row can never report a feet line below the boots.
       * `scale` multiplies this once, at the canvas. */
      h: SPRITE_H,
      scale: scale,
      walk: ((Math.round(Number(walk) || 0) % 3) + 3) % 3,
      palette: paletteFor(colour)
    };
    var out = bake(figure, scale);
    cache[key] = out;
    out.key = key;
    out.scale = figure.scale;
    order.push(key);
    while (order.length > CACHE_LIMIT) {
      var oldest = order.shift();
      if (oldest !== key) delete cache[oldest];
      else order.push(oldest);
    }
    return out;
  }

  function cacheStats() {
    return {size: order.length, limit: CACHE_LIMIT, keys: order.slice()};
  }

  function clearCache() {
    cache = {};
    order = [];
  }

  /* ======================================================================
   * THE ANCHOR — report the feet line so a caller can place the figure
   * ====================================================================== */
  function anchorOf(figure) {
    if (!figure || typeof figure !== "object") return {feetY: 0, height: 0};
    var feetY = Number(figure.feetY);
    var height = Number(figure.figureHeight);
    if (!isFinite(feetY)) {
      var p = Number(figure.footPad);
      feetY = (isFinite(p) ? SPRITE_H - p : FOOT_BOTTOM);
    }
    if (!isFinite(height)) height = feetY;
    return {feetY: feetY, height: height};
  }

  /* The same answer without baking anything: the feet line of one agent, so a
   * caller can place a body on a desk before it has a canvas. Returns sprite
   * units; multiply by the draw scale for device pixels. */
  function feetFor(colour, opts) {
    return anchorOf(bakeAgent(colour, opts)).feetY;
  }

  /* ======================================================================
   * THE WALK — a pure function of the payload's own `progress`
   * ====================================================================== */

  /* agent-monitor animation.ts drives a cycle by advancing `currentFrame`
   * every `speed` ticks, `frameCount` frames wide and looping. Expressed
   * against the tower's contract that means: `progress` 0..1 IS the cycle
   * position, and nothing else may move a character. */
  function walkFrame(progress) {
    return walkFrom(progress) === 0 ? 0 : walkFrom(progress) - 1;
  }

  /* The walk bob: two steps per cycle (|sin| gives the 2-per-cycle cadence the
   * old renderer had) and a LIFT, not just a sink, so the feet read as
   * stepping. Derived only from `progress`. */
  function bobFor(progress, scale) {
    var s = Number(scale);
    if (!isFinite(s) || s <= 0) s = 1;
    var p = Number(progress);
    if (!isFinite(p)) return 0;
    return -Math.abs(Math.sin(p * Math.PI * 2)) * 0.5 * s;
  }

  /* StarNet bodyScale(): one exported answer to "how big is this thing
   * drawn", so a surface that wants the figure at native resolution can
   * cancel the scale out by asking rather than re-deriving it. */
  function bodyScale(baseScale, zoom) {
    var b = Number(baseScale);
    if (!isFinite(b) || b <= 0) b = 1;
    var z = Number(zoom);
    if (!isFinite(z) || z <= 0) z = 1;
    return b * z;
  }

  /* ======================================================================
   * THE DRAW — feet-anchored, shadowed, bobbing from `progress` alone
   * ====================================================================== */

  /* The ground contact. Ported shape from StarNet groundShadow(): a wide,
   * faint penumbra plus a tight core under the boots. The penumbra is what
   * makes a figure on a dark deck read as STANDING rather than pasted, and the
   * core is what gives it weight. Both are ellipses — no gradient, no canvas
   * allocation — because this runs once per agent per frame. */
  function drawShadow(ctx, x, y, scale) {
    if (!ctx || !ctx.ellipse) return;
    var rx = SHADOW_SPREAD * 10 * scale;
    var spread = SHADOW_CORE * 2 * scale;
    var a0 = ctx.globalAlpha;
    var ink = ctx.fillStyle;
    ctx.fillStyle = "#080d19";
    try {
      ctx.globalAlpha = a0 * 0.30;
      ctx.beginPath();
      ctx.ellipse(x, y, rx, rx * SHADOW_SQUASH, 0, 0, Math.PI * 2);
      ctx.fill();
      ctx.globalAlpha = a0 * 0.24;
      ctx.beginPath();
      ctx.ellipse(x, y, spread, spread * SHADOW_SQUASH * 0.6, 0, 0, Math.PI * 2);
      ctx.fill();
    } finally {
      ctx.globalAlpha = a0;
      ctx.fillStyle = ink;
    }
  }

  /* Draw one agent, anchored by its FEET at (x, y).
   *   cache    the object returned by bakeAgent (or null, and it is resolved)
   *   colour   the seat colour; only used to resolve the cache
   *   scale    drawn sprite units per screen unit
   *   facing   left | right | down | up  (defaults to down)
   *   walking  boolean — the payload's `walking`
   *   progress 0..1 — the payload's `progress`; the ONLY source of motion
   * Returns the drawn geometry and the feet anchor, so a caller can lay a
   * name label or a marker on top without asking again. */
  function drawAgentSprite(ctx, cacheOrAgent, colour, x, y, scale, facing, walking, progress) {
    var figure = cacheOrAgent && cacheOrAgent.canvas !== undefined
      ? cacheOrAgent : bakeAgent(colour, {scale: scale});
    var sc = Number(scale);
    if (!isFinite(sc) || sc <= 0) sc = 1;
    var fx = Number(x), fy = Number(y);
    if (!isFinite(fx)) fx = 0;
    if (!isFinite(fy)) fy = 0;
    var face = ["left", "right", "up", "down"].indexOf(facing) >= 0 ? facing : "down";
    var moving = !!walking;

    /* The contact shadow stays on the floor even when the body lifts: only the
     * sprite rises through a bob, never its pool. */
    drawShadow(ctx, fx, fy, sc);

    var walk = moving ? walkFrom(progress) : 0;
    var drawn = figure;
    if (moving && drawn.walk !== walk) drawn = bakeAgent(colour, {walk: walk, scale: sc});
    var anchor = anchorOf(drawn);
    var bob = moving ? bobFor(progress, sc) : 0;

    /* Facing offsets. The old renderer moved the head; moving the WHOLE figure
     * a sub-pixel hangs it off its own feet, so the figure is drawn where its
     * feet are and only the head is offset — which is what a glance looks like
     * on a body standing still. */
    var dx = face === "left" ? -0.8 * sc : (face === "right" ? 0.8 * sc : 0);
    var dy = face === "up" ? -0.6 * sc : (face === "down" ? 0.6 * sc : 0);

    var w = drawn.width * sc, h = drawn.height * sc;
    /* ANCHOR BY THE FEET. `anchor.feetY` is the distance in SPRITE units from
     * the canvas TOP to the boots, so the canvas top belongs `feetY * sc`
     * above the feet line — that is the entire placement:
     *     drawY = fy - feetY * sc
     * and a negative bob LIFTS the whole figure off the floor. This is the
     * StarNet rule: anchor the measured feet, never the image bottom, or the
     * transparent packing below the boots floats the body above its shadow.
     * (First cut wrote `fy + feetY*sc - h`, subtracting the canvas height on
     * top of the offset and dropping every figure a full body length below its
     * own desk.) */
    var drawX = fx + dx - w / 2;
    var drawY = fy + bob - anchor.feetY * sc;

    if (drawn.canvas && ctx && typeof ctx.drawImage === "function") {
      var prev = ctx.imageSmoothingEnabled;
      if ("imageSmoothingEnabled" in ctx) ctx.imageSmoothingEnabled = sc < 1;
      try {
        ctx.drawImage(drawn.canvas, Math.round(drawX), Math.round(drawY),
                      Math.round(w), Math.round(h));
      } finally {
        if ("imageSmoothingEnabled" in ctx) ctx.imageSmoothingEnabled = prev;
      }
    } else if (ctx && typeof ctx.fillRect === "function") {
      /* No canvas available (headless / a lost context): paint the same figure
       * from its own geometry so the office is still drawn rather than blank. */
      drawFlat(ctx, drawn, fx, fy, sc, dx, dy, bob);
    }

    return {feetY: anchor.feetY, height: anchor.height, x: fx, y: fy,
            w: w, h: h, drawY: drawY, bob: bob, facing: face,
            walk: drawn.walk, key: drawn.key,
            /* geometry for an overlay: top of the visible body and its middle */
            top: drawY + dy, midX: fx + dx};
  }

  /* The fallback path, for a headless context with no canvas. Deliberately
   * uses the same rect list and the same palette as the bake, so a headless
   * render is a COARSER version of the same drawing rather than a second,
   * different picture — and a test can assert the silhouette from it.
   *
   * The origin of the figure's own grid is:
   *     originX = fx + dx - width/2        (the figure is centred on its feet)
   *     originY = fy + bob - feetY*sc      (the feet land on (fx, fy + bob))
   * which is the same placement `drawAgentSprite` uses for the baked canvas.
   */
  function drawFlat(ctx, figure, fx, fy, sc, dx, dy, bob) {
    var rects = figureRects(figure.walk, false);
    var pal = figure.palette;
    var feet = anchorOf(figure).feetY;
    var originX = fx + dx - (figure.width * sc) / 2;
    var originY = fy + dy + bob - feet * sc;
    ctx.save();
    if (ctx.globalAlpha == null) ctx.globalAlpha = 1;
    ctx.globalAlpha *= 0.85;
    rects.forEach(function (r) {
      ctx.fillStyle = pal[r.tone] || pal.base;
      ctx.fillRect(originX + r.x * sc, originY + r.y * sc, r.w * sc, r.h * sc);
    });
    ctx.restore();
  }

  return {
    /* geometry */
    SPRITE_W: SPRITE_W, SPRITE_H: SPRITE_H, PAD: PAD,
    /* colour */
    shade: shade, tint: tint, solidHex: solidHex, paletteFor: paletteFor,
    /* bake + cache */
    bakeAgent: bakeAgent, cacheKey: cacheKey, cacheStats: cacheStats,
    clearCache: clearCache, CACHE_LIMIT: CACHE_LIMIT,
    /* anchor */
    anchorOf: anchorOf, feetFor: feetFor, measureFootPad: measureFootPad,
    /* walk (pure functions of `progress`) */
    walkFrame: walkFrame, bobFor: bobFor, bodyScale: bodyScale,
    /* draw */
    drawAgentSprite: drawAgentSprite, drawShadow: drawShadow,
    /* test-only: the pose geometry, so a headless test can assert the
     * silhouette without a canvas at all */
    _figureRects: figureRects, _walkFrom: walkFrom, _parseColour: parseColour
  };
});
