/* The render loop for one weather layer.
 *
 * A "surface" is one 320×180 canvas drawing either the `back` or the `front`
 * pass of exactly one effect. Two surfaces (one per layer) are composed around
 * the DOM UI, which gives the plan's layer order:
 *
 *     background image → tint/vignette → back effects → UI → front effects
 *
 * The scheduler is injectable so tests can drive frames deterministically and
 * assert that unmount stops the loop (no effect leak). No fetch happens here.
 */

// @ts-expect-error - vendored pixelfx is plain ESM with no .d.ts
import { clearFrame, composite, createFrame, createFx } from "./pixelfx/core/index.js";
import { EFFECT_FPS, FRAME_HEIGHT, FRAME_WIDTH, effectModule, type PixelfxEffectModule } from "./effects";
import type { WeatherEffectId } from "./types";

export interface FrameScheduler {
  request(callback: (timestamp: number) => void): number;
  cancel(id: number): void;
  now(): number;
}

interface ImageDataLike {
  data: Uint8ClampedArray;
}

interface Canvas2DLike {
  createImageData?(w: number, h: number): ImageDataLike;
  putImageData(image: ImageDataLike, x: number, y: number): void;
}

const defaultScheduler: FrameScheduler = {
  request(callback) {
    if (typeof requestAnimationFrame === "function") return requestAnimationFrame(callback);
    // Non-browser fallback (SSR/test without a DOM): one deferred frame.
    return setTimeout(() => callback(defaultScheduler.now()), 16) as unknown as number;
  },
  cancel(id) {
    if (typeof cancelAnimationFrame === "function") cancelAnimationFrame(id);
    else clearTimeout(id);
  },
  now() {
    if (typeof performance !== "undefined" && typeof performance.now === "function") {
      return performance.now();
    }
    return Date.now();
  },
};

export interface EffectSurfaceOptions {
  canvas: HTMLCanvasElement | null;
  effect: WeatherEffectId;
  layer: "back" | "front";
  /** Draw one still frame and stop. */
  reducedMotion?: boolean;
  fps?: number;
  scheduler?: FrameScheduler;
  /** Test/telemetry hook; called after each painted frame. */
  onFrame?: (info: { frames: number; lastFrameMs: number; t: number }) => void;
}

export interface EffectSurface {
  readonly effect: WeatherEffectId;
  readonly layer: "back" | "front";
  /** Number of painted frames since construction (a still frame counts as 1). */
  readonly frames: number;
  /** CPU milliseconds for the last paint (update + draw + composite + blit). */
  readonly lastFrameMs: number;
  isRunning(): boolean;
  start(): void;
  stop(): void;
  dispose(): void;
}

/**
 * Create a surface, or `null` when there is nothing to draw (effect `none`,
 * no canvas, no 2D context, or a headless canvas that cannot allocate pixels).
 * A null surface is a supported state: the screen simply shows its gradient.
 */
export function createEffectSurface(options: EffectSurfaceOptions): EffectSurface | null {
  const { canvas, effect, layer } = options;
  if (!canvas || effect === "none") return null;
  const module = effectModule(effect);
  if (!module) return null;

  const ctx = canvas.getContext("2d", { alpha: true }) as unknown as Canvas2DLike | null;
  if (!ctx || typeof ctx.putImageData !== "function") return null;

  let image: ImageDataLike | null = null;
  try {
    if (typeof ctx.createImageData === "function") image = ctx.createImageData(FRAME_WIDTH, FRAME_HEIGHT);
    else if (typeof ImageData !== "undefined") image = new ImageData(FRAME_WIDTH, FRAME_HEIGHT);
  } catch {
    image = null;
  }
  if (!image) return null;

  canvas.width = FRAME_WIDTH;
  canvas.height = FRAME_HEIGHT;

  const frame = createFrame(FRAME_WIDTH, FRAME_HEIGHT);
  const origin = module.meta.origin ?? [FRAME_WIDTH / 2, FRAME_HEIGHT];
  // The vendored fx contract: x/y place the effect's own origin in frame space.
  const fx = createFx(module as PixelfxEffectModule, { x: origin[0], y: origin[1] });

  const scheduler = options.scheduler ?? defaultScheduler;
  const fps = Math.min(60, Math.max(1, options.fps ?? EFFECT_FPS));
  const interval = 1000 / fps;
  let running = false;
  let disposed = false;
  let rafId: number | null = null;
  let lastPaint = 0;
  let frames = 0;
  let lastFrameMs = 0;

  const paint = (): void => {
    if (disposed || !image) return;
    const t0 = scheduler.now();
    try {
      fx.update(1 / fps);
      clearFrame(frame);
      if (layer === "back") fx.draw(frame, "back");
      else fx.draw(frame, "front");
      composite(frame, image.data, null);
      ctx.putImageData(image as ImageDataLike, 0, 0);
    } finally {
      lastFrameMs = scheduler.now() - t0;
      frames += 1;
      options.onFrame?.({ frames, lastFrameMs, t: fx.t });
    }
  };

  const schedule = (): void => {
    if (disposed || !running || rafId !== null) return;
    if (typeof document !== "undefined" && document.hidden) return;
    rafId = scheduler.request((timestamp) => {
      rafId = null;
      if (disposed || !running) return;
      if (lastPaint && timestamp - lastPaint < interval - 1) {
        schedule();
        return;
      }
      lastPaint = timestamp;
      paint();
      schedule();
    });
  };

  const onVisibility = (): void => {
    if (disposed || !running) return;
    if (typeof document !== "undefined" && document.hidden) {
      if (rafId !== null) {
        scheduler.cancel(rafId);
        rafId = null;
      }
    } else {
      schedule();
    }
  };
  if (typeof document !== "undefined" && typeof document.addEventListener === "function") {
    document.addEventListener("visibilitychange", onVisibility);
  }

  const surface: EffectSurface = {
    effect,
    layer,
    get frames() { return frames; },
    get lastFrameMs() { return lastFrameMs; },
    isRunning: () => running,
    start() {
      if (disposed || running) return;
      running = true;
      if (options.reducedMotion) {
        // Still frame, no loop: motion off but the weather is still readable.
        paint();
        running = false;
        return;
      }
      schedule();
    },
    stop() {
      running = false;
      if (rafId !== null) {
        scheduler.cancel(rafId);
        rafId = null;
      }
    },
    dispose() {
      if (disposed) return;
      surface.stop();
      disposed = true;
      if (typeof document !== "undefined" && typeof document.removeEventListener === "function") {
        document.removeEventListener("visibilitychange", onVisibility);
      }
    },
  };

  return surface;
}
