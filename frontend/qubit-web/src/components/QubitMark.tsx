/**
 * The Qubit wordmark glyph.
 *
 * A qubit schematized as an orbit read edge-on: one nucleus and two steering
 * fields with particles in superposition around them. Deliberately NOT a
 * letterform in a rounded box — the box was the upstream-adjacent tile the
 * brand retired.
 *
 * MOTION MODEL — why it is built this way
 *
 * The obvious approach (spin one rigid <g> holding an ellipse plus dots) reads
 * as a loading spinner: ring and dots move as one solid body, at one speed, on
 * one circle. A qubit does not look like that — it looks like *independent*
 * particles on *independent* elliptical paths, each at its own rate, dimming as
 * it passes behind the nucleus and brightening as it comes forward.
 *
 * So every particle is its own group, offset to the orbit's rim and given that
 * orbit's tilt by an outer wrapper, then spun about the nucleus by an inner
 * group at a per-particle duration. SVG rotation is uniform, so a particle
 * offset along +x sweeps the tilted ellipse, and its distance from the viewer is
 * a pure function of its orbital angle — which is exactly what the depth
 * keyframes in styles/qubit-mark.css exploit: opacity/scale peak at the front of
 * the ellipse and dip when the particle is behind. That is per-particle parallax
 * rather than one rigid spin.
 *
 * Colour comes from the caller (`currentColor`) so the mark survives every
 * theme; the one accent — the particle that is both in and out of the baseline —
 * is the only hard-coded hue and matches the Qubit teal.
 */

/** Orbit radii, shared by the guide ellipses and the particle offsets. */
const RX = 15.2;
const RY = 6.3;
/** Orbit tilts in degrees, matching the guide ellipses. */
const TILT_A = -32;
const TILT_B = 32;

type OrbitName = "a" | "b";

interface Particle {
  /** Orbit this particle rides. */
  orbit: OrbitName;
  /** Orbital seed in degrees — where it sits at t=0. */
  seed: number;
  /** Radius in user units. */
  r: number;
  /** Seconds for one full revolution. Deliberately non-harmonic. */
  dur: number;
  /** The warm "measured" particle. */
  accent?: boolean;
}

/**
 * No two particles share a period, so the system only re-aligns after minutes.
 * That irregularity is what stops the mark reading as a spinner.
 */
const PARTICLES: Particle[] = [
  // Near field (tilted -32deg): faster, brighter, drawn over the nucleus.
  { orbit: "a", seed: 0, r: 1.5, dur: 7.3 },
  { orbit: "a", seed: 74, r: 1.0, dur: 9.1 },
  { orbit: "a", seed: 143, r: 0.85, dur: 6.4, accent: true },
  { orbit: "a", seed: 208, r: 1.1, dur: 10.7 },
  { orbit: "a", seed: 279, r: 0.9, dur: 8.2 },
  { orbit: "a", seed: 331, r: 1.25, dur: 12.1 },
  // Far field (tilted +32deg): slower, dimmer, drawn behind the nucleus.
  { orbit: "b", seed: 31, r: 0.75, dur: 15.4 },
  { orbit: "b", seed: 96, r: 1.05, dur: 13.7 },
  { orbit: "b", seed: 167, r: 0.8, dur: 18.9 },
  { orbit: "b", seed: 232, r: 0.95, dur: 16.3 },
  { orbit: "b", seed: 298, r: 0.7, dur: 21.1 },
];

/** Depth cycle length; each particle is phase-offset within this. */
const DEPTH_DUR = 3.6;

const TILT: Record<OrbitName, number> = { a: TILT_A, b: TILT_B };

export function QubitMark({ className }: { className?: string }) {
  /** Particles of one orbit: outer group carries the static tilt, inner spins. */
  const beads = (orbit: OrbitName) =>
    PARTICLES.filter((p) => p.orbit === orbit).map((p) => (
      <g key={`${orbit}-${p.seed}`} transform={`rotate(${TILT[orbit]} 20 20)`}>
        <g
          className={`qubit-mark__spin qubit-mark__spin--${orbit}`}
          style={{
            animationDuration: `${p.dur}s`,
            animationDelay: `${-(p.seed / 360) * p.dur}s`,
          }}
        >
          <circle
            className={`qubit-mark__bead qubit-mark__bead--${orbit}${p.accent ? " qubit-mark__bead--accent" : ""}`}
            style={{
              animationDuration: `${DEPTH_DUR}s`,
              animationDelay: `${-(p.seed / 360) * DEPTH_DUR}s`,
            }}
            cx={20 + RX}
            cy="20"
            r={p.r}
            fill={p.accent ? "url(#qubit-mark-bead-warm)" : "url(#qubit-mark-bead)"}
          />
        </g>
      </g>
    ));

  return (
    <svg
      aria-hidden="true"
      className={["qubit-mark", className].filter(Boolean).join(" ")}
      fill="none"
      viewBox="0 0 40 40"
      xmlns="http://www.w3.org/2000/svg"
    >
      <defs>
        <linearGradient
          id="qubit-mark-accent"
          x1="6"
          y1="34"
          x2="34"
          y2="6"
          gradientUnits="userSpaceOnUse"
        >
          <stop offset="0" stopColor="#2dd4bf" />
          <stop offset="1" stopColor="#ffe6cb" />
        </linearGradient>
        {/* Teal core glow that breathes with the nucleus. */}
        <radialGradient id="qubit-mark-core" cx="0.5" cy="0.5" r="0.5">
          <stop offset="0" stopColor="#2dd4bf" stopOpacity="0.6" />
          <stop offset="0.45" stopColor="#2dd4bf" stopOpacity="0.2" />
          <stop offset="1" stopColor="#2dd4bf" stopOpacity="0" />
        </radialGradient>
        {/* Soft bloom reused by every particle. */}
        <radialGradient id="qubit-mark-bead" cx="0.5" cy="0.5" r="0.5">
          <stop offset="0" stopColor="#f2fffc" stopOpacity="1" />
          <stop offset="0.4" stopColor="#7ef0e0" stopOpacity="0.7" />
          <stop offset="1" stopColor="#2dd4bf" stopOpacity="0" />
        </radialGradient>
        <radialGradient id="qubit-mark-bead-warm" cx="0.5" cy="0.5" r="0.5">
          <stop offset="0" stopColor="#fff6ea" stopOpacity="1" />
          <stop offset="0.4" stopColor="#ffd9a8" stopOpacity="0.7" />
          <stop offset="1" stopColor="#f59e0b" stopOpacity="0" />
        </radialGradient>
      </defs>

      {/* Ambient halo behind everything. */}
      <circle className="qubit-mark__halo" cx="20" cy="20" r="13" fill="url(#qubit-mark-core)" />

      {/* Far field: dim guide ring, particles behind the nucleus. */}
      <g className="qubit-mark__field qubit-mark__field--far">
        <ellipse
          cx="20"
          cy="20"
          rx={RX}
          ry={RY}
          stroke="currentColor"
          strokeOpacity=".26"
          strokeWidth="1.5"
          transform={`rotate(${TILT_B} 20 20)`}
        />
        {beads("b")}
      </g>

      {/* Nucleus with two staggered shockwaves. */}
      <circle className="qubit-mark__nucleus" cx="20" cy="20" r="3.5" fill="url(#qubit-mark-accent)" />
      <circle className="qubit-mark__pulse" cx="20" cy="20" r="3.5" stroke="currentColor" strokeWidth=".9" />
      <circle
        className="qubit-mark__pulse qubit-mark__pulse--late"
        cx="20"
        cy="20"
        r="3.5"
        stroke="currentColor"
        strokeWidth=".6"
      />

      {/* Near field: bright guide ring, particles over the nucleus. */}
      <g className="qubit-mark__field qubit-mark__field--near">
        <ellipse
          cx="20"
          cy="20"
          rx={RX}
          ry={RY}
          stroke="currentColor"
          strokeOpacity=".5"
          strokeWidth="1.5"
          transform={`rotate(${TILT_A} 20 20)`}
        />
        {beads("a")}
      </g>
    </svg>
  );
}
