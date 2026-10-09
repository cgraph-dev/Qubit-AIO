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
  getModelInfo: vi.fn(async () => ({
    model: "qubit-brain4",
    capabilities: { supports_reasoning: true },
  })),
  listChatSessions: vi.fn(async () => ({
    sessions: [{ id: "dashboard-chat" }],
    total: 1,
    limit: 100,
    offset: 0,
  })),
  getChatSessionMessages: vi.fn(async () => ({
    session_id: "dashboard-chat",
    messages: [] as Array<{
      role: string;
      content: string;
      meta?: Record<string, unknown>;
    }>,
    total: 0,
  })),
  getStatus: vi.fn(async () => ({ gateway_state: "stopped" })),
  getLogs: vi.fn(async () => ({ file: "agent", lines: ["debug log line"] })),
  // The debug overlay reads the canonical runtime inventory through
  // api.getRuntime(); the mock must expose the real production method (PRA-02).
  getRuntime: vi.fn(async () => ({
    agent: { state: "ready" },
    broker: { state: "ready" },
    approvals: { pending: 0 },
    providers: { resident_alias: "qubit-brain4" },
  })),
  getChatSessionDrafts: vi.fn(async () => ({ drafts: [] })),
  getChatSessionComments: vi.fn(async () => ({ comments: [] })),
  getChatSessionCi: vi.fn(async () => ({ ci: [] })),
  getChatSession: vi.fn(async () => ({
    session: { id: "dashboard-chat" },
    messages: [],
    message_count: 0,
    meta: {},
  })),
  patchChatSession: vi.fn(async () => ({ ok: true })),
}));

const streamChatTurn = vi.hoisted(() => vi.fn());

vi.mock("@/lib/api", () => ({
  api: {
    getModelInfo: apiMocks.getModelInfo,
    listChatSessions: apiMocks.listChatSessions,
    getChatSessionMessages: apiMocks.getChatSessionMessages,
    getStatus: apiMocks.getStatus,
    getLogs: apiMocks.getLogs,
    getRuntime: apiMocks.getRuntime,
    getChatSessionDrafts: apiMocks.getChatSessionDrafts,
    getChatSessionComments: apiMocks.getChatSessionComments,
    getChatSessionCi: apiMocks.getChatSessionCi,
    describeChatImage: vi.fn(async () => ({ description: "described" })),
    getChatSession: apiMocks.getChatSession,
    patchChatSession: apiMocks.patchChatSession,
  },
  // Transcript reads go through the backoff wrapper; mocked transports
  // never transiently fail, so a first-attempt passthrough matches the
  // real behavior on a healthy backend.
  withBackoff: (fn: () => Promise<unknown>) => fn(),
  authedFetch: vi.fn(),
  streamChatTurn,
  CHAT_REASONING_LEVELS: [
    { id: "none", budget: 64 },
    { id: "minimal", budget: 128 },
    { id: "low", budget: 256 },
    { id: "medium", budget: 512 },
    { id: "high", budget: 1024 },
    { id: "max", budget: 2048 },
  ],
}));
vi.mock("@/components/ChatSessionList", () => ({
  ChatSessionList: () => null,
}));
vi.mock("@/components/ChatSidebar", () => ({
  ChatSidebar: () => null,
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

/** Render /chat at an explicit URL (e.g. a `?resume=` deep link). */
async function renderChatAt(path: string) {
  const { default: ChatPage } = await import("./ChatPage");
  await render(
    <MemoryRouter initialEntries={[path]}>
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
  apiMocks.getChatSession.mockReset();
  apiMocks.getChatSession.mockResolvedValue({
    session: { id: "dashboard-chat" },
    messages: [],
    message_count: 0,
    meta: {},
  } as never);
  apiMocks.getStatus.mockReset();
  apiMocks.getStatus.mockResolvedValue({ gateway_state: "stopped" });
  apiMocks.getLogs.mockReset();
  apiMocks.getLogs.mockResolvedValue({
    file: "agent",
    lines: ["debug log line"],
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
      expect(container.textContent).toContain("Start a new run."),
    );
    await vi.waitFor(() =>
      expect(container.textContent).toContain("qubit-brain4"),
    );
    expect(apiMocks.getChatSessionMessages).toHaveBeenCalledWith(
      "dashboard-chat",
    );
  });

  it("renders the single Model & tools complementary wrapper (A31)", async () => {
    await renderChat();
    const panel = container.querySelector<HTMLElement>(
      '#chat-side-panel[role="complementary"]',
    );
    expect(panel).not.toBeNull();
    expect(panel?.getAttribute("aria-label")).toBe("Model & tools");
    expect(container.querySelectorAll('[role="complementary"]').length).toBe(1);
    expect(
      container.querySelector('button[aria-label="Collapse chat side panel"]'),
    ).not.toBeNull();
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

  it("renders tool rows distinctly with a tool icon (A36)", async () => {
    apiMocks.getChatSessionMessages.mockResolvedValueOnce({
      session_id: "chat-abc",
      messages: [
        { role: "assistant", content: "let me run that" },
        { role: "tool", content: "total 0", meta: { tool_name: "terminal" } },
      ],
      total: 2,
    });
    await renderChat();
    await vi.waitFor(() =>
      expect(container.textContent).toContain("tool: terminal"),
    );
    expect(container.textContent).toContain("total 0");
  });

  it("does not request a transcript for a not-yet-created session", async () => {
    // The unsaved default session has no server record yet: the page probes
    // the canonical session, gets a 404, and must NOT issue a messages request
    // (which would 404 in the console). The empty composer renders instead.
    apiMocks.listChatSessions.mockResolvedValueOnce({
      sessions: [],
      total: 0,
      limit: 100,
      offset: 0,
    });
    apiMocks.getChatSession.mockRejectedValueOnce(new Error("404: missing"));
    await renderChat();
    await vi.waitFor(() =>
      expect(container.textContent).toContain("Start a new run."),
    );
    expect(apiMocks.getChatSessionMessages).not.toHaveBeenCalled();
  });

  it("resumes a session hidden from the curated picker list via a direct probe", async () => {
    // The list route hides auto-titled ``dashboard-<hex>`` rows, so a real
    // session the user just created is ABSENT from it. Resuming must still
    // work: the page probes the canonical session directly and, on success,
    // loads its transcript instead of mislabelling it "foreign".
    const hiddenId = "dashboard-9997c619acdb";
    apiMocks.listChatSessions.mockResolvedValueOnce({
      sessions: [],
      total: 0,
      limit: 100,
      offset: 0,
    });
    apiMocks.getChatSession.mockResolvedValueOnce({
      session: { id: hiddenId },
      messages: [],
      message_count: 2,
      meta: {},
    } as never);
    apiMocks.getChatSessionMessages.mockResolvedValueOnce({
      session_id: hiddenId,
      messages: [
        { role: "user", content: "resumed question" },
        { role: "assistant", content: "resumed answer" },
      ],
      total: 2,
    });
    await renderChatAt(`/chat?resume=${hiddenId}`);
    await vi.waitFor(() =>
      expect(container.textContent).toContain("resumed answer"),
    );
    expect(apiMocks.getChatSessionMessages).toHaveBeenCalledWith(hiddenId);
    // Not flagged foreign.
    expect(
      container.querySelector('[data-testid="foreign-session-banner"]'),
    ).toBeNull();
  });

  it("flags a genuinely unknown resume id as foreign (no transcript read)", async () => {
    const strangerId = "not-a-real-session";
    apiMocks.listChatSessions.mockResolvedValueOnce({
      sessions: [],
      total: 0,
      limit: 100,
      offset: 0,
    });
    apiMocks.getChatSession.mockRejectedValueOnce(new Error("404: missing"));
    await renderChatAt(`/chat?resume=${strangerId}`);
    await vi.waitFor(() =>
      expect(
        container.querySelector('[data-testid="foreign-session-banner"]'),
      ).not.toBeNull(),
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

  it("sends the selected reasoning level and persists it per session", async () => {
    emitReply("reasoned");
    await renderChat();

    await act(async () => {
      const picker = container.querySelector(
        '[data-testid="chat-reasoning-picker"]',
      ) as HTMLSelectElement;
      expect(picker).not.toBeNull();
      picker.value = "high";
      picker.dispatchEvent(new Event("change", { bubbles: true }));
    });

    await vi.waitFor(() =>
      expect(apiMocks.patchChatSession).toHaveBeenCalledWith(
        "dashboard-chat",
        { reasoning: "high" },
      ),
    );

    await typeAndSend("think hard");

    await vi.waitFor(() =>
      expect(streamChatTurn).toHaveBeenCalledWith(
        { text: "think hard", session: "dashboard-chat", reasoning: "high" },
        expect.objectContaining({ onDelta: expect.any(Function) }),
        expect.anything(),
      ),
    );
    await vi.waitFor(() =>
      expect(container.textContent).toContain("reasoned"),
    );
  });

  it("never opens a WebSocket (no /api/pty reconnect loop)", async () => {
    emitReply("ok");
    await renderChat();
    await typeAndSend("hello");
    await vi.waitFor(() => expect(container.textContent).toContain("ok"));
    expect(globalThis.WebSocket).not.toHaveBeenCalled();
  });

  it("hides the reasoning selector when the model does not support reasoning", async () => {
    apiMocks.getModelInfo.mockResolvedValueOnce({
      model: "qubit-brain4",
      capabilities: { supports_reasoning: false },
    } as never);
    await renderChat();
    await vi.waitFor(() =>
      expect(apiMocks.getModelInfo).toHaveBeenCalled(),
    );
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    expect(
      container.querySelector('[data-testid="chat-reasoning-picker"]'),
    ).toBeNull();
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

  it("surfaces a mid-stream typed error and offers Retry (no crash)", async () => {
    // Deltas arrive, then a typed error before `done`. The page drops the
    // provisional streaming text (it was never a committed turn), surfaces the
    // error honestly, and offers Retry — never blanks the transcript or throws
    // on the truncated frame.
    streamChatTurn.mockImplementationOnce(
      async (
        _body: unknown,
        handlers: {
          onStart?: (e: unknown) => void;
          onDelta?: (t: string, e: unknown) => void;
          onError?: (e: unknown) => void;
        },
      ) => {
        handlers.onStart?.({ event: "start", session: "dashboard-chat" });
        handlers.onDelta?.("half ", { event: "delta" });
        handlers.onDelta?.("written", { event: "delta" });
        handlers.onError?.({ event: "error", code: "STREAM_BROKEN", detail: "socket closed" });
      },
    );
    await renderChat();
    await typeAndSend("stream please");

    await vi.waitFor(() =>
      expect(container.textContent).toContain("STREAM_BROKEN"),
    );
    expect(container.textContent).toContain("socket closed");
    // The truncated provisional text must not linger as a committed reply.
    expect(container.textContent).not.toContain("half written");
    expect(
      container.querySelector('button[aria-label="Retry the last message"]'),
    ).not.toBeNull();
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

  it("collapses and restores the chat side panel (A26 control family)", async () => {
    await renderChat();
    await vi.waitFor(() =>
      expect(container.querySelector("#chat-side-panel")).not.toBeNull(),
    );

    await act(async () => {
      container
        .querySelector('button[aria-label="Collapse chat side panel"]')!
        .dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(container.querySelector("#chat-side-panel")).toBeNull();
    expect(
      container.querySelector('button[aria-label="Show chat side panel"]'),
    ).not.toBeNull();

    await act(async () => {
      container
        .querySelector('button[aria-label="Show chat side panel"]')!
        .dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(container.querySelector("#chat-side-panel")).not.toBeNull();
  });

  it("renders the sandboxed HTML preview for assistant markup", async () => {
    apiMocks.getChatSessionMessages.mockResolvedValueOnce({
      session_id: "dashboard-chat",
      messages: [{ role: "assistant", content: "<b>bold</b> reply" }],
      total: 1,
    });
    await renderChat();

    await vi.waitFor(() =>
      expect(container.querySelector('iframe[sandbox=""]')).not.toBeNull(),
    );
    const frame = container.querySelector<HTMLIFrameElement>(
      'iframe[sandbox=""]',
    );
    expect(frame?.getAttribute("title")).toBe("Sandboxed HTML preview");
    expect(frame?.getAttribute("referrerpolicy")).toBe("no-referrer");
    expect(container.textContent).toContain("HTML preview (sandboxed)");
  });

  it("opens the debug overlay over the canonical runtime + logs", async () => {
    await renderChat();
    await act(async () => {
      container
        .querySelector('button[aria-label="Show debug overlay"]')!
        .dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    const region = await vi.waitFor(() => {
      const node = container.querySelector(
        '[role="region"][aria-label="Debug overlay"]',
      );
      expect(node).not.toBeNull();
      return node as HTMLElement;
    });
    expect(region.dataset.state).not.toBe("idle");
    expect(container.textContent).toContain("gateway: stopped");
    expect(container.textContent).toContain("debug log line");
    expect(apiMocks.getStatus).toHaveBeenCalled();
    expect(apiMocks.getLogs).toHaveBeenCalledWith({ file: "agent", lines: 50 });
    // PRA-02/PRA-03: the overlay must read the canonical runtime inventory and
    // render its real fields, not swallow a missing method.
    expect(apiMocks.getRuntime).toHaveBeenCalled();
    expect(container.textContent).toContain('"agent":"ready"');
    expect(container.textContent).toContain('"provider":"qubit-brain4"');
  });

  // ── Regression: reload must restore the session without a right-rail click ──
  it("loads the transcript even when the session LIST read fails", async () => {
    // The reported defect: on reload the transcript load was gated behind
    // `listChatSessions`; a slow/failing list (backend restarting) left the
    // page blank until a session row was clicked. The list is now auxiliary,
    // so a rejected list must not stop the transcript from painting.
    apiMocks.listChatSessions.mockRejectedValueOnce(new Error("502: backend restarting"));
    apiMocks.getChatSessionMessages.mockResolvedValueOnce({
      session_id: "dashboard-chat",
      messages: [
        { role: "user", content: "reload question" },
        { role: "assistant", content: "reload answer" },
      ],
      total: 2,
    });
    await renderChatAt("/chat?resume=dashboard-chat");
    await vi.waitFor(() =>
      expect(container.textContent).toContain("reload answer"),
    );
    // Not degraded to the page error banner — the transcript is authoritative.
    expect(container.querySelector('[role="alert"][data-state="degraded"]')).toBeNull();
  });

  it("does not duplicate a build-task report into the streaming bubble", async () => {
    // A build turn streams `progress` frames, then the full report as one
    // delta, and writes the same report back on `done` as reportText. Without
    // the guard the report painted twice (streaming bubble + message) and the
    // swap at done flickered. It must appear exactly once, as the message.
    await renderChat();
    await vi.waitFor(() =>
      expect(container.querySelector('#qubit-chat-input')).not.toBeNull(),
    );
    streamChatTurn.mockImplementationOnce(
      async (
        _body: unknown,
        handlers: {
          onProgress?: (e: unknown) => void;
          onDelta?: (t: string, e: unknown) => void;
          onDone?: (e: unknown) => void;
        },
      ) => {
        handlers.onProgress?.({
          event: "progress",
          task: "task-1",
          status: "working",
          detail: "worker started",
        });
        handlers.onDelta?.("FULL_REPORT_BODY", { event: "delta" });
        handlers.onDone?.({
          event: "done",
          reply: "FULL_REPORT_BODY",
          summary: "SHORT_SUMMARY",
          route: "task",
          task: "task-1",
          receipt: { route: "task", task: "task-1" },
        });
      },
    );
    await typeAndSend("build me something");
    await vi.waitFor(() =>
      expect(container.textContent).toContain("SHORT_SUMMARY"),
    );
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    // No live streaming bubble survived the done frame (the flicker source).
    expect(container.querySelector('[data-state="streaming"]')).toBeNull();
    // The full report lives in the message disclosure exactly once — it is NOT
    // also painted into the assistant message body (that would show the whole
    // report twice, once as `text`, once as `reportText`).
    const disclosure = container.querySelector(
      '[data-testid="message-report-full"]',
    );
    expect(disclosure).not.toBeNull();
    expect(
      (disclosure?.textContent?.match(/FULL_REPORT_BODY/g) ?? []).length,
    ).toBe(1);
    // The assistant message body carries the SHORT summary, not the report.
    const messageBody = container.querySelector(
      '[data-message-index] .whitespace-pre-wrap',
    );
    expect(messageBody?.textContent).not.toContain("FULL_REPORT_BODY");
  });

  it("renders a non-string reply without crashing the page", async () => {
    // <Markdown> parses via `content.split`; a frame whose reply is not text
    // used to throw inside render and blank the route (the black screen).
    await renderChat();
    await vi.waitFor(() =>
      expect(container.querySelector('#qubit-chat-input')).not.toBeNull(),
    );
    streamChatTurn.mockImplementationOnce(
      async (
        _body: unknown,
        handlers: {
          onDelta?: (t: string, e: unknown) => void;
          onDone?: (e: unknown) => void;
        },
      ) => {
        handlers.onDelta?.("partial", { event: "delta" });
        handlers.onDone?.({
          event: "done",
          reply: { not: "text" },
          receipt: { model: "qubit-brain4" },
        });
      },
    );
    await typeAndSend("hello");
    // The page is still mounted and interactive (no uncaught render throw).
    await vi.waitFor(() =>
      expect(container.querySelector("#qubit-chat-input")).not.toBeNull(),
    );
  });
});
