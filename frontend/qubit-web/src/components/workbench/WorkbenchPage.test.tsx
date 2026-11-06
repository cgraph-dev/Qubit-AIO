// @vitest-environment jsdom
/**
 * The workbench's honesty contract, pinned end to end.
 *
 * The failure these tests exist to catch is the one a half-landed backend makes
 * easy: a 404 from `/api/workbench/*` being swallowed into an empty array, so
 * the page renders "no sessions" — a confident, false claim — instead of "this
 * backend is not mounted". Every test below fails if that regresses.
 *
 * The network is stubbed at `fetch` so the REAL data layer runs (URL building,
 * status mapping, shape parsing) rather than a mocked module.
 */
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import WorkbenchPage from "@/pages/WorkbenchPage";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

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
  // Stub BOTH bindings. The data layer prefers `window.fetch`, but a bare
  // jsdom environment resolves the global to jsdom's own `fetch`, not this
  // one, so patching only `window.fetch` left node's undici answering and the
  // real server's 404 leaking into the "live backend" cases.
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
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 300));
  });
}

function text(): string {
  return container.textContent ?? "";
}

function all(selector: string): HTMLElement[] {
  return Array.from(container.querySelectorAll<HTMLElement>(selector));
}

describe("workbench — backend not available", () => {
  it("says so for sessions AND workspace, instead of rendering empty lists", async () => {
    await renderPage();

    // Both panels render the explicit state...
    const unavailable = all('[data-testid="workbench-backend-not-available"]');
    expect(unavailable.length).toBeGreaterThanOrEqual(2);
    expect(text()).toContain("Backend not available");
    expect(text()).toContain("/api/workbench/sessions");

    // ...and NEITHER the "no sessions" claim nor an empty-entry list exists.
    expect(all('[data-testid="workbench-sessions-empty"]')).toHaveLength(0);
    expect(all('[data-testid="workbench-session-row"]')).toHaveLength(0);
    expect(all('[aria-label="Workbench sessions"]')).toHaveLength(0);
    expect(all('[aria-label="Workspace entries"]')).toHaveLength(0);
    // The workspace panel does not claim "this folder is empty" either — an
    // empty folder is a fact only a mounted backend can report.
    expect(text()).not.toContain("This folder is empty");
  });

  it("an attempted send against the missing route says the route is not mounted", async () => {
    await renderPage();
    const input = container.querySelector<HTMLTextAreaElement>('textarea[aria-label="Message"]');
    expect(input).not.toBeNull();

    await act(async () => {
      // Drive the controlled textarea through the native setter so React's
      // value tracker sees the change.
      const setter = Object.getOwnPropertyDescriptor(
        HTMLTextAreaElement.prototype,
        "value",
      )!.set;
      setter!.call(input, "hello?");
      input!.dispatchEvent(new Event("input", { bubbles: true }));
    });

    await act(async () => {
      container
        .querySelector<HTMLFormElement>('[data-testid="workbench-composer"]')!
        .dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    });
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 50));
    });

    // The POST was attempted and its 404 is reported as "not mounted", never as
    // a sent message.
    expect(calls.some((c) => c.includes("/api/workbench/turn"))).toBe(true);
    expect(
      container.querySelector('[data-testid="workbench-turn-not-available"]'),
    ).not.toBeNull();
    expect(container.querySelector('[data-testid="workbench-message-local-reply"]')).toBeNull();
    // The transcript did not fall back to "no messages yet" — the send never
    // reached a backend, so claiming the session is empty would be invented.
    expect(container.querySelector('[data-testid="workbench-transcript-empty"]')).toBeNull();
  });

  it("does not call the turn endpoint before a send is attempted", async () => {
    await renderPage();
    expect(calls.some((c) => c.includes("/api/workbench/turn"))).toBe(false);
  });
});

describe("workbench — live backend", () => {
  it("renders the sessions it was given, and can open one", async () => {
    routes = {
      "/api/workbench/sessions/abc": {
        status: 200,
        body: {
          id: "abc",
          title: "Workbench session",
          messages: [{ role: "user", content: "Hello there" }],
          prompt_tokens: 32_000,
          context_window: 128_000,
        },
      },
      "/api/workbench/sessions": {
        status: 200,
        body: {
          sessions: [
            { id: "abc", title: "Workbench session", message_count: 2, model: "openai/gpt-x" },
          ],
        },
      },
      "/api/workbench/workspace/tree": {
        status: 200,
        body: { root: "/w", entries: [{ name: "notes.md", path: "notes.md", kind: "file", size: 12 }] },
      },
    };

    await renderPage();
    expect(all('[data-testid="workbench-backend-not-available"]')).toHaveLength(0);

    const row = container.querySelector<HTMLElement>('[data-testid="workbench-session-abc"]');
    expect(row).not.toBeNull();
    await act(async () => row!.click());
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 20));
    });

    expect(text()).toContain("Hello there");
    // The ring reads the LAST prompt over the window: 25%.
    const ring = container.querySelector<HTMLElement>('[data-testid="context-ring"]');
    expect(ring?.getAttribute("data-state")).toBe("measured");
    expect(ring?.getAttribute("aria-label")).toContain("25% used");
  });

  it("distinguishes a backend ERROR from NOT_AVAILABLE", async () => {
    // An exact route: the stub matches by prefix, so this must not also
    // swallow `/api/workbench/...` reads as 500s.
    routes = {
      "/api/workbench/sessions": { status: 500, text: "boom" },
      "/api/workbench/workspace/tree": { status: 200, body: { root: "/w", entries: [] } },
    };
    await renderPage();
    expect(all('[data-testid="workbench-panel-error"]').length).toBeGreaterThan(0);
    expect(text()).toContain("boom");
    // The workspace route answered, so it reports an empty folder — a fact from
    // a mounted backend, not the "not mounted" claim.
    expect(text()).toContain("This folder is empty");
  });
});
