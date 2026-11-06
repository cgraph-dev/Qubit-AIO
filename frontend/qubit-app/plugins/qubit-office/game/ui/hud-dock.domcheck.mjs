/**
 * END-TO-END PROOF that the HUD drawer shows the HUD, not a refusal.
 *
 * The owner's screenshot showed the dock painting
 *   "The HUD is not available on this page: its bundle ... has not registered."
 * because the previous design looked for the HUD's registration in the OFFICE
 * document, which never loads the HUD bundle.
 *
 * This test drives the two real documents instead:
 *
 *  1. The dock, mounted with real React + jsdom, renders an iframe whose `src`
 *     is `/qubit-hud` (rebased under a subpath), and NO refusal text.
 *  2. The HUD's actual served panel code — `hud-panel.mjs`, which is the
 *     HUD's own `register` call with a GET client that speaks to a real Qubit
 *     handler — renders its panels with REAL data fetched over real HTTP from
 *     the real WebUIHandler, inside that iframe's document.
 *
 * Step 2 is the load-bearing one: it proves the data arrives and produces the
 * panel DOM, which is what the drawer now shows.
 *
 * Run: node --experimental-vm-modules frontend/qubit-app/plugins/qubit-office/
 *        game/ui/HudPanel.domcheck.mjs
 */
import { JSDOM } from "jsdom";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { pathToFileURL } from "node:url";

const HERE = process.env.HUD_DOCK_DIR
  || path.dirname(fileURLToPath(import.meta.url));
const OUT = [];

function log(...a) { OUT.push(a.join(" ")); console.log(...a); }

// ---------------------------------------------------------------------------
// 1. The dock, in a real DOM, with real React
// ---------------------------------------------------------------------------
const dom = new JSDOM("<!doctype html><html><body><div id='root'></div></body></html>",
                      { url: "http://127.0.0.1:8890/qubit-office" });
globalThis.window = dom.window;
globalThis.document = dom.window.document;
globalThis.HTMLElement = dom.window.HTMLElement;

// React's production bundle strips `act` entirely (same trap vitest.config.ts
// documents); force the test bundle before React is imported.
process.env.NODE_ENV = "test";
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
const React = (await import("react")).default;
const { createRoot } = await import("react-dom/client");
const { act } = await import("react");

const mod = await import(
  pathToFileURL(path.join(HERE, "hud-panel.mjs")).href);
const HudDock = mod.createHudDock({ React, hooks: React });

const rootEl = document.getElementById("root");
const root = createRoot(rootEl);
await act(async () => {
  root.render(React.createElement(HudDock));
});

const frame = document.querySelector('[data-testid="office-game-hud-frame"]');
const wrap = document.querySelector('[data-testid="office-game-hud-frame-wrap"]');
const link = document.querySelector('[data-testid="office-game-hud-open"]');
const text = document.body.textContent || "";

log("DOCK: iframe present       :", !!frame);
log("DOCK: src                  :", frame?.getAttribute("src"));
log("DOCK: title                :", frame?.getAttribute("title"));
log("DOCK: wrapper data-state   :", wrap?.getAttribute("data-state"));
log("DOCK: direct link href     :", link?.getAttribute("href"));
log("DOCK: refusal text present :",
    /not available on this page|has not registered/.test(text));

let failures = 0;
const check = (name, cond, detail = "") => {
  if (!cond) failures += 1;
  log(`${cond ? "PASS" : "FAIL"} ${name}${detail ? ` — ${detail}` : ""}`);
};

check("the dock renders a frame", !!frame);
check("the frame targets the HUD route",
      frame?.getAttribute("src") === "/qubit-hud", frame?.getAttribute("src"));
check("the frame is titled for AT", frame?.getAttribute("title") === "Qubit HUD");
check("the 'Open HUD' link still works",
      link?.getAttribute("href") === "/qubit-hud");
check("the old refusal is NOT rendered",
      !/not available on this page|has not registered/.test(text));
check("the frame is not sandboxed away from its own origin",
      !frame?.hasAttribute("sandbox"));

// Rebase: a subpath deployment must frame <base>/qubit-hud.
window.__QUBIT_BASE_PATH__ = "/agents";
check("a subpath deployment frames <base>/qubit-hud",
      mod.hudFrameSrc() === "/agents/qubit-hud", mod.hudFrameSrc());
delete window.__QUBIT_BASE_PATH__;

// ---------------------------------------------------------------------------
// 2. The HUD's panel code, in its own document, against real HTTP
// ---------------------------------------------------------------------------
// The endpoint the probe server serves is produced by the Python harness; it
// is passed in as argv[2] (a JSON file of {base, token}).
const specPath = process.argv[2];
if (!specPath) {
  log("\n(no live-server spec given — skipping the data half)");
  process.exit(failures ? 1 : 0);
}
const spec = JSON.parse(readFileSync(specPath, "utf8"));

// A FRESH document = the HUD's own realm, exactly like the framed one.
const hudDom = new JSDOM("<!doctype html><html><body><div id='hud-root'></div></body></html>",
                         { url: spec.base + "/qubit-hud",
                           // The HUD bundle is evaluated through window.eval, which
                           // jsdom only provides with a script runner installed.
                           runScripts: "outside-only" });
const hudWin = hudDom.window;
// `react-dom` reads `window.event` when resolving an update priority, so the
// GLOBAL must be the HUD's window while its tree renders. Mounting the dock's
// tree is already done; from here on everything is the framed document.
for (const name of ["window", "document", "HTMLElement", "MouseEvent",
                    "Node", "Element", "Event", "getComputedStyle"]) {
  Object.defineProperty(globalThis, name,
    { value: hudWin[name], configurable: true, writable: true });
}
const hudReactModule = await import("react");
const hudReact = hudReactModule.default;
log("HUD: same React instance as the dock:", hudReact === React);
const hudRoot = (await import("react-dom/client")).createRoot(
  hudWin.document.getElementById("hud-root"));

// The client the HUD is given: the dashboard's authenticated GET client, in
// miniature — it does what `lib/api.ts::fetchJSON` does (prefix + session
// header) against the REAL server.
const CALLED = [];
const SERVER_ERRORS = [];
let answered = 0;  // responses that landed (success or 5xx)
const fetchJSON = async (route) => {
  CALLED.push(route);
  let res;
  try {
    res = await fetch(spec.base + "/agent" + route, {
      headers: { "X-Qubit-Session-Token": spec.token },
    });
  } catch (err) {
    answered += 1;
    throw err;
  }
  answered += 1;
  if (!res.ok) {
    SERVER_ERRORS.push({ route, status: res.status });
    throw new Error(`HTTP ${res.status} for ${route}`);
  }
  return res.json();
};

hudWin.__QUBIT_PLUGIN_SDK__ = { React: hudReact, hooks: hudReact, fetchJSON };
hudWin.__QUBIT_PLUGINS__ = {
  register: (name, comp) => { hudWin.__registered = { name, comp }; },
};

// Execute the HUD's served bundle in the HUD document — the same script the
// dashboard's loader injects on /qubit-hud.
const code = readFileSync(spec.hudBundle, "utf8");
try {
  hudWin.eval(code);
} catch (err) {
  log("HUD bundle threw:", err.constructor.name, err.message);
}
log("HUD: SDK on the hud window  :", !!hudWin.__QUBIT_PLUGIN_SDK__);
log("HUD: registry on the window :", !!hudWin.__QUBIT_PLUGINS__);

check("the HUD bundle registered", hudWin.__registered?.name === "qubit-hud");

if (hudWin.__registered) {
  await act(async () => {
    hudRoot.render(hudReact.createElement(hudWin.__registered.comp));
  });
  // The HUD's reads resolve in promises OUTSIDE React; act() only flushes
  // queued work, so the pattern is: let the jsdom loop run, THEN flush inside
  // act. Repeat until no route is still pending and the DOM stops changing.
  async function settle() {
    let prev = "";
    let stable = 0;
    for (let i = 0; i < 120; i += 1) {
      await new Promise((r) => setTimeout(r, 20));
      await act(async () => { await Promise.resolve(); });
      const now = hudWin.document.body.innerHTML;
      const caughtUp = CALLED.length === answered;
      if (now === prev && caughtUp) {
        stable += 1;
        if (stable >= 5) return;
      } else {
        stable = 0;
      }
      prev = now;
    }
  }

  await settle();
  const hdoc = hudWin.document;
  const hudText = hdoc.body.textContent || "";
  const tabs = [...hdoc.querySelectorAll('[role="tab"]')];
  const panels = [...hdoc.querySelectorAll(".hud__panel")];
  const states = [...hdoc.querySelectorAll(".hud-state")];
  const errorStates = [...hdoc.querySelectorAll(".hud-state--error, .hud-state--offline")];
  const stats = [...hdoc.querySelectorAll(".hud__stat")];

  log("\nHUD DOCUMENT (the framed page's own DOM):");
  log("  tabs          :", tabs.map((t) => t.textContent).join(" | "));
  log("  panels        :", panels.length);
  log("  stat tiles    :", stats.length);
  log("  state blocks  :", states.length, "(error/offline:", errorStates.length, ")");
  const sample = stats.slice(0, 8).map(
    (s) => `${s.querySelector(".hud__statLabel")?.textContent}=` +
           `${s.querySelector(".hud__statValue")?.textContent}`);
  log("  sample stats  :", sample.join(", "));
  log("  first panel   :", (panels[0]?.textContent || "").slice(0, 160));
  for (const st of states) {
    log("  state block   :", (st.textContent || "").replace(/\s+/g, " ").slice(0, 220));
  }

  check("the HUD renders its tab strip", tabs.length === 7,
        `${tabs.length} tabs`);
  check("the HUD renders panels", panels.length > 0, `${panels.length}`);
  check("the HUD renders real stat tiles", stats.length > 0, `${stats.length}`);
  check("no panel is in an error/offline state", errorStates.length === 0,
        `${errorStates.length} errored`);
  check("the HUD body is not the old refusal",
        !/not available on this page|has not registered/.test(hudText));

  // ---- every tab, not just the one that mounts first -------------------
  // The framed HUD is only "showing real data" if EACH tab paints tiles or
  // rows from a live route. A tab whose read the SERVER failed (a 5xx) is
  // reported separately: the HUD is correctly naming a backend failure, which
  // is not the HUD failing to render.
  log("\nPER-TAB DOM (clicking each tab, as an operator would):");
  let tabFailures = 0;
  for (const tab of tabs) {
    await act(async () => {
      tab.dispatchEvent(new hudWin.MouseEvent("click", { bubbles: true }));
    });
    await settle();
    const label = tab.textContent.trim();
    const tStats = [...hdoc.querySelectorAll(".hud__stat")];
    const tRows = [...hdoc.querySelectorAll(".hud-row, .hud__table tbody tr")];
    const tErr = [...hdoc.querySelectorAll(".hud-state--error, .hud-state--offline")];
    const sampleT = tStats.slice(0, 3).map(
      (x) => `${x.querySelector(".hud__statLabel")?.textContent}=` +
             `${x.querySelector(".hud__statValue")?.textContent}`);
    const server5xx = SERVER_ERRORS.filter((e) => e.status >= 500);
    const rendered = tStats.length > 0 || tRows.length > 0;
    // Honest outcomes only: real data rendered, and every error state that IS
    // shown is backed by a genuine 5xx from the server. A tab may do both (a
    // wide tab whose two reads are independent: one paints, one names).
    const unexplained = tErr.length > server5xx.length;
    const ok = (rendered || server5xx.length > 0) && !unexplained;
    if (!ok) tabFailures += 1;
    log(`  ${ok ? "PASS" : "FAIL"} ${label.padEnd(9)} `
        + `tiles=${tStats.length} rows=${tRows.length} `
        + `errors=${tErr.length} ${sampleT.join(", ")}`
        + (rendered ? "" : ` | ${(tErr[0]?.textContent || "").replace(/\s+/g, " ").slice(0, 110)}`));
  }
  if (SERVER_ERRORS.length) {
    log("\n  server-side failures the HUD correctly named:");
    for (const e of SERVER_ERRORS) log(`    ${e.status} ${e.route}`);
  }
  check("every tab renders real data (or names a server 5xx)", tabFailures === 0,
        `${tabFailures} tab(s) failed`);

  // No tab may be stuck in a LOADING state after settling.
  const stuck = [...hdoc.querySelectorAll(".hud-state")]
    .map((n) => n.textContent || "")
    .filter((t) => /^Reading\b/.test(t.trim()));
  check("no tab is stuck loading", stuck.length === 0,
        stuck.map((t) => t.slice(0, 60)).join(" / "));
}

log(`\n${failures === 0 ? "ALL CHECKS PASS" : failures + " CHECK(S) FAILED"}`);
process.exit(failures ? 1 : 0);
