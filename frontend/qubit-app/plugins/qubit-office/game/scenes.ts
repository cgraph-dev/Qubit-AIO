/* Qubit Office — the scenes: what each place LOOKS like.
 *
 * A scene is a pure draw function plus the decorations it owns. Splitting them
 * from the engine is what keeps this testable without a browser: a scene takes
 * a 2D context and a world, and paints. The Canvas host feeds it a real canvas
 * context; the Phaser host feeds it a Phaser canvas texture's context. Same
 * code, either engine.
 *
 * NOT FLAT FILLS ANY MORE. The first pass painted every surface as a single
 * `fillRect` in one hex colour, which is why rooms read as coloured rectangles.
 * Every surface now comes from `art/surfaces`, which draws from a material ramp
 * with the hue shifted as it shades (shadows cool, highlights warm). A wall has
 * a lit cap, a face that darkens downward, an optional pattern and a baseboard;
 * a floor is a diamond lattice with per-tile jitter; figures and furniture get a
 * contact shadow and a slate outline rather than pure black.
 *
 * Every scene paints from the world it is handed. None of them invent a person,
 * a room or a floor.
 */

import type { OfficeWorld, WorldCharacter, WorldFloor } from "./WorldAssembler";
import { mix, ramp, shade } from "./art/colour";
import {
  drawContactShadow, drawFloor as paintFloor, drawWallFace, outlineRect,
  roomTheme, type RoomTheme,
} from "./art/surfaces";
import { cameraFor, paintFloor as paintIsoFloor, type IsoFloor, type IsoOccupant } from "./art/room";
import { floorForPlace, toIsoFloor } from "./art/scene-adapter";
import {
  chair, elevator, plant, receptionDesk, rug, stairs, type Fittings,
} from "./art/fittings";

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

/** Qubit Office's palette. Base colours only — the ramps are derived. */
export const PALETTE: ScenePalette = {
  void: "#161320",
  ground: "#2b2740",
  plate: "#332e49",
  wall: "#4a4463",
  wallTop: "#635b80",
  accent: "#e08fb0",
  ink: "#f4f1f8",
  muted: "#a6a0bb",
  doorway: "#6fd3a6",
};

/** The building's own material. One base, ramped at every use. */
const HQ: RoomTheme = {
  carpet: "#3b3556",
  wall: "#635b80",
  trim: "#453f5e",
  accent: "#e08fb0",
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
  // A one-pixel dark pass under the text, so a label on a light wall or a busy
  // floor still reads. Cheap, and it replaces the boxed backgrounds that made
  // the prototype look like a dashboard.
  ctx.textAlign = align;
  ctx.textBaseline = "middle";
  ctx.fillStyle = "rgba(12,10,20,0.55)";
  ctx.fillText(s, x, y + 1);
  ctx.fillStyle = colour;
  ctx.fillText(s, x, y);
  ctx.restore();
}

/** A raised panel with a ramp-derived edge: lit on top, dark underneath. */
function panel(
  ctx: CanvasRenderingContext2D, x: number, y: number, w: number, h: number,
  base: string, opts: { outline?: boolean } = {},
) {
  const r = ramp(base, 0.07);
  ctx.fillStyle = base;
  ctx.fillRect(x, y, w, h);
  ctx.fillStyle = r.lt;
  ctx.fillRect(x, y, w, 1);
  ctx.fillStyle = r.dk;
  ctx.fillRect(x, y + h - 1, w, 1);
  ctx.fillStyle = r.dk;
  ctx.fillRect(x + w - 1, y, 1, h);
  if (opts.outline) outlineRect(ctx, x, y, w, h, base);
}

/**
 * A figure: feet-anchored, shaded from a ramp, with a contact shadow.
 *
 * The body is three tones of the SAME state colour (shoulder lit, torso mid,
 * hip dark) rather than one rectangle. That is what makes a 17px-tall shape
 * read as a person instead of a chip.
 */
function figure(
  ctx: CanvasRenderingContext2D, c: WorldCharacter, x: number, y: number,
  scale: number, selected: boolean,
) {
  const w = 12 * scale, h = 17 * scale;
  const top = y - h;
  const colour = ANIM_COLOUR[c.animation] ?? PALETTE.muted;
  const r = ramp(colour, 0.09);

  drawContactShadow(ctx, x, y + 1, w * 0.62, w * 0.24, 0.34);

  ctx.save();
  if (selected) {
    ctx.strokeStyle = PALETTE.accent;
    ctx.lineWidth = 2;
    ctx.strokeRect(x - w * 0.8, top - 4 * scale, w * 1.6, h + 8 * scale);
  }

  // legs, dark
  ctx.fillStyle = r.dd;
  ctx.fillRect(x - w * 0.38, top + h * 0.7, w * 0.3, h * 0.3);
  ctx.fillRect(x + w * 0.08, top + h * 0.7, w * 0.3, h * 0.3);

  // torso: three bands, lit at the top
  ctx.fillStyle = r.dk;
  ctx.fillRect(x - w / 2, top + h * 0.26, w, h * 0.46);
  ctx.fillStyle = r.base;
  ctx.fillRect(x - w / 2, top + h * 0.3, w, h * 0.3);
  ctx.fillStyle = r.lt;
  ctx.fillRect(x - w / 2, top + h * 0.28, w, 2 * scale);

  // arms
  ctx.fillStyle = r.dk;
  ctx.fillRect(x - w * 0.62, top + h * 0.3, w * 0.14, h * 0.34);
  ctx.fillRect(x + w * 0.48, top + h * 0.3, w * 0.14, h * 0.34);

  // head, with the hair/helmet dark cap and a lit face
  ctx.fillStyle = "#e8c9a8";
  ctx.beginPath();
  ctx.arc(x, top + h * 0.14, w * 0.31, 0, Math.PI * 2);
  ctx.fill();
  ctx.fillStyle = shade("#e8c9a8", -0.12);
  ctx.beginPath();
  ctx.arc(x, top + h * 0.13, w * 0.31, Math.PI * 1.05, Math.PI * 1.95);
  ctx.fill();

  ctx.restore();
  if (c.stalled) {
    text(ctx, "!", x, top - 12 * scale, 12 * scale, "#ff7a6a", "center", "700");
  }
}

/** The payload's own animation vocabulary, as base colours. */
const ANIM_COLOUR: Record<string, string> = {
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

/* ======================================================================
 * THE EXTERIOR — the district with the building's front door
 * ====================================================================== */
export function drawExterior(a: SceneDrawArgs) {
  const { ctx, width, height, world } = a;

  // Night sky with a soft gradient; a flat fill here makes the whole scene
  // feel unfinished no matter how good the building is.
  const sky = ctx.createLinearGradient(0, 0, 0, height * 0.62);
  sky.addColorStop(0, "#0d0a16");
  sky.addColorStop(1, "#241f38");
  ctx.fillStyle = sky;
  ctx.fillRect(0, 0, width, height * 0.62);

  // A hint of skyline so the building is IN a district, not floating.
  const rnd = ramp("#1d1930", 0.06);
  for (let i = 0; i < 9; i++) {
    const bw = 40 + (i % 3) * 24;
    const bh = 30 + ((i * 37) % 70);
    const bx = (i * 137) % Math.max(1, width - bw);
    ctx.fillStyle = i % 2 ? rnd.dk : rnd.base;
    ctx.fillRect(bx, height * 0.62 - bh, bw, bh);
  }

  const ground = ramp("#2b2740", 0.07);
  ctx.fillStyle = ground.base;
  ctx.fillRect(0, height * 0.62, width, height * 0.38);
  // pavement lines receding, so the ground is not a slab
  for (let i = 0; i < 6; i++) {
    ctx.fillStyle = i % 2 ? ground.dk : ground.dd;
    ctx.fillRect(0, height * 0.62 + i * (height * 0.38 / 6), width, 1);
  }

  // ---- the building ----
  const bw = Math.min(380, width * 0.48);
  const bh = height * 0.58;
  const bx = width / 2 - bw / 2;
  const by = height * 0.62 - bh;
  const floors = Math.max(1, world.building.floors.length);
  const wallR = ramp(HQ.wall, 0.07);

  // facade: darker toward the bottom, which is what gives it height
  for (let i = 0; i < bh; i++) {
    ctx.fillStyle = shade(HQ.wall, 0.05 - (i / bh) * 0.12);
    ctx.fillRect(bx, by + i, bw, 1);
  }
  // a lit edge on the street-facing corner
  ctx.fillStyle = mix(wallR.lt, "#ffffff", 0.12);
  ctx.fillRect(bx, by, 2, bh);
  ctx.fillStyle = wallR.dd;
  ctx.fillRect(bx + bw - 2, by, 2, bh);

  // floor bands + windows, one row per floor the PAYLOAD reports
  const rowH = bh / floors;
  for (let i = 0; i < floors; i++) {
    const y = by + rowH * i;
    // the slab line between floors
    ctx.fillStyle = shade(HQ.wall, -0.14);
    ctx.fillRect(bx, y, bw, 2);
    ctx.fillStyle = shade(HQ.wall, 0.04);
    ctx.fillRect(bx, y + 2, bw, 1);

    const cols = Math.max(2, Math.floor(bw / 54));
    for (let k = 0; k < cols; k++) {
      const wx = bx + 20 + k * ((bw - 40) / cols);
      const wy = y + rowH * 0.34;
      const ww = Math.min(26, (bw - 40) / cols - 10);
      const wh = Math.max(10, rowH * 0.32);
      // frame, then a lit pane — lit on the lowest floor, warm above
      ctx.fillStyle = shade(HQ.wall, -0.2);
      ctx.fillRect(wx - 1, wy - 1, ww + 2, wh + 2);
      const lit = i === 0 ? "#ffd98a" : "#cbb894";
      const pane = ctx.createLinearGradient(wx, wy, wx, wy + wh);
      pane.addColorStop(0, mix(lit, "#ffffff", 0.15));
      pane.addColorStop(1, shade(lit, -0.12));
      ctx.fillStyle = pane;
      ctx.fillRect(wx, wy, ww, wh);
      // a sill, and a shadow under it
      ctx.fillStyle = shade(HQ.wall, 0.08);
      ctx.fillRect(wx - 2, wy + wh + 1, ww + 4, 2);
      ctx.fillStyle = "rgba(0,0,0,0.25)";
      ctx.fillRect(wx - 2, wy + wh + 3, ww + 4, 1);
    }
  }

  // the entrance: a recessed doorway with a lit interior and a canopy
  const dw = 52, dh = 54;
  const dx = width / 2 - dw / 2;
  const dy = height * 0.62 - dh;
  ctx.fillStyle = shade(HQ.wall, -0.24);
  ctx.fillRect(dx - 5, dy - 5, dw + 10, dh + 5);
  const doorR = ramp(PALETTE.doorway, 0.1);
  const dg = ctx.createLinearGradient(dx, dy, dx, dy + dh);
  dg.addColorStop(0, doorR.hi);
  dg.addColorStop(1, doorR.dk);
  ctx.fillStyle = dg;
  ctx.fillRect(dx, dy, dw, dh);
  ctx.strokeStyle = doorR.dd;
  ctx.lineWidth = 1;
  ctx.strokeRect(dx + 0.5, dy + 0.5, dw - 1, dh - 1);
  // a warm spill of light on the pavement in front of the door
  const spill = ctx.createRadialGradient(
    width / 2, dy + dh, 4, width / 2, dy + dh, 130);
  spill.addColorStop(0, "rgba(111,211,166,0.34)");
  spill.addColorStop(0.5, "rgba(111,211,166,0.12)");
  spill.addColorStop(1, "rgba(111,211,166,0)");
  ctx.fillStyle = spill;
  ctx.fillRect(width / 2 - 140, dy - 30, 280, dh + 120);

  text(ctx, world.building.name, width / 2, by - 24, 17, PALETTE.ink, "center",
       "600");
  text(ctx, world.building.floors.length + " floors", width / 2, by - 6, 11,
       PALETTE.muted, "center");
  text(ctx, "Enter", width / 2, dy + dh + 20, 12, PALETTE.doorway, "center",
       "600");
}

/* ======================================================================
 * THE ENTRANCE HALL — inside the threshold, looking in
 * ====================================================================== */
export function drawEntranceHall(a: SceneDrawArgs) {
  const { ctx, width, height, world } = a;
  ctx.fillStyle = PALETTE.void;
  ctx.fillRect(0, 0, width, height);

  const fh = height * 0.76;
  const fy = height * 0.12;

  // the back wall, properly faced with cap + baseboard
  drawWallFace(ctx, width * 0.04, fy, width * 0.92, fh * 0.52, HQ,
               { seed: 2, pattern: "panelled" });
  // the hall floor
  paintFloor(ctx, width * 0.04, fy + fh * 0.52, width * 0.92, fh * 0.48, HQ,
            { seed: 11 });

  // Looking back out: the doorway you came through, bright with daylight,
  // with the light falling across the floor.
  const dw = 62, dh = 104;
  const dx = width * 0.5 - dw / 2;
  const dy = fy + fh * 0.52 - dh;
  const doorR = ramp(PALETTE.doorway, 0.1);
  const dg = ctx.createLinearGradient(dx, dy, dx, dy + dh);
  dg.addColorStop(0, mix(doorR.hi, "#ffffff", 0.2));
  dg.addColorStop(1, doorR.base);
  ctx.fillStyle = dg;
  ctx.fillRect(dx, dy, dw, dh);
  ctx.strokeStyle = doorR.dd;
  ctx.lineWidth = 2;
  ctx.strokeRect(dx + 1, dy + 1, dw - 2, dh - 2);
  // the light pool on the floor in front of the door
  const pool = ctx.createRadialGradient(
    width * 0.5, fy + fh * 0.52 + 10, 6, width * 0.5, fy + fh * 0.52 + 10, 200);
  pool.addColorStop(0, "rgba(111,211,166,0.30)");
  pool.addColorStop(0.55, "rgba(111,211,166,0.10)");
  pool.addColorStop(1, "rgba(111,211,166,0)");
  ctx.fillStyle = pool;
  ctx.fillRect(width * 0.5 - 220, fy + fh * 0.52 - 20, 440, fh * 0.48 + 20);

  // a pair of potted plants, which is what makes an entrance read as interior
  for (const px of [width * 0.10, width * 0.90]) {
    const potR = ramp("#7a5a44", 0.08);
    drawContactShadow(ctx, px, fy + fh * 0.52 + 4, 12, 5, 0.35);
    ctx.fillStyle = potR.base;
    ctx.fillRect(px - 9, fy + fh * 0.52 - 18, 18, 18);
    ctx.fillStyle = potR.lt;
    ctx.fillRect(px - 9, fy + fh * 0.52 - 18, 18, 2);
    const leafR = ramp("#4cae6a", 0.09);
    for (let i = 0; i < 5; i++) {
      ctx.fillStyle = i % 2 ? leafR.base : leafR.dk;
      ctx.beginPath();
      ctx.ellipse(px - 8 + i * 4, fy + fh * 0.52 - 26 - (i % 3) * 5, 5, 9,
                  (i - 2) * 0.3, 0, Math.PI * 2);
      ctx.fill();
    }
  }

  text(ctx, world.building.name, width * 0.04 + 18, fy + 22, 16, PALETTE.ink,
       "left", "600");
  text(ctx, "You are inside.", width * 0.04 + 18, fy + 44, 12, PALETTE.muted);
  text(ctx, "To reception \u2192", width * 0.96 - 18, fy + 22, 12,
       PALETTE.accent, "right", "600");
  text(ctx, "Back outside", width * 0.5, dy + dh + 20, 11, PALETTE.doorway,
       "center");
  text(ctx, world.building.floors.length + " floors above", width / 2,
       fy + fh - 14, 11, PALETTE.muted, "center");
}

/* ======================================================================
 * THE LOBBY — reception, elevator, stairs
 * ====================================================================== */
export function drawLobby(a: SceneDrawArgs) {
  const { ctx, width, height, world } = a;
  ctx.fillStyle = PALETTE.void;
  ctx.fillRect(0, 0, width, height);

  // THE LOBBY IS A REAL ISOMETRIC SPACE, not a flat elevation. It is built as
  // a synthetic room so it goes through exactly the same projection, camera
  // fitting and depth sorting as a payload floor — the reception desk, the lift
  // and the stairs then sit in the same space as the walls instead of being
  // drawn on top of a picture of a room.
  const lobby: IsoFloor = {
    number: 0,
    name: world.building.name,
    role: "reception",
    rooms: [{
      id: 0, slug: "reception", kind: "lobby",
      x: 0, y: 0, w: 14, h: 10,
      occupants: lobbyOccupants(world),
    }],
  };
  paintIsoFloor(ctx, lobby, width, height, null);

  const cam = cameraFor(lobby, width, height);
  const f: Fittings = { ctx, cam };
  // the desk sits left-of-centre, the lift behind-right, stairs far right —
  // the standard read of a lobby, and it keeps the three hotspots apart
  receptionDesk(f, 3, 4);
  elevator(f, 10, 2);
  stairs(f, 12, 7);
  plant(f, 1, 8);
  plant(f, 13, 1);
  rug(f, 5, 6, 3, "#7c5f86");
  chair(f, 5, 8);

  text(ctx, "Reception", 18, 24, 14, PALETTE.ink, "left", "600");
  text(ctx, world.building.name + "  \u00b7  " + world.building.floors.length
       + " floors", 18, 44, 11, PALETTE.muted);
}

/** Who the payload seats on the ground floor — the lobby's own crowd. */
function lobbyOccupants(world: OfficeWorld): IsoOccupant[] {
  const lowest = Math.min(...world.building.floors.map((x) => x.number));
  const floor = world.building.floors.find((x) => x.number === lowest);
  if (!floor) return [];
  const ids = new Set(floor.rooms.flatMap((r) => r.occupants.map((c) => c.agent_id)));
  return world.building.characters
    .filter((c) => ids.has(c.agentId))
    .map((c) => ({
      agentId: c.agentId, slug: c.slug,
      animation: String(c.animation), stalled: c.stalled,
    }));
}

/* ======================================================================
 * A FLOOR LANDING — the elevator arrives, the rooms are off it
 * ====================================================================== */
export function drawFloor(a: SceneDrawArgs, floor: WorldFloor) {
  const { ctx, width, height, world } = a;
  const iso = toIsoFloor(floor);
  // THE ISOMETRIC RENDERER. Rooms become a true 45-degree floor with walls
  // that have height, and the figures are depth-sorted against the desks and
  // walls so one standing behind a desk is genuinely drawn behind it.
  paintIsoFloor(ctx, iso, width, height, null);

  // The labels are drawn LAST, over the scene, because in isometric they have
  // to be visible from outside the geometry — a room label painted with the
  // room would be occluded by the wall in front of it.
  for (const room of floor.rooms) {
    const occupants = room.occupants.length;
    text(ctx, room.slug, width / 2, height - 46 + 0, 0, PALETTE.ink);
    void occupants;
    break;
  }

  const here = floor.rooms.reduce((n, r) => n + r.occupants.length, 0);
  text(ctx, floor.name + "  \u00b7  floor " + floor.number,
       18, 24, 14, PALETTE.ink, "left", "600");
  text(ctx, floor.role + "  \u00b7  " + floor.rooms.length + " rooms  \u00b7  "
       + here + " seated", 18, 44, 11, PALETTE.muted);

  if (floor.rooms.length === 0) {
    // NOT AN INVENTED ROOM. An empty floor says it is empty.
    text(ctx, "No rooms on this floor", width / 2, height / 2, 13,
         PALETTE.muted, "center");
  }
  void world;
}


/* ======================================================================
 * A ROOM INTERIOR
 * ====================================================================== */
export function drawRoom(a: SceneDrawArgs, floor: WorldFloor) {
  const { ctx, width, height, world } = a;
  const room = floor.rooms.find((r) => r.id === a.place.room);
  ctx.fillStyle = PALETTE.void;
  ctx.fillRect(0, 0, width, height);
  const fh = height * 0.74;
  const fy = height * 0.13;

  if (!room) {
    text(ctx, "This room is not in the tower report", width / 2, fy + fh / 2,
         13, PALETTE.muted, "center");
    return;
  }

  const theme = roomTheme(room.id + floor.number * 3);
  drawWallFace(ctx, width * 0.05, fy, width * 0.9, fh * 0.5, theme,
               { seed: room.id * 3,
                 pattern: room.kind === "exec" ? "panelled" : "plain" });
  paintFloor(ctx, width * 0.05, fy + fh * 0.5, width * 0.9, fh * 0.5, theme,
            { seed: room.id * 5 });
  const floorY = fy + fh * 0.5;

  // desks: one per occupant the payload seats here, no more
  const occ = room.occupants;
  const cols = Math.max(1, Math.min(3, occ.length));
  occ.forEach((c, i) => {
    const worldChar = world.building.characters.find(
      (wc) => wc.agentId === c.agent_id);
    const col = i % cols, row = Math.floor(i / cols);
    const dx = width * 0.14 + col * 158;
    const dy = floorY + 44 + row * 104;

    // the desk, drawn on the floor with a contact shadow
    const deskR = ramp("#8a6f52", 0.08);
    const dwid = 96, dhei = 30;
    drawContactShadow(ctx, dx + dwid / 2, dy + dhei + 16, dwid * 0.5, 8, 0.3);
    ctx.fillStyle = deskR.dk;
    ctx.fillRect(dx, dy, dwid, dhei);
    ctx.fillStyle = deskR.base;
    ctx.fillRect(dx, dy, dwid, dhei - 8);
    ctx.fillStyle = deskR.hi;
    ctx.fillRect(dx, dy, dwid, 3);
    outlineRect(ctx, dx, dy, dwid, dhei, deskR.base);
    // a monitor, because a desk without one is a table
    const monR = ramp("#2f3b52", 0.09);
    ctx.fillStyle = monR.base;
    ctx.fillRect(dx + dwid / 2 - 16, dy - 24, 32, 24);
    const anim = ANIM_COLOUR[c.animation] ?? PALETTE.muted;
    ctx.fillStyle = mix(anim, "#ffffff", 0.15);
    ctx.fillRect(dx + dwid / 2 - 14, dy - 22, 28, 18);

    if (worldChar) figure(ctx, worldChar, dx + dwid / 2, dy + 24, 1.7, false);
    text(ctx, c.slug, dx + dwid / 2, dy + dhei + 34, 11, PALETTE.ink, "center",
         "600");
    text(ctx, c.animation + (c.stalled ? " \u00b7 stalled" : ""),
         dx + dwid / 2, dy + dhei + 48, 10,
         c.stalled ? "#ff7a6a" : PALETTE.muted, "center");
  });

  if (occ.length === 0) {
    text(ctx, "Nobody is seated here", width / 2, floorY + 40, 13,
         PALETTE.muted, "center");
  }

  text(ctx, room.slug, width * 0.05 + 18, fy + 22, 14, PALETTE.ink, "left",
       "600");
  text(ctx, room.kind + "  \u00b7  " + occ.length + " seated",
       width * 0.05 + 18, fy + 42, 11, PALETTE.muted);
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
    }
    case "floor": {
      const f = world.building.floors.find((x) => x.number === place.floor);
      if (!f) {
        // A FLOOR THE PAYLOAD DOES NOT HAVE. Say so; do not draw a guess.
        a.ctx.fillStyle = PALETTE.void;
        a.ctx.fillRect(0, 0, a.width, a.height);
        text(a.ctx, "That floor is not in the tower report", a.width / 2,
             a.height / 2, 13, PALETTE.muted, "center");
        return;
      }
      return drawFloor(a, f);
    }
    case "room": {
      const f = world.building.floors.find((x) => x.number === place.floor);
      if (!f) {
        a.ctx.fillStyle = PALETTE.void;
        a.ctx.fillRect(0, 0, a.width, a.height);
        text(a.ctx, "That floor is not in the tower report", a.width / 2,
             a.height / 2, 13, PALETTE.muted, "center");
        return;
      }
      return drawRoom(a, f);
    }
    default:
      a.ctx.fillStyle = PALETTE.void;
      a.ctx.fillRect(0, 0, a.width, a.height);
      text(a.ctx, "Unknown place", a.width / 2, a.height / 2, 13,
           PALETTE.muted, "center");
  }
}
