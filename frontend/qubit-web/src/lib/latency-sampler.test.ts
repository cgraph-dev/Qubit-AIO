import { describe, expect, it } from "vitest";

import {
  formatLatency,
  LatencySampler,
  measureLatency,
} from "./latency-sampler";

describe("LatencySampler", () => {
  it("keeps the latest sample and a smoothed average", () => {
    const sampler = new LatencySampler(0.5);
    sampler.record(10);
    expect(sampler.stats).toEqual({ latestMs: 10, averageMs: 10, samples: 1 });

    sampler.record(20);
    expect(sampler.stats.latestMs).toBe(20);
    expect(sampler.stats.averageMs).toBe(15);
    expect(sampler.stats.samples).toBe(2);
  });

  it("ignores non-finite and negative samples", () => {
    const sampler = new LatencySampler();
    sampler.record(Number.NaN);
    sampler.record(Number.POSITIVE_INFINITY);
    sampler.record(-1);
    expect(sampler.stats).toEqual({ latestMs: null, averageMs: null, samples: 0 });
  });

  it("resets every reading", () => {
    const sampler = new LatencySampler();
    sampler.record(12);
    sampler.reset();
    expect(sampler.stats).toEqual({ latestMs: null, averageMs: null, samples: 0 });
  });

  it("falls back to the default weight for an invalid alpha", () => {
    const sampler = new LatencySampler(0);
    sampler.record(10);
    sampler.record(20);
    // alpha 0 -> 0.3 default: 0.3*20 + 0.7*10 = 13
    expect(sampler.stats.averageMs).toBeCloseTo(13, 5);
  });
});

describe("formatLatency", () => {
  it("renders a rounded millisecond value", () => {
    expect(formatLatency(12.4)).toBe("12 ms");
  });

  it("renders sub-millisecond and missing values honestly", () => {
    expect(formatLatency(0.4)).toBe("<1 ms");
    expect(formatLatency(null)).toBe("—");
    expect(formatLatency(undefined)).toBe("—");
    expect(formatLatency(Number.NaN)).toBe("—");
  });
});

describe("measureLatency", () => {
  it("returns the operation value with its elapsed time", async () => {
    let clock = 100;
    const result = await measureLatency(
      async () => "ok",
      () => (clock += 25),
    );
    expect(result).toEqual({ value: "ok", ms: 25 });
  });
});
