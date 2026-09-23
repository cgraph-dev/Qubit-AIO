// @vitest-environment jsdom
/**
 * /files drift — the empty-root reason must be surfaced (never a perpetual
 * "Loading"), and the listing response `detail` is the actionable operator
 * message ("register a project folder").
 */
import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const apiMocks = vi.hoisted(() => ({
  listFiles: vi.fn(),
  readFile: vi.fn(),
  uploadFile: vi.fn(),
  createDirectory: vi.fn(),
  deleteFile: vi.fn(),
}));

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, api: apiMocks };
});
vi.mock("@/plugins", () => ({ PluginSlot: () => null }));
vi.mock("@/contexts/usePageHeader", () => ({
  usePageHeader: () => ({ setAfterTitle: vi.fn(), setEnd: vi.fn() }),
}));

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

async function renderFiles() {
  const [{ default: FilesPage }, { I18nProvider }] = await Promise.all([
    import("./FilesPage"),
    import("@/i18n"),
  ]);
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  const ui: ReactNode = (
    <I18nProvider>
      <MemoryRouter initialEntries={["/files"]}>
        <FilesPage />
      </MemoryRouter>
    </I18nProvider>
  );
  await act(async () => root.render(ui));
}

beforeEach(() => {
  for (const fn of Object.values(apiMocks)) fn.mockReset();
  apiMocks.listFiles.mockResolvedValue({
    ok: true,
    root: null,
    path: "",
    locked_root: null,
    can_change_path: false,
    entries: [],
    detail:
      "no active project folder; register one with `qubit project add-folder`",
  });
});

afterEach(async () => {
  await act(async () => root?.unmount());
  container?.remove();
  vi.unstubAllGlobals();
});

describe("FilesPage empty-root reason", () => {
  it("surfaces the actionable API detail instead of a perpetual Loading", async () => {
    await renderFiles();
    await waitFor(() => container.textContent?.includes("project") === true);
    expect(container.textContent).toContain("no active project folder");
    expect(container.textContent).not.toContain("Loading");
  });
});
