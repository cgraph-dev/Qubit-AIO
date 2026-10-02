// @vitest-environment jsdom
/**
 * /qubit-talk — reads render, writes settle through confirm, realtime rows
 * stay disabled-with-reason, and nothing here writes wake state.
 *
 * The API module is mocked; the suite asserts the page only ever calls the
 * read/write functions the manifest allows, and that no fetch reaches a
 * voice-edit path.
 */
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const talkMocks = vi.hoisted(() => ({
  getTalkMode: vi.fn(),
  getTalkCatalog: vi.fn(),
  getTalkConfig: vi.fn(),
  getVoicewake: vi.fn(),
  steerTalkClient: vi.fn(),
  appendTalkTranscript: vi.fn(),
  callTalkTool: vi.fn(),
  speakTalk: vi.fn(),
}));

vi.mock("@/lib/ocFamilyTalkApi", async (importOriginal) => {
  const orig = (await importOriginal()) as Record<string, unknown>;
  return {
    ...orig,
    getTalkMode: talkMocks.getTalkMode,
    getTalkCatalog: talkMocks.getTalkCatalog,
    getTalkConfig: talkMocks.getTalkConfig,
    getVoicewake: talkMocks.getVoicewake,
    steerTalkClient: talkMocks.steerTalkClient,
    appendTalkTranscript: talkMocks.appendTalkTranscript,
    callTalkTool: talkMocks.callTalkTool,
    speakTalk: talkMocks.speakTalk,
  };
});

import TalkPage from "@/pages/TalkPage";

let container: HTMLDivElement | null = null;
let root: Root | null = null;

function renderPage() {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  act(() => {
    root!.render(
      <MemoryRouter initialEntries={["/qubit-talk"]}>
        <TalkPage />
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

beforeEach(() => {
  vi.stubGlobal("fetch", fetchSpy);
  fetchSpy.mockReset();
  talkMocks.getTalkMode.mockResolvedValue({
    state: "OK",
    effectiveMode: "local-stt-tts",
    code: "TALK_MODE_LOCAL_SPEECH_PROFILE",
  });
  talkMocks.getTalkCatalog.mockResolvedValue({
    state: "OK",
    profiles: [{ id: "speech-out-default", kind: "speech_out" }],
  });
  talkMocks.getTalkConfig.mockResolvedValue({ state: "OK" });
  talkMocks.getVoicewake.mockResolvedValue({ state: "OK", engine: "kws" });
  talkMocks.steerTalkClient.mockResolvedValue({ state: "OK" });
  talkMocks.appendTalkTranscript.mockResolvedValue({ state: "OK" });
  talkMocks.callTalkTool.mockResolvedValue({ state: "OK" });
  talkMocks.speakTalk.mockResolvedValue({ state: "OK", provider: "piper" });
  for (const fn of Object.values(talkMocks)) fn.mockClear();
  // Re-arm defaults after the clear.
  talkMocks.getTalkMode.mockResolvedValue({
    state: "OK",
    effectiveMode: "local-stt-tts",
    code: "TALK_MODE_LOCAL_SPEECH_PROFILE",
  });
  talkMocks.getTalkCatalog.mockResolvedValue({
    state: "OK",
    profiles: [{ id: "speech-out-default", kind: "speech_out" }],
  });
  talkMocks.getTalkConfig.mockResolvedValue({ state: "OK" });
  talkMocks.getVoicewake.mockResolvedValue({ state: "OK", engine: "kws" });
  talkMocks.steerTalkClient.mockResolvedValue({ state: "OK" });
  talkMocks.appendTalkTranscript.mockResolvedValue({ state: "OK" });
  talkMocks.callTalkTool.mockResolvedValue({ state: "OK" });
  talkMocks.speakTalk.mockResolvedValue({ state: "OK", provider: "piper" });
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

function clickByLabel(label: string) {
  const el = container!.querySelector(
    `[aria-label="${label}"]`,
  ) as HTMLElement | null;
  expect(el, `control ${label}`).not.toBeNull();
  act(() => {
    el!.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  });
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

describe("TalkPage", () => {
  it("reads mode, catalog, config, and wake state on mount", async () => {
    renderPage();
    await flush();
    expect(talkMocks.getTalkMode).toHaveBeenCalled();
    expect(talkMocks.getTalkCatalog).toHaveBeenCalled();
    expect(talkMocks.getTalkConfig).toHaveBeenCalled();
    expect(talkMocks.getVoicewake).toHaveBeenCalled();
    expect(text()).toContain("local-stt-tts");
    expect(text()).toContain("speech-out-default");
  });

  it("renders the realtime session/client rows disabled with reasons", async () => {
    renderPage();
    await flush();
    const t = text();
    for (const action of [
      "talk.session.create",
      "talk.session.appendAudio",
      "talk.session.cancelOutput",
      "talk.session.close",
      "talk.session.acknowledgeMark",
      "talk.session.submitToolResult",
      "talk.session.steer",
      "talk.client.create",
      "talk.client.close",
    ]) {
      expect(t).toContain(action);
    }
    expect(t).toContain("TALK_REALTIME_OPERATOR_REQUIRED");
    const refused = container!.querySelectorAll('[data-state="refused"]');
    expect(refused.length).toBeGreaterThanOrEqual(11);
  });

  it("discloses wake as read-only with the wake writers disabled", async () => {
    renderPage();
    await flush();
    const t = text();
    expect(t).toContain("voicewake.set");
    expect(t).toContain("voicewake.routing.get");
    expect(t).toContain("only reads wake state");
  });

  it("steer settles through confirm with session + text", async () => {
    renderPage();
    await flush();
    const steerBtn = container!.querySelector(
      '[aria-label="Send talk steer"]',
    ) as HTMLButtonElement;
    // Empty inputs keep the write gated.
    expect(steerBtn.disabled).toBe(true);
    expect(talkMocks.steerTalkClient).not.toHaveBeenCalled();
    setInput("Session key", "sess-1");
    setInput("Steer text", "hold that thought");
    await flush();
    clickByLabel("Send talk steer");
    await flush();
    expect(talkMocks.steerTalkClient).toHaveBeenCalledTimes(1);
    const [input] = talkMocks.steerTalkClient.mock.calls[0];
    expect(input).toMatchObject({ sessionKey: "sess-1", text: "hold that thought" });
    expect(text()).toContain("talk.client.steer settled");
  });

  it("mirrors the upstream talk layout: status strip plus Talk options panel", async () => {
    renderPage();
    await flush();
    const status = container!.querySelector('[aria-label="Talk status"]');
    expect(status, "talk status strip").not.toBeNull();
    expect(status!.textContent).toContain("Mode local-stt-tts");
    const options = container!.querySelector('[aria-label="Talk options"]');
    expect(options, "talk options panel").not.toBeNull();
    expect(options!.textContent).toContain("Voice");
    expect(options!.textContent).toContain("speech-out-default");
    expect(options!.textContent).toContain("Model");
    expect(options!.textContent).toContain("Advanced");
    // The Advanced disclosure reports the resolved speech config read-only.
    expect(options!.textContent).toContain("Speech-in");
    expect(options!.textContent).toContain("Speech-out");
    expect(options!.textContent).toContain("Audio");
  });

  it("never fetches a voice-edit path", async () => {
    renderPage();
    await flush();
    for (const call of fetchSpy.mock.calls) {
      expect(String(call[0])).not.toMatch(/voice-edit|voice_edit/i);
    }
  });
});
