// @vitest-environment jsdom
/**
 * `/qubit-chat` "awaiting confirmation" contract.
 *
 * When the shared backend reports a live staged action on a turn event
 * (`pending_confirmation: { action_summary, staged_ms }`), the page surfaces a
 * distinct banner with Confirm / Cancel affordances. Both affordances reuse the
 * normal `send` path ("yes" / "no") so the settle travels through the same
 * backend confirmation authority — no second protocol. The banner also restores
 * from the session-load response when the backend exposes it, and never renders
 * when the signal is absent.
 */
import { act } from "react";
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

/** Drive the mocked SSE transport: a done frame carrying pending_confirmation. */
function emitPendingConfirmation(actionSummary: string, reply = "Staged.") {
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
      handlers.onDelta?.(reply, { event: "delta" });
      handlers.onDone?.({
        event: "done",
        reply,
        elapsed_ms: 120,
        receipt: { model: "qubit-brain4" },
        pending_confirmation: { action_summary: actionSummary, staged_ms: 42 },
      });
    },
  );
}

/** Drive the mocked SSE transport: a plain reply with no confirmation. */
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
      handlers.onDelta?.(reply, { event: "delta" });
      handlers.onDone?.({
        event: "done",
        reply,
        elapsed_ms: 120,
        receipt: { model: "qubit-brain4" },
      });
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

async function renderChat() {
  const { default: ChatPage } = await import("./ChatPage");
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await act(async () =>
    root.render(
      <MemoryRouter initialEntries={["/chat"]}>
        <ChatPage isActive />
      </MemoryRouter>,
    ),
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

function confirmButton(): HTMLButtonElement {
  return container.querySelector(
    'button[aria-label="Confirm the staged action"]',
  ) as HTMLButtonElement;
}

function cancelButton(): HTMLButtonElement {
  return container.querySelector(
    'button[aria-label="Cancel the staged action"]',
  ) as HTMLButtonElement;
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
  });
  streamChatTurn.mockReset();
  // A default no-op transport so any follow-up turn (Confirm / Cancel) resolves
  // cleanly; individual tests override the first call with `mockImplementationOnce`.
  streamChatTurn.mockResolvedValue(undefined);
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

describe("Qubit ChatPage pending confirmation", () => {
  it("renders the awaiting-confirmation banner with the action summary", async () => {
    emitPendingConfirmation("Delete /tmp/qubit-probe.txt");
    await renderChat();

    await typeAndSend("delete that file");

    await vi.waitFor(() =>
      expect(
        container.querySelector('[data-testid="pending-confirmation"]'),
      ).not.toBeNull(),
    );
    const banner = container.querySelector(
      '[data-testid="pending-confirmation"]',
    ) as HTMLElement;
    expect(banner.getAttribute("role")).toBe("status");
    expect(banner.textContent).toContain("Awaiting confirmation");
    expect(banner.textContent).toContain("Delete /tmp/qubit-probe.txt");
    expect(confirmButton()).not.toBeNull();
    expect(cancelButton()).not.toBeNull();
  });

  it("sends 'yes' through the normal send path when Confirm is clicked", async () => {
    emitPendingConfirmation("Send the drafted reply");
    await renderChat();
    await typeAndSend("reply to that");

    await vi.waitFor(() => expect(confirmButton()).not.toBeNull());
    // The confirm affordance is disabled while a turn is in flight; wait until
    // the raising turn has fully settled before clicking.
    await vi.waitFor(() => expect(confirmButton().disabled).toBe(false));

    await act(async () => {
      confirmButton().dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    await vi.waitFor(() =>
      expect(streamChatTurn).toHaveBeenCalledWith(
        { text: "yes", session: "dashboard-chat" },
        expect.objectContaining({ onDelta: expect.any(Function) }),
        expect.anything(),
      ),
    );
  });

  it("sends 'no' through the normal send path when Cancel is clicked", async () => {
    emitPendingConfirmation("Send the drafted reply");
    await renderChat();
    await typeAndSend("reply to that");

    await vi.waitFor(() => expect(cancelButton()).not.toBeNull());
    await vi.waitFor(() => expect(cancelButton().disabled).toBe(false));

    await act(async () => {
      cancelButton().dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    await vi.waitFor(() =>
      expect(streamChatTurn).toHaveBeenCalledWith(
        { text: "no", session: "dashboard-chat" },
        expect.objectContaining({ onDelta: expect.any(Function) }),
        expect.anything(),
      ),
    );
  });

  it("does not render the banner when the turn carries no pending confirmation", async () => {
    emitReply("no confirmation needed");
    await renderChat();
    await typeAndSend("just say hi");

    await vi.waitFor(() =>
      expect(container.textContent).toContain("no confirmation needed"),
    );
    expect(
      container.querySelector('[data-testid="pending-confirmation"]'),
    ).toBeNull();
  });

  it("restores the banner from the session-load response when the backend exposes it", async () => {
    apiMocks.getChatSession.mockResolvedValue({
      session: { id: "dashboard-chat" },
      messages: [],
      message_count: 0,
      meta: {},
      pending_confirmation: {
        action_summary: "Publish the report",
        staged_ms: 5000,
      },
    } as never);

    await renderChat();

    await vi.waitFor(() =>
      expect(
        container.querySelector('[data-testid="pending-confirmation"]'),
      ).not.toBeNull(),
    );
    expect(container.textContent).toContain("Publish the report");
  });
});
