// @vitest-environment jsdom
/**
 * AnalyticsPage structure regressions.
 *
 * The page used to render the `dashboard.show_token_analytics` gate as a wall
 * of four bare paragraphs (one 201-char unbroken block) with no settings
 * chrome. These tests pin the ported openclaw settings structure:
 *
 *   - hidden gate  -> one `.settings-section` whose notice is `.settings-empty`
 *                     carrying the honest reason, plus a Config row. No
 *                     fabricated numbers — the backend never supplies them.
 *   - data present -> `.settings-summary` tiles + sections; never raw JSON.
 *
 * The providers mirror `@/test-utils/pageResilience` so the page sees the same
 * shell (i18n + router + page header) it does in the app.
 */
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const apiMocks = vi.hoisted(() => ({
  getConfig: vi.fn(),
  getAnalytics: vi.fn(),
}));

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, api: { ...actual.api, ...apiMocks } };
});

vi.mock("@/lib/eventsFeedClient", () => ({
  createEventsFeedClient: () => ({
    start: vi.fn(),
    stop: vi.fn(),
    close: vi.fn(),
  }),
}));

let container: HTMLDivElement;
let root: Root;
(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

async function waitFor(cond: () => boolean, timeoutMs = 5000) {
  const start = Date.now();
  while (!cond()) {
    if (Date.now() - start > timeoutMs) throw new Error("waitFor: condition never became true");
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 20));
    });
  }
}

async function renderPage() {
  // Import providers *together* with the page so every module resolves to the
  // same instance (vi.resetModules() in afterEach would otherwise give the page
  // a different PageHeaderContext than a statically-imported provider).
  const [{ default: AnalyticsPage }, { I18nProvider }, { PageHeaderProvider }, { SystemActionsProvider }] =
    await Promise.all([
      import("./AnalyticsPage"),
      import("@/i18n"),
      import("@/contexts/PageHeaderProvider"),
      import("@/contexts/SystemActions"),
    ]);
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await act(async () => {
    root.render(
      <I18nProvider>
        <SystemActionsProvider>
          <MemoryRouter initialEntries={["/analytics"]}>
            <PageHeaderProvider pluginTabs={[]}>
              <AnalyticsPage />
            </PageHeaderProvider>
          </MemoryRouter>
        </SystemActionsProvider>
      </I18nProvider>,
    );
  });
}

beforeEach(() => {
  apiMocks.getConfig.mockReset();
  apiMocks.getAnalytics.mockReset();
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

afterEach(async () => {
  await act(async () => {
    root?.unmount();
  });
  container?.remove();
  vi.unstubAllGlobals();
  vi.resetModules();
});

describe("AnalyticsPage settings structure", () => {
  it("renders the hidden gate as a settings section + empty notice, not bare prose", async () => {
    apiMocks.getConfig.mockResolvedValue({ dashboard: {} });
    await renderPage();
    const page = () => document.querySelector('[data-testid="analytics-page"]');

    await waitFor(() => page() !== null);
    await waitFor(
      () => document.querySelector(".settings-section") !== null,
    );

    // Structured settings chrome, not a wall of prose.
    expect(document.querySelector(".settings-section")).not.toBeNull();
    expect(document.querySelector(".settings-empty")).not.toBeNull();
    expect(document.querySelector(".settings-row")).not.toBeNull();

    const text = page()?.textContent ?? "";
    // Honest reason is present.
    expect(text).toMatch(/local\s+debug estimate/i);
    expect(text).toMatch(/auxiliary calls/i);

    // Action row points at Config; nothing inert.
    const link = document.querySelector(
      'a[aria-label="Open Config to enable token analytics"]',
    );
    expect(link).not.toBeNull();
    expect(link?.getAttribute("href")).toBe("/config");

    // The gate must not fetch analytics and must not fabricate numbers.
    expect(apiMocks.getAnalytics).not.toHaveBeenCalled();
    expect(page()?.textContent ?? "").not.toMatch(/[0-9]+\s*K\b/);
  });

  it("renders summary tiles and sections when analytics data is present", async () => {
    apiMocks.getConfig.mockResolvedValue({
      dashboard: { show_token_analytics: true },
    });
    apiMocks.getAnalytics.mockResolvedValue({
      daily: [
        {
          day: "2026-10-01",
          sessions: 3,
          input_tokens: 1200,
          output_tokens: 400,
          cache_read_tokens: 0,
          reasoning_tokens: 0,
          estimated_cost: 0,
          actual_cost: 0,
          api_calls: 3,
        },
      ],
      by_model: [],
      totals: {
        total_input: 1200,
        total_output: 400,
        total_cache_read: 0,
        total_reasoning: 0,
        total_estimated_cost: 0,
        total_actual_cost: 0,
        total_sessions: 3,
        total_api_calls: 3,
      },
      skills: {
        summary: {
          total_skill_actions: 0,
          distinct_skills_used: 0,
        },
        top_skills: [],
      },
    });

    await renderPage();
    const page = () => document.querySelector('[data-testid="analytics-page"]');

    await waitFor(() => document.querySelector(".settings-summary__tile") !== null);

    expect(document.querySelectorAll(".settings-summary__tile").length).toBe(5);
    expect(document.querySelector(".settings-section")).not.toBeNull();
    expect(apiMocks.getAnalytics).toHaveBeenCalled();

    const text = page()?.textContent ?? "";
    // Never leak raw JSON / object rendering.
    expect(text).not.toContain("[object Object]");
    expect(text).not.toMatch(/^\s*\{\s*"/);
  });
});
