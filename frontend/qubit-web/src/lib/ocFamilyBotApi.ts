/**
 * Qubit Bot family API.
 *
 * Qubit-owned client for the Qubit Bot page (`/qubit-bot`).
 *
 * Port attribution (MIT, license notice preserved): behavior ported from
 * `CopilotKit/openmuse` (https://github.com/CopilotKit/openmuse)
 * commit `9ec439fbaa878197d9d44c2aa982cca55676dd68`, architecture +
 * interaction design (`docs/EXPERIENCE.md`, `docs/RICH-THREADS.md`,
 * `docs/FEATURES.md`), domain shapes (`packages/domain/src/agent.ts`),
 * thread/queue lifecycle (`apps/mobile/src/chat.tsx`,
 * `apps/mobile/src/conversation-queue.ts`, `apps/mobile/src/conversation-run.ts`),
 * and the server engine surfaces (`apps/server/src/engine/*`,
 * `apps/server/src/computer-routes.ts`). The upstream persistence service
 * (CopilotKit Intelligence project key + hosted thread store) is NOT ported:
 * it is a separately configured service and is rendered disabled-with-reason
 * (see {@link DISABLED_BOT_ACTIONS}).
 *
 * Every path lives under `/api/qubit/bot/<action>` (see {@link ROUTES});
 * reads go through `fetchJSON`, mutations through the shared
 * staged-confirmation round-trip (`runMutationWithConfirm`). All rows are
 * `LIVE`: the bridge serves the row (family_bot_harness methods plus the
 * pre-existing registry rows); `NEEDS-BRIDGE` rows are never called.
 * Nothing here invents a transport: no new
 * HTTP/CDP clients, no raw sockets; computer state is read through the
 * existing `voicebridge.computer_use` status surface.
 *
 * Port mapping (existing Qubit backends the bridge must wire these to):
 * - thread list/get onto the canonical session store
 *   (`voicebridge/qubit_session_store.py`: `QubitSessionStore.list`, same
 *   store the `/sessions` page reads — open chats and their drafts stay
 *   mounted client-side while navigating, exactly like the upstream thread
 *   lifecycle).
 * - turn send onto `voicebridge.chat_transport` turns (the canonical gateway
 *   path voice + both chats use); turn stop onto `chat_transport.cancel`.
 * - follow-up queue onto the in-app queue only (held in the open page, not a
 *   server inbox — stopping a reply pauses the queue, it never cancels
 *   delegated tasks).
 * - drafts onto the session draft store (per-thread unsent text survives
 *   navigation; never a server inbox write).
 * - tasks list/get onto the read-only task ledger
 *   (`voicebridge/task_ledger.py`: `list_tasks` / `show`).
 * - tasks launch onto `voicebridge.orchestrator.launch_task` (the exact voice
 *   worker chain, headless) with receipts; pause/resume/cancel/retry onto
 *   `voicebridge.actions.ActionStore` (by receipt) / `chat_transport`
 *   (by session), same contract as the scheduler-media task controls.
 * - ideas onto memory workspace notes + skill suggestions (read-only list;
 *   accept launches a delegated worker with the idea prompt, dismiss records
 *   a note dismissal).
 * - goals + watches onto the `schedules` store (cron create/update for
 *   recurring watches; milestones tracked as memory notes); alerts onto task
 *   completions + schedule change alerts.
 * - computer console onto `voicebridge.computer_use` status (read-only;
 *   interactive takeover stays operator-scoped).
 * - rich cards onto task artifacts (plan / comparison / finance / report).
 */

import { fetchJSON, runMutationWithConfirm } from "@/lib/api";
import { refusalOrError } from "@/lib/policy-refusal";

// ── ROUTES manifest ──────────────────────────────────────────────────────

export type BotFamilyAction =
  | "thread-list"
  | "thread-get"
  | "turn-send"
  | "turn-stop"
  | "followups-queue"
  | "drafts-save"
  | "tasks-list"
  | "tasks-get"
  | "tasks-launch"
  | "tasks-pause"
  | "tasks-resume"
  | "tasks-cancel"
  | "tasks-retry"
  | "tasks-receipts"
  | "ideas-list"
  | "ideas-accept"
  | "ideas-edit"
  | "ideas-dismiss"
  | "goals-list"
  | "goals-set"
  | "watches-set"
  | "alerts-list"
  | "computer-status"
  | "cards-get";
export type RouteMethod = "GET" | "POST";
export type RouteStatus = "NEEDS-BRIDGE" | "LIVE";

export interface BotFamilyRoute {
  family: "bot";
  action: BotFamilyAction;
  method: RouteMethod;
  path: string;
  status: RouteStatus;
  /** Existing Qubit backend (or read policy) the bridge must wire this to. */
  counterpart: string;
}

/**
 * Every path this family client addresses. All rows are new
 * `/api/qubit/bot/<action>` paths with no backend route yet — the bridge
 * ports each onto the named existing backend. The upstream hosted-thread
 * persistence has no row by design: it stays a typed refusal and is never
 * wired (see {@link DISABLED_BOT_ACTIONS}).
 */
export const ROUTES: ReadonlyArray<BotFamilyRoute> = [
  {
    family: "bot",
    action: "thread-list",
    method: "GET",
    path: "/api/qubit/bot/thread-list",
    status: "NEEDS-BRIDGE",
    counterpart: "voicebridge.qubit_session_store.QubitSessionStore.list (canonical session store)",
  },
  {
    family: "bot",
    action: "thread-get",
    method: "GET",
    path: "/api/qubit/bot/thread-get",
    status: "NEEDS-BRIDGE",
    counterpart: "voicebridge.chat_transport transcript read (canonical gateway path)",
  },
  {
    family: "bot",
    action: "turn-send",
    method: "POST",
    path: "/api/qubit/bot/turn-send",
    status: "NEEDS-BRIDGE",
    counterpart: "voicebridge.chat_transport turn (canonical gateway path)",
  },
  {
    family: "bot",
    action: "turn-stop",
    method: "POST",
    path: "/api/qubit/bot/turn-stop",
    status: "NEEDS-BRIDGE",
    counterpart: "voicebridge.chat_transport.cancel (by session; preserves the current draft)",
  },
  {
    family: "bot",
    action: "followups-queue",
    method: "POST",
    path: "/api/qubit/bot/followups-queue",
    status: "LIVE",
    counterpart: "durable follow-up inbox in the Qubit state dir (page mirrors locally)",
  },
  {
    family: "bot",
    action: "drafts-save",
    method: "POST",
    path: "/api/qubit/bot/drafts-save",
    status: "LIVE",
    counterpart: "durable per-thread draft store in the Qubit state dir (page mirrors locally)",
  },
  {
    family: "bot",
    action: "tasks-list",
    method: "GET",
    path: "/api/qubit/bot/tasks-list",
    status: "NEEDS-BRIDGE",
    counterpart: "voicebridge.task_ledger.list_tasks (read-only ledger)",
  },
  {
    family: "bot",
    action: "tasks-get",
    method: "GET",
    path: "/api/qubit/bot/tasks-get",
    status: "NEEDS-BRIDGE",
    counterpart: "voicebridge.task_ledger.show (read-only ledger)",
  },
  {
    family: "bot",
    action: "tasks-launch",
    method: "POST",
    path: "/api/qubit/bot/tasks-launch",
    status: "LIVE",
    counterpart: "voicebridge.orchestrator.launch_task (durable delegated worker, headless)",
  },
  {
    family: "bot",
    action: "tasks-pause",
    method: "POST",
    path: "/api/qubit/bot/tasks-pause",
    status: "LIVE",
    counterpart: "worker box cancel flag + durable pause sidecar (confirm-gated)",
  },
  {
    family: "bot",
    action: "tasks-resume",
    method: "POST",
    path: "/api/qubit/bot/tasks-resume",
    status: "LIVE",
    counterpart: "kept prompt relaunched via orchestrator.launch_task (confirm-gated)",
  },
  {
    family: "bot",
    action: "tasks-cancel",
    method: "POST",
    path: "/api/qubit/bot/tasks-cancel",
    status: "NEEDS-BRIDGE",
    counterpart:
      "voicebridge.actions.ActionStore.cancel (by receipt) / voicebridge.chat_transport.cancel (by session)",
  },
  {
    family: "bot",
    action: "tasks-retry",
    method: "POST",
    path: "/api/qubit/bot/tasks-retry",
    status: "NEEDS-BRIDGE",
    counterpart:
      "voicebridge.actions.ActionStore.retry (by receipt, exact args) / voicebridge.orchestrator.wait_task",
  },
  {
    family: "bot",
    action: "tasks-receipts",
    method: "GET",
    path: "/api/qubit/bot/tasks-receipts",
    status: "NEEDS-BRIDGE",
    counterpart: "voicebridge.actions receipts / events.jsonl run receipts (read-only)",
  },
  {
    family: "bot",
    action: "ideas-list",
    method: "GET",
    path: "/api/qubit/bot/ideas-list",
    status: "LIVE",
    counterpart: "ledger suggestions + durable dismiss/edit overlay (read-only)",
  },
  {
    family: "bot",
    action: "ideas-accept",
    method: "POST",
    path: "/api/qubit/bot/ideas-accept",
    status: "LIVE",
    counterpart: "tasks-launch with the idea prompt (delegated worker)",
  },
  {
    family: "bot",
    action: "ideas-edit",
    method: "POST",
    path: "/api/qubit/bot/ideas-edit",
    status: "LIVE",
    counterpart: "durable overlay edit for the idea record (confirm-gated)",
  },
  {
    family: "bot",
    action: "ideas-dismiss",
    method: "POST",
    path: "/api/qubit/bot/ideas-dismiss",
    status: "LIVE",
    counterpart: "durable overlay dismissal for the idea record (confirm-gated)",
  },
  {
    family: "bot",
    action: "goals-list",
    method: "GET",
    path: "/api/qubit/bot/goals-list",
    status: "LIVE",
    counterpart: "durable goal sidecar + schedules store (read-only)",
  },
  {
    family: "bot",
    action: "goals-set",
    method: "POST",
    path: "/api/qubit/bot/goals-set",
    status: "LIVE",
    counterpart: "durable goal sidecar record (confirm-gated)",
  },
  {
    family: "bot",
    action: "watches-set",
    method: "POST",
    path: "/api/qubit/bot/watches-set",
    status: "LIVE",
    counterpart: "schedules cron create/update + change-alert watch (confirm-gated)",
  },
  {
    family: "bot",
    action: "alerts-list",
    method: "GET",
    path: "/api/qubit/bot/alerts-list",
    status: "NEEDS-BRIDGE",
    counterpart: "task completions + schedule change alerts (read-only inbox)",
  },
  {
    family: "bot",
    action: "computer-status",
    method: "GET",
    path: "/api/qubit/bot/computer-status",
    status: "NEEDS-BRIDGE",
    counterpart: "voicebridge.computer_use status (read-only; no new client)",
  },
  {
    family: "bot",
    action: "cards-get",
    method: "GET",
    path: "/api/qubit/bot/cards-get",
    status: "LIVE",
    counterpart: "session file artifacts, direct or bounded scan (read-only)",
  },
];

export function routePath(action: BotFamilyAction): BotFamilyRoute | undefined {
  return ROUTES.find((r) => r.action === action);
}

type ReadAction =
  | "thread-list"
  | "thread-get"
  | "tasks-list"
  | "tasks-get"
  | "tasks-receipts"
  | "ideas-list"
  | "goals-list"
  | "alerts-list"
  | "computer-status"
  | "cards-get";

type WriteAction = Exclude<BotFamilyAction, ReadAction>;

function url(action: ReadAction, query?: Record<string, string>): string {
  const route = routePath(action);
  if (!route || !route.path) throw new Error(`unknown bot route ${action}`);
  if (!query) return route.path;
  const qs = new URLSearchParams(query).toString();
  return qs ? `${route.path}?${qs}` : route.path;
}

function post(action: WriteAction, body: Record<string, unknown>): Promise<unknown> {
  const route = routePath(action);
  if (!route || !route.path) throw new Error(`unknown bot route ${action}`);
  return fetchJSON<unknown>(route.path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

// ── shapes (defensive: the page normalizes both envelope shapes) ────────

/** Delegated-task states (upstream `TaskStatus` vocabulary, Qubit ledger wording). */
export const QUBIT_BOT_TASK_STATES = [
  "queued",
  "running",
  "waiting_approval",
  "waiting_input",
  "scheduled",
  "paused",
  "succeeded",
  "failed",
  "cancelled",
] as const;

export type QubitBotTaskState = (typeof QUBIT_BOT_TASK_STATES)[number];

export interface BotThread {
  id: string;
  title?: string;
  updatedAt?: string | number;
  messageCount?: number;
  draft?: string;
}

/** One streamed thread event (upstream AG-UI run-event vocabulary, normalized). */
export interface ThreadEvent {
  id?: string;
  kind: string;
  title?: string;
  text?: string;
  state?: string;
}

export interface TaskStep {
  id: string;
  title: string;
  status: "pending" | "running" | "succeeded" | "failed" | "waiting";
  detail?: string;
}

export interface BotTask {
  id: string;
  title?: string;
  kind?: string;
  status?: string;
  goalId?: string;
  plan: TaskStep[];
  progress?: string;
  progressPercent?: number;
  result?: string;
  error?: string;
  receiptId?: string;
  attempts?: number;
  createdAt?: string | number;
  updatedAt?: string | number;
}

export interface BotTaskDetail extends BotTask {
  raw?: unknown;
}

export interface Evidence {
  id?: string;
  kind?: string;
  title: string;
  excerpt: string;
  url?: string;
}

export interface Idea {
  id: string;
  title?: string;
  reason?: string;
  evidence: Evidence[];
  prompt?: string;
  kind?: string;
  status?: string;
  taskId?: string;
  createdAt?: string | number;
}

export interface Milestone {
  id: string;
  title: string;
  done: boolean;
}

export interface Goal {
  id: string;
  title?: string;
  description?: string;
  category?: string;
  status?: string;
  milestones: Milestone[];
  createdAt?: string | number;
}

export interface Watch {
  id: string;
  title?: string;
  url?: string;
  condition?: string;
  value?: string;
  intervalMinutes?: number;
  status?: string;
  nextCheckAt?: string;
  checks?: number;
}

export interface Alert {
  id: string;
  title?: string;
  body?: string;
  taskId?: string;
  createdAt?: string | number;
  read?: boolean;
}

export interface ComputerStatus {
  state?: string;
  sessions?: unknown;
  files?: unknown;
  detail?: string;
  [key: string]: unknown;
}

export interface RichCard {
  id: string;
  kind?: string;
  title?: string;
  summary?: string;
  data?: unknown;
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

function bool(value: unknown): boolean | undefined {
  return typeof value === "boolean" ? value : undefined;
}

function innerOf(payload: unknown): Record<string, unknown> {
  if (!payload || typeof payload !== "object") return {};
  const env = payload as Record<string, unknown>;
  return (env.result ?? env) as Record<string, unknown>;
}

/** Normalize one thread row (upstream thread shape + Qubit session shape). */
export function normalizeBotThread(row: Record<string, unknown>): BotThread {
  return {
    id: str(row.id ?? row.threadId ?? row.thread_id ?? row.session ?? row.sessionKey),
    title: str(row.title ?? row.name) || undefined,
    updatedAt: strOrNum(row.updatedAt ?? row.updated_at ?? row.at),
    messageCount: num(row.messageCount ?? row.message_count ?? row.messages),
    draft: str(row.draft ?? row.unsent ?? row.pendingText) || undefined,
  };
}

export function normalizeBotThreads(payload: unknown): BotThread[] {
  const inner = innerOf(payload);
  return asArray(inner.threads ?? inner.sessions ?? inner.history)
    .map(normalizeBotThread)
    .filter((t) => t.id !== "");
}

/** Normalize one streamed thread event (run-event vocabulary). */
export function normalizeThreadEvent(row: Record<string, unknown>): ThreadEvent {
  return {
    id: str(row.id ?? row.eventId ?? row.event_id) || undefined,
    kind: str(row.kind ?? row.type ?? row.event) || "message",
    title: str(row.title ?? row.name) || undefined,
    text: str(row.text ?? row.detail ?? row.body ?? row.content) || undefined,
    state: str(row.state ?? row.status) || undefined,
  };
}

export function normalizeThreadEvents(payload: unknown): ThreadEvent[] {
  const inner = innerOf(payload);
  return asArray(inner.events ?? inner.messages ?? inner.thread ?? inner.items).map(
    normalizeThreadEvent,
  );
}

function normalizeStep(row: Record<string, unknown>): TaskStep {
  const raw = str(row.status);
  const status =
    raw === "pending" || raw === "running" || raw === "succeeded" || raw === "failed" || raw === "waiting"
      ? raw
      : "pending";
  return {
    id: str(row.id ?? row.stepId ?? row.title),
    title: str(row.title ?? row.name ?? row.id),
    status,
    detail: str(row.detail ?? row.text) || undefined,
  };
}

/** Normalize one delegated task (upstream `AgentTask` + Qubit ledger shape). */
export function normalizeBotTask(row: Record<string, unknown>): BotTask {
  const percentRaw = row.progressPercent ?? row.progress_percent ?? row.percent;
  let progressPercent = num(percentRaw);
  if (progressPercent !== undefined) {
    progressPercent = Math.max(0, Math.min(100, progressPercent));
  }
  return {
    id: str(row.id ?? row.task_id ?? row.taskId),
    title: str(row.title ?? row.prompt ?? row.text ?? row.summary) || undefined,
    kind: str(row.kind ?? row.taskKind ?? row.task_kind) || undefined,
    status: str(row.status ?? row.state) || undefined,
    goalId: str(row.goalId ?? row.goal_id) || undefined,
    plan: asArray(row.plan ?? row.steps).map(normalizeStep),
    progress: str(row.progress ?? row.progressSummary ?? row.progress_summary) || undefined,
    progressPercent,
    result: str(row.result ?? row.output ?? row.answer) || undefined,
    error: str(row.error ?? row.terminalSummary ?? row.terminal_summary) || undefined,
    receiptId: str(row.receiptId ?? row.receipt_id ?? row.actionId ?? row.action_id) || undefined,
    attempts: num(row.attempts),
    createdAt: strOrNum(row.createdAt ?? row.created_at),
    updatedAt: strOrNum(row.updatedAt ?? row.updated_at ?? row.at),
  };
}

export function normalizeBotTasks(payload: unknown): BotTask[] {
  const inner = innerOf(payload);
  return asArray(inner.tasks ?? inner.history)
    .map(normalizeBotTask)
    .filter((t) => t.id !== "");
}

export function normalizeBotTaskDetail(payload: unknown): BotTaskDetail | null {
  const inner = innerOf(payload);
  const row = (inner.task ?? inner) as Record<string, unknown>;
  if (!row || typeof row !== "object" || Array.isArray(row)) return null;
  const task = normalizeBotTask(row);
  if (!task.id) return null;
  return { ...task, raw: row.raw ?? undefined };
}

function normalizeEvidence(row: Record<string, unknown>): Evidence {
  return {
    id: str(row.id) || undefined,
    kind: str(row.kind) || undefined,
    title: str(row.title ?? row.name ?? row.subject),
    excerpt: str(row.excerpt ?? row.snippet ?? row.text ?? row.body),
    url: str(row.url ?? row.link) || undefined,
  };
}

/** Normalize one idea with its evidence (upstream `Idea` shape). */
export function normalizeIdea(row: Record<string, unknown>): Idea {
  return {
    id: str(row.id ?? row.ideaId ?? row.idea_id),
    title: str(row.title ?? row.name) || undefined,
    reason: str(row.reason ?? row.why ?? row.detail) || undefined,
    evidence: asArray(row.evidence ?? row.sources).map(normalizeEvidence),
    prompt: str(row.prompt ?? row.text) || undefined,
    kind: str(row.kind) || undefined,
    status: str(row.status ?? row.state) || undefined,
    taskId: str(row.taskId ?? row.task_id) || undefined,
    createdAt: strOrNum(row.createdAt ?? row.created_at),
  };
}

export function normalizeIdeas(payload: unknown): Idea[] {
  const inner = innerOf(payload);
  return asArray(inner.ideas ?? inner.suggestions)
    .map(normalizeIdea)
    .filter((i) => i.id !== "");
}

function normalizeMilestone(row: Record<string, unknown>): Milestone {
  return {
    id: str(row.id ?? row.title),
    title: str(row.title ?? row.name ?? row.id),
    done: bool(row.done ?? row.completed) ?? false,
  };
}

/** Normalize one goal with milestones (upstream `Goal` shape). */
export function normalizeGoal(row: Record<string, unknown>): Goal {
  return {
    id: str(row.id ?? row.goalId ?? row.goal_id),
    title: str(row.title ?? row.name) || undefined,
    description: str(row.description ?? row.detail) || undefined,
    category: str(row.category) || undefined,
    status: str(row.status ?? row.state) || undefined,
    milestones: asArray(row.milestones).map(normalizeMilestone),
    createdAt: strOrNum(row.createdAt ?? row.created_at),
  };
}

export function normalizeGoals(payload: unknown): Goal[] {
  const inner = innerOf(payload);
  const rows = asArray(inner.goals ?? inner.watches ?? inner.monitors).map(normalizeGoal);
  return rows.filter((g) => g.id !== "");
}

/** Normalize one watch (upstream `Monitor` shape). */
export function normalizeWatch(row: Record<string, unknown>): Watch {
  return {
    id: str(row.id ?? row.monitorId ?? row.monitor_id ?? row.watchId),
    title: str(row.title ?? row.name) || undefined,
    url: str(row.url ?? row.link) || undefined,
    condition: str(row.condition) || undefined,
    value: str(row.value ?? row.expected) || undefined,
    intervalMinutes: num(row.intervalMinutes ?? row.interval_minutes),
    status: str(row.status ?? row.state) || undefined,
    nextCheckAt: str(row.nextCheckAt ?? row.next_check_at) || undefined,
    checks: num(row.checks),
  };
}

export function normalizeWatches(payload: unknown): Watch[] {
  const inner = innerOf(payload);
  return asArray(inner.watches ?? inner.monitors ?? inner.goals)
    .map(normalizeWatch)
    .filter((w) => w.id !== "");
}

/** Normalize one alert (upstream `AgentNotification` shape). */
export function normalizeAlert(row: Record<string, unknown>): Alert {
  return {
    id: str(row.id ?? row.alertId ?? row.alert_id),
    title: str(row.title ?? row.name) || undefined,
    body: str(row.body ?? row.text ?? row.detail) || undefined,
    taskId: str(row.taskId ?? row.task_id) || undefined,
    createdAt: strOrNum(row.createdAt ?? row.created_at ?? row.at),
    read: bool(row.read),
  };
}

export function normalizeAlerts(payload: unknown): Alert[] {
  const inner = innerOf(payload);
  return asArray(inner.alerts ?? inner.notifications ?? inner.inbox)
    .map(normalizeAlert)
    .filter((a) => a.id !== "");
}

/** Normalize one rich card (upstream `AgentArtifact` shape). */
export function normalizeRichCard(payload: unknown): RichCard | null {
  const inner = innerOf(payload);
  const row = (inner.card ?? inner.artifact ?? inner) as Record<string, unknown>;
  if (!row || typeof row !== "object" || Array.isArray(row)) return null;
  const id = str(row.id ?? row.artifactId ?? row.artifact_id);
  if (!id) return null;
  return {
    id,
    kind: str(row.kind ?? row.type) || undefined,
    title: str(row.title ?? row.name) || undefined,
    summary: str(row.summary ?? row.text ?? row.detail) || undefined,
    data: row.data ?? undefined,
  };
}

// ── confirm plumbing ─────────────────────────────────────────────────────

export type ConfirmPrompt = (detail: string) => boolean | Promise<boolean>;

function mutate(action: WriteAction, body: Record<string, unknown>, confirm?: ConfirmPrompt): Promise<unknown> {
  return runMutationWithConfirm(() => post(action, body), confirm);
}

// ── thread / turns ───────────────────────────────────────────────────────

/** Port of the upstream thread list (open chats; drafts stay mounted client-side). */
export async function listBotThreads(): Promise<unknown> {
  return fetchJSON<unknown>(url("thread-list"));
}

/** Port of the upstream thread read (one conversation + its streamed events). */
export async function getBotThread(threadId: string): Promise<unknown> {
  return fetchJSON<unknown>(url("thread-get", { thread: threadId }));
}

/**
 * Send one turn through the canonical chat path (confirm-gated). The composer
 * stays editable while the turn runs; the send control becomes stop.
 */
export async function sendBotTurn(
  input: { text: string; thread?: string },
  confirm?: ConfirmPrompt,
): Promise<unknown> {
  return mutate("turn-send", { text: input.text, thread: input.thread ?? undefined }, confirm);
}

/**
 * Stop the running turn (confirm-gated). Stopping preserves the current
 * draft and pauses the follow-up queue; it never cancels delegated tasks.
 */
export async function stopBotTurn(thread: string, confirm?: ConfirmPrompt): Promise<unknown> {
  return mutate("turn-stop", { thread }, confirm);
}

/** Queue follow-ups to run in order after the current turn (confirm-gated). */
export async function queueBotFollowups(
  input: { thread: string; messages: string[] },
  confirm?: ConfirmPrompt,
): Promise<unknown> {
  return mutate("followups-queue", { thread: input.thread, messages: input.messages }, confirm);
}

/** Save the per-thread draft (confirm-gated; drafts survive navigation). */
export async function saveBotDraft(
  input: { thread: string; text: string },
  confirm?: ConfirmPrompt,
): Promise<unknown> {
  return mutate("drafts-save", { thread: input.thread, text: input.text }, confirm);
}

// ── delegated tasks ──────────────────────────────────────────────────────

/** Port of the upstream durable-task list (read-only ledger). */
export async function listBotTasks(filter?: { status?: string; limit?: number }): Promise<unknown> {
  const query: Record<string, string> = {};
  if (filter?.status) query.status = filter.status;
  if (filter?.limit !== undefined) query.limit = String(filter.limit);
  return fetchJSON<unknown>(url("tasks-list", query));
}

/** Port of the upstream task inspect (plan + progress + receipts). */
export async function getBotTask(lookup: string): Promise<unknown> {
  return fetchJSON<unknown>(url("tasks-get", { lookup }));
}

/**
 * Launch a durable delegated task (confirm-gated). Delegated tasks are
 * server work: they survive navigation and report plan/progress/receipts.
 */
export async function launchBotTask(
  input: { title: string; prompt: string; kind?: string; goalId?: string },
  confirm?: ConfirmPrompt,
): Promise<unknown> {
  return mutate(
    "tasks-launch",
    { title: input.title, prompt: input.prompt, kind: input.kind ?? undefined, goalId: input.goalId ?? undefined },
    confirm,
  );
}

/** Pause a delegated task by receipt, task id, or live session (confirm-gated). */
export async function pauseBotTask(
  target: { taskId?: string; receiptId?: string; session?: string } | string,
  confirm?: ConfirmPrompt,
): Promise<unknown> {
  const body =
    typeof target === "string" ? { taskId: target, session: target } : target;
  return mutate("tasks-pause", body, confirm);
}

/** Resume a paused delegated task (confirm-gated). */
export async function resumeBotTask(
  target: { taskId?: string; receiptId?: string; session?: string } | string,
  confirm?: ConfirmPrompt,
): Promise<unknown> {
  const body =
    typeof target === "string" ? { taskId: target, session: target } : target;
  return mutate("tasks-resume", body, confirm);
}

/**
 * Cancel a delegated task by receipt or session (confirm-gated). Cancelling
 * the queue never cancels delegated tasks; this is the explicit task cancel.
 */
export async function cancelBotTask(
  target: { taskId?: string; receiptId?: string; session?: string },
  confirm?: ConfirmPrompt,
): Promise<unknown> {
  return mutate(
    "tasks-cancel",
    { taskId: target.taskId ?? undefined, receiptId: target.receiptId ?? undefined, session: target.session ?? undefined },
    confirm,
  );
}

/** Retry a delegated task with its exact args (confirm-gated). */
export async function retryBotTask(
  target: { taskId?: string; receiptId?: string; session?: string },
  confirm?: ConfirmPrompt,
): Promise<unknown> {
  return mutate(
    "tasks-retry",
    { taskId: target.taskId ?? undefined, receiptId: target.receiptId ?? undefined, session: target.session ?? undefined },
    confirm,
  );
}

/** Read the run receipts for one task (read-only). */
export async function getBotTaskReceipts(taskId: string): Promise<unknown> {
  return fetchJSON<unknown>(url("tasks-receipts", { task: taskId }));
}

// ── ideas ────────────────────────────────────────────────────────────────

/** Port of the upstream ideas list with evidence (read-only). */
export async function listBotIdeas(): Promise<unknown> {
  return fetchJSON<unknown>(url("ideas-list"));
}

/** Accept an idea: launches a delegated worker with the idea prompt (confirm-gated). */
export async function acceptBotIdea(ideaId: string, confirm?: ConfirmPrompt): Promise<unknown> {
  return mutate("ideas-accept", { idea: ideaId }, confirm);
}

/** Edit an idea record (confirm-gated). */
export async function editBotIdea(
  input: { idea: string; title?: string; reason?: string; prompt?: string },
  confirm?: ConfirmPrompt,
): Promise<unknown> {
  return mutate(
    "ideas-edit",
    { idea: input.idea, title: input.title ?? undefined, reason: input.reason ?? undefined, prompt: input.prompt ?? undefined },
    confirm,
  );
}

/** Dismiss an idea record (confirm-gated). */
export async function dismissBotIdea(ideaId: string, confirm?: ConfirmPrompt): Promise<unknown> {
  return mutate("ideas-dismiss", { idea: ideaId }, confirm);
}

// ── goals, watches, alerts ───────────────────────────────────────────────

/** Port of the upstream goals/tracking list (read-only). */
export async function listBotGoals(): Promise<unknown> {
  return fetchJSON<unknown>(url("goals-list"));
}

/** Record a goal with milestones (confirm-gated). */
export async function setBotGoal(
  input: { title: string; description?: string; milestones?: string[] },
  confirm?: ConfirmPrompt,
): Promise<unknown> {
  return mutate(
    "goals-set",
    { title: input.title, description: input.description ?? undefined, milestones: input.milestones ?? undefined },
    confirm,
  );
}

/** Record a recurring watch with an alert condition (confirm-gated). */
export async function setBotWatch(
  input: { title: string; url?: string; condition?: string; value?: string; intervalMinutes?: number },
  confirm?: ConfirmPrompt,
): Promise<unknown> {
  return mutate(
    "watches-set",
    {
      title: input.title,
      url: input.url ?? undefined,
      condition: input.condition ?? undefined,
      value: input.value ?? undefined,
      intervalMinutes: input.intervalMinutes ?? undefined,
    },
    confirm,
  );
}

/** Read the durable alert inbox (read-only; alerts link to their task). */
export async function listBotAlerts(): Promise<unknown> {
  return fetchJSON<unknown>(url("alerts-list"));
}

// ── computer console + rich cards ────────────────────────────────────────

/**
 * Read the agent-computer console status (read-only). Interactive takeover
 * stays operator-scoped; this surface reports state only and opens no new
 * client.
 */
export async function getBotComputerStatus(): Promise<unknown> {
  return fetchJSON<unknown>(url("computer-status"));
}

/** Read one rich card (plan / comparison / finance / report, read-only). */
export async function getBotCard(cardId: string): Promise<unknown> {
  return fetchJSON<unknown>(url("cards-get", { card: cardId }));
}

// ── never-wired upstream service (disabled-with-reason rows) ─────────────

export interface DisabledBotAction {
  action: string;
  code: string;
  reason: string;
  counterpart: string;
}

/**
 * Upstream operations that are never called. Code + reason are the exact
 * `code` / `operator_prerequisite` vocabulary the backend emits — rendered
 * disabled-with-reason, never called. The hosted thread-persistence service
 * is a separately configured service (not MIT-covered, not bundled): thread
 * state lives in the canonical Qubit session store instead.
 */
export const DISABLED_BOT_ACTIONS: DisabledBotAction[] = [
  {
    action: "intelligence.threads.list",
    code: "INTELLIGENCE_PERSISTENCE_OPERATOR_REQUIRED",
    reason: "a separately configured hosted thread-persistence service, which Qubit does not run",
    counterpart: "the canonical Qubit session store (state.db) + thread drafts on this page",
  },
  {
    action: "intelligence.threads.replay",
    code: "INTELLIGENCE_PERSISTENCE_OPERATOR_REQUIRED",
    reason: "a separately configured hosted thread-persistence service, which Qubit does not run",
    counterpart: "the canonical Qubit session store (state.db) + thread drafts on this page",
  },
  {
    action: "intelligence.threads.archive",
    code: "INTELLIGENCE_PERSISTENCE_OPERATOR_REQUIRED",
    reason: "a separately configured hosted thread-persistence service, which Qubit does not run",
    counterpart: "the canonical Qubit session store (state.db) + thread drafts on this page",
  },
];

export { refusalOrError };
