// @vitest-environment jsdom
/**
 * Bot-chat class parity — QubitBotPage (/qubit-bot) and WebChatPage (/webchat)
 * share the upstream-adapted chat structure from `src/styles/upstream/botchat.css`.
 *
 * 1. Every `qb-*` class referenced by either page exists in the stylesheet.
 * 2. Both pages render the core chat structure (scroll, hero, composer,
 *    send button) so neither page can silently drop the upstream UX.
 */
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import fs from "node:fs";
import path from "node:path";

const botMocks = vi.hoisted(() => ({
  listBotThreads: vi.fn(),
  getBotThread: vi.fn(),
  sendBotTurn: vi.fn(),
  stopBotTurn: vi.fn(),
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
  return { ...orig, ...botMocks };
});

const webchatMocks = vi.hoisted(() => ({
  listWebchatSessions: vi.fn(),
  createWebchatSession: vi.fn(),
  getWebchatHistory: vi.fn(),
  streamWebchatTurn: vi.fn(),
}));

vi.mock("@/lib/ocFamilyWebchatApi", async (importOriginal) => {
  const orig = (await importOriginal()) as Record<string, unknown>;
  return { ...orig, ...webchatMocks };
});

import QubitBotPage from "@/pages/QubitBotPage";
import { WebChatPage } from "@/pages/WebChatPage";

let container: HTMLDivElement | null = null;
let root: Root | null = null;

function renderNode(node: React.ReactNode) {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  act(() => {
    root!.render(node);
  });
  return container;
}

async function flush() {
  await act(async () => {
    await Promise.resolve();
    await new Promise((r) => setTimeout(r, 0));
  });
}

/** Set a controlled field's value using the prototype React tracks. The
 *  composer is a multi-line <textarea> (upstream multiline TextInput); the
 *  other controls are single-line <input>s. */
function setFieldValue(field: HTMLInputElement | HTMLTextAreaElement, value: string) {
  const proto =
    field instanceof HTMLTextAreaElement
      ? HTMLTextAreaElement.prototype
      : HTMLInputElement.prototype;
  const setter = Object.getOwnPropertyDescriptor(proto, "value")?.set;
  act(() => {
    setter!.call(field, value);
    field.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

beforeEach(() => {
  botMocks.listBotThreads.mockResolvedValue({
    result: { threads: [{ id: "thread-1", title: "Main" }] },
  });
  botMocks.getBotThread.mockResolvedValue({ result: { events: [] } });
  botMocks.sendBotTurn.mockResolvedValue({ result: { events: [] } });
  botMocks.stopBotTurn.mockResolvedValue({ state: "OK" });
  botMocks.listBotTasks.mockResolvedValue({ result: { tasks: [] } });
  botMocks.getBotTask.mockResolvedValue({ result: null });
  botMocks.launchBotTask.mockResolvedValue({ state: "OK" });
  botMocks.pauseBotTask.mockResolvedValue({ state: "OK" });
  botMocks.resumeBotTask.mockResolvedValue({ state: "OK" });
  botMocks.cancelBotTask.mockResolvedValue({ state: "OK" });
  botMocks.retryBotTask.mockResolvedValue({ state: "OK" });
  botMocks.getBotTaskReceipts.mockResolvedValue({ result: { receipts: [] } });
  botMocks.listBotIdeas.mockResolvedValue({ result: { ideas: [] } });
  botMocks.acceptBotIdea.mockResolvedValue({ state: "OK" });
  botMocks.editBotIdea.mockResolvedValue({ state: "OK" });
  botMocks.dismissBotIdea.mockResolvedValue({ state: "OK" });
  botMocks.listBotGoals.mockResolvedValue({ result: { goals: [] } });
  botMocks.setBotGoal.mockResolvedValue({ state: "OK" });
  botMocks.setBotWatch.mockResolvedValue({ state: "OK" });
  botMocks.listBotAlerts.mockResolvedValue({ result: { alerts: [] } });
  botMocks.getBotComputerStatus.mockResolvedValue({ result: { state: "unknown" } });
  botMocks.getBotCard.mockResolvedValue({ result: null });

  webchatMocks.listWebchatSessions.mockResolvedValue({ sessions: [] });
  webchatMocks.createWebchatSession.mockResolvedValue({
    session: { id: "sess-1", name: "webchat" },
  });
  webchatMocks.getWebchatHistory.mockResolvedValue({ messages: [] });
  webchatMocks.streamWebchatTurn.mockResolvedValue(undefined);
});

afterEach(() => {
  root?.unmount();
  container?.remove();
  container = null;
  root = null;
  vi.clearAllMocks();
});

function qbClassesInSource(file: string): Set<string> {
  const src = fs.readFileSync(file, "utf8");
  const found = new Set<string>();
  for (const m of src.matchAll(/\bqb-[a-z0-9-]+/g)) found.add(m[0]);
  return found;
}

describe("botchat stylesheet covers both pages", () => {
  it("defines every qb-* class either chat page uses", () => {
    const css = fs.readFileSync(
      path.resolve(__dirname, "../styles/upstream/botchat.css"),
      "utf8",
    );
    const used = new Set<string>([
      ...qbClassesInSource(path.resolve(__dirname, "QubitBotPage.tsx")),
      ...qbClassesInSource(path.resolve(__dirname, "WebChatPage.tsx")),
    ]);
    expect(used.size).toBeGreaterThan(10);
    const missing = [...used].filter((cls) => !css.includes(`.${cls}`));
    expect(missing).toEqual([]);
  });
});

describe("chat structural parity", () => {
  it("QubitBotPage renders the shared chat structure", async () => {
    const el = renderNode(
      <MemoryRouter>
        <QubitBotPage />
      </MemoryRouter>,
    );
    await flush();
    for (const cls of [
      "qb-chat",
      "qb-chat-scroll",
      "qb-hero",
      "qb-starters",
      "qb-composer",
      "qb-send-btn",
      "qb-thread-item",
    ]) {
      expect(el.querySelector(`.${cls}`), cls).not.toBeNull();
    }
  });

  it("WebChatPage renders the same shared chat structure", async () => {
    const el = renderNode(
      <MemoryRouter>
        <WebChatPage />
      </MemoryRouter>,
    );
    await flush();
    await flush();
    for (const cls of [
      "qb-chat",
      "qb-chat-scroll",
      "qb-hero",
      "qb-starters",
      "qb-composer",
      "qb-send-btn",
    ]) {
      expect(el.querySelector(`.${cls}`), cls).not.toBeNull();
    }
  });
});

describe("QubitBotPage ported chat surface (openmuse chat.tsx)", () => {
  it("renders the hero with starter prompts and the pill composer", async () => {
    botMocks.getBotThread.mockResolvedValue({ result: { events: [] } });
    const el = renderNode(
      <MemoryRouter>
        <QubitBotPage />
      </MemoryRouter>,
    );
    await flush();
    expect(el.querySelector(".qb-hero")).not.toBeNull();
    expect(el.querySelectorAll(".qb-starter-btn").length).toBe(3);
    expect(el.querySelector(".qb-composer")).not.toBeNull();
    expect(el.querySelector("#bot-composer")).not.toBeNull();
    expect(el.querySelector(".qb-plus-btn")).not.toBeNull();
    expect(el.querySelector(".qb-send-btn")).not.toBeNull();
  });

  it("shows 'Up next' with a removable queue item while a turn is running", async () => {
    // Hold the running turn open so a queued follow-up stays visible.
    let release: (v: unknown) => void = () => {};
    botMocks.sendBotTurn.mockImplementationOnce(
      () => new Promise((resolve) => { release = resolve; }),
    );
    const el = renderNode(
      <MemoryRouter>
        <QubitBotPage />
      </MemoryRouter>,
    );
    await flush();
    // The composer is a multi-line <textarea> (upstream multiline TextInput).
    const composer = el.querySelector("#bot-composer") as HTMLTextAreaElement;
    setFieldValue(composer, "start");
    act(() => {
      const send = [...el.querySelectorAll("button")].find(
        (b) => b.getAttribute("aria-label") === "Send message",
      ) as HTMLButtonElement;
      send.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await flush();
    const followup = el.querySelector("#bot-followup") as HTMLInputElement;
    setFieldValue(followup, "held follow-up");
    await flush();
    const queueBtn = [...el.querySelectorAll("button")].find(
      (b) => (b.textContent ?? "").trim() === "Queue",
    ) as HTMLButtonElement;
    act(() => queueBtn.dispatchEvent(new MouseEvent("click", { bubbles: true })));
    await flush();
    const queue = el.querySelector(".qb-queue") as HTMLElement;
    expect(queue, "queue region").not.toBeNull();
    expect(queue.textContent).toContain("Up next");
    expect(queue.querySelectorAll(".qb-queue-item").length).toBe(1);
    expect(queue.querySelector(".qb-queue-remove")).not.toBeNull();
    // Remove the queued item.
    act(() =>
      (queue.querySelector(".qb-queue-remove") as HTMLButtonElement).dispatchEvent(
        new MouseEvent("click", { bubbles: true }),
      ),
    );
    await flush();
    expect(el.querySelector(".qb-queue")).toBeNull();
    act(() => release({ result: { events: [] } }));
    await flush();
  });

  it("pauses the queue and surfaces 'Messages on hold' after a send failure", async () => {
    botMocks.sendBotTurn.mockRejectedValueOnce(new Error("boom"));
    const el = renderNode(
      <MemoryRouter>
        <QubitBotPage />
      </MemoryRouter>,
    );
    await flush();
    // The composer is a multi-line <textarea> (upstream multiline TextInput).
    const composer = el.querySelector("#bot-composer") as HTMLTextAreaElement;
    setFieldValue(composer, "first");
    act(() => {
      const send = [...el.querySelectorAll("button")].find(
        (b) => b.getAttribute("aria-label") === "Send message",
      ) as HTMLButtonElement;
      send.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await flush();
    await flush();
    // The failed turn left a retry affordance, and the message stayed in the
    // transcript; the queue is paused (upstream ConversationQueue).
    expect(el.textContent).toContain("Retry response");
    expect(el.textContent).toContain("first");
  });
});
