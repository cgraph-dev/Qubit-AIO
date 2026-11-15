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
 * `zone_id` names no zone is counted as unplaced and is never painted.
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
 * Everything in this module is deterministic: the same layout + options paint
 * the same instruction sequence, which is what the render-evidence test pins.
 */
import type {
  OutsideChunk, OutsideEnvironmentEvent, OutsideFloorLink, OutsideSummary,
  OutsideWorld, OutsideZone,
} from "./outside-world";

/** The art manifest a local theme pack may carry. */
export const OUTSIDE_SCENE_ART_SCHEMA = "qubit-office-outside-art/1";
/** CSS px per exterior tile. The map is tile-space; this is presentation. */
export const SCENE_TILE_PX = 8;
/** Draw order for chunk layers; later layers paint on top. */
export const SCENE_LAYER_ORDER = ["ground", "floor", "building", "overlay"] as const;

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
}

export interface OutsideSceneArt {
  credit: string;
  source: "theme" | "injected";
  tiles: Partial<Record<OutsideArtKind, OutsideArtFrame>>;
}

export interface OutsideArtTileSpec {
  sheet: string;
  col: number;
  row: number;
}

export interface OutsideSceneArtManifest {
  schema: string;
  credit: string;
  cell: number;
  tiles: Partial<Record<OutsideArtKind, OutsideArtTileSpec>>;
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

/** Validate an outside-art manifest. Returns null instead of inventing art. */
export function parseOutsideSceneArtManifest(
  value: unknown,
): OutsideSceneArtManifest | null {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    return null;
  }
  const row = value as Record<string, unknown>;
  if (text(row.schema) !== OUTSIDE_SCENE_ART_SCHEMA) return null;
  const cell = int(row.cell);
  if (cell <= 0 || cell > 128) return null;
  const source = row.tiles;
  if (source === null || typeof source !== "object" || Array.isArray(source)) {
    return null;
  }
  const tiles: Partial<Record<OutsideArtKind, OutsideArtTileSpec>> = {};
  for (const [kind, entry] of Object.entries(source as Record<string, unknown>)) {
    if (!ART_KINDS.has(kind)) continue;
    if (entry === null || typeof entry !== "object" || Array.isArray(entry)) {
      continue;
    }
    const spec = entry as Record<string, unknown>;
    const sheet = safeSheet(spec.sheet);
    const col = int(spec.col);
    const tileRow = int(spec.row);
    if (!sheet || col < 0 || tileRow < 0) continue;
    tiles[kind as OutsideArtKind] = { sheet, col, row: tileRow };
  }
  if (!Object.keys(tiles).length) return null;
  return {
    schema: OUTSIDE_SCENE_ART_SCHEMA,
    credit: text(row.credit, "LimeZu — https://limezu.itch.io"),
    cell,
    tiles,
  };
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
  const sheets = new Map<string, { image: CanvasImageSource; width: number; height: number }>();
  try {
    for (const spec of Object.values(manifest.tiles)) {
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
    const sx = spec.col * manifest.cell;
    const sy = spec.row * manifest.cell;
    if (sx + manifest.cell > sheet.width || sy + manifest.cell > sheet.height) {
      continue;
    }
    tiles[kind as OutsideArtKind] = {
      image: sheet.image,
      sx,
      sy,
      sw: manifest.cell,
      sh: manifest.cell,
      tile: manifest.cell,
    };
  }
  if (!Object.keys(tiles).length) {
    return { status: "error", reason: "outside art has no usable tiles" };
  }
  return {
    status: "ready",
    art: { credit: manifest.credit, source, tiles },
  };
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

function drawChunk(ctx: Ctx, chunk: SceneChunk, palette: ScenePalette,
                   art: OutsideSceneArt | null, tilePx: number): void {
  const rect = chunk.rect;
  if (rect.width <= 0 || rect.height <= 0) return;
  const frame = chunk.art ? art?.tiles[chunk.art] ?? null : null;
  if (frame) {
    drawChunkArt(ctx, chunk, frame, tilePx);
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
  // A dormant zone never animates; the painter has no clock either, so this
  // only marks the state visually with a cool, low-contrast wash.
  if (chunk.layer !== "ground" && !chunk.active && !chunk.animated) {
    ctx.save();
    ctx.globalAlpha = 0.18;
    fill(ctx, "#5a6a86", rect.x, rect.y, rect.width, rect.height, tilePx);
    ctx.restore();
  }
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

  for (const chunk of orderedSceneChunks(layout)) {
    drawChunk(ctx, chunk, layout.palette, options.art ?? null, tilePx);
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
