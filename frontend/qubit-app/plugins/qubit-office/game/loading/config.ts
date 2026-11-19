/* Owner-facing loading configuration.
 *
 * The plan (§7, §18.2) names `office.loading.background`. There is no backend
 * loader in this lane, so the value is read from either of two host globals (a
 * future server injection) or passed as a prop:
 *
 *     window.__QUBIT_OFFICE_LOADING__ = { background, location, weather, ... }
 *     window.__QUBIT_OFFICE_CONFIG__  = { office: { loading: { ... } } }
 *
 * Both are OPTIONAL. Every key has a default, the background falls back to a
 * CSS/SVG placeholder, and weather is opt-in (no location = no request).
 */

import type {
  NormalizedWeather, OfficeLoadingConfig, WeatherLocation,
} from "./types";
import { normalizeSnapshot } from "./weather";

export const DEFAULT_TIPS: readonly string[] = [
  "Tip: press the Panels icons to open the HUD or the agents list.",
  "Tip: the office animates from real office events, not a scripted loop.",
  "Tip: every room is a real place; click a floor to travel.",
  "Tip: unsupported write actions answer with a typed refusal, never a lie.",
  "Tip: the same office backend powers the dashboard and the building.",
];

export const DEFAULT_LOADING_CONFIG: Readonly<OfficeLoadingConfig> = Object.freeze({
  background: "",
  backgroundPosition: "center",
  backgroundDim: 0.55,
  brand: "Qubit Office",
  tagline: "Nobody knows what it will become.",
  tips: [...DEFAULT_TIPS],
  weather: null,
  location: null,
  weatherEndpoint: "https://api.open-meteo.com/v1/forecast",
  weatherTimeoutMs: 1500,
  weatherCacheTtlMs: 15 * 60 * 1000,
  hysteresisMs: 60 * 1000,
  reducedMotion: null,
  lowPower: null,
  effects: false, // Decorative pixel-weather effects are opt-in; Sakuga progress remains active.
});

/** A partial owner/host overrides object; `weather` may be an unknown raw snapshot. */
export type OfficeLoadingConfigInput = Partial<OfficeLoadingConfig> & {
  weather?: unknown;
};

function clampNumber(value: unknown, fallback: number, min: number, max: number): number {
  const n = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(n)) return fallback;
  return Math.min(max, Math.max(min, n));
}

function readWindow(win?: Window | null): Window | null {
  if (win) return win;
  return typeof window === "undefined" ? null : window;
}

function record(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  return value as Record<string, unknown>;
}

/** The two documented injection shapes, merged shallowly (the first wins). */
export function readInjectedLoadingConfig(win?: Window | null): OfficeLoadingConfigInput | null {
  const w = readWindow(win) as (Window & {
    __QUBIT_OFFICE_LOADING__?: unknown;
    __QUBIT_OFFICE_CONFIG__?: { office?: { loading?: unknown } };
  }) | null;
  if (!w) return null;
  const direct = record(w.__QUBIT_OFFICE_LOADING__);
  const nested = record(record(w.__QUBIT_OFFICE_CONFIG__?.office)?.loading);
  if (!direct && !nested) return null;
  return { ...(nested ?? {}), ...(direct ?? {}) } as OfficeLoadingConfigInput;
}

/**
 * A background value that is safe to put in CSS `url(...)`.
 *
 * Accepts absolute HTTP(S), protocol-relative, root/relative paths and
 * `data:image/…`; rejects schemes that can execute (`javascript:`,
 * `vbscript:`, `file:`), control/whitespace characters, quotes, backslashes
 * and parentheses (which would break out of the CSS value). Empty string means
 * "use the placeholder".
 */
export function safeBackground(value: unknown): string {
  if (typeof value !== "string") return "";
  const trimmed = value.trim();
  if (!trimmed || trimmed.length > 2048) return "";
  if (/[\u0000-\u0020"'\\()<>]/.test(trimmed)) return "";
  const lower = trimmed.toLowerCase();
  if (lower.startsWith("javascript:") || lower.startsWith("vbscript:") || lower.startsWith("file:")) {
    return "";
  }
  if (/^(https?:)?\/\//.test(trimmed) || lower.startsWith("data:image/") || /^(\/|\.\/|\.\.\/)/.test(trimmed)) {
    return trimmed;
  }
  // A bare relative path (no scheme colon), e.g. media/office-loading.webp
  if (!trimmed.includes(":") && /^[A-Za-z0-9._~%+,-]+(?:\/[A-Za-z0-9._~%+,-]+)*(?:[?#][A-Za-z0-9._~%+=&,-]*)?$/.test(trimmed)) {
    return trimmed;
  }
  return "";
}

function safeLocation(value: unknown): WeatherLocation | null {
  const r = record(value);
  if (!r) return null;
  const latitude = Number(r.latitude);
  const longitude = Number(r.longitude);
  if (!Number.isFinite(latitude) || latitude < -90 || latitude > 90) return null;
  if (!Number.isFinite(longitude) || longitude < -180 || longitude > 180) return null;
  const name = typeof r.name === "string" ? r.name.slice(0, 120) : undefined;
  return { latitude, longitude, ...(name ? { name } : {}) };
}

function safeTips(value: unknown, fallback: string[]): string[] {
  if (!Array.isArray(value)) return fallback;
  const tips = value
    .filter((tip): tip is string => typeof tip === "string")
    .map((tip) => tip.trim())
    .filter(Boolean)
    .slice(0, 8);
  return tips.length ? tips : fallback;
}

function boolOrNull(value: unknown): boolean | null {
  return typeof value === "boolean" ? value : null;
}

/**
 * Resolve defaults ← host globals ← explicit props. Pure; no DOM read unless
 * `win` is omitted (then the current window is inspected).
 */
export function resolveLoadingConfig(
  input?: OfficeLoadingConfigInput | null,
  win?: Window | null,
): OfficeLoadingConfig {
  const base = DEFAULT_LOADING_CONFIG;
  const merged: Record<string, unknown> = {
    ...(readInjectedLoadingConfig(win) ?? {}),
    ...(input ?? {}),
  };
  const background = safeBackground(merged.background);
  const dim = merged.backgroundDim;
  return {
    background,
    backgroundPosition: typeof merged.backgroundPosition === "string"
      ? merged.backgroundPosition.slice(0, 80) : base.backgroundPosition,
    backgroundDim: clampNumber(dim, base.backgroundDim, 0, 0.95),
    brand: typeof merged.brand === "string" && merged.brand.trim()
      ? merged.brand.trim().slice(0, 60) : base.brand,
    tagline: typeof merged.tagline === "string"
      ? merged.tagline.trim().slice(0, 160) : base.tagline,
    tips: safeTips(merged.tips, base.tips),
    weather: normalizeSnapshot(merged.weather) ?? base.weather,
    location: safeLocation(merged.location),
    weatherEndpoint: typeof merged.weatherEndpoint === "string" && /^https?:\/\//i.test(merged.weatherEndpoint)
      ? merged.weatherEndpoint.slice(0, 300) : base.weatherEndpoint,
    // 1–2 s per the plan; do not let a bad host value hang the fetch.
    weatherTimeoutMs: clampNumber(merged.weatherTimeoutMs, base.weatherTimeoutMs, 500, 2000),
    weatherCacheTtlMs: clampNumber(merged.weatherCacheTtlMs, base.weatherCacheTtlMs, 10 * 60 * 1000, 30 * 60 * 1000),
    hysteresisMs: clampNumber(merged.hysteresisMs, base.hysteresisMs, 0, 5 * 60 * 1000),
    reducedMotion: boolOrNull(merged.reducedMotion),
    lowPower: boolOrNull(merged.lowPower),
    effects: typeof merged.effects === "boolean" ? merged.effects : base.effects,
  };
}

/** CSS for the owner's background art, or `null` for the placeholder. */
export function backgroundImageStyle(config: OfficeLoadingConfig): { backgroundImage: string } | null {
  if (!config.background) return null;
  return { backgroundImage: `url("${config.background}")` };
}
