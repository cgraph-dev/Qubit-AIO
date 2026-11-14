/* Typed client + projection helpers for the P2-07 effective-state panel.
 *
 * The panel renders only what the backend projects from the real stores. A
 * missing/refused read is a named `unavailable`/`error` state; it is never
 * converted into an empty "allowed" list. Statuses come from the projection,
 * never from a seat sprite or an optimistic local guess.
 */
import {
  BusinessApiError,
  businessFetch,
} from "./business-api";

export interface EffectiveKnown<T = unknown> {
  value: T | null;
  present: boolean;
  label: string;
  source: string;
  reason: string | null;
}

export interface EffectiveStatus {
  value: string;
  label: string;
  record_status: string;
  reason: string | null;
  connect?: EffectiveKnown<unknown>;
}

export interface EffectiveGrantRow {
  capability?: string;
  resource?: string;
  action?: string;
  label?: string;
  source?: string;
  grant_id?: string;
  identity_reasons?: string[];
  [key: string]: unknown;
}

export interface EffectiveProjection {
  schema: string;
  tenant_id: string;
  agent_id: string;
  business_id: string;
  generated_at: string;
  agent: { profile: EffectiveKnown<{ display_name?: string }> };
  status: EffectiveStatus;
  identity: {
    explicit: EffectiveGrantRow[];
    inherited: EffectiveGrantRow[];
    revoked: EffectiveGrantRow[];
    expired: EffectiveGrantRow[];
  };
  capabilities: {
    effective: EffectiveGrantRow[];
    denied: EffectiveGrantRow[];
    profile_applied: EffectiveGrantRow[];
    profile_denied: EffectiveGrantRow[];
    revoked: EffectiveGrantRow[];
    reason: string | null;
  };
  model: {
    primary: EffectiveKnown<{ route?: string; provider?: string; model?: string }>;
    fallback: EffectiveKnown<{ route?: string; provider?: string; model?: string }>;
  };
  memory: { scopes?: unknown[]; reason?: string | null };
  leases: {
    active: EffectiveGrantRow[];
    expired: EffectiveGrantRow[];
    released: EffectiveGrantRow[];
    reason: string | null;
  };
  budget: {
    currency?: string;
    day_spend_minor?: number;
    month_spend_minor?: number;
    limits?: Record<string, number | null>;
    remaining?: Record<string, number | null>;
  };
  approvals: {
    pending: EffectiveGrantRow[];
    expired: EffectiveGrantRow[];
    unlinked_pending?: number;
  };
  audit: { rows?: EffectiveGrantRow[]; total?: number; reason?: string | null };
  links: Record<string, unknown>;
  policy: Record<string, unknown>;
}

export type EffectiveRead =
  | { kind: "loading" }
  | { kind: "available"; projection: EffectiveProjection }
  | { kind: "unavailable"; code: string; detail: string }
  | { kind: "error"; message: string };

export interface EffectiveFetchOptions {
  agentId: string;
  base?: string;
  fetchImpl?: typeof fetch;
  signal?: AbortSignal;
  tenantId?: string;
}

export async function fetchEffectiveState(
  opts: EffectiveFetchOptions,
): Promise<EffectiveRead> {
  const agentId = String(opts.agentId || "").trim();
  if (!agentId) {
    return { kind: "unavailable", code: "AGENT_ID_REQUIRED",
      detail: "no agent id was supplied" };
  }
  try {
    const body = await businessFetch<{
      ok?: boolean;
      projection?: EffectiveProjection;
    }>(`/agents/${encodeURIComponent(agentId)}/effective`, {
      base: opts.base,
      fetchImpl: opts.fetchImpl,
      signal: opts.signal,
      query: opts.tenantId ? { tenant_id: opts.tenantId } : undefined,
    });
    if (!body || !body.projection) {
      return { kind: "error", message: "the backend returned no projection" };
    }
    return { kind: "available", projection: body.projection };
  } catch (exc) {
    if (exc instanceof BusinessApiError) {
      return {
        kind: "unavailable",
        code: exc.code || `HTTP_${exc.status}`,
        detail: exc.message,
      };
    }
    return {
      kind: "error",
      message: exc instanceof Error ? exc.message : String(exc),
    };
  }
}

export function statusLabel(projection: EffectiveProjection): string {
  return String(projection.status?.label || projection.status?.value
    || "unknown");
}

export function grantLabel(row: EffectiveGrantRow): string {
  const capability = String(row.capability || "unknown");
  const resource = row.resource ? ` ${row.resource}` : "";
  const action = row.action ? `:${row.action}` : "";
  return `${capability}${resource}${action}`;
}

export function grantState(row: EffectiveGrantRow, fallback: string): string {
  return String(row.label || fallback);
}

export function formatMinor(value: unknown, currency = "USD"): string {
  const amount = typeof value === "number" && Number.isFinite(value) ? value : null;
  if (amount === null) return "unknown";
  return `${(amount / 100).toFixed(2)} ${currency}`;
}

export function modelRoute(known: EffectiveKnown<{ route?: string }> | undefined): string {
  if (!known || !known.present || !known.value) {
    return `unknown${known?.reason ? ` (${known.reason})` : ""}`;
  }
  return String(known.value.route || "unknown");
}

export function leaseCounts(projection: EffectiveProjection): {
  active: number; expired: number; released: number;
} {
  return {
    active: projection.leases?.active?.length ?? 0,
    expired: projection.leases?.expired?.length ?? 0,
    released: projection.leases?.released?.length ?? 0,
  };
}
