// @vitest-environment jsdom
/**
 * /channels — the broker-confirm round-trip and the reference-only credential
 * contract (dashboard audit AI10 items 1/2).
 *
 * `msg_update` stages every channel change as a typed 409 CONFIRM_REQUIRED; the
 * page must settle that stage through the in-app ConfirmDialog (not toast a
 * dead refusal), and the credential form must send `env:`/`vault:`/`file:`
 * references — never a raw secret — and read back the stored `is_set` state.
 */
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const ConfirmRequiredError = vi.hoisted(() => {
  return class ConfirmRequiredError extends Error {
    readonly detail: string;
    readonly confirmationId: string;
    private readonly settleFn: () => Promise<unknown>;
    constructor(
      detail: string,
      confirmationId: string,
      settle: () => Promise<unknown>,
    ) {
      super(`CONFIRM_REQUIRED: ${detail}`);
      this.name = "ConfirmRequiredError";
      this.detail = detail;
      this.confirmationId = confirmationId;
      this.settleFn = settle;
    }
    settle<T = unknown>(): Promise<T> {
      return this.settleFn() as Promise<T>;
    }
  };
});

const apiMocks = vi.hoisted(() => ({
  getMessagingPlatforms: vi.fn(),
  updateMessagingPlatform: vi.fn(),
  testMessagingPlatform: vi.fn(),
  restartGateway: vi.fn(),
  startTelegramOnboarding: vi.fn(),
  getTelegramOnboardingStatus: vi.fn(),
  applyTelegramOnboarding: vi.fn(),
  cancelTelegramOnboarding: vi.fn(),
  startWhatsAppOnboarding: vi.fn(),
  getWhatsAppOnboardingStatus: vi.fn(),
  applyWhatsAppOnboarding: vi.fn(),
  cancelWhatsAppOnboarding: vi.fn(),
  getActionStatus: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  api: apiMocks,
  ConfirmRequiredError,
}));

function envVar(over: Record<string, unknown> = {}) {
  return {
    key: "TELEGRAM_BOT_TOKEN",
    required: true,
    is_set: false,
    redacted_value: null,
    description: "bot token",
    prompt: "Telegram bot token",
    help: "",
    url: null,
    is_password: true,
    advanced: false,
    ...over,
  };
}

function platform(over: Record<string, unknown> = {}) {
  return {
    id: "telegram",
    name: "Telegram",
    description: "Run Qubit from Telegram.",
    docs_url: "",
    enabled: false,
    configured: false,
    gateway_running: true,
    state: "disabled",
    error_code: null,
    error_message: null,
    updated_at: null,
    home_channel: null,
    whatsapp_setup: null,
    env_vars: [envVar()],
    ...over,
  };
}

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

function buttonByText(text: string): HTMLButtonElement | null {
  // Confirm dialogs portal into document.body, outside `container`.
  return (
    [...document.querySelectorAll("button")].find(
      (b) => b.textContent?.trim() === text,
    ) ?? null
  );
}

async function renderChannels() {
  const [{ default: ChannelsPage }, { I18nProvider }, { PageHeaderProvider }] =
    await Promise.all([
      import("./ChannelsPage"),
      import("@/i18n"),
      import("@/contexts/PageHeaderProvider"),
    ]);
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await act(async () =>
    root.render(
      <I18nProvider>
        <MemoryRouter initialEntries={["/channels"]}>
          <PageHeaderProvider pluginTabs={[]}>
            <ChannelsPage />
          </PageHeaderProvider>
        </MemoryRouter>
      </I18nProvider>,
    ),
  );
  await waitFor(() => Boolean(container.querySelector('[role="switch"]')));
}

beforeEach(() => {
  for (const fn of Object.values(apiMocks)) fn.mockReset();
  apiMocks.getMessagingPlatforms.mockResolvedValue({
    env_path: "/home/u/.local/state/qubit-voicebridge/runtime/.env",
    gateway_start_command: "qubit gateway start",
    platforms: [platform()],
  });
  apiMocks.restartGateway.mockResolvedValue({ ok: true });
  vi.stubGlobal(
    "ResizeObserver",
    class {
      disconnect() {}
      observe() {}
      unobserve() {}
    },
  );
  vi.stubGlobal("matchMedia", () => ({
    addEventListener() {},
    matches: false,
    media: "",
    removeEventListener() {},
  }));
});

afterEach(async () => {
  await act(async () => root?.unmount());
  container?.remove();
  vi.unstubAllGlobals();
});

describe("ChannelsPage broker-confirm round-trip (audit AI10 item 1)", () => {
  it("settles a staged 409 through the ConfirmDialog instead of toasting a dead refusal", async () => {
    let settled = false;
    apiMocks.updateMessagingPlatform.mockImplementation(
      async (id: string, _body: unknown, confirm?: (d: string) => Promise<boolean>) => {
        const ok = await confirm!("changing messaging channel requires confirmation");
        if (!ok) {
          throw new ConfirmRequiredError("cancelled", "cid", async () => ({}));
        }
        settled = true;
        return { ok: true, platform: id };
      },
    );

    await renderChannels();
    const toggle = container.querySelector('[role="switch"]') as HTMLButtonElement;
    await act(async () => toggle.click());

    // The page renders the confirmation dialog; nothing has been settled yet.
    await waitFor(() => Boolean(buttonByText("Confirm")));
    expect(settled).toBe(false);

    await act(async () => buttonByText("Confirm")!.click());
    await waitFor(() => settled);
    expect(apiMocks.updateMessagingPlatform).toHaveBeenCalledTimes(1);
    // A successful settle arms the real restart banner and never an error toast.
    await waitFor(() =>
      (container.textContent ?? "").includes("Restart the gateway"),
    );
    expect(container.textContent).not.toContain("unchanged");
  });

  it("keeps the channel unchanged and reports it when the confirmation is cancelled", async () => {
    apiMocks.updateMessagingPlatform.mockImplementation(
      async (_id: string, _body: unknown, confirm?: (d: string) => Promise<boolean>) => {
        const ok = await confirm!("changing messaging channel requires confirmation");
        if (!ok) {
          throw new ConfirmRequiredError("cancelled", "cid", async () => ({}));
        }
        return { ok: true };
      },
    );

    await renderChannels();
    const toggle = container.querySelector('[role="switch"]') as HTMLButtonElement;
    await act(async () => toggle.click());
    await waitFor(() => Boolean(buttonByText("Cancel")));
    await act(async () => buttonByText("Cancel")!.click());

    // The Toast portals into document.body; wait for the honest failure text.
    await waitFor(() =>
      (document.body.textContent ?? "").includes("confirmation cancelled"),
    );
    // State reverted: the switch is still unchecked.
    expect(
      (container.querySelector('[role="switch"]') as HTMLElement).getAttribute(
        "aria-checked",
      ),
    ).toBe("false");
  });
});

describe("ChannelsPage reference-only credentials (audit AI10 item 2)", () => {
  it("refuses a raw secret client-side before any PUT and shows the reference contract", async () => {
    apiMocks.getMessagingPlatforms.mockResolvedValue({
      env_path: "/home/u/.local/state/qubit-voicebridge/runtime/.env",
      gateway_start_command: "qubit gateway start",
      platforms: [
        platform({
          id: "slack",
          name: "Slack",
          env_vars: [
            envVar({ key: "SLACK_BOT_TOKEN", prompt: "Slack bot token" }),
          ],
        }),
      ],
    });
    await renderChannels();

    await act(async () => buttonByText("Configure")!.click());
    await waitFor(() => Boolean(container.querySelector("#field-SLACK_BOT_TOKEN")));
    const input = container.querySelector(
      "#field-SLACK_BOT_TOKEN",
    ) as HTMLInputElement;
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(
        window.HTMLInputElement.prototype,
        "value",
      )!.set!;
      setter.call(input, "xoxb-raw-secret-value");
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await act(async () => buttonByText("Save & enable")!.click());

    await waitFor(() =>
      (container.textContent ?? "").includes("stored as references"),
    );
    expect(apiMocks.updateMessagingPlatform).not.toHaveBeenCalled();
  });

  it("shows the read-back is_set state as a stored-reference badge", async () => {
    apiMocks.getMessagingPlatforms.mockResolvedValue({
      env_path: "/home/u/.local/state/qubit-voicebridge/runtime/.env",
      gateway_start_command: "qubit gateway start",
      platforms: [
        platform({
          id: "slack",
          name: "Slack",
          env_vars: [
            envVar({
              key: "SLACK_BOT_TOKEN",
              prompt: "Slack bot token",
              is_set: true,
            }),
          ],
        }),
      ],
    });
    await renderChannels();

    await act(async () => buttonByText("Configure")!.click());
    await waitFor(() =>
      (container.textContent ?? "").includes("reference stored"),
    );
  });
});
