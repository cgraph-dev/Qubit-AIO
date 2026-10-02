/**
 * Qubit background-tasks family API.
 *
 * New Qubit-owned client for the background-tasks page (`/qubit-tasks`).
 * Separate from the task-inbox client (`@/lib/ocFamilyBoardApi`, route
 * `/qubit-task-inbox`): this family mirrors the upstream background-tasks
 * depth (`docs/automation/tasks.md`: list/filter, inspect by lookup, cancel,
 * progress where reported) as its own surface.
 *
 * Every path lives under `/api/qubit/tasks/<action>` (see {@link ROUTES});
 * reads go through `fetchJSON`, mutations through the shared
 * staged-confirmation round-trip (`runMutationWithConfirm`).
 *
 * Port mapping (existing Qubit backends the bridge must wire these to):
 * - list/get onto the read-only task ledger (`voicebridge/task_ledger.py`:
 *   `list_tasks` / `show` — sources `opencode`/`schedules`/`kanban`, canonical
 *   states `running`/`ok`/`failed`/`blocked`/`cancelled`/`pending`/`unknown`).
 * - cancel-by-receipt onto `voicebridge.actions.ActionStore.cancel`
 *   (a bare ledger id maps to no live run — cancel needs a real action
 *   receipt or an in-flight chat session, same contract as
 *   `gateway/qubit_handlers/scheduler_media.py::_tasks_cancel`).
 * - retry onto `voicebridge.actions.ActionStore.retry` (exact-args retry of a
 *   real receipt, same contract as `_tasks_retry`).
 * - `tasks.dismiss` stays refused (`TASKS_DISMISS_OPERATOR_REQUIRED`) —
 *   disabled-with-reason on the page, never wired (see {@link TASKS_DISMISS_REFUSAL}).
 */

import { fetchJSON, runMutationWithConfirm } from "@/lib/api";
import { refusalOrError } from "@/lib/policy-refusal";

// ── ROUTES manifest ──────────────────────────────────────────────────────

export type TasksFamilyAction = "list" | "get" | "cancel" | "retry" | "dismiss";
export type RouteMethod = "GET" | "POST";
export type RouteStatus = "NEEDS-BRIDGE" | "REFUSED-NEVER-WIRED";

export interface TasksFamilyRoute {
  family: "tasks";
  action: TasksFamilyAction;
  method: RouteMethod;
  path: string;
  status: RouteStatus;
  /** Existing Qubit backend (or refusal) the bridge must wire this to. */
  counterpart: string;
}

/**
 * Every path this family client addresses. All five rows are new
 * `/api/qubit/tasks/<action>` paths with no backend route yet — the bridge
 * ports each onto the named existing backend. `dismiss` has no path by
 * design: it stays a typed refusal and is never wired.
 */
export const ROUTES: ReadonlyArray<TasksFamilyRoute> = [
  {
    family: "tasks",
    action: "list",
    method: "GET",
    path: "/api/qubit/tasks/list",
    status: "NEEDS-BRIDGE",
    counterpart: "voicebridge.task_ledger.list_tasks (read-only ledger)",
  },
  {
    family: "tasks",
    action: "get",
    method: "GET",
    path: "/api/qubit/tasks/get",
    status: "NEEDS-BRIDGE",
    counterpart: "voicebridge.task_ledger.show (read-only ledger)",
  },
  {
    family: "tasks",
    action: "cancel",
    method: "POST",
    path: "/api/qubit/tasks/cancel",
    status: "NEEDS-BRIDGE",
    counterpart:
      "voicebridge.actions.ActionStore.cancel (by receipt) / voicebridge.chat_transport.cancel (by session)",
  },
  {
    family: "tasks",
    action: "retry",
    method: "POST",
    path: "/api/qubit/tasks/retry",
    status: "NEEDS-BRIDGE",
    counterpart:
      "voicebridge.actions.ActionStore.retry (by receipt, exact args) / voicebridge.chat_transport.retry_last (by session)",
  },
  {
    family: "tasks",
    action: "dismiss",
    method: "POST",
    path: "",
    status: "REFUSED-NEVER-WIRED",
    counterpart: "none — TASKS_DISMISS_OPERATOR_REQUIRED (typed refusal)",
  },
];

export function routePath(action: TasksFamilyAction): TasksFamilyRoute | undefined {
  return ROUTES.find((r) => r.action === action);
}

function url(action: "list" | "get", query?: Record<string, string>): string {
  const route = routePath(action);
  if (!route || !route.path) throw new Error(`unknown tasks route ${action}`);
  if (!query) return route.path;
  const qs = new URLSearchParams(query).toString();
  return qs ? `${route.path}?${qs}` : route.path;
}

function post(action: "cancel" | "retry", body: Record<string, unknown>): Promise<unknown> {
  const route = routePath(action);
  if (!route || !route.path) throw new Error(`unknown tasks route ${action}`);
  return fetchJSON<unknown>(route.path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

// ── shapes (defensive: the page normalizes both envelope shapes) ────────

/** Canonical Qubit ledger states (`voicebridge/task_ledger.py::CANONICAL_STATES`). */
export const QUBIT_TASK_STATES = [
  "running",
  "ok",
  "failed",
  "blocked",
  "cancelled",
  "pending",
  "unknown",
] as const;

export type QubitTaskState = (typeof QUBIT_TASK_STATES)[number];

export interface BackgroundTask {
  id: string;
  kind?: string;
  runtime?: string;
  state?: string;
  title?: string;
  sessionKey?: string;
  childSessionKey?: string;
  runId?: string;
  agentId?: string;
  progress?: string;
  progressPercent?: number;
  error?: string;
  createdAt?: string | number;
  updatedAt?: string | number;
}

export interface BackgroundTaskDetail extends BackgroundTask {
  raw?: unknown;
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

function strOrNum(value: unknown): string | number | undefined {
  return typeof value === "string" || typeof value === "number" ? value : undefined;
}

/**
 * Normalize one task row. Accepts both the upstream `TaskSummary` shape
 * (`taskId`/`status`/`title`/`sessionKey`/`childSessionKey`/`runId`/
 * `progressSummary` — see `src/gateway/server-methods/tasks.ts`) and the
 * Qubit ledger shape (`id`/`state`/`title`/`session`/`updated_at`).
 */
export function normalizeBackgroundTask(row: Record<string, unknown>): BackgroundTask {
  const progressRaw = row.progress ?? row.progressSummary ?? row.progress_summary;
  const percentRaw = row.progressPercent ?? row.progress_percent ?? row.percent;
  let progressPercent = num(percentRaw);
  if (progressPercent !== undefined) {
    progressPercent = Math.max(0, Math.min(100, progressPercent));
  }
  return {
    id: str(row.id ?? row.task_id ?? row.taskId),
    kind: str(row.kind ?? row.taskKind ?? row.task_kind) || undefined,
    runtime: str(row.runtime) || undefined,
    state: str(row.state ?? row.status) || undefined,
    title: str(row.title ?? row.prompt ?? row.text ?? row.summary) || undefined,
    sessionKey: str(row.sessionKey ?? row.session_key ?? row.session) || undefined,
    childSessionKey: str(row.childSessionKey ?? row.child_session_key) || undefined,
    runId: str(row.runId ?? row.run_id ?? row.receipt_id ?? row.receiptId) || undefined,
    agentId: str(row.agentId ?? row.agent_id) || undefined,
    progress: typeof progressRaw === "string" ? progressRaw : undefined,
    progressPercent,
    error: str(row.error ?? row.terminalSummary ?? row.terminal_summary) || undefined,
    createdAt: strOrNum(row.createdAt ?? row.created_at),
    updatedAt: strOrNum(row.updatedAt ?? row.updated_at ?? row.at),
  };
}

export function normalizeBackgroundTasks(payload: unknown): BackgroundTask[] {
  if (!payload || typeof payload !== "object") return [];
  const env = payload as Record<string, unknown>;
  const inner = (env.result ?? env) as Record<string, unknown>;
  return asArray(inner.tasks ?? inner.history).map(normalizeBackgroundTask);
}

export function normalizeBackgroundTaskDetail(payload: unknown): BackgroundTaskDetail | null {
  if (!payload || typeof payload !== "object") return null;
  const env = payload as Record<string, unknown>;
  const inner = (env.result ?? env) as Record<string, unknown>;
  const row = (inner.task ?? inner) as Record<string, unknown>;
  if (!row || typeof row !== "object") return null;
  const task = normalizeBackgroundTask(row);
  if (!task.id) return null;
  return { ...task, raw: row.raw ?? undefined };
}

// ── confirm plumbing ─────────────────────────────────────────────────────

export type ConfirmPrompt = (detail: string) => boolean | Promise<boolean>;

function mutate(
  action: "cancel" | "retry",
  body: Record<string, unknown>,
  confirm?: ConfirmPrompt,
): Promise<unknown> {
  return runMutationWithConfirm(() => post(action, body), confirm);
}

// ── tasks family ─────────────────────────────────────────────────────────

export interface BackgroundTasksFilter {
  state?: string;
  sessionKey?: string;
  limit?: number;
}

/** Port of upstream `tasks list` (+ Qubit ledger state filter). */
export async function listBackgroundTasks(filter?: BackgroundTasksFilter): Promise<unknown> {
  const query: Record<string, string> = {};
  if (filter?.state) query.state = filter.state;
  if (filter?.sessionKey) query.sessionKey = filter.sessionKey;
  if (filter?.limit !== undefined) query.limit = String(filter.limit);
  return fetchJSON<unknown>(url("list", query));
}

/** Port of upstream `tasks show` / `tasks get` — lookup by task id, run/receipt id, or session key. */
export async function getBackgroundTask(lookup: string): Promise<unknown> {
  return fetchJSON<unknown>(url("get", { lookup }));
}

export interface BackgroundTaskTarget {
  receiptId?: string;
  session?: string;
  taskId?: string;
}

/**
 * Port of upstream `tasks cancel` onto cancel-by-receipt. A bare ledger id
 * maps to no live run — cancel needs a real action receipt or an in-flight
 * chat session (confirm-gated).
 */
export async function cancelBackgroundTask(
  target: BackgroundTaskTarget,
  confirm?: ConfirmPrompt,
): Promise<unknown> {
  return mutate(
    "cancel",
    {
      receiptId: target.receiptId || undefined,
      session: target.session || undefined,
      taskId: target.taskId || undefined,
    },
    confirm,
  );
}

/**
 * Retry a real action receipt (exact args, same contract as
 * `ActionStore.retry`) or the last chat turn (confirm-gated).
 */
export async function retryBackgroundTask(
  target: BackgroundTaskTarget,
  confirm?: ConfirmPrompt,
): Promise<unknown> {
  return mutate(
    "retry",
    {
      receiptId: target.receiptId || undefined,
      session: target.session || undefined,
      taskId: target.taskId || undefined,
    },
    confirm,
  );
}

// ── verbatim gateway refusal (disabled-with-reason row) ──────────────────
// Quoted exactly from the handler return
// (`voicebridge/gateway/qubit_handlers/scheduler_media.py::_tasks_dismiss`)
// so the page can never drift from the backend.

export interface GatewayRefusal {
  method: string;
  code: string;
  detail: string;
  prerequisite: string;
  counterpart: string;
}

export const TASKS_DISMISS_REFUSAL: GatewayRefusal = {
  method: "tasks.dismiss",
  code: "TASKS_DISMISS_OPERATOR_REQUIRED",
  detail: "Qubit keeps no detached-task completion-delivery registry; nothing is dismissed",
  prerequisite: "an upstream detached-task/subagent completion-delivery registry; Qubit keeps none",
  counterpart: "the canonical read-only task ledger (`qubit tasks`)",
};

export { refusalOrError };
