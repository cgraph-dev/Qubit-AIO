/**
 * Workbench data layer — the ONE place `/qubit-workbench` talks to the network.
 *
 * No component under `components/workbench/` calls `fetch`; they receive
 * `ApiResult` values and render whichever branch they were handed. That keeps
 * four distinct outcomes apart at the type level, which is the whole point:
 *
 * - `ok`            the backend answered with a shape this module parsed;
 * - `empty`         the backend answered, and its OWN code says there is
 *                   nothing to show for a reason it names (`NO_ACTIVE_PROJECT`
 *                   on the workspace tree). Rendering zero rows without that
 *                   reason would claim "this folder is empty", which is a
 *                   different, false statement;
 * - `not_available` the route is not mounted (a 404 from a backend whose
 *                   workbench seam has not landed). The UI MUST say so out
 *                   loud — an empty list here would claim "you have no
 *                   sessions", which is false;
 * - `error`         anything else (5xx, typed backend refusal, network
 *                   failure, unparseable body). The backend's `code`/`detail`
 *                   ride along untouched so the panel can quote them.
 *
 * Nothing is invented on the way in: a field the backend omits stays `null`
 * on the row and the component decides how to show "unknown".
 *
 * Route set (verified against `voicebridge/workbench/workbench_routes.py`,
 * branch v2/wb-full):
 *
 *   GET  /api/workbench/sessions?limit&offset&q&archived
 *   GET  /api/workbench/sessions/<id>?limit&offset      (header + transcript)
 *   GET  /api/workbench/sessions/<id>/timeline?limit
 *   POST /api/workbench/turn {session_id, text, model?, reasoning?}
 *   GET  /api/workbench/workspace/tree?path=
 *   GET  /api/workbench/workspace/file?path=&workspace=&max_chars=
 *
 * There is NO artifacts route and NO todos route on this backend. The panel
 * that renders those two tabs states that by name instead of faking rows —
 * see `MISSING_WORKBENCH_ROUTES`.
 *
 * Auth rides the dashboard's normal cookie + `X-Qubit-Session-Token` header,
 * the same pair the rest of the SPA uses.
 */

/** Endpoint table — every route this page reads, in one place. */
export const WORKBENCH_ROUTES = {
  sessions: "/api/workbench/sessions",
  session: (id: string) => `/api/workbench/sessions/${encodeURIComponent(id)}`,
  timeline: (id: string) =>
    `/api/workbench/sessions/${encodeURIComponent(id)}/timeline`,
  workspaceTree: "/api/workbench/workspace/tree",
  workspaceFile: (path: string) =>
    `/api/workbench/workspace/file?path=${encodeURIComponent(path)}`,
  turn: "/api/workbench/turn",
} as const;

/**
 * Capabilities this backend does not serve yet, named so the UI can point at
 * the exact gap instead of rendering an empty panel that looks like "none".
 * These are the PATHS a future route must take; nothing here is called.
 */
export const MISSING_WORKBENCH_ROUTES = {
  artifacts: {
    route: "/api/workbench/workspace/artifacts",
    why:
      "The workbench seam serves sessions, a transcript, a timeline and a read-only file tree. It has no artifacts collector, and no store in Qubit projects artifacts per session, so there is nothing honest to count.",
  },
  todos: {
    route: "/api/workbench/todos",
    why:
      "Qubit's task list is the agent's own todo store, read by `/api/qubit/tasks` on other surfaces. The workbench seam exposes no per-session todo projection, so this tab has no source.",
  },
} as const;

/** One row of `GET /api/workbench/sessions`. */
export interface WorkbenchSessionRow {
  id: string;
  title: string | null;
  preview: string | null;
  model: string | null;
  /** The store's own provenance: `tui`, `cron`, `voice`, …  Never invented. */
  source: string | null;
  message_count: number | null;
  tool_call_count: number | null;
  archived: boolean;
  /** Epoch seconds. */
  last_active: number | null;
  is_active: boolean;
}

/** Narrowed from the store: what a row needs to become a sidebar line. */
export interface WorkbenchSessionsPage {
  /** Sessions on THIS page only. `total` is the store's own count. */
  rows: WorkbenchSessionRow[];
  total: number;
  shown: number;
  has_more: boolean;
}

/** Reasoning text the backend stored for one assistant row, when it did. */
export interface WorkbenchReasoning {
  text: string;
  /** Seconds the model spent thinking, when the row measured it. */
  seconds: number | null;
}

/** A tool call recorded on an assistant row. */
export interface WorkbenchToolCall {
  name: string;
  /** Short preview of the call's arguments, when the backend carried one. */
  args: string | null;
}

export interface WorkbenchMessage {
  role: string;
  content: string;
  timestamp: number | null;
  reasoning: WorkbenchReasoning | null;
  toolCalls: WorkbenchToolCall[];
  toolName: string | null;
}

export interface WorkbenchSessionDetail {
  id: string;
  title: string | null;
  model: string | null;
  messages: WorkbenchMessage[];
  messageTotal: number | null;
  /** Tokens the LAST prompt occupied, when the backend measured one. */
  prompt_tokens: number | null;
  context_window: number | null;
}

/** One assistant turn in the timeline (newest first). */
export interface WorkbenchTimelineTurn {
  at: number;
  excerpt: string;
  chars: number;
}

export interface WorkbenchTimeline {
  turns: WorkbenchTimelineTurn[];
  turnsState: string | null;
  eventsState: string | null;
}

export interface WorkspaceEntry {
  name: string;
  path: string;
  /**
   * `dir`, `file`, `symlink`, or `unknown`. A symlink keeps its own kind so the
   * row can say so; whether it is navigable is `isDir`.
   */
  kind: "dir" | "file" | "symlink" | "unknown";
  /** True when the entry can be descended into. */
  isDir: boolean;
  /** Symlink whose target leaves the workspace: shown, never navigated. */
  targetOutsideWorkspace: boolean;
  /** Bytes, when the backend reports a size for a file. */
  size: number | null;
}

export interface WorkspaceTree {
  /** Absolute root the entries are relative to; `""` when the backend named none. */
  root: string;
  /** Workspace-relative directory that was listed. */
  path: string;
  entries: WorkspaceEntry[];
  /** The backend hit its per-directory cap. */
  truncated: boolean;
  /** Registered roots the backend will read (display only). */
  roots: string[];
}

export interface WorkspaceFile {
  path: string;
  content: string;
  truncated: boolean;
}

/** Why a workspace listing is legitimately empty, when the backend says so. */
export interface WorkspaceEmpty {
  code: string;
  detail: string;
}

export interface WorkbenchTurn {
  session_id: string;
  reply: string;
  prompt_tokens: number | null;
}

/**
 * Normalised failure reason. `code` is the backend's own typed code when it
 * answered one (`WORKBENCH_SESSION_STORE_UNAVAILABLE`, `FILES_OUTSIDE_ROOT`,
 * …); `status` is the HTTP status when there was one, `null` for network-level
 * failures.
 */
export interface ApiError {
  status: number | null;
  message: string;
  code: string | null;
}

export type ApiResult<T> =
  | { state: "ok"; data: T }
  | { state: "empty"; data: T; reason: WorkspaceEmpty }
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

/** The payload of an `ok`/`empty` result, or `null` for either failure state. */
export function resultData<T>(result: ApiResult<T> | null): T | null {
  if (result === null) return null;
  return result.state === "ok" || result.state === "empty" ? result.data : null;
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

function bool(value: unknown): boolean {
  return value === true;
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
  const source = str(row.source);
  return {
    id,
    title: str(row.title),
    preview: str(row.preview),
    model: str(row.model),
    // The backend sends `""` for a session with no provenance; that is
    // "unknown", not a bucket called "".
    source: source && source.trim() ? source.trim() : null,
    message_count: num(row.message_count),
    tool_call_count: num(row.tool_call_count),
    archived: bool(row.archived),
    last_active: epochSeconds(row.last_active),
    is_active: bool(row.is_active),
  };
}

/**
 * `GET /api/workbench/sessions` → the page plus the store's own totals.
 *
 * The backend answers `{sessions: [...], total, shown, has_more, …}`. Both the
 * bare-array and `{sessions}` shapes are accepted so a shape change shows up as
 * missing totals rather than a blank panel.
 */
export function parseSessions(raw: unknown): WorkbenchSessionsPage | null {
  const body = asRecord(raw);
  const list = Array.isArray(raw)
    ? raw
    : Array.isArray(body?.sessions)
      ? (body.sessions as unknown[])
      : null;
  if (!list) return null;
  const rows = list
    .map(parseSessionRow)
    .filter((row): row is WorkbenchSessionRow => row !== null);
  const total = num(body?.total) ?? rows.length;
  const shown = num(body?.shown) ?? rows.length;
  return {
    rows,
    total,
    shown,
    has_more: body?.has_more === true || shown < total,
  };
}

/**
 * Reasoning for one assistant row.
 *
 * The store persists it under `meta.reasoning_content` (a string, or a record
 * carrying the text and, sometimes, a duration). Anything else is "no
 * measurement" — the UI then renders no Thinking row at all rather than an
 * empty one.
 */
function parseReasoning(meta: Record<string, unknown> | null): WorkbenchReasoning | null {
  if (!meta) return null;
  const rawValue = meta.reasoning_content ?? meta.reasoning ?? meta.thinking;
  const direct = str(rawValue);
  if (direct !== null) {
    return direct.trim() ? { text: direct, seconds: null } : null;
  }
  const record = asRecord(rawValue);
  if (!record) return null;
  const text = str(record.text) ?? str(record.content);
  if (!text || !text.trim()) return null;
  const seconds = num(record.duration_seconds) ?? num(record.seconds);
  return { text, seconds };
}

/** Tool calls recorded on an assistant row (`meta.tool_calls`). */
function parseToolCalls(meta: Record<string, unknown> | null): WorkbenchToolCall[] {
  const list = meta && Array.isArray(meta.tool_calls) ? (meta.tool_calls as unknown[]) : [];
  const out: WorkbenchToolCall[] = [];
  for (const raw of list) {
    const call = asRecord(raw);
    const fn = asRecord(call?.function);
    const name = str(fn?.name) ?? str(call?.name);
    if (!name) continue;
    const argsRaw = fn?.arguments ?? call?.arguments;
    let args: string | null = null;
    if (typeof argsRaw === "string") args = argsRaw;
    else if (argsRaw !== undefined && argsRaw !== null) {
      try {
        args = JSON.stringify(argsRaw);
      } catch {
        args = null;
      }
    }
    out.push({ name, args: args && args.length > 240 ? `${args.slice(0, 240)}…` : args });
  }
  return out;
}

function parseMessage(raw: unknown): WorkbenchMessage | null {
  const msg = asRecord(raw);
  const role = str(msg?.role);
  const content = str(msg?.content);
  if (!msg || !role || content === null) return null;
  const meta = asRecord(msg.meta);
  return {
    role,
    content,
    timestamp: epochSeconds(msg.timestamp),
    reasoning: parseReasoning(meta),
    toolCalls: parseToolCalls(meta),
    toolName: str(meta?.tool_name),
  };
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
    messages: list.map(parseMessage).filter((m): m is WorkbenchMessage => m !== null),
    messageTotal: num(body.total),
    // The workbench transcript route does not carry token measurements; the
    // ring therefore renders its honest "no measurement" state (see the
    // composer). Both spellings are still accepted so the ring starts working
    // the day the backend adds one.
    prompt_tokens: num(body.prompt_tokens) ?? num(body.last_prompt_tokens),
    context_window: num(body.context_window) ?? num(body.effective_context_length),
  };
}

export function parseTimeline(raw: unknown): WorkbenchTimeline | null {
  const body = asRecord(raw);
  if (!body) return null;
  const list = Array.isArray(body.turns) ? body.turns : [];
  const turns: WorkbenchTimelineTurn[] = [];
  for (const item of list) {
    const turn = asRecord(item);
    const excerpt = str(turn?.excerpt);
    const at = epochSeconds(turn?.at);
    if (!turn || at === null || excerpt === null) continue;
    turns.push({ at, excerpt, chars: num(turn.chars) ?? excerpt.length });
  }
  const turnsState = asRecord(body.turns_state);
  const eventsState = asRecord(body.events_state);
  return {
    turns,
    turnsState: str(turnsState?.state),
    eventsState: str(eventsState?.state),
  };
}

/**
 * `GET /api/workbench/workspace/tree` → entries.
 *
 * The adapter answers `{entries: [{name, path, type, size, mtime_ns, …}]}`.
 * `type` is `dir` / `file` / `symlink` / `unknown`; a symlink may still be a
 * directory, and a symlink whose target escapes the workspace is display-only.
 */
function parseEntry(raw: unknown): WorkspaceEntry | null {
  const entry = asRecord(raw);
  const name = str(entry?.name);
  if (!entry || !name) return null;
  const kindRaw = str(entry.type);
  const kind =
    kindRaw === "dir" || kindRaw === "file" || kindRaw === "symlink"
      ? kindRaw
      : "unknown";
  const outside = bool(entry.target_outside_workspace);
  // A directory is navigable only when the backend did not mark it as an
  // escaping symlink.
  const isDir = kind === "dir" ? true : kind === "symlink" ? !outside && entry.is_dir === true : false;
  return {
    name,
    path: str(entry.path) ?? name,
    kind,
    isDir,
    targetOutsideWorkspace: outside,
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
  const roots = Array.isArray(body?.roots)
    ? (body.roots as unknown[]).map((r) => str(r)).filter((r): r is string => !!r)
    : [];
  return {
    root: str(body?.workspace) ?? str(body?.folder) ?? str(body?.root) ?? "",
    path: str(body?.path) ?? "",
    entries: list.map(parseEntry).filter((e): e is WorkspaceEntry => e !== null),
    truncated: bool(body?.truncated),
    roots,
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

/** The backend's typed refusal code for an empty-but-healthy workspace listing. */
const EMPTY_TREE_CODES = new Set(["NO_ACTIVE_PROJECT", "PROJECT_HAS_NO_FOLDERS"]);

// ---------------------------------------------------------------------------
// Transport. One generic request function; the public functions below are
// one-liners over it so the endpoint list stays readable in `WORKBENCH_ROUTES`.
// ---------------------------------------------------------------------------

function sessionToken(): string {
  return typeof window === "undefined"
    ? ""
    : ((window as unknown as { __QUBIT_SESSION_TOKEN__?: string }).__QUBIT_SESSION_TOKEN__ ?? "");
}

/**
 * The dashboard's API prefix (`window.__QUBIT_API_PREFIX__`, normally `/agent`).
 *
 * Read at call time, never at module load: the same rule `lib/api.ts` follows,
 * because a plugin tab or a test can install the global after this module is
 * evaluated. Without this every workbench request went to `/api/workbench/*` at
 * the document root, which the Python server does not serve — it answers 404,
 * and a 404 in this data layer means "route not mounted", so the page told the
 * reader the backend was missing while it was running fine under /agent.
 */
function apiPrefix(): string {
  if (typeof window === "undefined") return "";
  const raw = (window as unknown as { __QUBIT_API_PREFIX__?: string }).__QUBIT_API_PREFIX__;
  if (typeof raw !== "string") return "";
  const trimmed = raw.trim().replace(/\/+$/, "");
  return trimmed.startsWith("/") ? trimmed : trimmed ? `/${trimmed}` : "";
}

async function request<T>(
  route: string,
  parse: (raw: unknown) => T | null,
  init?: RequestInit,
): Promise<ApiResult<T>> {
  // Absolute, because node's undici (which backs a test's `window.fetch`) will
  // not parse a relative URL. In the browser this is the same request. The
  // prefix goes on the ROUTE, not on the resolved URL, so a subpath deployment
  // (`<base>/agents/...`) keeps both halves.
  const prefixed = route.startsWith("/") ? `${apiPrefix()}${route}` : route;
  const url = typeof window === "undefined" ? prefixed : new URL(prefixed, window.location.href).href;
  // Resolved from `window` at call time: in a browser that is the platform
  // `fetch`, and a test can stub it without `Response` existing as a global.
  const send = typeof window === "undefined" ? undefined : window.fetch;
  if (typeof send !== "function") {
    return failure({ status: null, message: "fetch is unavailable in this runtime", code: null });
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
    return failure({ status: null, message, code: null });
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
      code: null,
    });
  }

  let raw: unknown = null;
  if (text.trim()) {
    try {
      raw = JSON.parse(text) as unknown;
    } catch {
      return failure({ status: response.status, message: "response is not JSON", code: null });
    }
  }
  const envelope = asRecord(raw);

  if (!response.ok) {
    // A typed refusal carries its own vocabulary; quote it rather than
    // collapsing every 4xx/5xx into "request failed".
    const code = str(envelope?.code);
    const detail = str(envelope?.detail);
    return failure({
      status: response.status,
      message: detail?.trim() || text.trim() || response.statusText,
      code,
    });
  }

  const parsed = parse(raw);
  if (parsed === null) {
    return failure({
      status: response.status,
      message: "response shape was not recognised",
      code: str(envelope?.code),
    });
  }
  // A mounted backend can say "nothing to show, and here is why". That is a
  // fact, not an empty list to be rendered as one.
  const code = str(envelope?.code);
  if (code && EMPTY_TREE_CODES.has(code)) {
    return {
      state: "empty",
      data: parsed,
      reason: { code, detail: str(envelope?.detail) ?? "" },
    };
  }
  return ok(parsed);
}

/** GET /api/workbench/sessions */
export function getWorkbenchSessions(): Promise<ApiResult<WorkbenchSessionsPage>> {
  return request(WORKBENCH_ROUTES.sessions, parseSessions);
}

/** GET /api/workbench/sessions/<id> (header fields + transcript) */
export function getWorkbenchSession(id: string): Promise<ApiResult<WorkbenchSessionDetail>> {
  return request(WORKBENCH_ROUTES.session(id), parseSessionDetail);
}

/** GET /api/workbench/sessions/<id>/timeline */
export function getWorkbenchTimeline(id: string): Promise<ApiResult<WorkbenchTimeline>> {
  return request(WORKBENCH_ROUTES.timeline(id), parseTimeline);
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
