// @vitest-environment jsdom
/** VoiceEditPage — wake routing block.
 *
 *  The view exposes `wake_routing.routes` with a STRUCTURED target
 *  (`{mode:"current"}` | `{agentId}` | `{sessionKey}`). The add form must send
 *  that structured object through the existing settings PUT; a bare string is
 *  rejected by the backend (`voice_edit/wake_routing.py`). */
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

function setSelect(el: HTMLSelectElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(
    window.HTMLSelectElement.prototype,
    "value",
  )?.set;
  setter?.call(el, value);
  el.dispatchEvent(new Event("change", { bubbles: true }));
}

async function renderPage(over: Partial<VoiceEditSettings> = {}) {
  mocks.settings.mockResolvedValue({ settings: settings(over) });
  const { default: VoiceEditPage } = await import("./VoiceEditPage");
  await act(async () => root.render(<VoiceEditPage />));
  await flush();
}

async function addRoute(mode: string, trigger: string, value = "") {
  const triggerInput = container.querySelector(
    '[data-testid="wake-route-trigger"]',
  ) as HTMLInputElement;
  await act(async () => {
    setInput(triggerInput, trigger);
    await Promise.resolve();
  });
  const modeSelect = container.querySelector(
    '[data-testid="wake-route-target-mode"]',
  ) as HTMLSelectElement;
  await act(async () => {
    setSelect(modeSelect, mode);
    await Promise.resolve();
  });
  if (mode !== "current") {
    const valueInput = container.querySelector(
      '[data-testid="wake-route-target-value"]',
    ) as HTMLInputElement;
    await act(async () => {
      setInput(valueInput, value);
      await Promise.resolve();
    });
  }
  const add = container.querySelector(
    '[data-testid="wake-route-add"]',
  ) as HTMLButtonElement;
  await act(async () => {
    add.click();
    await Promise.resolve();
    await Promise.resolve();
  });
  return add;
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

describe("VoiceEditPage wake routing", () => {
  it("renders routes with human-readable targets", async () => {
    const wake_routing: WakeRouting = {
      version: 1,
      defaultTarget: { mode: "current" },
      routes: [
        { trigger: "hello", target: { mode: "current" } },
        { trigger: "hey claude", target: { agentId: "qubit-brain4" } },
        { trigger: "resume session", target: { sessionKey: "sess-42" } },
      ],
    };
    await renderPage({ wake_routing });

    expect(
      container.querySelector('[data-testid="voice-edit-wake-routing"]'),
    ).toBeTruthy();
    const current = container.querySelector('[data-testid="wake-route-row-0"]');
    expect(current?.textContent).toContain("hello");
    expect(current?.textContent).toContain("current");
    const agent = container.querySelector('[data-testid="wake-route-row-1"]');
    expect(agent?.textContent).toContain("hey claude");
    expect(agent?.textContent).toContain("qubit-brain4");
    const session = container.querySelector('[data-testid="wake-route-row-2"]');
    expect(session?.textContent).toContain("resume session");
    expect(session?.textContent).toContain("sess-42");
  });

  it("shows the empty state when there are no routes", async () => {
    await renderPage({
      wake_routing: { version: 1, defaultTarget: { mode: "current" }, routes: [] },
    });
    expect(
      container.querySelector('[data-testid="voice-edit-wake-routing-empty"]'),
    ).toBeTruthy();
  });

  it("removes a route through the settings PUT", async () => {
    await renderPage({
      wake_routing: {
        version: 1,
        defaultTarget: { mode: "current" },
        routes: [{ trigger: "hello", target: { mode: "current" } }],
      },
    });
    mocks.saveSettings.mockResolvedValue({
      settings: settings({
        wake_routing: { version: 1, defaultTarget: { mode: "current" }, routes: [] },
      }),
    });
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

  it("adds a current-target route as a structured object", async () => {
    await renderPage({ wake_routing: undefined });
    mocks.saveSettings.mockResolvedValue({ settings: settings() });
    await addRoute("current", "hey qubit");
    expect(mocks.saveSettings).toHaveBeenCalledWith({
      wake_route_add: { trigger: "hey qubit", target: { mode: "current" } },
    });
  });

  it("adds an agentId-target route as a structured object", async () => {
    await renderPage({ wake_routing: undefined });
    mocks.saveSettings.mockResolvedValue({ settings: settings() });
    await addRoute("agentId", "hey helper", "qubit-brain4");
    expect(mocks.saveSettings).toHaveBeenCalledWith({
      wake_route_add: { trigger: "hey helper", target: { agentId: "qubit-brain4" } },
    });
  });

  it("adds a sessionKey-target route as a structured object", async () => {
    await renderPage({ wake_routing: undefined });
    mocks.saveSettings.mockResolvedValue({ settings: settings() });
    await addRoute("sessionKey", "continue talk", "sess-42");
    expect(mocks.saveSettings).toHaveBeenCalledWith({
      wake_route_add: { trigger: "continue talk", target: { sessionKey: "sess-42" } },
    });
  });

  it("disables Add on a blank trigger", async () => {
    await renderPage({ wake_routing: undefined });
    const add = container.querySelector(
      '[data-testid="wake-route-add"]',
    ) as HTMLButtonElement;
    expect(add.disabled).toBe(true);
  });

  it("disables Add when a non-current target has no value", async () => {
    await renderPage({ wake_routing: undefined });
    const triggerInput = container.querySelector(
      '[data-testid="wake-route-trigger"]',
    ) as HTMLInputElement;
    await act(async () => {
      setInput(triggerInput, "hey helper");
      await Promise.resolve();
    });
    const modeSelect = container.querySelector(
      '[data-testid="wake-route-target-mode"]',
    ) as HTMLSelectElement;
    await act(async () => {
      setSelect(modeSelect, "agentId");
      await Promise.resolve();
    });
    const add = container.querySelector(
      '[data-testid="wake-route-add"]',
    ) as HTMLButtonElement;
    expect(add.disabled).toBe(true);
  });
});
