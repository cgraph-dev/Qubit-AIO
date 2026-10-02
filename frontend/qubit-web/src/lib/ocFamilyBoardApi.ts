/**
 * Qubit board-admin + task-inbox family API.
 *
 * New Qubit-owned client for the board-admin (`/qubit-board-admin`) and
 * task-inbox (`/qubit-task-inbox`) pages. Every path lives under
 * `/api/qubit/<family>/<action>` (see {@link ROUTES}); reads go through
 * `fetchJSON`, mutations through the shared staged-confirmation round-trip
 * (`runMutationWithConfirm`), and typed policy refusals render through
 * `refusalOrError` — the same contracts as the board plugin surface
 * (`frontend/qubit-app/plugins/qubit-boards/qubit-boards.js`: staged 409,
 * inline confirm settle, typed backend states, never a faked success).
 *
 * Refusal constants below are verbatim from the gateway handler returns
 * (`voicebridge/gateway/qubit_handlers/board.py`,
 * `voicebridge/gateway/qubit_handlers/scheduler_media.py`): code +
 * prerequisite + counterpart are quoted exactly so the disabled-with-reason
 * rows on the pages can never drift from the backend.
 */

import { fetchJSON, runMutationWithConfirm } from "@/lib/api";
import { refusalOrError } from "@/lib/policy-refusal";

// ── ROUTES manifest ──────────────────────────────────────────────────────

export type FamilyName = "board" | "task";
export type RouteMethod = "GET" | "POST" | "DELETE";

export interface FamilyRoute {
  family: FamilyName;
  action: string;
  method: RouteMethod;
  path: string;
}

/** Every path + method this family client addresses. */
export const ROUTES: ReadonlyArray<FamilyRoute> = [
  { family: "board", action: "tabs", method: "GET", path: "/api/qubit/board/tabs" },
  { family: "board", action: "tab_create", method: "POST", path: "/api/qubit/board/tab_create" },
  { family: "board", action: "tab_rename", method: "POST", path: "/api/qubit/board/tab_rename" },
  { family: "board", action: "tab_move", method: "POST", path: "/api/qubit/board/tab_move" },
  { family: "board", action: "tab_delete", method: "POST", path: "/api/qubit/board/tab_delete" },
  { family: "board", action: "widgets", method: "GET", path: "/api/qubit/board/widgets" },
  { family: "board", action: "widget_add", method: "POST", path: "/api/qubit/board/widget_add" },
  { family: "board", action: "widget_remove", method: "POST", path: "/api/qubit/board/widget_remove" },
  { family: "board", action: "roster", method: "GET", path: "/api/qubit/board/roster" },
  { family: "board", action: "role_grant", method: "POST", path: "/api/qubit/board/role_grant" },
  { family: "board", action: "log", method: "GET", path: "/api/qubit/board/log" },
  { family: "task", action: "ledger", method: "GET", path: "/api/qubit/task/ledger" },
  { family: "task", action: "suggestions", method: "GET", path: "/api/qubit/task/suggestions" },
  { family: "task", action: "cancel", method: "POST", path: "/api/qubit/task/cancel" },
  { family: "task", action: "retry", method: "POST", path: "/api/qubit/task/retry" },
];

export function routePath(family: FamilyName, action: string): FamilyRoute | undefined {
  return ROUTES.find((r) => r.family === family && r.action === action);
}

function url(family: FamilyName, action: string, query?: Record<string, string>): string {
  const route = routePath(family, action);
  if (!route) throw new Error(`unknown family route ${family}/${action}`);
  if (!query) return route.path;
  const qs = new URLSearchParams(query).toString();
  return qs ? `${route.path}?${qs}` : route.path;
}

function post(path: string, body: Record<string, unknown>): Promise<unknown> {
  return fetchJSON<unknown>(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

// ── shapes (defensive: the pages normalize both envelope shapes) ──────────

export interface BoardTab {
  slug: string;
  name?: string;
  title?: string;
  position?: number;
}

export interface BoardWidget {
  id: string;
  kind?: string;
  title?: string;
  tab?: string;
}

export interface BoardRole {
  subject: string;
  role: string;
  scope?: string;
}

export interface BoardEvent {
  id?: string;
  kind?: string;
  detail?: string;
  at?: string;
}

export interface TaskRow {
  id: string;
  title?: string;
  state?: string;
  session?: string;
  updated_at?: string;
}

export interface SuggestionRow {
  id: string;
  prompt?: string;
  title?: string;
  session?: string;
}

export interface MentionRow {
  id: string;
  from?: string;
  text?: string;
}

function asArray(value: unknown): Array<Record<string, unknown>> {
  return Array.isArray(value) ? (value as Array<Record<string, unknown>>) : [];
}

function str(value: unknown): string {
  return typeof value === "string" ? value : "";
}

export function normalizeTabs(payload: unknown): BoardTab[] {
  if (!payload || typeof payload !== "object") return [];
  const env = payload as Record<string, unknown>;
  const inner = env.result ?? env;
  const list = asArray((inner as Record<string, unknown>).tabs);
  // kanban_tab_list nests the array one level deeper; accept both shapes.
  const nested = !Array.isArray((inner as Record<string, unknown>).tabs)
    && (inner as Record<string, unknown>).tabs
    && typeof (inner as Record<string, unknown>).tabs === "object"
    ? asArray(
        ((inner as Record<string, unknown>).tabs as Record<string, unknown>).tabs,
      )
    : list;
  return nested.map((t) => ({
    slug: str(t.slug ?? t.tabId ?? t.tab_id ?? t.id),
    name: str(t.name ?? t.title ?? t.slug),
    title: str(t.title ?? t.name ?? ""),
    position: typeof t.position === "number" ? t.position : undefined,
  }));
}

export function normalizeWidgets(payload: unknown): BoardWidget[] {
  if (!payload || typeof payload !== "object") return [];
  const env = payload as Record<string, unknown>;
  const inner = (env.result ?? env) as Record<string, unknown>;
  return asArray(inner.widgets).map((w) => ({
    id: str(w.id ?? w.widget_id ?? w.name),
    kind: str(w.kind),
    title: str(w.title ?? w.name ?? ""),
    tab: str(w.tab ?? w.tabId ?? ""),
  }));
}

export function normalizeRoles(payload: unknown): BoardRole[] {
  if (!payload || typeof payload !== "object") return [];
  const env = payload as Record<string, unknown>;
  const inner = (env.result ?? env) as Record<string, unknown>;
  return asArray(inner.roles).map((r) => ({
    subject: str(r.subject ?? r.user ?? r.id),
    role: str(r.role ?? r.capability ?? ""),
    scope: str(r.scope ?? ""),
  }));
}

export function normalizeEvents(payload: unknown): BoardEvent[] {
  if (!payload || typeof payload !== "object") return [];
  const env = payload as Record<string, unknown>;
  const inner = (env.result ?? env) as Record<string, unknown>;
  const list = asArray(inner.events ?? inner.log);
  return list.map((e, i) => ({
    id: str(e.id ?? `#${i}`),
    kind: str(e.kind ?? e.type ?? ""),
    detail: str(e.detail ?? e.message ?? e.text ?? ""),
    at: str(e.at ?? e.ts ?? e.created_at ?? ""),
  }));
}

export function normalizeTasks(payload: unknown): TaskRow[] {
  if (!payload || typeof payload !== "object") return [];
  const env = payload as Record<string, unknown>;
  const inner = (env.result ?? env) as Record<string, unknown>;
  return asArray(inner.tasks ?? inner.history).map((t) => ({
    id: str(t.id ?? t.task_id ?? t.taskId),
    title: str(t.title ?? t.prompt ?? t.text ?? ""),
    state: str(t.state ?? t.status ?? ""),
    session: str(t.session ?? t.session_key ?? ""),
    updated_at: str(t.updated_at ?? t.at ?? ""),
  }));
}

export function normalizeSuggestions(payload: unknown): SuggestionRow[] {
  if (!payload || typeof payload !== "object") return [];
  const env = payload as Record<string, unknown>;
  const inner = (env.result ?? env) as Record<string, unknown>;
  return asArray(inner.suggestions).map((s) => ({
    id: str(s.id ?? s.task_id ?? s.taskId),
    prompt: str(s.prompt ?? s.title ?? s.text ?? ""),
    title: str(s.title ?? s.prompt ?? ""),
    session: str(s.session ?? s.session_key ?? ""),
  }));
}

export function normalizeMentions(payload: unknown): MentionRow[] {
  if (!payload || typeof payload !== "object") return [];
  const env = payload as Record<string, unknown>;
  const inner = (env.result ?? env) as Record<string, unknown>;
  return asArray(inner.mentions).map((m) => ({
    id: str(m.id),
    from: str(m.from ?? m.author ?? ""),
    text: str(m.text ?? m.body ?? ""),
  }));
}

// ── confirm plumbing ─────────────────────────────────────────────────────

export type ConfirmPrompt = (detail: string) => boolean | Promise<boolean>;

function mutate(path: string, body: Record<string, unknown>, confirm?: ConfirmPrompt): Promise<unknown> {
  return runMutationWithConfirm(() => post(path, body), confirm);
}

// ── board family ─────────────────────────────────────────────────────────

export async function listBoardTabs(board: string): Promise<unknown> {
  return fetchJSON<unknown>(url("board", "tabs", { board }));
}

export async function createBoardTab(
  board: string,
  slug: string,
  title: string,
  confirm?: ConfirmPrompt,
): Promise<unknown> {
  return mutate(url("board", "tab_create"), { board, tabId: slug, title }, confirm);
}

export async function renameBoardTab(
  board: string,
  slug: string,
  title: string,
  confirm?: ConfirmPrompt,
): Promise<unknown> {
  return mutate(url("board", "tab_rename"), { board, tabId: slug, title }, confirm);
}

export async function moveBoardTab(
  board: string,
  slug: string,
  position: number,
  confirm?: ConfirmPrompt,
): Promise<unknown> {
  return mutate(url("board", "tab_move"), { board, tabId: slug, position }, confirm);
}

export async function deleteBoardTab(
  board: string,
  slug: string,
  confirm?: ConfirmPrompt,
): Promise<unknown> {
  return mutate(url("board", "tab_delete"), { board, tabId: slug }, confirm);
}

export async function listBoardWidgets(board: string): Promise<unknown> {
  return fetchJSON<unknown>(url("board", "widgets", { board }));
}

export async function addBoardWidget(
  board: string,
  kind: string,
  title: string,
  content: string,
  confirm?: ConfirmPrompt,
): Promise<unknown> {
  return mutate(url("board", "widget_add"), { board, kind, title, content }, confirm);
}

export async function removeBoardWidget(
  board: string,
  id: string,
  confirm?: ConfirmPrompt,
): Promise<unknown> {
  return mutate(url("board", "widget_remove"), { board, id }, confirm);
}

export async function getBoardRoster(board: string): Promise<unknown> {
  return fetchJSON<unknown>(url("board", "roster", { board }));
}

export async function grantBoardRole(
  board: string,
  subject: string,
  role: string,
  confirm?: ConfirmPrompt,
): Promise<unknown> {
  return mutate(url("board", "role_grant"), { board, subject, role }, confirm);
}

export async function getBoardLog(board: string): Promise<unknown> {
  return fetchJSON<unknown>(url("board", "log", { board }));
}

// ── task family ──────────────────────────────────────────────────────────

export async function getTaskLedger(): Promise<unknown> {
  return fetchJSON<unknown>(url("task", "ledger"));
}

export async function getTaskSuggestions(): Promise<unknown> {
  return fetchJSON<unknown>(url("task", "suggestions"));
}

export interface TaskTarget {
  receiptId?: string;
  session?: string;
  taskId?: string;
}

export async function cancelTask(target: TaskTarget, confirm?: ConfirmPrompt): Promise<unknown> {
  return mutate(
    url("task", "cancel"),
    {
      receiptId: target.receiptId || undefined,
      session: target.session || undefined,
      taskId: target.taskId || undefined,
    },
    confirm,
  );
}

export async function retryTask(target: TaskTarget, confirm?: ConfirmPrompt): Promise<unknown> {
  return mutate(
    url("task", "retry"),
    {
      receiptId: target.receiptId || undefined,
      session: target.session || undefined,
      taskId: target.taskId || undefined,
    },
    confirm,
  );
}

// ── verbatim gateway refusals (disabled-with-reason rows) ────────────────
// Quoted exactly from the handler returns so the pages can never drift.

export interface GatewayRefusal {
  method: string;
  code: string;
  detail: string;
  prerequisite: string;
  counterpart: string;
}

export const BOARD_EVENT_REFUSAL: GatewayRefusal = {
  method: "board.event",
  code: "BOARD_EVENT_OPERATOR_REQUIRED",
  detail: "board.event appends a sandboxed widget notice; Qubit runs no board widget sandbox",
  prerequisite: "a board widget sandbox runtime + signed view tickets",
  counterpart: "`qubit board log` (kanban board event log)",
};

export const BOARD_PROMPT_AUTHORIZE_REFUSAL: GatewayRefusal = {
  method: "board.prompt.authorize",
  code: "BOARD_VIEW_TICKET_OPERATOR_REQUIRED",
  detail: "board.prompt.authorize validates a signed board view ticket; Qubit issues no board view tickets",
  prerequisite: "a signed board view-ticket runtime",
  counterpart: "`qubit board roles` + the shared confirmation journal",
};

export const BOARD_ACTION_REFUSAL: GatewayRefusal = {
  method: "board.action",
  code: "BOARD_ACTION_OPERATOR_REQUIRED",
  detail: "board.action runs a board capability verb through the widget sandbox/capability authority; Qubit runs neither",
  prerequisite: "a board widget sandbox + capability authority runtime",
  counterpart: "`qubit cron` (schedules) + the shared confirmation journal",
};

export const TASKS_DISMISS_REFUSAL: GatewayRefusal = {
  method: "tasks.dismiss",
  code: "TASKS_DISMISS_OPERATOR_REQUIRED",
  detail: "Qubit keeps no detached-task completion-delivery registry; nothing is dismissed",
  prerequisite: "an upstream detached-task/subagent completion-delivery registry; Qubit keeps none",
  counterpart: "the canonical read-only task ledger (`qubit tasks`)",
};

export const MENTIONS_REFUSAL: GatewayRefusal = {
  method: "mentions.list / mentions.dismiss",
  code: "MENTIONS_INBOX_OPERATOR_REQUIRED",
  detail: "Qubit runs no human-mention inbox service; no mention is listed or dismissed",
  prerequisite: "a human-mention inbox service backed by configured message channels",
  counterpart: "voicebridge.automation outbox/orders (`qubit channels`)",
};

export const TASK_SUGGESTIONS_REFUSAL: GatewayRefusal = {
  method: "taskSuggestions.create / taskSuggestions.accept / taskSuggestions.dismiss",
  code: "TASK_SUGGESTIONS_OPERATOR_REQUIRED",
  detail: "Qubit keeps no ephemeral model-proposed task-suggestion registry; no suggestion is created, accepted, or dismissed",
  prerequisite: "an upstream model-proposed task-suggestion registry + run-control runtime; Qubit runs none",
  counterpart: "durable scheduled work via voicebridge.schedules.create_job (`qubit cron add`) + the canonical chat session",
};

export { refusalOrError };
