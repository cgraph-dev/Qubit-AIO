// @vitest-environment jsdom
/** VoiceEditPage — wake words + wake routing blocks render from the settings
 *  view and save through the existing single-item add/remove settings PUT. */
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { VoiceEditSettings, WakeRouting } from "@/lib/voiceEdit";

const CATALOG = {
  ok: true,
  groups: [],
  custom_commands: [],
  custom_actions: ["open_website", "play_youtube", "open_app", "open_document"],
};

const mocks = vi.hoisted(() => ({
  settings: vi.fn(),
  log: vi.fn(),
  models: vi.fn(),
  commands: vi.fn(),
  saveSettings: vi.fn(),
  restore: vi.fn(),
  exportSettings: vi.fn(),
  importSettings: vi.fn(),
  begin: vi.fn(),
  capture: vi.fn(),
  transform: vi.fn(),
  edit: vi.fn(),
  retry: vi.fn(),
  apply: vi.fn(),
  revert: vi.fn(),
  cancel: vi.fn(),
}));

vi.mock("@/lib/voiceEdit", async () => {
  const actual = await vi.importActual<typeof import("@/lib/voiceEdit")>(
    "@/lib/voiceEdit",
  );
  return { ...actual, voiceEdit: mocks };
});
vi.mock("@/lib/clipboard", () => ({ copyTextToClipboard: vi.fn(async () => true) }));

function settings(over: Partial<VoiceEditSettings> = {}): VoiceEditSettings {
  return {
    enabled: false,
    routing: "auto",
    local_profile: "qubit-brain4",
    api_profile: "",
    allow_api: true,
    confirm_before_replace: true,
    auto_send: false,
    insertion_method: "clipboard_paste",
    default_prompt: "clarify",
    asr_device: "",
    stt_engine: "",
    hotkey_mode: "hold",
    request_ttl_s: 300,
    per_app: {},
    prompt_versions: { clarify: "1" },
    prompts: [
      {
        action: "clarify",
        title: "Clear for LLM",
        version: "1",
        text: "Rewrite the selected text so another language model can understand it.",
        customized: false,
        effective_text: "Rewrite the selected text so another language model can understand it.",
      },
    ],
    ...over,
  };
}

let container: HTMLDivElement;
let root: Root;
(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

async function flush() {
  await act(async () => {
    await Promise.resolve();
  });
}

function setInput(el: HTMLInputElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(
    window.HTMLInputElement.prototype,
    "value",
  )?.set;
  setter?.call(el, value);
  el.dispatchEvent(new Event("input", { bubbles: true }));
}

beforeEach(() => {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  mocks.log.mockResolvedValue({
    ok: true,
    events: [],
    summary: { count: 0, errors: 0, last_error: null, last_event: null },
  });
  mocks.models.mockResolvedValue({ models: [] });
  mocks.commands.mockResolvedValue(CATALOG);
});

afterEach(() => {
  act(() => root?.unmount());
  container.remove();
  vi.clearAllMocks();
});

describe("VoiceEditPage wake words & routing", () => {
  it("renders the wake words and wake routes from settings", async () => {
    const wake_routing: WakeRouting = {
      version: 1,
      defaultTarget: { mode: "current" },
      routes: [{ trigger: "hello", target: { mode: "current" } }],
    };
    mocks.settings.mockResolvedValue({
      settings: settings({
        wake_words: ["computer", "assistant"],
        wake_words_default: ["hey qubit"],
        wake_routing,
      }),
    });
    const { default: VoiceEditPage } = await import("./VoiceEditPage");
    await act(async () => root.render(<VoiceEditPage />));
    await flush();
    expect(container.querySelector('[data-testid="voice-edit-wake-words"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="wake-word-row-computer"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="wake-word-row-assistant"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="voice-edit-wake-routing"]')).toBeTruthy();
    const row = container.querySelector('[data-testid="wake-route-row-0"]');
    expect(row?.textContent).toContain("hello");
    expect(row?.textContent).toContain("current");
  });

  it("removes a wake word through the settings PUT", async () => {
    mocks.settings.mockResolvedValue({
      settings: settings({ wake_words: ["computer"] }),
    });
    mocks.saveSettings.mockResolvedValue({
      settings: settings({ wake_words: [] }),
    });
    const { default: VoiceEditPage } = await import("./VoiceEditPage");
    await act(async () => root.render(<VoiceEditPage />));
    await flush();
    const remove = container.querySelector(
      '[data-testid="wake-word-remove-computer"]',
    ) as HTMLButtonElement;
    await act(async () => {
      remove.click();
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(mocks.saveSettings).toHaveBeenCalledWith({
      wake_words_remove: "computer",
    });
  });

  it("adds a wake word through the settings PUT", async () => {
    mocks.settings.mockResolvedValue({
      settings: settings({ wake_words: [] }),
    });
    mocks.saveSettings.mockResolvedValue({
      settings: settings({ wake_words: ["assistant"] }),
    });
    const { default: VoiceEditPage } = await import("./VoiceEditPage");
    await act(async () => root.render(<VoiceEditPage />));
    await flush();
    const input = container.querySelector(
      '[data-testid="wake-word-input"]',
    ) as HTMLInputElement;
    await act(async () => {
      setInput(input, "assistant");
      await Promise.resolve();
    });
    const add = container.querySelector(
      '[data-testid="wake-word-add"]',
    ) as HTMLButtonElement;
    expect(add.disabled).toBe(false);
    await act(async () => {
      add.click();
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(mocks.saveSettings).toHaveBeenCalledWith({
      wake_words_add: "assistant",
    });
  });

  it("removes a wake route through the settings PUT", async () => {
    mocks.settings.mockResolvedValue({
      settings: settings({
        wake_routing: {
          version: 1,
          defaultTarget: { mode: "current" },
          routes: [{ trigger: "hello", target: { mode: "current" } }],
        },
      }),
    });
    mocks.saveSettings.mockResolvedValue({
      settings: settings({
        wake_routing: { version: 1, defaultTarget: { mode: "current" }, routes: [] },
      }),
    });
    const { default: VoiceEditPage } = await import("./VoiceEditPage");
    await act(async () => root.render(<VoiceEditPage />));
    await flush();
    const remove = container.querySelector(
      '[data-testid="wake-route-remove-hello"]',
    ) as HTMLButtonElement;
    await act(async () => {
      remove.click();
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(mocks.saveSettings).toHaveBeenCalledWith({
      wake_route_remove: "hello",
    });
  });

  it("adds a wake route through the settings PUT", async () => {
    mocks.settings.mockResolvedValue({
      settings: settings({ wake_routing: undefined }),
    });
    mocks.saveSettings.mockResolvedValue({ settings: settings() });
    const { default: VoiceEditPage } = await import("./VoiceEditPage");
    await act(async () => root.render(<VoiceEditPage />));
    await flush();
    const trigger = container.querySelector(
      '[data-testid="wake-route-trigger"]',
    ) as HTMLInputElement;
    await act(async () => {
      setInput(trigger, "hey qubit");
      await Promise.resolve();
    });
    const add = container.querySelector(
      '[data-testid="wake-route-add"]',
    ) as HTMLButtonElement;
    expect(add.disabled).toBe(false);
    await act(async () => {
      add.click();
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(mocks.saveSettings).toHaveBeenCalledWith({
      wake_route_add: { trigger: "hey qubit", target: { mode: "current" } },
    });
  });
});
