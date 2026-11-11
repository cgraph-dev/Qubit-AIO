import { afterEach, describe, expect, it, vi } from "vitest";
import {
  conditionFromCode, createEffectSelector, effectForCondition,
  fallbackWeather, loadWeather, normalizeOpenMeteo, normalizeSnapshot,
  readWeatherCache, weatherCacheKey, writeWeatherCache,
} from "./weather";
import { resolveLoadingConfig } from "./config";
import type { NormalizedWeather } from "./types";

const weather = (condition: NormalizedWeather["condition"]): NormalizedWeather => ({
  condition, temperatureC: null, windKph: null, precipitationMm: null,
  isDay: null, source: "injected", reason: null, fetchedAt: 0,
});

class MemoryStorage {
  private data = new Map<string, string>();
  get length() { return this.data.size; }
  clear() { this.data.clear(); }
  getItem(key: string) { return this.data.has(key) ? this.data.get(key)! : null; }
  key(index: number) { return [...this.data.keys()][index] ?? null; }
  removeItem(key: string) { this.data.delete(key); }
  setItem(key: string, value: string) { this.data.set(key, String(value)); }
}

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("WMO normalization", () => {
  it("maps every named range to one normalized state", () => {
    expect(conditionFromCode(0, true)).toBe("clear-day");
    expect(conditionFromCode(0, false)).toBe("clear-night");
    expect(conditionFromCode(1, true)).toBe("partly-cloudy");
    expect(conditionFromCode(2, true)).toBe("partly-cloudy");
    expect(conditionFromCode(3, true)).toBe("cloudy");
    expect(conditionFromCode(45, true)).toBe("fog");
    expect(conditionFromCode(48, true)).toBe("fog");
    expect(conditionFromCode(51, true)).toBe("rain");
    expect(conditionFromCode(53, true)).toBe("rain");
    expect(conditionFromCode(57, true)).toBe("rain");
    expect(conditionFromCode(61, true)).toBe("rain");
    expect(conditionFromCode(67, true)).toBe("rain");
    expect(conditionFromCode(71, true)).toBe("snow");
    expect(conditionFromCode(77, true)).toBe("snow");
    expect(conditionFromCode(80, true)).toBe("rain");
    expect(conditionFromCode(82, true)).toBe("rain");
    expect(conditionFromCode(85, true)).toBe("snow");
    expect(conditionFromCode(86, true)).toBe("snow");
    expect(conditionFromCode(95, true)).toBe("storm");
    expect(conditionFromCode(96, true)).toBe("storm");
    expect(conditionFromCode(99, true)).toBe("storm");
    expect(conditionFromCode(1000, true)).toBe("fallback");
    expect(conditionFromCode(undefined, true)).toBe("fallback");
  });

  it("never produces rain and storm at the same time", () => {
    expect(effectForCondition("rain")).toBe("rain");
    expect(effectForCondition("storm")).toBe("storm");
    expect(effectForCondition("clear-night")).toBe("none");
    expect(effectForCondition("fallback")).toBe("none");
    const ids = new Set(["none", "god-rays", "clouds", "rain", "storm", "snow", "fog"]);
    for (const condition of ["clear-day", "clear-night", "partly-cloudy", "cloudy", "rain", "storm", "snow", "fog", "fallback"] as const) {
      expect(ids.has(effectForCondition(condition))).toBe(true);
    }
  });
});

describe("snapshots", () => {
  it("normalizes an Open-Meteo current block", () => {
    const got = normalizeOpenMeteo({
      current: {
        weather_code: 95, is_day: 1, temperature_2m: 11.4,
        wind_speed_10m: 22, precipitation: 1.2,
      },
    }, 123);
    expect(got).toMatchObject({
      condition: "storm", temperatureC: 11.4, windKph: 22,
      precipitationMm: 1.2, isDay: true, source: "open-meteo", fetchedAt: 123,
    });
  });

  it("normalizes the legacy current_weather block", () => {
    const got = normalizeOpenMeteo({
      current_weather: { weathercode: 71, temperature: -2, windspeed: 4 },
    }, 5);
    expect(got?.condition).toBe("snow");
    expect(got?.temperatureC).toBe(-2);
  });

  it("accepts a condition string and a normalized-like object", () => {
    expect(normalizeSnapshot("rain")?.condition).toBe("rain");
    expect(normalizeSnapshot({ condition: "fog", temperatureC: 4 })?.condition).toBe("fog");
    expect(normalizeSnapshot({ nonsense: true })).toBeNull();
  });
});

describe("effect hysteresis", () => {
  it("adopts the first observation immediately, then holds", () => {
    const select = createEffectSelector({ hysteresisMs: 1000 });
    expect(select.select(weather("cloudy"), 0)).toBe("clouds");
    expect(select.select(weather("snow"), 500)).toBe("clouds");
    expect(select.select(weather("snow"), 1499)).toBe("clouds");
    expect(select.select(weather("snow"), 1500)).toBe("snow");
  });

  it("doubles the hold for the rain/storm pair", () => {
    const select = createEffectSelector({ hysteresisMs: 1000 });
    expect(select.select(weather("rain"), 0)).toBe("rain");
    expect(select.select(weather("storm"), 1000)).toBe("rain");
    expect(select.select(weather("storm"), 1999)).toBe("rain");
    expect(select.select(weather("storm"), 3000)).toBe("storm");
  });

  it("resets the pending candidate when a third state arrives", () => {
    const select = createEffectSelector({ hysteresisMs: 1000 });
    select.select(weather("cloudy"), 0);
    expect(select.select(weather("snow"), 900)).toBe("clouds");
    expect(select.select(weather("fog"), 1200)).toBe("clouds");
    // The fog candidate only started at 1200, not 900.
    expect(select.select(weather("fog"), 2100)).toBe("clouds");
    expect(select.select(weather("fog"), 2200)).toBe("fog");
  });

  it("keeps the last effect while weather is unavailable", () => {
    const select = createEffectSelector({ hysteresisMs: 1000 });
    select.select(weather("rain"), 0);
    expect(select.select(fallbackWeather("offline", 100), 100)).toBe("rain");
    expect(select.select(fallbackWeather("offline", 1099), 1099)).toBe("rain");
    expect(select.select(fallbackWeather("offline", 1100), 1100)).toBe("none");
  });

  it("adopts the first REAL weather after a fallback immediately", () => {
    const select = createEffectSelector({ hysteresisMs: 60000 });
    expect(select.select(fallbackWeather("no-location", 0), 0)).toBe("none");
    expect(select.select(weather("clear-day"), 1)).toBe("god-rays");
  });
});

describe("loadWeather", () => {
  const configured = resolveLoadingConfig({
    location: { latitude: 44.4, longitude: 26.1 },
    weatherTimeoutMs: 1500,
  });

  it("returns neutral immediately when no location is configured", async () => {
    const fetchImpl = vi.fn();
    const got = await loadWeather(resolveLoadingConfig({}), { fetchImpl: fetchImpl as never });
    expect(got.condition).toBe("fallback");
    expect(got.reason).toBe("no-location");
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it("uses an injected snapshot without any network", async () => {
    const fetchImpl = vi.fn();
    const config = resolveLoadingConfig({ weather: { condition: "snow", temperatureC: -1 } });
    const got = await loadWeather(config, { fetchImpl: fetchImpl as never });
    expect(got.condition).toBe("snow");
    expect(got.source).toBe("injected");
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it("times out at 1–2 s and falls back neutrally", async () => {
    vi.useFakeTimers();
    const fetchImpl = vi.fn((_url: string, init?: RequestInit) => new Promise((_resolve, reject) => {
      init?.signal?.addEventListener("abort", () => {
        reject(new DOMException("aborted", "AbortError"));
      });
    }));
    const pending = loadWeather(configured, { fetchImpl: fetchImpl as never, now: () => 0 });
    await vi.advanceTimersByTimeAsync(1500);
    const got = await pending;
    expect(got.condition).toBe("fallback");
    expect(got.reason).toBe("timeout");
  });

  it("reports an HTTP failure as a neutral fallback", async () => {
    const fetchImpl = vi.fn(async () => ({ ok: false, json: async () => ({}) }));
    const got = await loadWeather(configured, { fetchImpl: fetchImpl as never });
    expect(got.condition).toBe("fallback");
    expect(got.reason).toBe("http");
  });

  it("returns a parsed snapshot and warms the cache", async () => {
    const storage = new MemoryStorage() as unknown as Storage;
    const fetchImpl = vi.fn(async () => ({
      ok: true,
      json: async () => ({ current: { weather_code: 3, temperature_2m: 7 } }),
    }));
    const got = await loadWeather(configured, {
      fetchImpl: fetchImpl as never, storage, now: () => 1000,
    });
    expect(got.condition).toBe("cloudy");
    expect(got.source).toBe("open-meteo");
    const cached = readWeatherCache(configured.location!, configured.weatherCacheTtlMs, 2000, storage);
    expect(cached?.condition).toBe("cloudy");
    expect(cached?.source).toBe("cache");
    // A warm cache means the next open makes no request.
    const fetchAgain = vi.fn();
    const second = await loadWeather(configured, {
      fetchImpl: fetchAgain as never, storage, now: () => 2000,
    });
    expect(second.source).toBe("cache");
    expect(fetchAgain).not.toHaveBeenCalled();
  });

  it("expires the cache after its TTL", async () => {
    const storage = new MemoryStorage() as unknown as Storage;
    const loc = configured.location!;
    writeWeatherCache(loc, weather("rain"), 1000, storage);
    expect(readWeatherCache(loc, 10_000, 5000, storage)?.condition).toBe("rain");
    expect(readWeatherCache(loc, 10_000, 12_000, storage)).toBeNull();
  });

  it("does not call the network when the browser is offline", async () => {
    vi.stubGlobal("navigator", { onLine: false });
    const fetchImpl = vi.fn();
    const got = await loadWeather(configured, { fetchImpl: fetchImpl as never });
    expect(got.condition).toBe("fallback");
    expect(got.reason).toBe("offline");
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it("reports an external abort (unmount) separately from a timeout", async () => {
    const controller = new AbortController();
    const fetchImpl = vi.fn((_url: string, init?: RequestInit) => new Promise((_resolve, reject) => {
      init?.signal?.addEventListener("abort", () => {
        reject(new DOMException("aborted", "AbortError"));
      });
    }));
    const pending = loadWeather(configured, {
      fetchImpl: fetchImpl as never, signal: controller.signal, now: () => 0,
    });
    controller.abort();
    const got = await pending;
    expect(got.reason).toBe("aborted");
  });

  it("exposes a documented cache key shape", () => {
    expect(weatherCacheKey({ latitude: 1.23456, longitude: -2.34567 }))
      .toBe("qubit.office.loading.weather.v1:1.235,-2.346");
  });
});
