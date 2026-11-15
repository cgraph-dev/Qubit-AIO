/* The office theme's data-only contract, and the atlas helpers the panel uses.
 *
 * WHAT THIS FILE IS, AND WHAT IT IS NOT
 * -------------------------------------
 * It mirrors the payload ``GET /api/office/theme`` serves (Tasks 1-4) and turns
 * one seat's ``[col, row]`` atlas cell into a CSS sprite style. It does NOT
 * fetch, load, or apply a theme: Task 6 owns the loader and the application
 * (the optional ``?theme=`` fetch, the host link, the iframe injection), and
 * passes the resulting payload into ``AgentsPanel`` as a prop. Keeping the type
 * and the pure helpers here means every consumer — the panel today, the host
 * loader next — agrees on one shape and one cell convention.
 *
 * THE SPRITE CONVENTION
 * ---------------------
 * A pack ships ONE atlas image per kind plus an index of cells. A cell is
 * ``[col, row]``, measured in whole cells from the atlas' top-left. A sprite of
 * cell ``(col, row)`` is painted by shifting the atlas left by ``col * cellW``
 * and up by ``row * cellH`` — expressed with custom properties so the same
 * markup works for whichever atlas is active (and so a future theme can change
 * the cell size without new JS).
 *
 * No art is committed and no path is assembled here: the URLs arrive
 * pre-validated from the server (``?v=`` included), and an absent URL stays
 * ``null`` so a caller can fall back instead of emitting a broken request.
 */

import { hostBasePath } from "./office-app";

/** A ``[col, row]`` atlas cell, or nothing when the pack does not list it. */
export type AtlasCell = readonly [number, number] | null | undefined;

/** The ``bodies`` section of the payload: a second atlas of 32x64 frames. */
export interface OfficeThemeBodyFrame {
  w: number;
  h: number;
}

export interface OfficeThemeBodies {
  atlas: string | null;
  index: string | null;
  size: number | null;
  frame: OfficeThemeBodyFrame | null;
}

/**
 * One 9-slice descriptor. Validation lives on the server; this type only
 * describes what a validated payload can carry (Task 7 consumes it).
 */
export interface OfficeThemeSlice {
  src: string;
  slice: [number, number, number, number];
  width: string;
  repeat: "stretch" | "round" | "repeat";
}

/**
 * The theme payload, exactly as ``GET /api/office/theme`` serves it.
 *
 * ``active: false`` is the classic theme: every URL is ``null``, ``assetBase``
 * is empty, and ``tokens``/``slices`` are empty objects — never a fabricated
 * pack. ``templateVersion`` only exists on an active payload; ``reason`` only
 * when a requested theme fell back to classic.
 */
export interface OfficeTheme {
  ok?: boolean;
  schema?: string;
  id: string;
  label: string;
  version: string;
  credit: string;
  license: string;
  active: boolean;
  templateVersion?: number | null;
  assetBase: string;
  atlas: string | null;
  index: string | null;
  bodies: OfficeThemeBodies;
  tokens: Record<string, string>;
  slices: Record<string, OfficeThemeSlice>;
  /**
   * Full-pack Modern UI 9-slice backgrounds. The trimmed payload carries
   * ``{slices: {...}}`` with absolute asset URLs. Older/classic payloads omit
   * it or carry an empty map, which is why every reader treats it as optional.
   */
  ui?: { slices?: Record<string, OfficeThemeUiSlice> };
  reason?: string;
}

/** One Modern UI 9-slice descriptor (same shape as the legacy slice). */
export interface OfficeThemeUiSlice {
  src: string;
  slice: [number, number, number, number];
  width: string;
  repeat: "stretch" | "round" | "repeat";
}

/** Both atlas URLs, or ``null`` per kind when the payload has none. */
export interface ThemeAtlasUrls {
  portrait: string | null;
  body: string | null;
}

export function themeAtlasUrls(
  theme: OfficeTheme | null | undefined,
): ThemeAtlasUrls {
  return {
    portrait: (theme && theme.atlas) || null,
    body: (theme && theme.bodies && theme.bodies.atlas) || null,
  };
}

/** The custom properties + position one portrait sprite needs. */
export interface PortraitCellStyle {
  "--cell-x": number;
  "--cell-y": number;
  backgroundPosition: string;
}

/** The custom properties + position one body sprite needs. */
export interface BodyCellStyle {
  "--body-x": number;
  "--body-y": number;
  backgroundPosition: string;
}

/** A whole, positive cell size; the payload's own values are validated. */
function cellPx(value: number | null | undefined, fallback: number): number {
  return typeof value === "number" && Number.isFinite(value) && value > 0
    ? Math.round(value)
    : fallback;
}

/**
 * Position a portrait cell. ``size`` is the atlas cell edge in CSS pixels; the
 * fixed 32px face of the panel passes 32, and the helper keeps it explicit so a
 * 16/48 pack would not silently misalign.
 */
export function portraitCellStyle(
  cell: AtlasCell,
  size: number,
): PortraitCellStyle | undefined {
  if (!cell) return undefined;
  const [col, row] = cell;
  const px = cellPx(size, 32);
  return {
    "--cell-x": col,
    "--cell-y": row,
    backgroundPosition:
      `calc(-1 * var(--cell-x) * ${px}px) calc(-1 * var(--cell-y) * ${px}px)`,
  };
}

/**
 * Position a body frame. The body atlas packs 32x64 (by default) frames, so the
 * horizontal step is the frame width and the vertical step the frame height —
 * not one square cell size.
 */
export function bodyCellStyle(
  cell: AtlasCell,
  frame: OfficeThemeBodyFrame | null | undefined,
): BodyCellStyle | undefined {
  if (!cell || !frame) return undefined;
  const [col, row] = cell;
  const w = cellPx(frame.w, 32);
  const h = cellPx(frame.h, 64);
  return {
    "--body-x": col,
    "--body-y": row,
    backgroundPosition:
      `calc(-1 * var(--body-x) * ${w}px) calc(-1 * var(--body-y) * ${h}px)`,
  };
}

/**
 * A readable initials chip for an avatar fallback: first and last word, so
 * "Chief of Staff" reads "CS" and "One" reads "O". Returns "" when the name
 * carries no letters or digits, which lets the panel fall through to the emoji
 * instead of rendering an empty chip.
 */
export function initialsFor(name: string): string {
  const words = String(name || "")
    .trim()
    .split(/[\s._-]+/)
    .filter(Boolean);
  // Scan INTO each word rather than only its first codepoint: an emoji or
  // punctuation prefix must not drop the word ("🤖Bot" is still "B"). Words
  // with no letter/digit at all contribute nothing (the emoji fallback wins).
  const letters: string[] = [];
  for (const word of words) {
    const found = Array.from(word)
      .find((ch) => /[\p{L}\p{N}]/u.test(ch));
    if (found) letters.push(found);
  }
  if (letters.length === 0) return "";
  const picked = letters.length === 1
    ? [letters[0]]
    : [letters[0], letters[letters.length - 1]];
  return picked.join("").toUpperCase();
}

/** The bit of the platform ``Image`` the preload needs (testable seam). */
export interface AtlasImageLike {
  onload: (() => void) | null;
  onerror: (() => void) | null;
  src: string;
}

export type AtlasImageCtor = new () => AtlasImageLike;

/**
 * Preload every atlas the theme names, ONCE, and resolve true only when all of
 * them decoded.
 *
 * WHY PRELOAD AT ALL
 * ------------------
 * The panel renders a CSS sprite per seat, not an ``<img>`` per seat: done the
 * other way, 283 seats are 283 image requests and long scrolls flood the
 * browser's connection pool (the documented sprite-atlas decision). But a CSS
 * background paints nothing until the image is back, so the initials would
 * vanish into empty boxes for that first frame. Preloading the two sheets lets
 * the panel keep initials visible until both are actually decodable, then
 * commit ``office-portraits-ready`` once.
 *
 * Returns ``null`` when there is nothing to preload (classic, or no platform
 * ``Image``), which callers treat as "not ready" — never as a failure to retry.
 */
export function preloadThemeAtlases(
  theme: OfficeTheme | null | undefined,
  ImageImpl?: AtlasImageCtor,
): Promise<boolean> | null {
  const urls = themeAtlasUrls(theme);
  const wanted = Array.from(new Set(
    [urls.portrait, urls.body].filter((url): url is string => Boolean(url)),
  ));
  if (wanted.length === 0) return null;

  const Img = ImageImpl
    ?? (globalThis as { Image?: AtlasImageCtor }).Image;
  if (!Img) return null;

  return Promise.all(wanted.map((src) => new Promise<boolean>((resolve) => {
    const image = new Img();
    image.onload = () => resolve(true);
    image.onerror = () => resolve(false);
    image.src = src;
  }))).then((results) => results.every(Boolean));
}

/* ======================================================================
 * THE HOST THEME (Task 6)
 * ======================================================================
 * The office app document gets its tokens from the server (one injected
 * `<style>`), but the Qubit shell AROUND the iframe (the Agents/HUD docks, the
 * seat model panel, the injected menu icons) is a different document. It has
 * no server-injected style of its own, so the host loads the same validated
 * payload and links the generated host stylesheet.
 *
 * WHY A LINK AND NOT INLINE CSS
 * -----------------------------
 * The semantic-token -> CSS-variable mapping lives once, in
 * `voicebridge/office/theme.py`. `GET /plugins/qubit-office/themes/<id>/
 * theme.css?v=<version>` is that same mapping rendered for the host scope
 * (`:root[data-office-theme="<id>"]`), so the host and the iframe cannot drift.
 * The URL carries the pack version, so the response is safely cacheable.
 */

/** The subset of `fetch` this module needs (a test seam, not a polyfill). */
export type ThemeFetch = (
  input: string,
  init?: {
    cache?: "no-store";
    credentials?: "same-origin";
    headers?: Record<string, string>;
  },
) => Promise<{
  ok?: boolean;
  status?: number;
  json: () => Promise<unknown>;
}>;

/** The compat route the dashboard token gate already authorizes. */
export const OFFICE_THEME_ROUTE = "/agent/api/office/theme";

/** The route namespace the server serves theme assets under. */
export const OFFICE_THEME_ASSET_BASE = "/plugins/qubit-office/themes";

/** The server-injected global that carries the resolved host payload. */
export const OFFICE_THEME_GLOBAL = "__QUBIT_OFFICE_THEME__";

type ThemeWindow = (Window & {
  __QUBIT_SESSION_TOKEN__?: string;
  __QUBIT_OFFICE_THEME__?: unknown;
}) | null | undefined;

/** The window this module should read the global/token from. */
function themeWindow(win?: Window | null): ThemeWindow {
  if (win !== undefined) return win as ThemeWindow;
  if (typeof window === "undefined") return null;
  return window as ThemeWindow;
}

/** Prefix a root-relative URL with the dashboard base (subpath deployments). */
function withHostBase(url: string): string {
  const base = hostBasePath();
  if (!url || !base || !url.startsWith("/")) return url;
  if (url === base || url.startsWith(`${base}/`)) return url;
  return `${base}${url}`;
}

/** The class the shell root carries so Task 7 can scope its own chrome. */
export function officeThemeClass(
  theme: OfficeTheme | null | undefined,
): string {
  return theme && theme.active && theme.id ? `office-theme-${theme.id}` : "";
}

/** The shell roles/states a pack may 9-slice. */
export type OfficeSliceRole =
  | "dock"
  | "card"
  | "tab"
  | "button"
  | "button-hover"
  | "button-pressed";

/**
 * The `data-office-slice` value for a shell element, or undefined.
 *
 * The pack declares which roles it has art for; the host stylesheet generated
 * by `host_token_css` emits `[data-office-slice="<role>"]` rules only for
 * those names. An element must therefore opt in by carrying the attribute
 * ONLY when its role is declared — otherwise a theme switch would leave a
 * stale slice on classic chrome.
 *
 * `button-hover` / `button-pressed` are STATES of the `button` role: the
 * server attaches them to `[data-office-slice="button"]:hover` / `:active`
 * (and `[aria-pressed="true"]`), so an element only ever carries
 * `data-office-slice="button"`.
 */
export function officeSliceName(
  theme: OfficeTheme | null | undefined,
  role: OfficeSliceRole,
): string | undefined {
  if (!theme || !theme.active) return undefined;
  const slices = theme.slices;
  if (!slices || typeof slices !== "object") return undefined;
  return slices[role] ? role : undefined;
}

/* ======================================================================
 * MODERN UI 9-SLICE CHROME
 * ======================================================================
 * The full pack carries a second, richer slice block under ``ui.slices``:
 * panel / button / dialog (and, when a pack declares them, input / tab). The
 * server's generated host stylesheet intentionally only wires the LEGACY
 * top-level ``slices``; this module turns the ``ui`` block into a small,
 * scoped host stylesheet so the shell's own docks, buttons, tabs and the Talk
 * dialog can use the Modern UI frames without touching the vendored client.
 *
 * NO PACK PATH IS INVENTED. Every URL comes pre-validated from the payload;
 * an absent slice stays absent, and each role has an explicit, documented
 * fallback chain so a pack that only ships panel/button/dialog still frames
 * inputs and tabs instead of silently showing a generic box.
 */

/** Roles the shell can frame with the Modern UI art. */
export type OfficeUiRole =
  | "panel"
  | "dock"
  | "card"
  | "dialog"
  | "button"
  | "tab"
  | "input";

/** Roles whose caller opts into the filled centre. */
export const OFFICE_UI_FILLED_ROLES: readonly OfficeUiRole[] = [
  "dialog", "button", "tab", "input",
];

/**
 * The documented fallback chain per role. Each name is looked up in
 * ``theme.ui.slices``; the first present wins. The list is intentionally
 * narrow: a button must never borrow a dialog frame unless no button/panel art
 * exists at all.
 */
const OFFICE_UI_SLICE_CHAIN: Record<OfficeUiRole, string[]> = {
  // The Modern UI "panel" crop is the clean, full-frame sheet. The Talk panel
  // uses it deliberately for its frame/input/buttons: the current local
  // extraction of the 48x32 dialog and 36x32 button carries transparent
  // margins (the asset pipeline owns that crop), while the panel crop frames
  // the same dialogue surface without holes.
  panel: ["panel", "dialog"],
  dock: ["dock", "panel", "dialog"],
  card: ["card", "panel", "dialog"],
  dialog: ["dialog", "panel"],
  button: ["button", "panel"],
  tab: ["tab", "button", "panel"],
  input: ["input", "dialog", "panel"],
};

const OFFICE_UI_ROLES: readonly OfficeUiRole[] = [
  "panel", "dock", "card", "dialog", "button", "tab", "input",
];

/**
 * The resolved `ui.slices` entry name for a role, or undefined.
 *
 * Classic / absent / malformed payloads return undefined so callers emit no
 * attribute and no CSS — the classic chrome stays byte-identical.
 */
export function officeUiSliceName(
  theme: OfficeTheme | null | undefined,
  role: OfficeUiRole,
): string | undefined {
  if (!theme || !theme.active) return undefined;
  const slices = theme.ui && theme.ui.slices;
  if (!slices || typeof slices !== "object") return undefined;
  const chain = OFFICE_UI_SLICE_CHAIN[role] || [];
  for (const candidate of chain) {
    if (slices[candidate]) return candidate;
  }
  return undefined;
}

/**
 * The data attributes one shell element should carry for a role.
 *
 * The attribute VALUE is the role (not the resolved slice name) so the
 * generated rules stay stable when a pack later adds a purpose-built asset:
 * ``[data-office-ui-slice="button"]`` always gets the button frame, whichever
 * descriptor currently backs it.
 */
export function officeUiSliceAttrs(
  theme: OfficeTheme | null | undefined,
  role: OfficeUiRole,
  filled = false,
): Record<string, string> {
  if (!officeUiSliceName(theme, role)) return {};
  const attrs: Record<string, string> = { "data-office-ui-slice": role };
  if (filled) attrs["data-office-ui-filled"] = "true";
  return attrs;
}

function cssString(value: string): string {
  return String(value).replace(/\\/g, "\\\\").replace(/"/g, '\\"');
}

function cssUrl(value: string): string {
  // Prefix a subpath deployment's base, exactly like the theme CSS URL.
  return cssString(withHostBase(value));
}

function validSlice(
  descriptor: OfficeThemeUiSlice | undefined,
): { slice: [number, number, number, number]; width: string;
     repeat: "stretch" | "round" | "repeat"; src: string } | null {
  if (!descriptor || typeof descriptor !== "object") return null;
  const raw = descriptor.slice;
  if (!Array.isArray(raw) || raw.length !== 4) return null;
  const numbers = raw.map((value) =>
    typeof value === "number" && Number.isFinite(value) && value > 0
      ? Math.round(value)
      : NaN);
  if (numbers.some((value) => Number.isNaN(value))) return null;
  const width = String(descriptor.width || "");
  if (!/^[1-9][0-9]{0,3}px$/.test(width)) return null;
  const repeat = descriptor.repeat;
  if (repeat !== "stretch" && repeat !== "round" && repeat !== "repeat") {
    return null;
  }
  const src = typeof descriptor.src === "string" ? descriptor.src : "";
  // The payload's slice URLs are root-relative, server-validated paths. A
  // non-root value (javascript:, data:, a protocol-relative URL) is refused
  // here as defence in depth, not trusted because it "looks like" a string.
  if (!src.startsWith("/")) return null;
  return {
    slice: numbers as [number, number, number, number],
    width,
    repeat,
    src,
  };
}

/**
 * The scoped host stylesheet for the payload's ``ui.slices`` block.
 *
 * Only the roles a pack can actually frame are emitted, and every rule is
 * scoped to the Qubit shell root (``.office-game[data-office-theme="<id>"]``)
 * so the vendored office document and the rest of the dashboard are never
 * targeted. Returns "" for classic or a payload with no usable slices.
 */
export function officeUiSliceCss(
  theme: OfficeTheme | null | undefined,
): string {
  if (!theme || !theme.active || !theme.id) return "";
  const slices = theme.ui && theme.ui.slices;
  if (!slices || typeof slices !== "object") return "";
  const scope = `.office-game[data-office-theme="${cssString(theme.id)}"]`
    + ` [data-office-ui-slice=`;
  const lines: string[] = [];
  for (const role of OFFICE_UI_ROLES) {
    const name = officeUiSliceName(theme, role);
    if (!name) continue;
    const descriptor = validSlice(slices[name]);
    if (!descriptor) continue;
    const target = `${scope}"${role}"]`;
    lines.push(`${target} {`);
    lines.push(`  border-image-source: url("${cssUrl(descriptor.src)}");`);
    lines.push(
      `  border-image-slice: ${descriptor.slice.join(" ")};`);
    lines.push(`  border-image-width: ${descriptor.width};`);
    lines.push(`  border-image-repeat: ${descriptor.repeat};`);
    lines.push("  border-style: solid;");
    lines.push(`  border-width: ${descriptor.width};`);
    lines.push("  box-sizing: border-box;");
    lines.push("  image-rendering: pixelated;");
    lines.push("}");
    // The caller opts into the filled centre per element. Fill paints the
    // pack's own paper/plate colour, so text on a filled frame is dark ink
    // (the modern-UI sheets are light); unfilled frames keep the themed text.
    lines.push(`${target}[data-office-ui-filled="true"] {`);
    lines.push(
      `  border-image-slice: ${descriptor.slice.join(" ")} fill;`);
    lines.push("  color: #2a1a12;");
    lines.push("}");
  }
  return lines.length ? `${lines.join("\n")}\n` : "";
}

/**
 * Apply the Modern UI slice stylesheet to the HOST document, returning a
 * disposer that removes only what this call installed.
 *
 * Exactly one ``<style data-office-ui-slices>`` exists at a time; a classic
 * theme installs nothing.
 */
export function applyOfficeUiSlices(
  doc: Document | null | undefined,
  theme: OfficeTheme | null | undefined,
): () => void {
  if (!doc || typeof doc.createElement !== "function") return () => {};
  const previous = Array.from(
    doc.querySelectorAll("style[data-office-ui-slices]"));
  for (const node of previous) node.remove();
  const css = officeUiSliceCss(theme);
  if (!css) return () => {};
  const style = doc.createElement("style");
  style.setAttribute("data-office-ui-slices", theme ? theme.id : "");
  style.textContent = css;
  (doc.head ?? doc.documentElement ?? doc.body).appendChild(style);
  let disposed = false;
  return () => {
    if (disposed) return;
    disposed = true;
    if (style.isConnected) style.remove();
  };
}

/**
 * The generated host stylesheet URL for an active theme, or "" for classic.
 *
 * The CSS is generated server-side from the SAME validated payload: a classic
 * theme has no stylesheet, and every URL carries `?v=<version>` so it can be
 * cached for an hour (the version names the bytes). ``assetBase`` is a route
 * URL from the server; when it is absent (a hand-built/legacy payload) the
 * known namespace is used, and the dashboard's own base path is prefixed so a
 * subpath deployment resolves it (same rule as ``agentsPanelUrl``).
 */
export function themeCss(theme: OfficeTheme | null | undefined): string {
  if (!theme || !theme.active || !theme.id) return "";
  const base = String(
    theme.assetBase || `${OFFICE_THEME_ASSET_BASE}/${theme.id}`,
  ).replace(/\/+$/, "");
  if (!base) return "";
  const version = encodeURIComponent(theme.version || "");
  return withHostBase(`${base}/theme.css?v=${version}`);
}

/** Normalise a dashboard base path the same way `office-app.ts` does. */
function themeBase(base?: string): string {
  const raw = base === undefined ? hostBasePath() : base;
  return raw.endsWith("/") ? raw.slice(0, -1) : raw;
}

/** Read the document's injected session token (or the parent's, in an app). */
function sessionToken(win?: Window | null): string {
  const w = themeWindow(win);
  if (!w) return "";
  if (typeof w.__QUBIT_SESSION_TOKEN__ === "string"
      && w.__QUBIT_SESSION_TOKEN__) {
    return w.__QUBIT_SESSION_TOKEN__;
  }
  try {
    const parent = (w as unknown as {
      parent?: { __QUBIT_SESSION_TOKEN__?: string };
    }).parent;
    return (parent && parent.__QUBIT_SESSION_TOKEN__) || "";
  } catch {
    return "";
  }
}

/**
 * The server-injected host payload, when the shell is served by Qubit.
 *
 * The global is the PRIMARY source: it is generated on the server from the
 * same resolved payload as the iframe (so config/env themes work with no
 * `?theme=` and no first-paint fetch/auth gap). It is validated here rather
 * than trusted: an object with a non-empty string ``id`` and a boolean
 * ``active``. An absent/invalid global falls through to the authenticated
 * fetch below.
 */
export function readOfficeThemeGlobal(
  win?: Window | null,
): OfficeTheme | null {
  const raw = themeWindow(win)?.[OFFICE_THEME_GLOBAL];
  if (!raw || typeof raw !== "object") return null;
  const candidate = raw as Partial<OfficeTheme>;
  if (typeof candidate.id !== "string" || !candidate.id) return null;
  if (typeof candidate.active !== "boolean") return null;
  if (candidate.active) {
    if (typeof candidate.tokens !== "object" || candidate.tokens === null) {
      return null;
    }
  }
  return candidate as OfficeTheme;
}

/**
 * Load the resolved office theme for the host.
 *
 * ORDER MATTERS. The server-injected global wins: it already contains the
 * config/env/`?theme=` resolution (the env kill switch still applies on the
 * server), so the host paints the same theme as the iframe with no request.
 * Only when the global is absent (an older server, or the shell embedded
 * outside Qubit) does this make ONE authenticated `no-store` request.
 *
 * Classic is a valid answer from both paths and results in no class/link; an
 * unknown/invalid override or a failed response is also `null`, never a
 * fabricated pack.
 */
export async function loadOfficeTheme(
  fetchImpl: ThemeFetch | null | undefined,
  base?: string,
  requested?: string | null,
  win?: Window | null,
): Promise<OfficeTheme | null> {
  const injected = readOfficeThemeGlobal(win);
  if (injected) return injected;
  if (typeof fetchImpl !== "function") return null;
  const want = String(requested ?? "").trim();
  const query = want ? `?theme=${encodeURIComponent(want)}` : "";
  const url = `${themeBase(base)}${OFFICE_THEME_ROUTE}${query}`;
  const token = sessionToken(win);
  const headers: Record<string, string> = { Accept: "application/json" };
  if (token) headers["X-Qubit-Session-Token"] = token;
  try {
    const response = await fetchImpl(url, {
      cache: "no-store",
      credentials: "same-origin",
      headers,
    });
    if (!response || response.ok === false) return null;
    const payload = await response.json() as OfficeTheme | null;
    if (!payload || typeof payload.active !== "boolean") return null;
    if (typeof payload.id !== "string" || !payload.id) return null;
    return payload;
  } catch {
    return null;
  }
}

/**
 * Apply an active theme to the HOST document, and return a disposer.
 *
 * Adds `data-office-theme="<id>"` to `<html>` (the host stylesheet's scope) and
 * exactly ONE `<link rel="stylesheet" data-office-theme="<id>">`. Calling it
 * again replaces the previous application, never stacking links; classic
 * removes any prior application and adds nothing. The disposer removes only
 * what this call installed (a later application owns its own link).
 */
export function applyHostTheme(
  doc: Document | null | undefined,
  theme: OfficeTheme | null | undefined,
): () => void {
  if (!doc || typeof doc.createElement !== "function") return () => {};
  const root = doc.documentElement ?? null;
  // Only OUR carriers. The `data-office-theme` attribute is also set on
  // `<html>`, so a bare `[data-office-theme]` query would match the document
  // element itself and remove it.
  const previous = Array.from(
    doc.querySelectorAll("link[data-office-theme], style[data-office-theme]"));
  for (const node of previous) node.remove();
  if (root) root.removeAttribute("data-office-theme");
  if (!theme || !theme.active || !theme.id) return () => {};

  const href = themeCss(theme);
  if (!href) return () => {};
  const id = theme.id;
  if (root) root.setAttribute("data-office-theme", id);
  const link = doc.createElement("link");
  link.setAttribute("data-office-theme", id);
  link.setAttribute("rel", "stylesheet");
  link.setAttribute("href", href);
  (doc.head ?? root ?? doc.body).appendChild(link);

  let disposed = false;
  return () => {
    if (disposed) return;
    disposed = true;
    if (link.isConnected) link.remove();
    // A newer application may own the attribute now; only clear our own id.
    if (root && root.getAttribute("data-office-theme") === id
        && !doc.querySelector("link[data-office-theme]")) {
      root.removeAttribute("data-office-theme");
    }
  };
}
