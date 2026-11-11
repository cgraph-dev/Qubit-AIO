/* Shared vocabulary for the loading screen, its weather source and its
 * pixelfx effect mapping. Kept separate from the React layer so the pure
 * normalizer/selector/perf tests import no DOM and no hooks.
 */

/** The normalized states the owner-facing plan names (§7, §18.2). */
export type WeatherCondition =
  | "clear-day"
  | "clear-night"
  | "partly-cloudy"
  | "cloudy"
  | "rain"
  | "storm"
  | "snow"
  | "fog"
  | "fallback";

/** Exactly one effect at a time; `none` is the neutral/absent case. */
export type WeatherEffectId =
  | "none"
  | "god-rays"
  | "clouds"
  | "rain"
  | "storm"
  | "snow"
  | "fog";

export type WeatherSource = "injected" | "open-meteo" | "cache" | "fallback";

export interface NormalizedWeather {
  condition: WeatherCondition;
  /** `null` when the snapshot carried no reading (fallback/injected). */
  temperatureC: number | null;
  windKph: number | null;
  precipitationMm: number | null;
  isDay: boolean | null;
  source: WeatherSource;
  /** A typed reason on fallback: no-location | offline | timeout | http | parse | aborted | unknown. */
  reason: string | null;
  fetchedAt: number;
}

/** A location the owner configures (or a future server snapshot supplies). */
export interface WeatherLocation {
  latitude: number;
  longitude: number;
  name?: string;
}

/** The one object the owner can supply through `office.loading` /
 *  `window.__QUBIT_OFFICE_LOADING__`. Everything has a safe default. */
export interface OfficeLoadingConfig {
  /** A URL/path to the owner's final background art. Empty = CSS/SVG placeholder. */
  background: string;
  backgroundPosition: string;
  /** 0..1; the placeholder and owner art both sit under a readability veil. */
  backgroundDim: number;
  brand: string;
  tagline: string;
  tips: string[];
  /** A server-normalized snapshot, if one is injected. Skips the network. */
  weather: NormalizedWeather | null;
  /** When set, the client may ask Open-Meteo directly (opt-in, never automatic). */
  location: WeatherLocation | null;
  weatherEndpoint: string;
  /** 1–2 s per the plan; clamped in resolveLoadingConfig. */
  weatherTimeoutMs: number;
  /** 10–30 min per the plan; clamped in resolveLoadingConfig. */
  weatherCacheTtlMs: number;
  /** A candidate effect must persist this long before it replaces the current one. */
  hysteresisMs: number;
  /** Owner override; otherwise the OS preference is honoured. */
  reducedMotion: boolean | null;
  /** Owner override; otherwise hardware/connection hints decide. */
  lowPower: boolean | null;
  /** Master switch — `false` renders the background/overlay with no weather. */
  effects: boolean;
}
