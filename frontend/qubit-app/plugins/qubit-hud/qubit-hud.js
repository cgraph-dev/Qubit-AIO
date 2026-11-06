/* Qubit HUD — observability tabs, overlay plugin bundle (Qubit addition,
 * dependency-free IIFE). Uses only window.__QUBIT_PLUGIN_SDK__
 * (React, hooks, fetchJSON, utils).
 *
 * PROVENANCE — READ THIS BEFORE COMPARING THIS FILE TO ANYTHING UPSTREAM.
 * ---------------------------------------------------------------------------
 * The tab set and the panel LAYOUTS are re-expressed from the MIT-licensed
 * reference observability dashboard (hermes-hudui), whose panels are:
 *   DashboardPanel / HealthPanel / SessionsPanel / CronPanel / SkillsPanel /
 *   PluginsPanel / TokenCostsPanel / ModelInfoPanel / TopBar / useTheme.
 * NO TSX WAS PORTED AND NO UPSTREAM FILE WAS COPIED. Those panels read a
 * reference backend's own endpoints with reference field names
 * (`hermes_cli_status`, `diagnostics_ok`, `total_tool_calls`, …) and several of
 * them are not read-only. None of that survives here: every tab below reads one
 * of QUBIT's routes, parses the payload QUBIT actually serves, and shows
 * QUBIT's field names. Where the reference panel had a mutation control
 * (pause/resume/trigger a cron job, install/rescan a plugin, run a health
 * action) this page renders NOTHING — it is a viewer (see READ-ONLY below).
 * ---------------------------------------------------------------------------
 *
 * WHAT THIS PAGE IS
 * -----------------
 * Seven read-only tabs over routes that already exist in this build:
 *
 *   Health    GET /api/health, /api/health/idle
 *   Sessions  GET /api/sessions, /api/sessions/stats, /api/sessions/search,
 *                 /api/sessions/{id}/messages, /api/sessions/{id}/timeline
 *   Cron      GET /api/cron/jobs, /api/cron/jobs/{id}, /api/cron/jobs/{id}/runs
 *   Models    GET /api/model/options   (plus /api/model/info)
 *   Skills    GET /api/skills
 *   Plugins   GET /api/dashboard/plugins, /api/dashboard/plugins/catalog
 *   Costs     GET /api/analytics, /api/sessions/stats
 *
 * The route strings are literal and unrebased (`/api/...`) because the host's
 * `fetchJSON` adds the SPA's API prefix and the session auth itself. This file
 * never reads a token, never assembles an auth header, and never puts one in a
 * URL.
 *
 * MODELS — A NOTE ON `/api/models`, WHICH THIS BUILD DOES NOT SERVE
 * -----------------------------------------------------------------
 * The tab was specified against `GET /api/models`. That route does not exist in
 * this tree: `grep -rn 'api/models' voicebridge/` finds no GET handler, and a
 * live probe against the running bridge answers 404 with the compat envelope
 * `{"ok": false, "code": "NOT_FOUND"}`. Inventing a page over a 404 would be
 * exactly the fabricated integration this repo forbids, so the tab reads the
 * real model authorities that DO exist:
 *   - `/api/model/options` — the provider/model catalogue the dashboard picker
 *     serves (`providers[]` with `models[]`, `is_current`, `authenticated`);
 *   - `/api/model/info`    — the resolved default's capabilities and identity.
 * If a `/api/models` route is added later, `MODELS_PRIMARY` is the one line to
 * change. Until then the page states the substitution ON SCREEN, so a reader is
 * never told a number came from an endpoint it did not come from.
 *
 * THE THREE RULES THIS FILE OBEYS
 * -------------------------------
 * 1. IT WRITES NOTHING (READ-ONLY). No POST, PUT, PATCH or DELETE exists in
 *    this file, and no control that would imply one is rendered. Cron jobs and
 *    plugins are shown with their real state; the page does not offer to change
 *    it. Every mutation in this product is a staged op through the shared
 *    confirmation journal, and an observability view is not the place to make
 *    the first exception.
 *
 * 2. A FAILURE IS NAMED, NOT IMPROVISED. Every read goes through `useRead`,
 *    which keeps `loading | ok | error` per endpoint. A non-2xx response, a
 *    non-JSON body, or a payload of an unexpected SHAPE renders an explicit
 *    state that names the exact route and the mode of failure. A 404 says the
 *    route is missing (and names it); a 401 says the session token is stale; a
 *    network error says the backend is unreachable. Nothing is substituted, no
 *    zero is shown in place of an unknown, and no spinner is left turning after
 *    the read has failed.
 *
 * 3. PAYLOAD STRINGS ARE TEXT. Every backend string — session titles, job
 *    names, skill descriptions, provider names, error details — is passed to
 *    `React.createElement` as a CHILD. There is no innerHTML, no
 *    dangerouslySetInnerHTML, no `insertAdjacentHTML`, and no URL built from
 *    payload data that is then fetched.
 *
 * POLLING
 * -------
 * Each tab owns its own timer and passes a refresh interval to `useRead`. The
 * floor is enforced in code (`POLL_FLOOR_MS`): a caller asking for anything
 * faster is clamped, so a future edit cannot turn this page into a busy-loop
 * against the bridge. A tab that is not selected is UNMOUNTED, so the hidden
 * tabs issue no requests at all.
 *
 * BRAND
 * -----
 * Every visible string is Qubit's. The one place upstream vocabulary can still
 * reach the screen is the plugin catalog's `provenance` blob (which records
 * where a third-party plugin came from); this page renders catalog LABELS and
 * counts only and never prints that blob, so no legacy name reaches the UI.
 */
(function () {
  var SDK = window.__QUBIT_PLUGIN_SDK__;
  if (!SDK || !window.__QUBIT_PLUGINS__) return;
  var React = SDK.React, hooks = SDK.hooks, fetchJSON = SDK.fetchJSON;
  var e = React.createElement;
  var useState = hooks.useState, useEffect = hooks.useEffect;
  var useCallback = hooks.useCallback, useMemo = hooks.useMemo;
  var useRef = hooks.useRef;
  var UTILS = SDK.utils || {};

  /* ======================================================================
   * 1. ROUTES — the exact strings this page reads, each in one place.
   * ==================================================================== */
  var P = {
    health: "/api/health",
    healthIdle: "/api/health/idle",
    sessions: "/api/sessions",
    sessionsStats: "/api/sessions/stats",
    sessionsSearch: "/api/sessions/search",
    cronJobs: "/api/cron/jobs",
    modelOptions: "/api/model/options",
    modelInfo: "/api/model/info",
    skills: "/api/skills",
    plugins: "/api/dashboard/plugins",
    pluginsCatalog: "/api/dashboard/plugins/catalog",
    analytics: "/api/analytics"
  };

  /* The route the Models tab was specified against. It is NOT served by this
   * build (see the header). Kept as a named constant so the substitution is
   * visible in code and on screen, and so adding the route later is one edit. */
  var MODELS_SPECIFIED_ROUTE = "/api/models";

  /* Chat transcripts are paginated: a session with 3000 messages must not be
   * pulled whole into the browser to show its first screen. */
  var MESSAGES_PAGE = 100;
  /* Timeline rows are prompt-metadata index entries, cheap, still bounded. */
  var TIMELINE_LIMIT = 100;
  /* Session list page size. The route clamps at 100. */
  var SESSIONS_PAGE = 50;
  /* Cron run history per job. The route clamps at 100. */
  var CRON_RUNS = 10;

  /* ======================================================================
   * 2. POLLING — one floor, enforced here, not trusted from callers.
   * ==================================================================== */
  var POLL_FLOOR_MS = 2000;
  var POLL = {
    health: 5000,
    sessions: 10000,
    cron: 10000,
    models: 30000,
    skills: 30000,
    plugins: 30000,
    costs: 30000
  };

  function clampPoll(ms) {
    var n = Number(ms) || 0;
    return n < POLL_FLOOR_MS ? POLL_FLOOR_MS : n;
  }

  /* ======================================================================
   * 3. FORMATTERS — total, and honest about unknowns.
   *
   * A missing value formats as the em dash, never as 0 and never as "N/A"
   * dressed up as data. `isNum` is the gate every numeric cell uses.
   * ==================================================================== */
  var DASH = "\u2014";

  function isNum(v) {
    return typeof v === "number" && isFinite(v);
  }

  function num(v) {
    return isNum(v) ? v.toLocaleString() : DASH;
  }

  /* Compact token counts: 1.2B / 45.3M / 12.1k. Same reader intuition as the
   * reference, but it degrades to the em dash rather than to a fake 0. */
  function tokens(v) {
    if (!isNum(v)) return DASH;
    var n = Math.abs(v);
    if (n >= 1e9) return (v / 1e9).toFixed(2) + "B";
    if (n >= 1e6) return (v / 1e6).toFixed(2) + "M";
    if (n >= 1e3) return (v / 1e3).toFixed(1) + "k";
    return String(v);
  }

  function money(v) {
    if (!isNum(v)) return DASH;
    if (v === 0) return "$0.00";
    if (v >= 1) return "$" + v.toFixed(2);
    return "$" + v.toFixed(4);
  }

  /* Epoch seconds *or* milliseconds, whichever the value plausibly is. The
   * session routes mix both (chat rows carry float seconds, receipts carry
   * seconds too) so the unit is normalised rather than assumed. */
  function toMs(value) {
    var n = Number(value);
    if (!isFinite(n) || n <= 0) return 0;
    return n > 1e12 ? n : n * 1000;
  }

  function fmtTime(value) {
    var ms = toMs(value);
    if (!ms) return DASH;
    try {
      return new Date(ms).toLocaleString();
    } catch (ex) {
      return DASH;
    }
  }

  function fmtAgo(value) {
    var ms = toMs(value);
    if (!ms) return DASH;
    var diff = Date.now() - ms;
    if (diff < 0) diff = 0;             /* a future stamp is "just now", not
                                         * a negative age */
    var s = Math.floor(diff / 1000);
    if (s < 45) return "just now";
    var m = Math.floor(s / 60);
    if (m < 60) return m + "m ago";
    var h = Math.floor(m / 60);
    if (h < 24) return h + "h ago";
    var d = Math.floor(h / 24);
    if (d < 30) return d + "d ago";
    return fmtTime(value);
  }

  /* ISO-8601 strings (cron stamps) go through the same age formatter. */
  function fmtAgoIso(iso) {
    if (!iso) return DASH;
    var ms = Date.parse(String(iso));
    if (!isFinite(ms)) return DASH;
    return fmtAgo(ms / 1000);
  }

  function fmtDuration(startIso, endIso) {
    var a = Date.parse(String(startIso || ""));
    var b = Date.parse(String(endIso || ""));
    if (!isFinite(a) || !isFinite(b) || b < a) return DASH;
    var s = (b - a) / 1000;
    if (s < 1) return Math.round(s * 1000) + "ms";
    if (s < 60) return s.toFixed(1) + "s";
    var m = Math.floor(s / 60);
    return m + "m " + Math.round(s - m * 60) + "s";
  }

  function text(v) {
    if (v === null || v === undefined || v === "") return DASH;
    return String(v);
  }

  function str(v) {
    return v === null || v === undefined ? "" : String(v);
  }

  /* A payload string is safe as a React child by construction, but a VERY long
   * one (a transcript line) is bounded so one record cannot dominate a page.
   * The full value stays in the title attribute, which is a string attribute,
   * never markup. */
  function clip(v, max) {
    var s = str(v);
    var n = max || 240;
    if (s.length <= n) return s;
    return s.slice(0, n) + "\u2026";
  }

  function keysOf(obj) {
    if (!obj || typeof obj !== "object") return [];
    return Object.keys(obj);
  }

  /* ======================================================================
   * 3a. DISPLAY-NAME SANITIZER — zero legacy brand ON SCREEN.
   *
   * Some payloads carry an upstream product name inside a display field: the
   * model catalogue ships a provider whose `name` is another product's brand
   * and whose `source` is its internal id, and a couple of bundled skills are
   * still named after the runtime they shipped in. Those are VALUES from our
   * own config and catalogs, not copy this file authored — but a reader of the
   * HUD should never see another product's name on a Qubit surface
   * (docs/BRAND.md). This is a DISPLAY transform applied to names and one-line
   * descriptions only. Ids, slugs and paths are left byte-exact, because they
   * are what an operator types into a CLI and must round-trip.
   *
   * Nothing here invents a new label: the substitutions are neutral words for
   * the thing the record actually is ("upstream provider", "upstream runtime",
   * "upstream"), so no claim about the product is fabricated either.
   * ==================================================================== */
  var BRAND_SEXPR = [
    /* longest first: the compound product phrases before their bare tokens */
    [/nous research/gi, "upstream"],
    [/nous portal/gi, "upstream provider"],
    [/nous-?research/gi, "upstream"],
    [/hermes-?agent/gi, "upstream agent"],
    [/hermes[\s_-]?cli/gi, "upstream CLI"],
    [/hermes[\s_-]?desktop/gi, "upstream desktop"],
    [/hermes-?hudui/gi, "upstream HUD"],
    [/nous/gi, "upstream"],
    [/hermes/gi, "upstream"]
  ];

  function scrub(v) {
    var s = str(v);
    if (!s) return s;
    for (var i = 0; i < BRAND_SEXPR.length; i++) {
      s = s.replace(BRAND_SEXPR[i][0], BRAND_SEXPR[i][1]);
    }
    return s;
  }

  /* Display form with the sanitizer applied and empties folded to the dash. */
  function label(v) {
    if (v === null || v === undefined || v === "") return DASH;
    var s = scrub(v);
    return s || DASH;
  }

  function isObj(v) {
    return !!v && typeof v === "object" && !Array.isArray(v);
  }

  /* Sort key for a {name: count} map, largest first, name as the tiebreak so
   * the order is stable between renders. */
  function countRows(map) {
    var out = [];
    var ks = keysOf(map);
    for (var i = 0; i < ks.length; i++) {
      var v = map[ks[i]];
      if (typeof v === "number") out.push([ks[i], v]);
    }
    out.sort(function (a, b) {
      return b[1] - a[1] || String(a[0]).localeCompare(String(b[0]));
    });
    return out;
  }

  /* ======================================================================
   * 4. THE READ HOOK — the only way this file talks to the backend.
   *
   * States:  loading (first attempt only) | ok | error
   * A `null` deps array means "do not fetch yet" — used where a request needs
   * an id the reader has not picked.
   *
   * Failure is classified, not flattened:
   *   "missing"  404 (or a route the server does not know) -> names the route
   *   "auth"     401/403 -> stale session token, reload advice
   *   "network"  fetch never reached the server -> "backend unreachable"
   *   "http"     any other non-2xx
   *   "shape"    200 but not the payload this panel can render
   * ==================================================================== */
  function classifyError(ex) {
    var msg = str((ex && ex.message) || ex);
    var m = /^(\d{3})\s*:?\s*([\s\S]*)$/.exec(msg);
    var status = m ? parseInt(m[1], 10) : 0;
    var body = m ? m[2] : "";
    if (!status) {
      /* fetchJSON does not wrap a transport failure: the browser's TypeError
       * arrives as-is. Treat that as unreachable, which is what it is. */
      if (/failed to fetch|networkerror|load failed|fetch failed/i.test(msg) ||
          (ex && ex.name === "TypeError")) {
        return { kind: "network", status: 0, detail: msg };
      }
      return { kind: "http", status: 0, detail: msg || "request failed" };
    }
    if (status === 404) return { kind: "missing", status: status, detail: body };
    if (status === 401 || status === 403) {
      return { kind: "auth", status: status, detail: body };
    }
    return { kind: "http", status: status, detail: body || msg };
  }

  function useRead(route, deps, pollMs) {
    var d = deps || [];
    var depKey = d.length ? JSON.stringify(d) : "";
    var enabled = depKey !== "null";
    var _s = useState({ status: enabled ? "loading" : "idle", data: null,
                        error: null, at: 0, route: route });
    var state = _s[0], setState = _s[1];
    var alive = useRef(true);

    var run = useCallback(function () {
      if (!enabled) return;
      fetchJSON(route).then(function (data) {
        if (!alive.current) return;
        setState({ status: "ok", data: data, error: null, at: Date.now(),
                   route: route });
      }, function (ex) {
        if (!alive.current) return;
        setState({ status: "error", data: null,
                   error: classifyError(ex), at: Date.now(), route: route });
      });
      /* Deps are the identity of this read; `route` and `enabled` are read
       * from the closure of the current render, which is the one the effect
       * ran in. */
    }, [route, enabled, depKey]);

    useEffect(function () {
      alive.current = true;
      return function () { alive.current = false; };
    }, []);

    /* Reset to loading when the identity of the read changes (a new session
     * id, say), so a stale payload is never shown against a new id. */
    useEffect(function () {
      if (!enabled) {
        setState({ status: "idle", data: null, error: null, at: 0, route: route });
        return;
      }
      setState({ status: "loading", data: null, error: null, at: 0, route: route });
      run();
    }, [route, enabled, depKey, run]);

    useEffect(function () {
      if (!enabled || !pollMs) return undefined;
      var ms = clampPoll(pollMs);
      var id = setInterval(run, ms);
      return function () { clearInterval(id); };
    }, [run, enabled, pollMs]);

    state.reload = run;
    return state;
  }

  /* ======================================================================
   * 5. PRIMITIVES
   * ==================================================================== */

  function Panel(props) {
    var kids = props.children;
    var cls = "hud__panel" + (props.wide ? " hud__panel--wide" : "");
    return e("section", { className: cls, "aria-label": props.title || null },
      e("div", { className: "hud__panelHead" },
        e("h2", { className: "hud__panelTitle" }, props.title),
        props.hint ? e("span", { className: "hud__panelHint" }, props.hint) : null
      ),
      kids
    );
  }

  /* A stat tile. `tone` is "ok" | "warn" | "bad" | "accent" | "" — colour is
   * used for STATE only, never to decorate a number. */
  function Stat(props) {
    var tone = props.tone ? " hud__statValue--" + props.tone : "";
    return e("div", { className: "hud__stat" },
      e("div", { className: "hud__statLabel" }, props.label),
      e("div", { className: "hud__statValue" + tone }, props.value)
    );
  }

  function Chip(props) {
    return e("span", { className: "hud-chip" + (props.tone ? " hud-chip--" + props.tone : "") },
      props.children);
  }

  function StatGrid(props) {
    return e("div", { className: "hud__stats" }, props.children);
  }

  function KV(props) {
    var rows = props.rows || [];
    var out = [];
    for (var i = 0; i < rows.length; i++) {
      var r = rows[i];
      if (!r) continue;
      out.push(e("dt", { key: "t" + i }, r[0]));
      out.push(e("dd", { key: "d" + i }, r[1] === null || r[1] === undefined
        ? DASH : r[1]));
    }
    return e("dl", { className: "hud__kv" }, out);
  }

  /* A labelled bar. `max` of 0 or a non-number draws an EMPTY track rather than
   * a full one — an unknown denominator must not render as 100%. */
  function Bar(props) {
    var value = isNum(props.value) ? props.value : 0;
    var max = isNum(props.max) ? props.max : 0;
    var pct = max > 0 ? Math.max(0, Math.min(100, (value / max) * 100)) : 0;
    var fillCls = "hud-bar__fill" + (props.ok ? " hud-bar__fill--ok" : "");
    return e("div", { className: "hud-bar" },
      e("span", { className: "hud-bar__label", title: str(props.label) }, props.label),
      e("div", {
        className: "hud-bar__track",
        role: "img",
        "aria-label": str(props.label) + ": " + str(props.value)
      }, e("div", { className: fillCls, style: { width: pct.toFixed(1) + "%" } })),
      e("span", { className: "hud-bar__value" }, props.display !== undefined
        ? props.display : num(props.value))
    );
  }

  /* THE EMPTY STATE. `route` is always named: a reader must be able to tell
   * which endpoint produced nothing. `kind` picks the border tone:
   *   empty   — the route answered, the collection is genuinely empty
   *   offline — the backend could not be reached
   *   error   — the route answered with a failure or an unusable shape
   */
  function StateBox(props) {
    var kind = props.kind || "empty";
    var cls = "hud-state hud-state--" + kind;
    return e("div", { className: cls, role: kind === "empty" ? "status" : "alert" },
      e("div", { className: "hud-state__title" }, props.title),
      props.route ? e("div", { className: "hud-state__endpoint" },
        "GET " + props.route) : null,
      props.detail ? e("div", { className: "hud-state__detail" }, props.detail) : null,
      props.action && props.reload
        ? e("button", {
            type: "button",
            className: "hud-btn hud-state__action",
            onClick: props.reload
          }, "Retry")
        : null
    );
  }

  /* The one place a read failure becomes prose. Every tab uses it, so a 404, a
   * stale token and an unreachable bridge read consistently wherever they
   * happen. `what` names the panel ("Cron jobs"), `route` names the endpoint. */
  function ReadFailure(props) {
    var err = props.error || {};
    var route = props.route;
    var what = props.what || "this panel";
    if (err.kind === "network") {
      return e(StateBox, {
        kind: "offline",
        title: "Qubit backend unreachable",
        route: route,
        detail: "The request for " + what + " never reached the bridge. " +
          "The dashboard is open but the process behind it is not answering. " +
          "No data is shown because none was received.",
        reload: props.reload,
        action: true
      });
    }
    if (err.kind === "auth") {
      return e(StateBox, {
        kind: "error",
        title: "Not authenticated (" + err.status + ")",
        route: route,
        detail: "The session token this page was rendered with is no longer " +
          "accepted — the bridge most likely restarted. Reload the dashboard " +
          "and open this tab again.",
        reload: props.reload,
        action: true
      });
    }
    if (err.kind === "missing") {
      return e(StateBox, {
        kind: "error",
        title: "Route not served by this build",
        route: route,
        detail: what + " needs the route above, and this Qubit build answered " +
          "404 for it. Nothing is shown in its place: the panel is empty " +
          "because the endpoint is, not because the data is zero." +
          (err.detail ? " Server said: " + clip(err.detail, 200) : ""),
        reload: props.reload,
        action: true
      });
    }
    if (err.kind === "shape") {
      return e(StateBox, {
        kind: "error",
        title: "Unexpected response shape",
        route: route,
        detail: (props.shapeDetail || (what + " could not read the payload")) +
          ". The endpoint answered, but not with the fields this panel renders, " +
          "so no value is guessed from it." +
          (err.detail ? " Observed: " + clip(err.detail, 200) : ""),
        reload: props.reload,
        action: true
      });
    }
    return e(StateBox, {
      kind: "error",
      title: "Request failed" + (err.status ? " (" + err.status + ")" : ""),
      route: route,
      detail: clip(err.detail || "no detail returned", 300),
      reload: props.reload,
      action: true
    });
  }

  /* The loading state. It says what is loading and from where, and it stops
   * existing the moment the read settles — there is no perpetual spinner. */
  function Loading(props) {
    return e("div", { className: "hud-state", role: "status" },
      e("div", { className: "hud-state__title" }, "Reading " + props.what),
      props.route ? e("div", { className: "hud-state__endpoint" },
        "GET " + props.route) : null
    );
  }

  /* Wrap a read in the three honest states. `render` is only ever called with
   * a payload of the expected shape; `expect` returns an error string when the
   * shape is wrong (so a shape failure names the route like any other). */
  function Ready(props) {
    var st = props.state;
    if (st.status === "idle") return null;
    if (st.status === "loading") {
      return e(Loading, { what: props.what, route: st.route });
    }
    if (st.status === "error") {
      return e(ReadFailure, {
        what: props.what, route: st.route, error: st.error, reload: st.reload
      });
    }
    var shapeError = props.expect ? props.expect(st.data) : null;
    if (shapeError) {
      return e(ReadFailure, {
        what: props.what, route: st.route, reload: st.reload,
        error: { kind: "shape", detail: shapeError, status: 200 },
        shapeDetail: shapeError
      });
    }
    return props.render(st.data, st);
  }

  function Table(props) {
    var head = [];
    for (var i = 0; i < props.cols.length; i++) {
      head.push(e("th", { key: "h" + i, scope: "col",
                          className: props.cols[i].num ? "hud__num" : null },
        props.cols[i].label));
    }
    var body = [];
    for (var r = 0; r < props.rows.length; r++) {
      var cells = [];
      for (var c = 0; c < props.cols.length; c++) {
        var col = props.cols[c];
        var cell = props.cols[c].render
          ? props.cols[c].render(props.rows[r], r)
          : props.rows[r][props.cols[c].key];
        cells.push(e("td", {
          key: "c" + c,
          className: (col.num ? "hud__num" : null) + (col.trunc ? " hud__trunc" : "")
        }, cell === null || cell === undefined ? DASH : cell));
      }
      body.push(e("tr", { key: "r" + r }, cells));
    }
    return e("div", { className: "hud__tableWrap" },
      e("table", { className: "hud__table" },
        props.caption ? e("caption", null, props.caption) : null,
        e("thead", null, e("tr", null, head)),
        e("tbody", null, body)
      )
    );
  }

  /* ======================================================================
   * 6. HEALTH — /api/health, /api/health/idle
   *
   * `/api/health` is deliberately a LIGHTWEIGHT liveness answer (its own
   * docstring says so): ok, version, auth_required, state, detail. It carries
   * no diagnostics inventory. So this tab renders exactly those five facts plus
   * the idle proof, and does not invent a battery of counters the route never
   * promised. The reference panel's diagnostics/keys/services grids have no
   * source here and are not faked.
   * ==================================================================== */
  function healthTone(state) {
    var s = str(state).toUpperCase();
    if (s === "READY" || s === "OK" || s === "HEALTHY") return "ok";
    if (s === "DEGRADED" || s === "WARN" || s === "WARNING") return "warn";
    if (s === "BROKEN" || s === "FAILED" || s === "ERROR") return "bad";
    return "";
  }

  function idleTone(idle) {
    if (idle === true) return "ok";
    if (idle === false) return "warn";
    return "";                          /* null = indeterminate, honestly */
  }

  function idleWord(idle) {
    if (idle === true) return "Idle";
    if (idle === false) return "Busy";
    return "Indeterminate";
  }

  function HealthTab() {
    var live = useRead(P.health, [], POLL.health);
    var idle = useRead(P.healthIdle, [], POLL.health);

    var liveShaped = function (d) {
      if (!isObj(d)) return "payload is not an object";
      if (typeof d.state !== "string" && typeof d.ok !== "boolean") {
        return "no `state` or `ok` field";
      }
      return null;
    };

    return e(React.Fragment, null,
      e(Ready, {
        state: live, what: "backend health", expect: liveShaped,
        render: function (d) {
          var tone = healthTone(d.state);
          var okTone = d.ok === true ? "ok" : d.ok === false ? "bad" : "";
          return e(Panel, {
            title: "Backend", wide: true, hint: "polled every " +
              (clampPoll(POLL.health) / 1000) + "s"
          },
            e(StatGrid, null,
              e(Stat, {
                label: "State",
                value: str(d.state) || DASH,
                tone: tone
              }),
              e(Stat, { label: "Responding", value: d.ok === true ? "Yes"
                : d.ok === false ? "No" : DASH, tone: okTone }),
              e(Stat, { label: "Version", value: text(d.version) }),
              e(Stat, {
                label: "Auth required",
                value: d.auth_required === true ? "Yes"
                  : d.auth_required === false ? "No" : DASH,
                tone: d.auth_required === true ? "warn" : ""
              })
            ),
            e("div", { style: { marginTop: "var(--hud-s3)" } },
              e(KV, { rows: [
                ["Detail", text(d.detail)],
                ["Read at", fmtTime(live.at / 1000)]
              ] })
            ),
            e("div", { className: "hud__bar", style: { marginTop: "var(--hud-s3)" } },
              e("button", {
                type: "button", className: "hud-btn", onClick: live.reload
              }, "Refresh"),
              live.status === "error"
                ? e(Chip, { tone: "bad" }, "last read failed")
                : e(Chip, { tone: "ok" }, "last read ok")
            )
          );
        }
      }),
      e(Ready, {
        state: idle, what: "the idle proof",
        expect: function (d) {
          if (!isObj(d)) return "payload is not an object";
          if (!("idle" in d)) return "no `idle` field";
          return null;
        },
        render: function (d) {
          return e(Panel, {
            title: "Idle proof",
            hint: "no turn in flight, nothing waiting on a human"
          },
            e(StatGrid, null,
              e(Stat, {
                label: "Idle",
                value: idleWord(d.idle),
                tone: idleTone(d.idle)
              })
            ),
            e("div", { style: { marginTop: "var(--hud-s3)" } },
              e(KV, { rows: [
                ["Reason", text(d.reason)],
                ["Read at", fmtTime(idle.at / 1000)]
              ] })
            ),
            d.idle === null
              ? e("div", { className: "hud-state hud-state--empty",
                           style: { marginTop: "var(--hud-s2)" } },
                  e("div", { className: "hud-state__detail" },
                    "The probe could not determine idleness and reported " +
                    "null rather than guessing. That is the route's own " +
                    "answer, not a failure of this page."))
              : null
          );
        }
      }),
      e(Panel, { title: "What this tab does not show", wide: true },
        e("div", { className: "hud-state hud-state--empty" },
          e("div", { className: "hud-state__detail" },
            "Health is answered by a deliberately liveness-only route: " +
            "version, auth gate, lifecycle state and the idle proof. A " +
            "diagnostics inventory (per-check status, key presence, service " +
            "PIDs) is not part of that payload, so no such grid is rendered " +
            "here — an observability page that invents counters is worse " +
            "than a small one.")
        )
      )
    );
  }

  /* ======================================================================
   * 7. SESSIONS — list, stats, search, transcript, timeline
   * ==================================================================== */
  function sourceChip(source) {
    var s = str(source);
    if (!s) return null;
    /* The chip is a LABEL, so it goes through the brand scrub; the raw id is
     * still visible in the row's own id line where an operator needs it. */
    return e(Chip, null, scrub(s));
  }

  function msgRoleTone(role) {
    var r = str(role).toLowerCase();
    if (r === "user") return "accent";
    if (r === "assistant") return "ok";
    if (r === "system") return "";
    if (r === "tool") return "warn";
    return "";
  }

  /* The transcript for one session: paged messages plus the timeline index.
   * Both are separate reads, so one failing does not hide the other. */
  function Transcript(props) {
    var id = props.id;
    var offsetS = useState(0);
    var offset = offsetS[0], setOffset = offsetS[1];
    var showTlS = useState(false);
    var showTl = showTlS[0], setShowTl = showTlS[1];

    var route = P.sessions + "/" + encodeURIComponent(id) + "/messages" +
      "?limit=" + MESSAGES_PAGE + "&offset=" + offset;
    var msgs = useRead(route, [id, offset], 0);
    var tlRoute = P.sessions + "/" + encodeURIComponent(id) + "/timeline" +
      "?limit=" + TIMELINE_LIMIT;
    var tl = useRead(tlRoute, [id, showTl ? "on" : "off"], 0);

    /* A new session id starts its transcript at the first page again. */
    var idRef = useRef(id);
    useEffect(function () {
      if (idRef.current !== id) {
        idRef.current = id;
        setOffset(0);
        setShowTl(false);
      }
    }, [id]);

    var body = e(Ready, {
      state: msgs, what: "the transcript",
      expect: function (d) {
        if (!isObj(d)) return "payload is not an object";
        if (!Array.isArray(d.messages)) return "no `messages` array";
        return null;
      },
      render: function (d) {
        var rows = d.messages || [];
        var total = isNum(d.total) ? d.total : rows.length;
        var shown = offset + rows.length;
        var hasPrev = offset > 0;
        var hasNext = rows.length > 0 && shown < total;
        return e("div", { className: "hud-list" },
          e("div", { className: "hud__bar" },
            e("span", null, num(rows.length) + " of " + num(total) + " messages"),
            e("span", { className: "hud__spacer" }),
            e("button", {
              type: "button", className: "hud-btn", disabled: !hasPrev,
              onClick: function () {
                setOffset(Math.max(0, offset - MESSAGES_PAGE));
              }
            }, "Prev"),
            e("button", {
              type: "button", className: "hud-btn", disabled: !hasNext,
              onClick: function () { setOffset(offset + MESSAGES_PAGE); }
            }, "Next")
          ),
          rows.length === 0
            ? e(StateBox, {
                kind: "empty",
                title: "No messages on this page",
                route: route,
                detail: total === 0
                  ? "The session store reports zero messages for this id. Receipt-only sessions carry no chat transcript."
                  : "This page is past the end of the transcript. Step back with Prev."
              })
            : e("div", { className: "hud-list" },
                rows.map(function (m, i) {
                  return e("div", { key: (m && m.meta && m.meta.id) || i,
                                    className: "hud-row" },
                    e("div", { className: "hud-row__top" },
                      e(Chip, { tone: msgRoleTone(m && m.role) },
                        str(m && m.role) || "unknown"),
                      e("span", { className: "hud-row__meta" },
                        fmtTime(m && m.timestamp))
                    ),
                    e("div", {
                      className: "hud-row__desc",
                      style: { whiteSpace: "pre-wrap" },
                      title: str(m && m.content).length > 400
                        ? clip(m && m.content, 4000) : null
                    }, clip(m && m.content, 4000))
                  );
                })
              )
        );
      }
    });

    return e(Panel, {
      title: "Transcript",
      wide: true,
      hint: "session " + id
    },
      e("div", { className: "hud__bar", style: { marginBottom: "var(--hud-s2)" } },
        e("button", {
          type: "button",
          className: "hud-btn" + (showTl ? " hud-btn--on" : ""),
          onClick: function () { setShowTl(!showTl); }
        }, showTl ? "Hide timeline" : "Show timeline")
      ),
      showTl ? e(Ready, {
        state: tl, what: "the timeline",
        expect: function (d) {
          if (!isObj(d)) return "payload is not an object";
          if (!Array.isArray(d.entries)) return "no `entries` array";
          return null;
        },
        render: function (d) {
          var rows = d.entries || [];
          var pg = isObj(d.pagination) ? d.pagination : {};
          return e("div", { style: { marginBottom: "var(--hud-s3)" } },
            rows.length === 0
              ? e(StateBox, {
                  kind: "empty", title: "No timeline entries", route: tlRoute,
                  detail: "The prompt-metadata index holds no rows for this session."
                })
              : e(Table, {
                  caption: "Prompt index — " + num(rows.length) + " rows" +
                    (isNum(pg.total) ? " of " + num(pg.total) : ""),
                  cols: [
                    { key: "row_id", label: "Row", num: true },
                    { key: "timestamp", label: "When",
                      render: function (r) { return fmtTime(r.timestamp); } },
                    { key: "preview", label: "Preview", trunc: true,
                      render: function (r) { return clip(r.preview, 160); } }
                  ],
                  rows: rows
                })
          );
        }
      }) : null,
      body
    );
  }

  function SearchResults(props) {
    var q = props.query;
    var route = P.sessionsSearch + "?q=" + encodeURIComponent(q) +
      "&limit=" + SESSIONS_PAGE;
    var st = useRead(route, [q], 0);
    return e(Panel, { title: "Search", wide: true, hint: q },
      e(Ready, {
        state: st, what: "the search",
        expect: function (d) {
          if (!isObj(d)) return "payload is not an object";
          if (!Array.isArray(d.sessions) && !Array.isArray(d.results)) {
            return "neither `sessions` nor `results` is an array";
          }
          return null;
        },
        render: function (d) {
          var rows = Array.isArray(d.sessions) ? d.sessions
            : (Array.isArray(d.results) ? d.results : []);
          return rows.length === 0
            ? e(StateBox, {
                kind: "empty", title: "No sessions match",
                route: route,
                detail: "The search ran and returned no session for " +
                  JSON.stringify(q) + "."
              })
            : e("div", { className: "hud-list" },
                rows.map(function (s, i) {
                  var sid = str(s && s.id);
                  return e("button", {
                    key: sid || i,
                    type: "button",
                    className: "hud-row hud-row--button",
                    onClick: function () { props.onSelect(sid); }
                  },
                    e("div", { className: "hud-row__top" },
                      sourceChip(s && s.source),
                      e("span", { className: "hud-row__name" },
                        label(s && (s.title || s.name || s.id))),
                      e("span", { className: "hud-row__meta" },
                        fmtAgo(s && s.last_active))
                    ),
                    e("div", { className: "hud-row__desc" },
                      clip(s && s.preview, 180))
                  );
                })
              );
        }
      })
    );
  }

  function SessionsTab() {
    var stats = useRead(P.sessionsStats, [], POLL.sessions);
    var listRoute = P.sessions + "?limit=" + SESSIONS_PAGE;
    var list = useRead(listRoute, [], POLL.sessions);
    var qS = useState("");
    var q = qS[0], setQ = qS[1];
    var subS = useState("");
    var submitted = subS[0], setSubmitted = subS[1];
    var selS = useState("");
    var selected = selS[0], setSelected = selS[1];

    var statsPanel = e(Ready, {
      state: stats, what: "session statistics",
      expect: function (d) {
        if (!isObj(d)) return "payload is not an object";
        if (!isNum(d.total)) return "no numeric `total`";
        return null;
      },
      render: function (d) {
        var bySource = isObj(d.by_source) ? countRows(d.by_source) : [];
        var byState = isObj(d.by_state) ? countRows(d.by_state) : [];
        var maxSrc = bySource.length ? bySource[0][1] : 0;
        var stored = isNum(d.total) ? d.total : 0;
        var active = isNum(d.active_store) ? d.active_store : 0;
        return e(Panel, { title: "Sessions", wide: true,
                          hint: "polled every " +
                            (clampPoll(POLL.sessions) / 1000) + "s" },
          e(StatGrid, null,
            e(Stat, { label: "Total", value: num(d.total) }),
            e(Stat, { label: "Active", value: num(d.active_store), tone: "ok" }),
            e(Stat, { label: "Archived", value: num(d.archived) }),
            e(Stat, { label: "Messages", value: num(d.messages) })
          ),
          bySource.length
            ? e("div", { style: { marginTop: "var(--hud-s3)" } },
                e("div", { className: "hud__panelTitle",
                           style: { marginBottom: "var(--hud-s2)" } },
                  "By source"),
                bySource.map(function (row) {
                  return e(Bar, {
                    key: "src:" + row[0],
                    label: row[0],
                    value: row[1],
                    max: maxSrc,
                    display: num(row[1])
                  });
                })
              )
            : e("div", { className: "hud-state hud-state--empty",
                         style: { marginTop: "var(--hud-s3)" } },
                e("div", { className: "hud-state__detail" },
                  "The stats route answered with an empty `by_source` map.")),
          byState.length
            ? e("div", { style: { marginTop: "var(--hud-s3)" } },
                e("div", { className: "hud__panelTitle",
                           style: { marginBottom: "var(--hud-s2)" } },
                  "By store state"),
                e("dl", { className: "hud__kv" },
                  byState.map(function (row, i) {
                    return e(React.Fragment, { key: "st:" + row[0] },
                      e("dt", null, label(row[0])),
                      e("dd", { className: "hud__mono" }, num(row[1])));
                  })
                ))
            : null,
          e("div", { className: "hud__bar",
                     style: { marginTop: "var(--hud-s3)" } },
            e(Chip, null, "store " + text(d.store_authority)),
            active === stored && stored > 0
              ? e(Chip, { tone: "ok" }, "all rows active")
              : null)
        );
      }
    });

    var listPanel = e(Ready, {
      state: list, what: "the session list",
      expect: function (d) {
        if (!isObj(d)) return "payload is not an object";
        if (!Array.isArray(d.sessions)) return "no `sessions` array";
        return null;
      },
      render: function (d) {
        var rows = d.sessions || [];
        var total = isNum(d.total) ? d.total : rows.length;
        return e(Panel, {
          title: "Recent",
          wide: true,
          hint: num(rows.length) + " of " + num(total)
        },
          e("form", {
            className: "hud__bar",
            style: { marginBottom: "var(--hud-s2)" },
            onSubmit: function (ev) {
              ev.preventDefault();
              setSubmitted(q.trim());
            }
          },
            e("label", { htmlFor: "hud-session-q", className: "hud-sr" },
              "Search sessions"),
            e("input", {
              id: "hud-session-q",
              className: "hud__input",
              type: "search",
              value: q,
              placeholder: "Search sessions\u2026",
              onChange: function (ev) { setQ(ev.target.value); }
            }),
            e("button", { type: "submit", className: "hud-btn",
                          disabled: !q.trim() }, "Search"),
            submitted
              ? e("button", {
                  type: "button", className: "hud-btn",
                  onClick: function () { setSubmitted(""); setQ(""); }
                }, "Clear")
              : null
          ),
          rows.length === 0
            ? e(StateBox, {
                kind: "empty", title: "No sessions", route: listRoute,
                detail: "The session store returned an empty list. Nothing is " +
                  "being hidden by this page."
              })
            : e("div", { className: "hud-list" },
                rows.map(function (s, i) {
                  var sid = str(s && s.id);
                  var isSel = sid && sid === selected;
                  return e("button", {
                    key: sid || ("row" + i),
                    type: "button",
                    className: "hud-row hud-row--button" +
                      (isSel ? " hud-row--sel" : ""),
                    "aria-pressed": isSel ? "true" : "false",
                    onClick: function () {
                      setSelected(isSel ? "" : sid);
                    }
                  },
                    e("div", { className: "hud-row__top" },
                      e(Chip, { tone: s && s.is_active ? "ok" : "" },
                        s && s.is_active ? "active" : "idle"),
                      sourceChip(s && s.source),
                      e("span", { className: "hud-row__name" },
                        label(s && (s.title || s.name || s.id))),
                      s && s.archived ? e(Chip, { tone: "warn" }, "archived") : null,
                      e("span", { className: "hud-row__meta" },
                        num(s && s.message_count) + " msg \u00b7 " +
                        fmtAgo(s && s.last_active))
                    ),
                    e("div", { className: "hud-row__desc" },
                      clip(s && s.preview, 200)),
                    e("div", { className: "hud-row__meta" },
                      clip(sid, 80))
                  );
                })
              )
        );
      }
    });

    return e(React.Fragment, null,
      statsPanel,
      listPanel,
      submitted ? e(SearchResults, {
        query: submitted,
        onSelect: function (sid) { if (sid) setSelected(sid); }
      }) : null,
      selected
        ? e(Transcript, { key: selected, id: selected })
        : e(Panel, { title: "Transcript", wide: true },
            e("div", { className: "hud-state hud-state--empty" },
              e("div", { className: "hud-state__detail" },
                "Select a session above to read its transcript. The transcript " +
                "and timeline are read from " + P.sessions +
                "/{id}/messages and /{id}/timeline on demand, one page at a " +
                "time, so a long conversation is never pulled whole into this " +
                "page.")))
    );
  }

  /* ======================================================================
   * 8. CRON — jobs, one job, run history
   *
   * READ-ONLY: the reference panel offered pause/resume/trigger/delete and a
   * create drawer. Those are staged mutations on the shared confirmation
   * journal; this viewer offers none of them and says so once, quietly, rather
   * than rendering disabled controls that imply a capability.
   * ==================================================================== */
  function cronTone(state, enabled) {
    var s = str(state).toLowerCase();
    if (s === "paused") return "warn";
    if (s === "completed") return "";
    if (s === "scheduled" || s === "running" || s === "active") {
      return enabled === false ? "warn" : "ok";
    }
    if (s === "failed" || s === "error") return "bad";
    return "";
  }

  function runTone(state, code) {
    var s = str(state).toLowerCase();
    var c = str(code).toUpperCase();
    if (s === "completed" || c === "OK") return "ok";
    if (s === "failed" || s === "error" || (c && c !== "OK")) return "bad";
    if (s === "running" || s === "active") return "warn";
    return "";
  }

  function CronJobDetail(props) {
    var id = props.id;
    var showRunsS = useState(true);
    var showRuns = showRunsS[0], setShowRuns = showRunsS[1];
    var oneRoute = P.cronJobs + "/" + encodeURIComponent(id);
    var one = useRead(oneRoute, [id], 0);
    var runsRoute = oneRoute + "/runs?limit=" + CRON_RUNS;
    var runs = useRead(runsRoute, [id, showRuns ? "on" : "off"], 0);

    return e(Panel, { title: "Job detail", wide: true, hint: id },
      e(Ready, {
        state: one, what: "the cron job",
        expect: function (d) {
          if (!isObj(d)) return "payload is not an object";
          if (!d.id && !d.name) return "no `id` and no `name`";
          return null;
        },
        render: function (job) {
          var sched = isObj(job.schedule) ? job.schedule : {};
          var repeat = isObj(job.repeat) ? job.repeat : {};
          return e("div", { className: "hud-list" },
            e("div", { className: "hud-row__top" },
              e(Chip, { tone: cronTone(job.state, job.enabled) },
                label(job.state)),
              e("span", { className: "hud-row__name" },
                label(job.name || job.id)),
              job.no_agent ? e(Chip, null, "script only") : null,
              job.last_status
                ? e(Chip, { tone: runTone(job.last_status, "") },
                    "last " + str(job.last_status))
                : null
            ),
            e(KV, { rows: [
              ["Schedule", text(job.schedule_display || sched.display ||
                sched.expr)],
              ["Kind", text(sched.kind)],
              ["Enabled", job.enabled === true ? "Yes"
                : job.enabled === false ? "No" : DASH],
              ["Last run", fmtAgoIso(job.last_run_at)],
              ["Next run", job.next_run_at ? fmtIsoFuture(job.next_run_at)
                : DASH],
              ["Runs completed", num(repeat.completed)],
              ["Deliver", label(job.deliver)],
              ["Model", label(job.model)],
              ["Provider", label(job.provider)],
              ["Script", text(job.script)],
              ["Workdir", text(job.workdir)],
              ["Last error", text(job.last_error)],
              ["Delivery error", text(job.last_delivery_error)]
            ] }),
            e("div", { className: "hud__bar",
                       style: { marginTop: "var(--hud-s2)" } },
              e("button", {
                type: "button",
                className: "hud-btn" + (showRuns ? " hud-btn--on" : ""),
                onClick: function () { setShowRuns(!showRuns); }
              }, showRuns ? "Hide runs" : "Show runs")
            ),
            showRuns ? e(Ready, {
              state: runs, what: "the run history",
              expect: function (d) {
                if (!isObj(d)) return "payload is not an object";
                if (!Array.isArray(d.runs)) return "no `runs` array";
                return null;
              },
              render: function (d) {
                var rows = d.runs || [];
                return rows.length === 0
                  ? e(StateBox, {
                      kind: "empty", title: "No runs recorded",
                      route: runsRoute,
                      detail: "The cron execution ledger holds no run for this " +
                        "job yet. A job that has never fired is empty, not " +
                        "failed."
                    })
                  : e(Table, {
                      caption: "Run history — " + num(rows.length) + " most recent",
                      cols: [
                        { key: "state", label: "State",
                          render: function (r) {
                            return e(Chip, { tone: runTone(r.state, r.code) },
                              text(r.state));
                          } },
                        { key: "code", label: "Code" },
                        { key: "started_at", label: "Started",
                          render: function (r) { return fmtAgoIso(r.started_at); } },
                        { key: "duration", label: "Duration", num: true,
                          render: function (r) {
                            return fmtDuration(r.started_at,
                              r.ended_at || r.finished_at);
                          } },
                        { key: "detail", label: "Detail", trunc: true,
                          render: function (r) { return clip(r.detail, 120); } }
                      ],
                      rows: rows
                    });
              }
            }) : null
          );
        }
      })
    );
  }

  function fmtIsoFuture(iso) {
    var ms = Date.parse(String(iso || ""));
    if (!isFinite(ms)) return DASH;
    try {
      return new Date(ms).toLocaleString();
    } catch (ex) {
      return DASH;
    }
  }

  function CronTab() {
    var st = useRead(P.cronJobs, [], POLL.cron);
    var selS = useState("");
    var selected = selS[0], setSelected = selS[1];

    var list = e(Ready, {
      state: st, what: "the cron jobs",
      /* The route serves a BARE ARRAY of jobs (not an envelope). Accept only
       * that shape: anything else is reported, never coerced. */
      expect: function (d) {
        if (!Array.isArray(d)) return "payload is not an array of jobs";
        return null;
      },
      render: function (jobs) {
        var enabled = 0, paused = 0, failed = 0;
        for (var i = 0; i < jobs.length; i++) {
          var j = jobs[i] || {};
          if (j.enabled !== false && str(j.state).toLowerCase() !== "paused") {
            enabled++;
          }
          if (str(j.state).toLowerCase() === "paused") paused++;
          if (str(j.last_status).toLowerCase() === "failed" ||
              str(j.last_status).toLowerCase() === "error") failed++;
        }
        return e(Panel, {
          title: "Cron jobs", wide: true,
          hint: "polled every " + (clampPoll(POLL.cron) / 1000) + "s"
        },
          e(StatGrid, null,
            e(Stat, { label: "Jobs", value: num(jobs.length) }),
            e(Stat, { label: "Live", value: num(enabled), tone: "ok" }),
            e(Stat, { label: "Paused", value: num(paused),
                      tone: paused > 0 ? "warn" : "" }),
            e(Stat, { label: "Last run failed", value: num(failed),
                      tone: failed > 0 ? "bad" : "" })
          ),
          jobs.length === 0
            ? e(StateBox, {
                kind: "empty", title: "No cron jobs", route: P.cronJobs,
                detail: "The scheduler's job store is empty. Nothing fires and " +
                  "nothing is hidden by this page."
              })
            : e("div", { className: "hud-list",
                         style: { marginTop: "var(--hud-s3)" } },
                jobs.map(function (job, i) {
                  var id = str(job && job.id);
                  var isSel = id && id === selected;
                  return e("button", {
                    key: id || ("job" + i),
                    type: "button",
                    className: "hud-row hud-row--button" +
                      (isSel ? " hud-row--sel" : ""),
                    "aria-pressed": isSel ? "true" : "false",
                    onClick: function () { setSelected(isSel ? "" : id); }
                  },
                    e("div", { className: "hud-row__top" },
                      e(Chip, { tone: cronTone(job.state, job.enabled) },
                        label(job.state)),
                      e("span", { className: "hud-row__name" },
                        label(job.name || job.id)),
                      e("span", { className: "hud-row__meta" },
                        text(job.schedule_display))
                    ),
                    e("div", { className: "hud-row__meta" },
                      "last " + fmtAgoIso(job.last_run_at) +
                      " \u00b7 next " + (job.next_run_at
                        ? fmtIsoFuture(job.next_run_at) : DASH) +
                      " \u00b7 " + num(job && job.repeat && job.repeat.completed) +
                      " runs" +
                      (job.last_status ? " \u00b7 " + str(job.last_status) : "") +
                      (job.deliver ? " \u00b7 " + str(job.deliver) : ""))
                  );
                })
              ),
          e("div", { className: "hud-state hud-state--empty",
                     style: { marginTop: "var(--hud-s3)" } },
            e("div", { className: "hud-state__detail" },
              "This tab is a viewer. Pausing, triggering and editing a job are " +
              "staged operations on the shared confirmation journal and are " +
              "deliberately not offered here; no control is drawn for a " +
              "capability this page does not have."))
        );
      }
    });

    return e(React.Fragment, null,
      list,
      selected ? e(CronJobDetail, { key: selected, id: selected }) : null
    );
  }

  /* ======================================================================
   * 9. MODELS — /api/model/options (+ /api/model/info)
   *
   * See the header: `/api/models` is not served by this build, so this tab
   * reads the two real model authorities and names the substitution on screen.
   * ==================================================================== */
  function CapChip(props) {
    var v = props.value;
    var label = props.label;
    if (v === true) return e(Chip, { tone: "ok" }, label);
    if (v === false) return e(Chip, null, "no " + label);
    return e(Chip, { tone: "warn" }, label + " unknown");
  }

  function ModelsTab() {
    var info = useRead(P.modelInfo, [], POLL.models);
    var opts = useRead(P.modelOptions, [], POLL.models);
    var openS = useState("");
    var open = openS[0], setOpen = openS[1];

    var infoPanel = e(Ready, {
      state: info, what: "the resolved model",
      expect: function (d) {
        if (!isObj(d)) return "payload is not an object";
        if (!d.model && !d.provider) return "no `model` and no `provider`";
        return null;
      },
      render: function (d) {
        var caps = isObj(d.capabilities) ? d.capabilities : {};
        var ident = isObj(d.identity) ? d.identity : null;
        var mismatch = d.identity_match === false;
        return e(Panel, {
          title: "Resolved model", wide: true,
          hint: "GET " + P.modelInfo
        },
          e(StatGrid, null,
            e(Stat, { label: "Model", value: text(d.model) }),
            e(Stat, { label: "Provider", value: text(d.provider) }),
            e(Stat, {
              label: "Context window",
              value: isNum(d.effective_context_length)
                ? tokens(d.effective_context_length)
                : DASH
            }),
            e(Stat, {
              label: "Auto-detected",
              value: isNum(d.auto_context_length)
                ? tokens(d.auto_context_length) : DASH
            }),
            e(Stat, {
              label: "Configured override",
              value: isNum(d.config_context_length) && d.config_context_length > 0
                ? tokens(d.config_context_length) : "none"
            })
          ),
          e("div", { className: "hud__bar",
                     style: { marginTop: "var(--hud-s3)" } },
            e(CapChip, { label: "tools", value: caps.supports_tools }),
            e(CapChip, { label: "vision", value: caps.supports_vision }),
            e(CapChip, { label: "reasoning", value: caps.supports_reasoning }),
            e("span", { className: "hud__panelHint" },
              "family " + label(caps.model_family)),
            e("span", { className: "hud__panelHint" },
              "max out " + (isNum(caps.max_output_tokens)
                ? tokens(caps.max_output_tokens) : DASH))
          ),
          e("div", { style: { marginTop: "var(--hud-s3)" } },
            e(KV, { rows: [
              ["Identity", mismatch ? "mismatch" : text(d.identity_code || "consistent")],
              ["Detail", ident ? text(ident.detail) : DASH]
            ] })
          ),
          mismatch
            ? e("div", { className: "hud-state hud-state--error",
                         style: { marginTop: "var(--hud-s2)" } },
                e("div", { className: "hud-state__title" },
                  "Configured and active selections disagree"),
                e("div", { className: "hud-state__detail" },
                  "Effective selection is " +
                  text(ident && (ident.effective_provider + "/" +
                    ident.effective_model)) +
                  " from " + text(ident && ident.effective_source) +
                  ", while the persisted active profile names " +
                  text(ident && (ident.active_provider + "/" + ident.active_model)) +
                  ". This page reports the disagreement; it does not resolve it."))
            : null
        );
      }
    });

    var providerPanel = e(Ready, {
      state: opts, what: "the provider catalogue",
      expect: function (d) {
        if (!isObj(d)) return "payload is not an object";
        if (!Array.isArray(d.providers)) return "no `providers` array";
        return null;
      },
      render: function (d) {
        var rows = d.providers || [];
        var authed = 0, total = 0;
        for (var i = 0; i < rows.length; i++) {
          if (rows[i] && rows[i].authenticated) authed++;
          if (rows[i] && isNum(rows[i].total_models)) total += rows[i].total_models;
        }
        return e(Panel, {
          title: "Providers",
          wide: true,
          hint: "GET " + P.modelOptions
        },
          e(StatGrid, null,
            e(Stat, { label: "Providers", value: num(rows.length) }),
            e(Stat, { label: "Authenticated", value: num(authed),
                      tone: authed > 0 ? "ok" : "warn" }),
            e(Stat, { label: "Models listed", value: num(total) }),
            e(Stat, { label: "Current", value: text(d.provider) })
          ),
          rows.length === 0
            ? e(StateBox, {
                kind: "empty", title: "No providers configured",
                route: P.modelOptions,
                detail: "The catalogue route answered with an empty provider " +
                  "list. No model is selectable in this build."
              })
            : e("div", { style: { marginTop: "var(--hud-s3)" } },
                e(Table, {
                  cols: [
                    { key: "name", label: "Provider",
                      render: function (r) {
                        return e(React.Fragment, null,
                          e("span", { className: "hud-row__name" },
                            label(r.name || r.slug)),
                          r.is_current ? e("span", null, " ",
                            e(Chip, { tone: "accent" }, "current")) : null,
                          r.is_user_defined
                            ? e("span", null, " ",
                                e(Chip, null, "user-defined"))
                            : null
                        );
                      } },
                    { key: "authenticated", label: "Auth",
                      render: function (r) {
                        if (r.authenticated === true) {
                          return e(Chip, { tone: "ok" }, "signed in");
                        }
                        if (r.authenticated === false) {
                          return e(Chip, { tone: "warn" }, "no credentials");
                        }
                        return DASH;
                      } },
                    { key: "total_models", label: "Models", num: true,
                      render: function (r) { return num(r.total_models); } },
                    { key: "models", label: "",
                      render: function (r) {
                        var list = Array.isArray(r.models) ? r.models : [];
                        if (!list.length) return DASH;
                        var isOpen = open === str(r.slug);
                        return e("button", {
                          type: "button",
                          className: "hud-btn" + (isOpen ? " hud-btn--on" : ""),
                          "aria-expanded": isOpen ? "true" : "false",
                          onClick: function () {
                            setOpen(isOpen ? "" : str(r.slug));
                          }
                        }, isOpen ? "Hide" : "Show " + num(list.length));
                      } },
                    { key: "warning", label: "Note", trunc: true,
                      render: function (r) {
                        return r.warning ? clip(r.warning, 120) : DASH;
                      } }
                  ],
                  rows: rows
                }),
                open
                  ? (function () {
                      var hit = null;
                      for (var k = 0; k < rows.length; k++) {
                        if (str(rows[k] && rows[k].slug) === open) hit = rows[k];
                      }
                      if (!hit) return null;
                      var names = Array.isArray(hit.models) ? hit.models : [];
                      return e("div", { style: { marginTop: "var(--hud-s3)" } },
                        e(Table, {
                          caption: "Models on " + str(hit.name || hit.slug) +
                            " — " + num(names.length),
                          cols: [
                            { key: "m", label: "Model",
                              render: function (name) { return str(name); } }
                          ],
                          rows: names
                        })
                      );
                    })()
                  : null
              ),
          e("div", { className: "hud-state hud-state--empty",
                     style: { marginTop: "var(--hud-s3)" } },
            e("div", { className: "hud-state__detail" },
              "This tab was specified against " + MODELS_SPECIFIED_ROUTE +
              ". This build serves no GET route at that path (a live probe " +
              "answers 404 NOT_FOUND), so the tab reads the real model " +
              "authorities instead: " + P.modelOptions + " for the catalogue " +
              "and " + P.modelInfo + " for the resolved selection. Nothing " +
              "here is read from " + MODELS_SPECIFIED_ROUTE + "."))
        );
      }
    });

    return e(React.Fragment, null, infoPanel, providerPanel);
  }

  /* ======================================================================
   * 10. SKILLS — /api/skills (a bare array of SkillInfo)
   * ==================================================================== */
  function SkillsTab() {
    var st = useRead(P.skills, [], POLL.skills);
    var qS = useState("");
    var q = qS[0], setQ = qS[1];
    var catS = useState("");
    var cat = catS[0], setCat = catS[1];

    return e(Ready, {
      state: st, what: "the skill catalog",
      /* The route serves a bare ARRAY. */
      expect: function (d) {
        if (!Array.isArray(d)) return "payload is not an array of skills";
        return null;
      },
      render: function (skills) {
        var byCat = {};
        var enabled = 0;
        for (var i = 0; i < skills.length; i++) {
          var s = skills[i] || {};
          var c = str(s.category) || "uncategorized";
          byCat[c] = (byCat[c] || 0) + 1;
          if (s.enabled !== false) enabled++;
        }
        var cats = countRows(byCat);
        var maxCat = cats.length ? cats[0][1] : 0;
        var needle = q.trim().toLowerCase();
        var shown = [];
        for (var j = 0; j < skills.length; j++) {
          var sk = skills[j] || {};
          if (cat && str(sk.category || "uncategorized") !== cat) continue;
          if (needle) {
            var hay = (str(sk.name) + " " + str(sk.description) + " " +
                       str(sk.category)).toLowerCase();
            if (hay.indexOf(needle) === -1) continue;
          }
          shown.push(sk);
        }
        var listRoute = q || cat
          ? P.skills + "  (filtered in this page)"
          : P.skills;
        return e(React.Fragment, null,
          e(Panel, {
            title: "Skills", wide: true,
            hint: "polled every " + (clampPoll(POLL.skills) / 1000) + "s"
          },
            e(StatGrid, null,
              e(Stat, { label: "Total", value: num(skills.length) }),
              e(Stat, { label: "Enabled", value: num(enabled), tone: "ok" }),
              e(Stat, { label: "Disabled", value:
                num(skills.length - enabled),
                tone: skills.length - enabled > 0 ? "warn" : "" }),
              e(Stat, { label: "Categories", value: num(cats.length) })
            ),
            cats.length
              ? e("div", { style: { marginTop: "var(--hud-s3)" } },
                  e("div", { className: "hud__panelTitle",
                             style: { marginBottom: "var(--hud-s2)" } },
                    "By category"),
                  cats.slice(0, 12).map(function (row) {
                    var isOn = row[0] === cat;
                    return e("button", {
                      key: "cat:" + row[0],
                      type: "button",
                      className: "hud-bar",
                      style: { appearance: "none", background: "transparent",
                               border: 0, cursor: "pointer", font: "inherit",
                               padding: 0, width: "100%",
                               color: isOn ? "var(--hud-accent)" : null },
                      "aria-pressed": isOn ? "true" : "false",
                      onClick: function () { setCat(isOn ? "" : row[0]); }
                    },
                      e("span", { className: "hud-bar__label",
                                  title: row[0] }, row[0]),
                      e("span", { className: "hud-bar__track" },
                        e("div", {
                          className: "hud-bar__fill" +
                            (isOn ? "" : " hud-bar__fill--ok"),
                          style: { width: maxCat > 0
                            ? ((row[1] / maxCat) * 100).toFixed(1) + "%" : "0%" }
                        })),
                      e("span", { className: "hud-bar__value" }, num(row[1]))
                    );
                  }),
                  cats.length > 12
                    ? e("div", { className: "hud__panelHint",
                                 style: { marginTop: "var(--hud-s1)" } },
                        "showing the 12 largest of " + num(cats.length) +
                        " categories")
                    : null
                )
              : e("div", { className: "hud-state hud-state--empty",
                           style: { marginTop: "var(--hud-s3)" } },
                  e("div", { className: "hud-state__detail" },
                    "The catalog returned skills but none carries a category."))
          ),
          e(Panel, {
            title: cat ? "Category: " + cat : "All skills",
            wide: true,
            hint: num(shown.length) + " shown"
          },
            e("div", { className: "hud__bar",
                       style: { marginBottom: "var(--hud-s2)" } },
              e("label", { htmlFor: "hud-skill-q", className: "hud-sr" },
                "Filter skills"),
              e("input", {
                id: "hud-skill-q",
                className: "hud__input",
                type: "search",
                value: q,
                placeholder: "Filter by name or description\u2026",
                onChange: function (ev) { setQ(ev.target.value); }
              }),
              cat ? e("button", {
                type: "button", className: "hud-btn",
                onClick: function () { setCat(""); }
              }, "Clear category") : null
            ),
            shown.length === 0
              ? e(StateBox, {
                  kind: "empty",
                  title: skills.length === 0 ? "No skills installed"
                    : "No skill matches these filters",
                  route: listRoute,
                  detail: skills.length === 0
                    ? "The catalog route returned an empty list."
                    : num(skills.length) + " skills are installed; none match " +
                      "the current category and text filter."
                })
              : e("div", { className: "hud-list" },
                  shown.map(function (sk, i) {
                    return e("div", {
                      key: str(sk.name) || ("skill" + i),
                      className: "hud-row"
                    },
                      e("div", { className: "hud-row__top" },
                        e("span", { className: "hud-row__name" },
                          label(sk.name)),
                        e(Chip, { tone: sk.enabled === false ? "warn" : "ok" },
                          sk.enabled === false ? "disabled" : "enabled"),
                        str(sk.category) ? e(Chip, null, str(sk.category)) : null,
                        str(sk.origin) ? e(Chip, null, str(sk.origin)) : null
                      ),
                      e("div", { className: "hud-row__desc" },
                        clip(scrub(sk.description), 300))
                    );
                  })
                )
          )
        );
      }
    });
  }

  /* ======================================================================
   * 11. PLUGINS — /api/dashboard/plugins (bare manifest array) and
   *     /api/dashboard/plugins/catalog (the merged local catalog)
   *
   * Read-only: no enable/disable/install/rescan. The catalog is 1.2MB with ~950
   * pinned-index entries in this build, so the page renders the local entries
   * and the COUNTS of everything else — it never dumps the pinned index into
   * the DOM. It also never prints an entry's `provenance` blob, which is where
   * third-party names live.
   * ==================================================================== */
  function PluginsTab() {
    var dash = useRead(P.plugins, [], POLL.plugins);
    var cat = useRead(P.pluginsCatalog, [], POLL.plugins);
    var srcS = useState("");
    var src = srcS[0], setSrc = srcS[1];

    var dashPanel = e(Ready, {
      state: dash, what: "the dashboard plugins",
      expect: function (d) {
        if (!Array.isArray(d)) return "payload is not an array of manifests";
        return null;
      },
      render: function (rows) {
        var withTab = 0, withApi = 0;
        for (var i = 0; i < rows.length; i++) {
          if (rows[i] && rows[i].tab) withTab++;
          if (rows[i] && rows[i].has_api) withApi++;
        }
        return e(Panel, {
          title: "Dashboard plugins", wide: true,
          hint: "GET " + P.plugins
        },
          e(StatGrid, null,
            e(Stat, { label: "Plugins", value: num(rows.length) }),
            e(Stat, { label: "With a tab", value: num(withTab) }),
            e(Stat, { label: "With a backend", value: num(withApi) })
          ),
          rows.length === 0
            ? e(StateBox, {
                kind: "empty", title: "No dashboard plugins",
                route: P.plugins,
                detail: "The overlay plugin directory holds no manifest. This " +
                  "page is itself a plugin, so an empty answer here is worth " +
                  "a second look at the plugin root."
              })
            : e("div", { className: "hud-list",
                         style: { marginTop: "var(--hud-s3)" } },
                rows.map(function (m, i) {
                  var tab = isObj(m && m.tab) ? m.tab : null;
                  var slots = Array.isArray(m && m.slots) ? m.slots : [];
                  return e("div", {
                    key: str(m && m.name) || ("p" + i),
                    className: "hud-row"
                  },
                    e("div", { className: "hud-row__top" },
                      e("span", { className: "hud-row__name" },
                        label(m && (m.label || m.name))),
                      e("span", { className: "hud-row__meta" },
                        text(m && m.version)),
                      m && m.has_api
                        ? e(Chip, { tone: "ok" }, "backend")
                        : null,
                      tab && tab.hidden
                        ? e(Chip, null, "hidden tab")
                        : null,
                      str(m && m.source) ? e(Chip, null, str(m.source)) : null
                    ),
                    e("div", { className: "hud-row__desc" },
                      clip(scrub(m && m.description), 320)),
                    e("div", { className: "hud-row__meta" },
                      "tab " + text(tab && tab.path) +
                      " \u00b7 entry " + text(m && m.entry) +
                      " \u00b7 css " + text(m && m.css) +
                      (slots.length ? " \u00b7 slots " + slots.join(", ") : ""))
                  );
                })
              ),
          e("div", { className: "hud-state hud-state--empty",
                     style: { marginTop: "var(--hud-s3)" } },
            e("div", { className: "hud-state__detail" },
              "Read-only view. Enabling, disabling, installing and rescanning " +
              "are confirmation-gated operations; this page shows the fleet as " +
              "it is and offers no control that would change it."))
        );
      }
    });

    var catPanel = e(Ready, {
      state: cat, what: "the plugin catalog",
      expect: function (d) {
        if (!isObj(d)) return "payload is not an object";
        if (!Array.isArray(d.entries)) return "no `entries` array";
        return null;
      },
      render: function (d) {
        var entries = d.entries || [];
        var counts = isObj(d.counts) ? d.counts : {};
        var pinned = isObj(d.pinned_index) ? d.pinned_index : null;
        var sources = [];
        for (var i = 0; i < entries.length; i++) {
          var s = str(entries[i] && entries[i].source) || "unknown";
          if (sources.indexOf(s) === -1) sources.push(s);
        }
        sources.sort();
        var shown = [];
        for (var j = 0; j < entries.length; j++) {
          if (src && str(entries[j] && entries[j].source) !== src) continue;
          shown.push(entries[j]);
        }
        shown.sort(function (a, b) {
          return str(a && a.id).localeCompare(str(b && b.id));
        });
        return e(Panel, {
          title: "Plugin catalog", wide: true,
          hint: "GET " + P.pluginsCatalog
        },
          e(StatGrid, null,
            e(Stat, { label: "Local entries", value: num(counts.total !== undefined
              ? counts.total : entries.length) }),
            e(Stat, { label: "Enabled", value: num(counts.enabled), tone: "ok" }),
            e(Stat, { label: "Installed", value: num(counts.installed) }),
            e(Stat, {
              label: "Pinned index",
              value: pinned && isNum(pinned.count) ? num(pinned.count) : DASH
            }),
            e(Stat, {
              label: "Raw remote fetch",
              value: isObj(d.remote) ? text(d.remote.state) : DASH,
              tone: isObj(d.remote) && str(d.remote.state) === "REFUSED"
                ? "warn" : ""
            })
          ),
          e("div", { className: "hud__bar",
                     style: { marginTop: "var(--hud-s3)" } },
            e("button", {
              type: "button",
              className: "hud-btn" + (src === "" ? " hud-btn--on" : ""),
              onClick: function () { setSrc(""); }
            }, "All"),
            sources.map(function (s) {
              return e("button", {
                key: "src:" + s,
                type: "button",
                className: "hud-btn" + (src === s ? " hud-btn--on" : ""),
                onClick: function () { setSrc(s); }
              }, s);
            }),
            e("span", { className: "hud__spacer" }),
            e("span", { className: "hud__panelHint" },
              num(shown.length) + " shown")
          ),
          shown.length === 0
            ? e(StateBox, {
                kind: "empty", title: "No catalog entry for this source",
                route: P.pluginsCatalog,
                detail: "The catalog answered; no entry carries the selected " +
                  "source filter."
              })
            : e("div", { style: { marginTop: "var(--hud-s2)" } },
                e(Table, {
                  cols: [
                    { key: "name", label: "Plugin", trunc: true,
                      render: function (r) { return label(r.label || r.name); } },
                    { key: "source", label: "Source" },
                    { key: "kind", label: "Kind" },
                    { key: "version", label: "Version" },
                    { key: "enabled", label: "Enabled",
                      render: function (r) {
                        if (r.enabled === true) return e(Chip, { tone: "ok" }, "yes");
                        if (r.enabled === false) return e(Chip, { tone: "warn" }, "no");
                        return DASH;
                      } },
                    { key: "runtime_status", label: "Runtime" },
                    { key: "loadable", label: "Loadable",
                      render: function (r) {
                        if (r.loadable === true) return "yes";
                        if (r.loadable === false) return e(Chip, { tone: "bad" }, "no");
                        return DASH;
                      } },
                    { key: "description", label: "Description", trunc: true,
                      render: function (r) { return clip(scrub(r.description), 120); } }
                  ],
                  rows: shown
                }),
                e("div", { className: "hud__panelHint",
                           style: { marginTop: "var(--hud-s1)" } },
                  "Sorted by id. Provenance blobs are intentionally not " +
                  "rendered on this page.")
              ),
          isObj(d.remote)
            ? e("div", { className: "hud-state hud-state--empty",
                         style: { marginTop: "var(--hud-s3)" } },
                e("div", { className: "hud-state__title" },
                  "Remote fetch: " + text(d.remote.state)),
                e("div", { className: "hud-state__detail" },
                  clip(d.remote.detail, 400)))
            : null,
          isObj(d.estop) && d.estop === true
            ? e("div", { className: "hud-state hud-state--error",
                         style: { marginTop: "var(--hud-s2)" } },
                e("div", { className: "hud-state__title" },
                  "Emergency stop engaged"),
                e("div", { className: "hud-state__detail" },
                  "Plugin mutations are halted by the e-stop. Reads still work."))
            : null
        );
      }
    });

    return e(React.Fragment, null, dashPanel, catPanel);
  }

  /* ======================================================================
   * 12. COSTS — /api/analytics (+ /api/sessions/stats for the session count)
   *
   * The analytics route's own docstring guarantees an HONEST EMPTY answer
   * (zeros with no daily/by_model rows) when the canonical store has no usage
   * yet. This tab distinguishes that case from "we could not read it": zeros
   * with an empty trend render an explicit "no usage recorded" note instead of
   * a wall of $0.00 that would read as real spending.
   * ==================================================================== */
  function CostsTab() {
    var an = useRead(P.analytics, [], POLL.costs);
    var stats = useRead(P.sessionsStats, [], POLL.costs);

    var statsPanel = e(Ready, {
      state: stats, what: "the session count",
      expect: function (d) {
        if (!isObj(d)) return "payload is not an object";
        if (!isNum(d.total)) return "no numeric `total`";
        return null;
      },
      render: function (d) {
        return e(Panel, {
          title: "Stores", wide: true, hint: "GET " + P.sessionsStats
        },
          e(StatGrid, null,
            e(Stat, { label: "Sessions stored", value: num(d.total) }),
            e(Stat, { label: "Active", value: num(d.active_store) }),
            e(Stat, { label: "Archived", value: num(d.archived) }),
            e(Stat, { label: "Messages", value: num(d.messages) })
          ),
          e("div", { className: "hud__bar",
                     style: { marginTop: "var(--hud-s3)" } },
            e(Chip, null, "authority " + text(d.store_authority)))
        );
      }
    });

    var anPanel = e(Ready, {
      state: an, what: "the usage analytics",
      expect: function (d) {
        if (!isObj(d)) return "payload is not an object";
        if (!isObj(d.totals)) return "no `totals` object";
        if (!Array.isArray(d.daily)) return "no `daily` array";
        if (!Array.isArray(d.by_model)) return "no `by_model` array";
        return null;
      },
      render: function (d) {
        var t = d.totals || {};
        var daily = d.daily || [];
        var byModel = d.by_model || [];
        var days = daily.length;
        var noUsage = days === 0 && byModel.length === 0 &&
          !(t.total_api_calls > 0);
        var totalTokens = (isNum(t.total_input) ? t.total_input : 0) +
          (isNum(t.total_output) ? t.total_output : 0) +
          (isNum(t.total_cache_read) ? t.total_cache_read : 0) +
          (isNum(t.total_reasoning) ? t.total_reasoning : 0);
        var avgPerCall = isNum(t.total_api_calls) && t.total_api_calls > 0
          ? Math.round(totalTokens / t.total_api_calls) : 0;
        var maxDay = 0;
        for (var i = 0; i < daily.length; i++) {
          var row = daily[i] || {};
          var tt = (isNum(row.input_tokens) ? row.input_tokens : 0) +
                   (isNum(row.output_tokens) ? row.output_tokens : 0);
          if (tt > maxDay) maxDay = tt;
        }
        var maxModel = 0;
        for (var j = 0; j < byModel.length; j++) {
          var m = byModel[j] || {};
          var mt = (isNum(m.input_tokens) ? m.input_tokens : 0) +
                   (isNum(m.output_tokens) ? m.output_tokens : 0);
          if (mt > maxModel) maxModel = mt;
        }
        return e(React.Fragment, null,
          e(Panel, {
            title: "Tokens & cost", wide: true,
            hint: "GET " + P.analytics + " \u00b7 polled every " +
              (clampPoll(POLL.costs) / 1000) + "s"
          },
            e(StatGrid, null,
              e(Stat, { label: "Input", value: tokens(t.total_input) }),
              e(Stat, { label: "Output", value: tokens(t.total_output) }),
              e(Stat, { label: "Cache read", value: tokens(t.total_cache_read) }),
              e(Stat, { label: "Reasoning", value: tokens(t.total_reasoning) }),
              e(Stat, {
                label: "Total tokens",
                value: tokens(totalTokens),
                tone: "accent"
              }),
              e(Stat, {
                label: "API calls", value: num(t.total_api_calls)
              }),
              e(Stat, {
                label: "Estimated cost",
                value: money(t.total_estimated_cost),
                tone: "warn"
              }),
              e(Stat, {
                label: "Actual cost",
                value: money(t.total_actual_cost)
              }),
              e(Stat, {
                label: "Avg / call", value: avgPerCall ? tokens(avgPerCall) : DASH
              }),
              e(Stat, { label: "Sessions measured",
                        value: num(t.total_sessions) })
            ),
            noUsage
              ? e("div", { className: "hud-state hud-state--empty",
                           style: { marginTop: "var(--hud-s3)" } },
                  e("div", { className: "hud-state__title" },
                    "No usage recorded yet"),
                  e("div", { className: "hud-state__detail" },
                    "The analytics route returned an empty period: no daily " +
                    "or per-model rows and no API calls. Every zero above is " +
                    "the route's own answer, not a failed read."))
              : null
          ),
          days > 0
            ? e(Panel, {
                title: "Daily trend", wide: true,
                hint: num(days) + " days"
              },
                daily.slice(-14).map(function (row, i) {
                  var r = row || {};
                  var tt = (isNum(r.input_tokens) ? r.input_tokens : 0) +
                           (isNum(r.output_tokens) ? r.output_tokens : 0);
                  return e(Bar, {
                    key: "day:" + (r.day || i),
                    label: text(r.day),
                    value: tt,
                    max: maxDay,
                    ok: true,
                    display: tokens(tt) + " \u00b7 " +
                      money(r.estimated_cost) + " \u00b7 " +
                      num(r.sessions) + " sess"
                  });
                }),
                e("div", { className: "hud__panelHint",
                           style: { marginTop: "var(--hud-s2)" } },
                  days > 14
                    ? "showing the 14 most recent of " + num(days) + " days"
                    : "bar = input + output tokens")
              )
            : null,
          byModel.length
            ? e(Panel, {
                title: "By model", wide: true,
                hint: num(byModel.length) + " models"
              },
                byModel.map(function (row, i) {
                  var r = row || {};
                  var tt = (isNum(r.input_tokens) ? r.input_tokens : 0) +
                           (isNum(r.output_tokens) ? r.output_tokens : 0);
                  /* The analytics route does not carry a provider on these
                   * rows, and the same model id legitimately appears more than
                   * once (different providers, or an "unknown" bucket), so no
                   * provider is invented. The row index is shown instead, which
                   * keeps duplicate ids distinct without claiming which provider
                   * each one is. */
                  return e(Bar, {
                    key: "model:" + i,
                    label: "#" + (i + 1) + " " + label(r.model),
                    value: tt,
                    max: maxModel,
                    display: tokens(tt) + " \u00b7 " + money(r.estimated_cost) +
                      " \u00b7 " + num(r.api_calls) + " calls"
                  });
                }),
                e("div", { className: "hud__panelHint",
                           style: { marginTop: "var(--hud-s2)" } },
                  "Rows are the route's own per-model records in its own order; " +
                  "the route carries no provider field, so a repeated model id " +
                  "is not merged or attributed here.")
              )
            : e(StateBox, {
                kind: "empty", title: "No per-model usage",
                route: P.analytics,
                detail: "The route answered with an empty `by_model` list for " +
                  "this window."
              })
        );
      }
    });

    return e(React.Fragment, null, statsPanel, anPanel);
  }

  /* ======================================================================
   * 13. THE TABS — one table, so the strip and the body cannot disagree.
   * ==================================================================== */
  var TABS = [
    { id: "health", label: "Health", route: P.health, View: HealthTab },
    { id: "sessions", label: "Sessions", route: P.sessions, View: SessionsTab },
    { id: "cron", label: "Cron", route: P.cronJobs, View: CronTab },
    { id: "models", label: "Models", route: P.modelOptions, View: ModelsTab },
    { id: "skills", label: "Skills", route: P.skills, View: SkillsTab },
    { id: "plugins", label: "Plugins", route: P.plugins, View: PluginsTab },
    { id: "costs", label: "Costs", route: P.analytics, View: CostsTab }
  ];

  /* Roving focus across the tab strip: Left/Right move, Home/End jump. The
   * strip is a real `tablist`, so a screen reader announces position. */
  function onTabKey(ev, index, setActive) {
    var n = TABS.length;
    var next = -1;
    if (ev.key === "ArrowRight") next = (index + 1) % n;
    else if (ev.key === "ArrowLeft") next = (index - 1 + n) % n;
    else if (ev.key === "Home") next = 0;
    else if (ev.key === "End") next = n - 1;
    if (next < 0) return;
    ev.preventDefault();
    setActive(TABS[next].id);
    var el = ev.currentTarget.parentNode &&
      ev.currentTarget.parentNode.querySelectorAll(".hud__tab")[next];
    if (el && el.focus) el.focus();
  }

  function Hud(props) {
    var a = useState("health");
    var active = a[0], setActive = a[1];
    var tab = TABS[0];
    for (var i = 0; i < TABS.length; i++) {
      if (TABS[i].id === active) tab = TABS[i];
    }
    var View = tab.View;
    return e("div", { className: "hud" },
      e("header", { className: "hud__head" },
        e("div", { className: "hud__title" },
          e("span", { className: "hud__name" }, "HUD"),
          e("span", { className: "hud__subtitle" },
            "Health, sessions, cron, models, skills, plugins and cost \u2014 " +
            "read from Qubit's own routes, read-only.")
        ),
        e("div", { className: "hud__headRight" },
          e("span", { className: "hud__stamp" }, "GET only")
        )
      ),
      e("div", {
        className: "hud__tabs",
        role: "tablist",
        "aria-label": "HUD sections"
      },
        TABS.map(function (t, idx) {
          var on = t.id === active;
          return e("button", {
            key: t.id,
            type: "button",
            role: "tab",
            id: "hud-tab-" + t.id,
            "aria-selected": on ? "true" : "false",
            "aria-controls": "hud-panel-" + t.id,
            tabIndex: on ? 0 : -1,
            className: "hud__tab" + (on ? " hud__tab--on" : ""),
            onClick: function () { setActive(t.id); },
            onKeyDown: function (ev) { onTabKey(ev, idx, setActive); }
          }, t.label);
        })
      ),
      e("div", {
        className: "hud__body",
        role: "tabpanel",
        id: "hud-panel-" + tab.id,
        "aria-labelledby": "hud-tab-" + tab.id,
        tabIndex: 0
      },
        /* Keyed on the tab id so switching unmounts the previous tab fully:
         * its reads stop and its interval is cleared. */
        e("div", { key: tab.id, className: "hud__grid" },
          e(View, null)
        )
      ),
      e("footer", { className: "hud__foot" },
        e("span", null,
          "Read-only. Every request is a GET through the dashboard's own " +
          "authenticated client \u2014 no POST, PUT or DELETE originates here."),
        e("span", { className: "hud__footDim" },
          "A failed route names itself; nothing is shown in place of data " +
          "that did not arrive.")
      )
    );
  }

  window.__QUBIT_PLUGINS__.register("qubit-hud", Hud);
})();
