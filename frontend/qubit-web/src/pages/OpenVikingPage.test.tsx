// @vitest-environment jsdom
/**
 * Open Viking console page: three-pane layout renders from mocked-live
 * bridge payloads (nav groups, context tree, Preview/Source toggle,
 * Quick start, terminal stub); a 404ing bridge row renders its control
 * honestly disabled with reason instead of faked content.
 */
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const vikingMocks = vi.hoisted(() => ({
  getVikingStatus: vi.fn(),
  getVikingContextTree: vi.fn(),
  getVikingFile: vi.fn(),
  findVikingRelated: vi.fn(),
}));

vi.mock("@/lib/ocFamilyVikingApi", () => ({
  VIKING_CONTEXT_GROUPS: [
    { key: "user_memories", label: "User memories" },
    { key: "bot", label: "Bot" },
    { key: "sessions", label: "Sessions" },
  ],
  getVikingStatus: vikingMocks.getVikingStatus,
  getVikingContextTree: vikingMocks.getVikingContextTree,
  getVikingFile: vikingMocks.getVikingFile,
  findVikingRelated: vikingMocks.findVikingRelated,
  isBridgeRowMissing: (e: unknown) =>
    e instanceof Error && (e.message.includes("404") || e.message.toLowerCase().includes("not found")),
  normalizeVikingStatus: () => ({ connection: "connected" }),
  normalizeContextTree: (p: unknown) => (p as Record<string, unknown>).tree ?? {},
  normalizeVikingFile: (p: unknown) => (p as { file: unknown }).file ?? null,
  normalizeSearchHits: (p: unknown) => (p as { hits: unknown[] }).hits ?? [],
  toVikingUri: (path: string) => `viking://${String(path).replace(/^\/+/, "")}`,
}));

import OpenVikingPage from "@/pages/OpenVikingPage";

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

beforeEach(() => {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  vikingMocks.getVikingStatus.mockResolvedValue({ connection: "connected" });
  vikingMocks.getVikingContextTree.mockResolvedValue({
    tree: {
      user_memories: [{ id: "m1", label: "favorite editor", path: "memories/m1.md" }],
      bot: [{ id: "b1", label: "VikingBot", path: "bot/profile.md" }],
    },
  });
  vikingMocks.getVikingFile.mockResolvedValue({
    file: { path: "memories/m1.md", vikingUri: "viking://memories/m1.md", content: "editor: qubit", truncated: false },
  });
  vikingMocks.findVikingRelated.mockResolvedValue({ hits: [] });
});

afterEach(async () => {
  await act(async () => {
    root.unmount();
  });
  container.remove();
  vi.clearAllMocks();
});

function renderPage() {
  return act(async () => {
    root.render(
      <MemoryRouter>
        <OpenVikingPage />
      </MemoryRouter>,
    );
  });
}

describe("OpenVikingPage", () => {
  it("renders the workspace/resources nav, context tree, viewer, quick start, and terminal stub", async () => {
    await renderPage();
    await waitFor(() => container.textContent?.includes("User memories") ?? false);
    for (const label of ["Playground", "Retrieval", "Uploads", "Agent Hub", "Documentation", "About", "SDK & API", "Agent Integrations"]) {
      expect(container.textContent).toContain(label);
    }
    expect(container.querySelector('[data-testid="viking-context-tree"]')).not.toBeNull();
    expect(container.querySelector('[data-testid="viking-viewer-body"]')).not.toBeNull();
    expect(container.textContent).toContain("Quick start");
    expect(container.textContent).toContain("Add resource");
    expect(container.textContent).toContain("Add memory");
    expect(container.textContent).toContain("Find related context");
    expect(container.querySelector('[data-testid="viking-terminal"]')).not.toBeNull();
    expect(container.querySelector('[data-testid="viking-connection"]')?.textContent).toBe(
      "Connected (local engine)",
    );
  });

  it("opens a tree node into the viewer with Preview/Source toggle and viking:// uri", async () => {
    await renderPage();
    await waitFor(() => container.textContent?.includes("favorite editor") ?? false);
    const nodeButton = Array.from(container.querySelectorAll("button")).find((b) =>
      b.textContent?.includes("favorite editor"),
    );
    expect(nodeButton).toBeDefined();
    await act(async () => {
      nodeButton?.click();
    });
    await waitFor(() => container.textContent?.includes("editor: qubit") ?? false);
    expect(container.querySelector('[data-testid="viking-uri"]')?.textContent).toBe(
      "viking://memories/m1.md",
    );
    const sourceTab = Array.from(container.querySelectorAll('[role="tab"]')).find((t) =>
      t.textContent?.includes("Source"),
    );
    await act(async () => {
      (sourceTab as HTMLElement)?.click();
    });
    expect(container.querySelector('[data-testid="viking-viewer-body"]')?.textContent).toContain(
      "editor: qubit",
    );
  });

  it("renders the missing-row notice when a bridge row 404s instead of faked content", async () => {
    vikingMocks.getVikingStatus.mockRejectedValue(new Error("GET /api/qubit/viking/status: HTTP 404"));
    vikingMocks.getVikingContextTree.mockRejectedValue(new Error("HTTP 404 not found"));
    await renderPage();
    await waitFor(() => container.querySelector('[data-testid="viking-missing-rows"]') !== null);
    expect(container.querySelector('[data-testid="viking-tree-unavailable"]')).not.toBeNull();
    expect(container.textContent).not.toContain("SERVER_UNCONFIGURED");
  });
});
