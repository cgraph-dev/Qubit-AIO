// @vitest-environment jsdom
/**
 * /webhooks per-subscription operations — Rotate / Test / Deliveries / Retry
 * must call the real id-scoped routes (`/api/webhooks/{id}/rotate|test|
 * deliveries|retry`) and surface the honest delivery verdict, not a fabricated
 * success.
 */
import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const apiMocks = vi.hoisted(() => ({
  getWebhooks: vi.fn(),
  rotateWebhookSecret: vi.fn(),
  testWebhook: vi.fn(),
  retryWebhook: vi.fn(),
  getWebhookDeliveries: vi.fn(),
}));

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, api: apiMocks };
});
vi.mock("@/plugins", () => ({ PluginSlot: () => null }));
vi.mock("@/contexts/usePageHeader", () => ({
  usePageHeader: () => ({ setAfterTitle: vi.fn(), setEnd: vi.fn() }),
}));

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
  const [{ default: WebhooksPage }, { I18nProvider }] = await Promise.all([
    import("./WebhooksPage"),
    import("@/i18n"),
  ]);
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  const ui: ReactNode = (
    <I18nProvider>
      <MemoryRouter initialEntries={["/webhooks"]}>
        <WebhooksPage />
      </MemoryRouter>
    </I18nProvider>
  );
  await act(async () => root.render(ui));
}

function buttonByLabel(label: string): HTMLButtonElement | null {
  return container.querySelector<HTMLButtonElement>(
    `button[aria-label="${label}"]`,
  );
}

beforeEach(() => {
  Object.values(apiMocks).forEach((fn) => fn.mockReset());
  apiMocks.getWebhooks.mockResolvedValue({
    enabled: true,
    base_url: "https://example.test",
    subscriptions: [
      {
        name: "hook",
        description: "",
        events: ["task.completed"],
        deliver: "log",
        deliver_only: false,
        prompt: "",
        skills: [],
        created_at: null,
        url: "https://hooks.example.test/agent",
        secret_set: true,
        enabled: true,
      },
    ],
  });
  apiMocks.rotateWebhookSecret.mockResolvedValue({
    ok: true,
    reference_kind: "file:",
  });
  apiMocks.testWebhook.mockResolvedValue({
    ok: true,
    verdict: "FAILED",
    error: "connection refused",
  });
  apiMocks.retryWebhook.mockResolvedValue({ ok: true, verdict: "DELIVERED" });
  apiMocks.getWebhookDeliveries.mockResolvedValue({
    ok: true,
    deliveries: [{ verdict: "FAILED", event: "webhook.test", status: 0 }],
    count: 1,
    subscription: "hook",
  });
});

afterEach(async () => {
  await act(async () => root?.unmount());
  container?.remove();
  vi.unstubAllGlobals();
});

describe("WebhooksPage per-subscription operations", () => {
  it("calls the real id-scoped rotate/test/retry routes and lists deliveries", async () => {
    await renderPage();
    await waitFor(() => apiMocks.getWebhooks.mock.calls.length > 0);
    await waitFor(() => buttonByLabel("Rotate signing secret") !== null);

    await act(async () => {
      buttonByLabel("Rotate signing secret")?.click();
    });
    await waitFor(() => apiMocks.rotateWebhookSecret.mock.calls.length > 0);
    expect(apiMocks.rotateWebhookSecret).toHaveBeenCalledWith(
      "hook",
      expect.any(Function),
    );

    await act(async () => {
      buttonByLabel("Send test delivery")?.click();
    });
    await waitFor(() => apiMocks.testWebhook.mock.calls.length > 0);
    expect(apiMocks.testWebhook).toHaveBeenCalledWith(
      "hook",
      expect.any(Function),
    );

    await act(async () => {
      buttonByLabel("Retry last failed delivery")?.click();
    });
    await waitFor(() => apiMocks.retryWebhook.mock.calls.length > 0);
    expect(apiMocks.retryWebhook).toHaveBeenCalledWith(
      "hook",
      expect.any(Function),
    );

    await act(async () => {
      buttonByLabel("Delivery history")?.click();
    });
    await waitFor(() => apiMocks.getWebhookDeliveries.mock.calls.length > 0);
    expect(apiMocks.getWebhookDeliveries).toHaveBeenCalledWith("hook");
    await waitFor(() => container.textContent?.includes("FAILED") ?? false);
  });
});
