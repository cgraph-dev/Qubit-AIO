/* Room surfaces: the wall face, the floor, and the contact shading between them.
 *
 * These three functions are what make a room read as a ROOM rather than as a
 * rectangle. Each is drawn from a material ramp, so a wall is never one colour:
 * it has a lit cap, a face that darkens downward, a pattern, and a baseboard
 * with its own top and bottom lines.
 *
 * All deterministic (see `seeded`) and pure, so the same room always paints the
 * same pixels — which is what makes "the art changed" a detectable event.
 */

import {
  mix, outlineFor, ramp, seeded, shade, type Ramp,
} from "./colour";

/** Room themes. One base colour per material; the ramp derives the rest.
 *
 * The list alternates colour families on purpose, so two neighbouring rooms
 * get visibly different carpets instead of a wash of one hue. */
export interface RoomTheme {
  carpet: string;
  wall: string;
  trim: string;
  accent: string;
}

const THEMES: readonly RoomTheme[] = [
  { carpet: "#6b7688", wall: "#c3ccd8", trim: "#8d99ab", accent: "#3f7fd8" },
  { carpet: "#8a7a5f", wall: "#e2d6c0", trim: "#b0996f", accent: "#ec8a3c" },
  { carpet: "#5f7a63", wall: "#d3ddcb", trim: "#8aa07c", accent: "#4cae6a" },
  { carpet: "#6f6889", wall: "#d5d0e2", trim: "#9b92b8", accent: "#8d66cf" },
  { carpet: "#8a6558", wall: "#e6cec2", trim: "#b98d7c", accent: "#e2604f" },
  { carpet: "#4e7d78", wall: "#c8dedb", trim: "#79a9a2", accent: "#36b0b0" },
  { carpet: "#5a626f", wall: "#c9cdd4", trim: "#7d8794", accent: "#f2c14e" },
  { carpet: "#8a6377", wall: "#e4ccd6", trim: "#b98ca1", accent: "#e47aa8" },
  { carpet: "#5c6f8d", wall: "#ccd8e6", trim: "#89a0be", accent: "#5b8fe0" },
  { carpet: "#7d7166", wall: "#ddd3c6", trim: "#a4977f", accent: "#c9713f" },
];

/** A theme for a room, stable for a given seed. Negative seeds are fine. */
export function roomTheme(seed: number): RoomTheme {
  const n = THEMES.length;
  return THEMES[((Math.floor(seed) % n) + n) % n];
}

export const themeCount = THEMES.length;

/** Wall geometry, in CSS px. The face is two tiles tall, as a 2:1 iso face. */
export const WALL_CAP_H = 5;
export const WALL_FACE_H = 2 * 26;
export const BASEBOARD_H = 4;

/**
 * Paint a wall FACE into a rect.
 *
 * The face darkens toward its bottom edge, which is what tells the eye the wall
 * is a vertical surface lit from above rather than a flat rectangle of colour.
 */
export function drawWallFace(
  ctx: CanvasRenderingContext2D,
  x: number, y: number, w: number, h: number,
  theme: RoomTheme,
  opts: { seed?: number; pattern?: "plain" | "stripes" | "panelled" } = {},
): void {
  const base = theme.wall;
  const r = climb(base);
  const capH = Math.min(WALL_CAP_H, h);
  const baseH = Math.min(BASEBOARD_H, Math.max(0, h - capH));
  const faceY = y + capH;
  const faceH = Math.max(0, h - capH - baseH);
  const rand = seeded(opts.seed ?? 1);

  // The face: a shade gradient, lighter at the top. Six steps is enough to read
  // as a gradient at this scale and cheap enough to run per wall per frame.
  for (let i = 0; i < faceH; i++) {
    const t = i / Math.max(1, faceH);
    ctx.fillStyle = shade(base, 0.03 - t * 0.055);
    ctx.fillRect(x, faceY + i, w, 1);
  }

  // Pattern, derived from the same ramp so it never floats off the material.
  const pattern = opts.pattern ?? "plain";
  if (pattern === "stripes") {
    ctx.fillStyle = shade(base, -0.035);
    for (let sx = x; sx < x + w; sx += 8) ctx.fillRect(sx, faceY, 3, faceH);
    ctx.fillStyle = shade(base, -0.015);
    for (let sx = x + 4; sx < x + w; sx += 8) ctx.fillRect(sx, faceY, 1, faceH);
  } else if (pattern === "panelled") {
    // Wainscot on the lower half: panel posts with a lit edge, a dark edge and
    // a shadow line where the rail meets the upper wall.
    const top = faceY + Math.floor(faceH * 0.42);
    ctx.fillStyle = mix("#b98a5e", base, 0.2);
    ctx.fillRect(x, top, w, faceY + faceH - top);
    for (let sx = x; sx < x + w; sx += 7) {
      ctx.fillStyle = r.dd;
      ctx.fillRect(sx, top, 1, faceY + faceH - top);
      ctx.fillStyle = r.lt;
      ctx.fillRect(sx + 1, top + 1, 1, faceY + faceH - top - 1);
    }
    ctx.fillStyle = r.hi;
    ctx.fillRect(x, top - 2, w, 1);
    ctx.fillStyle = r.lt;
    ctx.fillRect(x, top - 1, w, 1);
    ctx.fillStyle = r.dk;
    ctx.fillRect(x, top, w, 1);
  }

  // Shadow cast by the cap, then the cap itself with a lit top edge.
  ctx.fillStyle = shade(base, -0.075);
  ctx.fillRect(x, faceY, w, 1);
  const cap = mix("#3d4356", base, 0.12);
  ctx.fillStyle = cap;
  ctx.fillRect(x, y, w, capH);
  ctx.fillStyle = shade(cap, 0.09);
  ctx.fillRect(x, y, w, 1);
  ctx.fillStyle = shade(cap, -0.07);
  ctx.fillRect(x, y + capH - 1, w, 1);

  // Baseboard: the line that makes the wall meet the floor instead of stopping.
  const trim = theme.trim;
  ctx.fillStyle = trim;
  ctx.fillRect(x, y + h - baseH, w, baseH);
  ctx.fillStyle = shade(trim, 0.09);
  ctx.fillRect(x, y + h - baseH, w, 1);
  ctx.fillStyle = shade(trim, -0.09);
  ctx.fillRect(x, y + h - 1, w, 1);

  // Exterior walls get a light speckle so a long blank run is not dead flat.
  if ((opts.seed ?? 0) % 3 === 0) {
    for (let i = 0; i < Math.floor(w / 6); i++) {
      const px = x + Math.floor(rand() * w);
      const py = faceY + Math.floor(rand() * Math.max(1, faceH));
      ctx.fillStyle = rand() < 0.5 ? shade(base, -0.03) : shade(base, 0.03);
      ctx.fillRect(px, py, 1, 1);
    }
  }
}

/**
 * Paint a FLOOR in isometric tiles.
 *
 * Each tile gets its own 2% lightness jitter. That is barely visible tile by
 * tile and is the entire reason a large floor does not read as a single sheet
 * of colour — a perfectly uniform plane looks like a placeholder.
 */
export function drawFloor(
  ctx: CanvasRenderingContext2D,
  x: number, y: number, w: number, h: number,
  theme: RoomTheme,
  opts: { seed?: number; tile?: number } = {},
): void {
  const tile = opts.tile ?? 26;
  const base = theme.carpet;
  const rand = seeded(opts.seed ?? 7);

  // Base fill first: any seam between tiles shows this, not the page void.
  ctx.fillStyle = shade(base, -0.04);
  ctx.fillRect(x, y, w, h);

  const cols = Math.ceil(w / tile) + 2;
  const rows = Math.ceil(h / tile) + 2;
  for (let row = 0; row < rows; row++) {
    for (let col = 0; col < cols; col++) {
      // Iso offset: every other row shifts half a tile, which is what turns a
      // grid of squares into a diamond lattice without rotating anything.
      const offX = (row % 2) * (tile / 2);
      const tx = x - tile + col * tile + offX;
      const ty = y - tile + row * (tile / 2);
      const jitter = (rand() - 0.5) * 0.04;
      ctx.fillStyle = shade(base, jitter);
      ctx.beginPath();
      ctx.moveTo(tx + tile / 2, ty);
      ctx.lineTo(tx + tile, ty + tile / 2);
      ctx.lineTo(tx + tile / 2, ty + tile);
      ctx.lineTo(tx, ty + tile / 2);
      ctx.closePath();
      ctx.fill();
      // A faint dark seam on two edges; not all four, or the lattice reads as
      // wireframe rather than as a surface with depth.
      ctx.strokeStyle = shade(base, -0.055);
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(tx, ty + tile / 2);
      ctx.lineTo(tx + tile / 2, ty + tile);
      ctx.lineTo(tx + tile, ty + tile / 2);
      ctx.stroke();
    }
  }
}

/**
 * A contact shadow under a figure or object.
 *
 * Composited as a soft ellipse at low alpha and DARKENED toward a blue-grey
 * rather than toward black. This is the cheapest single trick for making a flat
 * sprite look like it is standing ON something instead of pasted over it.
 */
export function drawContactShadow(
  ctx: CanvasRenderingContext2D,
  cx: number, cy: number, rx: number, ry: number,
  strength = 0.26,
): void {
  const g = ctx.createRadialGradient(cx, cy, 0, cx, cy, Math.max(rx, ry));
  g.addColorStop(0, `rgba(28,32,52,${strength})`);
  g.addColorStop(0.6, `rgba(28,32,52,${strength * 0.5})`);
  g.addColorStop(1, "rgba(28,32,52,0)");
  ctx.save();
  ctx.translate(cx, cy);
  ctx.scale(1, ry / Math.max(1, rx));
  ctx.fillStyle = g;
  ctx.beginPath();
  ctx.arc(0, 0, Math.max(rx, ry), 0, Math.PI * 2);
  ctx.fill();
  ctx.restore();
}

/**
 * A 1px outline around a rect, in the outline slate rather than black.
 *
 * Used for furniture and lifted elements. `outlineFor` pulls the outline toward
 * the material's own dark, so a wooden desk gets a woody outline and a steel
 * one gets a steely outline instead of both getting the same black border.
 */
export function outlineRect(
  ctx: CanvasRenderingContext2D,
  x: number, y: number, w: number, h: number, colour: string,
): void {
  ctx.strokeStyle = outlineFor(colour);
  ctx.lineWidth = 1;
  ctx.strokeRect(x + 0.5, y + 0.5, w - 1, h - 1);
}

/** Local alias so the import list stays short at the call sites. */
function climb(base: string): Ramp {
  return ramp(base);
}
