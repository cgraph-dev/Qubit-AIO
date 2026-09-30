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
  asr_device?: string;
  stt_engine?: string;
  stt_mode?: SttMode;
  stt_model?: SttModel;
  router?: VoiceRouter;
  hotkey_mode?: string;
  request_ttl_s?: number;
  brain_router?: boolean;
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
  wake?: {
    enabled: boolean;
    phrase: string;
    silence_ms: number;
    rms_gate: number;
  };
  prompts: PromptRow[];
  per_app: Record<string, string>;
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

export const voiceEdit = {
  status: () => fetchJSON<VoiceEditStatus>("/api/voice-edit/status"),
  log: (limit = 200) =>
    fetchJSON<VoiceLogResponse>(`/api/voice-edit/log?limit=${limit}`),
  settings: () =>
    fetchJSON<{ settings: VoiceEditSettings }>("/api/voice-edit/settings"),
  /** Locally installed models on THIS machine (Ollama) for the model pickers. */
  models: () =>
    fetchJSON<{ models: LocalModel[] }>("/api/voice-edit/models"),
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
