// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { TranscriptionController } from "./transcription-controller";
import {
  joinTranscript,
  type ActiveVoiceEngine,
  type AdapterEvents,
  type TranscriptionAdapter,
} from "./types";

class FakeAdapter implements TranscriptionAdapter {
  started = 0;
  stopped = 0;
  cancelled = 0;
  disposed = 0;
  events: AdapterEvents | null = null;
  readonly kind: ActiveVoiceEngine;
  readonly label: string;
  available: boolean;
  reason: string | null;

  constructor(
    kind: ActiveVoiceEngine,
    label: string,
    available = true,
    reason: string | null = null,
  ) {
    this.kind = kind;
    this.label = label;
    this.available = available;
    this.reason = reason;
  }

  isAvailable(): boolean {
    return this.available;
  }

  availabilityReason(): string | null {
    return this.reason;
  }

  async start(events: AdapterEvents): Promise<void> {
    this.started += 1;
    this.events = events;
    events.onEngine?.(`${this.kind}-engine`);
  }

  async stop(): Promise<void> {
    this.stopped += 1;
  }

  async cancel(): Promise<void> {
    this.cancelled += 1;
  }

  dispose(): void {
    this.disposed += 1;
  }

  partial(text: string): void {
    this.events?.onPartial(text);
  }

  final(text: string): void {
    this.events?.onFinal(text);
  }

  error(code: string, detail = "boom"): void {
    this.events?.onError(code, detail);
  }

  end(): void {
    this.events?.onEnd?.();
  }
}

function harness(overrides: Partial<Record<ActiveVoiceEngine, FakeAdapter>> = {}) {
  const adapters: Record<ActiveVoiceEngine, FakeAdapter> = {
    browser: overrides.browser ?? new FakeAdapter("browser", "browser"),
    local: overrides.local ?? new FakeAdapter("local", "local"),
    terminal: overrides.terminal ?? new FakeAdapter("terminal", "terminal"),
  };
  let clock = 1000;
  const controller = new TranscriptionController({
    factory: (kind) => adapters[kind],
    now: () => (clock += 10),
  });
  return { controller, adapters };
}

describe("joinTranscript", () => {
  it("preserves spaces and punctuation without duplicating spaces", () => {
    expect(joinTranscript("", "hello")).toBe("hello");
    expect(joinTranscript("hello", "world")).toBe("hello world");
    expect(joinTranscript("hello ", "world")).toBe("hello world");
    expect(joinTranscript("Hello,", "world!")).toBe("Hello, world!");
    expect(joinTranscript("hi", "  ")).toBe("hi");
  });
});

describe("TranscriptionController", () => {
  it("renders a browser engine's interim and final results", async () => {
    const { controller, adapters } = harness();
    await controller.start("browser");
    expect(controller.getSnapshot().engine).toBe("browser");
    expect(controller.getSnapshot().engineLabel).toBe("browser-engine");
    adapters.browser.partial("helo");
    expect(controller.getSnapshot().interimText).toBe("helo");
    adapters.browser.partial("hello world");
    expect(controller.getSnapshot().interimText).toBe("hello world");
    adapters.browser.final("hello world");
    const snap = controller.getSnapshot();
    expect(snap.interimText).toBe("");
    expect(snap.committedText).toBe("hello world");
  });

  it("does not duplicate text across repeated partial hypotheses", async () => {
    const { controller, adapters } = harness();
    await controller.start("browser");
    adapters.browser.partial("one");
    adapters.browser.partial("one two");
    adapters.browser.partial("one two");
    adapters.browser.partial("one two three");
    expect(controller.getSnapshot().interimText).toBe("one two three");
    adapters.browser.final("one two three");
    expect(controller.getSnapshot().committedText).toBe("one two three");
  });

  it("does not append a duplicate final", async () => {
    const { controller, adapters } = harness();
    await controller.start("browser");
    adapters.browser.final("repeat");
    adapters.browser.final("repeat");
    expect(controller.getSnapshot().committedText).toBe("repeat");
  });

  it("preserves text typed before speaking, then appends the final", async () => {
    const { controller, adapters } = harness();
    controller.setCommittedText("typed base");
    await controller.start("browser");
    adapters.browser.final("spoken result");
    expect(controller.getSnapshot().committedText).toBe(
      "typed base spoken result",
    );
  });

  it("cancel discards only the interim segment", async () => {
    const { controller, adapters } = harness();
    await controller.start("browser");
    adapters.browser.final("kept");
    adapters.browser.partial("dropped interim");
    expect(controller.getSnapshot().interimText).toBe("dropped interim");
    await controller.cancel();
    const snap = controller.getSnapshot();
    expect(snap.committedText).toBe("kept");
    expect(snap.interimText).toBe("");
    expect(adapters.browser.cancelled).toBe(1);
  });

  it("stop flushes via the adapter and leaves text editable", async () => {
    const { controller, adapters } = harness();
    await controller.start("browser");
    const stopping = controller.stop();
    // The adapter's stop finalizes the segment before returning.
    adapters.browser.final("finalized on stop");
    await stopping;
    expect(adapters.browser.stopped).toBe(1);
    expect(controller.getSnapshot().state).toBe("idle");
    expect(controller.getSnapshot().committedText).toBe("finalized on stop");
  });

  it("auto falls back to local STT with a visible reason when browser is absent", async () => {
    const browser = new FakeAdapter("browser", "browser", false, "no browser");
    const { controller, adapters } = harness({ browser });
    await controller.start("auto");
    const snap = controller.getSnapshot();
    expect(snap.engine).toBe("local");
    expect(snap.fallbackReason).toContain("using local Qubit STT");
    expect(adapters.local.started).toBe(1);
  });

  it("a Firefox-style environment with no SpeechRecognition uses local STT", async () => {
    const browser = new FakeAdapter("browser", "browser", false, "unavailable");
    const { controller } = harness({ browser });
    await controller.start("auto");
    expect(controller.getSnapshot().engine).toBe("local");
  });

  it("an explicit unavailable engine surfaces a typed error, never a silent switch", async () => {
    const browser = new FakeAdapter(
      "browser",
      "browser",
      false,
      "browser speech recognition unavailable",
    );
    const { controller, adapters } = harness({ browser });
    await controller.start("browser");
    const snap = controller.getSnapshot();
    expect(snap.state).toBe("error");
    expect(snap.errorCode).toBe("VOICE_BROWSER_UNAVAILABLE");
    expect(snap.engine).toBeNull();
    expect(adapters.local.started).toBe(0);
  });

  it("ignores events from a superseded utterance (stale requests)", async () => {
    const { controller, adapters } = harness();
    await controller.start("browser");
    const firstEvents = adapters.browser.events;
    firstEvents?.onPartial("stale partial");
    await controller.cancel();
    // Events delivered after cancel (same adapter double) are ignored.
    firstEvents?.onPartial("stale partial after cancel");
    firstEvents?.onFinal("stale final");
    const snap = controller.getSnapshot();
    expect(snap.interimText).toBe("");
    expect(snap.committedText).toBe("");
  });

  it("keeps committed text when recognition errors mid-utterance", async () => {
    const { controller, adapters } = harness();
    await controller.start("browser");
    adapters.browser.final("kept before error");
    adapters.browser.error("VOICE_NETWORK", "connection lost");
    const snap = controller.getSnapshot();
    expect(snap.state).toBe("error");
    expect(snap.committedText).toBe("kept before error");
  });

  it("records time-to-first-partial and end-to-end metrics", async () => {
    const { controller, adapters } = harness();
    await controller.start("browser");
    adapters.browser.partial("hi");
    adapters.browser.final("hi");
    const m = controller.getSnapshot().metrics;
    expect(m.timeToFirstPartialMs).not.toBeNull();
    expect(m.endToEndMs).not.toBeNull();
  });

  it("terminal falls back to local capture with a reason when no bridge exists", async () => {
    const terminal = new FakeAdapter(
      "terminal",
      "terminal",
      false,
      "desktop/terminal audio bridge unavailable",
    );
    const { controller, adapters } = harness({ terminal });
    await controller.start("terminal");
    expect(controller.getSnapshot().engine).toBe("local");
    expect(controller.getSnapshot().fallbackReason).toContain(
      "terminal audio bridge unavailable",
    );
    expect(adapters.local.started).toBe(1);
  });

  it("setRequestedEngine is reported in the snapshot", () => {
    const { controller } = harness();
    controller.setRequestedEngine("local");
    expect(controller.getSnapshot().requestedEngine).toBe("local");
  });
});
