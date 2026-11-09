/* The Qubit Office page: the vendored office client, mounted in the plugin.
 *
 * React comes from the HOST. This module has no `import from "react"` and uses
 * `R.createElement` rather than JSX — the dashboard injects exactly one React,
 * and a second copy would silently split the hook runtime.
 */

import type { OfficeGameShellProps } from "../OfficeGameShell";
import { createHudDock, hudFrameSrc } from "../ui/HudPanel";
import { OFFICE_APP_PATH, officeAppSrc } from "./office-app";
import { installOfficeApiBridge } from "./api-bridge";
import { installOfficeMenuButtons, type OfficeMenuController } from "./office-menu";
import { createSeatModelPanel } from "./SeatModelPanel";
import { createAgentsPanel } from "./AgentsPanel";
import {
  POLL_INTERVAL_MS, SNAPSHOT_ROUTE, STREAM_ROUTE,
  bindingSummary, bindingVerdict,
} from "./office-api";

export interface OfficeAppProps {
  /** The dashboard's base path (empty at the root). */
  base?: string;
  /** Serve the client's own demo data. Off by default — see office-app.ts. */
  mock?: boolean;
  /**
   * Which seat the model panel configures. Omitted = no panel, so the pure
   * game view (and every existing caller) is unchanged.
   */
  seatAgentId?: string | number;
  /** Heading text for the panel. */
  seatLabel?: string;
  /**
   * Selectable seats, from the tower payload's own characters. When given, the
   * shell offers a picker instead of relying on a fixed `seatAgentId` — the
   * office is the same Qubit backend, so the seats it draws are the seats it
   * can configure.
   */
  seats?: Array<{ agent_id: number; slug: string }>;
  /** Called when the operator picks a different seat. */
  onSeatChange?: (agentId: number) => void;
  /**
   * Why the seat list is empty, when it is empty because a read failed rather
   * than because the office has no seats. Shown as a banner, never swallowed.
   */
  seatError?: string;
  /**
   * The host's own read of the tower report. It mirrors the backend the iframe
   * streams from, so the page can settle out of "connecting" and show its real
   * room/agent counts (or the real error) instead of assuming success.
   */
  officeState?: "connecting" | "connected" | "stale" | "error";
  officeRooms?: number;
  officeAgents?: number;
  officeError?: string;
  /** Set once the iframe has loaded, for tests and for a load-failure banner. */
  onReady?: () => void;
}

function connectionText(props: OfficeAppProps): string {
  const state = props.officeState ?? "connecting";
  const rooms = props.officeRooms ?? 0;
  const agents = props.officeAgents ?? 0;
  const detail = props.officeError?.trim();
  if (state === "error") {
    return `Office backend unavailable${detail ? ` — ${detail}` : ""}.`;
  }
  if (state === "stale") {
    return `Office showing its last known state${detail ? ` — ${detail}` : ""}.`;
  }
  if (state === "connected") {
    if (rooms === 0 && agents === 0) return "Office connected — no rooms yet.";
    return `Office connected — ${rooms} room${rooms === 1 ? "" : "s"} · `
      + `${agents} agent${agents === 1 ? "" : "s"}.`;
  }
  return "Connecting to the office backend\u2026";
}

type ShellComponents = {
  base?: string;
  HudDock: ReturnType<typeof createHudDock>;
  AgentsPanel: ReturnType<typeof createAgentsPanel>;
  SeatModelPanel: ReturnType<typeof createSeatModelPanel>;
};

/**
 * Component factories memoised per (hooks, base).
 *
 * WHY THIS EXISTS — the refresh bug. These were called on EVERY render. A
 * factory returns a NEW function, so React saw a different component type each
 * time and unmounted/remounted the whole subtree: the HUD dock's iframe was
 * torn down and reloaded every time the page re-rendered, and the agents panel
 * refetched its roster on the same cadence. The page re-renders whenever the
 * host's tower poll updates its props — i.e. constantly — which is exactly the
 * "HUD keeps refreshing" the owner saw. A stable identity per shell instance
 * makes a re-render a re-render, not a reload.
 *
 * A WeakMap on the hooks object (not a React useRef) keeps this correct even
 * for callers that pass a minimal hook surface, and lets the cache die with the
 * shell.
 */
const SHELL_COMPONENT_CACHE = new WeakMap<object, Map<string, ShellComponents>>();

function shellComponents(
  R: OfficeGameShellProps["React"],
  hooks: OfficeGameShellProps["hooks"],
  base?: string,
): ShellComponents {
  const key = (hooks ?? {}) as object;
  const byBase = SHELL_COMPONENT_CACHE.get(key) ?? new Map<string, ShellComponents>();
  const cacheKey = base ?? "";
  const cached = byBase.get(cacheKey);
  if (cached) return cached;
  const built: ShellComponents = {
    base,
    HudDock: createHudDock({ React: R, hooks, base }),
    AgentsPanel: createAgentsPanel(R, hooks),
    SeatModelPanel: createSeatModelPanel(R, hooks),
  };
  byBase.set(cacheKey, built);
  SHELL_COMPONENT_CACHE.set(key, byBase);
  return built;
}

/**
 * Build the component. React and hooks arrive as arguments, exactly as the rest
 * of the plugin does it.
 */
export function createOfficeApp(
  R: OfficeGameShellProps["React"],
  hooks: OfficeGameShellProps["hooks"],
) {
  const { useState, useEffect, useRef } = hooks;

  return function OfficeApp(props: OfficeAppProps = {}) {
    const [loaded, setLoaded] = useState(false);
    const [failed, setFailed] = useState<string | null>(null);
    const [panelOpen, setPanelOpen] = useState(false);
    // The HUD, IN the office. A dock beside the game, never a mutation: the
    // dock frames the HUD's own dashboard route (../ui/HudPanel.ts), so the
    // panel that appears is the real HUD — its bundle, its client, its seven
    // GET tabs — and this page issues no request of its own.
    const [hudOpen, setHudOpen] = useState(false);
    // The agents panel: the roster's seats, grouped by division. It is part of
    // THIS document (the shell around the frame), because the embedded app has
    // no Qubit hook and its bridge refuses every write — the panel reads the
    // real route with the host session token, exactly as the seat-model panel
    // does. See app/AgentsPanel.tsx for why that is the only mechanism that
    // can cross the document boundary.
    const [agentsOpen, setAgentsOpen] = useState(false);
    // Stable across renders — see shellComponents().
    const { HudDock, AgentsPanel, SeatModelPanel } = shellComponents(R, hooks, props.base);
    const frameRef = useRef<HTMLIFrameElement | null>(null);
    // The two icon buttons installed into the office's own panel group. The
    // controller lives for one iframe load; a reload (or a new base) swaps it.
    const menuRef = useRef<OfficeMenuController | null>(null);

    useEffect(() => {
      const frame = frameRef.current;
      if (!frame) return;
      // Install before navigation. Registering onLoad first is not enough:
      // the upstream ES module can issue `/api/access` while parsing, before
      // the iframe's load event fires. Setting `src` only after replacing
      // fetch closes that startup race without touching the host's fetch.
      const win = frame.contentWindow as Window | null;
      if (!win) {
        setFailed("The office API could not be connected from this page.");
        return;
      }
      let restoreBridge: (() => void) | undefined;
      try {
        restoreBridge = installOfficeApiBridge(win);
      } catch {
        setFailed("The office API could not be connected from this page.");
        return;
      }
      const onLoad = () => {
        setLoaded(true);
        setFailed(null);
        // Put the Agents and HUD icons where the office keeps its own panel
        // buttons. The document is same-origin, so the shell can append to the
        // group; the buttons drive THIS component's dock state.
        menuRef.current?.dispose();
        menuRef.current = installOfficeMenuButtons(win, {
          onToggleAgents: () => setAgentsOpen((v: boolean) => !v),
          onToggleHud: () => setHudOpen((v: boolean) => !v),
        });
        menuRef.current?.update({ agents: agentsOpen, hud: hudOpen });
        props.onReady?.();
      };
      const onError = () => setFailed("The office app failed to load.");
      frame.addEventListener("load", onLoad);
      frame.addEventListener("error", onError);
      setFailed(null);
      setLoaded(false);
      frame.src = officeAppSrc({ base: props.base, mock: props.mock });
      return () => {
        frame.removeEventListener("load", onLoad);
        frame.removeEventListener("error", onError);
        menuRef.current?.dispose();
        menuRef.current = null;
        restoreBridge?.();
      };
    }, [props.base, props.mock]);

    // Mirror the dock state onto the injected buttons after every render: the
    // office paints its own active state from `aria-pressed`.
    useEffect(() => {
      menuRef.current?.update({ agents: agentsOpen, hud: hudOpen });
    });

    const children: unknown[] = [
      R.createElement("iframe", {
        key: "frame",
        ref: frameRef,
        title: "Habblaud virtual office",
        "data-testid": "office-app-frame",
        className: "office-game__frame",
        "aria-label": "Habblaud office game",
        "aria-describedby": "office-app-description",
        "aria-busy": !loaded && !failed,
        "data-state": failed ? "error" : loaded ? "ready" : "loading",
        // Keep the upstream app in a distinct origin and storage namespace.
        // allow-scripts + allow-same-origin is still required for its module
        // worker, canvas and storage APIs; the dashboard host stays separate.
        // The server injects the session token into the app document itself.
        sandbox: "allow-scripts allow-same-origin allow-forms allow-downloads allow-popups allow-modals",
        allow: "clipboard-read; clipboard-write",
        style: { border: "0", width: "100%", height: "100%", display: "block" },
      }),
      R.createElement("p", {
        key: "description",
        id: "office-app-description",
        className: "office-game__sr-only",
      }, "Interactive Habblaud virtual office. Use the controls within the game; unsupported write actions are disabled."),
    ];

    if (!loaded && !failed) {
      children.push(R.createElement("div", {
        key: "loading",
        className: "office-game__panel office-game__loading",
        "data-testid": "office-app-loading",
        role: "status",
        "aria-live": "polite",
      }, "Loading the office\u2026"));
    }

    if (failed) {
      children.push(R.createElement("div", {
        key: "failed",
        className: "office-game__refusal office-game__error",
        "data-testid": "office-app-failed",
        role: "alert",
      }, [
        R.createElement("div", { key: "m" }, `${failed} Check Qubit's office connection; do not start a separate upstream server.`),
        // The one thing a reader needs when it fails: WHERE it was looking.
        R.createElement("div", {
          key: "p", style: { fontSize: "var(--office-fs-xs)" },
        }, OFFICE_APP_PATH),
      ]));
    }

    // The host's tower read is the only connection state this page can prove.
    // Show it, so "Connecting…" is never a permanent lie and a failure is named
    // rather than hidden behind a silent spinner.
    if (props.officeState) {
      const state = props.officeState;
      children.push(R.createElement("div", {
        key: "connection",
        className: "office-game__connection",
        "data-testid": "office-app-connection",
        "data-state": state,
        role: state === "error" ? "alert" : "status",
        "aria-live": state === "connecting" ? "polite" : "assertive",
      }, connectionText(props)));
    }

    // Keep binding diagnostics available to assistive technology, but do not
    // place an invisible overlay over the game or intercept its keyboard/mouse.
    const verdict = bindingVerdict();
    children.push(R.createElement("div", {
      key: "status",
      className: "office-game__sr-only",
      "data-testid": "office-app-status",
      role: "status",
      "aria-label": `${bindingSummary()}; ${verdict.live ? `live stream ${STREAM_ROUTE}` : `poll ${POLL_INTERVAL_MS / 1000}s → ${SNAPSHOT_ROUTE}`}`,
    }, bindingSummary()));

    // Per-seat model routing. Rendered only when the host names a seat, so the
    // default office view is the game and nothing else. It sits OUTSIDE the
    // iframe on purpose: the api-bridge answers every non-GET with 501, so a
    // write control inside the vendored client could not save anything.
    if (props.seatAgentId !== undefined && props.seatAgentId !== null) {
      const seats = props.seats ?? [];
      children.push(R.createElement("button", {
        key: "seat-model-toggle",
        type: "button",
        className: "seat-model__toggle",
        "data-testid": "seat-model-toggle",
        "aria-expanded": panelOpen,
        onClick: () => setPanelOpen(!panelOpen),
      }, panelOpen ? "Hide seat model" : "Seat model"));
      if (panelOpen) {
        // A failed poll is named BEFORE the picker, so an empty office and an
        // unreachable one never look the same.
        if (props.seatError) {
          children.push(R.createElement("div", {
            key: "seat-error",
            className: "seat-model__error",
            role: "alert",
            "data-testid": "seat-model-seat-error",
          }, `The seat list could not be read (${props.seatError}); model routing is unavailable.`));
        }
        // The picker comes from the tower payload's own characters: the seats
        // drawn in the office are the seats that can be configured.
        if (seats.length > 0) {
          children.push(R.createElement("label", {
            key: "seat-picker", className: "seat-model__label",
          }, "Seat",
            R.createElement("select", {
              key: "seat",
              "data-testid": "seat-model-seat",
              value: String(props.seatAgentId),
              onChange: (e: { target: { value: string } }) => {
                const next = Number(e.target.value);
                if (Number.isFinite(next)) props.onSeatChange?.(next);
              },
            }, seats.map((s) =>
              R.createElement("option", {
                key: s.agent_id, value: String(s.agent_id),
              }, s.slug || `seat ${s.agent_id}`))),
          ));
        }
        children.push(R.createElement(SeatModelPanel, {
          key: "seat-model-panel",
          agentId: props.seatAgentId,
          seatLabel: props.seatLabel,
        }));
      }
    }

    // THE AGENTS PANEL, OPENED FROM INSIDE THE OFFICE. Every seat the building
    // plan places, grouped by division, from the roster on disk. Its toggle is
    // the icon installed into the office's own panel group (app/office-menu.ts),
    // so there is no second floating button here. It sits on the LEFT so it
    // never covers the HUD dock.
    if (agentsOpen) {
      children.push(R.createElement("div", {
        key: "agents-dock",
        id: "office-game-agents-dock",
        className: "office-game__dock office-game__agentsDock office-game__panel",
        "data-testid": "office-game-agents-dock",
        role: "region",
        "aria-label": "Office agents",
      }, [
        R.createElement("div", { key: "head", className: "office-game__dockHead" }, [
          R.createElement("span", { key: "t" }, "Agents"),
          R.createElement("button", {
            key: "x", type: "button", className: "office-game__btn",
            "data-testid": "office-game-agents-close", "aria-label": "Close agents",
            onClick: () => setAgentsOpen(false),
          }, "Close"),
        ]),
        R.createElement(AgentsPanel as never, { key: "body", base: props.base }),
      ]));
    }

    // THE HUD, OPENED FROM INSIDE THE OFFICE. The page's own link to
    // /qubit-hud leaves the office; this opens the same panels over it, so the
    // vitals are one click from the room they belong to. The toggle is the icon
    // installed into the office's own panel group (app/office-menu.ts); the
    // /qubit-hud link itself stays, as the HUD's own tab.
    if (hudOpen) {
      children.push(R.createElement("div", {
        key: "hud-dock",
        id: "office-game-hud-dock",
        className: "office-game__dock office-game__panel",
        "data-testid": "office-game-hud-dock",
        role: "region",
        "aria-label": "HUD",
      }, [
        R.createElement("div", { key: "head", className: "office-game__dockHead" }, [
          R.createElement("span", { key: "t" }, "HUD"),
          R.createElement("a", {
            key: "open",
            className: "office-game__hudLink",
            "data-testid": "office-game-hud-open-link",
            href: hudFrameSrc(props.base),
          }, "Open HUD"),
          R.createElement("button", {
            key: "x", type: "button", className: "office-game__btn",
            "data-testid": "office-game-hud-close", "aria-label": "Close HUD",
            onClick: () => setHudOpen(false),
          }, "Close"),
        ]),
        R.createElement(HudDock as never, { key: "body" }),
      ]));
    }

    return R.createElement("div", {
      "data-testid": "office-app",
      className: "office-game",
    }, children);
  };
}
