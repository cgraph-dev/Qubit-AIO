/**
 * Voice waveform state mapping (floating widget).
 *
 * STATE-SOURCE MAPPING (read-only reuse — this module never opens sockets,
 * never touches the mic, never writes settings):
 *
 * - `listening` ← the existing transcription authority
 *   (`src/lib/voice/transcription-controller.ts` → `TranscriptionSnapshot.state`
 *   ∈ {starting, listening, stopping}; ChatPage derives the same `voiceActive`
 *   / `voiceState` from it and exposes it as
 *   `[data-testid="voice-status"][data-state]`). The global host observes that
 *   attribute via MutationObserver — no ChatPage logic change.
 * - `thinking` ← turn in flight (ChatPage `busy`: the "Qubit is thinking…" /
 *   streaming row; the transcript container carries `aria-busy`). The host
 *   observes `.qchat__transcript[aria-busy]` — read-only.
 * - `speaking` ← audio playback active: `window.speechSynthesis.speaking`
 *   OR any `<audio>` element currently playing (the Talk/VoiceEdit TTS paths
 *   play through these; there is no in-ChatPage TTS state to reuse).
 * - `idle` ← everything else. Unknown/future transcription states, `error`,
 *   and `unsupported` all fall back to `idle` — the widget never gets stuck
 *   animating a state it does not understand.
 *
 * Priority: listening > speaking > thinking > idle (speaking outranks
 * thinking since 2026-10-04 — a busy transcript used to mask a playing
 * reply, same rule as the desktop orb). While the mic is open the user is
 * talking — that wins over a still-running turn or leftover audio.
 *
 * No new sockets (WS_SINGLE_PATH stands): the host is poll/event-driven like
 * the existing status consumers (`voiceEdit.status()` shape is untouched).
 */

/** The four widget animations. `idle` is the safe fallback for unknowns. */
export type WaveformState = "idle" | "listening" | "thinking" | "speaking";

export interface WaveformInputs {
  /** Raw `TranscriptionSnapshot.state` (or the voice-status data-state). */
  transcriptionState?: string | null;
  /** Turn in flight (ChatPage `busy` / transcript `aria-busy="true"`). */
  turnBusy?: boolean;
  /** TTS/audio playback currently audible. */
  speaking?: boolean;
}

/** Transcription states that mean the mic is open (mirrors ChatPage `voiceActive`). */
const LISTENING_STATES = new Set(["starting", "listening", "stopping"]);

export function resolveWaveformState(inputs: WaveformInputs): WaveformState {
  const raw = (inputs.transcriptionState ?? "idle").toLowerCase();
  if (LISTENING_STATES.has(raw)) return "listening";
  if (inputs.speaking === true) return "speaking";
  if (inputs.turnBusy === true) return "thinking";
  return "idle";
}

/**
 * Derive the waveform-relevant bit from the ChatPage voice-status
 * `data-state` attribute (`idle | listening | unsupported | error`).
 * Anything but an exact `listening` is `idle` — never stuck.
 */
export function waveformFromVoiceStatusAttr(
  attr: string | null | undefined,
): Extract<WaveformState, "idle" | "listening"> {
  return attr === "listening" ? "listening" : "idle";
}

/** `aria-busy="true"` on the transcript container means a turn is in flight. */
export function turnBusyFromAriaBusy(attr: string | null | undefined): boolean {
  return attr === "true";
}

// ── Position persistence (localStorage only; no settings mirror) ────────────

export const VOICE_WAVEFORM_STORAGE_KEY = "qubit.voiceWaveform.v1";

/** Movement budget before a press becomes a drag (click still toggles). */
export const VOICE_WAVEFORM_CLICK_DRAG_THRESHOLD_PX = 6;

export interface WaveformPosition {
  x: number;
  y: number;
  collapsed?: boolean;
}

export interface Viewport {
  width: number;
  height: number;
}

const WIDGET_W = 148;
const WIDGET_H = 148;
const MARGIN = 16;

/** Bottom-right default, kept inside the viewport with a margin. */
export function defaultWaveformPosition(viewport: Viewport): WaveformPosition {
  const width = Math.max(0, viewport.width || 0);
  const height = Math.max(0, viewport.height || 0);
  return {
    x: Math.max(MARGIN, width - WIDGET_W - MARGIN),
    y: Math.max(MARGIN, height - WIDGET_H - MARGIN),
    collapsed: false,
  };
}

function isValidPosition(value: unknown): value is WaveformPosition {
  if (typeof value !== "object" || value === null) return false;
  const pos = value as Record<string, unknown>;
  return typeof pos.x === "number" && Number.isFinite(pos.x) &&
    typeof pos.y === "number" && Number.isFinite(pos.y);
}

/** Read the persisted position (null when absent/corrupt — never throws). */
export function loadWaveformPosition(
  storage: Pick<Storage, "getItem">,
): WaveformPosition | null {
  try {
    const raw = storage.getItem(VOICE_WAVEFORM_STORAGE_KEY);
    if (!raw) return null;
    const parsed: unknown = JSON.parse(raw);
    if (!isValidPosition(parsed)) return null;
    return {
      x: parsed.x,
      y: parsed.y,
      collapsed: parsed.collapsed === true,
    };
  } catch {
    return null;
  }
}

/** Persist the position (fail-closed: storage errors are swallowed). */
export function saveWaveformPosition(
  storage: Pick<Storage, "setItem">,
  position: WaveformPosition,
): void {
  try {
    storage.setItem(
      VOICE_WAVEFORM_STORAGE_KEY,
      JSON.stringify({ x: position.x, y: position.y, collapsed: position.collapsed === true }),
    );
  } catch {
    /* private-mode / quota — the widget still works, just not persisted */
  }
}

/** True once pointer travel exceeds the click-vs-drag budget. */
export function isDragBeyondThreshold(
  dx: number,
  dy: number,
  thresholdPx: number = VOICE_WAVEFORM_CLICK_DRAG_THRESHOLD_PX,
): boolean {
  return Math.hypot(dx, dy) > thresholdPx;
}

/** Keep a position inside the viewport (so the dot can never be lost). */
export function clampWaveformPosition(
  position: WaveformPosition,
  viewport: Viewport,
): WaveformPosition {
  const width = Math.max(1, viewport.width || 1);
  const height = Math.max(1, viewport.height || 1);
  return {
    x: Math.min(Math.max(0, position.x), Math.max(0, width - 32)),
    y: Math.min(Math.max(0, position.y), Math.max(0, height - 32)),
    collapsed: position.collapsed,
  };
}
