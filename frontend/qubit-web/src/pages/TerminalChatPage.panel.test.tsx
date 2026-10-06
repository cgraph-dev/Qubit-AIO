// @vitest-environment jsdom
/**
 * `/chat` right-side panel contract.
 *
 * Pins the Qubit `/chat` + `/qubit-chat` (`ChatPage.tsx`) parity
 * surface on the terminal page: a collapsible `#chat-side-panel`
 * `role="complementary"` region containing the real terminal model profile
 * picker (its own labeled card), the lazy `ChatSidebar`, and `ChatSessionList`.
 * Also pins that the terminal test ids are not regressed by the layout move.
 */
import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const apiMocks = vi.hoisted(() => ({
  getTerminalProfiles: vi.fn(async () => [
    {
      id: "qubit-brain4",
      kind: "local",
      provider: "local",
      model: "brain4",
      locality: "local",
      enabled: true,
      default: false,
      context_tokens: 4096,
      min_context_tokens: 65536,
      meets_floor: false,
      code: "TERMINAL_CONTEXT_BELOW_MINIMUM",
      has_credential: true,
    },
    {
      id: "qubit-model",
      kind: "remote",
      provider: "opencode",
      model: "deepseek",
      locality: "remote",
      enabled: true,
      default: true,
      context_tokens: 65536,
      min_context_tokens: 65536,
      meets_floor: true,
      code: "",
      has_credential: true,
    },
    {
      id: "qubit-go",
      kind: "remote",
      provider: "opencode",
      model: "go",
      locality: "remote",
      enabled: true,
      default: false,
      context_tokens: 65536,
      min_context_tokens: 65536,
      meets_floor: true,
      code: "",
      has_credential: true,
    },
  ]),
  wsTicket: vi.fn(() => new Promise(() => {})),
  createChatSession: vi.fn(async () => ({
    session: { id: "dashboard-abc123def456" },
  })),
  setTerminalProfile: vi.fn(async () => ({
    profile_id: "qubit-model",
    meets_floor: true,
  })),
  listChatSessions: vi.fn(async () => ({
    sessions: [
      {
        id: "session-1",
        title: "Panel session",
        last_active: Date.now() / 1000,
        message_count: 2,
      },
    ],
    total: 1,
    limit: 30,
    offset: 0,
  })),
  // Canonical panel list (`source="canonical"` in TerminalChatPage) reads the
  // receipts store; rows carry the same render fields (id/title/...).
  getSessions: vi.fn(async () => ({
    sessions: [
      {
        id: "session-1",
        title: "Panel session",
        last_active: Date.now() / 1000,
        message_count: 2,
      },
    ],
    total: 1,
    limit: 30,
    offset: 0,
  })),
  // Read-only registries rendered by ChatToolsSummary in the panel.
  getToolsets: vi.fn(async () => []),
  getSkills: vi.fn(async () => []),
  getPlugins: vi.fn(async () => []),
  getMcpServers: vi.fn(async () => ({ servers: [] })),
  // Workspace picker (WORKSPACE card) reads this on mount.
  getChatWorkspaces: vi.fn(async () => ({
    projects: [],
    repos: [],
    default_cwd: "/home/trick",
    home: "/home/trick",
    scan_enabled: false,
  })),
}));

vi.mock("@/lib/api", () => ({
  api: {
    getTerminalProfiles: apiMocks.getTerminalProfiles,
    wsTicket: apiMocks.wsTicket,
    createChatSession: apiMocks.createChatSession,
    setTerminalProfile: apiMocks.setTerminalProfile,
    listChatSessions: apiMocks.listChatSessions,
    getSessions: apiMocks.getSessions,
    getToolsets: apiMocks.getToolsets,
    getSkills: apiMocks.getSkills,
    getPlugins: apiMocks.getPlugins,
    getMcpServers: apiMocks.getMcpServers,
    getChatWorkspaces: apiMocks.getChatWorkspaces,
  },
  // PTY resume/session reads go through the backoff wrapper (same rationale
  // as the ChatPage mock); passthrough matches a healthy backend.
  withBackoff: (fn: () => Promise<unknown>) => fn(),
  WsTicketError: class WsTicketError extends Error {
    code = "";
    detail = "";
  },
}));

vi.mock("@xterm/xterm", () => ({
  Terminal: class {
    cols = 80;
    rows = 24;
    unicode = { activeVersion: "11" };
    loadAddon() {}
    open() {}
    write() {}
    dispose() {}
    onData() {
      return { dispose() {} };
    }
    onResize() {
      return { dispose() {} };
    }
    onScroll() {
      return { dispose() {} };
    }
    attachCustomKeyEventHandler() {}
    getSelection() {
      return "";
    }
    clearSelection() {}
    focus() {}
    buffer = { active: { viewportY: 0, baseY: 0 } };
  },
}));
vi.mock("@xterm/addon-fit", () => ({ FitAddon: class { fit() {} } }));
vi.mock("@xterm/addon-unicode11", () => ({ Unicode11Addon: class {} }));
vi.mock("@xterm/addon-web-links", () => ({ WebLinksAddon: class {} }));

// ChatSidebar is lazy-imported; record the mount + props without opening the
// real SSE feed.
const sidebarMount = vi.hoisted(() => vi.fn());
vi.mock("@/components/ChatSidebar", () => ({
  ChatSidebar: (props: { channel?: string; profile?: string }) => {
    sidebarMount(props);
    return null;
  },
}));
vi.mock("@/contexts/useProfileScope", () => ({
  useProfileScope: () => ({ profile: "default" }),
}));
vi.mock("@/i18n", () => ({
  useI18n: () => ({
    t: {
      app: {
        nav: { chat: "Chat" },
        modelToolsSheetTitle: "Model",
        modelToolsSheetSubtitle: "& tools",
      },
      sessions: {
        title: "Sessions",
        noSessions: "No sessions",
        untitledSession: "Untitled",
        newChat: "New chat",
      },
      common: { loading: "Loading", retry: "Retry", refresh: "Refresh" },
    },
  }),
}));

class FakeWebSocket {
  static OPEN = 1;
  static instances: FakeWebSocket[] = [];
  url: string;
  readyState = 0;
  binaryType = "";
  onopen: ((ev: unknown) => void) | null = null;
  onmessage: ((ev: unknown) => void) | null = null;
  onerror: ((ev: unknown) => void) | null = null;
  onclose: ((ev: unknown) => void) | null = null;
  constructor(url: string) {
    this.url = url;
    FakeWebSocket.instances.push(this);
  }
  send() {}
  close() {}
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

async function renderTerminal() {
  const { default: TerminalChatPage } = await import("./TerminalChatPage");
  await render(
    <MemoryRouter initialEntries={["/chat"]}>
      <TerminalChatPage isActive />
    </MemoryRouter>,
  );
}

function panel(): HTMLElement | null {
  return container.querySelector("#chat-side-panel");
}

beforeEach(() => {
  apiMocks.getTerminalProfiles.mockClear();
  apiMocks.wsTicket.mockReset();
  apiMocks.wsTicket.mockImplementation(() => new Promise(() => {}));
  apiMocks.createChatSession.mockReset();
  apiMocks.createChatSession.mockResolvedValue({
    session: { id: "dashboard-abc123def456" },
  });
  apiMocks.listChatSessions.mockClear();
  apiMocks.getSessions.mockClear();
  sidebarMount.mockClear();
  FakeWebSocket.instances = [];
  vi.stubGlobal("WebSocket", FakeWebSocket);
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      disconnect() {}
      unobserve() {}
    },
  );
});

afterEach(async () => {
  await act(async () => root?.unmount());
  container?.remove();
  vi.unstubAllGlobals();
});

describe("Qubit TerminalChatPage right-side panel", () => {
  it("renders the complementary panel without the extra terminal-model card", async () => {
    await renderTerminal();
    await vi.waitFor(() => expect(panel()).not.toBeNull());

    const el = panel()!;
    expect(el.getAttribute("role")).toBe("complementary");
    // aria-label is the shared ChatPage "Model & tools" landmark label.
    expect(el.getAttribute("aria-label")).toBe("Model & tools");
    // Parity with the reference: model switching lives in the MODEL card, not a
    // separate "Chat terminal model / Set default" card.
    expect(el.querySelector('[data-testid="terminal-model-card"]')).toBeNull();
    expect(
      el.querySelector('[data-testid="terminal-profile-picker"]'),
    ).toBeNull();
  });

  it("mounts the lazy ChatSidebar and the session list in the panel", async () => {
    await renderTerminal();

    // ChatSidebar resolves through Suspense.
    await vi.waitFor(() => expect(sidebarMount).toHaveBeenCalled());
    const props = sidebarMount.mock.calls[0][0] as {
      channel?: string;
      profile?: string;
    };
    expect(props.profile).toBe("default");
    expect(props.channel).toMatch(/^qubit-terminal-/);

    // ChatSessionList is the real component; it fetches and renders sessions.
    await vi.waitFor(() =>
      expect(container.textContent).toContain("Panel session"),
    );
    // The panel list uses source="canonical", so it reads the receipts store.
    expect(apiMocks.getSessions).toHaveBeenCalled();
  });

  it("collapses and re-expands the panel with the a11y toggle", async () => {
    await renderTerminal();
    await vi.waitFor(() => expect(panel()).not.toBeNull());

    const collapse = Array.from(container.querySelectorAll("button")).find(
      (b) => b.getAttribute("aria-label") === "Collapse chat side panel",
    )!;
    expect(collapse).toBeDefined();
    expect(collapse.getAttribute("aria-expanded")).toBe("true");
    expect(collapse.getAttribute("aria-controls")).toBe("chat-side-panel");

    await act(async () => {
      collapse.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(panel()).toBeNull();
    expect(
      container.querySelector(
        'button[aria-label="Show chat side panel"]',
      ),
    ).not.toBeNull();
    expect(
      container
        .querySelector('button[aria-label="Show chat side panel"]')!
        .getAttribute("aria-expanded"),
    ).toBe("false");

    await act(async () => {
      container
        .querySelector('button[aria-label="Show chat side panel"]')!
        .dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(panel()).not.toBeNull();
  });

  it("keeps the terminal test ids when a grant is present", async () => {
    apiMocks.wsTicket.mockImplementation(async () => ({
      ticket: "tkt",
      expires_in: 60,
      scope: "pty",
      profile_id: "qubit-model",
      provider: "opencode",
      model: "deepseek",
      context_tokens: 65536,
      context_verified: false,
      context_window_source: "declared",
    }));
    await renderTerminal();
    // The terminal connects and the side panel renders; the extra chrome bar
    // (model badge + Reconnect) is gone for parity with the reference.
    await vi.waitFor(() => expect(FakeWebSocket.instances.length).toBe(1));
    expect(container.querySelector('[aria-label="Qubit terminal"]')).not.toBeNull();
    expect(panel()).not.toBeNull();
    expect(
      container.querySelector('[data-testid="terminal-active-model"]'),
    ).toBeNull();
    expect(
      container.querySelector('button[aria-label="Reconnect terminal"]'),
    ).toBeNull();
  });

  it("exposes model switching through the MODEL card, not a second picker", async () => {
    await renderTerminal();
    // The lazy ChatSidebar (which owns the MODEL card + `/api/model/set`)
    // resolves through Suspense; the extra terminal-profile picker is gone.
    await vi.waitFor(() => expect(sidebarMount).toHaveBeenCalled());
    expect(
      container.querySelector('[data-testid="terminal-profile-picker"]'),
    ).toBeNull();
  });

  it("resumes the ?resume session in the PTY WebSocket URL", async () => {
    apiMocks.wsTicket.mockImplementation(async () => ({
      ticket: "tkt",
      expires_in: 60,
      scope: "pty",
      profile_id: "qubit-go",
      provider: "opencode-go",
      model: "deepseek",
      context_tokens: 200000,
      context_verified: true,
    }));
    const { default: TerminalChatPage } = await import("./TerminalChatPage");
    await render(
      <MemoryRouter initialEntries={["/chat?resume=session-1"]}>
        <TerminalChatPage isActive />
      </MemoryRouter>,
    );
    await vi.waitFor(() =>
      expect(FakeWebSocket.instances.length).toBeGreaterThan(0),
    );
    expect(FakeWebSocket.instances[0].url).toContain("resume=session-1");
  });

  it("passes the canonical PTY session to the list as the active row", async () => {
    // The active session id handed to ChatSessionList must be the canonical
    // session the PTY is on (the resume target), so the list can highlight it
    // and sort it first even after a refresh.
    apiMocks.wsTicket.mockImplementation(async () => ({
      ticket: "tkt",
      expires_in: 60,
      scope: "pty",
      profile_id: "qubit-go",
      provider: "opencode-go",
      model: "deepseek",
      context_tokens: 200000,
      context_verified: true,
    }));
    const { default: TerminalChatPage } = await import("./TerminalChatPage");
    await render(
      <MemoryRouter initialEntries={["/chat?resume=session-1"]}>
        <TerminalChatPage isActive />
      </MemoryRouter>,
    );
    await vi.waitFor(() =>
      expect(container.textContent).toContain("Panel session"),
    );
    const active = container.querySelector('[aria-current="true"]');
    expect(active).not.toBeNull();
    expect(active!.className).toContain("qchat__session-row--active");
    expect(active!.textContent).toContain("Panel session");
  });

  it("starts a fresh PTY (no resume) when ?resume is absent", async () => {
    apiMocks.wsTicket.mockImplementation(async () => ({
      ticket: "tkt",
      expires_in: 60,
      scope: "pty",
      profile_id: "qubit-go",
      provider: "opencode-go",
      model: "deepseek",
      context_tokens: 200000,
      context_verified: true,
    }));
    await renderTerminal();
    await vi.waitFor(() =>
      expect(FakeWebSocket.instances.length).toBeGreaterThan(0),
    );
    expect(FakeWebSocket.instances[0].url).not.toContain("resume=");
  });

  it("tears down the PTY socket on unmount (no leaked terminal)", async () => {
    // Regression: the prior terminal must be closed on teardown, never leaked.
    apiMocks.wsTicket.mockImplementation(async () => ({
      ticket: "tkt",
      expires_in: 60,
      scope: "pty",
      profile_id: "qubit-go",
      provider: "opencode-go",
      model: "deepseek",
      context_tokens: 200000,
      context_verified: true,
    }));
    await renderTerminal();
    await vi.waitFor(() => expect(FakeWebSocket.instances.length).toBe(1));
    const first = FakeWebSocket.instances[0];
    const closeSpy = vi.spyOn(first, "close");

    await act(async () => {
      root?.unmount();
    });

    expect(closeSpy).toHaveBeenCalled();
  });

  it("starts a fresh server-minted session on New chat (no client resume)", async () => {
    // Regression (SESS-01/D1), new contract: New chat used to clear `?resume`
    // and reconnect, which the server mapped to the fixed default
    // `dashboard-chat`, so the SAME transcript reopened. Client-side minting
    // (createChatSession + resume=<newId>) was superseded: with no `?resume=`
    // the server emits no resume env and the vendored TUI forges a brand-new
    // session, while the rotated attach token keeps the new PTY from
    // inheriting the previous terminal. Pin the observable half: New chat
    // reconnects exactly once with a FRESH socket carrying no resume target
    // (and mints nothing up front).
    apiMocks.wsTicket.mockImplementation(async () => ({
      ticket: "tkt",
      expires_in: 60,
      scope: "pty",
      profile_id: "qubit-go",
      provider: "opencode-go",
      model: "deepseek",
      context_tokens: 200000,
      context_verified: true,
    }));
    apiMocks.createChatSession.mockResolvedValueOnce({
      session: { id: "dashboard-abc123def456" },
    });
    await renderTerminal();
    await vi.waitFor(() => expect(FakeWebSocket.instances.length).toBe(1));
    expect(FakeWebSocket.instances[0].url).not.toContain("resume=");

    const newChat = Array.from(container.querySelectorAll("button")).find((b) =>
      b.textContent?.includes("New chat"),
    );
    expect(newChat).toBeDefined();
    await act(async () => {
      newChat!.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    await vi.waitFor(() => expect(FakeWebSocket.instances.length).toBe(2));
    // Fresh PTY: no resume target, so the server forges a new session instead
    // of reopening the old transcript. Nothing is minted client-side.
    expect(FakeWebSocket.instances[1].url).not.toContain("resume=");
    expect(apiMocks.createChatSession).not.toHaveBeenCalled();
  });
});
