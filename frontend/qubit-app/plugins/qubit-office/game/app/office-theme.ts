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
