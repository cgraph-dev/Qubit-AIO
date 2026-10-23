// @vitest-environment jsdom
/**
 * `/qubit-chat` image routing (owner fix).
 *
 * A pasted/dropped/picked IMAGE must be handled by the AGENT, not merely
 * described into the text-only local brain. The page routes it through
 * `api.submitChatImage` (`POST /api/qubit/chat/image`), which describes the
 * image with the canonical vision model AND submits one agent turn on the
 * session; the agent's reply renders in the transcript.
 *
 * DOCUMENTS keep the review-before-send contract: they are extracted
 * (`api.extractChatFile`) into the COMPOSER and never auto-submitted.
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
  getLogs: vi.fn(async () => ({ file: "agent", lines: [] as string[] })),
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
  // The fix: image -> one agent turn via the canonical vision + agent backend.
  submitChatImage: vi.fn<
    (
      dataUrl: string,
      session: string,
      prompt?: string,
    ) => Promise<{
      ok: boolean;
      reply: string;
      description: string;
      vision_model: string;
      receipt: { model: string };
    }>
  >(async () => ({
    ok: true,
    reply: "The image shows a bar chart of quarterly revenue.",
    description: "a bar chart",
    vision_model: "qwen3-vl:4b",
    receipt: { model: "qwen3-vl:4b" },
  })),
  // Documents keep the extract-to-composer path.
  extractChatFile: vi.fn(async () => ({
    text: "EXTRACTED DOCUMENT TEXT",
    kind: "pdf",
  })),
  describeChatImage: vi.fn(async () => ({ description: "described" })),
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
    getChatSession: apiMocks.getChatSession,
    patchChatSession: apiMocks.patchChatSession,
    submitChatImage: apiMocks.submitChatImage,
    extractChatFile: apiMocks.extractChatFile,
    describeChatImage: apiMocks.describeChatImage,
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

let container: HTMLDivElement;
let root: Root;

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

/** Wait out the FileReader stub + the mocked promise chain. */
async function flush() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
    await Promise.resolve();
  });
}

function imageFile(): File {
  return new File([new Uint8Array([137, 80, 78, 71])], "shot.png", {
    type: "image/png",
  });
}

function documentFile(): File {
  return new File([new Uint8Array([37, 80, 68, 70])], "report.pdf", {
    type: "application/pdf",
  });
}

/** Dispatch a real `paste` with an image on the clipboard. */
async function pasteImage(file: File) {
  await act(async () => {
    const event = new Event("paste", { bubbles: true, cancelable: true });
    Object.defineProperty(event, "clipboardData", {
      value: {
        items: [{ kind: "file", type: file.type, getAsFile: () => file }],
        files: [file],
      },
    });
    textarea().dispatchEvent(event);
  });
  await flush();
}

/** Drive the hidden attach input (used for documents). */
async function attachFile(file: File) {
  const input = container.querySelector(
    'input[type="file"]',
  ) as HTMLInputElement;
  await act(async () => {
    Object.defineProperty(input, "files", {
      value: [file],
      configurable: true,
    });
    input.dispatchEvent(new Event("change", { bubbles: true }));
  });
  await flush();
}

class FakeFileReader {
  result: string | null = null;
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  readAsDataURL() {
    this.result = "data:image/png;base64,AAAA";
    this.onload?.();
  }
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.stubGlobal("FileReader", FakeFileReader);
  vi.stubGlobal("WebSocket", vi.fn());
  vi.stubGlobal("localStorage", {
    getItem: () => null,
    setItem: () => {},
    removeItem: () => {},
    clear: () => {},
  });
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

describe("Qubit ChatPage image routing", () => {
  it("routes a pasted image to the agent and renders its reply", async () => {
    await renderChat();
    await pasteImage(imageFile());

    expect(apiMocks.submitChatImage).toHaveBeenCalledTimes(1);
    const [dataUrl, session, prompt] = apiMocks.submitChatImage.mock.calls[0];
    expect(dataUrl).toBe("data:image/png;base64,AAAA");
    expect(session).toBe("dashboard-chat");
    // No composer text -> the sensible default ask is used.
    expect(prompt).toBe("");
    // It must NOT have gone down the describe-only path.
    expect(apiMocks.describeChatImage).not.toHaveBeenCalled();
    expect(apiMocks.extractChatFile).not.toHaveBeenCalled();

    expect(container.textContent).toContain(
      "The image shows a bar chart of quarterly revenue.",
    );
  });

  it("uses the composer text as the image prompt when present", async () => {
    await renderChat();
    await act(async () => {
      const el = textarea();
      const setter = Object.getOwnPropertyDescriptor(
        HTMLTextAreaElement.prototype,
        "value",
      )?.set;
      setter?.call(el, "what is the error here?");
      el.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await pasteImage(imageFile());

    expect(apiMocks.submitChatImage).toHaveBeenCalledTimes(1);
    expect(apiMocks.submitChatImage.mock.calls[0][2]).toBe(
      "what is the error here?",
    );
  });

  it("keeps documents on the extract-to-composer path", async () => {
    await renderChat();
    await attachFile(documentFile());

    expect(apiMocks.extractChatFile).toHaveBeenCalledTimes(1);
    expect(apiMocks.submitChatImage).not.toHaveBeenCalled();
    // The extracted text lands in the composer for review — never auto-sent.
    expect(textarea().value).toContain("EXTRACTED DOCUMENT TEXT");
    expect(streamChatTurn).not.toHaveBeenCalled();
  });
});
