// @vitest-environment jsdom
/**
 * Chunk-reload recovery: one automatic reload on a stale-chunk preload
 * error, never a loop.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

import { installChunkReloadRecovery } from "./preload-recovery";

function fakeStorage() {
  const data = new Map<string, string>();
  return {
    getItem: (k: string) => (data.has(k) ? data.get(k)! : null),
    setItem: (k: string, v: string) => void data.set(k, v),
    removeItem: (k: string) => void data.delete(k),
  };
}

function fakeTarget() {
  const listeners = new Map<string, Set<() => void>>();
  return {
    addEventListener: vi.fn((type: string, fn: () => void) => {
      let set = listeners.get(type);
      if (!set) listeners.set(type, (set = new Set()));
      set.add(fn);
    }),
    removeEventListener: vi.fn((type: string, fn: () => void) => {
      listeners.get(type)?.delete(fn);
    }),
    fire: (type: string) => {
      listeners.get(type)?.forEach((fn) => fn());
    },
  };
}

beforeEach(() => {
  vi.restoreAllMocks();
});

describe("installChunkReloadRecovery", () => {
  it("reloads once on the first preload error", () => {
    const target = fakeTarget();
    const reload = vi.fn();
    const uninstall = installChunkReloadRecovery(target, reload, fakeStorage());
    target.fire("vite:preloadError");
    expect(reload).toHaveBeenCalledTimes(1);
    // A second error on the same page load does not reload again.
    target.fire("vite:preloadError");
    expect(reload).toHaveBeenCalledTimes(1);
    uninstall();
    expect(target.removeEventListener).toHaveBeenCalledWith(
      "vite:preloadError",
      expect.any(Function),
    );
  });

  it("does not reload when this load already is the recovery", () => {
    const store = fakeStorage();
    store.setItem("qubit.chunk-reload-retry", "1");
    const target = fakeTarget();
    const reload = vi.fn();
    installChunkReloadRecovery(target, reload, store);
    // The marker is consumed: a later error on THIS load surfaces.
    expect(store.getItem("qubit.chunk-reload-retry")).toBeNull();
    target.fire("vite:preloadError");
    expect(reload).not.toHaveBeenCalled();
  });

  it("recovers again on a later page load (flag consumed)", () => {
    const store = fakeStorage();
    const first = fakeTarget();
    const reloadFirst = vi.fn();
    installChunkReloadRecovery(first, reloadFirst, store);
    first.fire("vite:preloadError");
    expect(reloadFirst).toHaveBeenCalledTimes(1);
    // Simulate the reloaded page: same store, fresh install.
    const second = fakeTarget();
    const reloadSecond = vi.fn();
    installChunkReloadRecovery(second, reloadSecond, store);
    // Post-recovery load: no auto-reload (flag consumed at install).
    second.fire("vite:preloadError");
    expect(reloadSecond).not.toHaveBeenCalled();
  });
});
