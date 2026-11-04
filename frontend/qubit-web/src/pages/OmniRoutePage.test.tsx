// @vitest-environment jsdom
/**
 * /omniroute — gateway status, wiring rows, and dashboard links.
 *
 * The dashboard runs under `connect-src 'self'`, so it must NOT fire a
 * cross-origin probe at the gateway (the browser blocks it and the page used
 * to claim "Unreachable" even while the gateway was healthy). The page points
 * at the dashboard instead, and the URLs it names stay secret-free.
 */
import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import OmniRoutePage from "./OmniRoutePage";

let container: HTMLDivElement;
let root: Root;
(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

const fetchUrls: string[] = [];

function stubFetch() {
  fetchUrls.length = 0;
  vi.stubGlobal("fetch", async (url: string) => {
    fetchUrls.push(String(url));
    return {
      ok: true,
      status: 200,
      json: async () => ({ status: "ok" }),
      text: async () => '{"status":"ok"}',
    };
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
    // The CSP forbids a cross-origin probe; the page must not attempt one.
    await act(async () => {});
    expect(fetchUrls).toEqual([]);
    expect(text()).toContain("cannot query it directly");
  });
});
