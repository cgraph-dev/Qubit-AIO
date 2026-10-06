// @vitest-environment jsdom
/**
 * `/qubit-chat` voice-session management surface.
 *
 * For a `voice-*` session the header shows the live-voice indicator (already
 * present) AND a small voice-session control that reads the real namespace +
 * retention policy from the settings view (`GET /api/voice-edit/settings`).
 * No endpoint is invented: retention is edited in Voice settings and old voice
 * sessions prune automatically on the voice path.
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

const voiceEditMocks = vi.hoisted(() => ({
  settings: vi.fn(
    async (): Promise<{ settings: Record<string, unknown> }> => ({
      settings: {
        voice_session_mode: "single",
        voice_session_ttl_s: 86400,
        voice_session_max_messages: 200,
        voice_session_max_sessions: 50,
      },
    }),
  ),
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

vi.mock("@/lib/voiceEdit", () => ({ voiceEdit: voiceEditMocks }));

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

async function flush() {
  await act(async () => {
    for (let i = 0; i < 8; i += 1) await Promise.resolve();
  });
}

beforeEach(() => {
  vi.useFakeTimers();
  apiMocks.listChatSessions.mockResolvedValue({
    sessions: [{ id: "dashboard-chat" }],
    total: 1,
    limit: 100,
    offset: 0,
  });
  apiMocks.getChatSessionMessages.mockResolvedValue({
    session_id: "dashboard-chat",
    messages: [],
    total: 0,
  });
  apiMocks.getChatSession.mockResolvedValue({
    session: { id: "dashboard-chat" },
    messages: [],
    message_count: 0,
    meta: {},
  });
  voiceEditMocks.settings.mockResolvedValue({
    settings: {
      voice_session_mode: "single",
      voice_session_ttl_s: 86400,
      voice_session_max_messages: 200,
      voice_session_max_sessions: 50,
    },
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

describe("Qubit ChatPage voice-session management", () => {
  it("shows the live indicator and real retention policy for a voice session", async () => {
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
    const controls = container.querySelector(
      '[data-testid="voice-session-controls"]',
    );
    expect(controls).not.toBeNull();
    const policy = container.querySelector(
      '[data-testid="voice-session-policy"]',
    );
    expect(policy?.textContent).toContain("single shared session");
    expect(policy?.textContent).toContain("keep 50");
    expect(policy?.textContent).toContain("1d TTL");
    expect(policy?.textContent).toContain("200 msgs");
    expect(voiceEditMocks.settings).toHaveBeenCalled();
  });

  it("reflects per-command mode and a custom retention from settings", async () => {
    apiMocks.listChatSessions.mockResolvedValue({
      sessions: [{ id: "voice-chat" }],
      total: 1,
      limit: 100,
      offset: 0,
    });
    voiceEditMocks.settings.mockResolvedValue({
      settings: {
        voice_session_mode: "per_command",
        voice_session_ttl_s: 7200,
        voice_session_max_messages: 500,
        voice_session_max_sessions: 25,
      },
    });
    await renderChat("voice-chat");
    await flush();

    const policy = container.querySelector(
      '[data-testid="voice-session-policy"]',
    );
    expect(policy?.textContent).toContain("per-command sessions");
    expect(policy?.textContent).toContain("keep 25");
    expect(policy?.textContent).toContain("2h TTL");
    expect(policy?.textContent).toContain("500 msgs");
  });

  it("does not show the voice-session controls for a normal session", async () => {
    await renderChat("dashboard-chat");
    await flush();
    expect(
      container.querySelector('[data-testid="voice-live"]'),
    ).toBeNull();
    expect(
      container.querySelector('[data-testid="voice-session-controls"]'),
    ).toBeNull();
  });

  it("degrades honestly when the settings read fails", async () => {
    apiMocks.listChatSessions.mockResolvedValue({
      sessions: [{ id: "voice-chat" }],
      total: 1,
      limit: 100,
      offset: 0,
    });
    voiceEditMocks.settings.mockRejectedValue(new Error("offline"));
    await renderChat("voice-chat");
    await flush();

    const policy = container.querySelector(
      '[data-testid="voice-session-policy"]',
    );
    expect(policy?.textContent).toContain("retention unavailable");
  });
});
