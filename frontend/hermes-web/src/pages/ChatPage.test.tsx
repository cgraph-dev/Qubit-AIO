// @vitest-environment jsdom
/**
 * Qubit-safe ChatPage contract.
 *
 * `/chat` drives the native agent turn over SSE
 * (`POST /api/qubit/chat/stream`, the same `voicebridge.chat_transport` module
 * as the non-streaming turn, CLI, and voice PTT) and loads the transcript from
 * the persisted chat-session store. It must never open a raw PTY WebSocket —
 * Qubit policy refuses `/api/pty` with `PTY_NO_SHELL`. These tests pin: real
 * streaming send/receive, server-backed transcript load, loading, typed error
 * + retry, voice fallback, and the absence of any WebSocket.
 */
import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const apiMocks = vi.hoisted(() => ({
  getModelInfo: vi.fn(async () => ({ model: "qubit-brain4" })),
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
}));

const streamChatTurn = vi.hoisted(() => vi.fn());

vi.mock("@/lib/api", () => ({
  api: {
    getModelInfo: apiMocks.getModelInfo,
    listChatSessions: apiMocks.listChatSessions,
    getChatSessionMessages: apiMocks.getChatSessionMessages,
  },
  streamChatTurn,
}));
vi.mock("@/components/ChatSessionList", () => ({
  ChatSessionList: () => null,
}));
vi.mock("@/plugins", () => ({
  PluginSlot: () => null,
}));
vi.mock("@/contexts/usePageHeader", () => ({
  usePageHeader: () => ({ setEnd: vi.fn(), setTitle: vi.fn() }),
}));
vi.mock("@/contexts/useProfileScope", () => ({
  useProfileScope: () => ({ profile: "" }),
}));
vi.mock("@/i18n", () => ({
  useI18n: () => ({ t: { app: { nav: { chat: "Chat" } } } }),
}));

/** Drive the mocked SSE transport: deltas then a done event. */
function emitReply(reply: string) {
  streamChatTurn.mockImplementationOnce(
    async (
      _body: unknown,
      handlers: {
        onStart?: (e: unknown) => void;
        onDelta?: (t: string, e: unknown) => void;
        onDone?: (e: unknown) => void;
      },
    ) => {
      handlers.onStart?.({ event: "start", session: "dashboard-chat" });
      const half = Math.max(1, Math.floor(reply.length / 2));
      handlers.onDelta?.(reply.slice(0, half), { event: "delta" });
      handlers.onDelta?.(reply.slice(half), { event: "delta" });
      handlers.onDone?.({
        event: "done",
        reply,
        elapsed_ms: 512,
        receipt: { model: "qubit-brain4" },
      });
    },
  );
}

/** Drive the mocked SSE transport: a typed error event. */
function emitError(code: string, detail: string) {
  streamChatTurn.mockImplementationOnce(
    async (
      _body: unknown,
      handlers: { onError?: (e: unknown) => void },
    ) => {
      handlers.onError?.({ event: "error", code, detail });
    },
  );
}

let container: HTMLDivElement;
let root: Root;

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

async function render(ui: ReactNode) {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await act(async () => root.render(ui));
}

async function renderChat() {
  const { default: ChatPage } = await import("./ChatPage");
  await render(
    <MemoryRouter initialEntries={["/chat"]}>
      <ChatPage isActive />
    </MemoryRouter>,
  );
}

function textarea(): HTMLTextAreaElement {
  return container.querySelector("textarea") as HTMLTextAreaElement;
}

async function typeAndSend(value: string) {
  await act(async () => {
    const el = textarea();
    const setter = Object.getOwnPropertyDescriptor(
      HTMLTextAreaElement.prototype,
      "value",
    )?.set;
    setter?.call(el, value);
    el.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await act(async () => {
    container
      .querySelector('button[aria-label="Send"]')!
      .dispatchEvent(new MouseEvent("click", { bubbles: true }));
  });
}

beforeEach(() => {
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
  streamChatTurn.mockReset();
  vi.stubGlobal("localStorage", localStorageMock);
  localStorageMock.clear();
  vi.stubGlobal("WebSocket", vi.fn());
  Object.defineProperty(window.navigator, "clipboard", {
    configurable: true,
    value: { writeText: vi.fn(async () => {}) },
  });
});

afterEach(async () => {
  await act(async () => root?.unmount());
  container?.remove();
  vi.unstubAllGlobals();
});

describe("Qubit ChatPage", () => {
  it("renders the empty state and the model line", async () => {
    await renderChat();
    await vi.waitFor(() =>
      expect(container.textContent).toContain("No messages yet"),
    );
    await vi.waitFor(() =>
      expect(container.textContent).toContain("qubit-brain4"),
    );
    expect(apiMocks.getChatSessionMessages).toHaveBeenCalledWith(
      "dashboard-chat",
    );
  });

  it("loads the persisted transcript from the chat store", async () => {
    apiMocks.getChatSessionMessages.mockResolvedValueOnce({
      session_id: "chat-abc",
      messages: [
        { role: "user", content: "persisted question" },
        { role: "assistant", content: "persisted answer" },
      ],
      total: 2,
    });
    await renderChat();
    await vi.waitFor(() =>
      expect(container.textContent).toContain("persisted answer"),
    );
    expect(container.textContent).toContain("persisted question");
  });

  it("does not request a transcript for a not-yet-created session", async () => {
    // The unsaved default session has no server record yet: the page must not
    // issue a messages request (which would 404 in the console).
    apiMocks.listChatSessions.mockResolvedValueOnce({
      sessions: [],
      total: 0,
      limit: 100,
      offset: 0,
    });
    await renderChat();
    await vi.waitFor(() =>
      expect(container.textContent).toContain("No messages yet"),
    );
    expect(apiMocks.getChatSessionMessages).not.toHaveBeenCalled();
  });

  it("streams a turn over SSE and renders the real reply", async () => {
    emitReply("QUBIT_BRAIN4_OK");
    await renderChat();

    await typeAndSend("hello qubit");

    await vi.waitFor(() =>
      expect(container.textContent).toContain("QUBIT_BRAIN4_OK"),
    );
    expect(container.textContent).toContain("You");
    expect(container.textContent).toContain("hello qubit");
    expect(container.textContent).toContain("model=qubit-brain4");
    expect(streamChatTurn).toHaveBeenCalledWith(
      { text: "hello qubit", session: "dashboard-chat" },
      expect.objectContaining({ onDelta: expect.any(Function) }),
      expect.anything(),
    );
  });

  it("never opens a WebSocket (no /api/pty reconnect loop)", async () => {
    emitReply("ok");
    await renderChat();
    await typeAndSend("hello");
    await vi.waitFor(() => expect(container.textContent).toContain("ok"));
    expect(globalThis.WebSocket).not.toHaveBeenCalled();
  });

  it("surfaces a typed SSE error and retries the same text", async () => {
    emitError("AGENT_UNAVAILABLE", "no model");
    emitReply("recovered");

    await renderChat();
    await typeAndSend("hello");

    await vi.waitFor(() =>
      expect(container.textContent).toContain("AGENT_UNAVAILABLE"),
    );

    await act(async () => {
      container
        .querySelector('button[aria-label="Retry the last message"]')!
        .dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    await vi.waitFor(() =>
      expect(container.textContent).toContain("recovered"),
    );
    expect(streamChatTurn).toHaveBeenCalledTimes(2);
  });

  it("reports a typed unsupported state when the browser has no speech API", async () => {
    await renderChat();
    await act(async () => {
      container
        .querySelector('button[aria-label="Voice input"]')!
        .dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await vi.waitFor(() =>
      expect(container.textContent).toContain("VOICE_UNSUPPORTED"),
    );
  });

  it("restores the server transcript after a remount", async () => {
    emitReply("remember me");
    await renderChat();
    await typeAndSend("hello");
    await vi.waitFor(() =>
      expect(container.textContent).toContain("remember me"),
    );

    await act(async () => root.unmount());
    container.remove();
    // The server store now holds the persisted transcript.
    apiMocks.getChatSessionMessages.mockResolvedValue({
      session_id: "dashboard-chat",
      messages: [
        { role: "user", content: "hello" },
        { role: "assistant", content: "remember me" },
      ],
      total: 2,
    });
    await renderChat();

    await vi.waitFor(() =>
      expect(container.textContent).toContain("remember me"),
    );
    expect(container.textContent).toContain("hello");
  });
});
