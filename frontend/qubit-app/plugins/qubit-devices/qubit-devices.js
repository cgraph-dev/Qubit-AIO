/* Qubit Devices — overlay plugin bundle (dependency-free IIFE).
 * Browser / desktop / IDE / vision readiness + pinned runtime inventory.
 * Backend: GET /api/ops/runtime and POST /api/ops/run on the shared
 * dashboard_ops broker (same as CLI and voice). No fabricated status:
 * every card renders the real typed backend result. */
(function () {
  var SDK = window.__HERMES_PLUGIN_SDK__;
  if (!SDK || !window.__HERMES_PLUGINS__) return;
  var React = SDK.React;
  var hooks = SDK.hooks;
  var fetchJSON = SDK.fetchJSON;
  var authedFetch = SDK.authedFetch;
  var e = React.createElement;

  var DEVICE_GROUPS = ["browser", "browser_tabs", "computer", "ghost", "ide",
    "acp", "editor", "lsp", "vision_status", "vision_describe", "wake",
    "audio", "audio_hw", "mic_guidance"];

  var S = {
    wrap: {padding: "16px 20px", maxWidth: 1100},
    h: {margin: "0 0 4px", fontSize: 20},
    sub: {opacity: 0.72, margin: "0 0 14px", fontSize: 13},
    card: {border: "1px solid #2a3443", borderRadius: 8, padding: "10px 12px",
           marginBottom: 10},
    btn: {padding: "6px 11px", cursor: "pointer", background: "#1b2634",
          color: "inherit", border: "1px solid #33445a", borderRadius: 6},
    code: {background: "#0c1118", border: "1px solid #2a3443",
           borderRadius: 6, padding: 8, overflowX: "auto",
           whiteSpace: "pre-wrap", fontSize: 12, maxHeight: 220},
    badge: {fontSize: 11, padding: "1px 6px", borderRadius: 999,
            border: "1px solid #33445a", marginLeft: 6, opacity: 0.85},
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

  function Devices() {
    var rst = hooks.useState(null); var runtime = rst[0], setRuntime = rst[1];
    var cst = hooks.useState(null); var ops = cst[0], setOps = cst[1];
    var vst = hooks.useState({}); var results = vst[0], setResults = vst[1];
    var est = hooks.useState(""); var err = est[0], setErr = est[1];
    var wst = hooks.useState(""); var working = wst[0], setWorking = wst[1];

    function probe(op) {
      setWorking(op.id);
      runOp(op.id, {}).then(function (r) {
        var next = Object.assign({}, results);
        next[op.id] = r;
        setResults(next);
        setWorking("");
      }).catch(function (ex) {
        var next = Object.assign({}, results);
        next[op.id] = {status: 0, body: {ok: false, code: "NETWORK",
          detail: String((ex && ex.message) || ex)}};
        setResults(next);
        setWorking("");
      });
    }

    hooks.useEffect(function () {
      fetchJSON("/api/ops/runtime").then(function (res) {
        setRuntime((res && (res.result || res)) || {});
      }).catch(function (ex) {
        setErr(String((ex && ex.message) || ex));
      });
      fetchJSON("/api/ops").then(function (res) {
        var all = (res && res.ops) || [];
        setOps(all.filter(function (o) {
          return DEVICE_GROUPS.indexOf(o.group) >= 0 ||
                 DEVICE_GROUPS.indexOf(o.id) >= 0;
        }));
      }).catch(function (ex) {
        setErr(String((ex && ex.message) || ex));
      });
    }, []);

    var images = (runtime && (runtime.images || runtime.runtimes)) || [];
    return e("div", {style: S.wrap},
      e("h1", {style: S.h}, "Devices & Runtimes"),
      e("p", {style: S.sub},
        "Truthful availability for browser, desktop, IDE, vision, and pinned ",
        "runtimes — same backend as CLI `qubit runtime` / device ops and voice."),
      err ? e("div", {role: "alert", "data-state": "degraded",
        style: {color: "#e5484d"}}, err) : null,
      e("div", {style: S.card},
        e("strong", null, "Pinned runtimes"),
        images.length ? images.map(function (img, i) {
          return e("div", {key: i, style: {fontSize: 13, marginTop: 4}},
            e("code", null, img.name || img.id || "runtime"),
            e("span", {style: S.badge}, img.state || "?") ,
            " pin " + (img.pin || "?") +
            (img.observed && img.observed.effective ? " · effective" : ""));
        }) : e("div", {"data-state": runtime ? "empty" : "loading",
          "aria-busy": runtime ? "false" : "true",
          style: {opacity: 0.7, fontSize: 13, marginTop: 4}},
          runtime ? "No runtimes staged." : "Loading runtime inventory…")),
      e("h2", {style: {fontSize: 15, marginTop: 18}}, "Device operations"),
      !ops ? e("p", {"data-state": "loading", "aria-busy": "true"},
          "Loading device operations…") :
        ops.map(function (op) {
          var r = results[op.id];
          return e("div", {key: op.id, style: S.card},
            e("div", {style: {display: "flex", gap: 8,
                alignItems: "center", flexWrap: "wrap"}},
              e("strong", {style: {fontSize: 14}}, op.title || op.id),
              e("span", {style: S.badge}, op.group || "device"),
              e("span", {style: {flex: "1 1 auto"}}),
              e("button", {style: S.btn, disabled: working === op.id,
                "aria-label": "Check " + op.id,
                onClick: function () { probe(op); }},
                working === op.id ? "Checking…" : "Check")),
            e("div", {style: {opacity: 0.65, fontSize: 12}},
              (op.help || "").slice(0, 120)),
            r ? e("pre", {style: Object.assign({}, S.code, {marginTop: 8})},
              JSON.stringify(r.body, null, 2).slice(0, 3000)) : null);
        }));
  }

  window.__HERMES_PLUGINS__.register("qubit-devices", Devices);
})();
