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

import { FRESH_CHAT_SESSION_ID } from "./ChatSessionList";

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
    className,
    ...rest
  }: {
    children?: ReactNode;
    onClick?: () => void;
    className?: string;
    "aria-current"?: "true" | undefined;
  }) => (
    <div
      role="button"
      tabIndex={0}
      data-testid="session-row"
      className={className}
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

async function renderList(activeSessionId: string | null = null) {
  const { ChatSessionList } = await import("./ChatSessionList");
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await act(async () =>
    root.render(
      <MemoryRouter initialEntries={["/chat"]}>
        <LocationProbe />
        <ChatSessionList activeSessionId={activeSessionId} />
      </MemoryRouter>,
    ),
  );
}

/** Render the `/chat` variant: the canonical receipts store, which is the
 * store the PTY resumes and the one that pages + carries `is_active`. */
async function renderCanonicalList(activeSessionId: string | null = null) {
  const { ChatSessionList } = await import("./ChatSessionList");
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await act(async () =>
    root.render(
      <MemoryRouter initialEntries={["/chat"]}>
        <LocationProbe />
        <ChatSessionList activeSessionId={activeSessionId} source="canonical" />
      </MemoryRouter>,
    ),
  );
}

function canonicalSession(id: string, over: Record<string, unknown> = {}) {
  return {
    id,
    title: `Session ${id}`,
    name: null,
    source: "tui",
    message_count: 2,
    last_active: Date.now() / 1000 - 60,
    ...over,
  };
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
    // The component now passes the (empty) search query as the third arg; the
    // contract under test is which STORE is read, not the query default.
    expect(apiMocks.listChatSessions).toHaveBeenCalledWith(30, 0, "");
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

  it("emphasizes the active row with the animation hook and keeps aria-current", async () => {
    apiMocks.listChatSessions.mockResolvedValueOnce({
      sessions: [
        {
          id: "chat-1",
          title: "First chat",
          name: null,
          source: "qubit-chat",
          message_count: 3,
          last_active: Date.now() / 1000 - 120,
        },
        {
          id: "chat-2",
          title: "Second chat",
          name: null,
          source: "qubit-chat",
          message_count: 1,
          last_active: Date.now() / 1000 - 600,
        },
      ],
      total: 2,
      limit: 30,
      offset: 0,
    });
    await renderList("chat-1");
    await vi.waitFor(() =>
      expect(container.textContent).toContain("Second chat"),
    );

    const rows = container.querySelectorAll('[data-testid="session-row"]');
    expect(rows.length).toBe(2);

    // Active row: emphasis hook class + aria-current, and NOT the plain hover style.
    const active = rows[0] as HTMLElement;
    expect(active.className).toContain("qchat__session-row--active");
    expect(active.getAttribute("aria-current")).toBe("true");

    // Inactive row keeps today's look exactly (no emphasis hook).
    const inactive = rows[1] as HTMLElement;
    expect(inactive.className).not.toContain("qchat__session-row--active");
    expect(inactive.className).toContain("text-text-secondary");
    expect(inactive.getAttribute("aria-current")).toBeNull();
  });

  it("does not mark any row active when no session is selected", async () => {
    await renderList(null);
    await vi.waitFor(() =>
      expect(container.textContent).toContain("First chat"),
    );
    expect(
      container.querySelectorAll(".qchat__session-row--active").length,
    ).toBe(0);
  });

  it("pins an active session absent from the fetched list to the top", async () => {
    // A canonical/worker resume id is often not in the chat-store list. The
    // active row must still render first, highlighted, and labelled with it.
    await renderList("canonical-worker-42");
    await vi.waitFor(() =>
      expect(container.textContent).toContain("First chat"),
    );

    const rows = container.querySelectorAll('[data-testid="session-row"]');
    expect(rows.length).toBe(2);

    const active = rows[0] as HTMLElement;
    expect(active.textContent).toContain("canonical-worker-42");
    expect(active.className).toContain("qchat__session-row--active");
    expect(active.getAttribute("aria-current")).toBe("true");

    const other = rows[1] as HTMLElement;
    expect(other.textContent).toContain("First chat");
    expect(other.className).not.toContain("qchat__session-row--active");
    expect(other.getAttribute("aria-current")).toBeNull();
  });

  it("refetches when the window regains focus (live update)", async () => {
    await renderList();
    await vi.waitFor(() =>
      expect(apiMocks.listChatSessions).toHaveBeenCalledTimes(1),
    );
    await act(async () => {
      window.dispatchEvent(new Event("focus"));
    });
    await vi.waitFor(() =>
      expect(apiMocks.listChatSessions).toHaveBeenCalledTimes(2),
    );
  });

  it("clears the live-refresh interval on unmount", async () => {
    const clearSpy = vi.spyOn(window, "clearInterval");
    await renderList();
    await vi.waitFor(() =>
      expect(apiMocks.listChatSessions).toHaveBeenCalled(),
    );
    await act(async () => root.unmount());
    // Stop the shared afterEach from unmounting the same root twice.
    root = undefined as unknown as Root;
    expect(clearSpy).toHaveBeenCalled();
    clearSpy.mockRestore();
  });

  // ── /chat (canonical store) ──────────────────────────────────────────
  // The right-side panel on `/chat` reads the canonical receipts store,
  // which is the store the PTY resumes and the only one that pages.

  it("canonical: reads the receipts store, not the chat store", async () => {
    apiMocks.getSessions.mockResolvedValue({
      sessions: [canonicalSession("s-1")],
      total: 1,
      limit: 30,
      offset: 0,
    });
    await renderCanonicalList();
    await vi.waitFor(() =>
      expect(container.textContent).toContain("Session s-1"),
    );
    expect(apiMocks.getSessions).toHaveBeenCalledWith(30, 0, "", "recent");
    expect(apiMocks.listChatSessions).not.toHaveBeenCalled();
  });

  it("canonical: highlights only the selected session, not every live one", async () => {
    // `is_active` marks live sessions server-side — a background voice/WhatsApp
    // session stays true while you read an old thread. Lighting every one of
    // them reads as multiple active rows, so selection must win.
    apiMocks.getSessions.mockResolvedValue({
      sessions: [
        canonicalSession("s-live", { is_active: true }),
        canonicalSession("s-old", { is_active: false }),
      ],
      total: 2,
      limit: 30,
      offset: 0,
    });
    await renderCanonicalList("s-old");
    await vi.waitFor(() =>
      expect(container.textContent).toContain("Session s-old"),
    );

    const rows = Array.from(
      container.querySelectorAll<HTMLElement>('[data-testid="session-row"]'),
    );
    const activeRows = rows.filter((r) =>
      r.className.includes("qchat__session-row--active"),
    );
    expect(activeRows.length).toBe(1);

    const old = rows.find((r) => r.textContent?.includes("Session s-old"))!;
    expect(old.className).toContain("qchat__session-row--active");
    expect(old.getAttribute("aria-current")).toBe("true");

    const live = rows.find((r) => r.textContent?.includes("Session s-live"))!;
    expect(live.className).not.toContain("qchat__session-row--active");
    expect(live.getAttribute("aria-current")).toBeNull();
  });

  it("canonical: falls back to the server's live session when nothing is selected", async () => {
    // With no resume id (fresh load / after restart) `is_active` is the only
    // signal for which session the server considers current, so it takes the
    // single highlight rather than leaving the panel unmarked.
    apiMocks.getSessions.mockResolvedValue({
      sessions: [
        canonicalSession("s-live", { is_active: true }),
        canonicalSession("s-old", { is_active: false }),
      ],
      total: 2,
      limit: 30,
      offset: 0,
    });
    await renderCanonicalList(null);
    await vi.waitFor(() =>
      expect(container.textContent).toContain("Session s-live"),
    );

    const rows = Array.from(
      container.querySelectorAll<HTMLElement>('[data-testid="session-row"]'),
    );
    const activeRows = rows.filter((r) =>
      r.className.includes("qchat__session-row--active"),
    );
    expect(activeRows.length).toBe(1);
    expect(activeRows[0].textContent).toContain("Session s-live");
  });

  it("canonical: pages older sessions in with See more instead of dropping them", async () => {
    // 512 sessions on the live server vs a 30-row window: without paging the
    // older ones are unreachable.
    apiMocks.getSessions.mockResolvedValueOnce({
      sessions: [canonicalSession("s-1")],
      total: 2,
      limit: 30,
      offset: 0,
    });
    await renderCanonicalList();
    await vi.waitFor(() =>
      expect(container.textContent).toContain("Session s-1"),
    );

    const seeMore = Array.from(container.querySelectorAll("button")).find((b) =>
      b.textContent?.includes("See more"),
    )!;
    expect(seeMore).toBeTruthy();

    apiMocks.getSessions.mockResolvedValueOnce({
      sessions: [canonicalSession("s-2")],
      total: 2,
      limit: 30,
      offset: 1,
    });
    await act(async () => {
      seeMore.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    await vi.waitFor(() =>
      expect(container.textContent).toContain("Session s-2"),
    );
    // Appended, not replaced — the first page is still there.
    expect(container.textContent).toContain("Session s-1");
    expect(apiMocks.getSessions).toHaveBeenLastCalledWith(30, 1, "", "recent");
    // Nothing left to page, so the affordance goes away.
    expect(
      Array.from(container.querySelectorAll("button")).some((b) =>
        b.textContent?.includes("See more"),
      ),
    ).toBe(false);
  });

  it("canonical: hides See more when the first page covers the whole store", async () => {
    apiMocks.getSessions.mockResolvedValue({
      sessions: [canonicalSession("s-only")],
      total: 1,
      limit: 30,
      offset: 0,
    });
    await renderCanonicalList();
    await vi.waitFor(() =>
      expect(container.textContent).toContain("Session s-only"),
    );
    expect(
      Array.from(container.querySelectorAll("button")).some((b) =>
        b.textContent?.includes("See more"),
      ),
    ).toBe(false);
  });

  it("canonical: highlights NOTHING on a deliberate fresh chat (New chat)", async () => {
    // Regression: pressing "New chat" clears `?resume=`, which used to arrive
    // here as `null` — indistinguishable from "no selection known yet" — so the
    // panel fell through to the `is_active` fallback and lit an unrelated live
    // session (subagents and the PTY are often all `is_active` at once). The
    // operator saw the OLD conversation still highlighted after New chat.
    // `FRESH_CHAT_SESSION_ID` is the explicit "nothing is selected" signal and
    // must suppress the fallback entirely.
    apiMocks.getSessions.mockResolvedValue({
      sessions: [
        canonicalSession("s-live", { is_active: true }),
        canonicalSession("s-old", { is_active: false }),
      ],
      total: 2,
      limit: 30,
      offset: 0,
    });
    await renderCanonicalList(FRESH_CHAT_SESSION_ID);
    await vi.waitFor(() =>
      expect(container.textContent).toContain("Session s-live"),
    );

    const rows = Array.from(
      container.querySelectorAll<HTMLElement>('[data-testid="session-row"]'),
    );
    expect(rows.length).toBe(2);
    const activeRows = rows.filter((r) =>
      r.className.includes("qchat__session-row--active"),
    );
    expect(activeRows).toEqual([]);
    expect(
      rows.map((r) => r.getAttribute("aria-current")).filter(Boolean),
    ).toEqual([]);
    // The sentinel is not a session id: it must never be rendered as a row.
    expect(container.textContent).not.toContain(FRESH_CHAT_SESSION_ID);
  });

  it("canonical: a real selection still wins over a live sibling", async () => {
    // Guards the fix's boundary: suppressing the fallback for the fresh-chat
    // sentinel must not weaken selection for an actual id.
    apiMocks.getSessions.mockResolvedValue({
      sessions: [
        canonicalSession("s-live", { is_active: true }),
        canonicalSession("s-old", { is_active: false }),
      ],
      total: 2,
      limit: 30,
      offset: 0,
    });
    await renderCanonicalList("s-old");
    await vi.waitFor(() =>
      expect(container.textContent).toContain("Session s-old"),
    );

    const rows = Array.from(
      container.querySelectorAll<HTMLElement>('[data-testid="session-row"]'),
    );
    const activeRows = rows.filter((r) =>
      r.className.includes("qchat__session-row--active"),
    );
    expect(activeRows.length).toBe(1);
    expect(activeRows[0].textContent).toContain("Session s-old");
  });
});
