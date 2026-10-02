// @vitest-environment jsdom
/**
 * Open Viking Agent Hub page: mocked-live bot sessions + skills render,
 * and a 404ing bridge row renders its panel honestly disabled with
 * reason (no unconfigured state — the local engine is always present).
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
  isBridgeRowMissing: (e: unknown) =>
    e instanceof Error && (e.message.includes("404") || e.message.toLowerCase().includes("not found")),
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
  it("renders mocked-live bot sessions and skills", async () => {
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

  it("renders honestly disabled panels when bridge rows 404", async () => {
    vikingMocks.listVikingBotSessions.mockRejectedValue(new Error("HTTP 404 not found"));
    vikingMocks.listVikingSkills.mockRejectedValue(new Error("HTTP 404 not found"));
    await act(async () => {
      root.render(
        <MemoryRouter>
          <OpenVikingAgentHubPage />
        </MemoryRouter>,
      );
    });
    await waitFor(() => container.querySelector('[data-testid="viking-sessions-unavailable"]') !== null);
    expect(container.querySelector('[data-testid="viking-skills-unavailable"]')).not.toBeNull();
    expect(container.textContent).not.toContain("SERVER_UNCONFIGURED");
  });
});
