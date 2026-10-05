// @vitest-environment jsdom
/**
 * Focused regressions for the missing-field crashes found by the resilience
 * sweep (`pageResilience.sweep.test.tsx`).
 *
 * Each case pins exactly one bug: a loader stored a backend field that can be
 * absent (`res.platforms`, `res.sessions`, `aux.main`, `stats.by_source`, …)
 * so the page later dereferenced `undefined` and blanked the route. The fix
 * folds in an empty default; this test fails if that guard is removed.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { probePage } from "@/test-utils/pageResilience";

/** Per-endpoint overrides; anything not listed returns `{}`. */
const overrides = vi.hoisted(() => ({ map: {} as Record<string, unknown> }));

const ARRAY_METHODS = new Set([
  "getTerminalProfiles",
  "getCronJobs",
  "getSkills",
  "getToolsets",
  "getAgentPlugins",
]);

function valueFor(methodOrUrl: string): unknown {
  if (Object.prototype.hasOwnProperty.call(overrides.map, methodOrUrl)) {
    return overrides.map[methodOrUrl];
  }
  return ARRAY_METHODS.has(methodOrUrl) ? [] : {};
}

const api = new Proxy(
  {},
  {
    get(_t, prop: string) {
      return vi.fn(async () => valueFor(prop));
    },
  },
);

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api,
    fetchJSON: vi.fn(async (url: string) => valueFor(String(url))),
    authedFetch: vi.fn(async () => ({
      ok: true,
      status: 200,
      json: async () => ({}),
      text: async () => "{}",
    })),
    streamChatTurn: vi.fn(async () => {}),
  };
});

vi.mock("@/lib/eventsFeedClient", () => ({
  createEventsFeedClient: () => ({
    start: vi.fn(),
    stop: vi.fn(),
    close: vi.fn(),
  }),
}));

beforeEach(() => {
  overrides.map = {};
  vi.stubGlobal("matchMedia", (q: string) => ({
    matches: false,
    media: q,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    addListener: vi.fn(),
    removeListener: vi.fn(),
    dispatchEvent: vi.fn(),
  }));
});

afterEach(() => {
  vi.unstubAllGlobals();
});

/**
 * Render a page and assert it mounts without throwing. `overrides` maps each
 * api method / fetchJSON url to the exact hostile payload for that case.
 */
async function expectPageResilient(
  name: string,
  load: () => Promise<{ default: React.ComponentType }>,
) {
  const { default: Page } = await load();
  const { result, unmount } = await probePage(Page);
  await unmount();
  if (result.threw) {
    throw new Error(`${name} threw: ${String(result.error)}`);
  }
  expect(result.rawJson, `${name} leaked raw JSON`).toBe(false);
}

describe("missing-field regressions", () => {
  it("ChannelsPage: envelope without `platforms` renders the empty channel list", async () => {
    // Regression: `setPlatforms(res.platforms)` stored undefined → the header
    // `platforms.length` read crashed the route.
    overrides.map.getMessagingPlatforms = {};
    await expectPageResilient("ChannelsPage", () => import("./ChannelsPage"));
  });

  it("CheckpointsPage: journal response without `sessions` renders 0 sessions", async () => {
    // Regression: `setJournal(res.sessions)` → `journal.length` crash.
    overrides.map["/api/ops/checkpoints"] = { total_bytes: 5 };
    await expectPageResilient("CheckpointsPage", () =>
      import("./CheckpointsPage"),
    );
  });

  it("FilesPage: listing without `entries` renders the empty state", async () => {
    // Regression: `listing.entries.length` crashed on a listing with no entries.
    overrides.map.listFiles = { path: "/" };
    await expectPageResilient("FilesPage", () => import("./FilesPage"));
  });

  it("LogsPage: log response without `lines` renders the empty log view", async () => {
    // Regression: `setLines(resp.lines)` → `lines.length` crash.
    overrides.map["/api/logs"] = { has_more: false };
    overrides.map.getLogs = { has_more: false };
    await expectPageResilient("LogsPage", () => import("./LogsPage"));
  });

  it("McpPage: catalog without `entries`/`diagnostics` renders empty", async () => {
    // Regression: `res.entries` / `res.diagnostics` → `.forEach` crash.
    overrides.map.getMcpServers = {};
    overrides.map.getMcpCatalog = {};
    await expectPageResilient("McpPage", () => import("./McpPage"));
  });

  it("ModelsPage: analytics without `totals`/`main` renders zeroed stats", async () => {
    // Regression: `data.totals.distinct_models` and `aux?.main.provider` both
    // dereferenced undefined.
    overrides.map.getModelsAnalytics = { models: [] };
    overrides.map.getAuxiliaryModels = {};
    await expectPageResilient("ModelsPage", () => import("./ModelsPage"));
  });

  it("PairingPage: pairing response without `pending`/`approved` renders empties", async () => {
    // Regression: `setPending(res.pending)` → `pending.length` crash.
    overrides.map.getPairing = {};
    await expectPageResilient("PairingPage", () => import("./PairingPage"));
  });

  it("ProfilesPage: profile list without `profiles` renders the empty list", async () => {
    // Regression: `setProfiles(res.profiles)` → `profiles.length` crash.
    overrides.map.getProfiles = {};
    await expectPageResilient("ProfilesPage", () => import("./ProfilesPage"));
  });

  it("SessionsPage: sessions response without `sessions`/`by_source` renders empty", async () => {
    // Regression: `resp.sessions` and `stats.by_source` (Object.keys).
    overrides.map.getSessions = {};
    overrides.map.getSessionStoreStats = {};
    await expectPageResilient("SessionsPage", () => import("./SessionsPage"));
  });

  it("SessionOpsPage: groups response without `groups` renders the empty list", async () => {
    // Regression: `setGroups(res.groups)` → `groups.map` crash.
    overrides.map["/api/ops/session-groups"] = {};
    await expectPageResilient("SessionOpsPage", () =>
      import("./SessionOpsPage"),
    );
  });

  it("SkillsPage: skills list resolving undefined renders the empty list", async () => {
    // Regression: `setSkills(s)` → `[...skills]` "not iterable" crash.
    overrides.map.getSkills = undefined;
    await expectPageResilient("SkillsPage", () => import("./SkillsPage"));
  });

  it("SystemPage: memory/hooks/pool without inner fields render defaults", async () => {
    // Regression: `memory?.builtin_files.memory`, `hooks.hooks.length`,
    // `pool.length`, `checkpoints?.sessions.length` all crashed.
    overrides.map.getMemory = {};
    overrides.map.getHooks = {};
    overrides.map.getCredentialPool = {};
    overrides.map.getCheckpoints = {};
    await expectPageResilient("SystemPage", () => import("./SystemPage"));
  });

  it("WorktreesPage: worktree/branch responses without arrays render empties", async () => {
    // Regression: `setWorktrees(wt.worktrees)` → `worktrees.length` crash.
    overrides.map["/api/git/worktrees"] = {};
    overrides.map["/api/git/branches"] = {};
    await expectPageResilient("WorktreesPage", () => import("./WorktreesPage"));
  });

  it("ConfigPage: status without `config_path` renders without crashing", async () => {
    // Regression: `resp.config_path` crashed when status omitted the path.
    overrides.map.getStatus = {};
    await expectPageResilient("ConfigPage", () => import("./ConfigPage"));
  });

  it("CronPage: job list and skills resolving undefined render empties", async () => {
    // Regression: `setJobs(nextJobs)` / `[...skills]` crashed on undefined.
    overrides.map.getCronJobs = undefined;
    overrides.map.getSkills = undefined;
    overrides.map.getToolsets = undefined;
    await expectPageResilient("CronPage", () => import("./CronPage"));
  });
});
