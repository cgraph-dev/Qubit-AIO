/* Typed client for the 100-floor tower scene.
 *
 * ONE DOOR, ONE FALLBACK
 * ----------------------
 * `GET /api/office/world/tower` is the planned room-scene endpoint: per floor
 * it carries rooms, fixed 16x12 cells and desk slots, plus active/dormant
 * state. This module normalizes that payload and, when the endpoint is absent
 * (404), unreachable, or malformed, projects the SAME shape from the existing
 * `directory.floors` templates. The fallback is deliberately explicit: rooms
 * are labelled templates and every desk is empty. It never invents a floor, a
 * room, an agent, or an occupant.
 *
 * The endpoint is read-only. No request here publishes, spends or contacts
 * anyone; a panel that cannot read the world still renders the directory.
 */
import { readToken } from "./agents-panel";
import { API_PREFIX, hostBasePath, type FloorTemplate } from "./business-api";

export const WORLD_PATH = "/api/office/world";
export const WORLD_SCHEMA = "qubit-office-world-tower/1";
export const CELL_COLS = 16;
export const CELL_ROWS = 12;
export const CELL_DESK_CAPACITY = 6;

/** Fixed desk slots inside one 16x12 cell (top-left tiles of a 2x1 desk). */
const DESK_TILES: ReadonlyArray<readonly [number, number]> = [
  [2, 2], [7, 2], [12, 2],
  [2, 8], [7, 8], [12, 8],
];

export interface WorldDeskSlot {
  desk_id: string;
  seat_id: string | null;
  tile: { x: number; y: number };
  footprint: { w: number; h: number };
  occupied_by: string | null;
}

export interface WorldCell {
  cell_id: string;
  index: number;
  cols: number;
  rows: number;
  origin: { x: number; y: number };
  desk_slots: WorldDeskSlot[];
}

export interface WorldRoom {
  room_id: string;
  name: string;
  kind: string;
  archetype: string;
  role_ids: string[];
  active: boolean;
  state: string;
  template: boolean;
  capacity: number;
  cell_count: number;
  cells: WorldCell[];
}

export interface WorldFloor {
  floor_id: string;
  index: number;
  wing: string;
  wing_name: string;
  name: string;
  active: boolean;
  active_by_default: boolean;
  department_ids: string[];
  role_ids: string[];
  room_count: number;
  desk_capacity: number;
  cells: number;
  capacity: { rooms: number; cells: number; desks: number };
  rooms: WorldRoom[];
}

export interface WorldWingSummary {
  wing: string;
  name: string;
  floor_count: number;
  active_floors: number;
  dormant_floors: number;
  rooms: number;
}

export interface WorldTowerCounts {
  floors: number;
  wings: number;
  active_floors: number;
  dormant_floors: number;
  rooms: number;
  cells: number;
  desks: number;
  active_rooms: number;
  dormant_rooms: number;
  agents: number;
}

export interface WorldTower {
  ok: true;
  schema: string;
  /** Where this payload came from: the endpoint, or the floor templates. */
  source: "endpoint" | "template";
  server_source?: string;
  staffed: boolean;
  campus?: { campus_id: string; name: string };
  counts: WorldTowerCounts;
  wings: WorldWingSummary[];
  floors: WorldFloor[];
  /** Present only on the fallback path; names why the endpoint was skipped. */
  fallback_reason?: string;
}

export interface WorldFetchOptions {
  base?: string;
  fetchImpl?: typeof fetch;
  win?: Window | null;
  signal?: AbortSignal;
}

export class WorldApiError extends Error {
  readonly code: string;
  readonly status: number;
  constructor(message: string, code: string, status: number) {
    super(message);
    this.name = "WorldApiError";
    this.code = code;
    this.status = status;
  }
}

export function worldUrl(path: string, base?: string): string {
  const raw = base === undefined ? hostBasePath() : base;
  const b = raw.endsWith("/") ? raw.slice(0, -1) : raw;
  const clean = path.startsWith("/") ? path : `/${path}`;
  return `${b}${API_PREFIX}${WORLD_PATH}${clean}`;
}

function int(value: unknown, fallback = 0): number {
  const n = typeof value === "number" ? value
    : typeof value === "string" ? Number(value) : NaN;
  return Number.isFinite(n) ? Math.trunc(n) : fallback;
}

function record(value: unknown): Record<string, any> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, any> : null;
}

function strings(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string") : [];
}

function tile(
  value: unknown, fallback: readonly [number, number],
): { x: number; y: number } {
  const row = record(value);
  return {
    x: Math.max(0, Math.min(CELL_COLS - 1, int(row?.x, fallback[0]))),
    y: Math.max(0, Math.min(CELL_ROWS - 1, int(row?.y, fallback[1]))),
  };
}

/** Plan fixed 16x12 cells for an exact desk count. Never stretches a cell. */
export function planCellCluster(roomId: string, desks: number): WorldCell[] {
  const safeDesks = Math.max(0, int(desks));
  const count = Math.max(1, Math.ceil(safeDesks / CELL_DESK_CAPACITY));
  const base = Math.floor(safeDesks / count);
  const extra = safeDesks % count;
  let seat = 0;
  const cells: WorldCell[] = [];
  for (let index = 0; index < count; index += 1) {
    const cellDesks = base + (index < extra ? 1 : 0);
    const slots: WorldDeskSlot[] = [];
    for (let slot = 0; slot < cellDesks; slot += 1) {
      const [x, y] = DESK_TILES[slot % DESK_TILES.length];
      slots.push({
        desk_id: `${roomId}:c${index}:d${slot}`,
        seat_id: `${roomId}:c${index}:s${seat}`,
        tile: { x, y },
        footprint: { w: 2, h: 1 },
        occupied_by: null,
      });
      seat += 1;
    }
    const col = index % 2;
    const row = Math.floor(index / 2);
    cells.push({
      cell_id: `c${index}`,
      index,
      cols: CELL_COLS,
      rows: CELL_ROWS,
      origin: { x: col * (CELL_COLS + 2), y: row * (CELL_ROWS + 2) },
      desk_slots: slots,
    });
  }
  return cells;
}

/** Derive labeled template rooms from a floor's room/desk counts. */
export function templateRooms(
  floorId: string, roomCount: number, capacity: number, active: boolean,
  roleIds: string[] = [],
): WorldRoom[] {
  const count = Math.max(0, int(roomCount));
  const desksTotal = Math.max(0, int(capacity));
  const base = count > 0 ? Math.floor(desksTotal / count) : 0;
  const extra = count > 0 ? desksTotal % count : 0;
  return Array.from({ length: count }, (_, index) => {
    const id = `${floorId}-room-${index + 1}`;
    const desks = base + (index < extra ? 1 : 0);
    const cells = planCellCluster(id, desks);
    return {
      room_id: id,
      name: `Room ${index + 1}`,
      kind: "template",
      archetype: "template",
      role_ids: roleIds,
      active,
      state: "template",
      template: true,
      capacity: desks,
      cell_count: cells.length,
      cells,
    };
  });
}

function normalizeSlots(raw: unknown): WorldDeskSlot[] {
  const items = Array.isArray(raw) ? raw : [];
  return items.map((item, index) => {
    const row = record(item) ?? {};
    const seat = record(row.seat);
    const footprint = record(row.footprint);
    const fallback = DESK_TILES[index % DESK_TILES.length];
    const deskId = typeof row.desk_id === "string" && row.desk_id
      ? row.desk_id : `desk-${index}`;
    const seatId = typeof row.seat_id === "string" ? row.seat_id
      : typeof seat?.seat_id === "string" ? seat.seat_id : null;
    const occupied = typeof row.occupied_by === "string" && row.occupied_by
      ? row.occupied_by
      : typeof seat?.occupied_by === "string" && seat.occupied_by
        ? seat.occupied_by : null;
    return {
      desk_id: deskId,
      seat_id: seatId,
      tile: tile(row.tile ?? seat?.tile, fallback),
      footprint: {
        w: Math.max(1, int(footprint?.w, 2)),
        h: Math.max(1, int(footprint?.h, 1)),
      },
      occupied_by: occupied,
    };
  });
}

function normalizeCells(raw: unknown, roomId: string, capacity: number): WorldCell[] {
  const items = Array.isArray(raw) ? raw : [];
  if (!items.length) return planCellCluster(roomId, capacity);
  const cells = items.map((item, index) => {
    const row = record(item) ?? {};
    const slotsRaw = row.desk_slots ?? row.desks;
    const slots = normalizeSlots(slotsRaw);
    const origin = record(row.origin);
    return {
      cell_id: typeof row.cell_id === "string" && row.cell_id
        ? row.cell_id : `c${index}`,
      index: int(row.index, index),
      cols: int(row.cols, CELL_COLS),
      rows: int(row.rows, CELL_ROWS),
      origin: {
        x: int(origin?.x, (index % 2) * (CELL_COLS + 2)),
        y: int(origin?.y, Math.floor(index / 2) * (CELL_ROWS + 2)),
      },
      desk_slots: slots,
    };
  });
  const slots = cells.reduce((sum, cell) => sum + cell.desk_slots.length, 0);
  return capacity > 0 && slots === 0 ? planCellCluster(roomId, capacity) : cells;
}

function normalizeRoom(raw: unknown, floorActive: boolean, index: number): WorldRoom {
  const row = record(raw) ?? {};
  const roomId = typeof row.room_id === "string" && row.room_id
    ? row.room_id : `room-${index + 1}`;
  const name = typeof row.name === "string" && row.name
    ? row.name : `Room ${index + 1}`;
  const kind = typeof row.kind === "string" && row.kind ? row.kind : "template";
  const archetype = typeof row.archetype === "string" && row.archetype
    ? row.archetype : kind;
  const state = typeof row.state === "string" ? row.state : "template";
  const active = typeof row.active === "boolean" ? row.active
    : state.toUpperCase() !== "DORMANT" && floorActive;
  const declared = int(row.capacity ?? row.desk_count ?? row.desks, 0);
  const cells = normalizeCells(row.cells, roomId, declared);
  const slots = cells.reduce((sum, cell) => sum + cell.desk_slots.length, 0);
  const capacity = slots > 0 ? slots : Math.max(0, declared);
  return {
    room_id: roomId,
    name,
    kind,
    archetype,
    role_ids: strings(row.role_ids),
    active,
    state,
    template: row.template !== false,
    capacity,
    cell_count: cells.length,
    cells,
  };
}

function normalizeFloor(raw: unknown, index: number): WorldFloor {
  const row = record(raw) ?? {};
  const floorId = typeof row.floor_id === "string" && row.floor_id
    ? row.floor_id : `floor-${String(index + 1).padStart(3, "0")}`;
  const wing = typeof row.wing === "string" && row.wing ? row.wing : "central";
  const active = row.active_by_default === true
    || (row.active_by_default === undefined && row.active === true);
  const roomsRaw = Array.isArray(row.rooms) ? row.rooms : [];
  const expectedRooms = int(row.room_count ?? record(row.capacity)?.rooms, 0);
  const expectedDesks = int(
    row.desk_capacity ?? record(row.capacity)?.desks, 0);
  const rooms = roomsRaw.length
    ? roomsRaw.map((room, i) => normalizeRoom(room, active, i))
    : expectedRooms > 0
      ? templateRooms(floorId, expectedRooms, expectedDesks, active,
        strings(row.role_ids))
      : [];
  const cells = rooms.reduce((sum, room) => sum + room.cell_count, 0);
  const desks = rooms.reduce((sum, room) => sum + room.capacity, 0);
  return {
    floor_id: floorId,
    index: int(row.index, index + 1),
    wing,
    wing_name: typeof row.wing_name === "string" && row.wing_name
      ? row.wing_name : `${wing.charAt(0).toUpperCase()}${wing.slice(1)} Wing`,
    name: typeof row.name === "string" && row.name
      ? row.name : `Floor ${String(index + 1).padStart(3, "0")}`,
    active,
    active_by_default: active,
    department_ids: strings(row.department_ids),
    role_ids: strings(row.role_ids),
    room_count: rooms.length,
    desk_capacity: desks,
    cells,
    capacity: { rooms: rooms.length, cells, desks },
    rooms,
  };
}

function countTower(floors: WorldFloor[]): WorldTowerCounts {
  let rooms = 0, cells = 0, desks = 0, activeRooms = 0;
  let agents = 0;
  const occupied = new Set<string>();
  for (const floor of floors) {
    rooms += floor.rooms.length;
    for (const room of floor.rooms) {
      if (room.active) activeRooms += 1;
      cells += room.cell_count;
      desks += room.capacity;
      for (const cell of room.cells) {
        for (const slot of cell.desk_slots) {
          if (slot.occupied_by) occupied.add(slot.occupied_by);
        }
      }
    }
  }
  agents = occupied.size;
  const activeFloors = floors.filter((floor) => floor.active).length;
  return {
    floors: floors.length,
    wings: new Set(floors.map((floor) => floor.wing)).size,
    active_floors: activeFloors,
    dormant_floors: floors.length - activeFloors,
    rooms,
    cells,
    desks,
    active_rooms: activeRooms,
    dormant_rooms: rooms - activeRooms,
    agents,
  };
}

function wingSummaries(floors: WorldFloor[]): WorldWingSummary[] {
  const order: string[] = [];
  const map = new Map<string, WorldWingSummary>();
  for (const floor of floors) {
    if (!map.has(floor.wing)) {
      order.push(floor.wing);
      map.set(floor.wing, {
        wing: floor.wing,
        name: floor.wing_name,
        floor_count: 0,
        active_floors: 0,
        dormant_floors: 0,
        rooms: 0,
      });
    }
    const item = map.get(floor.wing)!;
    item.floor_count += 1;
    item.active_floors += floor.active ? 1 : 0;
    item.dormant_floors += floor.active ? 0 : 1;
    item.rooms += floor.rooms.length;
  }
  return order.map((wing) => map.get(wing)!);
}

function normalizeTower(raw: unknown): WorldTower {
  const row = record(raw);
  if (!row || row.ok !== true || !Array.isArray(row.floors)) {
    throw new WorldApiError(
      "The world endpoint returned no usable tower.", "WORLD_BAD_RESPONSE", 200);
  }
  const floors = row.floors.map((floor, index) => normalizeFloor(floor, index));
  if (!floors.length) {
    throw new WorldApiError(
      "The world endpoint returned zero floors.", "WORLD_EMPTY", 200);
  }
  const counts = countTower(floors);
  return {
    ok: true,
    schema: typeof row.schema === "string" && row.schema ? row.schema : WORLD_SCHEMA,
    source: "endpoint",
    server_source: typeof row.source === "string" ? row.source : undefined,
    staffed: counts.agents > 0,
    campus: record(row.campus) as WorldTower["campus"],
    counts,
    wings: wingSummaries(floors),
    floors,
  };
}

/** A template floor scene derived only from the directory row. */
export function templateFloorScene(floor: FloorTemplate): WorldFloor {
  const rooms = templateRooms(
    floor.floor_id, floor.room_count, floor.desk_capacity,
    floor.active_by_default, []);
  const cells = rooms.reduce((sum, room) => sum + room.cell_count, 0);
  const desks = rooms.reduce((sum, room) => sum + room.capacity, 0);
  return {
    floor_id: floor.floor_id,
    index: floor.index,
    wing: floor.wing,
    wing_name: `${floor.wing.charAt(0).toUpperCase()}${floor.wing.slice(1)} Wing`,
    name: floor.name,
    active: floor.active_by_default,
    active_by_default: floor.active_by_default,
    department_ids: floor.department_ids,
    role_ids: floor.role_ids,
    room_count: rooms.length,
    desk_capacity: desks,
    cells,
    capacity: { rooms: rooms.length, cells, desks },
    rooms,
  };
}

/** Project the whole directory into scene floors without any network call. */
export function buildTemplateWorldTower(
  floors: FloorTemplate[], reason = "The world endpoint is unavailable.",
): WorldTower {
  const scenes = (floors ?? []).map(templateFloorScene);
  return {
    ok: true,
    schema: WORLD_SCHEMA,
    source: "template",
    staffed: false,
    counts: countTower(scenes),
    wings: wingSummaries(scenes),
    floors: scenes,
    fallback_reason: reason,
  };
}

function fallbackReason(exc: unknown): string {
  if (exc instanceof WorldApiError) return exc.message;
  if (exc instanceof Error && exc.message) return exc.message;
  return "The world endpoint is unavailable.";
}

/** Fetch the tower scene; always answer with a usable payload.
 *
 * A 404, an unreachable host, a malformed body or an empty floor list all
 * project the directory templates instead. The fallback marks every room as a
 * template and every desk as empty, so a missing endpoint never fabricates
 * staff.
 */
export async function fetchWorldTower(
  fallbackFloors: FloorTemplate[], opts: WorldFetchOptions = {},
): Promise<WorldTower> {
  const f = opts.fetchImpl ?? fetch;
  const url = worldUrl("/tower", opts.base);
  const headers: Record<string, string> = { Accept: "application/json" };
  const token = readToken(opts.win);
  if (token) headers["X-Qubit-Session-Token"] = token;
  try {
    const res = await f(url, {
      method: "GET", headers, credentials: "same-origin", signal: opts.signal,
    });
    if (!res.ok) {
      throw new WorldApiError(
        `The world endpoint answered HTTP ${res.status}.`,
        `WORLD_HTTP_${res.status}`, res.status);
    }
    const parsed = await res.json().catch(() => null);
    return normalizeTower(parsed);
  } catch (exc) {
    return buildTemplateWorldTower(fallbackFloors, fallbackReason(exc));
  }
}
