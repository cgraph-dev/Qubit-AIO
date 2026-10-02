// ROUTES MANIFEST — Qubit talk + scheduler-media families (integrator implements verbatim).
// Every HTTP path used by this module, with its method. All paths live under
// /api/qubit/<family>/<action>. No other paths are fetched from here.
//
//   Talk (local speech path; the realtime/duplex lane is removed):
//     GET  /api/qubit/talk/mode               (talk.mode, read-only)
//     GET  /api/qubit/talk/catalog             (talk.catalog, read-only)
//     GET  /api/qubit/talk/config              (talk.config, read-only)
//     POST /api/qubit/talk/client-steer        (talk.client.steer, confirm-gated)
//     POST /api/qubit/talk/client-transcript   (talk.client.transcript, confirm-gated)
//     POST /api/qubit/talk/client-tool-call    (talk.client.toolCall, confirm-gated)
//     POST /api/qubit/talk/speak               (talk.speak, confirm-gated)
//     GET  /api/qubit/talk/voicewake           (voicewake.get, live read-only)
//   Scheduler-media (schedules + speech + power + task control):
//     POST /api/qubit/scheduler/cron-update        (cron.update, enabled-only, confirm-gated)
//     POST /api/qubit/scheduler/tts-set-provider   (tts.setProvider, confirm-gated)
//     POST /api/qubit/scheduler/tts-enable         (tts.enable, confirm-gated)
//     POST /api/qubit/scheduler/tts-disable        (tts.disable, confirm-gated)
//     POST /api/qubit/scheduler/tts-set-persona    (tts.setPersona, confirm-gated)
//     GET  /api/qubit/scheduler/suspend-prepare    (gateway.suspend.prepare, power-state read)
//     GET  /api/qubit/scheduler/suspend-resume     (gateway.suspend.resume, power-state read)
//     POST /api/qubit/scheduler/tasks-cancel       (tasks.cancel, confirm-gated)
//     POST /api/qubit/scheduler/tasks-retry        (tasks.retry, confirm-gated)
//
// Wake-state rule: this module is a READ-ONLY consumer of wake state
// (voicewake.get). It performs zero writes to wake state and never touches
// voice-edit paths. Talk/scheduler pages must not gain wake writers.

import { fetchJSON, runMutationWithConfirm } from "@/lib/api";

/** Prompt shown when the backend stages a mutation (defaults to window.confirm). */
export type ConfirmPrompt = (
  detail: string,
) => boolean | Promise<boolean>;

/** One canonical route in the talk / scheduler-media namespace. */
export interface FamilyRoute {
  family: "talk" | "scheduler";
  action: string;
  method: "GET" | "POST";
  path: string;
}

/**
 * Manifest of every path+method this module calls, under
 * `/api/qubit/<family>/<action>`. The realtime session/client-create rows
 * are intentionally absent: the backend refuses them by policy and they are
 * listed in DISABLED_TALK_ACTIONS instead (never called).
 */
export const ROUTES: FamilyRoute[] = [
  // ── talk (reads) ──
  { family: "talk", action: "talk.mode", method: "GET", path: "/api/qubit/talk/mode" },
  { family: "talk", action: "talk.catalog", method: "GET", path: "/api/qubit/talk/catalog" },
  { family: "talk", action: "talk.config", method: "GET", path: "/api/qubit/talk/config" },
  { family: "talk", action: "voicewake.get", method: "GET", path: "/api/qubit/talk/voicewake" },
  // ── talk (confirm-gated writes) ──
  { family: "talk", action: "talk.client.steer", method: "POST", path: "/api/qubit/talk/client-steer" },
  { family: "talk", action: "talk.client.transcript", method: "POST", path: "/api/qubit/talk/client-transcript" },
  { family: "talk", action: "talk.client.toolCall", method: "POST", path: "/api/qubit/talk/client-tool-call" },
  { family: "talk", action: "talk.speak", method: "POST", path: "/api/qubit/talk/speak" },
  // ── scheduler (reads) ──
  { family: "scheduler", action: "gateway.suspend.prepare", method: "GET", path: "/api/qubit/scheduler/suspend-prepare" },
  { family: "scheduler", action: "gateway.suspend.resume", method: "GET", path: "/api/qubit/scheduler/suspend-resume" },
  // ── scheduler (confirm-gated writes) ──
  { family: "scheduler", action: "cron.update", method: "POST", path: "/api/qubit/scheduler/cron-update" },
  { family: "scheduler", action: "tts.setProvider", method: "POST", path: "/api/qubit/scheduler/tts-set-provider" },
  { family: "scheduler", action: "tts.enable", method: "POST", path: "/api/qubit/scheduler/tts-enable" },
  { family: "scheduler", action: "tts.disable", method: "POST", path: "/api/qubit/scheduler/tts-disable" },
  { family: "scheduler", action: "tts.setPersona", method: "POST", path: "/api/qubit/scheduler/tts-set-persona" },
  { family: "scheduler", action: "tasks.cancel", method: "POST", path: "/api/qubit/scheduler/tasks-cancel" },
  { family: "scheduler", action: "tasks.retry", method: "POST", path: "/api/qubit/scheduler/tasks-retry" },
];

/** A policy-refused operation: rendered disabled with its exact prerequisite. */
export interface DisabledAction {
  action: string;
  code: string;
  reason: string;
  counterpart: string;
}

/* ------------------------------------------------------------------ */
/*  talk                                                                  */
/* ------------------------------------------------------------------ */

export interface TalkModeResponse {
  state?: string;
  code?: string;
  requestedMode?: string | null;
  effectiveMode?: string;
  realtime?: Record<string, unknown>;
  profiles?: unknown;
  detail?: string;
  [key: string]: unknown;
}

export interface TalkCatalogResponse {
  state?: string;
  profiles?: Array<{ id?: string; kind?: string; [key: string]: unknown }>;
  [key: string]: unknown;
}

export interface TalkConfigResponse {
  state?: string;
  profiles?: unknown;
  speech_in?: Record<string, unknown>;
  speech_out?: Record<string, unknown>;
  audio?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface TalkMutationResponse {
  state?: string;
  code?: string;
  sessionKey?: string;
  detail?: string;
  [key: string]: unknown;
}

export interface TalkSpeakResponse {
  state?: string;
  mime_type?: string;
  data_url?: string;
  provider?: string;
  [key: string]: unknown;
}

export interface VoicewakeStatus {
  state?: string;
  [key: string]: unknown;
}

/** talk.mode: report the real local speech profile resolution (read-only). */
export function getTalkMode(requestedMode?: string): Promise<TalkModeResponse> {
  const q = requestedMode ? `?mode=${encodeURIComponent(requestedMode)}` : "";
  return fetchJSON<TalkModeResponse>(`/api/qubit/talk/mode${q}`);
}

/** talk.catalog: the STT/TTS/realtime speech-profile registry (read-only). */
export function getTalkCatalog(): Promise<TalkCatalogResponse> {
  return fetchJSON<TalkCatalogResponse>("/api/qubit/talk/catalog");
}

/** talk.config: speech profiles + resolved speech-in/out + audio status (read-only). */
export function getTalkConfig(): Promise<TalkConfigResponse> {
  return fetchJSON<TalkConfigResponse>("/api/qubit/talk/config");
}

/** voicewake.get: the single shared audio-listener wake status (live read-only). */
export function getVoicewake(): Promise<VoicewakeStatus> {
  return fetchJSON<VoicewakeStatus>("/api/qubit/talk/voicewake");
}

/** talk.client.steer: record a spoken control on the session for the next turn (confirm-gated). */
export function steerTalkClient(
  input: { sessionKey: string; text: string; mode?: string },
  confirm?: ConfirmPrompt,
): Promise<TalkMutationResponse> {
  return runMutationWithConfirm(
    () =>
      fetchJSON<TalkMutationResponse>("/api/qubit/talk/client-steer", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ confirm: true, ...input }),
      }),
    confirm,
  );
}

/** talk.client.transcript: append a finalized transcript item to the session (confirm-gated). */
export function appendTalkTranscript(
  input: { sessionKey: string; text: string; role?: string; entryId?: string },
  confirm?: ConfirmPrompt,
): Promise<TalkMutationResponse> {
  return runMutationWithConfirm(
    () =>
      fetchJSON<TalkMutationResponse>("/api/qubit/talk/client-transcript", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ confirm: true, ...input }),
      }),
    confirm,
  );
}

/** talk.client.toolCall: run one tool call through the canonical broker (confirm-gated). */
export function callTalkTool(
  input: { toolName: string; args?: Record<string, unknown>; sessionKey?: string },
  confirm?: ConfirmPrompt,
): Promise<TalkMutationResponse> {
  return runMutationWithConfirm(
    () =>
      fetchJSON<TalkMutationResponse>("/api/qubit/talk/client-tool-call", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ confirm: true, ...input }),
      }),
    confirm,
  );
}

/** talk.speak: render spoken audio via the local TTS chain (confirm-gated). */
export function speakTalk(
  text: string,
  confirm?: ConfirmPrompt,
): Promise<TalkSpeakResponse> {
  return runMutationWithConfirm(
    () =>
      fetchJSON<TalkSpeakResponse>("/api/qubit/talk/speak", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ confirm: true, text }),
      }),
    confirm,
  );
}

/**
 * Policy-refused talk operations. Code + reason are the exact `code` /
 * `operator_prerequisite` strings the backend emits — rendered
 * disabled-with-reason, never called.
 */
export const DISABLED_TALK_ACTIONS: DisabledAction[] = [
  {
    action: "talk.client.create",
    code: "TALK_REALTIME_OPERATOR_REQUIRED",
    reason: "an equivalent provider transport, which Qubit does not run",
    counterpart: "the /qubit-chat voice path (mic -> ASR -> route -> worker -> speak)",
  },
  {
    action: "talk.client.close",
    code: "TALK_REALTIME_OPERATOR_REQUIRED",
    reason: "an equivalent provider transport, which Qubit does not run",
    counterpart: "the /qubit-chat voice path (mic -> ASR -> route -> worker -> speak)",
  },
  {
    action: "talk.session.create",
    code: "TALK_REALTIME_OPERATOR_REQUIRED",
    reason: "an equivalent provider transport, which Qubit does not run",
    counterpart: "the /qubit-chat voice path (mic -> ASR -> route -> worker -> speak)",
  },
  {
    action: "talk.session.appendAudio",
    code: "TALK_REALTIME_OPERATOR_REQUIRED",
    reason: "an equivalent provider transport, which Qubit does not run",
    counterpart: "the /qubit-chat voice path (mic -> ASR -> route -> worker -> speak)",
  },
  {
    action: "talk.session.cancelOutput",
    code: "TALK_REALTIME_OPERATOR_REQUIRED",
    reason: "an equivalent provider transport, which Qubit does not run",
    counterpart: "the /qubit-chat voice path (mic -> ASR -> route -> worker -> speak)",
  },
  {
    action: "talk.session.close",
    code: "TALK_REALTIME_OPERATOR_REQUIRED",
    reason: "an equivalent provider transport, which Qubit does not run",
    counterpart: "the /qubit-chat voice path (mic -> ASR -> route -> worker -> speak)",
  },
  {
    action: "talk.session.acknowledgeMark",
    code: "TALK_REALTIME_OPERATOR_REQUIRED",
    reason: "an equivalent provider transport, which Qubit does not run",
    counterpart: "the /qubit-chat voice path (mic -> ASR -> route -> worker -> speak)",
  },
  {
    action: "talk.session.submitToolResult",
    code: "TALK_REALTIME_OPERATOR_REQUIRED",
    reason: "an equivalent provider transport, which Qubit does not run",
    counterpart: "the /qubit-chat voice path (mic -> ASR -> route -> worker -> speak)",
  },
  {
    action: "talk.session.steer",
    code: "TALK_REALTIME_OPERATOR_REQUIRED",
    reason: "an equivalent provider transport, which Qubit does not run",
    counterpart: "talk.client.steer on this page (recorded for the next turn)",
  },
  {
    action: "voicewake.set",
    code: "WAKE_TRIGGERS_OPERATOR_REQUIRED",
    reason: "a configurable wake-trigger store (writable keywords/model bundle)",
    counterpart: "voicewake.get on this page + the live-tuned wake chain (read-only here)",
  },
  {
    action: "voicewake.routing.get",
    code: "WAKE_ROUTING_OPERATOR_REQUIRED",
    reason: "a configurable wake-trigger/routing store",
    counterpart: "voicewake.get on this page + the live-tuned wake chain (read-only here)",
  },
];

/* ------------------------------------------------------------------ */
/*  scheduler-media                                                       */
/* ------------------------------------------------------------------ */

/** The TTS provider ids the canonical config/audio path recognizes. */
export const TTS_PROVIDERS = ["piper", "edge", "hybrid", "openai", "kokoro", "gateway"] as const;

export interface CronUpdateResponse {
  state?: string;
  code?: string;
  id?: number;
  applied?: unknown;
  detail?: string;
  [key: string]: unknown;
}

export interface TtsMutationResponse {
  state?: string;
  code?: string;
  provider?: string;
  enabled?: boolean;
  applied?: boolean;
  detail?: string;
  [key: string]: unknown;
}

export interface SuspendReadResponse {
  state?: string;
  code?: string;
  power?: Record<string, unknown>;
  detail?: string;
  [key: string]: unknown;
}

export interface TaskControlResponse {
  state?: string;
  code?: string;
  [key: string]: unknown;
}

/**
 * cron.update: pause/resume a schedule by id. The store supports ONLY the
 * `enabled` subset of an upstream patch — this call sends exactly that.
 * (confirm-gated).
 */
export function updateCronEnabled(
  id: number,
  enabled: boolean,
  confirm?: ConfirmPrompt,
): Promise<CronUpdateResponse> {
  return runMutationWithConfirm(
    () =>
      fetchJSON<CronUpdateResponse>("/api/qubit/scheduler/cron-update", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ confirm: true, id, patch: { enabled } }),
      }),
    confirm,
  );
}

/** tts.setProvider: persist the TTS provider through the canonical settings write (confirm-gated). */
export function setTtsProvider(
  provider: string,
  confirm?: ConfirmPrompt,
): Promise<TtsMutationResponse> {
  return runMutationWithConfirm(
    () =>
      fetchJSON<TtsMutationResponse>("/api/qubit/scheduler/tts-set-provider", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ confirm: true, provider }),
      }),
    confirm,
  );
}

/** tts.enable: flip the per-speech-out-profile `enabled` flag on (confirm-gated). */
export function enableTtsProfile(
  profileId: string,
  confirm?: ConfirmPrompt,
): Promise<TtsMutationResponse> {
  return runMutationWithConfirm(
    () =>
      fetchJSON<TtsMutationResponse>("/api/qubit/scheduler/tts-enable", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ confirm: true, profileId }),
      }),
    confirm,
  );
}

/** tts.disable: flip the per-speech-out-profile `enabled` flag off (confirm-gated). */
export function disableTtsProfile(
  profileId: string,
  confirm?: ConfirmPrompt,
): Promise<TtsMutationResponse> {
  return runMutationWithConfirm(
    () =>
      fetchJSON<TtsMutationResponse>("/api/qubit/scheduler/tts-disable", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ confirm: true, profileId }),
      }),
    confirm,
  );
}

/** tts.setPersona: select a speech-out profile for a session (confirm-gated). */
export function setTtsPersona(
  input: { persona: string; session?: string },
  confirm?: ConfirmPrompt,
): Promise<TtsMutationResponse> {
  return runMutationWithConfirm(
    () =>
      fetchJSON<TtsMutationResponse>("/api/qubit/scheduler/tts-set-persona", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ confirm: true, ...input }),
      }),
    confirm,
  );
}

/** gateway.suspend.prepare: report the real local power/e-stop state (read-only). */
export function getSuspendPrepare(): Promise<SuspendReadResponse> {
  return fetchJSON<SuspendReadResponse>("/api/qubit/scheduler/suspend-prepare");
}

/** gateway.suspend.resume: report the real local power/e-stop state (read-only). */
export function getSuspendResume(): Promise<SuspendReadResponse> {
  return fetchJSON<SuspendReadResponse>("/api/qubit/scheduler/suspend-resume");
}

/** tasks.cancel: cancel a real action receipt or an in-flight chat session (confirm-gated). */
export function cancelTask(
  input: { receiptId?: string; session?: string; taskId?: string },
  confirm?: ConfirmPrompt,
): Promise<TaskControlResponse> {
  return runMutationWithConfirm(
    () =>
      fetchJSON<TaskControlResponse>("/api/qubit/scheduler/tasks-cancel", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ confirm: true, ...input }),
      }),
    confirm,
  );
}

/** tasks.retry: retry a real action receipt or the last chat turn (confirm-gated). */
export function retryTask(
  input: { receiptId?: string; session?: string; taskId?: string },
  confirm?: ConfirmPrompt,
): Promise<TaskControlResponse> {
  return runMutationWithConfirm(
    () =>
      fetchJSON<TaskControlResponse>("/api/qubit/scheduler/tasks-retry", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ confirm: true, ...input }),
      }),
    confirm,
  );
}

/**
 * Policy-refused scheduler-media operations. Code + reason are the exact
 * `code` / `operator_prerequisite` strings the backend emits — rendered
 * disabled-with-reason, never called.
 */
export const DISABLED_SCHEDULER_ACTIONS: DisabledAction[] = [
  {
    action: "cron.scratch.get",
    code: "CRON_SCRATCH_OPERATOR_REQUIRED",
    reason: "an upstream per-job cron scratch store (cron_job_scratch); Qubit runs none",
    counterpart: "schedule job fields + memory workspace notes (`qubit memory note`)",
  },
  {
    action: "cron.scratch.set",
    code: "CRON_SCRATCH_OPERATOR_REQUIRED",
    reason: "an upstream per-job cron scratch store (cron_job_scratch); Qubit runs none",
    counterpart: "schedule job fields + memory workspace notes (`qubit memory note`)",
  },
  {
    action: "gateway.suspend.handoff",
    code: "SUSPEND_HANDOFF_OPERATOR_REQUIRED",
    reason: "a service manager that owns process exit (systemd/launchd external restart owner)",
    counterpart: "the operator runbook (`systemctl --user restart <qubit unit>`)",
  },
  {
    action: "portal.list",
    code: "PORTAL_SERVICE_OPERATOR_REQUIRED",
    reason: "a browser/portal hosting service (the upstream portal HTTP proxy listening on a local port)",
    counterpart: "the canonical website-integration store + the operator's own browser",
  },
  {
    action: "portal.open",
    code: "PORTAL_SERVICE_OPERATOR_REQUIRED",
    reason: "a browser/portal hosting service (the upstream portal HTTP proxy listening on a local port)",
    counterpart: "the canonical website-integration store + the operator's own browser",
  },
  {
    action: "portal.close",
    code: "PORTAL_SERVICE_OPERATOR_REQUIRED",
    reason: "a browser/portal hosting service (the upstream portal HTTP proxy listening on a local port)",
    counterpart: "the canonical website-integration store + the operator's own browser",
  },
  {
    action: "taskSuggestions.create",
    code: "TASK_SUGGESTIONS_OPERATOR_REQUIRED",
    reason: "an upstream model-proposed task-suggestion registry + run-control runtime; Qubit runs none",
    counterpart: "durable scheduled work (`qubit cron add`) + the canonical chat session",
  },
  {
    action: "taskSuggestions.accept",
    code: "TASK_SUGGESTIONS_OPERATOR_REQUIRED",
    reason: "an upstream model-proposed task-suggestion registry + run-control runtime; Qubit runs none",
    counterpart: "durable scheduled work (`qubit cron add`) + the canonical chat session",
  },
  {
    action: "taskSuggestions.dismiss",
    code: "TASK_SUGGESTIONS_OPERATOR_REQUIRED",
    reason: "an upstream model-proposed task-suggestion registry + run-control runtime; Qubit runs none",
    counterpart: "durable scheduled work (`qubit cron add`) + the canonical chat session",
  },
  {
    action: "tasks.dismiss",
    code: "TASKS_DISMISS_OPERATOR_REQUIRED",
    reason: "an upstream detached-task completion-delivery registry; Qubit keeps none",
    counterpart: "the canonical read-only task ledger (`qubit tasks`)",
  },
  {
    action: "mentions.list",
    code: "MENTIONS_INBOX_OPERATOR_REQUIRED",
    reason: "a human-mention inbox service backed by configured message channels",
    counterpart: "the automation outbox/orders (`qubit channels`)",
  },
  {
    action: "mentions.dismiss",
    code: "MENTIONS_INBOX_OPERATOR_REQUIRED",
    reason: "a human-mention inbox service backed by configured message channels",
    counterpart: "the automation outbox/orders (`qubit channels`)",
  },
];

export const ocFamilyTalkApi = {
  ROUTES,
  getTalkMode,
  getTalkCatalog,
  getTalkConfig,
  getVoicewake,
  steerTalkClient,
  appendTalkTranscript,
  callTalkTool,
  speakTalk,
  DISABLED_TALK_ACTIONS,
  TTS_PROVIDERS,
  updateCronEnabled,
  setTtsProvider,
  enableTtsProfile,
  disableTtsProfile,
  setTtsPersona,
  getSuspendPrepare,
  getSuspendResume,
  cancelTask,
  retryTask,
  DISABLED_SCHEDULER_ACTIONS,
};
