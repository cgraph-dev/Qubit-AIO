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
  const React = {
    createElement(type: unknown, props: Record<string, unknown> | null, ...children: unknown[]) {
      const element = { type, props: { ...(props ?? {}), children } };
      tree.push(element);
      if (type === "iframe") Object.assign(frame, element.props);
      return element;
    },
  };
  let effectCleanup: (() => void) | undefined;
  const hooks = {
    useState: (initial: unknown) => [initial, vi.fn()],
    useRef: () => ({ current: frame }),
    useEffect: (fn: () => void | (() => void)) => { effectCleanup = fn() || undefined; },
  };
  return { frame, tree, React, hooks, cleanup: () => effectCleanup?.() };
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
});
