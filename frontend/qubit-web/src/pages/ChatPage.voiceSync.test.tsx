// @vitest-environment jsdom
/**
 * `/qubit-chat` live voice-session sync contract.
 *
 * The desktop voice helper routes Right-Ctrl / "hey qubit" turns to a
 * `voice-*` session (`voicebridge/desktop_helper.py` `_DEFAULT_VOICE_SESSION`).
 * When `/qubit-chat` has such a session open — or the store exposes one — the
 * transcript must update LIVE, without a manual reload:
 *
 *  * the page polls the SAME persisted chat store
 *    (`GET /api/qubit/chat/sessions/<id>/messages`) on an interval and appends
 *    only the NEW rows (index-deduped, so a repeated poll never double-appends);
 *  * a `voice-live` indicator renders for a followed voice session and not for
 *    a normal one;
 *  * the newest `voice-*` session in the list gets a one-click
 *    "Open live voice session" affordance that switches `?resume=`.
 *
 * No new backend route is introduced; this pins a read-only poll of the store
 * the transcript already loads from.
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
  listChatSessions: vi.fn(async (): Promise<{
    sessions: Array<{ id: string; last_active?: number; started_at?: number }>;
    total: number;
    limit: number;
    offset: number;
  }> => ({
    sessions: [{ id: "dashboard-chat" }],
    total: 1,
    limit: 100,
    offset: 0,
  })),
  getChatSessionMessages: vi.fn(async () => ({
    session_id: "dashboard-chat",
    messages: [] as Array<{ role: string; content: string }>,
    total: 0,
  })),
  getChatSession: vi.fn(async () => ({
    session: { id: "dashboard-chat" },
    messages: [],
    message_count: 0,
    meta: {},
  })),
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

/** Drain the chained load promises (list -> messages -> session) under act. */
async function flush() {
  await act(async () => {
    for (let i = 0; i < 8; i += 1) await Promise.resolve();
  });
}

/** Fire the poll interval, then drain the resulting fetch chain. */
async function advance(ms: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
    for (let i = 0; i < 4; i += 1) await Promise.resolve();
  });
}

function voiceMessages(messages: Array<{ role: string; content: string }>) {
  return { session_id: "voice-chat", messages, total: messages.length };
}

beforeEach(() => {
  vi.useFakeTimers();
  apiMocks.getModelInfo.mockClear();
  apiMocks.listChatSessions.mockReset();
  apiMocks.listChatSessions.mockResolvedValue({
    sessions: [{ id: "dashboard-chat" }],
    total: 1,
    limit: 100,
    offset: 0,
  });
  apiMocks.getChatSessionMessages.mockReset();
  apiMocks.getChatSessionMessages.mockResolvedValue({
    session_id: "dashboard-chat",
    messages: [],
    total: 0,
  });
  apiMocks.getChatSession.mockReset();
  apiMocks.getChatSession.mockResolvedValue({
    session: { id: "dashboard-chat" },
    messages: [],
    message_count: 0,
    meta: {},
  });
  streamChatTurn.mockReset();
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

describe("Qubit ChatPage live voice sync", () => {
  it("appends a new voice turn on the poll interval without a reload", async () => {
    apiMocks.listChatSessions.mockResolvedValue({
      sessions: [{ id: "voice-chat" }],
      total: 1,
      limit: 100,
      offset: 0,
    });
    apiMocks.getChatSessionMessages
      .mockResolvedValueOnce(voiceMessages([]))
      .mockResolvedValue(
        voiceMessages([{ role: "user", content: "hello from voice" }]),
      );

    await renderChat("voice-chat");
    await flush();
    expect(container.textContent).not.toContain("hello from voice");

    await advance(2100);
    await flush();
    expect(container.textContent).toContain("hello from voice");
    // The live row arrived via the existing store read, not a page reload.
    expect(apiMocks.getChatSessionMessages.mock.calls.length).toBeGreaterThan(1);
  });

  it("does not double-append the same rows across polls (index dedupe)", async () => {
    apiMocks.listChatSessions.mockResolvedValue({
      sessions: [{ id: "voice-chat" }],
      total: 1,
      limit: 100,
      offset: 0,
    });
    apiMocks.getChatSessionMessages
      .mockResolvedValueOnce(voiceMessages([]))
      .mockResolvedValue(
        voiceMessages([{ role: "user", content: "same message" }]),
      );

    await renderChat("voice-chat");
    await flush();
    await advance(2100);
    await flush();
    await advance(2100);
    await flush();

    const occurrences = (container.textContent ?? "").split("same message")
      .length - 1;
    expect(occurrences).toBe(1);
  });

  it("shows the live-voice indicator for a voice session", async () => {
    apiMocks.listChatSessions.mockResolvedValue({
      sessions: [{ id: "voice-chat" }],
      total: 1,
      limit: 100,
      offset: 0,
    });
    await renderChat("voice-chat");
    await flush();
    expect(
      container.querySelector('[data-testid="voice-live"]'),
    ).not.toBeNull();
    // The follow toggle is offered for a voice session.
    expect(
      container.querySelector('[data-testid="voice-follow-toggle"]'),
    ).not.toBeNull();
  });

  it("does not show the live-voice indicator for a normal session", async () => {
    await renderChat("dashboard-chat");
    await flush();
    expect(container.querySelector('[data-testid="voice-live"]')).toBeNull();
    expect(
      container.querySelector('[data-testid="voice-follow-toggle"]'),
    ).toBeNull();
  });

  it("offers a one-click jump to the newest voice session", async () => {
    apiMocks.listChatSessions.mockResolvedValue({
      sessions: [
        { id: "dashboard-chat" },
        { id: "voice-1000", last_active: 1000 },
        { id: "voice-2000", last_active: 2000 },
      ],
      total: 3,
      limit: 100,
      offset: 0,
    });
    apiMocks.getChatSessionMessages.mockResolvedValue(voiceMessages([]));

    await renderChat("dashboard-chat");
    await flush();

    const button = container.querySelector<HTMLButtonElement>(
      '[data-testid="open-voice-session"]',
    );
    expect(button).not.toBeNull();

    await act(async () => {
      button!.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await flush();

    // The surface switched to the NEWEST voice session and now follows it live.
    expect(apiMocks.getChatSessionMessages).toHaveBeenCalledWith("voice-2000");
    expect(
      container.querySelector('[data-testid="voice-live"]'),
    ).not.toBeNull();
  });
});
