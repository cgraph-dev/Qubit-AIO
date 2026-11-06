// @vitest-environment jsdom
/**
 * The workbench's honesty contract, pinned end to end.
 *
 * Two failure modes are what these tests exist for:
 *
 * 1. A 404 from `/api/workbench/*` being swallowed into an empty array, so the
 *    page renders "no sessions" — a confident, false claim — instead of "this
 *    backend is not mounted".
 * 2. A backend that answers "nothing, and here is why" (`NO_ACTIVE_PROJECT`)
 *    being rendered as "this folder is empty", or a tab with no backend at all
 *    rendering a zero count.
 *
 * The network is stubbed at `fetch` so the REAL data layer runs (URL building,
 * status mapping, shape parsing) rather than a mocked module.
 *
 * One thing jsdom cannot show: layout. The page folds its columns with
 * `hidden`/`flex` classes, and jsdom keeps every element in the tree at every
 * width, so a click here travels to the mounted branch rather than to a
 * painted one. Every assertion below is about which STATE the page is in, which
 * is exactly what those classes never change.
 */
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import WorkbenchPage from "@/pages/WorkbenchPage";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

interface Reply {
  status: number;
  body?: unknown;
  text?: string;
}

/** Per-route canned replies; a missing route 404s, like the real seam today. */
let routes: Record<string, Reply> = {};
let calls: string[] = [];
let container: HTMLDivElement;
let root: Root;

const { I18nProvider } = await import("@/i18n");
const { PageHeaderProvider } = await import("@/contexts/PageHeaderProvider");

function reply(init: Reply): Response {
  const body = init.text ?? (init.body === undefined ? "" : JSON.stringify(init.body));
  return {
    ok: init.status >= 200 && init.status < 300,
    status: init.status,
    statusText: init.status === 404 ? "Not Found" : "OK",
    text: async () => body,
  } as unknown as Response;
}

beforeEach(() => {
  routes = {};
  calls = [];
  // Stub BOTH bindings. The data layer prefers `window.fetch`, but a bare jsdom
  // environment resolves the global to jsdom's own `fetch`, so patching only
  // `window.fetch` left node's undici answering the real server.
  const stub = ((url: string) => {
    calls.push(String(url));
    const key = Object.keys(routes).find((candidate) => String(url).includes(candidate));
    const payload = key ? routes[key] : { status: 404 };
    return Promise.resolve(reply(payload));
  }) as unknown as typeof fetch;
  vi.stubGlobal("fetch", stub);
  window.fetch = stub;
  Object.defineProperty(globalThis, "fetch", { value: stub, configurable: true, writable: true });
  Object.defineProperty(window, "__QUBIT_SESSION_TOKEN__", {
    value: "test-token",
    configurable: true,
  });

  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  vi.unstubAllGlobals();
});

async function settle(ms = 300) {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, ms));
  });
}

async function renderPage() {
  await act(async () => {
    root.render(
      <MemoryRouter initialEntries={["/qubit-workbench"]}>
        <I18nProvider>
          <PageHeaderProvider pluginTabs={[]}>
            <WorkbenchPage />
          </PageHeaderProvider>
        </I18nProvider>
      </MemoryRouter>,
    );
  });
  await settle();
}

function text(): string {
  return container.textContent ?? "";
}

function all(selector: string): HTMLElement[] {
  return Array.from(container.querySelectorAll<HTMLElement>(selector));
}

function byTestId(id: string): HTMLElement | null {
  return container.querySelector<HTMLElement>(`[data-testid="${id}"]`);
}

/** A page of sessions in the shape the adapter answers. */
function sessionsPage(rows: Record<string, unknown>[]) {
  return {
    status: 200,
    body: {
      state: "OK",
      ok: true,
      sessions: rows,
      total: rows.length,
      shown: rows.length,
      has_more: false,
    },
  };
}

describe("workbench — four-region layout", () => {
  it("renders the rail, the conversation sidebar and the workspace panel", async () => {
    await renderPage();
    expect(byTestId("workbench-rail")).not.toBeNull();
    expect(byTestId("workbench-sessions")).not.toBeNull();
    expect(byTestId("workbench-workspace-panel")).not.toBeNull();
    expect(byTestId("workbench-transcript")).not.toBeNull();
    // The rail, the sidebar and the workspace column are siblings inside ONE
    // flex row, in that order — the four-region arrangement itself.
    const rail = byTestId("workbench-rail")!;
    const row = rail.parentElement!;
    expect(byTestId("workbench-sessions")!.parentElement?.parentElement).toBe(row);
    // The workspace column is the last region in that row (wrapped in its own
    // width container, so the aside's parent is the row's last child).
    expect(byTestId("workbench-workspace-panel")!.parentElement?.parentElement).toBe(row);
  });

  it("draws exactly eight rail destinations, and every enabled one is a real route", async () => {
    await renderPage();
    const rail = byTestId("workbench-rail")!;
    const nodes = Array.from(rail.querySelectorAll("a, button"));
    expect(nodes).toHaveLength(8);
    for (const node of nodes) {
      if (node.tagName === "A") {
        expect(node.getAttribute("href")).toMatch(/^\//);
      } else {
        // A disabled entry has no href and says why in its title.
        expect(node.hasAttribute("disabled")).toBe(true);
        expect(node.getAttribute("title")).toBeTruthy();
      }
    }
  });

  it("carries the composer's control cluster, with voice visibly disabled and pointed at /qubit-chat", async () => {
    await renderPage();
    const mic = byTestId("workbench-mic");
    expect(mic).not.toBeNull();
    expect(mic!.hasAttribute("disabled")).toBe(true);
    expect(mic!.getAttribute("title")).toContain("/qubit-chat");
    for (const id of [
      "workbench-attach",
      "workbench-image",
      "workbench-profile-chip",
      "workbench-workspace-chip",
      "workbench-model-chip",
      "workbench-reasoning-chip",
      "workbench-send",
    ]) {
      expect(byTestId(id), id).not.toBeNull();
    }
  });
});

describe("workbench — backend not available", () => {
  it("says so for sessions AND workspace, instead of rendering empty lists", async () => {
    await renderPage();

    const unavailable = all('[data-testid="workbench-backend-not-available"]');
    expect(unavailable.length).toBeGreaterThanOrEqual(2);
    expect(text()).toContain("Backend not available");
    expect(text()).toContain("/api/workbench/sessions");

    // Neither the "no sessions" claim nor an empty-entry list exists.
    expect(all('[data-testid="workbench-sessions-empty"]')).toHaveLength(0);
    expect(all('[data-testid="workbench-session-row"]')).toHaveLength(0);
    expect(all('[aria-label="Workbench sessions"]')).toHaveLength(0);
    expect(all('[aria-label="Workspace entries"]')).toHaveLength(0);
    // The workspace panel does not claim "this folder is empty" either.
    expect(text()).not.toContain("This folder is empty");
  });

  it("an attempted send against the missing route says the route is not mounted", async () => {
    // A session must be open for the composer to send: the seam needs an id.
    routes = {
      "/api/workbench/sessions/abc": {
        status: 200,
        body: { id: "abc", messages: [{ role: "user", content: "Hello" }], state: "OK" },
      },
      "/api/workbench/sessions": sessionsPage([{ id: "abc", title: "A", source: "tui" }]),
    };
    await renderPage();
    await act(async () => byTestId("workbench-source-cli")!.click());
    await act(async () => byTestId("workbench-session-abc")!.click());
    await settle(60);

    const input = container.querySelector<HTMLTextAreaElement>('textarea[aria-label="Message"]');
    expect(input).not.toBeNull();

    await act(async () => {
      // Drive the controlled textarea through the native setter so React's
      // value tracker sees the change.
      const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")!.set!;
      setter.call(input, "hello?");
      input!.dispatchEvent(new Event("input", { bubbles: true }));
    });

    await act(async () => {
      container
        .querySelector<HTMLFormElement>('[data-testid="workbench-composer"]')!
        .dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    });
    await settle(60);

    expect(calls.some((c) => c.includes("/api/workbench/turn"))).toBe(true);
    expect(byTestId("workbench-turn-not-available")).not.toBeNull();
    expect(byTestId("workbench-message-local-reply")).toBeNull();
    expect(byTestId("workbench-transcript-empty")).toBeNull();
  });

  it("does not call the turn endpoint before a send is attempted", async () => {
    await renderPage();
    expect(calls.some((c) => c.includes("/api/workbench/turn"))).toBe(false);
  });
});

describe("workbench — a mounted backend that says why it is empty", () => {
  it("quotes NO_ACTIVE_PROJECT instead of claiming an empty folder", async () => {
    routes = {
      "/api/workbench/sessions": sessionsPage([]),
      "/api/workbench/workspace/tree": {
        status: 200,
        body: {
          state: "EMPTY",
          ok: true,
          code: "NO_ACTIVE_PROJECT",
          detail: "no active Qubit project, so the workbench has no folder to show.",
          entries: [],
        },
      },
    };
    await renderPage();
    const reason = byTestId("workbench-workspace-no-project");
    expect(reason).not.toBeNull();
    expect(reason!.textContent).toContain("NO_ACTIVE_PROJECT");
    expect(text()).not.toContain("This folder is empty");
    // The sessions route answered with a real empty page, so THAT fact is fine
    // to state — it is a mounted backend's own answer.
    expect(byTestId("workbench-sessions-empty")).not.toBeNull();
  });
});

describe("workbench — workspace tabs", () => {
  it("Artifacts and Todos name the missing route instead of faking content", async () => {
    routes = {
      "/api/workbench/sessions": sessionsPage([]),
      "/api/workbench/workspace/tree": { status: 200, body: { entries: [], workspace: "/w" } },
    };
    await renderPage();

    await act(async () => byTestId("workbench-ws-tab-artifacts")!.click());
    const artifacts = byTestId("workbench-artifacts-gap");
    expect(artifacts).not.toBeNull();
    expect(artifacts!.textContent).toContain("not implemented on the Qubit side yet");
    expect(artifacts!.textContent).toContain("/api/workbench/workspace/artifacts");

    await act(async () => byTestId("workbench-ws-tab-todos")!.click());
    const todos = byTestId("workbench-todos-gap");
    expect(todos!.textContent).toContain("not implemented on the Qubit side yet");
    expect(todos!.textContent).toContain("/api/workbench/todos");
  });

  it("Files renders the real tree with sizes from the backend's bytes", async () => {
    routes = {
      "/api/workbench/sessions": sessionsPage([]),
      "/api/workbench/workspace/tree": {
        status: 200,
        body: {
          state: "OK",
          ok: true,
          path: "",
          workspace: "/w",
          entries: [
            { name: "notes.md", path: "notes.md", type: "file", size: 5734 },
            { name: "empty-dir", path: "empty-dir", type: "dir", size: null },
          ],
        },
      },
    };
    await renderPage();
    expect(byTestId("workbench-entry-notes.md")?.textContent).toContain("5.6k");
    // A directory carries no size because the backend reports none for one.
    expect(byTestId("workbench-entry-empty-dir")?.textContent).not.toContain("k");
  });
});

describe("workbench — timestamps", () => {
  it("reads ISO timestamps and millisecond epochs as seconds", async () => {
    routes = {
      "/api/workbench/sessions/abc": {
        status: 200,
        body: {
          id: "abc",
          messages: [
            { role: "user", content: "iso", timestamp: new Date().toISOString() },
            { role: "assistant", content: "ms", timestamp: Date.now() },
          ],
        },
      },
      "/api/workbench/sessions": sessionsPage([{ id: "abc", title: "T", source: "tui" }]),
      "/api/workbench/workspace/tree": { status: 200, body: { entries: [] } },
    };
    await renderPage();
    await act(async () => byTestId("workbench-source-cli")!.click());
    await act(async () => byTestId("workbench-session-abc")!.click());
    await settle(200);
    // Both normalise to "now" rather than a 20000-day figure.
    expect(all('[data-testid="workbench-message-user"]')[0]?.textContent).toContain("now");
    expect(all('[data-testid="workbench-message-assistant"]')[0]?.textContent).toContain("now");
  });
});

describe("workbench — live backend", () => {
  it("renders the sessions it was given, groups them, and can open one", async () => {
    routes = {
      "/api/workbench/sessions/abc": {
        status: 200,
        body: {
          id: "abc",
          title: "Workbench session",
          model: "openai/gpt-x",
          messages: [{ role: "user", content: "Hello there" }],
          total: 1,
        },
      },
      "/api/workbench/sessions": sessionsPage([
        {
          id: "abc",
          title: "Workbench session",
          source: "tui",
          message_count: 2,
          model: "openai/gpt-x",
          last_active: Date.now() / 1000,
        },
      ]),
      "/api/workbench/workspace/tree": {
        status: 200,
        body: {
          root: "/w",
          workspace: "/w",
          entries: [{ name: "notes.md", path: "notes.md", type: "file", size: 12 }],
        },
      },
    };

    await renderPage();
    expect(all('[data-testid="workbench-backend-not-available"]')).toHaveLength(0);

    // A `tui`-sourced row belongs to the CLI side of the source switch; click
    // through to it, then assert the TODAY group and the source badge.
    await act(async () => byTestId("workbench-source-cli")!.click());
    expect(byTestId("workbench-group-today")).not.toBeNull();
    const row = byTestId("workbench-session-abc");
    expect(row).not.toBeNull();
    expect(row!.textContent).toContain("tui");

    await act(async () => row!.click());
    await settle(60);

    expect(text()).toContain("Hello there");
    // The ring says "unmeasured" — this seam carries no token measurement, and
    // a zero would be a claim it never made.
    const ring = byTestId("context-ring");
    expect(ring?.getAttribute("data-state")).toBe("unmeasured");
  });

  it("renders a stored reasoning block as a collapsible trace step", async () => {
    routes = {
      "/api/workbench/sessions/abc": {
        status: 200,
        body: {
          id: "abc",
          messages: [
            {
              role: "assistant",
              content: "Done.",
              meta: {
                reasoning_content: "I should check the file first.",
                tool_calls: [{ function: { name: "terminal", arguments: '{"command":"ls"}' } }],
              },
            },
          ],
        },
      },
      "/api/workbench/sessions": sessionsPage([{ id: "abc", title: "T", source: "tui" }]),
      "/api/workbench/workspace/tree": { status: 200, body: { entries: [] } },
    };
    await renderPage();
    await act(async () => byTestId("workbench-source-cli")!.click());
    await act(async () => byTestId("workbench-session-abc")!.click());
    await settle(60);

    const controls = byTestId("workbench-trace-controls");
    expect(controls).not.toBeNull();
    expect(controls!.textContent).toContain("Trace:");
    expect(controls!.textContent).toContain("2 steps");
    expect(controls!.textContent).toContain("Expand all");
    expect(controls!.textContent).toContain("Collapse all");
    expect(all('[data-testid="workbench-trace-step-thinking"]')).toHaveLength(1);
    expect(all('[data-testid="workbench-trace-step-tool"]')).toHaveLength(1);

    // Expand all reveals the stored reasoning text.
    const expand = Array.from(controls!.querySelectorAll("button")).find(
      (button) => button.textContent === "Expand all",
    );
    expect(expand).not.toBeUndefined();
    await act(async () => expand!.click());
    expect(text()).toContain("I should check the file first.");
  });

  it("distinguishes a backend ERROR from NOT_AVAILABLE and quotes its code", async () => {
    routes = {
      "/api/workbench/sessions": {
        status: 500,
        text: JSON.stringify({ code: "WORKBENCH_SESSION_STORE_UNAVAILABLE", detail: "boom" }),
      },
      "/api/workbench/workspace/tree": { status: 200, body: { root: "/w", entries: [] } },
    };
    await renderPage();
    expect(all('[data-testid="workbench-panel-error"]').length).toBeGreaterThan(0);
    expect(text()).toContain("boom");
    expect(text()).toContain("WORKBENCH_SESSION_STORE_UNAVAILABLE");
    expect(text()).toContain("This folder is empty");
  });
});
