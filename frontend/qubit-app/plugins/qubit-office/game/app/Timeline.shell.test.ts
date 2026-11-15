/* P4-07 shell regression: the timeline mounts as a Qubit-shell dock without
 * changing the iframe, the Agents/HUD docks, the toolbar offset or the menu
 * contracts. State-slot seeding mirrors the established shell harness. */
import { describe, expect, it, vi } from "vitest";
import { createOfficeApp } from "./OfficeAppShell";
import { MENU_BUTTONS } from "./office-menu";

function createStatefulHarness(seed: Record<number, unknown> = {}) {
  const frame = {
    contentWindow: { fetch: vi.fn() },
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    src: "",
  };
  const tree: Array<{ type: unknown; props: Record<string, unknown> }> = [];
  const refSlots: Array<{ current: unknown }> = [];
  const state: unknown[] = [];
  let stateIndex = 0;
  let refIndex = 0;
  const React = {
    createElement(
      type: unknown,
      props: Record<string, unknown> | null,
      ...children: unknown[]
    ) {
      const element = { type, props: { ...(props ?? {}), children } };
      tree.push(element);
      if (type === "iframe") {
        Object.assign(frame, element.props);
        const ref = element.props.ref as { current: unknown } | undefined;
        if (ref) ref.current = frame;
      }
      return element;
    },
  };
  const effectCleanups: Array<() => void> = [];
  const hooks = {
    useState: (initial: unknown) => {
      const index = stateIndex++;
      if (!(index in state)) {
        state[index] = index in seed ? seed[index] : initial;
      }
      const setter = (next: unknown) => {
        state[index] = typeof next === "function"
          ? (next as (value: unknown) => unknown)(state[index])
          : next;
      };
      return [state[index], setter];
    },
    useRef: (initial: unknown) => {
      const seedValue = refIndex === 0 ? frame : initial;
      const slot = refSlots[refIndex]
        ?? (refSlots[refIndex] = { current: seedValue });
      refIndex += 1;
      return slot;
    },
    useEffect: (fn: () => void | (() => void)) => {
      const cleanup = fn();
      if (typeof cleanup === "function") effectCleanups.push(cleanup);
    },
  };
  let ready = false;
  const App = createOfficeApp(React as never, hooks as never);
  return {
    frame,
    tree,
    render(props: Record<string, unknown> = {}) {
      if (ready) tree.length = 0;
      stateIndex = 0;
      refIndex = 0;
      ready = true;
      return App(props);
    },
    cleanup: () => {
      for (const cleanup of effectCleanups.splice(0)) cleanup();
    },
  };
}

describe("operator timeline shell dock", () => {
  it("mounts the timeline as a shell region without replacing the iframe", () => {
    const h = createStatefulHarness({ 14: true }); // timelineOpen
    h.render({});

    const dock = h.tree.find(
      (e) => e.props["data-testid"] === "office-game-timeline-dock");
    expect(dock).toBeDefined();
    expect(dock?.props["data-office-dock"]).toBe("timeline");
    expect(dock?.props.role).toBe("region");
    expect(dock?.props["aria-label"]).toBe("Operator timeline");
    expect(String(dock?.props.className)).toContain("office-game__timelineDock");

    const root = h.tree.find((e) => e.props["data-testid"] === "office-app");
    // The existing toolbar offset applies, so the sheet clears Panels/Agents/HUD.
    expect(root?.props["data-office-dock-top"]).toBe("toolbar");
    // A single timeline sheet is not a dual-dock split.
    expect(root?.props["data-office-docks"]).toBeUndefined();

    // The iframe keeps its full frame and the other docks are absent.
    const frame = h.tree.find((e) => e.type === "iframe");
    expect(frame?.props.style).toMatchObject({
      width: "100%", height: "100%", display: "block",
    });
    expect(h.tree.find(
      (e) => e.props["data-testid"] === "office-game-hud-dock")).toBeUndefined();
    expect(h.tree.find(
      (e) => e.props["data-testid"] === "office-game-agents-dock"))
      .toBeUndefined();
    h.cleanup();
  });

  it("exposes the timeline as an injected Panels icon, not a text toggle", () => {
    const h = createStatefulHarness({ 14: true });
    h.render({});
    // The dock exists...
    expect(h.tree.find(
      (e) => e.props["data-testid"] === "office-game-timeline-dock"))
      .toBeDefined();
    // ...and the duplicate shell text toggle is gone. The control is the
    // office's own icon menu entry, whose contract names the same dock.
    expect(h.tree.find(
      (e) => e.props["data-testid"] === "office-timeline-toggle")).toBeUndefined();
    const spec = MENU_BUTTONS.find((button) => button.id === "timeline");
    expect(spec).toBeDefined();
    expect(spec?.label).toBe("Timeline");
    expect(spec?.title).toContain("Timeline");
    expect(spec?.controls).toBe("office-game-timeline-dock");
    expect(spec?.icon.d).toMatch(/^M/);
    h.cleanup();
  });

  it("closing the timeline removes the sheet and clears the toolbar marker", () => {
    const h = createStatefulHarness({ 14: true });
    h.render({});
    expect(h.tree.find(
      (e) => e.props["data-testid"] === "office-game-timeline-dock"))
      .toBeDefined();
    // The dock's panel is the Timeline function element; call its real close
    // handler, which is what the dock's own Close button does.
    const panel = h.tree.find(
      (e) => typeof e.type === "function"
        && typeof e.props.onClose === "function");
    (panel?.props.onClose as () => void)();
    h.render({});
    expect(h.tree.find(
      (e) => e.props["data-testid"] === "office-game-timeline-dock"))
      .toBeUndefined();
    const root = h.tree.find((e) => e.props["data-testid"] === "office-app");
    expect(root?.props["data-office-dock-top"]).toBeUndefined();
    h.cleanup();
  });

  it("keeps the existing Agents/HUD dual-dock contract intact", () => {
    // State order: ..., hudOpen (3), agentsOpen (4), timelineOpen (14).
    const h = createStatefulHarness({ 3: true, 4: true });
    h.render({});
    const root = h.tree.find((e) => e.props["data-testid"] === "office-app");
    expect(root?.props["data-office-docks"]).toBe("both");
    expect(h.tree.find(
      (e) => e.props["data-testid"] === "office-game-hud-dock")).toBeDefined();
    expect(h.tree.find(
      (e) => e.props["data-testid"] === "office-game-agents-dock"))
      .toBeDefined();
    expect(h.tree.find(
      (e) => e.props["data-testid"] === "office-game-timeline-dock"))
      .toBeUndefined();
    h.cleanup();
  });
});
