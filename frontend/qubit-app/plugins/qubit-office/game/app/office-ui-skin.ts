/* Presentation-only skin for the vendored Habblaud chrome.
 *
 * The theme payload already carries optional 9-slice panels and icon URLs.
 * Habblaud applies the slices to its own `.ui-*` classes; this module extends
 * that skin to the shell-injected menu buttons and gives every toolbar button
 * a pixel-art icon without changing what any button does. The art stays in the
 * local theme pack; this file contains selectors and URLs only.
 */

type Slice = { src?: unknown; slice?: unknown; width?: unknown; repeat?: unknown };
type Icon = { src?: unknown };
type Payload = { active?: boolean; ui?: { slices?: Record<string, Slice>; icons?: Record<string, Icon> } };

const SAFE_URL = /^[A-Za-z0-9._~:/?=&%+,@!$'()*;-]+$/;
const STYLE_ID = "qubit-office-ui-skin";

function safeSrc(value: unknown): string | null {
  if (typeof value !== "string" || !value || value.includes("\\")) return null;
  if (!SAFE_URL.test(value) || !/^(?:\/(?!\/)|https?:\/\/)/.test(value)) return null;
  return value;
}

function sliceRule(slice: Slice | undefined, targets: readonly string[]): string[] {
  const src = safeSrc(slice?.src);
  const cell = Array.isArray(slice?.slice) && slice.slice.length === 4
    ? slice.slice.filter((n) => Number.isInteger(n) && n > 0)
    : [];
  if (!src || cell.length !== 4) return [];
  const width = typeof slice?.width === "string" && /^[1-9][0-9]{0,3}px$/.test(slice.width)
    ? slice.width : "4px";
  const repeat = slice?.repeat === "round" || slice?.repeat === "repeat"
    ? slice.repeat : "stretch";
  return targets.map((target) => `.ui-root ${target} {`
    + `border-style:solid;border-width:${width};`
    + `border-image-source:url("${src}");`
    + `border-image-slice:${cell.join(" ")};`
    + `border-image-width:${width};`
    + `border-image-repeat:${repeat};border-image-outset:0;`
    + `image-rendering:pixelated; }`);
}

/** CSS for panels/dialogs/buttons/bubbles, scoped to the office document. */
export function officeUiSkinRules(payload: Payload | null | undefined): string[] {
  if (!payload?.active) return [];
  const slices = payload.ui?.slices ?? {};
  return [
    ...sliceRule(slices.panel, [
      ".ui-panel", ".ui-drawer", ".ui-dialog", ".ui-feed", ".ui-sidebar",
      ".ui-settings", ".ui-spawn", ".ui-perm", ".ui-history",
      ".ui-timelapse", ".ui-daystats", ".ui-deliveries", ".ui-popover",
      ".ui-usage-card", ".ui-row", ".ui-tab",
    ]),
    ...sliceRule(slices.dock, [".ui-drawer", ".ui-feed", ".ui-sidebar"]),
    ...sliceRule(slices.card, [".ui-row", ".ui-usage-card", ".ui-deliv-row"]),
    ...sliceRule(slices.dialog, [".ui-dialog", ".ui-popover", ".ui-modal"]),
    ...sliceRule(slices.bubble, [".ui-bubble", ".ui-toast", ".ui-chip"]),
    ...sliceRule(slices.button, [".ui-btn"]),
    `.ui-root { image-rendering: pixelated; }`,
  ];
}

const ICON_SELECTORS: Record<string, readonly string[]> = {
  overview: ['button[title^="Visão geral"]', 'button[aria-label^="Visão geral"]'],
  zoomOut: ['button[title^="Afastar"]', 'button[aria-label^="Afastar"]'],
  zoomIn: ['button[title^="Aproximar"]', 'button[aria-label^="Aproximar"]'],
  timelapse: ['.ui-btn-timelapse', 'button[title^="Timelapse"]'],
  history: ['button[title^="Histórico"]', 'button[aria-label^="Histórico"]'],
  spawn: ['button[title^="Novo agente"]', 'button[aria-label^="Novo agente"]'],
  feed: ['.ui-btn-feed'],
  sidebar: ['.ui-btn-sidebar'],
  settings: ['.ui-btn-settings'],
  help: ['button[title^="Ajuda"]', 'button[aria-label^="Ajuda"]'],
  agents: ['[data-testid="office-menu-agents"]'],
  hud: ['[data-testid="office-menu-hud"]'],
  approvals: ['[data-testid="office-menu-approvals"]'],
  command: ['[data-testid="office-menu-command-center"]'],
  timeline: ['[data-testid="office-menu-timeline"]'],
  studio: ['[data-testid="office-menu-studio"]'],
  campus: ['[data-testid="office-menu-campus"]'],
  etsy: ['button[title^="Etsy"]', 'button[aria-label^="Etsy"]'],
};

function applyIcons(doc: Document, payload: Payload, touched: Set<HTMLElement>): void {
  const icons = payload.ui?.icons ?? {};
  for (const [name, selectors] of Object.entries(ICON_SELECTORS)) {
    const src = safeSrc(icons[name]?.src);
    if (!src) continue;
    for (const selector of selectors) {
      for (const node of Array.from(doc.querySelectorAll<HTMLElement>(selector))) {
        const el = node as HTMLElement;
        el.style.backgroundImage = `url("${src}")`;
        el.style.backgroundPosition = "center";
        el.style.backgroundRepeat = "no-repeat";
        el.style.backgroundSize = "18px 18px";
        el.style.imageRendering = "pixelated";
        for (const svg of Array.from(el.querySelectorAll<SVGElement>("svg"))) {
          svg.style.display = "none";
        }
        touched.add(el);
      }
    }
  }
}

/** Install the skin in a same-origin office iframe; returns an undo function. */
export function installOfficeUiSkin(win: Window): () => void {
  const doc = win.document;
  const payload = (win as Window & { __QUBIT_OFFICE_THEME__?: Payload }).__QUBIT_OFFICE_THEME__;
  if (!payload?.active) return () => {};
  const style = doc.createElement("style");
  style.id = STYLE_ID;
  style.setAttribute("data-office-ui-skin", "");
  style.textContent = officeUiSkinRules(payload).join("\n");
  doc.head.appendChild(style);
  const touched = new Set<HTMLElement>();
  const reapply = () => applyIcons(doc, payload, touched);
  reapply();
  const obs = new win.MutationObserver(reapply);
  obs.observe(doc.documentElement, { childList: true, subtree: true });
  return () => {
    obs.disconnect();
    style.remove();
    for (const el of touched) {
      el.style.backgroundImage = "";
      el.style.backgroundPosition = "";
      el.style.backgroundRepeat = "";
      el.style.backgroundSize = "";
      el.style.imageRendering = "";
      for (const svg of Array.from(el.querySelectorAll<SVGElement>("svg"))) {
        svg.style.display = "";
      }
    }
  };
}
