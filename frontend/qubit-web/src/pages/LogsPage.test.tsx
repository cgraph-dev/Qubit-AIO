// @vitest-environment jsdom
/**
 * /logs search — the free-text filter must ride the backend `?search=` param
 * (the vendored status router post-filters the tail) rather than filtering
 * client-side only.
 */
import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const apiMocks = vi.hoisted(() => ({
  getLogs: vi.fn(),
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

async function renderLogs() {
  const [{ default: LogsPage }, { I18nProvider }] = await Promise.all([
    import("./LogsPage"),
    import("@/i18n"),
  ]);
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  const ui: ReactNode = (
    <I18nProvider>
      <MemoryRouter initialEntries={["/logs"]}>
        <LogsPage />
      </MemoryRouter>
    </I18nProvider>
  );
  await act(async () => root.render(ui));
}

beforeEach(() => {
  apiMocks.getLogs.mockReset();
  apiMocks.getLogs.mockResolvedValue({
    file: "agent",
    lines: ["2026-09-23 07:00:00,000 INFO boot"],
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
    await waitFor(() => apiMocks.getLogs.mock.calls.length > 0);
    expect(apiMocks.getLogs).toHaveBeenCalledWith(
      expect.objectContaining({ file: "agent", lines: 100, search: "" }),
    );

    const input = container.querySelector<HTMLInputElement>(
      'input[aria-label="Search log lines"]',
    );
    expect(input).not.toBeNull();

    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(
        window.HTMLInputElement.prototype,
        "value",
      )?.set;
      setter?.call(input, "timeout");
      input?.dispatchEvent(new Event("input", { bubbles: true }));
      // Let the 300ms debounce fire.
      await new Promise((resolve) => setTimeout(resolve, 400));
    });

    await waitFor(() =>
      apiMocks.getLogs.mock.calls.some(
        (call) => call[0]?.search === "timeout",
      ),
    );
    expect(apiMocks.getLogs).toHaveBeenCalledWith(
      expect.objectContaining({ file: "agent", lines: 100, search: "timeout" }),
    );
  });
});
