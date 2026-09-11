/* Qubit Runtime — overlay plugin bundle (OpenClaw addition).
 * Backend: GET /api/ops/runtime -> pinned runtime inventory. */
(function () {
  var SDK = window.__HERMES_PLUGIN_SDK__;
  if (!SDK) return;
  var React = SDK.React, hooks = SDK.hooks, fetchJSON = SDK.fetchJSON;
  var e = React.createElement;

  function Runtime() {
    var st = hooks.useState(null);
    var data = st[0], setData = st[1];
    var est = hooks.useState("");
    var err = est[0], setErr = est[1];
    hooks.useEffect(function () {
      fetchJSON("/api/ops/runtime").then(setData).catch(function (ex) {
        setErr(String((ex && ex.message) || ex));
      });
    }, []);
    if (err) return e("div", {style: {padding: 16}},
      e("h2", null, "Runtime"), e("div", {role: "alert"}, err));
    if (!data) return e("div", {style: {padding: 16}},
      e("h2", null, "Runtime"), e("p", null, "Loading…"));
    var rt = data.result || data;
    var images = rt.images || rt.runtimes || [];
    return e("div", {style: {padding: 16, maxWidth: 960}},
      e("h2", null, "Runtime"),
      e("p", {style: {opacity: 0.75}},
        "Digest-pinned confined runtimes — same backend as `qubit runtime` CLI and voice. ",
        "No container daemon, no pulls."),
      e("pre", {style: {whiteSpace: "pre-wrap"}},
        JSON.stringify({state: data.state, images: images}, null, 2).slice(0, 4000)));
  }
  window.__HERMES_PLUGINS__.register("qubit-runtime", Runtime);
})();
