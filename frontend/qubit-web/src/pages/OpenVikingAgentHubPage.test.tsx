// @vitest-environment jsdom
/**
 * Open Viking Agent Hub page: mocked bot sessions + skills render, and
 * the typed SERVER_UNCONFIGURED state renders when the bridge reports
 * no configured server.
 */
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const vikingMocks = vi.hoisted(() => ({
  listVikingBotSessions: vi.fn(),
  listVikingSkills: vi.fn(),
}));

vi.mock("@/lib/ocFamilyVikingApi", () => ({
  listVikingBotSessions: vikingMocks.listVikingBotSessions,
  listVikingSkills: vikingMocks.listVikingSkills,
  isServerUnconfiguredPayload: (p: unknown) =>
    !!p && typeof p === "object" && (p as Record<string, unknown>).code === "SERVER_UNCONFIGURED",
  normalizeBotSessions: (p: unknown) => (p as { sessions: unknown[] }).sessions ?? [],
  normalizeVikingSkills: (p: unknown) => (p as { skills: unknown[] }).skills ?? [],
}));

import OpenVikingAgentHubPage from "@/pages/OpenVikingAgentHubPage";

let container: HTMLDivElement;
let root: Root;
(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

async function waitFor(cond: () => boolean, timeoutMs = 5000) {
  const start = Date.now();
  while (!cond()) {
    if (Date.now() - start > timeoutMs) throw new Error("waitFor: condition never became true");
    await act(async () => {
      await new Promise((r) => setTimeout(r, 20));
    });
  }
}

beforeEach(() => {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => {
    root.unmount();
  });
  container.remove();
  vi.clearAllMocks();
});

describe("OpenVikingAgentHubPage", () => {
  it("renders mocked bot sessions and skills", async () => {
    vikingMocks.listVikingBotSessions.mockResolvedValue({
      sessions: [{ id: "s1", title: "evening chat", state: "open" }],
    });
    vikingMocks.listVikingSkills.mockResolvedValue({
      skills: [{ id: "sk1", title: "summarize", description: "condenses threads" }],
    });
    await act(async () => {
      root.render(
        <MemoryRouter>
          <OpenVikingAgentHubPage />
        </MemoryRouter>,
      );
    });
    await waitFor(() => container.textContent?.includes("evening chat") ?? false);
    expect(container.querySelector('[data-testid="viking-bot-sessions"]')).not.toBeNull();
    expect(container.textContent).toContain("summarize");
    expect(container.querySelector('[data-testid="viking-skills"]')).not.toBeNull();
  });

  it("shows SERVER_UNCONFIGURED when the bridge reports no server", async () => {
    vikingMocks.listVikingBotSessions.mockResolvedValue({ code: "SERVER_UNCONFIGURED" });
    vikingMocks.listVikingSkills.mockResolvedValue({ code: "SERVER_UNCONFIGURED" });
    await act(async () => {
      root.render(
        <MemoryRouter>
          <OpenVikingAgentHubPage />
        </MemoryRouter>,
      );
    });
    await waitFor(() => container.textContent?.includes("SERVER_UNCONFIGURED") ?? false);
  });
});
