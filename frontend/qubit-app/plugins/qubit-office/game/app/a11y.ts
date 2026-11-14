/* P6-05 — reusable accessibility helpers for the Qubit Office shell.
 *
 * Framework-free on purpose: the shell receives the host's React and has no
 * `import from "react"` (a second runtime would split hooks), so these are
 * plain DOM/TS utilities any shell or panel can call. They are deliberately
 * small and deterministic so they can be unit-tested in jsdom without a
 * browser, a screen reader or the network.
 *
 * Nothing here claims a screen-reader review. It makes the pieces a review
 * depends on explicit and testable: focus capture/restore, a real focus trap,
 * roving tabindex math, live-region announcements, reduced-motion / contrast
 * detection, status semantics that never rely on colour alone, and the WCAG
 * 2.5.8 target-size check.
 */

export const FOCUSABLE_SELECTOR = [
  "a[href]",
  "button:not([disabled])",
  "input:not([disabled]):not([type='hidden'])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  "[tabindex]:not([tabindex='-1'])",
  "iframe",
].join(",");

/** WCAG 2.2 SC 2.5.8 — minimum target size in CSS pixels. */
export const MIN_TARGET_PX = 24;

export type StatusKind =
  | "loading"
  | "ready"
  | "working"
  | "idle"
  | "error"
  | "stale"
  | "unknown"
  | "approval"
  | "stopped";

export interface StatusAria {
  /** Landmark role used by the live element. */
  role: "status" | "alert" | "note";
  /** Live-region politeness. */
  ariaLive: "off" | "polite" | "assertive";
  /** Spoken/visible label; always a word, never a bare colour. */
  label: string;
  /** Non-colour glyph cue, paired with `label` (not a replacement for it). */
  marker: string;
}

/**
 * Status semantics that survive greyscale, high contrast and muted audio.
 * A caller must render `marker` AND `label`; the colour may change as well but
 * can never be the only signal.
 */
export function statusAria(kind: StatusKind): StatusAria {
  switch (kind) {
    case "loading":
      return { role: "status", ariaLive: "polite", label: "Loading", marker: "\u2026" };
    case "ready":
      return { role: "status", ariaLive: "polite", label: "Ready", marker: "\u2713" };
    case "working":
      return { role: "status", ariaLive: "polite", label: "Working", marker: "\u25B6" };
    case "idle":
      return { role: "status", ariaLive: "polite", label: "Idle", marker: "\u2022" };
    case "error":
      return { role: "alert", ariaLive: "assertive", label: "Error", marker: "!" };
    case "stale":
      return { role: "status", ariaLive: "polite", label: "Stale", marker: "\u25F7" };
    case "unknown":
      return { role: "status", ariaLive: "polite", label: "Unknown", marker: "?" };
    case "approval":
      return { role: "status", ariaLive: "polite", label: "Needs you", marker: "\u25B2" };
    case "stopped":
      return { role: "status", ariaLive: "polite", label: "Stopped", marker: "\u25A0" };
    default:
      return { role: "status", ariaLive: "polite", label: "Status", marker: "\u2022" };
  }
}

/** The live announcement for the office's current load/connection state. */
export function officeAnnouncement(input: {
  loaded?: boolean;
  failed?: string | null;
  state?: "connecting" | "connected" | "stale" | "error";
  rooms?: number;
  agents?: number;
  error?: string;
  dockLabel?: string | null;
}): string {
  const prefix = input.dockLabel ? `Opened ${input.dockLabel}. ` : "";
  const failed = typeof input.failed === "string" ? input.failed.trim() : "";
  if (failed) return `${prefix}Office error: ${failed}`;
  const state = input.state ?? "connecting";
  const detail = typeof input.error === "string" ? input.error.trim() : "";
  if (state === "error") {
    return `${prefix}Office backend unavailable${detail ? `: ${detail}` : ""}.`;
  }
  if (state === "stale") {
    return `${prefix}Office showing its last known state${detail ? `: ${detail}` : ""}.`;
  }
  if (state === "connected") {
    const rooms = input.rooms ?? 0;
    const agents = input.agents ?? 0;
    if (rooms === 0 && agents === 0) return `${prefix}Office connected, no rooms yet.`;
    return `${prefix}Office connected, ${rooms} room${rooms === 1 ? "" : "s"}, `
      + `${agents} agent${agents === 1 ? "" : "s"}.`;
  }
  if (input.loaded) return `${prefix}Office ready.`;
  return `${prefix}Loading the office.`;
}

function isVisible(element: Element): boolean {
  if (!element || typeof element !== "object") return false;
  if (element.getAttribute?.("aria-hidden") === "true") return false;
  if (element.hasAttribute?.("hidden")) return false;
  // jsdom has no layout engine, so visibility is judged from explicit author
  // state only: the `hidden` attribute, aria-hidden, and inline display /
  // visibility. Anything else is treated as rendered.
  const style = (element as HTMLElement).style;
  if (style && (style.display === "none" || style.visibility === "hidden")) {
    return false;
  }
  return true;
}

/** Tabbable descendants in DOM order. Never throws on a missing root. */
export function focusableElements(root: ParentNode | null | undefined): HTMLElement[] {
  if (!root || typeof (root as ParentNode).querySelectorAll !== "function") return [];
  const nodes = Array.from(
    (root as ParentNode).querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR));
  return nodes.filter(isVisible);
}

/** Focus the first tabbable descendant, or the root itself as a fallback. */
export function focusFirst(root: HTMLElement | null | undefined): HTMLElement | null {
  if (!root) return null;
  const first = focusableElements(root)[0];
  const target = first ?? root;
  (target as HTMLElement).focus?.();
  return target;
}

export interface CapturedFocus {
  element: HTMLElement | null;
  restore: () => boolean;
}

/**
 * Capture the currently focused element and return a restore closure.
 * Restoring only succeeds when the element is still attached, so a removed
 * dock opener cannot steal focus back from a later view.
 */
export function captureFocus(doc?: Document | null): CapturedFocus {
  const documentRef = doc ?? (typeof document === "undefined" ? null : document);
  const active = (documentRef?.activeElement as HTMLElement | null) ?? null;
  const element = active && active !== documentRef?.body ? active : null;
  return {
    element,
    restore: () => restoreFocus(element, documentRef),
  };
}

/** Restore focus to a previously captured element when it is still attached. */
export function restoreFocus(
  element: HTMLElement | null | undefined,
  doc?: Document | null,
): boolean {
  const documentRef = doc ?? (typeof document === "undefined" ? null : document);
  if (!element || !documentRef || !documentRef.contains(element)) return false;
  element.focus?.();
  return documentRef.activeElement === element;
}

export interface FocusTrapOptions {
  /** Called on Escape; the trap then releases itself. */
  onEscape?: () => void;
  /** Restore the element that had focus before the trap armed. */
  restoreOnRelease?: boolean;
  doc?: Document;
}

export interface FocusTrap {
  release: () => void;
  /** Focus the first tabbable element (or the container). */
  focus: () => HTMLElement | null;
}

/**
 * A real Tab/Shift+Tab focus trap for a modal surface.
 *
 * Side-sheet docks are deliberately NOT traps (the game stays usable beside
 * them); only modal dialogs use this. The listener is installed on the
 * document in the capture phase so a descendant that stops propagation cannot
 * defeat it.
 */
export function trapFocus(
  container: HTMLElement | null | undefined,
  options: FocusTrapOptions = {},
): FocusTrap {
  const doc = options.doc ?? (typeof document === "undefined" ? null : document);
  if (!container || !doc) {
    return { release: () => {}, focus: () => null };
  }
  const previouslyFocused = doc.activeElement as HTMLElement | null;
  let active = true;

  const onKeyDown = (event: KeyboardEvent) => {
    if (event.key === "Escape") {
      options.onEscape?.();
      if (!options.onEscape) return;
    }
    if (event.key !== "Tab") return;
    const items = focusableElements(container);
    if (items.length === 0) {
      event.preventDefault();
      container.focus?.();
      return;
    }
    const first = items[0];
    const last = items[items.length - 1];
    const current = doc.activeElement;
    if (event.shiftKey && (current === first || current === container || current === doc.body)) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && current === last) {
      event.preventDefault();
      first.focus();
    }
  };

  doc.addEventListener("keydown", onKeyDown, true);
  return {
    focus: () => focusFirst(container),
    release: () => {
      if (!active) return;
      active = false;
      doc.removeEventListener("keydown", onKeyDown, true);
      if (options.restoreOnRelease !== false) {
        restoreFocus(previouslyFocused, doc);
      }
    },
  };
}

/** Clamp `index` into `[0, count)`; `-1` when there is nothing to select. */
export function clampIndex(index: number, count: number): number {
  if (!Number.isFinite(index) || count <= 0) return count > 0 ? 0 : -1;
  if (index < 0) return 0;
  if (index >= count) return count - 1;
  return Math.floor(index);
}

/**
 * Roving-tabindex target for a key press: Arrow/Home/End move a single tab
 * stop through a list without adding every row to the tab order. Returns the
 * next index, or `null` when the key is not a roving key.
 */
export function nextRovingIndex(
  key: string,
  index: number,
  count: number,
  options: { rtl?: boolean } = {},
): number | null {
  if (count <= 0) return -1;
  const current = clampIndex(index, count);
  switch (key) {
    case "ArrowDown":
    case "ArrowRight":
      // RTL flips the horizontal direction but never the vertical one.
      if (key === "ArrowRight" && options.rtl) {
        return (current - 1 + count) % count;
      }
      return (current + 1) % count;
    case "ArrowUp":
    case "ArrowLeft":
      if (key === "ArrowLeft" && options.rtl) {
        return (current + 1) % count;
      }
      return (current - 1 + count) % count;
    case "Home":
      return 0;
    case "End":
      return count - 1;
    default:
      return null;
  }
}

/** Tabindex for every row; exactly one row is in the tab order. */
export function rovingTabIndexes(activeIndex: number, count: number): number[] {
  const active = clampIndex(activeIndex, count);
  return Array.from({ length: Math.max(0, count) }, (_, index) =>
    index === active ? 0 : -1);
}

/** WCAG 2.2 SC 2.5.8: a target meets the minimum in both dimensions. */
export function meetsTargetSize(
  width: number,
  height: number,
  minimum: number = MIN_TARGET_PX,
): boolean {
  return Number.isFinite(width) && Number.isFinite(height)
    && width >= minimum && height >= minimum;
}

export interface TargetBox {
  id?: string;
  width: number;
  height: number;
}

/** The boxes that fail SC 2.5.8 — used by the structural audit and tests. */
export function undersizedTargets(
  boxes: TargetBox[],
  minimum: number = MIN_TARGET_PX,
): TargetBox[] {
  return boxes.filter((box) => !meetsTargetSize(box.width, box.height, minimum));
}

function matchMediaList(query: string, win?: Window | null): MediaQueryList | null {
  const windowRef = win ?? (typeof window === "undefined" ? null : window);
  if (!windowRef || typeof windowRef.matchMedia !== "function") return null;
  try {
    return windowRef.matchMedia(query);
  } catch {
    return null;
  }
}

/** The OS/browser preference; false when it cannot be read (jsdom, tests). */
export function prefersReducedMotion(win?: Window | null): boolean {
  return Boolean(matchMediaList("(prefers-reduced-motion: reduce)", win)?.matches);
}

/** `prefers-contrast: more` (or the older `forced-colors: active`). */
export function prefersHighContrast(win?: Window | null): boolean {
  return Boolean(
    matchMediaList("(prefers-contrast: more)", win)?.matches
    || matchMediaList("(forced-colors: active)", win)?.matches,
  );
}

/** Subscribe to a media-query change; returns an unsubscribe function. */
export function onMediaQueryChange(
  query: string,
  listener: (matches: boolean) => void,
  win?: Window | null,
): () => void {
  const list = matchMediaList(query, win);
  if (!list) return () => {};
  const handler = (event: MediaQueryListEvent) => listener(Boolean(event.matches));
  if (typeof list.addEventListener === "function") {
    list.addEventListener("change", handler);
    return () => list.removeEventListener("change", handler);
  }
  // Safari < 14 has only the deprecated API.
  const legacy = list as unknown as {
    addListener?: (fn: (e: MediaQueryListEvent) => void) => void;
    removeListener?: (fn: (e: MediaQueryListEvent) => void) => void;
  };
  legacy.addListener?.(handler);
  return () => legacy.removeListener?.(handler);
}

export function onReducedMotionChange(
  listener: (reduced: boolean) => void,
  win?: Window | null,
): () => void {
  return onMediaQueryChange("(prefers-reduced-motion: reduce)", listener, win);
}

/**
 * Install an Escape-to-shell escape hatch inside the same-origin office frame.
 *
 * The vendored client owns its document and can swallow Tab, so the shell
 * provides a guaranteed way out: Escape returns focus to the shell controls.
 * No-op when the document is not reachable (tests, sandboxed frames).
 */
export function installEscapeToShell(
  frameWindow: Window | null | undefined,
  onEscape: () => void,
): () => void {
  const doc = frameWindow?.document;
  if (!doc || typeof doc.addEventListener !== "function") return () => {};
  const handler = (event: KeyboardEvent) => {
    if (event.key !== "Escape") return;
    onEscape();
  };
  doc.addEventListener("keydown", handler, true);
  return () => doc.removeEventListener("keydown", handler, true);
}

const ANNOUNCER_ID = "office-game-announcer-live";

/**
 * Post a message to a shared polite live region. The shell renders its own
 * region; panels that need an out-of-band announcement can use this helper.
 */
export function announce(
  message: string,
  options: { politeness?: "polite" | "assertive"; doc?: Document } = {},
): HTMLElement | null {
  const doc = options.doc ?? (typeof document === "undefined" ? null : document);
  if (!doc) return null;
  let region = doc.getElementById(ANNOUNCER_ID);
  if (!region) {
    region = doc.createElement("div");
    region.id = ANNOUNCER_ID;
    region.className = "office-game__sr-only";
    region.setAttribute("aria-atomic", "true");
    doc.body?.appendChild(region);
  }
  region.setAttribute("role", "status");
  region.setAttribute("aria-live", options.politeness ?? "polite");
  region.textContent = message;
  return region;
}
