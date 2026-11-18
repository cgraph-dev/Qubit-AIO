import { describe, expect, it } from "vitest";
import {
  DEFAULT_PETAL_COUNT,
  PETAL_TOKEN_CYCLE,
  computeAmbientCameraDrift,
  createPetalPool,
  lanternAnimationDelay,
  petalCssVars,
  sampleLanternFlicker,
  sampleMoonGlow,
} from "./ambient-motion";
import { isReducedMotionActive } from "./reduced-motion";

describe("createPetalPool", () => {
  it("is deterministic for a seed", () => {
    expect(createPetalPool(8, 7)).toEqual(createPetalPool(8, 7));
    expect(createPetalPool(8, 7)).not.toEqual(createPetalPool(8, 8));
  });

  it("builds the default sparse pool from token fills only", () => {
    const pool = createPetalPool();
    expect(pool).toHaveLength(DEFAULT_PETAL_COUNT);
    for (const petal of pool) {
      expect(PETAL_TOKEN_CYCLE).toContain(petal.fillToken);
      expect(petal.fallDurationMs).toBeGreaterThanOrEqual(11500);
      expect(petal.fallDurationMs).toBeLessThanOrEqual(18500);
      expect(petal.opacity).toBeGreaterThanOrEqual(0.44);
      expect(petal.opacity).toBeLessThanOrEqual(0.74);
      expect(petal.x0).toBeGreaterThanOrEqual(0);
      expect(petal.x0).toBeLessThan(1);
    }
  });

  it("accepts zero petals without inventing any", () => {
    expect(createPetalPool(0)).toEqual([]);
    expect(createPetalPool(-3)).toEqual([]);
  });
});

describe("petalCssVars", () => {
  const petal = createPetalPool(1, 3)[0]!;

  it("turns one petal into CSS custom properties", () => {
    const vars = petalCssVars(petal, 1600, 900);
    expect(vars["--fx-petal-x"]).toMatch(/px$/);
    expect(vars["--fx-petal-height"]).toBe("900px");
    expect(vars["--fx-petal-fall"]).toBe(`${petal.fallDurationMs}ms`);
    expect(vars["--fx-petal-sway"]).toBe(`${petal.swayPx}px`);
    expect(Number(vars["--fx-petal-delay"].replace("ms", ""))).toBeLessThanOrEqual(0);
  });

  it("falls back to the reference viewport for invalid dimensions", () => {
    const vars = petalCssVars(petal, Number.NaN, 0);
    expect(vars["--fx-petal-height"]).toBe("959px");
    expect(vars["--fx-petal-x"]).toMatch(/px$/);
  });
});

describe("ambient samplers", () => {
  it("keeps lantern flicker inside the flash-safe band", () => {
    for (let t = 0; t < 20000; t += 137) {
      const value = sampleLanternFlicker(t, 2.7);
      expect(value).toBeGreaterThanOrEqual(0.86);
      expect(value).toBeLessThanOrEqual(1);
    }
  });

  it("freezes moon and lantern under reduced motion", () => {
    expect(sampleMoonGlow(1234, true)).toBe(0.88);
    expect(sampleLanternFlicker(1234, 4.3, true)).toBe(0.92);
    expect(computeAmbientCameraDrift(9000, { reducedMotion: true }))
      .toEqual({ dx: 0, dy: 0 });
  });

  it("zeroes camera drift while the operator interacts", () => {
    expect(computeAmbientCameraDrift(9000, { isInteracting: true }))
      .toEqual({ dx: 0, dy: 0 });
  });

  it("keeps camera drift on the documented amplitude", () => {
    for (let t = 0; t < 60000; t += 311) {
      const { dx, dy } = computeAmbientCameraDrift(t);
      expect(Math.abs(dx)).toBeLessThanOrEqual(4);
      expect(Math.abs(dy)).toBeLessThanOrEqual(2);
    }
  });

  it("derives a negative lantern delay from the seed", () => {
    expect(lanternAnimationDelay({ id: "x", nx: 0, ny: 0, radiusPx: 40, seed: 2.7 }))
      .toMatch(/^-\d+\.\d{2}s$/);
  });
});

describe("reduced-motion detection", () => {
  it("is safe without a DOM", () => {
    expect(isReducedMotionActive()).toBe(false);
  });
});
