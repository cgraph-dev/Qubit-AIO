/* F07 — the world assembler: authoritative payload -> a drawable world.
 *
 * The slice needs something to walk around in. This turns the REAL tower
 * payload (see `adapter.ts` / the recorded fixture) into a world model, and it
 * does so with one rule that matters more than the rest:
 *
 *   **The world is DERIVED from the payload, never invented from it.**
 *
 * A floor exists because the payload has it. A room exists because the payload
 * has it. A character exists because the payload has it, standing where the
 * payload says — this file NEVER spawns a person to make a room look busy. That
 * is C7 applied to world-building: an office that invents inhabitants is a
 * decoration, and the entire product claim is that it is an instrument.
 *
 * Where the payload is silent (an exterior facade, a lobby that the tower model
 * has no concept of), the world marks those places as SCAFFOLD with an explicit
 * `placeholder: true`. Scaffold is honest as long as it is labelled; the failure
 * would be passing a drawing off as data.
 */

import type { OfficeCharacter, OfficeTowerPayload } from "./types";

export interface WorldRoom {
  id: number;
  slug: string;
  kind: string;
  /** Tile-space bounds from the payload. */
  x: number;
  y: number;
  w: number;
  h: number;
  /** Characters the PAYLOAD places in this room. Never synthesised. */
  occupants: OfficeCharacter[];
  /** True for rooms the renderer dresses but the payload does not describe. */
  placeholder: boolean;
}

export interface WorldFloor {
  number: number;
  name: string;
  role: string;
  z: number;
  rooms: WorldRoom[];
}

export interface WorldCharacter {
  agentId: number;
  slug: string;
  seatType: string;
  animation: OfficeCharacter["animation"];
  walking: boolean;
  facing: OfficeCharacter["facing"];
  stalled: boolean;
  /** The room the payload seats them in, by id. */
  roomId: number | null;
  /** Tile position within the floor they are on. */
  x: number;
  y: number;
  /** True when the payload gave no seat — surfaced, never hidden. */
  unseated: boolean;
}

export interface WorldBuilding {
  slug: string;
  name: string;
  floors: WorldFloor[];
  characters: WorldCharacter[];
  /** True when the whole building is scaffold (no payload at all). */
  placeholder: boolean;
}

export interface OfficeWorld {
  revision: number;
  building: WorldBuilding;
  /** The payload's own statement of the animation rule, carried not paraphrased. */
  motionRule: string;
  /** The payload's own empty statement. An empty office says so. */
  empty: boolean;
  emptyReason: string;
  preview: boolean;
  grid: { cols: number; rows: number; tile: number };
}

/** The slug the single-headquarters preview uses. Multi-building is F11. */
export const HQ_SLUG = "hq";

/**
 * Build the world. Pure: same payload in, same world out, no clock, no random.
 */
export function assembleWorld(
  payload: OfficeTowerPayload | null,
  revision: number,
): OfficeWorld {
  if (!payload) {
    // NO PAYLOAD IS NOT AN EMPTY OFFICE. Before the first read succeeds there
    // is no world to draw, and saying so is the honest thing; drawing an
    // invented office is the lie C7 forbids.
    return {
      revision,
      building: {
        slug: HQ_SLUG,
        name: "Headquarters",
        floors: [],
        characters: [],
        placeholder: true,
      },
      motionRule: "",
      empty: false,
      emptyReason: "",
      preview: false,
      grid: { cols: 39, rows: 16, tile: 1 },
    };
  }

  const floors: WorldFloor[] = (payload.floorplan?.floors ?? []).map((f) => {
    const rooms: WorldRoom[] = (f.rooms ?? []).map((r) => ({
      id: r.id,
      slug: r.slug,
      kind: r.kind,
      x: r.x,
      y: r.y,
      w: r.w,
      h: r.h,
      occupants: (payload.characters ?? []).filter(
        (c) => c.position?.room_id === r.id,
      ),
      // The payload describes the room, so it is not scaffold. A room the
      // renderer ADDS (a corridor, a stairwell) would be.
      placeholder: false,
    }));
    return {
      number: f.number,
      name: f.name,
      role: f.role,
      z: Number(f.z || 0),
      rooms,
    };
  });

  const characters: WorldCharacter[] = (payload.characters ?? []).map((c) => {
    const seated = c.seated !== false && c.position?.room_id !== undefined
      && c.position?.room_id !== null;
    return {
      agentId: c.agent_id,
      slug: c.slug,
      seatType: c.seat_type,
      animation: c.animation,
      walking: !!c.walking,
      facing: c.facing,
      stalled: !!c.stalled,
      roomId: seated ? Number(c.position.room_id) : null,
      x: Number(c.position?.x ?? 0),
      y: Number(c.position?.y ?? 0),
      unseated: !seated,
    };
  });

  return {
    revision,
    building: {
      slug: HQ_SLUG,
      // The tower payload has no building NAME, so the label is scaffold. The
      // floors, rooms and people are not.
      name: "Headquarters",
      floors,
      characters,
      placeholder: floors.length === 0,
    },
    motionRule: String(payload.motion_rule ?? ""),
    empty: !!payload.empty,
    emptyReason: String(payload.empty_reason ?? ""),
    preview: !!payload.preview,
    grid: payload.floorplan?.grid ?? { cols: 39, rows: 16, tile: 1 },
  };
}

/** The characters the payload places on one floor. */
export function charactersOnFloor(
  world: OfficeWorld,
  floorNumber: number,
): WorldCharacter[] {
  const floor = world.building.floors.find((f) => f.number === floorNumber);
  if (!floor) return [];
  const roomIds = new Set(floor.rooms.map((r) => r.id));
  return world.building.characters.filter(
    (c) => c.roomId !== null && roomIds.has(c.roomId),
  );
}

/** The characters the payload could not seat. Surfaced, never dropped. */
export function unseatedCharacters(world: OfficeWorld): WorldCharacter[] {
  return world.building.characters.filter((c) => c.unseated);
}
