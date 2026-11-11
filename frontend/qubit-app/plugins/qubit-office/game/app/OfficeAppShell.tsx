/* The Qubit Office page: the vendored office client, mounted in the plugin.
 *
 * React comes from the HOST. This module has no `import from "react"` and uses
 * `R.createElement` rather than JSX — the dashboard injects exactly one React,
 * and a second copy would silently split the hook runtime.
 */

import type { OfficeGameShellProps } from "../OfficeGameShell";
import { OFFICE_BRAND } from "./brand";
import { installOfficeBrand } from "./brand-patch";
import { createHudDock, hudFrameSrc } from "../ui/HudPanel";
import { OFFICE_APP_PATH, officeAppSrc } from "./office-app";
import { installOfficeApiBridge } from "./api-bridge";
import {
  installOfficeMenuButtons, menuIconGeometry, MENU_BUTTONS,
  type MenuButtonSpec, type OfficeMenuController,
} from "./office-menu";
import { createSeatModelPanel } from "./SeatModelPanel";
import { createAgentsPanel } from "./AgentsPanel";
import { createAgentControlDrawer } from "./AgentControlDrawer";
import { createWarRoomPanel } from "./WarRoomPanel";
import { createApprovalsPanel } from "./ApprovalsPanel";
import { fetchApprovals } from "./approvals-panel";
import type { AgentSeat } from "./agents-panel";
import {
  applyHostTheme, loadOfficeTheme, officeSliceName, officeThemeClass,
  type OfficeTheme,
} from "./office-theme";
import {
  POLL_INTERVAL_MS, SNAPSHOT_ROUTE, STREAM_ROUTE,
  bindingSummary, bindingVerdict,
} from "./office-api";
import { createOfficeLoadingScreen } from "../loading/OfficeLoadingScreen";
import type { OfficeLoadingConfigInput } from "../loading/config";

/** Always-present art credit; it must not depend on a theme being active. */
export const OFFICE_ART_CREDIT = OFFICE_BRAND.credit;
export const OFFICE_ART_CREDIT_URL = OFFICE_BRAND.creditUrl;

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
  /**
   * Owner loading-screen config. Normally supplied by a host global
   * (`window.__QUBIT_OFFICE_LOADING__` / `office.loading`); this prop is the
   * explicit/test path. See `../loading/config.ts`.
   */
  loading?: OfficeLoadingConfigInput;
}

/**
 * The theme the host page asked for (`/qubit-office?theme=<id>`).
 *
 * Read from the host document, not from a prop: the plugin component receives
 * no props in production. `classic` is the default and is not propagated (the
 * iframe URL stays clean and the host loads nothing).
 */
function hostThemeRequest(): string {
  if (typeof window === "undefined") return "";
  const search = (window.location && window.location.search) || "";
  const value = (new URLSearchParams(search).get("theme") || "").trim();
  return value === "classic" ? "" : value;
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
  AgentControlDrawer: ReturnType<typeof createAgentControlDrawer>;
  WarRoomPanel: ReturnType<typeof createWarRoomPanel>;
  ApprovalsPanel: ReturnType<typeof createApprovalsPanel>;
  LoadingScreen: ReturnType<typeof createOfficeLoadingScreen>;
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
    AgentControlDrawer: createAgentControlDrawer(R, hooks),
    WarRoomPanel: createWarRoomPanel(R, hooks),
    ApprovalsPanel: createApprovalsPanel(R, hooks),
    LoadingScreen: createOfficeLoadingScreen(R, hooks),
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
    // The host's theme (the shell AROUND the iframe). The app document is
    // themed by the server (`_serve_app_html`); this copy only styles OUR
    // docks and the Agents panel, from the same validated payload.
    const [officeTheme, setOfficeTheme] = useState<OfficeTheme | null>(null);
    // The menu fallback: if the app's Panels group never appears (an upstream
    // DOM change), the shell keeps the Agents/HUD controls by rendering its own
    // traced-icon buttons after a bounded wait. Injection suppresses them the
    // moment it attaches, so the two are never visible at once.
    const [menuFallback, setMenuFallback] = useState(false);
    // A retry must actually re-navigate the iframe; the token is appended to
    // the src so assigning the same URL still reloads the document. Declared
    // LAST so existing state slots/tests keep their indices.
    const [retryToken, setRetryToken] = useState(0);
    // The War Room / role directory, and the one office seat whose Business OS
    // control record is open. Both are Qubit-shell docks; neither mutates the
    // vendored office document. Declared AFTER ``retryToken`` for the same
    // state-slot stability reason as the comment above.
    const [warRoomOpen, setWarRoomOpen] = useState(false);
    const [controlSeat, setControlSeat] = useState<AgentSeat | null>(null);
    // The Approvals dock and its real pending count (the injected menu icon's
    // badge). Declared AFTER every existing slot so seeded state indices in the
    // shell's own tests stay stable.
    const [approvalsOpen, setApprovalsOpen] = useState(false);
    const [approvalCount, setApprovalCount] = useState(0);
    const requestedTheme = hostThemeRequest();
    // Stable across renders — see shellComponents().
    const {
      HudDock, AgentsPanel, SeatModelPanel, AgentControlDrawer, WarRoomPanel,
      ApprovalsPanel, LoadingScreen,
    } = shellComponents(R, hooks, props.base);
    const frameRef = useRef<HTMLIFrameElement | null>(null);
    // The two icon buttons installed into the office's own panel group. The
    // controller lives for one iframe load; a reload (or a new base) swaps it.
    const menuRef = useRef<OfficeMenuController | null>(null);

    // Load the host theme ONCE per request/base. The server injects the
    // resolved payload as `window.__QUBIT_OFFICE_THEME__` (config/env included),
    // so this normally makes NO request; the authenticated fetch is the
    // fallback for a shell served outside Qubit. A classic payload is a valid
    // answer and simply applies nothing.
    useEffect(() => {
      let cancelled = false;
      const hostWindow = typeof window === "undefined" ? null : window;
      const fetchImpl = (hostWindow && typeof hostWindow.fetch === "function")
        ? hostWindow.fetch.bind(hostWindow) : null;
      loadOfficeTheme(fetchImpl as never, props.base, requestedTheme, hostWindow)
        .then((theme) => { if (!cancelled) setOfficeTheme(theme); })
        .catch(() => { if (!cancelled) setOfficeTheme(null); });
      return () => { cancelled = true; };
    }, [props.base, requestedTheme]);

    // Apply the host theme to THIS document (the shell around the iframe).
    // `applyHostTheme` returns a disposer that removes only what it installed,
    // so a classic/reload/teardown leaves no attribute or link behind.
    useEffect(() => {
      if (typeof document === "undefined") return;
      return applyHostTheme(document, officeTheme);
    }, [officeTheme]);

    // The pending-approval count for the injected badge. Read-only, on the
    // existing session-token-gated route, at a slow cadence; the panel itself
    // re-reads on open and after every action and reports its own count, so
    // this only keeps the badge honest while the dock is closed. Guarded on
    // `window` so the node-env shell tests issue no request at all.
    useEffect(() => {
      if (typeof window === "undefined") return;
      let live = true;
      const read = () => {
        fetchApprovals({ base: props.base }).then((next) => {
          if (live && next.kind === "ready") setApprovalCount(next.items.length);
        });
      };
      read();
      const handle = window.setInterval(read, 30000);
      return () => {
        live = false;
        window.clearInterval(handle);
      };
    }, [props.base]);

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
        installOfficeBrand(win);
        // Put the Agents and HUD icons where the office keeps its own panel
        // buttons. The document is same-origin, so the shell can append to the
        // group; the buttons drive THIS component's dock state.
        menuRef.current?.dispose();
        setMenuFallback(false);
        menuRef.current = installOfficeMenuButtons(win, {
          onToggleAgents: () => {
            setApprovalsOpen(false);
            setAgentsOpen((v: boolean) => !v);
          },
          onToggleHud: () => {
            setApprovalsOpen(false);
            setControlSeat(null);
            setHudOpen((v: boolean) => !v);
          },
          onToggleApprovals: () => {
            setAgentsOpen(false);
            setHudOpen(false);
            setWarRoomOpen(false);
            setControlSeat(null);
            setApprovalsOpen((v: boolean) => !v);
          },
          onFallback: (show: boolean) => setMenuFallback(show),
          onAttach: () => setMenuFallback(false),
        });
        menuRef.current?.update({
          agents: agentsOpen, hud: hudOpen,
          approvals: approvalsOpen, pendingApprovals: approvalCount,
        });
        props.onReady?.();
      };
      const onError = () => setFailed("The office app failed to load.");
      frame.addEventListener("load", onLoad);
      frame.addEventListener("error", onError);
      setFailed(null);
      setLoaded(false);
      const baseSrc = officeAppSrc({
        base: props.base, mock: props.mock, theme: requestedTheme,
      });
      frame.src = retryToken
        ? `${baseSrc}${baseSrc.includes("?") ? "&" : "?"}_retry=${retryToken}`
        : baseSrc;
      return () => {
        frame.removeEventListener("load", onLoad);
        frame.removeEventListener("error", onError);
        menuRef.current?.dispose();
        menuRef.current = null;
        restoreBridge?.();
      };
    }, [props.base, props.mock, requestedTheme, retryToken]);

    // Mirror the dock state onto the injected buttons after every render: the
    // office paints its own active state from `aria-pressed`, and the
    // Approvals icon carries the live pending count badge.
    useEffect(() => {
      menuRef.current?.update({
        agents: agentsOpen, hud: hudOpen,
        approvals: approvalsOpen, pendingApprovals: approvalCount,
      });
    });

    // Optional 9-slice: the pack declares which shell roles carry art, and
    // only those elements opt in. The generated host stylesheet targets
    // [data-office-slice], so an absent/classic theme emits no border-image.
    const dockSlice = officeSliceName(officeTheme, "dock");
    const buttonSlice = officeSliceName(officeTheme, "button");

    const children: unknown[] = [
      R.createElement("iframe", {
        key: "frame",
        ref: frameRef,
        title: OFFICE_BRAND.title,
        "data-testid": "office-app-frame",
        className: "office-game__frame",
        "aria-label": OFFICE_BRAND.name,
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
      }, `Interactive ${OFFICE_BRAND.name}. Use the controls within the game; unsupported write actions are disabled.`),
      // The persistent Credits/About surface. It is part of the Qubit shell,
      // not the vendored client and not the theme, so the LimeZu attribution
      // stays visible with `classic` and after a theme is removed.
      R.createElement("div", {
        key: "credits",
        className: "office-game__credits",
        "data-testid": "office-app-credits",
        role: "note",
        "aria-label": "Art credit",
      }, R.createElement("a", {
        key: "limezu",
        href: OFFICE_ART_CREDIT_URL,
        target: "_blank",
        rel: "noopener noreferrer",
      }, OFFICE_ART_CREDIT),
      R.createElement("span", { key: "sep" }, " · "),
      R.createElement("a", {
        key: "legal",
        href: OFFICE_BRAND.legalUrl,
        title: `${OFFICE_BRAND.upstream} @ ${OFFICE_BRAND.upstreamCommit}`,
      }, OFFICE_BRAND.legalLabel)),
    ];

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
        className: "office-game__dock office-game__agentsDock office-game__panel office-theme-dock office-theme-agents-dock",
        "data-testid": "office-game-agents-dock",
        "data-office-dock": "agents",
        "data-office-slice": dockSlice,
        role: "region",
        "aria-label": "Office agents",
      }, [
        R.createElement("div", { key: "head", className: "office-game__dockHead office-theme-dock-head" }, [
          R.createElement("span", { key: "t", className: "office-theme-dock-title" }, "Agents"),
          R.createElement("button", {
            key: "warroom", type: "button",
            className: "office-game__btn office-theme-dock-link",
            "data-testid": "office-game-warroom-open",
            "aria-label": "Open War Room and role directory",
            "data-office-slice": buttonSlice,
            onClick: () => {
              setAgentsOpen(false);
              setApprovalsOpen(false);
              setControlSeat(null);
              setWarRoomOpen(true);
            },
          }, "War Room"),
          R.createElement("button", {
            key: "x", type: "button", className: "office-game__btn office-theme-dock-close",
            "data-testid": "office-game-agents-close", "aria-label": "Close agents",
            "data-office-slice": buttonSlice,
            onClick: () => setAgentsOpen(false),
          }, "Close"),
        ]),
        R.createElement(AgentsPanel as never, {
          key: "body", base: props.base, theme: officeTheme,
          onOpenAgent: (seat: AgentSeat) => {
            setAgentsOpen(false);
            setHudOpen(false);
            setApprovalsOpen(false);
            setControlSeat(seat);
          },
        }),
      ]));
    }

    // THE WAR ROOM / IDEA LAB. A Qubit shell dock beside the office, opened
    // from the Agents dock header. It reads/writes only the durable Business
    // OS (owner-scoped), never the vendored office document.
    if (warRoomOpen) {
      children.push(R.createElement("div", {
        key: "warroom-dock",
        id: "office-game-warroom-dock",
        className: "office-game__dock office-game__panel office-theme-dock office-game__warRoomDock",
        "data-testid": "office-game-warroom-dock",
        "data-office-dock": "warroom",
        "data-office-slice": dockSlice,
        role: "region",
        "aria-label": "War Room and role directory",
      }, [
        R.createElement(WarRoomPanel as never, {
          key: "body", base: props.base,
          onClose: () => setWarRoomOpen(false),
        }),
      ]));
    }

    // THE AGENT CONTROL DRAWER. Opened by clicking a seat in the Agents panel.
    // It resolves that seat to a Business OS agent by an explicit durable link
    // and says so when none exists; it never invents a control record.
    if (controlSeat) {
      children.push(R.createElement("div", {
        key: "agent-control-dock",
        id: "office-game-agent-control-dock",
        className: "office-game__dock office-game__panel office-theme-dock office-game__agentControlDock",
        "data-testid": "office-game-agent-control-dock",
        "data-office-dock": "agent-control",
        "data-office-slice": dockSlice,
        role: "region",
        "aria-label": "Agent controls",
      }, [
        R.createElement(AgentControlDrawer as never, {
          key: "body", base: props.base, seat: controlSeat,
          onClose: () => setControlSeat(null),
        }),
      ]));
    }

    // THE APPROVALS DOCK. The pending records on the SHARED journal, opened
    // from the Approvals icon injected into the office's own panel group (or
    // the host fallback). It is an exclusive right-hand sheet: opening it
    // closes the other docks so two sheets can never cover each other, while
    // every existing dock remains available and unchanged.
    if (approvalsOpen) {
      children.push(R.createElement("div", {
        key: "approvals-dock",
        id: "office-game-approvals-dock",
        className: "office-game__dock office-game__panel office-theme-dock office-game__approvalsDock",
        "data-testid": "office-game-approvals-dock",
        "data-office-dock": "approvals",
        "data-office-slice": dockSlice,
        role: "region",
        "aria-label": "Office approvals",
      }, [
        R.createElement("div", { key: "head", className: "office-game__dockHead office-theme-dock-head" }, [
          R.createElement("span", { key: "t", className: "office-theme-dock-title" }, "Approvals"),
          R.createElement("span", {
            key: "count",
            className: "office-game__approvalsCount",
            "data-testid": "office-game-approvals-count",
            "data-count": String(approvalCount),
          }, String(approvalCount)),
          R.createElement("button", {
            key: "x", type: "button", className: "office-game__btn office-theme-dock-close",
            "data-testid": "office-game-approvals-close", "aria-label": "Close approvals",
            "data-office-slice": buttonSlice,
            onClick: () => setApprovalsOpen(false),
          }, "Close"),
        ]),
        R.createElement(ApprovalsPanel as never, {
          key: "body", base: props.base,
          onCountChange: (n: number) => setApprovalCount(n),
          onClose: () => setApprovalsOpen(false),
        }),
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
        className: "office-game__dock office-game__panel office-theme-dock office-theme-hud-dock",
        "data-testid": "office-game-hud-dock",
        "data-office-dock": "hud",
        "data-office-slice": dockSlice,
        role: "region",
        "aria-label": "HUD",
      }, [
        R.createElement("div", { key: "head", className: "office-game__dockHead office-theme-dock-head" }, [
          R.createElement("span", { key: "t", className: "office-theme-dock-title" }, "HUD"),
          R.createElement("a", {
            key: "open",
            className: "office-game__hudLink office-theme-dock-link",
            "data-testid": "office-game-hud-open-link",
            href: hudFrameSrc(props.base),
          }, "Open HUD"),
          R.createElement("button", {
            key: "x", type: "button", className: "office-game__btn office-theme-dock-close",
            "data-testid": "office-game-hud-close", "aria-label": "Close HUD",
            "data-office-slice": buttonSlice,
            onClick: () => setHudOpen(false),
          }, "Close"),
        ]),
        R.createElement(HudDock as never, { key: "body" }),
      ]));
    }

    // HOST-RENDERED MENU FALLBACK. The app's own Panels group can disappear
    // across an upstream DOM change; the injected icons would then vanish with
    // it. After the bounded wait the controller asks for these shell buttons,
    // and the first successful injection removes them again.
    if (menuFallback) {
      children.push(R.createElement("div", {
        key: "menu-fallback",
        className: "office-game__menuFallback office-theme-menu-fallback",
        "data-testid": "office-menu-fallback",
        role: "group",
        "aria-label": "Office panels",
      }, MENU_BUTTONS.map((spec: MenuButtonSpec) => {
        const geometry = menuIconGeometry(spec);
        const pressed = spec.id === "agents" ? agentsOpen
          : spec.id === "hud" ? hudOpen : approvalsOpen;
        const onActivate = spec.id === "agents"
          ? () => {
              setApprovalsOpen(false);
              setAgentsOpen((v: boolean) => !v);
            }
          : spec.id === "hud"
          ? () => {
              setApprovalsOpen(false);
              setControlSeat(null);
              setHudOpen((v: boolean) => !v);
            }
          : () => {
              setAgentsOpen(false);
              setHudOpen(false);
              setWarRoomOpen(false);
              setControlSeat(null);
              setApprovalsOpen((v: boolean) => !v);
            };
        const label = spec.id === "approvals" && approvalCount > 0
          ? `${spec.label} (${approvalCount} pending)` : spec.label;
        const parts: unknown[] = [R.createElement("svg", {
          key: "icon",
          className: "ui-px-icon",
          width: geometry.width,
          height: geometry.height,
          viewBox: geometry.viewBox,
          shapeRendering: "crispEdges",
          "aria-hidden": "true",
          focusable: "false",
        }, R.createElement("path", {
          fill: "currentColor",
          d: geometry.d,
        }))];
        if (spec.id === "approvals" && approvalCount > 0) {
          parts.push(R.createElement("span", {
            key: "badge", className: "office-menu-badge",
            "data-testid": "office-menu-approvals-badge-fallback",
            "aria-hidden": "true",
          }, String(approvalCount)));
        }
        return R.createElement("button", {
          key: spec.id,
          type: "button",
          className: `office-game__btn office-theme-menu-btn office-theme-menu-${spec.id} office-theme-menu-fallback-btn`,
          "data-testid": `${spec.testId}-fallback`,
          "aria-label": label,
          title: spec.title,
          "aria-pressed": pressed ? "true" : "false",
          "data-pending": spec.id === "approvals" ? String(approvalCount) : undefined,
          "data-office-slice": buttonSlice,
          onClick: onActivate,
        }, ...parts);
      })));
    }

    // THE LOADING SCREEN. One import, one render seam: it overlays the iframe,
    // owns the weather/pixelfx layer, and fades itself out on `ready`. It is
    // ALWAYS mounted so a failed frame can show the same screen with a retry;
    // when it has faded it returns null and disposes its surfaces.
    children.push(R.createElement(LoadingScreen as never, {
      key: "loading-screen",
      phase: failed ? "error" : "loading",
      ready: loaded,
      error: failed
        ? `${failed} Check Qubit's office connection; do not start a separate upstream server. (${OFFICE_APP_PATH})`
        : null,
      config: props.loading,
      onRetry: () => {
        setFailed(null);
        setLoaded(false);
        setRetryToken((n: number) => n + 1);
      },
    }));

    const rootClass = ["office-game", officeThemeClass(officeTheme)]
      .filter(Boolean).join(" ");
    // The two side sheets are anchored to opposite edges, but the office host
    // is only ~1145px wide at a 1440px viewport (dashboard sidebar + padding).
    // Two `min(44rem, 92%)` sheets are wider than that together, so the right
    // HUD sheet covered the Agents sheet's close button. The marker lets the
    // stylesheet split the row into two non-overlapping halves with a gap;
    // a single sheet keeps its original width because the selector only
    // matches while both are open.
    const dualDocks = (agentsOpen && hudOpen)
      || (warRoomOpen && controlSeat !== null);
    // Either sheet open means the root is marked so the stylesheet can start
    // the sheets BELOW the vendored app's fixed top bar. That bar hosts the
    // injected Panels group that opens them; an offset sheet leaves the group
    // visible and clickable (measured: HUD top≈75 covered Panels at y≈90).
    const dockOpen = agentsOpen || hudOpen || warRoomOpen
      || controlSeat !== null || approvalsOpen;
    return R.createElement("div", {
      "data-testid": "office-app",
      "data-office-theme": officeTheme && officeTheme.active ? officeTheme.id : undefined,
      "data-office-docks": dualDocks ? "both" : undefined,
      "data-office-dock-top": dockOpen ? "toolbar" : undefined,
      className: rootClass,
    }, children);
  };
}
