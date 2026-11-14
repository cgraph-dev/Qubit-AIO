/* P7-04 — event-to-FX client planner (pure, no DOM).
 *
 * WHAT THIS IS
 * ------------
 * The renderer-side half of `voicebridge/office/fx.py`. It parses the FX
 * payload the backend projects from authoritative Business OS events and turns
 * it into a bounded, priority-ordered list of visual cues. It mirrors the
 * backend's rules exactly: dedupe by `fx_event_id`, cancel/replace by subject,
 * stable seeds, reduced motion, disable-nonessential, and a typed `unavailable`
 * result when an atlas/animation asset is missing.
 *
 * It intentionally holds no canvas, no Phaser/Pixi reference and no DOM access.
 * Applying a plan is the renderer's job; this module only decides *what* should
 * play and in *what order*. It never invents an effect: an empty plan is empty,
 * and an event that cannot be resolved is reported as unavailable rather than
 * replaced with fake ambient life.
 */

export const FX_SCHEMA = "qubit-office-fx/1";
export const FX_VERSION = 1;
export const FX_MODE = "presentation";
export const DEFAULT_FX_CAPACITY = 32;
export const SEED_LIMIT = 2 ** 31;

export const PRIORITY_CRITICAL = 0;
export const PRIORITY_HIGH = 10;
export const PRIORITY_NORMAL = 20;
export const PRIORITY_LOW = 30;

export const CANCELLATIONS = ["none", "replace", "cancel"] as const;
export type FxCancellation = (typeof CANCELLATIONS)[number];

export type FxUnavailableCode =
  | "FX_PAYLOAD_UNAVAILABLE"
  | "FX_PAYLOAD_INVALID"
  | "FX_ASSET_MISSING";

/** One presentation effect, mirroring `FXEvent.to_dict()`. */
export interface FxEvent {
  schema: string;
  mode: string;
  fx_event_id: string;
  event_id: string;
  event_type: string;
  room: string | null;
  agent: string | null;
  animation: string;
  still: string;
  emote: string | null;
  particles: string | null;
  sound: string | null;
  seed: number;
  priority: number;
  cancellation: FxCancellation;
  replay: boolean;
  reduced_motion: boolean;
  fallback: string;
  essential: boolean;
  group: string;
}

export interface FxParseOk {
  status: "ok";
  events: FxEvent[];
  problems: string[];
}

export interface FxParseUnavailable {
  status: "unavailable";
  code: FxUnavailableCode;
  detail: string;
}

export type FxParseResult = FxParseOk | FxParseUnavailable;

/** A cue the renderer can actually play. */
export interface FxActiveEffect {
  fx_event_id: string;
  event_id: string;
  event_type: string;
  room: string | null;
  agent: string | null;
  animation: string;
  still: string;
  emote: string | null;
  particles: string | null;
  sound: string | null;
  seed: number;
  priority: number;
  fallback: string;
  essential: boolean;
  replay: boolean;
  reduced_motion: boolean;
  group: string;
  /** True when the primary cue was missing and `fallback` was used. */
  degraded: boolean;
}

/** A typed refusal for one source event (never a silent drop). */
export interface FxUnavailableEffect {
  fx_event_id: string;
  event_id: string;
  event_type: string;
  code: FxUnavailableCode;
  detail: string;
  essential: boolean;
}

export interface FxPlannerOptions {
  /** When true, nonessential motion is dropped and essential cues use `still`. */
  reducedMotion?: boolean;
  /** Drop every effect whose `essential` flag is false. */
  disableNonessential?: boolean;
  /**
   * Available atlas/animation ids. Omitted means "the backend declared it
   * available"; a supplied set that lacks a cue triggers the fallback path.
   */
  assets?: ReadonlySet<string>;
  capacity?: number;
  /** Label every planned effect as a replay. */
  replay?: boolean;
}

export interface FxPlan {
  schema: string;
  capacity: number;
  active: FxActiveEffect[];
  unavailable: FxUnavailableEffect[];
  accepted: number;
  dropped: number;
  duplicates: number;
  rejected: number;
  replay: boolean;
}

/** Serialized state for a reducer-style caller. */
export interface FxState {
  schema: string;
  capacity: number;
  active: FxActiveEffect[];
  unavailable: FxUnavailableEffect[];
  seen: string[];
  replay: boolean;
}

const REQUIRED_STRINGS = [
  "fx_event_id",
  "event_id",
  "event_type",
  "animation",
  "still",
  "fallback",
  "group",
] as const;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function text(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function optionalText(value: unknown): string | null {
  const clean = text(value);
  return clean ? clean : null;
}

function finiteInt(value: unknown): number | null {
  if (typeof value !== "number" || !Number.isFinite(value)) return null;
  const int = Math.trunc(value);
  return int;
}

function isCancellation(value: unknown): value is FxCancellation {
  return value === "none" || value === "replace" || value === "cancel";
}

/** Structural validation for one event; returns a problem string or null. */
export function checkFxEvent(value: unknown): string | null {
  if (!isRecord(value)) return "event must be an object";
  for (const key of REQUIRED_STRINGS) {
    if (!text(value[key])) return `${key} is required`;
  }
  if (!isCancellation(value.cancellation)) {
    return "cancellation must be none/replace/cancel";
  }
  const seed = finiteInt(value.seed);
  if (seed === null || seed < 0 || seed >= SEED_LIMIT) {
    return "seed must be a 31-bit non-negative integer";
  }
  const priority = finiteInt(value.priority);
  if (priority === null) return "priority must be an integer";
  if (typeof value.essential !== "boolean") return "essential must be boolean";
  if (typeof value.replay !== "boolean") return "replay must be boolean";
  if (typeof value.reduced_motion !== "boolean") {
    return "reduced_motion must be boolean";
  }
  return null;
}

export function isFxEvent(value: unknown): value is FxEvent {
  return checkFxEvent(value) === null;
}

function toFxEvent(value: Record<string, unknown>): FxEvent {
  return {
    schema: text(value.schema) || FX_SCHEMA,
    mode: text(value.mode) || FX_MODE,
    fx_event_id: text(value.fx_event_id),
    event_id: text(value.event_id),
    event_type: text(value.event_type),
    room: optionalText(value.room),
    agent: optionalText(value.agent),
    animation: text(value.animation),
    still: text(value.still),
    emote: optionalText(value.emote),
    particles: optionalText(value.particles),
    sound: optionalText(value.sound),
    seed: finiteInt(value.seed) ?? 0,
    priority: finiteInt(value.priority) ?? PRIORITY_NORMAL,
    cancellation: isCancellation(value.cancellation) ? value.cancellation : "none",
    replay: value.replay === true,
    reduced_motion: value.reduced_motion === true,
    fallback: text(value.fallback),
    essential: value.essential === true,
    group: text(value.group) || "agent",
  };
}

/**
 * Parse the wire payload. `null`/`undefined` is a typed `unavailable` state,
 * not an empty effect list; a malformed payload is `invalid`; a valid payload
 * with zero events is `ok` with an empty list (an empty event is a no-op).
 */
export function parseFxPayload(value: unknown): FxParseResult {
  if (value === null || value === undefined) {
    return {
      status: "unavailable",
      code: "FX_PAYLOAD_UNAVAILABLE",
      detail: "no FX payload was provided",
    };
  }
  if (!isRecord(value)) {
    return {
      status: "unavailable",
      code: "FX_PAYLOAD_INVALID",
      detail: "FX payload must be an object",
    };
  }
  if (value.schema !== FX_SCHEMA) {
    return {
      status: "unavailable",
      code: "FX_PAYLOAD_INVALID",
      detail: `FX schema must be ${FX_SCHEMA}`,
    };
  }
  if (value.mode !== FX_MODE) {
    return {
      status: "unavailable",
      code: "FX_PAYLOAD_INVALID",
      detail: `FX mode must be ${FX_MODE}`,
    };
  }
  if (value.business_truth !== false) {
    return {
      status: "unavailable",
      code: "FX_PAYLOAD_INVALID",
      detail: "FX payload must not claim business truth",
    };
  }
  if (!Array.isArray(value.events)) {
    return {
      status: "unavailable",
      code: "FX_PAYLOAD_INVALID",
      detail: "FX events must be an array",
    };
  }
  const events: FxEvent[] = [];
  const problems: string[] = [];
  value.events.forEach((raw, index) => {
    const problem = checkFxEvent(raw);
    if (problem) {
      problems.push(`events[${index}]: ${problem}`);
      return;
    }
    events.push(toFxEvent(raw as Record<string, unknown>));
  });
  return { status: "ok", events, problems };
}

/** Stable variant index derived from an effect seed (never Math.random). */
export function seededIndex(seed: number, length: number): number {
  if (!Number.isFinite(seed) || length <= 0) return 0;
  const normalized = ((Math.trunc(seed) % SEED_LIMIT) + SEED_LIMIT) % SEED_LIMIT;
  return normalized % Math.trunc(length);
}

interface FxSubject {
  group: string;
  agent: string | null;
  room: string | null;
}

function subjectKey(event: FxSubject): string {
  if (event.group === "agent") return `agent:${event.agent ?? ""}`;
  if (event.group === "room") return `room:${event.room ?? ""}`;
  return "office:";
}

export interface FxCueResolution {
  status: "ok" | "unavailable";
  animation: string;
  degraded: boolean;
  code?: FxUnavailableCode;
  detail?: string;
}

/**
 * Resolve the cue to play for one event. `assets` omitted means the backend
 * declared availability; a supplied set that lacks both the cue and its
 * fallback is a typed `FX_ASSET_MISSING` refusal.
 */
export function resolveCue(
  event: FxEvent,
  assets?: ReadonlySet<string>,
): FxCueResolution {
  if (assets === undefined) {
    return { status: "ok", animation: event.animation, degraded: false };
  }
  if (assets.has(event.animation)) {
    return { status: "ok", animation: event.animation, degraded: false };
  }
  if (assets.has(event.fallback)) {
    return { status: "ok", animation: event.fallback, degraded: true };
  }
  return {
    status: "unavailable",
    animation: event.fallback,
    degraded: true,
    code: "FX_ASSET_MISSING",
    detail: `atlas cue "${event.animation}" and fallback "${event.fallback}" are missing`,
  };
}

function boundedCapacity(value: number | undefined): number {
  if (!Number.isInteger(value) || (value ?? 0) < 1) return DEFAULT_FX_CAPACITY;
  return value as number;
}

interface Working {
  effect: FxActiveEffect;
  seq: number;
}

function worstIndex(items: Working[]): number {
  let worst = 0;
  for (let index = 1; index < items.length; index += 1) {
    const candidate = items[index]!;
    const current = items[worst]!;
    if (
      candidate.effect.priority > current.effect.priority ||
      (candidate.effect.priority === current.effect.priority &&
        candidate.seq > current.seq)
    ) {
      worst = index;
    }
  }
  return worst;
}

function sortWorking(items: Working[]): FxActiveEffect[] {
  return [...items]
    .sort((a, b) =>
      a.effect.priority - b.effect.priority ||
      a.seq - b.seq ||
      a.effect.fx_event_id.localeCompare(b.effect.fx_event_id))
    .map((entry) => entry.effect);
}

/**
 * Plan a batch of FX events into a bounded, ordered effect list. This is the
 * pure decision step; it performs no side effect and touches no DOM.
 */
export function planFx(
  events: readonly FxEvent[],
  options: FxPlannerOptions = {},
): FxPlan {
  const capacity = boundedCapacity(options.capacity);
  let working: Working[] = [];
  const unavailable: FxUnavailableEffect[] = [];
  const seen = new Set<string>();
  let accepted = 0;
  let dropped = 0;
  let duplicates = 0;
  let rejected = 0;
  let seq = 0;

  for (const raw of events) {
    if (!isFxEvent(raw)) {
      rejected += 1;
      continue;
    }
    if (seen.has(raw.fx_event_id)) {
      duplicates += 1;
      continue;
    }
    seen.add(raw.fx_event_id);

    const reduced = raw.reduced_motion || options.reducedMotion === true;
    if (!raw.essential && (reduced || options.disableNonessential === true)) {
      dropped += 1;
      continue;
    }

    const effect = {
      ...raw,
      animation: reduced ? raw.still : raw.animation,
      particles: reduced ? null : raw.particles,
      replay: raw.replay || options.replay === true,
      reduced_motion: reduced,
      fallback: raw.fallback,
    };
    const cue = resolveCue(effect, options.assets);
    if (cue.status === "unavailable") {
      unavailable.push({
        fx_event_id: raw.fx_event_id,
        event_id: raw.event_id,
        event_type: raw.event_type,
        code: cue.code ?? "FX_ASSET_MISSING",
        detail: cue.detail ?? "cue unavailable",
        essential: raw.essential,
      });
      continue;
    }

    if (effect.cancellation === "replace" || effect.cancellation === "cancel") {
      const key = subjectKey(effect);
      working = working.filter((item) => subjectKey(item.effect) !== key);
    }

    if (working.length >= capacity) {
      const index = worstIndex(working);
      if (effect.priority >= working[index]!.effect.priority) {
        dropped += 1;
        continue;
      }
      working.splice(index, 1);
      dropped += 1;
    }

    const active: FxActiveEffect = {
      fx_event_id: effect.fx_event_id,
      event_id: effect.event_id,
      event_type: effect.event_type,
      room: effect.room,
      agent: effect.agent,
      animation: cue.animation,
      still: effect.still,
      emote: effect.emote,
      particles: effect.particles,
      sound: effect.sound,
      seed: effect.seed,
      priority: effect.priority,
      fallback: effect.fallback,
      essential: effect.essential,
      replay: effect.replay,
      reduced_motion: effect.reduced_motion,
      group: effect.group,
      degraded: cue.degraded,
    };
    working.push({ effect: active, seq });
    seq += 1;
    accepted += 1;
  }

  return {
    schema: FX_SCHEMA,
    capacity,
    active: sortWorking(working),
    unavailable,
    accepted,
    dropped,
    duplicates,
    rejected,
    replay: options.replay === true,
  };
}

export function emptyFxState(capacity = DEFAULT_FX_CAPACITY): FxState {
  return {
    schema: FX_SCHEMA,
    capacity: boundedCapacity(capacity),
    active: [],
    unavailable: [],
    seen: [],
    replay: false,
  };
}

/** Reducer: apply a new batch to an existing plan/state, preserving order. */
export function applyFx(
  state: FxState,
  events: readonly FxEvent[],
  options: FxPlannerOptions = {},
): FxState {
  const seenSet = new Set(state.seen);
  const fresh = events.filter(
    (event) => !isFxEvent(event) || !seenSet.has(event.fx_event_id),
  );
  const merged: FxEvent[] = [
    ...state.active.map(activeToEvent),
    ...fresh,
  ];
  const plan = planFx(merged, {
    ...options,
    capacity: options.capacity ?? state.capacity,
  });
  const seen = [...state.seen];
  for (const event of events) {
    if (isFxEvent(event) && !seen.includes(event.fx_event_id)) {
      seen.push(event.fx_event_id);
    }
  }
  const seenLimit = Math.max(64, (options.capacity ?? state.capacity) * 4);
  return {
    schema: FX_SCHEMA,
    capacity: plan.capacity,
    active: plan.active,
    unavailable: [...state.unavailable, ...plan.unavailable].slice(-seenLimit),
    seen: seen.slice(-seenLimit),
    replay: plan.replay,
  };
}

function activeToEvent(active: FxActiveEffect): FxEvent {
  return {
    schema: FX_SCHEMA,
    mode: FX_MODE,
    fx_event_id: active.fx_event_id,
    event_id: active.event_id,
    event_type: active.event_type,
    room: active.room,
    agent: active.agent,
    animation: active.animation,
    still: active.still,
    emote: active.emote,
    particles: active.particles,
    sound: active.sound,
    seed: active.seed,
    priority: active.priority,
    cancellation: "none",
    replay: active.replay,
    reduced_motion: active.reduced_motion,
    fallback: active.fallback,
    essential: active.essential,
    group: active.group,
  };
}

export function cancelFx(state: FxState, fxEventId: string): FxState {
  return {
    ...state,
    active: state.active.filter((event) => event.fx_event_id !== fxEventId),
  };
}

export function cancelFxSubject(
  state: FxState,
  subject: { agent?: string | null; room?: string | null; group?: string },
): FxState {
  const group =
    subject.group ?? (subject.agent ? "agent" : subject.room ? "room" : "office");
  const key =
    group === "agent"
      ? `agent:${subject.agent ?? ""}`
      : group === "room"
        ? `room:${subject.room ?? ""}`
        : "office:";
  const keyOf = (event: FxActiveEffect): string => {
    if (event.agent && group === "agent") return `agent:${event.agent}`;
    if (event.room && group === "room") return `room:${event.room}`;
    return "office:";
  };
  return {
    ...state,
    active: state.active.filter((event) => keyOf(event) !== key),
  };
}

/** Deterministic replay: same source events -> same ordered effects + seeds. */
export function replayFx(
  events: readonly FxEvent[],
  options: FxPlannerOptions = {},
): FxPlan {
  return planFx(events, { ...options, replay: true });
}
