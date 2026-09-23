// @vitest-environment jsdom
/**
 * /mcp tool invocation — the server row's Invoke control must call the
 * canonical confirm-gated `POST /api/mcp/servers/{name}/invoke` with the
 * selected tool + JSON args, and render the real result/refusal.
 */
import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const apiMocks = vi.hoisted(() => ({
  getMcpServers: vi.fn(),
  getMcpCatalog: vi.fn(),
  invokeMcpTool: vi.fn(),
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

async function renderPage() {
  const [{ default: McpPage }, { I18nProvider }] = await Promise.all([
    import("./McpPage"),
    import("@/i18n"),
  ]);
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  const ui: ReactNode = (
    <I18nProvider>
      <MemoryRouter initialEntries={["/mcp"]}>
        <McpPage />
      </MemoryRouter>
    </I18nProvider>
  );
  await act(async () => root.render(ui));
}

function setNativeValue(
  el: HTMLInputElement | HTMLTextAreaElement,
  value: string,
) {
  const proto =
    el instanceof HTMLTextAreaElement
      ? window.HTMLTextAreaElement.prototype
      : window.HTMLInputElement.prototype;
  Object.getOwnPropertyDescriptor(proto, "value")?.set?.call(el, value);
  el.dispatchEvent(new Event("input", { bubbles: true }));
}

beforeEach(() => {
  Object.values(apiMocks).forEach((fn) => fn.mockReset());
  apiMocks.getMcpServers.mockResolvedValue({
    servers: [
      {
        name: "cgraph-graft",
        transport: "stdio",
        url: null,
        command: "node",
        args: ["graft.js"],
        env: {},
        auth: null,
        enabled: true,
        tools: null,
        capabilities: [],
        invocable: true,
        invocation_code: null,
      },
    ],
  });
  apiMocks.getMcpCatalog.mockResolvedValue({ entries: [], diagnostics: [] });
  apiMocks.invokeMcpTool.mockResolvedValue({
    ok: true,
    state: "OK",
    result: { hits: 1 },
  });
});

afterEach(async () => {
  await act(async () => root?.unmount());
  container?.remove();
  vi.unstubAllGlobals();
});

describe("McpPage tool invocation", () => {
  it("invokes a tool with JSON args through the canonical route", async () => {
    await renderPage();
    await waitFor(() => apiMocks.getMcpServers.mock.calls.length > 0);
    await waitFor(
      () => container.querySelector('button[aria-label="Invoke a tool"]') !== null,
    );

    await act(async () => {
      container
        .querySelector<HTMLButtonElement>('button[aria-label="Invoke a tool"]')
        ?.click();
    });

    const toolInput = await (async () => {
      await waitFor(() => container.querySelector("#invoke-tool") !== null);
      return container.querySelector<HTMLInputElement>("#invoke-tool")!;
    })();
    const argsInput = container.querySelector<HTMLTextAreaElement>("#invoke-args")!;

    await act(async () => {
      setNativeValue(toolInput, "graft_find_code");
      setNativeValue(argsInput, '{"query":"x"}');
    });

    const invokeButton = Array.from(
      container.querySelectorAll<HTMLButtonElement>("button"),
    ).find((b) => b.textContent?.trim() === "Invoke");
    expect(invokeButton).toBeTruthy();

    await act(async () => {
      invokeButton?.click();
    });

    await waitFor(() => apiMocks.invokeMcpTool.mock.calls.length > 0);
    expect(apiMocks.invokeMcpTool).toHaveBeenCalledWith(
      "cgraph-graft",
      "graft_find_code",
      { query: "x" },
      expect.any(Function),
    );
  });
});
