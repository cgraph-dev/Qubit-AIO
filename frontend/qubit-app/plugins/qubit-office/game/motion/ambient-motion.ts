/* Ambient motion maths for the Office effects layer.
 *
 * Ported from the Stage 8 repo-aware motion spec, then narrowed to the
 * Stage 9 zero-control-motion rules. Everything here is a pure function or a
 * constant: there is no timer, no `requestAnimationFrame`, and no DOM access.
 * The renderer (`OfficeAmbientOverlay`) turns these values into CSS custom
 * properties, and `styles/motion.css` owns the actual keyframes. That keeps
 * the ambient layer out of the game loop and makes every trajectory
 * reproducible in a test.
 *
 * Colours are token NAMES, never literals, so the theme (LimeZu, classic, a
 * future pack) can reskin the petals without touching this file.
 */

export interface PetalSpec {
  readonly id: number;
  /** Normalized horizontal spawn coordinate, `0..1`. */
  readonly x0: number;
  /** Normalized initial fall progress, `0..1`. */
  readonly y0: number;
  /** Full viewport-height traversal in milliseconds (`11500..18500`). */
  readonly fallDurationMs: number;
  /** Horizontal sway amplitude in CSS pixels (`14..32`). */
  readonly swayPx: number;
  /** Sway oscillation period in milliseconds (`3600..5800`). */
  readonly swayPeriodMs: number;
  /** Uniform glyph scale, `0.8..1.2`. */
  readonly scale: number;
  /** Peak opacity, `0.44..0.74`. */
  readonly opacity: number;
  /** Token reference from `game/styles/tokens.css`. */
  readonly fillToken: string;
}

export interface LanternPoolAnchor {
  readonly id: string;
  /** Normalized viewport X, `0..1`. */
  readonly nx: number;
  /** Normalized viewport Y, `0..1`. */
  readonly ny: number;
  /** Glow radius in CSS pixels (`36..56`). */
  readonly radiusPx: number;
  /** Per-lantern phase seed for the CSS animation delay. */
  readonly seed: number;
}

/** The three token fills a petal may cycle through. All are real tokens. */
export const PETAL_TOKEN_CYCLE: readonly string[] = [
  "var(--office-accent)",
  "var(--office-warn)",
  "var(--office-ink)",
];

/**
 * Four warm pools around the room. Positions are normalized so the same
 * anchors work at 1679x959 and on a phone; the overlay multiplies them by the
 * measured viewport.
 */
export const DEFAULT_LANTERN_POOLS: readonly LanternPoolAnchor[] = [
  { id: "lantern-nw", nx: 0.27, ny: 0.24, radiusPx: 44, seed: 1.1 },
  { id: "lantern-ne", nx: 0.71, ny: 0.22, radiusPx: 48, seed: 2.7 },
  { id: "lantern-sw", nx: 0.34, ny: 0.64, radiusPx: 40, seed: 4.3 },
  { id: "lantern-se", nx: 0.74, ny: 0.68, radiusPx: 44, seed: 5.9 },
];

/** Default petal count — sparse atmosphere, never a blizzard. */
export const DEFAULT_PETAL_COUNT = 14;

/**
 * Deterministic `[0, 1)` generator (Mulberry32). Reproducible across renders
 * and test runs; no external dependency.
 */
function mulberry32(seed: number): () => number {
  let t = seed >>> 0;
  return () => {
    t += 0x6d2b79f5;
    let r = Math.imul(t ^ (t >>> 15), 1 | t);
    r ^= r + Math.imul(r ^ (r >>> 7), 61 | r);
    return ((r ^ (r >>> 14)) >>> 0) / 4294967296;
  };
}

/** Deterministic petal pool. Same seed always yields the same petals. */
export function createPetalPool(
  count: number = DEFAULT_PETAL_COUNT,
  seed: number = 20261118,
): readonly PetalSpec[] {
  const rand = mulberry32(seed);
  const total = Math.max(0, Math.floor(count));
  const pool: PetalSpec[] = [];
  for (let i = 0; i < total; i += 1) {
    pool.push({
      id: i,
      x0: rand(),
      y0: rand(),
      fallDurationMs: 11500 + Math.round(rand() * 7000),
      swayPx: 14 + Math.round(rand() * 18),
      swayPeriodMs: 3600 + Math.round(rand() * 2200),
      scale: 0.8 + Math.round(rand() * 4) * 0.1,
      opacity: 0.44 + rand() * 0.3,
      fillToken: PETAL_TOKEN_CYCLE[i % PETAL_TOKEN_CYCLE.length]!,
    });
  }
  return pool;
}

/**
 * Calm lunar halo breathing, `0.78..1.00` over 9600ms. Freezes at a static
 * `0.88` under reduced motion. Pure: the CSS keyframe is the live path; this
 * sampler is for hosts that draw the moon themselves.
 */
export function sampleMoonGlow(timeMs: number, reducedMotion = false): number {
  if (reducedMotion) {
    return 0.88;
  }
  const phase = ((timeMs % 9600) + 9600) % 9600 / 9600;
  return 0.89 + 0.11 * Math.sin(phase * Math.PI * 2);
}

/**
 * Dual-harmonic warm lantern luminance, clamped to `0.86..1.00` so the
 * peak-to-trough swing stays far below the WCAG 2.3.1 flash threshold.
 * Freezes at `0.92` under reduced motion.
 */
export function sampleLanternFlicker(
  timeMs: number,
  seed = 0,
  reducedMotion = false,
): number {
  if (reducedMotion) {
    return 0.92;
  }
  const t = timeMs * 0.001;
  const slowWave = Math.sin(t * 2.1 + seed * 1.7);
  const shimmerWave = Math.sin(t * 5.3 + seed * 3.1);
  const normalized = 0.93 + slowWave * 0.045 + shimmerWave * 0.025;
  return Math.max(0.86, Math.min(1, normalized));
}

export interface AmbientDriftOffset {
  readonly dx: number;
  readonly dy: number;
}

export interface AmbientDriftOptions {
  readonly reducedMotion?: boolean;
  readonly isInteracting?: boolean;
  readonly maxX?: number;
  readonly maxY?: number;
  readonly periodXMs?: number;
  readonly periodYMs?: number;
}

/**
 * Read-only Lissajous camera micro-drift (`+-4px` X, `+-2px` Y). The live
 * layer uses two CSS animations instead of calling this every frame; the
 * sampler stays available for a host that wants a numeric offset and for the
 * reduced-motion contract test.
 */
export function computeAmbientCameraDrift(
  timeMs: number,
  options: AmbientDriftOptions = {},
): AmbientDriftOffset {
  const {
    reducedMotion = false,
    isInteracting = false,
    maxX = 4,
    maxY = 2,
    periodXMs = 28000,
    periodYMs = 37000,
  } = options;

  if (reducedMotion || isInteracting) {
    return { dx: 0, dy: 0 };
  }

  const radX = (((timeMs % periodXMs) + periodXMs) % periodXMs) / periodXMs
    * Math.PI * 2;
  const radY = (((timeMs % periodYMs) + periodYMs) % periodYMs) / periodYMs
    * Math.PI * 2;
  return {
    dx: Math.round(Math.sin(radX) * maxX * 2) / 2,
    dy: Math.round(Math.cos(radY) * maxY * 2) / 2,
  };
}

/**
 * The CSS custom properties one petal needs. A negative delay starts the
 * petal mid-fall, so the first paint is already populated instead of an empty
 * sky that fills in over the first ~12 seconds.
 */
export function petalCssVars(
  petal: PetalSpec,
  width: number,
  height: number,
): Record<string, string> {
  const safeWidth = Number.isFinite(width) && width > 0 ? width : 1679;
  const safeHeight = Number.isFinite(height) && height > 0 ? height : 959;
  const delay = -Math.round(petal.y0 * petal.fallDurationMs);
  return {
    "--fx-petal-x": `${Math.round(petal.x0 * safeWidth)}px`,
    "--fx-petal-height": `${Math.round(safeHeight)}px`,
    "--fx-petal-breeze": "76px",
    "--fx-petal-fall": `${petal.fallDurationMs}ms`,
    "--fx-petal-delay": `${delay}ms`,
    "--fx-petal-sway": `${petal.swayPx}px`,
    "--fx-petal-sway-dur": `${petal.swayPeriodMs}ms`,
    "--fx-petal-scale": petal.scale.toFixed(2),
    "--fx-petal-opacity": petal.opacity.toFixed(3),
  };
}

/** CSS animation-delay for a lantern, derived from its seed (negative). */
export function lanternAnimationDelay(anchor: LanternPoolAnchor): string {
  const seconds = Math.abs(anchor.seed * 0.9) % 3.4;
  return `-${seconds.toFixed(2)}s`;
}
