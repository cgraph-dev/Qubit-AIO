/* F07 — the scenes: what each place LOOKS like.
 *
 * A scene is a pure draw function plus the decorations it owns. Splitting them
 * from the engine is what keeps this testable without a browser: a scene takes
 * a 2D context and a world, and paints. The Canvas host feeds it a real canvas
 * context; the Phaser host feeds it a Phaser canvas texture's context. Same
 * code, either engine.
 *
 * SCOPE, HONESTLY. These are SCAFFOLD interiors, per the plan: exterior
 * entrance + lobby + elevator/stairs + floor landing + rooms. They use flat
 * shapes and the palette from the design doc, not authored art. The plan's
 * visual gate ("the owner accepts the facade/lobby/interior/cast/HUD direction
 * as the requested game, not just an engineering prototype") has NOT been
 * passed, so this is explicitly a prototype and the full asset batch (F10) is
 * NOT produced here.
 *
 * Every scene paints from the world it is handed. None of them invent a person,
 * a room or a floor.
 */

import type { OfficeWorld, WorldCharacter, WorldFloor } from "./WorldAssembler";

export interface ScenePalette {
  void: string;
  ground: string;
  plate: string;
  wall: string;
  wallTop: string;
  accent: string;
  ink: string;
  muted: string;
  doorway: string;
}

/** The Japanese-anime-office direction from 40-ART-AND-ASSETS.md, as tokens. */
export const PALETTE: ScenePalette = {
  void: "#0b0f16",
  ground: "#1b2230",
  plate: "#2a3342",
  wall: "#39445a",
  wallTop: "#4b5876",
  accent: "#e08fb0",
  ink: "#e8edf6",
  muted: "#8b97ad",
  doorway: "#6fd3a6",
};

export interface SceneDrawArgs {
  ctx: CanvasRenderingContext2D;
  world: OfficeWorld;
  /** Viewport size in CSS px. */
  width: number;
  height: number;
  /** Which place we are drawing. */
  place: { kind: string; building?: string; floor?: number; room?: number };
  /** Progress of a travel animation, 0..1. Presentation only. */
  travel: number;
}

const TILE = 26;

function text(
  ctx: CanvasRenderingContext2D, s: string, x: number, y: number,
  size: number, colour: string, align: CanvasTextAlign = "left",
  weight = "400",
) {
  ctx.save();
  ctx.font = `${weight} ${size}px ui-sans-serif, system-ui, sans-serif`;
  ctx.fillStyle = colour;
  ctx.textAlign = align;
  ctx.textBaseline = "middle";
  ctx.fillText(s, x, y);
  ctx.restore();
}

function plate(
  ctx: CanvasRenderingContext2D, x: number, y: number, w: number, h: number,
  fill: string, stroke?: string,
) {
  ctx.save();
  ctx.fillStyle = fill;
  ctx.fillRect(x, y, w, h);
  if (stroke) {
    ctx.strokeStyle = stroke;
    ctx.lineWidth = 1;
    ctx.strokeRect(x + 0.5, y + 0.5, w - 1, h - 1);
  }
  ctx.restore();
}

/** A figure. Feet-anchored, tinted by state, with the state band the tower uses. */
function figure(
  ctx: CanvasRenderingContext2D, c: WorldCharacter, x: number, y: number,
  scale: number, selected: boolean,
) {
  const w = 12 * scale, h = 17 * scale;
  const top = y - h;
  const colour = ANIM_COLOUR[c.animation] ?? PALETTE.muted;
  ctx.save();
  if (selected) {
    ctx.strokeStyle = PALETTE.accent;
    ctx.lineWidth = 2;
    ctx.strokeRect(x - w * 0.8, top - 4 * scale, w * 1.6, h + 8 * scale);
  }
  // shadow
  ctx.fillStyle = "rgba(0,0,0,0.28)";
  ctx.beginPath();
  ctx.ellipse(x, y + 1, w * 0.7, w * 0.28, 0, 0, Math.PI * 2);
  ctx.fill();
  // body
  ctx.fillStyle = colour;
  ctx.fillRect(x - w / 2, top + h * 0.26, w, h * 0.46);
  // head
  ctx.fillStyle = PALETTE.ink;
  ctx.beginPath();
  ctx.arc(x, top + h * 0.14, w * 0.30, 0, Math.PI * 2);
  ctx.fill();
  // legs
  ctx.fillStyle = "rgba(0,0,0,0.35)";
  ctx.fillRect(x - w * 0.42, top + h * 0.72, w * 0.30, h * 0.28);
  ctx.fillRect(x + w * 0.12, top + h * 0.72, w * 0.30, h * 0.28);
  // the state band: the payload's own signal
  ctx.fillStyle = colour;
  ctx.fillRect(x - w * 0.62, top - 3 * scale, w * 1.24,
               Math.max(1, 2 * scale));
  ctx.restore();
  if (c.stalled) {
    text(ctx, "!", x, top - 12 * scale, 12 * scale, "#e0554a", "center", "700");
  }
}

const ANIM_COLOUR: Record<string, string> = {
  idle: "#8a8f98",
  working: "#f0c05a",
  walking: "#6fd3a6",
  meeting: "#8fb0e0",
  blocked: "#e0554a",
  carrying: "#c99ae0",
  glow: "#ffd98a",
  settling: "#9ad0c0",
  walking_to_manager: "#e08fb0",
  seated: "#8a8f98",
};

/* ======================================================================
 * THE EXTERIOR — the district with the building's front door
 * ====================================================================== */
export function drawExterior(a: SceneDrawArgs) {
  const { ctx, width, height, world } = a;
  plate(ctx, 0, 0, width, height, PALETTE.void);
  // ground plane
  plate(ctx, 0, height * 0.62, width, height * 0.38, PALETTE.ground);
  // a lane
  ctx.save();
  ctx.fillStyle = "rgba(255,255,255,0.05)";
  ctx.fillRect(0, height * 0.8, width, 2);
  ctx.restore();

  // the building silhouette
  const bw = Math.min(360, width * 0.46);
  const bh = height * 0.56;
  const bx = width / 2 - bw / 2;
  const by = height * 0.62 - bh;
  plate(ctx, bx, by, bw, bh, PALETTE.wall, PALETTE.wallTop);
  // floor lines
  const floors = Math.max(1, world.building.floors.length);
  for (let i = 1; i < floors; i++) {
    const y = by + (bh / floors) * i;
    ctx.save();
    ctx.fillStyle = "rgba(255,255,255,0.07)";
    ctx.fillRect(bx, y, bw, 1);
    ctx.restore();
  }
  // windows: one per floor, so the building reads as having the floors the
  // payload actually reports
  for (let i = 0; i < floors; i++) {
    const y = by + (bh / floors) * i + (bh / floors) * 0.34;
    for (let k = 0; k < 4; k++) {
      plate(ctx, bx + 24 + k * ((bw - 48) / 4), y, 18, 14,
            i === 0 ? PALETTE.accent : "rgba(255,255,255,0.16)");
    }
  }
  // the entrance
  const dw = 44, dh = 46;
  const dx = width / 2 - dw / 2, dy = height * 0.62 - dh;
  plate(ctx, dx, dy, dw, dh, PALETTE.doorway, "rgba(255,255,255,0.25)");
  // a soft glow at the door so the enter affordance is visible
  ctx.save();
  const g = ctx.createRadialGradient(width / 2, dy + dh, 4, width / 2, dy + dh,
                                     90);
  g.addColorStop(0, "rgba(111,211,166,0.30)");
  g.addColorStop(1, "rgba(111,211,166,0)");
  ctx.fillStyle = g;
  ctx.fillRect(width / 2 - 100, dy - 20, 200, dh + 60);
  ctx.restore();

  text(ctx, world.building.name, width / 2, by - 22, 16, PALETTE.ink, "center",
       "600");
  text(ctx, world.building.floors.length + " floors", width / 2, by - 6, 11,
       PALETTE.muted, "center");
  text(ctx, "Enter", width / 2, dy + dh + 16, 12, PALETTE.doorway, "center",
       "600");
}

/* ======================================================================
 * THE ENTRANCE HALL — inside the threshold, looking in
 * ====================================================================== */
export function drawEntranceHall(a: SceneDrawArgs) {
  const { ctx, width, height, world } = a;
  plate(ctx, 0, 0, width, height, PALETTE.void);
  const fh = height * 0.72;
  const fy = height * 0.14;
  plate(ctx, width * 0.05, fy, width * 0.9, fh, PALETTE.plate, PALETTE.wallTop);
  plate(ctx, width * 0.05, fy + fh - 28, width * 0.9, 28, PALETTE.ground);

  // Looking back out: the doorway you just came through, bright with daylight.
  const dw = 58, dh = 96;
  const dx = width * 0.5 - dw / 2;
  const dy = fy + fh - 28 - dh;
  plate(ctx, dx, dy, dw, dh, PALETTE.doorway, "rgba(255,255,255,0.3)");
  ctx.save();
  const g = ctx.createRadialGradient(width * 0.5, dy + dh, 6, width * 0.5,
                                     dy + dh, 150);
  g.addColorStop(0, "rgba(111,211,166,0.22)");
  g.addColorStop(1, "rgba(111,211,166,0)");
  ctx.fillStyle = g;
  ctx.fillRect(width * 0.5 - 160, dy - 40, 320, dh + 90);
  ctx.restore();

  // Deeper in: the way to reception, and the building's own signage.
  text(ctx, world.building.name, width * 0.05 + 14, fy + 20, 15, PALETTE.ink,
       "left", "600");
  text(ctx, "You are inside.", width * 0.05 + 14, fy + 40, 12, PALETTE.muted);
  text(ctx, "To reception \u2192", width * 0.95 - 14, fy + 20, 12, PALETTE.accent,
       "right", "600");
  text(ctx, "Back outside", width * 0.5, dy + dh + 18, 11, PALETTE.doorway,
       "center");
  text(ctx, world.building.floors.length + " floors above", width / 2,
       fy + fh - 12, 11, PALETTE.muted, "center");
}

/* ======================================================================
 * THE LOBBY — reception, elevator, stairs
 * ====================================================================== */
export function drawLobby(a: SceneDrawArgs) {
  const { ctx, width, height, world } = a;
  plate(ctx, 0, 0, width, height, PALETTE.void);
  const fh = height * 0.66;
  const fy = height * 0.2;
  plate(ctx, width * 0.06, fy, width * 0.88, fh, PALETTE.plate, PALETTE.wallTop);
  // floor
  plate(ctx, width * 0.06, fy + fh - 24, width * 0.88, 24, PALETTE.ground);

  // reception desk
  plate(ctx, width * 0.12, fy + fh - 96, 130, 40, PALETTE.wall,
        "rgba(255,255,255,0.18)");
  text(ctx, "Reception", width * 0.12 + 12, fy + fh - 74, 11, PALETTE.muted);

  // the elevator: one door per floor, as a real column of buttons would be
  const ex = width * 0.62, ew = 66, eh = 96;
  const ey = fy + fh - 24 - eh;
  plate(ctx, ex, ey, ew, eh, PALETTE.wall, PALETTE.accent);
  text(ctx, "\u25B2\u25BC", ex + ew / 2, ey + eh * 0.5, 20, PALETTE.accent,
       "center");
  text(ctx, "Elevator", ex + ew / 2, ey + eh + 14, 11, PALETTE.muted, "center");

  // the stairs
  const sx = width * 0.8;
  for (let i = 0; i < 5; i++) {
    plate(ctx, sx + i * 9, ey + eh - i * 12 - 10, 9, 10 + i * 12,
          i % 2 ? PALETTE.wallTop : PALETTE.wall);
  }
  text(ctx, "Stairs", sx + 22, ey + eh + 14, 11, PALETTE.muted, "center");

  // WHO IS HERE, from the payload only. The lobby is floor 1's floor, so the
  // figures standing here are the ones the payload seats on floor 1.
  const lowest = Math.min(...world.building.floors.map((f) => f.number));
  const here = world.building.characters.filter(
    (c) => c.roomId !== null
      && world.building.floors.find((f) => f.number === lowest)?.rooms
        .some((r) => r.id === c.roomId),
  );
  here.forEach((c, i) => {
    figure(ctx, c, width * 0.2 + i * 34, fy + fh - 34, 1.5, false);
  });

  text(ctx, "Reception", width * 0.06 + 10, fy + 16, 13, PALETTE.ink, "left",
       "600");
  text(ctx, world.building.name, width * 0.06 + 10, fy + 34, 11, PALETTE.muted);
}

/* ======================================================================
 * A FLOOR LANDING — the elevator arrives, the rooms are off it
 * ====================================================================== */
export function drawFloor(a: SceneDrawArgs, floor: WorldFloor) {
  const { ctx, width, height, world } = a;
  plate(ctx, 0, 0, width, height, PALETTE.void);
  const fh = height * 0.68;
  const fy = height * 0.16;
  plate(ctx, width * 0.04, fy, width * 0.92, fh, PALETTE.plate, PALETTE.wallTop);
  plate(ctx, width * 0.04, fy + fh - 26, width * 0.92, 26, PALETTE.ground);

  // the landing: the elevator you came out of
  plate(ctx, width * 0.08, fy + fh - 130, 54, 104, PALETTE.wall, PALETTE.accent);
  text(ctx, "\u25B2\u25BC", width * 0.08 + 27, fy + fh - 78, 16, PALETTE.accent,
       "center");

  // the ROOMS THIS FLOOR ACTUALLY HAS, laid out left to right
  const rooms = floor.rooms;
  const rw = Math.min(150, (width * 0.72) / Math.max(1, rooms.length) - 16);
  rooms.forEach((r, i) => {
    const rx = width * 0.2 + i * (rw + 16);
    const ry = fy + 30;
    const rh = fh - 90;
    plate(ctx, rx, ry, rw, rh, PALETTE.wall, "rgba(255,255,255,0.16)");
    // door
    plate(ctx, rx + rw / 2 - 16, ry + rh - 34, 32, 34, PALETTE.doorway);
    text(ctx, r.slug, rx + rw / 2, ry + 16, 11, PALETTE.ink, "center", "600");
    text(ctx, r.kind, rx + rw / 2, ry + 32, 10, PALETTE.muted, "center");
    // occupants, straight from the payload
    r.occupants.slice(0, 4).forEach((c, k) => {
      const occupant = world.building.characters.find(
        (wc) => wc.agentId === c.agent_id);
      if (occupant) {
        figure(ctx, occupant, rx + 22 + k * 30, ry + rh - 46, 1.3, false);
      }
    });
    text(ctx, r.occupants.length + " in room", rx + rw / 2, ry + rh - 12, 10,
         PALETTE.muted, "center");
  });

  if (rooms.length === 0) {
    // NOT AN INVENTED ROOM. An empty floor says it is empty.
    text(ctx, "No rooms on this floor", width / 2, fy + fh / 2, 13,
         PALETTE.muted, "center");
  }

  text(ctx, floor.name + "  \u00b7  floor " + floor.number, width * 0.04 + 10,
       fy + 16, 13, PALETTE.ink, "left", "600");
  text(ctx, floor.role, width * 0.04 + 10, fy + 34, 11, PALETTE.muted);
}

/* ======================================================================
 * A ROOM INTERIOR
 * ====================================================================== */
export function drawRoom(a: SceneDrawArgs, floor: WorldFloor) {
  const { ctx, width, height, world } = a;
  const room = floor.rooms.find((r) => r.id === a.place.room);
  plate(ctx, 0, 0, width, height, PALETTE.void);
  const fh = height * 0.7;
  const fy = height * 0.15;
  plate(ctx, width * 0.06, fy, width * 0.88, fh, PALETTE.plate, PALETTE.wallTop);
  plate(ctx, width * 0.06, fy + fh - 30, width * 0.88, 30, PALETTE.ground);

  if (!room) {
    text(ctx, "This room is not in the tower report", width / 2, fy + fh / 2, 13,
         PALETTE.muted, "center");
    return;
  }

  // desks: one per occupant the payload seats here, no more
  const occ = room.occupants;
  const cols = Math.max(1, Math.min(3, occ.length));
  occ.forEach((c, i) => {
    const worldChar = world.building.characters.find(
      (wc) => wc.agentId === c.agent_id);
    const col = i % cols, row = Math.floor(i / cols);
    const dx = width * 0.16 + col * 150;
    const dy = fy + 70 + row * 96;
    plate(ctx, dx, dy + 22, 92, 26, PALETTE.wall, "rgba(255,255,255,0.14)");
    if (worldChar) figure(ctx, worldChar, dx + 46, dy + 22, 1.7, false);
    text(ctx, c.slug, dx + 46, dy + 60, 11, PALETTE.ink, "center", "600");
    text(ctx, c.animation + (c.stalled ? " \u00b7 stalled" : ""), dx + 46,
         dy + 74, 10, c.stalled ? "#e0554a" : PALETTE.muted, "center");
  });

  if (occ.length === 0) {
    text(ctx, "Nobody is seated here", width / 2, fy + fh / 2, 13, PALETTE.muted,
         "center");
  }

  text(ctx, room.slug, width * 0.06 + 10, fy + 16, 13, PALETTE.ink, "left",
       "600");
  text(ctx, room.kind + "  \u00b7  " + occ.length + " seated", width * 0.06 + 10,
       fy + 34, 11, PALETTE.muted);
}

/** Paint the current place. The one entry point the hosts call. */
export function drawPlace(a: SceneDrawArgs) {
  const { place, world } = a;
  switch (place.kind) {
    case "exterior":
      return drawExterior(a);
    case "building":
      /* ENTERING MUST LOOK DIFFERENT FROM STANDING OUTSIDE. `building` and
       * `exterior` both painted `drawExterior`, so walking through the front
       * door produced byte-identical pixels — the walk LOOKED broken even
       * though the state had changed correctly, which is how a working
       * traversal gets reported as a dead click. The building view is the
       * entrance hall: same facade context, but you are inside the threshold. */
      return drawEntranceHall(a);
    case "lobby": {
      const lowest = world.building.floors.reduce<WorldFloor | null>(
        (best, f) => (!best || f.number < best.number ? f : best), null);
      return drawLobby({ ...a, place: { kind: "floor", floor: lowest?.number } });
    }    case "floor": {
      const f = world.building.floors.find((x) => x.number === place.floor);
      if (!f) {
        // A FLOOR THE PAYLOAD DOES NOT HAVE. Say so; do not draw a guess.
        plate(a.ctx, 0, 0, a.width, a.height, PALETTE.void);
        text(a.ctx, "That floor is not in the tower report", a.width / 2,
             a.height / 2, 13, PALETTE.muted, "center");
        return;
      }
      return drawFloor(a, f);
    }
    case "room": {
      const f = world.building.floors.find((x) => x.number === place.floor);
      if (!f) {
        plate(a.ctx, 0, 0, a.width, a.height, PALETTE.void);
        text(a.ctx, "That floor is not in the tower report", a.width / 2,
             a.height / 2, 13, PALETTE.muted, "center");
        return;
      }
      return drawRoom(a, f);
    }
    default:
      plate(a.ctx, 0, 0, a.width, a.height, PALETTE.void);
      text(a.ctx, "Unknown place", a.width / 2, a.height / 2, 13, PALETTE.muted,
           "center");
  }
}
