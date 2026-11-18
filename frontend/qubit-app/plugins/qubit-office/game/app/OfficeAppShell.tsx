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
import { isEditableTarget } from "./command-center";
import { createAgentsPanel } from "./AgentsPanel";
import { createAgentControlDrawer } from "./AgentControlDrawer";
import { createWarRoomPanel } from "./WarRoomPanel";
import { createApprovalsPanel } from "./ApprovalsPanel";
import { createCommandCenter } from "./CommandCenter";
import { createTimeline } from "./Timeline";
import { createRoomBuilder } from "./RoomBuilder";
import { createOutsideWorld } from "./OutsideWorld";
import { createPanelInventoryPanel } from "./PanelInventory";
import { createCampusWorld } from "./CampusWorld";
import { createTowerNavigator } from "./TowerNavigator";
import { fetchDirectory, type BusinessDirectory } from "./business-api";
import { fetchWorldTower, type WorldTower } from "./world-tower";
import { fetchOutsideWorld, type OutsideWorldView } from "./outside-world";
import { createEtsyConnectPanel } from "./EtsyConnectPanel";
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
import {
  officeFx, sakugaButton, sakugaLoading, sakugaProgress,
} from "./sakuga";
import {
  captureFocus,
  focusFirst,
  installEscapeToShell,
  onMediaQueryChange,
  onReducedMotionChange,
  officeAnnouncement,
  prefersHighContrast,
  prefersReducedMotion,
  restoreFocus,
} from "./a11y";

/** Always-present art credit; it must not depend on a theme being active. */
export const OFFICE_ART_CREDIT = OFFICE_BRAND.credit;
export const OFFICE_ART_CREDIT_URL = OFFICE_BRAND.creditUrl;

export interface OfficeAppProps {
  /** The dashboard's base path (empty at the root). */
  base?: string;
  /** Serve the client's own demo data. Off by default — see office-app.ts. */
  mock?: boolean;
  /**
   * Optional fetch override for the shell's own reads (campus directory,
   * outside world). Production leaves it undefined and uses the host fetch;
   * tests inject a hermetic one.
   */
  fetchImpl?: typeof fetch;
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
  /** Injected fetch for tests/hosts; the live paths default to `fetch`. */
  fetchImpl?: typeof fetch;
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
  CommandCenter: ReturnType<typeof createCommandCenter>;
  Timeline: ReturnType<typeof createTimeline>;
  RoomBuilder: ReturnType<typeof createRoomBuilder>;
  OutsideWorld: ReturnType<typeof createOutsideWorld>;
  PanelInventory: ReturnType<typeof createPanelInventoryPanel>;
  CampusWorld: ReturnType<typeof createCampusWorld>;
  TowerNavigator: ReturnType<typeof createTowerNavigator>;
  EtsyConnectPanel: ReturnType<typeof createEtsyConnectPanel>;
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
    CommandCenter: createCommandCenter(R, hooks),
    Timeline: createTimeline(R, hooks),
    RoomBuilder: createRoomBuilder(R, hooks),
    OutsideWorld: createOutsideWorld(R, hooks),
    PanelInventory: createPanelInventoryPanel(R, hooks),
    CampusWorld: createCampusWorld(R, hooks),
    TowerNavigator: createTowerNavigator(R, hooks),
    EtsyConnectPanel: createEtsyConnectPanel(R, hooks),
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
function initialFloorScope(): string {
  if (typeof window === "undefined") return "";
  try {
    return String(new URLSearchParams(window.location.search).get("floor") || "")
      .trim();
  } catch {
    return "";
  }
}

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
    // The Etsy OAuth connection surface. Declared last so every seeded state
    // index in the shell's own tests stays stable.
    const [etsyOpen, setEtsyOpen] = useState(false);
    // The command-center shell region. Declared last so every seeded state
    // index in the shell's own tests stays stable.
    const [commandCenterOpen, setCommandCenterOpen] = useState(false);
    // The operator timeline (P4-07). Declared last so every seeded state
    // index in the shell's own tests stays stable.
    const [timelineOpen, setTimelineOpen] = useState(false);
    // Phase 6 Studio dock (panel inventory / room builder / outside world).
    const [studioOpen, setStudioOpen] = useState(false);
    const [studioTab, setStudioTab] = useState<"panels" | "rooms" | "outside">(
      "panels");
    const [outsideView, setOutsideView] = useState<OutsideWorldView | null>(
      null);
    // The Habblaud office is the primary surface. Campus places its real
    // floors on one shared stage; it is not a directory and not a second renderer.
    const [campusOpen, setCampusOpen] = useState(false);
    const [floorScope, setFloorScope] = useState<string>(
      initialFloorScope());
    const [campusTab, setCampusTab] = useState<"floors" | "directory">("floors");
    const [campusDirectory, setCampusDirectory] = useState<
      BusinessDirectory | null>(null);
    const [campusWorld, setCampusWorld] = useState<WorldTower | null>(null);
    const [campusState, setCampusState] = useState<
      "loading" | "ready" | "error">("loading");
    const [campusError, setCampusError] = useState<string>("");
    // P6-05 accessibility preferences. Declared AFTER every earlier slot so
    // seeded state indices in the shell's own tests stay stable. The OS
    // preference is honoured by default; an explicit shell setting could win
    // later without changing the token contract.
    const [reducedMotion, setReducedMotion] = useState<boolean>(
      prefersReducedMotion());
    const [highContrast, setHighContrast] = useState<boolean>(
      prefersHighContrast());

    // THE PANEL TOGGLES. Every dock is opened from the office's OWN icon group
    // (the top-right Panels row), so the shell no longer paints a text-button
    // toolbar at the bottom of the screen. The callbacks keep the previous
    // exclusivity rules: a side sheet closes the others, while Agents and HUD
    // may share the row (the stylesheet splits it). Campus keeps its original
    // behavior because it is a full-surface view, not a side sheet.
    const toggleAgents = () => {
      if (!agentsOpen) officeFx("impact");
      setApprovalsOpen(false);
      setCommandCenterOpen(false);
      setTimelineOpen(false);
      setStudioOpen(false);
      setAgentsOpen((v: boolean) => !v);
    };
    const toggleHud = () => {
      if (!hudOpen) officeFx("impact");
      setApprovalsOpen(false);
      setCommandCenterOpen(false);
      setTimelineOpen(false);
      setStudioOpen(false);
      setControlSeat(null);
      setHudOpen((v: boolean) => !v);
    };
    const toggleApprovals = () => {
      if (!approvalsOpen) officeFx("impact");
      setAgentsOpen(false);
      setHudOpen(false);
      setWarRoomOpen(false);
      setCommandCenterOpen(false);
      setTimelineOpen(false);
      setStudioOpen(false);
      setControlSeat(null);
      setApprovalsOpen((v: boolean) => !v);
    };
    const toggleCommandCenter = () => {
      if (!commandCenterOpen) officeFx("impact");
      setAgentsOpen(false);
      setHudOpen(false);
      setWarRoomOpen(false);
      setApprovalsOpen(false);
      setEtsyOpen(false);
      setTimelineOpen(false);
      setStudioOpen(false);
      setControlSeat(null);
      setCommandCenterOpen((v: boolean) => !v);
    };
    const toggleTimeline = () => {
      if (!timelineOpen) officeFx("impact");
      setAgentsOpen(false);
      setHudOpen(false);
      setWarRoomOpen(false);
      setApprovalsOpen(false);
      setEtsyOpen(false);
      setCommandCenterOpen(false);
      setStudioOpen(false);
      setControlSeat(null);
      setTimelineOpen((v: boolean) => !v);
    };
    const toggleStudio = () => {
      if (!studioOpen) officeFx("impact");
      setAgentsOpen(false);
      setHudOpen(false);
      setWarRoomOpen(false);
      setApprovalsOpen(false);
      setEtsyOpen(false);
      setCommandCenterOpen(false);
      setTimelineOpen(false);
      setControlSeat(null);
      setStudioOpen((v: boolean) => !v);
    };
    const toggleCampus = () => {
      if (!campusOpen) officeFx("impact");
      setCampusOpen((v: boolean) => !v);
    };
    const toggleFor = (id: string): (() => void) => {
      switch (id) {
        case "agents": return toggleAgents;
        case "hud": return toggleHud;
        case "approvals": return toggleApprovals;
        case "command-center": return toggleCommandCenter;
        case "timeline": return toggleTimeline;
        case "studio": return toggleStudio;
        case "campus": return toggleCampus;
        default: return () => {};
      }
    };

    const requestedTheme = hostThemeRequest();
    // Stable across renders — see shellComponents().
    const {
      HudDock, AgentsPanel, SeatModelPanel, AgentControlDrawer, WarRoomPanel,
      ApprovalsPanel, CommandCenter, Timeline, RoomBuilder, OutsideWorld,
      PanelInventory, CampusWorld, TowerNavigator, EtsyConnectPanel,
      LoadingScreen,
    } = shellComponents(R, hooks, props.base);
    useEffect(() => {
      if (!studioOpen || studioTab !== "outside") return;
      let live = true;
      const ctl = typeof AbortController === "undefined" ? undefined
        : new AbortController();
      fetchOutsideWorld({ base: props.base, signal: ctl?.signal })
        .then((view) => { if (live) setOutsideView(view); });
      return () => { live = false; ctl?.abort(); };
    }, [studioOpen, studioTab, props.base]);
    useEffect(() => {
      if (!campusOpen || campusTab !== "directory") return;
      let live = true;
      const ctl = typeof AbortController === "undefined" ? undefined
        : new AbortController();
      setCampusState("loading");
      setCampusError("");
      fetchDirectory({ base: props.base, fetchImpl: props.fetchImpl,
        signal: ctl?.signal })
        .then(async (directory) => {
          const world = await fetchWorldTower(directory.floors, {
            base: props.base, fetchImpl: props.fetchImpl,
            signal: ctl?.signal,
          });
          if (!live) return;
          setCampusDirectory(directory);
          setCampusWorld(world);
          setCampusState("ready");
          officeFx("success");
        })
        .catch((exc) => {
          if (!live) return;
          setCampusState("error");
          setCampusError(exc instanceof Error ? exc.message : String(exc));
          officeFx("error");
        });
      return () => { live = false; ctl?.abort(); };
    }, [campusOpen, campusTab, props.base, props.fetchImpl]);
    const frameRef = useRef<HTMLIFrameElement | null>(null);
    // Previous pending count, for the one cue this shell owns: a NEW pending
    // approval is an event the operator must notice, not decoration. Declared
    // AFTER frameRef so the first ref slot stays the iframe (the shell tests
    // seed it by position).
    const approvalCountRef = useRef(0);
    // The two icon buttons installed into the office's own panel group. The
    // controller lives for one iframe load; a reload (or a new base) swaps it.
    const menuRef = useRef<OfficeMenuController | null>(null);
    // P6-05 focus management: which dock is open, and what had focus before it
    // opened. A ref (not state) so restoring focus never causes a render.
    const openDockRef = useRef<string | null>(null);
    const focusReturnRef = useRef<HTMLElement | null>(null);

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

    // P6-05 — the single open dock (sheet) and the label a live region uses.
    // Order mirrors exclusivity: the later sheets close the earlier ones, so at
    // most one id is ever non-null.
    const openDockId = agentsOpen ? "office-game-agents-dock"
      : hudOpen ? "office-game-hud-dock"
      : warRoomOpen ? "office-game-warroom-dock"
      : controlSeat ? "office-game-agent-control-dock"
      : approvalsOpen ? "office-game-approvals-dock"
      : etsyOpen ? "office-game-etsy-dock"
      : commandCenterOpen ? "office-game-command-center-dock"
      : timelineOpen ? "office-game-timeline-dock"
      : null;
    const openDockLabel = agentsOpen ? "Agents"
      : hudOpen ? "HUD"
      : warRoomOpen ? "War Room"
      : controlSeat ? "Agent controls"
      : approvalsOpen ? "Approvals"
      : etsyOpen ? "Etsy connection"
      : commandCenterOpen ? "Command Center"
      : timelineOpen ? "Timeline"
      : null;

    // Honour the OS reduced-motion and high-contrast preferences and keep the
    // root attributes in sync if they change while the page is open. The CSS
    // tokens already consume both attributes; setting them here is what makes
    // the shell (not just the vendored frame's own setting) obey the system.
    useEffect(() => {
      if (typeof window === "undefined") return;
      setReducedMotion(prefersReducedMotion());
      setHighContrast(prefersHighContrast());
      const offReduced = onReducedMotionChange((next: boolean) => setReducedMotion(next));
      const offContrast = onMediaQueryChange(
        "(prefers-contrast: more)",
        () => setHighContrast(prefersHighContrast()),
      );
      return () => { offReduced(); offContrast(); };
    }, []);

    // P6-05 — remember the control that opened a sheet. A capture-phase click
    // listener records it BEFORE React's onClick renders the dock, so a child
    // panel that focuses its own search field cannot steal the captured opener
    // (child effects run before the parent's, which made a restore target the
    // newly mounted input instead of the button the operator pressed).
    useEffect(() => {
      const doc = typeof document === "undefined" ? null : document;
      if (!doc) return;
      const remember = (event: Event) => {
        const target = event.target as HTMLElement | null;
        const control = target?.closest?.(
          "button, a, [role='button'], input, select, textarea") as HTMLElement | null;
        if (control) focusReturnRef.current = control;
      };
      doc.addEventListener("click", remember, true);
      return () => doc.removeEventListener("click", remember, true);
    }, []);

    // P6-05 — focus restoration. When a sheet opens, move focus into it; when
    // it closes, put focus back (or on the main landmark if the opener is
    // gone). Side sheets are not traps, so the office canvas stays usable.
    useEffect(() => {
      const doc = typeof document === "undefined" ? null : document;
      if (!doc) return;
      const previous = openDockRef.current;
      if (openDockId && openDockId !== previous) {
        if (!focusReturnRef.current) {
          const captured = captureFocus(doc);
          focusReturnRef.current = captured.element;
        }
        const dock = doc.getElementById(openDockId);
        if (dock) focusFirst(dock as HTMLElement);
      } else if (!openDockId && previous) {
        const target = focusReturnRef.current;
        if (!restoreFocus(target, doc)) {
          const main = doc.getElementById("office-game-main") as HTMLElement | null;
          main?.focus?.();
        }
        focusReturnRef.current = null;
      }
      openDockRef.current = openDockId;
    }, [openDockId]);

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
          if (!live || next.kind !== "ready") return;
          const count = next.items.length;
          if (count > approvalCountRef.current) officeFx("approval");
          approvalCountRef.current = count;
          setApprovalCount(count);
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
      // The vendored client owns its document and may swallow Tab. Give a
      // keyboard user a guaranteed escape back to the shell controls.
      let restoreEscape: (() => void) | undefined;
      const onLoad = () => {
        setLoaded(true);
        setFailed(null);
        installOfficeBrand(win);
        restoreEscape?.();
        // Resolve the frame window AT LOAD, not from the pre-navigation
        // closure: on a navigation the earlier window wrapper can still be
        // around while the live document belongs to the loaded frame.
        const loadedWindow = frame.contentWindow as Window | null;
        restoreEscape = installEscapeToShell(loadedWindow, () => {
          if (typeof document === "undefined") return;
          const controls = document.getElementById("office-game-controls");
          const main = document.getElementById("office-game-main");
          (controls ?? main)?.focus?.();
        });
        // Put the Agents and HUD icons where the office keeps its own panel
        // buttons. The document is same-origin, so the shell can append to the
        // group; the buttons drive THIS component's dock state.
        menuRef.current?.dispose();
        setMenuFallback(false);
        menuRef.current = installOfficeMenuButtons(win, {
          onToggleAgents: toggleAgents,
          onToggleHud: toggleHud,
          onToggleApprovals: toggleApprovals,
          onToggleCommandCenter: toggleCommandCenter,
          onToggleTimeline: toggleTimeline,
          onToggleStudio: toggleStudio,
          onToggleCampus: toggleCampus,
          onFallback: (show: boolean) => setMenuFallback(show),
          onAttach: () => setMenuFallback(false),
        });
        menuRef.current?.update({
          agents: agentsOpen, hud: hudOpen,
          approvals: approvalsOpen, pendingApprovals: approvalCount,
          commandCenter: commandCenterOpen, timeline: timelineOpen,
          studio: studioOpen, campus: campusOpen,
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
        floor: typeof floorScope === "string" ? floorScope : "",
      });
      frame.src = retryToken
        ? `${baseSrc}${baseSrc.includes("?") ? "&" : "?"}_retry=${retryToken}`
        : baseSrc;
      return () => {
        frame.removeEventListener("load", onLoad);
        frame.removeEventListener("error", onError);
        menuRef.current?.dispose();
        menuRef.current = null;
        restoreEscape?.();
        restoreBridge?.();
      };
    }, [props.base, props.mock, requestedTheme, floorScope, retryToken]);

    // Keep the URL shareable/reload-safe while staying on the office page.
    useEffect(() => {
      if (typeof window === "undefined") return;
      const url = new URL(window.location.href);
      if (typeof floorScope === "string" && floorScope) {
        url.searchParams.set("floor", floorScope);
      } else {
        url.searchParams.delete("floor");
      }
      window.history.replaceState(null, "", `${url.pathname}${url.search}${url.hash}`);
    }, [floorScope]);

    // Programmatic panel control. The injected menu is the visible surface;
    // this event lets the host, voice routing and tests open the same panels
    // without adding a second text toolbar.
    useEffect(() => {
      if (typeof window === "undefined") return;
      const onToggle = (event: Event) => {
        const detail = (event as CustomEvent).detail;
        const panel = typeof detail === "string"
          ? detail
          : (detail && typeof detail === "object" ? detail.panel : "");
        switch (panel) {
          case "command-center": setCommandCenterOpen(true); break;
          case "timeline": setTimelineOpen(true); break;
          case "studio": setStudioOpen(true); break;
          case "campus": setCampusOpen((v: boolean) => !v); break;
          case "agents": setAgentsOpen((v: boolean) => !v); break;
          case "hud": setHudOpen((v: boolean) => !v); break;
          case "approvals": setApprovalsOpen((v: boolean) => !v); break;
          default: break;
        }
      };
      window.addEventListener("qubit-office:toggle", onToggle);
      return () => window.removeEventListener("qubit-office:toggle", onToggle);
    }, []);

    // Mirror the dock state onto the injected buttons after every render: the
    // office paints its own active state from `aria-pressed`, and the
    // Approvals icon carries the live pending count badge.
    useEffect(() => {
      menuRef.current?.update({
        agents: agentsOpen, hud: hudOpen,
        approvals: approvalsOpen, pendingApprovals: approvalCount,
        commandCenter: commandCenterOpen, timeline: timelineOpen,
        studio: studioOpen, campus: campusOpen,
      });
    });

    // The office-wide command-center shortcut. It opens the shell region and
    // starts a fresh search; Escape/close is handled inside the region. Ignore
    // editable targets so typing "/" in any field is never hijacked.
    useEffect(() => {
      if (typeof window === "undefined") return;
      const onKey = (event: KeyboardEvent) => {
        const command = Boolean(event.ctrlKey || event.metaKey);
        const key = String(event.key || "");
        const opens = (command && (key === "k" || key === "K"))
          || (key === "/" && !isEditableTarget(event.target));
        if (!opens) return;
        event.preventDefault();
        setAgentsOpen(false);
        setHudOpen(false);
        setWarRoomOpen(false);
        setApprovalsOpen(false);
        setEtsyOpen(false);
        setTimelineOpen(false);
        setControlSeat(null);
        setCommandCenterOpen(true);
      };
      window.addEventListener("keydown", onKey);
      return () => window.removeEventListener("keydown", onKey);
    }, []);

    // Optional 9-slice: the pack declares which shell roles carry art, and
    // only those elements opt in. The generated host stylesheet targets
    // [data-office-slice], so an absent/classic theme emits no border-image.
    const dockSlice = officeSliceName(officeTheme, "dock");
    const buttonSlice = officeSliceName(officeTheme, "button");

    const children: unknown[] = [
      // P6-05 — the first focusable thing on the page is a skip link, so a
      // keyboard user is never forced through the canvas to reach the shell.
      // The second link jumps PAST the canvas to the shell controls, which is
      // the guaranteed escape from a client that swallows Tab.
      R.createElement("a", {
        key: "skip-main",
        href: "#office-game-main",
        className: "office-game__skipLink",
        "data-testid": "office-app-skip-link",
      }, "Skip to office content"),
      R.createElement("a", {
        key: "skip-controls",
        href: "#office-game-controls",
        className: "office-game__skipLink office-game__skipLink--controls",
        "data-testid": "office-app-skip-controls",
      }, "Skip past the office canvas"),
      R.createElement("main", {
        key: "main",
        id: "office-game-main",
        className: "office-game__main",
        tabIndex: -1,
        "aria-label": `${OFFICE_BRAND.name} workspace`,
      }, [
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
        }, `Interactive ${OFFICE_BRAND.name}. Use the controls within the game; unsupported write actions are disabled. `
          + `Every critical status is also shown as text, so muting audio never hides it. `
          + `On touch screens the shell controls are at least 24 by 24 pixels.`),
      ]),

    ];

    // P6-05 — one polite live region carries loading, connection and dock
    // changes. It is always mounted (an inserted region is not reliably
    // announced) and its text is derived from real state, never a spinner.
    children.push(R.createElement("div", {
      key: "announcer",
      className: "office-game__sr-only",
      "data-testid": "office-app-announcer",
      role: "status",
      "aria-live": "polite",
      "aria-atomic": "true",
    }, officeAnnouncement({
      loaded,
      failed,
      state: props.officeState,
      rooms: props.officeRooms,
      agents: props.officeAgents,
      error: props.officeError,
      dockLabel: openDockLabel,
    })));

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
      if (state === "connecting") {
        children.push(sakugaProgress(R, {
          key: "connection-progress",
          className: "office-game__connectionProgress",
          "data-testid": "office-app-connection-progress",
          label: "Connecting to the office backend",
          value: 0.08,
          indeterminate: true,
        }));
      }
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
      children.push(sakugaButton(R, {
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
          sakugaButton(R, {
            key: "warroom", type: "button",
            className: "office-game__btn office-theme-dock-link",
            "data-testid": "office-game-warroom-open",
            "aria-label": "Open War Room and role directory",
            "data-office-slice": buttonSlice,
            onClick: () => {
              setAgentsOpen(false);
              setApprovalsOpen(false);
              setTimelineOpen(false);
              setControlSeat(null);
              setWarRoomOpen(true);
            },
          }, "War Room"),
          sakugaButton(R, {
            key: "etsy", type: "button",
            className: "office-game__btn office-theme-dock-link",
            "data-testid": "office-game-etsy-open",
            "aria-label": "Open the Etsy connection settings",
            "data-office-slice": buttonSlice,
            onClick: () => {
              setAgentsOpen(false);
              setApprovalsOpen(false);
              setTimelineOpen(false);
              setControlSeat(null);
              setWarRoomOpen(false);
              setHudOpen(false);
              setEtsyOpen(true);
            },
          }, "Etsy"),
          sakugaButton(R, {
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
          sakugaButton(R, {
            key: "x", type: "button", className: "office-game__btn office-theme-dock-close",
            "data-testid": "office-game-approvals-close", "aria-label": "Close approvals",
            "data-office-slice": buttonSlice,
            onClick: () => setApprovalsOpen(false),
          }, "Close"),
        ]),
        R.createElement(ApprovalsPanel as never, {
          key: "body", base: props.base,
          onCountChange: (n: number) => {
            if (n > approvalCountRef.current) officeFx("approval");
            approvalCountRef.current = n;
            setApprovalCount(n);
          },
          onClose: () => setApprovalsOpen(false),
        }),
      ]));
    }

    // THE ETSY CONNECTION DOCK. Opened from the Agents dock header. It reads
    // the typed OAuth status from the Business OS bridge, opens the Etsy
    // authorization URL and completes the redirect code; tokens stay on the
    // server and no listing/publish/spend action exists here.
    if (etsyOpen) {
      children.push(R.createElement("div", {
        key: "etsy-dock",
        id: "office-game-etsy-dock",
        className: "office-game__dock office-game__panel office-theme-dock office-game__etsyDock",
        "data-testid": "office-game-etsy-dock",
        "data-office-dock": "etsy",
        "data-office-slice": dockSlice,
        role: "region",
        "aria-label": "Etsy connection",
      }, [
        R.createElement(EtsyConnectPanel as never, {
          key: "body", base: props.base,
          onClose: () => setEtsyOpen(false),
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
          sakugaButton(R, {
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
          : spec.id === "hud" ? hudOpen
          : spec.id === "approvals" ? approvalsOpen
          : spec.id === "command-center" ? commandCenterOpen
          : spec.id === "timeline" ? timelineOpen
          : spec.id === "studio" ? studioOpen
          : campusOpen;
        const onActivate = toggleFor(spec.id);
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
        return sakugaButton(R, {
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

    // THE COMMAND CENTER. Opened from the office's own Panels icon. A
    // read-only projection over the real tower, roster,
    // tasks, sessions and approvals. It is an exclusive side sheet, so it can
    // never cover another dock's close button.
    if (commandCenterOpen) {
      children.push(R.createElement("div", {
        key: "command-center-dock",
        id: "office-game-command-center-dock",
        className: "office-game__dock office-game__panel office-theme-dock office-game__commandCenterDock",
        "data-testid": "office-game-command-center-dock",
        "data-office-dock": "command-center",
        "data-office-slice": dockSlice,
        role: "region",
        "aria-label": "Command center",
      }, [
        R.createElement(CommandCenter as never, {
          key: "body",
          base: props.base,
          onClose: () => setCommandCenterOpen(false),
        }),
      ]));
    }

    // P6-05 — a real toolbar landmark remains the keyboard escape hatch and
    // skip target, but it no longer paints text buttons: every panel is opened
    // from the office's own Panels icon group (or the host fallback below). An
    // empty landmark is intentionally invisible; its label still announces the
    // destination when the skip link or Escape focuses it.
    children.push(R.createElement("div", {
      key: "toolbar",
      id: "office-game-controls",
      className: "office-game__toolbar",
      "data-testid": "office-app-toolbar",
      role: "toolbar",
      "aria-label": "Office panels",
      tabIndex: -1,
    }, R.createElement("span", {
      key: "hint",
      className: "office-game__sr-only",
    }, "Office panel buttons are in the office toolbar at the top right.")));

    // THE OPERATOR TIMELINE. A read-only, evidence-backed projection over the
    // durable Business OS. It is a Qubit-shell dock: the iframe keeps its full
    // frame, the toolbar offset applies through the existing root marker, and
    // replay is labelled inside the panel.
    if (timelineOpen) {
      children.push(R.createElement("div", {
        key: "timeline-dock",
        id: "office-game-timeline-dock",
        className: "office-game__dock office-game__panel office-theme-dock office-game__timelineDock",
        "data-testid": "office-game-timeline-dock",
        "data-office-dock": "timeline",
        "data-office-slice": dockSlice,
        role: "region",
        "aria-label": "Operator timeline",
      }, [
        R.createElement(Timeline as never, {
          key: "body",
          base: props.base,
          onClose: () => setTimelineOpen(false),
        }),
      ]));
    }

    if (studioOpen) {
      const tab = (id: "panels" | "rooms" | "outside", label: string) =>
        sakugaButton(R, {
          key: `studio-tab-${id}`, type: "button",
          className: "office-game__btn office-game__studioTab",
          "data-testid": `office-studio-tab-${id}`,
          "aria-pressed": studioTab === id,
          onClick: () => {
            if (studioTab !== id) officeFx("tick");
            setStudioTab(id);
          },
        }, label);
      const body = studioTab === "rooms"
        ? R.createElement(RoomBuilder as never, { key: "rooms" })
        : studioTab === "outside"
        ? R.createElement(OutsideWorld as never, {
            key: "outside",
            view: outsideView ?? undefined,
            loading: outsideView === null,
          })
        : R.createElement(PanelInventory as never, {
            key: "panels", base: props.base,
          });
      children.push(R.createElement("div", {
        key: "studio-dock",
        id: "office-game-studio-dock",
        className: "office-game__dock office-game__panel office-theme-dock office-game__studioDock",
        "data-testid": "office-game-studio-dock",
        "data-office-dock": "studio",
        "data-office-slice": dockSlice,
        role: "region",
        "aria-label": "Office studio",
      }, [
        R.createElement("div", {
          key: "head", className: "office-game__dockHead office-theme-dock-head",
        }, [
          R.createElement("span", {
            key: "t", className: "office-theme-dock-title",
          }, "Studio"),
          tab("panels", "Panels"),
          tab("rooms", "Rooms"),
          tab("outside", "Outside"),
          sakugaButton(R, {
            key: "x", type: "button",
            className: "office-game__btn office-theme-dock-close",
            "data-testid": "office-game-studio-close",
            "aria-label": "Close studio",
            onClick: () => setStudioOpen(false),
          }, "Close"),
        ]),
        R.createElement("div", {
          key: "body", className: "office-game__studioBody",
        }, body),
      ]));
    }

    if (typeof floorScope === "string" && floorScope) {
      children.push(R.createElement("div", {
        key: "floor-scope",
        className: "office-game__floorScope",
        "data-testid": "office-floor-scope",
        role: "status",
      }, [
        R.createElement("span", { key: "t" },
          `Viewing ${floorScope.replace("live:", "Floor ")}`),
        sakugaButton(R, {
          key: "all", type: "button", className: "office-game__btn",
          "data-testid": "office-floor-scope-clear",
          onClick: () => {
            officeFx("pop");
            setFloorScope("");
          },
        }, "All floors"),
      ]));
    }

    if (campusOpen) {
      const directoryBody = campusState === "loading"
        ? R.createElement("div", {
            key: "loading", className: "office-game__campusState",
            "data-testid": "office-campus-loading",
          }, [
            R.createElement("span", { key: "text" }, "Loading the 5-floor directory…"),
            sakugaLoading(R, {
              key: "progress",
              className: "office-game__campusLoading",
              label: "Loading the 5-floor directory",
              showLabel: false,
              value: 0.12,
            }),
          ])
        : campusState === "error"
        ? R.createElement("div", {
            key: "error", className: "office-game__campusState",
            "data-testid": "office-campus-error", role: "alert",
          }, `Directory unavailable: ${campusError}`)
        : campusDirectory
        ? R.createElement(TowerNavigator as never, {
            key: "tower",
            floors: campusDirectory.floors,
            world: campusWorld,
            onOpenLiveFloor: () => setCampusTab("floors"),
          })
        : null;
      children.push(R.createElement("section", {
        key: "campus",
        id: "office-game-campus",
        className: "office-game__campus office-game__campus--floors",
        "data-testid": "office-campus",
        "data-office-slice": dockSlice,
        "aria-label": "Qubit campus",
      }, [
        R.createElement("div", {
          key: "head", className: "office-game__campusHead",
        }, [
          R.createElement("div", { key: "title" }, [
            R.createElement("h2", { key: "h" }, "Qubit Campus"),
            R.createElement("p", {
              key: "counts", "data-testid": "office-campus-counts",
            }, campusTab === "floors"
              ? "Campus world · real live floors and unstaffed templates on one camera"
              : "5-floor directory · floors, rooms, desks and occupancy"),
            R.createElement("div", {
              key: "tabs", className: "office-game__campusTabs",
            }, [
              sakugaButton(R, {
                key: "floors", type: "button",
                className: "office-game__btn",
                "data-testid": "office-campus-tab-floors",
                "aria-pressed": campusTab === "floors",
                onClick: () => setCampusTab("floors"),
              }, "Campus world"),
              sakugaButton(R, {
                key: "directory", type: "button",
                className: "office-game__btn",
                "data-testid": "office-campus-tab-directory",
                "aria-pressed": campusTab === "directory",
                onClick: () => setCampusTab("directory"),
              }, "5-floor directory"),
            ]),
          ]),
          sakugaButton(R, {
            key: "close", type: "button",
            className: "office-game__btn office-theme-dock-close",
            "data-testid": "office-campus-close",
            onClick: () => setCampusOpen(false),
          }, "Close campus"),
        ]),
        R.createElement("div", {
          key: "body", className: "office-game__campusBody",
        }, campusTab === "floors"
          ? R.createElement(CampusWorld as never, {
              base: props.base,
              theme: requestedTheme,
              fetchImpl: props.fetchImpl,
              templates: campusDirectory?.floors,
              onOpenFloor: (floor: { scope: string }) => {
                officeFx("pop");
                setFloorScope(floor.scope);
                setCampusOpen(false);
              },
            })
          : directoryBody),
      ]));
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
      || controlSeat !== null || approvalsOpen || etsyOpen
      || commandCenterOpen || timelineOpen || studioOpen;
    return R.createElement("div", {
      "data-testid": "office-app",
      "data-office-theme": officeTheme && officeTheme.active ? officeTheme.id : undefined,
      "data-office-docks": dualDocks ? "both" : undefined,
      "data-office-dock-top": dockOpen ? "toolbar" : undefined,
      // P6-05 — system preferences become token switches the stylesheet
      // already understands; critical status cues stay textual either way.
      "data-office-reduced-motion": reducedMotion ? "1" : undefined,
      "data-office-contrast": highContrast ? "high" : undefined,
      className: rootClass,
      style: { position: "relative" },
    }, children);
  };
}
