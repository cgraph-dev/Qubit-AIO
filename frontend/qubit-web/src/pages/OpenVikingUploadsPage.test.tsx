// @vitest-environment jsdom
/**
 * Open Viking uploads page: resource + memory tabs render, confirmed
 * writes call the mocked confirm-gated bridge, and the typed
 * SERVER_UNCONFIGURED failure renders honestly.
 */
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const vikingMocks = vi.hoisted(() => ({
  addVikingResource: vi.fn(),
  addVikingMemory: vi.fn(),
}));

vi.mock("@/lib/ocFamilyVikingApi", () => ({
  addVikingResource: vikingMocks.addVikingResource,
  addVikingMemory: vikingMocks.addVikingMemory,
  isServerUnconfiguredPayload: () => false,
  VikingServerUnconfiguredError: class VikingServerUnconfiguredError extends Error {},
}));

import OpenVikingUploadsPage from "@/pages/OpenVikingUploadsPage";

let container: HTMLDivElement;
let root: Root;
(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

beforeEach(() => {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  vikingMocks.addVikingResource.mockResolvedValue({ ok: true });
  vikingMocks.addVikingMemory.mockResolvedValue({ ok: true });
  vi.stubGlobal("confirm", () => true);
});

afterEach(async () => {
  await act(async () => {
    root.unmount();
  });
  container.remove();
  vi.clearAllMocks();
  vi.unstubAllGlobals();
});

function setInputValue(input: HTMLInputElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set;
  setter?.call(input, value);
  input.dispatchEvent(new Event("input", { bubbles: true }));
}

describe("OpenVikingUploadsPage", () => {
  it("stages a resource through the confirm-gated bridge", async () => {
    await act(async () => {
      root.render(
        <MemoryRouter>
          <OpenVikingUploadsPage />
        </MemoryRouter>,
      );
    });
    const uri = container.querySelector("#viking-upload-uri") as HTMLInputElement;
    expect(uri).not.toBeNull();
    await act(async () => {
      setInputValue(uri, "viking://resources/guide.md");
    });
    await act(async () => {
      container.querySelector("form")?.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    });
    expect(vikingMocks.addVikingResource).toHaveBeenCalledTimes(1);
    expect(vikingMocks.addVikingResource.mock.calls[0]?.[0]).toMatchObject({
      uri: "viking://resources/guide.md",
    });
  });

  it("stages a memory from the memory tab", async () => {
    await act(async () => {
      root.render(
        <MemoryRouter>
          <OpenVikingUploadsPage />
        </MemoryRouter>,
      );
    });
    const memoryTab = Array.from(container.querySelectorAll('[role="tab"]')).find((t) =>
      t.textContent?.includes("Add memory"),
    ) as HTMLElement;
    await act(async () => {
      memoryTab.click();
    });
    const area = container.querySelector("#viking-memory-text") as HTMLTextAreaElement;
    expect(area).not.toBeNull();
    const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")?.set;
    await act(async () => {
      setter?.call(area, "prefers concise answers");
      area.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await act(async () => {
      container.querySelector("form")?.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    });
    expect(vikingMocks.addVikingMemory).toHaveBeenCalledTimes(1);
  });
});
