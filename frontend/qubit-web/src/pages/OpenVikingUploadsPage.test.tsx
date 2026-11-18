// @vitest-environment jsdom
/**
 * Open Viking uploads page: resource + memory tabs render, confirmed
 * writes call the mocked-live confirm-gated bridge, and a 404ing
 * bridge row fails honestly with reason (no unconfigured state).
 */
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import "./sakuga-test-dom";
import { sakugaInnerAll, typeIntoSakuga, waitForSakuga } from "@qubit/shared/sakuga";

const vikingMocks = vi.hoisted(() => ({
  addVikingResource: vi.fn(),
  addVikingMemory: vi.fn(),
}));

vi.mock("@/lib/ocFamilyVikingApi", () => ({
  addVikingResource: vikingMocks.addVikingResource,
  addVikingMemory: vikingMocks.addVikingMemory,
  isBridgeRowMissing: (e: unknown) =>
    e instanceof Error && (e.message.includes("404") || e.message.toLowerCase().includes("not found")),
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

describe("OpenVikingUploadsPage", () => {
  it("stages a resource through the confirm-gated bridge", async () => {
    await act(async () => {
      root.render(
        <MemoryRouter>
          <OpenVikingUploadsPage />
        </MemoryRouter>,
      );
    });
    const uri = container.querySelector("#viking-upload-uri");
    expect(uri).not.toBeNull();
    await waitForSakuga(uri);
    await act(async () => {
      typeIntoSakuga(uri, "viking://resources/guide.md");
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
    const tabs = container.querySelector("sakuga-tabs");
    await waitForSakuga(tabs);
    const memoryTab = sakugaInnerAll<HTMLButtonElement>(tabs, "button").find((b) =>
      b.textContent?.includes("Add memory"),
    );
    expect(memoryTab).toBeDefined();
    await act(async () => {
      memoryTab?.click();
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

  it("fails honestly when the bridge row 404s", async () => {
    vikingMocks.addVikingResource.mockRejectedValue(new Error("HTTP 404 not found"));
    await act(async () => {
      root.render(
        <MemoryRouter>
          <OpenVikingUploadsPage />
        </MemoryRouter>,
      );
    });
    const uri = container.querySelector("#viking-upload-uri");
    await waitForSakuga(uri);
    await act(async () => {
      typeIntoSakuga(uri, "viking://resources/guide.md");
    });
    await act(async () => {
      container.querySelector("form")?.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    });
    await act(async () => {
      await new Promise((r) => setTimeout(r, 20));
    });
    expect(container.querySelector('[role="alert"]')?.textContent).toMatch(/Bridge row missing/);
  });
});
