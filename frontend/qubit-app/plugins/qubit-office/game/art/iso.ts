/* True isometric projection — the geometry the office is built on.
 *
 * WHY THIS REPLACES THE FLAT VIEW
 * -------------------------------
 * The first pass drew rooms as flat rectangles in screen space: a "wall" was a
 * rectangle at the top of the view and a "floor" a rectangle below it. That
 * reads as a diagram, not a room. The reference (Habblaud) is a true 45°
 * isometric where a 16px tile becomes a 32x16 diamond and every object is
 * anchored to the grid, so floors recede, walls have real height, and a figure
 * standing "behind" a desk is genuinely drawn behind it.
 *
 * PROJECTION
 * ----------
 *   screenX = (gx - gy) * (TILE / 2)
 *   screenY = (gx + gy) * (TILE / 4) - z * (TILE / 2)
 *
 * A 2:1 diamond — the classic pixel-art isometric ratio. An object at grid
 * (gx, gy, z) with z = 0 has its feet on the floor plane; z = 1 is one storey
 * of wall height up. `depth()` gives the painter's-algorithm sort key.
 *
 * Everything here is pure arithmetic, so the projection itself is testable
 * without a canvas — which matters because a wrong projection silently makes
 * every scene subtly broken and nobody can say why.
 */

/** The tile edge in world px. 16 matches the reference's grid. */
export const TILE = 16;

/** Half-width and half-height of one tile's diamond, in screen px. */
export const HALF_W = TILE;
export const HALF_H = TILE / 2;

export interface GridPoint {
  gx: number;
  gy: number;
  /** Height in tiles above the floor plane. */
  z?: number;
}

export interface ScreenPoint {
  x: number;
  y: number;
}

/** Grid -> screen. Origin (0,0,0) maps to (0,0); callers translate. */
export function toScreen(p: GridPoint): ScreenPoint {
  const z = p.z ?? 0;
  return {
    x: (p.gx - p.gy) * HALF_W,
    y: (p.gx + p.gy) * HALF_H - z * TILE,
  };
}

/** Screen -> grid, on the z = 0 plane. The inverse of `toScreen` there. */
export function toGrid(p: ScreenPoint): { gx: number; gy: number } {
  return {
    gx: (p.y / HALF_H + p.x / HALF_W) / 2,
    gy: (p.y / HALF_H - p.x / HALF_W) / 2,
  };
}

/**
 * The painter's-algorithm sort key.
 *
 * Depth is `gx + gy` ASCENDING: a tile with a larger sum is nearer the viewer
 * and must be drawn later. `z` is subtracted so a high object (a wall) is drawn
 * before the floor in front of it. Two objects on the same key are on the same
 * diagonal and their order does not matter.
 */
export function depth(p: GridPoint): number {
  return (p.gx + p.gy) - (p.z ?? 0) * 2;
}

/** The four corners of a tile's floor diamond, in screen space. */
export function tileDiamond(gx: number, gy: number, ox = 0, oy = 0): ScreenPoint[] {
  const cx = (gx - gy) * HALF_W + ox;
  const cy = (gx + gy) * HALF_H + oy;
  return [
    { x: cx, y: cy - HALF_H },
    { x: cx + HALF_W, y: cy },
    { x: cx, y: cy + HALF_H },
    { x: cx - HALF_W, y: cy },
  ];
}

/**
 * The visible faces of a wall block, as screen-space polygons.
 *
 * A wall occupies grid cell (gx, gy) and rises `h` tiles. In isometric only two
 * of its four vertical faces are ever visible: the SOUTH-west face (facing the
 * viewer's lower-left) and the SOUTH-east face. The north faces are always
 * occluded, so drawing them would be wasted work and — worse — would z-fight.
 *
 * This is what gives a wall its apparent solidity: two faces at different
 * angles, each lit differently, meeting at a vertical edge.
 */
export function wallFaces(
  gx: number, gy: number, h: number, ox = 0, oy = 0,
): { west: ScreenPoint[]; east: ScreenPoint[]; top: ScreenPoint[] } {
  const hpx = h * TILE;
  const base = tileDiamond(gx, gy, ox, oy);
  // base: [top, right, bottom, left]  (top = north corner)
  const north = base[0], east = base[1], south = base[2], west = base[3];
  const lift = (p: ScreenPoint): ScreenPoint => ({ x: p.x, y: p.y - hpx });
  return {
    // the face you see looking from the lower-left
    west: [west, south, lift(south), lift(west)],
    // the face you see looking from the lower-right
    east: [south, east, lift(east), lift(south)],
    top: [lift(north), lift(east), lift(south), lift(west)],
  };
}

/** How many screen px one tile of height is worth. */
export const HEIGHT_UNIT = TILE;

/**
 * A whole room's worth of geometry, in tiles.
 *
 * The proportions are the reference's: a room is two tiles of visible north
 * wall face, nine tiles of floor, and one tile of south wall. Those numbers are
 * not arbitrary — 9 tiles of floor at a 2:1 diamond is 144px wide and 72 deep,
 * which is what makes a room read as a ROOM rather than a corridor.
 */
export const ROOM = {
  /** Tiles of north wall face standing at the back of a room. */
  wallFaceTiles: 2,
  /** Floor depth in tiles (north-to-south). */
  floorTiles: 9,
  /** South wall stub, so the room is enclosed. */
  southTiles: 1,
  /** Room width in tiles. */
  widthTiles: 16,
} as const;

/** Total room depth in tiles, including the walls. */
export const ROOM_DEPTH = ROOM.wallFaceTiles + ROOM.floorTiles + ROOM.southTiles;

/** Wall height in tiles for an interior partition. */
export const WALL_H = 2;

/**
 * Which tile is in front, for two objects the caller is about to draw.
 *
 * A convenience over `depth` so callers state the intent — "is A in front of B"
 * — rather than re-deriving the comparison at each site and getting it backwards
 * half the time.
 */
export function isInFront(a: GridPoint, b: GridPoint): boolean {
  return depth(a) > depth(b);
}

/** Sort items into draw order. Stable, so equal keys keep the caller's order. */
export function sortByDepth<T>(items: readonly T[], at: (item: T) => GridPoint): T[] {
  return items
    .map((item, index) => ({ item, index, key: depth(at(item)) }))
    .sort((a, b) => a.key - b.key || a.index - b.index)
    .map((e) => e.item);
}
