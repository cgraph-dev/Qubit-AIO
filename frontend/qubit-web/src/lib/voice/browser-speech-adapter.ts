/**
 * BrowserSpeechAdapter — wraps the browser's native SpeechRecognition
 * (`SpeechRecognition` / `webkitSpeechRecognition`).
 *
 * Chrome's own speech engine is fast and accurate, so this is left as the
 * canonical `browser` engine. It sets `interimResults=true` and
 * `continuous=true` (where supported) and emits non-final hypotheses
 * immediately. No local STT is ever substituted silently.
 */
import type { AdapterEvents, TranscriptionAdapter } from "./types";

interface SpeechRecognitionLike {
  lang: string;
  interimResults: boolean;
  continuous?: boolean;
  maxAlternatives: number;
  onresult: ((event: SpeechRecognitionEventLike) => void) | null;
  onerror: ((event: { error?: string }) => void) | null;
  onend: (() => void) | null;
  start: () => void;
  stop: () => void;
  abort: () => void;
}

interface SpeechRecognitionEventLike {
  resultIndex: number;
  results: ArrayLike<{
    isFinal: boolean;
    0: { transcript: string };
  }>;
}

/** The browser constructor, or null when the browser offers none. */
export function asSpeechRecognition():
  | (new () => SpeechRecognitionLike)
  | null {
  if (typeof window === "undefined") return null;
  const w = window as unknown as {
    SpeechRecognition?: new () => SpeechRecognitionLike;
    webkitSpeechRecognition?: new () => SpeechRecognitionLike;
  };
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null;
}

/** The user-visible privacy notice for the browser engine (vendor service). */
export const BROWSER_PRIVACY_NOTICE =
  "browser recognition may send audio to your browser vendor's recognition service";

export class BrowserSttUnavailable extends Error {
  readonly code = "VOICE_BROWSER_UNAVAILABLE";
  constructor() {
    super("browser speech recognition is unavailable in this browser");
  }
}

export class BrowserSpeechAdapter implements TranscriptionAdapter {
  readonly kind = "browser" as const;
  readonly label = "browser";

  #rec: SpeechRecognitionLike | null = null;
  #lang: string | undefined;

  constructor(opts: { lang?: string } = {}) {
    this.#lang = opts.lang;
  }

  isAvailable(): boolean {
    return asSpeechRecognition() !== null;
  }

  availabilityReason(): string | null {
    return this.isAvailable()
      ? null
      : "browser speech recognition unavailable (use local Qubit STT)";
  }

  async start(events: AdapterEvents): Promise<void> {
    const SR = asSpeechRecognition();
    if (!SR) throw new BrowserSttUnavailable();
    const rec = new SR();
    rec.lang = this.#lang ?? "en-US";
    rec.interimResults = true;
    // `continuous` is not honoured by every engine but is harmless where it is
    // unsupported; the adapter emits every result either way.
    rec.continuous = true;
    rec.maxAlternatives = 1;
    let finalText = "";
    rec.onresult = (event) => {
      let interim = "";
      for (let i = event.resultIndex; i < event.results.length; i++) {
        const result = event.results[i];
        if (result.isFinal) finalText += result[0].transcript;
        else interim += result[0].transcript;
      }
      const heard = interim.trim();
      if (heard) events.onPartial(heard);
      const done = finalText.trim();
      if (done) {
        finalText = "";
        events.onPartial("");
        events.onFinal(done);
      }
    };
    rec.onerror = (event) => {
      events.onError(mapBrowserError(event?.error), describeError(event?.error));
    };
    rec.onend = () => {
      this.#rec = null;
      if (finalText.trim()) {
        events.onFinal(finalText.trim());
        finalText = "";
      }
      events.onEnd?.();
    };
    this.#rec = rec;
    events.onEngine?.("browser");
    try {
      rec.start();
    } catch (err) {
      this.#rec = null;
      throw err instanceof Error ? err : new Error(String(err));
    }
  }

  async stop(): Promise<void> {
    try {
      this.#rec?.stop();
    } catch {
      /* already stopped */
    }
  }

  async cancel(): Promise<void> {
    const rec = this.#rec;
    this.#rec = null;
    try {
      rec?.abort();
    } catch {
      /* already stopped */
    }
  }

  dispose(): void {
    this.#rec = null;
  }
}

function mapBrowserError(code?: string): string {
  switch (code) {
    case "not-allowed":
    case "service-not-allowed":
      return "VOICE_DENIED";
    case "no-speech":
      return "VOICE_NO_SPEECH";
    case "aborted":
      return "VOICE_CANCELLED";
    case "audio-capture":
      return "VOICE_NO_MIC";
    case "network":
      return "VOICE_NETWORK";
    default:
      return "VOICE_ERROR";
  }
}

function describeError(code?: string): string {
  switch (code) {
    case "not-allowed":
    case "service-not-allowed":
      return "Microphone blocked. Allow mic access, or type below.";
    case "no-speech":
      return "No speech heard — try again, or type.";
    case "aborted":
      return "Recognition cancelled.";
    case "audio-capture":
      return "No microphone was found.";
    case "network":
      return "Browser recognition lost its network connection.";
    default:
      return `Speech recognition failed (${code ?? "unknown"}).`;
  }
}
