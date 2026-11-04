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

describe("FilesPage root-level download path resolution", () => {
  it("resolves an entry with an empty path from the listing directory + name", async () => {
    apiMocks.listFiles.mockResolvedValue({
      ok: true,
      root: "/repo",
      path: "/repo",
      locked_root: "/repo",
      can_change_path: false,
      entries: [
        {
          name: "AGENTS.md",
          path: "",
          is_directory: false,
          size: 10,
          mtime: 0,
          mime_type: null,
        },
      ],
    });
    apiMocks.readFile.mockResolvedValue({
      name: "AGENTS.md",
      data_url: "data:text/plain;base64,QQ==",
    });

    await renderFiles();
    await waitFor(() => container.textContent?.includes("AGENTS.md") === true);

    const download = container.querySelector<HTMLButtonElement>(
      'button[aria-label="Download AGENTS.md"]',
    );
    expect(download).not.toBeNull();
    await act(async () => {
      download?.click();
      await new Promise((resolve) => setTimeout(resolve, 20));
    });

    await waitFor(() => apiMocks.readFile.mock.calls.length > 0);
    expect(apiMocks.readFile).toHaveBeenCalledWith("/repo/AGENTS.md");
  });
});

describe("FilesPage path navigation", () => {
  it("shows the editable path + Go even when the listing is locked to a project", async () => {
    apiMocks.listFiles.mockResolvedValue({
      ok: true,
      root: "/repo",
      path: "/repo",
      locked_root: "/repo",
      can_change_path: false,
      entries: [],
    });

    await renderFiles();
    await waitFor(() => container.querySelector('input[aria-label="Path"]') !== null);

    const input = container.querySelector<HTMLInputElement>(
      'input[aria-label="Path"]',
    )!;
    const form = input.closest("form")!;
    await act(async () => {
      const setValue = Object.getOwnPropertyDescriptor(
        HTMLInputElement.prototype,
        "value",
      )?.set;
      setValue?.call(input, "/repo/docs");
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await act(async () => {
      form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    });

    await waitFor(() =>
      apiMocks.listFiles.mock.calls.some((call) => call[0] === "/repo/docs"),
    );
    expect(
      Array.from(container.querySelectorAll("button")).some(
        (button) => button.textContent?.trim() === "Go",
      ),
    ).toBe(true);
  });
});

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

describe("FilesPage request ordering", () => {
  it("aborts the previous listing and ignores its late response", async () => {
    const initial = deferred<Record<string, unknown>>();
    const firstNav = deferred<Record<string, unknown>>();
    const secondNav = deferred<Record<string, unknown>>();
    const secondPayload = {
      ok: true,
      root: "/repo",
      path: "/repo/other",
      locked_root: "/repo",
      can_change_path: false,
      entries: [
        {
          name: "second.txt",
          path: "/repo/other/second.txt",
          is_directory: false,
          size: 1,
          mtime: 0,
          mime_type: null,
        },
      ],
    };
    apiMocks.listFiles
      .mockImplementationOnce(() => initial.promise)
      .mockImplementationOnce(() => firstNav.promise)
      .mockImplementationOnce(() => secondNav.promise)
      // The listing's own `setCurrentPath` schedules a refresh effect; keep
      // it on the same view so the assertion measures the stale-response
      // guard rather than that follow-up.
      .mockResolvedValue(secondPayload);

    await renderFiles();
    const input = container.querySelector<HTMLInputElement>(
      'input[aria-label="Path"]',
    )!;
    const form = input.closest("form")!;
    const setPath = async (path: string) => {
      await act(async () => {
        const setValue = Object.getOwnPropertyDescriptor(
          HTMLInputElement.prototype,
          "value",
        )?.set;
        setValue?.call(input, path);
        input.dispatchEvent(new Event("input", { bubbles: true }));
      });
      await act(async () => {
        form.dispatchEvent(
          new Event("submit", { bubbles: true, cancelable: true }),
        );
      });
    };

    await setPath("/repo/docs");
    await waitFor(() => apiMocks.listFiles.mock.calls.length >= 2);
    const firstSignal = apiMocks.listFiles.mock.calls[1][1] as AbortSignal;

    await setPath("/repo/other");
    await waitFor(() => apiMocks.listFiles.mock.calls.length >= 3);
    expect(firstSignal.aborted).toBe(true);

    await act(async () => {
      secondNav.resolve(secondPayload);
      await new Promise((resolve) => setTimeout(resolve, 10));
    });
    await waitFor(() => container.textContent?.includes("second.txt") === true);

    // The stale response arrives last; it must not replace the newer view.
    await act(async () => {
      firstNav.resolve({
        ok: true,
        root: "/repo",
        path: "/repo/docs",
        locked_root: "/repo",
        can_change_path: false,
        entries: [
          {
            name: "first.txt",
            path: "/repo/docs/first.txt",
            is_directory: false,
            size: 1,
            mtime: 0,
            mime_type: null,
          },
        ],
      });
      await new Promise((resolve) => setTimeout(resolve, 10));
    });
    expect(container.textContent).toContain("second.txt");
    expect(container.textContent).not.toContain("first.txt");
  });

  it("announces a listing failure in a live alert region", async () => {
    apiMocks.listFiles.mockRejectedValue(new Error("permission denied"));
    await renderFiles();
    await waitFor(() => container.querySelector('[role="alert"]') !== null);
    const alert = container.querySelector('[role="alert"]')!;
    expect(alert.getAttribute("aria-live")).toBe("assertive");
    expect(alert.textContent?.trim().length).toBeGreaterThan(0);
  });
});
