/* Qubit Brand — overlay plugin bundle (dependency-free IIFE).
 * Registers the Qubit header mark in the header-left slot. The upstream
 * "Qubit Agent" wordmark is hidden by the active Qubit theme's customCSS
 * (theming API, no vendored edits). Upstream names remain only in legal
 * notices, attribution, provenance, and compatibility records. */
(function () {
  var SDK = window.__QUBIT_PLUGIN_SDK__;
  if (!SDK || !window.__QUBIT_PLUGINS__) return;
  var React = SDK.React;
  var e = React.createElement;

  function QubitMark() {
    return e("span", {
      className: "qubit-brandmark",
      style: {fontWeight: 800, letterSpacing: "0.05em", fontSize: "0.95rem",
              color: "var(--qubit-lime, #a3e635)", whiteSpace: "nowrap"}
    }, "Qubit Agent");
  }

  /* Hidden route body (tab is hidden:true; never shown in the sidebar) but
   * still reachable by URL. It must render a real component, not null: the
   * route documents the Qubit brand surface instead of being a dead path. */
  function QubitBrandPage() {
    return e("div", {style: {padding: "16px 20px", maxWidth: 760}},
      e("h1", {style: {margin: "0 0 4px", fontSize: 20}}, "Qubit Brand"),
      e("p", {style: {opacity: 0.72, margin: "0 0 14px", fontSize: 13}},
        "Product identity for the Qubit Agent dashboard."),
      e("div", {style: {border: "1px solid #2a3443", borderRadius: 8,
          padding: "10px 12px", marginBottom: 12}},
        e("strong", null, "Header mark"),
        e("p", {style: {fontSize: 13, margin: "6px 0 0"}},
          "The Qubit Agent mark is rendered in the sidebar by this plugin " +
          "(header-left slot); the vendored wordmark is hidden by the active " +
          "Qubit theme, never by editing the vendored source."),
        e("strong", {style: {display: "block", marginTop: 10}},
          "Attribution"),
        e("p", {style: {fontSize: 13, margin: "6px 0 0"}},
          "Third-party names remain only in legal notices, attribution, " +
          "provenance, and compatibility records \u2014 never in normal " +
          "product surfaces.")),
      e("div", {"data-state": "empty",
        style: {opacity: 0.7, fontSize: 13}},
        "No settings on this page; it records the brand surface only."));
  }
  window.__QUBIT_PLUGINS__.register("qubit-brand", QubitBrandPage);
  if (window.__QUBIT_PLUGINS__.registerSlot) {
    window.__QUBIT_PLUGINS__.registerSlot("qubit-brand", "header-left", QubitMark);
    window.__QUBIT_PLUGINS__.registerSlot("qubit-brand", "docs:top", DocsNotice);
  }

  /* Docs page notice: the built-in Docs tab embeds the upstream docs site
   * (third-party reference content). The Qubit operator guide lives in the
   * checkout (`docs/`) and the CLI (`qubit --help`); we deliberately do NOT
   * link to /docs here — the notice is rendered on /docs, so that link would
   * be a dead (self-referential) control. */
  function DocsNotice() {
    return e("div", {role: "note", style: {margin: 12, padding: 10,
      border: "1px solid", borderRadius: 8, fontSize: 13}},
      e("strong", null, "Qubit docs: "),
      e("span", null, "the frame below is the upstream reference site. "),
      e("span", null, "For Qubit-specific operation, run `qubit --help` or " +
        "read the docs/ directory in the Qubit checkout."));
  }
})();
