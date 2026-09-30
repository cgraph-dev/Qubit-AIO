// @vitest-environment jsdom
/**
 * /env drift A22 — the Qubit OAUTH/PROVIDERS/TOOLS/GATEWAY/SETTINGS/CUSTOM
 * KEYS tab family.
 *
 * Every section is structural (present even with an empty catalog) and each
 * header tab jumps to its card. This pins the six-tab surface, not a page
 * load.
 */
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const apiMocks = vi.hoisted(() => ({
  getEnvVars: vi.fn(),
  getOAuthProviders: vi.fn(),
  disconnectOAuthProvider: vi.fn(),
  setEnvVar: vi.fn(),
  deleteEnvVar: vi.fn(),
  revealEnvVar: vi.fn(),
}));

vi.mock("@/lib/api", () => ({ api: apiMocks }));
vi.mock("@/plugins", () => ({ PluginSlot: () => null }));

let container: HTMLDivElement;
let root: Root;
(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

async function waitFor(cond: () => boolean, timeoutMs = 5000) {
  const start = Date.now();
  while (!cond()) {
    if (Date.now() - start > timeoutMs) {
      throw new Error("waitFor: condition never became true");
    }
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 20));
    });
  }
}

async function renderEnv() {
  const [{ default: EnvPage }, { I18nProvider }, { PageHeaderProvider }] =
    await Promise.all([
      import("./EnvPage"),
      import("@/i18n"),
      import("@/contexts/PageHeaderProvider"),
    ]);
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await act(async () =>
    root.render(
      <I18nProvider>
        <MemoryRouter initialEntries={["/env"]}>
          <PageHeaderProvider pluginTabs={[]}>
            <EnvPage />
          </PageHeaderProvider>
        </MemoryRouter>
      </I18nProvider>,
    ),
  );
  await waitFor(() => Boolean(container.querySelector("#section-custom")));
}

beforeEach(() => {
  for (const fn of Object.values(apiMocks)) fn.mockReset();
  apiMocks.getEnvVars.mockResolvedValue({});
  apiMocks.getOAuthProviders.mockResolvedValue({ providers: [] });
  apiMocks.setEnvVar.mockResolvedValue({ ok: true });
  apiMocks.deleteEnvVar.mockResolvedValue({ ok: true });
  apiMocks.revealEnvVar.mockResolvedValue({ value: "" });
  vi.stubGlobal("ResizeObserver", class { disconnect() {} observe() {} unobserve() {} });
  vi.stubGlobal("matchMedia", () => ({ addEventListener() {}, matches: false, media: "", removeEventListener() {} }));
});

afterEach(async () => {
  await act(async () => root?.unmount());
  container?.remove();
  vi.unstubAllGlobals();
});

describe("EnvPage tab family (drift A22)", () => {
  it("renders the six-section jump nav and every section card", async () => {
    await renderEnv();

    const nav = container.querySelector('nav[aria-label="Jump to section"]');
    expect(nav).not.toBeNull();
    const labels = Array.from(nav!.querySelectorAll("button")).map((b) =>
      b.textContent?.trim(),
    );
    expect(labels).toEqual([
      "OAuth",
      "Providers",
      "Tools",
      "Gateway",
      "Settings",
      "Custom Keys",
    ]);

    for (const id of [
      "section-oauth",
      "section-providers",
      "section-tool",
      "section-messaging",
      "section-setting",
      "section-custom",
    ]) {
      expect(container.querySelector(`#${id}`)).not.toBeNull();
    }
  });

  it("shows the real is_set state and never a value", async () => {
    apiMocks.getEnvVars.mockResolvedValue({
      AUDIT_SET_KEY: {
        is_set: true,
        redacted_value: null,
        description: "set probe",
        category: "custom",
        is_password: true,
        tools: [],
      },
      AUDIT_UNSET_KEY: {
        is_set: false,
        redacted_value: null,
        description: "unset probe",
        category: "custom",
        is_password: true,
        tools: [],
      },
    });
    await renderEnv();

    await waitFor(() => container.textContent?.includes("AUDIT_SET_KEY") === true);
    expect(container.textContent).toContain("AUDIT_SET_KEY");
    expect(container.textContent).toContain("AUDIT_UNSET_KEY");
    // is_set=true renders the full row: the set-only Replace/Clear actions and
    // the redacted "---" (never a value). is_set=false keeps the "Set" action.
    expect(container.textContent).toContain("Replace");
    expect(container.textContent).toContain("Clear");
    expect(container.textContent).toContain("---");
    expect(container.textContent).toContain("Set");
  });
});
