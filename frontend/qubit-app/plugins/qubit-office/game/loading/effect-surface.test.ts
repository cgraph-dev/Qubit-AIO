import { describe, expect, it, vi } from "vitest";
import { createEffectSurface, type FrameScheduler } from "./effect-surface";

function harness() {
  const puts: number[] = [];
  const ctx = {
    createImageData: (w: number, h: number) => ({ data: new Uint8ClampedArray(w * h * 4) }),
    putImageData: vi.fn(() => { puts.push(1); }),
  };
  const canvas = {
    width: 0,
    height: 0,
    getContext: vi.fn(() => ctx),
  } as unknown as HTMLCanvasElement;
  let time = 0;
  let nextId = 0;
  const pending = new Map<number, (ts: number) => void>();
  const scheduler: FrameScheduler = {
    request(cb) { const id = ++nextId; pending.set(id, cb); return id; },
    cancel(id) { pending.delete(id); },
    now: () => time,
  };
  const tick = (delta = 40) => {
    time += delta;
    const batch = [...pending.entries()];
    pending.clear();
    for (const [, cb] of batch) cb(time);
  };
  return { canvas, ctx, puts, scheduler, pending, tick };
}

describe("effect surface", () => {
  it("paints at the authored frame size and throttles the loop", () => {
    const h = harness();
    const surface = createEffectSurface({
      canvas: h.canvas, effect: "rain", layer: "back", scheduler: h.scheduler,
    })!;
    expect(surface).not.toBeNull();
    expect(h.canvas.width).toBe(320);
    expect(h.canvas.height).toBe(180);
    surface.start();
    expect(surface.isRunning()).toBe(true);
    expect(h.pending.size).toBe(1);

    h.tick(40);
    expect(surface.frames).toBe(1);
    expect(h.puts.length).toBe(1);
    expect(h.ctx.putImageData).toHaveBeenCalledTimes(1);

    // 1 ms later is under the 30 fps interval: no second paint.
    h.tick(1);
    expect(surface.frames).toBe(1);
    h.tick(40);
    expect(surface.frames).toBe(2);
    surface.dispose();
  });

  it("draws a single still frame under reduced motion and does not loop", () => {
    const h = harness();
    const surface = createEffectSurface({
      canvas: h.canvas, effect: "snow", layer: "front",
      reducedMotion: true, scheduler: h.scheduler,
    })!;
    surface.start();
    expect(surface.frames).toBe(1);
    expect(h.puts.length).toBe(1);
    expect(surface.isRunning()).toBe(false);
    expect(h.pending.size).toBe(0);
    surface.dispose();
  });

  it("stop cancels the pending frame and a late callback cannot paint", () => {
    const h = harness();
    const surface = createEffectSurface({
      canvas: h.canvas, effect: "fog", layer: "back", scheduler: h.scheduler,
    })!;
    surface.start();
    const [, late] = [...h.pending.entries()][0];
    surface.stop();
    expect(surface.isRunning()).toBe(false);
    expect(h.pending.size).toBe(0);
    const before = surface.frames;
    late(9999);
    expect(surface.frames).toBe(before);
  });

  it("dispose is idempotent and leaves nothing scheduled", () => {
    const h = harness();
    const surface = createEffectSurface({
      canvas: h.canvas, effect: "storm", layer: "front", scheduler: h.scheduler,
    })!;
    surface.start();
    surface.dispose();
    surface.dispose();
    expect(surface.isRunning()).toBe(false);
    expect(h.pending.size).toBe(0);
  });

  it("returns null when there is nothing to draw", () => {
    const h = harness();
    expect(createEffectSurface({
      canvas: null, effect: "rain", layer: "back", scheduler: h.scheduler,
    })).toBeNull();
    expect(createEffectSurface({
      canvas: h.canvas, effect: "none", layer: "back", scheduler: h.scheduler,
    })).toBeNull();
    const noContext = {
      width: 0, height: 0, getContext: () => null,
    } as unknown as HTMLCanvasElement;
    expect(createEffectSurface({
      canvas: noContext, effect: "rain", layer: "back", scheduler: h.scheduler,
    })).toBeNull();
  });

  it("keeps a measured frame within a generous loading budget", () => {
    // Wall time, real mailbox: this is the "no pathological loop" guard. The
    // 4 ms target lives in the plan and is reported by effects.test.ts.
    const h = harness();
    const surface = createEffectSurface({
      canvas: h.canvas, effect: "storm", layer: "back", scheduler: h.scheduler,
    })!;
    surface.start();
    const started = performance.now();
    for (let i = 0; i < 12; i += 1) h.tick(40);
    const elapsed = performance.now() - started;
    expect(surface.frames).toBe(12);
    expect(elapsed).toBeLessThan(2000);
    surface.dispose();
  });
});
