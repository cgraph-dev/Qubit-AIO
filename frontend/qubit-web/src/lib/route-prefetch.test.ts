// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  __resetPrefetchState,
  hoverPrefetch,
  prefetchRoute,
} from "./route-prefetch";

/**
 * Prefetching must be invisible: it warms a chunk and nothing else. These
 * tests pin the three properties that keep it safe — one request per route,
 * failures swallowed, and no request at all when the session asked us not to
 * spend bandwidth.
 */

function stubEnv(opts: {
  reducedMotion?: boolean;
  saveData?: boolean;
  effectiveType?: string;
} = {}) {
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: Boolean(opts.reducedMotion) && query.includes("reduced-motion"),
    media: query,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    onchange: null,
    dispatchEvent: () => false,
  }));
  Object.defineProperty(navigator, "connection", {
    configurable: true,
    value: { saveData: Boolean(opts.saveData), effectiveType: opts.effectiveType },
  });
}

beforeEach(() => {
  __resetPrefetchState();
  stubEnv();
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("prefetchRoute", () => {
  it("imports a route once and never again", async () => {
    const importer = vi.fn(async () => ({ default: () => null }));

    prefetchRoute("/sessions", importer);
    prefetchRoute("/sessions", importer);
    await Promise.resolve();

    expect(importer).toHaveBeenCalledTimes(1);
  });

  it("swallows a failing import and allows a retry", async () => {
    const importer = vi
      .fn<() => Promise<unknown>>()
      .mockRejectedValueOnce(new Error("chunk 404"))
      .mockResolvedValueOnce({ ok: true });

    prefetchRoute("/cron", importer);
    await Promise.resolve();
    await Promise.resolve();

    prefetchRoute("/cron", importer);
    await Promise.resolve();

    expect(importer).toHaveBeenCalledTimes(2);
  });

  it("does nothing when there is no importer", () => {
    expect(() => prefetchRoute("/nope", undefined)).not.toThrow();
  });
});

describe("route prefetch opt-outs", () => {
  it("skips under prefers-reduced-motion", () => {
    stubEnv({ reducedMotion: true });
    const importer = vi.fn(async () => ({}));

    prefetchRoute("/skills", importer);

    expect(importer).not.toHaveBeenCalled();
  });

  it("skips on Save-Data connections", () => {
    stubEnv({ saveData: true });
    const importer = vi.fn(async () => ({}));

    prefetchRoute("/skills", importer);

    expect(importer).not.toHaveBeenCalled();
  });

  it("skips on 2g connections", () => {
    stubEnv({ effectiveType: "2g" });
    const importer = vi.fn(async () => ({}));

    prefetchRoute("/skills", importer);

    expect(importer).not.toHaveBeenCalled();
  });
});

describe("hoverPrefetch", () => {
  it("waits for the dwell before importing", () => {
    vi.useFakeTimers();
    const importer = vi.fn(async () => ({}));

    hoverPrefetch("/files", importer, 90);
    expect(importer).not.toHaveBeenCalled();

    vi.advanceTimersByTime(89);
    expect(importer).not.toHaveBeenCalled();

    vi.advanceTimersByTime(1);
    expect(importer).toHaveBeenCalledTimes(1);
  });

  it("cancels when the pointer leaves before the dwell", () => {
    vi.useFakeTimers();
    const importer = vi.fn(async () => ({}));

    const cancel = hoverPrefetch("/files", importer, 90);
    cancel();
    vi.advanceTimersByTime(500);

    expect(importer).not.toHaveBeenCalled();
  });
});
