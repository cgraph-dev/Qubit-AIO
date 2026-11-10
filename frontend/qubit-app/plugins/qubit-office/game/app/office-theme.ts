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
  reason?: string;
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

/** The class the shell root carries so Task 7 can scope its own chrome. */
export function officeThemeClass(
  theme: OfficeTheme | null | undefined,
): string {
  return theme && theme.active && theme.id ? `office-theme-${theme.id}` : "";
}

/**
 * The generated host stylesheet URL for an active theme, or "" for classic.
 *
 * The CSS is generated server-side from the SAME validated payload: a classic
 * theme has no stylesheet, and every URL carries `?v=<version>` so it can be
 * cached for an hour (the version names the bytes).
 */
export function themeCss(theme: OfficeTheme | null | undefined): string {
  if (!theme || !theme.active || !theme.id) return "";
  const base = String(theme.assetBase || "").replace(/\/+$/, "");
  if (!base) return "";
  const version = encodeURIComponent(theme.version || "");
  return `${base}/theme.css?v=${version}`;
}

/** Normalise a dashboard base path the same way `office-app.ts` does. */
function themeBase(base?: string): string {
  const raw = base ?? "";
  return raw.endsWith("/") ? raw.slice(0, -1) : raw;
}

/**
 * Load the active theme for the host, with ONE `no-store` request.
 *
 * Classic (no requested id, or the literal `classic`) resolves `null` WITHOUT
 * a request: there is nothing to theme, and an idle page must not poll. A
 * requested id asks the server, whose answer is authoritative — an unknown or
 * invalid override falls back to `classic` (`active: false`) and so does a
 * failed/garbage response, never a fabricated pack.
 *
 * The env kill switch (`QUBIT_OFFICE_THEME`) is applied by the SAME server
 * handler, so the returned payload is what the iframe is actually themed with;
 * `?theme=` is a request, not a client-side decision.
 */
export async function loadOfficeTheme(
  fetchImpl: ThemeFetch | null | undefined,
  base?: string,
  requested?: string | null,
): Promise<OfficeTheme | null> {
  const want = String(requested ?? "").trim();
  if (!want || want === "classic") return null;
  if (typeof fetchImpl !== "function") return null;
  const url = `${themeBase(base)}${OFFICE_THEME_ROUTE}?theme=${encodeURIComponent(want)}`;
  try {
    const response = await fetchImpl(url, {
      cache: "no-store",
      credentials: "same-origin",
      headers: { Accept: "application/json" },
    });
    if (!response || response.ok === false) return null;
    const payload = await response.json() as OfficeTheme | null;
    if (!payload || payload.active !== true) return null;
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
