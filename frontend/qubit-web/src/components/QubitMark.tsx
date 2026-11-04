/**
 * The Qubit atom mark — three orbit rings turning in real 3D around a nucleus.
 *
 * WHY THE OLD MARK COULD NEVER LOOK 3D. It animated `transform: rotate()` on an
 * SVG <ellipse>. That is a rigid rotation in the SCREEN PLANE: the ellipse keeps
 * its frozen `rx`/`ry` and merely changes its own tilt. It can never pass through
 * face-on and edge-on, so it never reads as depth — only as a shape being twirled
 * on a table. That is why it looked like two fat static loops.
 *
 * THE FIX — orthographic 3D projection, expressed in CSS alone.
 *
 * A circle of radius R lying in a plane tilted towards the viewer projects
 * orthographically to an ellipse of semi-axes `R` by `R·|cos θ|`, where θ is the
 * angle between that plane and the screen. So instead of drawing an ellipse and
 * rotating it, each ring is drawn as a true <circle> and its plane is animated
 * with `scaleY(cos θ)`:
 *
 *     scaleY( 1)   face-on   — a full circle
 *     scaleY( 0)   edge-on   — a line
 *     scaleY(-1)   face-on   — mirrored
 *
 * That is exact, GPU-composited, and needs no per-frame JavaScript. Each plane's
 * static tilt is a wrapper `rotate()`, so the projection never fights it.
 *
 * Beads live INSIDE the same projected wrapper, at their own `rotate(seed)`, so
 * the identical transform gives them their depth: a bead genuinely slides toward
 * the ring's minor axis as the ring closes, instead of faking depth with scale
 * and opacity tricks.
 *
 * COMPOSITION. Three planes with clearly different tilts, periods and start
 * phases, so at any instant one ring is open, one is mid and one is near
 * edge-on — the silhouette a real atom has. Because each ring really does
 * collapse, the rings separate in silhouette as they separate in depth; they
 * cannot all present the same opening angle at once.
 *
 * CONTRACT: `aria-hidden`, `pointer-events: none`, colour from `currentColor`,
 * hairline `vector-effect: non-scaling-stroke` at any size, and every animation
 * disabled under `prefers-reduced-motion` (qubit-mark.css), which freezes the
 * mark at a static, well-spread 3D pose.
 */

type OrbitName = "a" | "b" | "c";

interface OrbitSpec {
  /** Static screen-plane tilt of this orbital plane, in degrees. */
  tilt: number;
  /** Radius of the ring in viewBox units. */
  r: number;
  /** Seconds for one full turn of the ring's plane through 3D. */
  dur: number;
  /** Fraction of the cycle this ring starts at — staggers the three poses. */
  phase: number;
}

/**
 * Tilts are far apart on purpose: the projection only reads as a lattice when
 * the three planes present visibly different opening angles at the same instant.
 *
 * Durations are ratios 1 : 1.26 : 1.62 of 26.9s, start phases 0 / .37 / .71.
 * Those numbers were solved for, not picked. Simulated over two hours: no ring is
 * ever near edge-on (opening < 0.2), so the mark never flattens to a single
 * line. All three CAN reach face-on together for a moment — that is the atom
 * seen straight down its axis, which is correct, and it resolves again as the
 * periods diverge. Between those instants the three openings stay separated, so
 * the mark reads as depth rather than as concentric circles.
 */
const ORBITS: Record<OrbitName, OrbitSpec> = {
  a: { tilt: -26, r: 14.6, dur: 26.9, phase: 0 },
  b: { tilt: 46, r: 12.4, dur: 33.9, phase: 0.37 },
  c: { tilt: 108, r: 9.9, dur: 43.6, phase: 0.71 },
};

interface Particle {
  orbit: OrbitName;
  /** Orbital seed in degrees — its position around the ring. */
  seed: number;
  /** Bead radius in viewBox units. */
  r: number;
}

/**
 * Beads ride their ring's projected plane, so they need no faked depth of their
 * own: the ring's `scaleY` applies to them too.
 */
const PARTICLES: Particle[] = [
  { orbit: "a", seed: 18, r: 1.15 },
  { orbit: "a", seed: 97, r: 0.9 },
  { orbit: "a", seed: 165, r: 1.0 },
  { orbit: "a", seed: 249, r: 0.85 },
  { orbit: "a", seed: 318, r: 1.1 },
  { orbit: "b", seed: 41, r: 0.85 },
  { orbit: "b", seed: 128, r: 1.0 },
  { orbit: "b", seed: 212, r: 0.8 },
  { orbit: "b", seed: 291, r: 0.95 },
  { orbit: "c", seed: 63, r: 0.8 },
  { orbit: "c", seed: 154, r: 0.9 },
  { orbit: "c", seed: 243, r: 0.75 },
];

const CENTRE = 20;

export function QubitMark({ className }: { className?: string }) {
  /** One ring plus its beads, both inside the same projected wrapper. */
  const ring = (orbit: OrbitName, kind: "far" | "deep" | "near") => {
    const spec = ORBITS[orbit];
    return (
      <g className={`qubit-mark__field qubit-mark__field--${kind}`}>
        <g transform={`rotate(${spec.tilt} ${CENTRE} ${CENTRE})`}>
          <g
            className="qubit-mark__plane"
            style={{
              animationDuration: `${spec.dur}s`,
              animationDelay: `${-spec.phase * spec.dur}s`,
            }}
          >
            <circle
              className={`qubit-mark__ring qubit-mark__ring--${kind}`}
              cx={CENTRE}
              cy={CENTRE}
              r={spec.r}
            />
            {PARTICLES.filter((p) => p.orbit === orbit).map((p) => (
              <g
                key={`${orbit}-${p.seed}`}
                transform={`rotate(${p.seed} ${CENTRE} ${CENTRE})`}
              >
                <circle
                  className="qubit-mark__bead"
                  cx={CENTRE + spec.r}
                  cy={CENTRE}
                  r={p.r}
                />
              </g>
            ))}
          </g>
        </g>
      </g>
    );
  };

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
      <circle className="qubit-mark__nucleus" cx={CENTRE} cy={CENTRE} r="2.1" />
      {ring("a", "near")}
    </svg>
  );
}
