/** Typed client for the Qubit voice typing / selected-text editing API.
 *
 *  Additive surface: every call goes through the existing authenticated
 *  `fetchJSON` (session token / cookie), and the backend reuses the one model
 *  gateway, e-stop, audit, and STT authority.
 */
import { fetchJSON } from "@/lib/api";

export type VoiceEditAction =
  | "rewrite"
  | "clarify"
  | "humanize"
  | "professionalize"
  | "make_funny"
  | "shorten"
  | "expand"
  | "summarize"
  | "correct_grammar"
  | "translate"
  | "bullet_points"
  | "email"
  | "technical"
  | "casual"
  | "improve_prompt"
  | "custom_transform";

export interface PromptRow {
  action: VoiceEditAction;
  title: string;
  version: string;
  text: string;
  customized: boolean;
  effective_text: string;
}

/** How voice-typing slows the utterance: whole-utterance (best quality) or live. */
export type SttMode = "streaming" | "accurate";
/** Local faster-whisper model used by accurate mode. */
export type SttModel = "tiny" | "base" | "small" | "medium" | "large-v3";
/** How an uncertain spoken command is resolved. */
export type VoiceRouter = "deterministic" | "brain";
/** How the local microphone capture is pre-processed before STT. */
export type CaptureSource = "auto" | "raw" | "aec";
/** Which local model answers voice questions, and how. */
export type VoiceRouting = "local" | "api" | "auto";
/** Text-to-speech provider for spoken answers. */
export type TtsProvider = "piper" | "kokoro" | "edge" | "hybrid" | "openai";

/** One installed local model reported by `GET /api/voice-edit/models`. */
export interface LocalModel {
  name: string;
  size_bytes: number;
}

/** One user-defined voice alias (`voice_edit.custom_commands`). */
export interface CustomCommand {
  phrase: string;
  action: string;
  target: string;
}

/** Actions an alias may target (mirrors `custom_commands.CUSTOM_ACTIONS`). */
export const CUSTOM_COMMAND_ACTIONS: string[] = [
  "open_website",
  "play_youtube",
  "open_app",
  "open_document",
];

export interface VoiceCommandExample {
  text: string;
  verified: boolean;
}

export interface VoiceCommandItem {
  name: string;
  desc: string;
  examples: VoiceCommandExample[];
  confirm: boolean;
}

export interface VoiceCommandGroup {
  id: string;
  title: string;
  intro: string;
  items: VoiceCommandItem[];
}

export interface VoiceCommandsCatalog {
  groups: VoiceCommandGroup[];
  custom_commands: CustomCommand[];
  custom_actions: string[];
}

export interface VoiceEditSettings {
  enabled: boolean;
  routing: "local" | "api" | "auto";
  local_profile: string;
  api_profile: string;
  allow_api: boolean;
  confirm_before_replace: boolean;
  auto_send: boolean;
  insertion_method: string;
  default_prompt: string;
  /** Persisted default action (wire key is `default_prompt`; accepted either way). */
  default_action?: string;
  asr_device?: string;
  stt_engine?: string;
  /** Helper-connection endpoints (PUT-only: accepted by update_voice_edit and
   *  persisted under voice_edit.*, but not echoed by the GET settings view,
   *  so the page treats them as blank-until-edited drafts). */
  service_url?: string;
  stt_ws_url?: string;
  stt_mode?: SttMode;
  stt_model?: SttModel;
  router?: VoiceRouter;
  hotkey_mode?: string;
  request_ttl_s?: number;
  brain_router?: boolean;
  /** Brain-router classify timeout, seconds (backend default 4.0). */
  brain_timeout_s?: number;
  block_fullscreen?: boolean;
  beep?: boolean;
  debug_log?: boolean;
  mic_gain?: number;
  mic_target_peak?: number;
  noise_suppression?: boolean;
  answer_questions?: boolean;
  text_commands?: boolean;
  /** How the captured microphone signal is pre-processed before STT. */
  capture_source?: CaptureSource;
  /** Automatic gain control on the captured mic signal. */
  agc?: boolean;
  release_tail_ms?: number;
  /** Ignore OS key-repeat while Right-Ctrl is held. */
  disable_key_repeat?: boolean;
  // ── Brain / model (per-machine local model + API routing) ──────────
  /** The machine's configured local model (`intent.local_llm.model`); "" = auto. */
  local_model?: string;
  /** Model that answers voice questions; "" = use the configured model. */
  chat_model?: string;
  /** Model used for "more detail" answers; "" = use the configured model. */
  detail_model?: string;
  /** Phrases that trigger a "give me more detail" answer. */
  detail_cues?: string[];
  /** How many recent turns of context the voice brain sees (2..40). */
  voice_recent_turns?: number;
  /** Sampling temperature for the local voice brain (0..2). */
  local_temperature?: number;
  /** Let the local model "think" before answering. */
  local_think?: boolean;
  // ── Voice responses (TTS) ──────────────────────────────────────────
  /** Speak answers aloud instead of only showing them. */
  speak_answers?: boolean;
  tts_provider?: TtsProvider;
  tts_voice?: string;
  /** Kokoro speaker id (0..10); only used when tts_provider is "kokoro". */
  tts_kokoro_voice_sid?: number;
  /** Show the on-screen voice history panel. */
  history_panel?: boolean;
  /** Show the floating dashboard waveform widget (backend default false —
   *  hidden unless explicitly enabled; the OS orb is the always-on waveform). */
  waveform_visible?: boolean;
  /** Keep the OS orb mapped over fullscreen games (backend default false =
   *  today's auto-hide: withdraw on fullscreen focus, return on alt-tab). */
  waveform_ingame?: boolean;
  wake?: {
    enabled: boolean;
    phrase: string;
    silence_ms: number;
    rms_gate: number;
    /** Wake-word engine: auto|kws|asr (backend default "auto"). */
    engine?: string;
    /** Keyword-spotting accept threshold 0.05..0.9 (backend default 0.25). */
    kws_threshold?: number;
    /** True when a local KWS model bundle is present (read-only). */
    kws_available?: boolean;
    /** Fall back to ASR verification when KWS is unsure (backend default true). */
    asr_fallback?: boolean;
    /** ASR fallback listen window, seconds (backend default 8). */
    asr_fallback_max_s?: number;
    /** Longest wake segment, seconds, clamped 2..25 (backend default 8). */
    max_s?: number;
    /** Segmentation backend: auto|silero|webrtc|energy (backend default "auto"). */
    vad?: string;
  };
  /** Chrome-style transcripts: lowercase, no punctuation (backend default false). */
  verbatim_transcript?: boolean;
  prompts: PromptRow[];
  per_app: Record<string, string>;
  /** User-defined aliases; absent on older backends (treat as []). */
  custom_commands?: CustomCommand[];
  prompt_versions: Record<string, string>;
}

export interface VoiceEditStatus {
  ok?: boolean;
  enabled: boolean;
  /** True when the system-wide desktop helper is running and owns Right-Ctrl. */
  helper_live?: boolean;
  estop: boolean;
  session: { session_type?: string; display?: string; x11?: boolean };
  tools: Record<string, boolean>;
  routing: {
    mode: string;
    allow_api: boolean;
    local_profile: string;
    api_profile: string;
  };
  confirm_before_replace: boolean;
  auto_send: boolean;
  request_ttl_s: number;
  privacy: string;
}

export interface TransformPreview {
  ok: boolean;
  request_id: string;
  original: string;
  proposed: string;
  detected: { action: string; style: string; language: string; audience: string };
  model: string;
  provider: string;
  locality: string;
  privacy_mode: string;
  injection_suspected: boolean;
  clarification_returned?: boolean;
  facts_dropped: string[];
  elapsed_ms: number;
  target_app: string;
  mode?: string;
  code?: string;
  detail?: string;
}

export interface VoiceLogEntry {
  ts?: number;
  event?: string;
  [key: string]: unknown;
}

export interface VoiceLogResponse {
  ok: boolean;
  events: VoiceLogEntry[];
  summary: {
    count: number;
    errors: number;
    last_error: VoiceLogEntry | null;
    last_event: VoiceLogEntry | null;
  };
}

/** Every transform action the settings API accepts for per-app pins and the
 *  default prompt (mirrors `voice_edit/schema.py::ACTIONS`). */
export const VOICE_EDIT_ACTIONS: string[] = [
  "rewrite",
  "clarify",
  "humanize",
  "professionalize",
  "make_funny",
  "shorten",
  "expand",
  "summarize",
  "correct_grammar",
  "translate",
  "bullet_points",
  "email",
  "technical",
  "casual",
  "improve_prompt",
  "custom_transform",
  "dictate_text",
];

/** Backend clamps from `voice_edit/settings.py::update_voice_edit`. */
export const WAKE_LIMITS = {
  silenceMs: { min: 300, max: 5000 },
  rmsGate: { min: 0.0005, max: 0.2 },
  kwsThreshold: { min: 0.05, max: 0.9 },
  asrFallbackMaxS: { min: 2, max: 25 },
  maxS: { min: 2, max: 25 },
} as const;

export function clampVoiceEditNumber(v: number, lo: number, hi: number): number {
  if (!Number.isFinite(v)) return lo;
  return Math.min(hi, Math.max(lo, v));
}

export const voiceEdit = {
  status: () => fetchJSON<VoiceEditStatus>("/api/voice-edit/status"),
  log: (limit = 200) =>
    fetchJSON<VoiceLogResponse>(`/api/voice-edit/log?limit=${limit}`),
  settings: () =>
    fetchJSON<{ settings: VoiceEditSettings }>("/api/voice-edit/settings"),
  /** Locally installed models on THIS machine (Ollama) for the model pickers. */
  models: () =>
    fetchJSON<{ models: LocalModel[] }>("/api/voice-edit/models"),
  /** Browsable command catalog, derived live from the backend registries. */
  commands: () =>
    fetchJSON<{ ok: boolean } & VoiceCommandsCatalog>("/api/voice-edit/commands"),
  saveSettings: (update: Record<string, unknown>) =>
    fetchJSON<{ settings: VoiceEditSettings }>("/api/voice-edit/settings", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(update),
    }),
  restore: (action?: string) =>
    fetchJSON<{ settings: VoiceEditSettings }>("/api/voice-edit/settings/restore", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(action ? { action } : {}),
    }),
  exportSettings: () =>
    fetchJSON<{ export: Record<string, unknown> }>("/api/voice-edit/export"),
  importSettings: (payload: Record<string, unknown>) =>
    fetchJSON<{ settings: VoiceEditSettings }>("/api/voice-edit/import", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
  begin: (sessionId = "") =>
    fetchJSON<{ request_id: string }>("/api/voice-edit/session", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId }),
    }),
  capture: (rid: string) =>
    fetchJSON<{ selection: Record<string, unknown>; target_app: string }>(
      `/api/voice-edit/${encodeURIComponent(rid)}/capture`,
      { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" },
    ),
  transform: (rid: string, transcript: string, action = "", style = "") =>
    fetchJSON<TransformPreview>(
      `/api/voice-edit/${encodeURIComponent(rid)}/transform`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ transcript, action, style }),
      },
    ),
  edit: (rid: string, text: string) =>
    fetchJSON<{ proposed: string }>(`/api/voice-edit/${encodeURIComponent(rid)}/edit`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    }),
  retry: (rid: string, transcript = "") =>
    fetchJSON<TransformPreview>(`/api/voice-edit/${encodeURIComponent(rid)}/retry`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ transcript }),
    }),
  apply: (rid: string, confirm = true) =>
    fetchJSON<Record<string, unknown>>(
      `/api/voice-edit/${encodeURIComponent(rid)}/apply`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ confirm }),
      },
    ),
  revert: (rid: string) =>
    fetchJSON<Record<string, unknown>>(
      `/api/voice-edit/${encodeURIComponent(rid)}/revert`,
      { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" },
    ),
  cancel: (rid: string) =>
    fetchJSON<Record<string, unknown>>(
      `/api/voice-edit/${encodeURIComponent(rid)}/cancel`,
      { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" },
    ),
};
