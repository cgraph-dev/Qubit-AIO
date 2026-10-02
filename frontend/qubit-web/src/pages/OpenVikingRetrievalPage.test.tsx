// @vitest-environment jsdom
/**
 * Open Viking retrieval page: search form renders, mocked-live hits
 * render, and a 404ing bridge row renders honestly disabled with
 * reason (no unconfigured state — the local engine is always present).
 */
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const vikingMocks = vi.hoisted(() => ({
  searchVikingRetrieval: vi.fn(),
}));

vi.mock("@/lib/ocFamilyVikingApi", () => ({
  searchVikingRetrieval: vikingMocks.searchVikingRetrieval,
  isBridgeRowMissing: (e: unknown) =>
    e instanceof Error && (e.message.includes("404") || e.message.toLowerCase().includes("not found")),
  normalizeSearchHits: (p: unknown) => (p as { hits: unknown[] }).hits ?? [],
}));

import OpenVikingRetrievalPage from "@/pages/OpenVikingRetrievalPage";

let container: HTMLDivElement;
let root: Root;
(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

async function waitFor(cond: () => boolean, timeoutMs = 5000) {
  const start = Date.now();
  while (!cond()) {
    if (Date.now() - start > timeoutMs) throw new Error("waitFor: condition never became true");
    await act(async () => {
      await new Promise((r) => setTimeout(r, 20));
    });
  }
}

function setInputValue(input: HTMLInputElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set;
  setter?.call(input, value);
  input.dispatchEvent(new Event("input", { bubbles: true }));
}

beforeEach(() => {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => {
    root.unmount();
  });
  container.remove();
  vi.clearAllMocks();
});

describe("OpenVikingRetrievalPage", () => {
  it("renders mocked-live hits after search", async () => {
    vikingMocks.searchVikingRetrieval.mockResolvedValue({
      hits: [{ id: "h1", title: "memory note", snippet: "likes dark mode", path: "memories/m1.md" }],
    });
    await act(async () => {
      root.render(
        <MemoryRouter>
          <OpenVikingRetrievalPage />
        </MemoryRouter>,
      );
    });
    const input = container.querySelector("#viking-retrieval-q") as HTMLInputElement;
    expect(input).not.toBeNull();
    await act(async () => {
      setInputValue(input, "dark mode");
    });
    await act(async () => {
      container.querySelector("form")?.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    });
    await waitFor(() => container.textContent?.includes("memory note") ?? false);
    expect(vikingMocks.searchVikingRetrieval).toHaveBeenCalledWith("dark mode", 25);
    expect(container.querySelector('[data-testid="viking-retrieval-hits"]')).not.toBeNull();
  });

  it("renders honestly disabled state when the bridge row 404s", async () => {
    vikingMocks.searchVikingRetrieval.mockRejectedValue(new Error("HTTP 404 not found"));
    await act(async () => {
      root.render(
        <MemoryRouter>
          <OpenVikingRetrievalPage />
        </MemoryRouter>,
      );
    });
    const input = container.querySelector("#viking-retrieval-q") as HTMLInputElement;
    await act(async () => {
      setInputValue(input, "anything");
    });
    await act(async () => {
      container.querySelector("form")?.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    });
    await waitFor(() => container.querySelector('[data-testid="viking-retrieval-unavailable"]') !== null);
  });
});
