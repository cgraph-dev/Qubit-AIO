/**
 * The Qubit wordmark glyph.
 *
 * A qubit schematized as an orbit read edge-on: one nucleus, two steering
 * fields, and particles carried around them. Deliberately NOT a letterform in
 * a rounded box — the box was the upstream-adjacent tile the brand retired.
 *
 * Colour comes from the caller (`currentColor`) so the mark survives every
 * theme; the one accent — the particle that is both in and out of the
 * baseline — is the only hard-coded hue and matches the Qubit teal.
 *
 * Motion model (keyframes live in styles/qubit-mark.css):
 *   - the two orbit groups counter-rotate at different periods, so the lattice
 *     never repeats on a short cycle;
 *   - each orbit carries a bead of particles whose *depth* is animated rather
 *     than the particle itself: a bead is a child of the rotating group, so it
 *     rides the ellipse for free, and only its opacity/brightness change as it
 *     sweeps toward the viewer — that reads as perspective instead of a dot
 *     sliding along a line;
 *   - the nucleus breathes and emits staggered shockwaves on a slower beat.
 *
 * Everything is transform/opacity so the animation stays on the compositor.
 * `prefers-reduced-motion` freezes the whole thing at its resting frame.
 */

/** Orbit geometry, shared between the ellipse and its particle bead. */
const ORBIT_RX = 15.2;
const ORBIT_RY = 6.3;

/**
 * Particle seeds along each orbit, in degrees of orbital parameter. Spacing is
 * deliberately uneven so the beads never look like a clock face.
 */
const NEAR_SEEDS = [0, 58, 131, 199, 264, 322];
const FAR_SEEDS = [24, 96, 168, 240, 303];

/** Unrotated point on the orbit at orbital parameter `deg`. */
function orbitPoint(deg: number) {
  const rad = (deg * Math.PI) / 180;
  return { cx: 20 + ORBIT_RX * Math.cos(rad), cy: 20 + ORBIT_RY * Math.sin(rad) };
}

export function QubitMark({ className }: { className?: string }) {
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
          <stop offset="0" stopColor="#2dd4bf" stopOpacity="0.55" />
          <stop offset="0.55" stopColor="#2dd4bf" stopOpacity="0.16" />
          <stop offset="1" stopColor="#2dd4bf" stopOpacity="0" />
        </radialGradient>
        {/* Soft bloom reused by every particle. */}
        <radialGradient id="qubit-mark-bead" cx="0.5" cy="0.5" r="0.5">
          <stop offset="0" stopColor="#eafffb" stopOpacity="0.95" />
          <stop offset="0.45" stopColor="#2dd4bf" stopOpacity="0.55" />
          <stop offset="1" stopColor="#2dd4bf" stopOpacity="0" />
        </radialGradient>
      </defs>

      {/* Halo behind the nucleus. */}
      <circle className="qubit-mark__halo" cx="20" cy="20" r="12" fill="url(#qubit-mark-core)" />

      {/* Steering field A — the near orbit, drawn over the nucleus. */}
      <g className="qubit-mark__orbit qubit-mark__orbit--a">
        <ellipse
          cx="20"
          cy="20"
          rx={ORBIT_RX}
          ry={ORBIT_RY}
          stroke="currentColor"
          strokeOpacity=".55"
          strokeWidth="1.7"
          transform="rotate(-32 20 20)"
        />
        {NEAR_SEEDS.map((deg, i) => {
          const { cx, cy } = orbitPoint(deg);
          return (
            <circle
              // Fixed seed geometry, never reordered.
              key={`near-${deg}`}
              className="qubit-mark__bead qubit-mark__bead--near"
              style={{ animationDelay: `${-(deg / 360) * 11}s` }}
              cx={cx}
              cy={cy}
              r={i % 3 === 0 ? 1.15 : 0.8}
              fill="url(#qubit-mark-bead)"
              transform="rotate(-32 20 20)"
            />
          );
        })}
      </g>

      {/* Nucleus: gradient fill, pulsing in place. */}
      <circle className="qubit-mark__nucleus" cx="20" cy="20" r="3.6" fill="url(#qubit-mark-accent)" />
      {/* Shockwaves that expand outward and fade, offset from each other. */}
      <circle className="qubit-mark__pulse" cx="20" cy="20" r="3.6" stroke="currentColor" strokeWidth="1" />
      <circle
        className="qubit-mark__pulse qubit-mark__pulse--late"
        cx="20"
        cy="20"
        r="3.6"
        stroke="currentColor"
        strokeWidth=".7"
      />

      {/* Steering field B — the far orbit, behind the nucleus. */}
      <g className="qubit-mark__orbit qubit-mark__orbit--b">
        <ellipse
          cx="20"
          cy="20"
          rx={ORBIT_RX}
          ry={ORBIT_RY}
          stroke="currentColor"
          strokeOpacity=".28"
          strokeWidth="1.7"
          transform="rotate(32 20 20)"
        />
        {FAR_SEEDS.map((deg, i) => {
          const { cx, cy } = orbitPoint(deg);
          return (
            <circle
              // Fixed seed geometry, never reordered.
              key={`far-${deg}`}
              className="qubit-mark__bead qubit-mark__bead--far"
              style={{ animationDelay: `${-(deg / 360) * 17}s` }}
              cx={cx}
              cy={cy}
              r={i % 2 === 0 ? 1 : 0.72}
              fill="url(#qubit-mark-bead)"
              transform="rotate(32 20 20)"
            />
          );
        })}
      </g>
    </svg>
  );
}
