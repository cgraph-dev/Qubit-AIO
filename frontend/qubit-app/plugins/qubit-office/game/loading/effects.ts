/* The six vendored pixelfx weather effects, statically imported.
 *
 * Static imports are deliberate: there is no dynamic chunk and therefore no
 * network request (and no waterfall) when the weather changes. The subset is
 * ~180 KB of source and only these six modules are in the import graph.
 *
 * The tile frame is fixed at 320×180 per the plan; a CSS `image-rendering:
 * pixelated` upscale keeps the pixels square on every display.
 */

import type { WeatherEffectId } from "./types";

// @ts-expect-error - vendored pixelfx is plain ESM with no .d.ts
import * as godRays from "./pixelfx/fx/weather/god-rays.js";
// @ts-expect-error - vendored pixelfx is plain ESM with no .d.ts
import * as clouds from "./pixelfx/fx/weather/clouds.js";
// @ts-expect-error - vendored pixelfx is plain ESM with no .d.ts
import * as rain from "./pixelfx/fx/weather/rain.js";
// @ts-expect-error - vendored pixelfx is plain ESM with no .d.ts
import * as storm from "./pixelfx/fx/weather/storm.js";
// @ts-expect-error - vendored pixelfx is plain ESM with no .d.ts
import * as snow from "./pixelfx/fx/weather/snow.js";
// @ts-expect-error - vendored pixelfx is plain ESM with no .d.ts
import * as fog from "./pixelfx/fx/weather/fog.js";

export const FRAME_WIDTH = 320;
export const FRAME_HEIGHT = 180;
/** 30 fps is the effects' authored rate; the plan's budget is a 4 ms frame. */
export const EFFECT_FPS = 30;
export const FRAME_BUDGET_MS = 1000 / EFFECT_FPS;

export interface PixelfxMeta {
  name: string;
  desc?: string;
  size: readonly [number, number];
  origin: readonly [number, number];
  duration: number;
  loop?: boolean;
  fps?: number;
  tags?: readonly string[];
}

export interface PixelfxPainter {
  [key: string]: unknown;
}

export interface PixelfxEffectModule {
  meta: PixelfxMeta;
  draw?: (painter: PixelfxPainter, t: number, options: Record<string, unknown>) => void;
  drawBack?: (painter: PixelfxPainter, t: number, options: Record<string, unknown>) => void;
}

export const EFFECT_MODULES: Readonly<Partial<Record<WeatherEffectId, PixelfxEffectModule>>> = Object.freeze({
  "god-rays": godRays as PixelfxEffectModule,
  clouds: clouds as PixelfxEffectModule,
  rain: rain as PixelfxEffectModule,
  storm: storm as PixelfxEffectModule,
  snow: snow as PixelfxEffectModule,
  fog: fog as PixelfxEffectModule,
});

export function effectModule(id: WeatherEffectId): PixelfxEffectModule | null {
  return EFFECT_MODULES[id] ?? null;
}

/** Human labels for the debug/status surface (never the only announcement). */
export const EFFECT_LABELS: Readonly<Record<WeatherEffectId, string>> = Object.freeze({
  none: "clear",
  "god-rays": "sunbeams",
  clouds: "clouds",
  rain: "rain",
  storm: "storm",
  snow: "snow",
  fog: "fog",
});
