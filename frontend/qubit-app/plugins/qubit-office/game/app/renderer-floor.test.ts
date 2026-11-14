import { describe, expect, it } from "vitest";
import {
  DirtyTracker,
  LRUCache,
  RendererFloorError,
  StaticBakeTracker,
  activateFloor,
  clampCamera,
  coalesceClientEvents,
  contextRenderMode,
  createFloorSet,
  effectPolicy,
  floorBounds,
  renderFloors,
  resolveDeviceTier,
  roomBounds,
  transitionContextLoss,
  visibleChunks,
  visibleRooms,
} from "./renderer-floor";

const ROOM_A = {
  room_id: "a",
  cells: [{ cell_id: "a:c0", origin: { x: 0, y: 0 }, cols: 16, rows: 12 }],
};
const ROOM_B = {
  room_id: "b",
  cells: [{ cell_id: "b:c0", origin: { x: 34, y: 0 }, cols: 16, rows: 12 }],
};
const BOUNDS = floorBounds([ROOM_A, ROOM_B]);

describe("active floor", () => {
  it("keeps exactly one active floor and draws only that floor", () => {
    const state = createFloorSet(["f1", "f2", "f3"], "f2");
    expect(state.activeFloorId).toBe("f2");
    expect(renderFloors(state)).toEqual(["f2"]);
    const next = activateFloor(state, "f3");
    expect(renderFloors(next)).toEqual(["f3"]);
    expect(renderFloors(next)).toHaveLength(1);
  });

  it("refuses an unknown or empty floor instead of inventing one", () => {
    expect(() => createFloorSet([])).toThrow(RendererFloorError);
    try {
      createFloorSet([]);
    } catch (error) {
      expect((error as RendererFloorError).code).toBe("RENDERER_FLOOR_EMPTY");
    }
    const state = createFloorSet(["f1"]);
    expect(() => activateFloor(state, "nope")).toThrow(RendererFloorError);
    try {
      activateFloor(state, "nope");
    } catch (error) {
      expect((error as RendererFloorError).code).toBe("RENDERER_FLOOR_UNKNOWN");
    }
  });
});

describe("camera clamp", () => {
  it("clamps the viewport inside the floor", () => {
    const bounds = { minX: 0, minY: 0, maxX: 100, maxY: 80 };
    expect(clampCamera({ x: -10, y: -5, width: 30, height: 20 }, bounds))
      .toEqual({ x: 0, y: 0, width: 30, height: 20 });
    expect(clampCamera({ x: 90, y: 70, width: 30, height: 20 }, bounds))
      .toEqual({ x: 70, y: 60, width: 30, height: 20 });
  });

  it("centres a viewport larger than the floor", () => {
    const bounds = { minX: 0, minY: 0, maxX: 100, maxY: 80 };
    expect(clampCamera({ x: 10, y: 10, width: 120, height: 100 }, bounds))
      .toEqual({ x: -10, y: -10, width: 120, height: 100 });
  });

  it("is deterministic for repeated calls", () => {
    const camera = { x: 5, y: 6, width: 20, height: 10 };
    expect(clampCamera(camera, BOUNDS)).toEqual(clampCamera(camera, BOUNDS));
  });
});

describe("visibility and chunks", () => {
  it("returns only rooms that intersect the camera", () => {
    expect(roomBounds(ROOM_A)).toEqual({ minX: 0, minY: 0, maxX: 16, maxY: 12 });
    expect(visibleRooms([ROOM_A, ROOM_B], { x: 0, y: 0, width: 20, height: 20 }))
      .toEqual([ROOM_A]);
    expect(visibleRooms([ROOM_A, ROOM_B], { x: 34, y: 0, width: 10, height: 10 }))
      .toEqual([ROOM_B]);
    expect(visibleRooms([ROOM_A, ROOM_B], { x: 200, y: 200, width: 10, height: 10 }))
      .toEqual([]);
  });

  it("returns sorted chunk keys covered by the viewport", () => {
    const chunks = visibleChunks(
      { x: 0, y: 0, width: 32, height: 16 }, BOUNDS, 16,
    );
    expect(chunks.map((chunk) => chunk.key)).toEqual(["0,0", "1,0"]);
    expect(visibleChunks({ x: 999, y: 999, width: 5, height: 5 }, BOUNDS, 16))
      .toEqual([]);
  });
});

describe("bounded LRU cache", () => {
  it("evicts the least-recently used entry and stays bounded", () => {
    const cache = new LRUCache<string, number>(3);
    cache.set("a", 1);
    cache.set("b", 2);
    cache.set("c", 3);
    expect(cache.get("a")).toBe(1);
    cache.set("d", 4);
    expect(cache.has("a")).toBe(true);
    expect(cache.has("b")).toBe(false);
    expect(cache.has("c")).toBe(true);
    expect(cache.size).toBe(3);
    expect(cache.stats().evictions).toBe(1);
  });

  it("refuses a non-positive capacity", () => {
    expect(() => new LRUCache(0)).toThrow(RendererFloorError);
  });
});

describe("static bake and dirty flags", () => {
  it("reuses a bake across camera moves and invalidates on a state change", () => {
    const tracker = new StaticBakeTracker();
    expect(tracker.needsBake("room-1", 1)).toBe(true);
    expect(tracker.bake("room-1", 1)).toBe("baked");
    expect(tracker.bake("room-1", 1)).toBe("reused");
    tracker.noteCameraMove();
    tracker.noteCameraMove();
    expect(tracker.needsBake("room-1", 1)).toBe(false);
    expect(tracker.bake("room-1", 2)).toBe("invalidated");
    expect(tracker.revisionOf("room-1")).toBe(2);
    expect(tracker.snapshot()).toMatchObject({ cameraMoves: 2, bakes: 2 });
  });

  it("marks only the changed room dirty after a full draw", () => {
    const dirty = new DirtyTracker();
    expect(dirty.isFull()).toBe(true);
    dirty.clear();
    expect(dirty.isFull()).toBe(false);
    dirty.markRoom("room-2");
    expect(dirty.isRoomDirty("room-2")).toBe(true);
    expect(dirty.isRoomDirty("room-1")).toBe(false);
    expect(dirty.snapshot()).toEqual({ full: false, rooms: ["room-2"] });
  });
});

describe("device tiers", () => {
  it("resolves desktop by default and low from real signals", () => {
    expect(resolveDeviceTier({}).tier).toBe("desktop");
    const low = resolveDeviceTier({ saveData: true, deviceMemoryGb: 2 });
    expect(low.tier).toBe("low");
    expect(low.reasons).toEqual(["save_data", "low_memory"]);
    expect(low.limits.frameP95Ms).toBe(33.3);
    expect(resolveDeviceTier({ hardwareConcurrency: 2 }).tier).toBe("low");
  });
});

describe("context-loss state machine", () => {
  it("recovers through lost and recovering", () => {
    const lost = transitionContextLoss("ready", "context_lost");
    expect(lost.state).toBe("lost");
    const recovering = transitionContextLoss(lost.state, "restore_started");
    expect(recovering.state).toBe("recovering");
    expect(contextRenderMode(recovering.state)).toBe("suspended");
    expect(transitionContextLoss(recovering.state, "restore_succeeded").state)
      .toBe("ready");
    expect(contextRenderMode("ready")).toBe("gpu");
  });

  it("falls back to a static renderer when restore fails", () => {
    const fallback = transitionContextLoss("recovering", "restore_failed");
    expect(fallback.state).toBe("fallback");
    expect(contextRenderMode(fallback.state)).toBe("static");
    expect(transitionContextLoss(fallback.state, "reset").state).toBe("ready");
  });

  it("stays suspended when no fallback exists and ignores invalid events", () => {
    const lost = transitionContextLoss("recovering", "restore_failed", {
      fallbackAvailable: false,
    });
    expect(lost.state).toBe("lost");
    const ignored = transitionContextLoss("ready", "restore_succeeded");
    expect(ignored.changed).toBe(false);
    expect(ignored.state).toBe("ready");
    expect(ignored.ignored).not.toBeNull();
  });
});

describe("reduced motion", () => {
  it("drops decoration but keeps every status cue", () => {
    const policy = effectPolicy({ reducedMotion: true });
    expect(policy.effects.ambient).toBe(false);
    expect(policy.effects.particles).toBe(false);
    expect(policy.effects.cameraTween).toBe(false);
    expect(policy.effects.screenShake).toBe(false);
    expect(policy.effects.weather).toBe(false);
    expect(policy.statusCues).toEqual(["working", "needs_you", "error", "unknown"]);
    expect(effectPolicy({ reducedMotion: false }).effects.ambient).toBe(true);
  });

  it("keeps status cues on a low-tier device", () => {
    const policy = effectPolicy({ tier: "low" });
    expect(policy.effects.ambient).toBe(false);
    expect(policy.statusCues).toContain("working");
    expect(policy.statusCues).toContain("error");
  });
});

describe("event coalescing", () => {
  it("collapses consecutive same-kind events in one second", () => {
    const batch = coalesceClientEvents(
      Array.from({ length: 100 }, () => ({ kind: "presence", at: 5.2 })),
    );
    expect(batch.windows).toHaveLength(1);
    expect(batch.windows[0].raw).toBe(100);
    expect(batch.windows[0].emitted).toBe(100);
    expect(batch.windows[0].events).toEqual([{ kind: "presence", count: 100 }]);
    expect(batch.windows[0].coalesced).toBe(99);
  });

  it("caps a busy window at the burst budget and keeps windows separate", () => {
    const events = [
      ...Array.from({ length: 80 }, (_, i) => ({ kind: `k${i}`, at: 1 })),
      { kind: "late", at: 2 },
    ];
    const batch = coalesceClientEvents(events, { burstLimit: 60 });
    expect(batch.windows[0].emitted).toBe(60);
    expect(batch.windows[0].dropped).toBe(20);
    expect(batch.windows[1].events).toEqual([{ kind: "late", count: 1 }]);
    expect(batch.emitted).toBe(61);
    expect(batch.dropped).toBe(20);
  });

  it("is deterministic and handles an empty stream", () => {
    const events = [
      { kind: "a", at: 0.1 },
      { kind: "b", at: 0.2 },
      { kind: "a", at: 0.3 },
    ];
    expect(coalesceClientEvents(events)).toEqual(coalesceClientEvents(events));
    expect(coalesceClientEvents([])).toEqual({
      windows: [], emitted: 0, dropped: 0, coalesced: 0,
    });
  });
});
