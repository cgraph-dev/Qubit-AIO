// @vitest-environment jsdom
/*
 * P6-05 — unit contract for the reusable accessibility helpers.
 *
 * These run in jsdom, so focus, live regions and key handling are real DOM
 * behaviour, not a mock of it. They do not replace a screen-reader review;
 * they prove the pieces such a review depends on.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  announce,
  captureFocus,
  clampIndex,
  focusFirst,
  focusableElements,
  installEscapeToShell,
  meetsTargetSize,
  nextRovingIndex,
  officeAnnouncement,
  onMediaQueryChange,
  onReducedMotionChange,
  prefersHighContrast,
  prefersReducedMotion,
  restoreFocus,
  rovingTabIndexes,
  statusAria,
  trapFocus,
  undersizedTargets,
} from "./a11y";

function makeButton(label: string, attributes: Record<string, string> = {}) {
  const button = document.createElement("button");
  button.textContent = label;
  for (const [key, value] of Object.entries(attributes)) {
    button.setAttribute(key, value);
  }
  document.body.appendChild(button);
  return button;
}

function mediaQueryList(matches: boolean) {
  const listeners = new Set<(event: MediaQueryListEvent) => void>();
  return {
    matches,
    media: "",
    addEventListener: (_: string, fn: (event: MediaQueryListEvent) => void) => {
      listeners.add(fn);
    },
    removeEventListener: (_: string, fn: (event: MediaQueryListEvent) => void) => {
      listeners.delete(fn);
    },
    dispatch(matchesNext: boolean) {
      this.matches = matchesNext;
      for (const fn of listeners) {
        fn({ matches: matchesNext } as MediaQueryListEvent);
      }
    },
  };
}

beforeEach(() => {
  document.body.innerHTML = "";
});

afterEach(() => {
  document.body.innerHTML = "";
  vi.unstubAllGlobals();
});

describe("focus helpers", () => {
  it("lists tabbable elements in DOM order and skips disabled/hidden", () => {
    const first = makeButton("one");
    const second = makeButton("two");
    const disabled = makeButton("disabled", { disabled: "disabled" });
    const hidden = makeButton("hidden", { "aria-hidden": "true" });
    const root = document.createElement("div");
    root.append(first, second);
    document.body.append(root, disabled, hidden);
    expect(focusableElements(document.body)).toEqual([first, second]);
  });

  it("focusFirst prefers the first tabbable descendant", () => {
    const root = document.createElement("div");
    root.tabIndex = -1;
    const button = document.createElement("button");
    button.textContent = "inside";
    root.appendChild(button);
    document.body.appendChild(root);
    expect(focusFirst(root)).toBe(button);
    expect(document.activeElement).toBe(button);
  });

  it("captures and restores focus only while the element is attached", () => {
    const opener = makeButton("opener");
    opener.focus();
    const captured = captureFocus(document);
    expect(captured.element).toBe(opener);
    const other = makeButton("other");
    other.focus();
    expect(captured.restore()).toBe(true);
    expect(document.activeElement).toBe(opener);

    opener.remove();
    other.focus();
    expect(restoreFocus(opener, document)).toBe(false);
    expect(document.activeElement).toBe(other);
  });
});

describe("focus trap", () => {
  it("cycles Tab and Shift+Tab inside the container and restores on release", () => {
    const opener = makeButton("opener");
    opener.focus();
    const container = document.createElement("div");
    const first = document.createElement("button");
    first.textContent = "first";
    const middle = document.createElement("button");
    middle.textContent = "middle";
    const last = document.createElement("button");
    last.textContent = "last";
    container.append(first, middle, last);
    document.body.appendChild(container);

    const trap = trapFocus(container, { doc: document });
    last.focus();
    const forward = new KeyboardEvent("keydown", {
      key: "Tab", bubbles: true, cancelable: true,
    });
    document.dispatchEvent(forward);
    expect(forward.defaultPrevented).toBe(true);
    expect(document.activeElement).toBe(first);

    first.focus();
    const backward = new KeyboardEvent("keydown", {
      key: "Tab", shiftKey: true, bubbles: true, cancelable: true,
    });
    document.dispatchEvent(backward);
    expect(document.activeElement).toBe(last);

    trap.release();
    expect(document.activeElement).toBe(opener);
  });

  it("releases on Escape without trapping a later Tab", () => {
    const container = document.createElement("div");
    const button = document.createElement("button");
    button.textContent = "only";
    container.appendChild(button);
    document.body.appendChild(container);
    const onEscape = vi.fn();
    trapFocus(container, { doc: document, onEscape, restoreOnRelease: false });
    document.dispatchEvent(new KeyboardEvent("keydown", {
      key: "Escape", bubbles: true,
    }));
    expect(onEscape).toHaveBeenCalledTimes(1);
  });
});

describe("roving tabindex", () => {
  it("clamps indices and marks exactly one tab stop", () => {
    expect(clampIndex(-3, 3)).toBe(0);
    expect(clampIndex(9, 3)).toBe(2);
    expect(clampIndex(1, 0)).toBe(-1);
    expect(rovingTabIndexes(1, 3)).toEqual([-1, 0, -1]);
    expect(rovingTabIndexes(99, 3)).toEqual([-1, -1, 0]);
  });

  it("moves with arrows and Home/End, honouring RTL", () => {
    expect(nextRovingIndex("ArrowDown", 0, 3)).toBe(1);
    expect(nextRovingIndex("ArrowUp", 0, 3)).toBe(2);
    expect(nextRovingIndex("Home", 2, 3)).toBe(0);
    expect(nextRovingIndex("End", 0, 3)).toBe(2);
    expect(nextRovingIndex("ArrowRight", 0, 3, { rtl: true })).toBe(2);
    expect(nextRovingIndex("ArrowLeft", 0, 3, { rtl: true })).toBe(1);
    expect(nextRovingIndex("Enter", 0, 3)).toBeNull();
  });
});

describe("status semantics are never colour-only", () => {
  it("maps every status to a role, politeness, label and marker", () => {
    for (const kind of [
      "loading", "ready", "working", "idle", "error", "stale", "unknown",
      "approval", "stopped",
    ] as const) {
      const aria = statusAria(kind);
      expect(aria.label.length).toBeGreaterThan(0);
      expect(aria.marker.length).toBeGreaterThan(0);
      expect(["status", "alert", "note"]).toContain(aria.role);
      expect(["off", "polite", "assertive"]).toContain(aria.ariaLive);
    }
    expect(statusAria("error").role).toBe("alert");
    expect(statusAria("error").ariaLive).toBe("assertive");
  });

  it("announces loading, connection, dock and error states as text", () => {
    expect(officeAnnouncement({ loaded: false })).toMatch(/Loading/);
    expect(officeAnnouncement({ loaded: true })).toMatch(/ready/i);
    expect(officeAnnouncement({
      state: "connected", rooms: 4, agents: 10,
    })).toContain("4 rooms");
    expect(officeAnnouncement({ state: "error", error: "HTTP 401" }))
      .toContain("unavailable");
    expect(officeAnnouncement({ dockLabel: "Agents", loaded: true }))
      .toContain("Opened Agents");
    expect(officeAnnouncement({
      failed: "The office app failed to load.",
    })).toContain("Office error");
  });

  it("writes to a shared polite live region", () => {
    const region = announce("Office connected.");
    expect(region?.getAttribute("role")).toBe("status");
    expect(region?.getAttribute("aria-live")).toBe("polite");
    expect(region?.textContent).toBe("Office connected.");
    const assertive = announce("Failed.", { politeness: "assertive" });
    expect(assertive?.getAttribute("aria-live")).toBe("assertive");
    expect(document.querySelectorAll("#office-game-announcer-live")).toHaveLength(1);
  });
});

describe("target size (WCAG 2.2 SC 2.5.8)", () => {
  it("requires at least 24x24 CSS pixels", () => {
    expect(meetsTargetSize(24, 24)).toBe(true);
    expect(meetsTargetSize(23, 40)).toBe(false);
    expect(meetsTargetSize(40, 23)).toBe(false);
    expect(undersizedTargets([
      { id: "ok", width: 24, height: 24 },
      { id: "small", width: 12, height: 24 },
    ]).map((box) => box.id)).toEqual(["small"]);
  });
});

describe("preference detection", () => {
  it("reads prefers-reduced-motion and prefers-contrast from matchMedia", () => {
    const lists: Record<string, ReturnType<typeof mediaQueryList>> = {
      "(prefers-reduced-motion: reduce)": mediaQueryList(true),
      "(prefers-contrast: more)": mediaQueryList(false),
      "(forced-colors: active)": mediaQueryList(false),
    };
    vi.stubGlobal("matchMedia", (query: string) => lists[query]);
    expect(prefersReducedMotion()).toBe(true);
    expect(prefersHighContrast()).toBe(false);
  });

  it("notifies subscribers when the preference changes and unsubscribes", () => {
    const list = mediaQueryList(false);
    vi.stubGlobal("matchMedia", () => list);
    const seen: boolean[] = [];
    const off = onReducedMotionChange((next) => seen.push(next));
    list.dispatch(true);
    list.dispatch(false);
    off();
    list.dispatch(true);
    expect(seen).toEqual([true, false]);
  });

  it("falls back safely when matchMedia is unavailable", () => {
    vi.stubGlobal("matchMedia", undefined);
    expect(prefersReducedMotion()).toBe(false);
    expect(() => onMediaQueryChange("(prefers-contrast: more)", () => {})).not.toThrow();
  });
});

describe("escape hatch from the office canvas", () => {
  it("calls back on Escape inside the frame document and removes the listener", () => {
    const listeners = new Set<(event: KeyboardEvent) => void>();
    const fakeDocument = {
      addEventListener: (_: string, fn: (event: KeyboardEvent) => void) => {
        listeners.add(fn);
      },
      removeEventListener: (_: string, fn: (event: KeyboardEvent) => void) => {
        listeners.delete(fn);
      },
    };
    const frameWindow = { document: fakeDocument } as unknown as Window;
    const onEscape = vi.fn();
    const dispose = installEscapeToShell(frameWindow, onEscape);
    for (const listener of listeners) {
      listener({ key: "Tab" } as KeyboardEvent);
    }
    expect(onEscape).not.toHaveBeenCalled();
    for (const listener of listeners) {
      listener({ key: "Escape" } as KeyboardEvent);
    }
    expect(onEscape).toHaveBeenCalledTimes(1);
    dispose();
    expect(listeners.size).toBe(0);
  });

  it("is a harmless no-op without a reachable document", () => {
    expect(() => installEscapeToShell(null, () => {})()).not.toThrow();
    expect(() => installEscapeToShell(
      { document: undefined } as unknown as Window, () => {})()).not.toThrow();
  });
});
