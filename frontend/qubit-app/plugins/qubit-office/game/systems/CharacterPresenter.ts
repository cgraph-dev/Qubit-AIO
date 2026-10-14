/* F09 — the character presenter: a `characters[]` row -> what the panel draws.
 *
 * THE ONE RULE THIS FILE EXISTS TO ENFORCE
 * ----------------------------------------
 * **A field the payload did not carry is UNKNOWN, never a default.**
 *
 * The typed payload (`game/types.ts`, derived from the recorded
 * `tower-recording.json`) has exactly these keys per character:
 *
 *   agent_id, slug, seat_type, department_id, supervisor_id, seated, position,
 *   facing, walking, at, animation, last_event, stalled, memory, dossier
 *
 * Three of those are nullable or flag-guarded, and every one of them is a place
 * where a careless presenter invents a fact:
 *
 *  1. `supervisor_id` is `null` for the CEO. `null` means "reports to nobody",
 *     which is a FACT — it is not "id 0", not "unknown", and not a licence to
 *     print "reports to seat #null".
 *  2. `last_event` is `null` when the payload carried none. That is "nothing
 *     reported", not "idle" and not "0 seconds ago". The presenter keeps a
 *     three-way state: known / unknown / absent.
 *  3. `memory.known` and `dossier.known` are FLAGS, not "is the array empty".
 *
 * THE `known` TRAP, MEASURED ON THE REAL RECORDING
 * -----------------------------------------------
 * Every character in the recorded payload has `memory.known === false` and
 * `dossier.known === false`, with a dossier note reading
 * `no evidence yet: no seat row and no memory` — and EVERY ONE of them still
 * carries non-empty `dossier.beliefs`, with provenance:
 *
 *   identity        "ceo is a ceo"                evidence "office_agent#1"
 *   standing_orders "autonomy posture: unattended_full"
 *                                                evidence "office_agent.autonomy"
 *
 * So `known` is NOT "has no facts". It means "no SEAT ROW and no MEMORY"
 * (`voicebridge/office/agent_memory.py::dossier`: `known = bool(seat) and
 * bool(mem["memories"])`). Collapsing `known === false` into "show nothing
 * here" throws away beliefs the backend actually sent; collapsing it into
 * "known" presents a derived projection as earned evidence. Both are the
 * failure this packet exists to prevent, so this file models them as TWO
 * separate fields and never renders one as the other:
 *
 *   evidenceBasis — why the dossier is not established (the payload's own
 *                   `note`). Shown VERBATIM. Never paraphrased.
 *   beliefs       — what the backend DID send, each with its evidence string.
 *                   Always rendered, whether or not the dossier is established.
 *
 * Pure module: no DOM, no React, no fetch, no clock. `now` is always passed in
 * by the caller, because a hidden second clock is how a renderer invents state
 * (C7).
 */

import type {
  OfficeCharacter, OfficeTowerPayload,
} from "../types";
import {
  buildHeadquartersPortals, encodePlace, type NavState, type Place,
  type Portal,
} from "../navigation";
import { applyIntent } from "../InteractionController";
import { assembleWorld, HQ_SLUG, type OfficeWorld } from "../WorldAssembler";

/**
 * A row, seen as a bag of unknown keys.
 *
 * The `types.ts` shape is what the recording proves is THERE. Reading a field
 * defensively — asking whether a key arrived at all — needs the row as
 * `unknown`-valued, and a direct cast between two incompatible object types is
 * a TypeScript error rather than a silent lie. So every defensive read goes
 * through this helper, once, with the reason stated.
 */
function bag(o: unknown): Record<string, unknown> {
  return (o ?? {}) as Record<string, unknown>;
}

// ---------------------------------------------------------------------------
// Identity: one character, one stable id, one portrait that matches the world
// ---------------------------------------------------------------------------

/** The seat types `store.SEAT_TYPES` can emit. */
export type SeatType =
  | "ceo"
  | "director_portfolio"
  | "director_functional"
  | "manager"
  | "scraper"
  | "analyst"
  | "researcher"
  | "worker"
  | "support"
  | "auditor";

/** What role a row shows. A seat type the payload did not carry is NOT "worker". */
export interface RoleView {
  /** The payload's `seat_type`, verbatim. */
  seatType: string;
  /** A readable label. Falls back to the raw slug of the one we were given. */
  label: string;
  /** True when the seat type is one this build knows how to label. */
  recognised: boolean;
}

/**
 * Seat-type labels. Deliberately a closed map: a type absent from it is
 * rendered as its own raw string with `recognised: false`, because inventing a
 * label for an unknown role is exactly the "looks authoritative, says nothing"
 * failure. (`store.SEAT_TYPES` is the source list.)
 */
const SEAT_LABELS: Record<string, string> = {
  ceo: "Chief executive",
  director_portfolio: "Portfolio director",
  director_functional: "Functional director",
  manager: "Manager",
  scraper: "Scraper",
  analyst: "Analyst",
  researcher: "Researcher",
  worker: "Worker",
  support: "Support",
  auditor: "Auditor",
};

export function roleView(seatType: string | null | undefined): RoleView {
  const raw = String(seatType ?? "").trim();
  if (!raw) {
    // No seat type in the payload at all. Say that; do not assume "worker".
    return { seatType: "", label: "seat type not reported", recognised: false };
  }
  const label = SEAT_LABELS[raw];
  return label
    ? { seatType: raw, label, recognised: true }
    : { seatType: raw, label: raw, recognised: false };
}

/** The supervisor edge, in all four of its real states. */
export interface SupervisorView {
  state: "none" | "assigned" | "unresolved" | "unreported";
  /** The supervisor's seat id, when one is assigned. */
  agentId: number | null;
  /** The supervisor's slug, resolved against the roster the payload gave us. */
  slug: string | null;
  /** What to draw. */
  text: string;
}

/**
 * Resolve `supervisor_id` against the roster.
 *
 * THE FOUR STATES, AND WHY THEY ARE FOUR
 * --------------------------------------
 *   none       `supervisor_id === null`. A fact: this seat reports to nobody
 *              (the CEO reports to the owner). NOT an error, NOT unknown.
 *   assigned   an id that IS in the payload's roster. We can name it.
 *   unresolved an id that is NOT in the roster. Real information — the panel
 *              shows the raw id rather than a slug it cannot know.
 *   unreported the key was missing entirely. That is NOT "reports to nobody".
 *
 * Collapsing `unreported` into `none` would turn "the payload did not say" into
 * "the payload said nobody", which is a fabricated org fact.
 */
export function supervisorView(
  c: OfficeCharacter,
  roster: OfficeCharacter[],
): SupervisorView {
  const v = bag(c).supervisor_id;
  if (v === undefined) {
    return {
      state: "unreported", agentId: null, slug: null,
      text: "supervisor not reported",
    };
  }
  if (v === null) {
    return {
      state: "none", agentId: null, slug: null,
      // The CEO case. Stated as a fact, because it is one.
      text: "reports to the owner (no agent supervisor)",
    };
  }
  const id = Number(v);
  if (!Number.isFinite(id)) {
    return {
      state: "unreported", agentId: null, slug: null,
      text: "supervisor not reported",
    };
  }
  const boss = roster.find((r) => Number(r.agent_id) === id);
  if (!boss) {
    return {
      state: "unresolved", agentId: id, slug: null,
      text: `reports to seat #${id} (not in this roster)`,
    };
  }
  return {
    state: "assigned", agentId: id, slug: String(boss.slug),
    text: `reports to ${boss.slug}`,
  };
}

/**
 * The portrait key. Deterministic, derived from the payload's OWN ids, and the
 * SAME key the world draws with — so the card and the figure in the scene are
 * indisputably the same person rather than two lookalikes.
 *
 * It is a KEY, not a claim: this build has authored no portrait art (F10 owns
 * that), so every consumer must render it as an identity token, never as a
 * picture of a face.
 */
export function portraitKey(c: OfficeCharacter): string {
  return `agent:${Number(c.agent_id)}`;
}

/** The world identity, from the same character: where the figure actually is. */
export interface WorldIdentity {
  agentId: number;
  /** The place the F06 graph addresses for this character, or null if unseated. */
  place: Place | null;
  /** The deep-linkable encoding of `place`, or "" when there is none. */
  placeKey: string;
  roomId: number | null;
  room: string;
  floor: number | null;
  x: number | null;
  y: number | null;
  /** False when the payload gave no seat — surfaced, never hidden. */
  seated: boolean;
  /**
   * True when `place` was DERIVED from the character's own room. Leaves
   * `position.room_id` unset, in which case the card says so rather than
   * pointing at a room it guessed.
   */
  derived: boolean;
}

export function worldIdentity(c: OfficeCharacter): WorldIdentity {
  const raw = bag(bag(c).position);
  const roomId = raw.room_id === null || raw.room_id === undefined
    ? null : Number(raw.room_id);
  const floor = raw.floor === null || raw.floor === undefined
    ? null : Number(raw.floor);
  const seated = c.seated !== false && roomId !== null && Number.isFinite(roomId);

  let place: Place | null = null;
  let derived = false;
  if (seated && floor !== null && Number.isFinite(floor)) {
    // The F06 `Place` identity. This is the SAME shape `hotspotsFor` hands the
    // reducer when a figure is clicked, which is what makes "Go to target" the
    // same navigation a click performs rather than a parallel one.
    place = { kind: "room", building: HQ_SLUG, floor, room: roomId as number };
    derived = true;
  } else if (seated) {
    // A seat row exists but carried no floor. We can name the room and not the
    // floor, so we refuse to build a place rather than guess one.
    derived = false;
  }
  return {
    agentId: Number(c.agent_id),
    place,
    placeKey: place ? encodePlace(place) : "",
    roomId: seated ? (roomId as number) : null,
    room: String(raw.room ?? ""),
    floor: floor !== null && Number.isFinite(floor) ? floor : null,
    x: raw.x === null || raw.x === undefined ? null : Number(raw.x),
    y: raw.y === null || raw.y === undefined ? null : Number(raw.y),
    seated,
    derived,
  };
}

// ---------------------------------------------------------------------------
// Last event: three states, because "absent" is not "idle"
// ---------------------------------------------------------------------------

export interface LastEventView {
  state: "known" | "absent";
  kind: string;
  ageS: number | null;
  text: string;
}

/**
 * The last event, verbatim.
 *
 * `animation` is a SEPARATE axis from `at`, and neither is `last_event.kind`
 * (`types.ts` header fact 2). This returns the event, not an animation: a
 * presenter that maps the kind onto an animation has started inventing work.
 */
export function lastEventView(
  c: OfficeCharacter,
  windowS: number,
): LastEventView {
  const e = bag(c).last_event as
    { kind?: unknown; age_s?: unknown } | null | undefined;
  if (e === null || e === undefined) {
    return {
      state: "absent", kind: "", ageS: null,
      // Deliberately not "idle": the payload did not say. Saying "idle" would
      // be this module reporting a state the backend never sent.
      text: "no event reported",
    };
  }
  const kind = String(e.kind ?? "");
  const age = e.age_s === null || e.age_s === undefined
    ? null : Number(e.age_s);
  const within = age !== null && Number.isFinite(age)
    && Number.isFinite(windowS) && windowS > 0 && age <= windowS;
  return {
    state: "known", kind, ageS: age,
    text: age === null || !Number.isFinite(age)
      ? `${kind} (age not reported)`
      : `${kind} · ${Math.round(age)}s ago${within ? "" : " · outside the active window"}`,
  };
}

// ---------------------------------------------------------------------------
// Memory + dossier: the `known` trap, modelled explicitly
// ---------------------------------------------------------------------------

export interface MemoryView {
  /** False means UNKNOWN depth — not zero memories. */
  known: boolean;
  count: number;
  text: string;
}

/**
 * Memory depth.
 *
 * `count: 0, known: false` is the recorded payload's state for every seat and
 * it means "we do not know how many memories this seat has". Rendering `0` as
 * a remembered count, or "no memories", would be a claim the backend declined
 * to make.
 */
export function memoryView(c: OfficeCharacter): MemoryView {
  const m = bag(c).memory as { count?: unknown; known?: unknown } | undefined;
  const count = Number(m?.count ?? 0);
  const known = m?.known === true;
  if (!known) {
    return { known: false, count, text: "memory depth unknown" };
  }
  return {
    known: true, count,
    text: count === 1 ? "1 remembered action" : `${count} remembered actions`,
  };
}

export interface BeliefView {
  dimension: string;
  belief: string;
  /** The provenance string the payload carried. Never synthesised. */
  evidence: string;
}

export interface DossierView {
  /** The payload's own `dossier.known`. False = no seat row and no memory. */
  known: boolean;
  /** The payload's own `note`, VERBATIM. Empty when the dossier is known. */
  evidenceBasis: string;
  dimensions: string[];
  beliefs: BeliefView[];
  /**
   * What the panel shows. Two fields again: the BASIS (which may be empty when
   * the dossier is established) and the BELIEFS (which are always shown).
   */
  basis: string;
  /** True when beliefs exist that the `known: false` flag would hide. */
  hasUnestablishedEvidence: boolean;
}

/**
 * The dossier. **This is the function the packet exists for.**
 *
 * It reads `known` as a FLAG and the beliefs as DATA, and it hands both to the
 * panel. In the recorded payload that means: `known: false`, a verbatim basis
 * string, and three beliefs per seat with real provenance — all of which must
 * reach the screen.
 *
 * `dossier.agent_id` is checked against the character's own `agent_id`; a
 * mismatch is reported rather than silently rendered under the wrong slug,
 * because a dossier attached to the wrong agent is a false statement about a
 * person.
 */
export function dossierView(c: OfficeCharacter): DossierView {
  const raw = bag(bag(c).dossier);
  const known = raw.known === true;
  const beliefs: BeliefView[] = (Array.isArray(raw.beliefs) ? raw.beliefs : [])
    .map((b) => ({
      dimension: String((b as Record<string, unknown>)?.dimension ?? ""),
      belief: String((b as Record<string, unknown>)?.belief ?? ""),
      evidence: String((b as Record<string, unknown>)?.evidence ?? ""),
    }));
  const dimensions = (Array.isArray(raw.dimensions) ? raw.dimensions : [])
    .map((d) => String(d));
  const note = String(raw.note ?? "");
  const dossierAgent = raw.agent_id === undefined || raw.agent_id === null
    ? null : Number(raw.agent_id);
  let basis = known ? "" : note;
  if (dossierAgent !== null && Number.isFinite(dossierAgent)
      && dossierAgent !== Number(c.agent_id)) {
    // A dossier that belongs to someone else. Name it; never render it as this
    // person's profile.
    basis = `this dossier is keyed to seat #${dossierAgent}, not to seat ` +
            `#${Number(c.agent_id)}`;
  }
  if (!known) {
    basis = basis || "no evidence yet";
  }
  return {
    known,
    evidenceBasis: basis,
    dimensions,
    beliefs,
    basis,
    // The recorded case, stated as a fact the panel can style: the flag says
    // "not established" and the beliefs array is not empty.
    hasUnestablishedEvidence: !known && beliefs.length > 0,
  };
}

// ---------------------------------------------------------------------------
// The profile a panel draws
// ---------------------------------------------------------------------------

export interface CharacterProfile {
  agentId: number;
  slug: string;
  /** The portrait token — matches the world's identity for the same agent. */
  portrait: string;
  role: RoleView;
  supervisor: SupervisorView;
  memory: MemoryView;
  dossier: DossierView;
  lastEvent: LastEventView;
  world: WorldIdentity;
  departmentId: number | null;
  /** Presentation state, read verbatim from the payload. */
  animation: OfficeCharacter["animation"];
  at: string;
  facing: OfficeCharacter["facing"];
  walking: boolean;
  stalled: boolean;
  seated: boolean;
  /** The seat's floor role, as the payload reported it. */
  floorRole: string;
  /** Free-text unknowns, so a caller can assert none were silently defaulted. */
  unknownFields: string[];
}

/** Build the profile for one character. Pure; same input, same output. */
export function presentCharacter(
  c: OfficeCharacter,
  roster: OfficeCharacter[],
  windowS: number,
): CharacterProfile {
  const raw = bag(bag(c).position);
  const memory = memoryView(c);
  const dossier = dossierView(c);
  const supervisor = supervisorView(c, roster);
  const lastEvent = lastEventView(c, windowS);
  const world = worldIdentity(c);

  const unknownFields: string[] = [];
  if (supervisor.state === "unreported") unknownFields.push("supervisor_id");
  if (supervisor.state === "unresolved") unknownFields.push("supervisor_slug");
  if (!memory.known) unknownFields.push("memory.count");
  if (!dossier.known) unknownFields.push("dossier.known");
  if (lastEvent.state === "absent") unknownFields.push("last_event");
  if (world.floor === null) unknownFields.push("position.floor");
  if (world.roomId === null) unknownFields.push("position.room_id");
  if (raw.floor_role === undefined) unknownFields.push("position.floor_role");
  if (bag(c).department_id === undefined) {
    unknownFields.push("department_id");
  }

  return {
    agentId: Number(c.agent_id),
    slug: String(c.slug ?? ""),
    portrait: portraitKey(c),
    role: roleView(c.seat_type),
    supervisor,
    memory,
    dossier,
    lastEvent,
    world,
    departmentId: bag(c).department_id === undefined
      ? null
      : ((c.department_id as number | null) ?? null),
    animation: c.animation,
    at: String(c.at ?? ""),
    facing: c.facing,
    walking: !!c.walking,
    stalled: !!c.stalled,
    seated: !!c.seated,
    floorRole: String(raw.floor_role ?? ""),
    unknownFields,
  };
}

// ---------------------------------------------------------------------------
// The roster: filters, over a stable id
// ---------------------------------------------------------------------------

export type RosterFilter =
  | "all"
  | "seated"
  | "unseated"
  | "stalled"
  | "working";

export interface RosterFilters {
  filter: RosterFilter;
  /** Case-insensitive match on slug / role label / room. Empty = no text filter. */
  query: string;
}

export const ROSTER_FILTERS: readonly RosterFilter[] =
  ["all", "seated", "unseated", "stalled", "working"];

export interface RosterEntry {
  agentId: number;
  slug: string;
  portrait: string;
  role: RoleView;
  room: string;
  floor: number | null;
  seated: boolean;
  stalled: boolean;
  animation: OfficeCharacter["animation"];
  /** Shown next to the row, so "what is it doing" comes from the payload. */
  activity: string;
}

/**
 * The roster, from the payload's `characters` array and nothing else.
 *
 * ORDER IS THE PAYLOAD'S OWN ORDER, by seat id. Sorting by anything else (name,
 * animation) would make the list move under the player's cursor between polls.
 */
export function rosterEntries(
  payload: OfficeTowerPayload | null,
): RosterEntry[] {
  const rows = Array.isArray(payload?.characters) ? payload!.characters : [];
  return [...rows]
    .sort((a, b) => Number(a.agent_id) - Number(b.agent_id))
    .map((c) => {
      const p = presentCharacter(c, rows, Number(payload?.active_window_s ?? 0));
      return {
        agentId: p.agentId,
        slug: p.slug,
        portrait: p.portrait,
        role: p.role,
        room: p.world.room,
        floor: p.world.floor,
        seated: p.seated,
        stalled: p.stalled,
        animation: p.animation,
        activity: p.lastEvent.state === "known" ? p.lastEvent.text
          : "no event reported",
      };
    });
}

/** Apply the filters. Pure. */
export function filterRoster(
  entries: RosterEntry[],
  filters: RosterFilters,
): RosterEntry[] {
  const q = String(filters.query ?? "").trim().toLowerCase();
  return entries.filter((e) => {
    switch (filters.filter) {
      case "seated":
        if (!e.seated) return false;
        break;
      case "unseated":
        if (e.seated) return false;
        break;
      case "stalled":
        // `stalled` is the payload's own flag, not `animation === "blocked"`.
        if (!e.stalled) return false;
        break;
      case "working":
        // The payload's animation vocabulary, in the states that mean work.
        if (!["working", "settling", "carrying", "glow",
              "walking", "walking_to_manager", "meeting"].includes(e.animation)) {
          return false;
        }
        break;
      default:
        break;
    }
    if (!q) return true;
    return e.slug.toLowerCase().includes(q)
      || e.role.label.toLowerCase().includes(q)
      || e.room.toLowerCase().includes(q);
  });
}

export interface RosterView {
  entries: RosterEntry[];
  filters: RosterFilters;
  total: number;
  shown: number;
  /** The payload's own empty statement, carried not paraphrased. */
  empty: boolean;
  emptyReason: string;
}

export function rosterView(
  payload: OfficeTowerPayload | null,
  filters: RosterFilters = { filter: "all", query: "" },
): RosterView {
  const all = rosterEntries(payload);
  const shown = filterRoster(all, filters);
  return {
    entries: shown,
    filters,
    total: all.length,
    shown: shown.length,
    empty: payload ? !!payload.empty : false,
    emptyReason: payload ? String(payload.empty_reason ?? "") : "",
  };
}

// ---------------------------------------------------------------------------
// Go to target — the same reducer a click uses, never a parallel path
// ---------------------------------------------------------------------------

export interface GoToTargetResult {
  /** The next navigation state. Unchanged when a step refused. */
  nav: NavState;
  /** Every hop the route needed, in order, with its own outcome. */
  steps: Array<{ to: string; refused: boolean; reason: string }>;
  /** The destination we aimed at. */
  target: string;
  /** True when the viewpoint is the target's room once the route is done. */
  arrived: boolean;
  /** True when a hop was refused, so the caller shows the reason. */
  refused: boolean;
  reason: string;
}

/**
 * `Go to target`: move the viewpoint to the character's own place, through the
 * F06 reducer and the DECLARED portal graph.
 *
 * THE PART THAT IS EASY TO GET WRONG
 * ---------------------------------
 * The graph connects a room to its FLOOR only — `buildHeadquartersPortals`
 * declares `floor -> room` and `room -> floor`, never `floor 2 -> room on
 * floor 3`. A single `travel(nav, roomPlace, portals)` from anywhere else is
 * therefore REFUSED, and a "Go to target" that did only that would look
 * implemented and never move. So the hop is ROUTED: up through the lobby, then
 * to the target floor, then into the room — each hop a real declared portal
 * from the state the previous hop left behind.
 *
 * ATOMIC ON REFUSAL. If any hop refuses, the ORIGINAL navigation state is
 * returned, not the half-finished one. A partial route leaves the player in a
 * lobby they did not ask for while the button reported failure — the viewpoint
 * moved and nothing said it had, which is exactly the discontinuity `travel`
 * exists to prevent.
 *
 * WHAT IT DOES NOT DO
 * -------------------
 * It never moves an AGENT. `travel` moves the viewpoint; a character's own room
 * comes from the payload (amendment § 3). It also never guesses a route beyond
 * the declared graph: if no portal chain exists the result is a TYPED REFUSAL
 * carrying the first failing hop's reason, not a teleport.
 */
export function goToTarget(
  nav: NavState,
  target: CharacterProfile | { world?: WorldIdentity } | null,
  portals: Portal[],
  world?: OfficeWorld | null,
): GoToTargetResult {
  const origin = nav;
  const identity = target && "world" in target && target.world
    ? target.world
    : (target as WorldIdentity | null);
  const place = identity && identity.place ? identity.place : null;
  const targetKey = place ? encodePlace(place) : "";
  const steps: GoToTargetResult["steps"] = [];

  if (!place) {
    return {
      nav, steps, target: "", arrived: false, refused: true,
      // A refusal with a real reason. A silent no-op here would read as a
      // broken button rather than an unseated character.
      reason: "this character has no seat, so there is nowhere to go",
    };
  }

  /**
   * Walk one hop. `applyIntent` is used rather than `travel` directly so the
   * DIRECTION verbs (`enter` for exterior -> building -> lobby) resolve exactly
   * as the keyboard's Enter does — one reducer, two callers.
   */
  const world_ = world ?? onlyWorld(portals);
  const hop = (place2: Place) => {
    const res = applyIntent(
      nav, { kind: "goto", to: place2 }, portals, world_, null,
    );
    nav = res.nav;
    steps.push({
      to: encodePlace(place2),
      refused: !!res.refused,
      reason: res.nav.refused ? res.nav.refused.reason : "",
    });
    return !res.refused;
  };
  const enter = () => {
    const res = applyIntent(nav, { kind: "enter" }, portals, world_, null);
    nav = res.nav;
    steps.push({
      to: encodePlace(res.nav.viewpoint),
      refused: !!res.refused,
      reason: res.nav.refused ? res.nav.refused.reason : "",
    });
    return !res.refused;
  };

  const building = String(place.building ?? "");
  const floor = place.floor as number | undefined;

  // 1. Out of the street, if that is where we are.
  if (nav.viewpoint.kind === "exterior" && !enter()) {
    return failure(origin, steps, targetKey);
  }
  // 2. Into the building, if we are still outside it.
  if (nav.viewpoint.kind === "building" && !enter()) {
    return failure(origin, steps, targetKey);
  }
  // 3. The lobby is the hub every floor is reachable from.
  if (nav.viewpoint.kind !== "lobby"
      && !samePlace(nav.viewpoint, { kind: "lobby", building })) {
    if (!hop({ kind: "lobby", building })) {
      return failure(origin, steps, targetKey);
    }
  }
  // 4. Up to the target's floor. Already-on-floor is a no-op, not a refusal.
  const floorPlace: Place = { kind: "floor", building, floor };
  if (!(nav.viewpoint.kind === "floor" && nav.viewpoint.floor === floor)) {
    if (!hop(floorPlace)) return failure(origin, steps, targetKey);
  }
  // 5. Through the door.
  if (!hop(place)) return failure(origin, steps, targetKey);

  return {
    nav, steps, target: targetKey,
    arrived: samePlace(nav.viewpoint, place),
    refused: false, reason: "",
  };
}

/**
 * A refusal carries the ORIGINAL state back, so a partial route cannot leave
 * the viewpoint somewhere the player did not ask to be.
 */
function failure(
  origin: NavState, steps: GoToTargetResult["steps"], target: string,
): GoToTargetResult {
  const last = steps.length > 0 ? steps[steps.length - 1] : null;
  return {
    nav: origin, steps, target, arrived: false, refused: true,
    reason: (last && last.reason)
      || "there is no declared route to that character",
  };
}

function samePlace(a: Place, b: Place): boolean {
  return a.kind === b.kind && a.building === b.building
    && a.floor === b.floor && a.room === b.room;
}

/**
 * A world built from the payload is required by `applyIntent`; several callers
 * only have the portal graph. A world assembled from `null` deliberately has NO
 * characters and no floors, so a route can never be resolved from an invented
 * one — the reducer only reads `building.slug` for `goto`.
 */
function onlyWorld(_portals: Portal[]): OfficeWorld {
  return assembleWorld(null, 0);
}

/** The portals for the payload's own floorplan. Same derivation the shell uses. */
export function portalsFor(payload: OfficeTowerPayload | null): Portal[] {
  const floors: Array<{ number: number; rooms: Array<{ id: number }> }> =
    (payload?.floorplan?.floors ?? []).map((f) => ({
      number: Number(f.number),
      rooms: (f.rooms ?? []).map((r) => ({ id: Number(r.id) })),
    }));
  return buildHeadquartersPortals(HQ_SLUG, floors);
}