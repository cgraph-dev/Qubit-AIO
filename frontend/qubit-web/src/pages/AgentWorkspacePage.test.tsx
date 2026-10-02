// @vitest-environment jsdom
/**
 * /agent-workspace — read-only browse (agents.workspace.list), single-file
 * open (agents.workspace.get), run status (agent.wait). Unreadable rows render
 * disabled with their reason and never fire; create/edit/delete stays a gated
 * notice, never a dead control.
 */
import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

let container: HTMLDivElement;
let root: Root;
(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

const LIST = {
  files: [
    { path: "agent/main.py", size_bytes: 120, readable: true },
    {
      path: "agent/secret-store.bin",
      size_bytes: 88,
      readable: false,
      unreadable_reason: "Binary files cannot be opened from the dashboard.",
    },
  ],
  authority: "read-only agent workspace roster",
};

function jsonResponse(body: unknown) {
  return {
    ok: true,
    status: 200,
    json: async () => body,
    text: async () => JSON.stringify(body),
  };
}

const fetchCalls: string[] = [];
function stubFetch(
  impl: (url: string, init?: RequestInit) => unknown,
) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      fetchCalls.push(String(url));
      return impl(url, init);
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

async function renderPage(ui: ReactNode) {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await act(async () => root.render(ui));
}

async function renderWorkspacePage() {
  const { default: AgentWorkspacePage } = await import("./AgentWorkspacePage");
  await renderPage(<AgentWorkspacePage />);
  await waitFor(() =>
    Boolean(container.querySelector('[aria-label="Workspace files"]')),
  );
}

beforeEach(() => {
  fetchCalls.length = 0;
  stubFetch((url) => {
    if (url.includes("/agent-workspace/get")) {
      return jsonResponse({ path: "agent/main.py", content: "print('ok')\n" });
    }
    if (url.includes("/agent-workspace/run-status")) {
      return jsonResponse({ run_id: "run-1", state: "done", detail: "exit 0" });
    }
    return jsonResponse(LIST);
  });
});

afterEach(async () => {
  await act(async () => root?.unmount());
  container?.remove();
  vi.unstubAllGlobals();
});

describe("AgentWorkspacePage browse (agents.workspace.list/get)", () => {
  it("renders the workspace roster", async () => {
    await renderWorkspacePage();
    expect(container.textContent).toContain("agent/main.py");
    expect(container.textContent).toContain("agent/secret-store.bin");
    expect(container.querySelectorAll('[aria-label="Workspace files"] li').length).toBe(2);
  });

  it("renders unreadable rows disabled with their reason and never fires", async () => {
    await renderWorkspacePage();
    const refused = container.querySelector('[data-state="refused"]');
    expect(refused).not.toBeNull();
    expect(refused?.getAttribute("aria-disabled")).toBe("true");
    expect(refused?.getAttribute("title")).toContain("Binary files");
    expect(container.textContent).toContain("Binary files cannot be opened");
    const before = fetchCalls.filter((u) => u.includes("/agent-workspace/get")).length;
    await act(async () => {
      (refused as HTMLButtonElement).click();
    });
    expect(fetchCalls.filter((u) => u.includes("/agent-workspace/get")).length).toBe(before);
  });

  it("opens a readable file and shows its content", async () => {
    await renderWorkspacePage();
    const open = container.querySelector(
      '[aria-label="Open file agent/main.py"]',
    ) as HTMLButtonElement;
    await act(async () => open.click());
    await waitFor(() => (container.textContent ?? "").includes("print('ok')"));
  });

  it("checks run status (agent.wait)", async () => {
    await renderWorkspacePage();
    const input = container.querySelector('[aria-label="Run id"]') as HTMLInputElement;
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(
        window.HTMLInputElement.prototype,
        "value",
      )!.set!;
      setter.call(input, "run-1");
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    const check = container.querySelector(
      '[aria-label="Check run status"]',
    ) as HTMLButtonElement;
    await act(async () => check.click());
    await waitFor(() => (container.textContent ?? "").includes("run-1: done"));
  });

  it("honestly gates workspace create/edit/delete", async () => {
    await renderWorkspacePage();
    const gated = container.querySelector('[data-state="gated"]');
    expect(gated).not.toBeNull();
    expect(gated?.textContent).toContain("read-only browse");
  });
});
