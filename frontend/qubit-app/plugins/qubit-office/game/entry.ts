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
import { createOfficeApp } from "./app/OfficeAppShell";
// F08's styles. Imported here so the bundler emits them with the entry; the
// rules are all scoped to `.office-game`, so the dashboard is untouched.
import "./styles/tokens.css";
import "./styles/game.css";
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
  /* WHICH RENDERER MOUNTS, AND HOW TO GET BACK
   *
   * `/qubit-office` (no query param) mounts the VENDORED office client — the
   * full pixel-art app under /plugin-apps/qubit-office/habblaud/, fed by the
   * live `/api/stream` SSE route. `?shell=1` mounts the previous hand-built
   * shell (game/OfficeGameShell.tsx) instead. `?app=1` is retained as a
   * compatibility alias for deployments/bookmarks created during the first
   * Habblaud preview; the default is always the upstream app.
   *
   * WHY THE OLD SHELL IS RETAINED, NOT DELETED
   * ------------------------------------------
   * It is not dead code. It is the ROLLBACK for this promotion, exactly as
   * `qubit-office.bundle.js` is the rollback for the entry itself: the vendored
   * app is an external artifact whose whole interactive half depends on bindings
   * Qubit has not finished (writes must go through the confirmation authority —
   * see app/office-api.ts). The hand-built shell talks directly to the tower
   * snapshot the backend owns, so it is the renderer that still works if the
   * vendored app or its stream regresses. Keeping it one query param away means
   * a regression can be triaged and escaped in one reload, without a rebuild.
   *
   * A URL SWITCH RATHER THAN A CODE CHANGE because both renderers must stay
   * reachable while the vendored app's write half is settled, and a query param
   * means nobody has to edit and rebuild to compare them.
   */
  const params = new URLSearchParams(window.location.search);
  // The fallback is opt-IN via `?shell=1`; the app is the default.
  const wantShell = params.has("shell") && !params.has("app");
  const R = SDK.React as OfficeGameShellProps["React"];
  const H = SDK.hooks as OfficeGameShellProps["hooks"];
  const Office = wantShell
    ? createOfficeComponent(R, H, liveData)
    : createOfficeWithSeatRouting(R, H);
  window.__QUBIT_PLUGINS__?.register("qubit-office", Office);
}

/**
 * The default office, plus per-seat model routing.
 *
 * WHY THIS WRAPPER EXISTS
 * -----------------------
 * `createOfficeApp` renders the vendored client and nothing else. Per-seat
 * model selection needs the office's OWN seat list, which lives in the tower
 * payload — so this polls the same adapter the shell path uses and hands the
 * app its `seats`. Supplying the seats is what makes `seatAgentId` a real
 * choice; without it the app renders exactly as before (no panel).
 *
 * The poll drives only the seat LIST. The game inside the iframe keeps its own
 * `/api/stream` connection, so this wrapper cannot make the office stale.
 */
function createOfficeWithSeatRouting(
  R: OfficeGameShellProps["React"],
  H: OfficeGameShellProps["hooks"],
) {
  const OfficeApp = createOfficeApp(R, H);

  return function OfficeWithSeatRouting() {
    const [seats, setSeats] = H.useState<Array<{ agent_id: number; slug: string }>>([]);
    const [seatId, setSeatId] = H.useState<number | null>(null);
    const [seatError, setSeatError] = H.useState<string>("");

    H.useEffect(() => {
      const adapter = new OfficeAdapter({
        onSnapshot: (s) => {
          // Name a failed read rather than rendering an empty seat list: an
          // office with no seats and an office whose poll 401'd look identical
          // otherwise, and the operator cannot tell a quiet org from a broken one.
          if (s.error) {
            setSeatError(s.error);
          } else {
            setSeatError("");
          }
          const characters = s.payload?.characters ?? [];
          if (characters.length === 0) return;
          setSeats(characters.map((c) => ({ agent_id: c.agent_id, slug: c.slug })));
          // Select the first seat once, then keep the operator's choice.
          setSeatId((current) =>
            current !== null && characters.some((c) => c.agent_id === current)
              ? current
              : characters[0].agent_id);
        },
      });
      adapter.start();
      return () => adapter.dispose();
    }, []);

    return R.createElement(OfficeApp, {
      seats,
      seatAgentId: seatId ?? undefined,
      seatLabel: seatId === null
        ? undefined
        : seats.find((s) => s.agent_id === seatId)?.slug,
      seatError,
      onSeatChange: setSeatId,
    });
  };
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
