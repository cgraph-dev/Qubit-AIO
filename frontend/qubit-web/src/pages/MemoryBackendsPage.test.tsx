// @vitest-environment jsdom
/**
 * Qubit memory backends page — separate surface, not `/plugins`.
 *
 * The memory-provider gate exposes no selectable backends (read-only status
 * for `qubit-local` only), so the page must render status + disabled rows:
 * hosted/secret options assert disabled, and no control may fake a connect.
 */
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import MemoryBackendsPage from "./MemoryBackendsPage";
import { MEMORY_BACKENDS, ROUTES } from "@/lib/ocFamilyMemoryApi";

const fetchJSON = vi.hoisted(() => vi.fn());
const runMutationWithConfirm = vi.hoisted(() =>
  vi.fn((call: () => Promise<unknown>) => call()),
);

vi.mock("@/lib/api", () => ({
  fetchJSON,
  runMutationWithConfirm,
}));

function statusPayload() {
  return {
    active: "qubit-local",
    providers: [
      {
        name: "qubit-local",
        description: "local-only store",
        available: true,
        configured: true,
        status: "ready",
      },
    ],
    builtin_files: { memory: 3, user: 1 },
    state: "OK",
    enabled: true,
    store_state: "ok",
    store_path: "/tmp/memory.db",
  };
}

function probePayload() {
  return {
    provider: "qubit-local",
    active: "qubit-local",
    tiers: ["core", "working", "recall", "archive"],
    state: "OK",
    enabled: true,
    store_state: "ok",
    detail: "local-only store",
  };
}

let container: HTMLDivElement;
let root: Root | null = null;

function renderPage() {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  act(() => {
    root?.render(<MemoryBackendsPage />);
  });
}

async function flush() {
  await act(async () => {
    await Promise.resolve();
  });
}

function byTestId(id: string): HTMLElement | null {
  return container.querySelector(`[data-testid="${id}"]`);
}

beforeEach(() => {
  fetchJSON.mockReset();
  runMutationWithConfirm.mockReset();
  runMutationWithConfirm.mockImplementation((call: () => Promise<unknown>) => call());
  fetchJSON.mockImplementation((url: string) => {
    if (url === "/api/memory") return Promise.resolve(statusPayload());
    if (url === "/api/memory/providers/qubit-local")
      return Promise.resolve(probePayload());
    return Promise.reject(new Error(`unexpected GET ${url}`));
  });
});

afterEach(() => {
  root?.unmount();
  container.remove();
  vi.clearAllMocks();
});

describe("MemoryBackendsPage", () => {
  it("renders the status card with the live active provider", async () => {
    renderPage();
    await flush();
    await flush();
    expect(byTestId("memory-backends-title")?.textContent).toMatch(/memory backends/i);
    expect(byTestId("memory-active-provider")?.textContent).toBe("qubit-local");
    expect(byTestId("memory-read-probe")?.textContent).toContain("qubit-local");
  });

  it("lists all four backends with the local one selectable", async () => {
    renderPage();
    await flush();
    await flush();
    for (const backend of MEMORY_BACKENDS) {
      expect(byTestId(`memory-backend-${backend.id}`)).not.toBeNull();
    }
    const localSelect = byTestId("memory-backend-qubit-local-select") as HTMLButtonElement;
    expect(localSelect.disabled).toBe(false);
  });

  it("disables hosted/secret backends with a reason — never a fake connect", async () => {
    renderPage();
    await flush();
    await flush();
    for (const id of ["lancedb", "wiki", "active-memory"] as const) {
      const button = byTestId(`memory-backend-${id}-select`) as HTMLButtonElement;
      expect(button).not.toBeNull();
      expect(button.disabled).toBe(true);
      const reason = byTestId(`memory-backend-${id}-reason`)?.textContent ?? "";
      expect(reason.length).toBeGreaterThan(0);
    }
    // No control anywhere promises a hosted connect.
    const labels = Array.from(container.querySelectorAll("button"))
      .map((b) => b.textContent ?? "")
      .join(" ");
    expect(labels).not.toMatch(/connect/i);
  });

  it("selects the local backend through the confirm-gated mutation", async () => {
    fetchJSON.mockImplementation((url: string, init?: RequestInit) => {
      if (url === "/api/memory") return Promise.resolve(statusPayload());
      if (url === "/api/memory/providers/qubit-local")
        return Promise.resolve(probePayload());
      if (url === "/api/memory/provider" && init?.method === "PUT") {
        const body = JSON.parse(String(init.body)) as Record<string, unknown>;
        expect(body.provider).toBe("qubit-local");
        return Promise.resolve({ ok: true, active: "qubit-local" });
      }
      return Promise.reject(new Error(`unexpected ${init?.method ?? "GET"} ${url}`));
    });
    renderPage();
    await flush();
    await flush();
    const localSelect = byTestId("memory-backend-qubit-local-select") as HTMLButtonElement;
    await act(async () => {
      localSelect.click();
    });
    await flush();
    expect(runMutationWithConfirm).toHaveBeenCalledTimes(1);
    expect(byTestId("memory-action-message")?.textContent).toMatch(/qubit local memory confirmed/i);
  });

  it("ROUTES manifest stays under /api/qubit/memory and NEEDS-BRIDGE", () => {
    expect(ROUTES.length).toBeGreaterThan(0);
    for (const route of ROUTES) {
      expect(route.path.startsWith("/api/qubit/memory/")).toBe(true);
      expect(route.bridge).toBe("NEEDS-BRIDGE");
    }
  });
});
