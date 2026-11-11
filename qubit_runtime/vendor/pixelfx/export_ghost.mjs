// Export the REAL pixelfx ghost to a frame atlas for the Qubit orb.
//
// Delegates to the repo's own renderFrames() (tools/lib/fxlib.mjs) — the
// authoritative way to draw an effect. It owns placement, the per-frame update
// step and the `bare` (effect-only, transparent) buffer.
//
// A hand-rolled harness was wrong twice: it passed the module where createFx
// wants `{ meta, mod }` and got the painter placement wrong, producing a 26x9
// smear in the top-left corner instead of the ghost. The repo's own renderer
// (node tools/render.mjs critters/ghost --gif) draws it correctly, so extraction
// goes through the same code path rather than re-deriving it.
//
// Output on stdout: a JSON header, a NUL byte, then N RGBA frames.
import { loadEffect, renderFrames } from "./tools/lib/fxlib.mjs";

const loaded = await loadEffect("critters/ghost");
// loadEffect returns { id, file, mod, meta }; renderFrames expects { meta, mod }
// where `mod` is the imported module (createFx calls mod.create/mod.draw on it).
const fx = { meta: loaded.meta, mod: loaded.mod };
// MARGIN is load-bearing. The ghost's cold outer glow is drawn with radius 54
// in a 96x100 canvas, so it is CLIPPED at the canvas edge and the halo ends in a
// hard square — the "square aura" defect. renderFrames grows the frame by
// `margin` on every side (createFrame(w, h, -m, -m)), giving the glow room to
// fall off radially to nothing. 60 clears the largest glow (54) plus the drift.
const out = renderFrames(fx, { bg: "none", margin: 60 });

const W = out.w, H = out.h, N = out.n;
const total = new Uint8Array(W * H * 4 * N);
for (let i = 0; i < N; i++) {
  total.set(out.frames[i].clear, i * W * H * 4);
}

const header = {
  name: loaded.meta.name,
  w: W, h: H,
  originX: loaded.meta.origin[0], originY: loaded.meta.origin[1],
  fps: out.fps, duration: loaded.meta.duration, frames: N,
  loop: loaded.meta.loop ?? true,
  source: "patch-gg/pixelfx src/fx/critters/ghost.js",
  license: "MIT (Copyright (c) 2026 Patch)",
};

process.stdout.write(JSON.stringify(header) + "\0");
process.stdout.write(Buffer.from(total.buffer, total.byteOffset, total.byteLength));
