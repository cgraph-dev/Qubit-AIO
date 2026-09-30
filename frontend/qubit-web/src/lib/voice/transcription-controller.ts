/**
 * TranscriptionController — the single front-end transcription authority.
 *
 * One controller drives every engine through an adapter (browser / local /
 * terminal). It owns the committed/interim text model, engine selection and
 * fallback reporting, stale-event rejection, cancel semantics, and latency
 * metrics. The composer renders `committedText` as editable text and
 * `interimText` as a temporary, visually distinct hypothesis.
 *
 * Guarantees:
 *  - `interimText` is always REPLACED by the newest hypothesis (never appended),
 *    so repeated partials cannot duplicate text.
 *  - `onFinal` appends to `committedText`; a final identical to the tail is
 *    dropped.
 *  - `cancel()` discards only the interim segment; committed text is preserved.
 *  - events from a superseded utterance are ignored (generation guard).
 *  - the engine is never switched silently: `fallbackReason` explains any
 *    fallback, and a hard failure surfaces a typed error instead.
 */
import { BrowserSpeechAdapter } from "./browser-speech-adapter";
import { LocalStreamingSTTAdapter } from "./local-streaming-stt-adapter";
import { TerminalSTTAdapter } from "./terminal-stt-adapter";
import {
  joinTranscript,
  type ActiveVoiceEngine,
  type AdapterEvents,
  type TranscriptionAdapter,
  type TranscriptionMetrics,
  type TranscriptionSnapshot,
  type VoiceEngine,
} from "./types";

export type AdapterFactory = (
  kind: ActiveVoiceEngine,
  sessionId?: () => string,
) => TranscriptionAdapter;

export interface TranscriptionControllerOptions {
  factory?: AdapterFactory;
  now?: () => number;
  requestedEngine?: VoiceEngine;
  /** The canonical chat session id (carried on the STT request for continuity). */
  sessionId?: () => string;
}

export function defaultAdapterFactory(
  kind: ActiveVoiceEngine,
  sessionId?: () => string,
): TranscriptionAdapter {
  if (kind === "browser") return new BrowserSpeechAdapter();
  if (kind === "terminal") return new TerminalSTTAdapter({ sessionId });
  return new LocalStreamingSTTAdapter({ sessionId });
}

function idleMetrics(): TranscriptionMetrics {
  return {
    engine: "",
    timeToFirstPartialMs: null,
    finalizationMs: null,
    endToEndMs: null,
    audioMs: 0,
  };
}

export class TranscriptionController {
  #factory: AdapterFactory;
  #now: () => number;
  #sessionId?: () => string;
  #listeners = new Set<(snapshot: TranscriptionSnapshot) => void>();
  #adapter: TranscriptionAdapter | null = null;
  #generation = 0;
  #startAt = 0;
  #stopAt = 0;
  #firstPartialAt: number | null = null;
  #finalAt: number | null = null;
  #lastFinal = "";
  #snapshot: TranscriptionSnapshot;

  constructor(opts: TranscriptionControllerOptions = {}) {
    this.#factory = opts.factory ?? defaultAdapterFactory;
    this.#now = opts.now ?? (() => Date.now());
    this.#sessionId = opts.sessionId;
    this.#snapshot = {
      requestedEngine: opts.requestedEngine ?? "auto",
      engine: null,
      engineLabel: "",
      state: "idle",
      committedText: "",
      interimText: "",
      error: null,
      errorCode: null,
      fallbackReason: null,
      connected: false,
      metrics: idleMetrics(),
    };
  }

  getSnapshot(): TranscriptionSnapshot {
    return this.#snapshot;
  }

  subscribe(listener: (snapshot: TranscriptionSnapshot) => void): () => void {
    this.#listeners.add(listener);
    return () => {
      this.#listeners.delete(listener);
    };
  }

  get active(): boolean {
    return (
      this.#snapshot.state === "starting" ||
      this.#snapshot.state === "listening" ||
      this.#snapshot.state === "stopping"
    );
  }

  setRequestedEngine(engine: VoiceEngine): void {
    if (engine === this.#snapshot.requestedEngine) return;
    this.#patch({ requestedEngine: engine });
  }

  /** Replace the committed text (used when the user edits the composer). */
  setCommittedText(text: string): void {
    this.#patch({ committedText: text });
  }

  /** Append manually-committed text (same rules as a final result). */
  commitText(text: string): void {
    this.#commit(text);
  }

  async start(engine?: VoiceEngine): Promise<void> {
    const requested = engine ?? this.#snapshot.requestedEngine;
    if (this.active) return;
    const resolved = this.#resolve(requested);
    if ("error" in resolved) {
      this.#patch({
        state: "error",
        error: resolved.error,
        errorCode: resolved.code,
        engine: null,
        engineLabel: "",
        interimText: "",
      });
      return;
    }
    const { adapter, fallbackReason } = resolved;
    this.#generation += 1;
    const gen = this.#generation;
    this.#startAt = this.#now();
    this.#stopAt = 0;
    this.#firstPartialAt = null;
    this.#finalAt = null;
    this.#lastFinal = "";
    this.#patch({
      engine: adapter.kind,
      engineLabel: adapter.label,
      state: "starting",
      interimText: "",
      error: null,
      errorCode: null,
      fallbackReason,
      connected: false,
      metrics: { ...idleMetrics(), engine: adapter.label },
    });
    const events: AdapterEvents = {
      onPartial: (text) => {
        if (!this.#current(gen)) return;
        const clean = (text ?? "").trim();
        if (this.#firstPartialAt === null && clean) {
          this.#firstPartialAt = this.#now();
        }
        // REPLACE the interim span — never append (no duplicate text).
        this.#patch({ interimText: clean, metrics: this.#metrics() });
      },
      onFinal: (text) => {
        if (!this.#current(gen)) return;
        this.#commit(text);
      },
      onEngine: (label) => {
        if (!this.#current(gen)) return;
        this.#patch({ engineLabel: label, metrics: this.#metrics() });
      },
      onMetrics: (m) => {
        if (!this.#current(gen)) return;
        const cur = this.#snapshot.metrics;
        this.#patch({
          metrics: {
            ...cur,
            audioMs: m.audioMs ?? cur.audioMs,
            timeToFirstPartialMs:
              m.timeToFirstPartialMs ?? cur.timeToFirstPartialMs,
            finalizationMs: m.finalizationMs ?? cur.finalizationMs,
          },
        });
      },
      onError: (code, detail) => {
        if (!this.#current(gen)) return;
        this.#finalAt = this.#now();
        this.#patch({
          state: "error",
          errorCode: code,
          error: `[${code}] ${detail}`,
          metrics: this.#metrics(),
        });
      },
      onEnd: () => {
        if (!this.#current(gen)) return;
        if (this.#snapshot.state !== "error") {
          this.#patch({ state: "idle", connected: false });
        }
      },
      onConnected: () => {
        if (!this.#current(gen)) return;
        this.#patch({ connected: true });
      },
    };
    this.#adapter = adapter;
    try {
      await adapter.start(events);
      if (this.#current(gen) && this.#snapshot.state === "starting") {
        this.#patch({ state: "listening" });
      }
    } catch (err) {
      if (this.#current(gen)) {
        const code =
          (err as { code?: string }).code ?? "VOICE_ERROR";
        const detail = err instanceof Error ? err.message : String(err);
        this.#finalAt = this.#now();
        this.#patch({
          state: "error",
          errorCode: code,
          error: `[${code}] ${detail}`,
          metrics: this.#metrics(),
        });
      }
    }
  }

  /** Finalize: flush the last segment before returning; text stays editable. */
  async stop(): Promise<void> {
    const adapter = this.#adapter;
    if (!adapter) return;
    this.#stopAt = this.#now();
    this.#patch({ state: "stopping" });
    try {
      await adapter.stop();
    } finally {
      adapter.dispose();
      if (this.#adapter === adapter) this.#adapter = null;
      if (this.#snapshot.state === "stopping") {
        this.#patch({
          state: "idle",
          connected: false,
          interimText: "",
          metrics: this.#metrics(),
        });
      }
    }
  }

  /** Discard the current interim segment only (committed text is preserved). */
  async cancel(): Promise<void> {
    this.#generation += 1;
    const adapter = this.#adapter;
    this.#adapter = null;
    if (adapter) {
      try {
        await adapter.cancel();
      } finally {
        adapter.dispose();
      }
    }
    this.#patch({ state: "idle", interimText: "", connected: false });
  }

  dispose(): void {
    this.#generation += 1;
    this.#adapter?.dispose();
    this.#adapter = null;
    this.#listeners.clear();
  }

  // -- internals -------------------------------------------------------
  #resolve(
    requested: VoiceEngine,
  ): { adapter: TranscriptionAdapter; fallbackReason: string | null } | {
    error: string;
    code: string;
  } {
    const browser = this.#factory("browser", this.#sessionId);
    const local = this.#factory("local", this.#sessionId);
    const terminal = this.#factory("terminal", this.#sessionId);
    const noMic = local.availabilityReason() ?? "microphone unavailable";
    if (requested === "browser") {
      if (!browser.isAvailable()) {
        return {
          error: `[VOICE_BROWSER_UNAVAILABLE] ${browser.availabilityReason() ?? "browser recognition unavailable"}`,
          code: "VOICE_BROWSER_UNAVAILABLE",
        };
      }
      return { adapter: browser, fallbackReason: null };
    }
    if (requested === "local") {
      if (!local.isAvailable()) {
        return { error: `[VOICE_NO_MIC] ${noMic}`, code: "VOICE_NO_MIC" };
      }
      return { adapter: local, fallbackReason: null };
    }
    if (requested === "terminal") {
      if (terminal.isAvailable()) {
        return { adapter: terminal, fallbackReason: null };
      }
      if (local.isAvailable()) {
        return {
          adapter: local,
          fallbackReason:
            "terminal audio bridge unavailable — using local capture",
        };
      }
      return {
        error: `[VOICE_TERMINAL_UNAVAILABLE] ${
          terminal.availabilityReason() ?? "terminal capture unavailable"
        }`,
        code: "VOICE_TERMINAL_UNAVAILABLE",
      };
    }
    // auto: prefer the browser engine; otherwise local, with a visible reason.
    if (browser.isAvailable()) {
      return { adapter: browser, fallbackReason: null };
    }
    if (local.isAvailable()) {
      return {
        adapter: local,
        fallbackReason:
          "browser speech recognition unavailable — using local Qubit STT",
      };
    }
    if (terminal.isAvailable()) {
      return {
        adapter: terminal,
        fallbackReason:
          "browser speech recognition unavailable — using terminal capture",
      };
    }
    return {
      error:
        "[VOICE_UNSUPPORTED] no speech input engine is available in this browser",
      code: "VOICE_UNSUPPORTED",
    };
  }

  #current(gen: number): boolean {
    return gen === this.#generation;
  }

  #commit(text: string): void {
    const clean = (text ?? "").trim();
    this.#finalAt = this.#now();
    if (!clean) {
      this.#patch({ interimText: "" });
      return;
    }
    // Only suppress a *consecutive identical* final (e.g. a doubled onend
    // event); a legitimately repeated word in a later segment is kept.
    if (clean === this.#lastFinal) {
      this.#patch({ interimText: "", metrics: this.#metrics() });
      return;
    }
    this.#lastFinal = clean;
    this.#patch({
      committedText: joinTranscript(this.#snapshot.committedText, clean),
      interimText: "",
      metrics: this.#metrics(),
    });
  }

  #metrics(): TranscriptionMetrics {
    const m = this.#snapshot.metrics;
    const firstPartial =
      this.#firstPartialAt !== null
        ? this.#firstPartialAt - this.#startAt
        : m.timeToFirstPartialMs;
    const finalization =
      this.#stopAt > 0 && this.#finalAt !== null
        ? this.#finalAt - this.#stopAt
        : null;
    const endToEnd =
      this.#finalAt !== null ? this.#finalAt - this.#startAt : null;
    return {
      engine: this.#snapshot.engineLabel || m.engine,
      timeToFirstPartialMs: firstPartial,
      finalizationMs: finalization,
      endToEndMs: endToEnd,
      audioMs: m.audioMs,
    };
  }

  #patch(patch: Partial<TranscriptionSnapshot>): void {
    this.#snapshot = { ...this.#snapshot, ...patch };
    for (const listener of this.#listeners) listener(this.#snapshot);
  }
}
