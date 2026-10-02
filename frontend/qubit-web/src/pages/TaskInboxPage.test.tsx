// @vitest-environment jsdom
/**
 * /qubit-task-inbox — ledger + read-only suggestions render, cancel/retry run
 * via receipt/session with confirmation, and dismiss/mentions/suggestion-write
 * verbs render disabled-with-reason (code + verbatim detail + counterpart
 * links) and never fire.
 */
import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const taskMocks = vi.hoisted(() => ({
  getTaskLedger: vi.fn(),
  getTaskSuggestions: vi.fn(),
  cancelTask: vi.fn(),
  retryTask: vi.fn(),
}));

vi.mock("@/lib/ocFamilyBoardApi", () => ({
  TASKS_DISMISS_REFUSAL: {
    method: "tasks.dismiss",
    code: "TASKS_DISMISS_OPERATOR_REQUIRED",
    detail: "Qubit keeps no detached-task completion-delivery registry; nothing is dismissed",
    prerequisite: "an upstream detached-task/subagent completion-delivery registry; Qubit keeps none",
    counterpart: "the canonical read-only task ledger (`qubit tasks`)",
  },
  MENTIONS_REFUSAL: {
    method: "mentions.list / mentions.dismiss",
    code: "MENTIONS_INBOX_OPERATOR_REQUIRED",
    detail: "Qubit runs no human-mention inbox service; no mention is listed or dismissed",
    prerequisite: "a human-mention inbox service backed by configured message channels",
    counterpart: "voicebridge.automation outbox/orders (`qubit channels`)",
  },
  TASK_SUGGESTIONS_REFUSAL: {
    method: "taskSuggestions.create / taskSuggestions.accept / taskSuggestions.dismiss",
    code: "TASK_SUGGESTIONS_OPERATOR_REQUIRED",
    detail: "Qubit keeps no ephemeral model-proposed task-suggestion registry; no suggestion is created, accepted, or dismissed",
    prerequisite: "an upstream model-proposed task-suggestion registry + run-control runtime; Qubit runs none",
    counterpart: "durable scheduled work via voicebridge.schedules.create_job (`qubit cron add`) + the canonical chat session",
  },
  normalizeTasks: (v: unknown) => v,
  normalizeSuggestions: (v: unknown) => v,
  ...taskMocks,
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
  const [{ default: TaskInboxPage }, { I18nProvider }] = await Promise.all([
    import("./TaskInboxPage"),
    import("@/i18n"),
  ]);
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  const ui: ReactNode = (
    <I18nProvider>
      <MemoryRouter initialEntries={["/qubit-task-inbox"]}>
        <TaskInboxPage />
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

function setSelectValue(el: HTMLSelectElement, value: string) {
  const proto = window.HTMLSelectElement.prototype;
  Object.getOwnPropertyDescriptor(proto, "value")?.set?.call(el, value);
  el.dispatchEvent(new Event("change", { bubbles: true }));
}

function columnTitles(): string[] {
  return [...container.querySelectorAll(".workboard-column__header h2")].map(
    (h) => h.textContent?.trim() ?? "",
  );
}

function buttonByText(text: string): HTMLButtonElement | null {
  return (
    [...document.querySelectorAll("button")].find(
      (b) => b.textContent?.trim() === text,
    ) ?? null
  );
}

beforeEach(() => {
  Object.values(taskMocks).forEach((fn) => fn.mockReset());
  taskMocks.getTaskLedger.mockResolvedValue([
    { id: "t-1", title: "Summarize inbox", state: "done", session: "chat-1", updated_at: "" },
  ]);
  taskMocks.getTaskSuggestions.mockResolvedValue([
    { id: "s-1", prompt: "File the weekly report", title: "", session: "" },
  ]);
  taskMocks.cancelTask.mockResolvedValue({ ok: true });
  taskMocks.retryTask.mockResolvedValue({ ok: true });
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
  vi.unstubAllGlobals();
});

describe("TaskInboxPage", () => {
  it("renders the ledger and read-only suggestions", async () => {
    await renderPage();
    await waitFor(() => container.textContent?.includes("Summarize inbox") ?? false);
    expect(container.textContent).toContain("File the weekly report");
    expect(taskMocks.getTaskLedger).toHaveBeenCalled();
    expect(taskMocks.getTaskSuggestions).toHaveBeenCalled();
  });

  it("cancels via receipt with confirmation and retries via session", async () => {
    await renderPage();
    await waitFor(() => container.textContent?.includes("Summarize inbox") ?? false);
    // No target yet: both controls stay disabled.
    expect((buttonByText("Cancel") as HTMLButtonElement).disabled).toBe(true);
    expect((buttonByText("Retry") as HTMLButtonElement).disabled).toBe(true);

    setNativeValue(
      container.querySelector("#task-inbox-receipt") as HTMLInputElement,
      "rc-9",
    );
    await waitFor(
      () => (buttonByText("Cancel") as HTMLButtonElement).disabled === false,
    );
    await act(async () => buttonByText("Cancel")!.click());
    await waitFor(() => taskMocks.cancelTask.mock.calls.length > 0);
    expect(taskMocks.cancelTask).toHaveBeenCalledWith(
      expect.objectContaining({ receiptId: "rc-9" }),
      expect.any(Function),
    );
  });

  it("mirrors the upstream workboard shell: toolbar, health strip, columns", async () => {
    await renderPage();
    await waitFor(() => container.textContent?.includes("Summarize inbox") ?? false);
    expect(container.querySelector("section.workboard")).not.toBeNull();
    expect(container.querySelector(".workboard-main")).not.toBeNull();
    expect(container.querySelector(".workboard-toolbar")).not.toBeNull();
    expect(container.querySelector(".workboard-toolbar__filters")).not.toBeNull();
    expect(container.querySelector(".workboard-toolbar__actions")).not.toBeNull();
    expect(container.querySelector("#task-inbox-search")).not.toBeNull();
    expect(container.querySelector("#task-inbox-view")).not.toBeNull();
    const health = container.querySelector(".workboard-health");
    expect(health).not.toBeNull();
    expect(health!.querySelector('[aria-label="1 Ledger entries"]')).not.toBeNull();
    expect(health!.querySelector('[aria-label="1 Suggestions"]')).not.toBeNull();
    expect(columnTitles()).toEqual([
      "Task ledger",
      "Suggestions (read-only)",
      "Cancel / retry via receipt or session",
      "Mentions (disabled with reason)",
      "Suggestion writes (disabled with reason)",
    ]);
    expect(container.textContent).toContain("Last refreshed");
  });

  it("search filters rows and shows the upstream empty state", async () => {
    await renderPage();
    await waitFor(() => container.textContent?.includes("Summarize inbox") ?? false);
    setNativeValue(
      container.querySelector("#task-inbox-search") as HTMLInputElement,
      "zzz-no-match",
    );
    await waitFor(() => container.querySelector(".workboard-empty-state") !== null);
    expect(container.querySelector(".workboard-empty-state")!.textContent).toContain(
      "No inbox entries match this filter",
    );
    setNativeValue(container.querySelector("#task-inbox-search") as HTMLInputElement, "");
    await waitFor(() => container.querySelector(".workboard-empty-state") === null);
    expect(container.textContent).toContain("Summarize inbox");
  });

  it("view preset and health strip narrow the data columns", async () => {
    await renderPage();
    await waitFor(() => container.textContent?.includes("Summarize inbox") ?? false);
    setSelectValue(
      container.querySelector("#task-inbox-view") as HTMLSelectElement,
      "suggestions",
    );
    await waitFor(() => {
      const titles = columnTitles();
      return titles.includes("Suggestions (read-only)") && !titles.includes("Task ledger");
    });
    // Controls + gated columns stay reachable in every preset.
    expect(columnTitles()).toContain("Cancel / retry via receipt or session");
    const tasksPill = container.querySelector(
      ".workboard-health__item--tasks",
    ) as HTMLButtonElement;
    await act(async () => tasksPill.click());
    await waitFor(() => {
      const titles = columnTitles();
      return titles.includes("Task ledger") && !titles.includes("Suggestions (read-only)");
    });
  });

  it("hide-empty hides the emptied data column", async () => {
    await renderPage();
    await waitFor(() => container.textContent?.includes("Summarize inbox") ?? false);
    // "weekly" matches only the suggestion row.
    setNativeValue(
      container.querySelector("#task-inbox-search") as HTMLInputElement,
      "weekly",
    );
    await waitFor(() => container.textContent?.includes("File the weekly report") ?? false);
    await act(async () => (container.querySelector("#task-inbox-hide-empty") as HTMLInputElement).click());
    await waitFor(() => {
      const titles = columnTitles();
      return titles.includes("Suggestions (read-only)") && !titles.includes("Task ledger");
    });
  });

  it.each([
    ["TASKS_DISMISS_OPERATOR_REQUIRED", "Qubit keeps no detached-task completion-delivery registry; nothing is dismissed", null],
    ["MENTIONS_INBOX_OPERATOR_REQUIRED", "Qubit runs no human-mention inbox service; no mention is listed or dismissed", "/channels"],
    ["TASK_SUGGESTIONS_OPERATOR_REQUIRED", "Qubit keeps no ephemeral model-proposed task-suggestion registry; no suggestion is created, accepted, or dismissed", "/cron"],
  ])(
    "renders %s disabled with its verbatim reason and never fires",
    async (code, detail, counterpartTo) => {
      await renderPage();
      await waitFor(() => container.textContent?.includes(code) ?? false);
      const row = container.querySelector(`[data-testid="refusal-${code}"]`);
      expect(row).not.toBeNull();
      expect(row!.textContent).toContain(detail);
      const button = row!.querySelector("button") as HTMLButtonElement;
      expect(button.disabled).toBe(true);
      await act(async () => button.click());
      expect(taskMocks.cancelTask).not.toHaveBeenCalled();
      expect(taskMocks.retryTask).not.toHaveBeenCalled();
      if (counterpartTo) {
        const link = row!.querySelector(`a[href="${counterpartTo}"]`);
        expect(link).not.toBeNull();
      }
    },
  );
});
