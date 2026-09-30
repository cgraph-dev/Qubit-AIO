/* Qubit Runtime — overlay plugin bundle (Qubit addition).
 * Backend: GET /api/ops/runtime -> pinned runtime inventory. Renders the
 * real typed inventory (name, pin, state) with explicit loading / empty /
 * degraded / error states; the raw backend payload stays available behind a
 * disclosure instead of being the whole page (no status-only tile). */
(function () {
  var SDK = window.__QUBIT_PLUGIN_SDK__;
  if (!SDK || !window.__QUBIT_PLUGINS__) return;
  var React = SDK.React, hooks = SDK.hooks, fetchJSON = SDK.fetchJSON;
  var e = React.createElement;

  var S = {
    wrap: {padding: "16px 20px", maxWidth: 960},
    table: {borderCollapse: "collapse", width: "100%", marginTop: 8,
            fontSize: 13},
    th: {textAlign: "left", borderBottom: "1px solid #33445a",
         padding: "6px 8px"},
    td: {borderBottom: "1px solid #222c39", padding: "6px 8px"},
    code: {background: "#0c1118", border: "1px solid #2a3443",
           borderRadius: 6, padding: 8, overflowX: "auto",
           whiteSpace: "pre-wrap", fontSize: 12, maxHeight: 260},
  };

  function Runtime() {
    var st = hooks.useState(null);
    var data = st[0], setData = st[1];
    var est = hooks.useState("");
    var err = est[0], setErr = est[1];

    hooks.useEffect(function () {
      fetchJSON("/api/ops/runtime").then(function (res) {
        setData((res && (res.result || res)) || {});
        setErr("");
      }).catch(function (ex) {
        setErr(String((ex && ex.message) || ex));
      });
    }, []);

    var heading = e("h1", {style: {margin: "0 0 4px", fontSize: 20}},
      "Runtime");
    if (err) {
      return e("div", {style: S.wrap}, heading,
        e("div", {role: "alert", "data-state": "degraded",
          style: {color: "#e5484d"}}, err));
    }
    if (!data) {
      return e("div", {style: S.wrap}, heading,
        e("p", {"data-state": "loading", "aria-busy": "true"},
          "Loading runtime inventory…"));
    }
    var images = data.images || data.runtimes || [];
    return e("div", {style: S.wrap}, heading,
      e("p", {style: {opacity: 0.75, margin: "0 0 8px"}},
        "Digest-pinned confined runtimes — same backend as `qubit runtime` CLI and voice. ",
        "No container daemon, no pulls."),
      e("div", {style: {fontSize: 12, opacity: 0.7}},
        "state " + (data.state || "unknown") + " · " + images.length +
        " runtime" + (images.length === 1 ? "" : "s")),
      !images.length
        ? e("p", {"data-state": "empty"}, "No runtimes staged.")
        : e("table", {style: S.table},
            e("thead", null, e("tr", null,
              ["Runtime", "Pin", "State", "Effective"].map(function (hdr) {
                return e("th", {key: hdr, scope: "col", style: S.th}, hdr);
              }))),
            e("tbody", null, images.map(function (img, i) {
              var name = img.name || img.id || "runtime";
              return e("tr", {key: i},
                e("td", {style: S.td}, e("code", null, name)),
                e("td", {style: S.td}, e("code", null, img.pin || "—")),
                e("td", {style: S.td},
                  e("span", {style: {opacity: 0.85}},
                    img.state || "unknown")),
                e("td", {style: S.td},
                  img.observed && img.observed.effective ? "yes" : "no"));
            }))),
      e("details", {style: {marginTop: 12}},
        e("summary", {style: {cursor: "pointer", opacity: 0.75,
          fontSize: 13}}, "Raw backend payload"),
        e("pre", {style: Object.assign({}, S.code, {marginTop: 8})},
          JSON.stringify(data, null, 2).slice(0, 4000))));
  }
  window.__QUBIT_PLUGINS__.register("qubit-runtime", Runtime);
})();
