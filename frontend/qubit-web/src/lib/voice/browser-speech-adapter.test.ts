// @vitest-environment jsdom
import { afterEach, describe, expect, it } from "vitest";
import {
  asSpeechRecognition,
  BrowserSpeechAdapter,
  BrowserSttUnavailable,
} from "./browser-speech-adapter";

class FakeSR {
  static instances: FakeSR[] = [];
  lang = "";
  interimResults = false;
  continuous = false;
  maxAlternatives = 1;
  onresult: ((event: unknown) => void) | null = null;
  onerror: ((event: { error?: string }) => void) | null = null;
  onend: (() => void) | null = null;
  started = false;
  stopped = false;
  aborted = false;
  constructor() {
    FakeSR.instances.push(this);
  }
  start() {
    this.started = true;
  }
  stop() {
    this.stopped = true;
  }
  abort() {
    this.aborted = true;
  }
  push(results: ArrayLike<{ isFinal: boolean; 0: { transcript: string } }>) {
    this.onresult?.({ resultIndex: 0, results });
  }
}

function install() {
  FakeSR.instances = [];
  Object.defineProperty(window, "SpeechRecognition", {
    configurable: true,
    value: FakeSR,
  });
}

afterEach(() => {
  delete (window as { SpeechRecognition?: unknown }).SpeechRecognition;
  delete (window as { webkitSpeechRecognition?: unknown })
    .webkitSpeechRecognition;
});

describe("BrowserSpeechAdapter", () => {
  it("sets interim/continuous and emits interim then final results", async () => {
    install();
    const partials: string[] = [];
    const finals: string[] = [];
    const adapter = new BrowserSpeechAdapter();
    expect(adapter.isAvailable()).toBe(true);
    await adapter.start({
      onPartial: (t) => partials.push(t),
      onFinal: (t) => finals.push(t),
      onError: () => undefined,
    });
    const rec = FakeSR.instances[0];
    expect(rec.interimResults).toBe(true);
    expect(rec.continuous).toBe(true);
    expect(rec.started).toBe(true);
    rec.push([{ isFinal: false, 0: { transcript: "helo" } }]);
    expect(partials).toEqual(["helo"]);
    rec.push([{ isFinal: true, 0: { transcript: "hello world" } }]);
    expect(finals).toEqual(["hello world"]);
  });

  it("maps an error event to a typed code", async () => {
    install();
    const errors: string[] = [];
    const adapter = new BrowserSpeechAdapter();
    await adapter.start({
      onPartial: () => undefined,
      onFinal: () => undefined,
      onError: (code) => errors.push(code),
    });
    FakeSR.instances[0].onerror?.({ error: "not-allowed" });
    expect(errors).toEqual(["VOICE_DENIED"]);
  });

  it("is unavailable (and throws a typed error) without SpeechRecognition", async () => {
    delete (window as { SpeechRecognition?: unknown }).SpeechRecognition;
    const adapter = new BrowserSpeechAdapter();
    expect(asSpeechRecognition()).toBeNull();
    expect(adapter.isAvailable()).toBe(false);
    await expect(
      adapter.start({
        onPartial: () => undefined,
        onFinal: () => undefined,
        onError: () => undefined,
      }),
    ).rejects.toBeInstanceOf(BrowserSttUnavailable);
  });
});
