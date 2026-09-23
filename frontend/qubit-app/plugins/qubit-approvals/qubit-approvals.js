/* Qubit Approvals — overlay plugin bundle (dependency-free IIFE).
 * Uses only window.__HERMES_PLUGIN_SDK__ (React, hooks, fetchJSON).
 * Shell-wide pending-confirmation banner (header-banner slot) on the
 * SHARED journal: the same records CLI `qubit approvals`, voice yes/no,
 * and the classic dashboard settle. Confirm/Cancel POST the compat
 * endpoints, which run OPS.confirm_op/cancel_op — one permission /
 * confirmation path, settle-once, audited, redacted. Renders nothing
 * when nothing is pending. Staged mutations answer 409 (never 2xx) so a
 * pending item can never look executed before it is. */
(function () {
  var SDK = window.__HERMES_PLUGIN_SDK__;
  if (!SDK || !window.__HERMES_PLUGINS__) return;
  var React = SDK.React;
  var hooks = SDK.hooks;
  var fetchJSON = SDK.fetchJSON;
  var e = React.createElement;

  function ApprovalsBanner() {
    var st = hooks.useState([]);
    var items = st[0], setItems = st[1];
    var est = hooks.useState("");
    var err = est[0], setErr = est[1];
    var bst = hooks.useState(null);
    var working = bst[0], setWorking = bst[1];

    function refresh() {
      fetchJSON("/api/ops/approvals").then(function (res) {
        setItems((res && res.approvals) || []);
        setErr("");
      }).catch(function (ex) {
        /* Poll failures stay quiet unless the operator opens the flow:
         * a banner that errors loudly on every poll would spam the shell.
         * The last error is kept for the next settle attempt. */
        setErr(String((ex && ex.message) || ex));
      });
    }

    hooks.useEffect(function () {
      refresh();
      var t = setInterval(refresh, 5000);
      return function () { clearInterval(t); };
    }, []);

    function settle(id, action) {
      setWorking(id + ":" + action);
      setErr("");
      fetchJSON("/api/ops/" + action, {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({confirmation_id: id}),
      }).then(function (res) {
        if (res && res.ok) {
          setItems(function (prev) {
            return prev.filter(function (a) { return a.id !== id; });
          });
        } else {
          setErr("[" + (res && (res.code || res.state)) + "] " +
            ((res && res.detail) || (action + " failed")));
          refresh();
        }
        setWorking(null);
      }).catch(function (ex) {
        setErr(String((ex && ex.message) || ex));
        setWorking(null);
        refresh();
      });
    }

    if (!items.length) return null;
    return e("div", {role: "region", "aria-label": "Pending confirmations",
      "data-state": "pending",
      style: {margin: "8px 12px 0", padding: "8px 12px",
        border: "1px solid #b7791f", borderRadius: 8, fontSize: 13,
        background: "rgba(183,121,31,0.08)"}},
      e("strong", null, "Qubit: " + items.length + " pending confirmation" +
        (items.length === 1 ? "" : "s") + " — "),
      e("span", {style: {opacity: 0.8}},
        "same journal as CLI approvals and voice yes/no. "),
      items.slice(0, 5).map(function (a) {
        var busy = working && working.indexOf(a.id) === 0;
        return e("div", {key: a.id, style: {marginTop: 6, display: "flex",
          gap: 8, alignItems: "center", flexWrap: "wrap"}},
          e("span", {style: {flex: "1 1 240px"}},
            e("code", null, String(a.capability || "").slice(0, 48)), " — ",
            String(a.summary || "").slice(0, 140)),
          e("button", {
            onClick: function () { settle(a.id, "confirm"); },
            disabled: !!busy,
            "aria-label": "Confirm " + String(a.capability || a.id),
          }, busy ? "Working…" : "Confirm"),
          e("button", {
            onClick: function () { settle(a.id, "cancel"); },
            disabled: !!busy,
            "aria-label": "Cancel " + String(a.capability || a.id),
          }, "Cancel"));
      }),
      err ? e("div", {role: "alert", "data-state": "degraded",
        style: {color: "#e5484d", marginTop: 6}}, err) : null);
  }

  function ApprovalsPage() {
    var st = hooks.useState([]);
    var items = st[0], setItems = st[1];
    var est = hooks.useState("");
    var err = est[0], setErr = est[1];
    var bst = hooks.useState(null);
    var working = bst[0], setWorking = bst[1];

    function refresh() {
      fetchJSON("/api/ops/approvals").then(function (res) {
        setItems((res && res.approvals) || []);
        setErr("");
      }).catch(function (ex) {
        setErr(String((ex && ex.message) || ex));
      });
    }

    hooks.useEffect(function () {
      refresh();
      var t = setInterval(refresh, 5000);
      return function () { clearInterval(t); };
    }, []);

    function settle(id, action) {
      setWorking(id + ":" + action);
      setErr("");
      fetchJSON("/api/ops/" + action, {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({confirmation_id: id}),
      }).then(function () {
        setItems(function (prev) {
          return prev.filter(function (a) { return a.id !== id; });
        });
        setWorking(null);
      }).catch(function (ex) {
        setErr(String((ex && ex.message) || ex));
        setWorking(null);
        refresh();
      });
    }

    return e("div", {style: {padding: "16px 20px", maxWidth: 900}},
      e("h1", {style: {margin: "0 0 4px", fontSize: 20}}, "Approvals & Security"),
      e("p", {style: {opacity: 0.72, margin: "0 0 14px", fontSize: 13}},
        "Pending confirmations on the shared journal — the same records CLI ",
        "`qubit approvals`, voice yes/no, and the operations console settle. ",
        "Confirm executes once with a redacted audit receipt."),
      err ? e("div", {role: "alert", "data-state": "degraded",
        style: {color: "#e5484d", marginBottom: 10}}, err) : null,
      !items.length ? e("p", {"data-state": "empty", style: {opacity: 0.7}},
          "No pending confirmations.") :
        e("div", {role: "list", style: {border: "1px solid #2a3443",
          borderRadius: 8}},
          items.map(function (a) {
            var busy = working && working.indexOf(a.id) === 0;
            return e("div", {key: a.id, role: "listitem",
              style: {display: "flex", gap: 10, alignItems: "center",
                padding: "10px 12px", borderBottom: "1px solid #222c39",
                flexWrap: "wrap"}},
              e("span", {style: {flex: "1 1 300px"}},
                e("strong", null, String(a.capability || "").slice(0, 48)),
                e("div", {style: {opacity: 0.7, fontSize: 12}},
                  String(a.summary || "").slice(0, 220))),
              e("button", {disabled: !!busy,
                "aria-label": "Confirm " + String(a.capability || a.id),
                onClick: function () { settle(a.id, "confirm"); }},
                busy ? "Working…" : "Confirm"),
              e("button", {disabled: !!busy,
                "aria-label": "Cancel " + String(a.capability || a.id),
                onClick: function () { settle(a.id, "cancel"); }}, "Cancel"));
          })));
  }

  if (window.__HERMES_PLUGINS__.registerSlot) {
    window.__HERMES_PLUGINS__.registerSlot(
      "qubit-approvals", "header-banner", ApprovalsBanner);
  }
  window.__HERMES_PLUGINS__.register("qubit-approvals", ApprovalsPage);
})();
