/**
 * A drifting particle field behind the "Qubit AiO" wordmark.
 *
 * Twelve sparks are positioned along the top and bottom edges of the text box
 * and drift slowly outward, fading as they travel. The point is to make the
 * wordmark feel like it is emitting energy rather than merely sitting there —
 * the same idea as the shockwaves on the mark, scaled to the text.
 *
 * Each spark carries its own duration and delay (derived from its index, not
 * random, so the render is deterministic and matches between server and
 * client). Positions are percentages of the text box so the field stretches
 * with whatever the label says in the active locale.
 *
 * Decorative only: `aria-hidden`, pointer-events none, and the whole field is
 * switched off under `prefers-reduced-motion`.
 */

interface Spark {
  /** Horizontal position across the text box, %. */
  left: number;
  /** Vertical position across the text box, %. */
  top: number;
  /** Diameter in px. */
  size: number;
  /** Drift animation length in seconds. */
  dur: number;
  /** Negative delay so the field is already mid-flight on mount. */
  delay: number;
  /** Horizontal drift distance in px (signed). */
  drift: number;
}

/**
 * Hand-tuned rather than random: sparks cluster loosely around the x-height
 * band and the descenders, which is where they read as coming off the letters.
 */
const SPARKS: Spark[] = [
  { left: 6, top: 18, size: 2, dur: 3.8, delay: -0.4, drift: -5 },
  { left: 17, top: 78, size: 1.5, dur: 4.6, delay: -2.1, drift: 4 },
  { left: 29, top: 12, size: 2.5, dur: 3.2, delay: -1.2, drift: -3 },
  { left: 38, top: 86, size: 1.5, dur: 5.1, delay: -3.4, drift: 5 },
  { left: 47, top: 22, size: 2, dur: 4.1, delay: -0.9, drift: -4 },
  { left: 55, top: 74, size: 1.5, dur: 3.6, delay: -2.7, drift: 3 },
  { left: 63, top: 16, size: 2, dur: 4.9, delay: -1.7, drift: -5 },
  { left: 71, top: 82, size: 2.5, dur: 3.4, delay: -0.2, drift: 4 },
  { left: 79, top: 24, size: 1.5, dur: 4.4, delay: -2.9, drift: -3 },
  { left: 86, top: 70, size: 2, dur: 5.4, delay: -3.9, drift: 5 },
  { left: 92, top: 34, size: 1.5, dur: 3.9, delay: -1.4, drift: -4 },
  { left: 97, top: 60, size: 2, dur: 4.7, delay: -3.1, drift: 3 },
];

export function QubitBrandSparks() {
  return (
    <span className="qbrand-sparks" aria-hidden="true">
      {SPARKS.map((s) => (
        <span
          key={`${s.left}-${s.top}`}
          className="qbrand-sparks__dot"
          style={{
            left: `${s.left}%`,
            top: `${s.top}%`,
            width: `${s.size}px`,
            height: `${s.size}px`,
            animationDuration: `${s.dur}s`,
            animationDelay: `${s.delay}s`,
            // Consumed by the drift keyframes so each spark travels its own way.
            ["--spark-drift" as string]: `${s.drift}px`,
          }}
        />
      ))}
    </span>
  );
}
