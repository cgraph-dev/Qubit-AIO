/* The promoted office game entry.
 *
 * THE PLUGIN CONTRACT, MATCHED EXACTLY
 * ------------------------------------
 * The dashboard's loader (`frontend/qubit-web/src/plugins/usePlugins.ts`) does
 * roughly:
 *
 *     const SDK = window.__QUBIT_PLUGIN_SDK__;   // React, hooks, fetchJSON
 *     script.src = assetRoot + "/" + manifest.entry;
 *     // then: getPluginComponent(name) -> a React.ComponentType
 *
 * and `registry.ts::registerPlugin(name, component)` stores the SECOND argument
 * as the component itself. So `register(name, {Office, React, hooks})` is
 * wrong — that registers an OBJECT where a component type belongs, and React
 * fails with "Element type is invalid ... got: object". The hand-stitched
 * bundle gets this right (`qubit-office.js:1705`
 * `window.__QUBIT_PLUGINS__.register("qubit-office", Office)`), and this entry
 * now matches it.
 *
 * React comes from the SDK inside the component, never from an import: a second
 * React copy would split the hook runtime (session, context and theme would not
 * reach the plugin, silently).
 *
 * BUILT BY: frontend/qubit-web/vite.office-game.config.ts
 * SERVED AS: qubit-office.game.js
 * ROLLBACK:  qubit-office.bundle.js (the previous hand-stitched renderer)
 */

import { createOfficeComponent } from "./OfficeGameShell";

declare global {
  interface Window {
    __QUBIT_PLUGIN_SDK__?: { React?: unknown; hooks?: unknown };
    __QUBIT_PLUGINS__?: { register: (name: string, mod: unknown) => void };
  }
}

const SDK = window.__QUBIT_PLUGIN_SDK__;
if (!SDK || !SDK.React || !SDK.hooks) {
  // A named gap, never a silent no-op: without the SDK the plugin cannot mount,
  // and saying so beats a blank tab.
  console.warn(
    "[qubit-office] plugin SDK missing (React/hooks); the game entry cannot " +
    "register. The Office tab will show no office.");
} else {
  // The registry wants a COMPONENT, built against the host's React and hooks.
  const Office = createOfficeComponent(SDK.React, SDK.hooks);
  window.__QUBIT_PLUGINS__?.register("qubit-office", Office);
}
