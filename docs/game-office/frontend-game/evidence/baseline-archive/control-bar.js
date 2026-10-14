/* Qubit Office — the control bar: four buttons, and the six features that are
 * REFUSED BY NAME (Qubit addition, dependency-free, no build step).
 *
 * WHAT THIS FILE IS
 * ---------------------------------------------------------------------------
 * `docs/game-office/28-HUD-PANELS.md` §4 specifies a control bar at the top of
 * the tower page. The reference project the owner compared us against ships a
 * bar with time controls, an authoring toolbar and lit panel toggles; exactly
 * FOUR of those verbs survive our contract, because the tower plugin is
 * read-only (C4) and renders only from events (C7):
 *
 *   reset  — put the camera back on the fitted pan
 *   fit    — refit the camera to the building
 *   zoom   — step the camera zoom within [MIN_ZOOM, MAX_ZOOM]
 *   pnl    — toggle the local P&L-chart visibility flag
 *
 * All four are PURE VIEW OPERATIONS. None of them is a request: there is no
 * `fetch` in this file at all, so C4 holds by construction and not by review.
 * That absence is load-bearing rather than incidental, which is why the
 * the four HTTP write verbs are not written anywhere below — not even inside a
 * comment. A text check that strips comments would still trip on them, and the
 * honest fix for that is for the file never to contain the strings, not for the
 * check to grow an exemption.
 *
 * THE OTHER HALF OF THE JOB: THE SIX REFUSALS
 * ---------------------------------------------------------------------------
 * §4 lists six bar features that must NOT be built here because each one
 * requires a WRITE (Publish, Edit, Delete, Hire/fire/move, Approve/deny,
 * Speed/pause). A design doc that says "do not add these" is worth exactly
 * nothing the day someone adds them, so the refusal is CODE here: `isReadOnly`
 * returns false for all six and `refusalReason` returns the sentence naming
 * WHY, so a future editor building one of them hits a test that fails with the
 * reason already written out. The refusals are not a comment to be skimmed;
 * they are an exported answer.
 *
 * There is deliberately no DEFAULT-ALLOW branch anywhere: `isReadOnly` and
 * `apply` both consult a closed table of the four ids, so an unknown id is
 * refused rather than silently permitted. A whitelist that fails open is how a
 * read-only surface stops being read-only.
 *
 * NO TIMER, BY CONSTRUCTION. This module contains no timer API of any kind —
 * the interval, the one-shot, the frame loop and the microtask variants alike.
 * It does not need one: it computes a new state from an old one and returns.
 * C7 leaves the poll as the tower's only timer, and a "control bar" is the
 * classic place a decorative animation tick gets added; the test asserts the
 * absence.
 *
 * PURITY. `apply(id, state)` never touches its argument. It builds a fresh
 * object every call and only ever reads from the input, which is what lets the
 * test assert deep-equality of the input before and after. A reducer that
 * mutates is one the caller cannot diff, undo, or trust.
 *
 * Loads as `window.QubitControlBar` in the browser and as `module.exports`
 * under node (`node -e`), the same dual shim camera.js/iso-engine.js/sprites.js
 * use. No DOM is touched here at all — `buildButtons()` is pure data so the
 * renderer owns every pixel.
 */
(function (root, factory) {
  if (typeof module === "object" && module && module.exports) {
    module.exports = factory();           /* the `node -e` test path */
  } else {
    root.QubitControlBar = factory();     /* window.QubitControlBar */
  }
})(typeof window !== "undefined" ? window : this, function () {
  "use strict";

  /* ------------------------------------------------------------- constants
   * ZOOM_STEP and the bounds are exported because the renderer needs the same
   * numbers the reducer uses: a wheel handler that steps by its own copy of
   * "1.15" would drift from this module the first time either changed.
   *
   * MIN_ZOOM is the legibility floor. `camera.js::CHAR_FLOOR_ZOOM` is 0.85 and
   * `qubit-office.js` uses the same value with its own MAX_ZOOM of 2.4; this
   * module keeps 2.4 for the ceiling and re-states the floor as MIN_ZOOM so the
   * control bar's bounds are self-describing. The reason for the floor is not
   * tidiness: a character is ~15 world units tall and below ~0.85 the figures
   * are specks, and an office whose people are invisible is not a drawing of an
   * office (28-HUD-PANELS.md §4).
   */
  var ZOOM_STEP = 1.25;
  var MIN_ZOOM = 0.85;
  var MAX_ZOOM = 2.4;

  /* --------------------------------------------------------- the four verbs
   * The closed table. `kind` is the semantic the reducer switches on and the
   * renderer keys its icons off; `title` is the tooltip and says what the
   * button does NOT do where that is the interesting part ("local view state"
   * on the P&L toggle is the whole reason it is allowed on a read-only page).
   */
  var BUTTONS = [
    {
      id: "reset",
      label: "Reset",
      title: "Reset the pan to the fitted camera (moves the view only)",
      kind: "reset"
    },
    {
      id: "fit",
      label: "Fit",
      title: "Fit the camera to the building (moves the view only)",
      kind: "fit"
    },
    {
      id: "zoom",
      label: "Zoom",
      title: "Step the zoom between " + MIN_ZOOM + "\u00d7 and " + MAX_ZOOM +
             "\u00d7 (local view state; nothing is fetched)",
      kind: "zoom"
    },
    {
      id: "pnl",
      label: "P&L",
      title: "Show or hide the profit-and-loss panel (local visibility only)",
      kind: "pnl"
    }
  ];

  /* ------------------------------------------------------- the six refusals
   * Each entry is a sentence that names WHY, not merely that the id is not in
   * the table. The distinction matters: "unknown button" tells a future editor
   * nothing, and they will assume an oversight and add it. The reasons below
   * are 28-HUD-PANELS.md §4's own column, compressed to one breathable line.
   *
   * `_feature` is kept only so the text can be re-derived and read back in a
   * test; callers get the sentence, not the record.
   */
  var REFUSED = {
    publish: {
      feature: "Publish",
      reason: "publishing is a mandatory review under D22; it commits state " +
              "and its only allowed home is dashboard_ops -> a 409 carrying a " +
              "confirmation_id -> the confirm path, never this read-only page"
    },
    edit: {
      feature: "Edit",
      reason: "every edit writes office_room / office_agent (see 75-EDITOR.md), " +
              "which is a direct violation of C4; the tower may not write org state"
    },
    delete: {
      feature: "Delete",
      reason: "deleting a room, seat or task is a destructive write and must " +
              "run through the confirmation journal rather than a direct call"
    },
    hire: {
      feature: "Hire / fire / move seat",
      reason: "hiring, firing or moving a seat writes org state, which C4 " +
              "forbids here; the dashboard treats these as STAGED ops, i.e. " +
              "deliberately not this plugin's"
    },
    approve: {
      feature: "Approve / deny",
      reason: "approving or denying a staged review or escalation settles a " +
              "confirmation_id, which is a write on the confirmation path"
    },
    speed: {
      feature: "Speed / pause",
      reason: "there is no client-side clock to affect: every figure is " +
              "computed server-side against now and the payload is polled, so " +
              "faking a pause would either freeze the page against reality or " +
              "lie about it (and a real org pause is a server-side write)"
    }
  };

  /* --------------------------------------------------------------- helpers */

  function _num(v, d) {
    /* Strict coercion. `Number(null)` is 0 and `Number("")` is 0, both finite,
     * so a plain Number()+isFinite test launders a missing field into a real
     * coordinate at the origin. A null must be treated as MISSING. */
    if (typeof v === "number") return isFinite(v) ? v : d;
    if (typeof v === "string") {
      var s = v.trim();
      if (!s) return d;
      var n = Number(s);
      return isFinite(n) ? n : d;
    }
    return d;
  }

  /* clampZoom(z) -> number in [MIN_ZOOM, MAX_ZOOM].
   *
   * Applied at both ends: the floor keeps characters legible, the ceiling keeps
   * the building recognisable as architecture rather than one giant tile. A
   * non-numeric zoom is coerced to the floor rather than left as NaN — a NaN
   * camera makes the whole canvas blank, and a blank canvas is a worse failure
   * than a slightly wrong one, because the user cannot tell it apart from
   * "loading". */
  function clampZoom(z) {
    var v = _num(z, MIN_ZOOM);
    if (v < MIN_ZOOM) return MIN_ZOOM;
    if (v > MAX_ZOOM) return MAX_ZOOM;
    return v;
  }

  /* ------------------------------------------------------------ buildButtons
   * Pure data. No DOM, no React, no hooks: the renderer is free to draw these
   * as <button>, <a> or canvas rects, and the test can assert the four without
   * a browser. A fresh object is returned every call so a caller that mutates
   * a label (a badge, a count) cannot reach back into this table.
   */
  function buildButtons() {
    var out = [];
    for (var i = 0; i < BUTTONS.length; i++) {
      var b = BUTTONS[i];
      out.push({id: b.id, label: b.label, title: b.title, kind: b.kind});
    }
    return out;
  }

  function _button(id) {
    for (var i = 0; i < BUTTONS.length; i++) {
      if (BUTTONS[i].id === id) return BUTTONS[i];
    }
    return null;
  }

  function _refusal(id) {
    return Object.prototype.hasOwnProperty.call(REFUSED, id)
      ? REFUSED[id].reason : null;
  }

  /* ------------------------------------------------------------- the reducer
   * apply(id, state) -> state'
   *
   * `state` is the renderer's camera+view slice:
   *
   *   {cam: {cx, cy, zoom},          the camera; cx/cy are the FITTED pan
   *    floor: <number|"all">,        the floor filter the bar shares with P2
   *    showChart: <boolean>}         P9's visibility
   *
   * plus an optional `fit` field: the camera the last fit produced, i.e. the
   * canonical framed view. `camera.js::fit()` returns exactly
   * `{cx, cy, zoom}`, so the renderer passes it straight through.
   *
   * The four transitions, and the reasoning that makes each one honest:
   *
   *   reset — restore the FITTED PAN. NOT (0,0). §4's table says "sets cx/cy
   *           back to (0,0) then clampPan", because the awaited renderer keeps
   *           its world origin at the building centre and so its fitted pan IS
   *           (0,0). This module is written against the state contract the task
   *           fixed, where cx/cy are world coordinates and a fitted camera
   *           carries a real centre; the equivalent, origin-independent
   *           statement is "go back to the framed view". So reset restores the
   *           fitted pan AND the fitted zoom, and leaves `floor` and
   *           `showChart` alone: neither is a camera property, so a reset that
   *           changed them would be doing something the button does not claim.
   *           When the caller supplies no `fit`, the pan falls back to the
   *           origin (the fitted pan of a centred world) and the zoom is left
   *           as it is — inventing a zoom we were not given would be the
   *           synthesise-a-value failure this whole surface exists to avoid.
   *
   *   fit  — refit. Same target as reset, but it ALSO writes the fitted camera
   *           into `fit`, which is what makes reset idempotent afterwards: fit
   *           is the operation that recomputes the canonical view, reset is the
   *           one that returns to it. With no `fit` supplied the fit is a no-op
   *           EXCEPT that it clamps the existing zoom — a button that cannot
   *           compute a frame must not invent one, and clamping is the whole of
   *           what is left once framing is impossible.
   *
   *   zoom — step UP and clamp. This is a bare step, not a direction, which is
   *           noted here rather than papered over: §4 calls for Zoom - / +, and
   *           a single "zoom" id cannot express which way. The direction is
   *           meant to arrive as an argument, and the renderer may equally call
   *           `clampZoom(cam.zoom / ZOOM_STEP)` directly with the exported
   *           constant for the minus side. `buildButtons` still lists ONE zoom
   *           button because it accepts no arguments: a pair of entries saying
   *           "zoom in"/"zoom out" that both reduce to the identical step would
   *           be two buttons for one transition, which is worse than a
   *           documented limitation. (Reported as the one part of §4 not fully
   *           expressible through this three-argument signature.)
   *
   *   pnl  — flip `showChart`. A local visibility toggle; it reads
   *           `history[]`/`history_totals.net_cents` for the chart's content and
   *           writes nothing anywhere.
   *
   * Purity: every branch returns a NEW object built from the input's own
   * fields, and the input is never assigned to. `cam` is likewise a new object,
   * so a caller holding a reference to the old state sees it unchanged.
   */
  function apply(id, state) {
    var s = state || {};
    var b = _button(id);

    /* The refusal path is first and unconditional. `apply("publish", s)` is
     * the shape a future editor writes while wiring the button they added; it
     * must not silently reduce to something that looks like it worked. Throw
     * with the reason, so the failure names the rule instead of the typo. */
    if (!b) {
      var why = _refusal(id);
      if (why) throw new Error("QubitControlBar: refusing \"" + id + "\" — " + why);
      throw new Error("QubitControlBar: unknown control id \"" + id + "\"");
    }

    var cam = s.cam || {};
    var fit = s.fit || null;

    if (b.kind === "reset") {
      var pan = fit || {};
      var resetZoom = fit ? clampZoom(_num(fit.zoom, _num(cam.zoom, MIN_ZOOM)))
                          : clampZoom(_num(cam.zoom, MIN_ZOOM));
      return {
        cam: {
          cx: _num(pan.cx, 0),
          cy: _num(pan.cy, 0),
          zoom: resetZoom
        },
        floor: s.floor,
        showChart: !!s.showChart,
        fit: fit
      };
    }

    if (b.kind === "fit") {
      if (fit) {
        return {
          cam: {
            cx: _num(fit.cx, 0),
            cy: _num(fit.cy, 0),
            zoom: clampZoom(_num(fit.zoom, MIN_ZOOM))
          },
          floor: s.floor,
          showChart: !!s.showChart,
          fit: fit
        };
      }
      return {
        cam: {
          cx: _num(cam.cx, 0),
          cy: _num(cam.cy, 0),
          zoom: clampZoom(_num(cam.zoom, MIN_ZOOM))
        },
        floor: s.floor,
        showChart: !!s.showChart,
        fit: null
      };
    }

    if (b.kind === "zoom") {
      return {
        cam: {
          cx: _num(cam.cx, 0),
          cy: _num(cam.cy, 0),
          zoom: clampZoom(_num(cam.zoom, MIN_ZOOM) * ZOOM_STEP)
        },
        floor: s.floor,
        showChart: !!s.showChart,
        fit: fit
      };
    }

    /* pnl — the only non-camera verb, so it is the only one that leaves `cam`
     * untouched (a fresh object, but the same numbers). */
    return {
      cam: {cx: _num(cam.cx, 0), cy: _num(cam.cy, 0),
            zoom: clampZoom(_num(cam.zoom, MIN_ZOOM))},
      floor: s.floor,
      showChart: !s.showChart,
      fit: fit
    };
  }

  /* --------------------------------------------------------- read-only shape
   * isReadOnly(id) is true ONLY for the four ids in the closed table, and false
   * for the six refused features as well as for anything unknown. There is no
   * catch-all `return true`: the read-only promise is only worth something if
   * it fails CLOSED, and a plugin whose bar is one typo away from permitting an
   * unlisted verb is not read-only.
   */
  function isReadOnly(id) {
    return _button(id) !== null;
  }

  /* refusalReason(id) -> string | null
   *
   * The sentence that names WHY a refused feature cannot live on this bar, or
   * null for a supported id (which has nothing to refuse). The renderer can put
   * this in a disabled button's tooltip, and the test asserts every one of the
   * six produces a non-empty string — so the reason travels with the code
   * rather than rotting in a document nobody re-reads.
   */
  function refusalReason(id) {
    if (_button(id)) return null;
    return _refusal(id);
  }

  return {
    ZOOM_STEP: ZOOM_STEP,
    MIN_ZOOM: MIN_ZOOM,
    MAX_ZOOM: MAX_ZOOM,
    clampZoom: clampZoom,
    buildButtons: buildButtons,
    apply: apply,
    isReadOnly: isReadOnly,
    refusalReason: refusalReason,
    /* READ_ONLY / REFUSED are exposed for the renderer's own introspection
     * (a "why can't I publish from here?" affordance) and for tests that want
     * to enumerate the contract without a second copy of the list. They are
     * frozen-by-convention: mutate them and you have broken the contract, not
     * configured it. */
    READ_ONLY_IDS: ["reset", "fit", "zoom", "pnl"],
    REFUSED_IDS: ["publish", "edit", "delete", "hire", "approve", "speed"]
  };
});
