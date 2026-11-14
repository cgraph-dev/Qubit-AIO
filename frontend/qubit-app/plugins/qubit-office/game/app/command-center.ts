/* Typed client + projection helpers for the P3-05 command center.
 *
 * The command center is a read-only projection over the real Business OS:
 * floor/room/desk tower templates, the agent roster, tasks, sessions and
 * approvals. It never invents a count, a floor, a room, a desk or an agent:
 * an unavailable source is a typed state, and an empty result is an empty
 * result. All reads go through `businessFetch` (the one office compat door).
 */
import {
  BusinessApiError,
  businessFetch,
  hostBasePath,
} from "./business-api";

export type CommandCenterKind =
  | "floor" | "room" | "desk" | "agent" | "task" | "approval";

export const COMMAND_CENTER_KINDS: readonly CommandCenterKind[] = [
  "floor", "room", "desk", "agent", "task", "approval",
];

export const KIND_LABELS: Record<CommandCenterKind, string> = {
  floor: "Floor",
  room: "Room",
  desk: "Desk",
  agent: "Agent",
  task: "Task",
  approval: "Approval",
};

export interface CommandCenterLink {
  floor_id: string | null;
  room_id: string | null;
  agent_id: string | null;
  path: string | null;
  reason: string | null;
}

export interface CommandCenterEntry {
  entry_id: string;
  kind: CommandCenterKind;
  title: string;
  subtitle: string;
  status: string;
  source: string;
  attention: boolean;
  floor_id: string | null;
  room_id: string | null;
  desk_id: string | null;
  agent_id: string | null;
  task_id: string | null;
  approval_id: string | null;
  link: CommandCenterLink;
  [key: string]: unknown;
}

export type SourceState = "ok" | "empty" | "unavailable";

export interface CommandCenterSource {
  state: SourceState;
  count: number;
  code: string | null;
  reason: string | null;
}

export interface RosterSummary {
  total: number;
  active: number;
  paused: number;
  archived: number;
  placed: number;
  unplaced: number;
  by_status: Record<string, number>;
  by_department: Record<string, number>;
}

export interface InboxItem {
  approval_id: string;
  action: string;
  requested_by: string;
  business_id: string;
  job_id: string | null;
  status: string;
  created_at: string;
  link: CommandCenterLink;
}

export interface CommandCenterInbox {
  counts: { approvals_pending: number; approvals_total: number };
  items: InboxItem[];
}

export interface CommandCenterProjection {
  schema: string;
  tenant_id: string;
  generated_at: string;
  state: "ok" | "empty";
  query: {
    q: string;
    kind: CommandCenterKind | null;
    status: string | null;
    floor_id: string | null;
    room_id: string | null;
    agent_id: string | null;
    task_id: string | null;
    approval_id: string | null;
    limit: number;
  };
  counts: {
    floors: number;
    rooms: number;
    desks: number;
    agents: number;
    tasks: number;
    sessions: number;
    approvals: number;
    approvals_pending: number;
    entries: number;
    matched: number;
    returned: number;
  };
  entries: CommandCenterEntry[];
  matched: number;
  truncated: boolean;
  roster: RosterSummary;
  inbox: CommandCenterInbox;
  sessions: { total: number; by_status: Record<string, number> };
  links: {
    route_template: string;
    floors: Array<{
      floor_id: string;
      name: string;
      wing: string;
      active: boolean;
      rooms: number;
      path: string | null;
    }>;
  };
  sources: Record<string, CommandCenterSource>;
}

export interface CommandCenterPayload {
  ok: true;
  schema: string;
  projection: CommandCenterProjection;
}

export type CommandCenterRead =
  | { kind: "loading" }
  | { kind: "available"; projection: CommandCenterProjection }
  | { kind: "unavailable"; code: string; detail: string }
  | { kind: "error"; message: string };

export interface CommandCenterQuery {
  q?: string;
  kind?: CommandCenterKind | "all" | null;
  status?: string | null;
  floor_id?: string | null;
  room_id?: string | null;
  agent_id?: string | null;
  task_id?: string | null;
  approval_id?: string | null;
  limit?: number;
}

export interface CommandCenterFetchOptions extends CommandCenterQuery {
  base?: string;
  fetchImpl?: typeof fetch;
  win?: Window | null;
  signal?: AbortSignal;
}

/** Only non-empty filters are sent; the backend owns matching and counts. */
export function cleanQuery(query: CommandCenterQuery = {}):
  Record<string, string | undefined> {
  const out: Record<string, string | undefined> = {};
  const text = (value: unknown) => String(value ?? "").trim();
  if (text(query.q)) out.q = text(query.q);
  const kind = text(query.kind);
  if (kind && kind !== "all") out.kind = kind;
  const status = text(query.status);
  if (status && status !== "all") out.status = status;
  if (text(query.floor_id)) out.floor_id = text(query.floor_id);
  if (text(query.room_id)) out.room_id = text(query.room_id);
  if (text(query.agent_id)) out.agent_id = text(query.agent_id);
  if (text(query.task_id)) out.task_id = text(query.task_id);
  if (text(query.approval_id)) out.approval_id = text(query.approval_id);
  if (query.limit !== undefined && Number.isFinite(Number(query.limit))) {
    out.limit = String(Math.max(1, Math.trunc(Number(query.limit))));
  }
  return out;
}

export async function fetchCommandCenter(
  opts: CommandCenterFetchOptions = {},
): Promise<CommandCenterRead> {
  try {
    const body = await businessFetch<CommandCenterPayload>("/command-center", {
      base: opts.base,
      fetchImpl: opts.fetchImpl,
      win: opts.win,
      signal: opts.signal,
      query: cleanQuery(opts),
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

// ---------------------------------------------------------------------------
// Local search / filters (same rules as the backend, for instant feedback)
// ---------------------------------------------------------------------------
export interface CommandCenterFilter {
  q?: string;
  kind?: CommandCenterKind | "all" | null;
  status?: string | null;
  floor_id?: string | null;
  room_id?: string | null;
  agent_id?: string | null;
  task_id?: string | null;
  approval_id?: string | null;
}

const norm = (value: unknown) => String(value ?? "").trim().toLowerCase();

function entryHaystack(entry: CommandCenterEntry): string {
  return [
    entry.entry_id, entry.kind, entry.title, entry.subtitle, entry.status,
    entry.floor_id, entry.room_id, entry.desk_id, entry.agent_id,
    entry.task_id, entry.approval_id,
  ].map(norm).filter(Boolean).join(" ");
}

export function entryMatches(
  entry: CommandCenterEntry, filter: CommandCenterFilter = {},
): boolean {
  const needle = norm(filter.q);
  if (needle && !entryHaystack(entry).includes(needle)) return false;
  const kind = norm(filter.kind);
  if (kind && kind !== "all" && entry.kind !== kind) return false;
  if (filter.status && norm(entry.status) !== norm(filter.status)) return false;
  if (filter.floor_id && norm(entry.floor_id) !== norm(filter.floor_id)) return false;
  if (filter.room_id && norm(entry.room_id) !== norm(filter.room_id)) return false;
  if (filter.agent_id && norm(entry.agent_id) !== norm(filter.agent_id)) return false;
  if (filter.task_id && norm(entry.task_id) !== norm(filter.task_id)) return false;
  if (filter.approval_id
      && norm(entry.approval_id) !== norm(filter.approval_id)) return false;
  return true;
}

export function filterEntries(
  entries: readonly CommandCenterEntry[] | undefined,
  filter: CommandCenterFilter = {},
): CommandCenterEntry[] {
  return (entries ?? []).filter((entry) => entryMatches(entry, filter));
}

export function statusOptions(
  entries: readonly CommandCenterEntry[] | undefined,
): string[] {
  const seen = new Set<string>();
  for (const entry of entries ?? []) {
    const value = String(entry.status ?? "").trim();
    if (value) seen.add(value);
  }
  return Array.from(seen).sort();
}

// ---------------------------------------------------------------------------
// Deep links
// ---------------------------------------------------------------------------
function withBase(path: string, base?: string): string {
  const raw = base === undefined ? hostBasePath() : base;
  const root = String(raw ?? "").replace(/\/+$/, "");
  const clean = path.startsWith("/") ? path : `/${path}`;
  return `${root}${clean}`;
}

export interface FloorDeepLink {
  floorId: string;
  roomId?: string | null;
  agentId?: string | null;
  base?: string;
}

/** Build the contract deep link: `/qubit-office/floor/<id>?room=&agent=`. */
export function floorDeepLink(link: FloorDeepLink): string {
  const floorId = String(link.floorId ?? "").trim();
  if (!floorId) return "";
  const params = new URLSearchParams();
  if (link.roomId) params.set("room", String(link.roomId));
  if (link.agentId) params.set("agent", String(link.agentId));
  const suffix = params.toString();
  return withBase(
    `/qubit-office/floor/${encodeURIComponent(floorId)}`
      + (suffix ? `?${suffix}` : ""),
    link.base,
  );
}

/** The entry's own link when the backend proved one; never a guessed floor. */
export function entryDeepLink(
  entry: CommandCenterEntry, base?: string,
): string | null {
  const path = entry.link?.path ?? null;
  if (path) return withBase(path, base);
  if (entry.floor_id) {
    return floorDeepLink({
      floorId: entry.floor_id,
      roomId: entry.room_id,
      agentId: entry.agent_id,
      base,
    });
  }
  return null;
}

// ---------------------------------------------------------------------------
// Keyboard model
// ---------------------------------------------------------------------------
export type CommandCenterAction =
  | "focus-search" | "close" | "move-next" | "move-prev"
  | "move-first" | "move-last" | "jump";

export const COMMAND_CENTER_SHORTCUTS: ReadonlyArray<{
  id: CommandCenterAction;
  keys: string;
  description: string;
}> = [
  { id: "focus-search", keys: "Ctrl/⌘ K or /", description: "Focus the office search" },
  { id: "move-next", keys: "ArrowDown", description: "Next result" },
  { id: "move-prev", keys: "ArrowUp", description: "Previous result" },
  { id: "move-first", keys: "Home", description: "First result" },
  { id: "move-last", keys: "End", description: "Last result" },
  { id: "jump", keys: "Enter", description: "Jump to the selected result" },
  { id: "close", keys: "Escape", description: "Close the command center" },
];

export interface KeyEventLike {
  key: string;
  ctrlKey?: boolean;
  metaKey?: boolean;
  altKey?: boolean;
  shiftKey?: boolean;
  target?: unknown;
}

export function isEditableTarget(target: unknown): boolean {
  const node = target as {
    tagName?: string; isContentEditable?: boolean;
    getAttribute?: (name: string) => string | null;
  } | null;
  if (!node) return false;
  const tag = String(node.tagName ?? "").toUpperCase();
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return true;
  if (node.isContentEditable) return true;
  const role = node.getAttribute?.("role");
  return role === "textbox" || role === "searchbox" || role === "combobox";
}

/** Map a key event to one command-center action, or `null`. */
export function shortcutAction(
  event: KeyEventLike | null | undefined,
): CommandCenterAction | null {
  if (!event) return null;
  const key = String(event.key ?? "");
  const command = Boolean(event.ctrlKey || event.metaKey);
  if (key === "Escape") return "close";
  if (command && (key === "k" || key === "K")) return "focus-search";
  if (key === "/" && !isEditableTarget(event.target)) return "focus-search";
  if (key === "ArrowDown") return "move-next";
  if (key === "ArrowUp") return "move-prev";
  if (key === "Home") return "move-first";
  if (key === "End") return "move-last";
  if (key === "Enter") return "jump";
  return null;
}

export function clampIndex(index: number, length: number): number {
  if (length <= 0) return -1;
  const value = Number.isFinite(index) ? Math.trunc(index) : 0;
  return Math.min(length - 1, Math.max(0, value));
}

export function nextIndex(
  index: number, length: number, delta: number,
): number {
  if (length <= 0) return -1;
  if (index < 0) return delta >= 0 ? 0 : length - 1;
  const start = clampIndex(index, length);
  return (start + delta + length) % length;
}

// ---------------------------------------------------------------------------
// No-fake-state labels and announcements
// ---------------------------------------------------------------------------
export function kindLabel(kind: string): string {
  return KIND_LABELS[kind as CommandCenterKind] ?? String(kind || "Record");
}

/** A text marker so status is never conveyed by color alone. */
export function statusMarker(status: unknown): string {
  switch (norm(status)) {
    case "pending":
    case "waiting":
    case "waiting_approval":
    case "approval":
      return "!";
    case "active":
    case "doing":
    case "live":
    case "running":
      return ">";
    case "done":
    case "approved":
    case "completed":
      return "v";
    case "paused":
      return "||";
    case "cancelled":
    case "rejected":
    case "expired":
    case "failed":
      return "x";
    case "open":
    case "vacant":
      return "o";
    case "dormant":
    case "archived":
      return "-";
    default:
      return "?";
  }
}

export function statusLabel(status: unknown): string {
  const value = String(status ?? "").trim();
  return value || "unknown";
}

export function isAttention(entry: CommandCenterEntry): boolean {
  return entry.attention === true
    || ["pending", "waiting", "waiting_approval"].includes(norm(entry.status));
}

export function unavailableSources(
  projection: CommandCenterProjection | null | undefined,
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

export function resultSummary(
  projection: CommandCenterProjection | null | undefined,
): string {
  if (!projection) return "No command-center projection.";
  const matched = Number(projection.matched ?? 0);
  const returned = Number(projection.counts?.returned ?? projection.entries?.length ?? 0);
  if (matched === 0) return "No floors, rooms, desks, agents, tasks or approvals match.";
  if (projection.truncated) {
    return `${matched} matches; showing the first ${returned}.`;
  }
  return `${matched} match${matched === 1 ? "" : "es"}.`;
}

export function rosterSummaryText(
  roster: RosterSummary | null | undefined,
): string {
  if (!roster) return "Roster unavailable.";
  return [
    `${roster.total} agent${roster.total === 1 ? "" : "s"}`,
    `${roster.active} active`,
    `${roster.paused} paused`,
    `${roster.archived} archived`,
    `${roster.placed} placed`,
    `${roster.unplaced} unplaced`,
  ].join(" · ");
}

export function inboxCount(
  projection: CommandCenterProjection | null | undefined,
): number {
  return Number(projection?.inbox?.counts?.approvals_pending ?? 0);
}

export function inboxSummaryText(
  projection: CommandCenterProjection | null | undefined,
): string {
  const count = inboxCount(projection);
  if (count === 0) return "No pending approvals.";
  return `${count} pending approval${count === 1 ? "" : "s"}.`;
}

export function announcement(
  read: CommandCenterRead,
  projection?: CommandCenterProjection | null,
): string {
  if (read.kind === "loading") return "Loading the command center…";
  if (read.kind === "error") {
    return `Command center error: ${read.message}`;
  }
  if (read.kind === "unavailable") {
    return `Command center unavailable: ${read.code} — ${read.detail}`;
  }
  return resultSummary(projection ?? read.projection);
}

export function shortcutHelp(): string {
  return COMMAND_CENTER_SHORTCUTS
    .map((item) => `${item.keys}: ${item.description}`)
    .join("; ");
}
