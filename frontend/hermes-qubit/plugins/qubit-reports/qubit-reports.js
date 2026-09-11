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

    hooks.useEffect(function () {
      fetchJSON("/api/logs").then(setLogs).catch(function (ex) {
        setErr(String((ex && ex.message) || ex));
      });
      fetchJSON("/api/analytics").then(setAnalytics).catch(function (ex) {
        setErr(String((ex && ex.message) || ex));
      });
      runOp("reports_list", {}).then(function (r) { setReports(r); })
        .catch(function (ex) { setErr(String((ex && ex.message) || ex)); });
      runOp("insights", {}).then(function (r) { setInsights(r); })
        .catch(function () { /* insights optional */ });
    }, []);

    var lines = (logs && logs.lines) || [];
    var totals = (analytics && analytics.totals) || {};
    return e("div", {style: S.wrap},
      e("h1", {style: S.h}, "Reports & Activity"),
      e("p", {style: S.sub},
        "Live event log, usage totals, generated reports, and insights — ",
        "all journal-derived on the shared Qubit backend (CLI/voice parity)."),
      err ? e("div", {role: "alert", style: {color: "#e5484d"}},
        err) : null,
      e("div", {style: S.card},
        e("strong", null, "Usage totals"),
        analytics ? e("div", {style: {display: "flex", gap: 18,
            flexWrap: "wrap", marginTop: 8, fontSize: 13}},
          spanKV("Sessions", totals.total_sessions),
          spanKV("API calls", totals.total_api_calls),
          spanKV("Input tokens", totals.total_input),
          spanKV("Output tokens", totals.total_output),
          spanKV("Cost", totals.total_estimated_cost)) :
          e("div", {style: {opacity: 0.7, fontSize: 13}},
            "Loading analytics…")),
      e("div", {style: S.card},
        e("strong", null, "Recent activity"),
        lines.length ? e("pre", {style: {marginTop: 8, fontSize: 12,
            whiteSpace: "pre-wrap", maxHeight: 260, overflowY: "auto"}},
          lines.slice(-60).map(function (ln) {
            return typeof ln === "string" ? ln : JSON.stringify(ln);
          }).join("\n")) :
          e("div", {style: {opacity: 0.7, fontSize: 13}},
            logs ? "No events yet." : "Loading events…")),
      e("div", {style: S.card},
        e("strong", null, "Generated reports"),
        reports ? e("pre", {style: S.code},
          JSON.stringify(reports.body, null, 2).slice(0, 4000)) :
          e("div", {style: {opacity: 0.7, fontSize: 13}},
            "Loading reports…")),
      e("div", {style: S.card},
        e("strong", null, "Insights"),
        insights ? e("pre", {style: S.code},
          JSON.stringify(insights.body, null, 2).slice(0, 4000)) :
          e("div", {style: {opacity: 0.7, fontSize: 13}},
            "Loading insights…")));
  }

  function spanKV(label, value) {
    return e("span", {key: label},
      e("span", {style: {opacity: 0.65}}, label + ": "),
      e("strong", null, String(value === undefined ? "—" : value)));
  }

  window.__HERMES_PLUGINS__.register("qubit-reports", Reports);
})();
