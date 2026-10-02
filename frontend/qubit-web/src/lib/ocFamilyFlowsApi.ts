/**
 * Qubit taskflows family API.
 *
 * New Qubit-owned client for the separate taskflows page (`/qubit-taskflows`).
 * Ports the durable-flow semantics from upstream
 * `docs/automation/{taskflow,standing-orders,hooks}.md` onto the existing
 * `voicebridge.schedules.*` backend (`create`/`list_jobs`/`get_job`/`update_job`
 * /`set_paused`/`pause`/`resume`/`trigger`/`delete`) — never invents infra.
 *
 * Mapping (upstream → Qubit):
 * - Task Flow list/show/cancel + standing-order programs + one-shot reminders
 *   → cron-backed durable flows (`schedules.*` rows). Managed vs mirrored sync
 *   modes are a view concern: recurring schedules render as standing orders,
 *   `run_at` schedules as one-shots.
 * - Hooks (`hooks list/enable/disable`, the 5 bundled hooks) have NO backend
 *   route in Qubit — they render from {@link HOOK_CATALOG} (verbatim from the
 *   upstream bundled-hooks table) with enable/disable marked NEEDS-BRIDGE.
 * - The ephemeral-suggestion registry is NOT here by design — that refusal
 *   lives on `/qubit-task-inbox`.
 *
 * Reads go through `fetchJSON`; every mutation goes through the shared
 * staged-confirmation round-trip (`runMutationWithConfirm`), never a faked
 * success. Routes already served by the backend reuse the existing cron paths
 * (see {@link ROUTES} rows with `backend: "cron"`); rows with
 * `backend: "needs-bridge"` are new `/api/qubit/flows/<action>` paths with no
 * backend route yet — the page renders those controls disabled-with-reason.
 */

import { fetchJSON, runMutationWithConfirm } from "@/lib/api";

/** Human-confirm callback threaded into `runMutationWithConfirm`. */
export type ConfirmFn = (detail: string) => boolean | Promise<boolean>;

// ── ROUTES manifest ──────────────────────────────────────────────────────

export type RouteMethod = "GET" | "POST" | "PUT" | "DELETE";
export type RouteBackend = "cron" | "needs-bridge";

export interface FlowRoute {
  family: "flows";
  action: string;
  method: RouteMethod;
  path: string;
  /** "cron" = served today (reuse, don't duplicate); "needs-bridge" = no backend route yet. */
  backend: RouteBackend;
}

/**
 * Every path + method this family client addresses. Cron rows reuse the
 * existing `qubit_compat` cron routes (`h_cron_jobs`, `cron_create`,
 * `cron_update`, `cron_delete`, `cron_pause`/`cron_resume`/`cron_trigger`);
 * `/api/qubit/flows/*` rows are NEEDS-BRIDGE until a backend route exists.
 */
export const ROUTES: ReadonlyArray<FlowRoute> = [
  // Existing backend (voicebridge.schedules.* via qubit_compat cron routes).
  { family: "flows", action: "list", method: "GET", path: "/api/cron/jobs", backend: "cron" },
  { family: "flows", action: "get", method: "GET", path: "/api/cron/jobs/{id}", backend: "cron" },
  { family: "flows", action: "runs", method: "GET", path: "/api/cron/jobs/{id}/runs", backend: "cron" },
  { family: "flows", action: "create", method: "POST", path: "/api/cron/jobs", backend: "cron" },
  { family: "flows", action: "update", method: "PUT", path: "/api/cron/jobs/{id}", backend: "cron" },
  { family: "flows", action: "delete", method: "DELETE", path: "/api/cron/jobs/{id}", backend: "cron" },
  { family: "flows", action: "pause", method: "POST", path: "/api/cron/jobs/{id}/pause", backend: "cron" },
  { family: "flows", action: "resume", method: "POST", path: "/api/cron/jobs/{id}/resume", backend: "cron" },
  { family: "flows", action: "trigger", method: "POST", path: "/api/cron/jobs/{id}/trigger", backend: "cron" },
  { family: "flows", action: "blueprints", method: "GET", path: "/api/cron/blueprints", backend: "cron" },
  {
    family: "flows",
    action: "blueprint_instantiate",
    method: "POST",
    path: "/api/cron/blueprints/instantiate",
    backend: "cron",
  },
  // NEEDS-BRIDGE: no backend route serves these yet.
  { family: "flows", action: "hooks", method: "GET", path: "/api/qubit/flows/hooks", backend: "needs-bridge" },
  {
    family: "flows",
    action: "hook_enable",
    method: "POST",
    path: "/api/qubit/flows/hook_enable",
    backend: "needs-bridge",
  },
  {
    family: "flows",
    action: "hook_disable",
    method: "POST",
    path: "/api/qubit/flows/hook_disable",
    backend: "needs-bridge",
  },
  { family: "flows", action: "cancel", method: "POST", path: "/api/qubit/flows/cancel", backend: "needs-bridge" },
  { family: "flows", action: "programs", method: "GET", path: "/api/qubit/flows/programs", backend: "needs-bridge" },
];

export function routePath(action: string): FlowRoute | undefined {
  return ROUTES.find((r) => r.action === action);
}

/** Actions with no backend route — the page renders them disabled-with-reason. */
export function needsBridge(action: string): boolean {
  return routePath(action)?.backend === "needs-bridge";
}

function jobPath(action: "get" | "runs" | "update" | "delete" | "pause" | "resume" | "trigger", id: string): string {
  const route = routePath(action);
  if (!route) throw new Error(`unknown flows route ${action}`);
  return route.path.replace("{id}", encodeURIComponent(id));
}

function post(path: string, body: Record<string, unknown>): Promise<unknown> {
  return fetchJSON<unknown>(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

// ── shapes (defensive: the page normalizes every envelope the backend serves) ──

export type FlowKind = "standing-order" | "one-shot";

export interface FlowRow {
  id: string;
  name: string;
  kind: FlowKind;
  /** Cron expression or run_at the backend will fire on. */
  schedule: string;
  /** The action text the run executes. */
  prompt: string;
  enabled: boolean;
  lastStatus: string;
  /** Untouched backend row — carried so updates can re-send the full mutation. */
  raw: Record<string, unknown>;
}

export interface CreateFlowInput {
  name: string;
  /** Schedule expression (`0 8 * * 1-5`, `every 30m`, …) or `run_at`. */
  schedule: string;
  /** Action the run executes (upstream `--message`). */
  prompt: string;
  deliver?: string;
}

const PAUSED_JOB_STATES = new Set(["paused", "disabled", "stopped"]);

function asString(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

/** `GET /api/cron/jobs` serves a bare array; tolerate `{jobs}`/`{items}` too. */
function asRowList(payload: unknown): unknown[] {
  if (Array.isArray(payload)) return payload;
  const env = asRecord(payload);
  for (const key of ["jobs", "items", "flows"]) {
    if (Array.isArray(env[key])) return env[key] as unknown[];
  }
  return [];
}

export function normalizeFlowRow(job: unknown): FlowRow | null {
  const row = asRecord(job);
  const id = asString(row.id ?? row.job_id).trim() || String(row.id ?? "");
  if (!id) return null;
  const scheduleNode = row.schedule;
  const scheduleRecord = asRecord(scheduleNode);
  const expr =
    asString(scheduleRecord.expr).trim() ||
    asString(scheduleRecord.run_at).trim() ||
    (typeof scheduleNode === "string" ? scheduleNode.trim() : "") ||
    asString(row.schedule_display).trim() ||
    asString(row.when).trim();
  const runAt = asString(scheduleRecord.run_at).trim();
  const state = asString(row.state ?? row.status).trim().toLowerCase();
  const enabled = !(PAUSED_JOB_STATES.has(state) || row.enabled === false || row.paused === true);
  return {
    id,
    name: asString(row.name).trim() || `flow-${id}`,
    kind: runAt ? "one-shot" : "standing-order",
    schedule: expr,
    prompt: asString(row.prompt ?? row.task_text ?? row.message).trim(),
    enabled,
    lastStatus: asString(row.last_status ?? row.last_state).trim(),
    raw: row,
  };
}

// ── reads (no confirm gate) ──────────────────────────────────────────────

export async function listFlows(): Promise<FlowRow[]> {
  const payload = await fetchJSON<unknown>("/api/cron/jobs");
  return asRowList(payload)
    .map(normalizeFlowRow)
    .filter((r): r is FlowRow => r !== null);
}

export async function getFlow(id: string): Promise<FlowRow | null> {
  const payload = await fetchJSON<unknown>(jobPath("get", id));
  return normalizeFlowRow(asRecord(payload).job ?? payload);
}

export async function getFlowRuns(id: string): Promise<unknown[]> {
  const payload = await fetchJSON<unknown>(jobPath("runs", id));
  return asRowList(asRecord(payload).runs ?? payload);
}

// ── mutations (all staged-confirm via runMutationWithConfirm) ────────────

export function createFlow(input: CreateFlowInput, confirm: ConfirmFn): Promise<unknown> {
  return runMutationWithConfirm(
    () =>
      post("/api/cron/jobs", {
        name: input.name.trim(),
        prompt: input.prompt.trim(),
        schedule: input.schedule.trim(),
        deliver: (input.deliver ?? "").trim() || "local",
      }),
    confirm,
  );
}

/**
 * Honest enabled-only toggle. `cron_update` requires the current `enabled`
 * flag plus the full mutation, so the row's own fields ride along unchanged.
 */
export function setFlowEnabled(flow: FlowRow, enabled: boolean, confirm: ConfirmFn): Promise<unknown> {
  const raw = flow.raw;
  const body: Record<string, unknown> = {
    name: asString(raw.name) || flow.name,
    prompt: asString(raw.prompt ?? raw.task_text) || flow.prompt,
    schedule: raw.schedule ?? flow.schedule,
    deliver: asString(raw.deliver) || "local",
    enabled,
  };
  for (const key of ["skills", "provider", "model", "base_url", "script", "workdir", "context_from"]) {
    if (raw[key] !== undefined) body[key] = raw[key];
  }
  return runMutationWithConfirm(
    () =>
      fetchJSON<unknown>(jobPath("update", flow.id), {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      }),
    confirm,
  );
}

export function triggerFlowNow(id: string, confirm: ConfirmFn): Promise<unknown> {
  return runMutationWithConfirm(() => post(jobPath("trigger", id), {}), confirm);
}

export function deleteFlow(id: string, confirm: ConfirmFn): Promise<unknown> {
  return runMutationWithConfirm(
    () => fetchJSON<unknown>(jobPath("delete", id), { method: "DELETE" }),
    confirm,
  );
}

// ── hooks: catalog data only (enable/disable NEEDS-BRIDGE) ──────────────

export interface HookRow {
  name: string;
  events: string[];
  description: string;
}

/**
 * Verbatim from the upstream bundled-hooks table
 * (`docs/automation/hooks.md` → "Bundled hooks"). Client-side data only:
 * Qubit has no backend hooks route, so the page renders enable/disable as
 * disabled-with-reason rows pointing at the NEEDS-BRIDGE routes above.
 */
export const HOOK_CATALOG: ReadonlyArray<HookRow> = [
  {
    name: "session-memory",
    events: ["command:new", "command:reset"],
    description: "Saves session context to <workspace>/memory/",
  },
  {
    name: "bootstrap-extra-files",
    events: ["agent:bootstrap"],
    description: "Injects additional bootstrap files from glob patterns",
  },
  {
    name: "command-logger",
    events: ["command"],
    description: "Logs all commands to the commands log",
  },
  {
    name: "compaction-notifier",
    events: ["session:compact:before", "session:compact:after"],
    description: "Sends visible chat notices when session compaction starts/ends",
  },
  {
    name: "boot-md",
    events: ["gateway:startup"],
    description: "Runs BOOT.md from the active workspace when the gateway starts",
  },
];

export interface DisabledFlowAction {
  action: string;
  code: string;
  reason: string;
  counterpart: string;
}

/**
 * Honest disabled rows. Codes are Qubit-owned labels (no backend emits them —
 * there is no backend route to refuse); counterparts name the real surface.
 */
export const DISABLED_FLOW_ACTIONS: ReadonlyArray<DisabledFlowAction> = [
  {
    action: "flows.hook_enable",
    code: "NEEDS-BRIDGE",
    reason: "No backend hooks route exists yet (/api/qubit/flows/hook_enable).",
    counterpart: "Cron-backed flows below (enable/disable works today)",
  },
  {
    action: "flows.hook_disable",
    code: "NEEDS-BRIDGE",
    reason: "No backend hooks route exists yet (/api/qubit/flows/hook_disable).",
    counterpart: "Cron-backed flows below (enable/disable works today)",
  },
  {
    action: "flows.cancel",
    code: "NEEDS-BRIDGE",
    reason: "Sticky taskflow-cancel intent has no backend route (/api/qubit/flows/cancel).",
    counterpart: "Delete the durable flow (confirm-gated) or pause it",
  },
  {
    action: "flows.suggestions",
    code: "TASK_INBOX_ONLY",
    reason: "No ephemeral-suggestion registry on this page — durable flows only.",
    counterpart: "Ephemeral suggestions live on /qubit-task-inbox",
  },
];
