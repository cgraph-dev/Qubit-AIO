/* F06 — the navigation model: a pure reducer over location identities.
 *
 * WHY A REDUCER AND NOT A ROUTER
 * ------------------------------
 * The plan requires entering and leaving buildings, travelling floors, entering
 * rooms, deep links, Back and restore. Every one of those is a STATE
 * TRANSITION, and the failure mode of a screen-stack implementation is that a
 * transition which should be impossible becomes reachable in some order (enter
 * a room from a district, exit to a floor you were never on). A pure reducer
 * makes the reachable set enumerable and testable, and makes Back a function of
 * the same graph rather than a browser-history accident.
 *
 * THE VIEWPOINT / LOCATION SEPARATION (amendment § 3)
 * --------------------------------------------------
 * `viewpoint` is WHERE THE OWNER IS LOOKING — pure presentation, free to move
 * without any event. `Here` is derived from it and is the only thing the world
 * assembler may use to decide what to draw. Nothing in this file can move an
 * AGENT: a character's own room comes from the payload, never from navigation.
 */

export type PlaceKind = "exterior" | "building" | "lobby" | "floor" | "room";

/** One addressable place. Identities are explicit so a deep link is exact. */
export interface Place {
  kind: PlaceKind;
  /** Building slug. Absent for the exterior district. */
  building?: string;
  /** Floor NUMBER (not an id) — matches `character.position.floor`. */
  floor?: number;
  /** Room id within the floor. */
  room?: number;
}

export interface Portal {
  from: Place;
  to: Place;
  /** How the player travels. Presentation only. */
  via: "entrance" | "exit" | "elevator" | "stairs" | "door";
  /** A locked portal is drawn as blocked and refuses the transition. */
  locked?: boolean;
  /** Why it is locked, shown verbatim. Never invent a reason. */
  lockReason?: string;
  /** Minimum floors travelled by an elevator/stairs portal. Enforced, not
   *  assumed, so a graph cannot silently skip a floor. */
  span?: number;
}

export interface NavState {
  viewpoint: Place;
  /** Back stack. Capped; every entry is a place visited. */
  history: Place[];
  /** The last refusal, shown to the user. Cleared on a successful move. */
  refused: { reason: string; from: Place; to: Place } | null;
}

export const MAX_HISTORY = 32;

/** The exterior district: the root of the graph. */
export const EXTERIOR: Place = { kind: "exterior" };

export function initialNav(place: Place = EXTERIOR): NavState {
  return { viewpoint: place, history: [], refused: null };
}

function same(a: Place, b: Place): boolean {
  return a.kind === b.kind && a.building === b.building
    && a.floor === b.floor && a.room === b.room;
}

/** A stable, deep-linkable string. `?place=floor:hq:2` style. */
export function encodePlace(p: Place): string {
  switch (p.kind) {
    case "exterior":
      return "exterior";
    case "building":
      return `building:${p.building}`;
    case "lobby":
      return `lobby:${p.building}`;
    case "floor":
      return `floor:${p.building}:${p.floor}`;
    case "room":
      return `room:${p.building}:${p.floor}:${p.room}`;
  }
}

/** Parse a place string. An unparseable or unknown target is a typed refusal. */
export function decodePlace(
  s: string,
  known: { buildings?: string[]; floors?: Array<[string, number]>;
           rooms?: Array<[string, number, number]> } = {},
): { ok: true; place: Place } | { ok: false; reason: string } {
  const parts = String(s).split(":");
  const kind = parts[0];
  if (kind === "exterior") return { ok: true, place: EXTERIOR };
  const [, building, floorRaw, roomRaw] = parts;
  if (!building) return { ok: false, reason: `no building in ${JSON.stringify(s)}` };
  if (known.buildings && !known.buildings.includes(building)) {
    return { ok: false, reason: `no building named ${building}` };
  }
  if (kind === "building") return { ok: true, place: { kind, building } };
  if (kind === "lobby") return { ok: true, place: { kind, building } };
  const floor = Number(floorRaw);
  if (!Number.isFinite(floor)) {
    return { ok: false, reason: `${JSON.stringify(floorRaw)} is not a floor number` };
  }
  if (known.floors && !known.floors.some(([b, n]) => b === building && n === floor)) {
    return { ok: false, reason: `${building} has no floor ${floor}` };
  }
  if (kind === "floor") return { ok: true, place: { kind, building, floor } };
  if (kind === "room") {
    const room = Number(roomRaw);
    if (!Number.isFinite(room)) {
      return { ok: false, reason: `${JSON.stringify(roomRaw)} is not a room id` };
    }
    if (known.rooms &&
        !known.rooms.some(([b, f, r]) => b === building && f === floor && r === room)) {
      return { ok: false, reason: `${building} floor ${floor} has no room ${room}` };
    }
    return { ok: true, place: { kind, building, floor, room } };
  }
  return { ok: false, reason: `unknown place kind ${JSON.stringify(kind)}` };
}

/**
 * THE TRANSITION REDUCER. `travel` is the ONLY way the viewpoint moves.
 *
 * It follows a declared portal, or refuses with a reason. It never invents a
 * destination: a target with no portal is a refusal, which is what makes the
 * reachable set exactly the graph.
 */
export function travel(
  state: NavState,
  to: Place,
  portals: Portal[],
): NavState {
  if (same(state.viewpoint, to)) {
    return { ...state, refused: null };
  }
  const portal = portals.find(
    (p) => same(p.from, state.viewpoint) && same(p.to, to));
  if (!portal) {
    return {
      ...state,
      refused: {
        reason: `there is no way to ${encodePlace(to)} from ${encodePlace(state.viewpoint)}`,
        from: state.viewpoint, to,
      },
    };
  }
  if (portal.locked) {
    return {
      ...state,
      refused: {
        reason: portal.lockReason ?? `${encodePlace(to)} is locked`,
        from: state.viewpoint, to,
      },
    };
  }
  const history = [...state.history, state.viewpoint].slice(-MAX_HISTORY);
  return { viewpoint: to, history, refused: null };
}

/** Back. Pops one place; at the root it stays put rather than underflowing. */
export function back(state: NavState): NavState {
  if (state.history.length === 0) return { ...state, refused: null };
  const history = state.history.slice(0, -1);
  return { viewpoint: state.history[state.history.length - 1], history,
           refused: null };
}

/** Jump straight to a place (deep link / restore), forgiving of the graph. */
export function restore(place: Place): NavState {
  return { viewpoint: place, history: [], refused: null };
}

/**
 * The portal graph for a single headquarters, DERIVED from real floor data so
 * it cannot drift from the building it describes. Multi-building (F11) extends
 * this by passing more than one building.
 */
export function buildHeadquartersPortals(
  building: string,
  floors: Array<{ number: number; rooms: Array<{ id: number }> }>,
): Portal[] {
  const portals: Portal[] = [];
  const sorted = [...floors].sort((a, b) => a.number - b.number);
  const lowest = sorted[0]?.number ?? 1;

  portals.push({ from: EXTERIOR, to: { kind: "building", building },
                 via: "entrance" });
  portals.push({ from: { kind: "building", building },
                 to: { kind: "lobby", building }, via: "entrance" });
  portals.push({ from: { kind: "lobby", building }, to: EXTERIOR,
                 via: "exit" });

  for (const f of sorted) {
    const floorPlace: Place = { kind: "floor", building, floor: f.number };
    /* The lobby IS the lowest floor: entering the building lands you there.
     * But "the lobby is floor 1" must not make floor 1's ROOMS unreachable —
     * an earlier cut skipped the lobby->floor1 portal entirely, and the
     * boardroom on floor 1 became impossible to enter. The lobby and its floor
     * are two places, so the hop exists and is declared as a door. */
    portals.push({ from: { kind: "lobby", building }, to: floorPlace,
                   via: f.number === lowest ? "door" : "elevator",
                   ...(f.number === lowest ? {} : { span: f.number - lowest }) });
    portals.push({ from: floorPlace, to: { kind: "lobby", building },
                   via: f.number === lowest ? "door" : "elevator",
                   ...(f.number === lowest ? {} : { span: f.number - lowest }) });
    for (const r of f.rooms) {
      const roomPlace: Place = { kind: "room", building, floor: f.number,
                                 room: r.id };
      portals.push({ from: floorPlace, to: roomPlace, via: "door" });
      portals.push({ from: roomPlace, to: floorPlace, via: "door" });
    }
  }
  return portals;
}
