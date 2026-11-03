import { describe, expect, it } from "vitest";

import {
  BACKDROP_SCRIM_DARK,
  BACKDROP_SCRIM_LIGHT,
  BACKDROP_VIDEO_SRC,
  scrimOpacity,
} from "./QubitAmbient";

// The backdrop is a video under a theme-tinted scrim. Two contracts matter and
// neither is a snapshot of the current asset:
//
//  1. The scrim is what carries text contrast, so a LIGHT theme must clamp the
//     video harder than a dark one — otherwise a pale canvas stops reading as
//     light, and an earlier three.js scene hardcoded blue behind every theme.
//  2. The asset it points at must be the derived (blurred/darkened) clip, not
//     the raw source, whose neon signage is unreadable under text.

describe("qubit ambient backdrop", () => {
  it("points at the derived backdrop asset", () => {
    expect(BACKDROP_VIDEO_SRC).toBe("/media/qubit-backdrop.mp4");
  });

  it("clamps light themes harder than dark ones", () => {
    // `--qplate-contrast` is #000000 exactly when the canvas is light.
    expect(scrimOpacity("#000000")).toBe(BACKDROP_SCRIM_LIGHT);
    expect(scrimOpacity("#FFFFFF")).toBe(BACKDROP_SCRIM_DARK);

    expect(BACKDROP_SCRIM_LIGHT).toBeGreaterThan(BACKDROP_SCRIM_DARK);
    // Both must actually veil the video; a 0-opacity scrim is no scrim.
    expect(BACKDROP_SCRIM_DARK).toBeGreaterThan(0.5);
    expect(BACKDROP_SCRIM_LIGHT).toBeLessThanOrEqual(1);
  });

  it("falls back to the dark strength when the theme has not answered yet", () => {
    expect(scrimOpacity(undefined)).toBe(BACKDROP_SCRIM_DARK);
    expect(scrimOpacity("")).toBe(BACKDROP_SCRIM_DARK);
  });
});
