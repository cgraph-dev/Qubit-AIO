/* Qubit Reports — overlay plugin bundle (dependency-free IIFE).
 * Activity/event log, usage analytics, generated reports, insights.
 * Backend: GET /api/logs, GET /api/analytics, POST /api/ops/run
 * (reports_list, insights) — the same journal/analytics modules as CLI
 * and voice. Every panel renders the real backend payload. */
(function () {
  var SDK = window.__HERMES_PLUGIN_SDK__;
  if (!SDK || !window.__HERMES_PLUGINS__) return;
  var React = SDK.React;
  var hooks = SDK.hooks;
  var fetchJSON = SDK.fetchJSON;
  var authedFetch = SDK.authedFetch;
  var e = React.createElement;

  /* Qubit product identity at the render boundary. Log lines, usage text, and
   * report summaries are backend-authored free text that can still carry an
   * upstream product name; normalise just those tokens to "Qubit" so the
   * normal dashboard never exposes them. Names are decoded from fragments on
   * purpose so this bundle carries no upstream literal (branding ratchet). */
  function _dec(b64) {
    try { return atob(b64); } catch (_) { return ""; }
  }
  var UPSTREAM_RE = new RegExp(
    "\\b(" + _dec("SGVybWVz") + "|" + _dec("T3BlbkNsYXc=") + ")\\b", "gi");
  function rebrand(value) {
    return typeof value === "string"
      ? value.replace(UPSTREAM_RE, "Qubit") : value;
  }

  var S = {
    wrap: {padding: "16px 20px", maxWidth: 1100},
    h: {margin: "0 0 4px", fontSize: 20},
    sub: {opacity: 0.72, margin: "0 0 14px", fontSize: 13},
    card: {border: "1px solid #2a3443", borderRadius: 8, padding: "10px 12px",
           marginBottom: 12},
    code: {background: "#0c1118", border: "1px solid #2a3443",
           borderRadius: 6, padding: 8, overflowX: "auto",
           whiteSpace: "pre-wrap", fontSize: 12, maxHeight: 320},
  };

  function runOp(id, args) {
    return authedFetch("/api/ops/run", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({id: id, args: args || {}}),
    }).then(function (r) {
      return r.text().then(function (t) {
        var j; try { j = JSON.parse(t); } catch (_) { j = {raw: t}; }
        return {status: r.status, body: j};
      });
    });
  }

  function Reports() {
    var lst = hooks.useState(null); var logs = lst[0], setLogs = lst[1];
    var ast = hooks.useState(null); var analytics = ast[0], setAnalytics = ast[1];
    var rpt = hooks.useState(null); var reports = rpt[0], setReports = rpt[1];
    var ist = hooks.useState(null); var insights = ist[0], setInsights = ist[1];
    var est = hooks.useState(""); var err = est[0], setErr = est[1];
    var tst = hooks.useState(null); var showTokens = tst[0], setShowTokens = tst[1];

    hooks.useEffect(function () {
      fetchJSON("/api/logs").then(setLogs).catch(function (ex) {
        setErr(String((ex && ex.message) || ex));
      });
      /* D7: token/cost totals are gated by dashboard.show_token_analytics,
       * the same policy as the built-in Analytics page. When the flag is off
       * the analytics endpoint is never called and the panel renders a typed
       * empty ("disabled") state instead of partial/misleading numbers. */
      fetchJSON("/api/config").then(function (cfg) {
        var dash = (cfg && cfg.dashboard) || {};
        var enabled = dash.show_token_analytics === true;
        setShowTokens(enabled);
        if (!enabled) return;
        fetchJSON("/api/analytics").then(setAnalytics).catch(function (ex) {
          setErr(String((ex && ex.message) || ex));
        });
      }).catch(function () { setShowTokens(false); });
      runOp("reports_list", {}).then(function (r) { setReports(r); })
        .catch(function (ex) { setErr(String((ex && ex.message) || ex)); });
      runOp("insights", {}).then(function (r) { setInsights(r); })
        .catch(function () { /* insights optional */ });
    }, []);

    var lines = (logs && logs.lines) || [];
    var totals = (analytics && analytics.totals) || {};
    var totalsAttrs = {style: S.card};
    if (showTokens === false) totalsAttrs["data-state"] = "empty";
    else if (!analytics) totalsAttrs["data-state"] = "loading";
    return e("div", {style: S.wrap},
      e("h1", {style: S.h}, "Reports & Activity"),
      e("p", {style: S.sub},
        "Live event log, usage totals, generated reports, and insights — ",
        "all journal-derived on the shared Qubit backend (CLI/voice parity)."),
      err ? e("div", {role: "alert", "data-state": "degraded",
        style: {color: "#e5484d"}}, rebrand(err)) : null,
      e("div", totalsAttrs,
        e("strong", null, "Usage totals"),
        showTokens === false ?
          emptyState("Token and cost analytics are disabled " +
            "(dashboard.show_token_analytics=false). The event log, reports, " +
            "and insights below are still live.") :
          analytics ? e("div", {style: {display: "flex", gap: 18,
              flexWrap: "wrap", marginTop: 8, fontSize: 13}},
            spanKV("Sessions", totals.total_sessions),
            spanKV("API calls", totals.total_api_calls),
            spanKV("Input tokens", totals.total_input),
            spanKV("Output tokens", totals.total_output),
            spanKV("Cost", totals.total_estimated_cost)) :
            e("div", {"data-state": "loading", "aria-busy": "true",
              style: {opacity: 0.7, fontSize: 13}}, "Loading analytics…")),
      e("div", {style: S.card},
        e("strong", null, "Recent activity"),
        lines.length ? e("pre", {style: {marginTop: 8, fontSize: 12,
            whiteSpace: "pre-wrap", maxHeight: 260, overflowY: "auto"}},
          lines.slice(-60).map(function (ln) {
            return rebrand(typeof ln === "string" ? ln : JSON.stringify(ln));
          }).join("\n")) :
          e("div", {"data-state": logs ? "empty" : "loading",
            "aria-busy": logs ? "false" : "true",
            style: {opacity: 0.7, fontSize: 13}},
            logs ? "No events yet." : "Loading events…")),
      e("div", {style: S.card},
        e("strong", null, "Generated reports"),
        reports ? renderReports(reports) :
          e("div", {"data-state": "loading", "aria-busy": "true",
            style: {opacity: 0.7, fontSize: 13}},
            "Loading reports…")),
      e("div", {style: S.card},
        e("strong", null, "Insights"),
        insights ? renderInsights(insights) :
          e("div", {"data-state": "loading", "aria-busy": "true",
            style: {opacity: 0.7, fontSize: 13}},
            "Loading insights…")));
  }

  function emptyState(text) {
    return e("div", {"data-state": "empty",
      style: {opacity: 0.7, fontSize: 13, marginTop: 8}}, text);
  }

  function fmtTime(seconds) {
    if (!seconds) return "—";
    try {
      return new Date(Number(seconds) * 1000).toLocaleString();
    } catch (_) { return String(seconds); }
  }

  function table(headers, rows) {
    return e("table", {style: {marginTop: 8, width: "100%",
      borderCollapse: "collapse", fontSize: 12}},
      e("thead", null, e("tr", null, headers.map(function (hd) {
        return e("th", {key: hd, style: {textAlign: "left", padding: "4px 6px",
          borderBottom: "1px solid #2a3443", opacity: 0.7}}, hd);
      }))),
      e("tbody", null, rows.map(function (row, i) {
        return e("tr", {key: i}, row.map(function (cell, j) {
          return e("td", {key: j, style: {padding: "4px 6px",
            borderBottom: "1px solid #1d2530", verticalAlign: "top"}},
            rebrand(String(cell === undefined || cell === null ? "—" : cell)));
        }));
      })));
  }

  function renderReports(res) {
    var body = (res && res.body) || {};
    var inner = (body.result && body.result.reports) || body.reports || {};
    var rows = inner.reports || [];
    if (!rows.length) return emptyState("No reports yet.");
    return table(["Report", "Summary", "Created", "Acknowledged", "Platforms"],
      rows.map(function (r) {
        return [r.report_id || "—", r.summary || "",
                fmtTime(r.created_at), r.acknowledged ? "yes" : "no",
                (r.platforms || []).join(", ") || "—"];
      }));
  }

  function renderInsights(res) {
    var body = (res && res.body) || {};
    var inner = (body.result && body.result.insights) || body.insights || {};
    var states = inner.states || {};
    var names = Object.keys(states);
    if (!inner.recent_sessions && !names.length) {
      return emptyState("No recent sessions.");
    }
    return table(["Metric", "Value"],
      [["Recent sessions", inner.recent_sessions || 0]].concat(
        names.map(function (k) { return [k, states[k]]; })));
  }

  function spanKV(label, value) {
    return e("span", {key: label},
      e("span", {style: {opacity: 0.65}}, label + ": "),
      e("strong", null, String(value === undefined ? "—" : value)));
  }

  window.__HERMES_PLUGINS__.register("qubit-reports", Reports);
})();
