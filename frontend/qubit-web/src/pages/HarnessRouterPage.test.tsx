// @vitest-environment jsdom
/**
 * HarnessRouterPage — mocked harness-switch + cancel assertions.
 *
 * Mocks the `/api/qubit/harness/*` family at the fetch boundary (no backend
 * needed): pins that the task submit carries the switched harness id and
 * that Cancel posts the task id to `task_cancel`.
 */
import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

let container: HTMLDivElement;
let root: Root;
(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const HARNESSES = {
  result: {
    harnesses: [
      { id: "qubit-worker", kind: "agent", backend: "agent.session", state: "ready" },
      { id: "qubit-codex", kind: "agent", backend: "codex-runtime", state: "ready" },
    ],
  },
};
const KITS = { result: { harnesses: [{ id: "qubit-starter-docs", kind: "starter-kit" }] } };
const PLUGINS = { result: { harnesses: [{ id: "qubit-plugin-kanban", kind: "plugin", state: "ready" }] } };
const TASKS = {
  result: {
    tasks: [{ id: "task-1", harness: "qubit-worker", state: "running", title: "demo" }],
  },
};

const posted: Array<{ url: string; body: Record<string, unknown> }> = [];

function jsonResponse(payload: unknown) {
  return {
    ok: true,
    status: 200,
    json: async () => payload,
    text: async () => JSON.stringify(payload),
    clone: () => ({ json: async () => payload }),
  };
}

async function waitFor(cond: () => boolean, timeoutMs = 5000) {
  const start = Date.now();
  while (!cond()) {
    if (Date.now() - start > timeoutMs) throw new Error("waitFor: condition never became true");
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 20));
    });
  }
}

function query(sel: string): HTMLElement {
  const el = container.querySelector(sel);
  if (!el) throw new Error(`missing element ${sel}`);
  return el as HTMLElement;
}

async function renderPage() {
  const { default: HarnessRouterPage } = await import("./HarnessRouterPage");
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await act(async () => root.render(<HarnessRouterPage /> as ReactNode));
  await waitFor(() => Boolean(container.querySelector('[data-testid="harness-router-page"]')));
  await waitFor(() => Boolean(container.querySelector('[data-testid="harness-qubit-worker"]')));
}

beforeEach(() => {
  posted.length = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: unknown, init?: RequestInit) => {
      const url = String(input);
      if (init?.method === "POST") {
        const body = JSON.parse(String(init.body ?? "{}")) as Record<string, unknown>;
        posted.push({ url, body });
        if (url.includes("/task_submit")) return jsonResponse({ result: { id: "task-9" } });
        if (url.includes("/task_cancel")) return jsonResponse({ result: { ok: true } });
        return jsonResponse({ result: {} });
      }
      if (url.includes("/starter_kits")) return jsonResponse(KITS);
      if (url.includes("/harness/plugins")) return jsonResponse(PLUGINS);
      if (url.includes("/tasks")) return jsonResponse(TASKS);
      return jsonResponse(HARNESSES);
    }),
  );
});

afterEach(async () => {
  await act(async () => root?.unmount());
  container?.remove();
  vi.unstubAllGlobals();
});

describe("HarnessRouterPage", () => {
  it("lists agent harnesses, kits, and plugins", async () => {
    await renderPage();
    expect(container.querySelector('[data-testid="harness-qubit-worker"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="harness-qubit-codex"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="kit-qubit-starter-docs"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="plugin-qubit-plugin-kanban"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="task-task-1"]')).toBeTruthy();
  });

  it("submits with the switched harness", async () => {
    await renderPage();
    const prompt = query('[data-testid="harness-task-prompt"]') as HTMLInputElement;
    await act(async () => {
      prompt.focus();
    });
    // React-controlled input: set value via native setter + input event.
    const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value")?.set;
    setter?.call(prompt, "summarize the inbox");
    prompt.dispatchEvent(new Event("input", { bubbles: true }));
    const select = query('[data-testid="harness-switch"]') as HTMLSelectElement;
    await act(async () => {
      select.value = "qubit-codex";
      select.dispatchEvent(new Event("change", { bubbles: true }));
    });
    await act(async () => {
      query('[data-testid="harness-submit"]').click();
    });
    await waitFor(() => posted.some((p) => p.url.includes("/task_submit")));
    const submit = posted.find((p) => p.url.includes("/task_submit"));
    expect(submit?.body.harness).toBe("qubit-codex");
    expect(String(submit?.body.prompt)).toContain("summarize the inbox");
    await waitFor(() => Boolean(container.querySelector('[data-testid="harness-notice"]')));
  });

  it("cancels a task by id", async () => {
    await renderPage();
    await act(async () => {
      query('[data-testid="cancel-task-1"]').click();
    });
    await waitFor(() => posted.some((p) => p.url.includes("/task_cancel")));
    const cancel = posted.find((p) => p.url.includes("/task_cancel"));
    expect(cancel?.body.taskId).toBe("task-1");
    await waitFor(() => Boolean(container.querySelector('[data-testid="harness-notice"]')));
  });
});
