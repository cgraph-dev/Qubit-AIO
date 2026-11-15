/* The Qubit panel buttons, INSIDE the office's own panel menu.
 *
 * WHY THEY MOVED
 * --------------
 * They used to be Qubit-rendered text buttons sitting UNDER the iframe, outside
 * the app they control. The owner's ask was to put them where the office's own
 * tools live — the pixel-art icon group in the vendored app's top bar (My day,
 * Session history, New agent, Timelapse, Activity feed, Settings, Help) — and
 * to keep that group untouched apart from the added icons. Every Qubit panel
 * (Agents, HUD, Approvals, Command Center, Timeline, Studio, Campus) is now an
 * icon here; the bottom text toolbar is gone.
 *
 * WHY THE HOST INJECTS THEM
 * -------------------------
 * The group lives in the embedded document (a same-origin iframe), not in this
 * bundle: the vendored app has no extension point and its bundle is a build
 * artifact we do not edit. The shell around the frame already owns the panels
 * those buttons open (HudPanel, AgentsPanel), so it also owns the two buttons —
 * it appends them to the existing group and forwards clicks to the same state
 * toggles. Append-only: no existing node is moved, restyled or removed, so the
 * menu the owner called "perfect" keeps its exact markup and behaviour.
 *
 * WHAT HAPPENS WHEN REACT RE-RENDERS
 * ----------------------------------
 * The group is React-managed. A re-render can drop the appended nodes (any
 * reconciliation that rewrites the child list), so a MutationObserver re-adds
 * them where they went missing. The observer also re-finds the group if the
 * whole top bar is replaced. Nothing polls.
 *
 * THE ICONS
 * ---------
 * Traced from the supplied sheet into the app's OWN icon convention: a
 * ``ui-px-icon`` SVG of 1-unit cells with ``shape-rendering="crispEdges"`` and
 * ``fill="currentColor"``, exactly like every other button in the group. That
 * is why the colour, hover and pressed states match for free:
 * ``.ui-icon-btn[aria-pressed=true]`` paints the active state.
 */

/** One traced icon: its cell grid and the path of filled cells. */
export interface MenuIcon {
  /** Grid width in icon cells. */
  cols: number;
  /** Grid height in icon cells. */
  rows: number;
  /** ``M x y h<n> v1 H x z`` runs, one per filled span. */
  d: string;
}

/** HUD: the screen-with-refresh glyph from the supplied sheet. */
export const HUD_ICON: MenuIcon = {
  cols: 19,
  rows: 17,
  d: "M0 0h5v1H0zM17 0h1v1H17zM0 1h1v1H0zM0 2h1v1H0zM0 3h1v1H0zM7 3h2v1H7zM10 3h2v1H10zM18 3h1v1H18zM5 4h8v1H5zM5 5h2v1H5zM9 5h1v1H9zM12 5h2v1H12zM4 6h2v1H4zM13 6h1v1H13zM4 7h1v1H4zM9 7h1v1H9zM13 7h1v1H13zM3 8h3v1H3zM8 8h1v1H8zM10 8h1v1H10zM12 8h3v1H12zM3 9h2v1H3zM9 9h1v1H9zM13 9h2v1H13zM4 10h1v1H4zM13 10h1v1H13zM4 11h2v1H4zM12 11h2v1H12zM5 12h2v1H5zM9 12h1v1H9zM11 12h2v1H11zM0 13h1v1H0zM6 13h6v1H6zM0 14h1v1H0zM9 14h1v1H9zM0 15h1v1H0zM0 16h4v1H0zM11 16h3v1H11z",
};

/** Agents: the two-seat-plus glyph from the supplied sheet. */
export const AGENTS_ICON: MenuIcon = {
  cols: 27,
  rows: 14,
  d: "M5 0h4v1H5zM4 1h6v1H4zM13 1h3v1H13zM3 2h7v1H3zM12 2h5v1H12zM3 3h7v1H3zM12 3h5v1H12zM3 4h7v1H3zM12 4h5v1H12zM24 4h1v1H24zM3 5h7v1H3zM12 5h5v1H12zM24 5h1v1H24zM4 6h5v1H4zM13 6h3v1H13zM22 6h5v1H22zM4 7h5v1H4zM13 7h3v1H13zM24 7h1v1H24zM5 8h3v1H5zM11 8h7v1H11zM24 8h1v1H24zM3 9h7v1H3zM12 9h7v1H12zM1 10h11v1H1zM13 10h6v1H13zM0 11h13v1H0zM0 12h13v1H0zM0 13h13v1H0z",
};

/** Approvals: a check glyph authored for the same 1-cell grid. */
export const APPROVALS_ICON: MenuIcon = {
  cols: 13,
  rows: 11,
  d: "M12 0h1v1H12zM11 1h1v1H11zM10 2h1v1H10zM1 3h1v1H1zM9 3h1v1H9zM2 4h1v1H2zM8 4h1v1H8zM3 5h1v1H3zM7 5h1v1H7zM4 6h1v1H4zM6 6h1v1H6zM5 7h1v1H5z",
};

/** Command Center: a pixel magnifier over a small grid. */
export const COMMAND_CENTER_ICON: MenuIcon = {
  cols: 16,
  rows: 16,
  d: "M6 1h4v1H6zM4 2h2v1H4zM10 2h2v1H10zM3 3h1v1H3zM12 3h1v1H12zM2 4h1v1H2zM13 4h1v1H13zM2 5h1v1H2zM13 5h1v1H13zM2 6h1v1H2zM13 6h1v1H13zM2 7h1v1H2zM13 7h1v1H13zM3 8h1v1H3zM12 8h1v1H12zM4 9h2v1H4zM10 9h2v1H10zM6 10h4v1H6zM10 11h1v1H10zM11 12h1v1H11zM12 13h1v1H12zM13 14h1v1H13zM14 15h1v1H14z",
};

/** Timeline: a horizontal rail with causal nodes above and below it. */
export const TIMELINE_ICON: MenuIcon = {
  cols: 16,
  rows: 12,
  d: "M0 6h16v1H0zM2 3h2v2H2zM7 2h2v2H7zM12 4h2v2H12zM4 8h2v2H4zM9 8h2v2H9zM14 8h2v2H14z",
};

/** Studio: a paint palette with two colour wells. */
export const STUDIO_ICON: MenuIcon = {
  cols: 14,
  rows: 13,
  d: "M6 0h4v1H6zM3 2h2v1H3zM9 2h2v1H9zM2 3h1v1H2zM11 3h1v1H11zM1 5h1v1H1zM12 5h1v1H12zM1 7h1v1H1zM12 7h1v1H12zM2 9h1v1H2zM11 9h1v1H11zM3 10h2v1H3zM9 10h1v1H9zM6 11h4v1H6zM4 4h2v2H4zM8 4h2v2H8z",
};

/** Campus: three tower footprints at different heights. */
export const CAMPUS_ICON: MenuIcon = {
  cols: 16,
  rows: 16,
  d: "M1 5h4v11H1zM6 2h4v14H6zM11 7h4v9H11zM2 6h1v1H2zM2 8h1v1H2zM2 10h1v1H2zM2 12h1v1H2zM7 3h1v1H7zM7 5h1v1H7zM7 7h1v1H7zM7 9h1v1H7zM7 11h1v1H7zM12 8h1v1H12zM12 10h1v1H12zM12 12h1v1H12z",
};

/** The buttons' outward-facing identity: labels, titles, test ids. */
export type MenuButtonId =
  | "agents"
  | "hud"
  | "approvals"
  | "command-center"
  | "timeline"
  | "studio"
  | "campus";

export interface MenuButtonSpec {
  id: MenuButtonId;
  label: string;
  title: string;
  testId: string;
  icon: MenuIcon;
  /** The dock the icon toggles, so `aria-controls` names the real region. */
  controls: string;
}

export const MENU_BUTTONS: readonly MenuButtonSpec[] = [
  {
    id: "agents",
    label: "Agents",
    title: "Agents: every seat the office plans, by division",
    testId: "office-menu-agents",
    icon: AGENTS_ICON,
    controls: "office-game-agents-dock",
  },
  {
    id: "hud",
    label: "HUD",
    title: "HUD: the office and agent vitals",
    testId: "office-menu-hud",
    icon: HUD_ICON,
    controls: "office-game-hud-dock",
  },
  {
    id: "approvals",
    label: "Approvals",
    title: "Approvals: pending confirmations on the shared journal",
    testId: "office-menu-approvals",
    icon: APPROVALS_ICON,
    controls: "office-game-approvals-dock",
  },
  {
    id: "command-center",
    label: "Command Center",
    title: "Command Center: search floors, rooms, desks, agents and tasks",
    testId: "office-menu-command-center",
    icon: COMMAND_CENTER_ICON,
    controls: "office-game-command-center-dock",
  },
  {
    id: "timeline",
    label: "Timeline",
    title: "Timeline: the replayable operator record of every run",
    testId: "office-menu-timeline",
    icon: TIMELINE_ICON,
    controls: "office-game-timeline-dock",
  },
  {
    id: "studio",
    label: "Studio",
    title: "Studio: panel inventory, room builder and outside world",
    testId: "office-menu-studio",
    icon: STUDIO_ICON,
    controls: "office-game-studio-dock",
  },
  {
    id: "campus",
    label: "Campus",
    title: "Campus: floor scenes and the 100-floor directory",
    testId: "office-menu-campus",
    icon: CAMPUS_ICON,
    controls: "office-game-campus",
  },
];

/** The group the office renders its own panel buttons in. */
export const PANELS_GROUP_SELECTOR = '.ui-btn-group[aria-label="Panels"]';

/** The dock state the buttons mirror (approvals also carries its badge). */
export interface OfficeMenuState {
  agents: boolean;
  hud: boolean;
  approvals?: boolean;
  commandCenter?: boolean;
  timeline?: boolean;
  studio?: boolean;
  campus?: boolean;
  /** Pending approvals the button's badge should show (0 hides it). */
  pendingApprovals?: number;
}

export interface OfficeMenuOptions {
  onToggleAgents: () => void;
  onToggleHud: () => void;
  /** Optional so existing callers/tests keep the older menu. */
  onToggleApprovals?: () => void;
  onToggleCommandCenter?: () => void;
  onToggleTimeline?: () => void;
  onToggleStudio?: () => void;
  onToggleCampus?: () => void;
  /**
   * How long to wait for the app's own Panels group before telling the shell
   * to render its fallback buttons. Default 1500 ms.
   */
  fallbackAfterMs?: number;
  /**
   * Show/hide the host-rendered fallback. Called ``true`` once the bounded
   * wait expires with no group, and ``false`` as soon as injection attaches
   * (or on dispose), so the two are never visible at the same time.
   */
  onFallback?: (show: boolean) => void;
  /** The injected controls attached to the app's own group. */
  onAttach?: () => void;
}

export interface OfficeMenuController {
  /** Mirror the dock state onto the buttons (``aria-pressed``). */
  update(state: OfficeMenuState): void;
  /** Remove the buttons and stop watching the document. */
  dispose(): void;
  /** Whether the injected buttons are currently mounted in the app group. */
  attached(): boolean;
}

/**
 * Largest cell grid we render 1:1. Above this, scale down proportionally.
 *
 * The office's own icons are a 10×10 grid drawn at 20×20 (2 px per cell). The
 * traced icons are 19×17 and 27×14, so drawing them at 2 px per cell would
 * overflow the 32 px button. Rendering ONE CSS pixel per cell is both the
 * source's native scale and the only size that stays crisp under
 * `shape-rendering="crispEdges"`: a non-integer scale rounds 1-cell strokes to
 * nothing (the Agents plus lost its crossbar at 22 px).
 */
const MAX_ICON_PX = 28;

/** The traced icon's native geometry (shared by injected and fallback buttons). */
export function menuIconGeometry(spec: MenuButtonSpec): {
  width: number;
  height: number;
  viewBox: string;
  d: string;
} {
  const scale = Math.min(1, MAX_ICON_PX / spec.icon.cols);
  return {
    width: Math.max(1, Math.round(spec.icon.cols * scale)),
    height: Math.max(1, Math.round(spec.icon.rows * scale)),
    viewBox: `0 0 ${spec.icon.cols} ${spec.icon.rows}`,
    d: spec.icon.d,
  };
}

/** Build one button with the app's own classes and icon convention. */
function buildButton(
  doc: Document,
  spec: MenuButtonSpec,
  onActivate: () => void,
): HTMLButtonElement {
  const button = doc.createElement("button");
  button.type = "button";
  // The app's own visual classes (so colour/hover/pressed match for free) plus
  // Qubit's stable theme hooks: Task 7 scopes its token chrome to these, never
  // to Habblaud's internal structure.
  button.className = `ui-icon-btn office-theme-menu-btn office-theme-menu-${spec.id}`;
  button.setAttribute("aria-label", spec.label);
  button.setAttribute("title", spec.title);
  button.setAttribute("aria-pressed", "false");
  // Name the region it toggles. The dock may not be mounted yet, but the id is
  // a stable contract (`OfficeAppShell` renders exactly this id).
  button.setAttribute("aria-controls", spec.controls);
  button.setAttribute("data-testid", spec.testId);
  button.setAttribute("data-office-menu", spec.id);
  button.addEventListener("click", onActivate);

  const ns = "http://www.w3.org/2000/svg";
  const svg = doc.createElementNS(ns, "svg");
  const geometry = menuIconGeometry(spec);
  svg.setAttribute("class", "ui-px-icon");
  svg.setAttribute("width", String(geometry.width));
  svg.setAttribute("height", String(geometry.height));
  svg.setAttribute("viewBox", geometry.viewBox);
  svg.setAttribute("shape-rendering", "crispEdges");
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("focusable", "false");
  const path = doc.createElementNS(ns, "path");
  path.setAttribute("fill", "currentColor");
  path.setAttribute("d", geometry.d);
  svg.appendChild(path);
  button.appendChild(svg);
  if (spec.id === "approvals") {
    // The pending-count badge. It is part of THIS appended button only; no
    // existing node or style in the app's group is touched.
    const badge = doc.createElement("span");
    badge.className = "office-menu-badge";
    badge.setAttribute("data-testid", "office-menu-approvals-badge");
    badge.setAttribute("aria-hidden", "true");
    badge.hidden = true;
    button.appendChild(badge);
    button.setAttribute("data-has-badge", "true");
  }
  return button;
}

/**
 * Insert every Qubit panel button into the office's panel group, before
 * Settings. Existing nodes are never moved, restyled or removed.
 *
 * Returns ``null`` only when there is no document to install into. A group
 * that is missing at call time is not a failure: the observer picks it up when
 * it mounts, so a slow first paint still gets the buttons.
 */
export function installOfficeMenuButtons(
  win: Window,
  opts: OfficeMenuOptions,
): OfficeMenuController | null {
  const doc = win.document;
  if (!doc || !doc.body) return null;

  const byId: Record<MenuButtonId, HTMLButtonElement | null> = {
    agents: null,
    hud: null,
    approvals: null,
    "command-center": null,
    timeline: null,
    studio: null,
    campus: null,
  };
  const state: OfficeMenuState = {
    agents: false,
    hud: false,
    approvals: false,
    commandCenter: false,
    timeline: false,
    studio: false,
    campus: false,
  };
  const activateFor = (id: MenuButtonId): (() => void) => {
    switch (id) {
      case "agents": return opts.onToggleAgents;
      case "hud": return opts.onToggleHud;
      case "approvals": return opts.onToggleApprovals ?? (() => {});
      case "command-center": return opts.onToggleCommandCenter ?? (() => {});
      case "timeline": return opts.onToggleTimeline ?? (() => {});
      case "studio": return opts.onToggleStudio ?? (() => {});
      case "campus": return opts.onToggleCampus ?? (() => {});
    }
  };
  let fallbackShown = false;
  let attachedOnce = false;

  const findGroup = (): Element | null => doc.querySelector(PANELS_GROUP_SELECTOR);

  const isAttached = (): boolean => Boolean(
    byId.agents && byId.agents.isConnected
    && byId.hud && byId.hud.isConnected
    && byId.approvals && byId.approvals.isConnected);

  const setFallback = (show: boolean): void => {
    if (fallbackShown === show) return;
    fallbackShown = show;
    opts.onFallback?.(show);
  };

  const stateFor = (id: MenuButtonId): boolean => {
    switch (id) {
      case "agents": return Boolean(state.agents);
      case "hud": return Boolean(state.hud);
      case "approvals": return Boolean(state.approvals);
      case "command-center": return Boolean(state.commandCenter);
      case "timeline": return Boolean(state.timeline);
      case "studio": return Boolean(state.studio);
      case "campus": return Boolean(state.campus);
    }
  };

  const badgeFor = (id: MenuButtonId): HTMLSpanElement | null => {
    if (id !== "approvals") return null;
    return byId.approvals?.querySelector(".office-menu-badge") as
      HTMLSpanElement | null;
  };

  const paintApprovals = (): void => {
    const button = byId.approvals;
    const badge = badgeFor("approvals");
    const count = Math.max(0, Math.floor(Number(state.pendingApprovals ?? 0) || 0));
    if (badge) {
      badge.textContent = count > 0 ? String(count) : "";
      badge.hidden = count <= 0;
    }
    if (button) {
      button.setAttribute("aria-pressed", state.approvals ? "true" : "false");
      button.setAttribute("aria-label",
        count > 0 ? `Approvals (${count} pending)` : "Approvals");
      button.setAttribute("data-pending", String(count));
    }
  };

  const insert = (group: Element, button: HTMLButtonElement): void => {
    // Before the two utilities (Settings, Help), after the tools: the added
    // icons belong with the panels, not with the app's settings.
    const settings = Array.from(group.querySelectorAll("button"))
      .find((b) => (b.getAttribute("title") || "") === "Settings");
    if (settings) group.insertBefore(button, settings);
    else group.appendChild(button);
  };

  const attach = (): boolean => {
    const group = findGroup();
    if (!group) return false;
    for (const spec of MENU_BUTTONS) {
      const existing = byId[spec.id];
      if (existing && existing.isConnected) continue;
      const button = buildButton(doc, spec, activateFor(spec.id));
      button.setAttribute("aria-pressed", stateFor(spec.id) ? "true" : "false");
      byId[spec.id] = button;
      insert(group, button);
    }
    paintApprovals();
    if (!attachedOnce && isAttached()) {
      attachedOnce = true;
      clearTimeout(fallbackTimer);
      setFallback(false);
      opts.onAttach?.();
    }
    return isAttached();
  };

  // A group that is not in the document yet is picked up by the observer below
  // rather than treated as a failure: a mount that happens after the load
  // event must not lose the buttons. If it NEVER appears, the shell is told to
  // render its own fallback icons after a bounded wait, so an upstream DOM
  // change cannot silently remove the only way to open the Agents/HUD docks.
  const fallbackTimer = setTimeout(() => {
    if (!isAttached()) setFallback(true);
  }, Math.max(0, opts.fallbackAfterMs ?? 1500));
  attach();

  let scheduled = false;
  const observer = new MutationObserver(() => {
    if (scheduled) return;
    scheduled = true;
    const run = () => {
      scheduled = false;
      attach();
    };
    if (typeof win.requestAnimationFrame === "function") win.requestAnimationFrame(run);
    else setTimeout(run, 0);
  });
  observer.observe(doc.body, { childList: true, subtree: true });

  return {
    update(next: OfficeMenuState): void {
      state.agents = Boolean(next.agents);
      state.hud = Boolean(next.hud);
      state.approvals = Boolean(next.approvals);
      state.commandCenter = Boolean(next.commandCenter);
      state.timeline = Boolean(next.timeline);
      state.studio = Boolean(next.studio);
      state.campus = Boolean(next.campus);
      state.pendingApprovals = Math.max(
        0, Math.floor(Number(next.pendingApprovals ?? 0) || 0));
      for (const spec of MENU_BUTTONS) {
        const button = byId[spec.id];
        if (button) {
          button.setAttribute("aria-pressed", stateFor(spec.id) ? "true" : "false");
        }
      }
      paintApprovals();
    },
    attached(): boolean {
      return isAttached();
    },
    dispose(): void {
      observer.disconnect();
      clearTimeout(fallbackTimer);
      setFallback(false);
      for (const spec of MENU_BUTTONS) {
        const button = byId[spec.id];
        if (button && button.isConnected) button.remove();
        byId[spec.id] = null;
      }
    },
  };
}
