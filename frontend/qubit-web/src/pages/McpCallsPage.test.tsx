// @vitest-environment jsdom
/**
 * McpCallsPage — call tool posts server+tool+args through the confirm
 * round-trip; list/read resources render live rows; templates and
 * model-context stay disabled with the exact prerequisite reason.
 */
import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const apiMocks = vi.hoisted(() => ({
  callMcpTool: vi.fn(),
  listMcpResources: vi.fn(),
  readMcpResource: vi.fn(),
}));

vi.mock("@/lib/ocFamilyAdminApi", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/ocFamilyAdminApi")>();
  return { ...actual, ...apiMocks };
});

let container: HTMLDivElement;
let root: Root;
(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

async function waitFor(cond: () => boolean, timeoutMs = 5000) {
  const start = Date.now();
  while (!cond()) {
    if (Date.now() - start > timeoutMs) throw new Error("waitFor: timed out");
    await act(async () => {
      await new Promise((r) => setTimeout(r, 20));
    });
  }
}

async function renderPage() {
  const [{ default: McpCallsPage }] = await Promise.all([import("./McpCallsPage")]);
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  const ui: ReactNode = (
    <MemoryRouter initialEntries={["/admin/mcp-calls"]}>
      <McpCallsPage />
    </MemoryRouter>
  );
  await act(async () => root.render(ui));
}

function byTestId(id: string): HTMLElement | null {
  return container.querySelector(`[data-testid="${id}"]`);
}

function setInput(el: HTMLElement | null, value: string) {
  if (!el) throw new Error("missing input");
  const input = el as HTMLInputElement | HTMLTextAreaElement;
  const proto =
    input instanceof HTMLTextAreaElement
      ? window.HTMLTextAreaElement.prototype
      : window.HTMLInputElement.prototype;
  Object.getOwnPropertyDescriptor(proto, "value")?.set?.call(input, value);
  input.dispatchEvent(new Event("input", { bubbles: true }));
  input.dispatchEvent(new Event("change", { bubbles: true }));
}

beforeEach(() => {
  Object.values(apiMocks).forEach((fn) => fn.mockReset());
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

describe("McpCallsPage", () => {
  it("call tool posts server+tool+args", async () => {
    apiMocks.callMcpTool.mockResolvedValue({ state: "OK", toolName: "find" });
    await renderPage();
    setInput(byTestId("mc-server"), "graft");
    setInput(byTestId("mc-tool"), "find");
    setInput(byTestId("mc-args"), '{"q":"x"}');
    await act(async () => {
      byTestId("mc-invoke")?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await waitFor(() => apiMocks.callMcpTool.mock.calls.length > 0);
    expect(apiMocks.callMcpTool.mock.calls[0][0]).toEqual({
      server: "graft",
      tool: "find",
      args: { q: "x" },
    });
    await waitFor(() => byTestId("mc-result") !== null);
  });

  it("list resources renders the live rows", async () => {
    apiMocks.listMcpResources.mockResolvedValue({
      state: "OK",
      server: "graft",
      resources: [{ uri: "r1" }, { uri: "r2" }],
    });
    await renderPage();
    setInput(byTestId("mc-res-server"), "graft");
    await act(async () => {
      byTestId("mc-list-resources")?.dispatchEvent(
        new MouseEvent("click", { bubbles: true }),
      );
    });
    await waitFor(() => byTestId("mc-resources-list") !== null);
    expect(apiMocks.listMcpResources).toHaveBeenCalledWith("graft");
    expect(byTestId("mc-resources-list")?.textContent).toContain("r1");
  });

  it("read resource posts server+uri and renders contents", async () => {
    apiMocks.readMcpResource.mockResolvedValue({
      state: "OK",
      server: "graft",
      uri: "u1",
      contents: [{ text: "hello" }],
    });
    await renderPage();
    setInput(byTestId("mc-res-server"), "graft");
    setInput(byTestId("mc-read-uri"), "u1");
    await act(async () => {
      byTestId("mc-read-resource")?.dispatchEvent(
        new MouseEvent("click", { bubbles: true }),
      );
    });
    await waitFor(() => byTestId("mc-read-result") !== null);
    expect(apiMocks.readMcpResource).toHaveBeenCalledWith("graft", "u1");
  });

  it("templates + update-model-context are disabled with the exact prerequisite", async () => {
    await renderPage();
    const { DISABLED_MCP_ACTIONS } = await import("@/lib/ocFamilyAdminApi");
    expect(DISABLED_MCP_ACTIONS.map((d) => d.action).sort()).toEqual(
      ["mcp.app.listResourceTemplates", "mcp.app.updateModelContext"].sort(),
    );
    for (const d of DISABLED_MCP_ACTIONS) {
      const row = byTestId(`mc-disabled-${d.action}`);
      expect(row).not.toBeNull();
      expect(row?.querySelector("button")?.disabled).toBe(true);
      expect(row?.textContent).toContain(d.reason);
    }
  });
});
