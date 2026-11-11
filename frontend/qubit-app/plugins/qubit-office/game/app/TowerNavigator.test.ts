import { describe, expect, it, vi } from "vitest";
import { createTowerNavigator, type TowerNavigatorProps } from "./TowerNavigator";
import type { FloorTemplate } from "./business-api";
import {
  allByTestId, byTestId, createHarness, textOf,
} from "./business-test-harness";
import { buildTemplateWorldTower, type WorldTower } from "./world-tower";

/* Mirrors the real `build_floor_templates` shape: 100 floors, the five shipped
 * wings in generator order, and 22 active (one per department) / 78 dormant. */
const WINGS = ["north", "east", "south", "west", "central"];
const DESKS = [24, 27, 33, 39];

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
      desk_capacity: DESKS[i % DESKS.length],
      active_by_default: index <= 22,
      max_rooms: null,
      max_agents: null,
      expansion_allowed: true,
    };
  });
}

function mount(over: Partial<TowerNavigatorProps> = {}) {
  const h = createHarness();
  const Navigator = createTowerNavigator(
    h.React as never, h.hooks as never);
  h.mount(Navigator as never, {
    floors: realFloors(),
    ...over,
  });
  h.flush();
  return h;
}

function floor(h: ReturnType<typeof createHarness>, id: string) {
  return allByTestId(h, "tower-floor")
    .find((node) => node.props["data-floor"] === id);
}

function press(h: ReturnType<typeof createHarness>, id: string, key: string) {
  const node = floor(h, id);
  expect(node).toBeTruthy();
  (node!.props.onKeyDown as (event: {
    key: string; preventDefault?: () => void;
  }) => void)({ key, preventDefault: vi.fn() });
  h.flush();
}

describe("TowerNavigator renders the real 100-floor directory", () => {
  it("renders all 100 floors in 5 wing groups with the real summary", () => {
    const h = mount();
    expect(byTestId(h, "tower-navigator")).toHaveLength(1);
    expect(allByTestId(h, "tower-floor")).toHaveLength(100);
    expect(allByTestId(h, "tower-wing")).toHaveLength(5);
    expect(textOf(byTestId(h, "tower-summary")[0]))
      .toBe("100 floors · 5 wings · 22 active / 78 dormant");
    // Every band names its wing from the payload, not a hardcoded list.
    const wings = allByTestId(h, "tower-wing")
      .map((node) => node.props["data-wing"]);
    expect(wings).toEqual(WINGS);
    // Honest label: this is a directory, not a rendered 3D scene.
    expect(textOf(byTestId(h, "tower-honesty")[0]))
      .toContain("not a rendered 3D scene");
  });

  it("distinguishes active floors from dormant ones", () => {
    const h = mount();
    const bands = allByTestId(h, "tower-floor");
    const active = bands.filter((node) => node.props["data-active"] === "true");
    const dormant = bands.filter((node) => node.props["data-active"] === "false");
    expect(active).toHaveLength(22);
    expect(dormant).toHaveLength(78);
    expect(active[0].props.className).toContain("is-active");
    expect(dormant[0].props.className).toContain("is-dormant");
    expect(textOf(active[0])).toContain("active");
    expect(textOf(dormant[0])).toContain("dormant");
  });

  it("shows each band's index, name, room count and desk capacity", () => {
    const h = mount();
    const band = floor(h, "floor-050")!;
    expect(textOf(band)).toContain("050");
    expect(textOf(band)).toContain("South Wing · Floor 050");
    expect(textOf(band)).toContain("3 rooms · 27 desks");
    expect(band.props["data-wing"]).toBe("south");
  });

  it("opens a compact detail for the selected floor", () => {
    const h = mount();
    // The first floor is selected by default.
    expect(byTestId(h, "tower-floor-detail")[0].props["data-floor"])
      .toBe("floor-001");
    (floor(h, "floor-050")!.props.onClick as () => void)();
    h.flush();
    const detail = byTestId(h, "tower-floor-detail")[0];
    expect(detail.props["data-floor"]).toBe("floor-050");
    expect(detail.props["data-active"]).toBe("false");
    expect(textOf(byTestId(h, "tower-detail-name")[0]))
      .toContain("Floor 050");
    expect(textOf(byTestId(h, "tower-detail-wing")[0]))
      .toBe("South Wing");
    expect(textOf(byTestId(h, "tower-detail-departments")[0]))
      .toContain("dept-06");
    expect(textOf(byTestId(h, "tower-detail-roles")[0]))
      .toContain("role-50-a");
    expect(textOf(byTestId(h, "tower-detail-rooms")[0])).toBe("3");
    expect(textOf(byTestId(h, "tower-detail-desks")[0])).toBe("27");
    expect(textOf(byTestId(h, "tower-detail-state")[0])).toBe("dormant");
    expect(textOf(byTestId(h, "tower-detail-expansion")[0]))
      .toContain("allowed");
    expect(textOf(byTestId(h, "tower-detail-expansion")[0]))
      .toContain("no rooms cap");
    expect(floor(h, "floor-050")!.props["data-selected"]).toBe("true");
  });

  it("honours an initial floor for the host and tests", () => {
    const h = mount({ initialFloorId: "floor-073" });
    expect(byTestId(h, "tower-floor-detail")[0].props["data-floor"])
      .toBe("floor-073");
    expect(textOf(byTestId(h, "tower-detail-wing")[0])).toBe("West Wing");
    expect(floor(h, "floor-073")!.props["aria-selected"]).toBe("true");
  });

  it("moves the selection with the keyboard", () => {
    const h = mount();
    press(h, "floor-001", "ArrowDown");
    expect(byTestId(h, "tower-floor-detail")[0].props["data-floor"])
      .toBe("floor-002");
    press(h, "floor-002", "End");
    expect(byTestId(h, "tower-floor-detail")[0].props["data-floor"])
      .toBe("floor-100");
    press(h, "floor-100", "Home");
    expect(byTestId(h, "tower-floor-detail")[0].props["data-floor"])
      .toBe("floor-001");
    press(h, "floor-001", "ArrowUp");
    expect(byTestId(h, "tower-floor-detail")[0].props["data-floor"])
      .toBe("floor-001");
    press(h, "floor-001", "Enter");
    expect(byTestId(h, "tower-floor-detail")[0].props["data-floor"])
      .toBe("floor-001");
    // Roving tabindex: exactly one option is in the tab order.
    const tabbable = allByTestId(h, "tower-floor")
      .filter((node) => node.props.tabIndex === 0);
    expect(tabbable).toHaveLength(1);
  });

  it("names an empty directory instead of inventing floors", () => {
    const h = mount({ floors: [] });
    expect(allByTestId(h, "tower-floor")).toHaveLength(0);
    expect(byTestId(h, "tower-empty")).toHaveLength(1);
    expect(textOf(byTestId(h, "tower-empty")[0]))
      .toContain("no floor templates were returned");
    expect(textOf(byTestId(h, "tower-summary")[0]))
      .toBe("0 floors · 0 wings · 0 active / 0 dormant");
  });

  it("never dresses a role template as an agent", () => {
    const h = mount();
    expect(byTestId(h, "agent-control")).toHaveLength(0);
    expect(byTestId(h, "agents-panel")).toHaveLength(0);
    expect(byTestId(h, "warroom-role")).toHaveLength(0);
    expect(textOf(byTestId(h, "tower-detail-roles")[0]))
      .toContain("ids —");
    expect(textOf(byTestId(h, "tower-detail-note")[0]))
      .toContain("not staffed agents");
  });
});

describe("TowerNavigator room scenes", () => {
  it("renders the selected floor's rooms, 16x12 cells and empty desks", () => {
    const h = mount();
    const scene = byTestId(h, "tower-room-scene");
    expect(scene).toHaveLength(1);
    expect(scene[0].props["data-floor"]).toBe("floor-001");
    expect(scene[0].props["data-source"]).toBe("template");
    expect(allByTestId(h, "tower-room")).toHaveLength(3);
    const cells = allByTestId(h, "tower-cell");
    expect(cells.length).toBeGreaterThan(0);
    for (const cell of cells) {
      expect(cell.props["data-cols"]).toBe(16);
      expect(cell.props["data-rows"]).toBe(12);
      expect(cell.props.style.aspectRatio).toBe("16 / 12");
    }
    const desks = allByTestId(h, "tower-desk");
    expect(desks.length).toBeGreaterThan(0);
    expect(desks.every((desk) => desk.props["data-occupied"] === "false"))
      .toBe(true);
    expect(textOf(byTestId(h, "tower-scene-note")[0]))
      .toContain("Template rooms, not staffed agents");
    expect(textOf(byTestId(h, "tower-scene-capacity")[0]))
      .toContain("3 rooms");
    expect(byTestId(h, "agent-control")).toHaveLength(0);
    expect(byTestId(h, "agents-panel")).toHaveLength(0);
  });

  it("renders endpoint rooms when the world payload provides them", () => {
    const world: WorldTower = {
      ...buildTemplateWorldTower(realFloors()),
      source: "endpoint",
      floors: buildTemplateWorldTower(realFloors()).floors.map((floor) =>
        floor.floor_id === "floor-001"
          ? {
              ...floor,
              rooms: floor.rooms.map((room, index) => ({
                ...room,
                room_id: `live-${index}`,
                name: `Endpoint Room ${index + 1}`,
                archetype: "open_office",
                kind: "work",
              })),
            }
          : floor),
    };
    const h = mount({ world });
    const scene = byTestId(h, "tower-room-scene")[0];
    expect(scene.props["data-source"]).toBe("endpoint");
    expect(textOf(byTestId(h, "tower-scene-source")[0]))
      .toBe("endpoint rooms");
    const rooms = allByTestId(h, "tower-room");
    expect(rooms).toHaveLength(3);
    expect(textOf(rooms[0])).toContain("Endpoint Room 1");
    expect(rooms[0].props["data-archetype"]).toBe("open_office");
    expect(rooms[0].props["data-kind"]).toBe("work");
  });

  it("stays dormant/active with the floor and the room", () => {
    const h = mount();
    const active = allByTestId(h, "tower-room");
    expect(active.every((room) => room.props["data-active"] === "true"))
      .toBe(true);
    (floor(h, "floor-050")!.props.onClick as () => void)();
    h.flush();
    const dormant = allByTestId(h, "tower-room");
    expect(dormant.length).toBeGreaterThan(0);
    expect(dormant.every((room) => room.props["data-active"] === "false"))
      .toBe(true);
    expect(dormant[0].props.className).toContain("is-dormant");
  });

  it("closes the detail with the button and Escape without the list vanishing", () => {
    const h = mount();
    expect(byTestId(h, "tower-floor-detail")).toHaveLength(1);
    (byTestId(h, "tower-detail-close")[0].props.onClick as () => void)();
    h.flush();
    expect(byTestId(h, "tower-floor-detail")).toHaveLength(0);
    expect(byTestId(h, "tower-detail-empty")).toHaveLength(1);
    expect(allByTestId(h, "tower-floor")).toHaveLength(100);
    // Opening a floor again works, and Escape closes it again.
    (floor(h, "floor-002")!.props.onClick as () => void)();
    h.flush();
    expect(byTestId(h, "tower-floor-detail")).toHaveLength(1);
    press(h, "floor-002", "Escape");
    expect(byTestId(h, "tower-floor-detail")).toHaveLength(0);
  });

  it("guards a large payload: only the selected floor paints rooms", () => {
    const huge: FloorTemplate[] = Array.from({ length: 100 }, (_, i) => ({
      floor_id: `floor-${String(i + 1).padStart(3, "0")}`,
      index: i + 1,
      wing: WINGS[Math.floor(i / 20)],
      name: `Floor ${i + 1}`,
      department_ids: ["revenue"],
      role_ids: ["role-1"],
      room_count: 8,
      desk_capacity: 192,
      active_by_default: i < 22,
      max_rooms: null,
      max_agents: null,
      expansion_allowed: true,
    }));
    const world = buildTemplateWorldTower(huge);
    const h = mount({ floors: huge, world });
    expect(allByTestId(h, "tower-floor")).toHaveLength(100);
    expect(allByTestId(h, "tower-room")).toHaveLength(8);
    expect(allByTestId(h, "tower-cell")).toHaveLength(32);
    expect(h.tree.some((el) => el.type === "canvas")).toBe(false);
    expect(h.tree.length).toBeLessThan(3000);
  });
});
