// @vitest-environment jsdom
/**
 * Qubit ChatPage slash-catalog wiring (item 1).
 *
 * The composer offers the merged live catalog (`GET
 * /api/chat/slash-commands` over the static registry table): typing
 * `/skill-` lists live skill rows, and a palette pick of an alias sends the
 * canonical completion text (never a raw alias the turn path would not
 * dispatch).
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
  getChatSlashCommands: vi.fn(async () => ({
    ok: true,
    commands: [
      {
        name: "/skill-probe-skill",
        description: "A live skill row",
        source: "skill",
      },
    ],
    total: 1,
    sources: { skill: "catalog:1" },
  })),
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
    getChatSlashCommands: apiMocks.getChatSlashCommands,
  },
  withBackoff: (fn: () => Promise<unknown>) => fn(),
  authedFetch: vi.fn(),
  streamChatTurn,
  CHAT_REASONING_LEVELS: [],
}));

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

function typeIntoComposer(text: string) {
  const box = container.querySelector<HTMLTextAreaElement>(
    'textarea[aria-label="Message Qubit"]',
  );
  expect(box).not.toBeNull();
  const setter = Object.getOwnPropertyDescriptor(
    HTMLTextAreaElement.prototype,
    "value",
  )?.set;
  expect(setter).toBeDefined();
  setter!.call(box, text);
  box!.dispatchEvent(new Event("input", { bubbles: true }));
}

beforeEach(() => {
  vi.useFakeTimers();
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

describe("Qubit ChatPage slash catalog", () => {
  it("offers live /skill-* rows from the slash catalog in the composer popup", async () => {
    await renderChat("dashboard-chat");
    await flush();

    await act(async () => {
      typeIntoComposer("/skill-probe");
    });
    await flush();

    const popup = container.querySelector('[aria-label="Slash commands"]');
    expect(popup).not.toBeNull();
    expect(popup!.textContent).toContain("/skill-probe-skill");
  });

  it("still offers static registry verbs when the catalog read fails", async () => {
    apiMocks.getChatSlashCommands.mockRejectedValueOnce(new Error("down"));
    await renderChat("dashboard-chat");
    await flush();

    await act(async () => {
      typeIntoComposer("/mod");
    });
    await flush();

    const popup = container.querySelector('[aria-label="Slash commands"]');
    expect(popup).not.toBeNull();
    expect(popup!.textContent).toContain("/model");
  });
});
