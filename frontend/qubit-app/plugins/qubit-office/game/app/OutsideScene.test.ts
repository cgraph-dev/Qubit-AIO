/** @vitest-environment jsdom */
/* The visual scene in a real DOM: the canvas host, the explicit fallback
 * label, the real floor anchors and the optional art path. jsdom has no
 * Canvas2D, so the test installs a recording context; that also proves the
 * component degrades to a message instead of crashing when a device cannot
 * paint.
 */
import * as React from "react";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createOutsideScene } from "./OutsideScene";
import { OUTSIDE_SCHEMA, outsideWorldView } from "./outside-world";
import type { OutsideSceneArt } from "./outside-scene";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean })
  .IS_REACT_ACT_ENVIRONMENT = true;

function payload() {
  return {
    schema: OUTSIDE_SCHEMA, version: 1, mode: "presentation",
    business_truth: false, map_digest: "m", digest: "d", credit: "LimeZu",
    environment: {
      weather: "rain", day_night: "night", season: "autumn",
      weather_values: ["rain"], day_night_values: ["night"],
      season_values: ["autumn"],
      events: [
        { event_id: "w", kind: "weather", value: "rain", label: "Rain",
          cue: "rain-streaks", still_cue: "rain-static", animated: true },
        { event_id: "d", kind: "day_night", value: "night", label: "Night",
          cue: "dark", still_cue: "dark", animated: false },
        { event_id: "s", kind: "season", value: "autumn", label: "Autumn",
          cue: "leaves", still_cue: "leaves-static", animated: true },
      ],
    },
    zones: [
      { zone_id: "zone-campus", kind: "campus", label: "Campus exterior",
        state: "active", animated: true, entry_chunk: "chunk-ground",
        chunk_ids: ["chunk-ground", "chunk-entrance"],
        bounds: { x: 0, y: 0, width: 24, height: 16 },
        counts: { chunks: 2, active_chunks: 2, animated_chunks: 1,
          tiles: 400 } },
      { zone_id: "zone-parking", kind: "parking", label: "Visitor parking",
        state: "dormant", animated: false, entry_chunk: "chunk-parking",
        chunk_ids: ["chunk-parking"],
        bounds: { x: 0, y: 16, width: 10, height: 6 },
        counts: { chunks: 1, active_chunks: 0, animated_chunks: 0,
          tiles: 60 } },
    ],
    chunks: [
      { chunk_id: "chunk-ground", zone_id: "zone-campus", kind: "ground",
        layer: "ground", bounds: { x: 0, y: 0, width: 24, height: 12 },
        tiles: 288, active: true, animated: false, state: "active",
        assets: [], z_index: 0 },
      { chunk_id: "chunk-entrance", zone_id: "zone-campus", kind: "entrance",
        layer: "building", bounds: { x: 8, y: 8, width: 8, height: 4 },
        tiles: 32, active: true, animated: true, state: "active",
        assets: [], z_index: 4 },
      { chunk_id: "chunk-parking", zone_id: "zone-parking", kind: "parking",
        layer: "overlay", bounds: { x: 0, y: 16, width: 10, height: 6 },
        tiles: 60, active: false, animated: false, state: "dormant",
        assets: [], z_index: 1 },
    ],
    places: [],
    portals: [
      { portal_id: "portal-1", kind: "elevator", from_chunk: "chunk-entrance",
        to_chunk: "", to_floor: "floor-001", label: "Elevator to floor-001",
        bidirectional: true, capacity: 8, locked: false },
      { portal_id: "portal-2", kind: "stairs", from_chunk: "chunk-entrance",
        to_chunk: "", to_floor: "floor-001", label: "Stairs to floor-001",
        bidirectional: true, capacity: 4, locked: false },
    ],
    floor_links: [
      { kind: "elevator", floor_id: "floor-001", portal_id: "portal-1",
        label: "Take the elevator to floor-001" },
      { kind: "stairs", floor_id: "floor-001", portal_id: "portal-2",
        label: "Take the stairs to floor-001" },
    ],
    assets: [],
    summary: {
      zones: 2, active_zones: 1, dormant_zones: 1, chunks: 3,
      active_chunks: 2, dormant_chunks: 1, animated_chunks: 1, portals: 2,
      elevator_links: 1, stairs_links: 1, places: 0, assets: 0,
    },
    constraints: {
      presentation_only: true, business_truth: false,
      only_active_zones_animate: true, max_chunks: 256,
      max_active_chunks: 96, max_chunk_tiles: 4096, lru_capacity: 48,
      preload_depth: 1, lazy_load: true, reduced_motion_supported: true,
    },
  };
}

function recordingContext() {
  const calls: string[] = [];
  const gradient = { addColorStop: () => {} };
  const ctx = {
    clearRect: () => calls.push("clearRect"),
    createLinearGradient: () => gradient,
    fillRect: () => calls.push("fillRect"),
    strokeRect: () => calls.push("strokeRect"),
    beginPath: () => {},
    moveTo: () => {},
    lineTo: () => {},
    stroke: () => {},
    fill: () => {},
    arc: () => {},
    save: () => {},
    restore: () => {},
    translate: () => {},
    setLineDash: () => {},
    fillText: () => {},
    drawImage: () => calls.push("drawImage"),
    clip: () => {},
    rect: () => {},
    imageSmoothingEnabled: true,
    fillStyle: "", strokeStyle: "", lineWidth: 1, font: "",
    textAlign: "left", textBaseline: "alphabetic", globalAlpha: 1,
  };
  return { ctx, calls };
}

let container: HTMLDivElement;
let root: Root;
let getContext: ReturnType<typeof vi.spyOn>;

beforeEach(() => {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  getContext = vi.spyOn(HTMLCanvasElement.prototype, "getContext")
    .mockImplementation(() => recordingContext().ctx as never);
});

afterEach(async () => {
  await act(async () => { root.unmount(); });
  container.remove();
  getContext.mockRestore();
  delete (window as { __QUBIT_OFFICE_OUTSIDE_ART__?: unknown })
    .__QUBIT_OFFICE_OUTSIDE_ART__;
});

async function render(props: Record<string, unknown> = {}) {
  const Scene = createOutsideScene(React as never, React as never);
  const view = outsideWorldView({ payload: payload() });
  await act(async () => {
    root.render(React.createElement(Scene as never, { view, ...props }));
  });
}

function q<T extends Element>(selector: string): T {
  const node = container.querySelector<T>(selector);
  if (!node) throw new Error(`missing ${selector}`);
  return node;
}

describe("OutsideScene in a real DOM", () => {
  it("paints a canvas from the payload and labels the procedural fallback", async () => {
    await render();
    const scene = q('[data-testid="outside-scene"]');
    expect(scene.getAttribute("data-art-status")).toBe("absent");
    expect(scene.getAttribute("data-art-source")).toBe("procedural");
    const canvas = q<HTMLCanvasElement>('[data-testid="outside-scene-canvas"]');
    expect(canvas.width).toBe(24 * 8);
    expect(canvas.height).toBe(22 * 8);
    expect(canvas.getAttribute("aria-label")).toContain("1 active of 2 zones");
    expect(q('[data-testid="outside-scene-fallback"]').textContent)
      .toContain("Procedural scene");
    expect(getContext).toHaveBeenCalled();
  });

  it("draws only the environment cues the payload carries", async () => {
    await render();
    const chips = Array.from(
      container.querySelectorAll('[data-testid="outside-scene-env"]'));
    expect(chips.map((chip) => chip.getAttribute("data-kind")))
      .toEqual(["weather", "day_night", "season"]);
    expect(chips.map((chip) => chip.textContent))
      .toEqual(["Rain", "Night", "Autumn"]);
  });

  it("renders one accessible floor anchor per located floor link", async () => {
    const onSelectFloor = vi.fn();
    await render({ onSelectFloor });
    const anchors = Array.from(
      container.querySelectorAll<HTMLAnchorElement>(
        '[data-testid="outside-scene-floor"]'));
    expect(anchors).toHaveLength(2);
    expect(anchors.map((a) => a.getAttribute("data-kind")))
      .toEqual(["elevator", "stairs"]);
    expect(anchors[0].getAttribute("href")).toBe(
      "/qubit-office?floor=floor-001");
    await act(async () => {
      anchors[0].dispatchEvent(new MouseEvent("click", {
        bubbles: true, cancelable: true,
      }));
    });
    expect(onSelectFloor).toHaveBeenCalledTimes(1);
    expect(onSelectFloor.mock.calls[0][0].portal_id).toBe("portal-1");
  });

  it("uses explicit LimeZu art when the host supplies it", async () => {
    const art: OutsideSceneArt = {
      credit: "LimeZu — https://limezu.itch.io",
      source: "injected",
      tiles: {
        grass: {
          image: document.createElement("canvas"),
          sx: 0, sy: 0, sw: 16, sh: 16, tile: 16,
        },
      },
    };
    await render({ art });
    const scene = q('[data-testid="outside-scene"]');
    expect(scene.getAttribute("data-art-status")).toBe("ready");
    expect(scene.getAttribute("data-art-source")).toBe("injected");
    expect(q('[data-testid="outside-scene-art"]').textContent)
      .toContain("LimeZu");
  });

  it("states the scene is unavailable when a device has no Canvas2D", async () => {
    getContext.mockImplementation(() => null);
    await render();
    expect(q('[data-testid="outside-scene-unavailable"]').textContent)
      .toContain("Visual scene unavailable");
    // The floor anchors and environment remain reachable without the canvas.
    expect(container.querySelectorAll('[data-testid="outside-scene-floor"]'))
      .toHaveLength(2);
  });

  it("renders nothing unless the view is ready", async () => {
    const Scene = createOutsideScene(React as never, React as never);
    const view = outsideWorldView({ payload: null });
    await act(async () => {
      root.render(React.createElement(Scene as never, { view }));
    });
    expect(container.querySelector('[data-testid="outside-scene"]')).toBeNull();
  });
});
