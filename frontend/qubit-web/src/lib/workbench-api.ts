/**
 * Workbench data layer — the ONE place `/qubit-workbench` talks to the network.
 *
 * No component under `components/workbench/` calls `fetch`; they receive
 * `ApiResult` values and render whichever branch they were handed. That keeps
 * three distinct outcomes apart at the type level, which is the whole point:
 *
 * - `ok`            the backend answered with a shape this module parsed;
 * - `not_available` the route is not mounted yet (a 404 from a backend whose
 *                   workbench seam has not landed), or the body explicitly says
 *                   it is unavailable. The UI MUST say so out loud — an empty
 *                   list here would claim "you have no sessions", which is a
 *                   different, false statement;
 * - `error`         anything else (5xx, network failure, unparseable body).
 *
 * Nothing is invented on the way in: a field the backend omits stays `null`
 * on the row and the component decides how to show "unknown".
 *
 * Auth rides the dashboard's normal cookie + `X-Qubit-Session-Token` header,
 * the same pair the rest of the SPA uses.
 */

/** Endpoint table — every route this page reads, in one place. */
export const WORKBENCH_ROUTES = {
  sessions: "/api/workbench/sessions",
  session: (id: string) => `/api/workbench/sessions/${encodeURIComponent(id)}`,
  workspaceTree: "/api/workbench/workspace/tree",
  workspaceFile: (path: string) =>
    `/api/workbench/workspace/file?path=${encodeURIComponent(path)}`,
  turn: "/api/workbench/turn",
} as const;

export interface WorkbenchSessionRow {
  id: string;
  title: string | null;
  preview: string | null;
  model: string | null;
  source: string | null;
  message_count: number | null;
  last_active: number | null;
}

export interface WorkbenchMessage {
  role: string;
  content: string;
  timestamp: number | null;
}

export interface WorkbenchSessionDetail {
  id: string;
  title: string | null;
  model: string | null;
  messages: WorkbenchMessage[];
  /** Tokens the LAST prompt occupied, when the backend measured one. */
  prompt_tokens: number | null;
  context_window: number | null;
}

export interface WorkspaceEntry {
  name: string;
  path: string;
  kind: "file" | "directory";
  /** Bytes, when the backend reports a size for a file. */
  size: number | null;
}

export interface WorkspaceTree {
  root: string;
  entries: WorkspaceEntry[];
}

export interface WorkspaceFile {
  path: string;
  content: string;
  truncated: boolean;
}

export interface WorkbenchTurn {
  session_id: string;
  reply: string;
  prompt_tokens: number | null;
}

/**
 * Normalised failure reason. `status` is the HTTP status when there was one;
 * `null` covers network-level failures (DNS, offline, aborted).
 */
export interface ApiError {
  status: number | null;
  message: string;
}

export type ApiResult<T> =
  | { state: "ok"; data: T }
  | { state: "not_available"; routes: readonly string[] }
  | { state: "error"; error: ApiError };

/** One row of the Control Center's endpoint table. */
export interface RouteProbe {
  /** Human label for the route's purpose. */
  label: string;
  /** The path, or a path template when the route takes an id/query. */
  path: string;
  /** Result of the last read on this route; `null` = not yet read. */
  result: ApiResult<unknown> | null;
}

/** The 404 → `not_available` mapping is the entire seam contract. */
export function unavailable<T>(route: string | readonly string[]): ApiResult<T> {
  return {
    state: "not_available",
    routes: typeof route === "string" ? [route] : route,
  };
}

export function ok<T>(data: T): ApiResult<T> {
  return { state: "ok", data };
}

export function failure<T>(error: ApiError): ApiResult<T> {
  return { state: "error", error };
}

// ---------------------------------------------------------------------------
// Parsing helpers. Each one accepts `unknown` and returns the typed value or
// `null` — a body that does not have the shape we asked for is "unparseable",
// never a silently half-filled object.
// ---------------------------------------------------------------------------

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function str(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

function num(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

/**
 * Epoch seconds from either an epoch number or an ISO-8601 string. Milliseconds
 * are folded to seconds — the house `timeAgo` helper takes seconds, and a
 * millisecond value would read as "20000 days ago" rather than a bug report.
 */
function epochSeconds(value: unknown): number | null {
  const asNumber = num(value);
  if (asNumber !== null) {
    if (asNumber <= 0) return null;
    return asNumber > 1e11 ? Math.round(asNumber / 1000) : asNumber;
  }
  if (typeof value !== "string" || !value.trim()) return null;
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? Math.round(parsed / 1000) : null;
}

function parseSessionRow(raw: unknown): WorkbenchSessionRow | null {
  const row = asRecord(raw);
  const id = str(row?.id);
  if (!row || !id) return null;
  return {
    id,
    title: str(row.title),
    preview: str(row.preview),
    model: str(row.model),
    source: str(row.source),
    message_count: num(row.message_count),
    last_active: epochSeconds(row.last_active),
  };
}

export function parseSessions(raw: unknown): WorkbenchSessionRow[] | null {
  const body = asRecord(raw);
  const list = Array.isArray(raw)
    ? raw
    : Array.isArray(body?.sessions)
      ? (body.sessions as unknown[])
      : null;
  if (!list) return null;
  return list
    .map(parseSessionRow)
    .filter((row): row is WorkbenchSessionRow => row !== null);
}

function parseMessage(raw: unknown): WorkbenchMessage | null {
  const msg = asRecord(raw);
  const role = str(msg?.role);
  const content = str(msg?.content);
  if (!msg || !role || content === null) return null;
  return { role, content, timestamp: epochSeconds(msg.timestamp) };
}

export function parseSessionDetail(raw: unknown): WorkbenchSessionDetail | null {
  const body = asRecord(raw);
  if (!body) return null;
  const id = str(body.id) ?? str(body.session_id);
  const list = Array.isArray(body.messages) ? body.messages : null;
  if (!id || !list) return null;
  return {
    id,
    title: str(body.title),
    model: str(body.model),
    messages: list
      .map(parseMessage)
      .filter((m): m is WorkbenchMessage => m !== null),
    prompt_tokens: num(body.prompt_tokens) ?? num(body.last_prompt_tokens),
    context_window: num(body.context_window) ?? num(body.effective_context_length),
  };
}

function parseEntry(raw: unknown): WorkspaceEntry | null {
  const entry = asRecord(raw);
  const name = str(entry?.name);
  if (!entry || !name) return null;
  const kind = entry.kind === "directory" || entry.is_dir === true ? "directory" : "file";
  return {
    name,
    path: str(entry.path) ?? name,
    kind,
    size: num(entry.size),
  };
}

export function parseTree(raw: unknown): WorkspaceTree | null {
  const body = asRecord(raw);
  const list = Array.isArray(raw)
    ? raw
    : Array.isArray(body?.entries)
      ? (body.entries as unknown[])
      : null;
  if (!list) return null;
  return {
    root: str(body?.root) ?? "",
    entries: list
      .map(parseEntry)
      .filter((e): e is WorkspaceEntry => e !== null),
  };
}

export function parseFile(raw: unknown): WorkspaceFile | null {
  const body = asRecord(raw);
  const content = str(body?.content);
  if (!body || content === null) return null;
  return {
    path: str(body.path) ?? "",
    content,
    truncated: body.truncated === true,
  };
}

export function parseTurn(raw: unknown): WorkbenchTurn | null {
  const body = asRecord(raw);
  const reply = str(body?.reply) ?? str(body?.content);
  if (!body || reply === null) return null;
  return {
    session_id: str(body.session_id) ?? str(body.id) ?? "",
    reply,
    prompt_tokens: num(body.prompt_tokens),
  };
}

// ---------------------------------------------------------------------------
// Transport. One generic request function; the public functions below are
// one-liners over it so the endpoint list stays readable in `WORKBENCH_ROUTES`.
// ---------------------------------------------------------------------------

function sessionToken(): string {
  return typeof window === "undefined"
    ? ""
    : ((window as unknown as { __QUBIT_SESSION_TOKEN__?: string })
        .__QUBIT_SESSION_TOKEN__ ?? "");
}

async function request<T>(
  route: string,
  parse: (raw: unknown) => T | null,
  init?: RequestInit,
): Promise<ApiResult<T>> {
  // Absolute, because node's undici (which backs a test's `window.fetch`) will
  // not parse a relative URL. In the browser this is the same request.
  const url = typeof window === "undefined" ? route : new URL(route, window.location.href).href;
  // Resolved from `window` at call time: in a browser that is the platform
  // `fetch`, and a test can stub it without `Response` existing as a global.
  const send = typeof window === "undefined" ? undefined : window.fetch;
  if (typeof send !== "function") {
    return failure({ status: null, message: "fetch is unavailable in this runtime" });
  }
  let response: Response;
  try {
    response = await send(url, {
      credentials: "include",
      ...init,
      headers: {
        Accept: "application/json",
        "X-Qubit-Session-Token": sessionToken(),
        ...(init?.body ? { "Content-Type": "application/json" } : {}),
        ...init?.headers,
      },
    });
  } catch (cause) {
    const message = cause instanceof Error ? cause.message : String(cause);
    return failure({ status: null, message });
  }

  // The seam's honest state: no route at this path yet.
  if (response.status === 404) return unavailable<T>(route);

  let text: string;
  try {
    text = await response.text();
  } catch (cause) {
    return failure({
      status: response.status,
      message: cause instanceof Error ? cause.message : String(cause),
    });
  }

  if (!response.ok) {
    return failure({ status: response.status, message: text.trim() || response.statusText });
  }

  let raw: unknown = null;
  if (text.trim()) {
    try {
      raw = JSON.parse(text) as unknown;
    } catch {
      return failure({ status: response.status, message: "response is not JSON" });
    }
  }

  const parsed = parse(raw);
  if (parsed === null) {
    return failure({ status: response.status, message: "response shape was not recognised" });
  }
  return ok(parsed);
}

/** GET /api/workbench/sessions */
export function getWorkbenchSessions(): Promise<ApiResult<WorkbenchSessionRow[]>> {
  return request(WORKBENCH_ROUTES.sessions, parseSessions);
}

/** GET /api/workbench/sessions/<id> */
export function getWorkbenchSession(id: string): Promise<ApiResult<WorkbenchSessionDetail>> {
  return request(WORKBENCH_ROUTES.session(id), parseSessionDetail);
}

/** GET /api/workbench/workspace/tree?path=<dir> */
export function getWorkspaceTree(path = ""): Promise<ApiResult<WorkspaceTree>> {
  const route = path.trim()
    ? `${WORKBENCH_ROUTES.workspaceTree}?path=${encodeURIComponent(path.trim())}`
    : WORKBENCH_ROUTES.workspaceTree;
  return request(route, parseTree);
}

/** GET /api/workbench/workspace/file?path=... */
export function readWorkspaceFile(path: string): Promise<ApiResult<WorkspaceFile>> {
  return request(WORKBENCH_ROUTES.workspaceFile(path), parseFile);
}

/** POST /api/workbench/turn */
export function postWorkbenchTurn(input: {
  session_id: string | null;
  text: string;
}): Promise<ApiResult<WorkbenchTurn>> {
  return request(WORKBENCH_ROUTES.turn, parseTurn, {
    method: "POST",
    body: JSON.stringify(input),
  });
}
