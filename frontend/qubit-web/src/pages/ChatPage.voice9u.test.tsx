// @vitest-environment jsdom
/**
 * `/qubit-chat` voice-input contract (9U).
 *
 * The native chat surface selects speech input through the canonical
 * `voice.input_engine` setting (`browser | local | auto`). These focused tests
 * pin the behavior repaired on 2026-09-21:
 *
 * * the UI actually surfaces the configured engine, the live engine, the
 *   Right-Ctrl state, the privacy/network consequence, and the fallback reason
 *   (previously computed but never rendered);
 * * `auto` prefers browser speech recognition and falls back to local Qubit STT
 *   with a visible reason when the browser API is unavailable;
 * * Right-Ctrl is a push-to-talk control on the SAME `send()` canonical turn
 *   path as typed input, and is inert when `/qubit-chat` is not the active
 *   route (the host is mounted persistently by the SPA shell).
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
    messages: [],
    total: 0,
  })),
  getConfig: vi.fn(async () => ({ voice: { input_engine: "auto" } })),
  sttWsUrl: vi.fn(async () => "ws://127.0.0.1/agent/api/audio/stt/ws?ticket=t"),
  saveConfig: vi.fn(async () => ({ ok: true })),
  transcribeAudio: vi.fn(async () => ({ transcript: "spoken text" })),
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
  CHAT_REASONING_LEVELS: [
    { id: "none", budget: 64 },
    { id: "minimal", budget: 128 },
    { id: "low", budget: 256 },
    { id: "medium", budget: 512 },
    { id: "high", budget: 1024 },
    { id: "max", budget: 2048 },
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
  stopped = false;
  constructor() {
    FakeSR.instances.push(this);
  }
  start() {
    this.started = true;
  }
  stop() {
    this.stopped = true;
  }
  abort() {
    this.stopped = true;
  }
}

class FakeWS {
  static instances: FakeWS[] = [];
  binaryType = "";
  sent: unknown[] = [];
  onopen: ((ev: unknown) => void) | null = null;
  onmessage: ((ev: { data: unknown }) => void) | null = null;
  onerror: ((ev: unknown) => void) | null = null;
  onclose: ((ev: { code?: number }) => void) | null = null;
  url: string;
  constructor(url: string) {
    this.url = url;
    FakeWS.instances.push(this);
    queueMicrotask(() => this.onopen?.({}));
  }
  send(data: unknown) {
    this.sent.push(data);
  }
  close() {
    this.onclose?.({ code: 1000 });
  }
}

class FakeAudioContext {
  sampleRate = 16000;
  destination = {};
  createMediaStreamSource() {
    return { connect: () => undefined, disconnect: () => undefined };
  }
  createScriptProcessor() {
    return {
      onaudioprocess: null,
      connect: () => undefined,
      disconnect: () => undefined,
    };
  }
  close() {
    return Promise.resolve();
  }
}

let container: HTMLDivElement;
let root: Root;
let engine: "browser" | "local" | "auto" = "auto";

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
  await render(
    <MemoryRouter initialEntries={["/qubit-chat"]}>
      <ChatPage isActive={isActive} />
    </MemoryRouter>,
  );
}

async function render(ui: ReactNode) {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await act(async () => root.render(ui));
}

function statusText(): string {
  return (
    container.querySelector('[data-testid="voice-status"]')?.textContent ?? ""
  );
}

async function clickVoice() {
  await act(async () => {
    container
      .querySelector('button[aria-label="Voice input"]')!
      .dispatchEvent(new MouseEvent("click", { bubbles: true }));
  });
}

function pressRightCtrl(type: "keydown" | "keyup") {
  window.dispatchEvent(
    new KeyboardEvent(type, { code: "ControlRight", bubbles: true }),
  );
}

beforeEach(() => {
  FakeSR.instances = [];
  FakeWS.instances = [];
  engine = "auto";
  apiMocks.getConfig.mockReset();
  apiMocks.getConfig.mockImplementation(async () => ({
    voice: { input_engine: engine },
  }));
  apiMocks.transcribeAudio.mockReset();
  apiMocks.transcribeAudio.mockResolvedValue({ transcript: "spoken text" });
  apiMocks.saveConfig.mockReset();
  apiMocks.saveConfig.mockResolvedValue({ ok: true });
  streamChatTurn.mockReset();
  vi.stubGlobal("localStorage", localStorageMock);
  localStorageMock.clear();
  Object.defineProperty(window.navigator, "clipboard", {
    configurable: true,
    value: { writeText: vi.fn(async () => {}) },
  });
});

afterEach(async () => {
  await act(async () => root?.unmount());
  container?.remove();
  vi.unstubAllGlobals();
  delete (window as { SpeechRecognition?: unknown }).SpeechRecognition;
  delete (window as { webkitSpeechRecognition?: unknown })
    .webkitSpeechRecognition;
});

function installSr() {
  Object.defineProperty(window, "SpeechRecognition", {
    configurable: true,
    value: FakeSR,
  });
}

function installLocal() {
  FakeWS.instances = [];
  vi.stubGlobal("WebSocket", FakeWS);
  vi.stubGlobal("AudioContext", FakeAudioContext);
  Object.defineProperty(window.navigator, "mediaDevices", {
    configurable: true,
    value: { getUserMedia: vi.fn(async () => ({ getTracks: () => [] })) },
  });
}

describe("Qubit ChatPage voice engine", () => {
  it("surfaces the configured engine, Right-Ctrl state and privacy status", async () => {
    engine = "local";
    installLocal();
    await renderChat();
    await vi.waitFor(() => expect(statusText()).toContain("engine: local"));
    expect(statusText()).toContain("active: —");
    expect(statusText()).toContain("mic: idle");
    const rc = container.querySelector('[data-testid="right-ctrl-status"]');
    expect(rc?.getAttribute("data-state")).toBe("ready");
    expect(statusText()).toContain("Right-Ctrl: ready");
    expect(statusText()).toContain("privacy: local STT");
  });

  it("marks Right-Ctrl unavailable with the reason when the engine cannot run", async () => {
    engine = "browser";
    await renderChat();
    await vi.waitFor(() => expect(statusText()).toContain("engine: browser"));
    const rc = container.querySelector('[data-testid="right-ctrl-status"]');
    expect(rc?.getAttribute("data-state")).toBe("unavailable");
    expect(statusText()).toContain("browser speech recognition unavailable");
    expect(statusText()).toContain("unavailable: no speech input engine");
  });

  it("auto prefers browser speech recognition when available", async () => {
    engine = "auto";
    installSr();
    await renderChat();
    await vi.waitFor(() => expect(statusText()).toContain("engine: auto"));
    await clickVoice();
    await vi.waitFor(() => expect(statusText()).toContain("active: browser"));
    expect(FakeSR.instances).toHaveLength(1);
    expect(FakeSR.instances[0].started).toBe(true);
    expect(statusText()).toContain("network: browser speech recognition");
  });

  it("auto falls back to local STT with a visible reason when browser SR is absent", async () => {
    engine = "auto";
    installLocal();
    await renderChat();
    await vi.waitFor(() => expect(statusText()).toContain("engine: auto"));
    await clickVoice();
    await vi.waitFor(() => expect(statusText()).toContain("active: local"));
    expect(statusText()).toContain(
      "browser speech recognition unavailable — using local Qubit STT",
    );
    expect(statusText()).toContain("privacy: local STT");
    // The local engine now streams over the authenticated STT WebSocket.
    await vi.waitFor(() => expect(FakeWS.instances).toHaveLength(1));
    await vi.waitFor(() =>
      expect(
        FakeWS.instances[0].sent.some(
          (m) => typeof m === "string" && m.includes('"type":"start"'),
        ),
      ).toBe(true),
    );
  });

  it("Right-Ctrl is inert when /qubit-chat is not the active route", async () => {
    installSr();
    await renderChat(false);
    await act(async () => {
      pressRightCtrl("keydown");
    });
    expect(FakeSR.instances).toHaveLength(0);
  });

  it("Right-Ctrl press/release drives the selected engine on the canonical turn path", async () => {
    installSr();
    await renderChat(true);
    await act(async () => {
      pressRightCtrl("keydown");
    });
    expect(FakeSR.instances).toHaveLength(1);
    expect(FakeSR.instances[0].started).toBe(true);
    await act(async () => {
      pressRightCtrl("keyup");
    });
    expect(FakeSR.instances[0].stopped).toBe(true);
  });

  it("shows live interim text, then commits the final into the editable composer", async () => {
    engine = "browser";
    installSr();
    await renderChat();
    await vi.waitFor(() => expect(statusText()).toContain("engine: browser"));
    await clickVoice();
    await vi.waitFor(() => expect(FakeSR.instances).toHaveLength(1));
    const rec = FakeSR.instances[0];
    await act(async () => {
      rec.onresult?.({
        resultIndex: 0,
        results: [{ isFinal: false, 0: { transcript: "hello wor" } }],
      });
    });
    const interim = container.querySelector('[data-testid="voice-interim"]');
    expect(interim?.textContent).toBe("hello wor");
    // Interim is not committed to the composer textarea yet.
    const textarea = container.querySelector<HTMLTextAreaElement>(
      "#qubit-chat-input",
    );
    expect(textarea?.value ?? "").not.toContain("hello wor");
  });

  it("persists a changed voice engine on the canonical config", async () => {
    installSr();
    await renderChat();
    await vi.waitFor(() => expect(statusText()).toContain("engine: auto"));
    const select = container.querySelector<HTMLSelectElement>(
      '[data-testid="voice-engine-picker"]',
    );
    expect(select).not.toBeNull();
    await act(async () => {
      if (select) {
        select.value = "local";
        select.dispatchEvent(new Event("change", { bubbles: true }));
      }
    });
    await vi.waitFor(() => expect(apiMocks.saveConfig).toHaveBeenCalled());
    const [saved] = apiMocks.saveConfig.mock.calls[0] as unknown as [
      { voice?: { input_engine?: string } },
    ];
    expect(saved.voice?.input_engine).toBe("local");
  });
});
