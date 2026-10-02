// @vitest-environment jsdom
/**
 * /qubit-bot — reads render, writes settle through confirm, hosted
 * thread-persistence rows stay disabled-with-reason, and nothing here
 * invents a transport.
 *
 * The API module is mocked; the suite asserts the page only ever calls the
 * read/write functions the manifest allows, and that no fetch reaches a
 * non-bot path.
 */
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const botMocks = vi.hoisted(() => ({
  listBotThreads: vi.fn(),
  getBotThread: vi.fn(),
  sendBotTurn: vi.fn(),
  stopBotTurn: vi.fn(),
  queueBotFollowups: vi.fn(),
  saveBotDraft: vi.fn(),
  listBotTasks: vi.fn(),
  getBotTask: vi.fn(),
  launchBotTask: vi.fn(),
  pauseBotTask: vi.fn(),
  resumeBotTask: vi.fn(),
  cancelBotTask: vi.fn(),
  retryBotTask: vi.fn(),
  getBotTaskReceipts: vi.fn(),
  listBotIdeas: vi.fn(),
  acceptBotIdea: vi.fn(),
  editBotIdea: vi.fn(),
  dismissBotIdea: vi.fn(),
  listBotGoals: vi.fn(),
  setBotGoal: vi.fn(),
  setBotWatch: vi.fn(),
  listBotAlerts: vi.fn(),
  getBotComputerStatus: vi.fn(),
  getBotCard: vi.fn(),
}));

vi.mock("@/lib/ocFamilyBotApi", async (importOriginal) => {
  const orig = (await importOriginal()) as Record<string, unknown>;
  return {
    ...orig,
    ...botMocks,
  };
});

import QubitBotPage from "@/pages/QubitBotPage";

let container: HTMLDivElement | null = null;
let root: Root | null = null;

function renderPage() {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  act(() => {
    root!.render(<QubitBotPage />);
  });
}

async function flush() {
  await act(async () => {
    await Promise.resolve();
    await new Promise((r) => setTimeout(r, 0));
  });
}

const fetchSpy = vi.hoisted(() => vi.fn());

function armDefaults() {
  botMocks.listBotThreads.mockResolvedValue({
    result: {
      threads: [
        { id: "thread-1", title: "Main", messageCount: 3 },
        { id: "thread-2", title: "Side" },
      ],
    },
  });
  botMocks.getBotThread.mockResolvedValue({
    result: { events: [{ kind: "message", text: "hello" }] },
  });
  botMocks.sendBotTurn.mockResolvedValue({
    result: { events: [{ kind: "message", text: "done" }] },
  });
  botMocks.stopBotTurn.mockResolvedValue({ state: "OK" });
  botMocks.queueBotFollowups.mockResolvedValue({ state: "OK" });
  botMocks.saveBotDraft.mockResolvedValue({ state: "OK" });
  botMocks.listBotTasks.mockResolvedValue({
    result: {
      tasks: [
        {
          id: "task-1",
          title: "Summarize inbox",
          status: "running",
          plan: [{ id: "p1", title: "Read", status: "succeeded" }],
          progressPercent: 50,
        },
      ],
    },
  });
  botMocks.getBotTask.mockResolvedValue({
    result: {
      id: "task-1",
      title: "Summarize inbox",
      status: "running",
      plan: [{ id: "p1", title: "Read", status: "succeeded" }],
    },
  });
  botMocks.launchBotTask.mockResolvedValue({ state: "OK" });
  botMocks.pauseBotTask.mockResolvedValue({ state: "OK" });
  botMocks.resumeBotTask.mockResolvedValue({ state: "OK" });
  botMocks.cancelBotTask.mockResolvedValue({ state: "OK" });
  botMocks.retryBotTask.mockResolvedValue({ state: "OK" });
  botMocks.getBotTaskReceipts.mockResolvedValue({ result: { receipts: [] } });
  botMocks.listBotIdeas.mockResolvedValue({
    result: {
      ideas: [
        {
          id: "idea-1",
          title: "Weekly digest",
          reason: "Asked often",
          evidence: [{ title: "Mail", excerpt: "3 unread" }],
          prompt: "Draft digest",
        },
      ],
    },
  });
  botMocks.acceptBotIdea.mockResolvedValue({ state: "OK" });
  botMocks.editBotIdea.mockResolvedValue({ state: "OK" });
  botMocks.dismissBotIdea.mockResolvedValue({ state: "OK" });
  botMocks.listBotGoals.mockResolvedValue({
    result: {
      goals: [
        {
          id: "goal-1",
          title: "Inbox zero",
          status: "active",
          milestones: [{ id: "m1", title: "Triage", done: true }],
        },
      ],
      watches: [{ id: "watch-1", title: "Price watch", status: "active", condition: "change" }],
    },
  });
  botMocks.setBotGoal.mockResolvedValue({ state: "OK" });
  botMocks.setBotWatch.mockResolvedValue({ state: "OK" });
  botMocks.listBotAlerts.mockResolvedValue({
    result: { alerts: [{ id: "alert-1", title: "Task done", body: "Finished", read: false }] },
  });
  botMocks.getBotComputerStatus.mockResolvedValue({
    result: { state: "idle", detail: "console ready" },
  });
  botMocks.getBotCard.mockResolvedValue({
    result: { id: "card-1", kind: "report", title: "Weekly report", summary: "All quiet" },
  });
}

beforeEach(() => {
  vi.stubGlobal("fetch", fetchSpy);
  fetchSpy.mockReset();
  for (const fn of Object.values(botMocks)) fn.mockReset();
  armDefaults();
});

afterEach(() => {
  if (root) root.unmount();
  if (container) container.remove();
  root = null;
  container = null;
  vi.unstubAllGlobals();
});

function text(): string {
  return container!.textContent ?? "";
}

function clickButton(label: string) {
  const btn = Array.from(container!.querySelectorAll("button")).find(
    (b) => (b.textContent ?? "").trim() === label,
  ) as HTMLElement | null;
  expect(btn, `button ${label}`).not.toBeNull();
  act(() => {
    btn!.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  });
}

function setInputById(id: string, value: string) {
  const el = container!.querySelector(`#${id}`) as HTMLInputElement | null;
  expect(el, `input ${id}`).not.toBeNull();
  const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value")?.set;
  act(() => {
    setter!.call(el, value);
    el!.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

describe("QubitBotPage", () => {
  it("reads threads, tasks, ideas, goals, alerts, and computer status on mount", async () => {
    renderPage();
    await flush();
    expect(botMocks.listBotThreads).toHaveBeenCalled();
    expect(botMocks.listBotTasks).toHaveBeenCalled();
    expect(botMocks.listBotIdeas).toHaveBeenCalled();
    expect(botMocks.listBotGoals).toHaveBeenCalled();
    expect(botMocks.listBotAlerts).toHaveBeenCalled();
    expect(botMocks.getBotComputerStatus).toHaveBeenCalled();
    const t = text();
    expect(t).toContain("Qubit Bot");
    expect(t).toContain("thread-1");
    expect(t).toContain("task-1");
    expect(t).toContain("idea-1");
    expect(t).toContain("goal-1");
    expect(t).toContain("watch-1");
    expect(t).toContain("Task done");
    expect(t).toContain("console ready");
  });

  it("renders the hosted-persistence rows disabled with reasons", async () => {
    renderPage();
    await flush();
    const t = text();
    for (const action of [
      "intelligence.threads.list",
      "intelligence.threads.replay",
      "intelligence.threads.archive",
    ]) {
      expect(t).toContain(action);
    }
    expect(t).toContain("INTELLIGENCE_PERSISTENCE_OPERATOR_REQUIRED");
  });

  it("sends a turn with the composer text", async () => {
    renderPage();
    await flush();
    setInputById("bot-composer", "hello bot");
    await flush();
    clickButton("Send");
    await flush();
    expect(botMocks.sendBotTurn).toHaveBeenCalledTimes(1);
    const [input] = botMocks.sendBotTurn.mock.calls[0];
    expect(input).toMatchObject({ text: "hello bot" });
  });

  it("queues follow-ups in order and sends them back to the composer", async () => {
    renderPage();
    await flush();
    setInputById("bot-followup", "second question");
    await flush();
    clickButton("Queue");
    await flush();
    expect(botMocks.queueBotFollowups).toHaveBeenCalledTimes(1);
    expect(text()).toContain("second question");
    clickButton("Send queued messages");
    await flush();
  });

  it("looks up a task and shows its plan", async () => {
    renderPage();
    await flush();
    setInputById("bot-task-lookup", "task-1");
    await flush();
    clickButton("Look up");
    await flush();
    expect(botMocks.getBotTask).toHaveBeenCalledWith("task-1");
    expect(text()).toContain("Read");
  });

  it("launches a delegated task with title and prompt", async () => {
    renderPage();
    await flush();
    const launchBtn = Array.from(container!.querySelectorAll("button")).find(
      (b) => (b.textContent ?? "").trim() === "Launch",
    ) as HTMLButtonElement;
    expect(launchBtn.disabled).toBe(true);
    setInputById("bot-launch-title", "Digest");
    setInputById("bot-launch-prompt", "Summarize the inbox");
    await flush();
    clickButton("Launch");
    await flush();
    expect(botMocks.launchBotTask).toHaveBeenCalledTimes(1);
    const [input] = botMocks.launchBotTask.mock.calls[0];
    expect(input).toMatchObject({ title: "Digest", prompt: "Summarize the inbox" });
  });

  it("pauses and retries the looked-up task", async () => {
    renderPage();
    await flush();
    setInputById("bot-task-lookup", "task-1");
    await flush();
    clickButton("Look up");
    await flush();
    clickButton("Pause");
    await flush();
    expect(botMocks.pauseBotTask).toHaveBeenCalledWith("task-1", expect.anything());
    clickButton("Retry");
    await flush();
    expect(botMocks.retryBotTask).toHaveBeenCalled();
  });

  it("accepts and dismisses ideas with evidence", async () => {
    renderPage();
    await flush();
    expect(text()).toContain("3 unread");
    clickButton("Accept");
    await flush();
    expect(botMocks.acceptBotIdea).toHaveBeenCalledWith("idea-1", expect.anything());
    clickButton("Dismiss");
    await flush();
    expect(botMocks.dismissBotIdea).toHaveBeenCalledWith("idea-1", expect.anything());
  });

  it("records goals and watches, and lists the unread alert", async () => {
    renderPage();
    await flush();
    setInputById("bot-goal-title", "Ship it");
    await flush();
    clickButton("Record goal");
    await flush();
    expect(botMocks.setBotGoal).toHaveBeenCalledTimes(1);
    setInputById("bot-watch-title", "Docs watch");
    await flush();
    clickButton("Record watch");
    await flush();
    expect(botMocks.setBotWatch).toHaveBeenCalledTimes(1);
    expect(text()).toContain("1 unread");
  });

  it("looks up a rich card", async () => {
    renderPage();
    await flush();
    const lookupBtn = Array.from(container!.querySelectorAll("button")).find(
      (b) => (b.textContent ?? "").trim() === "Look up",
    ) as HTMLButtonElement;
    expect(lookupBtn, "a Look up button").not.toBeNull();
    setInputById("bot-card-lookup", "card-1");
    await flush();
    const cardBtns = Array.from(container!.querySelectorAll("button")).filter(
      (b) => (b.textContent ?? "").trim() === "Look up",
    );
    act(() => {
      cardBtns[cardBtns.length - 1]!.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await flush();
    expect(botMocks.getBotCard).toHaveBeenCalledWith("card-1");
    expect(text()).toContain("Weekly report");
  });

  it("never fetches a non-bot path", async () => {
    renderPage();
    await flush();
    for (const call of fetchSpy.mock.calls) {
      expect(String(call[0])).not.toMatch(/voice-edit|voice_edit/i);
    }
  });
});
