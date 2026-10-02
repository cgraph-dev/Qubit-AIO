/**
 * Open Viking console family API.
 *
 * Qubit-owned client for the Open Viking console pages (`/open-viking` and
 * its children). Every path below lives under `/api/qubit/viking/<action>`
 * (see {@link ROUTES}); each row is `LOCAL` — the bridge wires it onto our
 * own local context engine (sessions, skills, memory, resources), which is
 * always present. There is no server concept and no unconfigured state.
 *
 * A control whose bridge row answers 404 is rendered honestly disabled
 * with the missing-row reason (see {@link isBridgeRowMissing}) — never a
 * faked payload.
 *
 * Reads go through `fetchJSON`; mutations go through the shared
 * staged-confirmation round-trip (`runMutationWithConfirm`).
 *
 * License boundary: no upstream source is copied into this tree. This
 * module only describes our own bridge paths and normalizes whatever
 * JSON the bridge returns.
 */

import { fetchJSON, runMutationWithConfirm } from "@/lib/api";
import { refusalOrError } from "@/lib/policy-refusal";

// ── ROUTES manifest ──────────────────────────────────────────────────────

export type VikingFamilyAction =
  | "status"
  | "context_tree"
  | "file_get"
  | "retrieval_search"
  | "resource_add"
  | "memory_add"
  | "related_find"
  | "bot_sessions"
  | "skills_list";

export type VikingRouteMethod = "GET" | "POST";
export type VikingRouteStatus = "LOCAL";

export interface VikingFamilyRoute {
  family: "viking";
  action: VikingFamilyAction;
  method: VikingRouteMethod;
  path: string;
  status: VikingRouteStatus;
  /** Local Qubit backend the bridge wires this to. */
  counterpart: string;
}

/**
 * Every path this family client addresses. All rows are
 * `/api/qubit/viking/<action>` paths wired onto the local context engine.
 */
export const ROUTES: ReadonlyArray<VikingFamilyRoute> = [
  {
    family: "viking",
    action: "status",
    method: "GET",
    path: "/api/qubit/viking/status",
    status: "LOCAL",
    counterpart: "local context engine status (always present)",
  },
  {
    family: "viking",
    action: "context_tree",
    method: "GET",
    path: "/api/qubit/viking/context-tree",
    status: "LOCAL",
    counterpart: "local context tree (memories, bot, peers, entities, preferences, profiles, sessions, skills, resources)",
  },
  {
    family: "viking",
    action: "file_get",
    method: "GET",
    path: "/api/qubit/viking/file",
    status: "LOCAL",
    counterpart: "local context file fetch for the Preview/Source viewer",
  },
  {
    family: "viking",
    action: "retrieval_search",
    method: "GET",
    path: "/api/qubit/viking/retrieval-search",
    status: "LOCAL",
    counterpart: "local retrieval find/search over the context engine",
  },
  {
    family: "viking",
    action: "resource_add",
    method: "POST",
    path: "/api/qubit/viking/resource-add",
    status: "LOCAL",
    counterpart: "local resource ingest (confirm-gated)",
  },
  {
    family: "viking",
    action: "memory_add",
    method: "POST",
    path: "/api/qubit/viking/memory-add",
    status: "LOCAL",
    counterpart: "local memory write (confirm-gated)",
  },
  {
    family: "viking",
    action: "related_find",
    method: "GET",
    path: "/api/qubit/viking/related-find",
    status: "LOCAL",
    counterpart: "local related-context lookup over the context engine",
  },
  {
    family: "viking",
    action: "bot_sessions",
    method: "GET",
    path: "/api/qubit/viking/bot-sessions",
    status: "LOCAL",
    counterpart: "canonical session store (QubitSessionStore.list)",
  },
  {
    family: "viking",
    action: "skills_list",
    method: "GET",
    path: "/api/qubit/viking/skills",
    status: "LOCAL",
    counterpart: "local skill catalog",
  },
];

export function routePath(action: VikingFamilyAction): VikingFamilyRoute | undefined {
  return ROUTES.find((r) => r.action === action);
}

// ── missing-row honesty ──────────────────────────────────────────────────

function isRecord(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === "object" && !Array.isArray(value);
}

/**
 * True when a fetch failure (or payload) means the bridge row itself is
 * missing — typically an HTTP 404 from a not-yet-landed J1 row. Pages use
 * this to render the control honestly disabled with reason instead of a
 * generic error or invented data.
 */
export function isBridgeRowMissing(errorOrPayload: unknown): boolean {
  if (errorOrPayload instanceof Error) {
    const msg = errorOrPayload.message.toLowerCase();
    return (
      msg.includes("404") ||
      msg.includes("not found") ||
      msg.includes("no_route") ||
      msg.includes("no route") ||
      msg.includes("unknown path")
    );
  }
  if (!isRecord(errorOrPayload)) return false;
  const inner = isRecord(errorOrPayload.result)
    ? (errorOrPayload.result as Record<string, unknown>)
    : (errorOrPayload as Record<string, unknown>);
  const code = typeof inner.code === "string" ? inner.code.toUpperCase() : "";
  return code === "NOT_FOUND" || code === "NO_ROUTE" || code === "ROUTE_NOT_FOUND";
}

// ── shapes (defensive: pages normalize both envelope shapes) ─────────────

function asArray(value: unknown): Array<Record<string, unknown>> {
  return Array.isArray(value) ? (value as Array<Record<string, unknown>>) : [];
}

function str(value: unknown): string {
  return typeof value === "string" ? value : "";
}

export type VikingConnectionState = "connected" | "unknown";

export interface VikingStatus {
  connection: VikingConnectionState;
  detail?: string;
}

export function normalizeVikingStatus(payload: unknown): VikingStatus {
  if (!isRecord(payload)) return { connection: "unknown" };
  const inner = (isRecord(payload.result) ? payload.result : payload) as Record<string, unknown>;
  const raw = str(inner.connection || inner.state || inner.status).toLowerCase();
  const connection: VikingConnectionState =
    raw === "connected" || raw === "local" || raw === "ok" || raw === "ready" || raw === "available"
      ? "connected"
      : "unknown";
  return {
    connection,
    detail: str(inner.detail) || undefined,
  };
}

export interface VikingContextNode {
  id: string;
  kind: string;
  label: string;
  path?: string;
  children?: VikingContextNode[];
}

export type VikingContextGroup =
  | "user_memories"
  | "bot"
  | "peers"
  | "entities"
  | "preferences"
  | "profiles"
  | "sessions"
  | "skills"
  | "resources";

/** Context-tree groups shown in the console tree, in display order. */
export const VIKING_CONTEXT_GROUPS: ReadonlyArray<{ key: VikingContextGroup; label: string }> = [
  { key: "user_memories", label: "User memories" },
  { key: "bot", label: "Bot" },
  { key: "peers", label: "Peers" },
  { key: "entities", label: "Entities" },
  { key: "preferences", label: "Preferences" },
  { key: "profiles", label: "Profiles" },
  { key: "sessions", label: "Sessions" },
  { key: "skills", label: "Skills" },
  { key: "resources", label: "Resources" },
];

function normalizeNode(row: Record<string, unknown>): VikingContextNode {
  const kids = asArray(row.children).map(normalizeNode);
  const node: VikingContextNode = {
    id: str(row.id || row.key || row.name || row.label),
    kind: str(row.kind || row.group || row.type),
    label: str(row.label || row.name || row.title || row.id),
  };
  const path = str(row.path || row.viking_path || row.uri);
  if (path) node.path = path;
  if (kids.length > 0) node.children = kids;
  return node;
}

export function normalizeContextTree(payload: unknown): Partial<Record<VikingContextGroup, VikingContextNode[]>> {
  if (!isRecord(payload)) return {};
  const inner = (isRecord(payload.result) ? payload.result : payload) as Record<string, unknown>;
  const tree = isRecord(inner.tree) ? inner.tree : inner;
  const out: Partial<Record<VikingContextGroup, VikingContextNode[]>> = {};
  for (const { key } of VIKING_CONTEXT_GROUPS) {
    const rows = asArray(tree[key]);
    if (rows.length > 0) out[key] = rows.map(normalizeNode);
  }
  return out;
}

export interface VikingFilePayload {
  path: string;
  vikingUri: string;
  content: string;
  truncated: boolean;
}

/** Console `viking://` identity for a tree path (display only). */
export function toVikingUri(path: string): string {
  const trimmed = path.replace(/^\/+/, "");
  return `viking://${trimmed}`;
}

export function normalizeVikingFile(payload: unknown): VikingFilePayload | null {
  if (!isRecord(payload)) return null;
  const inner = (isRecord(payload.result) ? payload.result : payload) as Record<string, unknown>;
  const path = str(inner.path || inner.file || inner.name);
  if (!path) return null;
  const content = typeof inner.content === "string" ? inner.content : str(inner.preview);
  return {
    path,
    vikingUri: str(inner.viking_uri || inner.uri) || toVikingUri(path),
    content,
    truncated: inner.truncated === true,
  };
}

export interface VikingSearchHit {
  id: string;
  title: string;
  snippet?: string;
  path?: string;
  score?: number;
}

function num(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

export function normalizeSearchHits(payload: unknown): VikingSearchHit[] {
  if (!isRecord(payload)) return [];
  const inner = (isRecord(payload.result) ? payload.result : payload) as Record<string, unknown>;
  return asArray(inner.hits ?? inner.results ?? inner.items).map((row) => ({
    id: str(row.id || row.key || row.path || row.title),
    title: str(row.title || row.name || row.id || row.path),
    snippet: str(row.snippet || row.excerpt || row.preview) || undefined,
    path: str(row.path || row.uri) || undefined,
    score: num(row.score),
  }));
}

export interface VikingBotSession {
  id: string;
  title?: string;
  state?: string;
  updatedAt?: string;
}

export function normalizeBotSessions(payload: unknown): VikingBotSession[] {
  if (!isRecord(payload)) return [];
  const inner = (isRecord(payload.result) ? payload.result : payload) as Record<string, unknown>;
  return asArray(inner.sessions ?? inner.items).map((row) => ({
    id: str(row.id || row.session_id || row.key),
    title: str(row.title || row.name) || undefined,
    state: str(row.state || row.status) || undefined,
    updatedAt: str(row.updated_at || row.updatedAt) || undefined,
  }));
}

export interface VikingSkillRow {
  id: string;
  title?: string;
  description?: string;
}

export function normalizeVikingSkills(payload: unknown): VikingSkillRow[] {
  if (!isRecord(payload)) return [];
  const inner = (isRecord(payload.result) ? payload.result : payload) as Record<string, unknown>;
  return asArray(inner.skills ?? inner.items).map((row) => ({
    id: str(row.id || row.name || row.key),
    title: str(row.title || row.name) || undefined,
    description: str(row.description || row.summary) || undefined,
  }));
}

// ── reads ────────────────────────────────────────────────────────────────

function get(action: Extract<VikingFamilyAction, "status" | "context_tree" | "file_get" | "retrieval_search" | "related_find" | "bot_sessions" | "skills_list">, query?: Record<string, string>): Promise<unknown> {
  const route = routePath(action);
  if (!route) throw new Error(`unknown viking route ${action}`);
  const qs = query ? new URLSearchParams(query).toString() : "";
  return fetchJSON<unknown>(qs ? `${route.path}?${qs}` : route.path);
}

/** Local engine status: always present once the bridge row lands. */
export async function getVikingStatus(): Promise<unknown> {
  return get("status");
}

/** Full context tree grouped for the console tree. */
export async function getVikingContextTree(): Promise<unknown> {
  return get("context_tree");
}

/** One file for the Preview/Source viewer. */
export async function getVikingFile(path: string): Promise<unknown> {
  return get("file_get", { path });
}

/** Retrieval find/search. */
export async function searchVikingRetrieval(query: string, limit?: number): Promise<unknown> {
  const params: Record<string, string> = { q: query };
  if (limit !== undefined) params.limit = String(limit);
  return get("retrieval_search", params);
}

/** Related-context lookup for the Quick start action. */
export async function findVikingRelated(path: string): Promise<unknown> {
  return get("related_find", { path });
}

/** Bot sessions for the Agent Hub page. */
export async function listVikingBotSessions(): Promise<unknown> {
  return get("bot_sessions");
}

/** Skills catalog for the Agent Hub page. */
export async function listVikingSkills(): Promise<unknown> {
  return get("skills_list");
}

// ── confirm-gated mutations ──────────────────────────────────────────────

export type VikingConfirmPrompt = (detail: string) => boolean | Promise<boolean>;

function post(path: string, body: Record<string, unknown>): Promise<unknown> {
  return fetchJSON<unknown>(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

/** Add a resource (Uploads page Quick start): staged, confirm-gated. */
export async function addVikingResource(
  input: { uri: string; title?: string; notes?: string },
  confirm?: VikingConfirmPrompt,
): Promise<unknown> {
  const route = routePath("resource_add");
  if (!route) throw new Error("unknown viking route resource_add");
  return runMutationWithConfirm(
    () => post(route.path, { uri: input.uri, title: input.title, notes: input.notes }),
    confirm,
  );
}

/** Add a memory (Quick start): staged, confirm-gated. */
export async function addVikingMemory(
  input: { text: string; scope?: string },
  confirm?: VikingConfirmPrompt,
): Promise<unknown> {
  const route = routePath("memory_add");
  if (!route) throw new Error("unknown viking route memory_add");
  return runMutationWithConfirm(
    () => post(route.path, { text: input.text, scope: input.scope }),
    confirm,
  );
}

export { refusalOrError };
