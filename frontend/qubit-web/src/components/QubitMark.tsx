/**
 * The Qubit wordmark glyph.
 *
 * A qubit schematized as an orbit read edge-on: a nucleus and two steering
 * fields, with particles travelling the ellipses. Deliberately NOT a letterform
 * in a rounded box — the box was the upstream-adjacent tile the brand retired.
 *
 * VISUAL LANGUAGE: thin RGB outline, not a glow. The mark is a hairline
 * construction drawing — 1px chromatic strokes and small solid particles on a
 * transparent field. No bloom, no drop-shadow, no radial gradients: those read
 * as a toy app icon. The only "colour" is the red/green/blue fringe along the
 * thin outline, which is what makes it look like a technical diagram rather
 * than a sticker.
 *
 * MOTION: every particle sits at its own orbital seed on a tilted ellipse, and
 * an inner group spins it about the nucleus at a per-particle duration. The
 * seed offset is what distributes them — a bead placed at cx=20+RX with
 * rotate(0) just piles every particle on the same point, which is the bug that
 * made the previous version look empty and static.
 */

/** Orbit radii, shared by the guide ellipses and the particle offsets. */
const RX = 14.5;
const RY = 5.6;
/** Orbit tilts in degrees, matching the guide ellipses. */
const TILT_A = -30;
const TILT_B = 30;

type OrbitName = "a" | "b";

interface Particle {
  /** Orbit this particle rides. */
  orbit: OrbitName;
  /** Orbital seed in degrees — its position at t=0. */
  seed: number;
  /** Radius in user units. */
  r: number;
  /** Seconds for one full revolution. Deliberately non-harmonic. */
  dur: number;
}

/**
 * No two particles share a period, so the system only re-aligns after minutes.
 * That irregularity is what stops the mark reading as a spinner.
 */
const PARTICLES: Particle[] = [
  // Near field (tilted -30deg): faster, drawn over the nucleus.
  { orbit: "a", seed: 18, r: 1.15, dur: 7.3 },
  { orbit: "a", seed: 97, r: 0.9, dur: 9.1 },
  { orbit: "a", seed: 165, r: 1.0, dur: 6.4 },
  { orbit: "a", seed: 249, r: 0.85, dur: 10.7 },
  { orbit: "a", seed: 318, r: 1.1, dur: 8.2 },
  // Far field (tilted +30deg): slower, dimmer, drawn behind the nucleus.
  { orbit: "b", seed: 41, r: 0.85, dur: 15.4 },
  { orbit: "b", seed: 128, r: 1.0, dur: 13.7 },
  { orbit: "b", seed: 212, r: 0.8, dur: 18.9 },
  { orbit: "b", seed: 291, r: 0.95, dur: 16.3 },
];

const TILT: Record<OrbitName, number> = { a: TILT_A, b: TILT_B };

/** Unrotated point on the orbit at orbital parameter `deg`. */
function orbitPoint(deg: number) {
  const rad = (deg * Math.PI) / 180;
  return { cx: 20 + RX * Math.cos(rad), cy: 20 + RY * Math.sin(rad) };
}

export function QubitMark({ className }: { className?: string }) {
  /**
   * Particles of one orbit. The OUTER group applies the orbit tilt (static) and
   * the INNER group spins about the nucleus. The bead is placed at its seed
   * angle so the particles start distributed around the ellipse; the spin then
   * carries each one at its own rate.
   */
  const beads = (orbit: OrbitName) =>
    PARTICLES.filter((p) => p.orbit === orbit).map((p) => {
      const { cx, cy } = orbitPoint(p.seed);
      return (
        <g key={`${orbit}-${p.seed}`} transform={`rotate(${TILT[orbit]} 20 20)`}>
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

  return (
    <svg
      aria-hidden="true"
      className={["qubit-mark", className].filter(Boolean).join(" ")}
      fill="none"
      viewBox="0 0 40 40"
      xmlns="http://www.w3.org/2000/svg"
    >
      {/* Far field: hairline ring, particles behind the nucleus.
       * The tilt lives on the wrapper <g>; the ellipse itself is free for CSS
       * to rotate, so the ring actually spins instead of sitting static while
       * only the particles travel. */}
      <g className="qubit-mark__field qubit-mark__field--far">
        <g transform={`rotate(${TILT_B} 20 20)`}>
          <ellipse
            className="qubit-mark__ring qubit-mark__ring--far"
            cx="20"
            cy="20"
            rx={RX}
            ry={RY}
          />
        </g>
        {beads("b")}
      </g>

      {/* Nucleus: a small solid dot, no bloom. */}
      <circle className="qubit-mark__nucleus" cx="20" cy="20" r="2.4" />

      {/* Near field: hairline ring over the nucleus, particles on top. */}
      <g className="qubit-mark__field qubit-mark__field--near">
        <g transform={`rotate(${TILT_A} 20 20)`}>
          <ellipse
            className="qubit-mark__ring qubit-mark__ring--near"
            cx="20"
            cy="20"
            rx={RX}
            ry={RY}
          />
        </g>
        {beads("a")}
      </g>
    </svg>
  );
}
