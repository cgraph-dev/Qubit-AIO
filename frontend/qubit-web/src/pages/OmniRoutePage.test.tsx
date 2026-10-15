// @vitest-environment jsdom
/**
 * /omniroute — gateway status, wiring rows, and dashboard links.
 * `fetch` is stubbed (no backend); the health probe resolves through the
 * stubbed response.
 */
import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import OmniRoutePage from "./OmniRoutePage";

let container: HTMLDivElement;
let root: Root;
(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

function okHealth() {
  return {
    ok: true,
    status: 200,
    json: async () => ({ status: "ok" }),
    text: async () => '{"status":"ok"}',
  };
}

const fetchUrls: string[] = [];

function stubFetch() {
  fetchUrls.length = 0;
  vi.stubGlobal("fetch", async (url: string) => {
    fetchUrls.push(String(url));
    return okHealth();
  });
}

function renderPage(node: ReactNode) {
  act(() => {
    root.render(node);
  });
}

function text() {
  return container.textContent ?? "";
}

beforeEach(() => {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  stubFetch();
});

afterEach(() => {
  act(() => {
    root.unmount();
  });
  container.remove();
  vi.unstubAllGlobals();
});

describe("OmniRoutePage", () => {
  it("renders the gateway console with dashboard links", async () => {
    renderPage(<OmniRoutePage />);
    expect(text()).toContain("OmniRoute Gateway");
    const dashboard = container.querySelector(
      'a[href="http://127.0.0.1:20128"]',
    );
    expect(dashboard).not.toBeNull();
    expect(dashboard?.getAttribute("target")).toBe("_blank");
    // Wiring rows name the Qubit profiles, never a secret value.
    expect(text()).toContain("omniroute-auto");
    expect(text()).not.toContain("sk-");
    // The health probe hits the local gateway.
    await act(async () => {});
    expect(fetchUrls).toContain("http://127.0.0.1:20128/api/health");
    expect(text()).toContain("Running");
  });

  it("reports unreachable when the probe fails", async () => {
    vi.stubGlobal("fetch", async () => {
      throw new Error("refused");
    });
    renderPage(<OmniRoutePage />);
    await act(async () => {});
    expect(text()).toContain("Unreachable");
  });
});
