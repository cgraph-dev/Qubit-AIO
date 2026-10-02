// @vitest-environment jsdom
/**
 * /qubit-scheduler — enabled-only cron toggle, TTS writes, power reads,
 * task cancel/retry, and the operator-owned rows disabled-with-reason.
 *
 * The API module is mocked; the suite asserts the page only ever calls the
 * manifest functions, labels the portal rows disambiguated from billing,
 * and never fetches a voice-edit path.
 */
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const schedMocks = vi.hoisted(() => ({
  updateCronEnabled: vi.fn(),
  setTtsProvider: vi.fn(),
  enableTtsProfile: vi.fn(),
  disableTtsProfile: vi.fn(),
  setTtsPersona: vi.fn(),
  getSuspendPrepare: vi.fn(),
  getSuspendResume: vi.fn(),
  cancelTask: vi.fn(),
  retryTask: vi.fn(),
}));

vi.mock("@/lib/ocFamilyTalkApi", async (importOriginal) => {
  const orig = (await importOriginal()) as Record<string, unknown>;
  return {
    ...orig,
    updateCronEnabled: schedMocks.updateCronEnabled,
    setTtsProvider: schedMocks.setTtsProvider,
    enableTtsProfile: schedMocks.enableTtsProfile,
    disableTtsProfile: schedMocks.disableTtsProfile,
    setTtsPersona: schedMocks.setTtsPersona,
    getSuspendPrepare: schedMocks.getSuspendPrepare,
    getSuspendResume: schedMocks.getSuspendResume,
    cancelTask: schedMocks.cancelTask,
    retryTask: schedMocks.retryTask,
  };
});

import SchedulerMediaPage from "@/pages/SchedulerMediaPage";

let container: HTMLDivElement | null = null;
let root: Root | null = null;

function renderPage() {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  act(() => {
    root!.render(
      <MemoryRouter initialEntries={["/qubit-scheduler"]}>
        <SchedulerMediaPage />
      </MemoryRouter>,
    );
  });
}

async function flush() {
  await act(async () => {
    await Promise.resolve();
    await new Promise((r) => setTimeout(r, 0));
  });
}

const fetchSpy = vi.hoisted(() => vi.fn());

function armDefaults() {
  schedMocks.updateCronEnabled.mockResolvedValue({ state: "OK" });
  schedMocks.setTtsProvider.mockResolvedValue({ state: "OK", provider: "piper" });
  schedMocks.enableTtsProfile.mockResolvedValue({ state: "OK" });
  schedMocks.disableTtsProfile.mockResolvedValue({ state: "OK" });
  schedMocks.setTtsPersona.mockResolvedValue({ state: "OK" });
  schedMocks.getSuspendPrepare.mockResolvedValue({ state: "UNAVAILABLE", power: {} });
  schedMocks.getSuspendResume.mockResolvedValue({ state: "UNAVAILABLE", power: {} });
  schedMocks.cancelTask.mockResolvedValue({ state: "OK" });
  schedMocks.retryTask.mockResolvedValue({ state: "OK" });
}

beforeEach(() => {
  vi.stubGlobal("fetch", fetchSpy);
  fetchSpy.mockReset();
  for (const fn of Object.values(schedMocks)) fn.mockReset();
  armDefaults();
});

afterEach(() => {
  if (root) root.unmount();
  if (container) container.remove();
  root = null;
  container = null;
  vi.unstubAllGlobals();
});

function text(): string {
  return container!.textContent ?? "";
}

function setInput(label: string, value: string) {
  const el = container!.querySelector(
    `[aria-label="${label}"]`,
  ) as HTMLInputElement | null;
  expect(el, `input ${label}`).not.toBeNull();
  const setter = Object.getOwnPropertyDescriptor(
    window.HTMLInputElement.prototype,
    "value",
  )?.set;
  act(() => {
    setter!.call(el, value);
    el!.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

function clickByLabel(label: string) {
  const el = container!.querySelector(
    `[aria-label="${label}"]`,
  ) as HTMLElement | null;
  expect(el, `control ${label}`).not.toBeNull();
  act(() => {
    el!.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  });
}

describe("SchedulerMediaPage", () => {
  it("reads power state on mount", async () => {
    renderPage();
    await flush();
    expect(schedMocks.getSuspendPrepare).toHaveBeenCalled();
    expect(schedMocks.getSuspendResume).toHaveBeenCalled();
  });

  it("applies the enabled-only cron toggle with an integer id", async () => {
    renderPage();
    await flush();
    const apply = container!.querySelector(
      '[aria-label="Apply schedule enabled toggle"]',
    ) as HTMLButtonElement;
    expect(apply.disabled).toBe(true);
    setInput("Schedule job id", "7");
    await flush();
    clickByLabel("Apply schedule enabled toggle");
    await flush();
    expect(schedMocks.updateCronEnabled).toHaveBeenCalledTimes(1);
    expect(schedMocks.updateCronEnabled).toHaveBeenCalledWith(
      7,
      true,
      expect.any(Function),
    );
    expect(text()).toContain("cron.update settled");
  });

  it("sets the TTS provider and toggles a speech profile", async () => {
    renderPage();
    await flush();
    clickByLabel("Set TTS provider");
    await flush();
    expect(schedMocks.setTtsProvider).toHaveBeenCalledTimes(1);
    setInput("Speech-out profile id", "main-voice");
    await flush();
    clickByLabel("Enable TTS profile");
    await flush();
    expect(schedMocks.enableTtsProfile).toHaveBeenCalledWith(
      "main-voice",
      expect.any(Function),
    );
  });

  it("cancels by receipt and retries by session", async () => {
    renderPage();
    await flush();
    setInput("Cancel receipt id", "rcpt-1");
    await flush();
    clickByLabel("Cancel task");
    await flush();
    expect(schedMocks.cancelTask).toHaveBeenCalledWith(
      { receiptId: "rcpt-1", session: undefined },
      expect.any(Function),
    );
    setInput("Retry session", "sess-9");
    await flush();
    clickByLabel("Retry task");
    await flush();
    expect(schedMocks.retryTask).toHaveBeenCalledWith(
      { receiptId: undefined, session: "sess-9" },
      expect.any(Function),
    );
  });

  it("labels portal rows disambiguated from billing and disables the rest", async () => {
    renderPage();
    await flush();
    const t = text();
    expect(t).toContain("Qubit Gateway Portal (proxy) — portal.list");
    expect(t).toContain("Not the billing portal on the System page.");
    for (const action of [
      "cron.scratch.get",
      "cron.scratch.set",
      "gateway.suspend.handoff",
      "taskSuggestions.create",
      "tasks.dismiss",
      "mentions.list",
      "mentions.dismiss",
    ]) {
      expect(t).toContain(action);
    }
    const refused = container!.querySelectorAll('[data-state="refused"]');
    expect(refused.length).toBeGreaterThanOrEqual(12);
  });

  it("never fetches a voice-edit path", async () => {
    renderPage();
    await flush();
    for (const call of fetchSpy.mock.calls) {
      expect(String(call[0])).not.toMatch(/voice-edit|voice_edit/i);
    }
  });
});
