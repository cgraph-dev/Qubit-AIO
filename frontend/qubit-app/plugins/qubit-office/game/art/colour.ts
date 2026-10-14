/* The colour engine the scenes were missing.
 *
 * WHY THIS FILE EXISTS
 * --------------------
 * The first pass at `scenes.ts` had nine flat hex colours and painted every
 * surface as one solid fill (`grep -c 'shade(|ramp(|mix(' scenes.ts` returned
 * 0). Rooms read as flat rectangles because a wall was literally one value.
 *
 * The fix is not "more colours" — it is a SHADING MODEL. One base colour per
 * material, expanded into a five-tone ramp, with the hue shifted as it moves:
 *
 *   shadows pull toward BLUE (hue 235), highlights toward YELLOW (hue 48)
 *
 * That hue shift is the single thing that separates hand-drawn pixel art from
 * a CSS gradient. Shading a pure grey stays grey and always looks like a
 * wireframe; shading a grey so its darks go cool and its lights go warm reads
 * as a lit surface. It is also why the ramp can be derived rather than
 * authored: nobody hand-picks five values per material, so a new material is
 * one line, not a palette review.
 *
 * Everything here is pure and DOM-free, so it can be unit-tested directly —
 * which matters, because "the art looks wrong" is not otherwise testable.
 */

export type RGB = readonly [number, number, number, number];

/** '#rgb', '#rrggbb', '#rrggbbaa'. Unknown or non-hex input returns magenta.
 *
 * Magenta-on-failure is deliberate: a typo'd colour must be LOUD. Returning
 * black or transparent would hide the mistake inside a dark scene, which is
 * precisely how a bad colour survives to production.
 *
 * The `isFinite` guard is not defensive padding: `parseInt("zz", 16)` returns
 * NaN, so `#zzz` is well-formed LENGTH but not well-formed HEX. Without the
 * guard that colour becomes `#NaNNaNNaN` and paints as nothing at all. */
export function parse(colour: string): RGB {
  const s = colour.trim().toLowerCase();
  if (s.startsWith("#")) {
    const hex = s.slice(1);
    if (hex.length === 3 || hex.length === 4) {
      const n = [...hex].map((ch) => parseInt(ch + ch, 16));
      if (n.every((v) => Number.isFinite(v))) {
        return [n[0], n[1], n[2], hex.length === 4 ? n[3] : 255];
      }
    }
    if (hex.length === 6 || hex.length === 8) {
      const n = (i: number) => parseInt(hex.slice(i, i + 2), 16);
      const v = [n(0), n(2), n(4), hex.length === 8 ? n(6) : 255];
      if (v.every((x) => Number.isFinite(x))) return [v[0], v[1], v[2], v[3]];
    }
  }
  return [255, 0, 255, 255];
}

export function toHex(r: number, g: number, b: number): string {
  const h = (n: number) =>
    Math.round(Math.max(0, Math.min(255, n)))
      .toString(16)
      .padStart(2, "0");
  return `#${h(r)}${h(g)}${h(b)}`;
}

/** As `toHex`, but appends the alpha byte when it is not fully opaque. */
export function alphaHex(hex: string, a: number): string {
  return a >= 255 ? hex : `${hex}${Math.round(Math.max(0, Math.min(255, a))).toString(16).padStart(2, "0")}`;
}

export function rgbToHsl(r: number, g: number, b: number): [number, number, number] {
  const rn = r / 255;
  const gn = g / 255;
  const bn = b / 255;
  const max = Math.max(rn, gn, bn);
  const min = Math.min(rn, gn, bn);
  const l = (max + min) / 2;
  if (max === min) return [0, 0, l];
  const d = max - min;
  const s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
  let h: number;
  if (max === rn) h = (gn - bn) / d + (gn < bn ? 6 : 0);
  else if (max === gn) h = (bn - rn) / d + 2;
  else h = (rn - gn) / d + 4;
  return [h * 60, s, l];
}

export function hslToRgb(h: number, s: number, l: number): readonly [number, number, number] {
  const hh = (((h % 360) + 360) % 360) / 360;
  if (s === 0) {
    const v = Math.round(l * 255);
    return [v, v, v];
  }
  const q = l < 0.5 ? l * (1 + s) : l + s - l * s;
  const p = 2 * l - q;
  const ch = (t: number): number => {
    let x = t;
    if (x < 0) x += 1;
    if (x > 1) x -= 1;
    if (x < 1 / 6) return p + (q - p) * 6 * x;
    if (x < 1 / 2) return q;
    if (x < 2 / 3) return p + (q - p) * (2 / 3 - x) * 6;
    return p;
  };
  return [
    Math.round(ch(hh + 1 / 3) * 255),
    Math.round(ch(hh) * 255),
    Math.round(ch(hh - 1 / 3) * 255),
  ];
}

/** The hue a shadow drifts toward (cool) and a highlight drifts toward (warm). */
export const SHADOW_HUE = 235;
export const LIGHT_HUE = 48;

/** Move hue `h` toward `target`, by at most `deg`. Shortest way round. */
function towardHue(h: number, target: number, deg: number): number {
  let d = ((target - h + 540) % 360) - 180;
  if (Math.abs(d) < deg) return target;
  d = Math.sign(d) * deg;
  return (h + d + 360) % 360;
}

const clamp01 = (v: number) => (v < 0 ? 0 : v > 1 ? 1 : v);

/**
 * Lighten (k > 0) or darken (k < 0) by `k` of HSL lightness, WITH the hue shift.
 *
 * THE SATURATION IS THE POINT. A pure grey has saturation 0 and no hue, so a
 * shift scaled purely by saturation leaves it grey — which is exactly what made
 * the first version of this function useless here: the wall and carpet themes
 * are muted, and a muted material shaded to grey reads as a wireframe.
 *
 * Two things make the tint survive 8-bit rounding, and both are load-bearing:
 *
 *  1. A `+ 0.35` FLOOR on the shift gain, so a neutral colour still moves.
 *  2. A real saturation FLOOR on the result (`max(s, amt * 0.45)` for a shadow,
 *     `max(s, amt * 0.2)` for a light). Without it the hue is technically
 *     correct but the pixel rounds back to grey: saturation 0.027 in an 8-bit
 *     channel is under one unit of colour, so the tint is computed and then
 *     thrown away. A shadow that is *supposed* to be blue-grey must actually
 *     contain blue.
 */
export function shade(colour: string, k: number): string {
  if (k === 0) return colour;
  const [r, g, b, a] = parse(colour);
  let [h, s, l] = rgbToHsl(r, g, b);
  const amt = Math.abs(k);
  const gain = Math.min(1, s * 1.6 + 0.35);
  if (k < 0) {
    h = towardHue(h, SHADOW_HUE, amt * 55 * gain);
    s = clamp01(Math.max(s + amt * 0.12, amt * 0.45));
  } else {
    h = towardHue(h, LIGHT_HUE, amt * 35 * gain);
    s = clamp01(Math.max(s - amt * 0.05, amt * 0.2));
  }
  l = clamp01(l + k);
  const [nr, ng, nb] = hslToRgb(h, s, l);
  return alphaHex(toHex(nr, ng, nb), a);
}

/** Linear blend: t = 0 → a, t = 1 → b. */
export function mix(a: string, b: string, t: number): string {
  const ca = parse(a);
  const cb = parse(b);
  const u = clamp01(t);
  return toHex(
    ca[0] + (cb[0] - ca[0]) * u,
    ca[1] + (cb[1] - ca[1]) * u,
    ca[2] + (cb[2] - ca[2]) * u,
  );
}

/**
 * A five-tone ramp for one material. This is the unit the scenes paint with:
 * every surface asks for `ramp(base)` and uses `hi/lt/base/dk/dd` for its top
 * edge, face, shading and the line under it. Publishing the ramp rather than
 * letting each caller call `shade()` guarantees the five tones are consistent
 * across a material no matter where it is drawn.
 */
export interface Ramp {
  hi: string;
  lt: string;
  base: string;
  dk: string;
  dd: string;
}

const rampCache = new Map<string, Ramp>();

export function ramp(base: string, step = 0.08): Ramp {
  const key = `${base}|${step}`;
  let r = rampCache.get(key);
  if (!r) {
    r = {
      hi: shade(base, step * 2),
      lt: shade(base, step),
      base,
      dk: shade(base, -step),
      dd: shade(base, -step * 2),
    };
    rampCache.set(key, r);
  }
  return r;
}

/**
 * The outline colour for a pixel of colour `c`: a dark version of `c` pulled
 * most of the way to a slate, NEVER pure black.
 *
 * Pure black outlines are the classic amateur tell — they flatten the image and
 * read as a drawing on paper rather than a lit object. Mixing toward a slate
 * keeps the outline related to the material it surrounds.
 */
export const OUTLINE_SLATE = "#272b3b";

export function outlineFor(colour: string): string {
  return mix(shade(colour, -0.45), OUTLINE_SLATE, 0.7);
}

/** Approximate relative luminance, 0–1. Used to decide light/dark contrast. */
export function luminance(colour: string): number {
  const [r, g, b] = parse(colour);
  return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255;
}

/** A deterministic 0–1 generator. Same seed, same art, every run. */
export function seeded(seed: number): () => number {
  let s = (Math.floor(seed) || 1) >>> 0;
  return () => {
    s ^= s << 13;
    s >>>= 0;
    s ^= s >> 17;
    s ^= s << 5;
    s >>>= 0;
    return s / 4294967296;
  };
}
