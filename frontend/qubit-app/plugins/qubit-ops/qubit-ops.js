/* Qubit Operations — overlay plugin bundle (dependency-free IIFE).
 * Uses only window.__QUBIT_PLUGIN_SDK__ (React, hooks, fetchJSON,
 * authedFetch). Backend: GET /api/ops (catalog), POST /api/ops/run
 * (dashboard_ops.run_op — the SAME broker as CLI and voice), with
 * staged confirmations settled on the shared journal via
 * POST /api/ops/confirm|cancel. No frontend-only success: every control
 * surfaces the real backend state. */
(function () {
  var SDK = window.__QUBIT_PLUGIN_SDK__;
  if (!SDK || !window.__QUBIT_PLUGINS__) return;
  var React = SDK.React;
  var hooks = SDK.hooks;
  var fetchJSON = SDK.fetchJSON;
  var authedFetch = SDK.authedFetch;
  var e = React.createElement;

  var S = {
    wrap: {padding: "16px 20px", maxWidth: 1180},
    h: {margin: "0 0 4px", fontSize: 20},
    sub: {opacity: 0.72, margin: "0 0 14px", fontSize: 13},
    bar: {display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 12},
    col: {display: "flex", gap: 16, alignItems: "flex-start",
          flexWrap: "wrap"},
    panel: {flex: "1 1 420px", minWidth: 300},
    list: {maxHeight: 460, overflowY: "auto", border: "1px solid #2a3443",
           borderRadius: 8},
    row: {padding: "8px 10px", borderBottom: "1px solid #222c39",
          cursor: "pointer"},
    input: {padding: "7px 9px", width: "100%", boxSizing: "border-box",
            marginBottom: 8, background: "#0c1118", color: "inherit",
            border: "1px solid #2a3443", borderRadius: 6},
    btn: {padding: "7px 12px", cursor: "pointer", background: "#1b2634",
          color: "inherit", border: "1px solid #33445a", borderRadius: 6},
    code: {background: "#0c1118", border: "1px solid #2a3443",
           borderRadius: 6, padding: 10, overflowX: "auto",
           whiteSpace: "pre-wrap", fontSize: 12, maxHeight: 320},
    badge: {fontSize: 11, padding: "1px 6px", borderRadius: 999,
            border: "1px solid #33445a", marginLeft: 6, opacity: 0.85},
  };

  function call(path, init) {
    return authedFetch(path, init).then(function (r) {
      return r.text().then(function (t) {
        var j;
        try { j = JSON.parse(t); } catch (_) { j = {raw: t}; }
        return {status: r.status, body: j};
      });
    });
  }

  function postJS(path, obj) {
    return call(path, {method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(obj || {})});
  }

  function groupOf(op) { return op.group || "other"; }

  function Operations() {
    var cst = hooks.useState(null); var catalog = cst[0], setCatalog = cst[1];
    var est = hooks.useState(""); var err = est[0], setErr = est[1];
    var qst = hooks.useState(""); var query = qst[0], setQuery = qst[1];
    var gst = hooks.useState("all"); var group = gst[0], setGroup = gst[1];
    var sst = hooks.useState(null); var sel = sst[0], setSel = sst[1];
    var ast = hooks.useState({}); var args = ast[0], setArgs = ast[1];
    var rst = hooks.useState(null); var result = rst[0], setResult = rst[1];
    var pst = hooks.useState([]); var pending = pst[0], setPending = pst[1];
    var wst = hooks.useState(""); var working = wst[0], setWorking = wst[1];

    function refreshPending() {
      fetchJSON("/api/ops/approvals").then(function (res) {
        setPending((res && res.approvals) || []);
      }).catch(function () { /* banner quiet; page shows typed errors inline */ });
    }

    hooks.useEffect(function () {
      fetchJSON("/api/ops").then(function (res) {
        setCatalog((res && res.ops) || []);
        setErr("");
      }).catch(function (ex) {
        setErr(String((ex && ex.message) || ex));
      });
      refreshPending();
      var t = setInterval(refreshPending, 5000);
      return function () { clearInterval(t); };
    }, []);

    if (err && !catalog) {
      return e("div", {style: S.wrap}, e("h1", {style: S.h}, "Operations"),
        e("div", {role: "alert", "data-state": "degraded",
          style: {color: "#e5484d"}}, err));
    }
    if (!catalog) {
      return e("div", {style: S.wrap}, e("h1", {style: S.h}, "Operations"),
        e("p", {"data-state": "loading", "aria-busy": "true"},
          "Loading operations catalog…"));
    }

    var groups = ["all"];
    catalog.forEach(function (o) {
      if (groups.indexOf(groupOf(o)) < 0) groups.push(groupOf(o));
    });
    var q = query.trim().toLowerCase();
    var visible = catalog.filter(function (o) {
      if (group !== "all" && groupOf(o) !== group) return false;
      if (!q) return true;
      return (o.id + " " + (o.title || "") + " " +
        (o.help || "") + " " + (o.aliases || []).join(" ")).toLowerCase()
        .indexOf(q) >= 0;
    }).slice(0, 200);

    function openOp(op) {
      setSel(op);
      var a = {};
      (op.args || []).forEach(function (arg) { a[arg.name] = ""; });
      setArgs(a);
      setResult(null);
    }

    function runOp() {
      if (!sel || working) return;
      setWorking("run");
      setResult(null);
      var coerced = {};
      (sel.args || []).forEach(function (arg) {
        var v = args[arg.name];
        if (v === "" || v === undefined) return;
        if (arg.type === "int") {
          var n = parseInt(v, 10);
          coerced[arg.name] = isNaN(n) ? v : n;
        } else if (arg.type === "float") {
          var f = parseFloat(v);
          coerced[arg.name] = isNaN(f) ? v : f;
        } else {
          coerced[arg.name] = v;
        }
      });
      postJS("/api/ops/run", {id: sel.id, args: coerced}).then(function (r) {
        setResult(r);
        if (r.status === 409) refreshPending();
        setWorking("");
      }).catch(function (ex) {
        setResult({status: 0, body: {ok: false, code: "NETWORK",
                                     detail: String((ex && ex.message) || ex)}});
        setWorking("");
      });
    }

    function settle(id, action) {
      setWorking(id + ":" + action);
      postJS("/api/ops/" + action, {confirmation_id: id}).then(function () {
        refreshPending();
        setWorking("");
      }).catch(function (ex) {
        setErr(String((ex && ex.message) || ex));
        setWorking("");
      });
    }

    return e("div", {style: S.wrap},
      e("h1", {style: S.h}, "Operations"),
      e("p", {style: S.sub},
        "Every capability op runs on the shared Qubit broker — the same ",
        "dashboard_ops registry as CLI `qubit …` and voice. Confirmed ",
        "mutations stage on the shared approvals journal."),

      pending.length ? e("div", {role: "region",
          "aria-label": "Pending confirmations",
          "data-state": "pending",
          style: {border: "1px solid #b7791f", borderRadius: 8,
                  padding: "8px 12px", marginBottom: 14,
                  background: "rgba(183,121,31,0.08)"}},
        e("strong", null, pending.length + " pending confirmation" +
          (pending.length === 1 ? "" : "s")),
        pending.slice(0, 8).map(function (a) {
          var busy = working && working.indexOf(a.id) === 0;
          return e("div", {key: a.id, style: {display: "flex", gap: 8,
            alignItems: "center", marginTop: 6, flexWrap: "wrap"}},
            e("span", {style: {flex: "1 1 260px"}},
              e("code", null, String(a.capability || "").slice(0, 48)), " — ",
              String(a.summary || "").slice(0, 120)),
            e("button", {style: S.btn, disabled: !!busy,
              "aria-label": "Confirm " + (a.capability || a.id),
              onClick: function () { settle(a.id, "confirm"); }},
              busy ? "Working…" : "Confirm"),
            e("button", {style: S.btn, disabled: !!busy,
              "aria-label": "Cancel " + (a.capability || a.id),
              onClick: function () { settle(a.id, "cancel"); }}, "Cancel"));
        })
      ) : null,

      e("div", {style: S.bar},
        e("input", {style: Object.assign({}, S.input, {maxWidth: 320,
            marginBottom: 0}), placeholder: "Search operations…",
          value: query, "aria-label": "Search operations",
          onChange: function (ev) { setQuery(ev.target.value); }}),
        e("select", {style: Object.assign({}, S.input, {maxWidth: 220,
            marginBottom: 0}), value: group, "aria-label": "Filter by group",
          onChange: function (ev) { setGroup(ev.target.value); }},
          groups.map(function (g) {
            return e("option", {key: g, value: g}, g);
          })),
        e("span", {style: {alignSelf: "center", opacity: 0.7, fontSize: 12}},
          visible.length + " / " + catalog.length + " ops")),

      e("div", {style: S.col},
        e("div", {style: S.panel},
          e("div", {style: S.list, role: "list"},
            visible.map(function (op) {
              var active = sel && sel.id === op.id;
              return e("div", {key: op.id, role: "listitem", style:
                  Object.assign({}, S.row, active ?
                    {background: "#16202c"} : null),
                onClick: function () { openOp(op); }},
                e("div", {style: {fontSize: 13}},
                  e("strong", null, op.title || op.id),
                  op.confirm ? e("span", {style: S.badge}, "confirm") : null,
                  op.voice_guard ? e("span", {style: S.badge,
                    title: "spoken form: " + op.voice_guard}, "voice: linked")
                    : null,
                  e("span", {style: S.badge}, groupOf(op))),
                e("div", {style: {opacity: 0.65, fontSize: 12}},
                  op.id + " — " + (op.help || "").slice(0, 90)));
            }))),

        e("div", {style: S.panel},
          !sel ? e("p", {"data-state": "empty", style: {opacity: 0.7}},
              "Select an operation to configure and run it.") :
          e("div", null,
            e("h2", {style: {fontSize: 16, margin: "0 0 2px"}},
              sel.title || sel.id),
            e("p", {style: {opacity: 0.7, fontSize: 12, marginTop: 0}},
              sel.id, " · group ", groupOf(sel)),
            sel.interface_reason ? e("p", {style: {fontSize: 12,
                color: "#d29922", marginTop: 0},
              "data-state": "degraded"},
              (/^voice\b/i.test(sel.interface_reason) ? "" : "Voice: ") +
              sel.interface_reason) : null,
            (sel.args || []).length ? (sel.args || []).map(function (arg) {
              return e("label", {key: arg.name, style: {display: "block"}},
                e("span", {style: {fontSize: 12, opacity: 0.8}},
                  arg.name + (arg.required ? " *" : ""),
                  arg.clarifier ? " — " + arg.clarifier : ""),
                e("input", {style: S.input, value: args[arg.name] || "",
                  placeholder: arg.type || "text",
                  type: arg.secret ? "password" : "text",
                  autoComplete: arg.secret ? "new-password" : "off",
                  "aria-label": arg.name,
                  onChange: function (ev) {
                    var next = Object.assign({}, args);
                    next[arg.name] = ev.target.value;
                    setArgs(next);
                  }}));
            }) : e("p", {"data-state": "empty",
              style: {opacity: 0.7, fontSize: 13}}, "No arguments."),
            e("button", {style: S.btn, disabled: working === "run",
              "aria-label": "Run " + sel.id,
              onClick: runOp},
              working === "run" ? "Running…" : "Run operation"),
            result ? e("div", {style: {marginTop: 12},
              "data-state": result.status === 409 ? "pending"
                : (result.status >= 400 ? "degraded" : "ready")},
              e("div", {role: result.status >= 400 ? "alert" : "status",
                  "aria-live": "polite",
                  style: {fontSize: 13, marginBottom: 6,
                    color: result.status >= 400 ? "#e5484d" : "#3fb950"}},
                "HTTP " + result.status + " · " +
                (result.body.state || result.body.code || "ok")),
              result.status === 409 && result.body.confirmation_id ?
                e("div", {style: {marginBottom: 8}},
                  e("button", {style: S.btn, onClick: function () {
                      settle(result.body.confirmation_id, "confirm"); }},
                    "Confirm"),
                  " ",
                  e("button", {style: S.btn, onClick: function () {
                      settle(result.body.confirmation_id, "cancel"); }},
                    "Cancel")) : null,
              e("pre", {style: S.code},
                JSON.stringify(result.body, null, 2).slice(0, 6000))) : null,
            err ? e("div", {role: "alert",
              style: {color: "#e5484d", marginTop: 8, fontSize: 13}},
              err) : null))));
  }

  window.__QUBIT_PLUGINS__.register("qubit-ops", Operations);
})();
