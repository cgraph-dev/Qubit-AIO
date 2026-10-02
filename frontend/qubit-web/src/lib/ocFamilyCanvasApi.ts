// ROUTES MANIFEST — Qubit canvas family (integrator implements verbatim).
// Every HTTP path used by this module, with its method. All paths live under
// /api/qubit/canvas/<action>. No other paths are fetched from here.
//
//   Canvas file host (reads):
//     GET  /api/qubit/canvas/list                 (canvas.list)            NEEDS-BRIDGE
//     GET  /api/qubit/canvas/get                  (canvas.get, ?path=)     NEEDS-BRIDGE
//     GET  /api/qubit/canvas/live                 (canvas.live)            NEEDS-BRIDGE
//
//   Canvas file host (confirm-gated writes, brokered + path-confined):
//     POST /api/qubit/canvas/add                  (canvas.add)             NEEDS-BRIDGE
//     POST /api/qubit/canvas/remove               (canvas.remove)          NEEDS-BRIDGE
//
//   Node presentation (confirm-gated, mirrors the node capability-URL flow;
//   the scoped base URL comes from the existing node roster surface):
//     POST /api/qubit/canvas/present              (canvas.present)         NEEDS-BRIDGE
//     POST /api/qubit/canvas/hide                 (canvas.hide)            NEEDS-BRIDGE
//     POST /api/qubit/canvas/navigate             (canvas.navigate)        NEEDS-BRIDGE
//     POST /api/qubit/canvas/snapshot             (canvas.snapshot)        NEEDS-BRIDGE
//
// Bridge state: verified read-only that the backend serves no canvas route,
// so every row above is NEEDS-BRIDGE — the UI renders the staged state
// honestly and never fakes a success. Node identity itself is read through
// the EXISTING node roster routes (no new node client here); only the scoped
// presentation base is supplied per call once the bridge serves it.
//
// Behavior ported from the upstream canvas extension (read-only source):
// local file host rooted at a server-owned directory, live-reload semantics
// reported by the host, entry-URL sanitizing, and node capability-URL
// scoping. Transports are mirrored, never invented: this module issues only
// fetchJSON reads and runMutationWithConfirm writes (the shared staged-
// confirmation round-trip), plus pure URL-string builders for the preview
// frame (an iframe src is not an HTTP client).

import { fetchJSON, runMutationWithConfirm } from "@/lib/api";
import { refusalOrError } from "@/lib/policy-refusal";

// ── ROUTES manifest ──────────────────────────────────────────────────────

export type CanvasRouteMethod = "GET" | "POST";

export interface CanvasRoute {
  family: "canvas";
  action: string;
  method: CanvasRouteMethod;
  path: string;
  /** Backend bridge state. All rows are NEEDS-BRIDGE until wired. */
  bridge: "NEEDS-BRIDGE";
}

/** Every path + method this canvas client addresses. */
export const ROUTES: ReadonlyArray<CanvasRoute> = [
  { family: "canvas", action: "list", method: "GET", path: "/api/qubit/canvas/list", bridge: "NEEDS-BRIDGE" },
  { family: "canvas", action: "get", method: "GET", path: "/api/qubit/canvas/get", bridge: "NEEDS-BRIDGE" },
  { family: "canvas", action: "live", method: "GET", path: "/api/qubit/canvas/live", bridge: "NEEDS-BRIDGE" },
  { family: "canvas", action: "add", method: "POST", path: "/api/qubit/canvas/add", bridge: "NEEDS-BRIDGE" },
  { family: "canvas", action: "remove", method: "POST", path: "/api/qubit/canvas/remove", bridge: "NEEDS-BRIDGE" },
  { family: "canvas", action: "present", method: "POST", path: "/api/qubit/canvas/present", bridge: "NEEDS-BRIDGE" },
  { family: "canvas", action: "hide", method: "POST", path: "/api/qubit/canvas/hide", bridge: "NEEDS-BRIDGE" },
  { family: "canvas", action: "navigate", method: "POST", path: "/api/qubit/canvas/navigate", bridge: "NEEDS-BRIDGE" },
  { family: "canvas", action: "snapshot", method: "POST", path: "/api/qubit/canvas/snapshot", bridge: "NEEDS-BRIDGE" },
];

export function routePath(action: string): CanvasRoute | undefined {
  return ROUTES.find((r) => r.action === action);
}

function url(action: string, query?: Record<string, string>): string {
  const route = routePath(action);
  if (!route) throw new Error(`unknown canvas route ${action}`);
  if (!query) return route.path;
  const qs = new URLSearchParams(query).toString();
  return qs ? `${route.path}?${qs}` : route.path;
}

function post(path: string, body: Record<string, unknown>): Promise<unknown> {
  return fetchJSON<unknown>(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

// ── mirrored host shape (pure strings; NEEDS-BRIDGE) ─────────────────────
// Mirrors the upstream local-host + capability-scope URL semantics,
// rebranded. No backend serves these paths yet (see ROUTES above), so the
// page only uses them to build preview strings, never to fetch.

/** Served host path for canvas entries (mirrored shape, NEEDS-BRIDGE). */
export const CANVAS_HOST_PATH = "/__qubit__/canvas";
/** Scoped capability path prefix for node presentation (mirrored, NEEDS-BRIDGE). */
export const CANVAS_SCOPED_PATH_PREFIX = "/__qubit__/cap";

/**
 * Canvas root label. The root is a server-owned state-home subdirectory —
 * never the PM-live home, never outside approved roots. The client never
 * touches disk; this label is display-only until the bridge serves `root`.
 */
export const CANVAS_ROOT_LABEL = "state-home canvas/";

function isCanvasHostPath(pathname: string): boolean {
  return pathname === CANVAS_HOST_PATH || pathname.startsWith(`${CANVAS_HOST_PATH}/`);
}

/**
 * Keep only same-host canvas entry URLs (path + query + fragment); anything
 * else (external, or a non-canvas path) is refused client-side.
 */
export function sanitizeCanvasEntryUrl(rawEntryUrl: string): string | undefined {
  try {
    const entry = new URL(rawEntryUrl, "http://localhost");
    if (entry.origin !== "http://localhost") return undefined;
    if (!isCanvasHostPath(entry.pathname)) return undefined;
    return `${entry.pathname}${entry.search}${entry.hash}`;
  } catch {
    return undefined;
  }
}

/**
 * Resolve the preview URL for an entry, scoping it under a node capability
 * base URL when one is supplied (mirrors the upstream scoped-host flow).
 * Falls back to the sanitized same-host entry path.
 */
export function resolveCanvasPreviewUrl(
  entryUrl: string | undefined,
  scopedBaseUrl?: string | null,
): string | undefined {
  const raw = entryUrl?.trim();
  if (!raw) return undefined;
  const safe = sanitizeCanvasEntryUrl(raw);
  if (!safe) return undefined;
  if (!scopedBaseUrl?.trim()) return safe;
  try {
    const scoped = new URL(scopedBaseUrl);
    const prefix = scoped.pathname.replace(/\/+$/, "");
    if (!prefix.startsWith(CANVAS_SCOPED_PATH_PREFIX)) return safe;
    const entry = new URL(safe, scoped.origin);
    if (!isCanvasHostPath(entry.pathname)) return safe;
    entry.protocol = scoped.protocol;
    entry.username = scoped.username;
    entry.password = scoped.password;
    entry.host = scoped.host;
    entry.pathname = `${prefix}${entry.pathname}`;
    return entry.toString();
  } catch {
    return safe;
  }
}

/** Entry path for a canvas-root file (mirrored host shape). */
export function canvasEntryPath(name: string): string {
  return `${CANVAS_HOST_PATH}/${name}`;
}

// ── client-side path confinement ─────────────────────────────────────────
// Mirrors the upstream document logical-path guard: the page refuses
// traversal/absolute/odd names before any mutation is issued.

function hasControlCharacter(value: string): boolean {
  for (const char of value) {
    const code = char.charCodeAt(0);
    if (code < 0x20 || code === 0x7f) return true;
  }
  return false;
}

/** True when a canvas-root file name is safe to send (no traversal). */
export function isCanvasNameSafe(name: string): boolean {
  if (!name) return false;
  const normalized = name.replaceAll("\\", "/");
  // Absolute paths (either slash flavor) are refused, never relativized.
  if (normalized.startsWith("/")) return false;
  const parts = normalized.split("/").filter(Boolean);
  if (parts.length === 0) return false;
  if (parts.some((p) => p === "." || p === ".." || p.includes(":"))) return false;
  if (parts.some((p) => hasControlCharacter(p))) return false;
  if (/^[A-Za-z][A-Za-z0-9+.-]*:/.test(normalized)) return false;
  return true;
}

export function canvasNameRefusal(name: string): string {
  return `Refused client-side: "${name}" is not a safe canvas-root name (no traversal, absolute paths, or odd segments).`;
}

// ── shapes (defensive: the page normalizes both envelope shapes) ─────────

export interface CanvasFile {
  name: string;
  path: string;
  size_bytes?: number;
  updated_at?: string;
}

export interface CanvasListResponse {
  files: CanvasFile[];
  root?: string;
  live_reload?: boolean;
  authority?: string;
}

export interface CanvasFileResponse {
  path: string;
  content: string;
  truncated?: boolean;
}

export interface CanvasLiveResponse {
  live_reload: boolean;
  root?: string;
}

function asArray(value: unknown): Array<Record<string, unknown>> {
  return Array.isArray(value) ? (value as Array<Record<string, unknown>>) : [];
}

function str(value: unknown): string {
  return typeof value === "string" ? value : "";
}

export function normalizeCanvasFiles(payload: unknown): CanvasFile[] {
  if (!payload || typeof payload !== "object") return [];
  const env = payload as Record<string, unknown>;
  const inner = (env.result ?? env) as Record<string, unknown>;
  return asArray(inner.files).map((f) => ({
    name: str(f.name ?? f.path ?? f.id),
    path: str(f.path ?? f.name ?? ""),
    size_bytes: typeof f.size_bytes === "number" ? f.size_bytes : undefined,
    updated_at: str(f.updated_at ?? f.at ?? ""),
  }));
}

export function normalizeCanvasList(payload: unknown): CanvasListResponse {
  if (!payload || typeof payload !== "object") return { files: [] };
  const env = payload as Record<string, unknown>;
  const inner = (env.result ?? env) as Record<string, unknown>;
  return {
    files: normalizeCanvasFiles(payload),
    root: str(inner.root ?? CANVAS_ROOT_LABEL) || CANVAS_ROOT_LABEL,
    live_reload: typeof inner.live_reload === "boolean" ? inner.live_reload : undefined,
    authority: str(inner.authority ?? ""),
  };
}

// ── confirm plumbing ─────────────────────────────────────────────────────

export type ConfirmPrompt = (detail: string) => boolean | Promise<boolean>;

function mutate(path: string, body: Record<string, unknown>, confirm?: ConfirmPrompt): Promise<unknown> {
  return runMutationWithConfirm(() => post(path, body), confirm);
}

// ── reads ────────────────────────────────────────────────────────────────

/** List the canvas-root files (canvas.list). */
export async function listCanvasFiles(): Promise<unknown> {
  return fetchJSON<unknown>(url("list"));
}

/** Read one canvas-root file (canvas.get). */
export async function getCanvasFile(path: string): Promise<unknown> {
  return fetchJSON<unknown>(url("get", { path }));
}

/** Read the host live-reload state (canvas.live). */
export async function getCanvasLiveState(): Promise<unknown> {
  return fetchJSON<unknown>(url("live"));
}

// ── writes (all confirm-gated, brokered, path-confined) ──────────────────

export interface CanvasAddInput {
  path: string;
  content: string;
}

/** Add (or replace) a canvas-root file (canvas.add, confirm-gated). */
export async function addCanvasFile(input: CanvasAddInput, confirm?: ConfirmPrompt): Promise<unknown> {
  if (!isCanvasNameSafe(input.path)) throw new Error(canvasNameRefusal(input.path));
  return mutate(url("add"), { path: input.path, content: input.content }, confirm);
}

/** Remove a canvas-root file (canvas.remove, confirm-gated). */
export async function removeCanvasFile(path: string, confirm?: ConfirmPrompt): Promise<unknown> {
  if (!isCanvasNameSafe(path)) throw new Error(canvasNameRefusal(path));
  return mutate(url("remove"), { path }, confirm);
}

// ── node presentation (all confirm-gated) ────────────────────────────────

export interface CanvasPresentInput {
  node_id: string;
  entry: string;
  scoped_base_url?: string;
}

/** Show a canvas entry on a node (canvas.present, confirm-gated). */
export async function presentCanvas(input: CanvasPresentInput, confirm?: ConfirmPrompt): Promise<unknown> {
  return mutate(url("present"), { ...input }, confirm);
}

/** Hide the node canvas (canvas.hide, confirm-gated). */
export async function hideCanvas(nodeId: string, confirm?: ConfirmPrompt): Promise<unknown> {
  return mutate(url("hide"), { node_id: nodeId }, confirm);
}

/** Open a new URL on the node canvas (canvas.navigate, confirm-gated). */
export async function navigateCanvas(
  nodeId: string,
  entry: string,
  confirm?: ConfirmPrompt,
): Promise<unknown> {
  return mutate(url("navigate"), { node_id: nodeId, entry }, confirm);
}

/** Capture a node canvas screenshot proof (canvas.snapshot, confirm-gated). */
export async function snapshotCanvas(nodeId: string, confirm?: ConfirmPrompt): Promise<unknown> {
  return mutate(url("snapshot"), { node_id: nodeId }, confirm);
}

export { refusalOrError };
