/*
 * Asset + performance guard for the vendored pixelfx weather subset.
 *
 * This is the test that notices when a future "refresh the vendor" copies the
 * wrong tree, deletes the license, smuggles in a non-weather effect, or slows a
 * frame into something a loading screen cannot afford. It also renders each
 * effect once headlessly, so a module that throws or returns an empty frame
 * fails here rather than in the owner's browser.
 */
import { describe, expect, it } from "vitest";
import { createHash } from "node:crypto";
import { readFileSync, readdirSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import {
  EFFECT_FPS, EFFECT_LABELS, EFFECT_MODULES, FRAME_BUDGET_MS,
  FRAME_HEIGHT, FRAME_WIDTH, effectModule,
} from "./effects";
// @ts-expect-error - vendored pixelfx is plain ESM
import { composite, createFrame, createFx } from "./pixelfx/core/index.js";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const VENDOR = path.resolve(HERE, "pixelfx");
const EXPECTED_EFFECTS = ["god-rays", "clouds", "rain", "storm", "snow", "fog"] as const;

const sha256 = (file: string): string =>
  createHash("sha256").update(readFileSync(file)).digest("hex");

describe("vendored pixelfx subset", () => {
  it("carries only the six weather effects the plan names", () => {
    expect(Object.keys(EFFECT_MODULES).sort()).toEqual([...EXPECTED_EFFECTS].sort());
    expect(effectModule("none")).toBeNull();
    expect(effectModule("rain")?.meta.name).toBe("Rain");
    for (const id of EXPECTED_EFFECTS) {
      expect(EFFECT_LABELS[id]).toBeTruthy();
    }
  });

  it("every effect is a 320x180 looping 30 fps tile", () => {
    for (const id of EXPECTED_EFFECTS) {
      const meta = effectModule(id)!.meta;
      expect(meta.size).toEqual([FRAME_WIDTH, FRAME_HEIGHT]);
      expect(meta.loop).toBe(true);
      expect(meta.fps ?? EFFECT_FPS).toBe(EFFECT_FPS);
      expect(meta.duration).toBeGreaterThan(0);
      const mod = effectModule(id)!;
      expect(typeof mod.draw === "function" || typeof mod.drawBack === "function").toBe(true);
    }
  });

  it("keeps the MIT license and upstream notices", () => {
    const license = readFileSync(path.join(VENDOR, "LICENSE"), "utf8");
    expect(license).toContain("MIT License");
    expect(license).toContain("Patch");
    expect(license).toContain("Hugo Duprez");
    const notice = readFileSync(path.join(VENDOR, "NOTICE.md"), "utf8");
    expect(notice).toContain("Sprite Fusion");
    const provenance = readFileSync(path.join(VENDOR, "PROVENANCE.md"), "utf8");
    expect(provenance).toContain("044e12a45dc9cc312cec50ee606dfc98afe5adc2");
    expect(provenance).toContain("patch-gg/pixelfx");
    expect(provenance).toContain("core/index.js");
  });

  it("matches the recorded vendor hashes and contains no surprise files", () => {
    const manifest = JSON.parse(
      readFileSync(path.join(VENDOR, "VENDOR_HASHES.json"), "utf8"),
    ) as { files: Array<{ path: string; sha256: string }>; authored_locally: string[] };
    for (const entry of manifest.files) {
      expect(sha256(path.join(VENDOR, entry.path)), entry.path).toBe(entry.sha256);
    }
    expect(manifest.authored_locally).toEqual(["core/index.js"]);
    const present = readdirSync(VENDOR, { recursive: true, withFileTypes: true })
      .filter((entry) => entry.isFile())
      .map((entry) => {
        const parent = entry.parentPath ?? (entry as unknown as { path: string }).path;
        return path.relative(VENDOR, path.join(parent, entry.name)).split(path.sep).join("/");
      })
      .sort();
    const allowed = new Set([
      "LICENSE", "NOTICE.md", "PROVENANCE.md", "VENDOR_HASHES.json",
      "core/index.js", ...manifest.files.map((f) => f.path),
    ]);
    for (const file of present) expect(allowed.has(file), file).toBe(true);
  });

  it("renders every effect headlessly into a non-empty 320x180 frame", () => {
    const frame = createFrame(FRAME_WIDTH, FRAME_HEIGHT);
    const out = new Uint8ClampedArray(FRAME_WIDTH * FRAME_HEIGHT * 4);
    for (const id of EXPECTED_EFFECTS) {
      const module = effectModule(id)!;
      const origin = module.meta.origin;
      const fx = createFx(module as never, { x: origin[0], y: origin[1] });
      fx.update(1 / EFFECT_FPS);
      frame.color.fill(0);
      frame.light.fill(0);
      if (typeof module.drawBack === "function") fx.draw(frame, "back");
      if (typeof module.draw === "function") fx.draw(frame, "front");
      composite(frame, out, null);
      let opaque = 0;
      for (let i = 3; i < out.length; i += 4) if (out[i] > 0) opaque += 1;
      expect(opaque, `${id} painted no pixels`).toBeGreaterThan(1000);
    }
  });

  it("keeps one frame comfortably inside a loading-screen budget", () => {
    // The plan's target is 4 ms/frame; CI hosts are shared, so this guard uses
    // a generous ceiling and fails only on a pathological loop (10x+ target),
    // while the measured value is printed for the review record.
    const frame = createFrame(FRAME_WIDTH, FRAME_HEIGHT);
    const out = new Uint8ClampedArray(FRAME_WIDTH * FRAME_HEIGHT * 4);
    const measurements: string[] = [];
    for (const id of EXPECTED_EFFECTS) {
      const module = effectModule(id)!;
      const fx = createFx(module as never, { x: 0, y: 0 });
      const start = performance.now();
      fx.update(1 / EFFECT_FPS);
      fx.draw(frame, "back");
      fx.draw(frame, "front");
      composite(frame, out, null);
      const elapsed = performance.now() - start;
      measurements.push(`${id}=${elapsed.toFixed(2)}ms`);
      expect(elapsed, `${id} frame ${elapsed.toFixed(1)}ms`).toBeLessThan(FRAME_BUDGET_MS * 10);
    }
    // eslint-disable-next-line no-console
    console.info(`[pixelfx] one-frame cost: ${measurements.join(" ")}`);
  });
});
