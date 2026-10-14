/* F05 — the payload types, taken from the REAL recorded response.
 *
 * Every field here is copied from
 * `frontend/qubit-web/src/lib/office-game/fixtures/tower-recording.json`
 * (a byte-identical recording of `GET /agent/api/office/tower`; see its
 * PROVENANCE.md). Nothing is invented, and nothing is widened to `any` to make
 * a wrong assumption compile — a field the payload does not have is a field
 * this file does not declare.
 *
 * THREE SHAPE FACTS THAT ARE EASY TO "FIX" WRONGLY, and are typed to prevent it:
 *
 *  1. `position.floor` is a FLOOR NUMBER, not an id, and `floor_role` repeats
 *     the role so a character can be drawn without the floorplan in hand.
 *  2. `at` is a location KIND ("desk" | "station") and is NOT the same axis as
 *     `animation` ("idle" | "working" | ...). A character can be `at: "desk"`
 *     and `animation: "working"`. Collapsing them loses the distinction the
 *     tower exists to draw.
 *  3. `dossier.known === false` while `dossier.beliefs` is NON-EMPTY. Known-ness
 *     is a FIELD, never "is the array empty" — the beliefs carry provenance
 *     (an `evidence` string) even when the dossier is not established.
 */

/** The animation vocabulary `voicebridge/office/tower.py::_ANIMATION` can emit. */
export type AnimationState =
  | "idle"
  | "working"
  | "walking"
  | "meeting"
  | "blocked"
  | "carrying"
  | "glow"
  | "settling"
  | "walking_to_manager"
  | "seated";

export type Facing = "up" | "down" | "left" | "right";

/** Where a character is, physically. A floor NUMBER, not an id. */
export interface CharacterPosition {
  floor: number;
  floor_role: string;
  room: string;
  room_id: number;
  x: number;
  y: number;
  slot: number;
}

export interface LastEvent {
  kind: string;
  age_s: number;
}

export interface CharacterDossier {
  known: boolean;
  agent_id: number;
  dimensions: string[];
  beliefs: Array<{ dimension: string; belief: string; evidence: string }>;
  memory_count: number;
  note: string;
}

export interface OfficeCharacter {
  agent_id: number;
  slug: string;
  seat_type: string;
  department_id: number | null;
  supervisor_id: number | null;
  seated: boolean;
  position: CharacterPosition;
  facing: Facing;
  walking: boolean;
  /** Location KIND. Distinct from `animation` — see the header note. */
  at: string;
  animation: AnimationState;
  last_event: LastEvent | null;
  stalled: boolean;
  memory: { count: number; known: boolean };
  dossier: CharacterDossier;
}

export interface FloorRoom {
  id: number;
  slug: string;
  kind: string;
  department_id: number;
  x: number;
  y: number;
  w: number;
  h: number;
  seats: Array<{
    slot: number;
    agent_id: number | null;
    slug: string | null;
    x: number;
    y: number;
  }>;
}

export interface FloorPlanFloor {
  id: number;
  number: number;
  name: string;
  role: string;
  theme: string;
  /** The isometric z-offset, in world units. */
  z: number;
  rooms: FloorRoom[];
}

export interface OfficeFloorplan {
  schema: string;
  grid: { cols: number; rows: number; tile: number };
  floors: FloorPlanFloor[];
}

export interface HudFloor {
  floor_id: number;
  number: number;
  role: string;
  departments: number;
  revenue_cents: number;
  cost_cents: number;
  net_cents: number;
}

export interface OfficeHud {
  headcount: number;
  revenue_cents: number;
  cost_cents: number;
  net_cents: number;
  burn_cents_per_day: number;
  /** NULL means "nothing is burning yet" — never coerce to 0. */
  runway_days: number | null;
  runway_is_derived: boolean;
  floors: HudFloor[];
  survival: { floor_days: number };
  /** Which fold produced these numbers. Carried so the UI can name it. */
  pnl_source: string;
}

export interface ActivityRow {
  id: number;
  ts: number;
  kind: string;
  agent_id: number;
  agent_slug: string;
  text: string;
}

export interface HistoryRow {
  day: number;
  date: string;
  revenue_cents: number;
  cost_cents: number;
  net_cents: number;
}

/** The wire payload. `schema` is checked by the adapter, never assumed. */
export interface OfficeTowerPayload {
  ok: boolean;
  schema: string;
  now: number;
  active_window_s: number;
  walk_s: number;
  empty: boolean;
  empty_reason: string;
  hud: OfficeHud;
  floorplan: OfficeFloorplan;
  characters: OfficeCharacter[];
  activity: ActivityRow[];
  history: HistoryRow[];
  history_totals: { revenue_cents: number; cost_cents: number; net_cents: number };
  grid: { cols: number; rows: number; tile: number };
  /** The backend's own statement of the C7 rule. Carried, not paraphrased. */
  motion_rule: string;
  read_only: boolean;
  preview: boolean;
}

/** The schema this adapter is written against. A mismatch is refused, not coerced. */
export const EXPECTED_SCHEMA = "qubit-office-tower/1";
