// @vitest-environment jsdom
/**
 * /models drift A20 — the Qubit "USE AS" model-profile actions.
 *
 * The page must render one "Use as" action per REAL model profile from
 * `GET /api/terminal/profiles`, mark the active one with `aria-pressed`, and
 * route a click through the confirm-gated `PUT /api/terminal/profile`
 * (`setTerminalProfile`). This pins the real registry instead of the cosmetic
 * voice/brain `POST /api/profiles/active` write.
 */
import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const apiMocks = vi.hoisted(() => ({
  getConfig: vi.fn(),
  getModelsAnalytics: vi.fn(),
  getAuxiliaryModels: vi.fn(),
  getTerminalProfiles: vi.fn(),
  getActiveTerminalProfile: vi.fn(),
  setTerminalProfile: vi.fn(),
  setModelAssignment: vi.fn(),
  getModelOptions: vi.fn(),
}));

vi.mock("@/lib/api", () => ({ api: apiMocks }));
vi.mock("@/plugins", () => ({ PluginSlot: () => null }));

const PROFILE = (over: Record<string, unknown>) => ({
  id: "qubit-brain4",
  kind: "local",
  provider: "ollama",
  model: "qubit-brain4",
  locality: "local",
  enabled: true,
  default: true,
  context_tokens: 4096,
  min_context_tokens: 65536,
  meets_floor: false,
  code: "TERMINAL_CONTEXT_BELOW_MINIMUM",
  has_credential: false,
  ...over,
});

let container: HTMLDivElement;
let root: Root;
(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

async function renderModelsPage(ui: ReactNode) {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await act(async () => root.render(ui));
}

async function renderModels() {
  const [{ default: ModelsPage }, { I18nProvider }, { PageHeaderProvider }] =
    await Promise.all([
      import("./ModelsPage"),
      import("@/i18n"),
      import("@/contexts/PageHeaderProvider"),
    ]);
  await renderModelsPage(
    <I18nProvider>
      <MemoryRouter initialEntries={["/models"]}>
        <PageHeaderProvider pluginTabs={[]}>
          <ModelsPage />
        </PageHeaderProvider>
      </MemoryRouter>
    </I18nProvider>,
  );
}

beforeEach(() => {
  for (const fn of Object.values(apiMocks)) fn.mockReset();
  apiMocks.getConfig.mockResolvedValue({});
  apiMocks.getModelsAnalytics.mockResolvedValue({
    models: [],
    totals: {
      distinct_models: 0,
      total_input: 0,
      total_output: 0,
      total_cache_read: 0,
      total_reasoning: 0,
      total_estimated_cost: 0,
      total_actual_cost: 0,
      total_sessions: 0,
      total_api_calls: 0,
    },
    period_days: 30,
  });
  apiMocks.getAuxiliaryModels.mockResolvedValue({
    tasks: [],
    main: { provider: "ollama", model: "qubit-brain4" },
  });
  apiMocks.getTerminalProfiles.mockResolvedValue([
    PROFILE({ id: "qubit-brain4", provider: "ollama", model: "qubit-brain4" }),
    PROFILE({
      id: "qubit-go",
      kind: "api",
      provider: "opencode-go",
      model: "deepseek-v4-flash",
      locality: "external",
      default: false,
      meets_floor: true,
      code: "OK",
      has_credential: true,
    }),
  ]);
  apiMocks.getActiveTerminalProfile.mockResolvedValue(
    PROFILE({ id: "qubit-brain4", provider: "ollama", model: "qubit-brain4" }),
  );
  apiMocks.setTerminalProfile.mockResolvedValue({
    ok: true,
    profile_id: "qubit-go",
    meets_floor: true,
  });
});

afterEach(async () => {
  await act(async () => root?.unmount());
  container?.remove();
  vi.unstubAllGlobals();
});

describe("ModelsPage USE AS actions (drift A20)", () => {
  it("renders one Use as action per model profile and marks the active profile", async () => {
    await renderModels();
    await vi.waitFor(() =>
      expect(
        container.querySelector(
          'button[aria-label="Use qubit-brain4 as the active model profile"]',
        ),
      ).not.toBeNull(),
    );

    const active = container.querySelector<HTMLButtonElement>(
      'button[aria-label="Use qubit-brain4 as the active model profile"]',
    );
    expect(active?.textContent).toContain("Use as");
    expect(active?.getAttribute("aria-pressed")).toBe("true");

    const other = container.querySelector<HTMLButtonElement>(
      'button[aria-label="Use qubit-go as the active model profile"]',
    );
    expect(other).not.toBeNull();
    expect(other?.getAttribute("aria-pressed")).toBe("false");
  });

  it("activates a different model profile through the canonical registry", async () => {
    await renderModels();
    await vi.waitFor(() =>
      expect(
        container.querySelector(
          'button[aria-label="Use qubit-go as the active model profile"]',
        ),
      ).not.toBeNull(),
    );

    await act(async () => {
      container
        .querySelector(
          'button[aria-label="Use qubit-go as the active model profile"]',
        )!
        .dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    expect(apiMocks.setTerminalProfile).toHaveBeenCalledWith("qubit-go");
  });
});
