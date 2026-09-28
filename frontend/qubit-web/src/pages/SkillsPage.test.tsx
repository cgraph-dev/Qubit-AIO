// @vitest-environment jsdom
/**
 * /skills drift A21 — the category filter panel + live search.
 *
 * The page must render the All/Toolsets/Browse hub view switcher, a Categories
 * panel derived from the real skill payload, and the header search input that
 * narrows the list (and shows the typed empty state when nothing matches).
 */
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const apiMocks = vi.hoisted(() => ({
  getSkills: vi.fn(),
  getToolsets: vi.fn(),
  toggleSkill: vi.fn(),
  getProfiles: vi.fn(),
  getActiveProfile: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  api: apiMocks,
  ConfirmRequiredError: class ConfirmRequiredError extends Error {
    detail = "";
    confirmationId = "";
    get hasConfirmationId() {
      return false;
    }
  },
  setManagementProfile: vi.fn(),
  getManagementProfile: vi.fn(() => ""),
}));
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

function setInputValue(input: HTMLInputElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(
    HTMLInputElement.prototype,
    "value",
  )?.set;
  setter?.call(input, value);
  input.dispatchEvent(new Event("input", { bubbles: true }));
}

async function renderSkills() {
  const [
    { default: SkillsPage },
    { I18nProvider },
    { ProfileProvider },
    { PageHeaderProvider },
  ] = await Promise.all([
    import("./SkillsPage"),
    import("@/i18n"),
    import("@/contexts/ProfileProvider"),
    import("@/contexts/PageHeaderProvider"),
  ]);
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await act(async () =>
    root.render(
      <I18nProvider>
        <MemoryRouter initialEntries={["/skills"]}>
          <ProfileProvider>
            <PageHeaderProvider pluginTabs={[]}>
              <SkillsPage />
            </PageHeaderProvider>
          </ProfileProvider>
        </MemoryRouter>
      </I18nProvider>,
    ),
  );
  await waitFor(() =>
    Boolean(container.querySelector('input[placeholder="Search..."]')),
  );
}

beforeEach(() => {
  for (const fn of Object.values(apiMocks)) fn.mockReset();
  apiMocks.getSkills.mockResolvedValue([
    {
      name: "release-helper",
      description: "Cut release notes from the task journal.",
      category: "automation",
      enabled: true,
    },
  ]);
  apiMocks.getToolsets.mockResolvedValue([
    {
      name: "core",
      label: "Core",
      description: "Baseline tools",
      platform: "all",
      platform_label: "All",
      enabled: true,
      configured: true,
      tools: ["read_file"],
    },
  ]);
  apiMocks.toggleSkill.mockResolvedValue({ ok: true });
  apiMocks.getProfiles.mockResolvedValue({ profiles: [] });
  apiMocks.getActiveProfile.mockResolvedValue({ current: "default", active: "default" });
  vi.stubGlobal("fetch", vi.fn(async () => ({ ok: false, status: 500 })));
  vi.stubGlobal("ResizeObserver", class { disconnect() {} observe() {} unobserve() {} });
  vi.stubGlobal("matchMedia", () => ({ addEventListener() {}, matches: false, media: "", removeEventListener() {} }));
});

afterEach(async () => {
  await act(async () => root?.unmount());
  container?.remove();
  vi.unstubAllGlobals();
});

describe("SkillsPage filter panel (drift A21)", () => {
  it("renders the view switcher, category panel and search filter", async () => {
    await renderSkills();

    expect(container.textContent).toContain("Filters");
    expect(container.textContent).toContain("Categories");
    expect(container.textContent).toContain("All (1)");
    expect(container.textContent).toContain("Toolsets (1)");
    expect(container.textContent).toContain("Browse hub");
    expect(container.textContent).toContain("Automation");
    expect(container.querySelector('input[placeholder="Search..."]')).not.toBeNull();
  });

  it("shows the typed empty state when the search matches nothing", async () => {
    await renderSkills();
    const search = container.querySelector<HTMLInputElement>(
      'input[placeholder="Search..."]',
    );
    expect(search).not.toBeNull();

    await act(async () => setInputValue(search!, "zzz-no-skill"));
    await waitFor(() =>
      container.textContent!.includes("No skills match your search or filter."),
    );
  });
});
