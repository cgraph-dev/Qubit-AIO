// @vitest-environment jsdom
/**
 * `/sessions` -> `/chat` resume routing.
 *
 * Selecting a session must land the terminal on the session that owns the row
 * AND in that row's profile store. The server scopes the PTY's HOME from the
 * `/api/pty?profile=` query, so an href that drops the profile resumes the id
 * against the dashboard's launch home (where it does not exist) and the user
 * gets a fresh chat instead of the transcript.
 */
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter, useLocation } from "react-router";
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
  setManagementProfile: vi.fn(),
  getManagementProfile: vi.fn(() => ""),
}));
vi.mock("@/components/PlatformsCard", () => ({ PlatformsCard: () => null }));
vi.mock("@/components/Markdown", () => ({
  Markdown: ({ content }: { content?: unknown }) => (
    <span>{content == null ? "" : String(content)}</span>
  ),
}));

function LocationProbe() {
  const { search } = useLocation();
  return <span data-testid="location">{search}</span>;
}

let container: HTMLDivElement;
let root: Root;
(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

async function waitFor(cond: () => boolean, timeoutMs = 5000) {
  const start = Date.now();
  while (!cond()) {
    if (Date.now() - start > timeoutMs) {
      throw new Error("waitFor: condition never became true");
    }
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 20));
    });
  }
}

function click(el: Element | null) {
  if (!el) throw new Error("element not rendered");
  el.dispatchEvent(
    new MouseEvent("click", { bubbles: true, cancelable: true }),
  );
}

function row(overrides: Record<string, unknown>) {
  return {
    id: "sid-1",
    profile: null,
    source: "tui",
    model: "prov/model",
    title: "A real session",
    started_at: 1,
    ended_at: null,
    last_active: 10,
    is_active: false,
    message_count: 3,
    tool_call_count: 0,
    input_tokens: 0,
    output_tokens: 0,
    preview: "hi",
    ...overrides,
  };
}

async function renderSessionsPage(rows: Record<string, unknown>[], overview: Record<string, unknown>[] = []) {
  apiMocks.getSessions.mockImplementation(async (limit: number) => ({
    sessions: limit >= 50 ? overview : rows,
    total: limit >= 50 ? overview.length : rows.length,
    limit,
    offset: 0,
  }));
  const [
    { default: SessionsPage },
    { I18nProvider },
    { SystemActionsProvider },
    { ProfileProvider },
    { PageHeaderProvider },
  ] = await Promise.all([
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
        <MemoryRouter initialEntries={["/sessions"]}>
          <SystemActionsProvider>
            <ProfileProvider>
              <PageHeaderProvider pluginTabs={[]}>
                <SessionsPage />
                <LocationProbe />
              </PageHeaderProvider>
            </ProfileProvider>
          </SystemActionsProvider>
        </MemoryRouter>
      </I18nProvider>,
    ),
  );
  await waitFor(() => Boolean(document.querySelector('button[aria-label="Session source"]')));
}

function locationSearch() {
  return document.querySelector('[data-testid="location"]')?.textContent ?? "";
}

beforeEach(() => {
  for (const fn of Object.values(apiMocks)) fn.mockReset();
  apiMocks.getStatus.mockResolvedValue({});
  apiMocks.getEmptySessionsCount.mockResolvedValue({ count: 0 });
  apiMocks.getProfiles.mockResolvedValue({ profiles: [] });
  apiMocks.getActiveProfile.mockResolvedValue({
    current: "default",
    active: "default",
  });
  apiMocks.getSessionStats.mockResolvedValue({ by_source: {} });
  apiMocks.getSessionMessages.mockResolvedValue({ messages: [] });
  apiMocks.deleteSession.mockResolvedValue({ ok: true });
  apiMocks.renameSession.mockResolvedValue({ ok: true, title: "Renamed" });
  apiMocks.patchChatSession.mockResolvedValue({ ok: true });
  apiMocks.exportSessionUrl.mockReturnValue("/api/sessions/x/export");
  vi.stubGlobal("fetch", vi.fn(async () => ({ ok: false, status: 500 })));
  vi.stubGlobal("ResizeObserver", class { disconnect() {} observe() {} unobserve() {} });
  vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) =>
    setTimeout(() => cb(0), 0) as unknown as number);
  vi.stubGlobal("cancelAnimationFrame", (id: number) => clearTimeout(id));
  vi.stubGlobal("matchMedia", () => ({
    addEventListener() {},
    matches: false,
    media: "",
    removeEventListener() {},
  }));
  sessionStorage.clear();
});

afterEach(async () => {
  await act(async () => root?.unmount());
  container?.remove();
  // Keep the frame primitives installed for the life of the file: GSAP can
  // schedule one ticker callback after unmount, and removing them first is a
  // load-dependent unhandled ReferenceError.
});

describe("SessionsPage resume routing", () => {
  it("opens the History row's owning profile in the chat resume route", async () => {
    await renderSessionsPage([row({ id: "sid-guanli", profile: "guanli" })]);
    await waitFor(() => Boolean(document.querySelector('[data-testid="session-row-sid-guanli"]')));

    const resume = document.querySelector(
      '[data-testid="session-row-sid-guanli"] button[aria-label="Resume in Chat"]',
    );
    await act(async () => click(resume));

    const params = new URLSearchParams(locationSearch());
    expect(params.get("resume")).toBe("sid-guanli");
    expect(params.get("profile")).toBe("guanli");
  });

  it("keeps the plain resume route for a row without a profile stamp", async () => {
    await renderSessionsPage([row({ id: "sid-default", profile: null })]);
    await waitFor(() => Boolean(document.querySelector('[data-testid="session-row-sid-default"]')));

    const resume = document.querySelector(
      '[data-testid="session-row-sid-default"] button[aria-label="Resume in Chat"]',
    );
    await act(async () => click(resume));

    const params = new URLSearchParams(locationSearch());
    expect(params.get("resume")).toBe("sid-default");
    expect(params.get("profile")).toBeNull();
  });

  it("offers resume on the Overview recent-session card without expanding it", async () => {
    await renderSessionsPage([], [row({ id: "sid-recent", profile: "guanli", title: "Recent one" })]);

    const card = document.querySelector('[data-testid="recent-session-sid-recent"]');
    expect(card).not.toBeNull();
    const resume = card?.querySelector('button[aria-label="Resume in Chat"]');
    expect(resume).not.toBeNull();

    // Keyboard activation must not double as the disclosure toggle.
    await act(async () => {
      resume!.dispatchEvent(
        new KeyboardEvent("keydown", { key: "Enter", bubbles: true }),
      );
    });
    expect(card?.querySelector('[role="button"]')?.getAttribute("aria-expanded")).toBe(
      "false",
    );

    await act(async () => click(resume ?? null));

    const params = new URLSearchParams(locationSearch());
    expect(params.get("resume")).toBe("sid-recent");
    expect(params.get("profile")).toBe("guanli");
    // The resume affordance must not be a disclosure trigger too.
    expect(apiMocks.getSessionMessages).not.toHaveBeenCalled();
  });
});
