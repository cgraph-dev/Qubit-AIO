/* Qubit Models & Providers — overlay plugin bundle (dependency-free IIFE).
 * Uses only window.__HERMES_PLUGIN_SDK__ (React, hooks, fetchJSON).
 * Every control calls the canonical resolver through the shared dashboard
 * op-dispatch (POST /api/ops/run -> voicebridge.dashboard_ops.run_op ->
 * voicebridge.model_profiles_ops -> providers/resolver). Reads execute;
 * mutations stage on the shared confirmation journal and settle through
 * POST /api/ops/confirm. Credentials are secret references only
 * (env:VAR/file:PATH): a raw value is refused typed, and key_ref is never
 * journaled. No second store, no second listener, no fabricated success. */
(function () {
  var SDK = window.__HERMES_PLUGIN_SDK__;
  if (!SDK) return;
  var React = SDK.React, hooks = SDK.hooks, fetchJSON = SDK.fetchJSON;
  var C = SDK.components || {};
  var e = React.createElement;

  var KINDS = ["local", "api", "realtime"];
  var LOCALITIES = ["local", "external"];
  var EGRESS = ["deny", "allow"];
  var ROLES = ["chat", "hard", "coding", "tool", "summarize", "vision",
    "classify"];

  function detailOf(res) {
    if (!res || typeof res !== "object") return {};
    // A read/write op result may be top-level (res.result) or nested in the
    // settle envelope (res.receipt.result) for /api/ops/confirm. Unwrap both so
    // a typed refusal is never rendered as a success.
    if (res.result && typeof res.result === "object") return res.result;
    if (res.receipt && res.receipt.result &&
        typeof res.receipt.result === "object") return res.receipt.result;
    return res;
  }

  function isTypedState(value) {
    return value === "REFUSED" || value === "FAILED" ||
      value === "DEGRADED" || value === "UNAVAILABLE";
  }

  function parseStagedError(ex) {
    var msg = String((ex && ex.message) || ex || "");
    var at = msg.indexOf("{");
    if (at < 0) return null;
    try {
      var body = JSON.parse(msg.slice(at));
      if (body && (body.confirmation_id || body.detail && body.code ===
          "CONFIRM_REQUIRED")) {
        return {id: body.confirmation_id, detail: body.detail || "",
          state: body.state || "NEEDS_CONFIRMATION"};
      }
    } catch (parseEx) { /* not a staged envelope */ }
    return null;
  }

  function Models() {
    var pst = hooks.useState([]);
    var profiles = pst[0], setProfiles = pst[1];
    var sst = hooks.useState(null);
    var shown = sst[0], setShown = sst[1];
    var tst = hooks.useState(null);
    var tested = tst[0], setTested = tst[1];
    var est = hooks.useState("");
    var err = est[0], setErr = est[1];
    var nst = hooks.useState("");
    var notice = nst[0], setNotice = nst[1];
    var cst = hooks.useState(null);
    var pending = cst[0], setPending = cst[1];
    var bst = hooks.useState(false);
    var busy = bst[0], setBusy = bst[1];

    var sess = hooks.useState("dashboard");
    var sessRole = hooks.useState("chat");
    var target = hooks.useState("");

    var fId = hooks.useState("");
    var fKind = hooks.useState("local");
    var fProv = hooks.useState("ollama");
    var fModel = hooks.useState("");
    var fBase = hooks.useState("");
    var fKey = hooks.useState("");
    var fLoc = hooks.useState("local");
    var fEgress = hooks.useState("deny");
    var fCtx = hooks.useState("4096");
    var fTools = hooks.useState("false");
    var fVis = hooks.useState("false");
    var fRoles = hooks.useState("");

    function load() {
      setBusy(true); setErr("");
      fetchJSON("/api/ops/run", {
        method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({id: "list_model_profiles", args: {}}),
      }).then(function (res) {
        setBusy(false);
        var inner = detailOf(res);
        setProfiles((inner && inner.profiles) || []);
      }).catch(function (ex) {
        setBusy(false);
        setErr("list_model_profiles: " +
          String((ex && ex.message) || ex));
      });
    }

    hooks.useEffect(function () { load(); }, []);

    function clearFeed() { setNotice(""); setTested(null); }

    function runOp(opId, args, what, sensitive) {
      setBusy(true); setErr(""); setPending(null); clearFeed();
      fetchJSON("/api/ops/run", {
        method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({id: opId, args: args || {}}),
      }).then(function (res) {
        setBusy(false);
        var inner = detailOf(res);
        if (inner && isTypedState(inner.state)) {
          setNotice("[" + (inner.code || inner.state) + "] " +
            (inner.detail || "typed state") + " — use Retry to refresh.");
        } else {
          setNotice((what || opId) + ": " +
            ((inner && (inner.state || inner.action)) || "done"));
        }
        load();
        showProfile(target[0] || fId[0]);
      }).catch(function (ex) {
        var staged = parseStagedError(ex);
        setBusy(false);
        if (staged && staged.id) {
          setPending({id: staged.id, what: what, detail: staged.detail,
            sensitive: sensitive || null});
        } else {
          setErr("[" + (what || opId) + "] " +
            String((ex && ex.message) || ex) +
            " — Retry to refresh, or reset to defaults.");
        }
      });
    }

    function confirmPending(accept) {
      if (!pending) return;
      var body = {confirmation_id: pending.id};
      if (accept && pending.sensitive && pending.sensitive.key_ref) {
        body.sensitive = {key_ref: pending.sensitive.key_ref};
      }
      setBusy(true); setErr("");
      fetchJSON("/api/ops/" + (accept ? "confirm" : "cancel"), {
        method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify(body),
      }).then(function (res) {
        setBusy(false);
        setPending(null);
        if (res && res.ok) {
          var inner = detailOf(res);
          if (inner && isTypedState(inner.state)) {
            setNotice("[" + (inner.code || inner.state) + "] " +
              (inner.detail || "typed state"));
          } else {
            setNotice(accept ? "Confirmed: done" : "Cancelled");
          }
          load();
        } else if (res && res.state && res.state !== "executed") {
          setNotice("[" + (res.code || res.state) + "] " +
            (res.detail || "settle refused"));
        } else {
          setNotice("Settled");
          load();
        }
      }).catch(function (ex) {
        setBusy(false);
        setErr(String((ex && ex.message) || ex));
      });
    }

    function showProfile(id) {
      var pid = (id || "").trim();
      if (!pid) { setErr("[show] profile id required"); return; }
      setBusy(true); setErr(""); clearFeed();
      fetchJSON("/api/ops/run", {
        method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({id: "show_model_profile",
          args: {profile_id: pid}}),
      }).then(function (res) {
        setBusy(false);
        setShown(detailOf(res));
      }).catch(function (ex) {
        setBusy(false);
        setShown(null);
        setErr("[show] " + String((ex && ex.message) || ex));
      });
    }

    function testProfile(id) {
      var pid = (id || "").trim();
      if (!pid) { setErr("[test] profile id required"); return; }
      setBusy(true); setErr(""); clearFeed();
      fetchJSON("/api/ops/run", {
        method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({id: "test_model_profile",
          args: {profile_id: pid, probe: "false"}}),
      }).then(function (res) {
        setBusy(false);
        setTested(detailOf(res));
      }).catch(function (ex) {
        setBusy(false);
        setTested(null);
        setErr("[test] " + String((ex && ex.message) || ex));
      });
    }

    function selOpts(list) {
      return list.map(function (v) {
        return e("option", {key: v, value: v}, v);
      });
    }

    function field(label, node) {
      return e("label", {style: {display: "flex", flexDirection: "column",
        gap: 2, fontSize: 12}}, label, node);
    }

    var selectedId = target[0] || fId[0];

    return e("div", {style: {padding: 16, maxWidth: 1080}},
      e("h2", null, "Models & Providers"),
      e("p", {style: {opacity: 0.75}},
        "Canonical model and provider profiles — the same resolver the CLI ",
        "and voice use. Locality and egress are disclosed per profile; ",
        "credentials are references only (env:VAR or file:PATH)."),
      e("div", {role: "region", "aria-label": "Model profile session",
        style: {display: "flex", gap: 8, flexWrap: "wrap", alignItems: "end",
          marginBottom: 8}},
        field("Session", e(C.Input || "input",
          {"aria-label": "Session id", value: sess[0],
            onChange: function (ev) { sess[1](ev.target.value); },
            placeholder: "dashboard", style: {maxWidth: 160}})),
        field("Role", e("select", {"aria-label": "Session role", value: sessRole[0],
          onChange: function (ev) { sessRole[1](ev.target.value); }},
          selOpts(ROLES))),
        field("Profile", e(C.Input || "input",
          {"aria-label": "Target profile", value: target[0],
            onChange: function (ev) { target[1](ev.target.value); },
            placeholder: "qubit-brain4", style: {maxWidth: 180}})),
        e(C.Button || "button", {"aria-label": "Select profile for session",
          disabled: busy || !target[0],
          onClick: function () {
            runOp("select_session_model",
              {session: sess[0], profile_id: target[0], role: sessRole[0]},
              "select session profile " + target[0]);
          }}, "Select for session"),
        e(C.Button || "button", {"aria-label": "Clear session model override",
          disabled: busy || !sess[0],
          onClick: function () {
            runOp("clear_session_model",
              {session: sess[0], role: sessRole[0]},
              "clear session " + sess[0]);
          }}, "Clear override")),

      err ? e("div", {role: "alert", "data-state": "degraded",
        style: {color: "#e5484d", marginTop: 6}},
        err, " ",
        e("button", {onClick: function () { load(); }}, "Retry")) : null,
      notice ? e("div", {"data-state": "info", role: "status",
        style: {marginTop: 6, padding: "6px 10px", border: "1px solid #3a6",
          borderRadius: 8, fontSize: 13}}, notice) : null,
      pending ? e("div", {role: "region",
        "aria-label": "Pending model profile confirmation",
        "data-state": "pending",
        style: {marginTop: 8, padding: "8px 12px", border: "1px solid #b7791f",
          borderRadius: 8, fontSize: 13}},
        e("strong", null, "Confirm " + pending.what + ": "),
        e("span", null, String(pending.detail || "").slice(0, 160)),
        pending.sensitive && pending.sensitive.key_ref
          ? e("div", {style: {fontSize: 12, opacity: 0.8}},
              "The secret reference is re-supplied at confirm; it is never ",
              "written to the journal.")
          : null,
        e("div", {style: {marginTop: 6, display: "flex", gap: 8}},
          e(C.Button || "button", {"aria-label": "Confirm model profile change",
            disabled: busy, onClick: function () { confirmPending(true); }},
            busy ? "Working…" : "Confirm"),
          e(C.Button || "button", {"aria-label": "Cancel model profile change",
            disabled: busy, onClick: function () { confirmPending(false); }},
            "Cancel"))) : null,

      e("h3", {style: {marginTop: 16}},
        "Profiles (" + profiles.length + ")"),
      !profiles.length
        ? e("p", {"data-state": "empty", style: {opacity: 0.6}},
            busy ? "Loading profiles…" : "No profiles returned.")
        : e("table", {style: {borderCollapse: "collapse", width: "100%",
          fontSize: 13}},
            e("thead", null, e("tr", null,
              ["ID", "Kind", "Provider", "Model", "Locality", "Egress",
               "Gate", "Default", ""].map(function (h) {
                return e("th", {key: h, style: {textAlign: "left",
                  borderBottom: "1px solid", paddingRight: 8}}, h);
              }))),
            e("tbody", null, profiles.map(function (p) {
              var id = p.id;
              return e("tr", {key: id, "data-profile": id},
                e("td", null, id),
                e("td", null, p.kind || "—"),
                e("td", null, p.provider || "—"),
                e("td", null, p.model || "—"),
                e("td", {"data-state": p.locality === "external"
                  ? "external" : "local"}, p.locality || "—"),
                e("td", null, p.egress_consent || "—"),
                e("td", null, p.gate || "—"),
                e("td", null, p.default ? "yes" : ""),
                e("td", null,
                  e("button", {"aria-label": "Show profile " + id,
                    disabled: busy,
                    onClick: function () { target[1](id); showProfile(id); }},
                    "Show"),
                  e("button", {"aria-label": "Test profile " + id,
                    disabled: busy, style: {marginLeft: 4},
                    onClick: function () { testProfile(id); }}, "Test"),
                  e("button", {"aria-label": "Clone profile " + id,
                    disabled: busy, style: {marginLeft: 4},
                    onClick: function () {
                      var nid = window.prompt("New profile id", id + "-copy");
                      if (nid) runOp("clone_model_profile",
                        {source_id: id, new_id: nid},
                        "clone " + id + " -> " + nid);
                    }}, "Clone"),
                  e("button", {"aria-label": "Rollback profile " + id,
                    disabled: busy, style: {marginLeft: 4},
                    onClick: function () {
                      runOp("rollback_model_profile",
                        {profile_id: id, session: sess[0],
                         role: sessRole[0]}, "rollback " + id);
                    }}, "Rollback"),
                  e("button", {"aria-label": "Remove profile " + id,
                    disabled: busy || p.default, style: {marginLeft: 4},
                    onClick: function () {
                      runOp("remove_model_profile", {profile_id: id},
                        "remove " + id);
                    }}, "Remove")));
            }))),

      shown ? e("div", {"data-state": "detail",
        "aria-label": "Profile detail",
        style: {marginTop: 16, padding: 12, border: "1px solid #556",
          borderRadius: 8}},
        e("h3", null, "Profile " + (shown.profile && shown.profile.id ||
          selectedId)),
        e("p", {style: {margin: "4px 0"}},
          "Kind: " + ((shown.profile && shown.profile.kind) || "?") +
          " · Provider: " + ((shown.effective && shown.effective.provider) ||
            "?") + " · Route: " + ((shown.effective &&
            shown.effective.route) || "?")),
        e("p", {style: {margin: "4px 0"},
          "data-state": (shown.effective && shown.effective.executable)
            ? "executable" : "unavailable"},
          "Executable: " + ((shown.effective && shown.effective.executable)
            ? "yes" : "no") +
          " · Egress required: " + ((shown.effective &&
            shown.effective.egress_required) ? "yes" : "no") +
          " · Egress allowed: " + ((shown.effective &&
            shown.effective.egress_allowed) ? "yes" : "no") +
          " · Allowlisted: " + ((shown.effective &&
            shown.effective.allowlisted) ? "yes" : "no")),
        e("p", {style: {margin: "4px 0"}},
          "Capabilities: ", e("code", null, JSON.stringify(
            (shown.effective && shown.effective.capabilities) || {})),
          "  Budgets: ", e("code", null, JSON.stringify(
            (shown.effective && shown.effective.budgets) || {}))),
        e("p", {style: {margin: "4px 0"}},
          "Fallback chain: ", e("code", null, JSON.stringify(
            (shown.effective && shown.effective.fallback_chain) || [])),
          "  Rollback: ", e("code", null,
            (shown.effective && shown.effective.rollback_to) || "—")),
        e("p", {style: {margin: "4px 0"}},
          "Gate: " + ((shown.gate && shown.gate.state) || "?") + " " +
          ((shown.gate && shown.gate.detail) || ""))) : null,

      tested ? e("div", {"data-state": "test", "aria-label": "Profile test result",
        style: {marginTop: 12, padding: 10, border: "1px dashed #556",
          borderRadius: 8, fontSize: 13}},
        e("strong", null, "Test " + (tested.profile || selectedId) + ": "),
        tested.state || "?",
        tested.profile ? null : null,
        e("span", null, " · gate " + ((tested.gate && tested.gate.state) || "?"),
          " · executable " + ((tested.effective &&
            tested.effective.executable) ? "yes" : "no")),
        tested.probe_error ? e("div", {role: "alert"},
          "probe error: " + tested.probe_error) : null,
        tested.runner_error ? e("div", {role: "alert"},
          "runner error: " + tested.runner_error) : null) : null,

      e("h3", {style: {marginTop: 20}}, "Create profile"),
      e("p", {style: {fontSize: 12, opacity: 0.75}},
        "Non-secret fields only. A key is an env:VAR or file:PATH reference; ",
        "a raw value is refused."),
      e("div", {style: {display: "flex", gap: 8, flexWrap: "wrap",
        alignItems: "end"}},
        field("ID", e(C.Input || "input", {"aria-label": "New profile id",
          value: fId[0], onChange: function (ev) { fId[1](ev.target.value); },
          placeholder: "research-api", style: {maxWidth: 150}})),
        field("Kind", e("select", {"aria-label": "New profile kind",
          value: fKind[0], onChange: function (ev) {
            fKind[1](ev.target.value);
            fLoc[1](ev.target.value === "local" ? "local" : "external");
          }}, selOpts(KINDS))),
        field("Provider", e(C.Input || "input",
          {"aria-label": "New profile provider", value: fProv[0],
            onChange: function (ev) { fProv[1](ev.target.value); },
            style: {maxWidth: 120}})),
        field("Model", e(C.Input || "input",
          {"aria-label": "New profile model", value: fModel[0],
            onChange: function (ev) { fModel[1](ev.target.value); },
            placeholder: "defaults to id", style: {maxWidth: 160}})),
        field("Base URL", e(C.Input || "input",
          {"aria-label": "New profile base url", value: fBase[0],
            onChange: function (ev) { fBase[1](ev.target.value); },
            placeholder: "https://...", style: {maxWidth: 180}})),
        field("Key ref", e(C.Input || "input",
          {"aria-label": "New profile key ref", value: fKey[0],
            onChange: function (ev) { fKey[1](ev.target.value); },
            placeholder: "env:PROVIDER_KEY", style: {maxWidth: 180}})),
        field("Locality", e("select", {"aria-label": "New profile locality",
          value: fLoc[0], onChange: function (ev) {
            fLoc[1](ev.target.value); }}, selOpts(LOCALITIES))),
        field("Egress", e("select", {"aria-label": "New profile egress",
          value: fEgress[0], onChange: function (ev) {
            fEgress[1](ev.target.value); }}, selOpts(EGRESS))),
        field("Context", e(C.Input || "input",
          {"aria-label": "New profile context tokens", value: fCtx[0],
            onChange: function (ev) { fCtx[1](ev.target.value); },
            style: {maxWidth: 90}})),
        field("Tools", e("select", {"aria-label": "New profile tools",
          value: fTools[0], onChange: function (ev) {
            fTools[1](ev.target.value); }}, selOpts(["false", "true"]))),
        field("Vision", e("select", {"aria-label": "New profile vision",
          value: fVis[0], onChange: function (ev) {
            fVis[1](ev.target.value); }}, selOpts(["false", "true"]))),
        field("Roles", e(C.Input || "input",
          {"aria-label": "New profile roles", value: fRoles[0],
            onChange: function (ev) { fRoles[1](ev.target.value); },
            placeholder: "chat,coding", style: {maxWidth: 160}})),
        e(C.Button || "button", {"aria-label": "Create model profile",
          disabled: busy || !fId[0],
          onClick: function () {
            var args = {profile_id: fId[0].trim(), kind: fKind[0],
              provider: fProv[0], model: fModel[0], base_url: fBase[0],
              locality: fLoc[0], egress_consent: fEgress[0],
              context_tokens: fCtx[0], supports_tools: fTools[0],
              supports_vision: fVis[0], roles: fRoles[0]};
            var keyRef = (fKey[0] || "").trim();
            if (keyRef) args.key_ref = keyRef;
            runOp("create_model_profile", args,
              "create profile " + args.profile_id,
              {key_ref: keyRef});
          }}, "Create")));
  }
  window.__HERMES_PLUGINS__.register("qubit-models", Models);
})();
