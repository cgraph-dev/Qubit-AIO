/**
 * Live-transcription contract shared by the `/qubit-chat` composer and the
 * voice-engine adapters. One controller drives all engines; an adapter only
 * translates one capture/recognition source into `partial`/`final` events.
 */

/** The persisted engine selection (`voice.input_engine`). */
export type VoiceEngine = "auto" | "browser" | "local" | "terminal";

/** The concrete engine actually running (null = none). */
export type ActiveVoiceEngine = "browser" | "local" | "terminal";

export type TranscriptionState =
  | "idle"
  | "starting"
  | "listening"
  | "stopping"
  | "error";

export interface TranscriptionMetrics {
  /** Engine label actually decoding, e.g. `sherpa-moonshine` / `browser`. */
  engine: string;
  /** Mic-open/start -> first partial hypothesis. */
  timeToFirstPartialMs: number | null;
  /** stop() -> final result. */
  finalizationMs: number | null;
  /** start() -> final result. */
  endToEndMs: number | null;
  /** Audio duration fed to the recognizer this utterance. */
  audioMs: number;
}

export interface TranscriptionSnapshot {
  requestedEngine: VoiceEngine;
  engine: ActiveVoiceEngine | null;
  engineLabel: string;
  state: TranscriptionState;
  /** Stable recognized text, appended on each final. Editable by the user. */
  committedText: string;
  /** Temporary hypothesis for the current segment; never persisted. */
  interimText: string;
  error: string | null;
  errorCode: string | null;
  /** Why a different engine than requested ran (never a silent switch). */
  fallbackReason: string | null;
  connected: boolean;
  metrics: TranscriptionMetrics;
}

export interface AdapterEvents {
  /** Full hypothesis for the current segment (REPLACE, never append). */
  onPartial(text: string): void;
  /** Final text for the current segment (append + commit). */
  onFinal(text: string): void;
  /** The concrete decoding engine label (e.g. `local-window`). */
  onEngine?(label: string): void;
  /** Typed error; must not clear already-committed text. */
  onError(code: string, detail: string): void;
  /** Transport/utterance ended (final already delivered when applicable). */
  onEnd?(): void;
  onConnected?(): void;
  /** Server-side timing metrics (local/terminal engines only). */
  onMetrics?(metrics: {
    audioMs?: number;
    timeToFirstPartialMs?: number | null;
    finalizationMs?: number | null;
  }): void;
}

export interface TranscriptionAdapter {
  readonly kind: ActiveVoiceEngine;
  readonly label: string;
  isAvailable(): boolean;
  /** Human-readable reason `isAvailable()` is false, else null. */
  availabilityReason(): string | null;
  start(events: AdapterEvents): Promise<void>;
  /** Finalize: flush the last segment before returning. */
  stop(): Promise<void>;
  /** Discard the current segment only (committed text is the controller's). */
  cancel(): Promise<void>;
  dispose(): void;
}

/** Append `next` to `prev` preserving single spaces and existing punctuation. */
export function joinTranscript(prev: string, next: string): string {
  const a = prev ?? "";
  const b = (next ?? "").trim();
  if (!b) return a;
  if (!a) return b;
  if (/\s$/.test(a)) return a + b;
  return `${a} ${b}`;
}
