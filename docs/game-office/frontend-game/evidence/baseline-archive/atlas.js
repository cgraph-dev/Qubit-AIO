/* Qubit Office — the baked-sprite atlas loader (Qubit addition, dependency-free).
 *
 * WHAT THIS FILE IS FOR
 * ---------------------------------------------------------------------------
 * The agent figures used to be drawn by `sprites.js` from four rectangles
 * baked into an offscreen canvas. That technique removed the smear the inline
 * four-rectangle figure had, but a four-rectangle figure is still not a
 * sprite: at the tower's own scale (~15 screen px) the head merges into the
 * torso and neighbours on adjacent desks read as one shape. This module is the
 * half of the fix that FETCHES real art; `sprites.js` is the half that DRAWS
 * it. Together they replace the procedural bake with the vendored PNG atlas.
 *
 * THE ONE LAW THIS FILE MUST NOT BREAK (M12)
 * ---------------------------------------------------------------------------
 * A sprite changes HOW a figure is DRAWN, never WHERE it SORTS. The renderer
 * sorts on `depthKey(x, y, z)` -> `sortByDepth()` -> `paintOrder()`, keyed on
 * the screen-y of the cell the figure STANDS ON (its feet). This loader reports
 * each pose's feet line so `sprites.js` can anchor the PNG by its FEET exactly
 * as the vector figure was anchored. It never returns a sort key, never a
 * position, and never touches the payload. If it did, occlusion would regress.
 *
 * THE ANCHOR IS MEASURED, NOT ASSUMED (ported technique)
 * ---------------------------------------------------------------------------
 * A vendored pose is a 144x144 PNG with a large transparent margin below the
 * boots (measured: `rot_north` has 32 transparent rows under the feet,
 * `sit_north_4` has 37). Anchoring such a sprite by its IMAGE bottom lifts the
 * visible feet `pad` pixels above the contact shadow — the larger the figure,
 * the worse the float. `measureFootPad()` here answers "how many transparent
 * rows are under this pose's feet?" and the answer is reported in the pose's
 * own pixel units, so `sprites.js` can subtract it at any draw scale.
 * Ported from androoAGI/starnet `frontend/agent-demo/sprites.js`
 * `measureFootPad()` (MIT) — same question, same "unreadable is a safe
 * fallback, never a throw" contract.
 *
 * THE SURFACE IS FROZEN AS `window.QubitAtlas` (A2 -> A3 seam, 90-AGENT-CONTRACT
 * §2.7). A3 calls into it; A3 must not reach past it into this file's internals.
 *
 * THE WEATHERING RULE (50-ARCHITECTURE §5.3)
 * ---------------------------------------------------------------------------
 * No event, no pose. `tower._ANIMATION` maps an event kind to one of 12
 * animation states. This loader exposes a pose for EXACTLY those states and no
 * more: a pose the state machine cannot reach is never resolved, so an atlas
 * with poses nothing emits simply does not draw them. `POSE_MAP` below is the
 * whole vocabulary, and it is the same list the renderer's `ANIM_COLOR` uses.
 *
 * MISSING ART MUST NOT BLANK THE OFFICE
 * ---------------------------------------------------------------------------
 * A1's manifest may not have landed yet, a set may be absent, a single pose may
 * be missing, or the image may fail to decode. Every one of those returns a
 * NAMED fallback (`atlas.gaps`) rather than throwing or drawing nothing:
 *   1. the requested (state, dir) pose, if it exists in the set;
 *   2. the nearest available pose of the same state (walking at a missing
 *      facing falls back to a rot/stand of that facing, with the bob kept);
 *   3. the set's `rot_south` / `rot_north` stand-in;
 *   4. no atlas at all -> `ready()` reports false and `sprites.js` keeps its
 *      procedural figure (the office renders; the gap is named).
 *
 * PROVENANCE / LICENCE
 * ---------------------------------------------------------------------------
 *   art:  androoAGI/starnet  `website/app/assets/sprites/approved_*` (MIT),
 *         vendored by A1 into `.../qubit-office/assets/sprites/` and declared
 *         in `assets/MANIFEST.json`. Neutral families only (owner ruling O1 /
 *         90-CORRECTIONS C4). No celebrity likeness is ever referenced here.
 *   code: `measureFootPad()` technique from starnet `frontend/agent-demo/
 *         sprites.js` (MIT). Nothing else is copied; the loader itself is ours.
 * Deliberately NOT used: `rsanandres/aphae` ships no licence (all rights
 * reserved) and none of its code is referenced.
 * ---------------------------------------------------------------------------
 */
(function (root, factory) {
  var api = factory();
  if (typeof module === "object" && module && module.exports) {
    module.exports = api;                 /* `node -e` smoke test path */
  }
  if (root) {
    root.QubitAtlas = api;                /* browser <script> path */
  }
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  /* ======================================================================
   * THE POSE VOCABULARY — exactly the states `tower._ANIMATION` can reach
   * (`voicebridge/office/tower.py:46-70`). A state not in this map has no
   * pose, and a pose in the atlas with no state here is never resolved
   * (50-ARCHITECTURE §5.3: art is a rendering of state, never a garnish).
   *
   * The value is which ATLAS FAMILY a state draws from:
   *   "rot"  — a standing turn (8 directions; the 8 `rot_*.png`)
   *   "walk" — a walking cycle (N/E/S/W; 6 frames each)
   *   "sit"  — seated at a desk (N/E/S/W; a single frame `sit_<dir>_4`)
   *   "type" — typing (NORTH ONLY; 4 frames `type_north_1..4`)
   * ====================================================================== */
  var POSE_FAMILY = {
    /* runner's own events -> a turn toward the desk */
    "working": "type",            /* skill_used / run_started: the model is running */
    "settling": "rot",            /* run_settled / ledger_write: a stand, cooling */
    "paused": "rot",              /* run_unpriced */
    "stopped": "rot",             /* run_failed */
    "done": "rot",                /* task_done */
    "idle": "rot",                /* no fresh event: a stand, no phantom motion */
    "blocked": "rot",             /* consent_denied */
    "glow": "rot",                /* memory_written */
    /* capability events -> a moving figure */
    "walking": "walk",            /* tool_call / mcp_call */
    "carrying": "walk",           /* note_written */
    "walking_to_manager": "walk", /* escalation */
    /* a meeting is a seated figure */
    "meeting": "sit",             /* meeting */
    "seated": "sit"               /* a seat whose own state is "at desk" */
  };

  /* Which atlas directions exist, per family. Measured from the vendored set,
   * NOT assumed (90-CORRECTIONS C3): `rot` is 8 directions, `walk` is 4
   * (N/E/S/W), `sit` is 4, `type` is NORTH ONLY. The loop below only ever asks
   * for a cell that is in these lists, so it can never index off an atlas. */
  var DIRECTIONS_8 = ["north", "north-east", "east", "south-east",
                      "south", "south-west", "west", "north-west"];
  var DIRECTIONS_4 = ["north", "east", "south", "west"];
  var FAMILY_DIRS = {
    "rot": DIRECTIONS_8,
    "walk": DIRECTIONS_4,
    "sit": DIRECTIONS_4,
    "type": ["north"]
  };

  /* The renderer's own facing words (`qubit-office.js` drawAgent / payload)
   * map onto atlas directions. `down` is the camera-facing south; `up` is
   * north. A diagonal the payload never sends still resolves, because the
   * projection can produce one. */
  var FACING_TO_DIR = {
    "down": "south",
    "up": "north",
    "left": "west",
    "right": "east",
    "south": "south", "north": "north", "west": "west", "east": "east",
    "north-east": "north-east", "north-west": "north-west",
    "south-east": "south-east", "south-west": "south-west"
  };

  /* Frame counts, read from the fact of the atlas (C3): walks are SIX frames,
   * type is FOUR, sit is a single frame. Held here so a caller can index a
   * cycle without knowing the filename scheme. */
  var FAMILY_FRAMES = { "rot": 1, "walk": 6, "sit": 1, "type": 4 };

  /* ======================================================================
   * STATE — the manifest, the loaded sets, the gaps
   * ====================================================================== */
  var manifest = null;          /* the parsed MANIFEST.json, or null */
  var manifestTried = false;    /* we only ever ask the network once per page */
  var assetBase = "";           /* the served prefix the manifest declared */
  var poses = null;             /* basename -> path, across every set */
  var sets = null;              /* set name -> {basename: path} */
  var images = {};              /* "set/pose" -> HTMLImageElement (or a stub) */
  var pads = {};                /* "set/pose" -> measured foot pad in px */
  var gaps = [];               /* every named fallback the page should show */
  var pending = 0;              /* images still decoding */

  function note(what) {
    if (gaps.indexOf(what) < 0) gaps.push(what);
  }

  /* ======================================================================
   * THE MANIFEST — A1's contract (`assets/MANIFEST.json`, {path, sha256, bytes})
   *
   * Read ONCE. The manifest is the single authority for "which poses exist":
   * we never probe a URL that is not in it, so a missing pose is answered from
   * the manifest and not from a 404 round-trip (90-WIRING-TRAPS Trap 9).
   * ====================================================================== */
  function manifestUrl(base) {
    var b = String(base || "");
    if (b && b.charAt(b.length - 1) !== "/") b += "/";
    return b + "MANIFEST.json";
  }

  /* A synchronous XHR is used on purpose: the first frame must not draw a
   * different figure from every later frame, and the renderer's contract is
   * that a figure is a pure function of the payload. The manifest is small
   * (~5 KB) and fetched exactly once. Timers are banned in this layer; XHR is
   * not a timer, and this is the only I/O besides the images themselves. */
  function loadManifest(url) {
    try {
      var xhr = new XMLHttpRequest();
      xhr.open("GET", url, false);
      xhr.send(null);
      if (xhr.status && xhr.status >= 200 && xhr.status < 300 && xhr.responseText) {
        return JSON.parse(xhr.responseText);
      }
      note("manifest: HTTP " + (xhr.status || "0") + " at " + url);
    } catch (err) {
      note("manifest: unreadable at " + url + " (" + (err && err.message) + ")");
    }
    return null;
  }

  /* Adopt a manifest: index its pose basenames and remember the served base.
   *
   * THE BASE — verified live this session: `/plugins/qubit-office/assets/...`
   * returns 200 for a PNG; the same path under `/dashboard-plugins/...` 404s
   * (90-CORRECTIONS C1). So the default is `/plugins/qubit-office/assets`, and
   * the manifest's own base field wins when present. A1's manifest spells it
   * `asset_base_url`; the doc's name for the same field is `assetBase`. Both
   * are honoured, then the caller's `fallbackBase`. */
  var DEFAULT_ASSET_BASE = "/plugins/qubit-office/assets";
  function adopt(m, fallbackBase) {
    if (!m || typeof m !== "object") return false;
    var files = m.files;
    if (!files || !files.length) {
      note("manifest: no files[] entries");
      return false;
    }
    manifest = m;
    var declared = m.assetBase || m.asset_base_url || m.assetBaseUrl;
    if (declared) assetBase = String(declared);
    else if (fallbackBase) assetBase = String(fallbackBase);
    else assetBase = DEFAULT_ASSET_BASE;
    /* A declared base that is only a bare path (e.g. "/plugins/qubit-office")
     * gets the conventional `assets` suffix so callers can pass either shape. */
    if (assetBase && !/\/assets\/?$/.test(assetBase)) {
      assetBase = assetBase.replace(/\/+$/, "") + "/assets";
    }
    /* Index by SET as well as by basename. A basename alone ("rot_south.png")
     * is shared by every family, so checking it globally would report a pose
     * as present when the REQUESTED set has no such file — and the draw would
     * then fetch a 404. Existence is a per-set question. */
    poses = {};
    sets = {};
    for (var i = 0; i < files.length; i++) {
      var p = files[i] && (files[i].path || files[i].file);
      if (!p) continue;
      p = String(p);
      var cut = p.lastIndexOf("/");
      var base = cut < 0 ? p : p.slice(cut + 1);
      poses[base] = p;
      /* the set is the first path segment after a "sprites/" directory */
      var parts = p.split("/");
      var si = parts.indexOf("sprites");
      var set = si >= 0 && parts.length > si + 1 ? parts[si + 1] : "";
      if (set) {
        if (!sets[set]) sets[set] = {};
        sets[set][base] = p;
      }
    }
    return true;
  }

  /* The public init. `base` is the served asset prefix: default
   * `/plugins/qubit-office/assets` (the VERIFIED PNG route). Idempotent: a
   * second call is a no-op. */
  function init(base) {
    if (manifest || manifestTried) return ready();
    manifestTried = true;
    var b = base || DEFAULT_ASSET_BASE;
    var m = loadManifest(manifestUrl(b));
    adopt(m, b);
    prime();
    return ready();
  }

  /* ----------------------------------------------------------------------
   * PRIME — start every pose decoding up front.
   *
   * WHY THIS EXISTS. `imageFor` sets `img.src` and returns immediately: an
   * `Image` has `naturalWidth === 0` until it decodes, and decoding is
   * asynchronous. Without priming, the FIRST time a pose is asked for it
   * reports `w: 0, h: 0`, `atlasDraw` refuses to blit a zero-size image, and
   * the renderer falls through to the procedural figure for that frame — so
   * the live page drew 10 dark procedural silhouettes and `info().loaded`
   * stayed at 1, which is exactly how the sprite layer "loaded" and still
   * looked like nothing had changed.
   *
   * Priming the whole manifest costs one request per pose (448 of them, all
   * small PNGs, all cacheable) and makes the first painted frame use real art.
   * It is bounded: no timers, no retries, and a failed image is already
   * recorded as a named gap by `imageFor`.
   * -------------------------------------------------------------------- */
  function prime() {
    if (!sets) return;
    for (var set in sets) {
      if (!Object.prototype.hasOwnProperty.call(sets, set)) continue;
      for (var name in sets[set]) {
        if (!Object.prototype.hasOwnProperty.call(sets[set], name)) continue;
        imageFor(set, name);
      }
    }
  }

  /* Adopt an already-parsed manifest. This is the seam a test (or a caller that
   * fetched the manifest itself) uses to skip the network entirely. */
  function useManifest(m, fallbackBase) {
    manifestTried = true;
    var out = adopt(m, fallbackBase);
    prime();
    return out;
  }

  function ready() { return !!(manifest && poses); }
  function info() {
    var nsets = 0, nposes = 0, k;
    if (sets) {
      for (k in sets) {
        if (Object.prototype.hasOwnProperty.call(sets, k)) { nsets++; nposes += Object.keys(sets[k]).length; }
      }
    }
    return {ready: ready(), base: assetBase, sets: nsets, poses: nposes,
            loaded: Object.keys(images).length, pending: pending, gaps: gaps.slice()};
  }
  function reset() {
    manifest = null; manifestTried = false; assetBase = ""; poses = null;
    images = {}; pads = {}; gaps = []; pending = 0;
  }

  /* ======================================================================
   * THE POSE FILE — a name and the URL it lives at
   *
   * From the inventory (C3): `rot_<dir>.png`, `walk_<dir>_<1..6>.png`,
   * `sit_<dir>_4.png`, `type_north_<1..4>.png`. `seed` picks a frame inside a
   * cycle; the frame is reduced modulo the real count so an out-of-range value
   * can never name a file that does not exist.
   * ====================================================================== */
  function poseName(family, dir, seed) {
    var fam = String(family || "rot");
    var d = String(dir || "south");
    if (fam === "rot") return "rot_" + d + ".png";
    if (fam === "sit") return "sit_" + d + "_4.png";
    if (fam === "type") return "type_north_" + frame1(seed, FAMILY_FRAMES.type) + ".png";
    if (fam === "walk") return "walk_" + d + "_" + frame1(seed, FAMILY_FRAMES.walk) + ".png";
    return "rot_" + d + ".png";
  }

  /* 1-based frame, wrapped into 1..count using the module's own modulo — the
   * same discipline the sprite cache uses, so a caller passing 7, -1 or 2.7
   * can never mint a name off the end of a cycle. */
  function frame1(seed, count) {
    var n = Math.round(Number(seed) || 0);
    var c = Number(count) || 1;
    var f = (n % c + c) % c;      /* 0..c-1 */
    return f + 1;
  }

  function urlFor(set, name) {
    var b = assetBase;
    if (b && b.charAt(b.length - 1) !== "/") b += "/";
    return b + "sprites/" + String(set) + "/" + String(name);
  }

  /* ======================================================================
   * THE IMAGE — load once per (set, pose), measure its foot pad once
   * ====================================================================== */
  function makeImage() {
    if (typeof Image === "function") { try { return new Image(); } catch (e) {} }
    /* A headless/`node` harness has no Image. A stub that keeps the same shape
     * lets every caller run and report "no pixels" honestly instead of
     * throwing — the same contract `measureFootPad` uses. */
    if (typeof document !== "undefined" && document.createElement) {
      return document.createElement("img");
    }
    return null;
  }

  function imageFor(set, name) {
    var key = String(set) + "/" + String(name);
    if (images[key]) return images[key];
    if (!poseInSet(set, name)) return null;   /* not in the manifest: no fetch */
    var img = makeImage();
    if (!img) return null;
    images[key] = img;
    pending++;
    var url = urlFor(set, name);
    img.onload = function () {
      pending = Math.max(0, pending - 1);
      var pad = measureFootPad(img);
      if (pad !== null) pads[key] = pad;
    };
    img.onerror = function () {
      pending = Math.max(0, pending - 1);
      delete images[key];
      note("image: " + key + " failed to load (" + url + ")");
    };
    try { img.src = url; } catch (err) { pending = Math.max(0, pending - 1); }
    return img;
  }

  /* The transparent rows under the first opaque pixel, read from the BOTTOM of
   * a pose. Returns null when the pixels cannot be read (no canvas, a lost
   * context, a tainted surface, an image with no data) — the caller then uses
   * the pose's own authored foot line. Ported from starnet
   * `frontend/agent-demo/sprites.js` measureFootPad(): same question, same
   * "unreadable is a safe fallback, never a throw" contract. */
  function measureFootPad(img) {
    try {
      var w = img.naturalWidth || img.width, h = img.naturalHeight || img.height;
      if (!w || !h) return null;
      var cv = (typeof document !== "undefined" && document.createElement)
        ? document.createElement("canvas") : null;
      if (!cv) return null;
      cv.width = w; cv.height = h;
      var ctx = cv.getContext && cv.getContext("2d", {willReadFrequently: true});
      if (!ctx || typeof ctx.getImageData !== "function" ||
          typeof ctx.drawImage !== "function") return null;
      ctx.drawImage(img, 0, 0);
      var data = ctx.getImageData(0, 0, w, h).data;
      for (var y = h - 1; y >= 0; y--) {
        var row = y * w * 4;
        for (var x = 0; x < w; x++) {
          if (data[row + x * 4 + 3] > 16) return (h - 1) - y;
        }
      }
      return null;   /* nothing opaque at all */
    } catch (err) {
      return null;   /* tainted / lost context -> caller falls back, never throws */
    }
  }

  /* ======================================================================
   * RESOLUTION — (state, facing, seed) -> a drawable pose, with fallbacks
   *
   * Returns:
   *   { family, dir, name, img, pad, w, h, ok, fallback }
   * `ok` is true when the EXACT pose the state asked for exists. `fallback` is
   * non-empty when a different pose answered, and says which rule fired so the
   * page can name it. Never throws, never returns null when any pose exists.
   * ====================================================================== */
  function familyFor(state) {
    return POSE_FAMILY[String(state)] || "rot";
  }

  /* Nearest direction in a family that the SET actually has, preferring the
   * requested one, then the four cardinals, then any pose of the family. */
  function nearestDir(set, family, want) {
    var dirs = FAMILY_DIRS[family] || DIRECTIONS_4;
    if (dirs.indexOf(want) >= 0 && poseExists(set, family, want, 1)) return want;
    var cardinals = ["south", "north", "east", "west"];
    for (var i = 0; i < cardinals.length; i++) {
      var d = cardinals[i];
      if (dirs.indexOf(d) >= 0 && poseExists(set, family, d, 1)) return d;
    }
    for (var j = 0; j < dirs.length; j++) {
      if (poseExists(set, family, dirs[j], 1)) return dirs[j];
    }
    return null;
  }

  /* Does THIS set carry a pose file? Per-set, not global — two sets share the
   * same basenames, so a global check would lie about the requested set. */
  function poseInSet(set, name) {
    return !!(sets && sets[set] && sets[set][name]);
  }

  function poseExists(set, family, dir, seed) {
    return poseInSet(String(set), poseName(family, dir, seed));
  }

  /* Is any set at all known? Used to decide "no atlas" vs "this set is empty". */
  function anySet() {
    if (!sets) return false;
    for (var k in sets) { if (Object.prototype.hasOwnProperty.call(sets, k)) return true; }
    return false;
  }

  /* The public resolve. Falls back in four ordered steps (see the header). */
  function resolve(set, state, facing, seed) {
    var fam = familyFor(state);
    var wantDir = FACING_TO_DIR[String(facing || "down")] || "south";
    var out = {family: fam, dir: wantDir, name: null, img: null, pad: null,
               w: 0, h: 0, ok: false, fallback: "", set: String(set)};

    if (!ready()) {
      out.fallback = "no-atlas";
      note("atlas: no manifest — the procedural figure is drawn instead");
      return out;
    }

    /* The requested set is not vendored. Name it and stand this frame down, so
     * the caller draws its own figure rather than fetching a 404. */
    if (!sets || !sets[set]) {
      out.fallback = "set-absent:" + set;
      note("set: " + set + " is not in the manifest — procedural figure used");
      return out;
    }

    var name = poseName(fam, wantDir, seed);

    /* 1. the exact pose. */
    if (poseInSet(String(set), name)) {
      out.name = name; out.ok = true;
    } else {
      /* 2. the same family at the nearest available direction. For `walk` at
       *    a missing facing this is a walk of another facing; for `type`
       *    (north-only) at east it is `type_north`, which is the honest
       *    fallback because the atlas simply does not have an eastern typist. */
      var alt = nearestDir(String(set), fam, wantDir);
      if (alt !== null) {
        out.name = poseName(fam, alt, seed);
        out.dir = alt;
        out.ok = out.name === name;
        out.fallback = "family-nearest-dir:" + alt;
        note("pose: " + set + "/" + fam + "/" + wantDir + " missing — drew " + out.name);
      } else {
        /* 3. the family does not exist in this set at all (e.g. a set with no
         *    `type_*`). Stand instead of drawing nothing, and say so. */
        var stand = nearestDir(String(set), "rot", wantDir);
        if (stand !== null) {
          out.family = "rot";
          out.dir = stand;
          out.name = poseName("rot", stand, 1);
          out.fallback = "family-missing:" + fam + "->rot";
          note("pose: family " + fam + " absent in " + set + " — drew " + out.name);
        } else {
          out.fallback = "set-empty";
          note("pose: set " + set + " has no drawable pose for " + fam + "/" + wantDir);
          return out;
        }
      }
    }

    return out;
  }

  /* ---- the real image attach, split out so the resolver above stays flat --- */
  function attach(out, set) {
    if (!out.name) return out;
    out.img = imageFor(set, out.name);
    if (out.img) {
      out.w = out.img.naturalWidth || out.img.width || 0;
      out.h = out.img.naturalHeight || out.img.height || 0;
      var key = String(set) + "/" + String(out.name);
      out.pad = (key in pads) ? pads[key] : null;
    }
    out.set = set;
    return out;
  }

  /* The one entry `sprites.js` uses. Resolve THEN attach, so the caller never
   * has to know about a two-step. */
  function pose(set, state, facing, seed) {
    return attach(resolve(set, state, facing, seed), set);
  }

  /* ======================================================================
   * the reported gaps — the page names what is missing rather than blanking
   * (50-ARCHITECTURE §5.4; 90-AGENT-CONTRACT §2.3)
   * ====================================================================== */
  function gapText() {
    if (!gaps.length) return "";
    return "sprite atlas: " + gaps.slice(0, 3).join("; ") +
           (gaps.length > 3 ? " (+" + (gaps.length - 3) + " more)" : "");
  }

  return {
    /* lifecycle */
    init: init, useManifest: useManifest, ready: ready, info: info, reset: reset,
    /* the frozen draw-facing entry */
    pose: pose, resolve: resolve, familyFor: familyFor, poseName: poseName,
    poseInSet: poseInSet, anySet: anySet,
    /* the vocabulary, exposed so a test can prove no state is unmapped */
    POSE_FAMILY: POSE_FAMILY, FAMILY_DIRS: FAMILY_DIRS, FAMILY_FRAMES: FAMILY_FRAMES,
    FACING_TO_DIR: FACING_TO_DIR,
    /* diagnostics */
    gaps: function () { return gaps.slice(); }, gapText: gapText,
    measureFootPad: measureFootPad, frame1: frame1
  };
});
