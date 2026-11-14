/** @vitest-environment jsdom */
/* A real-DOM render test for the tower navigator.
 *
 * The recorder harness used by the other panel tests records elements but does
 * not execute child component functions, so it cannot prove that the 100 bands
 * actually mount. This file renders the real component with the real React and
 * a real DOM, and drives a click and a keypress through the browser event path.
 */
import * as React from "react";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { createTowerNavigator } from "./TowerNavigator";
import type { FloorTemplate } from "./business-api";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean })
  .IS_REACT_ACT_ENVIRONMENT = true;

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

async function render(floors: FloorTemplate[] = realFloors()) {
  const Navigator = createTowerNavigator(
    React as never, React as never);
  await act(async () => {
    root.render(React.createElement(Navigator as never, { floors }));
  });
}

function q<T extends Element>(selector: string): T {
  const node = container.querySelector<T>(selector);
  if (!node) throw new Error(`missing ${selector}`);
  return node;
}

describe("TowerNavigator in a real DOM", () => {
  it("mounts all 100 floors across 5 wings", async () => {
    await render();
    expect(container.querySelectorAll('[data-testid="tower-floor"]'))
      .toHaveLength(100);
    expect(container.querySelectorAll('[data-testid="tower-wing"]'))
      .toHaveLength(5);
    expect(q('[data-testid="tower-summary"]').textContent)
      .toBe("100 floors · 5 wings · 22 active / 78 dormant");
    expect(container.querySelectorAll('[data-testid="tower-floor"]' +
      '[data-active="true"]')).toHaveLength(22);
  });

  it("opens the detail on click and moves it with the keyboard", async () => {
    await render();
    await act(async () => {
      (q('[data-floor="floor-050"]') as HTMLElement).click();
    });
    expect(q('[data-testid="tower-floor-detail"]').getAttribute("data-floor"))
      .toBe("floor-050");
    expect(q('[data-testid="tower-detail-wing"]').textContent)
      .toBe("South Wing");

    await act(async () => {
      q('[data-floor="floor-050"]').dispatchEvent(new KeyboardEvent(
        "keydown", { key: "ArrowDown", bubbles: true }));
    });
    expect(q('[data-testid="tower-floor-detail"]').getAttribute("data-floor"))
      .toBe("floor-051");
    expect(q('[data-floor="floor-051"]').getAttribute("aria-selected"))
      .toBe("true");
    // Roving tabindex: only the selected floor is in the tab order.
    expect(container.querySelectorAll('[data-testid="tower-floor"]' +
      '[tabindex="0"]')).toHaveLength(1);
  });
});
