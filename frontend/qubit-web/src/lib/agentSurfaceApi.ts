/**
 * Agent-surface API client for the ported computer + agent-ui surfaces.
 *
 * Every function hits a route that was probed LIVE against the running
 * voicebridge bridge (`/agent/api/qubit/<family>/<action>`). No new daemon,
 * no invented transport.
 *
 * Route map (probed, real status codes in the port report):
 *   task ledger     GET  /api/qubit/bot/tasks-list          200
 *                   GET  /api/qubit/bot/tasks-get?task=..   200
 *   ideas           GET  /api/qubit/bot/ideas-list          200
 *   goals           GET  /api/qubit/bot/goals-list          200
 *   alerts/receipts GET  /api/qubit/bot/alerts-list         200
 *   rich cards      GET  /api/qubit/bot/cards-get?card=..   200/404
 *   computer status GET  /api/qubit/bot/computer-status     200
 *   desktop ops     GET  /api/qubit/desktop/ops             200
 *                   GET  /api/qubit/desktop/presentation    200
 *                   POST /api/qubit/desktop/pip|present     409 (confirmation)
 *   agent roster    GET  /api/qubit/harness/harnesses       200
 *   workspace       GET  /api/qubit/agent-workspace/list    200
 *                   GET  /api/qubit/agent-workspace/get     200
 *   run status      GET  /api/qubit/agent-workspace/run-status?runId=..  400 w/o id
 *
 * Capabilities with NO Qubit backend are declared in
 * {@link AGENT_SURFACE_GAPS} and surfaced as honest, stated empty states —
 * never a fake success and never a dead button.
 */
import { ConfirmRequiredError, fetchJSON, runMutationWithConfirm } from "@/lib/api";

/* --------------------------------------------------------------- envelopes */

export function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

export function unwrap(value: unknown): Record<string, unknown> {
  const env = asRecord(value);
  const inner = asRecord(env.result);
  return Object.keys(inner).length ? inner : env;
}

export function asArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

/* ------------------------------------------------------------ task ledger */

export interface AgentTaskRow {
  id: string;
  title: string;
  state: string;
  source: string;
  kind: string;
  detail?: string;
  updatedAt?: string;
}

/**
 * Normalize the read-only task ledger from either the live backend shape
 * (`{ state, tasks: [{ id, state, source, kind, title, updated_at }] }`) or
 * the bot family `tasks-list` envelope.
 */
export function normalizeTasks(payload: unknown): AgentTaskRow[] {
  const inner = unwrap(payload);
  const rows = asArray(inner.tasks);
  return rows.map((item) => {
    const row = asRecord(item);
    const id = String(row.id ?? "");
    // A task may carry an empty-string title (an anonymous scheduled run has
    // no job label). `row.title ?? row.id` would keep the "" because `??`
    // only falls back on null/undefined — the row then renders an unlabelled
    // button (empty accessible name). Fall back on a blank title too.
    const rawTitle = typeof row.title === "string" ? row.title.trim() : "";
    const title = rawTitle || id;
    return {
      id,
      title,
      state: String(row.state ?? row.status ?? "unknown"),
      source: String(row.source ?? ""),
      kind: String(row.kind ?? ""),
      detail:
        typeof row.detail === "string"
          ? row.detail
          : typeof row.source_state === "string"
            ? String(row.source_state)
            : undefined,
      updatedAt:
        typeof row.updated_at === "number"
          ? new Date((row.updated_at as number) * 1000).toISOString()
          : typeof row.updated_at === "string"
            ? row.updated_at
            : undefined,
    };
  });
}

export async function listAgentTasks(): Promise<AgentTaskRow[]> {
  return normalizeTasks(await fetchJSON<unknown>("/api/qubit/bot/tasks-list"));
}

export async function getAgentTask(taskId: string): Promise<Record<string, unknown>> {
  return unwrap(
    await fetchJSON<unknown>(
      `/api/qubit/bot/tasks-get?task=${encodeURIComponent(taskId)}`,
    ),
  );
}

/* ------------------------------------------------------------------- ideas */

export interface AgentIdeaEvidence {
  id: string;
  kind: string;
  title: string;
  excerpt: string;
  url?: string;
}

/**
 * The upstream ``Idea`` shape (domain/agent.ts), served verbatim by
 * ``bot/ideas-list`` from the derived inbox over Qubit's own stores.
 */
export interface AgentIdeaRow {
  id: string;
  title: string;
  reason: string;
  prompt?: string;
  kind?: string;
  status?: string;
  evidence?: AgentIdeaEvidence[];
  taskId?: string;
  createdAt?: string;
}

function normalizeEvidence(value: unknown): AgentIdeaEvidence[] {
  return asArray(value).map((item) => {
    const row = asRecord(item);
    return {
      id: String(row.id ?? ""),
      kind: String(row.kind ?? "user"),
      title: String(row.title ?? ""),
      excerpt: String(row.excerpt ?? ""),
      url: typeof row.url === "string" ? row.url : undefined,
    };
  });
}

export function normalizeIdeas(payload: unknown): AgentIdeaRow[] {
  const inner = unwrap(payload);
  const rows = asArray(inner.suggestions).length
    ? asArray(inner.suggestions)
    : asArray(inner.ideas);
  return rows.map((item) => {
    const row = asRecord(item);
    return {
      id: String(row.id ?? row.key ?? row.title ?? ""),
      title: String(row.title ?? row.id ?? ""),
      reason: String(row.reason ?? row.detail ?? ""),
      prompt: typeof row.prompt === "string" ? row.prompt : undefined,
      kind: typeof row.kind === "string" ? row.kind : undefined,
      status: typeof row.status === "string" ? row.status : undefined,
      evidence: normalizeEvidence(row.evidence),
      taskId: typeof row.taskId === "string" ? row.taskId : undefined,
      createdAt: typeof row.createdAt === "string" ? row.createdAt : undefined,
    };
  });
}

export async function listAgentIdeas(): Promise<{
  ideas: AgentIdeaRow[];
  authority: string;
}> {
  const payload = await fetchJSON<unknown>("/api/qubit/bot/ideas-list");
  const inner = unwrap(payload);
  return {
    ideas: normalizeIdeas(payload),
    authority: typeof inner.authority === "string" ? inner.authority : "",
  };
}

/** Confirm-gated POST to one idea action (accept/edit/dismiss). Reuses the
 *  shared staged-confirmation round-trip every other mutation uses. */
async function ideasMutation(
  action: "ideas-accept" | "ideas-edit" | "ideas-dismiss",
  body: Record<string, unknown>,
  confirm?: (detail: string) => boolean | Promise<boolean>,
): Promise<Record<string, unknown>> {
  const run = () =>
    fetchJSON<unknown>(`/api/qubit/bot/${action}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  const result = confirm
    ? await runMutationWithConfirm(run, confirm)
    : await run();
  return unwrap(result);
}

/** Accept an idea: launches a real delegated worker with the idea prompt. */
export async function acceptAgentIdea(
  ideaId: string,
  confirm?: (detail: string) => boolean | Promise<boolean>,
): Promise<Record<string, unknown>> {
  return ideasMutation("ideas-accept", { idea: ideaId }, confirm);
}

/** Dismiss an idea (removes it from the inbox; the record is kept). */
export async function dismissAgentIdea(
  ideaId: string,
  confirm?: (detail: string) => boolean | Promise<boolean>,
): Promise<Record<string, unknown>> {
  return ideasMutation("ideas-dismiss", { idea: ideaId }, confirm);
}

/** Edit an idea's title/reason/prompt before accepting it. */
export async function editAgentIdea(
  ideaId: string,
  patch: { title?: string; reason?: string; prompt?: string },
  confirm?: (detail: string) => boolean | Promise<boolean>,
): Promise<Record<string, unknown>> {
  return ideasMutation("ideas-edit", { idea: ideaId, ...patch }, confirm);
}

/* ------------------------------------------------------------------- goals */

export interface AgentGoalRow {
  id: string;
  title: string;
  status: string;
  description?: string;
}

export function normalizeGoals(payload: unknown): AgentGoalRow[] {
  const inner = unwrap(payload);
  return asArray(inner.goals).map((item) => {
    const row = asRecord(item);
    return {
      id: String(row.id ?? ""),
      title: String(row.title ?? row.id ?? ""),
      status: String(row.status ?? "active"),
      description:
        typeof row.description === "string" ? row.description : undefined,
    };
  });
}

export async function listAgentGoals(): Promise<{
  goals: AgentGoalRow[];
  authority: string;
}> {
  const payload = await fetchJSON<unknown>("/api/qubit/bot/goals-list");
  const inner = unwrap(payload);
  return {
    goals: normalizeGoals(payload),
    authority: typeof inner.authority === "string" ? inner.authority : "",
  };
}

/* ------------------------------------------------------------------ receipts */

export interface AgentRunRow {
  id: string;
  state: string;
  detail?: string;
}

export function normalizeAlerts(payload: unknown): AgentRunRow[] {
  const inner = unwrap(payload);
  return asArray(inner.runs).map((item) => {
    const row = asRecord(item);
    return {
      id: String(row.id ?? ""),
      state: String(row.state ?? "unknown"),
      detail:
        typeof row.detail === "string"
          ? String(row.detail)
          : typeof row.code === "string"
            ? String(row.code)
            : undefined,
    };
  });
}

export async function listAgentAlerts(): Promise<AgentRunRow[]> {
  return normalizeAlerts(await fetchJSON<unknown>("/api/qubit/bot/alerts-list"));
}

/* --------------------------------------------------------------- rich cards */

export async function getAgentCard(cardId: string): Promise<unknown> {
  return fetchJSON<unknown>(
    `/api/qubit/bot/cards-get?card=${encodeURIComponent(cardId)}`,
  );
}

/* --------------------------------------------------------- computer status */

export interface ComputerStatus {
  state: string;
  controlState: string;
  code: string;
  session: string;
  actuator: string;
  detail: string;
  atspiReachable: boolean;
}

/**
 * The live `computer-status` envelope:
 * `{ state, control: { state, code, session, atspi_reachable, actuator, detail } }`.
 */
export function normalizeComputerStatus(payload: unknown): ComputerStatus {
  const inner = unwrap(payload);
  const control = asRecord(inner.control);
  return {
    state: String(inner.state ?? "unknown"),
    controlState: String(control.state ?? "unknown"),
    code: String(control.code ?? ""),
    session: String(control.session ?? ""),
    actuator: String(control.actuator ?? ""),
    detail: String(control.detail ?? ""),
    atspiReachable: control.atspi_reachable === true,
  };
}

export async function getComputerStatus(): Promise<ComputerStatus> {
  return normalizeComputerStatus(
    await fetchJSON<unknown>("/api/qubit/bot/computer-status"),
  );
}

/* ------------------------------------------------------------ desktop ops */

export interface DesktopOps {
  state: string;
  ops: string[];
  apps: string[];
  presentationModes: string[];
}

export function normalizeDesktopOps(payload: unknown): DesktopOps {
  const inner = unwrap(payload);
  const desktop = asRecord(inner.desktop);
  const listOf = (value: unknown) => asArray(value).map((v) => String(v));
  return {
    state: String(desktop.state ?? "unknown"),
    ops: listOf(desktop.ops),
    apps: listOf(desktop.apps),
    presentationModes: listOf(desktop.presentation_modes),
  };
}

export async function getDesktopOps(): Promise<DesktopOps> {
  return normalizeDesktopOps(
    await fetchJSON<unknown>("/api/qubit/desktop/ops"),
  );
}

export interface DesktopPresentation {
  presenting: boolean;
  mode: string;
  panes: string[];
  updatedAt?: number;
  rollback: string;
}

export function normalizePresentation(payload: unknown): DesktopPresentation {
  const inner = unwrap(payload);
  const desktop = asRecord(inner.desktop);
  return {
    presenting: desktop.presenting === true,
    mode: String(desktop.mode ?? ""),
    panes: asArray(desktop.panes).map((v) => String(v)),
    updatedAt:
      typeof desktop.updated_at === "number" ? desktop.updated_at : undefined,
    rollback: String(desktop.rollback ?? ""),
  };
}

export async function getDesktopPresentation(): Promise<DesktopPresentation> {
  return normalizePresentation(
    await fetchJSON<unknown>("/api/qubit/desktop/presentation"),
  );
}

/**
 * Desktop ops are mutating: the bridge answers `409 NEEDS_CONFIRMATION` with a
 * `confirmation_id` the operator must approve. We surface that as a typed
 * "pending confirmation" result rather than pretending the op ran.
 */
export interface DesktopConfirmPending {
  pending: true;
  confirmationId: string;
  detail: string;
}

export type DesktopOpResult =
  | { pending: false; data: Record<string, unknown> }
  | DesktopConfirmPending;

export async function runDesktopOp(
  op: "pip" | "present" | "launch",
  body: Record<string, unknown> = {},
): Promise<DesktopOpResult> {
  try {
    const payload = await fetchJSON<unknown>(`/api/qubit/desktop/${op}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const inner = asRecord(payload);
    if (
      inner.state === "NEEDS_CONFIRMATION" ||
      inner.code === "CONFIRM_REQUIRED"
    ) {
      return {
        pending: true,
        confirmationId: String(inner.confirmation_id ?? ""),
        detail: String(
          inner.detail ?? "This desktop action needs operator confirmation.",
        ),
      };
    }
    return { pending: false, data: unwrap(payload) };
  } catch (err: unknown) {
    // A 409 confirmation is a real, expected backend answer: the bridge has
    // staged the op and is waiting on the operator. Surface it, never fake it.
    if (err instanceof ConfirmRequiredError && err.hasConfirmationId) {
      return {
        pending: true,
        confirmationId: err.confirmationId,
        detail: err.detail || "This desktop action needs operator confirmation.",
      };
    }
    throw err;
  }
}

/* --------------------------------------------------------- agent roster */

export interface AgentRosterRow {
  id: string;
  kind: string;
  route: string;
  state: string;
  model?: string;
  enabled: boolean;
}

export function normalizeRoster(payload: unknown): AgentRosterRow[] {
  const inner = unwrap(payload);
  return asArray(inner.agents).map((item) => {
    const row = asRecord(item);
    return {
      id: String(row.id ?? ""),
      kind: String(row.kind ?? ""),
      route: String(row.route ?? ""),
      state: String(row.state ?? "unknown"),
      model: typeof row.model === "string" ? row.model : undefined,
      enabled: row.enabled !== false,
    };
  });
}

export async function listAgentRoster(): Promise<AgentRosterRow[]> {
  return normalizeRoster(
    await fetchJSON<unknown>("/api/qubit/harness/harnesses"),
  );
}

/* ------------------------------------------------------------- run status */

export interface AgentRunStatus {
  runId: string;
  state: string;
  detail?: string;
}

export async function getAgentRunStatus(runId: string): Promise<AgentRunStatus> {
  const inner = unwrap(
    await fetchJSON<unknown>(
      `/api/qubit/agent-workspace/run-status?runId=${encodeURIComponent(runId)}`,
    ),
  );
  return {
    runId: String(inner.runId ?? inner.run_id ?? runId),
    state: String(inner.state ?? "unknown"),
    detail:
      typeof inner.detail === "string" ? String(inner.detail) : undefined,
  };
}

/* ------------------------------------------------------------- gaps ledger */

/**
 * Surfaces with NO Qubit backend that produces them. Rendered as complete
 * card components with honest, stated empty states — the card EXISTS in the
 * UI (surface completeness) but says plainly that nothing feeds it yet.
 */
export const AGENT_SURFACE_GAPS: ReadonlyArray<{
  surface: string;
  why: string;
  counterpart: string;
}> = [
  {
    surface: "Monitors (page watches)",
    why: "No Qubit backend produces monitor rows; the schedules store has no public-page watch record surface.",
    counterpart: "voicebridge.websites / schedules cron jobs (read-only)",
  },
  {
    surface: "Notifications inbox",
    why: "No Qubit backend exposes a per-user notification inbox; run receipts live in the alert ledger instead.",
    counterpart: "bot.alerts-list (used for Receipts)",
  },
  {
    surface: "Finance artifact ingestion",
    why: "No Qubit backend ingests transaction CSVs; the finance card renders from any artifact data it is given.",
    counterpart: "none declared",
  },
];
