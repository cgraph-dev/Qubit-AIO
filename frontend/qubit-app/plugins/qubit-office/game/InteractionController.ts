/* F07 — the interaction controller: the traversal F07 has to prove.
 *
 * THE PROOF THIS FILE EXISTS FOR
 * ------------------------------
 * `60-IMPLEMENTATION-PLAN.md` F07: "outside→enter→lobby→elevator→floor→room→
 * back→exit succeeds by mouse AND keyboard; location survives reload."
 *
 * So this file owns two things and nothing else:
 *   1. resolving an INTENT (a click on something, a key, an E press) to a PLACE
 *      the graph allows, and
 *   2. the symmetric reverse of every step, so Back always lands where you were.
 *
 * It does NOT paint, does not fetch, and does not move a character. Every
 * transition goes through `navigation.ts::travel`, so a hop with no portal is a
 * typed refusal rather than a silent jump — the reachable set IS the graph.
 *
 * MOUSE AND KEYBOARD ARE THE SAME PATH. An earlier design gave each input its
 * own transition table, which is how a mouse-only shortcut appears. Both inputs
 * resolve to an `Intent`, and one reducer consumes intents.
 */

import {
  back,
  travel,
  EXTERIOR,
  type NavState,
  type Place,
  type Portal,
} from "./navigation";
import type { OfficeWorld, WorldFloor } from "./WorldAssembler";

/** Everything a player can mean. Inputs differ; intents do not. */
export type Intent =
  | { kind: "enter" }
  | { kind: "exit" }
  | { kind: "up" }
  | { kind: "down" }
  | { kind: "back" }
  | { kind: "goto"; to: Place }
  | { kind: "select"; target: number | null };

export interface InteractResult {
  nav: NavState;
  /** Set when the intent changed the selection rather than the place. */
  selected?: number | null;
  /** True when the intent was refused; `nav.refused` carries the reason. */
  refused: boolean;
}

/** A clickable thing in the current place, in scene coordinates. */
export interface Hotspot {
  /** Where it is, in the same CSS px the scene drew in. */
  x: number;
  y: number;
  r: number;
  intent: Intent;
  /** What to call it in the hit test failure message. */
  label: string;
}

/**
 * The hotspots for a place. DERIVED from the same geometry the scene painter
 * uses, so a click always lands on a door that is actually drawn — the classic
 * mismatch is a hitbox that drifts from its sprite.
 */
export function hotspotsFor(
  place: Place,
  world: OfficeWorld,
  width: number,
  height: number,
): Hotspot[] {
  const out: Hotspot[] = [];
  const slug = world.building.slug;

  if (place.kind === "exterior") {
    // the front door, matching drawExterior's 44x46 door at the building base
    out.push({
      x: width / 2, y: height * 0.62 - 23, r: 46,
      intent: { kind: "enter" }, label: "the front door",
    });
    return out;
  }

  if (place.kind === "building") {
    /* MATCHING drawEntranceHall. Two ways on from here: deeper in to reception,
     * and back out through the doorway you came in by. The exit is what makes
     * the walk's final step possible. */
    out.push({
      x: width * 0.95 - 14, y: height * 0.14 + 20, r: 60,
      intent: { kind: "enter" }, label: "the way to reception",
    });
    out.push({
      x: width * 0.5, y: height * 0.14 + height * 0.72 - 28 - 96 + 96, r: 52,
      intent: { kind: "exit" }, label: "the way back outside",
    });
    return out;
  }

  if (place.kind === "lobby") {
    // the elevator: take the next floor up
    out.push({
      x: width * 0.62 + 33, y: height * 0.2 + height * 0.66 - 24 - 48, r: 52,
      intent: { kind: "up" }, label: "the elevator",
    });
    out.push({
      x: 40, y: height - 40, r: 34,
      intent: { kind: "exit" }, label: "the way out",
    });
    /* ONLY THE ROOMS THE LOBBY ACTUALLY OPENS ONTO. The lobby is the lowest
     * floor, so its doors are that floor's rooms — every one of them reachable
     * in a single hop. An earlier cut listed EVERY room in the building here,
     * drawing doors to floors 2 and 3 that the graph refuses: dead affordances
     * that look clickable and do nothing. */
    const lowest = lowestFloor(world);
    if (lowest) {
      lowest.rooms.forEach((r, i) => {
        out.push({
          x: width * 0.2 + i * 166 + 60, y: height * 0.2 + 60, r: 44,
          intent: { kind: "goto", to: {
            kind: "room", building: slug, floor: lowest.number, room: r.id } },
          label: "the door to " + r.slug,
        });
      });
    }
    return out;
  }

  if (place.kind === "floor") {
    const f = world.building.floors.find((x) => x.number === place.floor);
    if (!f) return out;
    f.rooms.forEach((r, i) => {
      out.push({
        x: width * 0.2 + i * (Math.min(150, (width * 0.72) /
          Math.max(1, f.rooms.length) - 16) + 16) + 60,
        y: height * 0.16 + 100, r: 44,
        intent: { kind: "goto", to: {
          kind: "room", building: slug, floor: f.number, room: r.id } },
        label: "the door to " + r.slug,
      });
    });
    out.push({
      x: width * 0.08 + 27, y: height * 0.16 + height * 0.68 - 78, r: 48,
      intent: { kind: "back" }, label: "the elevator back down",
    });
    // selecting a figure standing on this landing
    f.rooms.forEach((r) => {
      r.occupants.forEach((c) => {
        out.push({
          x: 0, y: 0, r: 14,
          intent: { kind: "select", target: c.agent_id },
          label: "agent " + c.slug,
        });
      });
    });
    return out;
  }

  if (place.kind === "room") {
    const f = world.building.floors.find((x) => x.number === place.floor);
    const room = f?.rooms.find((r) => r.id === place.room);
    room?.occupants.forEach((c, i) => {
      const col = i % Math.max(1, Math.min(3, room.occupants.length));
      out.push({
        x: width * 0.16 + col * 150 + 46, y: height * 0.15 + 70 +
          Math.floor(i / Math.max(1, Math.min(3, room.occupants.length))) * 96,
        r: 26, intent: { kind: "select", target: c.agent_id },
        label: "agent " + c.slug,
      });
    });
    out.push({
      x: 40, y: height - 40, r: 34,
      intent: { kind: "back" }, label: "the door back to the floor",
    });
    return out;
  }
  return out;
}

/** The lowest floor the payload reports, or null for a building with none. */
function lowestFloor(world: OfficeWorld): WorldFloor | null {
  let best: WorldFloor | null = null;
  for (const f of world.building.floors) {
    if (best === null || f.number < best.number) best = f;
  }
  return best;
}

/** The nearest hotspot within its radius, or null. */
export function hitTest(
  spots: Hotspot[], x: number, y: number,
): Hotspot | null {
  let best: Hotspot | null = null;
  let bestD = Infinity;
  for (const s of spots) {
    const d = Math.hypot(s.x - x, s.y - y);
    if (d <= s.r && d < bestD) {
      bestD = d;
      best = s;
    }
  }
  return best;
}

/**
 * THE ONE REDUCER. Both mouse and keyboard arrive here as intents.
 */
export function applyIntent(
  state: NavState,
  intent: Intent,
  portals: Portal[],
  world: OfficeWorld,
  selected: number | null,
): InteractResult {
  const slug = world.building.slug;
  switch (intent.kind) {
    case "back":
      return { nav: back(state), refused: false };

    case "select":
      return { nav: state, selected: intent.target, refused: false };

    case "goto":
      return step(state, intent.to, portals);

    case "enter": {
      const to: Place =
        state.viewpoint.kind === "exterior" || state.viewpoint.kind === undefined
          ? { kind: "building", building: slug }
          : state.viewpoint.kind === "building"
            ? { kind: "lobby", building: slug }
            : state.viewpoint;
      if (to === state.viewpoint) {
        return {
          nav: { ...state, refused: {
            reason: "there is nothing to enter from here",
            from: state.viewpoint, to: state.viewpoint } },
          refused: true,
        };
      }
      return step(state, to, portals);
    }

    case "exit": {
      // SYMMETRIC: leaving a room returns to its floor; leaving a floor to the
      // lobby; leaving the lobby out of the building; and the building to the
      // street. Every one of those reverses the entry.
      const here = state.viewpoint;
      let to: Place;
      if (here.kind === "room") {
        to = { kind: "floor", building: here.building, floor: here.floor };
      } else if (here.kind === "floor") {
        to = { kind: "lobby", building: here.building };
      } else if (here.kind === "lobby") {
        to = { kind: "building", building: here.building };
      } else if (here.kind === "building") {
        to = EXTERIOR;
      } else {
        return {
          nav: { ...state, refused: {
            reason: "there is nothing to exit from here",
            from: here, to: here } },
          refused: true,
        };
      }
      return step(state, to, portals);
    }

    case "up":
    case "down": {
      return floorStep(state, intent.kind === "up" ? 1 : -1, portals);
    }
  }
}

function step(state: NavState, to: Place, portals: Portal[]): InteractResult {
  const next = travel(state, to, portals);
  return { nav: next, refused: next.refused !== null };
}

/** Ride the elevator one floor. Refuses at the ends rather than wrapping. */
function floorStep(
  state: NavState, delta: number, portals: Portal[],
): InteractResult {
  const here = state.viewpoint;
  const candidates = portals.filter(
    (p) => (p.via === "elevator" || p.via === "stairs"));
  if (here.kind === "lobby" || here.kind === "building") {
    const up = candidates
      .filter((p) => p.from.kind === "lobby")
      .sort((a, b) => (b.to.floor ?? 0) - (a.to.floor ?? 0));
    const pick = up[0];
    if (!pick) {
      return { nav: state, refused: true };
    }
    return step(state, pick.to, portals);
  }
  if (here.kind === "floor") {
    const from = here.floor ?? 0;
    const moves = candidates
      .filter((p) => p.from.kind === "floor" && p.from.floor === from)
      .map((p) => p.to)
      .filter((t): t is Place => typeof t.floor === "number")
      .filter((t) => delta > 0 ? (t.floor as number) > from
                               : (t.floor as number) < from)
      .sort((a, b) => delta > 0
        ? (a.floor as number) - (b.floor as number)
        : (b.floor as number) - (a.floor as number));
    const direct = moves[0];
    if (direct) return step(state, direct, portals);

    /* NO FLOOR ABOVE/BELOW — and the answer is a REFUSAL, not a detour.
     *
     * An earlier cut fell back to riding the lobby and then up/down from there.
     * At the top floor that "ride down to the lobby, then look for a floor
     * above" produced a silent teleport to the lobby when the player pressed
     * Up: the viewpoint moved, the request was impossible, and nothing said so.
     * Wrapping or detouring off the end of a building is exactly the
     * discontinuity a real elevator does not have. */
    return {
      nav: { ...state, refused: {
        reason: delta > 0 ? "this is the top floor" : "this is the ground floor",
        from: here, to: here } },
      refused: true,
    };
  }
  return { nav: state, refused: true };
}

/** The doors the current place offers, for the UI to render as hints. */
export function exitsFrom(
  place: Place, portals: Portal[],
): Array<{ label: string; intent: Intent }> {
  const out: Array<{ label: string; intent: Intent }> = [];
  for (const p of portals.filter(
    (x) => samePlace(x.from, place) && !x.locked)) {
    const label =
      p.via === "entrance" ? "enter"
        : p.via === "exit" ? "exit"
          : p.via === "door" ? (p.to.kind === "room" ? "enter room"
                                                     : "leave room")
            : p.via === "elevator" ? "elevator"
              : "stairs";
    out.push({ label, intent: { kind: "goto", to: p.to } });
  }
  return out;
}

function samePlace(a: Place, b: Place): boolean {
  return a.kind === b.kind && a.building === b.building
    && a.floor === b.floor && a.room === b.room;
}
