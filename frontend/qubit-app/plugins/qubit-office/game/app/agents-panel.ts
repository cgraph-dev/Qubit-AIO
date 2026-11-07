/* The agents panel's data: the roster's seats, grouped by division.
 *
 * WHAT IT READS, AND FROM WHERE
 * -----------------------------
 * ``GET /agent/api/office/habblaud/agents/panel`` — the Qubit compat route that
 * projects ``voicebridge.office.agency_org``'s building plan onto the roster's
 * own divisions. It answers the seat list, the persona name per seat, the room
 * and the real per-division counts.
 *
 * WHY THE PREFIX, AND WHY THE TOKEN
 * ---------------------------------
 * The office document is a same-origin iframe of the vendored app, which speaks
 * a LITERAL ``/api/*`` dialect; the dashboard rewrites nothing for it. A bare
 * ``/api/agents/panel`` from that document is a 404, and the compat table is
 * gated by the session token, so a tokenless read is a 401. Both are handled
 * here in one place:
 *
 *   * the URL is built under ``/agent`` (``API_PREFIX``), which is the prefix
 *     the server serves the compat family under, and
 *   * the document's injected ``__QUBIT_SESSION_TOKEN__`` (or, inside the
 *     embedded app, the PARENT's) is sent as ``X-Qubit-Session-Token``.
 *
 * The pure helpers below carry every decision — the URL, the token lookup, the
 * grouping, the labels — so they are testable without a DOM, and the panel
 * component is only layout.
 */

/** The Office API prefix: the server serves the compat family under `/agent`. */
export const API_PREFIX = "/agent";

/**
 * The panel's route. Named after the resource it reads (`agents/panel`), so it
 * sits under the office family the api-bridge already owns and cannot be
 * confused with the general API.
 */
export const AGENTS_PANEL_PATH = "/api/office/habblaud/agents/panel";

/** The full URL, rebased onto the host's base path when the SPA is served one. */
export function agentsPanelUrl(base?: string): string {
  const raw = base === undefined ? hostBasePath() : base;
  const b = raw.endsWith("/") ? raw.slice(0, -1) : raw;
  return `${b}${API_PREFIX}${AGENTS_PANEL_PATH}`;
}

/** The dashboard's base path, from the host's injected global (may be absent). */
export function hostBasePath(): string {
  if (typeof window === "undefined") return "";
  const raw = (window as { __QUBIT_BASE_PATH__?: string }).__QUBIT_BASE_PATH__;
  if (!raw) return "";
  const withLead = raw.startsWith("/") ? raw : `/${raw}`;
  return withLead.replace(/\/+$/, "");
}

/**
 * The session token. The office document is served with its own copy; when it
 * is the embedded app the parent has one and `win.parent` can be read
 * (same origin). Never a query parameter — the server rejects those outright.
 */
export function readToken(win?: Window | null): string {
  const w = (win ?? (typeof window === "undefined" ? null : window)) as
    | (Window & { __QUBIT_SESSION_TOKEN__?: string })
    | null;
  if (!w) return "";
  if (typeof w.__QUBIT_SESSION_TOKEN__ === "string" && w.__QUBIT_SESSION_TOKEN__) {
    return w.__QUBIT_SESSION_TOKEN__;
  }
  try {
    const parent = w.parent as unknown as { __QUBIT_SESSION_TOKEN__?: string };
    return (parent && parent.__QUBIT_SESSION_TOKEN__) || "";
  } catch {
    return "";
  }
}

/** One seat as the route serves it. Every field is copied, never defaulted. */
export interface AgentSeat {
  slug: string;
  name: string;
  seat_type: string;
  supervisor: string | null;
  division: string;
  room: string;
  emoji: string;
  color: string;
}

/** One division bucket. The boardroom tier has `slug === ""`. */
export interface AgentDivision {
  slug: string;
  label: string;
  kind: string;
  floor: number | null;
  headcount: number;
  seats: AgentSeat[];
}

export interface AgentPanelCounts {
  headcount: number;
  divisions: number;
  roster_divisions: number;
  boardroom_seats: number;
  rooms: number;
  floors: number;
  seat_types: Record<string, number>;
  per_division: Record<string, number>;
}

/** The route's payload, as this module understands it. */
export interface AgentPanelData {
  ok: boolean;
  schema?: string;
  counts?: AgentPanelCounts;
  divisions?: AgentDivision[];
  seats?: AgentSeat[];
  detail?: string;
  code?: string;
}

/** What the component renders: data, or a NAMED reason there is none. */
export type AgentPanelState =
  | { kind: "loading" }
  | { kind: "ready"; data: AgentPanelData }
  | { kind: "error"; message: string };

/** The seat-type order the plan builds in; anything else sorts last. */
export const SEAT_TYPE_ORDER = [
  "ceo", "director_functional", "director_portfolio", "manager", "worker",
];

/** A human label for a seat type. The raw value when it is not one we know. */
export function seatTypeLabel(seatType: string): string {
  switch (seatType) {
    case "ceo": return "CEO";
    case "director_functional": return "Functional director";
    case "director_portfolio": return "Portfolio director";
    case "manager": return "Manager";
    case "worker": return "Worker";
    default: return seatType || "Unclassified seat";
  }
}

/** The one-line summary a reader sees before the list. Counts only. */
export function summaryLine(counts: AgentPanelCounts | undefined): string {
  if (!counts) return "";
  const divs = counts.divisions;
  const head = counts.headcount;
  const rooms = counts.rooms;
  const board = counts.boardroom_seats;
  return `${head} seats · ${divs} divisions · ${rooms} rooms`
    + (board ? ` · ${board} in the boardroom` : "");
}

/**
 * Grouping check: the buckets must account for every seat exactly once. A
 * mismatch is NAMED, never rendered as a shorter list that looks complete.
 */
export function groupingProblem(data: AgentPanelData): string {
  const rows = data.seats ?? [];
  const groups = data.divisions ?? [];
  const counted = groups.reduce((n, g) => n + (g.seats?.length ?? 0), 0);
  if (counted !== rows.length) {
    return `the panel groups ${counted} seats but the route served ${rows.length}`;
  }
  const claimed = data.counts?.headcount;
  if (typeof claimed === "number" && claimed !== rows.length) {
    return `the route's headcount (${claimed}) disagrees with its seat list `
      + `(${rows.length})`;
  }
  return "";
}

/**
 * Fetch the panel. Throws nothing: a failure is returned as a named error so
 * the component always has something true to say.
 */
export async function fetchAgentPanel(opts: {
  fetchImpl?: typeof fetch;
  win?: Window | null;
  base?: string;
  signal?: AbortSignal;
} = {}): Promise<AgentPanelState> {
  const f = opts.fetchImpl ?? fetch;
  const url = agentsPanelUrl(opts.base);
  const token = readToken(opts.win);
  const headers: Record<string, string> = { Accept: "application/json" };
  if (token) headers["X-Qubit-Session-Token"] = token;
  try {
    const res = await f(url, { headers, credentials: "same-origin", signal: opts.signal });
    if (res.status === 401) {
      return { kind: "error", message: `The agents panel was refused (HTTP 401); the office session token was not accepted. Reload the page to refresh it.` };
    }
    if (res.status === 404) {
      return { kind: "error", message: `The agents panel route is not served here (HTTP 404: ${url}). This server does not expose the office roster.` };
    }
    const body = (await res.json().catch(() => null)) as AgentPanelData | null;
    if (!body || typeof body !== "object") {
      return { kind: "error", message: `The agents panel answered HTTP ${res.status} with no JSON body.` };
    }
    if (body.ok !== true) {
      return { kind: "error", message: body.detail || body.code
        ? `The agents panel could not be built: ${body.detail || body.code}`
        : `The agents panel answered not-ok (HTTP ${res.status}).` };
    }
    const problem = groupingProblem(body);
    if (problem) return { kind: "error", message: `The agents panel data is inconsistent: ${problem}.` };
    return { kind: "ready", data: body };
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    return { kind: "error", message: `The agents panel could not be reached: ${msg}` };
  }
}
