/* Typed client for the Qubit Business OS (War Room / roles / agent controls).
 *
 * ONE DOOR
 * --------
 * Every read and write goes through the office compat family
 * (`/agent/api/office/business/...`), the same session-token-gated backend the
 * Office shell already uses. This module owns the URL shape, the token lookup,
 * the owner intent on mutations, and the typed refusal envelope. It never
 * invents a result: a non-2xx or `ok:false` answer throws `BusinessApiError`,
 * so a panel renders the real reason instead of a fake success.
 *
 * WHAT IT NEVER DOES
 * ------------------
 * No publish, spend, contact, or external API call is exposed here. Mutations
 * carry `owner: true` because the backend refuses them otherwise; the
 * dashboard session token is the owner credential.
 */
import { readToken } from "./agents-panel";

/** The compat family prefix (the SPA reaches it under `/agent`). */
export const API_PREFIX = "/agent";
export const BUSINESS_PATH = "/api/office/business";

export type BusinessMethod = "GET" | "POST";

export interface BusinessErrorBody {
  ok?: boolean;
  code?: string;
  detail?: string;
  state?: string;
  explanation?: string;
}

export class BusinessApiError extends Error {
  readonly code: string;
  readonly status: number;
  readonly body: BusinessErrorBody | null;

  constructor(message: string, code: string, status: number,
              body: BusinessErrorBody | null = null) {
    super(message);
    this.name = "BusinessApiError";
    this.code = code;
    this.status = status;
    this.body = body;
  }
}

export function hostBasePath(): string {
  if (typeof window === "undefined") return "";
  const raw = (window as { __QUBIT_BASE_PATH__?: string }).__QUBIT_BASE_PATH__;
  if (!raw) return "";
  const withLead = raw.startsWith("/") ? raw : `/${raw}`;
  return withLead.replace(/\/+$/, "");
}

/** Build a Business OS URL, rebased onto the dashboard's base path. */
export function businessUrl(path: string, base?: string,
                            query?: Record<string, string | undefined>): string {
  const raw = base === undefined ? hostBasePath() : base;
  const b = raw.endsWith("/") ? raw.slice(0, -1) : raw;
  const clean = path.startsWith("/") ? path : `/${path}`;
  let url = `${b}${API_PREFIX}${BUSINESS_PATH}${clean}`;
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query ?? {})) {
    if (value !== undefined && value !== null && String(value) !== "") {
      params.set(key, String(value));
    }
  }
  const suffix = params.toString();
  return suffix ? `${url}?${suffix}` : url;
}

/** A fresh idempotency key for one user action (double-click safe). */
export function newIdempotencyKey(action: string): string {
  const rand = typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `ui:${action}:${rand}`;
}

export interface BusinessFetchOptions {
  method?: BusinessMethod;
  body?: Record<string, unknown>;
  base?: string;
  fetchImpl?: typeof fetch;
  win?: Window | null;
  signal?: AbortSignal;
  query?: Record<string, string | undefined>;
}

/** Fetch one Business OS route and return its `ok:true` payload.
 *
 * Throws `BusinessApiError` for an auth failure, a non-JSON body, a typed
 * refusal, or any HTTP failure. The panel decides what to render; nothing here
 * converts an error into an empty/successful state.
 */
export async function businessFetch<T>(
  path: string, opts: BusinessFetchOptions = {},
): Promise<T> {
  const f = opts.fetchImpl ?? fetch;
  const method: BusinessMethod = opts.method ?? "GET";
  const url = businessUrl(path, opts.base, opts.query);
  const headers: Record<string, string> = { Accept: "application/json" };
  const token = readToken(opts.win);
  if (token) headers["X-Qubit-Session-Token"] = token;
  const init: RequestInit = {
    method,
    headers,
    credentials: "same-origin",
    signal: opts.signal,
  };
  if (method === "POST") {
    headers["Content-Type"] = "application/json";
    const body: Record<string, unknown> = { ...(opts.body ?? {}) };
    // The backend refuses a mutation without explicit owner intent. Adding it
    // here keeps every panel action correct without each caller remembering.
    if (body.owner === undefined) body.owner = true;
    init.body = JSON.stringify(body);
  }
  let res: Response;
  try {
    res = await f(url, init);
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    throw new BusinessApiError(
      `The Business OS could not be reached: ${msg}`,
      "BUSINESS_UNREACHABLE", 0);
  }
  const parsed = (await res.json().catch(() => null)) as
    | (T & BusinessErrorBody)
    | null;
  if (res.status === 401) {
    throw new BusinessApiError(
      "The Business OS refused the request (HTTP 401); the office session "
      + "token was not accepted. Reload the page to refresh it.",
      "BUSINESS_UNAUTHORIZED", 401, parsed);
  }
  if (!parsed || typeof parsed !== "object") {
    throw new BusinessApiError(
      `The Business OS answered HTTP ${res.status} with no JSON body.`,
      "BUSINESS_BAD_RESPONSE", res.status);
  }
  if (!res.ok || parsed.ok !== true) {
    const code = String(parsed.code || `BUSINESS_HTTP_${res.status}`);
    const detail = String(parsed.detail || parsed.explanation
      || `HTTP ${res.status}`);
    throw new BusinessApiError(detail, code, res.status, parsed);
  }
  return parsed as T;
}

// ---------------------------------------------------------------------------
// Payload shapes
// ---------------------------------------------------------------------------
export interface BusinessView {
  business_id: string;
  name: string;
  slug: string;
  status: string;
  currency: string;
}

export interface TenantView {
  tenant_id: string;
  name: string;
  created_at: string;
  businesses: BusinessView[];
}

export interface BusinessOverview {
  ok: true;
  schema: string;
  tenants: TenantView[];
  default_tenant_id: string | null;
  default_business_id: string | null;
  counts: { tenants: number };
}

export interface OfficeSeatBootstrap {
  ok: true;
  schema: string;
  tenant_id: string;
  business_id: string;
  created_tenant: boolean;
  created_business: boolean;
  plan_ok: boolean;
  total: number;
  created: number;
  existing: number;
  created_slugs: string[];
}

export interface WarRoomSession {
  session_id: string;
  tenant_id: string;
  business_id: string | null;
  title: string;
  state: string;
  brief: Record<string, unknown>;
  score: Record<string, unknown>;
  dissent: Array<Record<string, unknown>>;
  owner_note: string;
  created_at: string;
  updated_at: string;
}

export interface WarRoomSource {
  source_id: string;
  session_id: string;
  kind: string;
  locator: string;
  title: string;
  status: string;
  adapter: string;
  artifact_id: string | null;
  text: string | null;
  metadata: Record<string, unknown>;
  created_at: string;
}

export interface WarRoomEvidence {
  evidence_id: string;
  session_id: string;
  label: "FACT" | "INFERENCE" | "ASSUMPTION" | "UNKNOWN" | string;
  claim: string;
  source_id: string | null;
  quote: string;
  metadata: Record<string, unknown>;
  created_at: string;
}

export interface WarRoomArgument {
  argument_id: string;
  session_id: string;
  round_no: number;
  role: string;
  stance: string;
  text: string;
  evidence_ids: string[];
  created_at: string;
}

export interface WarRoomExperiment {
  experiment_id: string;
  session_id: string;
  name: string;
  hypothesis: string;
  method: string;
  success_metric: string;
  status: string;
  created_at: string;
}

export interface WarRoomDecision {
  decision_id: string;
  session_id: string;
  status: string;
  decided_by: string;
  note: string;
  created_at: string;
}

export interface BusinessTask {
  task_id: string;
  tenant_id: string;
  business_id: string;
  title: string;
  status: string;
  assigned_to: string | null;
  created_at: string;
  updated_at: string;
  metadata: Record<string, unknown>;
}

export interface WarRoomBundle {
  ok: true;
  schema: string;
  tenant_id: string;
  session: WarRoomSession;
  sources: WarRoomSource[];
  evidence: WarRoomEvidence[];
  arguments: WarRoomArgument[];
  experiments: WarRoomExperiment[];
  decisions: WarRoomDecision[];
  tasks: BusinessTask[];
}

export interface AuditEvent {
  event_id: string;
  tenant_id: string;
  business_id: string | null;
  actor: string;
  action: string;
  resource_type: string;
  resource_id: string | null;
  outcome: string;
  detail: Record<string, unknown>;
  created_at: string;
}

export interface AgentSafety {
  autonomy: "L0" | "L1" | "L2" | "L3" | "L4" | "L5" | string;
  daily_spend_minor: number;
  monthly_spend_minor: number;
  per_action_limit_minor: number;
  max_turns_per_day: number;
  max_tokens_per_turn: number;
  rate_limit_per_minute: number;
  approval_required: string[];
  escalation_target: string;
}

export interface AgentPermissions {
  tools: string[];
  toolsets: string[];
  files: string[];
  browser: string[];
  network: string[];
  terminal: string[];
  email: string[];
  social: string[];
  etsy: string[];
  payments: string[];
  data: string[];
  [category: string]: string[];
}

export interface AgentRecord {
  agent_id: string;
  tenant_id: string;
  business_id: string;
  role_id: string;
  department: string;
  display_name: string;
  human_owner: string;
  room: string | null;
  desk: string | null;
  status: string;
  provider: string;
  model: string;
  reasoning_effort: string;
  fallback_model: string | null;
  fallback_provider: string | null;
  temperature: number | null;
  max_tokens: number;
  schedule: string;
  timezone: string;
  max_concurrent_turns: number;
  permissions: AgentPermissions;
  safety: AgentSafety;
  metadata: Record<string, unknown>;
  taken_over_by: string | null;
  taken_over_at: string | null;
  revision: number;
  created_at: string;
  updated_at: string;
  archived_at: string | null;
}

export interface AgentDetail {
  ok: true;
  schema: string;
  tenant_id: string;
  agent: AgentRecord;
  audit: AuditEvent[];
}

export interface RoleTemplate {
  id: string;
  department: string;
  title: string;
  mission: string;
  responsibilities: string[];
  skills: string[];
  toolsets: string[];
  data_scopes: string[];
  permissions: string[];
  budget: Record<string, number | string>;
  autonomy: string;
  approvals: string[];
  schedule: string;
  kpis: string[];
  escalation: string;
  model: Record<string, string>;
  sop_refs: string[];
  memory_refs: string[];
  human_owner: string;
  service_lines: string[];
  active_by_default: boolean;
}

export interface FloorTemplate {
  floor_id: string;
  index: number;
  wing: string;
  name: string;
  department_ids: string[];
  role_ids: string[];
  room_count: number;
  desk_capacity: number;
  active_by_default: boolean;
  max_rooms: number | null;
  max_agents: number | null;
  expansion_allowed: boolean;
}

export interface DepartmentTemplate {
  id: string;
  title: string;
  description: string;
  service_lines: string[];
}

export interface BusinessDirectory {
  ok: true;
  schema: string;
  departments: DepartmentTemplate[];
  roles: RoleTemplate[];
  floors: FloorTemplate[];
  counts: {
    departments: number;
    roles: number;
    floors: number;
    active_floors: number;
    dormant_floors: number;
    wings: number;
  };
}

// ---------------------------------------------------------------------------
// Reads
// ---------------------------------------------------------------------------
export const fetchOverview = (opts: BusinessFetchOptions = {}) =>
  businessFetch<BusinessOverview>("/overview", opts);

/** Create the local Personal scope and import the office seat roster once.
 *
 * Idempotent: a repeat creates zero records. Owner intent is added by
 * `businessFetch`; the backend refuses without it.
 */
export const bootstrapOfficeSeats = (
  body: { idempotency_key?: string } = {},
  opts: BusinessFetchOptions = {},
) => businessFetch<OfficeSeatBootstrap>(
  "/bootstrap", { ...opts, method: "POST", body });

export const fetchDirectory = (opts: BusinessFetchOptions = {}) =>
  businessFetch<BusinessDirectory>("/directory", opts);

export const fetchWarRoomSessions = (
  query: Record<string, string | undefined> = {},
  opts: BusinessFetchOptions = {},
) => businessFetch<{ ok: true; tenant_id: string; sessions: WarRoomSession[] }>(
  "/warroom/sessions", { ...opts, query: { ...query, ...opts.query } });

export const fetchWarRoomBundle = (
  sessionId: string, query: Record<string, string | undefined> = {},
  opts: BusinessFetchOptions = {},
) => businessFetch<WarRoomBundle>(
  `/warroom/sessions/${encodeURIComponent(sessionId)}`,
  { ...opts, query: { ...query, ...opts.query } });

export const fetchAgents = (
  query: Record<string, string | undefined> = {},
  opts: BusinessFetchOptions = {},
) => businessFetch<{ ok: true; tenant_id: string; agents: AgentRecord[] }>(
  "/agents", { ...opts, query: { ...query, ...opts.query } });

export const fetchAgent = (
  agentId: string, query: Record<string, string | undefined> = {},
  opts: BusinessFetchOptions = {},
) => businessFetch<AgentDetail>(
  `/agents/${encodeURIComponent(agentId)}`,
  { ...opts, query: { ...query, ...opts.query } });

export const fetchAgentAudit = (
  agentId: string, query: Record<string, string | undefined> = {},
  opts: BusinessFetchOptions = {},
) => businessFetch<{ ok: true; tenant_id: string; agent_id: string;
                   audit: AuditEvent[] }>(
  `/agents/${encodeURIComponent(agentId)}/audit`,
  { ...opts, query: { ...query, ...opts.query } });

// ---------------------------------------------------------------------------
// War Room mutations
// ---------------------------------------------------------------------------
export const createWarRoomSession = (
  body: { title: string; tenant_id?: string; business_id?: string;
          idempotency_key?: string },
  opts: BusinessFetchOptions = {},
) => businessFetch<{ ok: true; created: boolean; tenant_id: string;
                    session: WarRoomSession }>(
  "/warroom/sessions", { ...opts, method: "POST", body });

export const addWarRoomSource = (
  sessionId: string,
  body: { kind: string; locator: string; title?: string;
          metadata?: Record<string, unknown> },
  opts: BusinessFetchOptions = {},
) => businessFetch<{ ok: true; created: boolean; source: WarRoomSource }>(
  `/warroom/sessions/${encodeURIComponent(sessionId)}/sources`,
  { ...opts, method: "POST", body });

export const addWarRoomEvidence = (
  sessionId: string,
  body: { label: string; claim: string; source_id?: string; quote?: string;
          metadata?: Record<string, unknown> },
  opts: BusinessFetchOptions = {},
) => businessFetch<{ ok: true; created: boolean; evidence: WarRoomEvidence }>(
  `/warroom/sessions/${encodeURIComponent(sessionId)}/evidence`,
  { ...opts, method: "POST", body });

export const addWarRoomArgument = (
  sessionId: string,
  body: { round_no?: number; role: string; stance: string; text: string;
          evidence_ids?: string[] },
  opts: BusinessFetchOptions = {},
) => businessFetch<{ ok: true; created: boolean;
                    argument: WarRoomArgument }>(
  `/warroom/sessions/${encodeURIComponent(sessionId)}/arguments`,
  { ...opts, method: "POST", body });

export const recordWarRoomDecision = (
  sessionId: string,
  body: { decision: "approved" | "rejected"; note?: string;
          idempotency_key?: string },
  opts: BusinessFetchOptions = {},
) => businessFetch<{ ok: true; created: boolean;
                    decision: WarRoomDecision }>(
  `/warroom/sessions/${encodeURIComponent(sessionId)}/decisions`,
  { ...opts, method: "POST", body });

export const createWarRoomTasks = (
  sessionId: string, opts: BusinessFetchOptions = {},
) => businessFetch<{ ok: true; tasks: BusinessTask[] }>(
  `/warroom/sessions/${encodeURIComponent(sessionId)}/tasks`,
  { ...opts, method: "POST", body: {} });

// ---------------------------------------------------------------------------
// Agent mutations
// ---------------------------------------------------------------------------
type AgentResult = { ok: true; agent_id: string; result: AgentRecord };
type TurnResult = { ok: true; agent_id: string;
                    result: Record<string, unknown> };

export const updateAgent = (
  agentId: string, changes: Record<string, unknown>,
  opts: BusinessFetchOptions = {},
) => businessFetch<{ ok: true; tenant_id: string; agent: AgentRecord }>(
  `/agents/${encodeURIComponent(agentId)}/update`,
  { ...opts, method: "POST", body: { changes } });

export const pauseAgent = (agentId: string, opts: BusinessFetchOptions = {}) =>
  businessFetch<AgentResult>(`/agents/${encodeURIComponent(agentId)}/pause`,
                             { ...opts, method: "POST" });

export const resumeAgent = (agentId: string, opts: BusinessFetchOptions = {}) =>
  businessFetch<AgentResult>(`/agents/${encodeURIComponent(agentId)}/resume`,
                             { ...opts, method: "POST" });

export const stopAgent = (
  agentId: string, body: { turn_id?: string; note?: string } = {},
  opts: BusinessFetchOptions = {},
) => businessFetch<TurnResult>(`/agents/${encodeURIComponent(agentId)}/stop`,
                               { ...opts, method: "POST", body });

export const takeoverAgent = (
  agentId: string, opts: BusinessFetchOptions = {},
) => businessFetch<AgentResult>(`/agents/${encodeURIComponent(agentId)}/takeover`,
                                { ...opts, method: "POST" });

export const revokeAgent = (
  agentId: string, scopes: string[] | undefined,
  opts: BusinessFetchOptions = {},
) => businessFetch<AgentResult>(`/agents/${encodeURIComponent(agentId)}/revoke`,
                                { ...opts, method: "POST", body: { scopes } });

export const archiveAgent = (
  agentId: string, opts: BusinessFetchOptions = {},
) => businessFetch<AgentResult>(`/agents/${encodeURIComponent(agentId)}/archive`,
                                { ...opts, method: "POST" });
