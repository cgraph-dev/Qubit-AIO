/* P3-04 — renderer-neutral floor model and culling.
 *
 * WHAT THIS IS
 * ------------
 * The pure part of the office floor renderer: one active floor, camera bounds
 * clamped to that floor, room/chunk visibility, a bounded LRU cache, static
 * bake / dirty tracking, device tiers, a context-loss state machine and reduced
 * motion. It holds no canvas, Phaser or DOM reference, so the same rules apply
 * to the shipped renderer, a future renderer and the Python verifier.
 *
 * Every function is deterministic: the same arguments produce the same result.
 * Nothing here reads a clock, a device or the network; a caller passes `now`
 * explicitly where time matters. A room is drawn only when the caller asks for
 * the visible set — there is no "draw every floor" path.
 */
import { tierLimits, type DeviceTier } from "./renderer-budget";

// ------------------------------------------------------------------- errors
export type RendererFloorErrorCode =
  | "RENDERER_FLOOR_EMPTY"
  | "RENDERER_FLOOR_UNKNOWN"
  | "RENDERER_CACHE_CAPACITY";

/** A typed refusal. `code` is stable and testable; never a bare `Error`. */
export class RendererFloorError extends Error {
  readonly code: RendererFloorErrorCode;

  constructor(code: RendererFloorErrorCode, detail: string) {
    super(detail);
    this.name = "RendererFloorError";
    this.code = code;
  }
}

// ------------------------------------------------------------------- bounds
export interface Bounds {
  minX: number;
  minY: number;
  maxX: number;
  maxY: number;
}

export interface Size {
  width: number;
  height: number;
}

export interface Camera extends Size {
  /** World coordinate of the viewport's top-left corner. */
  x: number;
  y: number;
}

export interface CellRect {
  cell_id: string;
  origin: { x: number; y: number };
  cols: number;
  rows: number;
}

export interface RoomRects {
  room_id: string;
  cells: readonly CellRect[];
}

export function roomBounds(room: RoomRects): Bounds {
  if (!room.cells.length) {
    return { minX: 0, minY: 0, maxX: 0, maxY: 0 };
  }
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  for (const cell of room.cells) {
    minX = Math.min(minX, cell.origin.x);
    minY = Math.min(minY, cell.origin.y);
    maxX = Math.max(maxX, cell.origin.x + cell.cols);
    maxY = Math.max(maxY, cell.origin.y + cell.rows);
  }
  return { minX, minY, maxX, maxY };
}

export function floorBounds(rooms: readonly RoomRects[]): Bounds {
  if (!rooms.length) return { minX: 0, minY: 0, maxX: 0, maxY: 0 };
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  for (const room of rooms) {
    const bounds = roomBounds(room);
    minX = Math.min(minX, bounds.minX);
    minY = Math.min(minY, bounds.minY);
    maxX = Math.max(maxX, bounds.maxX);
    maxY = Math.max(maxY, bounds.maxY);
  }
  return { minX, minY, maxX, maxY };
}

/** Clamp the camera so the viewport stays inside the active floor. When the
 *  floor is narrower/shorter than the viewport, centre it rather than expose
 *  an invented outside area. Pure and deterministic. */
export function clampCamera(camera: Camera, bounds: Bounds): Camera {
  const width = Math.max(0, camera.width);
  const height = Math.max(0, camera.height);
  const clampedX = clampAxis(camera.x, width, bounds.minX, bounds.maxX);
  const clampedY = clampAxis(camera.y, height, bounds.minY, bounds.maxY);
  return { x: clampedX, y: clampedY, width, height };
}

function clampAxis(
  value: number,
  size: number,
  min: number,
  max: number,
): number {
  const span = max - min;
  if (span <= size) return min - (size - span) / 2;
  return Math.min(Math.max(value, min), max - size);
}

function intersects(a: Bounds, b: Bounds): boolean {
  return a.minX < b.maxX && a.maxX > b.minX && a.minY < b.maxY
    && a.maxY > b.minY;
}

/** Rooms whose footprint intersects the camera. No camera means no room is
 *  visible — culling is explicit, never "all". */
export function visibleRooms<T extends RoomRects>(
  rooms: readonly T[],
  camera: Camera,
): T[] {
  const view: Bounds = {
    minX: camera.x,
    minY: camera.y,
    maxX: camera.x + camera.width,
    maxY: camera.y + camera.height,
  };
  return rooms.filter((room) => intersects(roomBounds(room), view));
}

export interface ChunkRef {
  key: string;
  x: number;
  y: number;
}

/** Chunk keys covering the camera ∩ floor. Sorted so the result is stable. */
export function visibleChunks(
  camera: Camera,
  bounds: Bounds,
  chunkSize: number,
): ChunkRef[] {
  const size = Math.max(1, Math.floor(chunkSize));
  const minX = Math.max(camera.x, bounds.minX);
  const minY = Math.max(camera.y, bounds.minY);
  const maxX = Math.min(camera.x + camera.width, bounds.maxX);
  const maxY = Math.min(camera.y + camera.height, bounds.maxY);
  if (maxX <= minX || maxY <= minY) return [];
  const startX = Math.floor(minX / size);
  const startY = Math.floor(minY / size);
  const endX = Math.ceil(maxX / size) - 1;
  const endY = Math.ceil(maxY / size) - 1;
  const out: ChunkRef[] = [];
  for (let y = startY; y <= endY; y += 1) {
    for (let x = startX; x <= endX; x += 1) {
      out.push({ key: `${x},${y}`, x, y });
    }
  }
  return out.sort((a, b) => a.y - b.y || a.x - b.x);
}

// ------------------------------------------------------------- active floor
export interface ActiveFloorState {
  readonly floors: readonly string[];
  readonly activeFloorId: string | null;
}

/** Build a floor set. Selecting an initial floor outside the set is a typed
 *  refusal — the renderer never invents a floor. */
export function createFloorSet(
  floors: readonly string[],
  initialFloorId?: string | null,
): ActiveFloorState {
  const unique = Array.from(new Set(floors.filter((id) => id.length > 0)));
  if (!unique.length) {
    throw new RendererFloorError(
      "RENDERER_FLOOR_EMPTY",
      "the floor set is empty; there is nothing to render",
    );
  }
  const initial = initialFloorId ?? unique[0];
  if (!unique.includes(initial)) {
    throw new RendererFloorError(
      "RENDERER_FLOOR_UNKNOWN",
      `floor ${JSON.stringify(initial)} is not part of this tower`,
    );
  }
  return { floors: unique, activeFloorId: initial };
}

/** Activate exactly one floor. The returned state still holds one id. */
export function activateFloor(
  state: ActiveFloorState,
  floorId: string,
): ActiveFloorState {
  if (!state.floors.includes(floorId)) {
    throw new RendererFloorError(
      "RENDERER_FLOOR_UNKNOWN",
      `floor ${JSON.stringify(floorId)} is not part of this tower`,
    );
  }
  if (state.activeFloorId === floorId) return state;
  return { floors: state.floors, activeFloorId: floorId };
}

/** The floors a renderer may draw for this state: exactly zero or one. */
export function renderFloors(state: ActiveFloorState): readonly string[] {
  return state.activeFloorId === null ? [] : [state.activeFloorId];
}

// ------------------------------------------------------------------ LRU cache
export interface CacheStats {
  hits: number;
  misses: number;
  evictions: number;
  size: number;
  capacity: number;
}

/** Bounded LRU. Insertion order is recency; `get` refreshes an entry. No
 *  unbounded variant cache exists here. */
export class LRUCache<K, V> {
  private readonly store = new Map<K, V>();
  private readonly limit: number;
  private hits = 0;
  private misses = 0;
  private evictions = 0;

  constructor(capacity: number) {
    if (!Number.isFinite(capacity) || capacity < 1) {
      throw new RendererFloorError(
        "RENDERER_CACHE_CAPACITY",
        `cache capacity must be a positive integer, got ${String(capacity)}`,
      );
    }
    this.limit = Math.floor(capacity);
  }

  get size(): number {
    return this.store.size;
  }

  get capacity(): number {
    return this.limit;
  }

  has(key: K): boolean {
    return this.store.has(key);
  }

  get(key: K): V | undefined {
    if (!this.store.has(key)) {
      this.misses += 1;
      return undefined;
    }
    const value = this.store.get(key) as V;
    this.store.delete(key);
    this.store.set(key, value);
    this.hits += 1;
    return value;
  }

  set(key: K, value: V): void {
    if (this.store.has(key)) this.store.delete(key);
    this.store.set(key, value);
    while (this.store.size > this.limit) {
      const oldest = this.store.keys().next();
      if (oldest.done) break;
      this.store.delete(oldest.value);
      this.evictions += 1;
    }
  }

  delete(key: K): boolean {
    return this.store.delete(key);
  }

  clear(): void {
    this.store.clear();
  }

  keys(): K[] {
    return Array.from(this.store.keys());
  }

  stats(): CacheStats {
    return {
      hits: this.hits,
      misses: this.misses,
      evictions: this.evictions,
      size: this.store.size,
      capacity: this.limit,
    };
  }
}

// ------------------------------------------------------- static bake / dirty
export type BakeReason = "baked" | "reused" | "invalidated";

export interface BakeRecord {
  roomId: string;
  revision: number;
}

/** Static rooms bake once per state revision. Camera movement is deliberately
 *  not an input: scrolling a floor must not rebuild a baked room. Only a real
 *  state change (a new revision) invalidates the bake. */
export class StaticBakeTracker {
  private readonly records = new Map<string, BakeRecord>();
  private cameraMoves = 0;
  private bakes = 0;

  /** Record a camera change. It exists so a test can prove it does not
   *  invalidate any bake. */
  noteCameraMove(): void {
    this.cameraMoves += 1;
  }

  needsBake(roomId: string, revision: number): boolean {
    const record = this.records.get(roomId);
    return record === undefined || record.revision !== revision;
  }

  /** Mark a room baked at `revision`. Returns why. A stale bake is replaced;
   *  the same revision is a no-op. */
  bake(roomId: string, revision: number): BakeReason {
    if (!this.needsBake(roomId, revision)) return "reused";
    const existed = this.records.has(roomId);
    this.records.set(roomId, { roomId, revision });
    this.bakes += 1;
    return existed ? "invalidated" : "baked";
  }

  revisionOf(roomId: string): number | null {
    return this.records.get(roomId)?.revision ?? null;
  }

  forget(roomId: string): void {
    this.records.delete(roomId);
  }

  reset(): void {
    this.records.clear();
  }

  snapshot(): {
    rooms: BakeRecord[];
    cameraMoves: number;
    bakes: number;
  } {
    return {
      rooms: Array.from(this.records.values()).sort((a, b) =>
        a.roomId.localeCompare(b.roomId)),
      cameraMoves: this.cameraMoves,
      bakes: this.bakes,
    };
  }
}

export interface DirtySnapshot {
  full: boolean;
  rooms: readonly string[];
}

/** Dirty-render flags. `markRoom` is a state change; a camera move is not.
 *  Clearing after a draw keeps the next frame cheap when nothing changed. */
export class DirtyTracker {
  private full = true;
  private readonly rooms = new Set<string>();

  markFull(): void {
    this.full = true;
  }

  markRoom(roomId: string): void {
    this.rooms.add(roomId);
  }

  isRoomDirty(roomId: string): boolean {
    return this.full || this.rooms.has(roomId);
  }

  isFull(): boolean {
    return this.full;
  }

  clear(): void {
    this.full = false;
    this.rooms.clear();
  }

  snapshot(): DirtySnapshot {
    return {
      full: this.full,
      rooms: Array.from(this.rooms).sort(),
    };
  }
}

// --------------------------------------------------------------- device tier
export interface DeviceProfile {
  deviceMemoryGb?: number | null;
  hardwareConcurrency?: number | null;
  saveData?: boolean;
  reducedMotion?: boolean;
}

export interface DeviceDecision {
  tier: DeviceTier;
  reasons: readonly string[];
  limits: ReturnType<typeof tierLimits>;
}

/** Deterministic device tier. `low` when the device asks to save data, has
 *  little memory, or very few cores; otherwise `desktop`. No user-agent
 *  sniffing and no guessed numbers. */
export function resolveDeviceTier(profile: DeviceProfile = {}): DeviceDecision {
  const reasons: string[] = [];
  if (profile.saveData === true) reasons.push("save_data");
  if (isPositive(profile.deviceMemoryGb) && profile.deviceMemoryGb! < 4) {
    reasons.push("low_memory");
  }
  if (isPositive(profile.hardwareConcurrency) && profile.hardwareConcurrency! < 4) {
    reasons.push("low_cores");
  }
  const tier: DeviceTier = reasons.length ? "low" : "desktop";
  return { tier, reasons, limits: tierLimits(tier) };
}

function isPositive(value: number | null | undefined): value is number {
  return typeof value === "number" && Number.isFinite(value) && value > 0;
}

// ------------------------------------------------------- context-loss machine
export type ContextLossState = "ready" | "lost" | "recovering" | "fallback";

export type ContextLossEvent =
  | "context_lost"
  | "restore_started"
  | "restore_succeeded"
  | "restore_failed"
  | "fallback_entered"
  | "reset";

export interface ContextTransition {
  state: ContextLossState;
  changed: boolean;
  /** Why an event did not move the machine. Empty when it did. */
  ignored: string | null;
}

/** `ready → lost → recovering → ready | fallback`. An illegal event is
 *  reported, not applied. `restore_failed` enters the static/2D fallback when
 *  one is available; otherwise the renderer stays suspended (`lost`). */
export function transitionContextLoss(
  state: ContextLossState,
  event: ContextLossEvent,
  options: { fallbackAvailable?: boolean } = {},
): ContextTransition {
  const fallbackAvailable = options.fallbackAvailable !== false;
  switch (state) {
    case "ready":
      if (event === "context_lost") return { state: "lost", changed: true, ignored: null };
      break;
    case "lost":
      if (event === "restore_started") {
        return { state: "recovering", changed: true, ignored: null };
      }
      if (event === "fallback_entered" && fallbackAvailable) {
        return { state: "fallback", changed: true, ignored: null };
      }
      if (event === "context_lost") {
        return { state: "lost", changed: false, ignored: null };
      }
      break;
    case "recovering":
      if (event === "restore_succeeded") {
        return { state: "ready", changed: true, ignored: null };
      }
      if (event === "restore_failed") {
        if (fallbackAvailable) {
          return { state: "fallback", changed: true, ignored: null };
        }
        return { state: "lost", changed: true, ignored: "no fallback available" };
      }
      break;
    case "fallback":
      if (event === "reset") return { state: "ready", changed: true, ignored: null };
      if (event === "context_lost") {
        return { state: "fallback", changed: false, ignored: null };
      }
      break;
  }
  return {
    state,
    changed: false,
    ignored: `${event} is not valid while ${state}`,
  };
}

export type ContextRenderMode = "gpu" | "suspended" | "static";

/** While the context is lost or restoring there is nothing to draw on the GPU;
 *  the fallback is a static 2D presentation, not a fabricated live scene. */
export function contextRenderMode(state: ContextLossState): ContextRenderMode {
  if (state === "fallback") return "static";
  if (state === "lost" || state === "recovering") return "suspended";
  return "gpu";
}

// -------------------------------------------------------------- effects policy
export type StatusCue = "working" | "needs_you" | "error" | "unknown";

export const STATUS_CUES: readonly StatusCue[] = [
  "working",
  "needs_you",
  "error",
  "unknown",
];

export interface EffectPolicy {
  reducedMotion: boolean;
  tier: DeviceTier;
  effects: {
    ambient: boolean;
    particles: boolean;
    cameraTween: boolean;
    screenShake: boolean;
    weather: boolean;
  };
  /** Always present: reduced motion removes decoration, never status. */
  statusCues: readonly StatusCue[];
}

/** Reduced motion disables nonessential effects; essential status cues and
 *  navigation remain. A low-tier device also drops ambient decoration but
 *  keeps every cue. */
export function effectPolicy(
  options: { reducedMotion?: boolean; tier?: DeviceTier } = {},
): EffectPolicy {
  const reducedMotion = options.reducedMotion === true;
  const tier: DeviceTier = options.tier === "low" ? "low" : "desktop";
  const decorative = !reducedMotion && tier !== "low";
  return {
    reducedMotion,
    tier,
    effects: {
      ambient: decorative,
      particles: decorative,
      cameraTween: !reducedMotion,
      screenShake: decorative,
      weather: decorative,
    },
    statusCues: STATUS_CUES,
  };
}

// ---------------------------------------------------------- event coalescing
export interface TimedEvent {
  kind: string;
  at: number;
}

export interface CoalescedWindow {
  /** Whole second (`Math.floor(at)`). */
  second: number;
  raw: number;
  emitted: number;
  coalesced: number;
  dropped: number;
  events: readonly { kind: string; count: number }[];
}

export interface CoalescedBatch {
  windows: readonly CoalescedWindow[];
  emitted: number;
  dropped: number;
  coalesced: number;
}

/** Coalesce client events per one-second window: consecutive same-kind events
 *  collapse to one, and a window emits at most `burstLimit` events. Pure and
 *  deterministic; the caller supplies timestamps. */
export function coalesceClientEvents(
  events: readonly TimedEvent[],
  options: { burstLimit?: number } = {},
): CoalescedBatch {
  const limit = Number.isFinite(options.burstLimit)
    ? Math.max(1, Math.floor(options.burstLimit as number))
    : 60;
  const bySecond = new Map<number, { kind: string; count: number }[]>();
  const order: number[] = [];
  for (const event of events) {
    const second = Math.floor(event.at);
    let bucket = bySecond.get(second);
    if (!bucket) {
      bucket = [];
      bySecond.set(second, bucket);
      order.push(second);
    }
    const last = bucket[bucket.length - 1];
    if (last && last.kind === event.kind) {
      last.count += 1;
    } else {
      bucket.push({ kind: event.kind, count: 1 });
    }
  }
  const windows: CoalescedWindow[] = [];
  let emitted = 0;
  let dropped = 0;
  let coalesced = 0;
  for (const second of order) {
    const bucket = bySecond.get(second) ?? [];
    const raw = bucket.reduce((sum, item) => sum + item.count, 0);
    const kept = bucket.slice(0, limit);
    const keptCount = kept.reduce((sum, item) => sum + item.count, 0);
    windows.push({
      second,
      raw,
      emitted: keptCount,
      coalesced: raw - bucket.length,
      dropped: raw - keptCount,
      events: kept,
    });
    emitted += keptCount;
    dropped += raw - keptCount;
    coalesced += raw - bucket.length;
  }
  return { windows, emitted, dropped, coalesced };
}
