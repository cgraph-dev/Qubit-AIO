/*
 * Stage 5 / Stage 6 contract tests.
 *
 * The port is renderer-independent and deliberately NOT mounted by the live
 * Habblaud office. These tests pin the invariants the live renderer would rely
 * on if it ever adopts the seam: fixed 16/16/8 projection, token-only palette
 * resolution, stable painter order, one draw path per fitting type, and no
 * Canvas text, raster assets or private frame loop.
 */
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

import {
  RECIPES,
  type FittingType,
  type PaletteId,
  type RoomFitting,
  type RoomRecipe,
} from "./2five-rooms";
import {
  HALF_H,
  HALF_W,
  TILE,
  WALL_HEIGHT_TILES,
  createAgentDepthDrawable,
  depthOf,
  drawDepthSorted,
  drawFitting,
  drawRoomFloor,
  drawRoomSurfaceRegions,
  drawRoomWalls,
  fittingDepthItem,
  fittingDrawables,
  floorMaterialRamp,
  paletteRamp,
  projectGrid,
  wallMaterialRamp,
  type OfficeTokens,
} from "./2five-primitives";
import { cameraTransform, drawStage6Room, originForRoom } from "./2five-adapter";

const FITTING_TYPES_EXPECTED: readonly FittingType[] = [
  "longTable", "boardroomTable", "deskCluster", "receptionDesk",
  "barCounter", "shelf", "sink", "toilet", "stall", "sofa", "rug",
  "redCarpet", "lantern", "sakuraTree", "staircase", "sign", "plant",
  "monitor", "chair",
];

type Calls = string[];

class RecordingContext {
  readonly calls: Calls = [];
  fillStyle: unknown = "";
  strokeStyle: unknown = "";
  lineWidth = 1;
  lineJoin = "miter";
  lineCap = "butt";
  globalAlpha = 1;
  shadowColor = "";
  shadowBlur = 0;
  textCalls = 0;

  save(): void { this.calls.push("save"); }
  restore(): void { this.calls.push("restore"); }
  beginPath(): void { this.calls.push("beginPath"); }
  moveTo(): void { this.calls.push("moveTo"); }
  lineTo(): void { this.calls.push("lineTo"); }
  closePath(): void { this.calls.push("closePath"); }
  fill(): void { this.calls.push("fill"); }
  stroke(): void { this.calls.push("stroke"); }
  arc(): void { this.calls.push("arc"); }
  ellipse(): void { this.calls.push("ellipse"); }
  createRadialGradient() {
    return { addColorStop: () => undefined };
  }
  fillText(): void { this.textCalls += 1; }
  strokeText(): void { this.textCalls += 1; }
}

function context(): CanvasRenderingContext2D {
  return new RecordingContext() as unknown as CanvasRenderingContext2D;
}

function recording(ctx: CanvasRenderingContext2D): RecordingContext {
  return ctx as unknown as RecordingContext;
}

const ORIGIN = { gx: 0, gy: 0 };
const CAMERA = { offsetX: 0, offsetY: 0, zoom: 1 };

/** The Stage 1 token names the primitives consume, with real values. */
function tokens(): OfficeTokens {
  return {
    void: "#0E011B",
    ground: "#221E3D",
    panel: "#14081E",
    panelRaised: "#2C1637",
    header: "#4C0D27",
    paper: "#EBD2C3",
    wall: "#200A20",
    text: "#EBD2C3",
    muted: "#A2758F",
    dim: "#7B6A6A",
    onLight: "#14081E",
    accent: "#EB5086",
    accentHot: "#F54C86",
    accentViolet: "#761F77",
    accentVioletLight: "#5A38A1",
    gold: "#EFAF67",
    goldBright: "#FFE092",
    goldShadow: "#C18867",
    ok: "#89E581",
    warn: "#EFAF67",
    bad: "#B21832",
    border: "#5A1730",
    borderMuted: "#761F77",
    borderAccent: "#EB5086",
    borderGold: "#EFAF67",
    borderHighlight: "#FFE092",
  };
}

function fitting(
  type: FittingType,
  palette: PaletteId = "executive_warm",
): RoomFitting {
  return {
    id: `test-${type}`,
    type,
    gx: 1,
    gy: 1,
    w: 2,
    h: 1.5,
    z: 0,
    rotation: 0,
    palette,
  };
}

describe("Stage 6 projection", () => {
  it("keeps the fixed 16/16/8 isometric contract", () => {
    expect(TILE).toBe(16);
    expect(HALF_W).toBe(16);
    expect(HALF_H).toBe(8);
    expect(WALL_HEIGHT_TILES).toBe(2);
  });

  it("projects grid coordinates and height exactly", () => {
    expect(projectGrid({ gx: 2, gy: 3 }, ORIGIN, CAMERA))
      .toEqual({ x: -16, y: 40 });
    expect(projectGrid({ gx: 2, gy: 3, z: 1 }, ORIGIN, CAMERA))
      .toEqual({ x: -16, y: 24 });
  });

  it("adds room origin before projecting and applies camera after", () => {
    expect(projectGrid(
      { gx: 2, gy: 3 },
      { gx: 10, gy: 20 },
      { offsetX: 100, offsetY: 50, zoom: 2 },
    )).toEqual({ x: -11 * HALF_W * 2 + 100, y: 35 * HALF_H * 2 + 50 });
  });
});

describe("Stage 5 palette resolution", () => {
  const PALETTES: PaletteId[] = [
    "cool_teal_neutral", "executive_warm", "finance_navy",
    "creative_coral", "control_room_amber", "open_office_sand",
  ];
  const FLOORS = [
    "floor_wood_light", "floor_wood_dark", "floor_carpet_dark",
    "floor_tile_grey", "floor_concrete_polished", "floor_marble_warm",
  ] as const;
  const WALLS = [
    "wall_panel_light", "wall_plaster_cool", "wall_glass_partition",
    "wall_tech_panel", "wall_brick_painted", "wall_panel_walnut",
  ] as const;

  it("maps every semantic palette to the Stage 1 token families", () => {
    const t = tokens();
    expect(paletteRamp("cool_teal_neutral", t).base).toBe(t.panelRaised);
    expect(paletteRamp("executive_warm", t).base).toBe(t.goldShadow);
    expect(paletteRamp("finance_navy", t).base).toBe(t.accentViolet);
    expect(paletteRamp("creative_coral", t).base).toBe(t.accent);
    expect(paletteRamp("control_room_amber", t).base).toBe(t.gold);
    expect(paletteRamp("open_office_sand", t).base).toBe(t.paper);
    for (const id of PALETTES) {
      const ramp = paletteRamp(id, t);
      expect(ramp.accent).toMatch(/^#|^rgba\(/);
      expect(ramp.outline).toMatch(/^#|^rgba\(/);
      expect(ramp.glow).toBe(ramp.accent);
    }
  });

  it("resolves every floor and wall material without a literal palette", () => {
    const t = tokens();
    for (const material of FLOORS) {
      const ramp = floorMaterialRamp(material, t);
      expect(ramp.base).toMatch(/^#/);
      expect(ramp.shadow).toMatch(/^#/);
      expect(ramp.highlight).toMatch(/^#/);
    }
    for (const material of WALLS) {
      const ramp = wallMaterialRamp(material, t);
      expect(ramp.base).toMatch(/^#/);
      expect(ramp.deep).toMatch(/^#/);
      expect(ramp.outline).toMatch(/^#/);
    }
    expect(floorMaterialRamp("floor_tile_grey", t).base).toBe(t.ground);
    expect(wallMaterialRamp("wall_panel_light", t).base).toBe(t.panelRaised);
  });
});

describe("depth ordering", () => {
  it("sorts ascending by gx+gy and preserves source order on ties", () => {
    const order: string[] = [];
    drawDepthSorted([
      { gx: 2, gy: 2, draw: () => order.push("far") },
      { gx: 0, gy: 1, draw: () => order.push("near-a") },
      { gx: 1, gy: 0, draw: () => order.push("near-b") },
      { gx: 0, gy: 0, draw: () => order.push("back") },
    ]);
    expect(order).toEqual(["back", "near-a", "near-b", "far"]);
  });

  it("anchors a fitting at the centre of its camera-facing edge", () => {
    const item = fittingDepthItem(fitting("deskCluster"), () => undefined);
    expect(item.gx).toBe(1 + 2 / 2);
    expect(item.gy).toBe(1 + 1.5);
    expect(depthOf(item)).toBe(item.gx + item.gy);
  });

  it("keeps rugs in the surface pass and out of the fitting pass", () => {
    const ctx = context();
    const items = fittingDrawables(ctx, [
      fitting("rug"), fitting("redCarpet"), fitting("chair"),
    ], ORIGIN, CAMERA, tokens());
    expect(items).toHaveLength(1);
  });

  it("interleaves an agent with fittings by the same depth rule", () => {
    const order: string[] = [];
    drawDepthSorted([
      createAgentDepthDrawable(5, 5, () => order.push("agent")),
      { gx: 1, gy: 1, draw: () => order.push("desk") },
    ]);
    expect(order).toEqual(["desk", "agent"]);
  });
});

describe("fitting coverage", () => {
  it("draws every declared fitting type, and skips only the surface aliases", () => {
    const ctx = context();
    const t = tokens();
    for (const type of FITTING_TYPES_EXPECTED) {
      const rec = recording(ctx);
      const before = rec.calls.length;
      drawFitting(ctx, fitting(type), ORIGIN, CAMERA, t);
      const drawn = rec.calls.length - before;
      if (type === "rug" || type === "redCarpet") {
        expect(drawn).toBe(0);
      } else {
        expect(drawn).toBeGreaterThan(0);
      }
    }
  });

  it("covers every fitting type in the eight Stage 5 recipes", () => {
    const used = new Set<string>();
    for (const recipe of Object.values(RECIPES)) {
      for (const item of recipe.fittings) used.add(item.type);
    }
    expect([...used].sort()).toEqual([...FITTING_TYPES_EXPECTED].sort());
  });
});

describe("no Canvas text or raster assets", () => {
  function paintRecipe(recipe: RoomRecipe): RecordingContext {
    const ctx = context();
    const t = tokens();
    drawRoomFloor(ctx, recipe, ORIGIN, CAMERA, t);
    drawRoomSurfaceRegions(ctx, recipe, ORIGIN, CAMERA, t);
    drawRoomWalls(ctx, recipe, ORIGIN, CAMERA, t, { foreground: true });
    for (const item of recipe.fittings) {
      drawFitting(ctx, item, ORIGIN, CAMERA, t);
    }
    return recording(ctx);
  }

  it("paints the complete room pass without fillText/strokeText", () => {
    for (const recipe of Object.values(RECIPES)) {
      const rec = paintRecipe(recipe);
      expect(rec.textCalls).toBe(0);
      expect(rec.calls).toContain("fill");
    }
  });

  it("has no text, image, frame-loop or timer call in the ported sources", () => {
    for (const file of [
      new URL("./2five-rooms.ts", import.meta.url),
      new URL("./2five-primitives.ts", import.meta.url),
      new URL("./2five-adapter.ts", import.meta.url),
    ]) {
      const source = readFileSync(file, "utf8");
      expect(source).not.toMatch(/\bfillText\s*\(/);
      expect(source).not.toMatch(/\bstrokeText\s*\(/);
      expect(source).not.toMatch(/\bdrawImage\s*\(/);
      expect(source).not.toMatch(/\bnew\s+Image\b/);
      expect(source).not.toMatch(/\brequestAnimationFrame\s*\(/);
      expect(source).not.toMatch(/\bsetInterval\s*\(/);
    }
  });
});

describe("adapter seam", () => {
  it("converts the native camera and room origin without owning either", () => {
    expect(originForRoom({ id: 1, slug: "work", kind: "work", x: 4, y: 6, w: 3, h: 2, occupants: [] }))
      .toEqual({ gx: 4, gy: 6 });
    expect(cameraTransform({ ox: 100, oy: 50, zoom: 2 }))
      .toEqual({ offsetX: 100, offsetY: 50, zoom: 2 });
  });

  it("runs the full pass and calls an agent drawable supplied by the renderer", () => {
    const ctx = context();
    let agentDraws = 0;
    drawStage6Room({
      ctx,
      recipe: RECIPES.reception,
      origin: ORIGIN,
      camera: CAMERA,
      tokens: tokens(),
      foreground: true,
      agents: [{
        agentId: 7,
        gx: 4,
        gy: 3,
        draw: () => { agentDraws += 1; },
      }],
    });
    expect(agentDraws).toBe(1);
    expect(recording(ctx).calls).toContain("fill");
  });
});
