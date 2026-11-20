/**
 * Persistence + route gating for the optional manga skin.
 *
 * Deliberately framework-free so the <html> marker can be applied before
 * React paints (main.tsx) and so the gate and the toggle share one source of
 * truth. No backend, route or component behaviour lives here.
 */

import { isMangaThemeName } from "@/themes/presets";

export const MANGA_SKIN_KEY = "qubit-manga-skin-enabled";
export const MANGA_SKIN_EVENT = "qubit-manga-change";
const THEME_STORAGE_KEY = "qubit-dashboard-theme";
const LEGACY_THEME_STORAGE_KEY = "hermes-dashboard-theme";

/** Surfaces that must never be skinned. */
export const MANGA_EXCLUDED_PATHS = [
  "/qubit-workbench",
  "/workbench", // Explicitly preserve the standalone Workbench route and descendants.
  "/qubit-hud",
] as const;

/** True when the current pathname may receive the manga skin. */
export function mangaRouteEligible(pathname: string): boolean {
  return !MANGA_EXCLUDED_PATHS.some(
    (path) => pathname === path || pathname.startsWith(path + "/"),
  );
}

/** The persisted opt-in. Private mode / disabled storage reads as OFF. */
export function readMangaPreference(): boolean {
  if (typeof window === "undefined") return false;
  try {
    return window.localStorage.getItem(MANGA_SKIN_KEY) === "true";
  } catch {
    return false;
  }
}

function writeMangaPreference(enabled: boolean): void {
  try {
    window.localStorage.setItem(MANGA_SKIN_KEY, String(enabled));
  } catch {
    /* storage disabled (private mode): the in-memory store still updates */
  }
}

// ---------------------------------------------------------------------------
// Tiny external store so the gate and the toggle can never disagree.
// ---------------------------------------------------------------------------

const listeners = new Set<() => void>();
let source: "memory" | "storage" = "memory";
let memoryEnabled: boolean | null = null;

export function subscribeManga(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function currentMangaEnabled(): boolean {
  if (memoryEnabled === null) memoryEnabled = readMangaPreference();
  // A storage event from another tab must win over the in-memory copy.
  if (source === "storage") {
    memoryEnabled = readMangaPreference();
    source = "memory";
  }
  return memoryEnabled;
}

function notify(): void {
  for (const listener of listeners) listener();
}

function onExternalChange(): void {
  source = "storage";
  notify();
}

if (typeof window !== "undefined") {
  window.addEventListener("storage", onExternalChange);
  window.addEventListener(MANGA_SKIN_EVENT, onExternalChange);
}

/** Persist + broadcast a new preference. Returns the value actually stored. */
export function setMangaEnabled(enabled: boolean): boolean {
  memoryEnabled = enabled;
  source = "memory";
  writeMangaPreference(enabled);
  notify();
  if (typeof window !== "undefined") {
    // `storage` does not fire in the writing tab; the custom event does.
    window.dispatchEvent(new Event(MANGA_SKIN_EVENT));
  }
  return enabled;
}

export function applyMangaAttribute(active: boolean): void {
  if (typeof document === "undefined") return;
  if (active) document.documentElement.dataset.qubitManga = "on";
  else delete document.documentElement.dataset.qubitManga;
}

/** The theme the shell is about to paint (server-injected or persisted). */
export function savedThemeName(): string {
  if (typeof window === "undefined") return "";
  const injected = (window as Window & { __QUBIT_THEME__?: unknown })
    .__QUBIT_THEME__;
  if (typeof injected === "string" && injected) return injected;
  try {
    return (
      window.localStorage.getItem(THEME_STORAGE_KEY) ??
      window.localStorage.getItem(LEGACY_THEME_STORAGE_KEY) ??
      ""
    );
  } catch {
    return "";
  }
}

/**
 * Apply the persisted preference before React paints, so an enabled skin
 * does not flash the default theme on first load. Q6/Q6-L is the source of
 * truth; the old standalone preference is only a fallback when no theme is
 * known yet. Safe to call repeatedly.
 */
export function bootstrapMangaSkin(pathname?: string): void {
  if (typeof window === "undefined") return;
  const path =
    pathname ??
    (typeof window.location !== "undefined" ? window.location.pathname : "/");
  const theme = savedThemeName();
  const wanted = theme ? isMangaThemeName(theme) : readMangaPreference();
  // Align the standalone preference with the theme before React reads it, so
  // the gate cannot flash a legacy ON state on a non-Q6 route for one frame.
  setMangaEnabled(wanted);
  const eligible = wanted && mangaRouteEligible(path);
  applyMangaAttribute(eligible);
  // The approved pack is verified by the gate on first load. Cache that fact
  // so a normal refresh paints the approved shell before React mounts instead
  // of flashing the pre-approved skeleton for one network round-trip.
  try {
    if (eligible && window.localStorage.getItem("qubit.manga.approved.ready.v2") === "1") {
      document.documentElement.dataset.qmApprovedAssets = "ready";
    }
  } catch {
    /* storage blocked */
  }
}

/** Convenience for tests and external callers. */
export function isMangaEnabled(pathname: string): boolean {
  return currentMangaEnabled() && mangaRouteEligible(pathname);
}
