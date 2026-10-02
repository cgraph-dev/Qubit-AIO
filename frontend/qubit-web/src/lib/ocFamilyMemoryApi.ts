/**
 * Qubit memory-backends family API.
 *
 * Separate Qubit-branded client for the memory-backends page
 * (`/qubit-memory`). This is intentionally NOT part of the plugins surface:
 * it ports the upstream memory extensions (`extensions/memory-lancedb`,
 * `extensions/memory-wiki`, `extensions/active-memory` — read-only source)
 * as local-only store wiring behind the existing memory-provider typed gate.
 *
 * Gate truth (reused exactly, never reinvented — see `voicebridge/`):
 * - `GET /api/memory` answers the live local store status with
 *   `active: "qubit-local"` (the only provider).
 * - `PUT /api/memory/provider` persists only `"qubit-local"`; any other name
 *   is a typed `400 MEMORY_LOCAL_ONLY` ("qubit-local is the only memory
 *   provider; cloud sync is refused by policy").
 * - `GET /api/memory/providers/<id>/config`, `PUT .../config`, and
 *   `POST .../setup` answer only `"qubit-local"`; any other name is the same
 *   typed `MEMORY_LOCAL_ONLY` gate (never a fake 200).
 * - `GET /api/memory/providers/<name>` (status) answers only `"qubit-local"`;
 *   any other name is a typed `404 NOT_FOUND`.
 *
 * Consequence: the gate exposes NO selectable backends — this page is status
 * plus disabled rows. There is deliberately no writer for lancedb / wiki /
 * active-memory here; anything needing hosted infra or secrets renders
 * disabled-with-reason, never a fake connect button.
 *
 * Reads go through `fetchJSON`; the select mutation goes through the shared
 * staged-confirmation round-trip (`runMutationWithConfirm`).
 */

import { fetchJSON, runMutationWithConfirm } from "@/lib/api";
import { refusalOrError } from "@/lib/policy-refusal";

// ── ROUTES manifest ──────────────────────────────────────────────────────
// Qubit namespace, served by family_memory.py (aliases over the canonical
// local surface). The client keeps calling the live canonical path in
// `via` (same store, same data); `bridge: "LIVE"` records that every
// `/api/qubit/memory/*` row is served too.

export type MemoryAction =
  | "status"
  | "provider"
  | "provider_config"
  | "provider_set"
  | "probe";

export interface MemoryRoute {
  action: MemoryAction;
  method: "GET" | "POST";
  path: string;
  /** Live canonical path the client calls (same store the bridge serves). */
  via: string;
  bridge: "LIVE";
}

/** Every path + method this family client wants, under `/api/qubit/memory/`. */
export const ROUTES: ReadonlyArray<MemoryRoute> = [
  { action: "status", method: "GET", path: "/api/qubit/memory/status", via: "GET /api/memory", bridge: "LIVE" },
  { action: "provider", method: "GET", path: "/api/qubit/memory/provider", via: "GET /api/memory", bridge: "LIVE" },
  { action: "provider_config", method: "GET", path: "/api/qubit/memory/provider-config", via: "GET /api/memory/providers/<id>/config", bridge: "LIVE" },
  { action: "provider_set", method: "POST", path: "/api/qubit/memory/provider-set", via: "PUT /api/memory/provider", bridge: "LIVE" },
  { action: "probe", method: "GET", path: "/api/qubit/memory/probe", via: "GET /api/memory/providers/qubit-local", bridge: "LIVE" },
];

export function routePath(action: MemoryAction): MemoryRoute | undefined {
  return ROUTES.find((r) => r.action === action);
}

// ── shapes (defensive: the page normalizes both envelope shapes) ─────────

export interface QubitLocalProviderRow {
  name: string;
  description: string;
  available: boolean;
  configured: boolean;
  status: string;
}

export interface MemoryStatusPayload {
  active: string;
  providers: QubitLocalProviderRow[];
  builtinFiles: { memory: number; user: number };
  state: string;
  enabled: boolean;
  storeState: string;
  storePath: string;
}

export interface ProviderStatusPayload {
  provider: string;
  active: string;
  tiers: string[];
  state: string;
  enabled: boolean;
  storeState: string;
  detail: string;
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" ? (value as Record<string, unknown>) : {};
}

function str(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function num(value: unknown): number {
  return typeof value === "number" ? value : 0;
}

function bool(value: unknown, fallback: boolean): boolean {
  return typeof value === "boolean" ? value : fallback;
}

function asArray(value: unknown): Array<Record<string, unknown>> {
  return Array.isArray(value) ? (value as Array<Record<string, unknown>>) : [];
}

export function normalizeMemoryStatus(payload: unknown): MemoryStatusPayload | null {
  const env = asRecord(payload);
  const inner = asRecord(env.result ?? env);
  if (!inner.active && !inner.providers) return null;
  const builtin = asRecord(inner.builtin_files ?? inner.builtinFiles);
  return {
    active: str(inner.active ?? "qubit-local"),
    providers: asArray(inner.providers).map((p) => ({
      name: str(p.name ?? "qubit-local"),
      description: str(p.description ?? ""),
      available: bool(p.available, false),
      configured: bool(p.configured, false),
      status: str(p.status ?? ""),
    })),
    builtinFiles: { memory: num(builtin.memory), user: num(builtin.user) },
    state: str(inner.state ?? inner.status ?? ""),
    enabled: bool(inner.enabled, true),
    storeState: str(inner.store_state ?? inner.storeState ?? ""),
    storePath: str(inner.store_path ?? inner.storePath ?? ""),
  };
}

export function normalizeProviderStatus(payload: unknown): ProviderStatusPayload | null {
  const env = asRecord(payload);
  const inner = asRecord(env.result ?? env);
  if (!inner.provider && !inner.active) return null;
  const tiers = inner.tiers;
  return {
    provider: str(inner.provider ?? inner.active ?? "qubit-local"),
    active: str(inner.active ?? inner.provider ?? "qubit-local"),
    tiers: Array.isArray(tiers) ? tiers.filter((t): t is string => typeof t === "string") : [],
    state: str(inner.state ?? ""),
    enabled: bool(inner.enabled, true),
    storeState: str(inner.store_state ?? inner.storeState ?? ""),
    detail: str(inner.detail ?? ""),
  };
}

// ── live reads (canonical paths; see ROUTES `via`) ────────────────────────

export async function getMemoryStatus(): Promise<unknown> {
  return fetchJSON<unknown>("/api/memory");
}

export async function getProviderStatus(name: string): Promise<unknown> {
  return fetchJSON<unknown>(`/api/memory/providers/${encodeURIComponent(name)}`);
}

export async function getProviderConfig(name: string): Promise<unknown> {
  return fetchJSON<unknown>(`/api/memory/providers/${encodeURIComponent(name)}/config`);
}

// ── confirm plumbing ─────────────────────────────────────────────────────

export type ConfirmPrompt = (detail: string) => boolean | Promise<boolean>;

function put(path: string, body: Record<string, unknown>): Promise<unknown> {
  return fetchJSON<unknown>(path, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

/**
 * Select the memory provider. Confirm-gated, local-only: the gate persists
 * only `"qubit-local"` and answers any other name with the typed
 * `MEMORY_LOCAL_ONLY` refusal (surfaced via `refusalOrError`, never faked).
 */
export async function setMemoryProvider(
  provider: string,
  confirm?: ConfirmPrompt,
): Promise<unknown> {
  return runMutationWithConfirm(
    () => put("/api/memory/provider", { provider }),
    confirm,
  );
}

// ── backend rows (what the page renders) ─────────────────────────────────
// `selectable` is true ONLY for `qubit-local`: the gate refuses every other
// name, so every other row is disabled-with-reason by construction.

export type BackendId = "qubit-local" | "lancedb" | "wiki" | "active-memory";

export interface MemoryBackendRow {
  id: BackendId;
  title: string;
  blurb: string;
  localOnly: boolean;
  selectable: boolean;
  disabledReason: string;
  probeKind: "live" | "disabled";
  probeDetail: string;
}

export const MEMORY_BACKENDS: ReadonlyArray<MemoryBackendRow> = [
  {
    id: "qubit-local",
    title: "Qubit local memory",
    blurb: "Local-only SQLite/FTS5 store (core / working / recall / archive tiers). The only selectable provider.",
    localOnly: true,
    selectable: true,
    disabledReason: "",
    probeKind: "live",
    probeDetail: "Live read probe via GET /api/memory.",
  },
  {
    id: "lancedb",
    title: "LanceDB vector memory",
    blurb: "Upstream vector long-term memory port (auto-recall, auto-capture, vector search).",
    localOnly: false,
    selectable: false,
    disabledReason: "Needs hosted embedding infra and an API key (secret) plus native vector deps — refused by policy, no writer exists.",
    probeKind: "disabled",
    probeDetail: "No live probe: no memory-provider route serves this backend.",
  },
  {
    id: "wiki",
    title: "Memory wiki",
    blurb: "Upstream persistent wiki-compiler port (knowledge vault).",
    localOnly: true,
    selectable: false,
    disabledReason: "No memory-provider writer exists; reads live under the separate plugin namespace, out of scope for this page.",
    probeKind: "disabled",
    probeDetail: "No live probe on this page: kept separate from the plugins surface by owner rule.",
  },
  {
    id: "active-memory",
    title: "Active memory",
    blurb: "Upstream bounded memory sub-agent port (injects relevant memory into prompt context).",
    localOnly: true,
    selectable: false,
    disabledReason: "No selectable writer behind the gate — the gate persists qubit-local only.",
    probeKind: "disabled",
    probeDetail: "Covered by the live GET /api/memory store-state read; no per-backend probe route exists.",
  },
];

// ── verbatim gateway refusals (disabled-with-reason rows) ────────────────
// Quoted exactly from the handler returns so the page can never drift.

export interface GatewayRefusal {
  method: string;
  code: string;
  detail: string;
  prerequisite: string;
  counterpart: string;
}

export const MEMORY_LOCAL_ONLY_REFUSAL: GatewayRefusal = {
  method: "PUT /api/memory/provider",
  code: "MEMORY_LOCAL_ONLY",
  detail: "qubit-local is the only memory provider; cloud sync is refused by policy",
  prerequisite: "none — local-only store, no setup needed",
  counterpart: "Qubit local memory (same local store)",
};

export const MEMORY_PROVIDER_NOT_FOUND_REFUSAL: GatewayRefusal = {
  method: "GET /api/memory/providers/<name>",
  code: "NOT_FOUND",
  detail: "no memory provider '<name>'; qubit-local is the only store",
  prerequisite: "none — local-only store",
  counterpart: "Qubit local memory (same local store)",
};

export { refusalOrError };
