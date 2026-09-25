// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const apiMocks = vi.hoisted(() => ({
  getSessions: vi.fn(),
  getSessionMessages: vi.fn(),
  getEmptySessionsCount: vi.fn(),
  getStatus: vi.fn(),
  searchSessions: vi.fn(),
  importSessions: vi.fn(),
  exportSessionUrl: vi.fn(),
  renameSession: vi.fn(),
  pruneSessions: vi.fn(),
  deleteSession: vi.fn(),
  deleteEmptySessions: vi.fn(),
  bulkDeleteSessions: vi.fn(),
  patchChatSession: vi.fn(),
  getProfiles: vi.fn(),
  getActiveProfile: vi.fn(),
  getSessionStats: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  api: apiMocks,
  // ProfileProvider mirrors its selection into the api module.
  setManagementProfile: vi.fn(),
  getManagementProfile: vi.fn(() => ""),
}));
vi.mock("@/components/PlatformsCard", () => ({ PlatformsCard: () => null }));
vi.mock("@/components/Markdown", () => ({ Markdown: () => null }));

let container: HTMLDivElement;
let root: Root;
(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

async function waitFor(cond: () => boolean, timeoutMs = 5000) {
  const start = Date.now();
  while (!cond()) {
    if (Date.now() - start > timeoutMs) throw new Error("waitFor: condition never became true");
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 20));
    });
  }
}

function click(el: Element | null) {
  if (!el) throw new Error("element not rendered");
  el.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
}

const button = (label: string) => document.querySelector(`button[aria-label="${label}"]`);

async function renderSessionsPage(
  rows: Record<string, unknown>[],
  overviewRows: Record<string, unknown>[] = [],
) {
  // Page list uses limit 20; the overview tab's recent-cards fetch uses 50 —
  // pass `overviewRows` to exercise the Overview panel, otherwise it stays
  // empty and the page falls back to the History list.
  apiMocks.getSessions.mockImplementation(async (limit: number) => ({
    sessions: limit >= 50 ? overviewRows : rows,
    total: limit >= 50 ? overviewRows.length : rows.length,
    limit,
    offset: 0,
  }));
  const [{ default: SessionsPage }, { I18nProvider }, { SystemActionsProvider }, { ProfileProvider }, { PageHeaderProvider }] =
    await Promise.all([
      import("./SessionsPage"),
      import("@/i18n"),
      import("@/contexts/SystemActions"),
      import("@/contexts/ProfileProvider"),
      import("@/contexts/PageHeaderProvider"),
    ]);
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await act(async () =>
    root.render(
      <I18nProvider>
        <MemoryRouter>
          <SystemActionsProvider>
            <ProfileProvider>
              <PageHeaderProvider pluginTabs={[]}>
                <SessionsPage />
              </PageHeaderProvider>
            </ProfileProvider>
          </SystemActionsProvider>
        </MemoryRouter>
      </I18nProvider>,
    ),
  );
  await waitFor(() => Boolean(button("Session source")));
}

beforeEach(() => {
  for (const fn of Object.values(apiMocks)) fn.mockReset();
  apiMocks.getStatus.mockResolvedValue({});
  apiMocks.getEmptySessionsCount.mockResolvedValue({ count: 0 });
  apiMocks.getProfiles.mockResolvedValue({ profiles: [] });
  // active === current keeps the management profile "" — the precondition
  // under which an unstamped request hits the process's own store.
  apiMocks.getActiveProfile.mockResolvedValue({ current: "default", active: "default" });
  apiMocks.getSessionStats.mockResolvedValue({ by_source: {} });
  apiMocks.getSessionMessages.mockResolvedValue({ messages: [] });
  apiMocks.deleteSession.mockResolvedValue({ ok: true });
  apiMocks.renameSession.mockResolvedValue({ ok: true, title: "Renamed" });
  apiMocks.patchChatSession.mockResolvedValue({ ok: true });
  apiMocks.exportSessionUrl.mockReturnValue("/api/sessions/x/export");
  vi.stubGlobal("fetch", vi.fn(async () => ({ ok: false, status: 500 })));
  vi.stubGlobal("ResizeObserver", class { disconnect() {} observe() {} unobserve() {} });
  // gsap ticks through rAF; a synchronous callback recurses to death.
  vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) => setTimeout(() => cb(0), 0) as unknown as number);
  vi.stubGlobal("cancelAnimationFrame", (id: number) => clearTimeout(id));
  vi.stubGlobal("matchMedia", () => ({ addEventListener() {}, matches: false, media: "", removeEventListener() {} }));
  sessionStorage.clear();
});

afterEach(async () => {
  await act(async () => root?.unmount());
  container?.remove();
  vi.unstubAllGlobals();
});

describe("SessionsPage per-row profile routing (#99387)", () => {
  it("sends every per-row request to the row's owning profile, not the management default", async () => {
    await renderSessionsPage([
      { id: "sid-guanli", profile: "guanli", source: "cli", model: null, title: "Managed", started_at: 1, ended_at: null,
        last_active: 1, is_active: false, message_count: 2, tool_call_count: 0, input_tokens: 1, output_tokens: 1, preview: "hi" },
    ]);

    // expand → transcript read
    await act(async () => click(button("Delete session")!.closest("div.cursor-pointer")));
    await waitFor(() => apiMocks.getSessionMessages.mock.calls.length > 0);
    expect(apiMocks.getSessionMessages).toHaveBeenCalledWith("sid-guanli", "guanli");

    await act(async () => click(button("Export session")));
    expect(apiMocks.exportSessionUrl).toHaveBeenCalledWith("sid-guanli", "guanli");

    await act(async () => click(button("Rename session")));
    const input = document.querySelector<HTMLInputElement>('input[placeholder="Session title"]');
    if (!input) throw new Error("rename input not rendered");
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input, "Renamed");
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await act(async () => click(button("Save title")));
    expect(apiMocks.renameSession).toHaveBeenCalledWith("sid-guanli", "Renamed", "guanli");

    await act(async () => click(button("Delete session")));
    await waitFor(() => Boolean(document.querySelector('[role="alertdialog"]')));
    const confirm = Array.from(document.querySelectorAll('[role="alertdialog"] button')).find(
      (b) => b.textContent?.trim() === "Delete",
    );
    await act(async () => click(confirm ?? null));
    expect(apiMocks.deleteSession).toHaveBeenCalledWith("sid-guanli", "guanli");
  });
});

describe("SessionsPage archived rows", () => {
  it("renders a Restore control that un-archives via the canonical chat-session store", async () => {
    await renderSessionsPage([
      {
        id: "sid-archived",
        profile: null,
        source: "qubit-chat",
        model: null,
        title: "Archived one",
        started_at: 1,
        ended_at: null,
        last_active: 1,
        is_active: false,
        archived: true,
        message_count: 1,
        tool_call_count: 0,
        input_tokens: 1,
        output_tokens: 1,
        preview: "hi",
      },
    ]);

    const restore = button("Restore session");
    expect(restore).not.toBeNull();
    // An archived row offers Restore, not Delete/Rename.
    expect(button("Delete session")).toBeNull();

    await act(async () => click(restore));
    expect(apiMocks.patchChatSession).toHaveBeenCalledWith("sid-archived", {
      archived: false,
    });
  });
});

/**
 * The Overview "Recent Sessions" card used to be inert metadata that looked
 * like an expandable History row (a dead affordance). It must open the
 * transcript on click.
 */
describe("SessionsPage overview recent-session cards", () => {
  const overviewRow = () => [
    {
      id: "sid-recent",
      profile: null,
      source: "cli",
      model: "prov/model",
      title: "Recent one",
      started_at: 1,
      ended_at: null,
      last_active: 1,
      is_active: false,
      message_count: 2,
      tool_call_count: 0,
      input_tokens: 0,
      output_tokens: 0,
      preview: "hello",
    },
  ];

  it("opens the transcript when a recent-session card is clicked", async () => {
    await renderSessionsPage([], overviewRow());
    const card = document.querySelector(
      '[data-testid="recent-session-sid-recent"]',
    );
    expect(card).not.toBeNull();
    const trigger = card?.querySelector<HTMLElement>('[role="button"]');
    expect(trigger).not.toBeNull();

    await act(async () => click(trigger ?? null));
    await waitFor(() => apiMocks.getSessionMessages.mock.calls.length > 0);
    expect(apiMocks.getSessionMessages).toHaveBeenCalledWith("sid-recent", null);
    expect(trigger?.getAttribute("aria-expanded")).toBe("true");
  });
});
describe("SessionsPage tab families + source popover dismissal", () => {
  const oneRow = () => [
    { id: "sid-1", profile: null, source: "cli", model: null, title: "One", started_at: 1,
      ended_at: null, last_active: 1, is_active: false, message_count: 1,
      tool_call_count: 0, input_tokens: 0, output_tokens: 0, preview: "hi" },
  ];

  it("renders the Chats/Automation/All and Overview/History tab families", async () => {
    await renderSessionsPage(oneRow());
    const labels = Array.from(document.querySelectorAll('[role="radio"]')).map(
      (node) => node.textContent?.trim(),
    );
    expect(labels).toEqual(
      expect.arrayContaining([
        "Chats",
        "Automation",
        "All",
        "Overview",
        "History",
      ]),
    );
  });

  it("switches between the Overview and History panels", async () => {
    await renderSessionsPage(oneRow(), oneRow());
    const radios = Array.from(
      document.querySelectorAll<HTMLElement>('[role="radio"]'),
    );
    const overview = radios.find((n) => n.textContent?.trim() === "Overview")!;
    const history = radios.find((n) => n.textContent?.trim() === "History")!;
    expect(overview).toBeTruthy();
    expect(history).toBeTruthy();

    await act(async () => click(overview));
    expect(overview.getAttribute("aria-checked")).toBe("true");
    expect(history.getAttribute("aria-checked")).toBe("false");

    await act(async () => click(history));
    expect(history.getAttribute("aria-checked")).toBe("true");
    expect(overview.getAttribute("aria-checked")).toBe("false");
  });

  it("dismisses the Session source popover on Escape", async () => {
    await renderSessionsPage(oneRow());
    const trigger = button("Session source");
    expect(trigger).toBeTruthy();
    expect(trigger!.getAttribute("aria-expanded")).toBe("false");

    await act(async () => click(trigger));
    expect(trigger!.getAttribute("aria-expanded")).toBe("true");

    await act(async () => {
      document.dispatchEvent(
        new KeyboardEvent("keydown", { key: "Escape", bubbles: true }),
      );
    });
    expect(trigger!.getAttribute("aria-expanded")).toBe("false");
  });

  it("de-brands the automation exclusion list without dropping real automation sources", async () => {
    await renderSessionsPage(oneRow());
    await waitFor(() =>
      apiMocks.getSessions.mock.calls.some(
        (call) =>
          (call[2] as { excludeSources?: string[] } | undefined)?.excludeSources,
      ),
    );
    // Default category is "Chats", which excludes the automation sources.
    const call = apiMocks.getSessions.mock.calls.find(
      (c) => (c[2] as { excludeSources?: string[] } | undefined)?.excludeSources,
    )!;
    const excluded = (call[2] as { excludeSources: string[] }).excludeSources;
    // The dead legacy session-source id has no backend emitter and is gone.
    expect(excluded).not.toContain("hermes_flow");
    // Every real automation source is still excluded from the Chats view.
    expect(excluded).toEqual(
      expect.arrayContaining([
        "cron",
        "tool",
        "api_server",
        "acp",
        "vulcan_delegate",
        "webhook",
      ]),
    );
  });
});
