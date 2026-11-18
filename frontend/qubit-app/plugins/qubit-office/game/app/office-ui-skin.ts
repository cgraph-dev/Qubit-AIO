/* Presentation-only 2Five skin for the vendored Habblaud chrome.
 *
 * The office iframe is a separate browsing context: the host's `game.css` and
 * `--office-*` tokens cannot reach its Habblaud `.ui-*` DOM. This module is the
 * CSS-only bridge. It appends ONE `<style>` to the iframe document and returns
 * the undo; it creates no nodes, mutates no control, and adds no listener to a
 * control.
 *
 * WHY IT ALWAYS MOUNTS
 * --------------------
 * The earlier version bailed on `payload.active`, so the `classic` theme (the
 * default) was never skinned. The baseline below is the Qubit chrome and is
 * emitted unconditionally; an active theme can only ADD its own panel/dialog
 * 9-slice, never replace the baseline or touch a button.
 *
 * ZERO CONTROL MOTION (Perplexity Stage 9)
 * ----------------------------------------
 * No rule here adds a `transition`, `animation` or press/chevron `transform`
 * to a control. The last block resets the motion the vendored CSS declares on
 * `.ui-btn`, `.ui-icon-btn`, `.ui-agent`, `.ui-feed__row`, `.ui-ttab`,
 * `.ui-seg__opt`, `.ui-counter`, `.ui-room__head`, inputs/selects and close
 * buttons. Sakuga (`sakuga-*`, `.qsk-*`, `--sk-*`), `.ui-switch` and
 * `input[type=checkbox]` are deliberately NOT touched.
 *
 * WHY BAKED SVG DATA URIs
 * -----------------------
 * Stage 4's `<svg><g id="...">` fragment URLs cannot cross the host→iframe
 * boundary, and a `var(--office-*)` inside an SVG loaded as an image resolves
 * to nothing. Every frame/ornament below is therefore the Stage 4 geometry
 * serialized as a self-contained `data:image/svg+xml` URL with concrete
 * colours. There is no `url(#...)` reference to the host document.
 */

type Slice = { src?: unknown; slice?: unknown; width?: unknown; repeat?: unknown };
type Payload = { active?: boolean; ui?: { slices?: Record<string, Slice> } };


const STYLE_ID = "qubit-office-ui-skin";
const SAFE_URL = /^[A-Za-z0-9._~:/?=&%+,@!$'()*;-]+$/;

/* The Stage 4 chrome, baked. Keys are used only to build `url(...)`. */
const SVG = {
  panel: "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='96' height='96' viewBox='0 0 96 96'%3E%3Cpath d='M20 2H76L94 20V76L76 94H20L2 76V20Z' fill='%2314081E' stroke='%2339171D' stroke-width='4'/%3E%3Cpath d='M20 6H76L90 20V76L76 90H20L6 76V20Z' fill='none' stroke='%23C18867' stroke-width='2'/%3E%3Cpath d='M22 10H74L86 22V74L74 86H22L10 74V22Z' fill='none' stroke='%23EFAF67' stroke-width='1.5'/%3E%3Cpath d='M26 14H70M14 26V70M82 26V70M26 82H70' fill='none' stroke='%23761F77' stroke-width='1.5'/%3E%3Cpath d='M6 20L20 6L24 10L10 24ZM90 20L76 6L72 10L86 24ZM6 76L20 90L24 86L10 72ZM90 76L76 90L72 86L86 72Z' fill='%23EB5086' stroke='%23FFE092' stroke-width='1'/%3E%3C/svg%3E",
  window: "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='96' height='96' viewBox='0 0 96 96'%3E%3Cpath d='M24 2H72L94 24V72L72 94H24L2 72V24Z' fill='%2314081E' stroke='%2339171D' stroke-width='4'/%3E%3Cpath d='M24 6H72L90 24V72L72 90H24L6 72V24Z' fill='none' stroke='%23C18867' stroke-width='2'/%3E%3Cpath d='M26 10H70L86 26V70L70 86H26L10 70V26Z' fill='none' stroke='%23EFAF67' stroke-width='2'/%3E%3Crect x='16' y='16' width='64' height='64' rx='4' fill='none' stroke='%23761F77' stroke-width='2'/%3E%3Cpath d='M4 24C14 24 24 14 24 4M72 4C72 14 82 24 92 24M4 72C14 72 24 82 24 92M72 92C72 82 82 72 92 72' fill='none' stroke='%23FFE092' stroke-width='2'/%3E%3Cpath d='M8 8H18L8 18ZM88 8H78L88 18ZM8 88H18L8 78ZM88 88H78L88 78Z' fill='%23EB5086' stroke='%23FFE092' stroke-width='1'/%3E%3Cpath d='M44 4L48 8L52 4L48 2ZM44 92L48 88L52 92L48 94Z' fill='%23F54C86'/%3E%3C/svg%3E",
  tooltip: "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='96' height='96' viewBox='0 0 96 96'%3E%3Cpath d='M14 2H82L94 14V82L82 94H14L2 82V14Z' fill='%232C1637' stroke='%23EFAF67' stroke-width='3'/%3E%3Cpath d='M16 8H80L88 16V80L80 88H16L8 80V16Z' fill='none' stroke='%23761F77' stroke-width='2'/%3E%3Crect x='10' y='10' width='4' height='4' fill='%23EB5086'/%3E%3Crect x='82' y='10' width='4' height='4' fill='%23EB5086'/%3E%3C/svg%3E",
  chevron: "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 12 12'%3E%3Cpath d='M2.5 4.25L6 7.75L9.5 4.25' fill='none' stroke='%23FFE092' stroke-width='1.6' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E",
  magnifier: "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='16' height='16' viewBox='0 0 16 16'%3E%3Ccircle cx='7' cy='7' r='4.5' fill='%2314081E' stroke='%23EFAF67' stroke-width='1.5'/%3E%3Cpath d='M5.2 5.2A2.5 2.5 0 0 1 8.5 4.8' fill='none' stroke='%23FFE092' stroke-width='1' stroke-linecap='round'/%3E%3Cpath d='M10.5 10.5L14 14' stroke='%23EB5086' stroke-width='2' stroke-linecap='round'/%3E%3C/svg%3E",
  skyline: "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='640' height='74' viewBox='0 0 640 74'%3E%3Cdefs%3E%3ClinearGradient id='s' x1='0' y1='0' x2='1' y2='1'%3E%3Cstop offset='0%25' stop-color='%234C0D27'/%3E%3Cstop offset='52%25' stop-color='%23761F77'/%3E%3Cstop offset='100%25' stop-color='%2314081E'/%3E%3C/linearGradient%3E%3CradialGradient id='m' cx='50%25' cy='50%25' r='50%25'%3E%3Cstop offset='0%25' stop-color='%23FFE092' stop-opacity='0.72'/%3E%3Cstop offset='100%25' stop-color='%23EFAF67' stop-opacity='0'/%3E%3C/radialGradient%3E%3CradialGradient id='l' cx='50%25' cy='50%25' r='50%25'%3E%3Cstop offset='0%25' stop-color='%23FFE092' stop-opacity='0.88'/%3E%3Cstop offset='100%25' stop-color='%23EB5086' stop-opacity='0'/%3E%3C/radialGradient%3E%3C/defs%3E%3Crect width='640' height='74' fill='url(%23s)'/%3E%3Cpath d='M0 60C88 50 134 64 216 56C298 48 368 62 452 52C528 44 580 58 640 50V74H0Z' fill='%2314081E' opacity='0.76'/%3E%3Ccircle cx='476' cy='34' r='40' fill='url(%23m)'/%3E%3Ccircle cx='476' cy='34' r='22' fill='%23FFE092' stroke='%23EBD2C3' stroke-width='1'/%3E%3Cpath d='M148 50L204 24L260 50L250 53L204 32L158 53Z' fill='%2314081E' stroke='%23C18867' stroke-width='1.5'/%3E%3Cpath d='M166 40L204 12L242 40L234 43L204 20L174 43Z' fill='%232C1637' stroke='%23EFAF67' stroke-width='1.5'/%3E%3Cpath d='M182 30L204 4L226 30L220 32L204 12L188 32Z' fill='%234C0D27' stroke='%23FFE092' stroke-width='1.2'/%3E%3Cpath d='M0 8C32 12 42 28 70 26M640 6C610 12 600 26 574 26' fill='none' stroke='%23C18867' stroke-width='1.6'/%3E%3Cg fill='%23F54C86'%3E%3Ccircle cx='34' cy='18' r='4'/%3E%3Ccircle cx='84' cy='34' r='3.5'/%3E%3Ccircle cx='602' cy='18' r='4'/%3E%3Ccircle cx='556' cy='38' r='3.5'/%3E%3C/g%3E%3Cg%3E%3Ccircle cx='132' cy='60' r='12' fill='url(%23l)'/%3E%3Crect x='128' y='54' width='8' height='10' rx='2' fill='%23EFAF67' stroke='%23FFE092' stroke-width='1'/%3E%3Ccircle cx='356' cy='60' r='12' fill='url(%23l)'/%3E%3Crect x='352' y='54' width='8' height='10' rx='2' fill='%23EFAF67' stroke='%23FFE092' stroke-width='1'/%3E%3Ccircle cx='540' cy='60' r='12' fill='url(%23l)'/%3E%3Crect x='536' y='54' width='8' height='10' rx='2' fill='%23EFAF67' stroke='%23FFE092' stroke-width='1'/%3E%3C/g%3E%3C/svg%3E",
} as const;

/* Reference palette, baked for the iframe. Mirrors the host tokens.css so the
   two documents read as one surface; kept here because a custom property
   cannot cross the iframe boundary in `classic`. */
const C = {
  void: "#14081E",
  plate: "#2C1637",
  hover: "#3A1A47",
  selected: "#501F41",
  header: "#190927",
  topbar: "#4C0D27",
  input: "#11071B",
  pill: "#0E061B",
  parchment: "#EBD2C3",
  parchmentRaised: "#F9E2D1",
  parchmentCream: "#FDF0DF",
  parchmentWell: "#281430",
  ink: "#EBD2C3",
  inkText: "#FFFFFF",
  muted: "#A2758F",
  dim: "#8891B5",
  faint: "#C18867",
  accent: "#EB5086",
  accentSoft: "#F54C86",
  plum: "#761F77",
  orchid: "#A73A7A",
  sakura: "#FE98AF",
  goldBright: "#FFE092",
  gold: "#C18867",
  goldHi: "#EFAF67",
  ok: "#4FD18B",
  warn: "#EFAF67",
  bad: "#F54C86",
  keyline: "#15010E",
} as const;

function safeSrc(value: unknown): string | null {
  if (typeof value !== "string" || !value || value.includes("\\")) return null;
  if (!SAFE_URL.test(value) || !/^(?:\/(?!\/)|https?:\/\/)/.test(value)) return null;
  return value;
}

/** The baseline chrome. Emitted for `classic` too. */
const BASELINE: readonly string[] = [
  /* --- 1. token bridge + layout geometry (matches Habblaud's breakpoints) */
  `html body .ui-root {
  --ui-bg: color-mix(in srgb, ${C.plate} 92%, transparent);
  --ui-bg-solid: ${C.void};
  --ui-bg-soft: ${C.plate};
  --ui-bg-hover: ${C.hover};
  --ui-line: ${C.gold};
  --ui-line-strong: ${C.goldHi};
  --ui-text: ${C.ink};
  --ui-muted: ${C.muted};
  --ui-faint: ${C.faint};
  --ui-accent: ${C.accent};
  --ui-accent-soft: rgba(236, 55, 108, 0.24);
  --st-working: ${C.ok};
  --st-waiting: ${C.goldBright};
  --st-shell: ${C.sakura};
  --st-idle: ${C.dim};
  --st-done: ${C.goldHi};
  --st-offline: ${C.faint};
  --lv-ok: ${C.ok};
  --lv-warn: ${C.goldHi};
  --lv-crit: ${C.bad};
  --radius: 6px;
  --radius-sm: 4px;
  color: ${C.ink};
  font-family: var(--font-ui, "Inter", "Segoe UI", system-ui, sans-serif);
  font-size: 14px;
  line-height: 18px;
}`,
  `@media (width >= 601px) {
  html body .ui-root { --top-h: 74px; --gap: 8px; --feed-head: 38px; }
  html body .ui-root.has-usage-row { --top-h: 134px; }
}`,
  `@media (width >= 901px) {
  html body .ui-root { --side-w: 314px; --drawer-w: 320px; --feed-max: 636px; --feed-h: 214px; }
}`,

  /* --- 2. top bar, brand, connection pill and summary counters ---------- */
  `html body .ui-root .ui-topbar {
  background-color: ${C.topbar};
  background-image: url("${SVG.skyline}"), linear-gradient(180deg, #5A1030 0%, ${C.topbar} 62%, ${C.plate} 100%);
  background-repeat: no-repeat, no-repeat;
  background-position: center bottom, 0 0;
  background-size: cover, 100% 100%;
  border-bottom: 2px solid ${C.gold};
  box-shadow: inset 0 1px 0 rgba(255, 224, 146, 0.24), inset 0 -1px 0 rgba(239, 175, 103, 0.45), 0 6px 18px rgba(10, 4, 16, 0.72);
  padding-inline: 14px;
}`,
  `html body .ui-root .ui-brand {
  background: linear-gradient(180deg, ${C.plate} 0%, ${C.void} 100%);
  border: 1px solid ${C.goldHi};
  border-radius: 6px;
  box-shadow: inset 0 0 0 1px rgba(255, 224, 146, 0.22), 0 2px 8px rgba(10, 4, 16, 0.55);
  padding: 6px 10px;
  gap: 8px;
}`,
  `html body .ui-root .ui-brand__name,
html body .ui-root .ui-wordmark {
  color: ${C.goldBright};
  text-shadow: 0 1px 0 ${C.void};
  font-weight: 700;
}`,
  `html body .ui-root .ui-brand__mark {
  border: 1px solid ${C.goldBright};
  border-radius: 4px;
  background: ${C.plate};
  box-shadow: inset 0 2px 6px rgba(11, 2, 22, 0.76), 0 0 10px rgba(255, 206, 111, 0.42);
}`,
  `html body .ui-root .ui-pill {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-height: 20px;
  padding: 4px 10px;
  background: ${C.plate};
  border: 1px solid ${C.gold};
  border-radius: 999px;
  box-shadow: inset 0 0 0 1px rgba(118, 31, 119, 0.45);
  color: ${C.ink};
  font-size: 12px;
  line-height: 16px;
}`,
  `html body .ui-root .ui-pill.is-open,
html body .ui-root .ui-pill.is-connecting,
html body .ui-root .ui-pill.is-mock {
  background: linear-gradient(180deg, #391944 0%, ${C.plate} 100%);
  border-color: ${C.goldHi};
  color: ${C.goldBright};
}`,
  `html body .ui-root .ui-pill.is-closed {
  border-color: ${C.bad};
  color: ${C.bad};
}`,
  `html body .ui-root .ui-pill__dot {
  width: 8px;
  height: 8px;
  border-radius: 999px;
  background: radial-gradient(circle at 35% 35%, ${C.goldBright} 0%, ${C.ok} 55%, #1E6F47 100%);
  box-shadow: 0 0 0 1px ${C.void}, 0 0 6px rgba(79, 209, 139, 0.65);
}`,
  `html body .ui-root .ui-counters {
  background: ${C.void};
  border: 1px solid ${C.gold};
  border-radius: 6px;
  box-shadow: inset 0 0 0 1px rgba(118, 31, 119, 0.55);
  padding: 3px 4px;
  gap: 4px;
}`,
  `html body .ui-root div.ui-counter {
  background: linear-gradient(180deg, ${C.plate} 0%, ${C.void} 100%);
  border: 1px solid rgba(193, 136, 103, 0.45);
  border-radius: 4px;
  color: ${C.muted};
  padding: 4px 8px;
}`,
  `html body .ui-root .ui-counter__value {
  color: ${C.goldBright};
  font-variant-numeric: tabular-nums;
  font-weight: 700;
}`,
  `html body .ui-root .ui-counter__label {
  color: ${C.ink};
  font-size: 10px;
  line-height: 14px;
  font-weight: 600;
}`,
  `html body .ui-root .ui-counter--working .ui-counter__value { color: ${C.ok}; }`,
  `html body .ui-root .ui-counter--subagents .ui-counter__value { color: ${C.sakura}; }`,
  `html body .ui-root .ui-topbar__right .ui-btn-group {
  background: ${C.void};
  border: 1px solid ${C.gold};
  border-radius: 6px;
  box-shadow: inset 0 0 0 1px rgba(118, 31, 119, 0.45);
  padding: 2px;
}`,

  /* --- 3. framed sidebar / drawer / feed / dialog / popover ------------ */
  `html body .ui-root .ui-panel.ui-sidebar,
html body .ui-root .ui-panel.ui-drawer,
html body .ui-root .ui-popover {
  border-style: solid;
  border-width: 8px;
  border-image-source: url("${SVG.panel}");
  border-image-slice: 16 fill;
  border-image-width: 12px;
  border-image-repeat: stretch;
  border-radius: 6px;
  background: linear-gradient(180deg, #1A0B26 0%, ${C.void} 100%);
  box-shadow: 0 12px 28px rgba(8, 3, 14, 0.78);
}`,
  `@media (width >= 901px) {
  html body .ui-root .ui-panel.ui-sidebar { bottom: calc(var(--gap, 8px) * 2 + 38px); }
}`,
  `html body .ui-root .ui-panel.ui-feed {
  border-style: solid;
  border-width: 8px;
  border-image-source: url("${SVG.panel}");
  border-image-slice: 16 fill;
  border-image-width: 12px;
  border-image-repeat: stretch;
  border-radius: 6px;
  background: linear-gradient(180deg, #1A0B26 0%, ${C.void} 100%);
  box-shadow: 0 12px 28px rgba(8, 3, 14, 0.78);
}`,
  `html body .ui-root .ui-dialog,
html body .ui-root .ui-deliv-modal {
  border-style: solid;
  border-width: 10px;
  border-image-source: url("${SVG.window}");
  border-image-slice: 20 fill;
  border-image-width: 14px;
  border-image-repeat: stretch;
  border-radius: 8px;
  background: ${C.void};
  color: ${C.ink};
  box-shadow: 0 20px 48px rgba(6, 2, 12, 0.88);
}`,
  `html body .ui-root .ui-dialog::backdrop,
html body .ui-root .ui-scrim {
  background: rgba(10, 4, 16, 0.72);
}`,
  `html body .ui-root .ui-side__head,
html body .ui-root .ui-dialog__head,
html body .ui-root .ui-deliv-modal__head,
html body .ui-root .ui-popover__head,
html body .ui-root .ui-drawer__bar,
html body .ui-root .ui-feed__head {
  background: linear-gradient(180deg, #5A1030 0%, ${C.topbar} 100%);
  border-bottom: 1px solid ${C.gold};
  box-shadow: inset 0 -1px 0 rgba(255, 224, 146, 0.22);
  color: ${C.ink};
}`,
  `html body .ui-root .ui-side__title h2,
html body .ui-root .ui-dialog__head h2,
html body .ui-root .ui-drawer__kind,
html body .ui-root .ui-feed__title h2 {
  color: ${C.goldBright};
  letter-spacing: 0.04em;
  text-shadow: 0 1px 0 ${C.void};
  font-weight: 700;
}`,
  `html body .ui-root .ui-tip,
html body .ui-root .ui-toast {
  border-style: solid;
  border-width: 6px;
  border-image-source: url("${SVG.tooltip}");
  border-image-slice: 14 fill;
  border-image-width: 10px;
  border-image-repeat: stretch;
  border-radius: 6px;
  background: linear-gradient(180deg, ${C.plate} 0%, ${C.void} 100%);
  color: ${C.ink};
  box-shadow: inset 0 0 0 1px rgba(235, 80, 134, 0.35), 0 8px 20px rgba(8, 3, 14, 0.82);
}`,
  `html body .ui-root .ui-tip__name { color: ${C.goldBright}; }`,
  `html body .ui-root .ui-tip__since,
html body .ui-root .ui-tip__mood { color: ${C.muted}; }`,

  /* --- 4. search + text fields (paint only; motion reset is block 11) --- */
  `html body .ui-root .ui-search,
html body .ui-root .ui-term__search,
html body .ui-root .ui-msg__input,
html body .ui-root .ui-rename__input {
  min-height: 34px;
  border: 1px solid ${C.gold};
  border-radius: 4px;
  background: ${C.plate};
  color: ${C.ink};
  box-shadow: inset 0 1px 3px rgba(10, 4, 16, 0.65);
}`,
  `html body .ui-root .ui-search {
  margin: 8px 10px;
}`,
  `html body .ui-root .ui-search__icon {
  width: 16px;
  height: 16px;
  background-image: url("${SVG.magnifier}");
  background-repeat: no-repeat;
  background-position: center;
  background-size: 14px 14px;
}`,
  `html body .ui-root .ui-search__icon > svg,
html body .ui-root .ui-search__icon .ui-px-icon {
  opacity: 0;
}`,
  `html body .ui-root .ui-search__input {
  background: transparent;
  color: ${C.ink};
}`,
  `html body .ui-root .ui-search__input::placeholder,
html body .ui-root .ui-msg__input::placeholder,
html body .ui-root .ui-rename__input::placeholder {
  color: ${C.muted};
  opacity: 1;
}`,
  `html body .ui-root .ui-kbd {
  padding: 0 4px;
  border: 1px solid rgba(193, 136, 103, 0.65);
  border-radius: 3px;
  background: ${C.void};
  color: ${C.goldBright};
  font-family: var(--font-mono, monospace);
  font-size: 10px;
}`,

  /* --- 5. rooms + roster rows ------------------------------------------ */
  `html body .ui-root .ui-rooms {
  gap: 8px;
  padding: 4px 8px 10px;
}`,
  `html body .ui-root .ui-room {
  background: rgba(20, 8, 30, 0.88);
  border: 1px solid rgba(193, 136, 103, 0.52);
  border-radius: 6px;
  box-shadow: inset 0 0 0 1px rgba(118, 31, 119, 0.32);
  overflow: hidden;
}`,
  `html body .ui-root .ui-room__head {
  position: relative;
  min-height: 32px;
  background: linear-gradient(90deg, ${C.topbar} 0%, ${C.plate} 100%);
  border-bottom: 1px solid rgba(193, 136, 103, 0.45);
  color: ${C.ink};
  padding-right: 26px;
}`,
  `html body .ui-root .ui-room__head::before {
  background: var(--room-bar, ${C.accent});
  width: 3px;
}`,
  `html body .ui-root .ui-room__head::after {
  content: "";
  position: absolute;
  right: 8px;
  top: 50%;
  width: 12px;
  height: 12px;
  transform: translateY(-50%);
  background-image: url("${SVG.chevron}");
  background-repeat: no-repeat;
  background-position: center;
  pointer-events: none;
}`,
  `html body .ui-root .ui-room__head[aria-expanded="false"]::after {
  transform: translateY(-50%) rotate(-90deg);
}`,
  `html body .ui-root .ui-room__head:hover:not(:disabled),
html body .ui-root .ui-room__head.is-selected {
  background: linear-gradient(90deg, ${C.plum} 0%, ${C.topbar} 100%);
  border-bottom-color: ${C.goldHi};
}`,
  `html body .ui-root .ui-room__name {
  color: ${C.goldBright};
  font-weight: 600;
}`,
  `html body .ui-root .ui-room__count {
  background: ${C.void};
  border: 1px solid ${C.goldHi};
  border-radius: 999px;
  color: ${C.goldBright};
  padding: 1px 7px;
  font-variant-numeric: tabular-nums;
}`,
  `html body .ui-root .ui-room__path,
html body .ui-root .ui-room__tasks,
html body .ui-root .ui-room__tasks-text {
  color: ${C.muted};
}`,
  `html body .ui-root .ui-nodes {
  padding: 4px;
  gap: 3px;
}`,
  `html body .ui-root .ui-agent {
  background: ${C.void};
  border: 1px solid rgba(118, 31, 119, 0.35);
  border-radius: 4px;
  color: ${C.ink};
}`,
  `html body .ui-root .ui-agent:hover:not(:disabled) {
  background: ${C.plate};
  border-color: ${C.gold};
}`,
  `html body .ui-root .ui-agent.is-selected,
html body .ui-root .ui-agent[aria-selected="true"] {
  background: linear-gradient(90deg, rgba(118, 31, 119, 0.52) 0%, ${C.plate} 100%);
  border-color: ${C.goldHi};
  box-shadow: inset 3px 0 0 ${C.accent};
}`,
  `html body .ui-root .ui-agent.is-waiting {
  border-color: rgba(239, 175, 103, 0.72);
}`,
  `html body .ui-root .ui-avatar {
  background: ${C.plate};
  border: 1px solid ${C.gold};
  border-radius: 4px;
  box-shadow: inset 0 0 0 1px rgba(255, 224, 146, 0.18);
}`,
  `html body .ui-root .ui-agent.is-selected .ui-avatar {
  border-color: ${C.goldBright};
}`,
  `html body .ui-root .ui-agent__name {
  color: ${C.ink};
  font-weight: 600;
}`,
  `html body .ui-root .ui-agent.is-selected .ui-agent__name {
  color: ${C.goldBright};
}`,
  `html body .ui-root .ui-role {
  background: ${C.plate};
  border: 1px solid rgba(193, 136, 103, 0.5);
  color: ${C.goldHi};
}`,
  `html body .ui-root .ui-agent__task {
  color: ${C.ink};
}`,
  `html body .ui-root .ui-agent__since,
html body .ui-root .ui-activity,
html body .ui-root .ui-activity__text,
html body .ui-root .ui-activity__time,
html body .ui-root .ui-subs__caption {
  color: ${C.muted};
}`,
  `html body .ui-root .ui-agent:hover .ui-activity__text,
html body .ui-root .ui-agent.is-selected .ui-activity__text,
html body .ui-root .ui-agent.is-selected .ui-agent__since {
  color: ${C.ink};
}`,
  `html body .ui-root .ui-activity__count {
  background: ${C.plum};
  border: 1px solid ${C.accent};
  color: ${C.goldBright};
}`,
  `html body .ui-root .ui-status-dot {
  border-radius: 999px;
  box-shadow: 0 0 0 1.5px ${C.void};
}`,
  `html body .ui-root .ui-status-dot.is-working {
  background: radial-gradient(circle at 35% 35%, ${C.goldBright} 0%, ${C.ok} 65%, #1B7A4E 100%);
}`,
  `html body .ui-root .ui-status-dot.is-waiting {
  background: radial-gradient(circle at 35% 35%, #FFFFFF 0%, ${C.goldBright} 60%, ${C.goldHi} 100%);
}`,
  `html body .ui-root .ui-status-dot.is-done {
  background: radial-gradient(circle at 35% 35%, ${C.goldBright} 0%, ${C.goldHi} 70%, ${C.gold} 100%);
}`,
  `html body .ui-root .ui-status-dot.is-shell {
  background: radial-gradient(circle at 35% 35%, ${C.goldBright} 0%, ${C.accentSoft} 65%, ${C.plum} 100%);
}`,
  `html body .ui-root .ui-status-dot.is-idle { background: ${C.muted}; }`,
  `html body .ui-root .ui-status-dot.is-offline { background: ${C.faint}; opacity: 0.65; }`,

  /* --- 6. activity feed ------------------------------------------------ */
  `html body .ui-root .ui-feed__head {
  height: var(--feed-head, 38px);
  padding-inline: 10px;
}`,
  `html body .ui-root .ui-feed__filter {
  background: ${C.plate};
  border: 1px solid ${C.gold};
  border-radius: 999px;
  color: ${C.goldBright};
  padding: 1px 8px;
}`,
  `html body .ui-root .ui-feed__ticker,
html body .ui-root .ui-feed__empty {
  color: ${C.muted};
}`,
  `html body .ui-root .ui-feed__item + .ui-feed__item {
  border-top: 1px solid rgba(118, 31, 119, 0.35);
}`,
  `html body .ui-root .ui-feed__row {
  background: transparent;
  border-radius: 4px;
  color: ${C.ink};
}`,
  `html body .ui-root .ui-feed__row:hover:not(:disabled) {
  background: ${C.plate};
}`,
  `html body .ui-root .ui-feed__time,
html body .ui-root .ui-feed__room {
  color: ${C.muted};
  font-variant-numeric: tabular-nums;
}`,
  `html body .ui-root .ui-feed__row:hover .ui-feed__time,
html body .ui-root .ui-feed__row:hover .ui-feed__room {
  color: ${C.ink};
}`,
  `html body .ui-root .ui-feed__who {
  color: ${C.goldBright};
  font-weight: 600;
}`,
  `html body .ui-root .ui-feed__icon { color: ${C.accent}; }`,
  `html body .ui-root .ui-feed__text { color: ${C.ink}; }`,

  /* --- 7. settings groups; the segmented TRACK only -------------------- */
  `html body .ui-root .ui-set-group {
  background: rgba(44, 22, 55, 0.62);
  border: 1px solid rgba(193, 136, 103, 0.52);
  border-radius: 6px;
  box-shadow: inset 0 0 0 1px rgba(118, 31, 119, 0.35);
  padding: 10px 12px;
}`,
  `html body .ui-root .ui-set-group + .ui-set-group {
  margin-top: 10px;
}`,
  `html body .ui-root .ui-set-group h3 {
  color: ${C.goldBright};
  border-bottom: 1px solid rgba(193, 136, 103, 0.35);
  padding-bottom: 6px;
  margin-bottom: 8px;
  letter-spacing: 0.04em;
}`,
  `html body .ui-root .ui-set + .ui-set {
  border-top: 1px solid rgba(118, 31, 119, 0.32);
}`,
  `html body .ui-root .ui-set__label {
  color: ${C.ink};
  font-weight: 500;
}`,
  `html body .ui-root .ui-set__hint {
  color: ${C.ink};
  opacity: 0.84;
}`,
  `html body .ui-root .ui-seg {
  background: ${C.void};
  border: 1px solid ${C.gold};
  border-radius: 5px;
  box-shadow: inset 0 1px 2px rgba(10, 4, 16, 0.65);
  padding: 2px;
}`,
  `html body .ui-root .ui-snd__details > summary {
  color: ${C.goldBright};
}`,

  /* --- 8. scrollbars --------------------------------------------------- */
  `html body .ui-root .ui-side__scroll,
html body .ui-root .ui-feed__scroll,
html body .ui-root .ui-drawer__body,
html body .ui-root .ui-dialog__body,
html body .ui-root .ui-term__scroll,
html body .ui-root .ui-hist__list,
html body .ui-root .ui-deliv-list {
  scrollbar-width: thin;
  scrollbar-color: ${C.gold} ${C.void};
}`,
  `html body .ui-root .ui-side__scroll::-webkit-scrollbar,
html body .ui-root .ui-feed__scroll::-webkit-scrollbar,
html body .ui-root .ui-drawer__body::-webkit-scrollbar,
html body .ui-root .ui-dialog__body::-webkit-scrollbar,
html body .ui-root .ui-term__scroll::-webkit-scrollbar,
html body .ui-root .ui-hist__list::-webkit-scrollbar,
html body .ui-root .ui-deliv-list::-webkit-scrollbar {
  width: 8px;
  height: 8px;
}`,
  `html body .ui-root .ui-side__scroll::-webkit-scrollbar-track,
html body .ui-root .ui-feed__scroll::-webkit-scrollbar-track,
html body .ui-root .ui-drawer__body::-webkit-scrollbar-track,
html body .ui-root .ui-dialog__body::-webkit-scrollbar-track,
html body .ui-root .ui-term__scroll::-webkit-scrollbar-track,
html body .ui-root .ui-hist__list::-webkit-scrollbar-track,
html body .ui-root .ui-deliv-list::-webkit-scrollbar-track {
  background: ${C.void};
  border-left: 1px solid rgba(118, 31, 119, 0.45);
}`,
  `html body .ui-root .ui-side__scroll::-webkit-scrollbar-thumb,
html body .ui-root .ui-feed__scroll::-webkit-scrollbar-thumb,
html body .ui-root .ui-drawer__body::-webkit-scrollbar-thumb,
html body .ui-root .ui-dialog__body::-webkit-scrollbar-thumb,
html body .ui-root .ui-term__scroll::-webkit-scrollbar-thumb,
html body .ui-root .ui-hist__list::-webkit-scrollbar-thumb,
html body .ui-root .ui-deliv-list::-webkit-scrollbar-thumb {
  background: linear-gradient(180deg, ${C.goldHi} 0%, ${C.gold} 100%);
  border: 1px solid ${C.void};
  border-radius: 4px;
}`,

  /* --- 9. non-control focus rings -------------------------------------- */
  `html body .ui-root .ui-search:focus-within {
  border-color: ${C.goldBright};
  box-shadow: 0 0 0 2px rgba(235, 80, 134, 0.55), inset 0 1px 3px rgba(10, 4, 16, 0.65);
}`,
  `html body .ui-root .ui-search__input:focus-visible { outline: none; }`,
  `html body .ui-root a:focus-visible,
html body .ui-root summary:focus-visible,
html body .ui-root select:focus-visible,
html body .ui-root textarea:focus-visible {
  outline: 2px solid ${C.goldBright};
  outline-offset: 2px;
  box-shadow: 0 0 0 4px rgba(235, 80, 134, 0.45);
}`,

  /* --- 10. ZERO CONTROL MOTION (Stage 9) -------------------------------
     Motion is reset to `none`; fill, size, position, hit area, focus and
     every action stay exactly as they were. `.ui-switch` and checkboxes are
     excluded (Sakuga owns toggles/checkboxes), as are all Sakuga elements. */
  `html body .ui-root .ui-btn,
html body .ui-root .ui-icon-btn,
html body .ui-root .ui-agent,
html body .ui-root .ui-feed__row,
html body .ui-root .ui-ttab,
html body .ui-root .ui-seg__opt,
html body .ui-root .ui-counter,
html body .ui-root .ui-room__head,
html body .ui-root .ui-room__head::after,
html body .ui-root .ui-side__close,
html body .ui-root .ui-ttab__close,
html body .ui-root .ui-term__close,
html body .ui-root .ui-search,
html body .ui-root .ui-term__search,
html body .ui-root .ui-msg__input,
html body .ui-root .ui-rename__input,
html body .ui-root .ui-deliv-card {
  transition: none;
  animation: none;
}`,
  `html body .ui-root .ui-counter:active:not(:disabled),
html body .ui-root .ui-deliv-card:active {
  transform: none;
}`,
];

/**
 * Iframe CSS. The baseline is unconditional; an active theme may add its own
 * panel/dialog 9-slice. A theme's button slice is never emitted.
 */
export function officeUiSkinRules(payload?: Payload | null): string[] {
  const rules = [...BASELINE];
  const slices = payload?.ui?.slices ?? {};
  for (const [key, targets] of [
    ["panel", [".ui-panel", ".ui-panel.ui-sidebar", ".ui-panel.ui-drawer", ".ui-panel.ui-feed"]],
    ["dialog", [".ui-dialog", ".ui-deliv-modal"]],
  ] as const) {
    const src = safeSrc(slices[key]?.src);
    const cell = Array.isArray(slices[key]?.slice) && slices[key]?.slice.length === 4
      ? (slices[key]?.slice as unknown[]).filter((n) => Number.isInteger(n) && Number(n) > 0)
      : [];
    if (!src || cell.length !== 4) continue;
    rules.push(targets.map((target) => `html body .ui-root ${target} {`
      + `border-image-source:url("${src}");`
      + `border-image-slice:${cell.join(" ")};`
      + "border-image-repeat:stretch; }").join("\n"));
  }
  return rules;
}

/** Install the skin in a same-origin office iframe; returns an undo function. */
export function installOfficeUiSkin(win: Window): () => void {
  const doc = win.document;
  const head = doc?.head;
  if (!head) return () => {};
  const payload =
    (win as Window & { __QUBIT_OFFICE_THEME__?: Payload }).__QUBIT_OFFICE_THEME__;
  const style = doc.createElement("style");
  style.id = STYLE_ID;
  style.setAttribute("data-office-ui-skin", "");
  style.textContent = officeUiSkinRules(payload).join("\n");
  head.appendChild(style);

  // Habblaud appends its stylesheets asynchronously after load. Keep the skin
  // last in the head (on equal specificity, the later rule wins) without ever
  // observing controls: only the head's direct children are watched.
  const Observer = (win as unknown as {
    MutationObserver?: typeof MutationObserver;
  }).MutationObserver;
  let observer: MutationObserver | null = null;
  const keepLast = (): void => {
    if (style.parentNode !== head) {
      head.appendChild(style);
      return;
    }
    const after = Array.from(head.children).slice(
      Array.from(head.children).indexOf(style) + 1);
    if (after.some((node) => node.tagName === "LINK" || node.tagName === "STYLE")) {
      head.appendChild(style);
    }
  };
  if (Observer) {
    observer = new Observer(keepLast);
    observer.observe(head, { childList: true });
  }
  return () => {
    observer?.disconnect();
    style.remove();
  };
}
