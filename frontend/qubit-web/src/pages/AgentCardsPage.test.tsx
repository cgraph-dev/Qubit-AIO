// @vitest-environment jsdom
/**
 * /agent-cards — the ported typed agent card / artifact surface.
 *
 * Real backend wiring: tasks-list (200), ideas-list (200), goals-list (200),
 * alerts-list (200), cards-get (200/404), harness/harnesses (200). Mutation
 * surfaces (delegate, monitor control, memory write) have no Qubit backend and
 * must render complete cards with honest, stated gaps — never a dead button
 * that looks live and never a fabricated success.
 */
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

let container: HTMLDivElement;
let root: Root;
(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

const TASKS = {
  schema: "qubit-task-ledger/1",
  state: "OK",
  tasks: [
    {
      id: "schedules:4",
      source: "schedules",
      kind: "cron",
      state: "failed",
      source_state: "UNAVAILABLE",
      title: "Weekly review",
      updated_at: 1791076256.4,
    },
  ],
};

const IDEAS = {
  state: "OK",
  suggestions: [],
  considered: 0,
  authority: "read-only ledger derivation",
};

const GOALS = {
  state: "OK",
  goals: [],
  authority: "durable goal sidecar plus the canonical cron store",
};

const ALERTS = {
  state: "OK",
  runs: [
    { id: 4, state: "UNAVAILABLE", code: "MODEL_ERROR", detail: "fallback failed" },
  ],
};

const ROSTER = {
  state: "OK",
  agents: [
    { id: "qubit-worker", kind: "worker", route: "local", state: "OK", enabled: true },
  ],
};

function jsonResponse(body: unknown, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
    text: async () => JSON.stringify(body),
  };
}

const fetchCalls: string[] = [];
function stubFetch(impl: (url: string) => unknown) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      fetchCalls.push(String(url));
      return impl(url);
    }),
  );
}

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

async function renderAgentCards() {
  const { default: Page } = await import("./AgentCardsPage");
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await act(async () => root.render(<Page />));
  await waitFor(() => (container.textContent ?? "").includes("Weekly review"));
}

function clickByText(text: string) {
  const btn = [...container.querySelectorAll("button")].find((b) =>
    b.textContent?.includes(text),
  ) as HTMLButtonElement;
  expect(btn, `button containing "${text}"`).toBeTruthy();
  return btn;
}

beforeEach(() => {
  fetchCalls.length = 0;
  stubFetch((url) => {
    if (url.includes("tasks-list")) return jsonResponse(TASKS);
    if (url.includes("ideas-list")) return jsonResponse(IDEAS);
    if (url.includes("goals-list")) return jsonResponse(GOALS);
    if (url.includes("alerts-list")) return jsonResponse(ALERTS);
    if (url.includes("harnesses")) return jsonResponse(ROSTER);
    return jsonResponse({ state: "OK" });
  });
});

afterEach(async () => {
  await act(async () => root?.unmount());
  container?.remove();
  vi.unstubAllGlobals();
});

describe("AgentCardsPage", () => {
  it("renders the live task ledger", async () => {
    await renderAgentCards();
    expect(container.textContent).toContain("Weekly review");
    expect(container.textContent).toContain("Failed");
  });

  it("renders run receipts from the alert ledger", async () => {
    await renderAgentCards();
    await waitFor(() => (container.textContent ?? "").includes("run 4"));
  });

  it("keeps the rich card lookup honest at 404", async () => {
    stubFetch((url) => {
      if (url.includes("tasks-list")) return jsonResponse(TASKS);
      if (url.includes("alerts-list")) return jsonResponse(ALERTS);
      if (url.includes("harnesses")) return jsonResponse(ROSTER);
      if (url.includes("cards-get"))
        return jsonResponse(
          { ok: false, code: "BOT_CARD_NOT_FOUND", detail: "no card matching" },
          404,
        );
      return jsonResponse({ state: "OK" });
    });
    await renderAgentCards();
    await act(async () => clickByText("Weekly review").click());
    await waitFor(() =>
      (container.textContent ?? "").includes("read-only"),
    );
  });

  it("wires delegation to the real confirm-gated harness submit", async () => {
    // Intentional change: `POST /api/qubit/harness/task_submit` is a live
    // confirm-gated mutation, so the old "serves no task create route" stated
    // gap no longer applies (om-agent-ui.tsx DelegateSheet docstring). The
    // submit stays disabled until a prompt is typed — never a dead-looking
    // button, and the copy states the real confirmation path.
    await renderAgentCards();
    const delegate = clickByText("Delegate task");
    expect(delegate.disabled).toBe(true);
    expect(container.textContent).toContain("Runs on the server as a real agent turn");
    expect(container.textContent).toContain("asks for confirmation first");
  });

  it("shows the ideas tab with an honest empty state", async () => {
    await renderAgentCards();
    await act(async () => clickByText("Ideas").click());
    await waitFor(() =>
      (container.textContent ?? "").includes("Room for a good idea"),
    );
  });

  it("shows the goals tab with an honest empty state", async () => {
    await renderAgentCards();
    await act(async () => clickByText("Goals").click());
    await waitFor(() =>
      (container.textContent ?? "").includes("Big plans start with one small step"),
    );
  });

  it("shows the notifications tab with run receipts, not a phantom inbox", async () => {
    await renderAgentCards();
    await act(async () => clickByText("Notifications").click());
    await waitFor(() => (container.textContent ?? "").includes("run 4"));
    // The sheet renders the real alert-ledger receipts (id + detail), not a
    // fabricated per-user inbox. The "no notification inbox" copy is only in
    // the empty state (see NotificationsSheet), so with a run present the
    // proof is the real receipt detail.
    expect(container.textContent).toContain("fallback failed");
  });

  it("renders the live agent roster on the Apps tab", async () => {
    await renderAgentCards();
    await act(async () => clickByText("Apps").click());
    await waitFor(() =>
      (container.textContent ?? "").includes("qubit-worker"),
    );
  });
});
