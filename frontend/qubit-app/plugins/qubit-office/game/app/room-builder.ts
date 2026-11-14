/* P6-02 room-builder client model.
 *
 * This is the UI's draft editor for a department floor. It is deliberately a
 * thin client: the authoritative map schema, registry and style validation
 * live in `voicebridge/office/world/` and `voicebridge/business/room_builder.py`.
 * The functions here keep a local draft so the owner can preview/undo/approve
 * before sending one publish intent to the Business OS; they do not invent
 * business facts and never ship paid art.
 *
 * A cell is always 16x12. A wider room is another cell, never a stretched one.
 */
export const CELL_COLS = 16;
export const CELL_ROWS = 12;
export const CELL_DESK_CAPACITY = 6;

export const DEPARTMENT_ZONES = [
  "reception", "work", "meeting", "focus", "head_office",
  "kitchen", "restroom", "lounge", "storage", "comms",
] as const;
export type DepartmentZone = (typeof DEPARTMENT_ZONES)[number];

export const ZONE_ALIASES: Record<string, string[]> = {
  reception: ["reception"],
  work: ["work", "open_office"],
  meeting: ["meeting", "executive", "boardroom"],
  focus: ["focus", "wellness"],
  head_office: ["head_office", "executive"],
  kitchen: ["kitchen"],
  restroom: ["restroom", "wellness"],
  lounge: ["lounge", "wellness"],
  storage: ["storage", "fulfillment"],
  comms: ["comms", "ops", "server_ops", "control_room"],
};

export const ARCHETYPES = [
  "reception", "open_office", "meeting", "wellness", "executive", "kitchen",
  "fulfillment", "control_room", "finance", "creative", "support", "legal",
  "research", "sales", "showcase", "training", "media", "server_ops",
] as const;
export type Archetype = (typeof ARCHETYPES)[number];

export const PALETTES = [
  "cool_teal_neutral", "executive_warm", "finance_navy",
  "creative_coral", "control_room_amber", "open_office_sand",
] as const;
export type Palette = (typeof PALETTES)[number];

export const SHADOWS = ["shadowless", "black_shadow"] as const;
export type Shadow = (typeof SHADOWS)[number];

export const LIGHTING = [
  "light_cool_overhead", "light_soft_panels", "light_warm_chandelier",
  "light_focused_task", "light_amber_console", "light_warm_strings",
] as const;

export const SIGN_PREFIXES = ["sign", "sign-finance", "sign-work", "sign-help"];
export const FLOOR_SLUGS = [
  "floor_wood_light", "floor_wood_dark", "floor_carpet_dark",
  "floor_tile_grey", "floor_concrete_polished", "floor_marble_warm",
];
export const WALL_SLUGS = [
  "wall_panel_light", "wall_plaster_cool", "wall_glass_partition",
  "wall_tech_panel", "wall_brick_painted", "wall_panel_walnut",
];

const CELL_DESK_SLOTS: ReadonlyArray<readonly [number, number]> = [
  [2, 2], [7, 2], [12, 2], [2, 8], [7, 8], [12, 8],
];

const SLUG_RE = /^[a-z0-9][a-z0-9._-]{0,79}$/;
const TOKEN_RE = /^[A-Za-z0-9][A-Za-z0-9._:/{}@#+-]{0,127}$/;
const RAW_ART_SUFFIXES = [".png", ".webp", ".psd", ".aseprite", ".ase", ".gif"];

export interface RoomStyle {
  archetype: string;
  palette: string;
  floor: string;
  wall: string;
  rug: string;
  furniture_set: string;
  lighting: string;
  signage: string;
  shadow: string;
  seed: number;
  style_version: number;
  department_style: string;
  zone: string;
}

export interface RoomAssignment {
  business_id: string;
  department_id: string;
  role_ids: string[];
  agent_ids: string[];
  capability_manifest_id: string;
  memory_scope: string;
}

export interface BuilderDesk {
  desk_id: string;
  seat_id: string;
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface BuilderCell {
  cell_id: string;
  index: number;
  cols: number;
  rows: number;
  desks: BuilderDesk[];
}

export interface RoomAsset {
  asset_id: string;
  kind: string;
  ref: string;
  paid: boolean;
}

export interface BuilderRoom {
  room_id: string;
  floor_id: string;
  name: string;
  kind: string;
  archetype: string;
  capacity: number;
  style: RoomStyle;
  assignment: RoomAssignment;
  assets: RoomAsset[];
  cells: BuilderCell[];
}

export interface BuilderFloor {
  floor_id: string;
  index: number;
  wing: string;
  name: string;
  entry_room_id: string;
  room_ids: string[];
  department_style: string;
  activation_state: "active" | "dormant";
}

export interface BuilderPortal {
  portal_id: string;
  floor_id: string;
  source_room_id: string;
  target_room_id: string;
  kind: string;
  x: number;
  y: number;
}

export interface BuilderSnapshot {
  floors: BuilderFloor[];
  rooms: BuilderRoom[];
  portals: BuilderPortal[];
  status: BuilderStatus;
}

export type BuilderStatus = "draft" | "approved" | "published" | "rolled_back";

export interface BuilderState extends BuilderSnapshot {
  map_id: string;
  draft_revision: number;
  history: BuilderSnapshot[];
  future: BuilderSnapshot[];
  problems: string[];
  approved_by: string | null;
  published_version: number | null;
  versions: Record<number, BuilderSnapshot>;
  last_action: string;
}

export interface PreviewResult {
  valid: boolean;
  problems: string[];
  counts: {
    floors: number;
    rooms: number;
    cells: number;
    desks: number;
    portals: number;
  };
  capacity: number;
  egress: number;
  digest: string;
}

export type PublishResult =
  | { kind: "published"; version: number; digest: string }
  | { kind: "error"; problems: string[] };

export interface PublishOptions {
  onPublish?: (state: BuilderState) => Promise<
    { ok: boolean; version?: number; problems?: string[] }>;
}

function emptyAssignment(): RoomAssignment {
  return {
    business_id: "",
    department_id: "",
    role_ids: [],
    agent_ids: [],
    capability_manifest_id: "",
    memory_scope: "",
  };
}

function hashNumber(text: string): number {
  let hash = 2166136261;
  for (let i = 0; i < text.length; i += 1) {
    hash ^= text.charCodeAt(i);
    hash = Math.imul(hash, 16777619) >>> 0;
  }
  return hash >>> 0;
}

function pick<T>(options: readonly T[], seed: number, salt: number): T {
  return options[(seed + salt * 2654435761) % options.length];
}

export function makeStyle(
  archetype: string,
  seed: number,
  departmentStyle: string,
  zone?: string,
): RoomStyle {
  const base = hashNumber(`${archetype}|${seed}|${departmentStyle}`);
  return {
    archetype,
    palette: pick(PALETTES, base, 1),
    floor: pick(FLOOR_SLUGS, base, 2),
    wall: pick(WALL_SLUGS, base, 3),
    rug: "rug_plain_grey",
    furniture_set: `${archetype}_desks`,
    lighting: pick(LIGHTING, base, 4),
    signage: `sign-${archetype}`,
    shadow: "shadowless",
    seed,
    style_version: 1,
    department_style: departmentStyle,
    zone: zone ?? archetype,
  };
}

function buildCells(roomId: string, desks: number, kind: string): BuilderCell[] {
  const cells: BuilderCell[] = [];
  let remaining = Math.max(0, desks);
  let index = 0;
  let seatCounter = 0;
  while (remaining > 0) {
    const count = Math.min(CELL_DESK_CAPACITY, remaining);
    const cellId = `${roomId}:c${index}`;
    const rows: BuilderDesk[] = [];
    for (let slot = 0; slot < count; slot += 1) {
      const [x, y] = CELL_DESK_SLOTS[slot];
      rows.push({
        desk_id: `${roomId}:${cellId}:d${slot}`,
        seat_id: `${roomId}:${cellId}:s${seatCounter}`,
        x,
        y,
        w: 2,
        h: 1,
      });
      seatCounter += 1;
    }
    cells.push({
      cell_id: cellId,
      index,
      cols: CELL_COLS,
      rows: CELL_ROWS,
      desks: rows,
    });
    remaining -= count;
    index += 1;
  }
  if (!cells.length) {
    cells.push({
      cell_id: `${roomId}:c0`, index: 0,
      cols: CELL_COLS, rows: CELL_ROWS, desks: [],
    });
  }
  return cells;
}

interface RoomSeed {
  room_id: string;
  name: string;
  kind: string;
  archetype: string;
  desks: number;
}

const SAMPLE_ROOMS: RoomSeed[] = [
  { room_id: "rec-1", name: "Reception", kind: "reception", archetype: "reception", desks: 2 },
  { room_id: "work-1", name: "Open Office", kind: "work", archetype: "open_office", desks: 6 },
  { room_id: "meet-1", name: "Boardroom", kind: "meeting", archetype: "meeting", desks: 2 },
  { room_id: "focus-1", name: "Focus Room", kind: "focus", archetype: "wellness", desks: 1 },
  { room_id: "head-1", name: "Head Office", kind: "head_office", archetype: "executive", desks: 1 },
  { room_id: "kitchen-1", name: "Kitchen", kind: "kitchen", archetype: "kitchen", desks: 1 },
  { room_id: "rest-1", name: "Restroom", kind: "restroom", archetype: "wellness", desks: 1 },
  { room_id: "lounge-1", name: "Lounge", kind: "lounge", archetype: "wellness", desks: 1 },
  { room_id: "store-1", name: "Storage", kind: "storage", archetype: "fulfillment", desks: 1 },
  { room_id: "comms-1", name: "Comms / Ops", kind: "comms", archetype: "control_room", desks: 1 },
];

function roomFactory(
  seed: RoomSeed,
  floorId: string,
  departmentStyle: string,
): BuilderRoom {
  const cells = buildCells(seed.room_id, seed.desks, seed.kind);
  return {
    room_id: seed.room_id,
    floor_id: floorId,
    name: seed.name,
    kind: seed.kind,
    archetype: seed.archetype,
    capacity: seed.desks,
    style: makeStyle(
      seed.archetype,
      hashNumber(`${floorId}|${seed.room_id}`) % 1_000_000,
      departmentStyle,
      seed.kind,
    ),
    assignment: emptyAssignment(),
    assets: [{
      asset_id: `style:${seed.kind}`,
      kind: "recipe",
      ref: `recipes/${seed.kind}`,
      paid: false,
    }],
    cells,
  };
}

function rebuildPortals(
  floors: BuilderFloor[],
  rooms: BuilderRoom[],
  _portals: BuilderPortal[],
): { floors: BuilderFloor[]; portals: BuilderPortal[] } {
  const portals: BuilderPortal[] = [];
  const ordered = floors.slice().sort((a, b) => a.index - b.index);
  const entries: Array<{ floor_id: string; entry: string }> = [];
  const newFloors: BuilderFloor[] = [];
  for (const floor of ordered) {
    const ids = rooms
      .filter((room) => room.floor_id === floor.floor_id)
      .map((room) => room.room_id);
    const entry = ids.includes(floor.entry_room_id)
      ? floor.entry_room_id
      : (ids[0] ?? "");
    newFloors.push({ ...floor, room_ids: ids, entry_room_id: entry });
    if (!ids.length) continue;
    entries.push({ floor_id: floor.floor_id, entry });
    ids.forEach((roomId, index) => {
      if (roomId === entry) return;
      portals.push({
        portal_id: `p:${floor.floor_id}:${entry}->${roomId}`,
        floor_id: floor.floor_id,
        source_room_id: entry,
        target_room_id: roomId,
        kind: "door",
        x: (index * 3) % CELL_COLS,
        y: CELL_ROWS - 1,
      });
    });
  }
  for (let i = 1; i < entries.length; i += 1) {
    const current = entries[i];
    const previous = entries[i - 1];
    if (!current.entry || !previous.entry) continue;
    portals.push({
      portal_id: `p:${current.floor_id}:elevator->${previous.entry}`,
      floor_id: current.floor_id,
      source_room_id: current.entry,
      target_room_id: previous.entry,
      kind: "elevator",
      x: 1,
      y: 1,
    });
  }
  return { floors: newFloors, portals };
}

export function createSampleState(): BuilderState {
  const floorId = "floor-001";
  const departmentStyle = "operations";
  const rooms = SAMPLE_ROOMS.map((seed) =>
    roomFactory(seed, floorId, departmentStyle));
  const floors: BuilderFloor[] = [{
    floor_id: floorId,
    index: 1,
    wing: "central",
    name: "Operations",
    entry_room_id: "rec-1",
    room_ids: rooms.map((room) => room.room_id),
    department_style: departmentStyle,
    activation_state: "active",
  }];
  const { floors: builtFloors, portals } = rebuildPortals(floors, rooms, []);
  const state: BuilderState = {
    map_id: "sample-map",
    floors: builtFloors,
    rooms,
    portals,
    status: "draft",
    draft_revision: 0,
    history: [],
    future: [],
    problems: [],
    approved_by: null,
    published_version: null,
    versions: {},
    last_action: "sample",
  };
  state.problems = validateState(state);
  return state;
}

export function createBuilderState(
  input?: Partial<BuilderState> | null,
): BuilderState {
  if (!input || !Array.isArray(input.rooms) || !input.rooms.length) {
    return createSampleState();
  }
  const state: BuilderState = {
    map_id: String(input.map_id ?? "sample-map"),
    floors: (input.floors ?? []).map((floor) => ({
      floor_id: String(floor.floor_id),
      index: Number(floor.index ?? 1),
      wing: String(floor.wing ?? "central"),
      name: String(floor.name ?? floor.floor_id),
      entry_room_id: String(floor.entry_room_id ?? ""),
      room_ids: Array.isArray(floor.room_ids) ? floor.room_ids.slice() : [],
      department_style: String(floor.department_style ?? "operations"),
      activation_state: floor.activation_state === "active"
        ? "active" : "dormant",
    })),
    rooms: input.rooms.map(cloneRoom),
    portals: (input.portals ?? []).map((portal) => ({ ...portal })),
    status: input.status ?? "draft",
    draft_revision: Number(input.draft_revision ?? 0),
    history: [],
    future: [],
    problems: [],
    approved_by: input.approved_by ?? null,
    published_version: input.published_version ?? null,
    versions: Object.fromEntries(
      Object.entries(input.versions ?? {}).map(([version, snap]) => [
        version, snapshot(snap as BuilderSnapshot),
      ]),
    ) as Record<number, BuilderSnapshot>,
    last_action: String(input.last_action ?? "load"),
  };
  state.problems = validateState(state);
  return state;
}

function cloneRoom(room: BuilderRoom): BuilderRoom {
  return {
    ...room,
    style: { ...room.style },
    assignment: {
      ...room.assignment,
      role_ids: (room.assignment.role_ids ?? []).slice(),
      agent_ids: (room.assignment.agent_ids ?? []).slice(),
    },
    assets: (room.assets ?? []).map((asset) => ({ ...asset })),
    cells: (room.cells ?? []).map((cell) => ({
      ...cell,
      desks: (cell.desks ?? []).map((desk) => ({ ...desk })),
    })),
  };
}

function snapshot(source: BuilderSnapshot): BuilderSnapshot {
  return {
    floors: source.floors.map((floor) => ({
      ...floor,
      room_ids: floor.room_ids.slice(),
    })),
    rooms: source.rooms.map(cloneRoom),
    portals: source.portals.map((portal) => ({ ...portal })),
    status: source.status,
  };
}

function commit(
  previous: BuilderState, next: BuilderState, action: string,
): BuilderState {
  return finalise({
    ...next,
    history: [...previous.history, snapshot(previous)].slice(-50),
    future: [],
    draft_revision: previous.draft_revision + 1,
    status: "draft",
    approved_by: null,
  }, action);
}

function withPortals(
  state: BuilderState,
  rooms: BuilderRoom[],
): BuilderState {
  const rebuilt = rebuildPortals(state.floors, rooms, state.portals);
  return { ...state, floors: rebuilt.floors, rooms, portals: rebuilt.portals };
}

function floorOf(state: BuilderState, floorId: string): BuilderFloor {
  const floor = state.floors.find((row) => row.floor_id === floorId);
  if (!floor) throw new Error(`unknown floor ${floorId}`);
  return floor;
}

function roomOf(state: BuilderState, roomId: string): BuilderRoom {
  const room = state.rooms.find((row) => row.room_id === roomId);
  if (!room) throw new Error(`unknown room ${roomId}`);
  return room;
}

export function addRoom(
  state: BuilderState,
  input: {
    floor_id: string;
    room_id: string;
    name: string;
    kind: string;
    archetype?: string;
    desks?: number;
    style?: Partial<RoomStyle>;
    assignment?: Partial<RoomAssignment>;
  },
): BuilderState {
  floorOf(state, input.floor_id);
  if (state.rooms.some((room) => room.room_id === input.room_id)) {
    throw new Error(`room ${input.room_id} already exists`);
  }
  if (!TOKEN_RE.test(input.room_id)) {
    throw new Error(`invalid room id ${input.room_id}`);
  }
  const desks = Math.max(1, Math.trunc(input.desks ?? 6));
  const archetype = input.archetype ?? archetypeFor(input.kind);
  const departmentStyle = floorOf(state, input.floor_id).department_style;
  const room: BuilderRoom = {
    room_id: input.room_id,
    floor_id: input.floor_id,
    name: input.name,
    kind: input.kind,
    archetype,
    capacity: desks,
    style: {
      ...makeStyle(
        archetype,
        hashNumber(`${input.floor_id}|${input.room_id}`) % 1_000_000,
        departmentStyle,
        input.kind,
      ),
      ...(input.style ?? {}),
    },
    assignment: { ...emptyAssignment(), ...(input.assignment ?? {}) },
    assets: [{
      asset_id: `style:${input.kind}`,
      kind: "recipe",
      ref: `recipes/${input.kind}`,
      paid: false,
    }],
    cells: buildCells(input.room_id, desks, input.kind),
  };
  const next = withPortals(state, [...state.rooms, room]);
  return commit(state, next, "add_room");
}

export function removeRoom(state: BuilderState, roomId: string): BuilderState {
  roomOf(state, roomId);
  const rooms = state.rooms.filter((room) => room.room_id !== roomId);
  const next = withPortals(state, rooms);
  return commit(state, next, "remove_room");
}

export function addCell(
  state: BuilderState, roomId: string, desks = 6,
): BuilderState {
  if (desks < 0 || desks > CELL_DESK_CAPACITY) {
    throw new Error(`a cell holds 0..${CELL_DESK_CAPACITY} desks`);
  }
  const room = roomOf(state, roomId);
  const index = Math.max(-1, ...room.cells.map((cell) => cell.index)) + 1;
  const cellId = `${roomId}:c${index}`;
  const cell = {
    cell_id: cellId,
    index,
    cols: CELL_COLS,
    rows: CELL_ROWS,
    desks: buildCells(`${roomId}-${cellId}`, desks, room.kind)[0].desks
      .map((desk) => ({
        ...desk,
        desk_id: `${roomId}:${cellId}:${desk.desk_id.split(":").pop()}`,
        seat_id: `${roomId}:${cellId}:${desk.seat_id.split(":").pop()}`,
      })),
  };
  const rooms = state.rooms.map((row) =>
    row.room_id === roomId
      ? {
        ...row,
        cells: [...row.cells, cell],
        capacity: row.capacity + cell.desks.length,
      }
      : row);
  return commit(state, { ...state, rooms }, "add_cell");
}

export function removeCell(
  state: BuilderState, roomId: string, cellId: string,
): BuilderState {
  const room = roomOf(state, roomId);
  const cell = room.cells.find((row) => row.cell_id === cellId);
  if (!cell) throw new Error(`unknown cell ${cellId}`);
  const removed = cell.desks.length;
  const rooms = state.rooms.map((row) =>
    row.room_id === roomId
      ? {
        ...row,
        cells: row.cells.filter((item) => item.cell_id !== cellId),
        capacity: row.capacity - removed,
      }
      : row);
  return commit(state, { ...state, rooms }, "remove_cell");
}

export function addDesk(
  state: BuilderState, roomId: string, cellId?: string,
): BuilderState {
  const room = roomOf(state, roomId);
  let target = cellId
    ? room.cells.find((cell) => cell.cell_id === cellId)
    : room.cells.find((cell) => cell.desks.length < CELL_DESK_CAPACITY);
  if (!target) {
    const withCell = addCell(state, roomId, 1);
    return finalise(withCell, "add_desk");
  }
  if (target.desks.length >= CELL_DESK_CAPACITY) {
    throw new Error(`cell ${target.cell_id} is full`);
  }
  const used = new Set(target.desks.map((desk) => `${desk.x},${desk.y}`));
  const slot = CELL_DESK_SLOTS.findIndex(
    ([x, y]) => !used.has(`${x},${y}`));
  if (slot < 0) throw new Error(`cell ${target.cell_id} has no free slot`);
  const [x, y] = CELL_DESK_SLOTS[slot];
  const seatNumber = target.desks.length;
  const desk: BuilderDesk = {
    desk_id: `${roomId}:${target.cell_id}:d${slot}`,
    seat_id: `${roomId}:${target.cell_id}:s${seatNumber}`,
    x,
    y,
    w: 2,
    h: 1,
  };
  const rooms = state.rooms.map((row) => {
    if (row.room_id !== roomId) return row;
    return {
      ...row,
      capacity: row.capacity + 1,
      cells: row.cells.map((cell) => cell.cell_id === target!.cell_id
        ? { ...cell, desks: [...cell.desks, desk] }
        : cell),
    };
  });
  return commit(state, { ...state, rooms }, "add_desk");
}

export function removeDesk(state: BuilderState, deskId: string): BuilderState {
  let roomId = "";
  let cellId = "";
  for (const room of state.rooms) {
    for (const cell of room.cells) {
      if (cell.desks.some((desk) => desk.desk_id === deskId)) {
        roomId = room.room_id;
        cellId = cell.cell_id;
      }
    }
  }
  if (!roomId) throw new Error(`unknown desk ${deskId}`);
  const rooms = state.rooms.map((room) => {
    if (room.room_id !== roomId) return room;
    return {
      ...room,
      capacity: room.capacity - 1,
      cells: room.cells.map((cell) => cell.cell_id === cellId
        ? {
          ...cell,
          desks: cell.desks.filter((desk) => desk.desk_id !== deskId),
        }
        : cell),
    };
  });
  return commit(state, { ...state, rooms }, "remove_desk");
}

export function setRoomStyle(
  state: BuilderState, roomId: string, patch: Partial<RoomStyle>,
): BuilderState {
  const room = roomOf(state, roomId);
  const style: RoomStyle = { ...room.style, ...patch };
  if (style.archetype && !ARCHETYPES.includes(style.archetype as Archetype)) {
    style.archetype = room.style.archetype;
  }
  if (style.palette && !PALETTES.includes(style.palette as Palette)) {
    style.palette = room.style.palette;
  }
  if (!Number.isFinite(style.seed) || style.seed < 0) {
    style.seed = room.style.seed;
  }
  const rooms = state.rooms.map((row) =>
    row.room_id === roomId ? { ...row, style } : row);
  return commit(state, { ...state, rooms }, "set_room_style");
}

export function setDepartmentStyle(
  state: BuilderState, floorId: string, signature: string,
): BuilderState {
  if (!SLUG_RE.test(signature)) {
    throw new Error(`invalid department style ${signature}`);
  }
  const floors = state.floors.map((floor) =>
    floor.floor_id === floorId
      ? { ...floor, department_style: signature } : floor);
  const rooms = state.rooms.map((room) =>
    room.floor_id === floorId
      ? { ...room, style: { ...room.style, department_style: signature } }
      : room);
  return commit(state, { ...state, floors, rooms }, "set_department_style");
}

export function assignRoom(
  state: BuilderState,
  roomId: string,
  assignment: Partial<RoomAssignment>,
): BuilderState {
  const room = roomOf(state, roomId);
  const merged: RoomAssignment = {
    ...room.assignment,
    ...assignment,
    role_ids: assignment.role_ids ?? room.assignment.role_ids,
    agent_ids: assignment.agent_ids ?? room.assignment.agent_ids,
  };
  const rooms = state.rooms.map((row) =>
    row.room_id === roomId
      ? { ...row, assignment: merged } : row);
  return commit(state, { ...state, rooms }, "assign_room");
}

export function cloneDepartment(
  state: BuilderState, sourceFloorId: string, newFloorId: string,
  newName?: string,
): BuilderState {
  if (state.floors.some((floor) => floor.floor_id === newFloorId)) {
    throw new Error(`floor ${newFloorId} already exists`);
  }
  floorOf(state, sourceFloorId);
  const index = Math.max(0, ...state.floors.map((floor) => floor.index)) + 1;
  const floor: BuilderFloor = {
    floor_id: newFloorId,
    index,
    wing: floorOf(state, sourceFloorId).wing,
    name: newName ?? `${floorOf(state, sourceFloorId).name} copy`,
    entry_room_id: "",
    room_ids: [],
    department_style: `${floorOf(state, sourceFloorId).department_style}.${newFloorId}`,
    activation_state: "dormant",
  };
  const clones = state.rooms
    .filter((room) => room.floor_id === sourceFloorId)
    .map((room) => {
      const newRoomId = `${newFloorId}:${room.room_id}`;
      return cloneRoom({
        ...room,
        room_id: newRoomId,
        floor_id: newFloorId,
        style: {
          ...room.style,
          department_style: floor.department_style,
        },
        cells: buildCells(newRoomId, Math.max(1, room.capacity), room.kind),
      });
    });
  const next = withPortals(
    { ...state, floors: [...state.floors, floor] },
    [...state.rooms, ...clones],
  );
  return commit(state, next, "clone_department");
}

export function mergeDepartments(
  state: BuilderState,
  floorIds: string[], targetFloorId: string,
): BuilderState {
  if (!floorIds.includes(targetFloorId)) {
    throw new Error("target floor must be one of the merged floors");
  }
  for (const floorId of floorIds) floorOf(state, floorId);
  const target = floorOf(state, targetFloorId);
  const rooms = state.rooms.map((room) =>
    room.floor_id === targetFloorId
      ? room
      : floorIds.includes(room.floor_id)
        ? {
          ...room,
          floor_id: targetFloorId,
          style: {
            ...room.style,
            department_style: target.department_style,
          },
        }
        : room);
  const floors = state.floors.filter(
    (floor) => !floorIds.includes(floor.floor_id)
      || floor.floor_id === targetFloorId);
  const next = withPortals({ ...state, floors, rooms }, rooms);
  return commit(state, next, "merge_departments");
}

export function splitDepartment(
  state: BuilderState,
  sourceFloorId: string,
  newFloorId: string,
  roomIds: string[],
): BuilderState {
  if (state.floors.some((floor) => floor.floor_id === newFloorId)) {
    throw new Error(`floor ${newFloorId} already exists`);
  }
  const source = floorOf(state, sourceFloorId);
  const sourceIds = state.rooms
    .filter((room) => room.floor_id === sourceFloorId)
    .map((room) => room.room_id);
  if (!roomIds.length || !roomIds.every((id) => sourceIds.includes(id))) {
    throw new Error("split rooms must belong to the source floor");
  }
  if (roomIds.length === sourceIds.length) {
    throw new Error("split would leave the source floor empty");
  }
  const index = Math.max(0, ...state.floors.map((floor) => floor.index)) + 1;
  const newFloor: BuilderFloor = {
    floor_id: newFloorId,
    index,
    wing: source.wing,
    name: `${source.name} split`,
    entry_room_id: "",
    room_ids: [],
    department_style: `${source.department_style}.${newFloorId}`,
    activation_state: "dormant",
  };
  const rooms = state.rooms.map((room) =>
    roomIds.includes(room.room_id)
      ? {
        ...room,
        floor_id: newFloorId,
        style: {
          ...room.style,
          department_style: newFloor.department_style,
        },
      }
      : room);
  const next = withPortals(
    { ...state, floors: [...state.floors, newFloor] }, rooms);
  return commit(state, next, "split_department");
}

export function undo(state: BuilderState): BuilderState {
  if (!state.history.length) return state;
  const history = state.history.slice();
  const previous = history.pop()!;
  return finalise({
    ...state,
    ...snapshot(previous),
    history,
    future: [snapshot(state), ...state.future].slice(0, 50),
    draft_revision: state.draft_revision + 1,
    approved_by: null,
    last_action: "undo",
  }, "undo");
}

export function redo(state: BuilderState): BuilderState {
  if (!state.future.length) return state;
  const [next, ...rest] = state.future;
  return finalise({
    ...state,
    ...snapshot(next),
    history: [...state.history, snapshot(state)].slice(-50),
    future: rest,
    draft_revision: state.draft_revision + 1,
    approved_by: null,
    last_action: "redo",
  }, "redo");
}

export function approveState(state: BuilderState): BuilderState {
  const problems = validateState(state);
  if (problems.length) {
    return finalise({ ...state, problems }, "approve_refused");
  }
  return finalise({
    ...state,
    status: "approved",
    approved_by: "owner",
    last_action: "approve",
  }, "approve");
}

export async function publishState(
  state: BuilderState, options: PublishOptions = {},
): Promise<{ state: BuilderState; result: PublishResult }> {
  const problems = validateState(state);
  if (problems.length) {
    return {
      state: finalise({ ...state, problems }, "publish_refused"),
      result: { kind: "error", problems },
    };
  }
  if (options.onPublish) {
    const answer = await options.onPublish(state);
    if (!answer.ok) {
      const detail = answer.problems ?? ["publish refused"];
      return {
        state: finalise({ ...state, problems: detail }, "publish_refused"),
        result: { kind: "error", problems: detail },
      };
    }
    const version = answer.version ?? (state.published_version ?? 0) + 1;
    return {
      state: finalise({
        ...state,
        status: "published",
        published_version: version,
        versions: { ...state.versions, [version]: snapshot(state) },
        approved_by: null,
        last_action: "publish",
      }, "publish"),
      result: {
        kind: "published", version, digest: digestState(state),
      },
    };
  }
  const version = (state.published_version ?? 0) + 1;
  return {
    state: finalise({
      ...state,
      status: "published",
      published_version: version,
      versions: { ...state.versions, [version]: snapshot(state) },
      approved_by: null,
      last_action: "publish",
    }, "publish"),
    result: { kind: "published", version, digest: digestState(state) },
  };
}

export function rollbackState(
  state: BuilderState, version: number,
): BuilderState {
  const target = state.versions[version];
  if (!target) {
    return finalise({
      ...state,
      problems: [`cannot roll back to version ${version}`],
    }, "rollback_refused");
  }
  return finalise({
    ...state,
    ...snapshot(target),
    status: "rolled_back",
    history: [...state.history, snapshot(state)].slice(-50),
    future: [],
    draft_revision: state.draft_revision + 1,
    approved_by: null,
    last_action: "rollback",
  }, "rollback");
}

export function previewState(state: BuilderState): PreviewResult {
  const problems = validateState(state);
  const cells = state.rooms.reduce((sum, room) => sum + room.cells.length, 0);
  const desks = state.rooms.reduce((sum, room) => sum + room.capacity, 0);
  return {
    valid: problems.length === 0,
    problems,
    counts: {
      floors: state.floors.length,
      rooms: state.rooms.length,
      cells,
      desks,
      portals: state.portals.length,
    },
    capacity: desks,
    egress: state.portals.length,
    digest: digestState(state),
  };
}

export function digestState(state: BuilderState): string {
  const payload = JSON.stringify({
    floors: state.floors,
    rooms: state.rooms,
    portals: state.portals,
  });
  let hash = 2166136261;
  for (let i = 0; i < payload.length; i += 1) {
    hash ^= payload.charCodeAt(i);
    hash = Math.imul(hash, 16777619) >>> 0;
  }
  return hash.toString(16).padStart(8, "0");
}

function overlaps(
  a: { x: number; y: number; w: number; h: number },
  b: { x: number; y: number; w: number; h: number },
): boolean {
  return a.x < b.x + b.w && b.x < a.x + a.w
    && a.y < b.y + b.h && b.y < a.y + a.h;
}

export function validateState(state: BuilderState): string[] {
  const problems: string[] = [];
  if (!state.floors.length) problems.push("SAMPLE: map has no floors");
  const floorIds = new Set(state.floors.map((floor) => floor.floor_id));
  const roomIds = new Set<string>();
  for (const room of state.rooms) {
    if (roomIds.has(room.room_id)) {
      problems.push(`room '${room.room_id}': duplicate id`);
    }
    roomIds.add(room.room_id);
    if (!floorIds.has(room.floor_id)) {
      problems.push(`room '${room.room_id}': unknown floor '${room.floor_id}'`);
    }
    if (room.capacity !== room.cells.reduce((n, c) => n + c.desks.length, 0)) {
      problems.push(`room '${room.room_id}': capacity ${room.capacity} does not match desks`);
    }
    const styleProblems = validateStyle(room.style);
    problems.push(...styleProblems.map((item) => `room '${room.room_id}': ${item}`));
    for (const asset of room.assets) {
      const ref = String(asset.ref).toLowerCase();
      if (asset.paid) {
        problems.push(`room '${room.room_id}' asset '${asset.asset_id}': paid art may not be embedded`);
      }
      if (RAW_ART_SUFFIXES.some((suffix) => ref.endsWith(suffix))) {
        problems.push(`room '${room.room_id}' asset '${asset.asset_id}': raw sprite sheets are not embedded`);
      }
      if (ref.includes("limezu") && asset.kind !== "recipe") {
        problems.push(`room '${room.room_id}' asset '${asset.asset_id}': references a paid art pack`);
      }
    }
    const rects: Array<{ x: number; y: number; w: number; h: number }> = [];
    for (const cell of room.cells) {
      if (cell.cols !== CELL_COLS || cell.rows !== CELL_ROWS) {
        problems.push(`cell '${cell.cell_id}': ${cell.cols}x${cell.rows} is not the 16x12 contract`);
      }
      if (cell.desks.length > CELL_DESK_CAPACITY) {
        problems.push(`cell '${cell.cell_id}': CELL_OVER_CAPACITY (${cell.desks.length} > ${CELL_DESK_CAPACITY})`);
      }
      const cellRect = { x: cell.index * (CELL_COLS + 2), y: 0, w: CELL_COLS, h: CELL_ROWS };
      if (rects.some((other) => overlaps(cellRect, other))) {
        problems.push(`room '${room.room_id}': CELL_COLLISION at cell '${cell.cell_id}'`);
      }
      rects.push(cellRect);
      const deskRects: BuilderDesk[] = [];
      for (const desk of cell.desks) {
        if (desk.x < 0 || desk.y < 0
          || desk.x + desk.w > cell.cols || desk.y + desk.h > cell.rows) {
          problems.push(`cell '${cell.cell_id}': desk '${desk.desk_id}' outside the cell`);
        }
        if (deskRects.some((other) => overlaps(desk, other))) {
          problems.push(`cell '${cell.cell_id}': DESK_COLLISION at desk '${desk.desk_id}'`);
        }
        deskRects.push(desk);
      }
    }
    problems.push(...validateAssignment(room));
  }

  for (const floor of state.floors) {
    const floorRooms = state.rooms.filter((room) => room.floor_id === floor.floor_id);
    if (floorRooms.length && !floorRooms.some((room) => room.room_id === floor.entry_room_id)) {
      problems.push(`floor '${floor.floor_id}': entry room is not on the floor`);
    }
    const present = new Set<string>();
    for (const room of floorRooms) {
      present.add(room.kind);
      present.add(room.style.archetype);
      present.add(room.style.zone);
    }
    for (const zone of DEPARTMENT_ZONES) {
      const aliases = ZONE_ALIASES[zone] ?? [zone];
      if (!aliases.some((alias) => present.has(alias))) {
        problems.push(`floor '${floor.floor_id}': MISSING_REQUIRED_ROOM '${zone}'`);
      }
    }
  }

  // Doors and reachability, per floor.
  const entries = new Map<string, string>();
  for (const floor of state.floors) {
    const rooms = state.rooms.filter((room) => room.floor_id === floor.floor_id);
    if (!rooms.length) continue;
    const entry = rooms.some((room) => room.room_id === floor.entry_room_id)
      ? floor.entry_room_id
      : rooms[0].room_id;
    entries.set(floor.floor_id, entry);
    const adjacency = new Map<string, Set<string>>();
    for (const room of rooms) adjacency.set(room.room_id, new Set());
    for (const portal of state.portals) {
      if (portal.floor_id !== floor.floor_id) continue;
      adjacency.get(portal.source_room_id)?.add(portal.target_room_id);
      adjacency.get(portal.target_room_id)?.add(portal.source_room_id);
    }
    const seen = new Set<string>([entry]);
    const queue = [entry];
    while (queue.length) {
      const current = queue.pop()!;
      for (const next of adjacency.get(current) ?? []) {
        if (!seen.has(next)) { seen.add(next); queue.push(next); }
      }
    }
    for (const room of rooms) {
      if (room.room_id !== entry && !seen.has(room.room_id)) {
        problems.push(`floor '${floor.floor_id}': unreachable room '${room.room_id}'`);
      }
      const portals = state.portals.filter((portal) =>
        portal.source_room_id === room.room_id
        || portal.target_room_id === room.room_id);
      if (!portals.length) {
        problems.push(`room '${room.room_id}': MISSING_DOOR (no door or portal)`);
      }
    }
  }

  // Unique department style.
  const signatures = new Map<string, string>();
  for (const floor of state.floors) {
    const floorRooms = state.rooms.filter((room) => room.floor_id === floor.floor_id);
    if (!floorRooms.length) continue;
    const styles = new Set(floorRooms.map((room) => room.style.department_style));
    if (styles.size > 1) {
      problems.push(`floor '${floor.floor_id}': MIXED_DEPARTMENT_STYLE ${JSON.stringify([...styles].sort())}`);
      continue;
    }
    const signature = [...styles][0];
    if (signatures.has(signature)) {
      problems.push(`STYLE_COLLISION: floors '${signatures.get(signature)}' and '${floor.floor_id}' both use '${signature}'`);
    } else {
      signatures.set(signature, floor.floor_id);
    }
  }
  return [...new Set(problems)];
}

function validateStyle(style: RoomStyle): string[] {
  const problems: string[] = [];
  if (!ARCHETYPES.includes(style.archetype as Archetype)) {
    problems.push(`unknown archetype '${style.archetype}'`);
  }
  if (!PALETTES.includes(style.palette as Palette)) {
    problems.push(`unknown palette '${style.palette}'`);
  }
  for (const [label, value] of [
    ["floor", style.floor], ["wall", style.wall], ["rug", style.rug],
    ["furniture_set", style.furniture_set], ["lighting", style.lighting],
    ["signage", style.signage], ["department_style", style.department_style],
    ["zone", style.zone],
  ] as Array<[string, string]>) {
    if (!SLUG_RE.test(String(value))) {
      problems.push(`${label} must be a slug, got '${value}'`);
    }
  }
  if (!Number.isInteger(style.seed) || style.seed < 0) {
    problems.push("seed must be a non-negative int");
  }
  if (!Number.isInteger(style.style_version) || style.style_version < 1) {
    problems.push("style_version must be >= 1");
  }
  return problems;
}

function validateAssignment(room: BuilderRoom): string[] {
  const problems: string[] = [];
  const assignment = room.assignment;
  for (const [label, value] of [
    ["business_id", assignment.business_id],
    ["department_id", assignment.department_id],
    ["capability_manifest_id", assignment.capability_manifest_id],
    ["memory_scope", assignment.memory_scope],
  ] as Array<[string, string]>) {
    if (value && !TOKEN_RE.test(value)) {
      problems.push(`room '${room.room_id}': assignment.${label} is not a valid token`);
    }
  }
  for (const [label, values] of [
    ["role_ids", assignment.role_ids],
    ["agent_ids", assignment.agent_ids],
  ] as Array<[string, string[]]>) {
    const seen = new Set<string>();
    for (const value of values) {
      if (!TOKEN_RE.test(value)) {
        problems.push(`room '${room.room_id}': assignment.${label} has invalid value '${value}'`);
      }
      if (seen.has(value)) {
        problems.push(`room '${room.room_id}': assignment.${label} repeats '${value}'`);
      }
      seen.add(value);
    }
  }
  return problems;
}

function archetypeFor(kind: string): string {
  const table: Record<string, string> = {
    reception: "reception",
    work: "open_office",
    meeting: "meeting",
    focus: "wellness",
    head_office: "executive",
    kitchen: "kitchen",
    restroom: "wellness",
    lounge: "wellness",
    storage: "fulfillment",
    comms: "control_room",
    server_ops: "server_ops",
  };
  return table[kind] ?? "open_office";
}

function finalise(state: BuilderState, action: string): BuilderState {
  const problems = validateState(state);
  return { ...state, problems, last_action: action };
}

export function liveText(state: BuilderState): string {
  const preview = previewState(state);
  return `Draft revision ${state.draft_revision}; ${state.status}; `
    + `${preview.counts.floors} floors, ${preview.counts.rooms} rooms, `
    + `${preview.counts.desks} desks; ${preview.problems.length} problems; `
    + `last action ${state.last_action}`;
}

export function selectedRoom(state: BuilderState, roomId: string | null):
BuilderRoom | null {
  if (!roomId) return null;
  return state.rooms.find((room) => room.room_id === roomId) ?? null;
}

export function roomList(state: BuilderState): BuilderRoom[] {
  return state.rooms.slice();
}
