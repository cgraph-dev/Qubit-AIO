import { describe, expect, it } from "vitest";
import { railTooltipPosition } from "./railTooltipPosition";

const viewport = { x: 0, y: 0, width: 1100, height: 650 };
const rail = { left: 0, right: 312, top: 10, bottom: 620 };

describe("sidebar tooltip placement", () => {
  it("clears the whole expanded rail and clamps to its measured height", () => {
    expect(railTooltipPosition({ ...rail, top: 12, bottom: 50 }, rail, 284, 137, viewport))
      .toEqual({ placement: "right", left: 320, top: 12 });
    expect(railTooltipPosition({ ...rail, top: 590 }, rail, 284, 144, viewport)?.top).toBe(494);
  });

  it("flips to the left if the rail sits against the right edge", () => {
    const rightRail = { ...rail, left: 800, right: 1100 };
    expect(railTooltipPosition(rightRail, rightRail, 284, 110, viewport)?.placement).toBe("left");
  });

  it("does not cover a full-width drawer or a scrolled-away entry", () => {
    expect(railTooltipPosition(rail, rail, 284, 92, { ...viewport, width: 390 })).toBeNull();
    expect(railTooltipPosition({ ...rail, top: -60, bottom: -20 }, rail, 284, 92, viewport)).toBeNull();
  });
});
