// @vitest-environment jsdom
/*
 * P6-05 — the shell's accessibility wiring, rendered with the real React the
 * dashboard injects (not the unit harness). It proves the structural contract
 * a browser would see: skip links first, the main/toolbar landmarks, the live
 * announcer, system-preference attributes, focus restoration and the
 * Escape-to-shell escape hatch from the same-origin office frame.
 *
 * It is NOT a screen-reader or axe run; those are recorded as not performed.
 */
import React, { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createOfficeApp } from "./OfficeAppShell";

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean })
  .IS_REACT_ACT_ENVIRONMENT = true;

const hooks = {
  useState: React.useState,
  useEffect: React.useEffect,
  useRef: React.useRef,
};

let container: HTMLDivElement;
let root: ReturnType<typeof createRoot>;

function render(props: Record<string, unknown> = {}) {
  const App = createOfficeApp(React as never, hooks as never);
  root.render(React.createElement(App, props));
}

function stubFetch() {
  const response = {
    ok: false,
    status: 500,
    statusText: "refused",
    headers: { get: () => "application/json" },
    json: async () => ({ ok: false, code: "TEST_REFUSAL", detail: "test" }),
    text: async () => "",
    clone() { return this; },
  };
  const fetchMock = vi.fn(async () => response);
  vi.stubGlobal("fetch", fetchMock);
  if (typeof window !== "undefined") {
    (window as unknown as { fetch: unknown }).fetch = fetchMock;
  }
  return fetchMock;
}

function media(matches: Record<string, boolean>) {
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: Boolean(matches[query]),
    media: query,
    addEventListener: () => {},
    removeEventListener: () => {},
  }));
}

beforeEach(() => {
  document.body.innerHTML = "";
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => { root.unmount(); });
  vi.unstubAllGlobals();
  document.body.innerHTML = "";
});

describe("office shell accessibility landmarks", () => {
  it("puts skip links first, wraps the canvas in main and labels a toolbar", async () => {
    stubFetch();
    await act(async () => { render({}); });

    const shell = document.querySelector('[data-testid="office-app"]') as HTMLElement;
    expect(shell).not.toBeNull();
    const first = shell.firstElementChild as HTMLAnchorElement;
    const second = shell.children[1] as HTMLAnchorElement;
    expect(first.getAttribute("data-testid")).toBe("office-app-skip-link");
    expect(first.getAttribute("href")).toBe("#office-game-main");
    expect(second.getAttribute("data-testid")).toBe("office-app-skip-controls");
    expect(second.getAttribute("href")).toBe("#office-game-controls");

    const main = document.getElementById("office-game-main") as HTMLElement;
    expect(main.tagName).toBe("MAIN");
    expect(main.getAttribute("tabindex")).toBe("-1");
    expect(main.querySelector("iframe")).not.toBeNull();

    const toolbar = document.getElementById("office-game-controls") as HTMLElement;
    expect(toolbar.getAttribute("role")).toBe("toolbar");
    expect(toolbar.getAttribute("aria-label")).toBe("Office panels");
    // The panel controls live in the office's own Panels icon group, not in a
    // second bottom text toolbar; the landmark stays as the escape target.
    expect(toolbar.querySelector('[data-testid="office-command-center-toggle"]'))
      .toBeNull();
    expect(toolbar.querySelector('[data-testid="office-timeline-toggle"]'))
      .toBeNull();
    expect(toolbar.textContent).toContain("top right");

    // The whole experience is not an ARIA application (that would trap SRs).
    expect(shell.getAttribute("role")).not.toBe("application");
    // The frame keeps its accessible name and description.
    const frame = main.querySelector("iframe") as HTMLIFrameElement;
    expect(frame.getAttribute("title")).toBe("Qubit Office");
    expect(frame.getAttribute("aria-describedby")).toBe("office-app-description");
    const description = document.getElementById("office-app-description");
    expect(description?.textContent).toContain("muting audio never hides it");
  });

  it("announces loading, connected and error states as text", async () => {
    stubFetch();
    await act(async () => { render({}); });
    let announcer = document.querySelector('[data-testid="office-app-announcer"]');
    expect(announcer?.getAttribute("role")).toBe("status");
    expect(announcer?.getAttribute("aria-live")).toBe("polite");
    expect(announcer?.textContent).toMatch(/Loading the office/);

    await act(async () => {
      render({ officeState: "connected", officeRooms: 4, officeAgents: 10 });
    });
    announcer = document.querySelector('[data-testid="office-app-announcer"]');
    expect(announcer?.textContent).toContain("4 rooms");
    expect(announcer?.textContent).toContain("10 agents");
    expect(document.querySelector('[data-testid="office-app-connection"]')
      ?.getAttribute("role")).toBe("status");

    await act(async () => {
      render({ officeState: "error", officeError: "HTTP 401" });
    });
    announcer = document.querySelector('[data-testid="office-app-announcer"]');
    expect(announcer?.textContent).toContain("unavailable");
    expect(announcer?.textContent).toContain("HTTP 401");
    expect(document.querySelector('[data-testid="office-app-connection"]')
      ?.getAttribute("role")).toBe("alert");
  });

  it("mirrors reduced-motion and high-contrast preferences onto the root", async () => {
    stubFetch();
    media({
      "(prefers-reduced-motion: reduce)": true,
      "(prefers-contrast: more)": true,
    });
    await act(async () => { render({}); });
    const shell = document.querySelector('[data-testid="office-app"]') as HTMLElement;
    expect(shell.getAttribute("data-office-reduced-motion")).toBe("1");
    expect(shell.getAttribute("data-office-contrast")).toBe("high");
  });

  it("opens the command center from the keyboard and restores focus on close", async () => {
    stubFetch();
    await act(async () => { render({}); });
    // No duplicate text toggle: the control is the injected Panels icon.
    expect(document.querySelector(
      '[data-testid="office-command-center-toggle"]')).toBeNull();

    await act(async () => {
      window.dispatchEvent(new KeyboardEvent("keydown", {
        key: "k", ctrlKey: true, bubbles: true,
      }));
    });
    const dock = document.getElementById("office-game-command-center-dock");
    expect(dock).not.toBeNull();
    // Focus landed inside the sheet (its search field or close button).
    expect(dock?.contains(document.activeElement)).toBe(true);

    const close = document.querySelector(
      '[data-testid="command-center-close"]') as HTMLButtonElement;
    await act(async () => { close.click(); });
    expect(document.getElementById("office-game-command-center-dock")).toBeNull();
    // The opener (a keyboard shortcut, not a button) has no element to restore
    // to, so focus returns to the workspace landmark instead of the body.
    expect(document.activeElement?.id).toBe("office-game-main");
  });

  it("opens the singular campus world instead of the old per-floor cards", async () => {
    stubFetch();
    await act(async () => { render({}); });
    // The visible control is the injected Panels icon. The host seam is the
    // same programmatic toggle voice/host code uses; no duplicate text button.
    await act(async () => {
      window.dispatchEvent(new CustomEvent("qubit-office:toggle", {
        detail: "campus",
      }));
    });
    expect(document.querySelector('[data-testid="office-campus-world"]'))
      .not.toBeNull();
    expect(document.querySelector('[data-testid="office-floor-campus"]'))
      .toBeNull();
    expect(document.querySelector('[data-testid="office-floor-card-1"]'))
      .toBeNull();
    expect(document.querySelector('[data-testid="campus-world-viewport"]'))
      .not.toBeNull();
  });

  it("offers Escape inside the office frame as an escape from the canvas", async () => {
    stubFetch();
    await act(async () => { render({}); });
    const frame = document.querySelector(
      '[data-testid="office-app-frame"]') as HTMLIFrameElement;
    expect(frame).not.toBeNull();
    // The shell installs the escape handler when the frame loads.
    await act(async () => {
      frame.dispatchEvent(new Event("load"));
    });
    const frameDocument = frame.contentDocument;
    expect(frameDocument).not.toBeNull();
    await act(async () => {
      frameDocument?.dispatchEvent(new KeyboardEvent("keydown", {
        key: "Escape", bubbles: true,
      }));
    });
    expect(document.activeElement?.id).toBe("office-game-controls");
  });
});
