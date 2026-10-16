/* Agent Quest — office2 plugin bundle (Qubit addition, dependency-free IIFE).
 *
 * WHAT THIS PAGE IS.
 * A real-time gamified dashboard for monitoring coding agents, following the
 * concept of FulAppiOS/Agent-Quest (MIT): context-window gauges, skill and
 * plugin telemetry, and subagent families. The upstream project is a Bun
 * server + client application; its client could not be built or vendored in
 * this environment, so this page is AUTHORED by Qubit against the shared
 * plugin SDK rather than being a copy of upstream code. The provenance notice
 * rendered at the bottom of the page says exactly this, in the UI, where a
 * reader can see it — it is not left only in a JSON file.
 *
 * WHAT THIS PAGE IS NOT.
 * It is not a live telemetry feed. The dashboard values below are STATIC
 * PLACEHOLDERS. They are labelled as placeholders in the page (a banner plus a
 * per-panel tag), and no timer, fetch or animation is used to make them look
 * live. There is no fabricated agent data presented as real: every number
 * carries the word "placeholder". A future revision can wire these panels to a
 * real endpoint; until it does, the page states its own honesty rather than
 * implying a feed that does not exist.
 *
 * CONTRACT. Uses only window.__QUBIT_PLUGIN_SDK__ (React, hooks) and registers
 * through window.__QUBIT_PLUGINS__.register, matching how qubit-office.js
 * registers. Payload strings render as React children only — no innerHTML, no
 * dangerouslySetInnerHTML. No network request is made.
 */
(function () {
  var SDK = window.__QUBIT_PLUGIN_SDK__;
  if (!SDK || !window.__QUBIT_PLUGINS__) return;
  var React = SDK.React, hooks = SDK.hooks;
  var C = SDK.components || {};
  var e = React.createElement;

  /* --- the three panels' placeholder data ---------------------------------
   * These arrays are authored constants. They are NOT produced by a poll, a
   * socket or a fetch; the page labels every one of them as a placeholder. The
   * shape mirrors what a real Agent-Quest feed would provide (id, label, a 0..1
   * context load, a state), so wiring a real source later is a data swap, not
   * a rewrite. ----------------------------------------------------------------- */
  var AGENTS = [
    {id: "agent-alpha", label: "Feature work — editor", family: "builder",
     context: 0.62, used: 124, limit: 200, state: "active", skills: 7, plugins: 3},
    {id: "agent-beta", label: "Test sweep — web", family: "verifier",
     context: 0.88, used: 176, limit: 200, state: "active", skills: 4, plugins: 2},
    {id: "agent-gamma", label: "Docs pass — API", family: "writer",
     context: 0.31, used: 62, limit: 200, state: "idle", skills: 2, plugins: 1},
    {id: "agent-delta", label: "Refactor — engine", family: "builder",
     context: 0.95, used: 190, limit: 200, state: "near-limit", skills: 9, plugins: 4},
    {id: "agent-epsilon", label: "Repro — bug 412", family: "verifier",
     context: 0.47, used: 94, limit: 200, state: "idle", skills: 3, plugins: 1},
    {id: "agent-zeta", label: "Scrape — changelog", family: "researcher",
     context: 0.73, used: 146, limit: 200, state: "active", skills: 5, plugins: 2}
  ];

  /* state -> token name in qubit-office2.css, so the colour lives in the
   * stylesheet and not in this file. */
  var STATE_TOKEN = {
    "active": "--aq-ok",
    "idle": "--aq-muted",
    "near-limit": "--aq-warn",
    "blocked": "--aq-bad"
  };

  var PLACEHOLDER_TAG = "placeholder data";

  function pct(v) { return Math.round(v * 100) + "%"; }

  /* --- a context-window gauge --------------------------------------------
   * A bar plus a caption. The bar is a plain div width, so there is no timer,
   * no canvas and no animation — the fill is a function of the datum alone. */
  function ContextGauge(a) {
    var row = a.row;
    var token = STATE_TOKEN[row.state] || "--aq-muted";
    var barStyle = {
      width: pct(row.context),
      background: "var(" + token + ")"
    };
    return e("div", {className: "aq-gauge", key: row.id,
                     title: row.label + " \u00b7 " + row.state},
      e("div", {className: "aq-gauge__head"},
        e("span", {className: "aq-gauge__label"}, row.label),
        e("span", {className: "aq-gauge__pct"}, pct(row.context))),
      e("div", {className: "aq-gauge__track"},
        e("div", {className: "aq-gauge__fill", style: barStyle})),
      e("div", {className: "aq-gauge__meta"},
        row.used + " / " + row.limit + " tokens \u00b7 " + row.state +
        " \u00b7 " + row.family),
      e("div", {className: "aq-gauge__tag"}, PLACEHOLDER_TAG));
  }

  /* --- skill / plugin telemetry ------------------------------------------
   * Two small tables: skills and plugins observed across the families, with a
   * use count. Authored constants, tagged as placeholders. */
  function TelemetryTable(a) {
    var rows = a.rows;
    return e("table", {className: "aq-table"},
      e("thead", null,
        e("tr", null,
          e("th", null, a.columns[0]),
          e("th", null, a.columns[1]),
          e("th", {className: "aq-table__num"}, a.columns[2]))),
      e("tbody", null,
        rows.map(function (r) {
          return e("tr", {key: r.name},
            e("td", null, r.name),
            e("td", {className: "aq-table__muted"}, r.kind),
            e("td", {className: "aq-table__num"}, String(r.uses)));
        })));
  }

  var SKILLS = [
    {name: "code-review", kind: "skill", uses: 18},
    {name: "unit-test", kind: "skill", uses: 12},
    {name: "doc-writer", kind: "skill", uses: 7},
    {name: "web-scrape", kind: "skill", uses: 5},
    {name: "browser-automation", kind: "plugin", uses: 9},
    {name: "vector-search", kind: "plugin", uses: 6},
    {name: "lint-fix", kind: "plugin", uses: 3}
  ];

  /* --- subagent families --------------------------------------------------
   * The fleet grouped by family, with a member count. Clicking does nothing:
   * this page reads and states; it stages no operation. */
  var FAMILIES = [
    {name: "builder", members: 2, note: "Feature and refactor work."},
    {name: "verifier", members: 2, note: "Tests, repros and review."},
    {name: "writer", members: 1, note: "Docs and prose surfaces."},
    {name: "researcher", members: 1, note: "Scraping and discovery."}
  ];

  /* --- the page ----------------------------------------------------------- */
  function AgentQuest() {
    var state = hooks.useState(null);
    var noteSeen = state[0], setNoteSeen = state[1];

    var gauges = React.useMemo(function () {
      return AGENTS.map(function (row) {
        return e(ContextGauge, {row: row, key: row.id});
      });
    }, []);

    var nearLimit = AGENTS.filter(function (r) { return r.context >= 0.85; }).length;

    return e("div", {className: "aq-page"},

      /* --- header --- */
      e("header", {className: "aq-head"},
        e("div", null,
          e("h1", {className: "aq-title"}, "Agent Quest"),
          e("p", {className: "aq-sub"},
            "A real-time gamified dashboard for monitoring coding agents \u2014 " +
            "context-window gauges, skill and plugin telemetry, and subagent " +
            "families.")),
        e("div", {className: "aq-head__stats"},
          e("span", {className: "aq-pill"}, AGENTS.length + " agents"),
          e("span", {className: "aq-pill"}, FAMILIES.length + " families"),
          e("span", {className: "aq-pill aq-pill--warn"},
            nearLimit + " near context limit"))),

      /* --- the honesty banner, before any number --- */
      e("div", {className: "aq-banner"},
        e("strong", null, "Static preview. "),
        "Every value on this page is placeholder data authored for layout, " +
        "not live telemetry. Nothing here is fetched, polled or streamed. " +
        "Agent-Quest (MIT, FulAppiOS) is a Bun server + client application " +
        "whose client could not be bundled in this environment, so this page " +
        "implements the concept as an authored Qubit surface."),

      /* --- context-window gauges --- */
      e("section", {className: "aq-panel"},
        e("div", {className: "aq-panel__head"},
          e("h2", {className: "aq-panel__title"}, "Context windows"),
          e("span", {className: "aq-tag"}, PLACEHOLDER_TAG)),
        e("div", {className: "aq-gauges"}, gauges)),

      /* --- skill / plugin telemetry --- */
      e("section", {className: "aq-panel"},
        e("div", {className: "aq-panel__head"},
          e("h2", {className: "aq-panel__title"}, "Skill and plugin telemetry"),
          e("span", {className: "aq-tag"}, PLACEHOLDER_TAG)),
        e(TelemetryTable, {rows: SKILLS,
                           columns: ["Name", "Kind", "Uses"]})),

      /* --- subagent families --- */
      e("section", {className: "aq-panel"},
        e("div", {className: "aq-panel__head"},
          e("h2", {className: "aq-panel__title"}, "Subagent families"),
          e("span", {className: "aq-tag"}, PLACEHOLDER_TAG)),
        e("div", {className: "aq-families"},
          FAMILIES.map(function (f) {
            return e("div", {className: "aq-family", key: f.name},
              e("div", {className: "aq-family__name"}, f.name),
              e("div", {className: "aq-family__count"},
                f.members + (f.members === 1 ? " member" : " members")),
              e("div", {className: "aq-family__note"}, f.note));
          }))),

      /* --- provenance, always visible, collapsible in appearance only --- */
      e("section", {className: "aq-provenance"},
        e("h2", {className: "aq-provenance__title"}, "Provenance"),
        e("p", {className: "aq-provenance__body"},
          "Agent-Quest (", e("code", null, "https://github.com/FulAppiOS/Agent-Quest"),
          ") is MIT licensed (Copyright (c) 2026 Fulvio Schilone). It is a " +
          "Bun + TypeScript server + client application: only its client could " +
          "ever be bundled as a static asset, and it could not be built here."),
        e("p", {className: "aq-provenance__body"},
          "This page is therefore an AUTHORED Qubit surface that implements the " +
          "Agent-Quest concept. No upstream file is copied; no server is run. " +
          "See ", e("code", null, "PROVENANCE.json"), " in this plugin " +
          "directory for the pinned commit and the full note."),
        noteSeen
          ? e("p", {className: "aq-provenance__ack"},
              "Acknowledged. This surface remains a static preview.")
          : e("button", {className: "aq-btn",
                         onClick: function () { setNoteSeen(true); }},
              "Acknowledge")));
  }

  window.__QUBIT_PLUGINS__.register("qubit-office2", AgentQuest);
})();
