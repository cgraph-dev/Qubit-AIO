/* P7-04 — the FX client planner (pure; no DOM). */
import { describe, expect, it } from "vitest";
import {
  CANCELLATIONS,
  DEFAULT_FX_CAPACITY,
  FX_MODE,
  FX_SCHEMA,
  PRIORITY_CRITICAL,
  PRIORITY_HIGH,
  PRIORITY_LOW,
  PRIORITY_NORMAL,
  applyFx,
  cancelFx,
  cancelFxSubject,
  checkFxEvent,
  emptyFxState,
  isFxEvent,
  parseFxPayload,
  planFx,
  replayFx,
  resolveCue,
  seededIndex,
  type FxEvent,
} from "./fx";

function fx(overrides: Partial<FxEvent> = {}): FxEvent {
  return {
    schema: FX_SCHEMA,
    mode: FX_MODE,
    fx_event_id: "fx_1",
    event_id: "e1",
    event_type: "agent.turn.started",
    room: "room-a",
    agent: "agent-a",
    animation: "typing",
    still: "typing-still",
    emote: "focus",
    particles: null,
    sound: null,
    seed: 1234,
    priority: PRIORITY_NORMAL,
    cancellation: "none",
    replay: false,
    reduced_motion: false,
    fallback: "typing-still",
    essential: false,
    group: "agent",
    ...overrides,
  };
}

function payload(events: unknown[] | null) {
  return {
    schema: FX_SCHEMA,
    version: 1,
    mode: FX_MODE,
    business_truth: false,
    events,
  };
}

describe("FX payload parsing", () => {
  it("treats a missing payload as typed unavailable, not an empty plan", () => {
    expect(parseFxPayload(null)).toMatchObject({
      status: "unavailable",
      code: "FX_PAYLOAD_UNAVAILABLE",
    });
    expect(parseFxPayload(undefined)).toMatchObject({
      status: "unavailable",
      code: "FX_PAYLOAD_UNAVAILABLE",
    });
  });

  it("refuses a payload that claims business truth or the wrong schema", () => {
    expect(parseFxPayload({ ...payload([]), business_truth: true })).toMatchObject({
      status: "unavailable",
      code: "FX_PAYLOAD_INVALID",
    });
    expect(parseFxPayload({ ...payload([]), schema: "nope" })).toMatchObject({
      status: "unavailable",
      code: "FX_PAYLOAD_INVALID",
    });
  });

  it("accepts an empty event list and reports malformed rows honestly", () => {
    const empty = parseFxPayload(payload([]));
    expect(empty.status).toBe("ok");
    if (empty.status === "ok") expect(empty.events).toEqual([]);

    const mixed = parseFxPayload(payload([fx(), { event_id: "e2" }]));
    expect(mixed.status).toBe("ok");
    if (mixed.status === "ok") {
      expect(mixed.events).toHaveLength(1);
      expect(mixed.problems).toHaveLength(1);
      expect(mixed.problems[0]).toContain("events[1]");
    }
  });

  it("validates every declared cancellation value", () => {
    for (const value of CANCELLATIONS) {
      expect(checkFxEvent(fx({ cancellation: value }))).toBeNull();
    }
    expect(checkFxEvent(fx({ cancellation: "explode" as never }))).toContain(
      "cancellation",
    );
    expect(isFxEvent(fx())).toBe(true);
  });
});

describe("FX planning", () => {
  it("never fabricates an effect from an empty plan", () => {
    const plan = planFx([]);
    expect(plan.active).toEqual([]);
    expect(plan.accepted).toBe(0);
    expect(plan.rejected).toBe(0);
    expect(plan.unavailable).toEqual([]);
  });

  it("orders by priority then insertion, deterministically", () => {
    const low = fx({ fx_event_id: "fx_low", event_id: "low", priority: PRIORITY_LOW });
    const normal = fx({ fx_event_id: "fx_normal", event_id: "normal" });
    const critical = fx({
      fx_event_id: "fx_crit",
      event_id: "critical",
      priority: PRIORITY_CRITICAL,
      essential: true,
    });
    const plan = planFx([low, normal, critical]);
    expect(plan.active.map((item) => item.event_id)).toEqual([
      "critical",
      "normal",
      "low",
    ]);
    expect(planFx([low, normal, critical])).toEqual(plan);
  });

  it("dedupes by fx_event_id", () => {
    const event = fx();
    const plan = planFx([event, event]);
    expect(plan.active).toHaveLength(1);
    expect(plan.duplicates).toBe(1);
  });

  it("replaces only the same agent subject", () => {
    const workA = fx({ fx_event_id: "fx_a", event_id: "a", agent: "agent-a" });
    const workB = fx({ fx_event_id: "fx_b", event_id: "b", agent: "agent-b" });
    const doneA = fx({
      fx_event_id: "fx_done_a",
      event_id: "done-a",
      agent: "agent-a",
      event_type: "agent.turn.completed",
      priority: PRIORITY_HIGH,
      cancellation: "replace",
      essential: true,
    });
    const plan = planFx([workA, workB, doneA]);
    expect(plan.active.map((item) => item.event_id)).toEqual(["done-a", "b"]);
  });

  it("cancel policy removes prior work and keeps the stop cue", () => {
    const work = fx({ fx_event_id: "fx_w", event_id: "work", agent: "agent-a" });
    const stop = fx({
      fx_event_id: "fx_s",
      event_id: "stop",
      agent: "agent-a",
      event_type: "agent.stopped",
      priority: PRIORITY_CRITICAL,
      cancellation: "cancel",
      essential: true,
    });
    const plan = planFx([work, stop]);
    expect(plan.active.map((item) => item.event_id)).toEqual(["stop"]);
  });

  it("bounds the plan and drops the least important effect", () => {
    const low = fx({ fx_event_id: "fx_low", event_id: "low", priority: PRIORITY_LOW });
    const normal = fx({ fx_event_id: "fx_n", event_id: "normal" });
    const critical = fx({
      fx_event_id: "fx_c",
      event_id: "critical",
      priority: PRIORITY_CRITICAL,
      essential: true,
    });
    const plan = planFx([low, normal, critical], { capacity: 2 });
    expect(plan.capacity).toBe(2);
    expect(plan.active.map((item) => item.event_id)).toEqual(["critical", "normal"]);
    expect(plan.dropped).toBe(1);

    const tooLow = fx({ fx_event_id: "fx_x", event_id: "x", priority: PRIORITY_LOW });
    const full = planFx(
      [fx({ fx_event_id: "fx_1", event_id: "1", priority: PRIORITY_CRITICAL }),
       fx({ fx_event_id: "fx_2", event_id: "2", priority: PRIORITY_CRITICAL }),
       tooLow],
      { capacity: 2 },
    );
    expect(full.active.map((item) => item.event_id)).toEqual(["1", "2"]);
    expect(full.dropped).toBe(1);
  });
});

describe("reduced motion and nonessential effects", () => {
  it("drops nonessential motion but keeps critical cues as a still", () => {
    const ambient = fx({ fx_event_id: "fx_amb", event_id: "ambient" });
    const done = fx({
      fx_event_id: "fx_done",
      event_id: "done",
      event_type: "agent.turn.completed",
      priority: PRIORITY_HIGH,
      essential: true,
      animation: "celebrate",
      still: "status-check",
      particles: "confetti",
    });
    const plan = planFx([ambient, done], { reducedMotion: true });
    expect(plan.active).toHaveLength(1);
    expect(plan.active[0]!.event_id).toBe("done");
    expect(plan.active[0]!.animation).toBe("status-check");
    expect(plan.active[0]!.particles).toBeNull();
    expect(plan.active[0]!.reduced_motion).toBe(true);
  });

  it("honours an event already marked reduced_motion", () => {
    const still = fx({
      fx_event_id: "fx_s",
      event_id: "still",
      essential: true,
      reduced_motion: true,
      animation: "status-check",
      still: "status-check",
    });
    const plan = planFx([still]);
    expect(plan.active[0]!.animation).toBe("status-check");
    expect(plan.active[0]!.reduced_motion).toBe(true);
  });

  it("disable-nonessential keeps only essential cues", () => {
    const plan = planFx(
      [
        fx({ fx_event_id: "fx_a", event_id: "a" }),
        fx({ fx_event_id: "fx_b", event_id: "b", essential: true }),
      ],
      { disableNonessential: true },
    );
    expect(plan.active.map((item) => item.event_id)).toEqual(["b"]);
    expect(plan.dropped).toBe(1);
  });
});

describe("asset fallback and typed unavailable", () => {
  it("uses the declared fallback when the atlas cue is missing", () => {
    const event = fx({
      fx_event_id: "fx_done",
      event_id: "done",
      animation: "celebrate",
      fallback: "status-check",
      essential: true,
    });
    const plan = planFx([event], { assets: new Set(["status-check"]) });
    expect(plan.active).toHaveLength(1);
    expect(plan.active[0]!.animation).toBe("status-check");
    expect(plan.active[0]!.degraded).toBe(true);
    expect(plan.unavailable).toEqual([]);
  });

  it("keeps the primary cue when the atlas has it", () => {
    const event = fx({ animation: "celebrate", fallback: "status-check" });
    expect(resolveCue(event, new Set(["celebrate"]))).toMatchObject({
      status: "ok",
      animation: "celebrate",
      degraded: false,
    });
  });

  it("reports a typed unavailable effect when cue and fallback are both missing", () => {
    const event = fx({
      fx_event_id: "fx_missing",
      event_id: "missing",
      animation: "celebrate",
      fallback: "status-check",
      essential: true,
    });
    const plan = planFx([event], { assets: new Set() });
    expect(plan.active).toEqual([]);
    expect(plan.unavailable).toHaveLength(1);
    expect(plan.unavailable[0]).toMatchObject({
      fx_event_id: "fx_missing",
      event_id: "missing",
      code: "FX_ASSET_MISSING",
      essential: true,
    });
  });
});

describe("seeds and replay", () => {
  it("derives a stable variant index from the seed", () => {
    expect(seededIndex(1234, 5)).toBe(1234 % 5);
    expect(seededIndex(1234, 5)).toBe(seededIndex(1234, 5));
    expect(seededIndex(-1, 5)).toBeGreaterThanOrEqual(0);
    expect(seededIndex(7, 0)).toBe(0);
  });

  it("replays deterministically with the same seeds and labels", () => {
    const events = [
      fx({ fx_event_id: "fx_1", event_id: "1", seed: 11 }),
      fx({ fx_event_id: "fx_2", event_id: "2", seed: 22 }),
    ];
    const first = replayFx(events);
    const second = replayFx(events);
    expect(first).toEqual(second);
    expect(first.active.map((item) => item.seed)).toEqual([11, 22]);
    expect(first.active.every((item) => item.replay)).toBe(true);
    expect(first.replay).toBe(true);
  });
});

describe("reducer", () => {
  it("applies a new batch and cancels/replaces by subject", () => {
    let state = emptyFxState();
    expect(state.capacity).toBe(DEFAULT_FX_CAPACITY);
    state = applyFx(state, [fx({ fx_event_id: "fx_w", event_id: "work" })]);
    expect(state.active.map((item) => item.event_id)).toEqual(["work"]);
    state = applyFx(state, [
      fx({
        fx_event_id: "fx_d",
        event_id: "done",
        event_type: "agent.turn.completed",
        cancellation: "replace",
        essential: true,
      }),
    ]);
    expect(state.active.map((item) => item.event_id)).toEqual(["done"]);
    expect(state.seen).toEqual(["fx_w", "fx_d"]);
  });

  it("ignores a repeated fx_event_id across batches", () => {
    const event = fx({ fx_event_id: "fx_once", event_id: "once" });
    let state = applyFx(emptyFxState(), [event]);
    state = applyFx(state, [event]);
    expect(state.active).toHaveLength(1);
  });

  it("cancels one effect or a whole subject", () => {
    const a = fx({ fx_event_id: "fx_a", event_id: "a", agent: "agent-a" });
    const b = fx({ fx_event_id: "fx_b", event_id: "b", agent: "agent-b" });
    const c = fx({ fx_event_id: "fx_c", event_id: "c", agent: "agent-c" });
    let state = applyFx(emptyFxState(), [a, b, c]);
    state = cancelFx(state, "fx_a");
    expect(state.active.map((item) => item.event_id)).toEqual(["b", "c"]);
    state = cancelFxSubject(state, { agent: "agent-b" });
    expect(state.active.map((item) => item.event_id)).toEqual(["c"]);
  });
});
