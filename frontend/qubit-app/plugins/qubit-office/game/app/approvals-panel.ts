/* The Office approvals panel's data: pending records on the SHARED journal.
 *
 * WHAT IT READS, AND FROM WHERE
 * -----------------------------
 * ``GET /agent/api/ops/approvals`` — the existing session-token-gated compat
 * route that projects ``voicebridge.approvals.ApprovalJournal.pending()``.
 * Approve/reject use the SAME journal routes the pending-approvals banner and
 * CLI use (``POST /api/ops/confirm`` / ``POST /api/ops/cancel``). This module
 * adds NO approval authority, no auto-approve and no optimistic success: a
 * non-2xx or ``ok:false`` answer becomes a typed error, so the panel can only
 * report what the journal actually did.
 *
 * WHY THE PREFIX, AND WHY THE TOKEN
 * ---------------------------------
 * The office shell is served under the dashboard's base path and talks to the
 * compat family under ``/agent`` (see ``agents-panel.ts``). The compat table is
 * gated by the host session token, which is injected into the document as
 * ``window.__QUBIT_SESSION_TOKEN__``; the same token lookup is reused here.
 */
import { API_PREFIX, hostBasePath, readToken } from "./agents-panel";

/** The pending-approvals read route. */
export const APPROVALS_PATH = "/api/ops/approvals";
/** The two settle routes the classic banner and CLI already use. */
export const APPROVAL_CONFIRM_PATH = "/api/ops/confirm";
export const APPROVAL_CANCEL_PATH = "/api/ops/cancel";

export type ApprovalDecision = "approve" | "deny";

/**
 * One pending journal row as the route serves it.
 *
 * ``action``/``risk``/``requested_by``/``session`` are bounded display fields
 * projected from the record's own meta; they may be absent (omit them, never
 * invent them). ``created_at``/``expires_at`` are epoch seconds.
 */
export interface PendingApproval {
  id: string;
  capability: string;
  summary: string;
  created_at: number;
  expires_at: number;
  state: string;
  action?: string;
  risk?: string;
  requested_by?: string;
  session?: string;
}

export interface ApprovalsPayload {
  ok?: boolean;
  approvals?: PendingApproval[];
  count?: number;
  code?: string;
  detail?: string;
  state?: string;
}

/** What the panel renders: the real list, or a NAMED reason there is none. */
export type ApprovalsState =
  | { kind: "loading" }
  | { kind: "ready"; items: PendingApproval[] }
  | { kind: "error"; message: string; code?: string };

/** One settle failure, carrying the server's typed code when it sent one. */
export class ApprovalApiError extends Error {
  readonly code: string;
  readonly status: number;

  constructor(message: string, code: string, status: number) {
    super(message);
    this.name = "ApprovalApiError";
    this.code = code;
    this.status = status;
  }
}

function rebase(base: string | undefined, path: string): string {
  const raw = base === undefined ? hostBasePath() : base;
  const b = raw.endsWith("/") ? raw.slice(0, -1) : raw;
  return `${b}${API_PREFIX}${path}`;
}

/** The pending-approvals URL, rebased onto the dashboard's base path. */
export function approvalsUrl(base?: string): string {
  return rebase(base, APPROVALS_PATH);
}

/** The settle URL for one decision. */
export function approvalActionUrl(
  decision: ApprovalDecision,
  base?: string,
): string {
  return rebase(
    base,
    decision === "approve" ? APPROVAL_CONFIRM_PATH : APPROVAL_CANCEL_PATH,
  );
}

function authHeaders(token: string): Record<string, string> {
  const headers: Record<string, string> = { Accept: "application/json" };
  if (token) headers["X-Qubit-Session-Token"] = token;
  return headers;
}

function errorText(exc: unknown): string {
  return exc instanceof Error ? exc.message : String(exc);
}

/**
 * Fetch the pending list. Never throws: a failure is a named error state so
 * an empty office and an unreachable one can never look the same.
 */
export async function fetchApprovals(opts: {
  fetchImpl?: typeof fetch;
  win?: Window | null;
  base?: string;
  signal?: AbortSignal;
} = {}): Promise<ApprovalsState> {
  const f = opts.fetchImpl ?? fetch;
  const url = approvalsUrl(opts.base);
  const headers = authHeaders(readToken(opts.win));
  try {
    const res = await f(url, {
      headers,
      credentials: "same-origin",
      signal: opts.signal,
    });
    if (res.status === 401) {
      return {
        kind: "error",
        message: "Pending approvals were refused (HTTP 401); the office "
          + "session token was not accepted. Reload the page to refresh it.",
        code: "UNAUTHORIZED",
      };
    }
    if (res.status === 404) {
      return {
        kind: "error",
        message: `The approvals route is not served here (HTTP 404: ${url}).`,
        code: "NOT_FOUND",
      };
    }
    const body = (await res.json().catch(() => null)) as ApprovalsPayload | null;
    if (!body || typeof body !== "object") {
      return {
        kind: "error",
        message: `The approvals route answered HTTP ${res.status} with no JSON body.`,
      };
    }
    if (body.ok !== true) {
      return {
        kind: "error",
        message: body.detail || body.code
          ? `Pending approvals could not be read: ${body.detail || body.code}`
          : `Pending approvals answered not-ok (HTTP ${res.status}).`,
        code: body.code,
      };
    }
    const items = Array.isArray(body.approvals) ? body.approvals : [];
    return { kind: "ready", items };
  } catch (exc) {
    return {
      kind: "error",
      message: `Pending approvals could not be reached: ${errorText(exc)}`,
    };
  }
}

/**
 * Settle ONE pending record through the shared journal route.
 *
 * Resolves only when the backend reports ``ok:true`` and ``state:"executed"``.
 * Anything else (a typed refusal, an expired id, a non-JSON body) throws
 * ``ApprovalApiError`` so the caller renders the real reason. This function
 * never retries and never assumes the decision landed.
 */
export async function decideApproval(
  id: string,
  decision: ApprovalDecision,
  opts: {
    fetchImpl?: typeof fetch;
    win?: Window | null;
    base?: string;
    signal?: AbortSignal;
  } = {},
): Promise<void> {
  const trimmed = String(id ?? "").trim();
  if (!trimmed) {
    throw new ApprovalApiError(
      "An approval id is required to settle a pending record.",
      "MALFORMED",
      400,
    );
  }
  const f = opts.fetchImpl ?? fetch;
  const url = approvalActionUrl(decision, opts.base);
  const headers = authHeaders(readToken(opts.win));
  headers["Content-Type"] = "application/json";
  let res: Response;
  try {
    res = await f(url, {
      method: "POST",
      headers,
      credentials: "same-origin",
      body: JSON.stringify({ confirmation_id: trimmed }),
      signal: opts.signal,
    });
  } catch (exc) {
    throw new ApprovalApiError(
      `The approval request could not be sent: ${errorText(exc)}`,
      "NETWORK",
      0,
    );
  }
  const body = (await res.json().catch(() => null)) as ApprovalsPayload | null;
  if (!body || typeof body !== "object") {
    throw new ApprovalApiError(
      `The approval route answered HTTP ${res.status} with no JSON body.`,
      "NO_BODY",
      res.status,
    );
  }
  if (!res.ok || body.ok !== true || body.state !== "executed") {
    throw new ApprovalApiError(
      body.detail || `The journal did not settle that record (HTTP ${res.status}).`,
      body.code || `HTTP_${res.status}`,
      res.status,
    );
  }
}

/** A compact, honest age label from the record's epoch-seconds timestamp. */
export function formatAge(
  createdAt: number,
  nowSeconds: number = Date.now() / 1000,
): string {
  const created = Number(createdAt);
  if (!Number.isFinite(created) || created <= 0) return "age unknown";
  const seconds = Math.max(0, Math.floor(nowSeconds - created));
  if (seconds < 60) return `${seconds}s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return `${Math.floor(seconds / 86400)}d ago`;
}

/** The tool/action a reader recognizes: the capability, never a summary. */
export function approvalTool(item: PendingApproval): string {
  return String(item.capability || "").trim() || "unknown capability";
}

/** The reviewer-facing action, from the record's meta; empty when absent. */
export function approvalAction(item: PendingApproval): string {
  return String(item.action || "").trim();
}

/** The reviewer-facing risk, from the record's meta; empty when absent. */
export function approvalRisk(item: PendingApproval): string {
  return String(item.risk || "").trim();
}

/** Who asked, from the record's meta; empty when absent. */
export function approvalRequester(item: PendingApproval): string {
  return String(item.requested_by || "").trim();
}
