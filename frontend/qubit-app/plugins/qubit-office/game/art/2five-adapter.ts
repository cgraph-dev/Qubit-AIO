/* The documented seam between the native renderer and the Stage 6 primitives.
 *
 * WHY THIS IS NOT MOUNTED IN THE LIVE OFFICE
 * ------------------------------------------
 * The live `/qubit-office` page mounts the VENDORED Habblaud client inside an
 * iframe (`game/app/office-app.ts`); Habblaud owns its canvas, camera,
 * simulation and painter order. The Qubit-native canvas renderer in this
 * package is only reachable with `?shell=1` for comparison. So there is no
 * additive production call site to wire without either patching Habblaud or
 * replacing the native shell's own furniture + figure pass.
 *
 * This adapter is the exact seam a future renderer uses if the owner chooses
 * one of those paths:
 *
 *   const cam = cameraTransform(cameraFor(isoFloor, width, height));
 *   drawStage6Room({
 *     ctx, recipe, origin: originForRoom(room), camera: cam, tokens,
 *     agents: liveAgents.map(a => ({ ...a, draw: () => paintFigure(...) })),
 *   });
 *
 * The live renderer remains authoritative for camera, agent coordinates and
 * movement; this module only turns those values into Stage 6 drawables. It
 * never selects a recipe for a live room: Stage 5's recipes describe fixed
 * 14x8 mockup rooms, while live rooms are payload-sized, so mapping one onto
 * the other needs an owner-approved layout decision rather than a guess.
 */

import type { IsoCamera, IsoRoom } from "./room";
import type { RoomRecipe } from "./2five-rooms";
import {
  createAgentDepthDrawable,
  drawDepthSorted,
  drawRoomFloor,
  drawRoomSurfaceRegions,
  drawRoomWalls,
  fittingDrawables,
  type CameraTransform,
  type OfficeTokens,
  type RoomOrigin,
} from "./2five-primitives";

/** An agent anchor supplied by the existing renderer, not by this module. */
export interface Stage6AgentAnchor {
  readonly agentId: number;
  readonly gx: number;
  readonly gy: number;
  readonly draw: () => void;
}

export interface Stage6RoomArgs {
  readonly ctx: CanvasRenderingContext2D;
  readonly recipe: RoomRecipe;
  readonly origin: RoomOrigin;
  readonly camera: CameraTransform;
  readonly tokens: OfficeTokens;
  readonly agents?: readonly Stage6AgentAnchor[];
  /** Pass true for a camera-facing foreground room to draw wall cutaways. */
  readonly foreground?: boolean;
}

export function originForRoom(room: IsoRoom): RoomOrigin {
  return { gx: room.x, gy: room.y };
}

export function cameraTransform(cam: IsoCamera): CameraTransform {
  return { offsetX: cam.ox, offsetY: cam.oy, zoom: cam.zoom };
}

/**
 * The complete Stage 6 pass order:
 * floor -> carpet/rug surfaces -> walls -> fittings + figures in one stable
 * depth pass. Labels stay DOM data; nothing here writes Canvas text.
 */
export function drawStage6Room(args: Stage6RoomArgs): void {
  const {
    ctx, recipe, origin, camera, tokens, agents = [], foreground = false,
  } = args;
  drawRoomFloor(ctx, recipe, origin, camera, tokens);
  drawRoomSurfaceRegions(ctx, recipe, origin, camera, tokens);
  drawRoomWalls(ctx, recipe, origin, camera, tokens, { foreground });

  const items = [
    ...fittingDrawables(ctx, recipe.fittings, origin, camera, tokens),
    ...agents.map((agent) => createAgentDepthDrawable(
      agent.gx, agent.gy, agent.draw)),
  ];
  drawDepthSorted(items);
}
