/**
 * The Qubit atom mark — several orbit rings turning around one nucleus.
 *
 * VISUAL LANGUAGE: thin chromatic outline, not a glow. The mark is a hairline
 * construction drawing — 1px strokes with a red/green/blue fringe and small
 * solid beads on a transparent field. No bloom, no drop-shadow, no radial
 * gradient; those read as a toy app icon.
 *
 * GEOMETRY — why the rings no longer collapse into one line.
 *
 * Every ring is a true ellipse seen in perspective at its own tilt, and each one
 * turns. Earlier versions flattened all three by the same ratio, and a set of
 * identically-squashed ellipses passes through a common horizontal pose every
 * half-turn — that common pose IS the "all circles stack on one line" frame.
 *
 * The three rings now have clearly different squashes and widely separated
 * tilts (0.46 at −24°, 0.70 at 58°, 0.86 at 116°). No two share a silhouette, so
 * the mark reads as a lattice at every instant while still turning.
 *
 * MOTION — two independent, non-harmonic motions per ring:
 *   1. the RING rotates about the nucleus (its own period),
 *   2. each BEAD travels its orbit (its own period, unrelated to the ring's).
 * Neither is a multiple of the other, so the system does not visibly repeat.
 *
 * The ring turn lives in CSS on `.qubit-mark__ring`; the bead travel lives in CSS
 * on `.qubit-mark__spin`. The wrapper groups carry only the static tilt, so a
 * running transform animation never fights an inline transform.
 *
 * CONTRACT: `aria-hidden`, `pointer-events: none`, and every animation disabled
 * under `prefers-reduced-motion` (see qubit-mark.css).
 */

type OrbitName = "a" | "b" | "c";

interface OrbitSpec {
  /** Static plane tilt in degrees. */
  tilt: number;
  /** Semi-axis along the plane's local x. */
  rx: number;
  /** Semi-axis along the plane's local y — the perspective foreshortening. */
  ry: number;
  /** Seconds for one full turn of the RING itself. */
  ringDur: number;
  /** Ring turns the other way — extra variety, a further distinct phase. */
  reverse: boolean;
}

/**
 * Squash ratios, deliberately far apart:
 *   a  6.9 / 15.0 = 0.46  wide and shallow — an orbit at a glancing angle
 *   b  8.4 / 12.0 = 0.70  a rounder orbit on a clearly different axis
 *   c  6.6 /  7.7 = 0.86  nearly circular — the face-on ring
 * That spread is what keeps them from ever aligning into one line.
 */
const ORBITS: Record<OrbitName, OrbitSpec> = {
  a: { tilt: -24, rx: 15.0, ry: 6.9, ringDur: 21, reverse: false },
  b: { tilt: 58, rx: 12.0, ry: 8.4, ringDur: 31, reverse: true },
  c: { tilt: 116, rx: 7.7, ry: 6.6, ringDur: 43, reverse: false },
};

interface Particle {
  /** Orbit this particle rides. */
  orbit: OrbitName;
  /** Orbital seed in degrees — its position at t=0. */
  seed: number;
  /** Radius in user units. */
  r: number;
  /** Seconds for one full revolution around the nucleus. Non-harmonic. */
  dur: number;
}

/**
 * No two particles share a period, so the beads never travel in lockstep and
 * the mark never reads as a spinner. The seed offset distributes them around
 * the ellipse — a bead placed at rx with no seed would pile every particle on
 * the same point and leave the ring looking empty.
 */
const PARTICLES: Particle[] = [
  // Near field: fastest, drawn over the nucleus.
  { orbit: "a", seed: 18, r: 1.15, dur: 7.3 },
  { orbit: "a", seed: 97, r: 0.9, dur: 9.1 },
  { orbit: "a", seed: 165, r: 1.0, dur: 6.4 },
  { orbit: "a", seed: 249, r: 0.85, dur: 10.7 },
  { orbit: "a", seed: 318, r: 1.1, dur: 8.2 },
  // A second pass over the same ring, so one orbit carries several travellers.
  { orbit: "a", seed: 58, r: 0.7, dur: 5.9 },
  { orbit: "a", seed: 212, r: 0.65, dur: 7.7 },
  // Middle plane: slower, dimmer, drawn behind the nucleus.
  { orbit: "b", seed: 41, r: 0.85, dur: 15.4 },
  { orbit: "b", seed: 128, r: 1.0, dur: 13.7 },
  { orbit: "b", seed: 212, r: 0.8, dur: 18.9 },
  { orbit: "b", seed: 291, r: 0.95, dur: 16.3 },
  // Innermost, slowest field.
  { orbit: "c", seed: 63, r: 0.8, dur: 19.6 },
  { orbit: "c", seed: 154, r: 0.9, dur: 21.2 },
  { orbit: "c", seed: 243, r: 0.75, dur: 17.8 },
];

/** Unrotated point on `orbit` at orbital parameter `deg`, in the plane's own
 *  pre-tilt frame (the wrapper <g> applies the tilt). */
function orbitPoint(orbit: OrbitName, deg: number) {
  const rad = (deg * Math.PI) / 180;
  const spec = ORBITS[orbit];
  return { cx: 20 + spec.rx * Math.cos(rad), cy: 20 + spec.ry * Math.sin(rad) };
}

export function QubitMark({ className }: { className?: string }) {
  /** Beads of one orbit. Outer group = static tilt, inner group = bead travel. */
  const beads = (orbit: OrbitName) =>
    PARTICLES.filter((p) => p.orbit === orbit).map((p) => {
      const { cx, cy } = orbitPoint(orbit, p.seed);
      return (
        <g
          key={`${orbit}-${p.seed}`}
          transform={`rotate(${ORBITS[orbit].tilt} 20 20)`}
        >
          <g
            className="qubit-mark__spin"
            style={{
              animationDuration: `${p.dur}s`,
              animationDelay: `${-(p.seed / 360) * p.dur}s`,
            }}
          >
            <circle
              className="qubit-mark__bead"
              style={{
                animationDuration: "3.6s",
                animationDelay: `${-(p.seed / 360) * 3.6}s`,
              }}
              cx={cx}
              cy={cy}
              r={p.r}
            />
          </g>
        </g>
      );
    });

  /** One turning ring plus its travelling beads. */
  const ring = (orbit: OrbitName, kind: "far" | "deep" | "near") => (
    <g className={`qubit-mark__field qubit-mark__field--${kind}`}>
      <g transform={`rotate(${ORBITS[orbit].tilt} 20 20)`}>
        <ellipse
          className={`qubit-mark__ring qubit-mark__ring--${kind}`}
          cx="20"
          cy="20"
          rx={ORBITS[orbit].rx}
          ry={ORBITS[orbit].ry}
          style={{
            animationDuration: `${ORBITS[orbit].ringDur}s`,
            animationDirection: ORBITS[orbit].reverse ? "reverse" : "normal",
          }}
        />
      </g>
      {beads(orbit)}
    </g>
  );

  return (
    <svg
      aria-hidden="true"
      className={["qubit-mark", className].filter(Boolean).join(" ")}
      fill="none"
      viewBox="0 0 40 40"
      xmlns="http://www.w3.org/2000/svg"
    >
      {ring("b", "far")}
      {ring("c", "deep")}
      <circle className="qubit-mark__nucleus" cx="20" cy="20" r="2.4" />
      {ring("a", "near")}
    </svg>
  );
}
