// @vitest-environment jsdom
/**
 * A41 — /agents home backed by the canonical worker/adapter roster.
 *
 * Pins that the page renders the real `GET /api/qubit/agents` roster (worker
 * lane + adapters), not a fabricated list.
 */
import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

let container: HTMLDivElement;
let root: Root;
(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

const AGENTS = {
  agents: [
    {
      id: "qubit-worker",
      kind: "worker",
      route: "local",
      state: "OK",
      enabled: true,
      model: "qubit-brain4",
    },
    { id: "adapter:worker", kind: "adapter", route: "local", state: "OK" },
  ],
  authority:
    "read-only aggregation of the canonical worker lane, runtime adapters",
};

async function waitFor(cond: () => boolean, timeoutMs = 5000) {
  const start = Date.now();
  while (!cond()) {
    if (Date.now() - start > timeoutMs) {
      throw new Error("waitFor: condition never became true");
    }
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 20));
    });
  }
}

async function renderAgents(ui: ReactNode) {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await act(async () => root.render(ui));
}

async function renderAgentsPage() {
  const [{ default: AgentsPage }, { I18nProvider }] = await Promise.all([
    import("./AgentsPage"),
    import("@/i18n"),
  ]);
  await renderAgents(
    <I18nProvider>
      <AgentsPage />
    </I18nProvider>,
  );
  await waitFor(() =>
    Boolean(container.querySelector('[aria-label="Agent roster"]')),
  );
}

beforeEach(() => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => ({
      ok: true,
      status: 200,
      json: async () => AGENTS,
      text: async () => "",
    })),
  );
});

afterEach(async () => {
  await act(async () => root?.unmount());
  container?.remove();
  vi.unstubAllGlobals();
});

describe("AgentsPage roster (A41)", () => {
  it("renders the canonical worker + adapter roster", async () => {
    await renderAgentsPage();
    expect(container.textContent).toContain("qubit-worker");
    expect(container.textContent).toContain("adapter:worker");
    expect(container.textContent).toContain("qubit-brain4");
    expect(container.querySelectorAll('[aria-label="Agent roster"] li').length)
      .toBe(2);
  });

  it("honestly gates the desktop-only agent-management controls", async () => {
    await renderAgentsPage();
    const gated = container.querySelector('[data-state="gated"]');
    expect(gated).not.toBeNull();
    expect(gated?.textContent).toContain("desktop app");
  });
});
