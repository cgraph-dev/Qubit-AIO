// @vitest-environment jsdom
/**
 * `/qubit-chat` context ring + voice coexistence.
 *
 * The ring is the first absorbed hermes-webui component. This file pins the
 * two things that make adding it safe:
 *
 * 1. It reports honestly — with no turn receipt it renders the no-measurement
 *    state, and after a turn it reports `prompt_tokens / window`, never the
 *    session's cumulative `input_tokens`.
 * 2. It does not disturb the voice path: the Voice button still starts
 *    recognition, Right-Ctrl is still wired, and the read-aloud control the
 *    transcript renders is still present. If a future layout change mounts
 *    the ring by restructuring the composer, these assertions fail.
 */
import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const MODEL_WINDOW = 200_000;

const apiMocks = vi.hoisted(() => ({
  getModelInfo: vi.fn(),
  listChatSessions: vi.fn(async () => ({
    sessions: [{ id: "dashboard-chat" }],
    total: 1,
    limit: 100,
    offset: 0,
  })),
  getChatSessionMessages: vi.fn(async () => ({
    session_id: "dashboard-chat",
    messages: [
      // A long session: cumulative input far exceeds the window. The ring
      // must NOT use these.
      { role: "assistant", content: "earlier reply", meta: {} },
    ],
    total: 1,
  })),
  getChatSession: vi.fn(async () => ({
    session: { id: "dashboard-chat", input_tokens: 900_000, output_tokens: 40_000 },
    messages: [{ role: "assistant", content: "earlier reply", meta: {} }],
    message_count: 1,
    meta: {},
  })),
  getConfig: vi.fn(async () => ({ voice: { input_engine: "browser" } })),
  sttWsUrl: vi.fn(async () => "ws://127.0.0.1/agent/api/audio/stt/ws?ticket=t"),
  saveConfig: vi.fn(async () => ({ ok: true })),
  transcribeAudio: vi.fn(async () => ({ transcript: "spoken" })),
  describeChatImage: vi.fn(async () => ({ description: "described" })),
  getStatus: vi.fn(async () => ({ gateway_state: "stopped" })),
  getLogs: vi.fn(async () => ({ file: "agent", lines: [] })),
  getRuntime: vi.fn(async () => null),
  getChatSessionDrafts: vi.fn(async () => ({ drafts: [] })),
  getChatSessionComments: vi.fn(async () => ({ comments: [] })),
  getChatSessionCi: vi.fn(async () => ({ ci: [] })),
}));

const streamChatTurn = vi.hoisted(() => vi.fn());

vi.mock("@/lib/api", () => ({
  api: {
    getModelInfo: apiMocks.getModelInfo,
    listChatSessions: apiMocks.listChatSessions,
    getChatSessionMessages: apiMocks.getChatSessionMessages,
    getChatSession: apiMocks.getChatSession,
    getConfig: apiMocks.getConfig,
    sttWsUrl: apiMocks.sttWsUrl,
    saveConfig: apiMocks.saveConfig,
    transcribeAudio: apiMocks.transcribeAudio,
    describeChatImage: apiMocks.describeChatImage,
    getStatus: apiMocks.getStatus,
    getLogs: apiMocks.getLogs,
    getRuntime: apiMocks.getRuntime,
    getChatSessionDrafts: apiMocks.getChatSessionDrafts,
    getChatSessionComments: apiMocks.getChatSessionComments,
    getChatSessionCi: apiMocks.getChatSessionCi,
  },
  authedFetch: vi.fn(),
  streamChatTurn,
  withBackoff: (fn: () => Promise<unknown>) => fn(),
  CHAT_REASONING_LEVELS: [
    { id: "none", budget: 64 },
    { id: "high", budget: 1024 },
  ],
}));

vi.mock("@/components/ChatSessionList", () => ({ ChatSessionList: () => null }));
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

class FakeSR {
  static instances: FakeSR[] = [];
  lang = "";
  interimResults = false;
  maxAlternatives = 1;
  onresult: ((event: unknown) => void) | null = null;
  onerror: ((event: { error?: string }) => void) | null = null;
  onend: (() => void) | null = null;
  started = false;
  constructor() {
    FakeSR.instances.push(this);
  }
  start() {
    this.started = true;
  }
  stop() {
    this.started = false;
  }
  abort() {
    this.started = false;
  }
}

let container: HTMLDivElement;
let root: Root;

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

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

async function renderChat(isActive = true) {
  const { default: ChatPage } = await import("./ChatPage");
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await act(async () =>
    root.render(
      <MemoryRouter initialEntries={["/qubit-chat"]}>
        <ChatPage isActive={isActive} />
      </MemoryRouter> as ReactNode,
    ),
  );
}

function ring() {
  return container.querySelector<HTMLElement>('[data-testid="context-ring"]');
}

beforeEach(() => {
  FakeSR.instances = [];
  apiMocks.getModelInfo.mockReset();
  apiMocks.getModelInfo.mockImplementation(async () => ({
    model: "qubit-brain4",
    auto_context_length: MODEL_WINDOW,
    config_context_length: MODEL_WINDOW,
    effective_context_length: MODEL_WINDOW,
    capabilities: { supports_reasoning: true, context_window: MODEL_WINDOW },
  }));
  streamChatTurn.mockReset();
  vi.stubGlobal("localStorage", localStorageMock);
  localStorageMock.clear();
  vi.stubGlobal("fetch", vi.fn(async () => new Response("{}")));
});

afterEach(async () => {
  await act(async () => root?.unmount());
  container?.remove();
  vi.unstubAllGlobals();
  delete (window as { SpeechRecognition?: unknown }).SpeechRecognition;
});

describe("/qubit-chat context ring", () => {
  it("renders the no-measurement state before any turn has reported usage", async () => {
    await renderChat();
    const el = ring();
    expect(el).not.toBeNull();
    expect(el!.dataset.state).toBe("unmeasured");
    // Cumulative input_tokens (900k, from getChatSession) must never be
    // turned into a percentage.
    expect(el!.getAttribute("aria-label")).not.toMatch(/\d+%/);
  });

  it("reports prompt_tokens over the model window after a turn", async () => {
    streamChatTurn.mockImplementationOnce(
      async (
        _body: unknown,
        handlers: { onDone?: (event: unknown) => void },
      ) => {
        handlers.onDone?.({
          event: "done",
          reply: "ok",
          receipt: { usage: { prompt_tokens: 50_000 } },
        });
      },
    );
    await renderChat();

    // Same drive path the ChatPage suite uses: type into the real textarea,
    // then press the real Send button.
    const input = container.querySelector<HTMLTextAreaElement>(
      "#qubit-chat-input",
    )!;
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(
        HTMLTextAreaElement.prototype,
        "value",
      )!.set!;
      setter.call(input, "hello");
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await act(async () => {
      container
        .querySelector('button[aria-label="Send"]')!
        .dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    await vi.waitFor(() =>
      expect(ring()!.dataset.state).toBe("measured"),
    );
    const el = ring()!;
    expect(el.getAttribute("aria-label")).toContain("25% used");
    expect(el.getAttribute("aria-label")).toContain("50.0K");
    expect(el.getAttribute("aria-label")).toContain("200.0K");
  });
});

describe("/qubit-chat voice path survives the ring", () => {
  it("keeps the Voice button live in the same composer as the ring", async () => {
    Object.defineProperty(window, "SpeechRecognition", {
      configurable: true,
      value: FakeSR,
    });
    await renderChat();
    expect(ring()).not.toBeNull();
    const mic = container.querySelector('button[aria-label="Voice input"]');
    expect(mic).not.toBeNull();
    await act(async () => {
      mic!.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(FakeSR.instances.length).toBeGreaterThan(0);
    expect(FakeSR.instances[0].started).toBe(true);
  });

  it("keeps Right-Ctrl push-to-talk wired while the ring is mounted", async () => {
    Object.defineProperty(window, "SpeechRecognition", {
      configurable: true,
      value: FakeSR,
    });
    await renderChat();
    expect(ring()).not.toBeNull();
    await act(async () => {
      window.dispatchEvent(
        new KeyboardEvent("keydown", { code: "ControlRight", bubbles: true }),
      );
    });
    expect(FakeSR.instances.length).toBeGreaterThan(0);
    expect(FakeSR.instances[0].started).toBe(true);
  });

  it("keeps the per-message read-aloud control in the transcript", async () => {
    await renderChat();
    expect(ring()).not.toBeNull();
    // The loaded transcript carries one assistant message; the TTS control is
    // rendered per assistant message.
    const readAloud = container.querySelector('[data-testid^="read-aloud-"]');
    expect(readAloud).not.toBeNull();
  });
});
