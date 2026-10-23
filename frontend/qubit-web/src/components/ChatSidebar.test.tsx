// @vitest-environment jsdom
import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const apiMocks = vi.hoisted(() => ({
  getModelInfo: vi.fn(async () => ({
    capabilities: { supports_reasoning: false },
    model: "test/model",
  })),
  patchChatSession: vi.fn(async (id: string, patch: { model?: string }) => ({
    ok: true,
    session: { id, model: patch.model ?? null },
  })),
  setModelAssignment: vi.fn(async () => ({
    ok: true,
    terminal_follows: true,
  }) as {
    ok: boolean;
    terminal_follows: boolean;
    provider?: string;
    model?: string;
    terminal_detail?: string;
    confirm_required?: boolean;
  }),
}));

const reloadMocks = vi.hoisted(() => ({
  maybeReloadForLoopbackWsAuthFailure: vi.fn(() => false),
}));

// Captures the reload-confirm dialog's props so a test can assert the reload
// prompt really appears after a successful main-model write (upstream parity).
const reloadConfirmMocks = vi.hoisted(() => ({
  lastProps: null as null | {
    model?: string | null;
    onCancel?: () => void;
  },
}));

vi.mock("@/lib/api", () => ({
  api: {
    getModelInfo: apiMocks.getModelInfo,
    patchChatSession: apiMocks.patchChatSession,
    setModelAssignment: apiMocks.setModelAssignment,
  },
  QUBIT_BASE_PATH: "",
}));
vi.mock("@/lib/dashboard-auth-reload", () => ({
  maybeReloadForLoopbackWsAuthFailure:
    reloadMocks.maybeReloadForLoopbackWsAuthFailure,
}));
const pickerMocks = vi.hoisted(() => ({
  lastProps: null as null | {
    onApply?: (a: {
      provider: string;
      model: string;
      confirmExpensiveModel?: boolean;
      persistGlobal?: boolean;
    }) => unknown;
  },
}));

vi.mock("@/components/ModelPickerDialog", () => ({
  ModelPickerDialog: (props: unknown) => {
    pickerMocks.lastProps = props as typeof pickerMocks.lastProps;
    return null;
  },
}));
vi.mock("@/components/ModelReloadConfirm", () => ({
  ModelReloadConfirm: (props: unknown) => {
    reloadConfirmMocks.lastProps = props as typeof reloadConfirmMocks.lastProps;
    return null;
  },
}));
vi.mock("@/components/ReasoningPicker", () => ({
  ReasoningPicker: () => null,
}));
vi.mock("@nous-research/ui/ui/components/button", () => ({
  Button: ({
    children,
    title,
    onClick,
  }: {
    children?: ReactNode;
    title?: string;
    onClick?: () => void;
  }) => (
    <button title={title} onClick={onClick}>
      {children}
    </button>
  ),
}));
vi.mock("@nous-research/ui/ui/components/badge", () => ({
  Badge: ({ children }: { children?: ReactNode }) => <span>{children}</span>,
}));
vi.mock("@nous-research/ui/ui/components/card", () => ({
  Card: ({ children }: { children?: ReactNode }) => <div>{children}</div>,
}));

interface FakeStream {
  response: Response;
  send: (text: string) => void;
  close: () => void;
}

function makeStream(): FakeStream {
  let controller!: ReadableStreamDefaultController<Uint8Array>;
  const body = new ReadableStream<Uint8Array>({
    start(c) {
      controller = c;
    },
  });
  const response = { ok: true, status: 200, body } as unknown as Response;
  return {
    response,
    send: (text) => controller.enqueue(new TextEncoder().encode(text)),
    close: () => controller.close(),
  };
}

function errorResponse(status: number): Response {
  return { ok: false, status, body: null } as unknown as Response;
}

let container: HTMLDivElement;
let root: Root;
let stream: FakeStream;
const fetchMock = vi.fn();
const webSocketSpy = vi.fn();

async function render(ui: ReactNode) {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await act(async () => root.render(ui));
}

async function flushMicrotasks() {
  await act(async () => {
    for (let i = 0; i < 10; i++) {
      await Promise.resolve();
    }
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  stream = makeStream();
  fetchMock.mockReset();
  fetchMock.mockResolvedValue(stream.response);
  vi.stubGlobal("fetch", fetchMock);
  vi.stubGlobal("WebSocket", webSocketSpy);
  reloadMocks.maybeReloadForLoopbackWsAuthFailure.mockReturnValue(false);
  Object.defineProperty(window, "__QUBIT_SESSION_TOKEN__", {
    configurable: true,
    writable: true,
    value: "session-token-1",
  });
  Object.defineProperty(window, "__QUBIT_AUTH_REQUIRED__", {
    configurable: true,
    writable: true,
    value: false,
  });
});

afterEach(async () => {
  await act(async () => root?.unmount());
  container?.remove();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("ChatSidebar SSE event feed", () => {
  it("uses the authenticated SSE reader and never opens a WebSocket", async () => {
    const { ChatSidebar } = await import("./ChatSidebar");

    await render(<ChatSidebar channel="chat-1" />);
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalled());

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toContain("/api/events/stream");
    const headers = init.headers as Headers;
    expect(headers.get("Accept")).toBe("text/event-stream");
    expect(headers.get("X-Qubit-Session-Token")).toBe("session-token-1");
    expect(headers.get("X-Api-Token")).toBe("session-token-1");
    expect(webSocketSpy).not.toHaveBeenCalled();
  });

  it("dispatches session.info frames to onSessionTitleChange", async () => {
    const onSessionTitleChange = vi.fn();
    const { ChatSidebar } = await import("./ChatSidebar");

    await render(
      <ChatSidebar channel="chat-1" onSessionTitleChange={onSessionTitleChange} />,
    );
    await flushMicrotasks();
    expect(fetchMock).toHaveBeenCalled();

    stream.send(
      'id: 3\ndata: {"method":"event","params":{"type":"session.info","payload":{"title":"Live title"}}}\n\n',
    );
    await flushMicrotasks();

    expect(onSessionTitleChange).toHaveBeenCalledWith("Live title");
  });

  it("dispatches a bare AG-UI dashboard.new_session_requested frame", async () => {
    const onDashboardNewSessionRequest = vi.fn();
    const { ChatSidebar } = await import("./ChatSidebar");

    await render(
      <ChatSidebar
        channel="chat-1"
        onDashboardNewSessionRequest={onDashboardNewSessionRequest}
      />,
    );
    await flushMicrotasks();

    stream.send(
      'id: 4\ndata: {"type":"dashboard.new_session_requested"}\n\n',
    );
    await flushMicrotasks();

    expect(onDashboardNewSessionRequest).toHaveBeenCalledTimes(1);
  });

  it("reconnects after the stream ends and resumes from the last event id", async () => {
    vi.useFakeTimers();
    const second = makeStream();
    fetchMock
      .mockResolvedValueOnce(stream.response)
      .mockResolvedValueOnce(second.response);
    const { ChatSidebar } = await import("./ChatSidebar");

    await render(<ChatSidebar channel="chat-1" />);
    await flushMicrotasks();
    expect(fetchMock).toHaveBeenCalledTimes(1);

    stream.send('id: 5\ndata: {"type":"RUN_STARTED"}\n\n');
    await flushMicrotasks();
    stream.close();
    await flushMicrotasks();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_000);
    });
    await flushMicrotasks();

    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls[1][0]).toContain("last_seq=5");
  });

  it("does not retry an auth rejection", async () => {
    vi.useFakeTimers();
    fetchMock.mockReset();
    fetchMock.mockResolvedValue(errorResponse(401));
    const { ChatSidebar } = await import("./ChatSidebar");

    await render(<ChatSidebar channel="chat-1" />);
    await flushMicrotasks();

    expect(container.textContent).toContain("events feed rejected (401)");

    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000);
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("routes a loopback 401 through stale-token recovery", async () => {
    reloadMocks.maybeReloadForLoopbackWsAuthFailure.mockReturnValue(true);
    fetchMock.mockReset();
    fetchMock.mockResolvedValue(errorResponse(401));
    const { ChatSidebar } = await import("./ChatSidebar");

    await render(<ChatSidebar channel="chat-1" />);
    await flushMicrotasks();

    expect(
      reloadMocks.maybeReloadForLoopbackWsAuthFailure,
    ).toHaveBeenCalledWith(4401);
    expect(container.textContent).not.toContain("events feed rejected");
  });

  it("aborts the stream on unmount", async () => {
    const { ChatSidebar } = await import("./ChatSidebar");

    await render(<ChatSidebar channel="chat-1" />);
    await flushMicrotasks();

    const signal = (fetchMock.mock.calls[0] as [string, RequestInit])[1]
      .signal as AbortSignal;
    expect(signal.aborted).toBe(false);

    await act(async () => root.unmount());
    expect(signal.aborted).toBe(true);
  });

  it("shows the terminal's resolved model over the default role", async () => {
    // /api/model/info (the default role) says test/model; the terminal grant
    // actually resolved qubit-go/deepseek-v4-flash. The MODEL card must show
    // the terminal's real model, not the default-role label.
    const { ChatSidebar } = await import("./ChatSidebar");

    await render(
      <ChatSidebar
        channel="chat-1"
        terminalModel={{
          provider: "opencode-go",
          model: "deepseek-v4-flash",
          contextTokens: 200000,
        }}
      />,
    );
    await flushMicrotasks();
    // Let the fallback /api/model/info read (test/model) settle too — the
    // terminal label must still win.
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });

    // The MODEL card's title carries the fully-qualified resolved model.
    expect(
      container.querySelector('[title="opencode-go/deepseek-v4-flash"]'),
    ).not.toBeNull();
    expect(container.textContent).toContain("deepseek-v4-flash");
    expect(
      container.querySelector('[title="test/model"]'),
    ).toBeNull();
  });

  it("falls back to the default-role model when no terminal grant is passed", async () => {
    const { ChatSidebar } = await import("./ChatSidebar");

    await render(<ChatSidebar channel="chat-1" />);
    await flushMicrotasks();

    expect(apiMocks.getModelInfo).toHaveBeenCalled();
    await vi.waitFor(() =>
      expect(
        container.querySelector('[title="test/model"]'),
      ).not.toBeNull(),
    );
  });

  it("shows live from the terminal grant while the events feed is still connecting", async () => {
    // The MODEL pill reports the model, not the SSE reader: with a resolved
    // PTY grant the terminal session runs that model even when the events
    // stream never opens (fetch hangs -> state stays "connecting").
    const { ChatSidebar } = await import("./ChatSidebar");
    fetchMock.mockImplementationOnce(() => new Promise(() => {}));

    await render(
      <ChatSidebar
        channel="chat-1"
        terminalModel={{
          provider: "opencode-go",
          model: "muse-spark-1.3-contributor",
        }}
      />,
    );
    await flushMicrotasks();

    expect(container.textContent).toContain("live");
    expect(container.textContent).not.toContain("connecting");
  });

  it("adds no nested <aside> landmark — the chat side panel owns the one (drift A31)", async () => {
    const { ChatSidebar } = await import("./ChatSidebar");

    await render(<ChatSidebar channel="chat-1" />);
    await flushMicrotasks();

    expect(container.querySelector("aside")).toBeNull();
    expect(container.querySelectorAll('[role="complementary"]').length).toBe(0);
  });

  it("relays a live session.updated model for OUR session to the host", async () => {
    // A per-session pick writes session.updated on the SSE feed; the sidebar
    // must surface the new stored model to the host (so the panel and the
    // ChatPage state agree) without a page reload — the reported defect.
    const onSessionModelChange = vi.fn();
    const { ChatSidebar } = await import("./ChatSidebar");

    await render(
      <ChatSidebar
        channel="chat-1"
        sessionId="dashboard-live"
        onSessionModelChange={onSessionModelChange}
      />,
    );
    await flushMicrotasks();

    stream.send(
      'id: 7\ndata: {"type":"CUSTOM","name":"session.updated","value":{"session_id":"dashboard-live","session":{"id":"dashboard-live","model":"deepseek-v4.1-flash"}}}\n\n',
    );
    await flushMicrotasks();

    expect(onSessionModelChange).toHaveBeenCalledWith("deepseek-v4.1-flash");
  });

  it("ignores a live session.updated for a DIFFERENT session (no cross-talk)", async () => {
    const onSessionModelChange = vi.fn();
    const { ChatSidebar } = await import("./ChatSidebar");

    await render(
      <ChatSidebar
        channel="chat-1"
        sessionId="dashboard-mine"
        onSessionModelChange={onSessionModelChange}
      />,
    );
    await flushMicrotasks();

    stream.send(
      'id: 8\ndata: {"type":"CUSTOM","name":"session.updated","value":{"session_id":"dashboard-other","session":{"id":"dashboard-other","model":"x"}}}\n\n',
    );
    await flushMicrotasks();

    expect(onSessionModelChange).not.toHaveBeenCalled();
  });

  it("does not re-read the model for a journal replay of other sessions", async () => {
    // The durable journal replays thousands of historical frames on connect.
    // Only OUR session (or an id-less broadcast) may refresh the model card;
    // otherwise a page load fans out one config/model read per history frame.
    const { ChatSidebar } = await import("./ChatSidebar");

    await render(<ChatSidebar channel="chat-1" sessionId="dashboard-mine" />);
    await flushMicrotasks();
    const before = apiMocks.getModelInfo.mock.calls.length;

    for (let i = 0; i < 20; i += 1) {
      stream.send(
        `id: ${100 + i}\ndata: {"type":"CUSTOM","name":"session.updated","value":{"session_id":"dashboard-other","session":{"id":"dashboard-other","model":"x"}}}\n\n`,
      );
    }
    await flushMicrotasks();

    expect(apiMocks.getModelInfo.mock.calls.length).toBe(before);
  });

  it("coalesces a burst of our own session frames into one model read", async () => {
    vi.useFakeTimers();
    const { ChatSidebar } = await import("./ChatSidebar");

    await render(<ChatSidebar channel="chat-1" sessionId="dashboard-mine" />);
    await flushMicrotasks();
    expect(apiMocks.getModelInfo).toHaveBeenCalled();
    const before = apiMocks.getModelInfo.mock.calls.length;

    for (let i = 0; i < 10; i += 1) {
      stream.send(
        `id: ${200 + i}\ndata: {"type":"CUSTOM","name":"session.updated","value":{"session_id":"dashboard-mine","session":{"id":"dashboard-mine","model":"deepseek-v4.1-flash"}}}\n\n`,
      );
    }
    await flushMicrotasks();
    expect(apiMocks.getModelInfo.mock.calls.length).toBe(before);
    await act(async () => {
      vi.advanceTimersByTime(250);
    });
    await flushMicrotasks();

    expect(apiMocks.getModelInfo.mock.calls.length).toBe(before + 1);
  });

  it("re-reads the effective model on a live model.changed frame", async () => {
    const { ChatSidebar } = await import("./ChatSidebar");

    await render(<ChatSidebar channel="chat-1" />);
    await flushMicrotasks();
    const before = apiMocks.getModelInfo.mock.calls.length;

    stream.send(
      'id: 9\ndata: {"type":"CUSTOM","name":"model.changed","value":{"provider":"opencode-go","model":"deepseek-v4.1-flash","scope":"main"}}\n\n',
    );
    // Live model events are coalesced with a short trailing window so a
    // journal replay of many model.changed frames is one re-read, not one each.
    await new Promise((resolve) => setTimeout(resolve, 250));
    await flushMicrotasks();

    expect(apiMocks.getModelInfo.mock.calls.length).toBeGreaterThan(before);
  });

  it("writes a bound session's model per-session (never the global set)", async () => {
    // With a sessionId the pick must PATCH the session, not POST the global
    // /api/model/set — otherwise two surfaces share one model and the pick
    // does not apply to the open conversation.
    const onSessionModelChange = vi.fn();
    const { ChatSidebar } = await import("./ChatSidebar");

    await render(
      <ChatSidebar
        channel="chat-1"
        sessionId="dashboard-bound"
        onSessionModelChange={onSessionModelChange}
      />,
    );
    await flushMicrotasks();

    // Open the picker so ModelPickerDialog mounts and records its props.
    const pickerButton = container.querySelector("button[title]");
    await act(async () => {
      pickerButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await flushMicrotasks();

    const onApply = pickerMocks.lastProps?.onApply;
    expect(typeof onApply).toBe("function");
    await act(async () => {
      await onApply!({ provider: "opencode-go", model: "deepseek-v4.1-flash" });
    });

    expect(apiMocks.patchChatSession).toHaveBeenCalledWith("dashboard-bound", {
      model: "deepseek-v4.1-flash",
      provider: "opencode-go",
    });
    expect(apiMocks.setModelAssignment).not.toHaveBeenCalled();
    expect(onSessionModelChange).toHaveBeenCalledWith("deepseek-v4.1-flash");
  });

  it("keeps the GLOBAL set path when no session is bound (/chat)", async () => {
    const { ChatSidebar } = await import("./ChatSidebar");

    await render(<ChatSidebar channel="chat-1" />);
    await flushMicrotasks();

    const pickerButton = container.querySelector("button[title]");
    await act(async () => {
      pickerButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await flushMicrotasks();

    const onApply = pickerMocks.lastProps?.onApply;
    await act(async () => {
      await onApply!({ provider: "opencode-go", model: "deepseek-v4.1-flash" });
    });

    expect(apiMocks.setModelAssignment).toHaveBeenCalled();
    expect(apiMocks.patchChatSession).not.toHaveBeenCalled();
  });

  it("prompts a reload after a successful main-model write (upstream parity)", async () => {
    // A main-slot write persists to config.yaml, but the RUNNING chat keeps its
    // model until its session is rebuilt — so the picker must offer the
    // confirm+reload, exactly like the reference ChatSidebar. Without it the
    // panel kept rendering the previous provider after a pick.
    apiMocks.setModelAssignment.mockResolvedValueOnce({
      ok: true,
      provider: "opencode-go",
      model: "deepseek-v4.1-flash",
      terminal_follows: true,
    });
    const { ChatSidebar } = await import("./ChatSidebar");

    await render(<ChatSidebar channel="chat-1" />);
    await flushMicrotasks();

    const pickerButton = container.querySelector("button[title]");
    await act(async () => {
      pickerButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await flushMicrotasks();

    const onApply = pickerMocks.lastProps?.onApply;
    await act(async () => {
      await onApply!({ provider: "opencode-go", model: "deepseek-v4.1-flash" });
    });

    // The confirm dialog is handed the short model name awaiting confirmation.
    expect(reloadConfirmMocks.lastProps?.model).toBe("deepseek-v4.1-flash");
  });

  it("does not prompt a reload when the terminal kept its own model", async () => {
    // `terminal_follows === false` means /chat refused the pick (below the
    // context floor); the write landed but the running chat did NOT take it, so
    // offering "reload to apply" would promise a switch the terminal did not
    // make. The honest notice is shown instead.
    apiMocks.setModelAssignment.mockResolvedValueOnce({
      ok: true,
      provider: "opencode-go",
      model: "deepseek-v4.1-flash",
      terminal_follows: false,
      terminal_detail: "Main model set. /chat keeps its current model.",
    });
    const { ChatSidebar } = await import("./ChatSidebar");

    await render(<ChatSidebar channel="chat-1" />);
    await flushMicrotasks();

    const pickerButton = container.querySelector("button[title]");
    await act(async () => {
      pickerButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await flushMicrotasks();

    const onApply = pickerMocks.lastProps?.onApply;
    await act(async () => {
      await onApply!({ provider: "opencode-go", model: "deepseek-v4.1-flash" });
    });

    expect(reloadConfirmMocks.lastProps?.model ?? null).toBeNull();
  });
});
