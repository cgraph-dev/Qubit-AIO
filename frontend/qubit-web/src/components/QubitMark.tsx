/**
 * The Qubit atom mark — five orbit rings turning in real 3D around a nucleus.
 *
 * WHY AN ELLIPSE ROTATE WAS NEVER 3D. Animating `transform: rotate()` on an SVG
 * <ellipse> is a rigid rotation in the SCREEN PLANE: the ellipse keeps its fixed
 * `rx`/`ry` and merely changes its own tilt. It can never pass through face-on and
 * edge-on, so it never shows depth — only a shape being twirled on a table.
 *
 * THE PROJECTION. A circle of radius R in a plane tilted towards the viewer
 * projects orthographically to an ellipse of semi-axes `R` by `R·|cos θ|`. So each
 * ring is a true <circle> and its plane is animated with `scaleY(cos θ)`:
 *
 *     scaleY( 1)   face-on   — a full circle
 *     scaleY( 0)   edge-on   — a line
 *     scaleY(-1)   face-on   — mirrored
 *
 * Exact, GPU-composited, no per-frame JavaScript. The plane's static tilt is a
 * wrapper `rotate()`, so the projection never fights it.
 *
 * WHY FIVE RINGS STILL READ AS FIVE. With five incommensurate periods any two
 * rings will occasionally present a similar opening angle — that is geometry, not
 * a bug. The rings stay distinguishable because every plane has its own RADIUS and
 * its own TILT, so a coincident pair shows as two nested ellipses of clearly
 * different size and axis rather than as one fat ring drawn twice. Radii step down
 * 15.2 → 13.4 → 11.6 → 9.8 → 8.2 units (42.6px → 23.0px across at 56px), so no two
 * silhouettes are ever the same size.
 *
 * MOTION. Each plane turns on its own period (29–61s, none a multiple of another)
 * from its own start phase, so the lattice never visibly loops. Beads ride inside
 * the same projected group, so their depth comes from the same transform instead
 * of faked scale/opacity tricks.
 *
 * CONTRACT: `aria-hidden`, `pointer-events: none`, colour from `currentColor`,
 * hairline `vector-effect: non-scaling-stroke` at any size, and every animation
 * disabled under `prefers-reduced-motion` (qubit-mark.css), which freezes the mark
 * at a static, well-spread 3D pose.
 */

type OrbitName = "a" | "b" | "c" | "d" | "e";

interface OrbitSpec {
  /** Static screen-plane tilt of this orbital plane, in degrees. */
  tilt: number;
  /** Radius of the ring in viewBox units. Unique per plane — see header. */
  r: number;
  /** Seconds for one full turn of the ring's plane through 3D. */
  dur: number;
  /** Fraction of the cycle this ring starts at — staggers the five poses. */
  phase: number;
  /** Which paint-order field the ring belongs to. */
  kind: "far" | "mid" | "deep" | "near" | "closest";
}

/**
 * Tilts are spread across the full 180° so no two planes share an axis. Radii
 * descend monotonically, so the deepest ring is always the smallest: the mark
 * reads as a lattice seen from outside, not as five rings of one size.
 */
const ORBITS: Record<OrbitName, OrbitSpec> = {
  a: { tilt: -24, r: 15.2, dur: 29.0, phase: 0.0, kind: "near" },
  b: { tilt: 44, r: 13.4, dur: 34.5, phase: 0.21, kind: "far" },
  c: { tilt: 104, r: 11.6, dur: 41.5, phase: 0.44, kind: "deep" },
  d: { tilt: 148, r: 9.8, dur: 50.8, phase: 0.63, kind: "mid" },
  e: { tilt: 72, r: 8.2, dur: 61.2, phase: 0.84, kind: "closest" },
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
 * own: the ring's `scaleY` applies to them too. Seeds are spread so every ring
 * always carries at least two visible travellers.
 */
const PARTICLES: Particle[] = [
  { orbit: "a", seed: 18, r: 1.15 },
  { orbit: "a", seed: 112, r: 0.95 },
  { orbit: "a", seed: 205, r: 1.05 },
  { orbit: "a", seed: 298, r: 0.85 },
  { orbit: "b", seed: 46, r: 1.0 },
  { orbit: "b", seed: 158, r: 0.9 },
  { orbit: "b", seed: 262, r: 1.05 },
  { orbit: "c", seed: 72, r: 0.95 },
  { orbit: "c", seed: 196, r: 0.85 },
  { orbit: "c", seed: 310, r: 0.9 },
  { orbit: "d", seed: 34, r: 0.85 },
  { orbit: "d", seed: 216, r: 0.9 },
  { orbit: "e", seed: 128, r: 0.8 },
  { orbit: "e", seed: 288, r: 0.85 },
];

const CENTRE = 20;

/** Paint order, back to front. The nucleus is drawn inside this list so nearer
 *  rings genuinely pass in front of it. */
const PAINT_ORDER: OrbitName[] = ["b", "d", "c", "e", "a"];

export function QubitMark({ className }: { className?: string }) {
  /** One ring plus its beads, both inside the same projected wrapper. */
  const ring = (orbit: OrbitName) => {
    const spec = ORBITS[orbit];
    return (
      <g
        key={orbit}
        className={`qubit-mark__field qubit-mark__field--${spec.kind}`}
      >
        <g transform={`rotate(${spec.tilt} ${CENTRE} ${CENTRE})`}>
          <g
            className="qubit-mark__plane"
            style={{
              animationDuration: `${spec.dur}s`,
              animationDelay: `${-spec.phase * spec.dur}s`,
            }}
          >
            <circle
              className={`qubit-mark__ring qubit-mark__ring--${spec.kind}`}
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
      {PAINT_ORDER.slice(0, 3).map(ring)}
      <circle className="qubit-mark__nucleus" cx={CENTRE} cy={CENTRE} r="2.1" />
      {PAINT_ORDER.slice(3).map(ring)}
    </svg>
  );
}
