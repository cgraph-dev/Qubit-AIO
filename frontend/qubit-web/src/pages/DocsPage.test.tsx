// @vitest-environment jsdom
/**
 * /docs drift A23 — a real OpenAPI reference, not a link stub.
 *
 * The page fetches the served OAS document, flattens its operations, groups
 * them by namespace, and filters them from the header input. This pins the
 * rendered operation path + filter behaviour against the served schema shape.
 */
import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/plugins", () => ({ PluginSlot: () => null }));
vi.mock("@/contexts/usePageHeader", () => ({
  usePageHeader: () => ({ setEnd: vi.fn(), setAfterTitle: vi.fn(), setTitle: vi.fn() }),
}));

const OAS = {
  openapi: "3.1.0",
  info: { title: "Qubit Agent", version: "0.21.1" },
  paths: {
    "/api/config/schema": {
      get: { summary: "Config schema", responses: { "200": { description: "ok" } } },
    },
    "/api/status": {
      get: { summary: "Status", responses: { "200": { description: "ok" } } },
    },
    // Legacy-named compat aliases (route still resolves, marked by the served
    // document). Per BRAND.md these must not lead the product surface.
    "/api/hermes/update": {
      post: {
        summary: "update alias",
        "x-qubit-compat-alias": true,
        responses: { "200": { description: "ok" } },
      },
    },
    "/api/plugins/qubit-achievements/achievements": {
      get: {
        summary: "achievements alias",
        "x-qubit-compat-alias": true,
        responses: { "200": { description: "ok" } },
      },
    },
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

function setInputValue(input: HTMLInputElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(
    HTMLInputElement.prototype,
    "value",
  )?.set;
  setter?.call(input, value);
  input.dispatchEvent(new Event("input", { bubbles: true }));
}

async function renderDocs(ui: ReactNode) {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await act(async () => root.render(ui));
}

async function renderDocsPage() {
  const [{ default: DocsPage }, { I18nProvider }] = await Promise.all([
    import("./DocsPage"),
    import("@/i18n"),
  ]);
  await renderDocs(
    <I18nProvider>
      <DocsPage />
    </I18nProvider>,
  );
  await waitFor(() =>
    Boolean(container.querySelector('input[placeholder="Filter operations"]')),
  );
}

beforeEach(() => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => ({
      ok: true,
      status: 200,
      json: async () => OAS,
      text: async () => "",
    })),
  );
});

afterEach(async () => {
  await act(async () => root?.unmount());
  container?.remove();
  vi.unstubAllGlobals();
});

describe("DocsPage OpenAPI reference (drift A23)", () => {
  it("renders the served OAS operations and groups, not a single link", async () => {
    await renderDocsPage();

    expect(container.textContent).toContain("/api/config/schema");
    expect(container.textContent).toContain("Config schema");
    expect(container.textContent).toContain("/api/status");
    // Group headers come from the namespace segment.
    expect(container.textContent).toContain("config");
    expect(container.querySelectorAll("section").length).toBeGreaterThanOrEqual(2);
  });

  it("segregates legacy compat aliases into a collapsed group (BRAND.md)", async () => {
    await renderDocsPage();

    // The legacy-named paths must not lead the product surface: they are
    // grouped under one "compatibility aliases" heading with no legacy group
    // label of their own ("hermes"/"plugins").
    expect(container.textContent).toContain("compatibility aliases");
    expect(
      container.querySelector('[aria-controls="docs-group-hermes"]'),
    ).toBeNull();
    expect(
      container.querySelector('[aria-controls="docs-group-plugins"]'),
    ).toBeNull();

    // Collapsed by default — the alias operations are not yet in the DOM.
    const aliasToggle = container.querySelector<HTMLButtonElement>(
      '[aria-controls="docs-group-compatibility aliases"]',
    );
    expect(aliasToggle).not.toBeNull();
    expect(aliasToggle!.getAttribute("aria-expanded")).toBe("false");
    expect(container.textContent).not.toContain("/api/hermes/update");

    // Expanding reveals them, still badged as aliases.
    await act(async () => aliasToggle!.click());
    await waitFor(() => container.textContent!.includes("/api/hermes/update"));
    expect(
      container.querySelectorAll('[data-testid="docs-compat-alias"]').length,
    ).toBe(2);
  });

  it("filters operations from the reference input", async () => {
    await renderDocsPage();
    const input = container.querySelector<HTMLInputElement>(
      'input[placeholder="Filter operations"]',
    );
    expect(input).not.toBeNull();

    await act(async () => setInputValue(input!, "config"));
    await waitFor(() => !container.textContent!.includes("/api/status"));
    expect(container.textContent).toContain("/api/config/schema");

    await act(async () => setInputValue(input!, "zzz-no-op"));
    await waitFor(() =>
      container.textContent!.includes("No API operations match"),
    );
  });
});
