// @vitest-environment jsdom
/* The REAL keyboard path, in a real DOM with the host React.
 *
 * WHY THIS FILE EXISTS
 * --------------------
 * The card is the single keyboard owner of the roster: it is always visible,
 * always tabbable, and drives the active seat across every division — including
 * divisions whose rows are collapsed and therefore cannot take focus. This test
 * mounts the panel with React 19 + jsdom while ALL divisions are collapsed,
 * focuses the card, sends real `keydown` events, and proves the reachable path:
 * ArrowDown opens the target division and updates the card, Home/End jump, and
 * no row is ever mistaken for a tab stop.
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
  useRef: (value: unknown) => { current: unknown };
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

/** Two divisions, one seat each: the minimal cross-division keyboard case. */
function twoDivisionPayload(): AgentPanelData {
  const alpha = [seat({
    slug: "alpha-one", name: "Alpha One", division: "alpha",
  })];
  const beta = [seat({
    slug: "beta-one", name: "Beta One", division: "beta", room: "beta-desk-a",
  })];
  return {
    ok: true,
    counts: {
      headcount: 2, divisions: 2, roster_divisions: 2,
      boardroom_seats: 0, rooms: 2, floors: 2,
      seat_types: { worker: 2 }, per_division: { alpha: 1, beta: 1 },
    },
    divisions: [
      { slug: "alpha", label: "Alpha", kind: "product", floor: 1,
        headcount: 1, seats: alpha },
      { slug: "beta", label: "Beta", kind: "product", floor: 2,
        headcount: 1, seats: beta },
    ],
    seats: [...alpha, ...beta],
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

const press = async (target: HTMLElement, key: string) => {
  await act(async () => {
    target.dispatchEvent(new KeyboardEvent("keydown", { key, bubbles: true }));
  });
};

describe("the agents panel's real keyboard path", () => {
  it("makes the always-visible card the reachable keyboard owner", async () => {
    const Panel = createAgentsPanel(
      React as never,
      {
        useState: React.useState,
        useEffect: React.useEffect,
        useRef: React.useRef,
      } as never,
    );
    const fetchImpl = (async () =>
      respond(twoDivisionPayload())) as unknown as typeof fetch;

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

    const card = () => host.querySelector<HTMLElement>(
      "[data-testid=agents-panel-card]")!;
    const cardName = () => host.querySelector(
      "[data-testid=agents-panel-card] .agents-panel__cardName")?.textContent;
    const rows = () => Array.from(host.querySelectorAll<HTMLElement>(
      "[data-testid=agents-panel-seat]"));
    const division = (slug: string) => host.querySelector<HTMLDetailsElement>(
      `[data-division="${slug}"]`)!;

    // Everything starts collapsed; the card is still the one reachable stop.
    expect(division("alpha").open).toBe(false);
    expect(division("beta").open).toBe(false);
    expect(card().tabIndex).toBe(0);
    expect(card().getAttribute("role")).toBe("group");
    expect(card().getAttribute("aria-live")).toBe("polite");
    expect(card().getAttribute("aria-atomic")).toBe("true");
    // The card is the ONLY keyboard entry into the roster.
    const tabStops = host.querySelectorAll<HTMLElement>('[tabindex="0"]');
    expect(tabStops).toHaveLength(1);
    expect(tabStops[0]).toBe(card());
    await act(async () => { card().focus(); });
    expect(document.activeElement).toBe(card());

    // No row is a tab stop, collapsed or not.
    expect(rows().length).toBe(2);
    expect(rows().every((row) => row.tabIndex === -1)).toBe(true);
    expect(rows().some((row) => row.tabIndex === 0)).toBe(false);
    expect(cardName()).toBe("Alpha One");

    // ArrowDown moves the active seat to the OTHER division's collapsed row…
    await press(card(), "ArrowDown");
    expect(document.activeElement).toBe(card());
    expect(cardName()).toBe("Beta One");
    // …and opens that division, without handing focus to the row.
    expect(division("beta").open).toBe(true);
    expect(division("alpha").open).toBe(false);
    // The card is the live readout; the row id is a stable anchor, not a focus
    // target or an out-of-scope aria-activedescendant.
    expect(rows().every((row) => row.tabIndex === -1)).toBe(true);

    // End -> last, Home -> first (opening the first division on the way).
    await press(card(), "End");
    expect(cardName()).toBe("Beta One");
    await press(card(), "Home");
    expect(cardName()).toBe("Alpha One");
    expect(division("alpha").open).toBe(true);

    // Clamp both ends (documented): no wrap.
    await press(card(), "ArrowUp");
    expect(cardName()).toBe("Alpha One");
    await press(card(), "End");
    await press(card(), "ArrowDown");
    expect(cardName()).toBe("Beta One");
    expect(document.activeElement).toBe(card());
  });
});
