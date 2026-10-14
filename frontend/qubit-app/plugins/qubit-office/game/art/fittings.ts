/* Isometric furniture and fittings.
 *
 * Everything here is a BOX in the same projection as the rooms, drawn with the
 * two visible faces shaded apart plus a contact shadow. That consistency is the
 * whole point: a desk drawn as a screen-space rectangle next to isometric walls
 * looks pasted on, however good the desk is on its own.
 *
 * The pieces are deliberately small and parametric — a reception desk, a lift
 * with a door pair, a stair run, a chair, a monitor, a plant. That is the
 * vocabulary an office actually needs, and each is a few dozen lines rather
 * than an asset pipeline.
 */

import { mix, ramp, shade } from "./colour";
import {
  HALF_H, HALF_W, TILE, tileDiamond, toScreen, wallFaces,
} from "./iso";
import { drawContactShadow } from "./surfaces";
import type { IsoCamera } from "./room";

export interface Fittings {
  ctx: CanvasRenderingContext2D;
  cam: IsoCamera;
}

function poly(f: Fittings, pts: readonly { x: number; y: number }[]): void {
  const { ctx, cam } = f;
  ctx.beginPath();
  pts.forEach((p, i) => {
    const x = p.x * cam.zoom + cam.ox;
    const y = p.y * cam.zoom + cam.oy;
    if (i === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.closePath();
}

function fill(f: Fittings, pts: readonly { x: number; y: number }[], colour: string): void {
  poly(f, pts);
  f.ctx.fillStyle = colour;
  f.ctx.fill();
}

function withCam(f: Fittings, fn: () => void): void {
  f.ctx.save();
  f.ctx.translate(f.cam.ox, f.cam.oy);
  f.ctx.scale(f.cam.zoom, f.cam.zoom);
  fn();
  f.ctx.restore();
}

/** A box standing on the floor: height in tiles, footprint one tile. */
function box(
  f: Fittings, gx: number, gy: number, height: number, base: string,
  opts: { topColour?: string; alpha?: number } = {},
): void {
  const r = ramp(base, 0.09);
  const faces = wallFaces(gx, gy, height);
  const ctx = f.ctx;
  if (opts.alpha !== undefined) ctx.globalAlpha = opts.alpha;
  fill(f, faces.west, r.base);
  fill(f, faces.east, shade(r.base, -0.14));
  fill(f, faces.top, opts.topColour ?? mix(r.hi, "#ffffff", 0.05));
  if (opts.alpha !== undefined) ctx.globalAlpha = 1;
}

/** A wide bench (desk, counter): a box spanning several tiles on X. */
function bench(
  f: Fittings, gx: number, gy: number, tilesX: number, height: number,
  base: string,
): void {
  // Draw as a strip of boxes: the projection is affine along X, so a run of
  // unit boxes is seamless and avoids a duplicate of the face maths.
  for (let i = 0; i < tilesX; i++) box(f, gx + i, gy, height, base);
}

/**
 * The reception counter: a bench with a lip, a monitor and a chair.
 *
 * The lip is a second, slightly larger top box — that overhang is what makes a
 * counter read as furniture rather than as a wall stub.
 */
export function receptionDesk(f: Fittings, gx: number, gy: number): void {
  const base = "#9a7c58";
  withCam(f, () => {
    const c = toScreen({ gx: gx + 1, gy });
    drawContactShadow(f.ctx, c.x, c.y + 8, 34, 12, 0.32);
    bench(f, gx, gy, 3, 0.75, base);
    // the overhanging lip
    bench(f, gx, gy, 3, 0.9, mix(base, "#ffffff", 0.12));
    // a monitor on the counter, angled toward the room
    const mon = toScreen({ gx: gx + 1, gy });
    f.ctx.fillStyle = "#2f3b52";
    f.ctx.fillRect(mon.x - 9, mon.y - 40, 18, 14);
    f.ctx.fillStyle = mix("#5fd0c0", "#ffffff", 0.18);
    f.ctx.fillRect(mon.x - 7, mon.y - 38, 14, 10);
    f.ctx.fillStyle = "#20222c";
    f.ctx.fillRect(mon.x - 3, mon.y - 26, 6, 3);
  });
}

/**
 * The lift: a recessed frame with two doors and a floor indicator above.
 *
 * The doors are drawn as two vertical gradients meeting at a hairline, which in
 * isometric reads as a metal door pair. The green indicator is the one bright
 * accent that says "this is active".
 */
export function elevator(
  f: Fittings, gx: number, gy: number, height = 2.4,
): void {
  const ctx = f.ctx;
  withCam(f, () => {
    const r = ramp("#6a7284", 0.09);
    // the recess: a darker block behind, so the doors sit IN the wall
    box(f, gx, gy, height, shade(r.base, -0.28));
    // the door pair on the south-west face
    const faces = wallFaces(gx, gy, height);
    const wf = faces.west; // [w, s, s-lifted, w-lifted]
    const left = wf[0], right = wf[1];
    const halfN = 3;
    for (let i = 0; i < halfN; i++) {
      const t0 = i / halfN, t1 = (i + 1) / halfN;
      const x0 = left.x + (right.x - left.x) * t0;
      const x1 = left.x + (right.x - left.x) * t1;
      const y0 = left.y + (right.y - left.y) * t0;
      const y1 = left.y + (right.y - left.y) * t1;
      const top0 = y0 - height * TILE, top1 = y1 - height * TILE;
      const g = ctx.createLinearGradient(x0, 0, x1, 0);
      g.addColorStop(0, shade(r.lt, -i * 0.02));
      g.addColorStop(1, shade(r.base, -i * 0.02));
      ctx.fillStyle = g;
      poly(f, [
        { x: x0, y: y0 }, { x: x1, y: y1 },
        { x: x1, y: top1 }, { x: x0, y: top0 },
      ]);
      ctx.fill();
    }
    // the seam between the doors
    const midX = (left.x + right.x) / 2, midY = (left.y + right.y) / 2;
    ctx.strokeStyle = shade(r.dd, -0.1);
    ctx.lineWidth = 1 / f.cam.zoom;
    ctx.beginPath();
    ctx.moveTo(midX, midY);
    ctx.lineTo(midX, midY - height * TILE);
    ctx.stroke();
    // the indicator above the doors
    const cap = wallFaces(gx, gy, height).top;
    const ix = (cap[0].x + cap[2].x) / 2;
    const iy = (cap[0].y + cap[2].y) / 2;
    ctx.fillStyle = "#1c1f28";
    ctx.fillRect(ix - 8, iy - 7, 16, 6);
    ctx.fillStyle = "#6fd3a6";
    ctx.fillRect(ix - 6, iy - 6, 12, 3);
  });
}

/** A run of stairs, each step a box, ascending away from the viewer. */
export function stairs(
  f: Fittings, gx: number, gy: number, steps = 6,
): void {
  const base = "#5f5a72";
  withCam(f, () => {
    for (let i = 0; i < steps; i++) {
      box(f, gx, gy - i, 0.35 + i * 0.32, shade(base, -i * 0.012));
    }
  });
}

/** A chair: a low box with a back. */
export function chair(f: Fittings, gx: number, gy: number, base = "#4a6ea8"): void {
  withCam(f, () => {
    box(f, gx, gy, 0.42, base);
    box(f, gx, gy - 0.001 + 0.0, 0.72, shade(base, -0.1));
  });
}

/** A plant in a pot: two boxes and a few leaves. */
export function plant(f: Fittings, gx: number, gy: number): void {
  const ctx = f.ctx;
  withCam(f, () => {
    const c = toScreen({ gx, gy });
    drawContactShadow(ctx, c.x, c.y + 3, 8, 4, 0.34);
    box(f, gx, gy, 0.4, "#8a6a4e");
    const leaf = ramp("#4cae6a", 0.11);
    for (let i = 0; i < 6; i++) {
      const a = (i / 6) * Math.PI * 2;
      ctx.fillStyle = i % 2 ? leaf.base : leaf.dk;
      ctx.beginPath();
      ctx.ellipse(c.x + Math.cos(a) * 4, c.y - 12 + Math.sin(a) * 4, 3, 6, a,
                  0, Math.PI * 2);
      ctx.fill();
    }
    ctx.fillStyle = leaf.lt;
    ctx.beginPath();
    ctx.ellipse(c.x, c.y - 16, 3, 5, 0, 0, Math.PI * 2);
    ctx.fill();
  });
}

/** A lit sign panel, drawn flat against a wall face and readable. */
export function sign(
  f: Fittings, gx: number, gy: number, facing: "west" | "east",
  height: number,
): void {
  const ctx = f.ctx;
  withCam(f, () => {
    const faces = wallFaces(gx, gy, height);
    const face = facing === "west" ? faces.west : faces.east;
    const a = face[0], b = face[1];
    const inset = 0.18;
    const ax = a.x + (b.x - a.x) * inset, ay = a.y + (b.y - a.y) * inset;
    const bx = a.x + (b.x - a.x) * (1 - inset), by = a.y + (b.y - a.y) * (1 - inset);
    const r = ramp("#f4f1f8", 0.1);
    ctx.fillStyle = mix(r.base, "#ffffff", 0.2);
    poly(f, [
      { x: ax, y: ay - 9 }, { x: bx, y: by - 9 },
      { x: bx, y: by - 27 }, { x: ax, y: ay - 27 },
    ]);
    ctx.fill();
    ctx.strokeStyle = r.dd;
    ctx.lineWidth = 1 / f.cam.zoom;
    ctx.stroke();
  });
}

/** A rug: a flat diamond on the floor, a few tones off the carpet. */
export function rug(
  f: Fittings, gx: number, gy: number, tiles: number, base: string,
): void {
  const ctx = f.ctx;
  withCam(f, () => {
    const r = ramp(base, 0.1);
    for (let i = 0; i < tiles; i++) {
      for (let j = 0; j < tiles; j++) {
        const d = tileDiamond(gx + i, gy + j);
        // a border two rings in, so the rug reads as a deliberate object
        const edge = i === 0 || j === 0 || i === tiles - 1 || j === tiles - 1;
        fill(f, d, edge ? r.dk : (i + j) % 3 === 0 ? r.lt : r.base);
      }
    }
    // a single bright centre line, which is what a rug usually has
    for (let i = 0; i < tiles; i++) {
      const d = tileDiamond(gx + i, gy + Math.floor(tiles / 2));
      fill(f, d, mix(r.hi, "#ffffff", 0.1));
    }
  });
}

/** All the tile coordinates a fitting occupies, for depth sorting. */
export function fittingTiles(
  items: readonly { gx: number; gy: number }[],
): { cols: number; rows: number } {
  let cols = 0, rows = 0;
  for (const it of items) {
    cols = Math.max(cols, it.gx + 1);
    rows = Math.max(rows, it.gy + 1);
  }
  return { cols, rows };
}

export { HALF_W, HALF_H, TILE };
