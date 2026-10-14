/* The isometric room renderer: what a floor of the office actually looks like.
 *
 * This is the piece that turns the tower payload into the reference's look.
 * It builds a grid from the PAYLOAD's own floorplan (rooms, their positions and
 * sizes, their occupants) and paints it in true isometric with the shading
 * model from `colour.ts`.
 *
 * THE ORDER OF OPERATIONS IS THE RENDERING
 * ----------------------------------------
 * Isometric has no depth buffer, so correctness is entirely in the draw order:
 *
 *   1. the floor plane, tile by tile, back to front
 *   2. wall blocks, sorted by depth (their two visible faces shaded apart)
 *   3. furniture and figures, sorted by depth, interleaved
 *
 * Getting 2 and 3 into one sorted list is what makes a figure standing behind a
 * desk be drawn behind it. Drawing all walls then all people looks correct until
 * someone walks in front of a wall, and then it is obviously wrong.
 */

import { mix, ramp, shade, seeded, type Ramp } from "./colour";
import { drawContactShadow } from "./surfaces";
import {
  HALF_H, HALF_W, ROOM, TILE, WALL_H, depth, tileDiamond, toScreen, wallFaces,
} from "./iso";

/** One room, as the payload describes it. */
export interface IsoRoom {
  id: number;
  slug: string;
  kind: string;
  /** Grid position and size, in tiles. */
  x: number;
  y: number;
  w: number;
  h: number;
  occupants: readonly IsoOccupant[];
}

export interface IsoOccupant {
  agentId: number;
  slug: string;
  animation: string;
  stalled: boolean;
}

/** A floor to draw. */
export interface IsoFloor {
  number: number;
  name: string;
  role: string;
  rooms: readonly IsoRoom[];
}

export interface IsoTheme {
  carpet: string;
  wall: string;
  trim: string;
  accent: string;
}

const THEMES: readonly IsoTheme[] = [
  { carpet: "#7d8ba3", wall: "#c9d3e0", trim: "#93a0b5", accent: "#3f7fd8" },
  { carpet: "#a8906a", wall: "#e6dac4", trim: "#bda57c", accent: "#ec8a3c" },
  { carpet: "#7a9b7f", wall: "#d6e0cf", trim: "#9bb48d", accent: "#4cae6a" },
  { carpet: "#8b83a8", wall: "#dbd6e8", trim: "#a89fc4", accent: "#8d66cf" },
  { carpet: "#a87d6d", wall: "#e9d4c9", trim: "#c49886", accent: "#e2604f" },
  { carpet: "#6a9b95", wall: "#cfe3df", trim: "#8db3ac", accent: "#36b0b0" },
  { carpet: "#77808f", wall: "#d0d4db", trim: "#8b95a2", accent: "#f2c14e" },
];

export function themeFor(seed: number): IsoTheme {
  const n = THEMES.length;
  return THEMES[((Math.floor(seed) % n) + n) % n];
}

const ANIM: Record<string, string> = {
  idle: "#9aa0b0",
  working: "#f0c05a",
  walking: "#6fd3a6",
  meeting: "#8fb0e0",
  blocked: "#e0554a",
  carrying: "#c99ae0",
  glow: "#ffd98a",
  settling: "#9ad0c0",
  walking_to_manager: "#e08fb0",
  seated: "#9aa0b0",
};

/** What a scene painter needs to place the view. */
export interface IsoCamera {
  /** Screen-space offset of grid origin. */
  ox: number;
  oy: number;
  /** Pixels per world pixel. */
  zoom: number;
}

/**
 * Fit a floor into the viewport and return the camera.
 *
 * Centres the floor's bounding diamond. Without this a floor drifts off-screen
 * as it grows, and "the room is empty" gets reported when the room is merely
 * somewhere else.
 */
export function cameraFor(
  floor: IsoFloor, width: number, height: number,
): IsoCamera {
  let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
  for (const r of floor.rooms) {
    for (const [gx, gy] of [
      [r.x, r.y], [r.x + r.w, r.y], [r.x, r.y + r.h], [r.x + r.w, r.y + r.h],
    ]) {
      const p = toScreen({ gx, gy });
      minX = Math.min(minX, p.x - HALF_W);
      maxX = Math.max(maxX, p.x + HALF_W);
      // WALL HEIGHT IS RESERVED AT BOTH ENDS. A wall rises ABOVE its base tile,
      // so the top of the farthest wall is `WALL_H * TILE` px above the topmost
      // floor corner. Reserving the height only at the bottom edge (as this did
      // originally) pushed every wall top out of the frame — the rooms rendered
      // as bare floor diamonds with no walls visible at all.
      minY = Math.min(minY, p.y - HALF_H - WALL_H * TILE);
      maxY = Math.max(maxY, p.y + HALF_H + WALL_H * TILE);
    }
  }
  if (!Number.isFinite(minX)) return { ox: width / 2, oy: height / 2, zoom: 1 };
  const spanX = Math.max(1, maxX - minX);
  const spanY = Math.max(1, maxY - minY);
  const zoom = Math.min(
    Math.max(0.35, (width * 0.92) / spanX),
    Math.max(0.35, (height * 0.9) / spanY),
    3.2,
  );
  return {
    ox: width / 2 - ((minX + maxX) / 2) * zoom,
    oy: height / 2 - ((minY + maxY) / 2) * zoom,
    zoom,
  };
}

/** A 2D context plus the camera: everything below draws through this. */
interface Painter {
  ctx: CanvasRenderingContext2D;
  cam: IsoCamera;
}

function poly(p: Painter, pts: readonly { x: number; y: number }[]): void {
  const { ctx, cam } = p;
  ctx.beginPath();
  pts.forEach((pt, i) => {
    const x = pt.x * cam.zoom + cam.ox;
    const y = pt.y * cam.zoom + cam.oy;
    if (i === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.closePath();
}

function fillPoly(p: Painter, pts: readonly { x: number; y: number }[], colour: string): void {
  poly(p, pts);
  p.ctx.fillStyle = colour;
  p.ctx.fill();
}

/** The floor plane: every tile of every room, back to front. */
function paintFloorPlane(p: Painter, floor: IsoFloor): void {
  const { ctx, cam } = p;
  for (const room of floor.rooms) {
    const theme = themeFor(room.id + floor.number * 3);
    const rand = seeded(room.id * 977 + floor.number);
    for (let gy = room.y; gy < room.y + room.h; gy++) {
      for (let gx = room.x; gx < room.x + room.w; gx++) {
        const d = tileDiamond(gx, gy);
        ctx.save();
        ctx.translate(cam.ox, cam.oy);
        ctx.scale(cam.zoom, cam.zoom);
        // Per-tile jitter. This is the single reason a large floor does not
        // read as one flat sheet of colour.
        fillPoly(p, d, shade(theme.carpet, (rand() - 0.5) * 0.055));
        // A soft seam on the two far edges so the lattice is legible without
        // turning into a wireframe.
        ctx.strokeStyle = shade(theme.carpet, -0.075);
        ctx.lineWidth = 1 / cam.zoom;
        ctx.beginPath();
        ctx.moveTo(d[0].x, d[0].y);
        ctx.lineTo(d[3].x, d[3].y);
        ctx.lineTo(d[2].x, d[2].y);
        ctx.stroke();
        ctx.restore();
      }
    }
  }
}

/**
 * Wall blocks around each room, sorted so nearer walls overdraw farther ones.
 *
 * The two visible faces are shaded differently — the east face is the dark one
 * — which is what makes the corner between them read as an edge rather than a
 * colour change.
 */
function paintWalls(p: Painter, floor: IsoFloor): void {
  interface Block { gx: number; gy: number; theme: IsoTheme; key: number }
  const blocks: Block[] = [];
  for (const room of floor.rooms) {
    const theme = themeFor(room.id + floor.number * 3);
    // the north wall, spanning the room's width, two tiles tall
    for (let gx = room.x; gx < room.x + room.w; gx++) {
      const gy = room.y;
      blocks.push({ gx, gy, theme, key: depth({ gx, gy }) });
    }
    // the two side walls, so the room is enclosed rather than open-ended
    for (let gy = room.y; gy < room.y + room.h + ROOM.southTiles; gy++) {
      blocks.push({ gx: room.x, gy, theme, key: depth({ gx: room.x, gy }) });
      blocks.push({ gx: room.x + room.w - 1, gy, theme, key: depth({ gx: room.x + room.w - 1, gy }) });
    }
  }
  blocks.sort((a, b) => a.key - b.key);

  const { ctx, cam } = p;
  for (const b of blocks) {
    const faces = wallFaces(b.gx, b.gy, WALL_H);
    const r = ramp(b.theme.wall, 0.08);
    ctx.save();
    ctx.translate(cam.ox, cam.oy);
    ctx.scale(cam.zoom, cam.zoom);
    // the west (lit) face
    fillPoly(p, faces.west, r.base);
    // the east face, clearly darker so the corner is visible
    fillPoly(p, faces.east, r.dk);
    // the cap: the top of the wall, lit
    fillPoly(p, faces.top, mix(r.hi, "#ffffff", 0.06));
    // the baseboard along the bottom of the lit face
    const bs = b.theme.trim;
    const wf = faces.west;
    const t = 3 / cam.zoom;
    fillPoly(p, [
      wf[0], wf[1],
      { x: wf[1].x, y: wf[1].y - t },
      { x: wf[0].x, y: wf[0].y - t },
    ], bs);
    ctx.restore();
  }
}

/** A desk: an isometric box with a lit top and a drawn shadow. */
function paintDesk(p: Painter, gx: number, gy: number, colour = "#a07f5c"): void {
  const { ctx, cam } = p;
  const r = ramp(colour, 0.09);
  const faces = wallFaces(gx, gy, 0.55);
  ctx.save();
  ctx.translate(cam.ox, cam.oy);
  ctx.scale(cam.zoom, cam.zoom);
  fillPoly(p, faces.west, r.dk);
  fillPoly(p, faces.east, r.dd);
  fillPoly(p, faces.top, r.lt);
  // a monitor on the desk
  const mon = ramp("#39445a", 0.1);
  const mp = wallFaces(gx, gy, 1.15);
  fillPoly(p, [mp.west[2], mp.west[3], mp.west[2], mp.west[2]], mon.base);
  const screen = ramp("#5fd0c0", 0.1);
  fillPoly(p, [
    { x: mp.top[0].x, y: mp.top[0].y },
    { x: mp.top[0].x + HALF_W, y: mp.top[0].y + HALF_H },
    { x: mp.top[0].x + HALF_W, y: mp.top[0].y + HALF_H - 10 },
    { x: mp.top[0].x, y: mp.top[0].y - 10 },
  ], screen.base);
  ctx.restore();
}

/** A potted plant: the cheapest way to make an office look inhabited. */
function paintPlant(p: Painter, gx: number, gy: number): void {
  const { ctx, cam } = p;
  ctx.save();
  ctx.translate(cam.ox, cam.oy);
  ctx.scale(cam.zoom, cam.zoom);
  const centre = toScreen({ gx, gy });
  const pot = ramp("#8a6a4e", 0.1);
  fillPoly(p, [
    { x: centre.x - 5, y: centre.y - 7 },
    { x: centre.x + 5, y: centre.y - 7 },
    { x: centre.x + 4, y: centre.y + 1 },
    { x: centre.x - 4, y: centre.y + 1 },
  ], pot.base);
  const leaf = ramp("#4cae6a", 0.1);
  for (let i = 0; i < 5; i++) {
    ctx.fillStyle = i % 2 ? leaf.base : leaf.dk;
    const a = (i / 5) * Math.PI * 2;
    ctx.beginPath();
    ctx.ellipse(centre.x + Math.cos(a) * 4, centre.y - 12 + Math.sin(a) * 4,
                3, 6, a, 0, Math.PI * 2);
    ctx.fill();
  }
  ctx.restore();
}

/**
 * A figure, in isometric.
 *
 * Feet-anchored at the character's tile so it stands ON the floor rather than
 * floating, with a contact shadow sized to the same anchor. The body is three
 * tones of one state colour, which is what makes a 20px shape read as a person.
 */
function paintFigure(
  p: Painter, gx: number, gy: number, colour: string, stalled: boolean,
  selected: boolean,
): void {
  const { ctx, cam } = p;
  const c = toScreen({ gx, gy });
  const r = ramp(colour, 0.1);

  ctx.save();
  ctx.translate(cam.ox, cam.oy);
  ctx.scale(cam.zoom, cam.zoom);

  // the contact shadow, drawn on the floor underneath
  drawContactShadow(ctx, c.x, c.y, 7, 3.4, 0.36);

  if (selected) {
    ctx.strokeStyle = "#e08fb0";
    ctx.lineWidth = 1.5 / cam.zoom;
    ctx.beginPath();
    ctx.ellipse(c.x, c.y, 9, 5, 0, 0, Math.PI * 2);
    ctx.stroke();
  }

  const top = c.y - 20;
  // legs
  ctx.fillStyle = r.dd;
  ctx.fillRect(c.x - 4, top + 15, 3, 6);
  ctx.fillRect(c.x + 1, top + 15, 3, 6);
  // torso, lit at the top
  ctx.fillStyle = r.dk;
  ctx.fillRect(c.x - 6, top + 8, 12, 9);
  ctx.fillStyle = r.base;
  ctx.fillRect(c.x - 6, top + 9, 12, 6);
  ctx.fillStyle = r.lt;
  ctx.fillRect(c.x - 6, top + 8, 12, 2);
  // arms
  ctx.fillStyle = r.dk;
  ctx.fillRect(c.x - 8, top + 9, 2, 7);
  ctx.fillRect(c.x + 6, top + 9, 2, 7);
  // head with a hair cap, so it is not a bare circle
  ctx.fillStyle = "#f0d0ae";
  ctx.beginPath();
  ctx.arc(c.x, top + 4, 4.2, 0, Math.PI * 2);
  ctx.fill();
  ctx.fillStyle = shade("#f0d0ae", -0.22);
  ctx.beginPath();
  ctx.arc(c.x, top + 3, 4.2, Math.PI, Math.PI * 2);
  ctx.fill();

  if (stalled) {
    ctx.fillStyle = "#ff7a6a";
    ctx.fillRect(c.x - 1, top - 12, 2, 7);
    ctx.fillRect(c.x - 1, top - 3, 2, 2);
  }
  ctx.restore();
}

/**
 * Paint a floor of the office.
 *
 * Order: floor plane, then walls, then everything that stands on the floor
 * sorted by depth. That last sort is what puts a figure behind the desk it is
 * sitting at and in front of the wall behind it.
 */
export function paintFloor(
  ctx: CanvasRenderingContext2D, floor: IsoFloor,
  width: number, height: number, selectedAgent: number | null = null,
): void {
  const cam = cameraFor(floor, width, height);
  const p: Painter = { ctx, cam };

  // the ground behind everything, so a gap between rooms is not the page void
  ctx.fillStyle = "#141221";
  ctx.fillRect(0, 0, width, height);

  /* ORDER: the floor plane first, then the walls.
   *
   * A wall's FOOTPRINT IS A FLOOR TILE. The north run sits on `gy = room.y`,
   * which is also the first row of floor tiles, and the side runs sit on
   * `gx = room.x`. So if the floor were painted second it would cover every
   * wall's base and the room would read as an empty diamond with blocks
   * floating above it — which is exactly the bug this ordering fixes.
   *
   * Walls therefore come after the floor, and because they are raised on the
   * far edges plus the sides they are also behind everything standing on the
   * floor, which the standee sort below then handles.
   */
  paintFloorPlane(p, floor);
  paintWalls(p, floor);

  // walls and standees share one depth ordering
  interface Standee { gx: number; gy: number; draw: () => void; key: number }
  const standees: Standee[] = [];

  for (const room of floor.rooms) {
    const theme = themeFor(room.id + floor.number * 3);
    // desks: one per seat the payload reports, laid along the room's middle
    const seats = room.occupants.length;
    for (let i = 0; i < seats; i++) {
      const gx = room.x + 2 + (i % 4) * 3;
      const gy = room.y + 3 + Math.floor(i / 4) * 3;
      if (gx > room.x + room.w - 2 || gy > room.y + room.h - 1) continue;
      standees.push({
        gx, gy, key: depth({ gx, gy }),
        draw: () => paintDesk(p, gx, gy),
      });
      const occ = room.occupants[i];
      standees.push({
        gx, gy: gy + 1, key: depth({ gx, gy: gy + 1 }) + 0.4,
        draw: () => paintFigure(
          p, gx, gy + 1, ANIM[occ.animation] ?? "#9aa0b0", occ.stalled,
          occ.agentId === selectedAgent),
      });
    }
    // a plant in the room's far corner, for the rooms that have space
    if (room.w > 4 && room.h > 4) {
      const px2 = room.x + 1, py2 = room.y + room.h - 2;
      standees.push({
        gx: px2, gy: py2, key: depth({ gx: px2, gy: py2 }),
        draw: () => paintPlant(p, px2, py2),
      });
    }
    void theme;
  }

  standees.sort((a, b) => a.key - b.key);
  for (const s of standees) s.draw();
}
