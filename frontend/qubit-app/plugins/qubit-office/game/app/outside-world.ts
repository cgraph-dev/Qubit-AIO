/* Typed client + pure view model for the Qubit Office outside world (P6-03).
 *
 * WHAT THIS IS
 * ------------
 * The semantic, no-canvas model behind `OutsideWorld.tsx`: it parses the
 * presentation projection produced by
 * `voicebridge/office/world/outside_world.py`, normalizes it, and exposes the
 * three things a panel needs — the environment (weather/day-night/season),
 * the active/dormant zone selector, and the lobby → tower floor links.
 *
 * WHAT THIS IS NOT
 * ----------------
 * It is not a renderer and not a second source of business truth. A zone, a
 * chunk or a weather cue is presentation only; the parser refuses a payload
 * that claims business truth or carries occupant/agent/run/desk fields. An
 * absent endpoint is an explicit `unavailable` state, never a fabricated
 * campus.
 *
 * Everything here is pure; only `fetchOutsideWorld` performs a read-only GET.
 */
import { readToken } from "./agents-panel";
import { API_PREFIX, hostBasePath } from "./business-api";

export const OUTSIDE_SCHEMA = "qubit-office-world-outside/1";
export const OUTSIDE_PATH = "/api/office/world/outside";

export type OutsideZoneState = "active" | "dormant";
export type OutsideFloorKind = "elevator" | "stairs" | string;

export interface OutsideEnvironmentEvent {
  event_id: string;
  kind: string;
  value: string;
  label: string;
  cue: string;
  still_cue: string;
  animated: boolean;
}

export interface OutsideEnvironment {
  weather: string;
  day_night: string;
  season: string;
  weather_values: string[];
  day_night_values: string[];
  season_values: string[];
  events: OutsideEnvironmentEvent[];
}

export interface OutsideBounds {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface OutsideZoneCounts {
  chunks: number;
  active_chunks: number;
  animated_chunks: number;
  tiles: number;
}

export interface OutsideZone {
  zone_id: string;
  kind: string;
  label: string;
  state: OutsideZoneState;
  animated: boolean;
  entry_chunk: string;
  chunk_ids: string[];
  bounds: OutsideBounds;
  counts: OutsideZoneCounts;
}

export interface OutsideChunk {
  chunk_id: string;
  zone_id: string;
  kind: string;
  layer: string;
  bounds: OutsideBounds;
  tiles: number;
  active: boolean;
  animated: boolean;
  state: OutsideZoneState;
  assets: string[];
  z_index: number;
}

export interface OutsidePlace {
  place_id: string;
  kind: string;
  label: string;
  zone_id: string;
  chunk_id: string;
  active: boolean;
  animated: boolean;
}

export interface OutsidePortal {
  portal_id: string;
  kind: string;
  from_chunk: string;
  to_chunk: string;
  to_floor: string;
  label: string;
  bidirectional: boolean;
  capacity: number;
  locked: boolean;
}

export interface OutsideFloorLink {
  kind: OutsideFloorKind;
  floor_id: string;
  portal_id: string;
  label: string;
}

export interface OutsideAsset {
  asset_id: string;
  feature: string;
  pack: string;
  paid: boolean;
  local_only: boolean;
  redistribution_allowed: boolean;
  license_status: string;
  credit: string;
}

export interface OutsideSummary {
  zones: number;
  active_zones: number;
  dormant_zones: number;
  chunks: number;
  active_chunks: number;
  dormant_chunks: number;
  animated_chunks: number;
  portals: number;
  elevator_links: number;
  stairs_links: number;
  places: number;
  assets: number;
}

export interface OutsideConstraints {
  presentation_only: boolean;
  business_truth: boolean;
  only_active_zones_animate: boolean;
  max_chunks: number;
  max_active_chunks: number;
  max_chunk_tiles: number;
  lru_capacity: number;
  preload_depth: number;
  lazy_load: boolean;
  reduced_motion_supported: boolean;
}

export interface OutsideWorld {
  schema: string;
  version: number;
  mode: "presentation";
  business_truth: false;
  map_digest: string;
  digest: string;
  credit: string;
  environment: OutsideEnvironment;
  zones: OutsideZone[];
  chunks: OutsideChunk[];
  places: OutsidePlace[];
  portals: OutsidePortal[];
  floor_links: OutsideFloorLink[];
  assets: OutsideAsset[];
  summary: OutsideSummary;
  constraints: OutsideConstraints;
}

// ------------------------------------------------------------------- errors
export type OutsideWorldErrorCode =
  | "OUTSIDE_WORLD_MALFORMED"
  | "OUTSIDE_WORLD_TRUTH_VIOLATION"
  | "OUTSIDE_WORLD_UNSUPPORTED_VERSION";

/** A typed refusal: a stable code a caller can branch on, never a bare Error. */
export class OutsideWorldError extends Error {
  readonly code: OutsideWorldErrorCode;

  constructor(code: OutsideWorldErrorCode, detail: string) {
    super(detail);
    this.name = "OutsideWorldError";
    this.code = code;
  }
}

// ------------------------------------------------------------------- status
export type OutsideWorldStatus =
  | "loading"
  | "ready"
  | "empty"
  | "error"
  | "unavailable";

export interface OutsideWorldView {
  status: OutsideWorldStatus;
  world: OutsideWorld | null;
  /** Human-readable, announced text. Never a stack trace. */
  message: string;
  /** Typed code for an error/unavailable state, else empty. */
  code: string;
}

// ------------------------------------------------------------------ helpers
const TRUTH_KEYS = new Set([
  "agent", "agents", "agent_id", "occupant", "occupants", "desk", "desks",
  "desk_id", "seat", "seats", "seat_id", "run", "run_id", "task", "task_id",
  "working", "completed", "business_id", "tenant_id", "session_id",
]);

function record(value: unknown): Record<string, any> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, any> : null;
}

function int(value: unknown, fallback = 0): number {
  const n = typeof value === "number" ? value
    : typeof value === "string" ? Number(value) : NaN;
  return Number.isFinite(n) ? Math.trunc(n) : fallback;
}

function text(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function strings(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string") : [];
}

function bool(value: unknown, fallback = false): boolean {
  return typeof value === "boolean" ? value : fallback;
}

function bounds(value: unknown): OutsideBounds {
  const row = record(value) ?? {};
  return {
    x: int(row.x), y: int(row.y),
    width: Math.max(0, int(row.width)), height: Math.max(0, int(row.height)),
  };
}

/** Walk every object key; used to refuse a payload that grew business truth. */
function findTruthKeys(value: unknown, found: Set<string> = new Set()): Set<string> {
  if (Array.isArray(value)) {
    for (const item of value) findTruthKeys(item, found);
    return found;
  }
  const row = record(value);
  if (!row) return found;
  for (const [key, item] of Object.entries(row)) {
    if (TRUTH_KEYS.has(key)) found.add(key);
    findTruthKeys(item, found);
  }
  return found;
}

// ------------------------------------------------------------------- parser
/** Normalize the server projection. Throws a typed `OutsideWorldError`. */
export function parseOutsideWorld(value: unknown): OutsideWorld {
  const root = record(value);
  if (!root) {
    throw new OutsideWorldError(
      "OUTSIDE_WORLD_MALFORMED", "The outside world payload is not an object.");
  }
  if (text(root.schema) !== OUTSIDE_SCHEMA) {
    throw new OutsideWorldError(
      "OUTSIDE_WORLD_UNSUPPORTED_VERSION",
      `Expected ${OUTSIDE_SCHEMA}, got ${text(root.schema) || "(none)"}.`);
  }
  if (int(root.version, 0) !== 1) {
    throw new OutsideWorldError(
      "OUTSIDE_WORLD_UNSUPPORTED_VERSION",
      `Unsupported outside world version ${int(root.version, 0)}.`);
  }
  if (root.mode !== "presentation" || root.business_truth !== false) {
    throw new OutsideWorldError(
      "OUTSIDE_WORLD_TRUTH_VIOLATION",
      "The outside world is presentation only; it may not claim business truth.");
  }
  const truth = findTruthKeys(root);
  if (truth.size) {
    throw new OutsideWorldError(
      "OUTSIDE_WORLD_TRUTH_VIOLATION",
      `The outside world carried business-truth keys: ${[...truth].sort().join(", ")}.`);
  }

  const env = record(root.environment) ?? {};
  const zones = Array.isArray(root.zones) ? root.zones : null;
  const chunks = Array.isArray(root.chunks) ? root.chunks : null;
  if (!zones || !chunks) {
    throw new OutsideWorldError(
      "OUTSIDE_WORLD_MALFORMED", "The outside world needs zones and chunks.");
  }

  const parsedZones: OutsideZone[] = zones.map((item, index) => {
    const row = record(item) ?? {};
    const zoneId = text(row.zone_id);
    if (!zoneId) {
      throw new OutsideWorldError(
        "OUTSIDE_WORLD_MALFORMED", `Zone ${index} has no zone_id.`);
    }
    const state = text(row.state) === "dormant" ? "dormant" : "active";
    const counts = record(row.counts) ?? {};
    return {
      zone_id: zoneId,
      kind: text(row.kind),
      label: text(row.label, zoneId),
      state,
      animated: bool(row.animated) && state === "active",
      entry_chunk: text(row.entry_chunk),
      chunk_ids: strings(row.chunk_ids),
      bounds: bounds(row.bounds),
      counts: {
        chunks: int(counts.chunks),
        active_chunks: int(counts.active_chunks),
        animated_chunks: int(counts.animated_chunks),
        tiles: int(counts.tiles),
      },
    };
  });

  const parsedChunks: OutsideChunk[] = chunks.map((item, index) => {
    const row = record(item) ?? {};
    const chunkId = text(row.chunk_id);
    if (!chunkId) {
      throw new OutsideWorldError(
        "OUTSIDE_WORLD_MALFORMED", `Chunk ${index} has no chunk_id.`);
    }
    const state = text(row.state) === "dormant" ? "dormant" : "active";
    return {
      chunk_id: chunkId,
      zone_id: text(row.zone_id),
      kind: text(row.kind),
      layer: text(row.layer),
      bounds: bounds(row.bounds),
      tiles: int(row.tiles),
      active: bool(row.active) && state === "active",
      animated: bool(row.animated) && state === "active",
      state,
      assets: strings(row.assets),
      z_index: int(row.z_index),
    };
  });

  const events: OutsideEnvironmentEvent[] = (
    Array.isArray(env.events) ? env.events : []
  ).map((item, index) => {
    const row = record(item) ?? {};
    return {
      event_id: text(row.event_id, `event-${index}`),
      kind: text(row.kind),
      value: text(row.value),
      label: text(row.label),
      cue: text(row.cue),
      still_cue: text(row.still_cue, text(row.cue)),
      animated: bool(row.animated),
    };
  });

  const places: OutsidePlace[] = (Array.isArray(root.places) ? root.places : [])
    .map((item, index) => {
      const row = record(item) ?? {};
      return {
        place_id: text(row.place_id, `place-${index}`),
        kind: text(row.kind),
        label: text(row.label),
        zone_id: text(row.zone_id),
        chunk_id: text(row.chunk_id),
        active: bool(row.active),
        animated: bool(row.animated),
      };
    });

  const portals: OutsidePortal[] = (Array.isArray(root.portals) ? root.portals : [])
    .map((item, index) => {
      const row = record(item) ?? {};
      return {
        portal_id: text(row.portal_id, `portal-${index}`),
        kind: text(row.kind),
        from_chunk: text(row.from_chunk),
        to_chunk: text(row.to_chunk),
        to_floor: text(row.to_floor),
        label: text(row.label),
        bidirectional: bool(row.bidirectional, true),
        capacity: int(row.capacity, 1),
        locked: bool(row.locked),
      };
    });

  const floorLinks: OutsideFloorLink[] = (
    Array.isArray(root.floor_links) ? root.floor_links : []
  ).map((item, index) => {
    const row = record(item) ?? {};
    return {
      kind: text(row.kind),
      floor_id: text(row.floor_id),
      portal_id: text(row.portal_id, `portal-${index}`),
      label: text(row.label),
    };
  });

  const assets: OutsideAsset[] = (Array.isArray(root.assets) ? root.assets : [])
    .map((item, index) => {
      const row = record(item) ?? {};
      return {
        asset_id: text(row.asset_id, `asset-${index}`),
        feature: text(row.feature),
        pack: text(row.pack),
        paid: bool(row.paid),
        local_only: bool(row.local_only),
        redistribution_allowed: bool(row.redistribution_allowed),
        license_status: text(row.license_status),
        credit: text(row.credit),
      };
    });

  const summaryRow = record(root.summary) ?? {};
  const summary: OutsideSummary = {
    zones: int(summaryRow.zones, parsedZones.length),
    active_zones: int(summaryRow.active_zones,
      parsedZones.filter((z) => z.state === "active").length),
    dormant_zones: int(summaryRow.dormant_zones,
      parsedZones.filter((z) => z.state === "dormant").length),
    chunks: int(summaryRow.chunks, parsedChunks.length),
    active_chunks: int(summaryRow.active_chunks,
      parsedChunks.filter((c) => c.active).length),
    dormant_chunks: int(summaryRow.dormant_chunks,
      parsedChunks.filter((c) => c.state === "dormant").length),
    animated_chunks: int(summaryRow.animated_chunks,
      parsedChunks.filter((c) => c.animated).length),
    portals: int(summaryRow.portals, portals.length),
    elevator_links: int(summaryRow.elevator_links,
      floorLinks.filter((f) => f.kind === "elevator").length),
    stairs_links: int(summaryRow.stairs_links,
      floorLinks.filter((f) => f.kind === "stairs").length),
    places: int(summaryRow.places, places.length),
    assets: int(summaryRow.assets, assets.length),
  };

  const constraintRow = record(root.constraints) ?? {};
  const constraints: OutsideConstraints = {
    presentation_only: bool(constraintRow.presentation_only, true),
    business_truth: bool(constraintRow.business_truth, false),
    only_active_zones_animate: bool(
      constraintRow.only_active_zones_animate, true),
    max_chunks: int(constraintRow.max_chunks),
    max_active_chunks: int(constraintRow.max_active_chunks),
    max_chunk_tiles: int(constraintRow.max_chunk_tiles),
    lru_capacity: int(constraintRow.lru_capacity),
    preload_depth: int(constraintRow.preload_depth),
    lazy_load: bool(constraintRow.lazy_load, true),
    reduced_motion_supported: bool(
      constraintRow.reduced_motion_supported, true),
  };

  return {
    schema: OUTSIDE_SCHEMA,
    version: 1,
    mode: "presentation",
    business_truth: false,
    map_digest: text(root.map_digest),
    digest: text(root.digest),
    credit: text(root.credit),
    environment: {
      weather: text(env.weather),
      day_night: text(env.day_night),
      season: text(env.season),
      weather_values: strings(env.weather_values),
      day_night_values: strings(env.day_night_values),
      season_values: strings(env.season_values),
      events,
    },
    zones: parsedZones,
    chunks: parsedChunks,
    places,
    portals,
    floor_links: floorLinks,
    assets,
    summary,
    constraints,
  };
}

// --------------------------------------------------------------------- view
/** Map loading/error/payload inputs to the single view the panel renders. */
export function outsideWorldView(input: {
  payload?: unknown;
  loading?: boolean;
  error?: string | null;
  code?: string;
}): OutsideWorldView {
  if (input.loading) {
    return {
      status: "loading",
      world: null,
      message: "Loading the campus outside world…",
      code: "",
    };
  }
  if (input.error) {
    return {
      status: "error",
      world: null,
      message: input.error,
      code: input.code || "OUTSIDE_WORLD_MALFORMED",
    };
  }
  if (input.payload === null || input.payload === undefined) {
    return {
      status: "unavailable",
      world: null,
      message: "The campus outside world is not available yet.",
      code: input.code || "OUTSIDE_WORLD_UNAVAILABLE",
    };
  }
  try {
    const world = parseOutsideWorld(input.payload);
    if (!world.zones.length) {
      return {
        status: "empty",
        world,
        message: "No outside-world zones are configured.",
        code: "",
      };
    }
    return {
      status: "ready",
      world,
      message: `Campus outside world: ${world.summary.active_zones} active `
        + `of ${world.summary.zones} zones.`,
      code: "",
    };
  } catch (exc) {
    const code = exc instanceof OutsideWorldError
      ? exc.code : "OUTSIDE_WORLD_MALFORMED";
    return {
      status: "error",
      world: null,
      message: exc instanceof Error ? exc.message
        : "The outside world payload could not be read.",
      code,
    };
  }
}

// -------------------------------------------------------------------- fetch
export function outsideWorldUrl(base?: string): string {
  const raw = base === undefined ? hostBasePath() : base;
  const b = raw.endsWith("/") ? raw.slice(0, -1) : raw;
  return `${b}${API_PREFIX}${OUTSIDE_PATH}`;
}

export interface OutsideFetchOptions {
  base?: string;
  fetchImpl?: typeof fetch;
  win?: Window | null;
  signal?: AbortSignal;
}

/**
 * Read-only GET of the outside-world projection. A 404 is `unavailable`, not a
 * fabricated campus; a malformed payload is a typed `error`.
 */
export async function fetchOutsideWorld(
  opts: OutsideFetchOptions = {},
): Promise<OutsideWorldView> {
  const f = opts.fetchImpl ?? fetch;
  const headers: Record<string, string> = { Accept: "application/json" };
  const token = readToken(opts.win);
  if (token) headers["X-Qubit-Session-Token"] = token;
  try {
    const res = await f(outsideWorldUrl(opts.base), {
      method: "GET", headers, credentials: "same-origin", signal: opts.signal,
    });
    if (res.status === 404) {
      return {
        status: "unavailable",
        world: null,
        message: "The campus outside world is not available yet.",
        code: "OUTSIDE_WORLD_UNAVAILABLE",
      };
    }
    if (!res.ok) {
      return {
        status: "error",
        world: null,
        message: `The outside world endpoint answered HTTP ${res.status}.`,
        code: `OUTSIDE_WORLD_HTTP_${res.status}`,
      };
    }
    const body = await res.json().catch(() => null);
    const payload = body && typeof body === "object"
      && "world" in (body as Record<string, unknown>)
      ? (body as Record<string, unknown>).world : body;
    return outsideWorldView({ payload });
  } catch (exc) {
    return {
      status: "unavailable",
      world: null,
      message: "The campus outside world could not be reached.",
      code: "OUTSIDE_WORLD_UNREACHABLE",
    };
  }
}

// ------------------------------------------------------------------- helpers
/** Semantic ordering: active zones first, then dormant, each by label. */
export function orderedZones(world: OutsideWorld): OutsideZone[] {
  return [...world.zones].sort((a, b) => {
    if (a.state !== b.state) return a.state === "active" ? -1 : 1;
    return a.label.localeCompare(b.label);
  });
}

export function zoneChunks(
  world: OutsideWorld, zoneId: string,
): OutsideChunk[] {
  return world.chunks.filter((chunk) => chunk.zone_id === zoneId);
}

/** The environment events that describe the current selection. */
export function currentEnvironment(
  world: OutsideWorld,
): OutsideEnvironmentEvent[] {
  const wanted: Array<[string, string]> = [
    ["weather", world.environment.weather],
    ["day_night", world.environment.day_night],
    ["season", world.environment.season],
  ];
  return wanted
    .map(([kind, value]) => world.environment.events.find(
      (event) => event.kind === kind && event.value === value) ?? null)
    .filter((event): event is OutsideEnvironmentEvent => event !== null);
}

/** The canonical floor route for a lobby link. */
export function floorHref(floorId: string): string {
  const safe = String(floorId || "").trim();
  return `/qubit-office/floor/${encodeURIComponent(safe)}`;
}

/** Non-color, text-first status label for a zone/chunk state. */
export function stateLabel(state: OutsideZoneState): string {
  return state === "active" ? "active" : "dormant";
}
