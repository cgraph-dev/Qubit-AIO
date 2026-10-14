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

import { createOfficeComponent, type OfficeGameShellProps } from "./OfficeGameShell";
import { OfficeAdapter } from "./adapter";
import { buildHeadquartersPortals } from "./navigation";
import { HQ_SLUG } from "./WorldAssembler";
import type { OfficeTowerPayload } from "./types";

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
  // The live data wiring is injected as `load`, so the shell stays a pure
  // renderer of (world, viewpoint) — the shape the F07 tests drive.
  const Office = createOfficeComponent(SDK.React, SDK.hooks, liveData);
  window.__QUBIT_PLUGINS__?.register("qubit-office", Office);
}

/**
 * Poll the tower and hand the shell a payload + the portals derived from it.
 *
 * The portal graph is built from the payload's OWN floorplan, so the building a
 * player walks through is the building the backend reported — a floor appears in
 * the game because it exists in the store, not because the renderer assumed it.
 */
function liveData(hooks: OfficeGameShellProps["hooks"]) {
  const [payload, setPayload] = hooks.useState<OfficeTowerPayload | null>(null);
  const [revision, setRevision] = hooks.useState(0);

  hooks.useEffect(() => {
    const adapter = new OfficeAdapter({
      onSnapshot: (s) => {
        // The adapter already drops stale revisions and RETAINS the last good
        // payload on a failed read, so this only advances on a real success.
        if (s.payload) {
          setPayload(s.payload);
          setRevision((n) => n + 1);
        }
      },
    });
    adapter.start();
    return () => adapter.dispose();
  }, []);

  const floors = (payload?.floorplan?.floors ?? []).map((f) => ({
    number: f.number,
    rooms: f.rooms.map((r) => ({ id: r.id })),
  }));
  return {
    payload,
    revision,
    portals: buildHeadquartersPortals(HQ_SLUG, floors),
  };
}
