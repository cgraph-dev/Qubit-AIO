/* Typed client + causal helpers for the P4-07 operator timeline.
 *
 * The timeline is a read-only projection over the durable Business OS:
 * events, jobs/runs, checkpoints, approvals, artifacts, ledger receipts and
 * tool calls. This module owns the one compat route, the local causal sort,
 * filters, cost/evidence/unknown helpers and the replay label.
 *
 * It never upgrades a state. A `completed` status is only shown as complete
 * when the item carries an evidence reference; an `unknown` or `pending` item
 * stays unknown/pending, and a replay is labelled as a simulation with stubbed
 * effects. All reads go through `businessFetch` (the one office compat door).
 */
import {
  BusinessApiError,
  businessFetch,
} from "./business-api";

/** The one office compat route this module reads. */
export const TIMELINE_PATH = "/operator/timeline";
export const TIMELINE_SCHEMA = "qubit-office-operator-timeline/1";

export const TIMELINE_KINDS = [
  "run", "task", "tool", "event", "approval", "checkpoint", "artifact",
  "receipt", "reconciliation",
] as const;

export type TimelineKind = (typeof TIMELINE_KINDS)[number];

export const KIND_LABELS: Record<TimelineKind, string> = {
  run: "Run",
  task: "Task",
  tool: "Tool",
  event: "Event",
  approval: "Approval",
  checkpoint: "Checkpoint",
  artifact: "Artifact",
  receipt: "Receipt",
  reconciliation: "Reconciliation",
};

/** Causal stage order: run -> task -> tool -> approval -> artifact/receipt. */
const STAGE_RANK: Record<TimelineKind, number> = {
  run: 0,
  task: 1,
  tool: 2,
  event: 3,
  approval: 4,
  checkpoint: 5,
  artifact: 6,
  receipt: 7,
  reconciliation: 8,
};

export interface TimelineEvidenceRef {
  kind: string;
  id: string;
  item_id?: string | null;
}

export interface TimelineCost {
  amount_minor: number;
  currency: string;
  direction?: string;
}

export interface TimelineTokens {
  prompt: number;
  completion: number;
  total: number;
}

export interface TimelineItem {
  item_id: string;
  kind: TimelineKind;
  at: string;
  status: string;
  claimed_status: string | null;
  title: string;
  summary: string;
  actor: string;
  source: string;
  run_id: string | null;
  task_id: string | null;
  agent_id: string | null;
  session_id: string | null;
  approval_id: string | null;
  artifact_id: string | null;
  ledger_id: string | null;
  checkpoint_id: string | null;
  correlation_id: string | null;
  causation_id: string | null;
  sequence: number | null;
  evidence: boolean;
  evidence_refs: TimelineEvidenceRef[];
  completed: boolean;
  unknown: boolean;
  pending: boolean;
  reason: string | null;
  cost: TimelineCost | null;
  tokens: TimelineTokens | null;
  replay?: boolean;
  effect?: string | null;
  simulated?: boolean;
  detail?: Record<string, unknown>;
}

export interface TimelineCostSummary {
  debit_minor: number;
  credit_minor: number;
  net_minor: number;
  entries: number;
  by_currency: Record<string, {
    debit_minor: number; credit_minor: number; entries: number;
  }>;
  source: string;
}

export interface TimelineCounts {
  total: number;
  runs: number;
  events: number;
  tasks: number;
  tools: number;
  approvals: number;
  artifacts: number;
  receipts: number;
  checkpoints: number;
  reconciliations: number;
  completed: number;
  completed_with_evidence: number;
  pending: number;
  unknown: number;
  replay: number;
  matched: number;
  returned: number;
  truncated: boolean;
}

export interface TimelineSource {
  state: "ok" | "empty" | "unavailable";
  count: number;
  code: string | null;
  reason: string | null;
}

export interface TimelineProjection {
  schema: string;
  tenant_id: string;
  generated_at: string;
  mode: "live" | "replay";
  state: "ok" | "empty" | "partial";
  query: {
    run: string | null;
    task: string | null;
    agent: string | null;
    session: string | null;
    limit: number;
    replay: boolean;
    filtered: boolean;
  };
  counts: TimelineCounts;
  cost: TimelineCostSummary;
  tokens: TimelineTokens & { runs: number };
  items: TimelineItem[];
  sources: Record<string, TimelineSource>;
  replay: {
    enabled: boolean;
    effects: "stubbed" | "none";
    real_calls: boolean;
    label: string;
    note: string;
  };
  unavailable_sources: string[];
}

export interface TimelinePayload {
  ok: true;
  schema: string;
  projection: TimelineProjection;
}

export type TimelineRead =
  | { kind: "loading" }
  | { kind: "available"; projection: TimelineProjection }
  | { kind: "empty"; projection: TimelineProjection }
  | { kind: "unavailable"; code: string; detail: string }
  | { kind: "error"; message: string };

export interface TimelineQuery {
  run?: string | null;
  task?: string | null;
  agent?: string | null;
  session?: string | null;
  limit?: number | null;
  replay?: boolean;
}

export interface TimelineFetchOptions extends TimelineQuery {
  base?: string;
  fetchImpl?: typeof fetch;
  win?: Window | null;
  signal?: AbortSignal;
}

/** Only non-empty filters are sent; the backend owns matching and counts. */
export function cleanQuery(
  query: TimelineQuery = {},
): Record<string, string | undefined> {
  const out: Record<string, string | undefined> = {};
  const text = (value: unknown) => String(value ?? "").trim();
  if (text(query.run)) out.run = text(query.run);
  if (text(query.task)) out.task = text(query.task);
  if (text(query.agent)) out.agent = text(query.agent);
  if (text(query.session)) out.session = text(query.session);
  if (query.limit !== undefined && query.limit !== null
      && Number.isFinite(Number(query.limit))) {
    out.limit = String(Math.max(1, Math.trunc(Number(query.limit))));
  }
  if (query.replay) out.replay = "1";
  return out;
}

export async function fetchTimeline(
  opts: TimelineFetchOptions = {},
): Promise<TimelineRead> {
  try {
    const body = await businessFetch<TimelinePayload>(TIMELINE_PATH, {
      base: opts.base,
      fetchImpl: opts.fetchImpl,
      win: opts.win,
      signal: opts.signal,
      query: cleanQuery(opts),
    });
    if (!body || !body.projection) {
      return { kind: "error", message: "the backend returned no projection" };
    }
    const projection = body.projection;
    return projection.state === "empty" && projection.items.length === 0
      ? { kind: "empty", projection }
      : { kind: "available", projection };
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

// ---------------------------------------------------------------------------
// Causal ordering (same contract as the backend)
// ---------------------------------------------------------------------------
const stage = (kind: string): number =>
  STAGE_RANK[kind as TimelineKind] ?? TIMELINE_KINDS.length;

const num = (value: unknown): number => {
  const parsed = Number(value ?? 0);
  return Number.isFinite(parsed) ? parsed : 0;
};

/**
 * Sort chronologically, then enforce causation: a caused item never appears
 * before its cause. A cycle falls back to the chronological order instead of
 * dropping an item.
 */
export function causalSort(items: readonly TimelineItem[]): TimelineItem[] {
  const ordered = [...items].sort((a, b) => {
    if (a.at !== b.at) return a.at < b.at ? -1 : 1;
    if (stage(a.kind) !== stage(b.kind)) return stage(a.kind) - stage(b.kind);
    if (num(a.sequence) !== num(b.sequence)) return num(a.sequence) - num(b.sequence);
    return a.item_id < b.item_id ? -1 : a.item_id > b.item_id ? 1 : 0;
  });
  if (ordered.length < 2) return ordered;
  const byId = new Map(ordered.map((item) => [item.item_id, item]));
  const position = new Map(ordered.map((item, index) => [item.item_id, index]));
  const children = new Map<string, string[]>();
  const indegree = new Map<string, number>();
  for (const item of ordered) indegree.set(item.item_id, 0);
  for (const item of ordered) {
    const cause = String(item.causation_id ?? "").trim();
    if (!cause || cause === item.item_id || !byId.has(cause)) continue;
    const list = children.get(cause) ?? [];
    list.push(item.item_id);
    children.set(cause, list);
    indegree.set(item.item_id, (indegree.get(item.item_id) ?? 0) + 1);
  }
  // Stable Kahn walk using the chronological position as the priority.
  const ready = ordered
    .filter((item) => (indegree.get(item.item_id) ?? 0) === 0)
    .map((item) => item.item_id);
  const result: TimelineItem[] = [];
  while (ready.length > 0) {
    ready.sort((a, b) => (position.get(a) ?? 0) - (position.get(b) ?? 0));
    const next = ready.shift() as string;
    result.push(byId.get(next) as TimelineItem);
    for (const child of children.get(next) ?? []) {
      indegree.set(child, (indegree.get(child) ?? 1) - 1);
      if ((indegree.get(child) ?? 0) === 0) ready.push(child);
    }
  }
  return result.length === ordered.length ? result : ordered;
}

// ---------------------------------------------------------------------------
// Filters (same rules as the backend, for instant feedback)
// ---------------------------------------------------------------------------
export interface TimelineFilter {
  q?: string | null;
  kind?: TimelineKind | "all" | null;
  status?: string | null;
}

const norm = (value: unknown) => String(value ?? "").trim().toLowerCase();

function haystack(item: TimelineItem): string {
  return [
    item.item_id, item.kind, item.title, item.summary, item.status,
    item.actor, item.run_id, item.task_id, item.agent_id, item.session_id,
    item.approval_id, item.artifact_id, item.ledger_id, item.checkpoint_id,
  ].map(norm).filter(Boolean).join(" ");
}

export function itemMatches(
  item: TimelineItem, filter: TimelineFilter = {},
): boolean {
  const needle = norm(filter.q);
  if (needle && !haystack(item).includes(needle)) return false;
  const kind = norm(filter.kind);
  if (kind && kind !== "all" && item.kind !== kind) return false;
  if (filter.status && norm(item.status) !== norm(filter.status)) return false;
  return true;
}

export function filterTimeline(
  items: readonly TimelineItem[] | undefined,
  filter: TimelineFilter = {},
): TimelineItem[] {
  return (items ?? []).filter((item) => itemMatches(item, filter));
}

export function statusOptions(
  items: readonly TimelineItem[] | undefined,
): string[] {
  const seen = new Set<string>();
  for (const item of items ?? []) {
    const value = String(item.status ?? "").trim();
    if (value) seen.add(value);
  }
  return Array.from(seen).sort();
}

// ---------------------------------------------------------------------------
// No-fake-state helpers
// ---------------------------------------------------------------------------
export function statusLabel(status: unknown): string {
  const value = String(status ?? "").trim();
  return value || "unknown";
}

/** Text marker so status is never conveyed by color alone. */
export function statusMarker(status: unknown): string {
  switch (norm(status)) {
    case "completed":
      return "[v]";
    case "pending":
    case "queued":
    case "waiting":
    case "approval":
      return "[..]";
    case "executing":
    case "running":
    case "leased":
      return "[>]";
    case "checkpointed":
      return "[#]";
    case "unknown":
    case "done-unverified":
      return "[?]";
    case "failed":
      return "[x]";
    case "cancelled":
    case "rejected":
    case "expired":
      return "[-]";
    case "committed":
    case "approved":
      return "[$]";
    case "recorded":
    case "success":
      return "[.]";
    case "simulated":
    case "dry_run":
      return "[~]";
    default:
      return "[?]";
  }
}

export function kindLabel(kind: string): string {
  return KIND_LABELS[kind as TimelineKind] ?? String(kind || "Record");
}

/**
 * The status the operator may trust for this item.
 *
 * A `completed` string without an evidence reference is NOT completion: it is
 * reported as `unknown` so no badge can upgrade it. This mirrors the backend
 * and is the client half of "no fake completion".
 */
export function effectiveStatus(item: TimelineItem): string {
  const status = statusLabel(item.status);
  if (norm(status) === "completed" && !item.evidence) return "unknown";
  return status;
}

export function isCompleted(item: TimelineItem): boolean {
  return item.completed === true
    && item.evidence === true
    && norm(effectiveStatus(item)) === "completed";
}

export function isUnknown(item: TimelineItem): boolean {
  return item.unknown === true || norm(effectiveStatus(item)) === "unknown";
}

export function isPending(item: TimelineItem): boolean {
  return item.pending === true;
}

export function isReplayItem(item: TimelineItem): boolean {
  return item.replay === true || item.effect === "stubbed";
}

export function hasEvidence(item: TimelineItem): boolean {
  return item.evidence === true && (item.evidence_refs?.length ?? 0) > 0;
}

export function evidenceRefs(item: TimelineItem): TimelineEvidenceRef[] {
  return Array.isArray(item.evidence_refs) ? item.evidence_refs : [];
}

/** A stable DOM id for an item id (ids contain `:`). */
export function domId(value: string): string {
  return `tl-${String(value).replace(/[^A-Za-z0-9_-]+/g, "-")}`;
}

/** An in-page anchor to a referenced record when it is on this timeline. */
export function evidenceHref(
  ref: TimelineEvidenceRef, items: readonly TimelineItem[],
): string | null {
  const target = ref.item_id || (ref.kind && ref.id ? `${ref.kind}:${ref.id}` : "");
  if (!target) return null;
  return items.some((item) => item.item_id === target) ? `#${domId(target)}` : null;
}

// ---------------------------------------------------------------------------
// Cost / tokens / replay labels
// ---------------------------------------------------------------------------
export function formatMinor(amountMinor: unknown, currency?: string | null): string {
  const value = Number(amountMinor ?? 0);
  const amount = Number.isFinite(value) ? value : 0;
  const code = (currency || "USD").toUpperCase();
  const sign = amount < 0 ? "-" : "";
  const whole = Math.floor(Math.abs(amount) / 100);
  const cents = String(Math.abs(amount) % 100).padStart(2, "0");
  return `${sign}${whole}.${cents} ${code}`;
}

export function costLabel(cost: TimelineCost | null | undefined): string {
  if (!cost) return "cost unknown";
  return formatMinor(cost.amount_minor, cost.currency);
}

export function tokensLabel(tokens: TimelineTokens | null | undefined): string {
  if (!tokens) return "tokens unknown";
  return `${tokens.total} tokens (${tokens.prompt} in / ${tokens.completion} out)`;
}

export function costSummaryLabel(
  projection: TimelineProjection | null | undefined,
): string {
  const cost = projection?.cost;
  if (!cost) return "Cost unavailable.";
  return `${formatMinor(cost.debit_minor, "USD")} debit · `
    + `${formatMinor(cost.credit_minor, "USD")} credit · `
    + `net ${formatMinor(cost.net_minor, "USD")}`;
}

export function replayLabel(
  projection: TimelineProjection | null | undefined,
): string | null {
  if (!projection?.replay?.enabled) return null;
  return projection.replay.label
    || "Replay (simulated): effects stubbed, no real action performed";
}

export function isReplay(projection: TimelineProjection | null | undefined): boolean {
  return projection?.mode === "replay" || projection?.replay?.enabled === true;
}

export function countsSummary(
  projection: TimelineProjection | null | undefined,
): string {
  if (!projection) return "No timeline projection.";
  const counts = projection.counts;
  const total = Number(counts?.total ?? projection.items?.length ?? 0);
  if (total === 0) return "No durable records match this timeline.";
  return [
    `${total} item${total === 1 ? "" : "s"}`,
    `${counts.runs} run${counts.runs === 1 ? "" : "s"}`,
    `${counts.completed_with_evidence} completed with evidence`,
    `${counts.pending} pending`,
    `${counts.unknown} unknown`,
  ].join(" · ");
}

export function evidenceSummary(item: TimelineItem): string {
  if (isCompleted(item)) {
    const refs = evidenceRefs(item);
    return refs.length > 0
      ? `evidence: ${refs.map((ref) => `${ref.kind} ${ref.id}`).join(", ")}`
      : "completed";
  }
  if (isUnknown(item)) {
    return item.reason
      ? `unknown — ${item.reason}`
      : "unknown; reconciliation required";
  }
  if (isPending(item)) return "pending; not completed";
  return "";
}

export function announcement(read: TimelineRead): string {
  switch (read.kind) {
    case "loading":
      return "Loading the operator timeline.";
    case "empty":
      return "The operator timeline has no records for this query.";
    case "available": {
      const label = replayLabel(read.projection);
      return `${countsSummary(read.projection)}${label ? ` ${label}` : ""}`;
    }
    case "unavailable":
      return `The operator timeline is unavailable (${read.code}): ${read.detail}`;
    default:
      return `The operator timeline failed: ${read.message}`;
  }
}

export function unavailableSources(
  projection: TimelineProjection | null | undefined,
): Array<{ name: string; code: string | null; reason: string | null }> {
  if (!projection?.sources) return [];
  return Object.entries(projection.sources)
    .filter(([, state]) => state?.state === "unavailable")
    .map(([name, state]) => ({
      name,
      code: state?.code ?? null,
      reason: state?.reason ?? null,
    }));
}

export function clampIndex(index: number, length: number): number {
  if (length <= 0) return -1;
  const value = Number.isFinite(index) ? Math.trunc(index) : 0;
  return Math.min(length - 1, Math.max(0, value));
}

export function nextIndex(index: number, length: number, delta: number): number {
  if (length <= 0) return -1;
  if (index < 0) return delta >= 0 ? 0 : length - 1;
  const start = clampIndex(index, length);
  return (start + delta + length) % length;
}
