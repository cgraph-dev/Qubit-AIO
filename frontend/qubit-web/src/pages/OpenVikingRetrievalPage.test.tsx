// @vitest-environment jsdom
/**
 * Open Viking retrieval page: search form renders, mocked hits render,
 * and the typed SERVER_UNCONFIGURED state renders when the bridge
 * reports no configured server.
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
  isServerUnconfiguredPayload: (p: unknown) =>
    !!p && typeof p === "object" && (p as Record<string, unknown>).code === "SERVER_UNCONFIGURED",
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
  it("renders mocked hits after search", async () => {
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

  it("shows SERVER_UNCONFIGURED when the bridge reports no server", async () => {
    vikingMocks.searchVikingRetrieval.mockResolvedValue({ code: "SERVER_UNCONFIGURED" });
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
    await waitFor(() => container.textContent?.includes("SERVER_UNCONFIGURED") ?? false);
  });
});
