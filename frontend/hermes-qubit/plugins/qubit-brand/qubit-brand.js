/* Qubit Brand — overlay plugin bundle (dependency-free IIFE).
 * Registers the Qubit header mark in the header-left slot. The upstream
 * "Hermes Agent" wordmark is hidden by the active Qubit theme's customCSS
 * (theming API, no vendored edits). Upstream names remain only in legal
 * notices, attribution, provenance, and compatibility records. */
(function () {
  var SDK = window.__HERMES_PLUGIN_SDK__;
  if (!SDK || !window.__HERMES_PLUGINS__) return;
  var React = SDK.React;
  var e = React.createElement;

  function QubitMark() {
    return e("span", {
      className: "qubit-brandmark",
      style: {fontWeight: 800, letterSpacing: "0.05em", fontSize: "0.95rem",
              color: "var(--qubit-lime, #a3e635)", whiteSpace: "nowrap"}
    }, "Qubit Agent");
  }

  // Hidden route component (tab is hidden:true; never shown in sidebar).
  function Hidden() { return null; }
  window.__HERMES_PLUGINS__.register("qubit-brand", Hidden);
  if (window.__HERMES_PLUGINS__.registerSlot) {
    window.__HERMES_PLUGINS__.registerSlot("qubit-brand", "header-left", QubitMark);
    window.__HERMES_PLUGINS__.registerSlot("qubit-brand", "docs:top", DocsNotice);
  }

  /* Docs page notice: the built-in Docs tab embeds the upstream docs site
   * (third-party reference content). Point operators at Qubit docs. */
  function DocsNotice() {
    return e("div", {role: "note", style: {margin: 12, padding: 10,
      border: "1px solid", borderRadius: 8, fontSize: 13}},
      e("strong", null, "Qubit docs: "),
      e("span", null, "the frame below is the upstream reference site. "),
      e("a", {href: "/docs"}, "Open the Qubit operator guide"));
  }
})();
