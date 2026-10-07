// @vitest-environment jsdom
import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const apiMocks = vi.hoisted(() => ({
  getModelInfo: vi.fn(async () => ({
    capabilities: { supports_reasoning: false },
    model: "test/model",
  })),
  patchChatSession: vi.fn(async () => ({
    ok: true,
    session: { id: "s-1", model: "deepseek-v4.1-flash" },
  })),
  setModelAssignment: vi.fn(async () => ({ ok: true, scope: "main" })),
}));

const reloadMocks = vi.hoisted(() => ({
  maybeReloadForLoopbackWsAuthFailure: vi.fn(() => false),
}));

// Captured ModelPickerDialog props so a test can invoke the real `onApply`
// without rendering the dialog UI.
const pickerProps = vi.hoisted(() => ({
  onApply: undefined as
    | ((args: {
        provider: string;
        model: string;
        confirmExpensiveModel?: boolean;
      }) => unknown)
    | undefined,
  onClose: undefined as (() => void) | undefined,
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
vi.mock("@/components/ModelPickerDialog", () => ({
  ModelPickerDialog: (props: {
    onApply?: (args: {
      provider: string;
      model: string;
      confirmExpensiveModel?: boolean;
    }) => unknown;
    onClose?: () => void;
  }) => {
    pickerProps.onApply = props.onApply;
    pickerProps.onClose = props.onClose;
    return null;
  },
}));
vi.mock("@/components/ModelReloadConfirm", () => ({
  ModelReloadConfirm: () => null,
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
  pickerProps.onApply = undefined;
  pickerProps.onClose = undefined;
  apiMocks.patchChatSession.mockClear();
  apiMocks.setModelAssignment.mockClear();
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

  it("writes the PER-SESSION model when bound to a session (no global set)", async () => {
    const onSessionModelChange = vi.fn();
    const { ChatSidebar } = await import("./ChatSidebar");

    await render(
      <ChatSidebar
        channel="chat-1"
        sessionId="dashboard-abc123"
        onSessionModelChange={onSessionModelChange}
      />,
    );
    await flushMicrotasks();

    // Open the picker (the model Button) so ModelPickerDialog mounts and its
    // onApply is captured.
    await act(async () => {
      container.querySelector("button")?.dispatchEvent(
        new MouseEvent("click", { bubbles: true }),
      );
    });
    await flushMicrotasks();
    expect(pickerProps.onApply).toBeTypeOf("function");

    await act(async () => {
      await pickerProps.onApply?.({
        provider: "opencode-go",
        model: "deepseek-v4.1-flash",
      });
    });

    // The per-session write fires with the picked model; the GLOBAL assignment
    // must NOT be touched when a session is bound.
    expect(apiMocks.patchChatSession).toHaveBeenCalledWith("dashboard-abc123", {
      model: "deepseek-v4.1-flash",
    });
    expect(apiMocks.setModelAssignment).not.toHaveBeenCalled();
    expect(onSessionModelChange).toHaveBeenCalledWith("deepseek-v4.1-flash");
  });

  it("falls back to the global assignment when no session is bound", async () => {
    const { ChatSidebar } = await import("./ChatSidebar");

    await render(<ChatSidebar channel="chat-1" />);
    await flushMicrotasks();

    await act(async () => {
      container.querySelector("button")?.dispatchEvent(
        new MouseEvent("click", { bubbles: true }),
      );
    });
    await flushMicrotasks();

    await act(async () => {
      await pickerProps.onApply?.({
        provider: "opencode-go",
        model: "deepseek-v4.1-flash",
      });
    });

    expect(apiMocks.setModelAssignment).toHaveBeenCalled();
    expect(apiMocks.patchChatSession).not.toHaveBeenCalled();
  });
});
