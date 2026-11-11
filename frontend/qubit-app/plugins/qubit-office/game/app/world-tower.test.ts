import { describe, expect, it } from "vitest";
import type { FloorTemplate } from "./business-api";
import { recordingFetch, respond } from "./business-test-harness";
import {
  CELL_COLS, CELL_ROWS, buildTemplateWorldTower, fetchWorldTower,
  planCellCluster, templateFloorScene, worldUrl,
} from "./world-tower";

const WINGS = ["north", "east", "south", "west", "central"];

function realFloors(): FloorTemplate[] {
  return Array.from({ length: 100 }, (_, i) => {
    const index = i + 1;
    const wing = WINGS[Math.floor(i / 20)];
    return {
      floor_id: `floor-${String(index).padStart(3, "0")}`,
      index,
      wing,
      name: `${wing[0].toUpperCase()}${wing.slice(1)} Wing · `
        + `Floor ${String(index).padStart(3, "0")}`,
      department_ids: [`dept-${String((i % 22) + 1).padStart(2, "0")}`],
      role_ids: [`role-${index}-a`, `role-${index}-b`],
      room_count: 3,
      desk_capacity: [39, 27, 33][i % 3],
      active_by_default: index <= 22,
      max_rooms: null,
      max_agents: null,
      expansion_allowed: true,
    };
  });
}

function endpointPayload() {
  const base = buildTemplateWorldTower(realFloors());
  return {
    ok: true,
    schema: "qubit-office-world-tower/1",
    source: "templates",
    staffed: false,
    campus: { campus_id: "campus:office", name: "Qubit Office" },
    counts: base.counts,
    wings: base.wings,
    floors: base.floors,
  };
}

describe("world tower client", () => {
  it("builds the endpoint URL under the agent prefix", () => {
    expect(worldUrl("/tower", "")).toBe("/agent/api/office/world/tower");
    expect(worldUrl("/tower", "/base/")).toBe("/base/agent/api/office/world/tower");
  });

  it("reads the endpoint payload and keeps 100 floors / 5 wings", async () => {
    const { calls, impl } = recordingFetch(() => respond(200, endpointPayload()));
    const tower = await fetchWorldTower(realFloors(), { fetchImpl: impl });
    expect(calls[0].url).toContain("/agent/api/office/world/tower");
    expect(tower.source).toBe("endpoint");
    expect(tower.counts.floors).toBe(100);
    expect(tower.counts.wings).toBe(5);
    expect(tower.counts.agents).toBe(0);
    expect(tower.staffed).toBe(false);
    expect(tower.floors[0].rooms.length).toBe(3);
    expect(tower.floors[0].rooms[0].cells.length).toBeGreaterThan(0);
  });

  it("falls back to the directory templates on 404", async () => {
    const { impl } = recordingFetch(() =>
      respond(404, { ok: false, code: "NOT_FOUND", detail: "no world route" }));
    const tower = await fetchWorldTower(realFloors(), { fetchImpl: impl });
    expect(tower.source).toBe("template");
    expect(tower.fallback_reason).toMatch(/404/);
    expect(tower.counts.floors).toBe(100);
    expect(tower.counts.wings).toBe(5);
    expect(tower.counts.active_floors).toBe(22);
    expect(tower.counts.dormant_floors).toBe(78);
    expect(tower.staffed).toBe(false);
    for (const floor of tower.floors) {
      expect(floor.rooms).toHaveLength(3);
      for (const room of floor.rooms) {
        expect(room.template).toBe(true);
        for (const cell of room.cells) {
          expect(cell.cols).toBe(CELL_COLS);
          expect(cell.rows).toBe(CELL_ROWS);
          for (const slot of cell.desk_slots) {
            expect(slot.occupied_by).toBeNull();
          }
        }
      }
    }
  });

  it("derives template rooms when endpoint floors carry only counts", async () => {
    const floors = realFloors().map((floor) => ({
      floor_id: floor.floor_id,
      index: floor.index,
      wing: floor.wing,
      name: floor.name,
      active_by_default: floor.active_by_default,
      department_ids: floor.department_ids,
      role_ids: floor.role_ids,
      room_count: floor.room_count,
      desk_capacity: floor.desk_capacity,
    }));
    const { impl } = recordingFetch(() => respond(200, {
      ok: true, schema: "qubit-office-world-tower/1", floors,
    }));
    const tower = await fetchWorldTower(realFloors(), { fetchImpl: impl });
    expect(tower.source).toBe("endpoint");
    expect(tower.floors[0].rooms).toHaveLength(3);
    expect(tower.floors[0].rooms.every((room) => room.template)).toBe(true);
    expect(tower.floors[0].rooms[0].cells[0].cols).toBe(16);
  });

  it("falls back on a network error and on a malformed body", async () => {
    const network = (async () => {
      throw new Error("connection refused");
    }) as unknown as typeof fetch;
    const a = await fetchWorldTower(realFloors(), { fetchImpl: network });
    expect(a.source).toBe("template");
    expect(a.fallback_reason).toContain("connection refused");

    const { impl } = recordingFetch(() => respond(200, { ok: true, floors: [] }));
    const b = await fetchWorldTower(realFloors(), { fetchImpl: impl });
    expect(b.source).toBe("template");
    expect(b.counts.floors).toBe(100);
  });

  it("never invents agents or occupants in the template projection", () => {
    const tower = buildTemplateWorldTower(realFloors());
    expect(tower.counts.agents).toBe(0);
    expect(tower.staffed).toBe(false);
    expect(tower.floors.every((floor) =>
      floor.rooms.every((room) => room.template))).toBe(true);
    for (const floor of tower.floors) {
      for (const room of floor.rooms) {
        for (const cell of room.cells) {
          for (const slot of cell.desk_slots) {
            expect(slot.occupied_by).toBeNull();
          }
        }
      }
    }
  });

  it("distributes the exact desk capacity and keeps 16x12 cells", () => {
    const floor = realFloors()[0];
    const scene = templateFloorScene(floor);
    expect(scene.capacity.desks).toBe(floor.desk_capacity);
    expect(scene.rooms.reduce((sum, room) => sum + room.capacity, 0))
      .toBe(floor.desk_capacity);
    const cells = planCellCluster("room-x", 9);
    expect(cells).toHaveLength(2);
    expect(cells.flatMap((cell) => cell.desk_slots)).toHaveLength(9);
    expect(cells.every((cell) => cell.cols === 16 && cell.rows === 12))
      .toBe(true);
  });
});
