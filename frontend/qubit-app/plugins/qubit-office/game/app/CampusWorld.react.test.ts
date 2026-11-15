/** @vitest-environment jsdom */
import * as React from "react";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import {
  createCampusWorld, type CampusLiveFloor, type CampusRoom,
} from "./CampusWorld";
import type { FloorTemplate } from "./business-api";
import type { WorldTower } from "./world-tower";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean })
  .IS_REACT_ACT_ENVIRONMENT = true;

function template(index: number, wing = "central"): FloorTemplate {
  return {
    floor_id: `floor-${String(index).padStart(3, "0")}`,
    index,
    wing,
    name: `${wing[0].toUpperCase()}${wing.slice(1)} Wing · `
      + `Floor ${String(index).padStart(3, "0")}`,
    department_ids: [`dept-${index}`],
    role_ids: [],
    room_count: 3,
    desk_capacity: 24,
    active_by_default: index <= 3,
    max_rooms: null,
    max_agents: null,
    expansion_allowed: true,
  };
}

function room(id: string, name: string, agents: string[],
              capacity = 4): CampusRoom {
  return {
    id, name, kind: "office", template: false, active: true,
    capacity, agents,
  };
}

const LIVE: CampusLiveFloor[] = [
  {
    number: 1, name: "Executive", role: "exec", rooms: 2, agents: 2,
    characters: ["alpha", "bravo"],
    roomDetails: [
      room("f1r1", "Operations", ["alpha", "bravo"]),
      room("f1r2", "Laboratory", []),
    ],
  },
  {
    number: 2, name: "Operations", role: "ops", rooms: 2, agents: 1,
    characters: ["charlie"],
    roomDetails: [
      room("f2r1", "Operations", ["charlie"]),
      room("f2r2", "Laboratory", []),
    ],
  },
];

const TEMPLATES: FloorTemplate[] = Array.from(
  { length: 8 }, (_, i) => template(i + 1, "north"));

const WORLD: WorldTower = {
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
  const CampusWorld = createCampusWorld(React as never, React as never);
  await act(async () => {
    root.render(React.createElement(CampusWorld as never, {
      base: "/",
      liveFloors: LIVE,
      templates: TEMPLATES,
      world: WORLD,
      ...props,
    }));
  });
}

function q<T extends Element>(selector: string): T {
  const node = container.querySelector<T>(selector);
  if (!node) throw new Error(`missing ${selector}`);
  return node;
}

function qa<T extends Element>(selector: string): T[] {
  return Array.from(container.querySelectorAll<T>(selector));
}

function click(selector: string) {
  const node = q<HTMLElement>(selector);
  act(() => { node.click(); });
}

describe("CampusWorld room-scale world", () => {
  it("renders one large real floor, not a stack of miniature previews", async () => {
    await render();
    expect(qa('[data-testid="campus-world-frame"]')).toHaveLength(1);
    expect(qa('[data-testid="campus-world-floor"]')).toHaveLength(1);
    expect(container.querySelector('[data-testid="tower-floor"]')).toBeNull();
    expect(q('[data-testid="campus-world-viewport"]')).toBeTruthy();
    expect(q('[data-testid="campus-world-floor"]')
      .getAttribute("data-live")).toBe("true");
    expect(q('[data-testid="campus-world-counts"]').textContent)
      .toContain("8 floors · 2 live");
    expect(q('[data-testid="office-campus-world"]')
      .getAttribute("data-rendered-frames")).toBe("1");
  });

  it("uses the real Habblaud renderer with the selected live floor scope", async () => {
    await render({ theme: "limezu" });
    const frame = q<HTMLIFrameElement>('[data-testid="campus-world-frame"]');
    expect(frame.getAttribute("data-floor-scope")).toBe("live:1");
    expect(frame.getAttribute("src")).toContain("/plugin-apps/qubit-office/habblaud/");
    expect(frame.getAttribute("src")).toContain("theme=limezu");
    expect(frame.getAttribute("src")).toContain("floor=live%3A1");
    expect(frame.getAttribute("src")).not.toContain("mock=1");
    expect(frame.getAttribute("sandbox")).toContain("allow-scripts");
  });

  it("switches floors from the elevator without mounting a second iframe", async () => {
    await render();
    const options = qa('[data-testid="campus-world-floor-option"]');
    expect(options).toHaveLength(8);
    expect(options[0].getAttribute("data-floor-id")).toBe("live:1");
    expect(options[0].getAttribute("aria-current")).toBe("true");
    expect(options[2].getAttribute("data-floor-id")).toBe("floor-003");
    expect(options[2].getAttribute("data-live")).toBe("false");

    click('[data-floor-id="floor-003"]');
    expect(qa('[data-testid="campus-world-frame"]')).toHaveLength(1);
    expect(q<HTMLIFrameElement>('[data-testid="campus-world-frame"]')
      .getAttribute("data-floor-scope")).toBe("floor-003");
    expect(q<HTMLIFrameElement>('[data-testid="campus-world-frame"]')
      .getAttribute("src")).toContain("floor=floor-003");
    expect(q('[data-testid="campus-world-floor"]')
      .getAttribute("data-live")).toBe("false");
    expect(q('[data-testid="campus-world-floor"]')
      .getAttribute("data-agents")).toBe("0");
    expect(q('[data-floor-id="floor-003"]').getAttribute("aria-current"))
      .toBe("true");
    expect(q('[data-testid="campus-world-unstaffed"]').textContent)
      .toContain("No agent is placed");
  });

  it("shows real room occupants and enters a room from its label", async () => {
    await render();
    const rooms = qa('[data-testid="campus-world-room"]');
    expect(rooms.map((chip) => chip.getAttribute("data-room-id")))
      .toEqual(["f1r1", "f1r2"]);
    expect(rooms[0].textContent).toContain("Operations");
    expect(rooms[0].textContent).toContain("2 agents");
    expect(rooms[1].textContent).toContain("0 agents");

    click('[data-room-id="f1r1"]');
    expect(q('[data-testid="campus-world-room-focus"]')
      .getAttribute("data-room-id")).toBe("f1r1");
    expect(q('[data-testid="campus-world-room-focus-title"]').textContent)
      .toBe("Operations");
    expect(q('[data-testid="campus-world-room-focus-status"]').textContent)
      .toBe("2 agents · capacity 4");
    expect(qa('[data-testid="campus-world-room-focus"] '
      + '[data-testid="campus-world-character"]')
      .map((chip) => chip.getAttribute("data-character")))
      .toEqual(["alpha", "bravo"]);
  });

  it("focuses a room when its real character is clicked", async () => {
    await render();
    click('[data-testid="campus-world-character"][data-character="bravo"]');
    expect(q('[data-testid="campus-world-room-focus-title"]').textContent)
      .toBe("Operations");
    expect(q('[data-testid="campus-world-character"][data-character="bravo"]')
      .getAttribute("aria-pressed")).toBe("true");
  });

  it("never moves an unplaced character into a room", async () => {
    const unplaced: CampusLiveFloor[] = [{
      number: 1, name: "Executive", role: "exec", rooms: 1, agents: 2,
      characters: ["alpha", "unplaced"],
      roomDetails: [room("f1r1", "Operations", ["alpha"])],
    }];
    await render({ liveFloors: unplaced, templates: [], world: null });
    click('[data-testid="campus-world-character"][data-character="unplaced"]');
    expect(container.querySelector('[data-testid="campus-world-room-focus"]'))
      .toBeNull();
    const note = q('[data-testid="campus-world-unplaced"]').textContent;
    expect(note).toContain("not");
    expect(note).toContain("declared room");
    click('[data-testid="campus-world-character"][data-character="alpha"]');
    expect(q('[data-testid="campus-world-room-focus-title"]').textContent)
      .toBe("Operations");
  });

  it("labels a template floor's rooms as unstaffed and empty", async () => {
    await render();
    click('[data-floor-id="floor-003"]');
    const rooms = qa('[data-testid="campus-world-room"]');
    expect(rooms.map((chip) => chip.getAttribute("data-room-id")))
      .toEqual(["floor-003-room-1", "floor-003-room-2"]);
    expect(rooms.every((chip) => chip.getAttribute("data-template") === "true"))
      .toBe(true);
    expect(rooms.map((chip) => chip.textContent)).toEqual([
      "RecordsUnstaffed", "Mail RoomUnstaffed",
    ]);
    click('[data-room-id="floor-003-room-1"]');
    expect(q('[data-testid="campus-world-room-empty"]').textContent)
      .toContain("Unstaffed template room");
    expect(qa('[data-testid="campus-world-character"]')).toHaveLength(0);
    expect(q('[data-testid="campus-world-room-focus"]')
      .getAttribute("data-template")).toBe("true");
  });

  it("moves between floors and rooms from the keyboard", async () => {
    await render();
    const viewport = q<HTMLElement>('[data-testid="campus-world-viewport"]');
    expect(viewport.getAttribute("role")).toBe("region");

    act(() => {
      viewport.dispatchEvent(new KeyboardEvent("keydown", {
        key: "ArrowDown", bubbles: true,
      }));
    });
    expect(q('[data-testid="campus-world-focus-title"]').textContent)
      .toContain("Floor 002");
    expect(q<HTMLIFrameElement>('[data-testid="campus-world-frame"]')
      .getAttribute("data-floor-scope")).toBe("live:2");

    act(() => {
      viewport.dispatchEvent(new KeyboardEvent("keydown", {
        key: "ArrowRight", bubbles: true,
      }));
    });
    expect(q('[data-testid="campus-world-room-focus-title"]').textContent)
      .toBe("Operations");

    act(() => {
      viewport.dispatchEvent(new KeyboardEvent("keydown", {
        key: "Escape", bubbles: true,
      }));
    });
    expect(container.querySelector('[data-testid="campus-world-room-focus"]'))
      .toBeNull();

    act(() => {
      viewport.dispatchEvent(new KeyboardEvent("keydown", {
        key: "ArrowUp", bubbles: true,
      }));
    });
    expect(q('[data-testid="campus-world-focus-title"]').textContent)
      .toContain("Floor 001");
  });

  it("announces the selected floor and room to assistive technology", async () => {
    await render();
    const announcer = q('[data-testid="campus-world-announcer"]');
    expect(announcer.getAttribute("aria-live")).toBe("polite");
    expect(announcer.textContent).toContain("Floor 001");
    expect(announcer.textContent).toContain("Live · 2 agents");
    click('[data-room-id="f1r1"]');
    expect(q('[data-testid="campus-world-announcer"]').textContent)
      .toContain("Room Operations focused");
  });

  it("shows an honest empty state instead of inventing floors", async () => {
    await render({ liveFloors: [], templates: [], world: null });
    expect(qa('[data-testid="campus-world-floor"]')).toHaveLength(0);
    expect(qa('[data-testid="campus-world-frame"]')).toHaveLength(0);
    const state = q('[data-testid="campus-world-state"]');
    expect(state.getAttribute("role")).toBe("alert");
    expect(state.textContent).toContain("No floors or agents are shown");
  });
});
