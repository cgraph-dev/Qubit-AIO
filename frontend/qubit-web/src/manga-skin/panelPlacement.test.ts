import { describe, expect, it } from "vitest";
import { panelPlacement } from "./panelPlacement";

describe("Manga popup layout", () => {
  it("docks next to the sidebar and aligns the panel bottom with its trigger", () => {
    expect(panelPlacement({ left: 22, right: 308, top: 812, bottom: 852 },
      { width: 1440, height: 900, x: 0, y: 0 },
      { width: 390, height: 590, dock: true },
    )).toEqual({ width: 390, maxHeight: 590, left: 316, top: 262 });
  });

  it("keeps a touch picker inside the zoomed or keyboard-reduced viewport", () => {
    expect(panelPlacement({ left: 18, right: 308, top: 490, bottom: 540 },
      { width: 290, height: 320, x: 25, y: 40 },
      { width: 390, height: 590, dock: true },
    )).toEqual({ width: 266, maxHeight: 296, left: 37, top: 52 });
  });

  it("fits menus above bottom anchors and clamps them horizontally", () => {
    expect(panelPlacement({ left: 295, right: 314, top: 580, bottom: 610 },
      { width: 320, height: 640, x: 0, y: 0 },
      { width: 340, height: 280 },
    )).toEqual({ width: 296, maxHeight: 280, left: 12, top: 292 });
  });
});
