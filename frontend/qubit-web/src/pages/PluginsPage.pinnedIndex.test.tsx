// @vitest-environment jsdom
/**
 * /plugins — the curated, pinned plugin index section.
 *
 * The page must render the curated + pinned entries with their full pinned
 * ref, and the install affordance must call the pinned-index install (name +
 * curated ref) and surface the real outcome — success toast on install, the
 * typed error otherwise. Never a fabricated success.
 */
import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { brandText } from "@/lib/catalog-brand";

const apiMocks = vi.hoisted(() => ({
  getPluginsHub: vi.fn(),
  getPluginCatalog: vi.fn(),
  installAgentPluginFromIndex: vi.fn(),
  installAgentPlugin: vi.fn(),
  reloadPlugins: vi.fn(),
  rescanPlugins: vi.fn(),
  enableAgentPlugin: vi.fn(),
  disableAgentPlugin: vi.fn(),
  removeAgentPlugin: vi.fn(),
  getMemoryProviderConfig: vi.fn(),
}));

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, api: apiMocks };
});
vi.mock("@/plugins", () => ({ PluginSlot: () => null }));
vi.mock("@/contexts/usePageHeader", () => ({
  usePageHeader: () => ({ setAfterTitle: vi.fn(), setEnd: vi.fn() }),
}));

const PINNED_REF = "e8d59971d2b7901405b39dac7b03bdd616272d0d";

const pinnedIndex = {
  ok: true,
  state: "CURATED_PINNED",
  schema: "qubit-pinned-plugin-index/1",
  curated: true,
  pinned: true,
  source: "qubit_runtime/qubit_cli/data/plugin_index.json",
  curation: "curated subset",
  license_note: "unverified",
  install_policy: "operator-gated",
  query: "",
  count: 1,
  install_capability: "plugin.install",
  entries: [
    {
      id: "hermes-media-studio",
      name: "hermes-media-studio",
      label: "Media Studio",
      description: "generative media workspace",
      repo: "https://github.com/NousResearch/hermes-media-studio",
      subdir: "",
      pinned_ref: PINNED_REF,
      pinned_ref_short: PINNED_REF.slice(0, 8),
      homepage: "https://github.com/NousResearch/hermes-media-studio",
      maintainer: "NousResearch",
      license: "unverified",
      tier: "official",
      category: "desktop",
      capabilities: ["tools", "dashboard"],
      api_version: 1,
      tags: ["media"],
      curated: true,
      pinned: true,
      install_state: "READY",
      install_code: "PLUGIN_INDEX_INSTALLABLE",
      install_capability: "plugin.install",
      provenance: { curation: "curated+pinned" },
    },
  ],
  install: {
    state: "READY",
    code: "PLUGIN_INDEX_INSTALLABLE",
    detail: "fetch at pin",
    prerequisite: "confirm",
    endpoint: "POST /api/dashboard/agent-plugins/install",
  },
  raw_remote_fetch: {
    state: "REFUSED",
    code: "BLOCKED_REMOTE_PLUGIN",
    detail: "raw fetch refused",
    prerequisite: "operator-reviewed local install",
  },
};

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

async function renderPage() {
  const [{ default: PluginsPage }, { I18nProvider }] = await Promise.all([
    import("./PluginsPage"),
    import("@/i18n"),
  ]);
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  const ui: ReactNode = (
    <I18nProvider>
      <MemoryRouter initialEntries={["/plugins"]}>
        <PluginsPage />
      </MemoryRouter>
    </I18nProvider>
  );
  await act(async () => root.render(ui));
}

beforeEach(() => {
  Object.values(apiMocks).forEach((fn) => fn.mockReset());
  apiMocks.getPluginsHub.mockResolvedValue({
    plugins: [],
    orphan_dashboard_plugins: [],
    providers: {
      memory_provider: "",
      memory_options: [],
      context_engine: "compressor",
      context_options: [],
    },
    catalog_index: pinnedIndex,
  });
  apiMocks.getPluginCatalog.mockResolvedValue({
    ok: true,
    state: "OK",
    schema: "qubit-dashboard-plugin-catalog/1",
    generated_at: "2026-09-23T00:00:00Z",
    query: "",
    source: "",
    count: 0,
    counts: {},
    estop: false,
    remote: {
      state: "REFUSED",
      code: "BLOCKED_REMOTE_PLUGIN",
      detail: "raw fetch refused",
      prerequisite: "operator-reviewed local install",
    },
    pinned_index: pinnedIndex,
    install_capability: "plugin.install",
    entries: [],
  });
  apiMocks.installAgentPluginFromIndex.mockRejectedValue(
    new Error("PLUGIN_INDEX_OPERATOR_GATED: REFUSED_PROOF"),
  );
});

afterEach(async () => {
  await act(async () => root?.unmount());
  container?.remove();
});

describe("PluginsPage curated pinned index", () => {
  it("renders curated + pinned entries with their full pinned ref", async () => {
    await renderPage();
    await waitFor(() =>
      container.textContent?.includes("Plugin index · pinned") === true,
    );
    await waitFor(() => container.textContent?.includes("Media Studio") === true);
    expect(container.textContent).toContain(PINNED_REF);
    expect(container.textContent).toContain("curated");
    expect(container.textContent).toContain("pinned");
    expect(
      container.querySelector('[data-testid="plugin-pinned-index-section"]'),
    ).not.toBeNull();
    expect(
      container.querySelector('[data-testid="plugin-pinned-index-install-gate"]'),
    ).not.toBeNull();
  });

  it("marks unreviewed upstream rows with an unreviewed badge, not curated", async () => {
    apiMocks.getPluginCatalog.mockResolvedValue({
      ok: true,
      state: "OK",
      schema: "qubit-dashboard-plugin-catalog/1",
      generated_at: "2026-09-23T00:00:00Z",
      query: "",
      source: "",
      count: 1,
      counts: {},
      estop: false,
      remote: { state: "REFUSED", code: "BLOCKED_REMOTE_PLUGIN" },
      pinned_index: {
        ...pinnedIndex,
        count: 2,
        entries: [
          pinnedIndex.entries[0],
          {
            ...pinnedIndex.entries[0],
            id: "adspirer",
            name: "adspirer",
            label: "Adspirer",
            curated: false,
            review: "unreviewed",
          },
        ],
      },
      install_capability: "plugin.install",
      entries: [],
    });
    await renderPage();
    await waitFor(() => container.textContent?.includes("Adspirer") === true);
    expect(container.textContent).toContain("unreviewed");
  });

  it("shows installed instead of Install for already-installed rows", async () => {
    apiMocks.getPluginCatalog.mockResolvedValue({
      ok: true,
      state: "OK",
      schema: "qubit-dashboard-plugin-catalog/1",
      generated_at: "2026-09-23T00:00:00Z",
      query: "",
      source: "",
      count: 1,
      counts: {},
      estop: false,
      remote: { state: "REFUSED", code: "BLOCKED_REMOTE_PLUGIN" },
      pinned_index: {
        ...pinnedIndex,
        count: 1,
        entries: [
          {
            ...pinnedIndex.entries[0],
            installed: true,
            installed_plugin: "hermes-media-studio",
            install_state: "INSTALLED",
          },
        ],
      },
      install_capability: "plugin.install",
      entries: [],
    });
    await renderPage();
    await waitFor(() => container.textContent?.includes("Media Studio") === true);
    const section = container.querySelector(
      '[data-testid="plugin-pinned-index-section"]',
    );
    expect(section?.textContent).toContain("installed");
    const installButtons = Array.from(
      section?.querySelectorAll<HTMLButtonElement>("button") ?? [],
    ).filter((b) => (b.textContent ?? "").trim() === "Install");
    expect(installButtons).toHaveLength(0);
  });

  it("installs by name + curated ref and toasts the real success", async () => {
    apiMocks.installAgentPluginFromIndex.mockResolvedValue({ ok: true });
    await renderPage();
    await waitFor(() => container.textContent?.includes("Media Studio") === true);
    const section = container.querySelector(
      '[data-testid="plugin-pinned-index-section"]',
    );
    const button = section?.querySelector("button");
    expect(button).not.toBeNull();
    await act(async () => {
      button?.click();
    });
    await waitFor(
      () => apiMocks.installAgentPluginFromIndex.mock.calls.length > 0,
    );
    expect(apiMocks.installAgentPluginFromIndex).toHaveBeenCalledWith(
      "hermes-media-studio",
      PINNED_REF,
    );
    // The real install resolves: success toast names the entry; the catalog
    // reloads instead of showing a refusal.
    await waitFor(
      () => document.body.textContent?.includes("Media Studio installed") === true,
    );
  });

  it("surfaces a typed install failure in the error toast, not success", async () => {
    apiMocks.installAgentPluginFromIndex.mockRejectedValue(
      new Error("PLUGIN_INDEX_REF_MISMATCH: REFUSED_PROOF"),
    );
    await renderPage();
    await waitFor(() => container.textContent?.includes("Media Studio") === true);
    const section = container.querySelector(
      '[data-testid="plugin-pinned-index-section"]',
    );
    const button = section?.querySelector("button");
    expect(button).not.toBeNull();
    await act(async () => {
      button?.click();
    });
    await waitFor(
      () => apiMocks.installAgentPluginFromIndex.mock.calls.length > 0,
    );
    // The typed failure is surfaced in the toast; no fabricated success.
    await waitFor(
      () => document.body.textContent?.includes("REFUSED_PROOF") === true,
    );
  });
});

describe("PluginsPage install error surfacing", () => {
  it("renders the typed install error inline with aria-invalid, not a silent dead control", async () => {
    apiMocks.installAgentPlugin.mockRejectedValue(
      new Error(
        '400: {"ok": false, "code": "PLUGIN_INSTALL_SOURCE", "detail": "no reviewed local plugin manifest for \'/nonexistent/path\'", "qubit_counterpart": "CLI `qubit plugin install`"}',
      ),
    );
    await renderPage();
    await waitFor(() => container.querySelector("#install-url") !== null);

    const input = container.querySelector<HTMLInputElement>("#install-url")!;
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(
        window.HTMLInputElement.prototype,
        "value",
      )?.set;
      setter?.call(input, "/nonexistent/path");
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });

    const installBtn = Array.from(
      container.querySelectorAll<HTMLButtonElement>("button"),
    ).find((b) => (b.textContent ?? "").trim() === "Install");
    expect(installBtn).not.toBeUndefined();
    await act(async () => installBtn?.click());

    await waitFor(
      () =>
        container.querySelector('[data-testid="plugin-install-error"]') !== null,
    );
    const err = container.querySelector('[data-testid="plugin-install-error"]');
    expect(err?.textContent).toContain("no reviewed local plugin manifest");
    expect(err?.getAttribute("role")).toBe("alert");
    expect(input.getAttribute("aria-invalid")).toBe("true");
  });
});

describe("PluginsPage catalog brand boundary", () => {
  it("rebrands legacy product prose without rewriting IDs, env contracts, or third-party names", () => {
    expect(brandText("Run Hermes Agent from the Hermes Desktop pane")).toBe(
      "Run Qubit Agent from the Qubit Desktop pane",
    );
    expect(brandText("config lives in ~/.hermes/config.yaml")).toBe(
      "config lives in ~/.config/qubit-voicebridge/config.yaml",
    );
    // Contracts and third-party identifiers stay untouched.
    expect(brandText("HERMES_HOME stays a wire contract")).toContain("HERMES_HOME");
    expect(brandText("githermes")).toBe("githermes");
    expect(brandText("https://github.com/NousResearch/hermes-media-studio")).toBe(
      "https://github.com/NousResearch/hermes-media-studio",
    );
  });

  it("renders pinned-index title and description through the brand boundary", async () => {
    apiMocks.getPluginCatalog.mockResolvedValue({
      ok: true,
      state: "OK",
      schema: "qubit-dashboard-plugin-catalog/1",
      generated_at: "2026-09-23T00:00:00Z",
      query: "",
      source: "",
      count: 1,
      counts: {},
      estop: false,
      remote: { state: "REFUSED", code: "BLOCKED_REMOTE_PLUGIN" },
      pinned_index: {
        ...pinnedIndex,
        count: 1,
        entries: [
          {
            ...pinnedIndex.entries[0],
            id: "hermes-media-studio",
            name: "hermes-media-studio",
            label: "Hermes Media Studio",
            description:
              "Generative media pane in Hermes Desktop; settings live in ~/.hermes/config.yaml.",
          },
        ],
      },
      install_capability: "plugin.install",
      entries: [],
    });
    await renderPage();
    await waitFor(() => container.textContent?.includes("Qubit Media Studio") === true);
    expect(container.textContent).toContain("Qubit Media Studio");
    expect(container.textContent).toContain("Qubit Desktop");
    expect(container.textContent).toContain("~/.config/qubit-voicebridge/config.yaml");
    expect(container.textContent).not.toContain("Hermes");
  });

  it("renders the local catalog label and description through the brand boundary", async () => {
    apiMocks.getPluginCatalog.mockResolvedValue({
      ok: true,
      state: "OK",
      schema: "qubit-dashboard-plugin-catalog/1",
      generated_at: "2026-09-23T00:00:00Z",
      query: "",
      source: "",
      count: 1,
      counts: {},
      estop: false,
      remote: { state: "REFUSED", code: "BLOCKED_REMOTE_PLUGIN" },
      pinned_index: null,
      install_capability: "plugin.install",
      entries: [
        {
          id: "hermes-media-studio",
          name: "hermes-media-studio",
          label: "Hermes Media Studio",
          description: "A pane inside Hermes Desktop.",
          version: "1.0.0",
          source: "bundled",
          kind: "plugin",
          path: "/tmp/hermes-media-studio",
          manifest_path: "/tmp/hermes-media-studio/plugin.yaml",
          installed: true,
          enabled: true,
          runtime_status: "enabled",
          loadable: true,
          load_state: "",
          has_dashboard_manifest: false,
          dashboard: null,
          provides: {
            tools: [],
            hooks: [],
            skills: [],
            web_providers: [],
            browser_providers: [],
            requires_env: [],
            optional_env: [],
          },
          provenance: {},
          can_enable: false,
          can_disable: true,
          can_remove: false,
          can_update: false,
          can_hide: false,
          can_install: false,
          user_hidden: false,
          declared_dependencies: [],
          install_capability: "plugin.install",
          error: null,
        },
      ],
    });
    await renderPage();
    await waitFor(() => container.textContent?.includes("Qubit Media Studio") === true);
    const section = container.querySelector('[data-testid="plugin-catalog-section"]');
    expect(section?.textContent).toContain("Qubit Media Studio");
    expect(section?.textContent).toContain("A pane inside Qubit Desktop.");
    expect(section?.textContent).not.toContain("Hermes");
  });
});

describe("PluginsPage duplicate catalog rows", () => {
  const row = (path: string, description: string) => ({
    name: "xai",
    version: "1.0.0",
    description,
    source: "bundled",
    runtime_status: "enabled" as const,
    has_dashboard_manifest: false,
    dashboard_manifest: null,
    path,
    can_remove: false,
    can_update_git: false,
    auth_required: false,
    auth_command: "",
    user_hidden: false,
  });

  it("keeps distinct providers that share a manifest name and emits no duplicate-key warning", async () => {
    const errors: string[] = [];
    const spy = vi.spyOn(console, "error").mockImplementation((...args: unknown[]) => {
      errors.push(args.map(String).join(" "));
    });
    try {
      apiMocks.getPluginsHub.mockResolvedValue({
        plugins: [
          row("/plugins/video_gen/xai", "xAI video backend"),
          row("/plugins/image_gen/xai", "xAI image backend"),
        ],
        orphan_dashboard_plugins: [],
        providers: {
          memory_provider: "",
          memory_options: [],
          context_engine: "compressor",
          context_options: [],
        },
        catalog_index: pinnedIndex,
      });
      await renderPage();
      await waitFor(() => container.textContent?.includes("xAI video backend") === true);
      expect(container.textContent).toContain("xAI video backend");
      expect(container.textContent).toContain("xAI image backend");
      expect(errors.join("\n")).not.toMatch(/same key/i);
    } finally {
      spy.mockRestore();
    }
  });
});

describe("PluginsPage memory provider refusal", () => {
  it("shows the typed MEMORY_LOCAL_ONLY refusal instead of a false ready/active state", async () => {
    apiMocks.getPluginsHub.mockResolvedValue({
      plugins: [],
      orphan_dashboard_plugins: [],
      providers: {
        memory_provider: "openviking",
        memory_options: [
          {
            name: "openviking",
            description: "OpenViking memory provider",
            available: true,
            configured: true,
            status: "ready",
          },
        ],
        context_engine: "compressor",
        context_options: [],
      },
      catalog_index: pinnedIndex,
    });
    apiMocks.getMemoryProviderConfig.mockRejectedValue(
      new Error(
        '403: {"ok": false, "state": "UNAVAILABLE", "code": "MEMORY_LOCAL_ONLY", "detail": "qubit-local is the only store and cloud sync is refused by policy"}',
      ),
    );

    await renderPage();
    await waitFor(
      () =>
        container.querySelector('[data-testid="memory-config-refusal"]') !== null,
    );
    const refusal = container.querySelector('[data-testid="memory-config-refusal"]');
    expect(refusal?.textContent).toContain("MEMORY_LOCAL_ONLY");
    expect(container.textContent).toContain("dashboard read-only");
    expect(container.textContent).toContain("active (agent config)");
    expect(container.textContent).not.toContain(
      "This provider does not expose dashboard settings.",
    );
    const save = Array.from(
      container.querySelectorAll<HTMLButtonElement>("button"),
    ).find((button) => (button.textContent ?? "").trim() === "Save memory provider");
    expect(save?.disabled).toBe(true);
  });
});

describe("PluginsPage plugin row controls", () => {
  it("gives the icon-only remove control an accessible name", async () => {
    apiMocks.getPluginsHub.mockResolvedValue({
      plugins: [
        {
          name: "demo-plugin",
          version: "1.0.0",
          description: "A removable demo plugin.",
          source: "user",
          runtime_status: "enabled",
          has_dashboard_manifest: false,
          dashboard_manifest: null,
          path: "/tmp/demo-plugin",
          can_remove: true,
          can_update_git: false,
          auth_required: false,
          auth_command: "",
          user_hidden: false,
        },
      ],
      orphan_dashboard_plugins: [],
      providers: {
        memory_provider: "",
        memory_options: [],
        context_engine: "compressor",
        context_options: [],
      },
      catalog_index: pinnedIndex,
    });
    await renderPage();
    await waitFor(
      () => container.textContent?.includes("demo-plugin") === true,
    );

    const remove = [...container.querySelectorAll("button")].find((button) =>
      (button.getAttribute("aria-label") ?? "").includes("Remove this plugin"),
    );
    expect(remove).toBeTruthy();
    expect(remove?.getAttribute("title")).toContain("Remove this plugin");
  });
});
