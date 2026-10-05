// @vitest-environment jsdom
/** VoiceEditPage in-game orb checkbox — renders from settings, saves through the PUT. */
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

describe("VoiceEditPage in-game orb checkbox", () => {
  it("renders checked when the backend reports the override on", async () => {
    mocks.settings.mockResolvedValue({
      settings: settings({ waveform_ingame: true }),
    });
    const { default: VoiceEditPage } = await import("./VoiceEditPage");
    await act(async () => root.render(<VoiceEditPage />));
    await flush();
    const box = container.querySelector(
      '[data-testid="voice-edit-waveform-ingame"]',
    ) as HTMLInputElement;
    expect(box).toBeTruthy();
    expect(box.checked).toBe(true);
  });

  it("renders unchecked when the backend reports the override off", async () => {
    mocks.settings.mockResolvedValue({
      settings: settings({ waveform_ingame: false }),
    });
    const { default: VoiceEditPage } = await import("./VoiceEditPage");
    await act(async () => root.render(<VoiceEditPage />));
    await flush();
    const box = container.querySelector(
      '[data-testid="voice-edit-waveform-ingame"]',
    ) as HTMLInputElement;
    expect(box).toBeTruthy();
    expect(box.checked).toBe(false);
  });

  it("saves the toggle through the existing settings PUT", async () => {
    mocks.settings.mockResolvedValue({
      settings: settings({ waveform_ingame: false }),
    });
    mocks.saveSettings.mockResolvedValue({
      settings: settings({ waveform_ingame: true }),
    });
    const { default: VoiceEditPage } = await import("./VoiceEditPage");
    await act(async () => root.render(<VoiceEditPage />));
    await flush();
    const box = container.querySelector(
      '[data-testid="voice-edit-waveform-ingame"]',
    ) as HTMLInputElement;
    await act(async () => {
      box.click();
      await Promise.resolve();
    });
    expect(mocks.saveSettings).toHaveBeenCalledWith({
      waveform_ingame: true,
    });
  });

  it("adopts the server echo after save (no optimistic fantasy state)", async () => {
    // Same contract as the widget toggle: the checkbox follows the PUT echo,
    // never the click alone.
    mocks.settings.mockResolvedValue({
      settings: settings({ waveform_ingame: false }),
    });
    mocks.saveSettings.mockResolvedValue({
      settings: settings({ waveform_ingame: true }),
    });
    const { default: VoiceEditPage } = await import("./VoiceEditPage");
    await act(async () => root.render(<VoiceEditPage />));
    await flush();
    expect(
      (container.querySelector(
        '[data-testid="voice-edit-waveform-ingame"]',
      ) as HTMLInputElement).checked,
    ).toBe(false);
    const box = container.querySelector(
      '[data-testid="voice-edit-waveform-ingame"]',
    ) as HTMLInputElement;
    await act(async () => {
      box.click();
      await Promise.resolve();
    });
    await flush();
    expect(
      (container.querySelector(
        '[data-testid="voice-edit-waveform-ingame"]',
      ) as HTMLInputElement).checked,
    ).toBe(true);
  });

  it("surfaces a failed save honestly and keeps the old state", async () => {
    mocks.settings.mockResolvedValue({
      settings: settings({ waveform_ingame: false }),
    });
    mocks.saveSettings.mockRejectedValue(new Error("500: boom"));
    const { default: VoiceEditPage } = await import("./VoiceEditPage");
    await act(async () => root.render(<VoiceEditPage />));
    await flush();
    const box = container.querySelector(
      '[data-testid="voice-edit-waveform-ingame"]',
    ) as HTMLInputElement;
    await act(async () => {
      box.click();
      await Promise.resolve();
    });
    await flush();
    expect(container.querySelector('[role="alert"]')?.textContent).toContain(
      "500: boom",
    );
    expect(
      (container.querySelector(
        '[data-testid="voice-edit-waveform-ingame"]',
      ) as HTMLInputElement).checked,
    ).toBe(false);
  });

  it("never writes the sibling widget key when toggling the orb override", async () => {
    // waveform_ingame is the orb's fullscreen rule only: flipping it must not
    // mount/unmount the dashboard widget key.
    mocks.settings.mockResolvedValue({
      settings: settings({ waveform_visible: false, waveform_ingame: false }),
    });
    mocks.saveSettings.mockResolvedValue({
      settings: settings({ waveform_visible: false, waveform_ingame: true }),
    });
    const { default: VoiceEditPage } = await import("./VoiceEditPage");
    await act(async () => root.render(<VoiceEditPage />));
    await flush();
    const box = container.querySelector(
      '[data-testid="voice-edit-waveform-ingame"]',
    ) as HTMLInputElement;
    await act(async () => {
      box.click();
      await Promise.resolve();
    });
    expect(mocks.saveSettings).toHaveBeenCalledWith({
      waveform_ingame: true,
    });
    expect(mocks.saveSettings).not.toHaveBeenCalledWith(
      expect.objectContaining({ waveform_visible: expect.anything() }),
    );
  });
});
