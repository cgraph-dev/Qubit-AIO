import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import {
  getCachedManifests,
  cacheManifests,
  canSeedLoadedFromCache,
  isLegacyBrandedManifest,
  sanitizeManifests,
  MANIFEST_CACHE_KEY,
  LEGACY_MANIFEST_CACHE_KEYS,
} from "./usePlugins";
import type { PluginManifest } from "./types";

function makeStorage(): Storage {
  const store = new Map<string, string>();
  return {
    getItem(key: string) {
      return store.get(key) ?? null;
    },
    setItem(key: string, value: string) {
      store.set(key, value);
    },
    removeItem(key: string) {
      store.delete(key);
    },
    clear() {
      store.clear();
    },
    get length() {
      return store.size;
    },
    key(index: number) {
      return Array.from(store.keys())[index] ?? null;
    },
  } as Storage;
}

const exampleManifest: PluginManifest = {
  name: "test",
  label: "Test",
  description: "A test plugin",
  icon: "Puzzle",
  version: "1.0.0",
  tab: { path: "/test" },
  entry: "index.js",
  has_api: false,
  source: "local",
};

describe("plugin manifest cache helpers", () => {
  let storage: Storage;

  beforeEach(() => {
    storage = makeStorage();
    vi.stubGlobal("sessionStorage", storage);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("getCachedManifests returns null when nothing is cached", () => {
    expect(getCachedManifests()).toBeNull();
  });

  it("getCachedManifests returns null for invalid JSON", () => {
    storage.setItem(MANIFEST_CACHE_KEY, "not-json");
    expect(getCachedManifests()).toBeNull();
  });

  it("getCachedManifests returns null for non-array JSON", () => {
    storage.setItem(MANIFEST_CACHE_KEY, JSON.stringify({ foo: "bar" }));
    expect(getCachedManifests()).toBeNull();
  });

  it("getCachedManifests returns null for scalar JSON", () => {
    storage.setItem(MANIFEST_CACHE_KEY, JSON.stringify(42));
    expect(getCachedManifests()).toBeNull();
  });

  it("getCachedManifests returns a valid manifest array", () => {
    const list: PluginManifest[] = [exampleManifest];
    cacheManifests(list);
    expect(getCachedManifests()).toEqual(list);
  });

  it("cacheManifests overwrites a previous cache on refresh", () => {
    const first: PluginManifest[] = [exampleManifest];
    cacheManifests(first);
    expect(getCachedManifests()).toEqual(first);

    const second: PluginManifest[] = [
      { ...exampleManifest, name: "updated", label: "Updated" },
    ];
    cacheManifests(second);
    expect(getCachedManifests()).toEqual(second);
  });

  it("cacheManifests swallows storage errors", () => {
    const badStorage = makeStorage();
    badStorage.setItem = () => {
      throw new Error("QuotaExceededError");
    };
    vi.stubGlobal("sessionStorage", badStorage);
    expect(() => cacheManifests([exampleManifest])).not.toThrow();
  });

  it("drops a legacy-branded cached manifest (the 404 /plugins/hermes-achievements entry)", () => {
    const legacy: PluginManifest = {
      ...exampleManifest,
      name: "hermes-achievements",
      assetBase: "/plugins/hermes-achievements",
    };
    storage.setItem(MANIFEST_CACHE_KEY, JSON.stringify([legacy, exampleManifest]));
    const got = getCachedManifests();
    expect(got).toEqual([exampleManifest]);
    expect(got?.some((m) => m.name === "hermes-achievements")).toBe(false);
  });

  it("purges the pre-rename cache key on read so the stale entry cannot survive", () => {
    const legacy: PluginManifest = {
      ...exampleManifest,
      name: "hermes-achievements",
      assetBase: "/plugins/hermes-achievements",
    };
    storage.setItem(
      LEGACY_MANIFEST_CACHE_KEYS[0],
      JSON.stringify([legacy]),
    );
    getCachedManifests();
    expect(storage.getItem(LEGACY_MANIFEST_CACHE_KEYS[0])).toBeNull();
  });

  it("cacheManifests never persists a legacy-branded manifest", () => {
    const legacy: PluginManifest = {
      ...exampleManifest,
      name: "hermes-achievements",
      assetBase: "/plugins/hermes-achievements",
    };
    cacheManifests([legacy, exampleManifest]);
    const raw = storage.getItem(MANIFEST_CACHE_KEY);
    expect(raw).not.toContain("hermes");
    expect(getCachedManifests()).toEqual([exampleManifest]);
  });
});

describe("manifest sanitizer", () => {
  it("flags legacy names or asset roots, never a canonical qubit manifest", () => {
    expect(
      isLegacyBrandedManifest({
        ...exampleManifest,
        assetBase: "/plugins/hermes-achievements",
      }),
    ).toBe(true);
    expect(
      isLegacyBrandedManifest({ ...exampleManifest, name: "openclaw-x" }),
    ).toBe(true);
    expect(
      isLegacyBrandedManifest({
        ...exampleManifest,
        name: "qubit-achievements",
        assetBase: "/plugins/qubit-achievements",
      }),
    ).toBe(false);
  });

  it("keeps only canonical manifests and tolerates malformed entries", () => {
    const list = sanitizeManifests([
      exampleManifest,
      { ...exampleManifest, name: "hermes-achievements" },
      null as unknown as PluginManifest,
    ]);
    expect(list).toEqual([exampleManifest]);
  });
});

describe("canSeedLoadedFromCache (loading seed gate)", () => {
  it("returns false when there is no cache (first visit keeps loading=true)", () => {
    expect(canSeedLoadedFromCache(null)).toBe(false);
  });

  it("returns true for an empty cached list", () => {
    expect(canSeedLoadedFromCache([])).toBe(true);
  });

  it("returns true when no cached manifest overrides /chat", () => {
    const list: PluginManifest[] = [
      exampleManifest,
      {
        ...exampleManifest,
        name: "other",
        tab: { path: "/other", override: "/skills" },
      },
    ];
    expect(canSeedLoadedFromCache(list)).toBe(true);
  });

  it("returns false when a cached manifest overrides /chat — loading must stay true so App.tsx's pluginsLoading gate keeps the persistent chat host unmounted", () => {
    const list: PluginManifest[] = [
      exampleManifest,
      {
        ...exampleManifest,
        name: "chat-replacer",
        tab: { path: "/chat-alt", override: "/chat" },
      },
    ];
    expect(canSeedLoadedFromCache(list)).toBe(false);
  });

  it("tolerates malformed cached entries missing a tab object", () => {
    const malformed = [
      { ...exampleManifest, tab: undefined },
    ] as unknown as PluginManifest[];
    expect(canSeedLoadedFromCache(malformed)).toBe(true);
  });
});
