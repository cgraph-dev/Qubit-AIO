// @vitest-environment jsdom
/**
 * Open Viking shell consistency: all five console routes present the same
 * page opening (intro + sibling-view switcher), and every sub-page carries
 * the parent breadcrumb. This is the contract that keeps the five pages a
 * single, uniform surface instead of five hand-rolled headers.
 */
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const vikingMocks = vi.hoisted(() => ({
  getVikingStatus: vi.fn(),
  getVikingContextTree: vi.fn(),
  getVikingFile: vi.fn(),
  findVikingRelated: vi.fn(),
  searchVikingRetrieval: vi.fn(),
  addVikingResource: vi.fn(),
  addVikingMemory: vi.fn(),
  listVikingBotSessions: vi.fn(),
  listVikingSkills: vi.fn(),
}));

vi.mock("@/lib/ocFamilyVikingApi", () => ({
  VIKING_CONTEXT_GROUPS: [{ key: "user_memories", label: "User memories" }],
  getVikingStatus: vikingMocks.getVikingStatus,
  getVikingContextTree: vikingMocks.getVikingContextTree,
  getVikingFile: vikingMocks.getVikingFile,
  findVikingRelated: vikingMocks.findVikingRelated,
  searchVikingRetrieval: vikingMocks.searchVikingRetrieval,
  addVikingResource: vikingMocks.addVikingResource,
  addVikingMemory: vikingMocks.addVikingMemory,
  listVikingBotSessions: vikingMocks.listVikingBotSessions,
  listVikingSkills: vikingMocks.listVikingSkills,
  isBridgeRowMissing: () => false,
  normalizeVikingStatus: () => ({ connection: "connected" }),
  normalizeContextTree: () => ({}),
  normalizeVikingFile: () => null,
  normalizeSearchHits: () => [],
  normalizeBotSessions: () => [],
  normalizeVikingSkills: () => [],
  toVikingUri: (p: string) => `viking://${p}`,
}));

import OpenVikingPage from "@/pages/OpenVikingPage";
import OpenVikingRetrievalPage from "@/pages/OpenVikingRetrievalPage";
import OpenVikingUploadsPage from "@/pages/OpenVikingUploadsPage";
import OpenVikingResourcesPage from "@/pages/OpenVikingResourcesPage";
import OpenVikingAgentHubPage from "@/pages/OpenVikingAgentHubPage";

let container: HTMLDivElement;
let root: Root;
(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
const VIEWS = ["Playground", "Retrieval", "Uploads", "Agent Hub", "Resources"];

beforeEach(() => {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  vikingMocks.getVikingStatus.mockResolvedValue({ connection: "connected" });
  vikingMocks.getVikingContextTree.mockResolvedValue({ tree: {} });
  vikingMocks.findVikingRelated.mockResolvedValue({ hits: [] });
  vikingMocks.listVikingBotSessions.mockResolvedValue({ sessions: [] });
  vikingMocks.listVikingSkills.mockResolvedValue({ skills: [] });
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  vi.clearAllMocks();
});

async function render(node: React.ReactNode) {
  await act(async () => {
    root.render(<MemoryRouter>{node}</MemoryRouter>);
  });
  await act(async () => {
    await new Promise((r) => setTimeout(r, 30));
  });
}

const PAGES: Array<[string, React.ReactNode, boolean]> = [
  ["playground", <OpenVikingPage />, false],
  ["retrieval", <OpenVikingRetrievalPage />, true],
  ["uploads", <OpenVikingUploadsPage />, true],
  ["resources", <OpenVikingResourcesPage />, true],
  ["agent-hub", <OpenVikingAgentHubPage />, true],
];

describe("Open Viking shell consistency", () => {
  for (const [name, node, isSub] of PAGES) {
    it(`${name}: intro + sibling switcher${isSub ? " + parent breadcrumb" : ""}`, async () => {
      await render(node);
      // Same opening pattern on every page: the shared intro line.
      expect(container.querySelector(".settings-page__intro")).not.toBeNull();
      // The sibling-view switcher lists all five routes on every page.
      const subnav = container.querySelector('[data-testid="viking-subnav"]');
      expect(subnav).not.toBeNull();
      for (const label of VIEWS) {
        expect(subnav?.textContent).toContain(label);
      }
      // Sub-pages carry the parent breadcrumb; the root does not.
      const crumb = container.querySelector('[data-testid="viking-breadcrumb"]');
      if (isSub) {
        expect(crumb).not.toBeNull();
        expect(crumb?.textContent).toContain("Open Viking");
      } else {
        expect(crumb).toBeNull();
      }
    });
  }

  it("reuses the shared, defined viking shell classes (no invented names)", async () => {
    await render(<OpenVikingRetrievalPage />);
    for (const cls of ["viking-intro", "viking-subnav"]) {
      expect(container.querySelector(`.${cls}`)).not.toBeNull();
    }
  });
});
