/* Weather normalizer, cache, fetch and effect hysteresis.
 *
 * Contract (plan §7, §18.2):
 *  - normalized states: clear-day, clear-night, partly-cloudy, cloudy, rain,
 *    storm, snow, fog, fallback
 *  - NEVER blocks the first render; a caller starts with `fallback`/no effect
 *  - 1–2 s timeout, 10–30 min cache, neutral fallback on any failure
 *  - exactly ONE effect at a time, with hysteresis so rain↔storm cannot flicker
 *  - no network call on an animation frame
 *
 * Network is opt-in: a location must be configured (or a snapshot injected).
 */

import type {
  NormalizedWeather, OfficeLoadingConfig, WeatherCondition, WeatherEffectId,
  WeatherLocation, WeatherSource,
} from "./types";

export const WEATHER_CONDITIONS: readonly WeatherCondition[] = [
  "clear-day", "clear-night", "partly-cloudy", "cloudy",
  "rain", "storm", "snow", "fog", "fallback",
];

const CONDITION_SET = new Set<string>(WEATHER_CONDITIONS);

export const EFFECT_FOR_CONDITION: Readonly<Record<WeatherCondition, WeatherEffectId>> = Object.freeze({
  "clear-day": "god-rays",
  // Clear night has no beam/cloud/rain; the placeholder sky is the effect.
  "clear-night": "none",
  "partly-cloudy": "clouds",
  "cloudy": "clouds",
  rain: "rain",
  // Storm already contains rain + lightning; never layer rain on top.
  storm: "storm",
  snow: "snow",
  fog: "fog",
  fallback: "none",
});

export const EFFECTS: readonly WeatherEffectId[] = [
  "none", "god-rays", "clouds", "rain", "storm", "snow", "fog",
];

const EFFECT_SET = new Set<string>(EFFECTS);

function isCondition(value: unknown): value is WeatherCondition {
  return typeof value === "string" && CONDITION_SET.has(value);
}

/** WMO weather interpretation code → normalized condition. */
export function conditionFromCode(code: unknown, isDay: unknown = true): WeatherCondition {
  const n = typeof code === "number" ? code : Number(code);
  if (!Number.isFinite(n)) return "fallback";
  const day = isDay !== false && isDay !== 0 && isDay !== "0";
  if (n === 0) return day ? "clear-day" : "clear-night";
  if (n === 1 || n === 2) return "partly-cloudy";
  if (n === 3) return "cloudy";
  if (n === 45 || n === 48) return "fog";
  // Drizzle / rain / freezing rain / rain showers.
  if ((n >= 50 && n <= 67) || (n >= 80 && n <= 82)) return "rain";
  // Snow / snow grains / snow showers.
  if ((n >= 70 && n <= 77) || n === 85 || n === 86) return "snow";
  // Thunderstorm, with or without hail.
  if (n === 95 || n === 96 || n === 99) return "storm";
  return "fallback";
}

export function effectForCondition(condition: WeatherCondition): WeatherEffectId {
  return EFFECT_FOR_CONDITION[condition] ?? "none";
}

export function fallbackWeather(reason: string, at = Date.now()): NormalizedWeather {
  return {
    condition: "fallback",
    temperatureC: null,
    windKph: null,
    precipitationMm: null,
    isDay: null,
    source: "fallback",
    reason,
    fetchedAt: at,
  };
}

function numOrNull(value: unknown): number | null {
  const n = typeof value === "number" ? value : Number(value);
  return Number.isFinite(n) ? n : null;
}

function boolOrNull(value: unknown): boolean | null {
  if (typeof value === "boolean") return value;
  if (value === 1 || value === "1") return true;
  if (value === 0 || value === "0") return false;
  return null;
}

function sourceOr(value: unknown, fallback: WeatherSource): WeatherSource {
  return value === "injected" || value === "open-meteo" || value === "cache" || value === "fallback"
    ? value : fallback;
}

/**
 * Normalize any accepted snapshot shape: a condition string, a
 * `NormalizedWeather`-like object, or an Open-Meteo response. Returns `null`
 * for something unrecognizable so the caller can keep its neutral fallback.
 */
export function normalizeSnapshot(raw: unknown, at = Date.now()): NormalizedWeather | null {
  if (typeof raw === "string") {
    return isCondition(raw) ? {
      condition: raw,
      temperatureC: null, windKph: null, precipitationMm: null, isDay: null,
      source: "injected", reason: null, fetchedAt: at,
    } : null;
  }
  if (!raw || typeof raw !== "object") return null;
  const obj = raw as Record<string, unknown>;
  if (isCondition(obj.condition)) {
    const condition = obj.condition;
    return {
      condition,
      temperatureC: numOrNull(obj.temperatureC),
      windKph: numOrNull(obj.windKph),
      precipitationMm: numOrNull(obj.precipitationMm),
      isDay: boolOrNull(obj.isDay),
      source: sourceOr(obj.source, "injected"),
      reason: typeof obj.reason === "string" ? obj.reason : null,
      fetchedAt: Number.isFinite(Number(obj.fetchedAt)) ? Number(obj.fetchedAt) : at,
    };
  }
  return normalizeOpenMeteo(obj, at);
}

/** Normalize an Open-Meteo `/v1/forecast?current=…` response. */
export function normalizeOpenMeteo(json: unknown, at = Date.now()): NormalizedWeather | null {
  if (!json || typeof json !== "object") return null;
  const root = json as Record<string, unknown>;
  const current = (root.current ?? root.current_weather) as Record<string, unknown> | undefined;
  if (!current || typeof current !== "object") return null;
  const code = current.weather_code ?? current.weathercode;
  if (code === undefined || code === null) return null;
  const isDay = boolOrNull(current.is_day ?? current.isday);
  return {
    condition: conditionFromCode(code, isDay ?? true),
    temperatureC: numOrNull(current.temperature_2m ?? current.temperature),
    windKph: numOrNull(current.wind_speed_10m ?? current.windspeed_10m ?? current.windspeed),
    precipitationMm: numOrNull(current.precipitation),
    isDay,
    source: "open-meteo",
    reason: null,
    fetchedAt: at,
  };
}

// ---------------------------------------------------------------------------
// Cache (sessionStorage; per-tab, safe under private/blocked storage)
// ---------------------------------------------------------------------------

const CACHE_PREFIX = "qubit.office.loading.weather.v1:";

export function weatherCacheKey(location: WeatherLocation): string {
  return `${CACHE_PREFIX}${location.latitude.toFixed(3)},${location.longitude.toFixed(3)}`;
}

function defaultStorage(): Storage | null {
  try {
    return typeof sessionStorage === "undefined" ? null : sessionStorage;
  } catch {
    return null;
  }
}

export function readWeatherCache(
  location: WeatherLocation,
  ttlMs: number,
  at = Date.now(),
  storage?: Storage | null,
): NormalizedWeather | null {
  const store = storage === undefined ? defaultStorage() : storage;
  if (!store) return null;
  try {
    const text = store.getItem(weatherCacheKey(location));
    if (!text) return null;
    const parsed = JSON.parse(text) as { savedAt?: unknown; weather?: unknown };
    const savedAt = Number(parsed.savedAt);
    if (!Number.isFinite(savedAt) || at - savedAt > ttlMs || at < savedAt) return null;
    const weather = normalizeSnapshot(parsed.weather, savedAt);
    if (!weather || weather.condition === "fallback") return null;
    return { ...weather, source: "cache" };
  } catch {
    return null;
  }
}

export function writeWeatherCache(
  location: WeatherLocation,
  weather: NormalizedWeather,
  at = Date.now(),
  storage?: Storage | null,
): void {
  const store = storage === undefined ? defaultStorage() : storage;
  if (!store) return;
  try {
    store.setItem(weatherCacheKey(location), JSON.stringify({ savedAt: at, weather }));
  } catch {
    // Quota/private mode: the cache is an optimisation, never a requirement.
  }
}

// ---------------------------------------------------------------------------
// Fetch (timeout, never throws)
// ---------------------------------------------------------------------------

export interface LoadWeatherOptions {
  fetchImpl?: typeof fetch | null;
  storage?: Storage | null;
  now?: () => number;
  signal?: AbortSignal | null;
}

export function weatherUrl(config: OfficeLoadingConfig): string {
  const location = config.location;
  const url = new URL(config.weatherEndpoint);
  if (location) {
    url.searchParams.set("latitude", String(location.latitude));
    url.searchParams.set("longitude", String(location.longitude));
  }
  url.searchParams.set(
    "current",
    "weather_code,is_day,temperature_2m,wind_speed_10m,precipitation",
  );
  url.searchParams.set("timezone", "auto");
  return url.toString();
}

/**
 * Resolve the weather without ever blocking or throwing.
 *
 * Order: injected snapshot → valid cache → network (if a location is set) →
 * neutral fallback with a typed reason. The loading screen renders before this
 * settles; it calls this from an effect and paints the fallback immediately.
 */
export async function loadWeather(
  config: OfficeLoadingConfig,
  opts: LoadWeatherOptions = {},
): Promise<NormalizedWeather> {
  const now = opts.now ?? Date.now;
  if (config.weather) return config.weather;
  const location = config.location;
  if (!location) return fallbackWeather("no-location", now());

  const cached = readWeatherCache(
    location, config.weatherCacheTtlMs, now(), opts.storage);
  if (cached) return cached;

  if (typeof navigator !== "undefined" && navigator.onLine === false) {
    return fallbackWeather("offline", now());
  }
  const fetchImpl = opts.fetchImpl === undefined
    ? (typeof fetch === "function" ? fetch : null)
    : opts.fetchImpl;
  if (!fetchImpl) return fallbackWeather("unknown", now());

  const controller = new AbortController();
  const onExternalAbort = () => controller.abort();
  if (opts.signal) {
    if (opts.signal.aborted) return fallbackWeather("aborted", now());
    opts.signal.addEventListener("abort", onExternalAbort, { once: true });
  }
  const timer = setTimeout(() => controller.abort(), config.weatherTimeoutMs);

  try {
    const response = await fetchImpl(weatherUrl(config), {
      method: "GET",
      signal: controller.signal,
      credentials: "omit",
      cache: "no-store",
      headers: { Accept: "application/json" },
    });
    if (!response.ok) return fallbackWeather("http", now());
    const json = await response.json();
    const weather = normalizeOpenMeteo(json, now());
    if (!weather) return fallbackWeather("parse", now());
    writeWeatherCache(location, weather, now(), opts.storage);
    return weather;
  } catch {
    // An abort is either the plan's timeout or the caller unmounting.
    if (opts.signal?.aborted) return fallbackWeather("aborted", now());
    return fallbackWeather(controller.signal.aborted ? "timeout" : "unknown", now());
  } finally {
    clearTimeout(timer);
    opts.signal?.removeEventListener("abort", onExternalAbort);
  }
}

// ---------------------------------------------------------------------------
// Hysteresis: one effect at a time, no rain/storm flicker
// ---------------------------------------------------------------------------

export interface EffectSelector {
  current(): WeatherEffectId;
  select(weather: NormalizedWeather, at?: number): WeatherEffectId;
  selectEffect(candidate: WeatherEffectId, at?: number, known?: boolean): WeatherEffectId;
  reset(): void;
}

export interface EffectSelectorOptions {
  hysteresisMs?: number;
}

/**
 * A candidate must persist `hysteresisMs` before replacing the current effect.
 * The rain↔storm pair uses twice that hold, because those are the two states
 * weather APIs oscillate between. A storm is a single effect: the player never
 * draws rain under it.
 */
export function createEffectSelector(opts: EffectSelectorOptions = {}): EffectSelector {
  const baseHold = Math.max(0, Number(opts.hysteresisMs) || 0);
  let current: WeatherEffectId = "none";
  let initialized = false;
  let hasKnownWeather = false;
  let pending: WeatherEffectId | null = null;
  let pendingSince = 0;

  const holdFor = (from: WeatherEffectId, to: WeatherEffectId): number => {
    const pair = (from === "rain" && to === "storm") || (from === "storm" && to === "rain");
    return pair ? baseHold * 2 : baseHold;
  };

  const selectEffect = (candidate: WeatherEffectId, at = Date.now(), known = true): WeatherEffectId => {
    if (!EFFECT_SET.has(candidate)) return current;
    if (!initialized) {
      initialized = true;
      current = candidate;
      if (known) hasKnownWeather = true;
      return current;
    }
    if (candidate === current) {
      pending = null;
      if (known) hasKnownWeather = true;
      return current;
    }
    // The very first real observation wins immediately: the screen should
    // not sit neutral for a minute because the initial state was fallback.
    if (!hasKnownWeather && known) {
      current = candidate;
      pending = null;
      hasKnownWeather = true;
      return current;
    }
    if (pending !== candidate) {
      pending = candidate;
      pendingSince = at;
    }
    if (at - pendingSince >= holdFor(current, candidate)) {
      current = candidate;
      pending = null;
      if (known) hasKnownWeather = true;
    }
    return current;
  };

  return {
    current: () => current,
    reset() {
      current = "none";
      initialized = false;
      hasKnownWeather = false;
      pending = null;
      pendingSince = 0;
    },
    select(weather, at = Date.now()) {
      return selectEffect(
        effectForCondition(weather.condition),
        at,
        weather.condition !== "fallback",
      );
    },
    selectEffect,
  };
}
