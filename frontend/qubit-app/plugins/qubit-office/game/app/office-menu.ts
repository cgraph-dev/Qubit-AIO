/* The Agents and HUD buttons, INSIDE the office's own panel menu.
 *
 * WHY THEY MOVED
 * --------------
 * They used to be Qubit-rendered text buttons sitting UNDER the iframe, outside
 * the app they control. The owner's ask was to put them where the office's own
 * tools live — the pixel-art icon group in the vendored app's top bar (My day,
 * Session history, New agent, Timelapse, Activity feed, Settings, Help) — and
 * to keep that group untouched apart from the two added icons.
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

/** The buttons' outward-facing identity: labels, titles, test ids. */
export interface MenuButtonSpec {
  id: "agents" | "hud";
  label: string;
  title: string;
  testId: string;
  icon: MenuIcon;
}

export const MENU_BUTTONS: readonly MenuButtonSpec[] = [
  {
    id: "agents",
    label: "Agents",
    title: "Agents: every seat the office plans, by division",
    testId: "office-menu-agents",
    icon: AGENTS_ICON,
  },
  {
    id: "hud",
    label: "HUD",
    title: "HUD: the office and agent vitals",
    testId: "office-menu-hud",
    icon: HUD_ICON,
  },
];

/** The group the office renders its own panel buttons in. */
export const PANELS_GROUP_SELECTOR = '.ui-btn-group[aria-label="Panels"]';

/** The dock state the two buttons mirror. */
export interface OfficeMenuState {
  agents: boolean;
  hud: boolean;
}

export interface OfficeMenuOptions {
  onToggleAgents: () => void;
  onToggleHud: () => void;
}

export interface OfficeMenuController {
  /** Mirror the dock state onto the buttons (``aria-pressed``). */
  update(state: OfficeMenuState): void;
  /** Remove the buttons and stop watching the document. */
  dispose(): void;
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

/** Build one button with the app's own classes and icon convention. */
function buildButton(
  doc: Document,
  spec: MenuButtonSpec,
  onActivate: () => void,
): HTMLButtonElement {
  const button = doc.createElement("button");
  button.type = "button";
  button.className = "ui-icon-btn";
  button.setAttribute("aria-label", spec.label);
  button.setAttribute("title", spec.title);
  button.setAttribute("aria-pressed", "false");
  button.setAttribute("data-testid", spec.testId);
  button.setAttribute("data-office-menu", spec.id);
  button.addEventListener("click", onActivate);

  const ns = "http://www.w3.org/2000/svg";
  const svg = doc.createElementNS(ns, "svg");
  const scale = Math.min(1, MAX_ICON_PX / spec.icon.cols);
  const width = Math.max(1, Math.round(spec.icon.cols * scale));
  const height = Math.max(1, Math.round(spec.icon.rows * scale));
  svg.setAttribute("class", "ui-px-icon");
  svg.setAttribute("width", String(width));
  svg.setAttribute("height", String(height));
  svg.setAttribute("viewBox", `0 0 ${spec.icon.cols} ${spec.icon.rows}`);
  svg.setAttribute("shape-rendering", "crispEdges");
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("focusable", "false");
  const path = doc.createElementNS(ns, "path");
  path.setAttribute("fill", "currentColor");
  path.setAttribute("d", spec.icon.d);
  svg.appendChild(path);
  button.appendChild(svg);
  return button;
}

/**
 * Insert the two buttons into the office's panel group, before Settings.
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

  const byId: Record<"agents" | "hud", HTMLButtonElement | null> = {
    agents: null,
    hud: null,
  };
  const state: OfficeMenuState = { agents: false, hud: false };

  const findGroup = (): Element | null => doc.querySelector(PANELS_GROUP_SELECTOR);

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
      const onActivate = spec.id === "agents" ? opts.onToggleAgents : opts.onToggleHud;
      const button = buildButton(doc, spec, onActivate);
      button.setAttribute("aria-pressed", state[spec.id] ? "true" : "false");
      byId[spec.id] = button;
      insert(group, button);
    }
    return true;
  };

  // A group that is not in the document yet is picked up by the observer below
  // rather than treated as a failure: a mount that happens after the load
  // event must not lose the buttons.
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
      for (const spec of MENU_BUTTONS) {
        const button = byId[spec.id];
        if (button) button.setAttribute("aria-pressed", state[spec.id] ? "true" : "false");
      }
    },
    dispose(): void {
      observer.disconnect();
      for (const spec of MENU_BUTTONS) {
        const button = byId[spec.id];
        if (button && button.isConnected) button.remove();
        byId[spec.id] = null;
      }
    },
  };
}
