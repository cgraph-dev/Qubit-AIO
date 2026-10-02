// @vitest-environment jsdom
/**
 * /qubit-tasks — background task list (state filter), detail by
 * receipt/session id, cancel staged-then-settled with confirmation + retry,
 * and dismiss disabled-with-reason (never firing, never wired).
 */
import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const tasksMocks = vi.hoisted(() => ({
  listBackgroundTasks: vi.fn(),
  getBackgroundTask: vi.fn(),
  cancelBackgroundTask: vi.fn(),
  retryBackgroundTask: vi.fn(),
}));

vi.mock("@/lib/ocFamilyTasksApi", () => ({
  QUBIT_TASK_STATES: ["running", "ok", "failed", "blocked", "cancelled", "pending", "unknown"],
  TASKS_DISMISS_REFUSAL: {
    method: "tasks.dismiss",
    code: "TASKS_DISMISS_OPERATOR_REQUIRED",
    detail: "Qubit keeps no detached-task completion-delivery registry; nothing is dismissed",
    prerequisite: "an upstream detached-task/subagent completion-delivery registry; Qubit keeps none",
    counterpart: "the canonical read-only task ledger (`qubit tasks`)",
  },
  normalizeBackgroundTasks: (v: unknown) => v,
  normalizeBackgroundTaskDetail: (v: unknown) => v,
  ...tasksMocks,
}));

vi.mock("@/contexts/usePageHeader", () => ({
  usePageHeader: () => ({ setAfterTitle: vi.fn(), setEnd: vi.fn() }),
}));

let container: HTMLDivElement;
let root: Root;
(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

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

async function renderPage() {
  const [{ default: TasksPage }, { I18nProvider }] = await Promise.all([
    import("./TasksPage"),
    import("@/i18n"),
  ]);
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  const ui: ReactNode = (
    <I18nProvider>
      <MemoryRouter initialEntries={["/qubit-tasks"]}>
        <TasksPage />
      </MemoryRouter>
    </I18nProvider>
  );
  await act(async () => root.render(ui));
}

function setNativeValue(el: HTMLInputElement, value: string) {
  const proto = window.HTMLInputElement.prototype;
  Object.getOwnPropertyDescriptor(proto, "value")?.set?.call(el, value);
  el.dispatchEvent(new Event("input", { bubbles: true }));
}

function setNativeSelect(el: HTMLSelectElement, value: string) {
  el.value = value;
  el.dispatchEvent(new Event("change", { bubbles: true }));
}

function buttonInScope(scope: ParentNode, text: string): HTMLButtonElement | null {
  return (
    [...scope.querySelectorAll("button")].find(
      (b) => b.textContent?.trim() === text,
    ) ?? null
  );
}

const TASK_ROWS = [
  {
    id: "opencode:act-1",
    kind: "acp",
    state: "running",
    title: "Summarize inbox",
    sessionKey: "chat-1",
    progress: "drafting",
    progressPercent: 40,
  },
  {
    id: "schedules:7",
    kind: "cron",
    state: "ok",
    title: "Nightly digest",
    sessionKey: "chat-2",
  },
];

beforeEach(() => {
  Object.values(tasksMocks).forEach((fn) => fn.mockReset());
  tasksMocks.listBackgroundTasks.mockResolvedValue(TASK_ROWS);
  tasksMocks.getBackgroundTask.mockResolvedValue(null);
  tasksMocks.cancelBackgroundTask.mockResolvedValue({ ok: true });
  tasksMocks.retryBackgroundTask.mockResolvedValue({ ok: true });
  vi.stubGlobal(
    "ResizeObserver",
    class {
      disconnect() {}
      observe() {}
      unobserve() {}
    },
  );
  vi.stubGlobal("matchMedia", () => ({
    addEventListener() {},
    matches: false,
    media: "",
    removeEventListener() {},
  }));
});

afterEach(async () => {
  await act(async () => root?.unmount());
  container?.remove();
  document.body.querySelectorAll('[role="alertdialog"], [role="dialog"]').forEach((d) => d.remove());
  vi.unstubAllGlobals();
});

describe("TasksPage", () => {
  it("renders the background list with state filter and reported progress", async () => {
    await renderPage();
    await waitFor(() => container.textContent?.includes("Summarize inbox") ?? false);
    expect(container.textContent).toContain("Nightly digest");
    expect(container.textContent).toContain("40%");
    expect(container.textContent).toContain("drafting");
    const filter = container.querySelector(
      "#background-tasks-state",
    ) as HTMLSelectElement;
    expect(filter).not.toBeNull();
    expect(tasksMocks.listBackgroundTasks).toHaveBeenCalledWith(undefined);
    await act(async () => {
      setNativeSelect(filter, "running");
    });
    await waitFor(() => tasksMocks.listBackgroundTasks.mock.calls.length >= 2);
    expect(tasksMocks.listBackgroundTasks).toHaveBeenLastCalledWith({ state: "running" });
  });

  it("looks up one task by receipt/session id and shows progress + session", async () => {
    tasksMocks.getBackgroundTask.mockResolvedValue({
      id: "opencode:act-1",
      state: "running",
      title: "Summarize inbox",
      sessionKey: "chat-1",
      runId: "run-9",
      progress: "drafting",
      progressPercent: 40,
    });
    await renderPage();
    await waitFor(() => container.textContent?.includes("Summarize inbox") ?? false);
    setNativeValue(
      container.querySelector("#background-tasks-lookup") as HTMLInputElement,
      "run-9",
    );
    await act(async () => buttonInScope(container, "Look up")!.click());
    await waitFor(
      () => container.querySelector('[data-testid="background-task-detail"]') !== null,
    );
    const detail = container.querySelector('[data-testid="background-task-detail"]')!;
    expect(detail.textContent).toContain("run-9");
    expect(detail.textContent).toContain("40%");
    expect(tasksMocks.getBackgroundTask).toHaveBeenCalledWith("run-9");
  });

  it("cancel stages, settles through the confirm dialog, then reloads", async () => {
    tasksMocks.cancelBackgroundTask.mockImplementation(
      async (_target: unknown, confirm?: (detail: string) => Promise<boolean>) => {
        if (confirm) await confirm("staged: cancel receipt rc-9");
        return { ok: true };
      },
    );
    await renderPage();
    await waitFor(() => container.textContent?.includes("Summarize inbox") ?? false);
    // No target yet: both controls stay disabled.
    expect(buttonInScope(container, "Cancel")!.disabled).toBe(true);
    expect(buttonInScope(container, "Retry")!.disabled).toBe(true);

    setNativeValue(
      container.querySelector("#background-tasks-receipt") as HTMLInputElement,
      "rc-9",
    );
    await waitFor(() => buttonInScope(container, "Cancel")!.disabled === false);
    await act(async () => buttonInScope(container, "Cancel")!.click());
    // Staged: the broker confirm dialog opens with the staged detail.
    // (Radix AlertDialog renders role="alertdialog", not "dialog".)
    const DIALOG_SEL = '[role="alertdialog"], [role="dialog"]';
    await waitFor(() => document.querySelector(DIALOG_SEL) !== null);
    const dialog = document.querySelector(DIALOG_SEL)!;
    expect(dialog.textContent).toContain("staged: cancel receipt rc-9");
    await act(async () => buttonInScope(dialog, "Confirm")!.click());
    // Settled: the mutation ran with the receipt + confirm callback, then reloaded.
    await waitFor(() => tasksMocks.cancelBackgroundTask.mock.calls.length > 0);
    expect(tasksMocks.cancelBackgroundTask).toHaveBeenCalledWith(
      expect.objectContaining({ receiptId: "rc-9" }),
      expect.any(Function),
    );
    await waitFor(() => tasksMocks.listBackgroundTasks.mock.calls.length >= 2);
  });

  it("renders tasks.dismiss disabled with its verbatim reason and never fires", async () => {
    await renderPage();
    await waitFor(
      () =>
        container.textContent?.includes("TASKS_DISMISS_OPERATOR_REQUIRED") ?? false,
    );
    const row = container.querySelector(
      '[data-testid="refusal-TASKS_DISMISS_OPERATOR_REQUIRED"]',
    );
    expect(row).not.toBeNull();
    expect(row!.textContent).toContain(
      "Qubit keeps no detached-task completion-delivery registry; nothing is dismissed",
    );
    expect(row!.querySelector('a[href="/qubit-task-inbox"]')).not.toBeNull();
    const button = row!.querySelector("button") as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    await act(async () => button.click());
    expect(tasksMocks.cancelBackgroundTask).not.toHaveBeenCalled();
    expect(tasksMocks.retryBackgroundTask).not.toHaveBeenCalled();
  });
});
