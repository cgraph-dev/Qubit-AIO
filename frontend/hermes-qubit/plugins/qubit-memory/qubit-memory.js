/* Qubit Memory — overlay plugin bundle (dependency-free IIFE).
 * Memory provider status + search on the shared MemoryStore, and the
 * learning ledger (curator). Backend: GET /api/memory, GET /api/curator,
 * POST /api/ops/run (memory_search) — the same modules as CLI and voice. */
(function () {
  var SDK = window.__HERMES_PLUGIN_SDK__;
  if (!SDK || !window.__HERMES_PLUGINS__) return;
  var React = SDK.React;
  var hooks = SDK.hooks;
  var fetchJSON = SDK.fetchJSON;
  var authedFetch = SDK.authedFetch;
  var e = React.createElement;

  var S = {
    wrap: {padding: "16px 20px", maxWidth: 1000},
    h: {margin: "0 0 4px", fontSize: 20},
    sub: {opacity: 0.72, margin: "0 0 14px", fontSize: 13},
    card: {border: "1px solid #2a3443", borderRadius: 8, padding: "10px 12px",
           marginBottom: 10},
    btn: {padding: "7px 12px", cursor: "pointer", background: "#1b2634",
          color: "inherit", border: "1px solid #33445a", borderRadius: 6},
    input: {padding: "7px 9px", background: "#0c1118", color: "inherit",
            border: "1px solid #2a3443", borderRadius: 6, minWidth: 260},
    code: {background: "#0c1118", border: "1px solid #2a3443",
           borderRadius: 6, padding: 8, overflowX: "auto",
           whiteSpace: "pre-wrap", fontSize: 12, maxHeight: 260},
    badge: {fontSize: 11, padding: "1px 6px", borderRadius: 999,
            border: "1px solid #33445a", marginLeft: 6, opacity: 0.85},
  };

  function Memory() {
    var mst = hooks.useState(null); var mem = mst[0], setMem = mst[1];
    var cst = hooks.useState(null); var curator = cst[0], setCurator = cst[1];
    var qst = hooks.useState(""); var query = qst[0], setQuery = qst[1];
    var rst = hooks.useState(null); var result = rst[0], setResult = rst[1];
    var est = hooks.useState(""); var err = est[0], setErr = est[1];
    var wst = hooks.useState(false); var busy = wst[0], setBusy = wst[1];

    hooks.useEffect(function () {
      fetchJSON("/api/memory").then(setMem).catch(function (ex) {
        setErr(String((ex && ex.message) || ex));
      });
      fetchJSON("/api/curator").then(setCurator).catch(function (ex) {
        setErr(String((ex && ex.message) || ex));
      });
    }, []);

    function search() {
      var q = query.trim();
      if (!q || busy) return;
      setBusy(true); setResult(null); setErr("");
      authedFetch("/api/ops/run", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({id: "memory_search", args: {query: q}}),
      }).then(function (r) {
        return r.text().then(function (t) {
          var j; try { j = JSON.parse(t); } catch (_) { j = {raw: t}; }
          setResult({status: r.status, body: j});
          setBusy(false);
        });
      }).catch(function (ex) {
        setErr(String((ex && ex.message) || ex));
        setBusy(false);
      });
    }

    var providers = (mem && mem.providers) || [];
    var summary = curator && curator.summary;
    return e("div", {style: S.wrap},
      e("h1", {style: S.h}, "Memory & Learning"),
      e("p", {style: S.sub},
        "Local memory provider, grounded search, and the learning ledger — ",
        "same store as CLI `qubit memory`/`qubit lessons` and voice."),
      err ? e("div", {role: "alert", style: {color: "#e5484d"}}, err) : null,
      e("div", {style: S.card},
        e("strong", null, "Providers"),
        providers.length ? providers.map(function (p, i) {
          return e("div", {key: i, style: {fontSize: 13, marginTop: 4}},
            e("strong", null, p.name || p.id || "provider"),
            e("span", {style: S.badge}, p.status || (p.available ? "ready" : "?")),
            " " + (p.description || ""));
        }) : e("div", {style: {opacity: 0.7, fontSize: 13}},
          mem ? "No providers." : "Loading provider status…")),
      e("div", {style: S.card},
        e("strong", null, "Search memory"),
        e("div", {style: {display: "flex", gap: 8, marginTop: 8,
            flexWrap: "wrap"}},
          e("input", {style: S.input, value: query,
            placeholder: "Search the local memory store…",
            "aria-label": "Memory search query",
            onChange: function (ev) { setQuery(ev.target.value); },
            onKeyDown: function (ev) { if (ev.key === "Enter") search(); }}),
          e("button", {style: S.btn, disabled: busy,
            "aria-label": "Search memory", onClick: search},
            busy ? "Searching…" : "Search")),
        result ? e("pre", {style: Object.assign({}, S.code, {marginTop: 8})},
          JSON.stringify(result.body, null, 2).slice(0, 4000)) : null),
      e("div", {style: S.card},
        e("strong", null, "Learning ledger"),
        summary ? e("pre", {style: {marginTop: 8, fontSize: 12,
            whiteSpace: "pre-wrap"}},
          JSON.stringify({enabled: curator.enabled, paused: curator.paused,
            verified: summary.verified && summary.verified.by_status,
            pinned: summary.verified && summary.verified.pinned,
            journal: summary.verified && summary.verified.journal},
            null, 2)) :
          e("div", {style: {opacity: 0.7, fontSize: 13}},
            curator ? "No ledger data." : "Loading ledger…")));
  }

  window.__HERMES_PLUGINS__.register("qubit-memory", Memory);
})();
