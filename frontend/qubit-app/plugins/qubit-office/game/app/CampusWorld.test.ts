import { describe, expect, it } from "vitest";
import {
  buildCampusFloors, campusFloorStatus, campusRoomStatus,
  liveFloorsFromSnapshot, stepCampusFloor, stepCampusRoom, templateRoomsFor,
  type CampusLiveFloor, type CampusRoom,
} from "./CampusWorld";
import type { FloorTemplate } from "./business-api";
import type { WorldTower } from "./world-tower";

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

function room(number: number, id: number, name: string, agents: string[],
              capacity = 4): CampusRoom {
  return {
    id: `f${number}r${id}`,
    name,
    kind: "office",
    template: false,
    active: true,
    capacity,
    agents,
  };
}

function live(number: number, names: string[]): CampusLiveFloor {
  const half = Math.ceil(names.length / 2);
  const roomDetails = names.length
    ? [
        room(number, 1, "Operations", names.slice(0, half)),
        room(number, 2, "Laboratory", names.slice(half)),
      ]
    : [];
  return {
    number,
    name: `Live floor ${number}`,
    role: `role-${number}`,
    rooms: roomDetails.length,
    agents: names.length,
    characters: names,
    roomDetails,
  };
}

function worldFixture(): WorldTower {
  return {
    ok: true,
    schema: "test",
    source: "endpoint",
    staffed: false,
    counts: {
      floors: 1, wings: 1, active_floors: 0, dormant_floors: 1, rooms: 2,
      cells: 0, desks: 12, active_rooms: 0, dormant_rooms: 2, agents: 0,
    },
    wings: [],
    floors: [{
      floor_id: "floor-003",
      index: 3,
      wing: "north",
      wing_name: "North Wing",
      name: "North Wing · Floor 003",
      active: false,
      active_by_default: false,
      department_ids: [],
      role_ids: [],
      room_count: 2,
      desk_capacity: 12,
      cells: 0,
      capacity: { rooms: 2, cells: 0, desks: 12 },
      rooms: [{
        room_id: "floor-003-room-1",
        name: "Records",
        kind: "archive",
        archetype: "archive",
        role_ids: [],
        active: false,
        state: "template",
        template: true,
        capacity: 6,
        cell_count: 0,
        cells: [],
      }, {
        room_id: "floor-003-room-2",
        name: "Mail Room",
        kind: "service",
        archetype: "service",
        role_ids: [],
        active: false,
        state: "template",
        template: true,
        capacity: 6,
        cell_count: 0,
        cells: [],
      }],
    }],
  };
}

function payload() {
  return {
    floorplan: {
      floors: [
        {
          number: 1,
          name: "First",
          role: "exec",
          rooms: [
            { id: 11, slug: "operations", kind: "office", seats: [{}, {}] },
            { id: 12, slug: "laboratory", kind: "lab", seats: [{}] },
          ],
        },
        { number: 1, name: "First duplicate", role: "exec", rooms: [] },
        { number: 2, name: "Second", role: "ops", rooms: [{ id: 21, slug: "desk" }] },
      ],
    },
    characters: [
      { agent_id: 10, slug: "alpha", position: { floor: 1, room_id: 11 } },
      { agent_id: 11, slug: "bravo", position: { floor: 1, room_id: 11 } },
      { agent_id: 12, slug: "charlie", position: { floor: 1, room_id: 12 } },
      { agent_id: 13, slug: "unplaced", position: { floor: 1 } },
      { agent_id: 14, slug: "elsewhere", position: { floor: 99, room_id: 11 } },
    ],
  };
}

describe("liveFloorsFromSnapshot", () => {
  it("groups real characters by their real floor and room", () => {
    const rows = liveFloorsFromSnapshot(payload());
    expect(rows.map((row) => row.number)).toEqual([1, 2]);
    expect(rows[0]).toMatchObject({
      number: 1, name: "First", role: "exec", rooms: 2, agents: 4,
      characters: ["alpha", "bravo", "charlie", "unplaced"],
    });
    expect(rows[0].roomDetails).toEqual([
      {
        id: "f1r11", name: "operations", kind: "office", template: false,
        active: true, capacity: 2, agents: ["alpha", "bravo"],
      },
      {
        id: "f1r12", name: "laboratory", kind: "lab", template: false,
        active: true, capacity: 1, agents: ["charlie"],
      },
    ]);
    expect(rows[1]).toMatchObject({
      number: 2, agents: 0, roomDetails: [{
        id: "f2r21", name: "desk", agents: [],
      }],
    });
  });

  it("keeps a real character on the floor but never invents a room for it", () => {
    const rows = liveFloorsFromSnapshot(payload());
    expect(rows[0].characters).toContain("unplaced");
    expect(rows[0].roomDetails?.flatMap((room) => room.agents))
      .not.toContain("unplaced");
    expect(rows.some((row) => row.number === 99)).toBe(false);
  });

  it("returns no floors for a missing or malformed payload", () => {
    expect(liveFloorsFromSnapshot(null)).toEqual([]);
    expect(liveFloorsFromSnapshot({})).toEqual([]);
    expect(liveFloorsFromSnapshot({ floorplan: { floors: [{}] } })).toEqual([]);
  });
});

describe("buildCampusFloors", () => {
  it("keeps live floors real and marks template rooms unstaffed", () => {
    const world = worldFixture();
    const floors = buildCampusFloors(
      [live(1, ["alpha", "bravo"]), live(2, ["charlie"])],
      [template(1), template(2), template(3)], { world });
    expect(floors.map((floor) => floor.number)).toEqual([1, 2, 3]);
    expect(floors[0]).toMatchObject({
      id: "live:1", live: true, agents: 2, scope: "live:1",
      characters: ["alpha", "bravo"],
    });
    expect(floors[1]).toMatchObject({ id: "live:2", live: true, agents: 1 });
    expect(floors[2]).toMatchObject({
      id: "floor-003", live: false, agents: 0, characters: [],
      scope: "floor-003",
    });
    expect(floors[2].roomDetails.map((room) => room.name))
      .toEqual(["Records", "Mail Room"]);
    expect(floors[2].roomDetails.every(
      (room) => room.template && room.agents.length === 0)).toBe(true);
    expect(floors.every((floor) => floor.live || floor.agents === 0)).toBe(true);
  });

  it("uses the directory room count when the world plan is unavailable", () => {
    const floors = buildCampusFloors([], [template(3)]);
    expect(floors).toHaveLength(1);
    expect(floors[0].rooms).toBe(3);
    expect(floors[0].roomDetails).toHaveLength(3);
    expect(floors[0].roomDetails.map((room) => room.name))
      .toEqual(["Room 1", "Room 2", "Room 3"]);
    expect(floors[0].roomDetails.every(
      (room) => room.template && room.agents.length === 0)).toBe(true);
  });

  it("never lets a template duplicate a real floor number", () => {
    const floors = buildCampusFloors([live(4, ["delta"])], [template(1), template(4)]);
    expect(floors.map((floor) => floor.id)).toEqual(["floor-001", "live:4"]);
    expect(floors.find((floor) => floor.number === 4)?.live).toBe(true);
  });

  it("lists every known floor by default, not a miniature window", () => {
    const templates = Array.from({ length: 100 }, (_, i) => template(i + 1));
    const floors = buildCampusFloors([live(1, ["alpha"])], templates);
    expect(floors).toHaveLength(100);
    expect(floors.filter((floor) => floor.live)).toHaveLength(1);
    expect(floors.at(-1)?.number).toBe(100);
  });

  it("honours an explicit cap when a host asks for one", () => {
    const templates = Array.from({ length: 10 }, (_, i) => template(i + 1));
    const floors = buildCampusFloors(
      [live(1, ["alpha"]), live(2, ["bravo"])], templates, { maxFloors: 5 });
    expect(floors).toHaveLength(5);
    expect(floors.slice(0, 2).every((floor) => floor.live)).toBe(true);
    expect(floors.slice(2).every((floor) => !floor.live && floor.agents === 0))
      .toBe(true);
  });

  it("does not stretch a smaller live set with invented floors", () => {
    const floors = buildCampusFloors([live(7, ["golf"])], null);
    expect(floors).toHaveLength(1);
    expect(floors[0]).toMatchObject({ number: 7, live: true, agents: 1 });
  });

  it("derives template rooms without occupants", () => {
    const rooms = templateRoomsFor(template(3));
    expect(rooms).toHaveLength(3);
    expect(rooms.every((room) => room.template && room.agents.length === 0))
      .toBe(true);
    expect(rooms.reduce((sum, room) => sum + room.capacity, 0)).toBe(24);
  });
});

describe("floor and room stepping", () => {
  const floors = buildCampusFloors(
    [live(1, ["alpha", "bravo"]), live(2, [])], [template(3), template(4)]);
  const rooms = floors[0].roomDetails;

  it("walks the floor order and clamps at the ends", () => {
    expect(stepCampusFloor(floors, null, 1)?.id).toBe("live:1");
    expect(stepCampusFloor(floors, "live:1", 1)?.id).toBe("live:2");
    expect(stepCampusFloor(floors, "floor-004", 1)?.id).toBe("floor-004");
    expect(stepCampusFloor(floors, "live:1", -1)?.id).toBe("live:1");
    expect(stepCampusFloor([], "live:1", 1)).toBeNull();
  });

  it("walks rooms on one floor and clamps at the ends", () => {
    expect(stepCampusRoom(rooms, null, 1)?.id).toBe("f1r1");
    expect(stepCampusRoom(rooms, "f1r1", 1)?.id).toBe("f1r2");
    expect(stepCampusRoom(rooms, "f1r2", 1)?.id).toBe("f1r2");
    expect(stepCampusRoom(rooms, null, -1)?.id).toBe("f1r2");
    expect(stepCampusRoom([], "f1r1", 1)).toBeNull();
  });

  it("names real live occupancy and unstaffed templates differently", () => {
    expect(campusFloorStatus(floors[0])).toBe("Live · 2 agents · 2 rooms");
    expect(campusFloorStatus(floors[2])).toBe(
      "Unstaffed template · 3 rooms · 24 desks");
    expect(campusRoomStatus(rooms[0])).toBe(
      "1 agent · capacity 4");
    expect(campusRoomStatus({
      ...rooms[0], template: true, agents: [], capacity: 6,
    })).toBe("Unstaffed template · capacity 6");
  });
});
