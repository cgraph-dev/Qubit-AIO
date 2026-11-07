/* DOM check: the agents panel's markup, built from the SERVED payload.
 *
 * WHAT IT PROVES, AND WHAT IT CANNOT
 * ----------------------------------
 * It boots the REAL `voicebridge.qubit_compat.dispatch` behind a real HTTP
 * server on an ephemeral loopback port, mounts the panel's real React
 * component against that server through the panel's own fetch helper (prefix +
 * session-token header included), and asserts the text a reader sees: the real
 * seat count, the real division labels, and the real persona names.
 *
 * The component here is a `.ts` module with no JSX, transpiled by the calling
 * test with the repo's own tsc. Real React comes from the repo's own
 * `frontend/qubit-web/node_modules`, so the hooks and `act` are genuine.
 *
 * NOT proved here: the OFFICE page's chrome (the trigger button, the dock).
 * That is the shell's own suite; this check owns the panel body.
 *
 * Spec: { base, token, module } — module is the transpiled agents-panel.mjs
 * plus AgentsPanel.mjs, both staged beside this script.
 */
import fs from "node:fs";
import { createRequire } from "node:module";

// jsdom/react resolve from the WEB package: that is the repo's only copy, and
// an ESM `import "jsdom"` resolves relative to THIS file, not the cwd.
const webRequire = createRequire(process.env.NODE_PATH.replace(/\/node_modules$/, "") + "/package.json");
const { JSDOM } = webRequire("jsdom");
// Real React + act from the same tree, so `act` IS `React.act` and the hook
// runtime is the one ReactDOM renders with (Node 22's ESM/CJS interop would
// drop `act` off the default export otherwise).
const React = webRequire("react");
const ReactDOMClient = webRequire("react-dom/client");
// React 19 keeps `act` on the DEVELOPMENT react entry; `react-dom/test-utils`
// delegates to `React.act`, so the check needs the dev build to be in play.
const act = React.act || webRequire("react-dom/test-utils").act;

const spec = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const moduleDir = process.env.AGENTS_PANEL_DIR;

const nodeRequire = createRequire(import.meta.url);

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const dom = new JSDOM("<!doctype html><div id='root'></div>", {
  url: spec.base + "/qubit-office?app=1",
});
globalThis.window = dom.window;
globalThis.document = dom.window.document;
// Node 22 defines `navigator` as a getter-only global; redefine it rather than
// assigning, so real React sees the jsdom one.
Object.defineProperty(globalThis, "navigator", {
  value: dom.window.navigator, configurable: true, writable: true,
});
globalThis.Node = dom.window.Node;
globalThis.Element = dom.window.Element;
globalThis.HTMLElement = dom.window.HTMLElement;

// The host's injected session token — the same global the dashboard writes.
dom.window.__QUBIT_SESSION_TOKEN__ = spec.token;

// REAL fetch against the REAL server, so the URL and the header are exercised.
// `undici`'s fetch is used explicitly: jsdom does not ship one, and Node's
// global fetch would reject a relative URL the panel hands it.
const { fetch: realFetch } = webRequire("undici");
dom.window.fetch = async (input, init) => {
  const url = String(input).startsWith("http") ? String(input) : spec.base + String(input);
  return realFetch(url, init);
};
// The panel module may read either binding: set the global `fetch` to the same
// wrapper so the check cannot pass or fail on WHICH global it saw.
globalThis.fetch = dom.window.fetch;

const failures = [];
const warnings = [];
const stripActWarnings = () => {
  // React appends act() advisories as TEXT NODES to document.body, where they
  // pollute `body.textContent`. They are warnings, not rendered output: collect
  // them, report them separately, and remove them from the body.
  const walk = (node) => {
    for (const child of Array.from(node.childNodes)) {
      if (child.nodeType === 3 && /An update to .* was not wrapped in act/.test(child.textContent)) {
        warnings.push(child.textContent.trim().split("\n")[0]);
        node.removeChild(child);
      } else if (child.nodeType === 1) {
        walk(child);
      }
    }
  };
  walk(document.body);
};
const check = (ok, what) => {
  if (ok) console.log(`ok   ${what}`);
  else { console.log(`FAIL ${what}`); failures.push(what); }
};

const { fetchAgentPanel } = await import(`${moduleDir}/agents-panel.mjs`);
// Point the module's `window` at the jsdom document: it is evaluated as its own
// ESM module, so it reads the global set above — asserted rather than assumed.
check(globalThis.window === dom.window && globalThis.window.__QUBIT_SESSION_TOKEN__ === spec.token,
  "the panel module sees the host's injected session token");
const { createAgentsPanel } = await import(`${moduleDir}/AgentsPanel.mjs`);
check(typeof act === "function", "real React provides act (no double flush needed)");

// ---- 1. the panel's own fetch helper, against the real route --------------
const direct = await fetchAgentPanel({ win: dom.window, base: "" });
check(direct.kind === "ready", `the panel route answered ready (got ${direct.kind}${direct.kind === "error" ? ": " + direct.message : ""})`);
if (direct.kind !== "ready") {
  process.exit(1);
}
const counts = direct.data.counts;
const rows = direct.data.seats || [];
const groups = direct.data.divisions || [];

// ---- 2. mount the real component and read the rendered text --------------
const hooks = { useState: React.useState, useEffect: React.useEffect, useRef: React.useRef };
const Panel = createAgentsPanel(React, hooks);
const root = ReactDOMClient.createRoot(document.getElementById("root"));
await act(async () => {
  root.render(React.createElement(Panel, { base: "" }));
});
await act(async () => {});
// React commits from the NEXT macrotask after an awaited act scope, so the
// panel is still on its loading state here. Poll for the real transition with
// an empty flush after each tick (minus any warning text React emitted).
let transitioned = false;
for (let i = 0; i < 40; i += 1) {
  await new Promise((r) => setTimeout(r, 25));
  await act(async () => {});
  if (document.querySelector("[data-testid=agents-panel][data-state=ready]")) {
    transitioned = true;
    break;
  }
}
stripActWarnings();
check(transitioned, "the panel transitioned loading -> ready after the real fetch");

const el = document.querySelector("[data-testid=agents-panel]");
check(!!el, "the panel mounted");
check(el?.dataset.state === "ready", `the panel is in its ready state (got ${el?.dataset.state})`);
check(!document.body.textContent.includes("could not"), "no error text is rendered");

const renderedSeats = document.querySelectorAll("[data-testid=agents-panel-seat]");
const renderedGroups = document.querySelectorAll("[data-testid=agents-panel-division]");
check(renderedSeats.length === rows.length,
  `every seat is listed: ${renderedSeats.length} rows for ${rows.length} seats`);
check(renderedGroups.length === groups.length,
  `every group is listed: ${renderedGroups.length} buckets for ${groups.length}`);

// The grouping is by division, with the real per-bucket counts on the DOM.
let bucketOK = true;
for (const g of groups) {
  const node = document.querySelector(`[data-testid=agents-panel-division][data-division="${g.slug}"]`);
  if (!node) { bucketOK = false; console.log(`     missing bucket "${g.slug}"`);
    continue; }
  const listed = node.querySelectorAll("[data-testid=agents-panel-seat]").length;
  const head = node.querySelector("summary")?.textContent ?? "";
  if (listed !== g.headcount || !head.includes(`${g.headcount} seat`)) {
    bucketOK = false;
    console.log(`     bucket ${g.slug}: ${listed} rows, headcount ${g.headcount}, head "${head}"`);
  }
}
check(bucketOK, "each division bucket shows its seats and its real headcount");

// The persona names are on screen, not slugs.
check(rows.every((r) => el.textContent.includes(r.name)),
  "every persona name is on screen");
const summary = document.querySelector("[data-testid=agents-panel-summary]")?.textContent ?? "";
check(summary.includes(`${counts.headcount} seats`) && summary.includes(`${counts.divisions} divisions`),
  `the summary carries the real counts: "${summary}"`);

// Show the reader's real text: the summary, the first three buckets, and the
// first seat of each.
console.log("--- rendered summary ---");
console.log(summary);
console.log("--- rendered buckets (first 3) ---");
for (const node of Array.from(renderedGroups).slice(0, 3)) {
  console.log(node.querySelector("summary").textContent.trim());
  const first = node.querySelector("[data-testid=agents-panel-seat]");
  if (first) console.log("    " + first.textContent.trim());
}
console.log(`--- totals: ${counts.headcount} seats, ${counts.divisions} divisions, `
  + `${counts.rooms} rooms, ${counts.boardroom_seats} boardroom seats ---`);

if (failures.length) { console.log(`${failures.length} FAILED`); process.exit(1); }
if (warnings.length) {
  console.log(`note: ${warnings.length} React act() advisory text node(s) were `
    + `removed from the body before reading text`);
}
console.log("ALL CHECKS PASS");
process.exit(0);
