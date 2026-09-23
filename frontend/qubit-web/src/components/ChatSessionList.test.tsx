// @vitest-environment jsdom
/**
 * ChatSessionList contract.
 *
 * The Chat sidebar is server-backed by the Qubit-native chat-session store
 * (`GET /api/qubit/chat/sessions`), NOT the receipts `GET /api/sessions`
 * store. These tests pin the store call, the rendered rows, refresh, and the
 * `?resume=<id>` selection that drives the transcript load.
 */
import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter, useLocation } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const apiMocks = vi.hoisted(() => ({
  listChatSessions: vi.fn(),
  getSessions: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  api: {
    listChatSessions: apiMocks.listChatSessions,
    getSessions: apiMocks.getSessions,
  },
}));

vi.mock("@/i18n", () => ({
  useI18n: () => ({
    t: {
      common: { loading: "Loading", refresh: "Refresh", retry: "Retry" },
      sessions: {
        title: "Chats",
        noSessions: "No sessions",
        untitledSession: "Untitled",
        newChat: "New chat",
      },
    },
  }),
}));

vi.mock("@nous-research/ui/ui/components/button", () => ({
  Button: ({
    children,
    onClick,
    prefix,
    ...rest
  }: {
    children?: ReactNode;
    onClick?: () => void;
    prefix?: ReactNode;
    "aria-label"?: string;
    title?: string;
  }) => (
    <button
      type="button"
      onClick={onClick}
      aria-label={rest["aria-label"]}
      title={rest.title}
    >
      {prefix}
      {children}
    </button>
  ),
}));

vi.mock("@nous-research/ui/ui/components/list-item", () => ({
  ListItem: ({
    children,
    onClick,
    ...rest
  }: {
    children?: ReactNode;
    onClick?: () => void;
    "aria-current"?: "true" | undefined;
  }) => (
    <div
      role="button"
      tabIndex={0}
      data-testid="session-row"
      onClick={onClick}
      aria-current={rest["aria-current"]}
    >
      {children}
    </div>
  ),
}));

vi.mock("@nous-research/ui/ui/components/spinner", () => ({
  Spinner: () => <span data-testid="spinner" />,
}));

function LocationProbe() {
  return <span data-testid="location">{useLocation().search}</span>;
}

let container: HTMLDivElement;
let root: Root;

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

async function renderList() {
  const { ChatSessionList } = await import("./ChatSessionList");
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await act(async () =>
    root.render(
      <MemoryRouter initialEntries={["/chat"]}>
        <LocationProbe />
        <ChatSessionList activeSessionId={null} />
      </MemoryRouter>,
    ),
  );
}

beforeEach(() => {
  apiMocks.listChatSessions.mockReset();
  apiMocks.getSessions.mockReset();
  apiMocks.listChatSessions.mockResolvedValue({
    sessions: [
      {
        id: "chat-1",
        title: "First chat",
        name: null,
        source: "qubit-chat",
        message_count: 3,
        last_active: Date.now() / 1000 - 120,
      },
    ],
    total: 1,
    limit: 30,
    offset: 0,
  });
});

afterEach(async () => {
  await act(async () => root?.unmount());
  container?.remove();
});

describe("ChatSessionList", () => {
  it("lists the server-backed chat store, not the receipts store", async () => {
    await renderList();
    await vi.waitFor(() =>
      expect(container.textContent).toContain("First chat"),
    );
    expect(apiMocks.listChatSessions).toHaveBeenCalledWith(30, 0);
    expect(apiMocks.getSessions).not.toHaveBeenCalled();
    expect(container.textContent).toContain("3 msgs");
  });

  it("selects a row by setting ?resume=<id>", async () => {
    await renderList();
    await vi.waitFor(() =>
      expect(container.textContent).toContain("First chat"),
    );
    await act(async () => {
      container
        .querySelector('[data-testid="session-row"]')!
        .dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await vi.waitFor(() =>
      expect(container.querySelector('[data-testid="location"]')?.textContent).toBe(
        "?resume=chat-1",
      ),
    );
  });

  it("refetches on Refresh", async () => {
    await renderList();
    await vi.waitFor(() =>
      expect(container.textContent).toContain("First chat"),
    );
    await act(async () => {
      container
        .querySelector('button[aria-label="Refresh"]')!
        .dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await vi.waitFor(() =>
      expect(apiMocks.listChatSessions).toHaveBeenCalledTimes(2),
    );
  });

  it("renders the empty state when the store has no sessions", async () => {
    apiMocks.listChatSessions.mockResolvedValueOnce({
      sessions: [],
      total: 0,
      limit: 30,
      offset: 0,
    });
    await renderList();
    await vi.waitFor(() =>
      expect(container.textContent).toContain("No sessions"),
    );
  });

  it("adds no nested <aside> landmark — the chat side panel owns the one", async () => {
    await renderList();
    expect(container.querySelector("aside")).toBeNull();
    expect(container.querySelectorAll('[role="complementary"]').length).toBe(0);
  });
});
