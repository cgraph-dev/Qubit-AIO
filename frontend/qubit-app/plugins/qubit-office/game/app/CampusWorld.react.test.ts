/** @vitest-environment jsdom */
import * as React from "react";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { createCampusWorld, type CampusLiveFloor } from "./CampusWorld";
import type { FloorTemplate } from "./business-api";

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

const LIVE: CampusLiveFloor[] = [
  {
    number: 1, name: "Executive", role: "exec", rooms: 2, agents: 2,
    characters: ["alpha", "bravo"],
  },
  {
    number: 2, name: "Operations", role: "ops", rooms: 1, agents: 1,
    characters: ["charlie"],
  },
];

const TEMPLATES: FloorTemplate[] = Array.from(
  { length: 8 }, (_, i) => template(i + 1, "north"));

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

describe("CampusWorld in a real DOM", () => {
  it("renders the bounded real world, not cards or a text directory", async () => {
    await render();
    const floors = qa('[data-testid="campus-world-floor"]');
    expect(floors).toHaveLength(8);
    expect(container.querySelector('[data-testid="tower-floor"]')).toBeNull();
    expect(qa('[data-testid="campus-world-floor"]')
      .filter((node) => node.getAttribute("data-live") === "true"))
      .toHaveLength(2);
    expect(q('[data-testid="campus-world-counts"]').textContent)
      .toContain("8 of 8 floors in view · 2 live");
    expect(q('[data-testid="campus-world-viewport"]')).toBeTruthy();
    expect(q('[data-testid="campus-world-stage"]')).toBeTruthy();
  });

  it("uses the real Habblaud renderer for every floor scope", async () => {
    await render({ theme: "limezu" });
    const frames = qa<HTMLIFrameElement>('[data-testid="campus-world-frame"]');
    expect(frames).toHaveLength(8);
    const scopes = frames.map((frame) => frame.getAttribute("data-floor-scope"));
    expect(scopes).toEqual(expect.arrayContaining(["live:1", "live:2", "floor-003"]));
    for (const frame of frames) {
      expect(frame.getAttribute("src")).toContain("/plugin-apps/qubit-office/habblaud/");
      expect(frame.getAttribute("src")).toContain("theme=limezu");
      expect(frame.getAttribute("src")).not.toContain("mock=1");
      expect(frame.getAttribute("sandbox")).toContain("allow-scripts");
    }
    const live = frames.find(
      (frame) => frame.getAttribute("data-floor-scope") === "live:1");
    expect(live?.getAttribute("src")).toContain("floor=live%3A1");
  });

  it("keeps real occupancy on live floors and never fabricates template staff", async () => {
    await render();
    const floors = qa('[data-testid="campus-world-floor"]');
    const live = floors.filter((node) => node.getAttribute("data-live") === "true");
    expect(live.map((node) => node.getAttribute("data-agents")))
      .toEqual(["2", "1"]);
    const templates = floors.filter(
      (node) => node.getAttribute("data-live") !== "true");
    expect(templates.length).toBeGreaterThan(0);
    expect(templates.every((node) => node.getAttribute("data-agents") === "0"))
      .toBe(true);
    expect(templates.every(
      (node) => node.textContent?.includes("Unstaffed template"))).toBe(true);
    expect(floors.some((node) => node.textContent?.includes("Live · 2 agents")))
      .toBe(true);
  });

  it("selects a floor to see its characters without a picker", async () => {
    await render();
    const focusTitle = () => q('[data-testid="campus-world-focus-title"]').textContent;
    expect(focusTitle()).toContain("Floor 001");
    expect(q('[data-testid="campus-world-focus-people"]').textContent)
      .toBe("alpha, bravo");
    const ops = q<HTMLButtonElement>(
      '[data-floor="live:2"] [data-testid="campus-world-floor-label"]');
    await act(async () => { ops!.click(); });
    expect(focusTitle()).toContain("Floor 002");
    expect(q('[data-testid="campus-world-focus-people"]').textContent)
      .toBe("charlie");
    expect(ops!.getAttribute("aria-pressed")).toBe("true");
    await act(async () => {
      q<HTMLButtonElement>('[data-testid="campus-world-next"]').click();
    });
    expect(focusTitle()).toContain("Floor 003");
  });

  it("moves through the world with the keyboard, not a picker", async () => {
    await render();
    const viewport = q<HTMLElement>('[data-testid="campus-world-viewport"]');
    expect(viewport.getAttribute("role")).toBe("region");
    await act(async () => {
      viewport.dispatchEvent(new KeyboardEvent("keydown", {
        key: "ArrowDown", bubbles: true,
      }));
    });
    expect(q('[data-testid="campus-world-focus-title"]').textContent)
      .toContain("Floor 002");
    await act(async () => {
      viewport.dispatchEvent(new KeyboardEvent("keydown", {
        key: "ArrowUp", bubbles: true,
      }));
    });
    expect(q('[data-testid="campus-world-focus-title"]').textContent)
      .toContain("Floor 001");
  });

  it("shows an honest empty state instead of inventing floors", async () => {
    await render({ liveFloors: [], templates: [] });
    expect(qa('[data-testid="campus-world-floor"]')).toHaveLength(0);
    const state = q('[data-testid="campus-world-state"]');
    expect(state.getAttribute("role")).toBe("alert");
    expect(state.textContent).toContain("No floors or agents are shown");
  });
});
