// @vitest-environment jsdom
/**
 * `/qubit-chat` transcript must never discard a message the operator sent.
 *
 * The load effect probes the canonical session first
 * (`GET /api/qubit/chat/sessions/<id>`) so a not-yet-created id never issues a
 * transcript read. That probe answers 404 for a session the store has no row
 * for yet — which is exactly the state of the default chat on a fresh install.
 *
 * The probe settles LATE (it is chained behind `withBackoff`, so the rejection
 * lands a few hundred ms after the request). If the operator types and presses
 * Send in that window, the optimistic user row is already rendered when the
 * 404 arrives. The old handling ran `setMessages([])` unconditionally and
 * deleted it, leaving the page showing the reply with no record of the prompt
 * — "I sent a message and it vanished".
 *
 * Contract pinned here: a FAILED session read may not erase a row appended
 * AFTER that read was issued. A read with no local appends still clears the
 * transcript (that is the fresh/empty default chat it was written for).
 */
import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const apiMocks = vi.hoisted(() => ({
  getModelInfo: vi.fn(async () => ({
    model: "qubit-brain4",
    capabilities: { supports_reasoning: false },
  })),
  listChatSessions: vi.fn(async () => ({
    sessions: [] as Array<{ id: string }>,
    total: 0,
    limit: 100,
    offset: 0,
  })),
  getChatSessionMessages: vi.fn(async () => ({
    session_id: "dashboard-chat",
    messages: [] as Array<{ role: string; content: string }>,
    total: 0,
  })),
  /** 404: the store has no row for this id yet (fresh install / new chat). */
  getChatSession: vi.fn(async () => {
    throw new Error('404: {"ok":false,"code":"SESSION_NOT_FOUND"}');
  }),
  patchChatSession: vi.fn(async () => ({ ok: true })),
  getConfig: vi.fn(async () => ({ voice: { input_engine: "auto" } })),
  describeChatImage: vi.fn(async () => ({ description: "described" })),
}));

const streamChatTurn = vi.hoisted(() => vi.fn());

vi.mock("@/lib/api", () => ({
  api: {
    getModelInfo: apiMocks.getModelInfo,
    listChatSessions: apiMocks.listChatSessions,
    getChatSessionMessages: apiMocks.getChatSessionMessages,
    getChatSession: apiMocks.getChatSession,
    patchChatSession: apiMocks.patchChatSession,
    getConfig: apiMocks.getConfig,
    describeChatImage: apiMocks.describeChatImage,
  },
  withBackoff: (fn: () => Promise<unknown>) => fn(),
  authedFetch: vi.fn(),
  streamChatTurn,
  CHAT_REASONING_LEVELS: [],
}));

vi.mock("@/components/ChatSessionList", () => ({
  ChatSessionList: () => null,
}));
vi.mock("@/components/ChatSidebar", () => ({ ChatSidebar: () => null }));
vi.mock("@/plugins", () => ({ PluginSlot: () => null }));
vi.mock("@/contexts/usePageHeader", () => ({
  usePageHeader: () => ({ setEnd: vi.fn(), setTitle: vi.fn() }),
}));
vi.mock("@/contexts/useProfileScope", () => ({
  useProfileScope: () => ({ profile: "" }),
}));
vi.mock("@/i18n", () => ({
  useI18n: () => ({
    t: {
      app: {
        nav: { chat: "Chat" },
        modelToolsSheetTitle: "Model",
        modelToolsSheetSubtitle: "& tools",
      },
    },
  }),
}));

const localStorageMock = (() => {
  let store: Record<string, string> = {};
  return {
    getItem: (key: string) => store[key] ?? null,
    setItem: (key: string, value: string) => {
      store[key] = String(value);
    },
    removeItem: (key: string) => {
      delete store[key];
    },
    clear: () => {
      store = {};
    },
  };
})();

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

let container: HTMLDivElement;
let root: Root;

async function renderChat(resume: string) {
  const { default: ChatPage } = await import("./ChatPage");
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await act(async () => {
    root.render(
      <MemoryRouter initialEntries={[`/qubit-chat?resume=${resume}`]}>
        <ChatPage isActive />
      </MemoryRouter>,
    ) as ReactNode;
  });
}

/** Drain chained promises (list -> session probe) without resolving the probe. */
async function drain(times = 8) {
  await act(async () => {
    for (let i = 0; i < times; i += 1) await Promise.resolve();
  });
}

function composer(): HTMLTextAreaElement {
  const el = container.querySelector<HTMLTextAreaElement>(
    'textarea[aria-label="Message Qubit"]',
  );
  if (!el) throw new Error("composer not found");
  return el;
}

/** Type a message and press Send, exactly as the operator does. */
async function typeAndSend(text: string) {
  const box = composer();
  await act(async () => {
    const setter = Object.getOwnPropertyDescriptor(
      window.HTMLTextAreaElement.prototype,
      "value",
    )?.set;
    setter?.call(box, text);
    box.dispatchEvent(new Event("input", { bubbles: true }));
  });
  const send = Array.from(container.querySelectorAll("button, sakuga-button")).find(
    (b) => (b.textContent ?? "").trim() === "Send",
  );
  if (!send) throw new Error("Send button not found");
  await act(async () => {
    send.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  });
}

beforeEach(() => {
  vi.useFakeTimers();
  apiMocks.getChatSession.mockReset();
  apiMocks.getChatSession.mockImplementation(async () => {
    throw new Error('404: {"ok":false,"code":"SESSION_NOT_FOUND"}');
  });
  apiMocks.listChatSessions.mockReset();
  apiMocks.listChatSessions.mockResolvedValue({
    sessions: [],
    total: 0,
    limit: 100,
    offset: 0,
  });
  apiMocks.getChatSessionMessages.mockReset();
  apiMocks.getChatSessionMessages.mockResolvedValue({
    session_id: "dashboard-chat",
    messages: [],
    total: 0,
  });
  streamChatTurn.mockReset();
  streamChatTurn.mockImplementation(async () => undefined);
  vi.stubGlobal("localStorage", localStorageMock);
  localStorageMock.clear();
  vi.stubGlobal("WebSocket", vi.fn());
});

afterEach(async () => {
  await act(async () => root?.unmount());
  container?.remove();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("Qubit ChatPage: a failed session read never erases a sent message", () => {
  it("keeps the optimistic user row when the session probe 404s after Send", async () => {
    // Hold the probe open so the 404 settles AFTER the operator's turn — the
    // exact ordering that lost the message.
    let rejectProbe: (err: Error) => void = () => {};
    apiMocks.getChatSession.mockImplementation(
      () =>
        new Promise((_resolve, reject) => {
          rejectProbe = reject;
        }),
    );

    await renderChat("dashboard-chat");
    await drain();

    await typeAndSend("hello qubit");
    expect(container.textContent).toContain("hello qubit");

    // The stale 404 now lands.
    await act(async () => {
      rejectProbe(
        new Error('404: {"ok":false,"code":"SESSION_NOT_FOUND"}'),
      );
      for (let i = 0; i < 8; i += 1) await Promise.resolve();
    });
    await drain();

    // The message the operator typed is still on screen.
    expect(container.textContent).toContain("hello qubit");
    expect(container.querySelectorAll(".qchat__message--user").length).toBe(1);
  });

  it("still clears the transcript for a 404 with no local appends", async () => {
    // The fresh/empty default chat this branch was written for: no local
    // rows, so the 404 resolves to an empty ready transcript as before.
    await renderChat("dashboard-chat");
    await drain();

    expect(container.querySelectorAll(".qchat__message--user").length).toBe(0);
    // Cleared to the empty state, not stuck on the loading spinner.
    expect(container.textContent).not.toContain("Loading chat…");
  });
});
