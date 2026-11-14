/** @vitest-environment jsdom */
/* P6-03 — the outside-world panel in a real DOM.
 *
 * The recorder harness cannot execute child component functions, so this file
 * renders the real component with the real React and drives clicks, keypresses
 * and floor links through the browser event path. It also pins the non-ready
 * states and reduced motion, because those are acceptance items, not decoration.
 */
import * as React from "react";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createOutsideWorld } from "./OutsideWorld";
import { OUTSIDE_SCHEMA } from "./outside-world";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean })
  .IS_REACT_ACT_ENVIRONMENT = true;

const CREDIT = "Office art: LimeZu — https://limezu.itch.io";

function payload(zones = 2): Record<string, any> {
  const allZones = [
    {
      zone_id: "zone-campus", kind: "campus", label: "Campus exterior",
      state: "active", animated: true, entry_chunk: "chunk-ground",
      chunk_ids: ["chunk-ground", "chunk-road"],
      bounds: { x: 0, y: 0, width: 48, height: 16 },
      counts: { chunks: 2, active_chunks: 2, animated_chunks: 1, tiles: 768 },
    },
    {
      zone_id: "zone-parking", kind: "parking", label: "Visitor parking",
      state: "dormant", animated: false, entry_chunk: "chunk-parking",
      chunk_ids: ["chunk-parking"],
      bounds: { x: 0, y: 16, width: 24, height: 8 },
      counts: { chunks: 1, active_chunks: 0, animated_chunks: 0, tiles: 192 },
    },
  ];
  const chunks = [
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
      tiles: 192, active: false, animated: false, state: "dormant",
      assets: ["asset-parking"], z_index: 2 },
  ];
  return {
    schema: OUTSIDE_SCHEMA, version: 1, mode: "presentation",
    business_truth: false, map_digest: "m", digest: "d", credit: CREDIT,
    environment: {
      weather: "rain", day_night: "night", season: "autumn",
      weather_values: ["rain"], day_night_values: ["night"],
      season_values: ["autumn"],
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
    zones: allZones.slice(0, zones),
    chunks: zones > 1 ? chunks : chunks.slice(0, 2),
    places: [
      { place_id: "place-ground", kind: "ground", label: "Campus · ground",
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
      zones, active_zones: 1, dormant_zones: zones - 1, chunks: 3,
      active_chunks: 2, dormant_chunks: 1, animated_chunks: 1, portals: 2,
      elevator_links: 1, stairs_links: 1, places: 1, assets: 1,
    },
    constraints: {
      presentation_only: true, business_truth: false,
      only_active_zones_animate: true, max_chunks: 256,
      max_active_chunks: 96, max_chunk_tiles: 4096, lru_capacity: 48,
      preload_depth: 1, lazy_load: true, reduced_motion_supported: true,
    },
  };
}

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => { root.unmount(); });
  container.remove();
});

async function render(props: Record<string, unknown> = {}) {
  const Panel = createOutsideWorld(React as never, React as never);
  await act(async () => {
    root.render(React.createElement(Panel as never, props));
  });
}

function q<T extends Element>(selector: string): T {
  const node = container.querySelector<T>(selector);
  if (!node) throw new Error(`missing ${selector}`);
  return node;
}

function qa(selector: string): NodeListOf<Element> {
  return container.querySelectorAll(selector);
}

describe("OutsideWorld in a real DOM", () => {
  it("renders the semantic region, environment, zones and floor links", async () => {
    await render({ payload: payload() });
    const region = q('[data-testid="outside-world"]');
    expect(region.getAttribute("role")).toBe("region");
    expect(region.getAttribute("aria-label")).toBe("Campus outside world");
    expect(region.getAttribute("data-status")).toBe("ready");
    expect(region.getAttribute("data-reduced-motion")).toBe("false");

    expect(q('[data-testid="outside-world-environment-text"]').textContent)
      .toContain("Rain");
    expect(qa('[data-testid="outside-zone"]')).toHaveLength(2);
    expect(qa('[data-testid="outside-floor-link"]')).toHaveLength(2);
    expect(q('[data-testid="outside-world-credit"]').textContent)
      .toBe(CREDIT);
  });

  it("shows non-color status text, not colour alone", async () => {
    await render({ payload: payload() });
    const states = Array.from(qa('[data-testid="outside-zone-state"]'))
      .map((node) => node.textContent);
    expect(states).toEqual(["active", "dormant"]);
    const chunks = Array.from(qa('[data-testid="outside-chunk"]'))
      .map((node) => node.textContent);
    expect(chunks.join(" ")).toContain("active");
    expect(chunks.join(" ")).not.toMatch(/working|completed/);
  });

  it("moves the selection with the keyboard and updates the detail", async () => {
    await render({ payload: payload() });
    expect(q('[data-testid="outside-world-zone-detail"]')
      .getAttribute("data-zone")).toBe("zone-campus");

    await act(async () => {
      q('[data-zone="zone-campus"]').dispatchEvent(new KeyboardEvent(
        "keydown", { key: "ArrowDown", bubbles: true }));
    });
    expect(q('[data-testid="outside-world-zone-detail"]')
      .getAttribute("data-zone")).toBe("zone-parking");
    expect(q('[data-zone="zone-parking"]').getAttribute("aria-selected"))
      .toBe("true");
    // Roving tabindex: exactly one zone is tabbable.
    expect(qa('[data-testid="outside-zone"][tabindex="0"]')).toHaveLength(1);
    // The dormant zone's chunk list is rendered, and dormant chunks are still.
    expect(q('[data-testid="outside-chunk"]').getAttribute("data-state"))
      .toBe("dormant");
  });

  it("activates a zone on click and calls onSelectZone", async () => {
    const onSelectZone = vi.fn();
    await render({ payload: payload(), onSelectZone });
    await act(async () => {
      (q('[data-zone="zone-parking"]') as HTMLElement).click();
    });
    expect(onSelectZone).toHaveBeenCalledTimes(1);
    expect(onSelectZone.mock.calls[0][0].zone_id).toBe("zone-parking");
  });

  it("renders reduced motion with still cues and no animated markers", async () => {
    await render({ payload: payload(), reducedMotion: true });
    const region = q('[data-testid="outside-world"]');
    expect(region.getAttribute("data-reduced-motion")).toBe("true");
    expect(q('[data-testid="outside-world-environment-text"]').textContent)
      .toContain("rain-static");
    expect(q('[data-testid="outside-world-environment-text"]').textContent)
      .not.toContain("rain-streaks");
    expect(qa('[data-testid="outside-zone"][data-animated="true"]'))
      .toHaveLength(0);
  });

  it("intercepts a floor link when the host supplies onSelectFloor", async () => {
    const onSelectFloor = vi.fn();
    await render({ payload: payload(), onSelectFloor });
    const link = q('[data-testid="outside-floor-link"]');
    expect(link.getAttribute("href")).toBe("/qubit-office/floor/floor-001");
    await act(async () => {
      link.dispatchEvent(new MouseEvent("click", { bubbles: true,
        cancelable: true }));
    });
    expect(onSelectFloor).toHaveBeenCalledTimes(1);
    expect(onSelectFloor.mock.calls[0][0].floor_id).toBe("floor-001");
  });

  it("renders loading, unavailable, empty and error states with text", async () => {
    await render({ loading: true });
    expect(q('[data-testid="outside-world"]').getAttribute("data-status"))
      .toBe("loading");
    expect(q('[data-testid="outside-world-status"]').textContent)
      .toMatch(/Loading/);

    await render({ payload: null });
    expect(q('[data-testid="outside-world-status"]').textContent)
      .toMatch(/not available/);

    await render({ payload: payload(0) });
    expect(q('[data-testid="outside-world"]').getAttribute("data-status"))
      .toBe("empty");

    await render({ payload: { schema: "bad/1" } });
    expect(q('[data-testid="outside-world"]').getAttribute("data-status"))
      .toBe("error");
    expect(q('[data-testid="outside-world-code"]').textContent)
      .toContain("OUTSIDE_WORLD_UNSUPPORTED_VERSION");
  });

  it("retries from an error state and announces it", async () => {
    const onRetry = vi.fn();
    await render({ error: "The endpoint failed.", onRetry });
    expect(q('[data-testid="outside-world-status"]')
      .getAttribute("role")).toBe("alert");
    await act(async () => {
      (q('[data-testid="outside-world-retry"]') as HTMLElement).click();
    });
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("does not render a business-truth field", async () => {
    await render({ payload: payload() });
    const text = container.textContent ?? "";
    expect(text).not.toMatch(/working|completed|agent_id|desk_id/);
  });
});
