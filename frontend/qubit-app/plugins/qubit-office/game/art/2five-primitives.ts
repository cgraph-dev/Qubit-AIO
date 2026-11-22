/**
 * Qubit Office — Stage 6 Canvas 2D drawing primitives.
 *
 * This module consumes the Stage 5 `2five-rooms.ts` schema. It does not create a
 * second room renderer, React tree, DOM labels, or simulation. The existing
 * renderer owns camera, room placement, agents, hit-testing and movement; it
 * calls these primitives from its floor/wall/fitting passes.
 *
 * Projection contract (fixed by the original brief):
 *   TILE = 16, HALF_W = 16, HALF_H = 8
 *   screenX = (gx - gy) * HALF_W
 *   screenY = (gx + gy) * HALF_H - z * TILE
 *   depth  = gx + gy
 *
 * All fitting proportions below are deliberately derived from Stage 5 tile
 * footprints and are marked as art proportions, not backend geometry. Every
 * colour is resolved from a Stage 1 --office-* token supplied by the caller;
 * the current `game/styles/tokens.css` is the shell palette, not that Stage 1
 * set. No literal colour values, raster assets, or text are introduced here.
 *
 * PORT PROVENANCE: ported verbatim from the canonical ChatGPT Stage 6
 * `drawing-primitives.ts` in the original local drawing prototype; the Perplexity alternate
 * primitives file was not merged. See `2five-adapter.ts` for the documented
 * seam into the native renderer and the exact reason it is not mounted live.
 */

import type {
  FittingType,
  FloorMaterial,
  PaletteId,
  RoomFitting,
  RoomRecipe,
  SurfaceRegion,
  WallMaterial,
} from "./2five-rooms";

export const TILE = 16;
export const HALF_W = 16;
export const HALF_H = 8;
export const WALL_HEIGHT_TILES = 2;

export interface GridPoint {
  readonly gx: number;
  readonly gy: number;
  readonly z?: number;
}

export interface ScreenPoint {
  readonly x: number;
  readonly y: number;
}

export interface RoomOrigin {
  readonly gx: number;
  readonly gy: number;
}

export interface CameraTransform {
  readonly offsetX: number;
  readonly offsetY: number;
  readonly zoom: number;
}

export interface GridTransform {
  readonly gx: number;
  readonly gy: number;
  readonly w: number;
  readonly h: number;
  readonly rotation: 0 | 90 | 180 | 270;
}

export interface ColourRamp {
  readonly base: string;
  readonly shadow: string;
  readonly deep: string;
  readonly highlight: string;
  readonly accent: string;
  readonly outline: string;
  readonly glow: string;
}

/** Resolved values from tokens.css; aliases are names, not new design tokens. */
export interface OfficeTokens {
  readonly void: string;
  readonly ground: string;
  readonly panel: string;
  readonly panelRaised: string;
  readonly header: string;
  readonly paper: string;
  readonly wall: string;
  readonly text: string;
  readonly muted: string;
  readonly dim: string;
  readonly onLight: string;
  readonly accent: string;
  readonly accentHot: string;
  readonly accentViolet: string;
  readonly accentVioletLight: string;
  readonly gold: string;
  readonly goldBright: string;
  readonly goldShadow: string;
  readonly ok: string;
  readonly warn: string;
  readonly bad: string;
  readonly border: string;
  readonly borderMuted: string;
  readonly borderAccent: string;
  readonly borderGold: string;
  readonly borderHighlight: string;
}

function readToken(style: CSSStyleDeclaration, name: string): string {
  const value = style.getPropertyValue(name).trim();
  if (!value) throw new Error(`Missing required Stage 1 token: ${name}`);
  return value;
}

/** Resolve only tokens declared by the Stage 1 tokens.css :root block. */
export function resolveOfficeTokens(
  source: Element = document.documentElement,
): OfficeTokens {
  const style = getComputedStyle(source);
  return {
    void: readToken(style, "--office-surface-void"),
    ground: readToken(style, "--office-surface-ground"),
    panel: readToken(style, "--office-surface-panel"),
    panelRaised: readToken(style, "--office-surface-panel-raised"),
    header: readToken(style, "--office-surface-header"),
    paper: readToken(style, "--office-surface-paper"),
    wall: readToken(style, "--office-surface-wall"),
    text: readToken(style, "--office-ink-text"),
    muted: readToken(style, "--office-ink-muted"),
    dim: readToken(style, "--office-ink-dim"),
    onLight: readToken(style, "--office-ink-on-light"),
    accent: readToken(style, "--office-accent"),
    accentHot: readToken(style, "--office-accent-hot"),
    accentViolet: readToken(style, "--office-accent-violet"),
    accentVioletLight: readToken(style, "--office-accent-violet-light"),
    gold: readToken(style, "--office-gold"),
    goldBright: readToken(style, "--office-gold-bright"),
    goldShadow: readToken(style, "--office-gold-shadow"),
    ok: readToken(style, "--office-state-ok"),
    warn: readToken(style, "--office-state-warn"),
    bad: readToken(style, "--office-state-bad"),
    border: readToken(style, "--office-border"),
    borderMuted: readToken(style, "--office-border-muted"),
    borderAccent: readToken(style, "--office-border-accent"),
    borderGold: readToken(style, "--office-border-gold"),
    borderHighlight: readToken(style, "--office-border-highlight"),
  };
}

function hexChannels(colour: string): [number, number, number] {
  const value = colour.trim();
  const short = /^#([\da-f])([\da-f])([\da-f])$/i.exec(value);
  const full = /^#([\da-f]{2})([\da-f]{2})([\da-f]{2})$/i.exec(value);
  if (short) {
    return [
      parseInt(short[1] + short[1], 16),
      parseInt(short[2] + short[2], 16),
      parseInt(short[3] + short[3], 16),
    ];
  }
  if (full) {
    return [parseInt(full[1], 16), parseInt(full[2], 16), parseInt(full[3], 16)];
  }
  throw new Error(`Canvas palette expected a hex token value, received: ${value}`);
}

function toHex(value: number): string {
  return Math.max(0, Math.min(255, Math.round(value)))
    .toString(16)
    .padStart(2, "0");
}

export function mixColour(a: string, b: string, amount: number): string {
  const t = Math.max(0, Math.min(1, amount));
  const ca = hexChannels(a);
  const cb = hexChannels(b);
  return `#${toHex(ca[0] + (cb[0] - ca[0]) * t)}${toHex(ca[1] + (cb[1] - ca[1]) * t)}${toHex(ca[2] + (cb[2] - ca[2]) * t)}`;
}

export function withAlpha(colour: string, alpha: number): string {
  const [r, g, b] = hexChannels(colour);
  const a = Math.max(0, Math.min(1, alpha));
  return `rgba(${r}, ${g}, ${b}, ${a})`;
}

function ramp(
  base: string,
  dark: string,
  light: string,
  accent: string,
  outline: string,
): ColourRamp {
  return {
    base,
    shadow: mixColour(base, dark, 0.36),
    deep: mixColour(dark, base, 0.08),
    highlight: mixColour(base, light, 0.58),
    accent,
    outline,
    glow: accent,
  };
}

/**
 * Stage 5 semantic palettes mapped onto Stage 1's available token families.
 * The previous screenshot's exact object pigments are not encoded in rooms.ts;
 * therefore these are explicitly presentation mappings, not sampled materials.
 * They use no colours outside Stage 1.
 */
export function paletteRamp(palette: PaletteId, t: OfficeTokens): ColourRamp {
  switch (palette) {
    case "cool_teal_neutral":
      return ramp(t.panelRaised, t.void, t.paper, t.accentVioletLight, t.borderMuted);
    case "executive_warm":
      return ramp(t.goldShadow, t.header, t.goldBright, t.gold, t.borderGold);
    case "finance_navy":
      return ramp(t.accentViolet, t.void, t.accentVioletLight, t.gold, t.borderMuted);
    case "creative_coral":
      return ramp(t.accent, t.header, t.goldBright, t.accentHot, t.borderAccent);
    case "control_room_amber":
      return ramp(t.gold, t.goldShadow, t.goldBright, t.accentHot, t.borderHighlight);
    case "open_office_sand":
      return ramp(t.paper, t.goldShadow, t.goldBright, t.gold, t.goldShadow);
    default: {
      const exhaustive: never = palette;
      throw new Error(`Unsupported Stage 5 palette: ${String(exhaustive)}`);
    }
  }
}

export function floorMaterialRamp(material: FloorMaterial, t: OfficeTokens): ColourRamp {
  switch (material) {
    case "floor_wood_light":
      return ramp(mixColour(t.paper, t.goldShadow, 0.22), t.goldShadow, t.goldBright, t.gold, t.borderGold);
    case "floor_wood_dark":
      return ramp(mixColour(t.goldShadow, t.header, 0.38), t.void, t.gold, t.goldShadow, t.borderGold);
    case "floor_carpet_dark":
      return ramp(mixColour(t.header, t.accentViolet, 0.28), t.void, t.accent, t.gold, t.borderMuted);
    case "floor_tile_grey":
      return ramp(t.ground, t.void, t.panelRaised, t.accentVioletLight, t.borderMuted);
    case "floor_concrete_polished":
      return ramp(mixColour(t.ground, t.paper, 0.16), t.void, t.paper, t.accentVioletLight, t.borderMuted);
    case "floor_marble_warm":
      return ramp(mixColour(t.paper, t.gold, 0.12), t.goldShadow, t.goldBright, t.gold, t.borderGold);
    default: {
      const exhaustive: never = material;
      throw new Error(`Unsupported Stage 5 floor material: ${String(exhaustive)}`);
    }
  }
}

export function wallMaterialRamp(material: WallMaterial, t: OfficeTokens): ColourRamp {
  switch (material) {
    case "wall_panel_light":
      return ramp(t.panelRaised, t.panel, t.paper, t.gold, t.borderGold);
    case "wall_plaster_cool":
      return ramp(t.wall, t.void, t.panelRaised, t.accentVioletLight, t.borderMuted);
    case "wall_glass_partition":
      return ramp(t.accentViolet, t.void, t.paper, t.accent, t.borderAccent);
    case "wall_tech_panel":
      return ramp(t.panel, t.void, t.accentVioletLight, t.accent, t.borderMuted);
    case "wall_brick_painted":
      return ramp(t.header, t.void, t.accentViolet, t.goldShadow, t.borderGold);
    case "wall_panel_walnut":
      return ramp(t.goldShadow, t.void, t.gold, t.goldBright, t.borderGold);
    default: {
      const exhaustive: never = material;
      throw new Error(`Unsupported Stage 5 wall material: ${String(exhaustive)}`);
    }
  }
}

export function projectGrid(
  point: GridPoint,
  origin: RoomOrigin,
  camera: CameraTransform,
): ScreenPoint {
  const gx = origin.gx + point.gx;
  const gy = origin.gy + point.gy;
  const z = point.z ?? 0;
  return {
    x: ((gx - gy) * HALF_W) * camera.zoom + camera.offsetX,
    y: ((gx + gy) * HALF_H - z * TILE) * camera.zoom + camera.offsetY,
  };
}

/** Rotate local art coordinates around the fitting centre before projection. */
function rotateLocal(
  x: number,
  y: number,
  transform: GridTransform,
): { x: number; y: number } {
  const cx = transform.w / 2;
  const cy = transform.h / 2;
  const dx = x - cx;
  const dy = y - cy;
  switch (transform.rotation) {
    case 0: return { x: x + transform.gx, y: y + transform.gy };
    case 90: return { x: cx - dy + transform.gx, y: cy + dx + transform.gy };
    case 180: return { x: cx - dx + transform.gx, y: cy - dy + transform.gy };
    case 270: return { x: cx + dy + transform.gx, y: cy - dx + transform.gy };
  }
}

function projectLocal(
  transform: GridTransform,
  x: number,
  y: number,
  z: number,
  origin: RoomOrigin,
  camera: CameraTransform,
): ScreenPoint {
  const rotated = rotateLocal(x, y, transform);
  return projectGrid({ gx: rotated.x, gy: rotated.y, z }, origin, camera);
}

function projectRectCorners(
  transform: GridTransform,
  x: number,
  y: number,
  w: number,
  h: number,
  z: number,
  origin: RoomOrigin,
  camera: CameraTransform,
): ScreenPoint[] {
  return [
    projectLocal(transform, x, y, z, origin, camera),
    projectLocal(transform, x + w, y, z, origin, camera),
    projectLocal(transform, x + w, y + h, z, origin, camera),
    projectLocal(transform, x, y + h, z, origin, camera),
  ];
}

function tracePolygon(ctx: CanvasRenderingContext2D, points: readonly ScreenPoint[]): void {
  if (points.length === 0) return;
  ctx.beginPath();
  ctx.moveTo(points[0].x, points[0].y);
  for (let i = 1; i < points.length; i += 1) ctx.lineTo(points[i].x, points[i].y);
  ctx.closePath();
}

function fillPolygon(
  ctx: CanvasRenderingContext2D,
  points: readonly ScreenPoint[],
  colour: string,
  alpha = 1,
): void {
  ctx.save();
  ctx.globalAlpha *= alpha;
  tracePolygon(ctx, points);
  ctx.fillStyle = colour;
  ctx.fill();
  ctx.restore();
}

function strokePolygon(
  ctx: CanvasRenderingContext2D,
  points: readonly ScreenPoint[],
  colour: string,
  width: number,
  alpha = 1,
): void {
  ctx.save();
  ctx.globalAlpha *= alpha;
  tracePolygon(ctx, points);
  ctx.strokeStyle = colour;
  ctx.lineWidth = width;
  ctx.lineJoin = "miter";
  ctx.stroke();
  ctx.restore();
}

function strokeLine(
  ctx: CanvasRenderingContext2D,
  a: ScreenPoint,
  b: ScreenPoint,
  colour: string,
  width: number,
  alpha = 1,
): void {
  ctx.save();
  ctx.globalAlpha *= alpha;
  ctx.beginPath();
  ctx.moveTo(a.x, a.y);
  ctx.lineTo(b.x, b.y);
  ctx.strokeStyle = colour;
  ctx.lineWidth = width;
  ctx.lineCap = "square";
  ctx.stroke();
  ctx.restore();
}

function projectedEllipse(
  ctx: CanvasRenderingContext2D,
  centre: ScreenPoint,
  rx: number,
  ry: number,
  fill: string,
  alpha = 1,
): void {
  ctx.save();
  ctx.globalAlpha *= alpha;
  ctx.beginPath();
  ctx.ellipse(centre.x, centre.y, Math.max(0.5, rx), Math.max(0.5, ry), 0, 0, Math.PI * 2);
  ctx.fillStyle = fill;
  ctx.fill();
  ctx.restore();
}

function scaleLine(camera: CameraTransform, width: number): number {
  return Math.max(0.75, width * camera.zoom);
}

/** Stage 6 primitive: draw the fitting's ground diamond footprint. */
export function drawDiamondFootprint(
  ctx: CanvasRenderingContext2D,
  transform: GridTransform,
  x: number,
  y: number,
  w: number,
  h: number,
  origin: RoomOrigin,
  camera: CameraTransform,
  tokens: OfficeTokens,
  alpha = 0.24,
): ScreenPoint[] {
  const points = projectRectCorners(transform, x, y, w, h, 0, origin, camera);
  fillPolygon(ctx, points, tokens.borderMuted, alpha);
  strokePolygon(ctx, points, tokens.borderGold, scaleLine(camera, 0.75), alpha * 0.8);
  return points;
}

/** Stage 6 primitive: soft projected contact shadow under a fitting. */
export function drawBaseShadow(
  ctx: CanvasRenderingContext2D,
  transform: GridTransform,
  x: number,
  y: number,
  w: number,
  h: number,
  origin: RoomOrigin,
  camera: CameraTransform,
  tokens: OfficeTokens,
): void {
  const points = projectRectCorners(transform, x, y, w, h, 0, origin, camera);
  const cx = points.reduce((sum, point) => sum + point.x, 0) / points.length;
  const cy = points.reduce((sum, point) => sum + point.y, 0) / points.length;
  projectedEllipse(
    ctx,
    { x: cx + TILE * 0.08 * camera.zoom, y: cy + TILE * 0.10 * camera.zoom },
    Math.max(2, (w + h) * TILE * 0.20 * camera.zoom),
    Math.max(1, (w + h) * TILE * 0.055 * camera.zoom),
    tokens.void,
    0.42,
  );
}

export interface PrismOptions {
  readonly shadow?: boolean;
  readonly outline?: boolean;
  readonly glow?: boolean;
  readonly alpha?: number;
  readonly topInset?: number;
  readonly lineWidth?: number;
}

export interface PrismGeometry {
  readonly bottom: readonly ScreenPoint[];
  readonly top: readonly ScreenPoint[];
  readonly westFace: readonly ScreenPoint[];
  readonly eastFace: readonly ScreenPoint[];
}

/**
 * Shared box primitive. Order is footprint → contact shadow → two visible body
 * faces → top face → highlights → outline → optional glow. The near faces are
 * the south-west and south-east faces of the 2:1 isometric box.
 */
export function drawIsoPrism(
  ctx: CanvasRenderingContext2D,
  transform: GridTransform,
  x: number,
  y: number,
  w: number,
  h: number,
  baseZ: number,
  heightTiles: number,
  colours: ColourRamp,
  origin: RoomOrigin,
  camera: CameraTransform,
  tokens: OfficeTokens,
  options: PrismOptions = {},
): PrismGeometry {
  const alpha = options.alpha ?? 1;
  const bottom = projectRectCorners(transform, x, y, w, h, baseZ, origin, camera);
  const top = projectRectCorners(transform, x, y, w, h, baseZ + heightTiles, origin, camera);
  const footprint = projectRectCorners(transform, x, y, w, h, baseZ, origin, camera);

  fillPolygon(ctx, footprint, colours.accent, 0.11 * alpha);
  if (options.shadow !== false) {
    drawBaseShadow(ctx, transform, x, y, w, h, origin, camera, tokens);
  }

  const westFace = [bottom[3], bottom[2], top[2], top[3]];
  const eastFace = [bottom[2], bottom[1], top[1], top[2]];
  fillPolygon(ctx, westFace, colours.shadow, alpha);
  fillPolygon(ctx, eastFace, colours.deep, alpha);
  fillPolygon(ctx, top, colours.base, alpha);

  // Two restrained top-face highlights, aligned to the isometric edges.
  strokeLine(ctx, top[0], top[1], colours.highlight, scaleLine(camera, 0.9), alpha * 0.88);
  strokeLine(ctx, top[0], top[3], colours.highlight, scaleLine(camera, 0.65), alpha * 0.60);

  if (options.topInset && options.topInset > 0) {
    const inset = options.topInset;
    const inner = projectRectCorners(
      transform,
      x + inset,
      y + inset,
      Math.max(0.1, w - inset * 2),
      Math.max(0.1, h - inset * 2),
      baseZ + heightTiles,
      origin,
      camera,
    );
    strokePolygon(ctx, inner, colours.accent, scaleLine(camera, 0.65), alpha * 0.78);
  }

  if (options.outline !== false) {
    strokePolygon(ctx, top, colours.outline, scaleLine(camera, options.lineWidth ?? 0.85), alpha);
    strokeLine(ctx, bottom[3], top[3], colours.outline, scaleLine(camera, 0.8), alpha);
    strokeLine(ctx, bottom[1], top[1], colours.outline, scaleLine(camera, 0.8), alpha);
  }

  if (options.glow) {
    ctx.save();
    ctx.shadowColor = colours.glow;
    ctx.shadowBlur = 9 * camera.zoom;
    strokePolygon(ctx, top, colours.glow, scaleLine(camera, 0.8), alpha * 0.7);
    ctx.restore();
  }

  return { bottom, top, westFace, eastFace };
}

/** Paint the floor tile field for a Stage 5 room recipe. */
export function drawRoomFloor(
  ctx: CanvasRenderingContext2D,
  recipe: RoomRecipe,
  origin: RoomOrigin,
  camera: CameraTransform,
  tokens: OfficeTokens,
  showTileSeams = true,
): void {
  const colours = floorMaterialRamp(recipe.floorMaterial, tokens);
  for (let gy = 0; gy < recipe.height; gy += 1) {
    for (let gx = 0; gx < recipe.width; gx += 1) {
      const tileTransform: GridTransform = { gx, gy, w: 1, h: 1, rotation: 0 };
      const diamond = projectRectCorners(tileTransform, 0, 0, 1, 1, 0, origin, camera);
      const alternate = (gx + gy) % 4 === 0 ? 0.045 : 0;
      fillPolygon(ctx, diamond, mixColour(colours.base, colours.highlight, alternate), 1);
      if (showTileSeams) {
        strokeLine(ctx, diamond[0], diamond[1], colours.shadow, scaleLine(camera, 0.42), 0.28);
        strokeLine(ctx, diamond[0], diamond[3], colours.shadow, scaleLine(camera, 0.42), 0.28);
      }
    }
  }
  const roomOutline = projectRectCorners(
    { gx: 0, gy: 0, w: recipe.width, h: recipe.height, rotation: 0 },
    0, 0, recipe.width, recipe.height, 0, origin, camera,
  );
  strokePolygon(ctx, roomOutline, colours.outline, scaleLine(camera, 1), 0.8);
}

function drawSurfaceRegion(
  ctx: CanvasRenderingContext2D,
  region: SurfaceRegion,
  origin: RoomOrigin,
  camera: CameraTransform,
  tokens: OfficeTokens,
): void {
  const transform: GridTransform = {
    gx: region.gx, gy: region.gy, w: region.w, h: region.h, rotation: region.rotation,
  };
  const materialColours = floorMaterialRamp(region.material, tokens);
  const paletteColours = paletteRamp(region.palette, tokens);
  const colours = { ...materialColours, accent: paletteColours.accent };
  const points = projectRectCorners(transform, 0, 0, region.w, region.h, 0.025, origin, camera);
  const isRug = region.kind === "rug";
  const base = isRug
    ? mixColour(colours.base, paletteColours.base, 0.15)
    : mixColour(tokens.bad, tokens.header, 0.32);
  fillPolygon(ctx, points, base, 0.96);
  strokePolygon(ctx, points, isRug ? tokens.gold : tokens.goldBright, scaleLine(camera, 1.15), 0.96);

  const inset = isRug ? 0.16 : 0.10;
  const inner = projectRectCorners(
    transform,
    inset,
    inset,
    Math.max(0.1, region.w - inset * 2),
    Math.max(0.1, region.h - inset * 2),
    0.035,
    origin,
    camera,
  );
  strokePolygon(ctx, inner, isRug ? tokens.borderGold : tokens.gold, scaleLine(camera, 0.65), 0.78);

  // A small repeated border glint; no lettering or texture image is baked in.
  const edgeA = projectLocal(transform, inset, inset, 0.04, origin, camera);
  const edgeB = projectLocal(transform, region.w - inset, inset, 0.04, origin, camera);
  strokeLine(ctx, edgeA, edgeB, tokens.goldBright, scaleLine(camera, 0.5), 0.58);
}

/** Rug and red-carpet records are painted in this surface pass exactly once. */
export function drawRoomSurfaceRegions(
  ctx: CanvasRenderingContext2D,
  recipe: RoomRecipe,
  origin: RoomOrigin,
  camera: CameraTransform,
  tokens: OfficeTokens,
): void {
  for (const region of recipe.carpetRegions) {
    drawSurfaceRegion(ctx, region, origin, camera, tokens);
  }
  for (const region of recipe.rugRegions) {
    drawSurfaceRegion(ctx, region, origin, camera, tokens);
  }
}

export interface RoomWallOptions {
  readonly heightTiles?: number;
  /** Foreground cutaway applies only to the camera-facing south/east wall runs. */
  readonly foreground?: boolean;
  /** 0 hides the front wall; 1 draws it opaque. Default foreground value is 0.16. */
  readonly cutawayAlpha?: number;
}

function wallSegmentTransform(
  gx: number,
  gy: number,
  w: number,
  h: number,
): GridTransform {
  return { gx, gy, w, h, rotation: 0 };
}

function drawWallSegment(
  ctx: CanvasRenderingContext2D,
  transform: GridTransform,
  height: number,
  colours: ColourRamp,
  origin: RoomOrigin,
  camera: CameraTransform,
  tokens: OfficeTokens,
  alpha: number,
): void {
  drawIsoPrism(
    ctx, transform, 0, 0, transform.w, transform.h, 0, height,
    colours, origin, camera, tokens,
    { shadow: false, outline: true, alpha, lineWidth: 0.62 },
  );
}

/**
 * Room wall rule:
 * - default extrusion is two TILE-height units (32 world pixels);
 * - north and west/back walls remain opaque;
 * - foreground south and east walls are retained as faint cutaway guides,
 *   default alpha 0.16, so room contents and moving agents stay visible;
 * - wall collision and doorway logic remain owned by the existing world model.
 */
export function drawRoomWalls(
  ctx: CanvasRenderingContext2D,
  recipe: RoomRecipe,
  origin: RoomOrigin,
  camera: CameraTransform,
  tokens: OfficeTokens,
  options: RoomWallOptions = {},
): void {
  const height = options.heightTiles ?? WALL_HEIGHT_TILES;
  const cutawayAlpha = Math.max(0, Math.min(1, options.cutawayAlpha ?? 0.16));
  const colours = wallMaterialRamp(recipe.wallMaterial, tokens);
  const thickness = 0.28;

  const segments: Array<{ transform: GridTransform; alpha: number; depth: number }> = [
    { transform: wallSegmentTransform(0, 0, recipe.width, thickness), alpha: 1, depth: recipe.width / 2 },
    { transform: wallSegmentTransform(0, 0, thickness, recipe.height), alpha: 1, depth: recipe.height / 2 },
    {
      transform: wallSegmentTransform(0, recipe.height - thickness, recipe.width, thickness),
      alpha: options.foreground ? cutawayAlpha : 1,
      depth: recipe.width / 2 + recipe.height - thickness,
    },
    {
      transform: wallSegmentTransform(recipe.width - thickness, 0, thickness, recipe.height),
      alpha: options.foreground ? cutawayAlpha : 1,
      depth: recipe.width - thickness + recipe.height / 2,
    },
  ];
  segments.sort((a, b) => a.depth - b.depth);
  for (const segment of segments) {
    drawWallSegment(ctx, segment.transform, height, colours, origin, camera, tokens, segment.alpha);
  }
}

function centreOf(points: readonly ScreenPoint[]): ScreenPoint {
  return {
    x: points.reduce((sum, point) => sum + point.x, 0) / points.length,
    y: points.reduce((sum, point) => sum + point.y, 0) / points.length,
  };
}

function drawInsetLine(
  ctx: CanvasRenderingContext2D,
  transform: GridTransform,
  x1: number,
  y1: number,
  x2: number,
  y2: number,
  z: number,
  colour: string,
  camera: CameraTransform,
  origin: RoomOrigin,
  alpha = 1,
): void {
  strokeLine(
    ctx,
    projectLocal(transform, x1, y1, z, origin, camera),
    projectLocal(transform, x2, y2, z, origin, camera),
    colour,
    scaleLine(camera, 0.8),
    alpha,
  );
}

function drawLongTable(
  ctx: CanvasRenderingContext2D,
  fit: RoomFitting,
  origin: RoomOrigin,
  camera: CameraTransform,
  tokens: OfficeTokens,
  palette: ColourRamp,
): void {
  drawIsoPrism(ctx, fit, 0.08, 0.08, fit.w - 0.16, Math.max(0.55, fit.h - 0.16), fit.z, 0.34,
    palette, origin, camera, tokens, { topInset: 0.10, lineWidth: 0.9 });
  const topZ = fit.z + 0.34;
  drawInsetLine(ctx, fit, 0.28, fit.h * 0.50, fit.w - 0.28, fit.h * 0.50,
    topZ + 0.01, palette.accent, camera, origin, 0.52);
  // Fine end caps articulate the furniture without adding extra object slots.
  drawInsetLine(ctx, fit, 0.18, 0.18, 0.18, fit.h - 0.18,
    topZ + 0.01, palette.highlight, camera, origin, 0.48);
}

function drawBoardroomTable(
  ctx: CanvasRenderingContext2D,
  fit: RoomFitting,
  origin: RoomOrigin,
  camera: CameraTransform,
  tokens: OfficeTokens,
  palette: ColourRamp,
): void {
  drawIsoPrism(ctx, fit, 0.04, 0.05, fit.w - 0.08, fit.h - 0.10, fit.z, 0.42,
    palette, origin, camera, tokens, { topInset: 0.12, lineWidth: 1.0, glow: false });
  const centre = projectLocal(fit, fit.w / 2, fit.h / 2, fit.z + 0.44, origin, camera);
  projectedEllipse(ctx, centre, fit.w * TILE * 0.10 * camera.zoom,
    fit.h * TILE * 0.08 * camera.zoom, palette.accent, 0.34);
  drawInsetLine(ctx, fit, 0.4, fit.h / 2, fit.w - 0.4, fit.h / 2,
    fit.z + 0.445, palette.highlight, camera, origin, 0.74);
}

function drawDeskCluster(
  ctx: CanvasRenderingContext2D,
  fit: RoomFitting,
  origin: RoomOrigin,
  camera: CameraTransform,
  tokens: OfficeTokens,
  palette: ColourRamp,
): void {
  const columns = fit.w >= 3.5 ? 2 : 1;
  const rows = fit.h >= 2.5 ? 2 : 1;
  const gapX = 0.16;
  const gapY = 0.16;
  const cellW = (fit.w - gapX * (columns + 1)) / columns;
  const cellH = (fit.h - gapY * (rows + 1)) / rows;
  for (let row = 0; row < rows; row += 1) {
    for (let col = 0; col < columns; col += 1) {
      const x = gapX + col * (cellW + gapX);
      const y = gapY + row * (cellH + gapY);
      drawIsoPrism(ctx, fit, x, y, cellW, cellH, fit.z, 0.28,
        palette, origin, camera, tokens, { topInset: 0.07, lineWidth: 0.72 });
      drawInsetLine(ctx, fit, x + cellW * 0.18, y + cellH * 0.5,
        x + cellW * 0.82, y + cellH * 0.5, fit.z + 0.29,
        palette.highlight, camera, origin, 0.42);
    }
  }
}

function drawReceptionDesk(
  ctx: CanvasRenderingContext2D,
  fit: RoomFitting,
  origin: RoomOrigin,
  camera: CameraTransform,
  tokens: OfficeTokens,
  palette: ColourRamp,
): void {
  drawIsoPrism(ctx, fit, 0.04, 0.08, fit.w - 0.08, fit.h - 0.16, fit.z, 0.56,
    palette, origin, camera, tokens, { topInset: 0.08, lineWidth: 1.0, glow: true });
  drawIsoPrism(ctx, fit, 0.18, 0.10, fit.w - 0.36, 0.16, fit.z + 0.56, 0.12,
    ramp(palette.highlight, palette.shadow, tokens.goldBright, palette.accent, palette.outline),
    origin, camera, tokens, { shadow: false, lineWidth: 0.7 });
  drawInsetLine(ctx, fit, 0.28, fit.h - 0.20, fit.w - 0.28, fit.h - 0.20,
    fit.z + 0.57, palette.accent, camera, origin, 0.52);
}

function drawBarCounter(
  ctx: CanvasRenderingContext2D,
  fit: RoomFitting,
  origin: RoomOrigin,
  camera: CameraTransform,
  tokens: OfficeTokens,
  palette: ColourRamp,
): void {
  drawIsoPrism(ctx, fit, 0.04, 0.06, fit.w - 0.08, fit.h - 0.12, fit.z, 0.50,
    palette, origin, camera, tokens, { topInset: 0.08, lineWidth: 0.9 });
  drawIsoPrism(ctx, fit, 0.02, 0.04, fit.w - 0.04, 0.12, fit.z + 0.50, 0.10,
    ramp(palette.highlight, palette.shadow, tokens.goldBright, palette.accent, palette.outline),
    origin, camera, tokens, { shadow: false, lineWidth: 0.65 });
}

function drawShelf(
  ctx: CanvasRenderingContext2D,
  fit: RoomFitting,
  origin: RoomOrigin,
  camera: CameraTransform,
  tokens: OfficeTokens,
  palette: ColourRamp,
): void {
  drawIsoPrism(ctx, fit, 0.06, 0.14, fit.w - 0.12, fit.h - 0.22, fit.z, 0.74,
    palette, origin, camera, tokens, { topInset: 0.04, lineWidth: 0.75 });
  const shelfCount = 3;
  for (let i = 1; i <= shelfCount; i += 1) {
    const z = fit.z + (0.74 / (shelfCount + 1)) * i;
    const a = projectLocal(fit, 0.12, fit.h - 0.12, z, origin, camera);
    const b = projectLocal(fit, fit.w - 0.12, fit.h - 0.12, z, origin, camera);
    strokeLine(ctx, a, b, palette.highlight, scaleLine(camera, 1.0), 0.82);
  }
  // Book spines are abstract coloured bars, not copied covers or lettering.
  const count = Math.max(3, Math.floor(fit.w * 1.4));
  for (let i = 0; i < count; i += 1) {
    const x = 0.16 + (fit.w - 0.32) * (i / count);
    const top = projectLocal(fit, x, fit.h - 0.12, fit.z + 0.74 * 0.72, origin, camera);
    const base = projectLocal(fit, x, fit.h - 0.12, fit.z + 0.74 * 0.38, origin, camera);
    strokeLine(ctx, base, top, i % 2 === 0 ? palette.accent : palette.highlight,
      scaleLine(camera, 1.15), 0.74);
  }
}

function drawSink(
  ctx: CanvasRenderingContext2D,
  fit: RoomFitting,
  origin: RoomOrigin,
  camera: CameraTransform,
  tokens: OfficeTokens,
  palette: ColourRamp,
): void {
  drawIsoPrism(ctx, fit, 0.06, 0.12, fit.w - 0.12, fit.h - 0.24, fit.z, 0.34,
    palette, origin, camera, tokens, { topInset: 0.08, lineWidth: 0.8 });
  const bowl = projectLocal(fit, fit.w / 2, fit.h / 2, fit.z + 0.35, origin, camera);
  projectedEllipse(ctx, bowl, fit.w * TILE * 0.28 * camera.zoom,
    fit.h * TILE * 0.17 * camera.zoom, tokens.panel, 0.94);
  projectedEllipse(ctx, bowl, fit.w * TILE * 0.19 * camera.zoom,
    fit.h * TILE * 0.10 * camera.zoom, tokens.accentVioletLight, 0.60);
  const tapA = projectLocal(fit, fit.w / 2, 0.18, fit.z + 0.43, origin, camera);
  const tapB = projectLocal(fit, fit.w / 2, 0.18, fit.z + 0.60, origin, camera);
  strokeLine(ctx, tapA, tapB, tokens.goldBright, scaleLine(camera, 0.9));
}

function drawToilet(
  ctx: CanvasRenderingContext2D,
  fit: RoomFitting,
  origin: RoomOrigin,
  camera: CameraTransform,
  tokens: OfficeTokens,
  palette: ColourRamp,
): void {
  drawIsoPrism(ctx, fit, 0.28, 0.10, 0.48, 0.30, fit.z, 0.46,
    palette, origin, camera, tokens, { shadow: true, lineWidth: 0.7 });
  const bowl = projectLocal(fit, 0.52, 0.63, fit.z + 0.12, origin, camera);
  projectedEllipse(ctx, bowl, TILE * 0.30 * camera.zoom, TILE * 0.18 * camera.zoom,
    tokens.paper, 1);
  projectedEllipse(ctx, bowl, TILE * 0.18 * camera.zoom, TILE * 0.10 * camera.zoom,
    tokens.panelRaised, 0.92);
  const tank = projectLocal(fit, 0.52, 0.18, fit.z + 0.47, origin, camera);
  projectedEllipse(ctx, tank, TILE * 0.24 * camera.zoom, TILE * 0.11 * camera.zoom,
    palette.highlight, 0.88);
}

function drawStall(
  ctx: CanvasRenderingContext2D,
  fit: RoomFitting,
  origin: RoomOrigin,
  camera: CameraTransform,
  tokens: OfficeTokens,
  palette: ColourRamp,
): void {
  const sideThickness = Math.min(0.20, fit.w * 0.12);
  drawIsoPrism(ctx, fit, 0, 0, sideThickness, fit.h, fit.z, 1.55,
    palette, origin, camera, tokens, { shadow: false, lineWidth: 0.7 });
  drawIsoPrism(ctx, fit, fit.w - sideThickness, 0, sideThickness, fit.h, fit.z, 1.55,
    palette, origin, camera, tokens, { shadow: false, lineWidth: 0.7 });
  drawIsoPrism(ctx, fit, sideThickness, 0, fit.w - sideThickness * 2, 0.16,
    fit.z, 1.55, palette, origin, camera, tokens, { shadow: false, lineWidth: 0.7 });
  const latch = projectLocal(fit, fit.w - sideThickness * 1.8, fit.h * 0.48,
    fit.z + 0.85, origin, camera);
  projectedEllipse(ctx, latch, 1.5 * camera.zoom, 1.5 * camera.zoom, tokens.goldBright, 0.9);
}

function drawSofa(
  ctx: CanvasRenderingContext2D,
  fit: RoomFitting,
  origin: RoomOrigin,
  camera: CameraTransform,
  tokens: OfficeTokens,
  palette: ColourRamp,
): void {
  drawIsoPrism(ctx, fit, 0.08, 0.24, fit.w - 0.16, fit.h - 0.30, fit.z, 0.28,
    palette, origin, camera, tokens, { topInset: 0.08, lineWidth: 0.8 });
  drawIsoPrism(ctx, fit, 0.10, 0.02, fit.w - 0.20, 0.28, fit.z + 0.18, 0.64,
    ramp(palette.shadow, palette.deep, palette.highlight, palette.accent, palette.outline),
    origin, camera, tokens, { shadow: false, lineWidth: 0.8 });
  drawIsoPrism(ctx, fit, 0.02, 0.18, 0.22, fit.h - 0.24, fit.z + 0.10, 0.38,
    palette, origin, camera, tokens, { shadow: false, lineWidth: 0.7 });
  drawIsoPrism(ctx, fit, fit.w - 0.24, 0.18, 0.22, fit.h - 0.24, fit.z + 0.10, 0.38,
    palette, origin, camera, tokens, { shadow: false, lineWidth: 0.7 });
  const cushionCount = Math.max(1, Math.floor(fit.w / 1.3));
  for (let i = 0; i < cushionCount; i += 1) {
    const x = 0.28 + (fit.w - 0.56) * ((i + 0.5) / cushionCount);
    const p = projectLocal(fit, x, fit.h * 0.56, fit.z + 0.30, origin, camera);
    projectedEllipse(ctx, p, TILE * 0.22 * camera.zoom, TILE * 0.11 * camera.zoom,
      palette.highlight, 0.60);
  }
}

function drawLantern(
  ctx: CanvasRenderingContext2D,
  fit: RoomFitting,
  origin: RoomOrigin,
  camera: CameraTransform,
  tokens: OfficeTokens,
  palette: ColourRamp,
): void {
  const centre = projectLocal(fit, fit.w / 2, fit.h / 2, fit.z + 0.55, origin, camera);
  const radius = Math.max(4, TILE * 1.1 * camera.zoom);
  const glow = ctx.createRadialGradient(centre.x, centre.y, 0, centre.x, centre.y, radius);
  glow.addColorStop(0, withAlpha(tokens.goldBright, 0.46));
  glow.addColorStop(0.45, withAlpha(tokens.gold, 0.22));
  glow.addColorStop(1, withAlpha(tokens.gold, 0));
  ctx.save();
  ctx.fillStyle = glow;
  ctx.beginPath();
  ctx.arc(centre.x, centre.y, radius, 0, Math.PI * 2);
  ctx.fill();
  ctx.restore();

  drawIsoPrism(ctx, fit, 0.22, 0.22, fit.w - 0.44, fit.h - 0.44, fit.z, 0.14,
    palette, origin, camera, tokens, { shadow: false, lineWidth: 0.65, glow: true });
  const top = projectLocal(fit, fit.w / 2, fit.h / 2, fit.z + 0.82, origin, camera);
  const bottom = projectLocal(fit, fit.w / 2, fit.h / 2, fit.z + 0.22, origin, camera);
  strokeLine(ctx, top, bottom, tokens.goldBright, scaleLine(camera, 1.0));
  projectedEllipse(ctx, centre, TILE * 0.18 * camera.zoom, TILE * 0.12 * camera.zoom,
    tokens.goldBright, 0.92);
}

function drawSakuraTree(
  ctx: CanvasRenderingContext2D,
  fit: RoomFitting,
  origin: RoomOrigin,
  camera: CameraTransform,
  tokens: OfficeTokens,
  palette: ColourRamp,
): void {
  const base = projectLocal(fit, fit.w / 2, fit.h * 0.74, fit.z, origin, camera);
  projectedEllipse(ctx, { x: base.x, y: base.y + 2 * camera.zoom },
    fit.w * TILE * 0.30 * camera.zoom, fit.h * TILE * 0.14 * camera.zoom,
    tokens.void, 0.30);

  const trunkBottom = projectLocal(fit, fit.w / 2, fit.h * 0.72, fit.z, origin, camera);
  const trunkTop = projectLocal(fit, fit.w / 2, fit.h * 0.56, fit.z + 1.55, origin, camera);
  strokeLine(ctx, trunkBottom, trunkTop, tokens.goldShadow, scaleLine(camera, 4.2));
  strokeLine(ctx, trunkBottom, trunkTop, tokens.gold, scaleLine(camera, 1.4), 0.9);

  const branchTargets = [
    projectLocal(fit, fit.w * 0.20, fit.h * 0.34, fit.z + 1.85, origin, camera),
    projectLocal(fit, fit.w * 0.80, fit.h * 0.34, fit.z + 1.90, origin, camera),
    projectLocal(fit, fit.w * 0.50, fit.h * 0.12, fit.z + 2.10, origin, camera),
  ];
  for (const target of branchTargets) {
    strokeLine(ctx, trunkTop, target, tokens.goldShadow, scaleLine(camera, 1.3), 0.94);
  }

  const clusters = [
    [0.20, 0.34], [0.42, 0.18], [0.62, 0.17], [0.80, 0.34],
    [0.31, 0.52], [0.68, 0.52], [0.50, 0.38],
  ] as const;
  clusters.forEach(([x, y], index) => {
    const point = projectLocal(fit, fit.w * x, fit.h * y, fit.z + 1.78 + (index % 3) * 0.08,
      origin, camera);
    const size = TILE * (index === 6 ? 0.32 : 0.24) * camera.zoom;
    projectedEllipse(ctx, point, size * 1.25, size * 0.82, tokens.accentViolet, 0.92);
    projectedEllipse(ctx, { x: point.x - size * 0.22, y: point.y - size * 0.18 },
      size * 0.72, size * 0.52, tokens.accent, 0.94);
    projectedEllipse(ctx, { x: point.x + size * 0.18, y: point.y + size * 0.12 },
      size * 0.40, size * 0.31, tokens.goldBright, 0.56);
  });
  // Small blossom points are token-coloured and deterministic, not animated assets.
  for (let i = 0; i < 5; i += 1) {
    const point = projectLocal(fit, fit.w * (0.18 + i * 0.15), fit.h * 0.26,
      fit.z + 2.05, origin, camera);
    projectedEllipse(ctx, point, 1.4 * camera.zoom, 1.1 * camera.zoom, palette.highlight, 0.82);
  }
}

function drawStaircase(
  ctx: CanvasRenderingContext2D,
  fit: RoomFitting,
  origin: RoomOrigin,
  camera: CameraTransform,
  tokens: OfficeTokens,
  palette: ColourRamp,
): void {
  const stepCount = Math.max(3, Math.round(Math.max(fit.w, fit.h) * 2));
  const alongX = fit.w >= fit.h;
  const run = (alongX ? fit.w : fit.h) / stepCount;
  for (let i = 0; i < stepCount; i += 1) {
    const x = alongX ? i * run : 0;
    const y = alongX ? 0 : i * run;
    const w = alongX ? run + 0.015 : fit.w;
    const h = alongX ? fit.h : run + 0.015;
    const height = 0.10 + (i + 1) * 0.055;
    drawIsoPrism(ctx, fit, x, y, w, h, fit.z, height,
      palette, origin, camera, tokens,
      { shadow: i === 0, lineWidth: 0.62, topInset: 0.035 });
    const frontA = projectLocal(fit, x, y + h, fit.z + height, origin, camera);
    const frontB = projectLocal(fit, x + w, y + h, fit.z + height, origin, camera);
    strokeLine(ctx, frontA, frontB, palette.highlight, scaleLine(camera, 0.62), 0.7);
  }
}

function drawSign(
  ctx: CanvasRenderingContext2D,
  fit: RoomFitting,
  origin: RoomOrigin,
  camera: CameraTransform,
  tokens: OfficeTokens,
  palette: ColourRamp,
): void {
  const bottomLeft = projectLocal(fit, 0, fit.h, fit.z + 0.10, origin, camera);
  const bottomRight = projectLocal(fit, fit.w, fit.h, fit.z + 0.10, origin, camera);
  const topRight = projectLocal(fit, fit.w, fit.h, fit.z + 0.88, origin, camera);
  const topLeft = projectLocal(fit, 0, fit.h, fit.z + 0.88, origin, camera);
  const plate = [bottomLeft, bottomRight, topRight, topLeft];
  fillPolygon(ctx, plate, tokens.panel, 0.98);
  strokePolygon(ctx, plate, palette.outline, scaleLine(camera, 1.0));
  const inner = [
    projectLocal(fit, 0.12, fit.h, fit.z + 0.20, origin, camera),
    projectLocal(fit, fit.w - 0.12, fit.h, fit.z + 0.20, origin, camera),
    projectLocal(fit, fit.w - 0.12, fit.h, fit.z + 0.78, origin, camera),
    projectLocal(fit, 0.12, fit.h, fit.z + 0.78, origin, camera),
  ];
  strokePolygon(ctx, inner, tokens.goldBright, scaleLine(camera, 0.62), 0.92);
  const postA = projectLocal(fit, fit.w / 2, fit.h, fit.z, origin, camera);
  const postB = projectLocal(fit, fit.w / 2, fit.h, fit.z + 0.10, origin, camera);
  strokeLine(ctx, postA, postB, tokens.goldShadow, scaleLine(camera, 1.3));
  // Sign faces remain deliberately text-free; existing DOM labels supply words.
}

function drawPlant(
  ctx: CanvasRenderingContext2D,
  fit: RoomFitting,
  origin: RoomOrigin,
  camera: CameraTransform,
  tokens: OfficeTokens,
  palette: ColourRamp,
): void {
  drawIsoPrism(ctx, fit, 0.18, 0.18, fit.w - 0.36, fit.h - 0.36, fit.z, 0.28,
    palette, origin, camera, tokens, { lineWidth: 0.65, shadow: true });
  const centre = projectLocal(fit, fit.w / 2, fit.h / 2, fit.z + 0.42, origin, camera);
  const leafCount = 7;
  for (let i = 0; i < leafCount; i += 1) {
    const angle = (Math.PI * 2 * i) / leafCount;
    const leaf = {
      x: centre.x + Math.cos(angle) * TILE * 0.21 * camera.zoom,
      y: centre.y - TILE * 0.16 * camera.zoom + Math.sin(angle) * TILE * 0.10 * camera.zoom,
    };
    projectedEllipse(ctx, leaf, TILE * 0.14 * camera.zoom, TILE * 0.22 * camera.zoom,
      i % 2 === 0 ? tokens.ok : palette.accent, 0.90);
  }
  projectedEllipse(ctx, centre, TILE * 0.12 * camera.zoom, TILE * 0.14 * camera.zoom,
    tokens.ok, 0.96);
}

function drawMonitor(
  ctx: CanvasRenderingContext2D,
  fit: RoomFitting,
  origin: RoomOrigin,
  camera: CameraTransform,
  tokens: OfficeTokens,
  palette: ColourRamp,
): void {
  // Base foot and neck are procedural. The active screen interior is never
  // painted: the existing GIF/image/video layer remains visible and untouched.
  drawIsoPrism(ctx, fit, 0.20, 0.70, 0.60, 0.18, fit.z, 0.08,
    palette, origin, camera, tokens, { shadow: false, lineWidth: 0.55 });
  const standTop = projectLocal(fit, 0.50, 0.79, fit.z + 0.34, origin, camera);
  const standBottom = projectLocal(fit, 0.50, 0.79, fit.z + 0.08, origin, camera);
  strokeLine(ctx, standBottom, standTop, palette.shadow, scaleLine(camera, 1.6));

  const screenBottomLeft = projectLocal(fit, 0.12, 0.82, fit.z + 0.34, origin, camera);
  const screenBottomRight = projectLocal(fit, 0.88, 0.82, fit.z + 0.34, origin, camera);
  const screenTopRight = projectLocal(fit, 0.88, 0.82, fit.z + 1.05, origin, camera);
  const screenTopLeft = projectLocal(fit, 0.12, 0.82, fit.z + 1.05, origin, camera);
  const bezel = [screenBottomLeft, screenBottomRight, screenTopRight, screenTopLeft];
  strokePolygon(ctx, bezel, palette.shadow, scaleLine(camera, 3.0), 0.98);
  strokePolygon(ctx, bezel, palette.outline, scaleLine(camera, 0.8), 0.96);
  const inner = [
    projectLocal(fit, 0.18, 0.82, fit.z + 0.42, origin, camera),
    projectLocal(fit, 0.82, 0.82, fit.z + 0.42, origin, camera),
    projectLocal(fit, 0.82, 0.82, fit.z + 0.97, origin, camera),
    projectLocal(fit, 0.18, 0.82, fit.z + 0.97, origin, camera),
  ];
  strokePolygon(ctx, inner, tokens.borderMuted, scaleLine(camera, 0.55), 0.72);
  const led = projectLocal(fit, 0.50, 0.82, fit.z + 0.38, origin, camera);
  projectedEllipse(ctx, led, 0.8 * camera.zoom, 0.8 * camera.zoom, tokens.ok, 0.85);
}

function drawChair(
  ctx: CanvasRenderingContext2D,
  fit: RoomFitting,
  origin: RoomOrigin,
  camera: CameraTransform,
  tokens: OfficeTokens,
  palette: ColourRamp,
): void {
  drawIsoPrism(ctx, fit, 0.25, 0.28, 0.50, 0.42, fit.z, 0.22,
    palette, origin, camera, tokens, { topInset: 0.06, lineWidth: 0.7 });
  // A narrow raised back, plus a central stem and two feet.
  drawIsoPrism(ctx, fit, 0.28, 0.04, 0.44, 0.18, fit.z + 0.18, 0.70,
    ramp(palette.shadow, palette.deep, palette.highlight, palette.accent, palette.outline),
    origin, camera, tokens, { shadow: false, lineWidth: 0.7 });
  const stemTop = projectLocal(fit, 0.50, 0.51, fit.z + 0.16, origin, camera);
  const stemBottom = projectLocal(fit, 0.50, 0.51, fit.z + 0.035, origin, camera);
  strokeLine(ctx, stemBottom, stemTop, tokens.goldShadow, scaleLine(camera, 1.0));
  for (const dx of [0.20, 0.80]) {
    const foot = projectLocal(fit, dx, 0.55, fit.z + 0.02, origin, camera);
    projectedEllipse(ctx, foot, 2.2 * camera.zoom, 1.3 * camera.zoom, palette.deep, 0.85);
  }
}

/**
 * Paint one Stage 5 fitting. `rug` and `redCarpet` are intentionally skipped:
 * their matching `rugRegions` / `carpetRegions` are drawn in the earlier
 * surface pass, avoiding the duplicate records present in the Stage 5 schema.
 */
export function drawFitting(
  ctx: CanvasRenderingContext2D,
  fitting: RoomFitting,
  origin: RoomOrigin,
  camera: CameraTransform,
  tokens: OfficeTokens,
): void {
  const palette = paletteRamp(fitting.palette, tokens);
  switch (fitting.type) {
    case "longTable":
      drawLongTable(ctx, fitting, origin, camera, tokens, palette);
      return;
    case "boardroomTable":
      drawBoardroomTable(ctx, fitting, origin, camera, tokens, palette);
      return;
    case "deskCluster":
      drawDeskCluster(ctx, fitting, origin, camera, tokens, palette);
      return;
    case "receptionDesk":
      drawReceptionDesk(ctx, fitting, origin, camera, tokens, palette);
      return;
    case "barCounter":
      drawBarCounter(ctx, fitting, origin, camera, tokens, palette);
      return;
    case "shelf":
      drawShelf(ctx, fitting, origin, camera, tokens, palette);
      return;
    case "sink":
      drawSink(ctx, fitting, origin, camera, tokens, palette);
      return;
    case "toilet":
      drawToilet(ctx, fitting, origin, camera, tokens, palette);
      return;
    case "stall":
      drawStall(ctx, fitting, origin, camera, tokens, palette);
      return;
    case "sofa":
      drawSofa(ctx, fitting, origin, camera, tokens, palette);
      return;
    case "rug":
    case "redCarpet":
      return;
    case "lantern":
      drawLantern(ctx, fitting, origin, camera, tokens, palette);
      return;
    case "sakuraTree":
      drawSakuraTree(ctx, fitting, origin, camera, tokens, palette);
      return;
    case "staircase":
      drawStaircase(ctx, fitting, origin, camera, tokens, palette);
      return;
    case "sign":
      drawSign(ctx, fitting, origin, camera, tokens, palette);
      return;
    case "plant":
      drawPlant(ctx, fitting, origin, camera, tokens, palette);
      return;
    case "monitor":
      drawMonitor(ctx, fitting, origin, camera, tokens, palette);
      return;
    case "chair":
      drawChair(ctx, fitting, origin, camera, tokens, palette);
      return;
    default: {
      const exhaustive: never = fitting.type;
      throw new Error(`Unsupported Stage 5 fitting type: ${String(exhaustive)}`);
    }
  }
}

/** Exact painter depth: the entity's anchor is a grid point, depth = gx + gy. */
export function depthOf(point: GridPoint): number {
  return point.gx + point.gy;
}

export interface DepthDrawable {
  readonly gx: number;
  readonly gy: number;
  readonly draw: () => void;
}

/** Stable ascending painter order; equal-depth entries retain their input order. */
export function drawDepthSorted(items: readonly DepthDrawable[]): void {
  items
    .map((item, index) => ({ item, index, depth: depthOf(item) }))
    .sort((a, b) => a.depth - b.depth || a.index - b.index)
    .forEach(({ item }) => item.draw());
}

/**
 * A fitting's painter anchor is the centre of its camera-facing footprint edge.
 * Agents should supply their existing world anchor to the same drawDepthSorted
 * pass, so figures and furniture occlude one another by the same gx+gy rule.
 */
export function fittingDepthItem(
  fitting: RoomFitting,
  draw: () => void,
): DepthDrawable {
  return {
    gx: fitting.gx + fitting.w / 2,
    gy: fitting.gy + fitting.h,
    draw,
  };
}

/** Build fitting entries for the existing renderer's combined depth pass. */
export function fittingDrawables(
  ctx: CanvasRenderingContext2D,
  fittings: readonly RoomFitting[],
  origin: RoomOrigin,
  camera: CameraTransform,
  tokens: OfficeTokens,
): DepthDrawable[] {
  return fittings
    .filter((fitting) => fitting.type !== "rug" && fitting.type !== "redCarpet")
    .map((fitting) => createFittingDrawable(ctx, fitting, origin, camera, tokens));
}

export function createFittingDrawable(
  ctx: CanvasRenderingContext2D,
  fitting: RoomFitting,
  origin: RoomOrigin,
  camera: CameraTransform,
  tokens: OfficeTokens,
): DepthDrawable {
  return fittingDepthItem(fitting, () => drawFitting(ctx, fitting, origin, camera, tokens));
}

export function createAgentDepthDrawable(
  gx: number,
  gy: number,
  draw: () => void,
): DepthDrawable {
  return { gx, gy, draw };
}

/**
 * Complete Stage 6 pass contract for an existing room renderer:
 *   1. drawRoomFloor(...)
 *   2. drawRoomSurfaceRegions(...)
 *   3. drawRoomWalls(...)
 *   4. collect createFittingDrawable(...) and createAgentDepthDrawable(...)
 *   5. drawDepthSorted(combinedItems)
 *   6. render recipe.label / subLabels as DOM overlays, never Canvas text
 *
 * The module intentionally does not instantiate a renderer or control any
 * movement, collision, navigation, event timing, image or GIF lifecycle.
 */
export type Stage6FittingType = FittingType;
