// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { playLocalFx } from "./localFx";

const emitFxAt = vi.hoisted(() => vi.fn().mockResolvedValue(undefined));
vi.mock("./ui", () => ({ emitFxAt }));

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  emitFxAt.mockClear();
});

it("preserves the screen position when the picker removes its button", () => {
  vi.spyOn(document, "hidden", "get").mockReturnValue(false);
  const target = document.createElement("button");
  target.getBoundingClientRect = () => new DOMRect(110, 350, 40, 30);
  playLocalFx("impact", target, .5);
  expect(emitFxAt).toHaveBeenCalledWith("impact", target, { x: 130, y: 365, scale: .5 });
});

it("does not emit decorative cues in reduced motion", () => {
  vi.spyOn(document, "hidden", "get").mockReturnValue(false);
  vi.stubGlobal("matchMedia", () => ({ matches: true }));
  playLocalFx("focus", document.body);
  expect(emitFxAt).not.toHaveBeenCalled();
});
