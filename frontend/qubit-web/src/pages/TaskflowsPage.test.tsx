// @vitest-environment jsdom
/**
 * TaskflowsPage — separate durable-flows page (route `/qubit-taskflows`).
 *
 * Mocked: `fetchJSON` only (real `runMutationWithConfirm` +
 * `ConfirmRequiredError`, so staged-then-settle is exercised for real).
 * `GET /api/cron/jobs` serves a bare array (the real `h_cron_jobs` shape).
 */
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const apiMocks = vi.hoisted(() => ({ fetchJSON: vi.fn() }));

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, fetchJSON: apiMocks.fetchJSON };
});

import { ConfirmRequiredError } from "@/lib/api";

let container: HTMLDivElement;
let root: Root;
(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

async function waitFor(cond: () => boolean, timeoutMs = 5000) {
  const start = Date.now();
  while (!cond()) {
    if (Date.now() - start > timeoutMs) throw new Error("waitFor: condition never became true");
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 20));
    });
  }
}

function click(label: string) {
  const el = document.querySelector(`button[aria-label="${label}"]`);
  if (!el) throw new Error(`button not rendered: ${label}`);
  el.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
}

function setInput(label: string, value: string) {
  const el = document.querySelector(`input[aria-label="${label}"]`);
  if (!el) throw new Error(`input not rendered: ${label}`);
  const input = el as HTMLInputElement;
  const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value")?.set;
  setter?.call(input, value);
  input.dispatchEvent(new Event("input", { bubbles: true }));
  input.dispatchEvent(new Event("change", { bubbles: true }));
}

async function renderPage() {
  const { default: TaskflowsPage } = await import("./TaskflowsPage");
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await act(async () => {
    root.render(<TaskflowsPage />);
  });
}

const STANDING_JOB = {
  id: 7,
  name: "morning-ops",
  prompt: "Run the morning ops routine",
  schedule: { expr: "0 8 * * 1-5" },
  enabled: true,
  last_status: "ok",
};

const ONESHOT_JOB = {
  id: 9,
  name: "remind-once",
  prompt: "Ping the owner",
  schedule: { run_at: "2026-10-03T08:00:00" },
  enabled: true,
};

function mockList(jobs: unknown[] = [STANDING_JOB, ONESHOT_JOB]) {
  return async (url: string) => {
    if (url === "/api/cron/jobs") return jobs;
    if (url === "/api/qubit/flows/hooks")
      return {
        hooks: [
          { name: "session-memory", enabled: true },
          { name: "command-logger", enabled: false },
        ],
      };
    throw new Error(`unexpected GET: ${url}`);
  };
}

beforeEach(() => {
  apiMocks.fetchJSON.mockReset();
  apiMocks.fetchJSON.mockImplementation(mockList());
});

afterEach(async () => {
  await act(async () => {
    root?.unmount();
  });
  container?.remove();
  vi.resetModules();
});

describe("TaskflowsPage", () => {
  it("lists standing orders and one-shots separately from the cron backend", async () => {
    await renderPage();
    await waitFor(() => document.querySelector('[aria-label="flow-row-7"]') !== null);
    await waitFor(() => document.querySelector('[aria-label="flow-row-9"]') !== null);
    expect(document.querySelector('[aria-label="standing-orders"]')?.textContent).toMatch("morning-ops");
    expect(document.querySelector('[aria-label="one-shots"]')?.textContent).toMatch("remind-once");
    expect(apiMocks.fetchJSON.mock.calls.some((c) => String(c[0]) === "/api/cron/jobs")).toBe(true);
  });

  it("states the durable-flows-only scope and links the task inbox", async () => {
    await renderPage();
    await waitFor(() => document.querySelector('[aria-label="taskflows-scope-notice"]') !== null);
    const notice = document.querySelector('[aria-label="taskflows-scope-notice"]')?.textContent ?? "";
    expect(notice).toMatch("Durable flows only");
    const hrefs = [...document.querySelectorAll('[aria-label="taskflows-scope-notice"] a')].map((a) =>
      a.getAttribute("href"),
    );
    expect(hrefs).toContain("/qubit-task-inbox");
  });

  it("renders hooks with live guard states and staged enable", async () => {
    await renderPage();
    await waitFor(() => document.querySelector('[aria-label="hook-row-session-memory"]') !== null);
    await waitFor(
      () => document.querySelector('[aria-label="hook-state-command-logger"]')?.textContent === "disabled",
    );
    expect(document.querySelector('[aria-label="hook-state-session-memory"]')?.textContent).toBe("enabled");
    const enable = document.querySelector(
      'button[aria-label="hook-enable-command-logger"]',
    ) as HTMLButtonElement;
    expect(enable.disabled).toBe(false);
    expect(enable.title).toMatch("Enable this hook");
    const disableOn = document.querySelector(
      'button[aria-label="hook-disable-session-memory"]',
    ) as HTMLButtonElement;
    expect(disableOn.disabled).toBe(false);
  });

  it("deletes assert staged-then-settle", async () => {
    apiMocks.fetchJSON.mockImplementation(async (url: string, init?: RequestInit) => {
      if (url === "/api/cron/jobs") return [STANDING_JOB];
      if (url === "/api/cron/jobs/7" && init?.method === "DELETE") {
        throw new ConfirmRequiredError("confirm delete", "stage-delete-7", async () => ({ ok: true }));
      }
      throw new Error(`unexpected: ${init?.method ?? "GET"} ${url}`);
    });
    await renderPage();
    await waitFor(() => document.querySelector('[aria-label="flow-delete-7"]') !== null);
    await act(async () => {
      click("flow-delete-7");
    });
    await waitFor(() => document.querySelector('[aria-label="staged-op-confirm"]') !== null);
    expect(document.querySelector('[aria-label="op-result"]')).toBeNull();
    await act(async () => {
      click("confirm-staged-op");
    });
    await waitFor(() => document.querySelector('[aria-label="op-result"]') !== null);
    const deletes = apiMocks.fetchJSON.mock.calls.filter(
      (c) => String(c[0]) === "/api/cron/jobs/7" && (c[1] as RequestInit)?.method === "DELETE",
    );
    expect(deletes.length).toBeGreaterThanOrEqual(1);
  });

  it("creates assert staged-then-settle", async () => {
    apiMocks.fetchJSON.mockImplementation(async (url: string, init?: RequestInit) => {
      if (url === "/api/cron/jobs" && (init?.method ?? "GET") === "GET") return [STANDING_JOB];
      if (url === "/api/cron/jobs" && init?.method === "POST") {
        const body = JSON.parse(String(init?.body ?? "{}"));
        expect(body.name).toBe("evening-ops");
        expect(body.schedule).toBe("0 18 * * *");
        throw new ConfirmRequiredError("confirm create", "stage-create-1", async () => ({
          ok: true,
          id: 11,
        }));
      }
      throw new Error(`unexpected: ${init?.method ?? "GET"} ${url}`);
    });
    await renderPage();
    await waitFor(() => document.querySelector('[aria-label="flow-create-submit"]') !== null);
    await act(async () => {
      setInput("flow-create-name", "evening-ops");
      setInput("flow-create-schedule", "0 18 * * *");
      setInput("flow-create-prompt", "Run the evening ops routine");
    });
    await act(async () => {
      click("flow-create-submit");
    });
    await waitFor(() => document.querySelector('[aria-label="staged-op-confirm"]') !== null);
    expect(document.querySelector('[aria-label="op-result"]')).toBeNull();
    await act(async () => {
      click("confirm-staged-op");
    });
    await waitFor(() => document.querySelector('[aria-label="op-result"]') !== null);
  });

  it("toggle sends the enabled flag on the update route", async () => {
    apiMocks.fetchJSON.mockImplementation(async (url: string, init?: RequestInit) => {
      if (url === "/api/cron/jobs") return [STANDING_JOB];
      if (url === "/api/cron/jobs/7" && init?.method === "PUT") {
        const body = JSON.parse(String(init?.body ?? "{}"));
        expect(body.enabled).toBe(false);
        throw new ConfirmRequiredError("confirm update", "stage-update-7", async () => ({
          ok: true,
          enabled: false,
        }));
      }
      throw new Error(`unexpected: ${init?.method ?? "GET"} ${url}`);
    });
    await renderPage();
    await waitFor(() => document.querySelector('[aria-label="flow-toggle-7"]') !== null);
    await act(async () => {
      click("flow-toggle-7");
    });
    await waitFor(() => document.querySelector('[aria-label="staged-op-confirm"]') !== null);
    await act(async () => {
      click("confirm-staged-op");
    });
    await waitFor(() => document.querySelector('[aria-label="op-result"]') !== null);
  });
});
