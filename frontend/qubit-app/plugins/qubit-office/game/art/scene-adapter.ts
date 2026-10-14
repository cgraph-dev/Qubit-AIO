/* Wire the tower world into the isometric renderer.
 *
 * The two shapes look similar and are not the same thing:
 *
 *   WorldFloor / WorldRoom    — the ASSEMBLED world (WorldAssembler). Nested
 *                               characters, camelCase, an explicit
 *                               `placeholder` flag.
 *   IsoFloor / IsoRoom        — the RENDERER's view (art/room). Flat occupant
 *                               rows, only the fields a painter needs.
 *
 * Keeping them apart is deliberate. The renderer should not know about
 * placeholder flags or memory surfaces, and the assembler should not know about
 * tile diamonds. This module is the single place the two vocabularies meet, so
 * a change to either side breaks HERE and is obvious, rather than corrupting
 * the other silently.
 *
 * The one rule carried across: a floor, a room and a person exist in the render
 * because the PAYLOAD put them there. Nothing is filled in to make a scene look
 * busier.
 */

import type { WorldBuilding, WorldFloor } from "../WorldAssembler";
import type { IsoFloor, IsoOccupant, IsoRoom } from "./room";

/** The payload's tile grid. The floorplan may describe a bigger grid than the
 *  rooms occupy, so this is carried through rather than assumed. */
export interface GridSize {
  cols: number;
  rows: number;
}

function occupantsOf(room: WorldFloor["rooms"][number]): IsoOccupant[] {
  // `WorldRoom.occupants` carries the PAYLOAD rows (snake_case `agent_id`), not
  // the assembled `WorldCharacter` — so the field name here is `agent_id`, and
  // getting that wrong is a compile error rather than a silent undefined.
  return room.occupants.map((c) => ({
    agentId: c.agent_id,
    slug: c.slug,
    animation: String(c.animation),
    stalled: Boolean(c.stalled),
  }));
}

/**
 * One floor of the building, ready to paint.
 *
 * Rooms with no occupants still come through: an empty room is a real state
 * and drawing it empty is the honest rendering. Dropping it would make a floor
 * look smaller than the payload says it is.
 */
export function toIsoFloor(floor: WorldFloor): IsoFloor {
  const rooms: IsoRoom[] = floor.rooms.map((r) => ({
    id: r.id,
    slug: r.slug,
    kind: r.kind,
    x: r.x,
    y: r.y,
    w: Math.max(1, r.w),
    h: Math.max(1, r.h),
    occupants: occupantsOf(r),
  }));
  return {
    number: floor.number,
    name: floor.name,
    role: floor.role,
    rooms,
  };
}

/** The floor the LAYOUT should be drawn for, given a place. */
export function floorForPlace(
  building: WorldBuilding, floorNumber: number | undefined,
): WorldFloor | null {
  if (floorNumber === undefined) {
    // No floor named: the lowest is the ground floor, which is what "the
    // building" means when nothing narrower is specified. Not a guess about
    // the data — the data is already here and this is which slice of it shows.
    return building.floors.reduce<WorldFloor | null>(
      (best, f) => (!best || f.number < best.number ? f : best), null);
  }
  return building.floors.find((f) => f.number === floorNumber) ?? null;
}

/** How many rooms the grid actually needs, so the camera can frame it. */
export function gridExtent(floor: IsoFloor): { cols: number; rows: number } {
  let cols = 1;
  let rows = 1;
  for (const r of floor.rooms) {
    cols = Math.max(cols, r.x + r.w);
    rows = Math.max(rows, r.y + r.h);
  }
  return { cols, rows };
}
