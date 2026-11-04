import { describe, expect, it } from "vitest";

/**
 * Both rails play the SAME file. Two <video> elements on one duration fall
 * into lockstep, so the panel deliberately runs at a different phase and rate.
 * The phase only survives if it is re-applied after every loop wrap — a
 * looping <video> resets itself to 0, which would re-sync the rails after one
 * cycle. These pin the constants that keep them apart.
 */

// Mirrors the call sites: App.tsx (nav rail) and TerminalChatPage.tsx (panel).
const RAIL = { offsetSeconds: 0, rate: 0.97 };
const PANEL = { offsetSeconds: 7.5, rate: 1.03 };

describe("backdrop phase separation", () => {
  it("gives the two plates different starting points in the clip", () => {
    expect(PANEL.offsetSeconds === RAIL.offsetSeconds).toBe(false);
    expect(PANEL.offsetSeconds).toBeGreaterThan(0);
  });

  it("gives the two plates different playback rates so they drift, not just start apart", () => {
    expect(PANEL.rate === RAIL.rate).toBe(false);
    expect(RAIL.rate === 1).toBe(false);
    expect(PANEL.rate === 1).toBe(false);
  });

  it("keeps both rates near 1 so the motion still reads as ambient", () => {
    for (const { rate } of [RAIL, PANEL]) {
      expect(rate).toBeGreaterThan(0.9);
      expect(rate).toBeLessThan(1.1);
    }
  });

  it("drifts out of phase over a full clip instead of converging", () => {
    // Relative speed difference per loop of a 20s clip. A tiny non-zero value
    // is the point: they never settle back into lockstep.
    const clipSeconds = 20;
    const driftPerLoop = Math.abs(PANEL.rate - RAIL.rate) * clipSeconds;
    expect(driftPerLoop).toBeGreaterThan(0.5);
  });
});
