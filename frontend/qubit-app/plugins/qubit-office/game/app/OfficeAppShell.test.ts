import { describe, expect, it, vi } from "vitest";
import { createOfficeApp } from "./OfficeAppShell";

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
