/* The visual outside-world scene for Qubit Office (P6-03).
 *
 * WHAT THIS IS
 * ------------
 * A pure scene builder and Canvas2D painter for the campus exterior that
 * `OutsideWorld.tsx` already projects. It reads ONLY the presentation payload
 * (`qubit-office-world-outside/1`): zones, chunks, their tile bounds, portals /
 * floor links and the weather / day-night / season events. Everything drawn is
 * either one of those rows or a deterministic decorative detail on top of one.
 *
 * WHAT THIS IS NOT
 * ----------------
 * It is not a second data source and it is not a business surface. The scene
 * has no agent, desk, run, task or session concept, and it does not place a
 * zone, a chunk or a floor marker the payload did not carry. A chunk whose
 * `zone_id` names no zone is counted as unplaced and is never painted. No
 * water, tree, flower or building part is invented: those layers only appear
 * over a chunk the payload already carries.
 *
 * REAL ART, HONESTLY OPTIONAL
 * ---------------------------
 * The owner's LimeZu / Serene Village packs are paid art and are never
 * committed. At runtime the component probes the ACTIVE office theme pack for
 * an optional outside manifest at:
 *
 *     <theme assetBase>/outside/scene.json
 *
 * That URL is served by the existing theme static route, which only serves
 * files declared in `theme.json`. A pack that ships the manifest + sheets uses
 * real tiles; an absent manifest (the normal case today) draws the clearly
 * labelled procedural scene below. Nothing is fabricated to look like the
 * pack is present.
 *
 * COMPOSITION, NOT A REPEATED TILE
 * --------------------------------
 * A manifest may declare an additive `composition` block (roles -> tile
 * specs, including multi-tile frames and edge roles) or name the built-in
 * `serene-village-16x16` preset. A pack built by the existing theme pipeline
 * does not carry the block yet, so the loader also recognises the exact
 * Serene Village 16x16 sheet (304x720, cell 16) and applies the preset
 * coordinates in code. Those coordinates are pack metadata, not art: the PNG
 * itself still comes from the local pack. When a role is missing the painter
 * falls back to the procedural path and the caption says so.
 *
 * Everything in this module is deterministic: the same layout + options paint
 * the same instruction sequence, which is what the render-evidence test pins.
 */
import type {
  OutsideChunk, OutsideEnvironmentEvent, OutsideFloorLink, OutsideSummary,
  OutsideWorld, OutsideZone,
} from "./outside-world";

/** The art manifest a local theme pack may carry. */
export const OUTSIDE_SCENE_ART_SCHEMA = "qubit-office-outside-art/1";
/** Additive manifest revision: same document plus `composition`/`preset`. */
export const OUTSIDE_SCENE_ART_SCHEMA_V2 = "qubit-office-outside-art/2";
/** The one built-in composition preset this module knows by name. */
export const SERENE_VILLAGE_PRESET = "serene-village-16x16";
/** The real Serene Village 16x16 sheet this preset's coordinates target. */
export const SERENE_VILLAGE_SHEET_SIZE = { width: 304, height: 720 } as const;
/** CSS px per exterior tile. The map is tile-space; this is presentation. */
export const SCENE_TILE_PX = 8;
/** Draw order for chunk layers; later layers paint on top. */
export const SCENE_LAYER_ORDER = ["ground", "floor", "building", "overlay"] as const;

/** Named tile roles a composition manifest may declare. */
export type OutsideCompositionRole =
  | "grass" | "road" | "sidewalk" | "path"
  | "water" | "water_n" | "water_s" | "water_e" | "water_w"
  | "water_nw" | "water_ne" | "water_sw" | "water_se"
  | "trees" | "bush" | "flower" | "fence" | "prop"
  | "building" | "roof" | "wall" | "window" | "door" | "sign";

const COMPOSITION_ROLES: ReadonlySet<string> = new Set<OutsideCompositionRole>([
  "grass", "road", "sidewalk", "path", "water", "water_n", "water_s",
  "water_e", "water_w", "water_nw", "water_ne", "water_sw", "water_se",
  "trees", "bush", "flower", "fence", "prop", "building", "roof", "wall",
  "window", "door", "sign",
]);

/** Water edge role for a tile position inside one payload water chunk. */
export function waterRoleAt(
  rect: SceneRect, x: number, y: number,
): OutsideCompositionRole {
  const left = x <= rect.x;
  const right = x >= rect.x + rect.width - 1;
  const top = y <= rect.y;
  const bottom = y >= rect.y + rect.height - 1;
  if (top && left) return "water_nw";
  if (top && right) return "water_ne";
  if (bottom && left) return "water_sw";
  if (bottom && right) return "water_se";
  if (top) return "water_n";
  if (bottom) return "water_s";
  if (left) return "water_w";
  if (right) return "water_e";
  return "water";
}

export type OutsideArtKind =
  | "grass" | "road" | "sidewalk" | "path" | "parking" | "garden"
  | "trees" | "fence" | "prop" | "facade" | "roof" | "entrance"
  | "windows" | "signage" | "lobby" | "security" | "elevator"
  | "stairs" | "water";

const ART_KINDS: ReadonlySet<string> = new Set<OutsideArtKind>([
  "grass", "road", "sidewalk", "path", "parking", "garden", "trees",
  "fence", "prop", "facade", "roof", "entrance", "windows", "signage",
  "lobby", "security", "elevator", "stairs", "water",
]);

/** Chunk kind -> art sheet kind. Unknown kinds stay procedural. */
const CHUNK_ART_KIND: Readonly<Record<string, OutsideArtKind>> = {
  ground: "grass",
  road: "road",
  sidewalk: "sidewalk",
  parking: "parking",
  path: "path",
  trees: "trees",
  garden: "garden",
  fence: "fence",
  prop: "prop",
  facade: "facade",
  roof: "roof",
  entrance: "entrance",
  windows: "windows",
  signage: "signage",
  lobby: "lobby",
  elevator_bank: "elevator",
  security: "security",
};

export interface SceneRect {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface SceneZone {
  zone_id: string;
  kind: string;
  label: string;
  state: "active" | "dormant";
  animated: boolean;
  rect: SceneRect | null;
  chunks: number;
}

export interface SceneChunk {
  chunk_id: string;
  zone_id: string;
  kind: string;
  layer: string;
  rect: SceneRect;
  z_index: number;
  active: boolean;
  animated: boolean;
  art: OutsideArtKind | null;
}

export interface SceneFloorMarker {
  kind: string;
  floor_id: string;
  portal_id: string;
  label: string;
  chunk_id: string;
  x: number;
  y: number;
}

export interface SceneEnvironment {
  weather: string;
  day_night: string;
  season: string;
  events: OutsideEnvironmentEvent[];
}

export interface ScenePalette {
  skyTop: string;
  skyBottom: string;
  ground: string;
  groundLine: string;
  tint: string;
  tintAlpha: number;
  foliage: string;
  foliageAlt: string;
  weatherKnown: boolean;
  dayNightKnown: boolean;
  seasonKnown: boolean;
}

export interface OutsideSceneLayout {
  /** Exterior map size in tiles (derived from the payload's own bounds). */
  width: number;
  height: number;
  tileSize: number;
  /** Content bounds in tiles (the payload's chunks, never a guessed campus). */
  bounds: SceneRect;
  zones: SceneZone[];
  chunks: SceneChunk[];
  floors: SceneFloorMarker[];
  /** Chunks the payload carried whose zone is not in `zones`. */
  unplacedChunks: number;
  environment: SceneEnvironment;
  palette: ScenePalette;
  summary: OutsideSummary;
  digest: string;
}

export interface OutsideArtFrame {
  image: CanvasImageSource;
  sx: number;
  sy: number;
  sw: number;
  sh: number;
  tile: number;
  /** Animation frame count when the source is a spritesheet row. */
  frames?: number;
  fps?: number;
}

export interface OutsideSceneArt {
  credit: string;
  source: "theme" | "injected";
  tiles: Partial<Record<OutsideArtKind, OutsideArtFrame>>;
  /** Composition roles (grass variants, water edges, building parts, ...). */
  frames?: Partial<Record<OutsideCompositionRole, OutsideArtFrame[]>>;
  /** True when at least one composition role resolved to real art. */
  composed?: boolean;
  /** True when any resolved frame carries more than one animation frame. */
  animated?: boolean;
}

export interface OutsideArtTileSpec {
  sheet: string;
  col: number;
  row: number;
  /** Frame width in cells (default 1). */
  w?: number;
  /** Frame height in cells (default 1). */
  h?: number;
  /** Optional exact source width in pixels (overrides `w * cell`). */
  sw?: number;
  /** Optional exact source height in pixels (overrides `h * cell`). */
  sh?: number;
  /** Animation frame count (default 1). */
  frames?: number;
  /** Animation frames per second (default 6). */
  fps?: number;
}

export type OutsideSceneComposition =
  Partial<Record<OutsideCompositionRole, OutsideArtTileSpec[]>>;

export interface OutsideSceneArtManifest {
  schema: string;
  credit: string;
  cell: number;
  tiles: Partial<Record<OutsideArtKind, OutsideArtTileSpec>>;
  /** Additive (schema /2 or later): named composition roles. */
  composition?: OutsideSceneComposition;
  /** Optional built-in preset id. */
  preset?: string;
}

// ----------------------------------------------------------------- helpers
function int(value: unknown, fallback = 0): number {
  const n = typeof value === "number" ? value
    : typeof value === "string" ? Number(value) : NaN;
  return Number.isFinite(n) ? Math.trunc(n) : fallback;
}

function text(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function rectOf(value: SceneRect): SceneRect {
  return {
    x: int(value.x), y: int(value.y),
    width: Math.max(0, int(value.width)),
    height: Math.max(0, int(value.height)),
  };
}

function unionRects(rects: readonly SceneRect[]): SceneRect | null {
  if (!rects.length) return null;
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  for (const rect of rects) {
    if (rect.width <= 0 || rect.height <= 0) continue;
    minX = Math.min(minX, rect.x);
    minY = Math.min(minY, rect.y);
    maxX = Math.max(maxX, rect.x + rect.width);
    maxY = Math.max(maxY, rect.y + rect.height);
  }
  if (!Number.isFinite(minX) || maxX <= minX || maxY <= minY) return null;
  return { x: minX, y: minY, width: maxX - minX, height: maxY - minY };
}

/** A stable 32-bit hash for deterministic decorative detail. */
export function hashString(value: string): number {
  let hash = 2166136261;
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return hash >>> 0;
}

/** Deterministic 0..1 from an integer seed (no platform RNG). */
export function hash01(seed: number): number {
  let x = (seed ^ 0x9e3779b9) >>> 0;
  x = Math.imul(x ^ (x >>> 16), 0x85ebca6b) >>> 0;
  x = Math.imul(x ^ (x >>> 13), 0xc2b2ae35) >>> 0;
  return ((x ^ (x >>> 16)) >>> 0) / 4294967296;
}

/** Payload chunk kind -> the matching art sheet kind (or null). */
export function artKindForChunk(kind: string): OutsideArtKind | null {
  return CHUNK_ART_KIND[kind] ?? null;
}

function layerRank(layer: string): number {
  const index = (SCENE_LAYER_ORDER as readonly string[]).indexOf(layer);
  return index < 0 ? SCENE_LAYER_ORDER.length : index;
}

// --------------------------------------------------------------- palette
const DAY_PALETTES: Record<string, {
  skyTop: string; skyBottom: string; tint: string; tintAlpha: number;
}> = {
  dawn: {
    skyTop: "#7aa0c8", skyBottom: "#f2c9a0", tint: "#f7b57a", tintAlpha: 0.06,
  },
  day: {
    skyTop: "#8fc7e8", skyBottom: "#d9edf7", tint: "#ffffff", tintAlpha: 0,
  },
  dusk: {
    skyTop: "#5a4a7a", skyBottom: "#e58b6b", tint: "#ff8f5a", tintAlpha: 0.12,
  },
  night: {
    skyTop: "#0b1030", skyBottom: "#1c2946", tint: "#101a35", tintAlpha: 0.34,
  },
};

const SEASON_PALETTES: Record<string, {
  ground: string; groundLine: string; foliage: string; foliageAlt: string;
}> = {
  spring: {
    ground: "#7fc471", groundLine: "#68ad5c",
    foliage: "#4f9a55", foliageAlt: "#8fd07f",
  },
  summer: {
    ground: "#6fb862", groundLine: "#579b4d",
    foliage: "#3f8449", foliageAlt: "#7cc06f",
  },
  autumn: {
    ground: "#b9a25f", groundLine: "#9c8547",
    foliage: "#b56b2f", foliageAlt: "#d69a45",
  },
  winter: {
    ground: "#dfe8ee", groundLine: "#c2d0da",
    foliage: "#6f8b7f", foliageAlt: "#adc3bd",
  },
};

/** Map the payload's three environment axes to a deterministic palette. */
export function environmentPalette(
  dayNight: string, season: string, weather: string,
): ScenePalette {
  const day = DAY_PALETTES[dayNight] ?? DAY_PALETTES.day;
  const seasonPalette = SEASON_PALETTES[season] ?? SEASON_PALETTES.summer;
  const weatherKnown = ["clear", "rain", "snow", "fog"].includes(weather);
  // Weather is drawn as an overlay; its colour never changes the terrain.
  const tintAlpha = day.tintAlpha
    + (weather === "fog" ? 0.08 : weather === "rain" ? 0.05 : 0);
  return {
    skyTop: day.skyTop,
    skyBottom: day.skyBottom,
    ground: seasonPalette.ground,
    groundLine: seasonPalette.groundLine,
    tint: day.tint,
    tintAlpha: Math.min(0.5, tintAlpha),
    foliage: seasonPalette.foliage,
    foliageAlt: seasonPalette.foliageAlt,
    weatherKnown,
    dayNightKnown: Boolean(DAY_PALETTES[dayNight]),
    seasonKnown: Boolean(SEASON_PALETTES[season]),
  };
}

// --------------------------------------------------------- layout builder
function placedChunks(world: OutsideWorld): OutsideChunk[] {
  const zoneIds = new Set(world.zones.map((zone) => zone.zone_id));
  return world.chunks.filter((chunk) => zoneIds.has(chunk.zone_id));
}

function floorMarkers(
  world: OutsideWorld, chunks: readonly SceneChunk[],
): SceneFloorMarker[] {
  const byId = new Map(chunks.map((chunk) => [chunk.chunk_id, chunk]));
  const portals = new Map(
    world.portals.map((portal) => [portal.portal_id, portal]));
  const markers: SceneFloorMarker[] = [];
  for (const link of world.floor_links) {
    const portal = portals.get(link.portal_id);
    if (!portal) continue;
    const chunk = byId.get(portal.from_chunk);
    if (!chunk || chunk.rect.width <= 0 || chunk.rect.height <= 0) continue;
    markers.push({
      kind: link.kind,
      floor_id: link.floor_id,
      portal_id: link.portal_id,
      label: link.label,
      chunk_id: chunk.chunk_id,
      x: chunk.rect.x + chunk.rect.width / 2,
      y: chunk.rect.y + chunk.rect.height / 2,
    });
  }
  // Several portals may share one elevator bank. Spread them symmetrically in
  // payload order so both the elevator and the stairs stay visible; the offset
  // is presentation only and never adds a floor.
  const groups = new Map<string, SceneFloorMarker[]>();
  for (const marker of markers) {
    const list = groups.get(marker.chunk_id) ?? [];
    list.push(marker);
    groups.set(marker.chunk_id, list);
  }
  for (const group of groups.values()) {
    if (group.length < 2) continue;
    for (let index = 0; index < group.length; index += 1) {
      group[index].x += (index - (group.length - 1) / 2) * 3.2;
    }
  }
  return markers;
}

/**
 * Turn the parsed projection into the one scene model the painter consumes.
 *
 * The map extent is the union of the PAYLOAD's chunk bounds. If it is empty,
 * the union of the payload's zone bounds is used instead. A 1x1 canvas is the
 * honest answer for a payload with no geometry, never a fabricated campus.
 */
export function buildOutsideScene(world: OutsideWorld): OutsideSceneLayout {
  const placed = placedChunks(world);
  const chunks: SceneChunk[] = placed.map((chunk) => ({
    chunk_id: chunk.chunk_id,
    zone_id: chunk.zone_id,
    kind: chunk.kind,
    layer: chunk.layer,
    rect: rectOf(chunk.bounds),
    z_index: chunk.z_index,
    active: chunk.active,
    animated: chunk.animated,
    art: artKindForChunk(chunk.kind),
  }));

  const zoneRows: SceneZone[] = world.zones.map((zone: OutsideZone) => {
    const members = chunks.filter((chunk) => chunk.zone_id === zone.zone_id);
    return {
      zone_id: zone.zone_id,
      kind: zone.kind,
      label: zone.label,
      state: zone.state,
      animated: zone.animated,
      rect: unionRects(members.map((chunk) => chunk.rect))
        ?? unionRects([rectOf(zone.bounds)]),
      chunks: members.length,
    };
  });

  const bounds = unionRects(chunks.map((chunk) => chunk.rect))
    ?? unionRects(world.zones.map((zone) => rectOf(zone.bounds)))
    ?? { x: 0, y: 0, width: 1, height: 1 };
  const width = Math.max(1, bounds.width);
  const height = Math.max(1, bounds.height);

  return {
    width,
    height,
    tileSize: 16,
    bounds,
    zones: zoneRows,
    chunks,
    floors: floorMarkers(world, chunks),
    unplacedChunks: world.chunks.length - placed.length,
    environment: {
      weather: world.environment.weather,
      day_night: world.environment.day_night,
      season: world.environment.season,
      events: world.environment.events,
    },
    palette: environmentPalette(
      world.environment.day_night,
      world.environment.season,
      world.environment.weather,
    ),
    summary: world.summary,
    digest: world.digest,
  };
}

/**
 * Painter order: depth first, then the payload's layer, then id.
 *
 * The payload's `z_index` is the only per-chunk depth fact. Sorting by layer
 * first would draw an overlay path (z 0) over a building (z 10) because both
 * are "overlay" vs "building" layers; depth-first keeps the path on the
 * ground where it belongs while still respecting the declared layer.
 */
export function paintOrderedChunks(
  layout: OutsideSceneLayout,
): SceneChunk[] {
  return [...layout.chunks].sort((a, b) => {
    if (a.z_index !== b.z_index) return a.z_index - b.z_index;
    const layer = layerRank(a.layer) - layerRank(b.layer);
    if (layer !== 0) return layer;
    return a.chunk_id.localeCompare(b.chunk_id);
  });
}

/** Stable ordering so a repaint is byte-identical for identical payloads. */
export function orderedSceneChunks(
  layout: OutsideSceneLayout,
): SceneChunk[] {
  return [...layout.chunks].sort((a, b) => {
    const layer = layerRank(a.layer) - layerRank(b.layer);
    if (layer !== 0) return layer;
    if (a.z_index !== b.z_index) return a.z_index - b.z_index;
    return a.chunk_id.localeCompare(b.chunk_id);
  });
}

// ------------------------------------------------------------- art parsing
function safeSheet(value: unknown): string {
  const sheet = text(value);
  if (!sheet) return "";
  const lower = sheet.trim().toLowerCase();
  if (lower.startsWith("javascript:") || lower.startsWith("vbscript:")) {
    return "";
  }
  if (lower.includes("..")) return "";
  return sheet;
}

function tileSpec(entry: unknown): OutsideArtTileSpec | null {
  if (entry === null || typeof entry !== "object" || Array.isArray(entry)) {
    return null;
  }
  const spec = entry as Record<string, unknown>;
  const sheet = safeSheet(spec.sheet);
  const col = int(spec.col);
  const row = int(spec.row);
  if (!sheet || col < 0 || row < 0) return null;
  const parsed: OutsideArtTileSpec = { sheet, col, row };
  const w = int(spec.w, 1);
  const h = int(spec.h, 1);
  if (w >= 1 && w <= 16) parsed.w = w;
  if (h >= 1 && h <= 16) parsed.h = h;
  const sw = int(spec.sw);
  const sh = int(spec.sh);
  if (sw > 0 && sw <= 4096) parsed.sw = sw;
  if (sh > 0 && sh <= 4096) parsed.sh = sh;
  const frames = int(spec.frames, 1);
  if (frames >= 1 && frames <= 64) parsed.frames = frames;
  const fps = int(spec.fps);
  if (fps > 0 && fps <= 60) parsed.fps = fps;
  return parsed;
}

function parseComposition(
  source: unknown,
): OutsideSceneComposition | undefined {
  if (source === null || typeof source !== "object" || Array.isArray(source)) {
    return undefined;
  }
  const composition: OutsideSceneComposition = {};
  let found = 0;
  for (const [role, entry] of Object.entries(source as Record<string, unknown>)) {
    if (!COMPOSITION_ROLES.has(role)) continue;
    const list = Array.isArray(entry) ? entry : [entry];
    const specs: OutsideArtTileSpec[] = [];
    for (const item of list) {
      const parsed = tileSpec(item);
      if (parsed) specs.push(parsed);
    }
    if (specs.length) {
      composition[role as OutsideCompositionRole] = specs;
      found += 1;
    }
  }
  return found ? composition : undefined;
}

/** Validate an outside-art manifest. Returns null instead of inventing art. */
export function parseOutsideSceneArtManifest(
  value: unknown,
): OutsideSceneArtManifest | null {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    return null;
  }
  const row = value as Record<string, unknown>;
  const schema = text(row.schema);
  if (schema !== OUTSIDE_SCENE_ART_SCHEMA
      && schema !== OUTSIDE_SCENE_ART_SCHEMA_V2) {
    return null;
  }
  const cell = int(row.cell);
  if (cell <= 0 || cell > 128) return null;
  const source = row.tiles;
  const tiles: Partial<Record<OutsideArtKind, OutsideArtTileSpec>> = {};
  if (source !== null && typeof source === "object" && !Array.isArray(source)) {
    for (const [kind, entry] of Object.entries(
      source as Record<string, unknown>)) {
      if (!ART_KINDS.has(kind)) continue;
      const parsed = tileSpec(entry);
      if (parsed) tiles[kind as OutsideArtKind] = parsed;
    }
  }
  const composition = parseComposition(row.composition);
  if (!Object.keys(tiles).length && !composition) return null;
  const parsed: OutsideSceneArtManifest = {
    schema: OUTSIDE_SCENE_ART_SCHEMA,
    credit: text(row.credit, "LimeZu — https://limezu.itch.io"),
    cell,
    tiles,
  };
  if (composition) parsed.composition = composition;
  const preset = text(row.preset);
  if (preset) parsed.preset = preset;
  return parsed;
}

/**
 * Built-in coordinates for the real Serene Village 16x16 sheet (304x720).
 *
 * These are pack metadata (which cell holds which role), not art. The PNG is
 * always read from the local pack; nothing here is a committed sprite.
 */
export function sereneVillageComposition(
  sheet: string,
): OutsideSceneComposition {
  const frame = (col: number, row: number, w = 1, h = 1) =>
    ({ sheet, col, row, w, h });
  return {
    grass: [frame(3, 0), frame(4, 0), frame(5, 0), frame(17, 1)],
    road: [frame(10, 2), frame(6, 3), frame(9, 3)],
    sidewalk: [frame(17, 3), frame(17, 4), frame(17, 5)],
    path: [frame(6, 3), frame(9, 3), frame(10, 2)],
    water: [frame(12, 1)],
    water_n: [frame(12, 0)],
    water_s: [frame(12, 2)],
    water_w: [frame(11, 1)],
    water_e: [frame(13, 1)],
    water_nw: [frame(11, 0)],
    water_ne: [frame(13, 0)],
    water_sw: [frame(11, 2)],
    water_se: [frame(13, 2)],
    trees: [frame(8, 12, 4, 3), frame(11, 12, 4, 3), frame(14, 12, 4, 3)],
    bush: [frame(5, 12), frame(0, 15), frame(1, 15)],
    flower: [frame(3, 13), frame(2, 14), frame(4, 14)],
    fence: [frame(5, 14), frame(5, 15), frame(4, 14)],
    prop: [frame(0, 13), frame(6, 13), frame(6, 14)],
    building: [{ sheet, col: 0, row: 25, w: 5, h: 4, sw: 72, sh: 64 }],
    roof: [frame(2, 26, 1, 1), frame(3, 26, 1, 1)],
    wall: [frame(0, 27), frame(5, 27)],
    window: [frame(1, 27), frame(4, 27)],
    door: [frame(2, 27, 2, 2)],
    sign: [frame(6, 13, 1, 1), frame(6, 14, 1, 1)],
  };
}

function mergeComposition(
  base: OutsideSceneComposition | undefined,
  extra: OutsideSceneComposition | undefined,
): OutsideSceneComposition | undefined {
  if (!base && !extra) return undefined;
  const merged: OutsideSceneComposition = { ...(base ?? {}) };
  if (extra) {
    for (const [role, specs] of Object.entries(extra)) {
      if (specs && specs.length) {
        merged[role as OutsideCompositionRole] = specs;
      }
    }
  }
  return merged;
}

export interface OutsideArtLoadInput {
  manifest?: unknown;
  manifestUrl?: string | null;
  /** Theme asset base; sheet paths are relative to this, not the manifest. */
  assetBase?: string | null;
  fetchImpl?: typeof fetch;
  loadImage?: (url: string) => Promise<{
    image: CanvasImageSource; width: number; height: number;
  }>;
  source?: "theme" | "injected";
}

export type OutsideArtLoadResult =
  | { status: "ready"; art: OutsideSceneArt }
  | { status: "absent" | "error"; reason: string };

function resolveSheetUrl(sheet: string, baseUrl: string | null): string {
  if (/^[a-z][a-z0-9+.-]*:/i.test(sheet)) return sheet;
  if (!baseUrl) return sheet;
  try {
    return new URL(sheet, baseUrl).toString();
  } catch {
    return sheet;
  }
}

function defaultLoadImage(url: string): Promise<{
  image: CanvasImageSource; width: number; height: number;
}> {
  return new Promise((resolve, reject) => {
    if (typeof Image !== "function") {
      reject(new Error("no Image implementation"));
      return;
    }
    const img = new Image();
    img.onload = () => {
      const width = img.naturalWidth || img.width;
      const height = img.naturalHeight || img.height;
      if (!(width > 0) || !(height > 0)) {
        reject(new Error("image has no pixels"));
        return;
      }
      resolve({ image: img, width, height });
    };
    img.onerror = () => reject(new Error("image failed to load"));
    img.src = url;
  });
}

/**
 * Resolve an optional outside art pack.
 *
 * A missing manifest is `absent` (the normal case), never an error banner. A
 * present-but-invalid manifest is a typed `error`, so the badge can say the
 * art was rejected instead of silently pretending it loaded.
 */
export async function loadOutsideSceneArt(
  input: OutsideArtLoadInput,
): Promise<OutsideArtLoadResult> {
  const source = input.source ?? "theme";
  let raw = input.manifest;
  let baseUrl = input.assetBase ?? input.manifestUrl ?? null;
  if (input.assetBase) {
    baseUrl = baseUrl.endsWith("/") ? baseUrl : `${baseUrl}/`;
  }
  if (raw === undefined && input.manifestUrl) {
    const fetchImpl = input.fetchImpl ?? fetch;
    try {
      const response = await fetchImpl(input.manifestUrl, {
        credentials: "same-origin",
        headers: { Accept: "application/json" },
      });
      if (!response || response.ok === false) {
        return { status: "absent", reason: "no local outside manifest" };
      }
      raw = await response.json().catch(() => undefined);
    } catch {
      return { status: "absent", reason: "local outside manifest unreachable" };
    }
  }
  if (raw === undefined || raw === null) {
    return { status: "absent", reason: "no local outside manifest" };
  }
  const manifest = parseOutsideSceneArtManifest(raw);
  if (!manifest) {
    return { status: "error", reason: "outside art manifest is invalid" };
  }
  const loadImage = input.loadImage ?? defaultLoadImage;
  const specs: OutsideArtTileSpec[] = [
    ...Object.values(manifest.tiles).filter(Boolean) as OutsideArtTileSpec[],
    ...Object.values(manifest.composition ?? {}).flat()
      .filter(Boolean) as OutsideArtTileSpec[],
  ];
  const sheets = new Map<string, { image: CanvasImageSource; width: number; height: number }>();
  try {
    for (const spec of specs) {
      if (!spec) continue;
      if (sheets.has(spec.sheet)) continue;
      const url = resolveSheetUrl(spec.sheet, baseUrl);
      sheets.set(spec.sheet, await loadImage(url));
    }
  } catch (exc) {
    return {
      status: "error",
      reason: exc instanceof Error ? exc.message : "outside art failed to load",
    };
  }
  const tiles: OutsideSceneArt["tiles"] = {};
  for (const [kind, spec] of Object.entries(manifest.tiles)) {
    if (!spec) continue;
    const sheet = sheets.get(spec.sheet);
    if (!sheet) continue;
    const sw = spec.sw ?? manifest.cell * (spec.w ?? 1);
    const sh = spec.sh ?? manifest.cell * (spec.h ?? 1);
    const sx = spec.col * manifest.cell;
    const sy = spec.row * manifest.cell;
    if (sx + sw > sheet.width || sy + sh > sheet.height) continue;
    tiles[kind as OutsideArtKind] = {
      image: sheet.image, sx, sy, sw, sh, tile: manifest.cell,
      frames: spec.frames, fps: spec.fps,
    };
  }

  // A pack built by the theme pipeline predates the composition block. When
  // its only sheet is the exact Serene Village 16x16 sheet, apply the built-in
  // role coordinates; an explicit composition (or preset) always wins.
  let composition = manifest.composition;
  const firstSpec = specs[0];
  if (manifest.preset === SERENE_VILLAGE_PRESET && firstSpec) {
    composition = mergeComposition(
      sereneVillageComposition(firstSpec.sheet), composition);
  } else if (!manifest.preset && !composition && firstSpec) {
    const sheet = sheets.get(firstSpec.sheet);
    if (manifest.cell === 16 && sheet
        && sheet.width === SERENE_VILLAGE_SHEET_SIZE.width
        && sheet.height === SERENE_VILLAGE_SHEET_SIZE.height) {
      composition = sereneVillageComposition(firstSpec.sheet);
    }
  }
  const frames: NonNullable<OutsideSceneArt["frames"]> = {};
  let animated = false;
  let composed = false;
  for (const [role, roleSpecs] of Object.entries(composition ?? {})) {
    if (!roleSpecs || !roleSpecs.length) continue;
    const resolved: OutsideArtFrame[] = [];
    for (const spec of roleSpecs) {
      const sheet = sheets.get(spec.sheet);
      if (!sheet) continue;
      const sw = spec.sw ?? manifest.cell * (spec.w ?? 1);
      const sh = spec.sh ?? manifest.cell * (spec.h ?? 1);
      const sx = spec.col * manifest.cell;
      const sy = spec.row * manifest.cell;
      if (sx + sw > sheet.width || sy + sh > sheet.height) continue;
      const frame: OutsideArtFrame = {
        image: sheet.image, sx, sy, sw, sh, tile: manifest.cell,
        frames: spec.frames, fps: spec.fps,
      };
      if ((spec.frames ?? 1) > 1) animated = true;
      resolved.push(frame);
    }
    if (resolved.length) {
      frames[role as OutsideCompositionRole] = resolved;
      composed = true;
    }
  }
  if (!Object.keys(tiles).length && !Object.keys(frames).length) {
    return { status: "error", reason: "outside art has no usable tiles" };
  }
  const art: OutsideSceneArt = {
    credit: manifest.credit, source, tiles, composed, animated,
  };
  if (composed) art.frames = frames;
  return { status: "ready", art };
}

// ---------------------------------------------------------------- painter
export interface OutsideDrawOptions {
  timeMs?: number;
  reducedMotion?: boolean;
  art?: OutsideSceneArt | null;
  selectedZoneId?: string | null;
}

type Ctx = CanvasRenderingContext2D;

function px(value: number, tilePx: number): number {
  return value * tilePx;
}

function fill(ctx: Ctx, colour: string, x: number, y: number,
              w: number, h: number, tilePx: number): void {
  ctx.fillStyle = colour;
  ctx.fillRect(px(x, tilePx), px(y, tilePx),
    Math.max(0, px(w, tilePx)), Math.max(0, px(h, tilePx)));
}

function detailGrass(ctx: Ctx, rect: SceneRect, palette: ScenePalette,
                     seed: number, tilePx: number): void {
  const count = Math.min(160, Math.max(4, Math.round(rect.width * rect.height / 12)));
  ctx.strokeStyle = palette.groundLine;
  ctx.lineWidth = Math.max(1, tilePx / 8);
  for (let index = 0; index < count; index += 1) {
    const x = rect.x + hash01(seed + index * 3) * rect.width;
    const y = rect.y + hash01(seed + index * 3 + 1) * rect.height;
    const height = 0.6 + hash01(seed + index * 3 + 2) * 1.4;
    ctx.beginPath();
    ctx.moveTo(px(x, tilePx), px(y, tilePx));
    ctx.lineTo(px(x, tilePx), px(y - height, tilePx));
    ctx.stroke();
  }
}

function detailRoad(ctx: Ctx, rect: SceneRect, tilePx: number): void {
  ctx.fillStyle = "#e6e2c8";
  const dash = 2;
  const gap = 2;
  if (rect.width >= rect.height) {
    const y = rect.y + rect.height / 2;
    for (let x = rect.x + 1; x < rect.x + rect.width - 1; x += dash + gap) {
      ctx.fillRect(px(x, tilePx), px(y - 0.25, tilePx),
        px(dash, tilePx), px(0.5, tilePx));
    }
  } else {
    const x = rect.x + rect.width / 2;
    for (let y = rect.y + 1; y < rect.y + rect.height - 1; y += dash + gap) {
      ctx.fillRect(px(x - 0.25, tilePx), px(y, tilePx),
        px(0.5, tilePx), px(dash, tilePx));
    }
  }
}

function detailPaving(ctx: Ctx, rect: SceneRect, colour: string,
                      tilePx: number): void {
  ctx.strokeStyle = colour;
  ctx.lineWidth = Math.max(1, tilePx / 10);
  for (let x = rect.x + 2; x < rect.x + rect.width; x += 2) {
    ctx.beginPath();
    ctx.moveTo(px(x, tilePx), px(rect.y, tilePx));
    ctx.lineTo(px(x, tilePx), px(rect.y + rect.height, tilePx));
    ctx.stroke();
  }
  for (let y = rect.y + 2; y < rect.y + rect.height; y += 2) {
    ctx.beginPath();
    ctx.moveTo(px(rect.x, tilePx), px(y, tilePx));
    ctx.lineTo(px(rect.x + rect.width, tilePx), px(y, tilePx));
    ctx.stroke();
  }
}

function detailParking(ctx: Ctx, rect: SceneRect, tilePx: number): void {
  ctx.strokeStyle = "#f0f0e6";
  ctx.lineWidth = Math.max(1, tilePx / 8);
  for (let x = rect.x + 2; x < rect.x + rect.width; x += 3) {
    ctx.beginPath();
    ctx.moveTo(px(x, tilePx), px(rect.y + 1, tilePx));
    ctx.lineTo(px(x, tilePx), px(rect.y + rect.height - 1, tilePx));
    ctx.stroke();
  }
}

function detailTrees(ctx: Ctx, rect: SceneRect, palette: ScenePalette,
                     seed: number, tilePx: number): void {
  const count = Math.min(7, Math.max(2, Math.round(rect.width / 3)));
  for (let index = 0; index < count; index += 1) {
    const x = rect.x + 0.8 + hash01(seed + index * 2) * Math.max(0.1, rect.width - 1.6);
    const y = rect.y + 0.8 + hash01(seed + index * 2 + 1) * Math.max(0.1, rect.height - 1.6);
    ctx.fillStyle = "#6b4a2f";
    ctx.fillRect(px(x - 0.15, tilePx), px(y, tilePx),
      px(0.3, tilePx), px(0.8, tilePx));
    ctx.fillStyle = index % 2 ? palette.foliageAlt : palette.foliage;
    ctx.beginPath();
    ctx.arc(px(x, tilePx), px(y - 0.2, tilePx),
      px(0.9, tilePx), 0, Math.PI * 2);
    ctx.fill();
  }
}

function detailGarden(ctx: Ctx, rect: SceneRect, seed: number,
                      tilePx: number): void {
  fill(ctx, "#7a5d3c", rect.x, rect.y, rect.width, rect.height, tilePx);
  const colours = ["#e46b8a", "#f2c14e", "#d96fd0", "#f5f5f5"];
  const count = Math.min(28, Math.max(3, Math.round(rect.width * rect.height / 6)));
  for (let index = 0; index < count; index += 1) {
    const x = rect.x + 0.3 + hash01(seed + index * 2) * Math.max(0.1, rect.width - 0.6);
    const y = rect.y + 0.3 + hash01(seed + index * 2 + 1) * Math.max(0.1, rect.height - 0.6);
    ctx.fillStyle = colours[index % colours.length];
    ctx.fillRect(px(x, tilePx), px(y, tilePx),
      Math.max(1, px(0.22, tilePx)), Math.max(1, px(0.22, tilePx)));
  }
}

function detailFence(ctx: Ctx, rect: SceneRect, tilePx: number): void {
  ctx.fillStyle = "#4a4c55";
  if (rect.width >= rect.height) {
    for (let x = rect.x; x < rect.x + rect.width; x += 1) {
      ctx.fillRect(px(x, tilePx), px(rect.y, tilePx),
        Math.max(1, px(0.18, tilePx)), px(rect.height, tilePx));
    }
  } else {
    for (let y = rect.y; y < rect.y + rect.height; y += 1) {
      ctx.fillRect(px(rect.x, tilePx), px(y, tilePx),
        px(rect.width, tilePx), Math.max(1, px(0.18, tilePx)));
    }
  }
}

function detailFacade(ctx: Ctx, rect: SceneRect, seed: number,
                      tilePx: number): void {
  fill(ctx, "#9a5f4b", rect.x, rect.y, rect.width, rect.height, tilePx);
  ctx.fillStyle = "#7d4939";
  for (let y = rect.y; y < rect.y + rect.height; y += 1) {
    ctx.fillRect(px(rect.x, tilePx), px(y, tilePx),
      px(rect.width, tilePx), Math.max(1, px(0.16, tilePx)));
  }
  const step = 2.2;
  for (let y = rect.y + 1.2; y < rect.y + rect.height - 1; y += step) {
    for (let x = rect.x + 1; x < rect.x + rect.width - 1; x += step) {
      const lit = hash01(seed + Math.round(x * 17 + y * 31)) > 0.38;
      ctx.fillStyle = lit ? "#f6d98a" : "#3b4f66";
      ctx.fillRect(px(x, tilePx), px(y, tilePx),
        Math.max(1, px(1, tilePx)), Math.max(1, px(1, tilePx)));
    }
  }
}

function detailRoof(ctx: Ctx, rect: SceneRect, tilePx: number): void {
  fill(ctx, "#3d4358", rect.x, rect.y, rect.width, rect.height, tilePx);
  ctx.strokeStyle = "#555d78";
  ctx.lineWidth = Math.max(1, tilePx / 9);
  for (let y = rect.y; y < rect.y + rect.height; y += 1.5) {
    ctx.beginPath();
    ctx.moveTo(px(rect.x, tilePx), px(y, tilePx));
    ctx.lineTo(px(rect.x + rect.width, tilePx), px(y, tilePx));
    ctx.stroke();
  }
}

function detailEntrance(ctx: Ctx, rect: SceneRect, tilePx: number): void {
  fill(ctx, "#2f3644", rect.x, rect.y, rect.width, rect.height, tilePx);
  const doorW = Math.max(1, rect.width * 0.32);
  const doorX = rect.x + (rect.width - doorW) / 2;
  fill(ctx, "#8fd3e8", doorX, rect.y + 0.6, doorW,
    Math.max(0, rect.height - 0.9), tilePx);
  ctx.strokeStyle = "#e7f5fb";
  ctx.lineWidth = Math.max(1, tilePx / 8);
  ctx.strokeRect(px(doorX, tilePx), px(rect.y + 0.6, tilePx),
    px(doorW, tilePx), px(Math.max(0, rect.height - 0.9), tilePx));
}

function detailSignage(ctx: Ctx, rect: SceneRect, tilePx: number): void {
  fill(ctx, "#1f2430", rect.x, rect.y, rect.width, rect.height, tilePx);
  ctx.fillStyle = "#e08fb0";
  ctx.fillRect(px(rect.x + 0.8, tilePx), px(rect.y + 0.9, tilePx),
    px(Math.max(0.2, rect.width - 1.6), tilePx),
    Math.max(1, px(0.35, tilePx)));
}

function detailLobby(ctx: Ctx, rect: SceneRect, tilePx: number): void {
  fill(ctx, "#b9b3a4", rect.x, rect.y, rect.width, rect.height, tilePx);
  ctx.strokeStyle = "#8f8a7d";
  ctx.lineWidth = Math.max(1, tilePx / 12);
  for (let x = rect.x + 1; x < rect.x + rect.width; x += 1) {
    ctx.beginPath();
    ctx.moveTo(px(x, tilePx), px(rect.y, tilePx));
    ctx.lineTo(px(x, tilePx), px(rect.y + rect.height, tilePx));
    ctx.stroke();
  }
}

function detailElevator(ctx: Ctx, rect: SceneRect, tilePx: number): void {
  fill(ctx, "#4a4f63", rect.x, rect.y, rect.width, rect.height, tilePx);
  const gap = 0.3;
  const half = rect.width / 2;
  fill(ctx, "#8fa3bf", rect.x + gap, rect.y + gap,
    Math.max(0, half - gap * 2), Math.max(0, rect.height - gap * 2), tilePx);
  fill(ctx, "#8fa3bf", rect.x + half + gap, rect.y + gap,
    Math.max(0, half - gap * 2), Math.max(0, rect.height - gap * 2), tilePx);
}

function detailSecurity(ctx: Ctx, rect: SceneRect, tilePx: number): void {
  fill(ctx, "#8a8f9c", rect.x, rect.y, rect.width, rect.height, tilePx);
  fill(ctx, "#5d6270", rect.x + 1, rect.y + 1,
    Math.max(0, rect.width - 2), Math.max(0, rect.height - 2), tilePx);
}

function detailPath(ctx: Ctx, rect: SceneRect, seed: number,
                    tilePx: number): void {
  fill(ctx, "#c9b58c", rect.x, rect.y, rect.width, rect.height, tilePx);
  const count = Math.min(60, Math.max(3, Math.round(rect.width * rect.height / 8)));
  ctx.fillStyle = "#a8946d";
  for (let index = 0; index < count; index += 1) {
    const x = rect.x + hash01(seed + index * 2) * rect.width;
    const y = rect.y + hash01(seed + index * 2 + 1) * rect.height;
    ctx.fillRect(px(x, tilePx), px(y, tilePx),
      Math.max(1, px(0.2, tilePx)), Math.max(1, px(0.2, tilePx)));
  }
}

/** One composed building layer placement, derived only from payload chunks. */
export interface BuildingPlacement {
  role: "roof" | "wall" | "window" | "door" | "sign";
  chunk_id: string;
  /** Tile-space rectangle; the painter tiles/stretches the real frame in it. */
  x: number;
  y: number;
  width: number;
  height: number;
  variant: number;
}

const BUILDING_KINDS: ReadonlySet<string> = new Set([
  "facade", "roof", "windows", "entrance", "signage",
]);

/** True when a payload chunk kind belongs to the composed building layer. */
export function isBuildingChunkKind(kind: string): boolean {
  return BUILDING_KINDS.has(kind);
}

function buildingChunks(layout: OutsideSceneLayout): SceneChunk[] {
  return orderedSceneChunks(layout).filter(
    (chunk) => isBuildingChunkKind(chunk.kind)
      && chunk.rect.width > 0 && chunk.rect.height > 0);
}

/**
 * Plan the composed building from the payload's own building chunks.
 *
 * A `roof` chunk becomes a multi-tile roof band, a `facade` chunk a multi-tile
 * wall band plus a deterministic window grid, an `entrance` chunk a door, and
 * a `signage` chunk a sign. If the payload has no building chunk, the plan is
 * empty. Nothing is placed outside its source chunk's rectangle.
 */
export function planBuildingPlacements(
  layout: OutsideSceneLayout,
): BuildingPlacement[] {
  const plan: BuildingPlacement[] = [];
  const hasEntrance = layout.chunks.some(
    (chunk) => chunk.kind === "entrance"
      && chunk.rect.width > 0 && chunk.rect.height > 0);
  for (const chunk of buildingChunks(layout)) {
    const rect = chunk.rect;
    const seed = hashString(chunk.chunk_id);
    if (chunk.kind === "roof" || chunk.kind === "facade") {
      plan.push({
        role: chunk.kind === "roof" ? "roof" : "wall",
        chunk_id: chunk.chunk_id,
        x: rect.x, y: rect.y, width: rect.width, height: rect.height,
        variant: seed % 2,
      });
      if (chunk.kind === "facade") {
        // A real window grid inside the facade; no window is invented when
        // the pack has no window role (the painter falls back per role).
        const stepX = 4;
        const stepY = 6;
        let index = 0;
        for (let y = rect.y + 2; y <= rect.y + rect.height - 3; y += stepY) {
          for (let x = rect.x + 2; x <= rect.x + rect.width - 2; x += stepX) {
            plan.push({
              role: "window", chunk_id: chunk.chunk_id,
              x, y, width: 1, height: 1, variant: index % 2,
            });
            index += 1;
          }
        }
        if (!hasEntrance && rect.height >= 3) {
          const doorWidth = Math.min(2, rect.width);
          plan.push({
            role: "door", chunk_id: chunk.chunk_id,
            x: rect.x + (rect.width - doorWidth) / 2,
            y: rect.y + rect.height - 2,
            width: doorWidth, height: 2, variant: 0,
          });
        }
      }
      continue;
    }
    if (chunk.kind === "windows") {
      for (let y = rect.y; y < rect.y + rect.height; y += 2) {
        for (let x = rect.x; x < rect.x + rect.width; x += 2) {
          plan.push({
            role: "window", chunk_id: chunk.chunk_id,
            x, y, width: 1, height: 1,
            variant: (x + y) % 2,
          });
        }
      }
      continue;
    }
    if (chunk.kind === "entrance") {
      const width = Math.min(rect.width, 4);
      const height = Math.min(rect.height, 3);
      plan.push({
        role: "door", chunk_id: chunk.chunk_id,
        x: rect.x + (rect.width - width) / 2,
        y: rect.y + rect.height - height,
        width, height, variant: seed % 2,
      });
      continue;
    }
    if (chunk.kind === "signage") {
      const width = Math.min(rect.width, 4);
      const height = Math.min(rect.height, 2);
      plan.push({
        role: "sign", chunk_id: chunk.chunk_id,
        x: rect.x + (rect.width - width) / 2,
        y: rect.y + (rect.height - height) / 2,
        width, height, variant: seed % 2,
      });
    }
  }
  return plan;
}

function frameVariant(
  frames: OutsideArtFrame[] | undefined, variant: number,
): OutsideArtFrame | null {
  if (!frames || !frames.length) return null;
  const index = ((variant % frames.length) + frames.length) % frames.length;
  return frames[index];
}

function drawFrameAt(
  ctx: Ctx, frame: OutsideArtFrame, x: number, y: number,
  w: number, h: number, tilePx: number, timeMs: number,
): void {
  const count = frame.frames && frame.frames > 1 ? frame.frames : 1;
  const fps = frame.fps && frame.fps > 0 ? frame.fps : 6;
  const index = count > 1
    ? Math.floor((timeMs / 1000) * fps) % count : 0;
  ctx.drawImage(
    frame.image, frame.sx + index * frame.sw, frame.sy, frame.sw, frame.sh,
    px(x, tilePx), px(y, tilePx),
    Math.max(1, px(w, tilePx)), Math.max(1, px(h, tilePx)));
}

/** Tile one composition role across a payload rectangle. */
function drawTiledRole(
  ctx: Ctx, rect: SceneRect, role: OutsideCompositionRole,
  art: OutsideSceneArt, tilePx: number, seed: number, timeMs: number,
): boolean {
  const frames = art.frames?.[role];
  const first = frameVariant(frames, 0);
  if (!first || !frames) return false;
  const stepX = Math.max(1, Math.round(first.sw / first.tile));
  const stepY = Math.max(1, Math.round(first.sh / first.tile));
  ctx.save();
  ctx.beginPath();
  ctx.rect(px(rect.x, tilePx), px(rect.y, tilePx),
    px(rect.width, tilePx), px(rect.height, tilePx));
  ctx.clip();
  ctx.imageSmoothingEnabled = false;
  for (let y = rect.y; y < rect.y + rect.height; y += stepY) {
    for (let x = rect.x; x < rect.x + rect.width; x += stepX) {
      const pick = frameVariant(
        frames,
        Math.floor(hash01(seed + x * 31 + y * 17) * frames.length));
      if (!pick) continue;
      drawFrameAt(ctx, pick, x, y, pick.sw / pick.tile,
        pick.sh / pick.tile, tilePx, timeMs);
    }
  }
  ctx.restore();
  return true;
}

/** Scatter multi-tile decoration (trees, bushes, flowers, props) in a rect. */
function drawScatterRole(
  ctx: Ctx, rect: SceneRect, role: OutsideCompositionRole,
  art: OutsideSceneArt, tilePx: number, seed: number, count: number,
  timeMs: number,
): boolean {
  const frames = art.frames?.[role];
  if (!frames || !frames.length || count <= 0) return false;
  ctx.save();
  ctx.imageSmoothingEnabled = false;
  for (let index = 0; index < count; index += 1) {
    const frame = frameVariant(
      frames, Math.floor(hash01(seed + index * 13) * frames.length));
    if (!frame) continue;
    const w = frame.sw / frame.tile;
    const h = frame.sh / frame.tile;
    if (w > rect.width || h > rect.height) continue;
    const x = rect.x + hash01(seed + index * 7 + 1) * (rect.width - w);
    const y = rect.y + hash01(seed + index * 7 + 2) * (rect.height - h);
    drawFrameAt(ctx, frame, Math.round(x), Math.round(y), w, h,
      tilePx, timeMs);
  }
  ctx.restore();
  return true;
}

/** Real water with edge tiles chosen by neighbour position. */
function drawWaterRole(
  ctx: Ctx, rect: SceneRect, art: OutsideSceneArt, tilePx: number,
  seed: number, timeMs: number,
): boolean {
  const center = art.frames?.water;
  if (!center || !center.length) return false;
  ctx.save();
  ctx.beginPath();
  ctx.rect(px(rect.x, tilePx), px(rect.y, tilePx),
    px(rect.width, tilePx), px(rect.height, tilePx));
  ctx.clip();
  ctx.imageSmoothingEnabled = false;
  for (let y = rect.y; y < rect.y + rect.height; y += 1) {
    for (let x = rect.x; x < rect.x + rect.width; x += 1) {
      const role = waterRoleAt(rect, x, y);
      const frames = art.frames?.[role] ?? center;
      const frame = frameVariant(
        frames, Math.floor(hash01(seed + x * 29 + y * 23) * frames.length));
      if (!frame) continue;
      drawFrameAt(ctx, frame, x, y, frame.sw / frame.tile,
        frame.sh / frame.tile, tilePx, timeMs);
    }
  }
  ctx.restore();
  return true;
}

/** Draw the whole composed building once, from payload building chunks. */
function drawBuildingComposition(
  ctx: Ctx, layout: OutsideSceneLayout, art: OutsideSceneArt,
  tilePx: number, timeMs: number,
): boolean {
  const plan = planBuildingPlacements(layout);
  if (!plan.length || !art.frames) return false;
  let painted = false;

  // A whole-building frame (the real Serene Village house) is a multi-tile
  // facade with its own roof, windows and entrance. It is drawn once over the
  // union of the payload's roof/facade/windows chunks; no tile is repeated.
  const whole = frameVariant(art.frames.building, 0);
  if (whole) {
    const union = unionRects(buildingChunks(layout)
      .filter((chunk) => chunk.kind === "roof" || chunk.kind === "facade"
        || chunk.kind === "windows")
      .map((chunk) => chunk.rect));
    if (union) {
      ctx.save();
      ctx.beginPath();
      ctx.rect(px(union.x, tilePx), px(union.y, tilePx),
        px(union.width, tilePx), px(union.height, tilePx));
      ctx.clip();
      ctx.imageSmoothingEnabled = false;
      drawFrameAt(ctx, whole, union.x, union.y,
        union.width, union.height, tilePx, timeMs);
      ctx.restore();
      painted = true;
      // Entrance and signage are separate payload chunks, so they stay real
      // anchors rather than being folded into the house sprite.
      for (const item of plan) {
        if (item.role !== "door" && item.role !== "sign") continue;
        const frames = art.frames[item.role];
        const frame = frameVariant(frames, item.variant);
        if (!frame) continue;
        drawFrameAt(ctx, frame, item.x, item.y, item.width, item.height,
          tilePx, timeMs);
      }
      return true;
    }
  }

  for (const item of plan) {
    const frames = art.frames[item.role];
    if (!frames || !frames.length) continue;
    const seed = hashString(item.chunk_id);
    if (item.role === "roof" || item.role === "wall") {
      if (drawTiledRole(ctx, {
        x: item.x, y: item.y, width: item.width, height: item.height,
      }, item.role, art, tilePx, seed, timeMs)) painted = true;
      continue;
    }
    const frame = frameVariant(frames, item.variant);
    if (!frame) continue;
    drawFrameAt(ctx, frame, item.x, item.y, item.width, item.height,
      tilePx, timeMs);
    painted = true;
  }
  return painted;
}

function drawChunkArt(ctx: Ctx, chunk: SceneChunk, frame: OutsideArtFrame,
                      tilePx: number): void {
  const rect = chunk.rect;
  ctx.save();
  ctx.beginPath();
  ctx.rect(px(rect.x, tilePx), px(rect.y, tilePx),
    px(rect.width, tilePx), px(rect.height, tilePx));
  ctx.clip();
  ctx.imageSmoothingEnabled = false;
  const destTile = Math.max(1, px(frame.tile / 16, tilePx));
  for (let y = rect.y; y < rect.y + rect.height; y += frame.tile / 16) {
    for (let x = rect.x; x < rect.x + rect.width; x += frame.tile / 16) {
      ctx.drawImage(
        frame.image, frame.sx, frame.sy, frame.sw, frame.sh,
        px(x, tilePx), px(y, tilePx), destTile, destTile);
    }
  }
  ctx.restore();
}

function dormantWash(ctx: Ctx, chunk: SceneChunk, tilePx: number): void {
  if (chunk.layer === "ground" || chunk.active || chunk.animated) return;
  ctx.save();
  ctx.globalAlpha = 0.18;
  fill(ctx, "#5a6a86", chunk.rect.x, chunk.rect.y,
    chunk.rect.width, chunk.rect.height, tilePx);
  ctx.restore();
}

/**
 * Real-art composition for a payload chunk. Returns false when the pack has
 * no role for this chunk kind, so the caller can use the single-tile frame or
 * the procedural fallback. It never draws outside the chunk's own rectangle.
 */
function drawComposedChunk(
  ctx: Ctx, chunk: SceneChunk, palette: ScenePalette,
  art: OutsideSceneArt | null, tilePx: number, timeMs: number,
): boolean {
  if (!art?.composed || !art.frames) return false;
  const rect = chunk.rect;
  const seed = hashString(chunk.chunk_id);
  switch (chunk.kind) {
    case "ground":
      fill(ctx, palette.ground, rect.x, rect.y, rect.width, rect.height, tilePx);
      return drawTiledRole(ctx, rect, "grass", art, tilePx, seed, timeMs);
    case "road":
      fill(ctx, palette.ground, rect.x, rect.y, rect.width, rect.height, tilePx);
      return drawTiledRole(ctx, rect, "road", art, tilePx, seed, timeMs);
    case "sidewalk":
      fill(ctx, palette.ground, rect.x, rect.y, rect.width, rect.height, tilePx);
      return drawTiledRole(ctx, rect, "sidewalk", art, tilePx, seed, timeMs);
    case "parking":
      fill(ctx, palette.ground, rect.x, rect.y, rect.width, rect.height, tilePx);
      return drawTiledRole(ctx, rect, "road", art, tilePx, seed, timeMs)
        || drawTiledRole(ctx, rect, "sidewalk", art, tilePx, seed, timeMs);
    case "path":
      fill(ctx, palette.ground, rect.x, rect.y, rect.width, rect.height, tilePx);
      return drawTiledRole(ctx, rect, "path", art, tilePx, seed, timeMs);
    case "water":
      fill(ctx, palette.ground, rect.x, rect.y, rect.width, rect.height, tilePx);
      return drawWaterRole(ctx, rect, art, tilePx, seed, timeMs);
    case "trees": {
      if (!art.frames.trees && !art.frames.bush) return false;
      if (!drawTiledRole(ctx, rect, "grass", art, tilePx, seed, timeMs)) {
        fill(ctx, palette.ground, rect.x, rect.y, rect.width, rect.height, tilePx);
      }
      const count = Math.min(8, Math.max(1,
        Math.round((rect.width * rect.height) / 42)));
      const trees = drawScatterRole(
        ctx, rect, "trees", art, tilePx, seed, count, timeMs);
      const bushes = drawScatterRole(
        ctx, rect, "bush", art, tilePx, seed + 1, Math.min(4, count), timeMs);
      return trees || bushes;
    }
    case "garden": {
      if (!art.frames.flower && !art.frames.bush) return false;
      if (!drawTiledRole(ctx, rect, "grass", art, tilePx, seed, timeMs)) {
        fill(ctx, palette.ground, rect.x, rect.y, rect.width, rect.height, tilePx);
      }
      const count = Math.min(18, Math.max(2,
        Math.round((rect.width * rect.height) / 10)));
      const flowers = drawScatterRole(
        ctx, rect, "flower", art, tilePx, seed, count, timeMs);
      const bushes = drawScatterRole(
        ctx, rect, "bush", art, tilePx, seed + 2, Math.min(4, count), timeMs);
      return flowers || bushes;
    }
    case "fence":
      return drawTiledRole(ctx, rect, "fence", art, tilePx, seed, timeMs);
    case "prop": {
      const count = Math.min(6, Math.max(1,
        Math.round((rect.width * rect.height) / 18)));
      return drawScatterRole(
        ctx, rect, "prop", art, tilePx, seed, count, timeMs);
    }
    case "lobby":
    case "security":
      return drawTiledRole(ctx, rect, "sidewalk", art, tilePx, seed, timeMs);
    default:
      return false;
  }
}

function drawChunk(ctx: Ctx, chunk: SceneChunk, palette: ScenePalette,
                   art: OutsideSceneArt | null, tilePx: number,
                   timeMs: number): void {
  const rect = chunk.rect;
  if (rect.width <= 0 || rect.height <= 0) return;
  if (drawComposedChunk(ctx, chunk, palette, art, tilePx, timeMs)) {
    dormantWash(ctx, chunk, tilePx);
    return;
  }
  const frame = chunk.art ? art?.tiles[chunk.art] ?? null : null;
  if (frame) {
    drawChunkArt(ctx, chunk, frame, tilePx);
    dormantWash(ctx, chunk, tilePx);
    return;
  }
  const seed = hashString(chunk.chunk_id);
  switch (chunk.kind) {
    case "ground":
      fill(ctx, palette.ground, rect.x, rect.y, rect.width, rect.height, tilePx);
      detailGrass(ctx, rect, palette, seed, tilePx);
      break;
    case "road":
      fill(ctx, "#3d4048", rect.x, rect.y, rect.width, rect.height, tilePx);
      detailRoad(ctx, rect, tilePx);
      break;
    case "sidewalk":
      fill(ctx, "#b9b9b2", rect.x, rect.y, rect.width, rect.height, tilePx);
      detailPaving(ctx, rect, "#9a9a92", tilePx);
      break;
    case "parking":
      fill(ctx, "#454852", rect.x, rect.y, rect.width, rect.height, tilePx);
      detailParking(ctx, rect, tilePx);
      break;
    case "path":
      detailPath(ctx, rect, seed, tilePx);
      break;
    case "trees":
      fill(ctx, palette.ground, rect.x, rect.y, rect.width, rect.height, tilePx);
      detailTrees(ctx, rect, palette, seed, tilePx);
      break;
    case "garden":
      detailGarden(ctx, rect, seed, tilePx);
      break;
    case "fence":
      detailFence(ctx, rect, tilePx);
      break;
    case "prop":
      fill(ctx, "#7a5d3c", rect.x + 0.6, rect.y + 0.8,
        Math.max(0.2, rect.width - 1.2), Math.max(0.2, rect.height - 1.6),
        tilePx);
      fill(ctx, "#5b4329", rect.x + 0.8, rect.y + 1,
        Math.max(0.1, rect.width - 1.6), Math.max(0.1, rect.height - 2),
        tilePx);
      break;
    case "facade":
    case "windows":
      detailFacade(ctx, rect, seed, tilePx);
      break;
    case "roof":
      detailRoof(ctx, rect, tilePx);
      break;
    case "entrance":
      detailEntrance(ctx, rect, tilePx);
      break;
    case "signage":
      detailSignage(ctx, rect, tilePx);
      break;
    case "lobby":
      detailLobby(ctx, rect, tilePx);
      break;
    case "elevator_bank":
      detailElevator(ctx, rect, tilePx);
      break;
    case "security":
      detailSecurity(ctx, rect, tilePx);
      break;
    default: {
      // An unknown kind is still payload data: draw its footprint and name it
      // rather than inventing a feature it did not claim.
      fill(ctx, "#5a6072", rect.x, rect.y, rect.width, rect.height, tilePx);
      ctx.strokeStyle = "#9aa3b8";
      ctx.lineWidth = Math.max(1, tilePx / 10);
      ctx.strokeRect(px(rect.x, tilePx), px(rect.y, tilePx),
        px(rect.width, tilePx), px(rect.height, tilePx));
      ctx.fillStyle = "#e8edf7";
      ctx.font = `${Math.max(7, tilePx)}px monospace`;
      ctx.fillText(chunk.kind, px(rect.x + 0.3, tilePx),
        px(rect.y + 1, tilePx) + Math.max(7, tilePx));
    }
  }
  dormantWash(ctx, chunk, tilePx);
}

function drawWeather(ctx: Ctx, layout: OutsideSceneLayout, tilePx: number,
                     timeMs: number): void {
  const weather = layout.environment.weather;
  if (weather !== "rain" && weather !== "snow" && weather !== "fog") return;
  const width = px(layout.width, tilePx);
  const height = px(layout.height, tilePx);
  if (weather === "fog") {
    ctx.save();
    ctx.globalAlpha = 0.16;
    ctx.fillStyle = "#dfe8f0";
    ctx.fillRect(0, 0, width, height);
    for (let index = 0; index < 4; index += 1) {
      const y = height * (0.16 + index * 0.22);
      ctx.fillStyle = "#f4f8fb";
      ctx.fillRect(0, y, width, Math.max(2, px(1.2, tilePx)));
    }
    ctx.restore();
    return;
  }
  ctx.save();
  const seed = hashString(weather);
  const count = Math.round(layout.width * layout.height / 3);
  const offset = (timeMs / 14) % height;
  for (let index = 0; index < count; index += 1) {
    const x = hash01(seed + index * 3) * width;
    const y = (hash01(seed + index * 3 + 1) * height + offset) % height;
    if (weather === "rain") {
      ctx.strokeStyle = "rgba(205,225,245,0.42)";
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(x, y);
      ctx.lineTo(x - 2, y + px(1.8, tilePx));
      ctx.stroke();
    } else {
      ctx.fillStyle = "rgba(245,250,255,0.72)";
      ctx.fillRect(x, y, Math.max(1, px(0.3, tilePx)),
        Math.max(1, px(0.3, tilePx)));
    }
  }
  ctx.restore();
}

/** Paint one deterministic scene frame. */
export function drawOutsideScene(
  ctx: CanvasRenderingContext2D, layout: OutsideSceneLayout,
  options: OutsideDrawOptions = {},
): void {
  const tilePx = SCENE_TILE_PX;
  const timeMs = options.timeMs ?? 0;
  const width = px(layout.width, tilePx);
  const height = px(layout.height, tilePx);
  ctx.clearRect(0, 0, width, height);

  const sky = ctx.createLinearGradient(0, 0, 0, height);
  sky.addColorStop(0, layout.palette.skyTop);
  sky.addColorStop(1, layout.palette.skyBottom);
  ctx.fillStyle = sky;
  ctx.fillRect(0, 0, width, height);

  // The map itself. `bounds` may start at a non-zero tile; shift the whole
  // scene so the payload's own content starts at the canvas origin.
  ctx.save();
  ctx.translate(-px(layout.bounds.x, tilePx), -px(layout.bounds.y, tilePx));
  fill(ctx, layout.palette.ground, layout.bounds.x, layout.bounds.y,
    layout.width, layout.height, tilePx);

  const art = options.art ?? null;
  let buildingAttempted = false;
  for (const chunk of paintOrderedChunks(layout)) {
    if (isBuildingChunkKind(chunk.kind) && art?.composed) {
      if (!buildingAttempted) {
        buildingAttempted = true;
        if (drawBuildingComposition(ctx, layout, art, tilePx, timeMs)) {
          continue;
        }
      } else {
        continue;
      }
    }
    drawChunk(ctx, chunk, layout.palette, art, tilePx, timeMs);
  }

  // The selected zone outline is a real payload zone, not a decorative ring.
  const selected = layout.zones.find(
    (zone) => zone.zone_id === options.selectedZoneId);
  if (selected && selected.rect) {
    ctx.save();
    ctx.strokeStyle = "rgba(224,143,176,0.95)";
    ctx.lineWidth = 2;
    ctx.setLineDash([6, 4]);
    ctx.strokeRect(
      px(selected.rect.x, tilePx), px(selected.rect.y, tilePx),
      px(selected.rect.width, tilePx), px(selected.rect.height, tilePx));
    ctx.restore();
  }

  // Floor markers sit at the payload portal's source chunk.
  for (const floor of layout.floors) {
    const x = px(floor.x, tilePx);
    const y = px(floor.y, tilePx);
    ctx.save();
    ctx.fillStyle = "rgba(12,16,28,0.78)";
    ctx.fillRect(x - 12, y - 8, 24, 16);
    ctx.strokeStyle = floor.kind === "stairs" ? "#8fd3e8" : "#e08fb0";
    ctx.lineWidth = 1.5;
    ctx.strokeRect(x - 12, y - 8, 24, 16);
    ctx.fillStyle = "#eef4fb";
    ctx.font = "8px monospace";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(floor.kind === "stairs" ? "ST" : "EL", x, y);
    ctx.restore();
  }
  ctx.restore();

  drawWeather(ctx, layout, tilePx, timeMs);

  if (layout.palette.tintAlpha > 0) {
    ctx.save();
    ctx.globalAlpha = layout.palette.tintAlpha;
    ctx.fillStyle = layout.palette.tint;
    ctx.fillRect(0, 0, width, height);
    ctx.restore();
  }
}

/** One-line accessible description of what the canvas is showing. */
export function sceneDescription(layout: OutsideSceneLayout): string {
  const active = layout.zones.filter((zone) => zone.state === "active").length;
  return `Campus exterior scene: ${active} active of `
    + `${layout.zones.length} zones, ${layout.chunks.length} visible chunks, `
    + `${layout.floors.length} floor links. Weather `
    + `${layout.environment.weather || "unknown"}, `
    + `${layout.environment.day_night || "unknown"} time, `
    + `${layout.environment.season || "unknown"} season.`;
}

/** Floor link rows for the scene's accessible overlay (payload order). */
export function sceneFloorLinks(
  world: OutsideWorld, layout: OutsideSceneLayout,
): OutsideFloorLink[] {
  const allowed = new Set(layout.floors.map((floor) => floor.portal_id));
  return world.floor_links.filter((link) => allowed.has(link.portal_id));
}
