import { describe, expect, it } from "vitest";
import * as RB from "./room-builder";

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

function problemsOf(state: RB.BuilderState, needle: string): string[] {
  return RB.validateState(state).filter((item) => item.includes(needle));
}

describe("room-builder model", () => {
  it("builds a complete, valid sample department floor", () => {
    const state = RB.createSampleState();
    expect(RB.validateState(state)).toEqual([]);
    const preview = RB.previewState(state);
    expect(preview.valid).toBe(true);
    expect(preview.counts).toEqual({
      floors: 1,
      rooms: 10,
      cells: 10,
      desks: 17,
      portals: 9,
    });
    expect(preview.capacity).toBe(17);
    expect(preview.egress).toBe(9);
    expect(RB.liveText(state)).toContain("Draft revision 0");
  });

  it("edits rooms with history, undo and redo", () => {
    let state = RB.createSampleState();
    state = RB.addRoom(state, {
      floor_id: "floor-001", room_id: "extra-1",
      name: "Extra", kind: "storage", desks: 2,
    });
    expect(state.rooms.length).toBe(11);
    expect(state.draft_revision).toBe(1);
    expect(RB.validateState(state)).toEqual([]);
    state = RB.undo(state);
    expect(state.rooms.length).toBe(10);
    state = RB.redo(state);
    expect(state.rooms.length).toBe(11);
    state = RB.removeRoom(state, "extra-1");
    expect(state.rooms.length).toBe(10);
  });

  it("keeps every cell fixed at 16x12 and grows rooms by cells", () => {
    let state = RB.createSampleState();
    const before = state.rooms.find((row) => row.room_id === "work-1")!;
    state = RB.addCell(state, "work-1", 2);
    const work = state.rooms.find((row) => row.room_id === "work-1")!;
    expect(work.cells.length).toBe(before.cells.length + 1);
    for (const cell of work.cells) {
      expect([cell.cols, cell.rows]).toEqual([16, 12]);
    }
    expect(RB.validateState(state)).toEqual([]);
  });

  it("adds and removes desks without stretching a cell", () => {
    let state = RB.createSampleState();
    const before = state.rooms.find((row) => row.room_id === "work-1")!;
    state = RB.addDesk(state, "work-1");
    const after = state.rooms.find((row) => row.room_id === "work-1")!;
    expect(after.capacity).toBe(before.capacity + 1);
    const desk = after.cells.flatMap((cell) => cell.desks).at(-1)!;
    state = RB.removeDesk(state, desk.desk_id);
    const restored = state.rooms.find((row) => row.room_id === "work-1")!;
    expect(restored.capacity).toBe(before.capacity);
    expect(RB.validateState(state)).toEqual([]);
  });

  it("edits style, light, signage and seed controls", () => {
    let state = RB.createSampleState();
    state = RB.setRoomStyle(state, "work-1", {
      palette: "finance_navy", lighting: "light_focused_task",
      signage: "sign-finance", seed: 4242,
    });
    const work = state.rooms.find((row) => row.room_id === "work-1")!;
    expect(work.style.palette).toBe("finance_navy");
    expect(work.style.lighting).toBe("light_focused_task");
    expect(work.style.signage).toBe("sign-finance");
    expect(work.style.seed).toBe(4242);
    expect(RB.validateState(state)).toEqual([]);
  });

  it("records a business/role/agent/capability/memory assignment", () => {
    let state = RB.createSampleState();
    state = RB.assignRoom(state, "work-1", {
      business_id: "business:sample",
      department_id: "operations",
      role_ids: ["operations-lead", "operations-analyst"],
      agent_ids: ["agent:demo"],
      capability_manifest_id: "capability:work:work-1",
      memory_scope: "memory:operations:work-1",
    });
    const work = state.rooms.find((row) => row.room_id === "work-1")!;
    expect(work.assignment.role_ids).toEqual([
      "operations-lead", "operations-analyst",
    ]);
    expect(work.assignment.memory_scope).toBe("memory:operations:work-1");
    expect(RB.validateState(state)).toEqual([]);
  });

  it("clones and merges departments and split rooms onto a new floor", () => {
    let state = RB.createSampleState();
    state = RB.cloneDepartment(state, "floor-001", "floor-002");
    expect(state.floors.length).toBe(2);
    expect(state.rooms.length).toBe(20);
    expect(RB.validateState(state)).toEqual([]);

    state = RB.mergeDepartments(
      state, ["floor-001", "floor-002"], "floor-002");
    expect(state.floors.length).toBe(1);
    expect(state.rooms.length).toBe(20);
    expect(RB.validateState(state)).toEqual([]);

    let split = RB.createSampleState();
    split = RB.splitDepartment(
      split, "floor-001", "floor-003", ["store-1"]);
    const moved = split.rooms.find((row) => row.room_id === "store-1")!;
    expect(moved.floor_id).toBe("floor-003");
    expect(problemsOf(split, "MISSING_REQUIRED_ROOM").length).toBeGreaterThan(0);
  });

  it("publishes, versions and rolls back to an earlier version", async () => {
    let state = RB.createSampleState();
    const first = await RB.publishState(state);
    expect(first.result.kind).toBe("published");
    state = first.state;
    expect(state.published_version).toBe(1);
    expect(state.status).toBe("published");

    state = RB.addRoom(state, {
      floor_id: "floor-001", room_id: "extra-1",
      name: "Extra", kind: "storage", desks: 1,
    });
    const second = await RB.publishState(state);
    expect(second.result.kind).toBe("published");
    state = second.state;
    expect(state.published_version).toBe(2);
    expect(state.rooms.length).toBe(11);

    state = RB.rollbackState(state, 1);
    expect(state.status).toBe("rolled_back");
    expect(state.rooms.length).toBe(10);
  });

  it("refuses to publish an invalid draft and exposes the problems", async () => {
    let state = RB.createSampleState();
    state.rooms[0].cells[0].cols = 24;
    const result = await RB.publishState(state);
    expect(result.result.kind).toBe("error");
    if (result.result.kind === "error") {
      expect(result.result.problems.join(" ")).toContain("16x12");
    }
    const approved = RB.approveState(state);
    expect(approved.status).not.toBe("approved");
    expect(approved.problems.length).toBeGreaterThan(0);
  });

  it("surfaces an injected publish refusal", async () => {
    const state = RB.createSampleState();
    const result = await RB.publishState(state, {
      onPublish: async () => ({
        ok: false, problems: ["OFFICE_MAP_INVALID from server"],
      }),
    });
    expect(result.result.kind).toBe("error");
    if (result.result.kind === "error") {
      expect(result.result.problems).toEqual([
        "OFFICE_MAP_INVALID from server",
      ]);
    }
  });

  it("reports a stretched cell", () => {
    const state = clone(RB.createSampleState());
    state.rooms[0].cells[0].cols = 24;
    expect(problemsOf(state, "16x12").length).toBeGreaterThan(0);
  });

  it("reports a missing door", () => {
    const state = clone(RB.createSampleState());
    state.portals = state.portals.filter(
      (portal) => portal.source_room_id !== "rec-1"
        && portal.target_room_id !== "rec-1");
    expect(problemsOf(state, "MISSING_DOOR").length).toBeGreaterThan(0);
  });

  it("reports an unreachable room", () => {
    const state = clone(RB.createSampleState());
    state.portals = state.portals.filter(
      (portal) => portal.target_room_id !== "store-1");
    expect(problemsOf(state, "unreachable").length).toBeGreaterThan(0);
  });

  it("reports a cell collision", () => {
    const state = clone(RB.createSampleState());
    const room = state.rooms.find((row) => row.room_id === "work-1")!;
    room.cells.push({
      ...room.cells[0],
      cell_id: "work-1:collision",
    });
    expect(problemsOf(state, "CELL_COLLISION").length).toBeGreaterThan(0);
  });

  it("reports a department style collision", () => {
    let state = RB.createSampleState();
    state = RB.cloneDepartment(state, "floor-001", "floor-002");
    for (const room of state.rooms) {
      if (room.floor_id === "floor-002") {
        room.style.department_style = "operations";
      }
    }
    expect(problemsOf(state, "STYLE_COLLISION").length).toBeGreaterThan(0);
  });

  it("refuses paid art and raw sprite sheets", () => {
    const paid = clone(RB.createSampleState());
    paid.rooms[0].assets.push({
      asset_id: "art:paid", kind: "recipe", ref: "recipes/local",
      paid: true,
    });
    expect(problemsOf(paid, "paid art").length).toBeGreaterThan(0);

    const raw = clone(RB.createSampleState());
    raw.rooms[0].assets.push({
      asset_id: "art:raw", kind: "tileset", ref: "art/limezu/tile.png",
      paid: false,
    });
    expect(problemsOf(raw, "raw sprite").length).toBeGreaterThan(0);
  });

  it("reports a capacity mismatch", () => {
    const state = clone(RB.createSampleState());
    state.rooms[0].capacity += 1;
    expect(problemsOf(state, "capacity").length).toBeGreaterThan(0);
  });
});
