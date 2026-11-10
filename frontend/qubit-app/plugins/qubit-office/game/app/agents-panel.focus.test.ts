// @vitest-environment jsdom
/* The REAL keyboard path, in a real DOM with the host React.
 *
 * WHY THIS FILE EXISTS
 * --------------------
 * The fake-React recorder can prove a handler is wired, but not that a user can
 * reach it: invoking `props.onFocus()` directly passes even for an <li> that a
 * browser will never focus. This test mounts the panel with React 19 + jsdom,
 * calls the DOM's own `.focus()` on a seat row, and reads the card the user
 * would see — so "hover/focus updates the card" is proven, not asserted.
 */
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createAgentsPanel } from "./AgentsPanel";
import type { AgentPanelData, AgentSeat } from "./agents-panel";

// React is a HOST dependency and this test lives outside the web package, so a
// bare `import "react"` cannot resolve from here. Load the repo's one copy from
// the web package, exactly as the existing DOM probe does.
const HERE = path.dirname(fileURLToPath(import.meta.url));
const webRequire = createRequire(
  path.resolve(HERE, "../../../../../qubit-web/package.json"));
const React = webRequire("react") as {
  createElement: (...args: unknown[]) => unknown;
  useState: (value: unknown) => [unknown, (next: unknown) => void];
  useEffect: (fn: () => void | (() => void), deps?: unknown[]) => void;
  act: (cb: () => void | Promise<void>) => Promise<void>;
};
const { createRoot } = webRequire("react-dom/client") as {
  createRoot: (host: HTMLElement) => { render: (node: unknown) => void; unmount: () => void };
};
const act = React.act;
type Root = ReturnType<typeof createRoot>;

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean })
  .IS_REACT_ACT_ENVIRONMENT = true;

function seat(over: Partial<AgentSeat> & { slug: string; name: string }): AgentSeat {
  return {
    seat_type: "worker", supervisor: null, division: "alpha",
    room: "alpha-desk-a", emoji: "🤖", color: "#111",
    portrait: null, body: null,
    ...over,
  };
}

function payload(seats: AgentSeat[]): AgentPanelData {
  return {
    ok: true,
    counts: {
      headcount: seats.length, divisions: 1, roster_divisions: 1,
      boardroom_seats: 0, rooms: 1, floors: 1,
      seat_types: { worker: seats.length }, per_division: { alpha: seats.length },
    },
    divisions: [{
      slug: "alpha", label: "Alpha", kind: "product", floor: 1,
      headcount: seats.length, seats,
    }],
    seats,
  };
}

const respond = (body: unknown): Response => ({
  status: 200,
  json: async () => body,
} as unknown as Response);

let host: HTMLDivElement;
let root: Root;

beforeEach(() => {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
});

afterEach(async () => {
  await act(async () => { root.unmount(); });
  host.remove();
  document.body.innerHTML = "";
});

describe("the agents panel's real keyboard path", () => {
  it("keeps one tabbable row and moves focus/card with arrows, Home and End", async () => {
    const seats = [
      seat({ slug: "first", name: "First Agent" }),
      seat({ slug: "second", name: "Second Agent" }),
      seat({ slug: "third", name: "Third Agent" }),
    ];
    const Panel = createAgentsPanel(
      React as never,
      { useState: React.useState, useEffect: React.useEffect, useRef: React.useRef } as never,
    );
    const fetchImpl = (async () => respond(payload(seats))) as unknown as typeof fetch;

    await act(async () => {
      root.render(React.createElement(Panel as never, {
        base: "", fetchImpl, theme: null,
      }));
    });
    for (let i = 0; i < 40; i += 1) {
      if (host.querySelector("[data-testid=agents-panel][data-state=ready]")) break;
      await act(async () => {
        await new Promise((resolve) => setTimeout(resolve, 10));
      });
    }

    const rows = Array.from(host.querySelectorAll<HTMLElement>(
      "[data-testid=agents-panel-seat]"));
    expect(rows).toHaveLength(3);
    const card = () => host.querySelector<HTMLElement>(
      "[data-testid=agents-panel-card]");

    // Exactly ONE row is in the tab order, and it is the active one.
    expect(rows.filter((row) => row.tabIndex === 0)).toHaveLength(1);
    expect(rows[0].tabIndex).toBe(0);
    expect(rows[1].tabIndex).toBe(-1);
    expect(rows[2].tabIndex).toBe(-1);
    expect(rows[0].getAttribute("aria-current")).toBe("true");
    expect(card()?.dataset.seatSlug).toBe("first");

    // Open the group the way a reader does, then put DOM focus on the row.
    const details = host.querySelector("details") as HTMLDetailsElement;
    details.open = true;
    await act(async () => { rows[0].focus(); });
    expect(document.activeElement).toBe(rows[0]);

    // ArrowDown: real keydown -> focus AND the live card move together.
    await act(async () => {
      rows[0].dispatchEvent(new KeyboardEvent("keydown", {
        key: "ArrowDown", bubbles: true,
      }));
    });
    expect(document.activeElement).toBe(rows[1]);
    expect(card()?.dataset.seatSlug).toBe("second");
    expect(rows[1].tabIndex).toBe(0);
    expect(rows[0].tabIndex).toBe(-1);

    // End: jump to the last seat.
    await act(async () => {
      (document.activeElement as HTMLElement).dispatchEvent(
        new KeyboardEvent("keydown", { key: "End", bubbles: true }));
    });
    expect(document.activeElement).toBe(rows[2]);
    expect(card()?.dataset.seatSlug).toBe("third");

    // Home: jump to the first seat.
    await act(async () => {
      (document.activeElement as HTMLElement).dispatchEvent(
        new KeyboardEvent("keydown", { key: "Home", bubbles: true }));
    });
    expect(document.activeElement).toBe(rows[0]);
    expect(card()?.dataset.seatSlug).toBe("first");

    // Clamp (documented): ArrowUp at the top stays on the first seat.
    await act(async () => {
      (document.activeElement as HTMLElement).dispatchEvent(
        new KeyboardEvent("keydown", { key: "ArrowUp", bubbles: true }));
    });
    expect(document.activeElement).toBe(rows[0]);
    expect(card()?.dataset.seatSlug).toBe("first");
  });
});
