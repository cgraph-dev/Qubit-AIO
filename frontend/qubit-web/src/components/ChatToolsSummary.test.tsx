// @vitest-environment jsdom
/**
 * ChatToolsSummary — the read-only tools/skills/plugins/MCP summary added to
 * the `/chat` side panel (M1 boot item 5).
 *
 * Pins that the card reads the real endpoints via `@/lib/api`, renders the
 * real counts + short lists, links to the full pages, and degrades per-row
 * (never throws) when one endpoint fails — so it can never take down the
 * terminal panel it sits in.
 */
import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const apiMocks = vi.hoisted(() => ({
  getToolsets: vi.fn(),
  getSkills: vi.fn(),
  getPlugins: vi.fn(),
  getMcpServers: vi.fn(),
}));

vi.mock("@/lib/api", () => ({ api: apiMocks }));

let container: HTMLDivElement;
let root: Root;

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

async function render(ui: ReactNode) {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await act(async () => root.render(ui));
}

async function renderSummary(profile = "default") {
  const { ChatToolsSummary } = await import("./ChatToolsSummary");
  await render(
    <MemoryRouter initialEntries={["/chat"]}>
      <ChatToolsSummary profile={profile} />
    </MemoryRouter>,
  );
}

function count(testId: string): string | null {
  return (
    container.querySelector(`[data-testid="${testId}-count"]`)?.textContent ??
    null
  );
}

beforeEach(() => {
  apiMocks.getToolsets.mockReset();
  apiMocks.getSkills.mockReset();
  apiMocks.getPlugins.mockReset();
  apiMocks.getMcpServers.mockReset();
});

afterEach(async () => {
  await act(async () => root?.unmount());
  container?.remove();
});

describe("ChatToolsSummary", () => {
  beforeEach(() => {
    // Real shapes (trimmed) from the live endpoints.
    apiMocks.getToolsets.mockResolvedValue([
      {
        name: "web",
        label: "Web",
        description: "",
        platform: "qubit",
        platform_label: "Qubit",
        enabled: true,
        configured: true,
        tools: ["web_extract", "web_search"],
      },
      {
        name: "file",
        label: "File",
        description: "",
        platform: "qubit",
        platform_label: "Qubit",
        enabled: true,
        configured: true,
        tools: ["read_file", "write_file"],
      },
      {
        name: "disabled-set",
        label: "Disabled",
        description: "",
        platform: "qubit",
        platform_label: "Qubit",
        enabled: false,
        configured: false,
        tools: ["never_counted"],
      },
    ]);
    apiMocks.getSkills.mockResolvedValue([
      { name: "antigravity-cli", description: "", category: "a", enabled: true },
      { name: "blackbox", description: "", category: "a", enabled: true },
    ]);
    apiMocks.getPlugins.mockResolvedValue([
      {
        name: "qubit-boards",
        label: "Boards",
        description: "",
        icon: "",
        version: "1.0.0",
        tab: { path: "/qubit-boards" },
        entry: "x.js",
        has_api: true,
        source: "qubit-overlay",
      },
    ]);
    apiMocks.getMcpServers.mockResolvedValue({
      servers: [
        {
          name: "cgraph-graft",
          transport: "stdio",
          url: null,
          command: "graft",
          args: [],
          env: {},
          auth: null,
          enabled: true,
          tools: null,
          connected: false,
          status: "configured-not-connected",
        },
      ],
    });
  });

  it("renders real counts and short lists from the four endpoints", async () => {
    await renderSummary();
    await act(async () => {});

    expect(container.querySelector('[data-testid="chat-tools-summary"]')).not.toBeNull();
    // Tools = distinct tools across ENABLED toolsets (4), in 2 toolsets.
    expect(count("chat-tools-tools")).toBe("4");
    expect(container.textContent).toContain("in 2 toolsets");
    expect(container.textContent).toContain("web, file");
    expect(count("chat-tools-skills")).toBe("2");
    expect(container.textContent).toContain("antigravity-cli, blackbox");
    expect(count("chat-tools-plugins")).toBe("1");
    expect(container.textContent).toContain("Boards");
    expect(count("chat-tools-mcp")).toBe("1");
    expect(container.textContent).toContain("cgraph-graft");
    // Configured ≠ connected: the row reports both real numbers, matching the
    // TUI banner's connected-only headline and the backend `connected:false`.
    expect(container.textContent).toContain("configured · 0 connected");

    // The disabled toolset's tools are never counted.
    expect(container.textContent).not.toContain("never_counted");
  });

  it("links each registry to its full page", async () => {
    await renderSummary();
    await act(async () => {});

    const hrefs = Array.from(container.querySelectorAll("a")).map((a) =>
      a.getAttribute("href"),
    );
    expect(hrefs).toContain("/skills");
    expect(hrefs).toContain("/plugins");
    expect(hrefs).toContain("/mcp");
  });

  it("degrades per-row when one endpoint fails, without throwing", async () => {
    apiMocks.getMcpServers.mockRejectedValue(new Error("offline"));
    await renderSummary();
    await act(async () => {});

    // Failed row: honest "unavailable", no count.
    expect(
      container.querySelector('[data-testid="chat-tools-mcp-error"]'),
    ).not.toBeNull();
    expect(count("chat-tools-mcp")).toBeNull();
    // Other rows are unaffected.
    expect(count("chat-tools-skills")).toBe("2");
    expect(count("chat-tools-plugins")).toBe("1");
  });
});
