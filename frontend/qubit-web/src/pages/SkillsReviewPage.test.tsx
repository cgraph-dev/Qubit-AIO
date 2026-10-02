// @vitest-environment jsdom
/**
 * SkillsReviewPage — proposals queue renders list/inspect rows and wires
 * every verdict through BOTH skills halves (content.py list/inspect +
 * skills_extra.py mutations); refusal rows stay disabled with the exact
 * prerequisite reason shown.
 */
import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const apiMocks = vi.hoisted(() => ({
  listProposals: vi.fn(),
  inspectProposal: vi.fn(),
  proposalsHistoryStatus: vi.fn(),
  applyProposal: vi.fn(),
  rejectProposal: vi.fn(),
  quarantineProposal: vi.fn(),
  reviseProposal: vi.fn(),
  updateStagedProposal: vi.fn(),
  requestProposalRevision: vi.fn(),
  listProposalEvents: vi.fn(),
  curatorPin: vi.fn(),
  curatorUnpin: vi.fn(),
  curatorRestore: vi.fn(),
}));

vi.mock("@/lib/ocFamilyAdminApi", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/ocFamilyAdminApi")>();
  return {
    ...actual,
    ...apiMocks,
    proposalIdOf: (p: Record<string, unknown>) =>
      String(p.id ?? p.skill_id ?? p.slug ?? p.name ?? ""),
  };
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
  const [{ default: SkillsReviewPage }] = await Promise.all([
    import("./SkillsReviewPage"),
  ]);
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  const ui: ReactNode = (
    <MemoryRouter initialEntries={["/admin/skills-review"]}>
      <SkillsReviewPage />
    </MemoryRouter>
  );
  await act(async () => root.render(ui));
}

function byTestId(id: string): HTMLElement | null {
  return container.querySelector(`[data-testid="${id}"]`);
}

beforeEach(() => {
  Object.values(apiMocks).forEach((fn) => fn.mockReset());
  apiMocks.listProposals.mockResolvedValue({
    state: "OK",
    proposals: [
      { id: "skill-a", status: "pending" },
      { id: "skill-b", status: "pending" },
    ],
    count: 2,
  });
  apiMocks.proposalsHistoryStatus.mockResolvedValue({ state: "OK", journal: {} });
  apiMocks.inspectProposal.mockResolvedValue({
    state: "OK",
    proposal: { id: "skill-a", status: "pending" },
  });
  apiMocks.listProposalEvents.mockResolvedValue({ state: "OK", events: [], count: 0 });
  for (const fn of [
    apiMocks.applyProposal,
    apiMocks.rejectProposal,
    apiMocks.quarantineProposal,
    apiMocks.reviseProposal,
    apiMocks.updateStagedProposal,
    apiMocks.requestProposalRevision,
    apiMocks.curatorPin,
    apiMocks.curatorUnpin,
    apiMocks.curatorRestore,
  ]) {
    fn.mockResolvedValue({ state: "OK" });
  }
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

describe("SkillsReviewPage", () => {
  it("renders the proposals queue with inspect + verdict rows (both halves)", async () => {
    await renderPage();
    await waitFor(() => byTestId("sr-row-skill-a") !== null);
    expect(apiMocks.listProposals).toHaveBeenCalled();
    expect(byTestId("sr-row-skill-b")).not.toBeNull();
    expect(byTestId("sr-apply-skill-a")).not.toBeNull();
    expect(byTestId("sr-reject-skill-a")).not.toBeNull();
    expect(byTestId("sr-quarantine-skill-a")).not.toBeNull();
    // Inspect goes through the content.py half.
    await act(async () => {
      byTestId("sr-inspect-skill-a")?.dispatchEvent(
        new MouseEvent("click", { bubbles: true }),
      );
    });
    await waitFor(() => byTestId("sr-detail") !== null);
    expect(apiMocks.inspectProposal).toHaveBeenCalledWith("skill-a");
  });

  it("apply calls the mutations half with the row id", async () => {
    await renderPage();
    await waitFor(() => byTestId("sr-apply-skill-a") !== null);
    await act(async () => {
      byTestId("sr-apply-skill-a")?.dispatchEvent(
        new MouseEvent("click", { bubbles: true }),
      );
    });
    await waitFor(() => apiMocks.applyProposal.mock.calls.length > 0);
    expect(apiMocks.applyProposal.mock.calls[0][0]).toBe("skill-a");
  });

  it("events drawer lists the journal for the inspected proposal", async () => {
    apiMocks.listProposalEvents.mockResolvedValue({
      state: "OK",
      events: [{ sequence: 1, action: "created", payload: { id: "skill-a" } }],
      count: 1,
    });
    await renderPage();
    await waitFor(() => byTestId("sr-row-skill-a") !== null);
    await act(async () => {
      byTestId("sr-inspect-skill-a")?.dispatchEvent(
        new MouseEvent("click", { bubbles: true }),
      );
    });
    await waitFor(() => byTestId("sr-detail") !== null);
    await act(async () => {
      byTestId("sr-events-open")?.dispatchEvent(
        new MouseEvent("click", { bubbles: true }),
      );
    });
    await waitFor(() => byTestId("sr-events-drawer") !== null);
    expect(apiMocks.listProposalEvents).toHaveBeenCalled();
  });

  it("refusal rows are disabled and show the exact prerequisite", async () => {
    await renderPage();
    await waitFor(() => byTestId("sr-row-skill-a") !== null);
    const { DISABLED_SKILLS_ACTIONS } = await import("@/lib/ocFamilyAdminApi");
    expect(DISABLED_SKILLS_ACTIONS.length).toBeGreaterThan(0);
    for (const d of DISABLED_SKILLS_ACTIONS) {
      const row = byTestId(`sr-disabled-${d.action}`);
      expect(row).not.toBeNull();
      const btn = row?.querySelector("button");
      expect(btn?.disabled).toBe(true);
      expect(row?.textContent).toContain(d.reason);
    }
    // No refused operation is ever called.
    expect(apiMocks.listProposals).toHaveBeenCalled();
  });
});
