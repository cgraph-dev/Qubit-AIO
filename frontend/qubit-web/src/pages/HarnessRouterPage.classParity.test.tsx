// @vitest-environment jsdom
/**
 * Harness-console structural parity — HarnessRouterPage (/harness-router)
 * renders the upstream console structure from
 * `src/styles/upstream/harness.css`: the console skeleton (panel, header,
 * index table) and the per-harness console (submit + harness switch, tasks
 * + cancellation, sessions, files).
 *
 * Static selector coverage (every `hx-*` / `ak-*` class the page uses
 * resolves in the shipped CSS, provenance header intact) lives in
 * `HarnessRouterPage.classParity.test.ts` (node env: `node:fs` does not
 * load under jsdom in this repo's vitest setup).
 */
import type { ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const harnessMocks = vi.hoisted(() => ({
  listHarnesses: vi.fn(),
  listStarterKits: vi.fn(),
  listHarnessPlugins: vi.fn(),
  listHarnessTasks: vi.fn(),
}));

vi.mock("@/lib/ocFamilyHarnessApi", async (importOriginal) => {
  const orig = (await importOriginal()) as Record<string, unknown>;
  return { ...orig, ...harnessMocks };
});

import HarnessRouterPage from "@/pages/HarnessRouterPage";

// NOTE: no `act()` in this file. The installed React build exposes no working
// `act` (neither `react` nor `react-dom/test-utils` — both throw
// "React.act is not a function"), so these tests render + poll with plain
// timers instead. React logs not-act warnings; assertions still observe the
// settled DOM.

let container: HTMLDivElement;
let root: Root;

function query(sel: string): HTMLElement {
  const el = container.querySelector(sel);
  if (!el) throw new Error(`missing element ${sel}`);
  return el as HTMLElement;
}

async function waitFor(cond: () => boolean, timeoutMs = 5000) {
  const start = Date.now();
  while (!cond()) {
    if (Date.now() - start > timeoutMs) throw new Error("waitFor: condition never became true");
    await new Promise((resolve) => setTimeout(resolve, 20));
  }
}

beforeEach(() => {
  harnessMocks.listHarnesses.mockResolvedValue({
    result: {
      harnesses: [
        { id: "qubit-worker", kind: "agent", backend: "agent.session", state: "ready" },
        { id: "qubit-codex", kind: "agent", backend: "codex-runtime", state: "ready" },
      ],
    },
  });
  harnessMocks.listStarterKits.mockResolvedValue({
    result: { harnesses: [{ id: "qubit-starter-docs", kind: "starter-kit" }] },
  });
  harnessMocks.listHarnessPlugins.mockResolvedValue({
    result: { harnesses: [{ id: "qubit-plugin-kanban", kind: "plugin", state: "ready" }] },
  });
  harnessMocks.listHarnessTasks.mockResolvedValue({
    result: {
      tasks: [{ id: "task-1", harness: "qubit-worker", state: "running", title: "demo" }],
    },
  });
});

afterEach(() => {
  root?.unmount();
  container?.remove();
  vi.clearAllMocks();
});

async function renderPage() {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  root.render(<HarnessRouterPage /> as ReactNode);
  await waitFor(() => Boolean(container.querySelector('[data-testid="harness-router-page"]')));
  await waitFor(() => Boolean(container.querySelector('[data-testid="harness-qubit-worker"]')));
}

describe("harness console structure", () => {
  it("renders the console skeleton: panel, header, index table", async () => {
    await renderPage();
    for (const cls of [
      "hx-root",
      "hx-side",
      "hx-side-head",
      "hx-search",
      "hx-index",
      "hx-group",
      "hx-proj",
      "hx-main",
      "hx-head",
      "hx-crumb",
      "hx-title",
      "hx-pill",
      "hx-head-actions",
      "hx-body",
      "hx-index-wrap",
      "hx-card",
      "hx-thead",
      "hx-row",
      "hx-health",
    ]) {
      expect(container.querySelector(`.${cls}`), cls).not.toBeNull();
    }
    expect(container.querySelector("#view-harnesses")).not.toBeNull();
  });

  it("opens a harness into the task console with submit switch and cancellation", async () => {
    await renderPage();
    query('[data-testid="harness-qubit-worker"]').click();
    await waitFor(() => Boolean(container.querySelector('[data-testid="section-submit-task"]')));
    expect(container.querySelector('[data-testid="harness-task-prompt"]')).not.toBeNull();
    expect(container.querySelector('[data-testid="harness-switch"]')).not.toBeNull();
    expect(container.querySelector('[data-testid="harness-submit"]')).not.toBeNull();
    expect(container.querySelector('[data-testid="section-tasks"]')).not.toBeNull();
    expect(container.querySelector('[data-testid="task-task-1"]')).not.toBeNull();
    expect(container.querySelector('[data-testid="cancel-task-1"]')).not.toBeNull();
    expect(container.querySelector('[data-testid="section-sessions"]')).not.toBeNull();
    expect(container.querySelector('[data-testid="section-files"]')).not.toBeNull();
    // Console states travel with the rows: running dot + pill.
    expect(container.querySelector(".hx-dot")).not.toBeNull();
    expect(container.querySelector(".hx-pill")).not.toBeNull();
  });
});
