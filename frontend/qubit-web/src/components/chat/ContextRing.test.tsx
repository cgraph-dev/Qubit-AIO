// @vitest-environment jsdom
/**
 * ContextRing — pins the two things that make the ring trustworthy rather
 * than decorative:
 *
 * 1. No measurement renders the no-data state (`·`), never a fabricated
 *    percentage. The absorbed upstream made exactly this call
 *    (hermes-webui `static/ui.js:9940-9972`) and it is the reason the ring is
 *    safe to show at all.
 * 2. Pressure bands are read from the number, not guessed, and the label
 *    always carries both token counts so a user can check the arithmetic.
 */
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { ContextRing } from "./ContextRing";
import {
  contextPercent,
  contextPressure,
  contextRingLabel,
} from "./contextRingMath";

let container: HTMLDivElement;
let root: Root;

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

beforeEach(() => {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

function render(node: React.ReactNode) {
  act(() => root.render(node));
}

function ring(): HTMLElement {
  const el = container.querySelector<HTMLElement>('[data-testid="context-ring"]');
  if (!el) throw new Error("context ring not rendered");
  return el;
}

describe("contextPercent", () => {
  it("divides the LAST prompt by the window", () => {
    expect(contextPercent(32_000, 128_000)).toBeCloseTo(25, 5);
  });

  it("returns null — not 0 — when either side is unknown", () => {
    expect(contextPercent(null, 128_000)).toBeNull();
    expect(contextPercent(1_000, null)).toBeNull();
    expect(contextPercent(0, 128_000)).toBeNull();
    expect(contextPercent(1_000, 0)).toBeNull();
    expect(contextPercent(Number.NaN, 128_000)).toBeNull();
  });
});

describe("contextPressure", () => {
  it("bands at 50 and 85 inclusive as calm", () => {
    expect(contextPressure(0)).toBe("calm");
    expect(contextPressure(50)).toBe("calm");
    expect(contextPressure(50.1)).toBe("warm");
    expect(contextPressure(85)).toBe("warm");
    expect(contextPressure(85.1)).toBe("hot");
    expect(contextPressure(100)).toBe("hot");
  });
});

describe("contextRingLabel", () => {
  it("states the measurement and both token counts", () => {
    const label = contextRingLabel(25, 32_000, 128_000);
    expect(label).toContain("25% used");
    expect(label).toContain("75% left");
    expect(label).toContain("32.0K");
    expect(label).toContain("128.0K");
  });

  it("says so plainly when there is no measurement", () => {
    expect(contextRingLabel(null)).toBe("Context: no measurement yet");
    expect(contextRingLabel(null, 4_000)).toContain("4.0K tokens used");
    expect(contextRingLabel(null, 4_000)).toContain("window unknown");
  });
});

describe("ContextRing render", () => {
  it("shows a dot, not a number, when unmeasured", () => {
    render(<ContextRing percent={null} />);
    const el = ring();
    expect(el.dataset.state).toBe("unmeasured");
    expect(el.dataset.pressure).toBe("none");
    expect(el.textContent).toContain("·");
    expect(el.textContent).not.toMatch(/\d%/);
  });

  it("renders the measured percentage and its pressure band", () => {
    render(<ContextRing percent={62} usedTokens={79_360} windowTokens={128_000} />);
    const el = ring();
    expect(el.dataset.state).toBe("measured");
    expect(el.dataset.pressure).toBe("warm");
    expect(el.textContent).toContain("62");
    expect(el.getAttribute("aria-label")).toContain("62% used");
  });

  it("clamps the arc at a full circle but keeps the true label", () => {
    render(<ContextRing percent={140} usedTokens={179_200} windowTokens={128_000} />);
    const el = ring();
    expect(el.dataset.pressure).toBe("hot");
    const arc = container.querySelector<SVGCircleElement>(
      '[data-testid="context-ring-arc"]',
    );
    expect(arc).not.toBeNull();
    // 140% still paints a full ring rather than wrapping past the circle.
    expect(Number(arc!.getAttribute("stroke-dashoffset"))).toBeCloseTo(0, 3);
    expect(el.getAttribute("aria-label")).toContain("100% used");
  });

  it("does not derive a percentage from a missing window", () => {
    render(<ContextRing percent={null} usedTokens={12_000} />);
    expect(ring().dataset.state).toBe("unmeasured");
  });
});
