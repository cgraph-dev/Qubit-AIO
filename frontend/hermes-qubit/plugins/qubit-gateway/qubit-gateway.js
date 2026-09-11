/* Qubit Gateway — overlay plugin bundle (OpenClaw addition, dependency-free IIFE).
 * Uses only window.__HERMES_PLUGIN_SDK__ (React, hooks, fetchJSON).
 * Messaging accounts + gateway records on the canonical modules (same
 * voicebridge/messaging.py + gateway_peers.py as CLI and voice): add
 * account, add gateway, typed link check, remove. Records only — process
 * supervision stays refused by policy (typed GATEWAY_NO_SUPERVISION via
 * the voice refusal op). Secrets never travel as values: key_ref accepts
 * env:VAR/file:PATH references only (raw values fail fast, typed, before
 * anything is staged); outputs carry the kind only. Mutations run the
 * shared confirmation journal: 409 staged + inline Confirm/Cancel. */
(function () {
  var SDK = window.__HERMES_PLUGIN_SDK__;
  if (!SDK) return;
  var React = SDK.React, hooks = SDK.hooks, fetchJSON = SDK.fetchJSON;
  var C = SDK.components || {};
  var e = React.createElement;

  var PROVIDERS = ["whatsapp", "whatsapp-cloud", "slack"];
  var CHANNELS = ["webhook", "polling", "websocket"];

  function parseStagedError(ex) {
    var msg = String((ex && ex.message) || ex || "");
    var at = msg.indexOf("{");
    if (at < 0) return null;
    try {
      var body = JSON.parse(msg.slice(at));
      if (body && body.state === "NEEDS_CONFIRMATION" && body.confirmation_id) {
        return {id: body.confirmation_id, detail: body.detail || ""};
      }
    } catch (parseEx) { /* not a staged envelope */ }
    return null;
  }

  function Gateway() {
    var st = hooks.useState([]);
    var accounts = st[0], setAccounts = st[1];
    var gst = hooks.useState([]);
    var gateways = gst[0], setGateways = gst[1];
    var est = hooks.useState("");
    var err = est[0], setErr = est[1];
    var pst = hooks.useState(null);
    var pending = pst[0], setPending = pst[1];
    var bst = hooks.useState(false);
    var busy = bst[0], setBusy = bst[1];
    var lst = hooks.useState({});
    var links = lst[0], setLinks = lst[1];

    var aAid = hooks.useState("");
    var aProv = hooks.useState(PROVIDERS[0]);
    var aLabel = hooks.useState("");
    var aKey = hooks.useState("");
    var gId = hooks.useState("");
    var gProv = hooks.useState(PROVIDERS[0]);
    var gAcct = hooks.useState("");
    var gChan = hooks.useState(CHANNELS[0]);
    var gKey = hooks.useState("");

    function load() {
      fetchJSON("/api/ops/msg-accounts").then(function (res) {
        var inner = (res && res.result) || {};
        setAccounts(inner.accounts || []);
      }).catch(function (ex) {
        setErr(String((ex && ex.message) || ex));
      });
      fetchJSON("/api/ops/gateways").then(function (res) {
        var inner = (res && res.result) || {};
        setGateways(inner.gateways || []);
      }).catch(function (ex) {
        setErr(String((ex && ex.message) || ex));
      });
    }

    hooks.useEffect(function () { load(); }, []);

    function runMutation(url, body, what) {
      if (busy) return;
      setBusy(true); setErr(""); setPending(null);
      fetchJSON(url, {method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify(body)}).then(function () {
        setBusy(false);
        load();
      }).catch(function (ex) {
        var staged = parseStagedError(ex);
        setBusy(false);
        if (staged) {
          setPending({id: staged.id, what: what, detail: staged.detail});
        } else {
          setErr(String((ex && ex.message) || ex));
        }
      });
    }

    function settlePending(confirm) {
      if (!pending) return;
      var id = pending.id;
      setBusy(true); setErr("");
      fetchJSON("/api/ops/" + (confirm ? "confirm" : "cancel"), {
        method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({confirmation_id: id}),
      }).then(function (res) {
        setBusy(false);
        setPending(null);
        if (res && res.ok) {
          load();
        } else {
          setErr("[" + (res && (res.code || res.state)) + "] " +
            ((res && res.detail) || "settle failed"));
        }
      }).catch(function (ex) {
        setBusy(false);
        setErr(String((ex && ex.message) || ex));
      });
    }

    function checkLink(id) {
      setErr("");
      fetchJSON("/api/ops/gateway-link?gateway_id=" + encodeURIComponent(id))
        .then(function (res) {
          var inner = (res && res.result) || res || {};
          setLinks(function (prev) {
            var next = {};
            for (var k in prev) next[k] = prev[k];
            next[id] = inner.state || "?";
            return next;
          });
        }).catch(function (ex) {
          setErr(String((ex && ex.message) || ex));
        });
    }

    function selOpts(list) {
      return list.map(function (v) {
        return e("option", {key: v, value: v}, v);
      });
    }

    return e("div", {style: {padding: 16, maxWidth: 960}},
      e("h2", null, "Gateway"),
      e("p", {style: {opacity: 0.75}},
        "Account-bound records with typed link states — same backend as CLI and voice. ",
        "Process supervision is refused by policy; key references only (env:VAR/file:PATH)."),
      err ? e("div", {role: "alert", style: {color: "#e5484d"}}, err) : null,
      pending ? e("div", {role: "region", "aria-label": "Pending gateway confirmation",
        style: {marginTop: 8, padding: "8px 12px", border: "1px solid #b7791f",
          borderRadius: 8, fontSize: 13}},
        e("strong", null, "Confirm " + pending.what + ": "),
        e("span", null, pending.detail.slice(0, 160)),
        e("div", {style: {marginTop: 6, display: "flex", gap: 8}},
          e(C.Button || "button", {onClick: function () { settlePending(true); },
            disabled: busy}, busy ? "Working…" : "Confirm"),
          e(C.Button || "button", {onClick: function () { settlePending(false); },
            disabled: busy}, "Cancel"))) : null,
      e("h3", {style: {marginTop: 16}}, "Messaging accounts (" + accounts.length + ")"),
      e("ul", null, accounts.map(function (a) {
        return e("li", {key: a.id || a.account_id},
          e("strong", null, a.id || a.account_id), " — ",
          e("span", null, (a.provider || "?") + " / " + (a.label || "") +
            " / key:" + (a.key_ref_kind || a.key_ref || "?")));
      })),
      e("div", {style: {marginTop: 8, display: "flex", gap: 8, flexWrap: "wrap", alignItems: "end"}},
        e("label", null, "Account id ",
          e(C.Input || "input", {"aria-label": "New account id", value: aAid[0],
            onChange: function (ev) { aAid[1](ev.target.value); },
            placeholder: "team", style: {maxWidth: 140}})),
        e("label", null, "Provider ",
          e("select", {"aria-label": "New account provider", value: aProv[0],
            onChange: function (ev) { aProv[1](ev.target.value); }},
            selOpts(PROVIDERS))),
        e("label", null, "Label ",
          e(C.Input || "input", {"aria-label": "New account label", value: aLabel[0],
            onChange: function (ev) { aLabel[1](ev.target.value); },
            placeholder: "Team", style: {maxWidth: 140}})),
        e("label", null, "Key ref ",
          e(C.Input || "input", {"aria-label": "New account key ref", value: aKey[0],
            onChange: function (ev) { aKey[1](ev.target.value); },
            placeholder: "env:VAR or file:PATH", style: {maxWidth: 200}})),
        e(C.Button || "button", {"aria-label": "Add messaging account", disabled: busy,
          onClick: function () {
            if (!(aAid[0] || "").trim()) { setErr("[MALFORMED] account id required"); return; }
            runMutation("/api/ops/msg-account-add",
              {account_id: aAid[0].trim(), provider: aProv[0],
               label: (aLabel[0] || "").trim() || aAid[0].trim(),
               key_ref: (aKey[0] || "").trim()}, "add messaging account");
            aAid[1](""); aLabel[1](""); aKey[1]("");
          }}, "Add account")),
      e("h3", {style: {marginTop: 16}}, "Gateways (" + gateways.length + ")"),
      !gateways.length
        ? e("p", {style: {opacity: 0.6}}, "No gateway records yet.")
        : e("table", {style: {borderCollapse: "collapse", width: "100%"}},
            e("thead", null, e("tr", null,
              ["ID", "Provider", "Account", "Channel", "Link", ""].map(function (h) {
                return e("th", {key: h, style: {textAlign: "left", borderBottom: "1px solid"}}, h);
              }))),
            e("tbody", null, gateways.map(function (r) {
              var id = r.id;
              return e("tr", {key: id},
                e("td", null, id),
                e("td", null, r.provider || "—"),
                e("td", null, r.account || "—"),
                e("td", null, r.channel || "—"),
                e("td", null, links[id] || r.last_outcome || "—"),
                e("td", null,
                  e("button", {"aria-label": "Check link " + id,
                    onClick: function () { checkLink(id); }}, "Link"),
                  e("button", {"aria-label": "Remove gateway " + id, disabled: busy,
                    style: {marginLeft: 6},
                    onClick: function () {
                      runMutation("/api/ops/gateway-remove",
                        {gateway_id: id}, "remove gateway " + id);
                    }}, "Remove")));
            }))),
      e("div", {style: {marginTop: 8, display: "flex", gap: 8, flexWrap: "wrap", alignItems: "end"}},
        e("label", null, "Gateway id ",
          e(C.Input || "input", {"aria-label": "New gateway id", value: gId[0],
            onChange: function (ev) { gId[1](ev.target.value); },
            placeholder: "gw1", style: {maxWidth: 140}})),
        e("label", null, "Provider ",
          e("select", {"aria-label": "New gateway provider", value: gProv[0],
            onChange: function (ev) { gProv[1](ev.target.value); }},
            selOpts(PROVIDERS))),
        e("label", null, "Account ",
          e(C.Input || "input", {"aria-label": "New gateway account", value: gAcct[0],
            onChange: function (ev) { gAcct[1](ev.target.value); },
            placeholder: "team", style: {maxWidth: 140}})),
        e("label", null, "Channel ",
          e("select", {"aria-label": "New gateway channel", value: gChan[0],
            onChange: function (ev) { gChan[1](ev.target.value); }},
            selOpts(CHANNELS))),
        e("label", null, "Key ref ",
          e(C.Input || "input", {"aria-label": "New gateway key ref", value: gKey[0],
            onChange: function (ev) { gKey[1](ev.target.value); },
            placeholder: "env:VAR or file:PATH", style: {maxWidth: 200}})),
        e(C.Button || "button", {"aria-label": "Add gateway", disabled: busy,
          onClick: function () {
            if (!(gId[0] || "").trim()) { setErr("[MALFORMED] gateway id required"); return; }
            runMutation("/api/ops/gateway-add",
              {gateway_id: gId[0].trim(), provider: gProv[0],
               account: (gAcct[0] || "").trim(),
               channel: gChan[0], key_ref: (gKey[0] || "").trim()},
              "add gateway " + gId[0].trim());
            gId[1](""); gAcct[1](""); gKey[1]("");
          }}, "Add gateway")));
  }
  window.__HERMES_PLUGINS__.register("qubit-gateway", Gateway);
})();
