// @vitest-environment jsdom
/**
 * /qubit-board-admin — tabs/widgets/roles/log render from the board family
 * client, and the sandbox-gated verbs (board.event, board.prompt.authorize,
 * board.action) render disabled-with-reason: code + verbatim detail,
 * prerequisite, and counterpart shown, and the buttons never fire.
 */
import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const boardMocks = vi.hoisted(() => ({
  listBoardTabs: vi.fn(),
  createBoardTab: vi.fn(),
  renameBoardTab: vi.fn(),
  moveBoardTab: vi.fn(),
  deleteBoardTab: vi.fn(),
  listBoardWidgets: vi.fn(),
  addBoardWidget: vi.fn(),
  removeBoardWidget: vi.fn(),
  getBoardRoster: vi.fn(),
  grantBoardRole: vi.fn(),
  getBoardLog: vi.fn(),
}));

vi.mock("@/lib/ocFamilyBoardApi", () => ({
  BOARD_EVENT_REFUSAL: {
    method: "board.event",
    code: "BOARD_EVENT_OPERATOR_REQUIRED",
    detail: "board.event appends a sandboxed widget notice; Qubit runs no board widget sandbox",
    prerequisite: "a board widget sandbox runtime + signed view tickets",
    counterpart: "`qubit board log` (kanban board event log)",
  },
  BOARD_PROMPT_AUTHORIZE_REFUSAL: {
    method: "board.prompt.authorize",
    code: "BOARD_VIEW_TICKET_OPERATOR_REQUIRED",
    detail: "board.prompt.authorize validates a signed board view ticket; Qubit issues no board view tickets",
    prerequisite: "a signed board view-ticket runtime",
    counterpart: "`qubit board roles` + the shared confirmation journal",
  },
  BOARD_ACTION_REFUSAL: {
    method: "board.action",
    code: "BOARD_ACTION_OPERATOR_REQUIRED",
    detail: "board.action runs a board capability verb through the widget sandbox/capability authority; Qubit runs neither",
    prerequisite: "a board widget sandbox + capability authority runtime",
    counterpart: "`qubit cron` (schedules) + the shared confirmation journal",
  },
  normalizeTabs: (v: unknown) => v,
  normalizeWidgets: (v: unknown) => v,
  normalizeRoles: (v: unknown) => v,
  normalizeEvents: (v: unknown) => v,
  ...boardMocks,
}));

vi.mock("@/contexts/usePageHeader", () => ({
  usePageHeader: () => ({ setAfterTitle: vi.fn(), setEnd: vi.fn() }),
}));

let container: HTMLDivElement;
let root: Root;
(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

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

async function renderPage() {
  const [{ default: BoardAdminPage }, { I18nProvider }] = await Promise.all([
    import("./BoardAdminPage"),
    import("@/i18n"),
  ]);
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  const ui: ReactNode = (
    <I18nProvider>
      <MemoryRouter initialEntries={["/qubit-board-admin"]}>
        <BoardAdminPage />
      </MemoryRouter>
    </I18nProvider>
  );
  await act(async () => root.render(ui));
}

function setNativeValue(el: HTMLInputElement, value: string) {
  const proto = window.HTMLInputElement.prototype;
  Object.getOwnPropertyDescriptor(proto, "value")?.set?.call(el, value);
  el.dispatchEvent(new Event("input", { bubbles: true }));
}

function buttonByText(text: string): HTMLButtonElement | null {
  return (
    [...document.querySelectorAll("button")].find(
      (b) => b.textContent?.trim() === text,
    ) ?? null
  );
}

beforeEach(() => {
  Object.values(boardMocks).forEach((fn) => fn.mockReset());
  boardMocks.listBoardTabs.mockResolvedValue([
    { slug: "ideas", name: "Ideas", position: 0 },
  ]);
  boardMocks.listBoardWidgets.mockResolvedValue([
    { id: "w1", kind: "note", title: "Shopping list", tab: "ideas" },
  ]);
  boardMocks.getBoardRoster.mockResolvedValue([
    { subject: "teammate", role: "viewer", scope: "" },
  ]);
  boardMocks.getBoardLog.mockResolvedValue([
    { id: "e1", kind: "tab_create", detail: "created ideas", at: "today" },
  ]);
  boardMocks.createBoardTab.mockResolvedValue({ ok: true });
  vi.stubGlobal(
    "ResizeObserver",
    class {
      disconnect() {}
      observe() {}
      unobserve() {}
    },
  );
  vi.stubGlobal("matchMedia", () => ({
    addEventListener() {},
    matches: false,
    media: "",
    removeEventListener() {},
  }));
});

afterEach(async () => {
  await act(async () => root?.unmount());
  container?.remove();
  vi.unstubAllGlobals();
});

describe("BoardAdminPage", () => {
  it("renders tabs, widgets, roster, and log from the family client", async () => {
    await renderPage();
    await waitFor(() => container.textContent?.includes("ideas") ?? false);
    expect(container.textContent).toContain("Shopping list");
    expect(container.textContent).toContain("teammate");
    expect(container.textContent).toContain("created ideas");
    expect(boardMocks.listBoardTabs).toHaveBeenCalled();
    expect(boardMocks.listBoardWidgets).toHaveBeenCalled();
    expect(boardMocks.getBoardRoster).toHaveBeenCalled();
    expect(boardMocks.getBoardLog).toHaveBeenCalled();
  });

  it("creates a tab through the family client", async () => {
    await renderPage();
    await waitFor(() => container.textContent?.includes("ideas") ?? false);
    setNativeValue(
      container.querySelector("#board-admin-new-slug") as HTMLInputElement,
      "later",
    );
    setNativeValue(
      container.querySelector("#board-admin-new-title") as HTMLInputElement,
      "Later",
    );
    await act(async () => buttonByText("Create tab")!.click());
    await waitFor(() => boardMocks.createBoardTab.mock.calls.length > 0);
    expect(boardMocks.createBoardTab).toHaveBeenCalledWith(
      "main",
      "later",
      "Later",
      expect.any(Function),
    );
  });

  it.each([
    ["BOARD_EVENT_OPERATOR_REQUIRED", "board.event appends a sandboxed widget notice; Qubit runs no board widget sandbox", "a board widget sandbox runtime + signed view tickets", "`qubit board log` (kanban board event log)"],
    ["BOARD_VIEW_TICKET_OPERATOR_REQUIRED", "board.prompt.authorize validates a signed board view ticket; Qubit issues no board view tickets", "a signed board view-ticket runtime", "`qubit board roles` + the shared confirmation journal"],
    ["BOARD_ACTION_OPERATOR_REQUIRED", "board.action runs a board capability verb through the widget sandbox/capability authority; Qubit runs neither", "a board widget sandbox + capability authority runtime", "`qubit cron` (schedules) + the shared confirmation journal"],
  ])(
    "renders %s disabled with its verbatim reason and never fires",
    async (code, detail, prerequisite, counterpart) => {
      await renderPage();
      await waitFor(() => container.textContent?.includes(code) ?? false);
      const row = container.querySelector(`[data-testid="refusal-${code}"]`);
      expect(row).not.toBeNull();
      expect(row!.textContent).toContain(detail);
      expect(row!.textContent).toContain(prerequisite);
      expect(row!.textContent).toContain(counterpart);
      const button = row!.querySelector("button") as HTMLButtonElement;
      expect(button.disabled).toBe(true);
      await act(async () => button.click());
      expect(boardMocks.createBoardTab).not.toHaveBeenCalled();
      expect(boardMocks.deleteBoardTab).not.toHaveBeenCalled();
      expect(boardMocks.addBoardWidget).not.toHaveBeenCalled();
    },
  );
});
