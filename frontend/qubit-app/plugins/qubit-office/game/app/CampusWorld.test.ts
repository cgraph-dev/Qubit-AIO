import { describe, expect, it } from "vitest";
import {
  buildCampusFloors, campusCameraFor, campusFloorStatus, CAMPUS_FLOOR_GAP,
  CAMPUS_FLOOR_HEIGHT, CAMPUS_FLOOR_WIDTH, CAMPUS_MAX_ZOOM, CAMPUS_MIN_ZOOM,
  layoutCampusFloors, liveFloorsFromSnapshot, stepCampusFloor,
  type CampusLiveFloor,
} from "./CampusWorld";
import type { FloorTemplate } from "./business-api";

function template(index: number, wing = "central"): FloorTemplate {
  return {
    floor_id: `floor-${String(index).padStart(3, "0")}`,
    index,
    wing,
    name: `${wing[0].toUpperCase()}${wing.slice(1)} Wing · `
      + `Floor ${String(index).padStart(3, "0")}`,
    department_ids: [`dept-${index}`],
    role_ids: [`role-${index}`],
    room_count: 3,
    desk_capacity: 24,
    active_by_default: index <= 3,
    max_rooms: null,
    max_agents: null,
    expansion_allowed: true,
  };
}

function live(number: number, agents: number): CampusLiveFloor {
  return {
    number,
    name: `Live floor ${number}`,
    role: `role-${number}`,
    rooms: 2,
    agents,
    characters: Array.from({ length: agents }, (_, i) => `agent-${number}-${i}`),
  };
}

function payload() {
  return {
    floorplan: {
      floors: [
        { number: 2, name: "Second", role: "ops", rooms: [{ id: 1 }, { id: 2 }] },
        { number: 1, name: "First", role: "exec", rooms: [{ id: 1 }] },
        { number: 1, name: "First duplicate", role: "exec", rooms: [] },
      ],
    },
    characters: [
      { agent_id: 10, slug: "alpha", position: { floor: 1 } },
      { agent_id: 11, slug: "bravo", position: { floor: 1 } },
      { agent_id: 12, slug: "charlie", position: { floor: 2 } },
      { agent_id: 13, slug: "unlisted", position: { floor: 99 } },
    ],
  };
}

describe("liveFloorsFromSnapshot", () => {
  it("groups real characters by their real floor and dedupes floor rows", () => {
    const rows = liveFloorsFromSnapshot(payload());
    expect(rows.map((row) => row.number)).toEqual([1, 2]);
    expect(rows[0]).toMatchObject({
      number: 1, name: "First", role: "exec", rooms: 1, agents: 2,
      characters: ["alpha", "bravo"],
    });
    expect(rows[1]).toMatchObject({
      number: 2, agents: 1, characters: ["charlie"],
    });
  });

  it("never invents a floor for a character the floorplan does not declare", () => {
    const rows = liveFloorsFromSnapshot(payload());
    expect(rows.some((row) => row.number === 99)).toBe(false);
    expect(rows.flatMap((row) => row.characters)).not.toContain("unlisted");
  });

  it("returns no floors for a missing or malformed payload", () => {
    expect(liveFloorsFromSnapshot(null)).toEqual([]);
    expect(liveFloorsFromSnapshot({})).toEqual([]);
    expect(liveFloorsFromSnapshot({ floorplan: { floors: [{}] } })).toEqual([]);
  });
});

describe("buildCampusFloors", () => {
  it("keeps live floors real and marks template floors unstaffed", () => {
    const floors = buildCampusFloors(
      [live(1, 2), live(2, 1)], [template(1), template(2), template(3)]);
    expect(floors.map((floor) => floor.number)).toEqual([1, 2, 3]);
    expect(floors[0]).toMatchObject({
      id: "live:1", live: true, agents: 2, scope: "live:1",
      characters: ["agent-1-0", "agent-1-1"],
    });
    expect(floors[1]).toMatchObject({ id: "live:2", live: true, agents: 1 });
    expect(floors[2]).toMatchObject({
      id: "floor-003", live: false, agents: 0, characters: [],
      scope: "floor-003",
    });
    expect(floors.every((floor) => floor.live || floor.agents === 0)).toBe(true);
  });

  it("never lets a template duplicate a real floor number", () => {
    const floors = buildCampusFloors([live(4, 1)], [template(1), template(4)]);
    expect(floors.map((floor) => floor.id)).toEqual(["floor-001", "live:4"]);
    expect(floors.find((floor) => floor.number === 4)?.live).toBe(true);
  });

  it("always includes live floors and fills the window nearest them", () => {
    const templates = Array.from({ length: 10 }, (_, i) => template(i + 1));
    const floors = buildCampusFloors(
      [live(1, 1), live(2, 1), live(3, 1)], templates, { maxFloors: 5 });
    expect(floors).toHaveLength(5);
    expect(floors.filter((floor) => floor.live)).toHaveLength(3);
    expect(floors.map((floor) => floor.number)).toEqual([1, 2, 3, 4, 5]);
    expect(floors.filter((floor) => !floor.live).every(
      (floor) => floor.agents === 0)).toBe(true);
  });

  it("uses template floors when there are no live floors", () => {
    const templates = Array.from({ length: 8 }, (_, i) => template(i + 1));
    const floors = buildCampusFloors([], templates, { maxFloors: 4 });
    expect(floors).toHaveLength(4);
    expect(floors.every((floor) => !floor.live && floor.agents === 0))
      .toBe(true);
  });

  it("does not stretch a smaller live set with invented floors", () => {
    const floors = buildCampusFloors([live(7, 2)], null, { maxFloors: 8 });
    expect(floors).toHaveLength(1);
    expect(floors[0]).toMatchObject({ number: 7, live: true, agents: 2 });
  });
});

describe("layoutCampusFloors", () => {
  it("places floors in one vertical tower with wing offsets", () => {
    const floors = buildCampusFloors(
      [live(1, 1)],
      [template(2, "north"), template(3, "south"), template(4, "central")]);
    const layout = layoutCampusFloors(floors);
    expect(layout.floors).toHaveLength(4);
    const ys = layout.floors.map((item) => item.y);
    expect(new Set(ys).size).toBe(4);
    for (let i = 1; i < ys.length; i += 1) {
      expect(ys[i] - ys[i - 1]).toBe(CAMPUS_FLOOR_HEIGHT + CAMPUS_FLOOR_GAP);
    }
    const xs = layout.floors.map((item) => item.x);
    expect(new Set(xs).size).toBeGreaterThan(1);
    expect(Math.min(...xs)).toBeGreaterThanOrEqual(0);
    expect(Math.min(...ys)).toBe(0);
    expect(layout.width).toBeGreaterThanOrEqual(CAMPUS_FLOOR_WIDTH);
    expect(layout.height).toBeGreaterThanOrEqual(CAMPUS_FLOOR_HEIGHT * 4);
  });

  it("normalises the stage origin so the camera can centre every floor", () => {
    const layout = layoutCampusFloors(buildCampusFloors([], [template(1, "north")]));
    expect(layout.floors[0].x).toBeGreaterThanOrEqual(0);
    expect(layout.floors[0].y).toBe(0);
  });
});

describe("campusCameraFor", () => {
  it("centres the focused floor under the shared camera", () => {
    const layout = layoutCampusFloors([buildCampusFloors(
      [live(1, 1), live(2, 1)], null)[1]]);
    const camera = campusCameraFor(
      layout.floors[0], { width: 1000, height: 800 }, 1);
    expect(camera.x).toBe(
      500 - (layout.floors[0].x + CAMPUS_FLOOR_WIDTH / 2));
    expect(camera.y).toBe(
      400 - (layout.floors[0].y + CAMPUS_FLOOR_HEIGHT / 2));
  });

  it("clamps zoom to the declared camera range", () => {
    expect(campusCameraFor(null, { width: 10, height: 10 }, 99).zoom)
      .toBe(CAMPUS_MAX_ZOOM);
    expect(campusCameraFor(null, { width: 10, height: 10 }, -99).zoom)
      .toBe(CAMPUS_MIN_ZOOM);
    expect(campusCameraFor(null, { width: 10, height: 10 }, Number.NaN).zoom)
      .toBe(1);
  });
});

describe("stepCampusFloor and status text", () => {
  const floors = buildCampusFloors(
    [live(1, 2), live(2, 0)], [template(3), template(4)]);

  it("walks the world order and clamps at the ends", () => {
    expect(stepCampusFloor(floors, null, 1)?.id).toBe("live:1");
    expect(stepCampusFloor(floors, "live:1", 1)?.id).toBe("live:2");
    expect(stepCampusFloor(floors, "floor-004", 1)?.id).toBe("floor-004");
    expect(stepCampusFloor(floors, "live:1", -1)?.id).toBe("live:1");
    expect(stepCampusFloor([], "live:1", 1)).toBeNull();
  });

  it("names real live occupancy and unstaffed templates differently", () => {
    expect(campusFloorStatus(floors[0])).toBe("Live · 2 agents · 2 rooms");
    expect(campusFloorStatus(floors[1])).toBe("Live · 0 agents · 2 rooms");
    expect(campusFloorStatus(floors[2])).toBe(
      "Unstaffed template · 3 rooms · 24 desks");
  });
});
