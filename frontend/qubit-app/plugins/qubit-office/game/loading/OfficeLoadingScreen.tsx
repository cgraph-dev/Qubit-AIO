/* Qubit Office loading screen.
 *
 * React comes from the HOST, exactly like the rest of the plugin: this module
 * has no `import … from "react"` and uses `R.createElement`. A second React
 * copy would split the hook runtime.
 *
 * Integration contract: `OfficeAppShell` mounts ONE of these over the iframe
 * and passes `ready` once the frame's load event fires. The screen fades out
 * itself and unmounts; every animation frame, timer, listener and fetch is
 * torn down on the way out. It imports no API, business or world module.
 */

import type { OfficeGameShellProps } from "../OfficeGameShell";
import {
  backgroundImageStyle, resolveLoadingConfig,
  type OfficeLoadingConfigInput,
} from "./config";
import { EFFECT_LABELS, FRAME_HEIGHT, FRAME_WIDTH, effectModule } from "./effects";
import { createEffectSurface, type EffectSurface } from "./effect-surface";
import {
  createEffectSelector, effectForCondition, fallbackWeather, loadWeather,
  type EffectSelector, type LoadWeatherOptions,
} from "./weather";
import type { NormalizedWeather, OfficeLoadingConfig, WeatherEffectId } from "./types";
// The stylesheet travels with this component; Vite emits it into the plugin's
// one CSS file. No other shell stylesheet is edited.
import "./loading.css";

export type LoadingPhase = "boot" | "loading" | "error";

export interface OfficeLoadingScreenProps {
  phase?: LoadingPhase;
  /** True once the office iframe has loaded; the screen cross-fades away. */
  ready?: boolean;
  /** 0..1 when a real progress source exists; omit for indeterminate. */
  progress?: number | null;
  /** Status line while loading (never the only accessibility signal). */
  status?: string;
  /** The real failure detail on `phase: "error"`. */
  error?: string | null;
  onRetry?: () => void;
  /** Owner config; normally supplied by a host global (see config.ts). */
  config?: OfficeLoadingConfigInput;
  /** Test seam: override the OS reduced-motion preference. */
  prefersReducedMotion?: boolean;
  /** Test seam: override the low-power heuristic. */
  lowPower?: boolean;
  /** Test seam: replace the network call. */
  weatherLoader?: (config: OfficeLoadingConfig, opts: LoadWeatherOptions) => Promise<NormalizedWeather>;
}

const FADE_MS = 420;
const TIP_MS = 9000;

function systemPrefersReducedMotion(): boolean {
  try {
    return typeof matchMedia === "function"
      && matchMedia("(prefers-reduced-motion: reduce)").matches;
  } catch {
    return false;
  }
}

function detectLowPower(): boolean {
  if (typeof navigator === "undefined") return false;
  const nav = navigator as Navigator & {
    deviceMemory?: number;
    connection?: { saveData?: boolean };
  };
  if (nav.connection?.saveData) return true;
  if (typeof nav.hardwareConcurrency === "number" && nav.hardwareConcurrency <= 2) return true;
  if (typeof nav.deviceMemory === "number" && nav.deviceMemory <= 2) return true;
  return false;
}

function weatherLabel(weather: NormalizedWeather | null): string {
  if (!weather) return "Reading the weather\u2026";
  if (weather.condition === "fallback") {
    if (weather.reason === "offline") return "Weather paused \u2014 offline.";
    if (weather.reason === "timeout") return "Weather is taking too long; showing neutral light.";
    return "Showing neutral weather.";
  }
  const label = weather.condition === "clear-night"
    ? "clear night" : weather.condition.replace(/-/g, " ");
  if (weather.temperatureC !== null) {
    return `${label} \u00b7 ${Math.round(weather.temperatureC)}\u00b0C`;
  }
  return label;
}

function pct(value: number): number {
  return Math.min(100, Math.max(0, Math.round(value * 100)));
}

type ReactLike = OfficeGameShellProps["React"];
type HooksLike = OfficeGameShellProps["hooks"];

export function createOfficeLoadingScreen(
  R: ReactLike,
  hooks: HooksLike,
) {
  const { useState, useEffect, useRef } = hooks;

  return function OfficeLoadingScreen(props: OfficeLoadingScreenProps) {
    const configRef = useRef<OfficeLoadingConfig | null>(null);
    if (!configRef.current) configRef.current = resolveLoadingConfig(props.config ?? null);
    const config = configRef.current;

    const [weather, setWeather] = useState<NormalizedWeather | null>(
      config.weather ?? null,
    );
    const [effect, setEffect] = useState<WeatherEffectId>(
      config.effects ? effectForInitial(config) : "none",
    );
    const [online, setOnline] = useState<boolean>(
      typeof navigator === "undefined" || navigator.onLine !== false,
    );
    const [hidden, setHidden] = useState(false);
    const [leaving, setLeaving] = useState(false);
    const [tipIndex, setTipIndex] = useState(0);
    const selectorRef = useRef<EffectSelector | null>(null);
    if (!selectorRef.current) {
      selectorRef.current = createEffectSelector({ hysteresisMs: config.hysteresisMs });
    }
    const backCanvas = useRef<HTMLCanvasElement | null>(null);
    const frontCanvas = useRef<HTMLCanvasElement | null>(null);
    const surfaces = useRef<EffectSurface[]>([]);

    const reducedMotion = config.reducedMotion ?? props.prefersReducedMotion
      ?? systemPrefersReducedMotion();
    const lowPower = config.lowPower ?? props.lowPower ?? detectLowPower();
    const phase: LoadingPhase = props.phase ?? (props.error ? "error" : "loading");

    // Weather is fetched from an effect, never during render: the first paint
    // is the placeholder and the neutral layer regardless of the network.
    useEffect(() => {
      if (config.weather) {
        setWeather(config.weather);
        return;
      }
      if (!config.location) {
        setWeather(fallbackWeather("no-location"));
        return;
      }
      const controller = typeof AbortController === "function" ? new AbortController() : null;
      let cancelled = false;
      const loader = props.weatherLoader ?? loadWeather;
      loader(config, { signal: controller?.signal ?? null })
        .then((next) => { if (!cancelled) setWeather(next); })
        .catch(() => { if (!cancelled) setWeather(fallbackWeather("unknown")); })
        .finally(() => { /* no-op: state above owns the outcome */ });
      return () => {
        cancelled = true;
        controller?.abort();
      };
      // Config is resolved once per screen instance; props.config is a launch seat.
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    // Map the normalized weather to exactly one effect, with hysteresis. A
    // no-effect result also disposes the surfaces below on the same commit.
    useEffect(() => {
      if (!config.effects) {
        setEffect("none");
        return;
      }
      const next = selectorRef.current?.select(weather ?? fallbackWeather("init")) ?? "none";
      setEffect(next);
    }, [weather, config.effects]);

    // Online/offline: a real state, shown as a typed line, not a spinner lie.
    useEffect(() => {
      if (typeof window === "undefined") return;
      const update = () => setOnline(typeof navigator === "undefined" || navigator.onLine !== false);
      update();
      window.addEventListener("online", update);
      window.addEventListener("offline", update);
      return () => {
        window.removeEventListener("online", update);
        window.removeEventListener("offline", update);
      };
    }, []);

    // Tips rotate while the screen is up; the timer is cleared on unmount and
    // never used for anything that could re-render the world.
    useEffect(() => {
      if (!config.tips.length || hidden) return;
      const id = setInterval(() => setTipIndex((i: number) => (i + 1) % config.tips.length), TIP_MS);
      return () => clearInterval(id);
    }, [config.tips.length, hidden]);

    // One surface per layer. `effect: none` disposes them; unmount disposes
    // them. Reduced motion/low power draw a single still frame.
    useEffect(() => {
      const previous = surfaces.current.splice(0);
      for (const surface of previous) surface.dispose();
      if (effect === "none" || !config.effects || hidden) return;
      const module = effectModule(effect);
      if (!module) return;
      const still = reducedMotion || lowPower;
      // Only allocate the passes this effect actually draws (e.g. clouds has
      // no back pass, so it must not run an empty 30 fps loop).
      const back = typeof module.drawBack === "function"
        ? createEffectSurface({ canvas: backCanvas.current, effect, layer: "back", reducedMotion: still })
        : null;
      const front = typeof module.draw === "function"
        ? createEffectSurface({ canvas: frontCanvas.current, effect, layer: "front", reducedMotion: still })
        : null;
      if (back) { back.start(); surfaces.current.push(back); }
      if (front) { front.start(); surfaces.current.push(front); }
      return () => {
        for (const surface of surfaces.current.splice(0)) surface.dispose();
      };
    }, [effect, config.effects, reducedMotion, lowPower, hidden]);

    // Cross-fade out when the frame reports load; a later error brings it back.
    useEffect(() => {
      if (props.ready && phase !== "error") {
        setLeaving(true);
        setHidden(false);
        const id = setTimeout(() => setHidden(true), FADE_MS);
        return () => clearTimeout(id);
      }
      setLeaving(false);
      setHidden(false);
      return undefined;
    }, [props.ready, phase]);

    if (hidden) return null;

    const statusText = phase === "error"
      ? (props.error?.trim() || "The office could not be loaded.")
      : !online
        ? "Offline \u2014 the office will connect when the network returns."
        : props.status?.trim() || "Preparing the office\u2026";
    const weatherText = weatherLabel(weather);
    const tip = config.tips.length ? config.tips[tipIndex % config.tips.length] : "";
    const bg = backgroundImageStyle(config);
    const progressPct = typeof props.progress === "number" ? pct(props.progress) : null;
    const style = {
      "--office-loading-dim": String(config.backgroundDim),
    } as unknown as Record<string, string>;

    const activeModule = config.effects && effect !== "none" ? effectModule(effect) : null;
    const showBack = !!activeModule && typeof activeModule.drawBack === "function";
    const showFront = !!activeModule && typeof activeModule.draw === "function";

    const children: unknown[] = [
      R.createElement("div", {
        key: "bg", className: "office-loading__bg", "aria-hidden": "true",
        style: {
          ...(bg ?? {}),
          backgroundPosition: config.backgroundPosition,
        },
      }),
      R.createElement("div", {
        key: "placeholder", className: "office-loading__placeholder", "aria-hidden": "true",
      }),
      R.createElement("div", {
        key: "veil", className: "office-loading__veil", "aria-hidden": "true",
      }),
    ];

    if (showBack) {
      children.push(R.createElement("canvas", {
        key: "fx-back",
        ref: backCanvas,
        className: "office-loading__fx office-loading__fx--back",
        "data-testid": "office-loading-fx-back",
        width: FRAME_WIDTH,
        height: FRAME_HEIGHT,
        "aria-hidden": "true",
      }));
    }

    const contentChildren: unknown[] = [
      R.createElement("div", {
        key: "mark", className: "office-loading__mark", "aria-hidden": "true",
      }, R.createElement("span", { key: "ring" })),
      R.createElement("h1", {
        key: "brand", className: "office-loading__brand",
      }, config.brand),
      R.createElement("p", {
        key: "tagline", className: "office-loading__tagline",
      }, config.tagline),
      R.createElement("div", {
        key: "status",
        className: "office-loading__status",
        "data-testid": "office-loading-status",
        role: phase === "error" ? "alert" : "status",
        "aria-live": phase === "error" ? "assertive" : "polite",
      }, [
        phase !== "error" ? R.createElement("span", {
          key: "spin", className: "office-loading__spinner", "aria-hidden": "true",
        }) : null,
        R.createElement("span", { key: "text", className: "office-loading__statusText" }, statusText),
      ]),
      R.createElement("div", {
        key: "progress",
        className: `office-loading__progress${progressPct === null ? " is-indeterminate" : ""}`,
        "data-testid": "office-loading-progress",
        role: "progressbar",
        "aria-label": "Loading the office",
        ...(progressPct === null
          ? {}
          : { "aria-valuemin": 0, "aria-valuemax": 100, "aria-valuenow": progressPct }),
      }, R.createElement("span", {
        key: "bar",
        className: "office-loading__bar",
        style: progressPct === null ? undefined : { width: `${progressPct}%` },
      })),
      R.createElement("p", {
        key: "weather",
        className: "office-loading__weather",
        "data-testid": "office-loading-weather",
        "data-condition": weather?.condition ?? "none",
        "data-effect": effect,
      }, weatherText),
    ];

    if (phase === "error") {
      contentChildren.push(R.createElement("div", {
        key: "retry",
        className: "office-loading__error",
        "data-testid": "office-loading-retry",
      }, [
        R.createElement("p", { key: "detail" }, statusText),
        props.onRetry ? R.createElement("button", {
          key: "button",
          type: "button",
          className: "office-loading__button",
          "data-testid": "office-loading-retry-button",
          onClick: () => props.onRetry?.(),
        }, "Retry") : null,
      ]));
    } else if (tip) {
      contentChildren.push(R.createElement("p", {
        key: "tip", className: "office-loading__tip", "data-testid": "office-loading-tip",
      }, tip));
    }

    children.push(R.createElement("div", {
      key: "content", className: "office-loading__content",
    }, contentChildren));

    if (showFront) {
      children.push(R.createElement("canvas", {
        key: "fx-front",
        ref: frontCanvas,
        className: "office-loading__fx office-loading__fx--front",
        "data-testid": "office-loading-fx-front",
        width: FRAME_WIDTH,
        height: FRAME_HEIGHT,
        "aria-hidden": "true",
      }));
    }

    // A single polite announcement, updated only by state changes (never by a
    // frame), so a screen reader is not flooded by the animation.
    children.push(R.createElement("div", {
      key: "announcer",
      className: "office-game__sr-only",
      "data-testid": "office-loading-announcer",
      role: "status",
      "aria-live": "polite",
      "aria-atomic": "true",
    }, `${statusText} ${weatherText}`));

    return R.createElement("div", {
      className: "office-loading",
      "data-testid": "office-loading",
      "data-phase": phase,
      "data-effect": effect,
      "data-weather": weather?.condition ?? "none",
      "data-reduced-motion": reducedMotion ? "1" : "0",
      "data-low-power": lowPower ? "1" : "0",
      "data-leaving": leaving ? "1" : "0",
      "data-offline": online ? "0" : "1",
      role: "region",
      "aria-label": "Qubit Office loading",
      "aria-busy": phase !== "error" && !props.ready,
      style,
    }, children);
  };
}

function effectForInitial(config: OfficeLoadingConfig): WeatherEffectId {
  if (!config.effects || !config.weather) return "none";
  // The selector owns hysteresis after mount; the very first frame may use the
  // injected snapshot directly so a server-rendered weather state is immediate.
  return effectForCondition(config.weather.condition);
}

export { EFFECT_LABELS };
