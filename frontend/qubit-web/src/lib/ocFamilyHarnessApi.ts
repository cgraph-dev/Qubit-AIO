/**
 * Qubit harness-router family API.
 *
 * Qubit-owned client for the Harness Router page (`/harness-router`).
 *
 * Port of the Unified Harness Protocol pattern (Apache-2.0; upstream
 * `HarnessRouter/harnessrouter`, reference checkout `/tmp/hr-src` — exact
 * repo+commit+paths are recorded in the feature handoff, and the upstream
 * NOTICE is preserved there, not in this file): harness list across
 * backends, task submit with a per-task harness switch, sessions
 * create/resume/inspect, streaming snapshots, files/artifacts, cancellation,
 * and structured failures — rebranded Qubit-only, onto existing Qubit
 * backends.
 *
 * Every path lives under `/api/qubit/harness/<action>` (see {@link ROUTES});
 * reads go through `fetchJSON`, mutations through the shared
 * staged-confirmation round-trip (`runMutationWithConfirm`). No new HTTP or
 * CDP clients are introduced here: streaming is a polling snapshot over
 * `fetchJSON` (the same transport as every other dashboard read), never a new
 * WebSocket/EventSource client.
 *
 * Port mapping (existing Qubit backends the bridge must wire these to):
 * - harnesses/tasks onto `WorkerGuard` `agent.session` roster + the planner
 *   tool backends + the `codex-runtime` port.
 * - task_submit/task_cancel onto the read-only task ledger
 *   (`voicebridge/task_ledger.py`: `list_tasks` / `show`) for history plus
 *   `voicebridge.actions.ActionStore.cancel` (by receipt) /
 *   `voicebridge.chat_transport.cancel` (by session) for live work — the same
 *   contract as the background-tasks family.
 * - session_create/session_resume/session_inspect onto the canonical session
 *   store (the same store the Sessions page reads).
 * - files/artifact_get onto the brokered file ops (`local.files_*` —
 *   path-confined, confirm + audit + e-stop).
 * - starter_kits/plugins onto the local curated kit list and the real plugin
 *   catalog (read-only rows; remote install stays operator-gated).
 *
 * The upstream reference harness is deliberately NOT wired: upstream supports
 * it, Qubit neither renders nor routes it (no row, no path, no fallback).
 */

import { fetchJSON, runMutationWithConfirm } from "@/lib/api";
import { refusalOrError } from "@/lib/policy-refusal";

// ── ROUTES manifest ──────────────────────────────────────────────────────

export type HarnessFamilyAction =
  | "harnesses"
  | "starter_kits"
  | "plugins"
  | "tasks"
  | "task_submit"
  | "task_cancel"
  | "session_create"
  | "session_resume"
  | "session_inspect"
  | "stream"
  | "files"
  | "artifact_get";
export type RouteMethod = "GET" | "POST";
export type RouteStatus = "NEEDS-BRIDGE";

export interface HarnessFamilyRoute {
  family: "harness";
  action: HarnessFamilyAction;
  method: RouteMethod;
  path: string;
  status: RouteStatus;
  /** Existing Qubit backend the bridge must wire this to. */
  counterpart: string;
}

/**
 * Every path this family client addresses. All rows are new
 * `/api/qubit/harness/<action>` paths with no backend route yet — the bridge
 * ports each onto the named existing backend.
 */
export const ROUTES: ReadonlyArray<HarnessFamilyRoute> = [
  {
    family: "harness",
    action: "harnesses",
    method: "GET",
    path: "/api/qubit/harness/harnesses",
    status: "NEEDS-BRIDGE",
    counterpart:
      "WorkerGuard agent.session roster + planner tool backends + codex-runtime port",
  },
  {
    family: "harness",
    action: "starter_kits",
    method: "GET",
    path: "/api/qubit/harness/starter_kits",
    status: "NEEDS-BRIDGE",
    counterpart: "local curated starter-kit list (read-only rows)",
  },
  {
    family: "harness",
    action: "plugins",
    method: "GET",
    path: "/api/qubit/harness/plugins",
    status: "NEEDS-BRIDGE",
    counterpart: "real plugin catalog (read-only rows; remote install operator-gated)",
  },
  {
    family: "harness",
    action: "tasks",
    method: "GET",
    path: "/api/qubit/harness/tasks",
    status: "NEEDS-BRIDGE",
    counterpart: "voicebridge.task_ledger.list_tasks (read-only ledger)",
  },
  {
    family: "harness",
    action: "task_submit",
    method: "POST",
    path: "/api/qubit/harness/task_submit",
    status: "NEEDS-BRIDGE",
    counterpart:
      "agent.session dispatch (WorkerGuard) + task_ledger record (confirm-gated)",
  },
  {
    family: "harness",
    action: "task_cancel",
    method: "POST",
    path: "/api/qubit/harness/task_cancel",
    status: "NEEDS-BRIDGE",
    counterpart:
      "voicebridge.actions.ActionStore.cancel (by receipt) / voicebridge.chat_transport.cancel (by session)",
  },
  {
    family: "harness",
    action: "session_create",
    method: "POST",
    path: "/api/qubit/harness/session_create",
    status: "NEEDS-BRIDGE",
    counterpart: "canonical session store create (confirm-gated)",
  },
  {
    family: "harness",
    action: "session_resume",
    method: "POST",
    path: "/api/qubit/harness/session_resume",
    status: "NEEDS-BRIDGE",
    counterpart: "canonical session store resume (confirm-gated)",
  },
  {
    family: "harness",
    action: "session_inspect",
    method: "GET",
    path: "/api/qubit/harness/session_inspect",
    status: "NEEDS-BRIDGE",
    counterpart: "canonical session store transcript read",
  },
  {
    family: "harness",
    action: "stream",
    method: "GET",
    path: "/api/qubit/harness/stream",
    status: "NEEDS-BRIDGE",
    counterpart:
      "polling snapshot over the session transcript (no new WS/SSE client)",
  },
  {
    family: "harness",
    action: "files",
    method: "GET",
    path: "/api/qubit/harness/files",
    status: "NEEDS-BRIDGE",
    counterpart: "brokered local.files list (path-confined, read-only)",
  },
  {
    family: "harness",
    action: "artifact_get",
    method: "GET",
    path: "/api/qubit/harness/artifact_get",
    status: "NEEDS-BRIDGE",
    counterpart: "brokered local.files read (path-confined, read-only)",
  },
];

export function routePath(action: HarnessFamilyAction): HarnessFamilyRoute | undefined {
  return ROUTES.find((r) => r.action === action);
}

function getUrl(action: Extract<HarnessFamilyAction, "harnesses" | "starter_kits" | "plugins" | "tasks">): string;
function getUrl(
  action: "session_inspect" | "stream" | "files" | "artifact_get",
  query: Record<string, string>,
): string;
function getUrl(action: HarnessFamilyAction, query?: Record<string, string>): string {
  const route = routePath(action);
  if (!route || !route.path) throw new Error(`unknown harness route ${action}`);
  if (!query) return route.path;
  const qs = new URLSearchParams(query).toString();
  return qs ? `${route.path}?${qs}` : route.path;
}

function post(
  action: "task_submit" | "task_cancel" | "session_create" | "session_resume",
  body: Record<string, unknown>,
): Promise<unknown> {
  const route = routePath(action);
  if (!route || !route.path) throw new Error(`unknown harness route ${action}`);
  return fetchJSON<unknown>(route.path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

// ── shapes (defensive: the page normalizes both envelope shapes) ────────

export type QubitHarnessKind = "agent" | "starter-kit" | "plugin";

export interface QubitHarness {
  id: string;
  kind: QubitHarnessKind;
  backend?: string;
  state?: string;
  description?: string;
}

export interface QubitHarnessFailure {
  code: string;
  detail: string;
  retryable: boolean;
}

export interface QubitHarnessTask {
  id: string;
  harness?: string;
  state?: string;
  title?: string;
  failure?: QubitHarnessFailure | null;
}

export interface QubitHarnessSession {
  id: string;
  harness?: string;
  state?: string;
  title?: string;
  transcript?: string;
  failure?: QubitHarnessFailure | null;
}

export interface QubitHarnessFile {
  path: string;
  sizeBytes?: number;
}

function asArray(value: unknown): Array<Record<string, unknown>> {
  return Array.isArray(value) ? (value as Array<Record<string, unknown>>) : [];
}

function str(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function num(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

function bool(value: unknown): boolean {
  return value === true;
}

/** Normalize one harness row (UHP `harness` / Qubit roster shapes). */
export function normalizeHarness(row: Record<string, unknown>): QubitHarness {
  const rawKind = str(row.kind ?? row.type).toLowerCase();
  const kind: QubitHarnessKind =
    rawKind === "starter-kit" || rawKind === "starter_kit" || rawKind === "kit"
      ? "starter-kit"
      : rawKind === "plugin"
        ? "plugin"
        : "agent";
  return {
    id: str(row.id ?? row.harness_id ?? row.harnessId ?? row.name),
    kind,
    backend: str(row.backend ?? row.runtime ?? row.provider) || undefined,
    state: str(row.state ?? row.status) || undefined,
    description: str(row.description ?? row.summary ?? row.title) || undefined,
  };
}

export function normalizeHarnesses(payload: unknown): QubitHarness[] {
  if (!payload || typeof payload !== "object") return [];
  const env = payload as Record<string, unknown>;
  const inner = (env.result ?? env) as Record<string, unknown>;
  return asArray(inner.harnesses ?? inner.items ?? inner.rows)
    .map(normalizeHarness)
    .filter((h) => h.id !== "");
}

/** Normalize a structured failure (UHP `errors` shape: code + detail). */
export function normalizeFailure(value: unknown): QubitHarnessFailure | null {
  if (!value || typeof value !== "object") return null;
  const row = value as Record<string, unknown>;
  const code = str(row.code ?? row.error ?? row.error_code);
  if (!code) return null;
  return {
    code,
    detail: str(row.detail ?? row.message ?? row.terminal_summary) || code,
    retryable: bool(row.retryable ?? row.can_retry),
  };
}

export function normalizeHarnessTask(row: Record<string, unknown>): QubitHarnessTask {
  return {
    id: str(row.id ?? row.task_id ?? row.taskId),
    harness: str(row.harness ?? row.harness_id ?? row.harnessId) || undefined,
    state: str(row.state ?? row.status) || undefined,
    title: str(row.title ?? row.prompt ?? row.summary) || undefined,
    failure: normalizeFailure(row.failure ?? row.error),
  };
}

export function normalizeHarnessTasks(payload: unknown): QubitHarnessTask[] {
  if (!payload || typeof payload !== "object") return [];
  const env = payload as Record<string, unknown>;
  const inner = (env.result ?? env) as Record<string, unknown>;
  return asArray(inner.tasks ?? inner.history ?? inner.items)
    .map(normalizeHarnessTask)
    .filter((t) => t.id !== "");
}

export function normalizeHarnessSession(payload: unknown): QubitHarnessSession | null {
  if (!payload || typeof payload !== "object") return null;
  const env = payload as Record<string, unknown>;
  const inner = (env.result ?? env) as Record<string, unknown>;
  const row = (inner.session ?? inner) as Record<string, unknown>;
  if (!row || typeof row !== "object") return null;
  const id = str(row.id ?? row.session_id ?? row.sessionId ?? row.key);
  if (!id) return null;
  const transcript = row.transcript ?? row.messages_text ?? row.text;
  return {
    id,
    harness: str(row.harness ?? row.harness_id ?? row.harnessId) || undefined,
    state: str(row.state ?? row.status) || undefined,
    title: str(row.title ?? row.label) || undefined,
    transcript: typeof transcript === "string" ? transcript : undefined,
    failure: normalizeFailure(row.failure ?? row.error),
  };
}

export function normalizeHarnessFiles(payload: unknown): QubitHarnessFile[] {
  if (!payload || typeof payload !== "object") return [];
  const env = payload as Record<string, unknown>;
  const inner = (env.result ?? env) as Record<string, unknown>;
  return asArray(inner.files ?? inner.artifacts ?? inner.items)
    .map((row) => ({
      path: str(row.path ?? row.name ?? row.id),
      sizeBytes: num(row.size_bytes ?? row.sizeBytes ?? row.size),
    }))
    .filter((f) => f.path !== "");
}

// ── confirm plumbing ─────────────────────────────────────────────────────

export type ConfirmPrompt = (detail: string) => boolean | Promise<boolean>;

function mutate(
  action: "task_submit" | "task_cancel" | "session_create" | "session_resume",
  body: Record<string, unknown>,
  confirm?: ConfirmPrompt,
): Promise<unknown> {
  return runMutationWithConfirm(() => post(action, body), confirm);
}

// ── harness family ───────────────────────────────────────────────────────

/** Port of the console harness list (agent harnesses across Qubit backends). */
export async function listHarnesses(): Promise<unknown> {
  return fetchJSON<unknown>(getUrl("harnesses"));
}

/** Console Starter Kits section (local curated rows, read-only). */
export async function listStarterKits(): Promise<unknown> {
  return fetchJSON<unknown>(getUrl("starter_kits"));
}

/** Console Plugins section (real catalog rows, read-only). */
export async function listHarnessPlugins(): Promise<unknown> {
  return fetchJSON<unknown>(getUrl("plugins"));
}

/** Task history for the console task list. */
export async function listHarnessTasks(): Promise<unknown> {
  return fetchJSON<unknown>(getUrl("tasks"));
}

export interface HarnessTaskSubmit {
  prompt: string;
  harness: string;
}

/**
 * Submit one task onto the selected harness (the per-task harness switch).
 * Confirm-gated; the bridge dispatches through `agent.session` and records
 * the task in the ledger.
 */
export async function submitHarnessTask(
  submit: HarnessTaskSubmit,
  confirm?: ConfirmPrompt,
): Promise<unknown> {
  return mutate("task_submit", { prompt: submit.prompt, harness: submit.harness }, confirm);
}

/** Cancel a live task by receipt/task id or session (confirm-gated). */
export async function cancelHarnessTask(
  target: { taskId?: string; receiptId?: string; session?: string },
  confirm?: ConfirmPrompt,
): Promise<unknown> {
  return mutate(
    "task_cancel",
    {
      taskId: target.taskId || undefined,
      receiptId: target.receiptId || undefined,
      session: target.session || undefined,
    },
    confirm,
  );
}

/** Create a session on the selected harness (confirm-gated). */
export async function createHarnessSession(
  init: { harness: string; title?: string },
  confirm?: ConfirmPrompt,
): Promise<unknown> {
  return mutate(
    "session_create",
    { harness: init.harness, title: init.title || undefined },
    confirm,
  );
}

/** Resume an existing session (confirm-gated). */
export async function resumeHarnessSession(
  session: string,
  confirm?: ConfirmPrompt,
): Promise<unknown> {
  return mutate("session_resume", { session }, confirm);
}

/** Inspect a session transcript (read-only). */
export async function inspectHarnessSession(session: string): Promise<unknown> {
  return fetchJSON<unknown>(getUrl("session_inspect", { session }));
}

/**
 * Streaming snapshot for a session (polling read over the transcript —
 * deliberately not a new WS/SSE client).
 */
export async function streamHarnessSession(
  session: string,
  cursor?: string,
): Promise<unknown> {
  const query: Record<string, string> = { session };
  if (cursor) query.cursor = cursor;
  return fetchJSON<unknown>(getUrl("stream", query));
}

/** Files/artifacts for a session or task (read-only, path-confined). */
export async function listHarnessFiles(scope: { session?: string; task?: string }): Promise<unknown> {
  const query: Record<string, string> = {};
  if (scope.session) query.session = scope.session;
  if (scope.task) query.task = scope.task;
  return fetchJSON<unknown>(getUrl("files", query));
}

/** Read one artifact (read-only, path-confined). */
export async function getHarnessArtifact(path: string): Promise<unknown> {
  return fetchJSON<unknown>(getUrl("artifact_get", { path }));
}

export { refusalOrError };
