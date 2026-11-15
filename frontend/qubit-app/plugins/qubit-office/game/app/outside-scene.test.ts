/* The outside-world scene's pure contract: payload mapping, honest fallbacks
 * and the no-invented-data rule. No DOM is needed here; the component test
 * next to this file covers the canvas host and its floor anchors.
 */
import { describe, expect, it, vi } from "vitest";
import {
  type OutsideSceneLayout, OUTSIDE_SCENE_ART_SCHEMA, SCENE_TILE_PX,
  artKindForChunk, buildOutsideScene, drawOutsideScene, environmentPalette,
  hash01, loadOutsideSceneArt, orderedSceneChunks,
  parseOutsideSceneArtManifest, sceneDescription, sceneFloorLinks,
} from "./outside-scene";
import {
  OUTSIDE_SCHEMA, outsideWorldView, parseOutsideWorld,
} from "./outside-world";

const CREDIT = "Office art: LimeZu — https://limezu.itch.io";

function payload(overrides: Record<string, unknown> = {}) {
  return {
    schema: OUTSIDE_SCHEMA, version: 1, mode: "presentation",
    business_truth: false, map_digest: "map", digest: "digest", credit: CREDIT,
    environment: {
      weather: "rain", day_night: "night", season: "autumn",
      weather_values: ["rain"], day_night_values: ["night"],
      season_values: ["autumn"],
      events: [
        { event_id: "w-rain", kind: "weather", value: "rain",
          label: "Rain", cue: "rain-streaks", still_cue: "rain-static",
          animated: true },
        { event_id: "d-night", kind: "day_night", value: "night",
          label: "Night", cue: "dark-blue", still_cue: "dark-blue",
          animated: false },
        { event_id: "s-autumn", kind: "season", value: "autumn",
          label: "Autumn", cue: "leaves", still_cue: "leaves-static",
          animated: true },
      ],
    },
    zones: [
      { zone_id: "zone-campus", kind: "campus", label: "Campus exterior",
        state: "active", animated: true, entry_chunk: "chunk-ground",
        chunk_ids: ["chunk-ground", "chunk-road", "chunk-entrance"],
        bounds: { x: 0, y: 0, width: 40, height: 24 },
        counts: { chunks: 3, active_chunks: 3, animated_chunks: 1,
          tiles: 900 } },
      { zone_id: "zone-garden", kind: "garden", label: "North garden",
        state: "active", animated: true, entry_chunk: "chunk-garden",
        chunk_ids: ["chunk-garden"],
        bounds: { x: 20, y: 0, width: 8, height: 8 },
        counts: { chunks: 1, active_chunks: 1, animated_chunks: 1,
          tiles: 64 } },
      { zone_id: "zone-parking", kind: "parking", label: "Visitor parking",
        state: "dormant", animated: false, entry_chunk: "chunk-parking",
        chunk_ids: ["chunk-parking"],
        bounds: { x: 0, y: 24, width: 12, height: 8 },
        counts: { chunks: 1, active_chunks: 0, animated_chunks: 0,
          tiles: 96 } },
    ],
    chunks: [
      { chunk_id: "chunk-ground", zone_id: "zone-campus", kind: "ground",
        layer: "ground", bounds: { x: 0, y: 0, width: 40, height: 16 },
        tiles: 640, active: true, animated: false, state: "active",
        assets: [], z_index: 0 },
      { chunk_id: "chunk-road", zone_id: "zone-campus", kind: "road",
        layer: "ground", bounds: { x: 0, y: 16, width: 40, height: 4 },
        tiles: 160, active: true, animated: false, state: "active",
        assets: [], z_index: 0 },
      { chunk_id: "chunk-entrance", zone_id: "zone-campus", kind: "entrance",
        layer: "building", bounds: { x: 14, y: 12, width: 10, height: 4 },
        tiles: 40, active: true, animated: true, state: "active",
        assets: [], z_index: 5 },
      { chunk_id: "chunk-garden", zone_id: "zone-garden", kind: "garden",
        layer: "overlay", bounds: { x: 22, y: 2, width: 6, height: 5 },
        tiles: 30, active: true, animated: true, state: "active",
        assets: [], z_index: 2 },
      { chunk_id: "chunk-parking", zone_id: "zone-parking", kind: "parking",
        layer: "overlay", bounds: { x: 0, y: 24, width: 12, height: 8 },
        tiles: 96, active: false, animated: false, state: "dormant",
        assets: [], z_index: 1 },
      { chunk_id: "chunk-orphan", zone_id: "zone-missing", kind: "prop",
        layer: "overlay", bounds: { x: 50, y: 50, width: 4, height: 4 },
        tiles: 16, active: true, animated: false, state: "active",
        assets: [], z_index: 3 },
    ],
    places: [],
    portals: [
      { portal_id: "portal-elevator-001", kind: "elevator",
        from_chunk: "chunk-entrance", to_chunk: "", to_floor: "floor-001",
        label: "Elevator to floor-001", bidirectional: true, capacity: 8,
        locked: false },
      { portal_id: "portal-stairs-001", kind: "stairs",
        from_chunk: "chunk-entrance", to_chunk: "", to_floor: "floor-001",
        label: "Stairs to floor-001", bidirectional: true, capacity: 4,
        locked: false },
      { portal_id: "portal-unknown-chunk", kind: "elevator",
        from_chunk: "chunk-lobby-missing", to_chunk: "", to_floor: "floor-099",
        label: "Elevator to floor-099", bidirectional: true, capacity: 8,
        locked: false },
    ],
    floor_links: [
      { kind: "elevator", floor_id: "floor-001",
        portal_id: "portal-elevator-001", label: "Take the elevator to floor-001" },
      { kind: "stairs", floor_id: "floor-001",
        portal_id: "portal-stairs-001", label: "Take the stairs to floor-001" },
      { kind: "elevator", floor_id: "floor-099",
        portal_id: "portal-unknown-chunk", label: "Take the elevator to floor-099" },
      { kind: "elevator", floor_id: "floor-404",
        portal_id: "portal-absent", label: "Take the elevator to floor-404" },
    ],
    assets: [],
    summary: {
      zones: 3, active_zones: 2, dormant_zones: 1, chunks: 6,
      active_chunks: 4, dormant_chunks: 2, animated_chunks: 2, portals: 3,
      elevator_links: 2, stairs_links: 1, places: 0, assets: 0,
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

function world(overrides: Record<string, unknown> = {}) {
  return parseOutsideWorld(payload(overrides));
}

function layoutOf(overrides: Record<string, unknown> = {}): OutsideSceneLayout {
  return buildOutsideScene(world(overrides));
}

function keysDeep(value: unknown, found: string[] = []): string[] {
  if (Array.isArray(value)) {
    for (const item of value) keysDeep(item, found);
    return found;
  }
  if (value && typeof value === "object") {
    for (const [key, item] of Object.entries(value)) {
      found.push(key);
      keysDeep(item, found);
    }
  }
  return found;
}

describe("buildOutsideScene", () => {
  it("maps only payload zones and chunks, in a deterministic order", () => {
    const layout = layoutOf();
    expect(layout.zones.map((zone) => zone.zone_id))
      .toEqual(["zone-campus", "zone-garden", "zone-parking"]);
    expect(layout.chunks.map((chunk) => chunk.chunk_id).sort()).toEqual([
      "chunk-entrance", "chunk-garden", "chunk-ground",
      "chunk-parking", "chunk-road",
    ]);
    expect(layout.unplacedChunks).toBe(1);
    expect(layout.chunks.some((chunk) => chunk.chunk_id === "chunk-orphan"))
      .toBe(false);
    expect(orderedSceneChunks(layout).map((chunk) => chunk.chunk_id))
      .toEqual([
        "chunk-ground", "chunk-road", "chunk-entrance",
        "chunk-parking", "chunk-garden",
      ]);
  });

  it("derives the canvas extent from the payload's own chunk bounds", () => {
    const layout = layoutOf();
    expect(layout.bounds).toEqual({ x: 0, y: 0, width: 40, height: 32 });
    expect(layout.width).toBe(40);
    expect(layout.height).toBe(32);
    expect(layout.tileSize).toBe(16);
    expect(SCENE_TILE_PX).toBe(8);
  });

  it("keeps dormant zones dormant and maps chunk kinds to art kinds", () => {
    const layout = layoutOf();
    const parking = layout.zones.find((zone) => zone.zone_id === "zone-parking");
    expect(parking?.state).toBe("dormant");
    expect(parking?.animated).toBe(false);
    expect(layout.chunks.find((chunk) => chunk.chunk_id === "chunk-parking")
      ?.animated).toBe(false);
    expect(artKindForChunk("ground")).toBe("grass");
    expect(artKindForChunk("elevator_bank")).toBe("elevator");
    expect(artKindForChunk("mystery")).toBeNull();
  });

  it("places floor markers only where the payload has a portal and a chunk", () => {
    const layout = layoutOf();
    expect(layout.floors.map((floor) => floor.portal_id)).toEqual([
      "portal-elevator-001", "portal-stairs-001",
    ]);
    expect(layout.floors.map((floor) => floor.floor_id))
      .toEqual(["floor-001", "floor-001"]);
    const links = sceneFloorLinks(world(), layout);
    expect(links.map((link) => link.portal_id)).toEqual([
      "portal-elevator-001", "portal-stairs-001",
    ]);
    expect(links.some((link) => link.floor_id === "floor-404")).toBe(false);
  });

  it("carries the environment and summary without inventing axes", () => {
    const layout = layoutOf();
    expect(layout.environment.weather).toBe("rain");
    expect(layout.environment.events.map((event) => event.kind))
      .toEqual(["weather", "day_night", "season"]);
    expect(layout.summary.active_zones).toBe(2);
    expect(sceneDescription(layout)).toContain("2 active of 3 zones");
  });

  it("has no business-truth or occupancy key anywhere in the scene model", () => {
    const keys = new Set(keysDeep(layoutOf()));
    for (const forbidden of [
      "agent", "agents", "agent_id", "occupant", "occupants", "desk",
      "desk_id", "seat", "seat_id", "run", "run_id", "task", "task_id",
      "business_id", "tenant_id", "session_id",
    ]) {
      expect(keys.has(forbidden)).toBe(false);
    }
  });

  it("is deterministic: identical payloads produce identical layouts", () => {
    expect(layoutOf()).toEqual(layoutOf());
    expect(hash01(7)).toBe(hash01(7));
  });
});

describe("environmentPalette", () => {
  it("uses known day/season palettes and flags unknown values", () => {
    const night = environmentPalette("night", "autumn", "rain");
    expect(night.dayNightKnown).toBe(true);
    expect(night.seasonKnown).toBe(true);
    expect(night.weatherKnown).toBe(true);
    expect(night.tintAlpha).toBeGreaterThan(0.3);
    const odd = environmentPalette("eclipse", "monsoon", "hail");
    expect(odd.dayNightKnown).toBe(false);
    expect(odd.seasonKnown).toBe(false);
    expect(odd.weatherKnown).toBe(false);
  });
});

describe("outside art manifest", () => {
  const manifest = {
    schema: OUTSIDE_SCENE_ART_SCHEMA,
    credit: "LimeZu — https://limezu.itch.io",
    cell: 16,
    tiles: {
      grass: { sheet: "outside/ground.png", col: 2, row: 1 },
      road: { sheet: "outside/ground.png", col: 3, row: 1 },
    },
  };

  it("accepts a valid manifest and drops unknown kinds", () => {
    const parsed = parseOutsideSceneArtManifest({
      ...manifest,
      tiles: { ...manifest.tiles, magic: { sheet: "x.png", col: 0, row: 0 } },
    });
    expect(parsed?.cell).toBe(16);
    expect(Object.keys(parsed?.tiles ?? {}).sort()).toEqual(["grass", "road"]);
  });

  it("rejects unsafe or empty manifests instead of faking art", () => {
    expect(parseOutsideSceneArtManifest(null)).toBeNull();
    expect(parseOutsideSceneArtManifest({ ...manifest, schema: "x" })).toBeNull();
    expect(parseOutsideSceneArtManifest({ ...manifest, cell: 0 })).toBeNull();
    expect(parseOutsideSceneArtManifest({
      ...manifest,
      tiles: { grass: { sheet: "javascript:alert(1)", col: 0, row: 0 } },
    })).toBeNull();
  });

  it("loads tiles through an injected image loader", async () => {
    const loadImage = vi.fn(async () => ({
      image: {} as CanvasImageSource, width: 64, height: 64,
    }));
    const result = await loadOutsideSceneArt({
      manifest,
      manifestUrl: "https://example.test/theme/outside/scene.json",
      loadImage,
    });
    expect(result.status).toBe("ready");
    if (result.status !== "ready") return;
    expect(result.art.source).toBe("theme");
    expect(result.art.tiles.grass?.sx).toBe(32);
    expect(result.art.tiles.grass?.sy).toBe(16);
    // One sheet, loaded once even though two tiles reference it.
    expect(loadImage).toHaveBeenCalledTimes(1);
    expect(loadImage.mock.calls[0][0])
      .toBe("https://example.test/theme/outside/outside/ground.png");
  });

  it("is absent without a manifest and errors on a rejected one", async () => {
    const absent = await loadOutsideSceneArt({ manifest: null });
    expect(absent.status).toBe("absent");
    const bad = await loadOutsideSceneArt({ manifest: { schema: "nope" } });
    expect(bad.status).toBe("error");
    const missingPixels = await loadOutsideSceneArt({
      manifest,
      loadImage: async () => ({
        image: {} as CanvasImageSource, width: 16, height: 16,
      }),
    });
    // The frame at col 2 is outside a 16x16 sheet: no usable tiles.
    expect(missingPixels.status).toBe("error");
  });
});

describe("drawOutsideScene", () => {
  function recorder() {
    const calls: Array<[string, ...unknown[]]> = [];
    const gradient = { addColorStop: vi.fn() };
    const ctx: Record<string, unknown> = {
      clearRect: (...args: unknown[]) => calls.push(["clearRect", ...args]),
      createLinearGradient: (...args: unknown[]) => {
        calls.push(["createLinearGradient", ...args]);
        return gradient;
      },
      fillRect: (...args: unknown[]) => calls.push(["fillRect", ...args]),
      strokeRect: (...args: unknown[]) => calls.push(["strokeRect", ...args]),
      beginPath: () => calls.push(["beginPath"]),
      moveTo: (...args: unknown[]) => calls.push(["moveTo", ...args]),
      lineTo: (...args: unknown[]) => calls.push(["lineTo", ...args]),
      stroke: () => calls.push(["stroke"]),
      fill: () => calls.push(["fill"]),
      arc: (...args: unknown[]) => calls.push(["arc", ...args]),
      save: () => calls.push(["save"]),
      restore: () => calls.push(["restore"]),
      translate: (...args: unknown[]) => calls.push(["translate", ...args]),
      setLineDash: (...args: unknown[]) => calls.push(["setLineDash", ...args]),
      fillText: (...args: unknown[]) => calls.push(["fillText", ...args]),
      drawImage: (...args: unknown[]) => calls.push(["drawImage", ...args]),
      clip: () => calls.push(["clip"]),
      rect: (...args: unknown[]) => calls.push(["rect", ...args]),
      imageSmoothingEnabled: true,
      fillStyle: "", strokeStyle: "", lineWidth: 1, font: "",
      textAlign: "left", textBaseline: "alphabetic", globalAlpha: 1,
    };
    return { ctx: ctx as unknown as CanvasRenderingContext2D, calls };
  }

  it("paints identically for the same layout and options", () => {
    const layout = layoutOf();
    const first = recorder();
    const second = recorder();
    drawOutsideScene(first.ctx, layout, { selectedZoneId: "zone-campus" });
    drawOutsideScene(second.ctx, layout, { selectedZoneId: "zone-campus" });
    expect(first.calls).toEqual(second.calls);
    expect(first.calls.length).toBeGreaterThan(20);
    expect(first.calls.some(([name]) => name === "clearRect")).toBe(true);
  });

  it("uses real art frames when supplied and the procedural path otherwise", () => {
    const layout = layoutOf();
    const procedural = recorder();
    drawOutsideScene(procedural.ctx, layout);
    expect(procedural.calls.some(([name]) => name === "drawImage")).toBe(false);

    const art = {
      credit: "LimeZu",
      source: "theme" as const,
      tiles: {
        grass: {
          image: {} as CanvasImageSource, sx: 0, sy: 0, sw: 16, sh: 16,
          tile: 16,
        },
        road: {
          image: {} as CanvasImageSource, sx: 16, sy: 0, sw: 16, sh: 16,
          tile: 16,
        },
      },
    };
    const painted = recorder();
    drawOutsideScene(painted.ctx, layout, { art });
    expect(painted.calls.some(([name]) => name === "drawImage")).toBe(true);
  });

  it("only marks a selected zone that the payload actually has", () => {
    const layout = layoutOf();
    const selected = recorder();
    drawOutsideScene(selected.ctx, layout, { selectedZoneId: "zone-missing" });
    expect(selected.calls.some(([name]) => name === "setLineDash")).toBe(false);

    const real = recorder();
    drawOutsideScene(real.ctx, layout, { selectedZoneId: "zone-campus" });
    expect(real.calls.some(([name]) => name === "setLineDash")).toBe(true);
  });
});

describe("outsideWorldView integration", () => {
  it("draws from the same view status the semantic panel uses", () => {
    const view = outsideWorldView({ payload: payload() });
    expect(view.status).toBe("ready");
    expect(buildOutsideScene(view.world!).chunks).toHaveLength(5);
    expect(outsideWorldView({ payload: null }).world).toBeNull();
  });
});
