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
    expect(toolbar.querySelector('[data-testid="office-command-center-toggle"]'))
      .not.toBeNull();
    expect(toolbar.querySelector('[data-testid="office-timeline-toggle"]'))
      .not.toBeNull();

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

  it("moves focus into a sheet and restores the opener when it closes", async () => {
    stubFetch();
    await act(async () => { render({}); });
    const toggle = document.querySelector(
      '[data-testid="office-command-center-toggle"]') as HTMLButtonElement;
    expect(toggle).not.toBeNull();

    toggle.focus();
    expect(document.activeElement).toBe(toggle);
    await act(async () => { toggle.click(); });
    const dock = document.getElementById("office-game-command-center-dock");
    expect(dock).not.toBeNull();
    // Focus landed inside the sheet (its own close button from the panel).
    expect(dock?.contains(document.activeElement)).toBe(true);

    await act(async () => { toggle.click(); });
    expect(document.getElementById("office-game-command-center-dock")).toBeNull();
    // The opener is still attached, so focus returns to it exactly.
    expect(document.activeElement).toBe(toggle);
  });

  it("opens the singular campus world instead of the old per-floor cards", async () => {
    stubFetch();
    await act(async () => { render({}); });
    const toggle = document.querySelector(
      '[data-testid="office-campus-toggle"]') as HTMLButtonElement;
    expect(toggle).not.toBeNull();
    await act(async () => { toggle.click(); });
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
