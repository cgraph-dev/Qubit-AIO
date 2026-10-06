// @vitest-environment jsdom
/**
 * VoiceEditPage — "Voice sessions" + "Continuous listening" blocks.
 *
 * The settings API exposes `voice_session_mode` + the three retention numbers
 * and the continuous-listening keys. These tests pin that the page renders them
 * from the settings view and saves edits through the existing `update({...})`
 * PUT, with backend-matching clamps (settings.py::update_voice_edit).
 */
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { VoiceEditSettings } from "@/lib/voiceEdit";

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
    enabled: true,
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
        text: "Rewrite the selected text.",
        customized: false,
        effective_text: "Rewrite the selected text.",
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
    for (let i = 0; i < 4; i += 1) await Promise.resolve();
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

async function renderPage() {
  const { default: VoiceEditPage } = await import("./VoiceEditPage");
  await act(async () => root.render(<VoiceEditPage />));
  await flush();
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

describe("VoiceEditPage voice sessions & continuous listening", () => {
  it("renders the voice-session and continuous blocks from settings", async () => {
    mocks.settings.mockResolvedValue({
      settings: settings({
        voice_session_mode: "per_command",
        voice_session_ttl_s: 7200,
        voice_session_max_messages: 500,
        voice_session_max_sessions: 25,
        continuous_mode: true,
        continuous_window_s: 30,
        continuous_max_followups: 8,
      }),
    });
    await renderPage();

    expect(
      container.querySelector('[data-testid="voice-edit-voice-sessions"]'),
    ).toBeTruthy();
    expect(
      container.querySelector('[data-testid="voice-edit-continuous"]'),
    ).toBeTruthy();

    const mode = container.querySelector(
      '[data-testid="voice-session-mode"]',
    ) as HTMLSelectElement;
    const ttl = container.querySelector(
      '[data-testid="voice-session-ttl"]',
    ) as HTMLInputElement;
    const maxMessages = container.querySelector(
      '[data-testid="voice-session-max-messages"]',
    ) as HTMLInputElement;
    const maxSessions = container.querySelector(
      '[data-testid="voice-session-max-sessions"]',
    ) as HTMLInputElement;
    expect(mode.value).toBe("per_command");
    expect(ttl.value).toBe("7200");
    expect(maxMessages.value).toBe("500");
    expect(maxSessions.value).toBe("25");

    const continuous = container.querySelector(
      '[data-testid="continuous-mode"]',
    ) as HTMLInputElement;
    const windowS = container.querySelector(
      '[data-testid="continuous-window"]',
    ) as HTMLInputElement;
    const followups = container.querySelector(
      '[data-testid="continuous-max-followups"]',
    ) as HTMLInputElement;
    expect(continuous.checked).toBe(true);
    expect(windowS.value).toBe("30");
    expect(followups.value).toBe("8");
  });

  it("saves the voice session mode + retention through the settings PUT (clamped)", async () => {
    mocks.settings.mockResolvedValue({ settings: settings() });
    mocks.saveSettings.mockResolvedValue({ settings: settings() });
    await renderPage();

    const mode = container.querySelector(
      '[data-testid="voice-session-mode"]',
    ) as HTMLSelectElement;
    const ttl = container.querySelector(
      '[data-testid="voice-session-ttl"]',
    ) as HTMLInputElement;
    const maxMessages = container.querySelector(
      '[data-testid="voice-session-max-messages"]',
    ) as HTMLInputElement;
    const maxSessions = container.querySelector(
      '[data-testid="voice-session-max-sessions"]',
    ) as HTMLInputElement;

    await act(async () => {
      mode.value = "per_command";
      mode.dispatchEvent(new Event("change", { bubbles: true }));
      // Below the backend floors: these must clamp up on save.
      setInput(ttl, "30");
      setInput(maxMessages, "5");
      setInput(maxSessions, "0");
      await Promise.resolve();
    });

    const save = container.querySelector(
      '[data-testid="voice-session-save"]',
    ) as HTMLButtonElement;
    await act(async () => {
      save.click();
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(mocks.saveSettings).toHaveBeenCalledWith({
      voice_session_mode: "per_command",
      voice_session_ttl_s: 60,
      voice_session_max_messages: 10,
      voice_session_max_sessions: 1,
    });
  });

  it("saves continuous listening through the settings PUT (clamped)", async () => {
    mocks.settings.mockResolvedValue({ settings: settings() });
    // Echo the patch back (the real backend returns the merged settings view),
    // so an immediate checkbox PUT is reflected in the next render/save.
    mocks.saveSettings.mockImplementation(
      async (patch: Partial<VoiceEditSettings>) => ({
        settings: settings(patch),
      }),
    );
    await renderPage();

    const continuous = container.querySelector(
      '[data-testid="continuous-mode"]',
    ) as HTMLInputElement;
    await act(async () => {
      continuous.click();
      await Promise.resolve();
      await Promise.resolve();
    });
    // The enable switch applies immediately through the same PUT.
    expect(mocks.saveSettings).toHaveBeenCalledWith({ continuous_mode: true });

    const windowS = container.querySelector(
      '[data-testid="continuous-window"]',
    ) as HTMLInputElement;
    const followups = container.querySelector(
      '[data-testid="continuous-max-followups"]',
    ) as HTMLInputElement;
    await act(async () => {
      setInput(windowS, "999");
      setInput(followups, "99");
      await Promise.resolve();
    });

    const save = container.querySelector(
      '[data-testid="continuous-save"]',
    ) as HTMLButtonElement;
    await act(async () => {
      save.click();
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(mocks.saveSettings).toHaveBeenCalledWith({
      continuous_mode: true,
      continuous_window_s: 120,
      continuous_max_followups: 20,
    });
  });
});
