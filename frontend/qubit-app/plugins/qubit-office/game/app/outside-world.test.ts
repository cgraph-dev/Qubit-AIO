/* P6-03 — the outside-world client/view model (no DOM).
 *
 * Pins the projection contract the panel depends on: parsing, presentation-only
 * refusal, loading/empty/error/unavailable mapping, deterministic ordering, the
 * environment cue lookup and the read-only fetch. No canvas, no network.
 */
import { describe, expect, it, vi } from "vitest";
import {
  OUTSIDE_PATH,
  OUTSIDE_SCHEMA,
  OutsideWorldError,
  currentEnvironment,
  fetchOutsideWorld,
  floorHref,
  orderedZones,
  outsideWorldUrl,
  outsideWorldView,
  parseOutsideWorld,
  stateLabel,
  zoneChunks,
} from "./outside-world";

const CREDIT = "Office art: LimeZu — https://limezu.itch.io";

function payload(overrides: Record<string, unknown> = {}): Record<string, any> {
  return {
    schema: OUTSIDE_SCHEMA,
    version: 1,
    mode: "presentation",
    business_truth: false,
    map_digest: "map-digest",
    digest: "projection-digest",
    credit: CREDIT,
    environment: {
      weather: "rain",
      day_night: "night",
      season: "autumn",
      weather_values: ["clear", "rain"],
      day_night_values: ["day", "night"],
      season_values: ["summer", "autumn"],
      events: [
        { event_id: "weather-rain", kind: "weather", value: "rain",
          label: "Rain", cue: "rain-streaks", still_cue: "rain-static",
          animated: true },
        { event_id: "day-night", kind: "day_night", value: "night",
          label: "Night", cue: "dark-blue", still_cue: "dark-blue",
          animated: false },
        { event_id: "season-autumn", kind: "season", value: "autumn",
          label: "Autumn", cue: "falling-leaves", still_cue: "leaves-static",
          animated: true },
      ],
    },
    zones: [
      {
        zone_id: "zone-campus", kind: "campus", label: "Campus exterior",
        state: "active", animated: true, entry_chunk: "chunk-ground",
        chunk_ids: ["chunk-ground", "chunk-road"],
        bounds: { x: 0, y: 0, width: 48, height: 16 },
        counts: { chunks: 2, active_chunks: 2, animated_chunks: 1,
                  tiles: 768 },
      },
      {
        zone_id: "zone-parking", kind: "parking", label: "Visitor parking",
        state: "dormant", animated: false, entry_chunk: "chunk-parking",
        chunk_ids: ["chunk-parking"],
        bounds: { x: 0, y: 16, width: 24, height: 8 },
        counts: { chunks: 1, active_chunks: 0, animated_chunks: 0,
                  tiles: 192 },
      },
    ],
    chunks: [
      { chunk_id: "chunk-ground", zone_id: "zone-campus", kind: "ground",
        layer: "ground", bounds: { x: 0, y: 0, width: 48, height: 16 },
        tiles: 768, active: true, animated: false, state: "active",
        assets: ["asset-ground"], z_index: 0 },
      { chunk_id: "chunk-road", zone_id: "zone-campus", kind: "road",
        layer: "ground", bounds: { x: 0, y: 16, width: 48, height: 4 },
        tiles: 192, active: true, animated: true, state: "active",
        assets: ["asset-road"], z_index: 1 },
      { chunk_id: "chunk-parking", zone_id: "zone-parking", kind: "parking",
        layer: "overlay", bounds: { x: 0, y: 20, width: 24, height: 8 },
        tiles: 192, active: false, animated: true, state: "dormant",
        assets: ["asset-parking"], z_index: 2 },
    ],
    places: [
      { place_id: "place-ground", kind: "ground", label: "Campus · ground",
        zone_id: "zone-campus", chunk_id: "chunk-ground", active: true,
        animated: false },
      { place_id: "place-lobby", kind: "lobby", label: "Tower lobby",
        zone_id: "zone-campus", chunk_id: "chunk-ground", active: true,
        animated: false },
    ],
    portals: [
      { portal_id: "portal-elevator-001", kind: "elevator",
        from_chunk: "chunk-ground", to_chunk: "", to_floor: "floor-001",
        label: "Elevator to floor-001", bidirectional: true, capacity: 8,
        locked: false },
      { portal_id: "portal-stairs-001", kind: "stairs",
        from_chunk: "chunk-ground", to_chunk: "", to_floor: "floor-001",
        label: "Stairs to floor-001", bidirectional: true, capacity: 4,
        locked: false },
    ],
    floor_links: [
      { kind: "elevator", floor_id: "floor-001",
        portal_id: "portal-elevator-001",
        label: "Take the elevator to floor-001" },
      { kind: "stairs", floor_id: "floor-001",
        portal_id: "portal-stairs-001",
        label: "Take the stairs to floor-001" },
    ],
    assets: [
      { asset_id: "asset-ground", feature: "ground", pack: "Serene",
        paid: false, local_only: true, redistribution_allowed: true,
        license_status: "unknown", credit: CREDIT },
    ],
    summary: {
      zones: 2, active_zones: 1, dormant_zones: 1, chunks: 3,
      active_chunks: 2, dormant_chunks: 1, animated_chunks: 1, portals: 2,
      elevator_links: 1, stairs_links: 1, places: 2, assets: 1,
    },
    constraints: {
      presentation_only: true, business_truth: false,
      only_active_zones_animate: true, max_chunks: 256,
      max_active_chunks: 96, max_chunk_tiles: 4096, lru_capacity: 48,
      preload_depth: 1, lazy_load: true, reduced_motion_supported: true,
    },
    ...overrides,
  };
}

describe("parseOutsideWorld", () => {
  it("normalizes a valid presentation payload", () => {
    const world = parseOutsideWorld(payload());
    expect(world.schema).toBe(OUTSIDE_SCHEMA);
    expect(world.mode).toBe("presentation");
    expect(world.business_truth).toBe(false);
    expect(world.zones).toHaveLength(2);
    expect(world.floor_links.map((link) => link.kind))
      .toEqual(["elevator", "stairs"]);
    expect(world.credit).toBe(CREDIT);
  });

  it("never leaves a dormant chunk animated", () => {
    const world = parseOutsideWorld(payload());
    const dormant = world.chunks.find((c) => c.chunk_id === "chunk-parking");
    expect(dormant?.state).toBe("dormant");
    expect(dormant?.active).toBe(false);
    expect(dormant?.animated).toBe(false);
    expect(dormant?.state).toBe("dormant");
  });

  it("refuses a payload that claims business truth", () => {
    expect(() => parseOutsideWorld(payload({ business_truth: true })))
      .toThrowError(OutsideWorldError);
    try {
      parseOutsideWorld(payload({ business_truth: true }));
    } catch (exc) {
      expect((exc as OutsideWorldError).code)
        .toBe("OUTSIDE_WORLD_TRUTH_VIOLATION");
    }
    expect(() => parseOutsideWorld(payload({ mode: "renderer" })))
      .toThrowError(/presentation only/);
  });

  it("refuses a payload that grew occupant/agent/run fields", () => {
    const dirty = payload();
    dirty.zones[0].agent_id = "agent-01";
    try {
      parseOutsideWorld(dirty);
      throw new Error("expected refusal");
    } catch (exc) {
      expect((exc as OutsideWorldError).code)
        .toBe("OUTSIDE_WORLD_TRUTH_VIOLATION");
      expect((exc as Error).message).toMatch(/agent_id/);
    }
  });

  it("refuses an unsupported schema/version and a malformed body", () => {
    try {
      parseOutsideWorld(payload({ schema: "other/1" }));
    } catch (exc) {
      expect((exc as OutsideWorldError).code)
        .toBe("OUTSIDE_WORLD_UNSUPPORTED_VERSION");
    }
    try {
      parseOutsideWorld(payload({ version: 2 }));
    } catch (exc) {
      expect((exc as OutsideWorldError).code)
        .toBe("OUTSIDE_WORLD_UNSUPPORTED_VERSION");
    }
    try {
      parseOutsideWorld(payload({ zones: null }));
    } catch (exc) {
      expect((exc as OutsideWorldError).code).toBe("OUTSIDE_WORLD_MALFORMED");
    }
    expect(() => parseOutsideWorld(null)).toThrowError(OutsideWorldError);
  });
});

describe("outsideWorldView", () => {
  it("maps loading, error, unavailable, empty and ready", () => {
    expect(outsideWorldView({ loading: true }).status).toBe("loading");
    const error = outsideWorldView({ error: "boom", code: "X" });
    expect(error.status).toBe("error");
    expect(error.code).toBe("X");
    expect(outsideWorldView({ payload: null }).status).toBe("unavailable");
    expect(outsideWorldView({ payload: payload({ zones: [] }) }).status)
      .toBe("empty");
    const ready = outsideWorldView({ payload: payload() });
    expect(ready.status).toBe("ready");
    expect(ready.world?.zones).toHaveLength(2);
    expect(ready.message).toMatch(/1 active of 2 zones/);
  });

  it("turns a malformed payload into a typed error view, never a crash", () => {
    const view = outsideWorldView({ payload: { schema: "nope" } });
    expect(view.status).toBe("error");
    expect(view.code).toBe("OUTSIDE_WORLD_UNSUPPORTED_VERSION");
    expect(view.world).toBeNull();
  });
});

describe("helpers", () => {
  it("orders active zones before dormant ones", () => {
    const world = parseOutsideWorld(payload());
    expect(orderedZones(world).map((z) => z.zone_id))
      .toEqual(["zone-campus", "zone-parking"]);
  });

  it("selects only the zone's chunks", () => {
    const world = parseOutsideWorld(payload());
    expect(zoneChunks(world, "zone-campus").map((c) => c.chunk_id))
      .toEqual(["chunk-ground", "chunk-road"]);
    expect(zoneChunks(world, "zone-parking").map((c) => c.chunk_id))
      .toEqual(["chunk-parking"]);
  });

  it("resolves the current environment events", () => {
    const world = parseOutsideWorld(payload());
    const events = currentEnvironment(world);
    expect(events.map((e) => e.value)).toEqual(["rain", "night", "autumn"]);
    expect(events.find((e) => e.kind === "weather")?.still_cue)
      .toBe("rain-static");
  });

  it("uses a non-color state label and a canonical floor href", () => {
    expect(stateLabel("active")).toBe("active");
    expect(stateLabel("dormant")).toBe("dormant");
    expect(floorHref("floor-001")).toBe("/qubit-office?floor=floor-001");
  });

  it("builds the read-only API url", () => {
    expect(outsideWorldUrl("")).toBe(`/agent${OUTSIDE_PATH}`);
    expect(outsideWorldUrl("/base/")).toBe(`/base/agent${OUTSIDE_PATH}`);
  });
});

describe("fetchOutsideWorld", () => {
  function response(status: number, body: unknown): Response {
    return {
      ok: status >= 200 && status < 300,
      status,
      json: async () => body,
    } as unknown as Response;
  }

  it("reads the projection behind a {world} envelope", async () => {
    const fetchImpl = vi.fn(async () => response(200, { world: payload() }));
    const view = await fetchOutsideWorld({ fetchImpl: fetchImpl as never });
    expect(view.status).toBe("ready");
    expect(fetchImpl).toHaveBeenCalledTimes(1);
    const [url, init] = fetchImpl.mock.calls[0] as unknown as [
      string, RequestInit,
    ];
    expect(url).toBe(`/agent${OUTSIDE_PATH}`);
    expect(init.method).toBe("GET");
    expect(init.credentials).toBe("same-origin");
  });

  it("treats 404 as unavailable, not a fabricated campus", async () => {
    const fetchImpl = vi.fn(async () => response(404, null));
    const view = await fetchOutsideWorld({ fetchImpl: fetchImpl as never });
    expect(view.status).toBe("unavailable");
    expect(view.world).toBeNull();
    expect(view.code).toBe("OUTSIDE_WORLD_UNAVAILABLE");
  });

  it("maps non-2xx to an error view and a thrown fetch to unavailable", async () => {
    const failing = vi.fn(async () => response(500, null));
    const error = await fetchOutsideWorld({ fetchImpl: failing as never });
    expect(error.status).toBe("error");
    expect(error.code).toBe("OUTSIDE_WORLD_HTTP_500");

    const throwing = vi.fn(async () => { throw new Error("offline"); });
    const unavailable = await fetchOutsideWorld({
      fetchImpl: throwing as never,
    });
    expect(unavailable.status).toBe("unavailable");
    expect(unavailable.code).toBe("OUTSIDE_WORLD_UNREACHABLE");
  });

  it("turns a malformed body into a typed error", async () => {
    const fetchImpl = vi.fn(async () => response(200, { world: { schema: "x" } }));
    const view = await fetchOutsideWorld({ fetchImpl: fetchImpl as never });
    expect(view.status).toBe("error");
  });
});
