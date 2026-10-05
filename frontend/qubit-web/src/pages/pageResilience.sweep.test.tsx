// @vitest-environment jsdom
/**
 * Cross-page resilience sweep.
 *
 * Every dashboard route must survive a hostile backend: an empty body, a
 * payload missing fields the page assumed required, and a plain error. This
 * suite mounts each page against a data layer that returns `{}` for object
 * endpoints (nothing the page expects is present) and `[]` for list
 * endpoints, then asserts the page renders *something* honest — it does not
 * crash and does not leak raw JSON / `[object Object]`.
 *
 * Pages that pass here are correct under missing-field input; the ones that
 * fail point at a real bug (the fix lives in the page, with a focused
 * regression test added alongside).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { probePage } from "@/test-utils/pageResilience";

/**
 * Programmable data layer. `mode` controls what every call resolves to:
 *   "empty"     -> {} for objects, [] for lists (no expected field present)
 *   "undefined" -> undefined (whole body absent)
 *   "error"     -> rejected promise
 */
const state = vi.hoisted(() => ({
  mode: "empty" as "empty" | "undefined" | "error",
}));

/** api.* methods whose real return type is a bare array (not an envelope). */
const ARRAY_METHODS = new Set([
  "getTerminalProfiles",
  "getCronJobs",
  "getSkills",
  "getToolsets",
  "getAgentPlugins",
]);

/** fetchJSON endpoints whose real return type is a bare array. */
const ARRAY_URLS = [
  "/api/terminal/profiles",
  "/api/skills",
  "/api/tools/toolsets",
  "/api/dashboard/plugins",
  "/api/cron/jobs",
];

function valueFor(methodOrUrl: string): unknown {
  if (state.mode === "undefined") return undefined;
  if (state.mode === "error") throw new Error("PROBE_BACKEND_ERROR");
  const isArray =
    ARRAY_METHODS.has(methodOrUrl) ||
    ARRAY_URLS.some((u) => methodOrUrl.startsWith(u));
  return isArray ? [] : {};
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

// Optional network/stream clients some pages import directly.
vi.mock("@/lib/eventsFeedClient", () => ({
  createEventsFeedClient: () => ({
    start: vi.fn(),
    stop: vi.fn(),
    close: vi.fn(),
  }),
}));

const PAGES: Array<[string, () => Promise<{ default: React.ComponentType }>]> = [
  ["AgentsPage", () => import("./AgentsPage")],
  ["AgentWorkspacePage", () => import("./AgentWorkspacePage")],
  ["AnalyticsPage", () => import("./AnalyticsPage")],
  ["BoardAdminPage", () => import("./BoardAdminPage")],
  ["CanvasPage", () => import("./CanvasPage")],
  ["ChannelsPage", () => import("./ChannelsPage")],
  ["CheckpointsPage", () => import("./CheckpointsPage")],
  ["ConfigPage", () => import("./ConfigPage")],
  ["CronPage", () => import("./CronPage")],
  ["DevicesPage", () => import("./DevicesPage")],
  ["DocsPage", () => import("./DocsPage")],
  ["EnvPage", () => import("./EnvPage")],
  ["FilesPage", () => import("./FilesPage")],
  ["HarnessRouterPage", () => import("./HarnessRouterPage")],
  ["LogsPage", () => import("./LogsPage")],
  ["McpCallsPage", () => import("./McpCallsPage")],
  ["McpPage", () => import("./McpPage")],
  ["MemoryBackendsPage", () => import("./MemoryBackendsPage")],
  ["ModelsPage", () => import("./ModelsPage")],
  ["NodesPage", () => import("./NodesPage")],
  ["OpenVikingAgentHubPage", () => import("./OpenVikingAgentHubPage")],
  ["OpenVikingPage", () => import("./OpenVikingPage")],
  ["OpenVikingResourcesPage", () => import("./OpenVikingResourcesPage")],
  ["OpenVikingRetrievalPage", () => import("./OpenVikingRetrievalPage")],
  ["OpenVikingUploadsPage", () => import("./OpenVikingUploadsPage")],
  ["PairingPage", () => import("./PairingPage")],
  ["PluginsPage", () => import("./PluginsPage")],
  ["ProfileBuilderPage", () => import("./ProfileBuilderPage")],
  ["ProfilesPage", () => import("./ProfilesPage")],
  ["QubitBotPage", () => import("./QubitBotPage")],
  ["SchedulerMediaPage", () => import("./SchedulerMediaPage")],
  ["SessionOpsPage", () => import("./SessionOpsPage")],
  ["SessionsPage", () => import("./SessionsPage")],
  ["SkillsPage", () => import("./SkillsPage")],
  ["SkillsReviewPage", () => import("./SkillsReviewPage")],
  ["SystemPage", () => import("./SystemPage")],
  ["TalkPage", () => import("./TalkPage")],
  ["TaskInboxPage", () => import("./TaskInboxPage")],
  ["TaskflowsPage", () => import("./TaskflowsPage")],
  ["TasksPage", () => import("./TasksPage")],
  ["UsersAdminPage", () => import("./UsersAdminPage")],
  ["VoiceEditPage", () => import("./VoiceEditPage")],
  ["WebhooksPage", () => import("./WebhooksPage")],
  ["WorktreesPage", () => import("./WorktreesPage")],
  ["WebChatPage", () => import("./WebChatPage").then((m) => ({ default: m.WebChatPage }))],
  ["ChatPage", () => import("./ChatPage")],
];

beforeEach(() => {
  state.mode = "empty";
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

const MODES: Array<["empty" | "error" | "undefined", string]> = [
  ["empty", "an empty payload"],
  ["error", "a backend error"],
  ["undefined", "an undefined body"],
];

for (const [mode, label] of MODES) {
  describe(`page resilience sweep — backend returns ${label}`, () => {
    for (const [name, load] of PAGES) {
      it(`${name} renders without crashing on ${label}`, async () => {
        state.mode = mode;
        const { default: Page } = await load();
        const { result, unmount } = await probePage(Page);
        await unmount();
        if (result.threw) {
          throw new Error(`${name} threw on ${label}: ${String(result.error)}`);
        }
        expect(result.rawJson, `${name} leaked raw JSON`).toBe(false);
      });
    }
  });
}
