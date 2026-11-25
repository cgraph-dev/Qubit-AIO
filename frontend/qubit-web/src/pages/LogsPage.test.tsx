// @vitest-environment jsdom
/**
 * /logs — the free-text filter must ride the backend `?search=` param, and the
 * page must use the vendored status router's additive `?offset=` page contract
 * (`qubit_cli/web_routers/status.py` get_logs) for "Load older". The page calls
 * the exported `fetchJSON` seam (lib/api.ts is owned elsewhere) so this test
 * mocks `fetchJSON` and asserts on the real request URL.
 */
import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import "./sakuga-test-dom";
import { typeIntoSakuga } from "@qubit/shared/sakuga";

const apiMocks = vi.hoisted(() => ({
  fetchJSON: vi.fn(),
}));

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, fetchJSON: apiMocks.fetchJSON };
});
vi.mock("@/plugins", () => ({ PluginSlot: () => null }));

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

async function renderLogs() {
  const [{ default: LogsPage }, { I18nProvider }, { PageHeaderProvider }] =
    await Promise.all([
      import("./LogsPage"),
      import("@/i18n"),
      import("@/contexts/PageHeaderProvider"),
    ]);
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  const ui: ReactNode = (
    <I18nProvider>
      <MemoryRouter initialEntries={["/logs"]}>
        <PageHeaderProvider pluginTabs={[]}>
          <LogsPage />
        </PageHeaderProvider>
      </MemoryRouter>
    </I18nProvider>
  );
  await act(async () => root.render(ui));
}

function requestedUrls(): string[] {
  return apiMocks.fetchJSON.mock.calls.map((call) => String(call[0]));
}

beforeEach(() => {
  apiMocks.fetchJSON.mockReset();
  apiMocks.fetchJSON.mockResolvedValue({
    file: "agent",
    lines: ["2026-09-23 07:00:00,000 INFO boot"],
    has_more: false,
    next_offset: null,
  });
});

afterEach(async () => {
  await act(async () => root?.unmount());
  container?.remove();
  vi.unstubAllGlobals();
});

describe("LogsPage search", () => {
  it("loads the tail and forwards a debounced search term to the backend", async () => {
    await renderLogs();
    await waitFor(() => apiMocks.fetchJSON.mock.calls.length > 0);
    expect(requestedUrls().some((u) => u.includes("file=agent&lines=100"))).toBe(
      true,
    );
    // Hermes parity + capture: the default view must be UNFILTERED. A narrowed
    // default level silently drops a severity class AND breaks paging (the
    // backend over-reads only offset+cap+1 raw lines before filtering), so the
    // default request must not send a `level` param at all.
    expect(requestedUrls().every((u) => !u.includes("level="))).toBe(true);

    const input = container.querySelector<HTMLElement>(
      'sakuga-input[aria-label="Search log lines"]',
    );
    expect(input).not.toBeNull();

    await act(async () => {
      typeIntoSakuga(input, "timeout");
      // Let the 300ms debounce fire.
      await new Promise((resolve) => setTimeout(resolve, 400));
    });

    await waitFor(() => requestedUrls().some((u) => u.includes("search=timeout")));
    expect(requestedUrls().some((u) => u.includes("search=timeout"))).toBe(true);
  });
});

describe("LogsPage pagination", () => {
  it("Load older sends the server next_offset and renders the older page", async () => {
    apiMocks.fetchJSON
      .mockResolvedValueOnce({
        file: "agent",
        lines: ["line-newest"],
        has_more: true,
        next_offset: 1,
        snapshot: "aa:bb:cc",
      })
      .mockResolvedValueOnce({
        file: "agent",
        lines: ["line-older"],
        has_more: false,
        next_offset: null,
      });

    await renderLogs();
    await waitFor(() => requestedUrls().some((u) => u.includes("lines=100")));
    expect(container.textContent).toContain("line-newest");

    const older = container.querySelector<HTMLButtonElement>(
      '[aria-label="Load older lines"]',
    );
    expect(older).not.toBeNull();
    expect(older?.disabled).toBe(false);

    await act(async () => {
      older?.click();
      await new Promise((resolve) => setTimeout(resolve, 50));
    });

    await waitFor(() => requestedUrls().some(
      (u) => u.includes("offset=1") && u.includes("snapshot=aa%3Abb%3Acc"),
    ));
    await waitFor(() => container.textContent?.includes("line-older") ?? false);
    expect(container.textContent).toContain("line-older");
    // The newest page is replaced by the older page (offset is server-driven).
    expect(container.textContent).not.toContain("line-newest");
  });
});

describe("LogsPage export", () => {
  it("exports server lines with exactly their original line breaks", async () => {
    const lines = ["one\r\n", "two\n", "\n", "three", "four"];
    apiMocks.fetchJSON.mockResolvedValue({ file: "agent", lines });
    const createObjectURL = vi.fn((_blob: Blob) => "blob:logs-fixture");
    const URLMock = Object.assign(class extends URL {}, { createObjectURL, revokeObjectURL: vi.fn() });
    vi.stubGlobal("URL", URLMock);
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});

    try {
      await renderLogs();
      const exportButton = container.querySelector<HTMLButtonElement>('[aria-label="Export log lines"]');
      expect(exportButton?.disabled).toBe(false);
      await act(async () => { exportButton?.click(); });
      const blob = createObjectURL.mock.calls[0][0];
      const contents = await new Promise<string>((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(String(reader.result));
        reader.onerror = () => reject(reader.error);
        reader.readAsText(blob);
      });
      expect(contents).toBe("one\r\ntwo\n\nthree\nfour");
    } finally {
      click.mockRestore();
    }
  });


  it("disables Export and names the truthful reason when no lines are loaded", async () => {
    apiMocks.fetchJSON.mockResolvedValue({
      file: "agent",
      lines: [],
      has_more: false,
      next_offset: null,
    });

    await renderLogs();
    await waitFor(() => apiMocks.fetchJSON.mock.calls.length > 0);

    const exportBtn = container.querySelector<HTMLButtonElement>(
      '[aria-label="Export log lines"]',
    );
    expect(exportBtn).not.toBeNull();
    expect(exportBtn?.disabled).toBe(true);
    expect(exportBtn?.getAttribute("title")).toContain("Nothing to export");
  });
});

describe("LogsPage detail view", () => {
  it("expands a line to show its index, level, and char count", async () => {
    apiMocks.fetchJSON.mockResolvedValue({
      file: "agent",
      lines: ["2026-09-23 07:00:00,000 ERROR boom"],
      has_more: false,
      next_offset: null,
    });

    await renderLogs();
    await waitFor(() => apiMocks.fetchJSON.mock.calls.length > 0);

    const row = container.querySelector<HTMLElement>(
      '[aria-label="Log line 1 detail"]',
    );
    expect(row).not.toBeNull();
    await act(async () => {
      row?.click();
      await new Promise((resolve) => setTimeout(resolve, 20));
    });

    expect(container.textContent).toContain("Line 1");
    expect(container.textContent).toContain("chars");
  });
});

it("ignores a slow older page after a new filter loads", async () => {
  let settleOlder: (value: { lines: string[] }) => void = () => {};
  apiMocks.fetchJSON
    .mockResolvedValueOnce({ lines: ["newest"], has_more: true, next_offset: 100 })
    .mockImplementationOnce(() => new Promise((resolve) => { settleOlder = resolve; }))
    .mockResolvedValueOnce({ lines: ["timeout in active filter"] });
  await renderLogs();
  const older = container.querySelector<HTMLButtonElement>('[aria-label="Load older lines"]');
  await act(async () => { older?.click(); });
  expect(requestedUrls().at(-1)).toContain("offset=100");
  const input = container.querySelector<HTMLElement>('sakuga-input[aria-label="Search log lines"]');
  await act(async () => {
    typeIntoSakuga(input, "timeout");
    await new Promise((resolve) => setTimeout(resolve, 350));
  });
  expect(requestedUrls().at(-1)).toContain("search=timeout");
  expect(requestedUrls().at(-1)).not.toContain("offset");
  await act(async () => { settleOlder({ lines: ["stale older page"] }); });
  expect(container.textContent).toContain("timeout in active filter");
  expect(container.textContent).not.toContain("stale older page");
});
