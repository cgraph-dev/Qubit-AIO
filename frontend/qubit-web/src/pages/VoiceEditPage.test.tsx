// @vitest-environment jsdom
/** VoiceEditPage — settings load, opt-in toggle, prompt restore, command help. */
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { VoiceEditSettings } from "@/lib/voiceEdit";

const mocks = vi.hoisted(() => ({
  settings: vi.fn(),
  log: vi.fn(),
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
    events: [
      { ts: 2, event: "voice_edit.wake.command", chars: 41, engine: "kws" },
      { ts: 3, event: "qubit_compat.voice_edit.dictate", chars: 24,
        explicit: true },
    ],
    summary: { count: 2, errors: 0, last_error: null, last_event: null },
  });
});

afterEach(() => {
  act(() => root?.unmount());
  container.remove();
  vi.clearAllMocks();
});

describe("VoiceEditPage", () => {
  it("renders settings and the type-this-for-me command help", async () => {
    mocks.settings.mockResolvedValue({ settings: settings() });
    const { default: VoiceEditPage } = await import("./VoiceEditPage");
    await act(async () => root.render(<VoiceEditPage />));
    await flush();
    expect(container.textContent).toContain("Voice Typing & Editing");
    expect(container.textContent).toContain("Qubit, type this for me:");
    expect(container.textContent).toContain("Clear for LLM");
  });

  it("shows the voice activity log with metadata only", async () => {
    mocks.settings.mockResolvedValue({ settings: settings() });
    const { default: VoiceEditPage } = await import("./VoiceEditPage");
    await act(async () => root.render(<VoiceEditPage />));
    await flush();
    expect(container.querySelector('[data-testid="voice-edit-log"]')).toBeTruthy();
    const rows = container.querySelectorAll('[data-testid="voice-log-row"]');
    expect(rows.length).toBe(2);
    const text = container.querySelector('[data-testid="voice-edit-log"]')?.textContent ?? "";
    expect(text).toContain("wake.command");
    expect(text).toContain("chars=41");
    expect(text).toContain("dictate");
    expect(mocks.log).toHaveBeenCalled();
  });

  it("opts in through the enable checkbox", async () => {
    mocks.settings.mockResolvedValue({ settings: settings() });
    mocks.saveSettings.mockResolvedValue({ settings: settings({ enabled: true }) });
    const { default: VoiceEditPage } = await import("./VoiceEditPage");
    await act(async () => root.render(<VoiceEditPage />));
    await flush();
    const box = container.querySelector(
      '[data-testid="voice-edit-enabled"]',
    ) as HTMLInputElement;
    await act(async () => {
      box.click();
      await Promise.resolve();
    });
    expect(mocks.saveSettings).toHaveBeenCalledWith({ enabled: true });
    expect(container.textContent).toContain("qubit-voice-typing");
  });

  it("renders the STT mode/model and router controls from settings", async () => {
    mocks.settings.mockResolvedValue({
      settings: settings({
        stt_mode: "streaming",
        stt_model: "medium",
        router: "brain",
      }),
    });
    const { default: VoiceEditPage } = await import("./VoiceEditPage");
    await act(async () => root.render(<VoiceEditPage />));
    await flush();
    const mode = container.querySelector(
      '[data-testid="voice-edit-stt-mode"]',
    ) as HTMLSelectElement;
    const model = container.querySelector(
      '[data-testid="voice-edit-stt-model"]',
    ) as HTMLSelectElement;
    const router = container.querySelector(
      '[data-testid="voice-edit-router"]',
    ) as HTMLSelectElement;
    expect(mode.value).toBe("streaming");
    expect(model.value).toBe("medium");
    expect(router.value).toBe("brain");
    expect(container.textContent).toContain("small ≈ 0.5 GB RAM/VRAM");
    expect(container.textContent).toContain(
      "never bypasses confirmation or e-stop",
    );
  });

  it("saves the STT mode through the existing settings PUT", async () => {
    mocks.settings.mockResolvedValue({ settings: settings() });
    mocks.saveSettings.mockResolvedValue({
      settings: settings({ stt_mode: "streaming" }),
    });
    const { default: VoiceEditPage } = await import("./VoiceEditPage");
    await act(async () => root.render(<VoiceEditPage />));
    await flush();
    const mode = container.querySelector(
      '[data-testid="voice-edit-stt-mode"]',
    ) as HTMLSelectElement;
    await act(async () => {
      mode.value = "streaming";
      mode.dispatchEvent(new Event("change", { bubbles: true }));
      await Promise.resolve();
    });
    expect(mocks.saveSettings).toHaveBeenCalledWith({ stt_mode: "streaming" });
  });

  it("saves the router selection through the existing settings PUT", async () => {
    mocks.settings.mockResolvedValue({ settings: settings() });
    mocks.saveSettings.mockResolvedValue({
      settings: settings({ router: "brain" }),
    });
    const { default: VoiceEditPage } = await import("./VoiceEditPage");
    await act(async () => root.render(<VoiceEditPage />));
    await flush();
    const router = container.querySelector(
      '[data-testid="voice-edit-router"]',
    ) as HTMLSelectElement;
    await act(async () => {
      router.value = "brain";
      router.dispatchEvent(new Event("change", { bubbles: true }));
      await Promise.resolve();
    });
    expect(mocks.saveSettings).toHaveBeenCalledWith({ router: "brain" });
  });

  it("shows a dictation transcript from the try-it panel", async () => {
    mocks.settings.mockResolvedValue({ settings: settings({ enabled: true }) });
    mocks.begin.mockResolvedValue({ request_id: "r1" });
    mocks.capture.mockResolvedValue({ selection: {}, target_app: "" });
    mocks.transform.mockResolvedValue({
      ok: true,
      mode: "dictate",
      transcript: "typed by voice",
      request_id: "r1",
    });
    const { default: VoiceEditPage } = await import("./VoiceEditPage");
    await act(async () => root.render(<VoiceEditPage />));
    await flush();
    const run = container.querySelector(
      '[data-testid="voice-edit-run"]',
    ) as HTMLButtonElement;
    await act(async () => {
      run.click();
      await Promise.resolve();
      await Promise.resolve();
    });
    const ta = container.querySelector(
      '[data-testid="voice-edit-original-input"]',
    ) as HTMLTextAreaElement;
    expect(ta.value).toBe("typed by voice");
    expect(mocks.transform).toHaveBeenCalled();
  });
});
