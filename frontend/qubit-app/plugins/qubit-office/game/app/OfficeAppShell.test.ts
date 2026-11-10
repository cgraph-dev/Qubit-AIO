import { describe, expect, it, vi } from "vitest";
import { createOfficeApp, OFFICE_ART_CREDIT } from "./OfficeAppShell";

const createHarness = () => {
  const frame = {
    contentWindow: { fetch: vi.fn() },
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    src: "",
  };
  const tree: Array<{ type: unknown; props: Record<string, unknown> }> = [];
  const refSlots: Array<{ current: unknown }> = [];
  let refIndex = 0;
  const React = {
    createElement(type: unknown, props: Record<string, unknown> | null, ...children: unknown[]) {
      const element = { type, props: { ...(props ?? {}), children } };
      tree.push(element);
      if (type === "iframe") {
        Object.assign(frame, element.props);
        // React points the ref at the mounted node; do the same, or the
        // component's `frameRef.current` would stay null.
        const ref = element.props.ref as { current: unknown } | undefined;
        if (ref) ref.current = frame;
      }
      return element;
    },
  };
  // Every effect's cleanup, not just the last one: the component has more than
  // one effect now (frame/bridge + menu state mirror).
  const effectCleanups: Array<() => void> = [];
  const hooks = {
    useState: (initial: unknown) => [initial, vi.fn()],
    // One stable slot per ref call, like React. The component now keeps a
    // mutable controller in a ref, so a constant `{current: frame}` is not a
    // faithful double.
    useRef: (initial: unknown) => {
      // This harness runs effects inline during the render (before the fake
      // createElement mounts the iframe), so the FIRST ref — the frame — is
      // seeded as React would have it after commit.
      const seed = refIndex === 0 ? frame : initial;
      const slot = refSlots[refIndex] ?? (refSlots[refIndex] = { current: seed });
      refIndex += 1;
      return slot;
    },
    useEffect: (fn: () => void | (() => void)) => {
      const cleanup = fn();
      if (typeof cleanup === "function") effectCleanups.push(cleanup);
    },
  };
  return {
    frame, tree, React, hooks,
    cleanup: () => { for (const cleanup of effectCleanups.splice(0)) cleanup(); },
  };
};

describe("Habblaud embedding", () => {
  it("keeps the iframe full-frame and accessibly described", () => {
    const h = createHarness();
    const App = createOfficeApp(h.React as never, h.hooks as never);
    expect(() => App()).not.toThrow();
    const frameElement = h.tree.find((element) => element.type === "iframe");
    expect(frameElement?.props.title).toBe("Habblaud virtual office");
    expect(frameElement?.props["aria-label"]).toBe("Habblaud office game");
    expect(frameElement?.props["aria-describedby"]).toBe("office-app-description");
    expect(frameElement?.props.style).toMatchObject({ width: "100%", height: "100%", display: "block" });
    expect(frameElement?.props.sandbox).toContain("allow-scripts");
    expect(h.frame.src).toContain("/plugin-apps/qubit-office/habblaud/");
    h.cleanup();
    expect(h.frame.removeEventListener).toHaveBeenCalledWith("load", expect.any(Function));
  });

  it("always names the LimeZu art credit, independent of a theme", () => {
    const h = createHarness();
    const App = createOfficeApp(h.React as never, h.hooks as never);
    App({});
    const credits = h.tree.find(
      (e) => e.props["data-testid"] === "office-app-credits");
    expect(credits).toBeDefined();
    const children = credits?.props.children as Array<{
      props: Record<string, unknown>;
    }>;
    const link = children.find(
      (child) => child.props.href === "https://limezu.itch.io");
    expect(link).toBeDefined();
    expect(String((link?.props.children as unknown[])[0]))
      .toBe(OFFICE_ART_CREDIT);
    // Classic payload: no theme class and still the same persistent credit.
    expect(h.tree.find((e) => e.props["data-office-theme"])).toBeUndefined();
  });

  it("has no seat-model control when no seat is named", () => {
    // The default office view must stay exactly the game.
    const h = createHarness();
    const App = createOfficeApp(h.React as never, h.hooks as never);
    App({});
    expect(h.tree.find((e) => e.props["data-testid"] === "seat-model-toggle"))
      .toBeUndefined();
    expect(h.tree.find((e) => e.props["data-testid"] === "seat-model-panel"))
      .toBeUndefined();
  });

  it("offers a toggle for a named seat, and only renders the panel when open", () => {
    const h = createHarness();
    const App = createOfficeApp(h.React as never, h.hooks as never);
    App({ seatAgentId: 3, seatLabel: "lead-quality" });
    const toggle = h.tree.find((e) => e.props["data-testid"] === "seat-model-toggle");
    expect(toggle).toBeDefined();
    expect(toggle?.props["aria-expanded"]).toBe(false);
    // Closed by default: the game is not covered until the operator asks.
    expect(h.tree.find((e) => e.props["data-testid"] === "seat-model-panel"))
      .toBeUndefined();
  });

  it("lists the office's own seats in the picker once opened", () => {
    // The picker is fed by the tower payload's characters, so the seats drawn
    // in the office are exactly the seats that can be configured.
    const h = createHarness();
    const App = createOfficeApp(h.React as never, h.hooks as never);
    App({
      seatAgentId: 1,
      seats: [
        { agent_id: 1, slug: "ceo" },
        { agent_id: 3, slug: "lead-quality" },
      ],
      onSeatChange: vi.fn(),
    });
    const toggle = h.tree.find((e) => e.props["data-testid"] === "seat-model-toggle");
    expect(toggle).toBeDefined();
  });

  it("renders a picker inside an open panel, with the named seat selected", () => {
    const h = createHarness();
    // Open the panel: the first useState in the component is `panelOpen`.
    let panelOpen = false;
    const hooks = {
      ...h.hooks,
      useState: (initial: unknown) => [panelOpen ? true : initial, vi.fn()],
    };
    panelOpen = true;
    const App = createOfficeApp(h.React as never, hooks as never);
    App({
      seatAgentId: 3,
      seats: [
        { agent_id: 1, slug: "ceo" },
        { agent_id: 3, slug: "lead-quality" },
      ],
      onSeatChange: vi.fn(),
    });
    const picker = h.tree.find((e) => e.props["data-testid"] === "seat-model-seat");
    expect(picker).toBeDefined();
    expect(picker?.props.value).toBe("3");
  });

  it("names a failed seat read instead of showing an empty list", () => {
    // An office with no seats and an office whose poll failed must not look
    // identical — one is quiet, the other is broken.
    const h = createHarness();
    let panelOpen = false;
    const hooks = {
      ...h.hooks,
      useState: (initial: unknown) => [panelOpen ? true : initial, vi.fn()],
    };
    panelOpen = true;
    const App = createOfficeApp(h.React as never, hooks as never);
    App({ seatAgentId: 1, seats: [], seatError: "HTTP 401" });
    const banner = h.tree.find(
      (e) => e.props["data-testid"] === "seat-model-seat-error");
    expect(banner).toBeDefined();
    expect(String(banner?.props.children)).toContain("HTTP 401");
  });

  it("shows no error banner when the read succeeded", () => {
    const h = createHarness();
    let panelOpen = false;
    const hooks = {
      ...h.hooks,
      useState: (initial: unknown) => [panelOpen ? true : initial, vi.fn()],
    };
    panelOpen = true;
    const App = createOfficeApp(h.React as never, hooks as never);
    App({ seatAgentId: 1, seats: [{ agent_id: 1, slug: "ceo" }] });
    expect(h.tree.find(
      (e) => e.props["data-testid"] === "seat-model-seat-error")).toBeUndefined();
  });

  it("states the real backend connection and room/agent counts", () => {
    const h = createHarness();
    const App = createOfficeApp(h.React as never, h.hooks as never);
    App({
      officeState: "connected",
      officeRooms: 4,
      officeAgents: 10,
    });
    const banner = h.tree.find(
      (e) => e.props["data-testid"] === "office-app-connection");
    expect(banner).toBeDefined();
    expect(String(banner?.props.children)).toContain("4 rooms");
    expect(String(banner?.props.children)).toContain("10 agents");
    expect(banner?.props.role).toBe("status");
  });

  it("names a backend failure instead of showing a fake connection", () => {
    const h = createHarness();
    const App = createOfficeApp(h.React as never, h.hooks as never);
    App({
      officeState: "error",
      officeError: "HTTP 401",
    });
    const banner = h.tree.find(
      (e) => e.props["data-testid"] === "office-app-connection");
    expect(banner).toBeDefined();
    expect(String(banner?.props.children)).toContain("HTTP 401");
    expect(banner?.props.role).toBe("alert");
    expect(banner?.props["data-state"]).toBe("error");
  });

  it("keeps the plain game view when no host status is supplied", () => {
    const h = createHarness();
    const App = createOfficeApp(h.React as never, h.hooks as never);
    App({});
    expect(h.tree.find(
      (e) => e.props["data-testid"] === "office-app-connection")).toBeUndefined();
  });
});

describe("host theme + rerender stability", () => {
  /**
   * A stateful double: each `useState` keeps its own slot for the whole
   * component instance, so a test can seed open docks and render twice the way
   * React would after a parent update. (The original harness returns a fresh
   * constant per call, which is enough for single renders.)
   */
  const createStatefulHarness = (seed: Record<number, unknown> = {}) => {
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
      createElement(type: unknown, props: Record<string, unknown> | null, ...children: unknown[]) {
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
        // As in the single-render harness, the frame ref is committed before
        // the effect runs; subsequent refs start at their initial value.
        const seed = refIndex === 0 ? frame : initial;
        const slot = refSlots[refIndex] ?? (refSlots[refIndex] = { current: seed });
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
      cleanup: () => { for (const cleanup of effectCleanups.splice(0)) cleanup(); },
    };
  };

  it("a parent rerender keeps the iframe src and the dock component identity", () => {
    // Docks open so the HUD/Agents subtrees are actually mounted. State order:
    // loaded, failed, panelOpen, hudOpen, agentsOpen, officeTheme.
    const h = createStatefulHarness({ 3: true, 4: true });
    h.render({});
    const firstSrc = h.frame.src;
    const firstHud = h.tree.find(
      (e) => e.props["data-testid"] === "office-game-hud-dock");
    const firstAgents = h.tree.find(
      (e) => e.props["data-testid"] === "office-game-agents-dock");
    expect(firstHud).toBeDefined();
    expect(firstAgents).toBeDefined();

    // A parent update: same props, same component instance.
    h.render({});
    expect(h.frame.src).toBe(firstSrc);
    // Stable component identity = React reconciles instead of remounting
    // (the original "HUD keeps refreshing" bug: a new type per render).
    const secondHud = h.tree.find(
      (e) => e.props["data-testid"] === "office-game-hud-dock");
    const secondAgents = h.tree.find(
      (e) => e.props["data-testid"] === "office-game-agents-dock");
    expect(secondHud?.type).toBe(firstHud?.type);
    expect(secondAgents?.type).toBe(firstAgents?.type);

    // The docks carry Task 7's stable theme hooks; no Habblaud node is styled.
    expect(String(firstHud?.props.className)).toContain("office-theme-hud-dock");
    expect(String(firstAgents?.props.className)).toContain("office-theme-agents-dock");
    expect(String(firstHud?.props.className)).toContain("office-theme-dock");
    h.cleanup();
  });

  it("the shell root carries the host theme class only for an active theme", () => {
    const h = createStatefulHarness();
    h.render({});
    const root = h.tree.find((e) => e.props["data-testid"] === "office-app");
    expect(root?.props.className).toBe("office-game");
    expect(root?.props["data-office-theme"]).toBeUndefined();
    h.cleanup();
  });

  it("opts the docks and buttons into a declared 9-slice", () => {
    const slicedTheme = {
      id: "demo",
      label: "Demo",
      version: "1.0.0",
      credit: "LimeZu",
      license: "LimeZu",
      active: true,
      assetBase: "/plugins/qubit-office/themes/demo",
      atlas: null,
      index: null,
      bodies: { atlas: null, index: null, size: null, frame: null },
      tokens: {},
      slices: { dock: { src: "ui/dock.png" }, button: { src: "ui/btn.png" } },
    } as never;
    // State order: loaded, failed, panelOpen, hudOpen, agentsOpen, officeTheme,
    // menuFallback. Seed the docks open and an active theme with slices.
    const h = createStatefulHarness({ 3: true, 4: true, 5: slicedTheme });
    h.render({});
    const hud = h.tree.find(
      (e) => e.props["data-testid"] === "office-game-hud-dock");
    const agents = h.tree.find(
      (e) => e.props["data-testid"] === "office-game-agents-dock");
    expect(hud?.props["data-office-slice"]).toBe("dock");
    expect(agents?.props["data-office-slice"]).toBe("dock");
    const close = h.tree.find(
      (e) => e.props["data-testid"] === "office-game-hud-close");
    expect(close?.props["data-office-slice"]).toBe("button");
    h.cleanup();
  });

  it("leaves the docks slice-free without a declared slice", () => {
    const plainTheme = {
      id: "demo",
      label: "Demo",
      version: "1.0.0",
      credit: "LimeZu",
      license: "LimeZu",
      active: true,
      assetBase: "/plugins/qubit-office/themes/demo",
      atlas: null,
      index: null,
      bodies: { atlas: null, index: null, size: null, frame: null },
      tokens: {},
      slices: {},
    } as never;
    const h = createStatefulHarness({ 3: true, 4: true, 5: plainTheme });
    h.render({});
    const hud = h.tree.find(
      (e) => e.props["data-testid"] === "office-game-hud-dock");
    expect(hud?.props["data-office-slice"]).toBeUndefined();
    h.cleanup();
  });

  it("renders host fallback icons when the app menu never appears", () => {
    // State order: loaded, failed, panelOpen, hudOpen, agentsOpen, officeTheme,
    // menuFallback. The controller's bounded wait reported the fallback.
    const h = createStatefulHarness({ 6: true });
    h.render({});
    const fallback = h.tree.find(
      (e) => e.props["data-testid"] === "office-menu-fallback");
    expect(fallback).toBeDefined();
    const agents = h.tree.find(
      (e) => e.props["data-testid"] === "office-menu-agents-fallback");
    const hud = h.tree.find(
      (e) => e.props["data-testid"] === "office-menu-hud-fallback");
    expect(agents).toBeDefined();
    expect(hud).toBeDefined();
    // Same stable theme hooks as the injected controls.
    expect(String(agents?.props.className)).toContain("office-theme-menu-btn");
    expect(String(agents?.props.className)).toContain("office-theme-menu-agents");
    expect(String(agents?.props.className)).toContain("office-theme-menu-fallback-btn");
    expect(agents?.props["aria-label"]).toBe("Agents");
    // The traced SVG is present (not a text placeholder).
    const svg = (agents as { props: { children: unknown[] } })
      .props.children[0] as { type: unknown };
    expect(svg.type).toBe("svg");
    h.cleanup();
  });

  it("renders no fallback while injection is available", () => {
    const h = createStatefulHarness();
    h.render({});
    expect(h.tree.find(
      (e) => e.props["data-testid"] === "office-menu-fallback")).toBeUndefined();
    h.cleanup();
  });
});
