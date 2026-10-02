// @vitest-environment jsdom
/**
 * Qubit WebChat page contract (separate SSE page — no duplex socket).
 *
 * `/webchat` drives the native agent turn over SSE
 * (`POST /api/qubit/chat/stream`, the same `voicebridge.chat_transport`
 * module both chats use) through the `ocFamilyWebchatApi` wrapper, and loads
 * the transcript from the persisted chat-session store. It must never open a
 * duplex WebSocket — no duplex runtime exists. These tests pin: session
 * create/reuse, mocked streaming send/receive (start/delta/progress/report/
 * done frames), reconnect-resume on the SAME session id after a failure, and
 * the reuse-only ROUTES manifest (no NEEDS-BRIDGE rows).
 */
import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const apiMocks = vi.hoisted(() => ({
  listChatSessions: vi.fn(async () => ({
    sessions: [{ id: "webchat-1" }],
    total: 1,
    limit: 50,
    offset: 0,
  })),
  createChatSession: vi.fn(async () => ({
    session: { id: "webchat-new" },
    messages: [],
  })),
  getChatSessionMessages: vi.fn(async () => ({
    session_id: "webchat-1",
    messages: [] as Array<{ role: string; content: string }>,
    total: 0,
  })),
  fetchJSON: vi.fn(async () => ({ state: "OK" })),
}));

const streamChatTurn = vi.hoisted(() => vi.fn());

vi.mock("@/lib/api", () => ({
  api: {
    listChatSessions: apiMocks.listChatSessions,
    createChatSession: apiMocks.createChatSession,
    getChatSessionMessages: apiMocks.getChatSessionMessages,
  },
  fetchJSON: apiMocks.fetchJSON,
  streamChatTurn,
  CHAT_REASONING_LEVELS: [],
}));

interface StreamHandlers {
  onStart?: (e: unknown) => void;
  onDelta?: (t: string, e: unknown) => void;
  onProgress?: (e: unknown) => void;
  onDone?: (e: unknown) => void;
  onError?: (e: unknown) => void;
  onCancelled?: (e: unknown) => void;
}

/** Drive the mocked SSE transport: start, deltas, progress, report, done. */
function emitReply(reply: string) {
  streamChatTurn.mockImplementationOnce(
    async (_body: unknown, handlers: StreamHandlers) => {
      handlers.onStart?.({ event: "start", session_id: "webchat-1" });
      const half = Math.max(1, Math.floor(reply.length / 2));
      handlers.onDelta?.(reply.slice(0, half), { event: "delta" });
      handlers.onDelta?.(reply.slice(half), { event: "delta" });
      handlers.onProgress?.({ event: "progress", status: "working", task: "t1" });
      handlers.onProgress?.({ event: "report", status: "done", task: "t1" });
      handlers.onDone?.({
        event: "done",
        reply,
        elapsed_ms: 128,
        receipt: { model: "qubit-spark17" },
      });
    },
  );
}

/** Drive the mocked SSE transport: a typed error event. */
function emitError(code: string, detail: string) {
  streamChatTurn.mockImplementationOnce(
    async (_body: unknown, handlers: StreamHandlers) => {
      handlers.onStart?.({ event: "start", session_id: "webchat-1" });
      handlers.onError?.({ event: "error", code, detail });
    },
  );
}

let container: HTMLDivElement;
let root: Root;

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

async function render(ui: ReactNode) {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await act(async () => root.render(ui));
}

async function renderWebchat(initialSession?: string) {
  const { default: WebChatPage } = await import("./WebChatPage");
  await render(
    <MemoryRouter
      initialEntries={[initialSession ? `/webchat?session=${initialSession}` : "/webchat"]}
    >
      <WebChatPage />
    </MemoryRouter>,
  );
  // Flush the mount-time session/history promises.
  await act(async () => {});
  await act(async () => {});
}

function input(): HTMLTextAreaElement {
  return container.querySelector(
    '[data-testid="webchat-input"]',
  ) as HTMLTextAreaElement;
}

async function typeAndSend(value: string) {
  await act(async () => {
    const el = input();
    const setter = Object.getOwnPropertyDescriptor(
      HTMLTextAreaElement.prototype,
      "value",
    )?.set;
    setter?.call(el, value);
    el.dispatchEvent(new Event("input", { bubbles: true }));
    // React 18 reads textarea via change/input; set the value + notify.
    el.dispatchEvent(new Event("change", { bubbles: true }));
  });
  await act(async () => {
    container
      .querySelector('[data-testid="webchat-send"]')!
      .dispatchEvent(new MouseEvent("click", { bubbles: true }));
  });
  await act(async () => {});
}

beforeEach(() => {
  apiMocks.listChatSessions.mockClear();
  apiMocks.createChatSession.mockClear();
  apiMocks.getChatSessionMessages.mockClear();
  apiMocks.fetchJSON.mockClear();
  streamChatTurn.mockReset();
});

afterEach(() => {
  root?.unmount();
  container?.remove();
  vi.resetModules();
});

describe("WebChatPage (SSE transport)", () => {
  it("reuses the listed session and streams a mocked reply", async () => {
    emitReply("Hello from the stream");
    await renderWebchat();

    expect(apiMocks.listChatSessions).toHaveBeenCalled();
    expect(apiMocks.createChatSession).not.toHaveBeenCalled();
    expect(
      container.querySelector('[data-testid="webchat-session"]')?.textContent,
    ).toContain("webchat-1");

    await typeAndSend("hi there");
    expect(streamChatTurn).toHaveBeenCalledTimes(1);
    expect(streamChatTurn.mock.calls[0][0]).toMatchObject({
      text: "hi there",
      session: "webchat-1",
    });
    expect(container.textContent).toContain("Hello from the stream");
    expect(
      container.querySelector('[data-testid="webchat-status"]')?.textContent,
    ).toContain("Ready");
  });

  it("reconnect-resume: a failed turn retries on the SAME session id", async () => {
    emitError("STREAM_BROKEN", "connection reset");
    await renderWebchat();
    await typeAndSend("first attempt");

    expect(streamChatTurn).toHaveBeenCalledTimes(1);
    expect(
      container.querySelector('[data-testid="webchat-error"]')?.textContent,
    ).toContain("connection reset");

    const firstSession = (streamChatTurn.mock.calls[0][0] as { session: string })
      .session;

    emitReply("recovered reply");
    await act(async () => {
      container
        .querySelector('[data-testid="webchat-retry"]')!
        .dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await act(async () => {});

    expect(streamChatTurn).toHaveBeenCalledTimes(2);
    const secondBody = streamChatTurn.mock.calls[1][0] as {
      text: string;
      session: string;
    };
    // Resume, not a new session: same id, same prompt re-sent.
    expect(secondBody.session).toBe(firstSession);
    expect(secondBody.text).toBe("first attempt");
    expect(container.textContent).toContain("recovered reply");
  });

  it("creates a fresh session when the store is empty", async () => {
    apiMocks.listChatSessions.mockResolvedValueOnce({
      sessions: [],
      total: 0,
      limit: 50,
      offset: 0,
    });
    await renderWebchat();
    expect(apiMocks.createChatSession).toHaveBeenCalled();
    expect(
      container.querySelector('[data-testid="webchat-session"]')?.textContent,
    ).toContain("webchat-new");
  });
});

describe("ocFamilyWebchatApi ROUTES manifest (reuse-only)", () => {
  it("lists only existing /api/qubit/chat endpoints with no bridge rows", async () => {
    const { ROUTES, NEEDS_BRIDGE } = await import("@/lib/ocFamilyWebchatApi");
    expect(ROUTES.length).toBeGreaterThan(0);
    for (const row of ROUTES) {
      expect(row.path.startsWith("/api/qubit/")).toBe(true);
      expect(["GET", "POST"]).toContain(row.method);
    }
    const paths = ROUTES.map((r) => r.path);
    expect(paths).toContain("/api/qubit/chat/stream");
    expect(paths).toContain("/api/qubit/chat");
    // No duplex socket, no PTY, no foreign namespace in the manifest.
    for (const p of paths) {
      expect(p).not.toMatch(/ws|socket|pty/i);
    }
    expect(NEEDS_BRIDGE).toEqual([]);
  });
});
