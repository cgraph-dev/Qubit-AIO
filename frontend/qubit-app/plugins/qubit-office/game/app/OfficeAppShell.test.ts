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
});
