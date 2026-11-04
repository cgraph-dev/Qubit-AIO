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

import type { CSSProperties } from "react";

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
  /** Per-bead brightness. Deep-plane beads dim toward the back so the cloud has
   *  depth instead of reading as one flat sheet of identical dots. */
  dim?: number;
}

/**
 * Beads ride their ring's projected plane, so they need no faked depth of their
 * own: the ring's `scaleY` applies to them too. Seeds are spread so every ring
 * always carries at least three visible travellers, and each plane has its own
 * bead scale — the near rings carry larger, brighter beads than the deep ones,
 * which adds depth without a second transform.
 */
const PARTICLES: Particle[] = [
  // a — outer ring.
  { orbit: "a", seed: 12, r: 1.2, dim: 1.0 },
  { orbit: "a", seed: 74, r: 0.85, dim: 1.0 },
  { orbit: "a", seed: 132, r: 1.05, dim: 1.0 },
  { orbit: "a", seed: 196, r: 0.75, dim: 1.0 },
  { orbit: "a", seed: 258, r: 1.1, dim: 1.0 },
  { orbit: "a", seed: 316, r: 0.9, dim: 1.0 },
  // b — second plane.
  { orbit: "b", seed: 28, r: 1.0, dim: 0.88 },
  { orbit: "b", seed: 92, r: 0.8, dim: 0.88 },
  { orbit: "b", seed: 158, r: 1.05, dim: 0.88 },
  { orbit: "b", seed: 224, r: 0.7, dim: 0.88 },
  { orbit: "b", seed: 288, r: 0.95, dim: 0.88 },
  // c — third plane, the lattice's spine.
  { orbit: "c", seed: 44, r: 0.95, dim: 0.78 },
  { orbit: "c", seed: 118, r: 0.75, dim: 0.78 },
  { orbit: "c", seed: 186, r: 0.9, dim: 0.78 },
  { orbit: "c", seed: 252, r: 0.7, dim: 0.78 },
  { orbit: "c", seed: 324, r: 0.85, dim: 0.78 },
  // d — inner plane.
  { orbit: "d", seed: 18, r: 0.85, dim: 0.7 },
  { orbit: "d", seed: 96, r: 0.7, dim: 0.7 },
  { orbit: "d", seed: 174, r: 0.8, dim: 0.7 },
  { orbit: "d", seed: 250, r: 0.65, dim: 0.7 },
  { orbit: "d", seed: 330, r: 0.75, dim: 0.7 },
  // e — innermost plane, smallest beads so the centre stays readable.
  { orbit: "e", seed: 62, r: 0.7, dim: 0.62 },
  { orbit: "e", seed: 142, r: 0.6, dim: 0.62 },
  { orbit: "e", seed: 222, r: 0.72, dim: 0.62 },
  { orbit: "e", seed: 302, r: 0.58, dim: 0.62 },
];

/** Free-floating motes: they do not ride an orbit, they drift in the mark's own
 *  field and cross every plane. Two jobs — they fill the dead gaps between rings
 *  so the lattice reads as a cloud rather than five wires, and their independent
 *  periods keep the mark from ever looking frozen even when the planes are slow. */
interface Mote {
  /** Position at t=0, in viewBox units. */
  cx: number;
  cy: number;
  /** Radius in viewBox units — deliberately small; motes are dust, not beads. */
  r: number;
  /** Drift amplitude in units and the seconds for one drift cycle. */
  drift: number;
  dur: number;
  /** Offset into the drift so no two motes start together. */
  phase: number;
}

const MOTES: Mote[] = [
  // Scattered through the whole 40-unit box, deliberately NOT on any orbit —
  // that is the point. Beads prove the rings turn; motes fill the volume between
  // them so the mark reads as a cloud with structure inside it, not as five
  // wires. Radii stay under 0.5 units: dust, never a bead.
  { cx: 9.4, cy: 25.2, r: 0.42, drift: 1.6, dur: 17.3, phase: 0.05 },
  { cx: 30.8, cy: 13.6, r: 0.36, drift: 1.9, dur: 23.1, phase: 0.31 },
  { cx: 14.2, cy: 11.8, r: 0.48, drift: 1.3, dur: 29.7, phase: 0.62 },
  { cx: 26.4, cy: 28.6, r: 0.34, drift: 2.1, dur: 19.4, phase: 0.18 },
  { cx: 6.2, cy: 16.4, r: 0.32, drift: 1.5, dur: 26.5, phase: 0.77 },
  { cx: 33.6, cy: 22.8, r: 0.44, drift: 1.7, dur: 21.8, phase: 0.44 },
  { cx: 18.6, cy: 32.2, r: 0.3, drift: 1.2, dur: 31.2, phase: 0.9 },
  { cx: 22.2, cy: 7.4, r: 0.38, drift: 1.8, dur: 25.3, phase: 0.12 },
  { cx: 11.8, cy: 20.4, r: 0.28, drift: 1.4, dur: 33.7, phase: 0.25 },
  { cx: 29.2, cy: 18.2, r: 0.26, drift: 1.6, dur: 28.4, phase: 0.53 },
  { cx: 16.4, cy: 27.6, r: 0.3, drift: 2.0, dur: 22.9, phase: 0.68 },
  { cx: 24.8, cy: 10.4, r: 0.24, drift: 1.1, dur: 35.1, phase: 0.37 },
  { cx: 7.8, cy: 30.6, r: 0.34, drift: 1.9, dur: 24.6, phase: 0.81 },
  { cx: 32.4, cy: 8.2, r: 0.28, drift: 1.3, dur: 27.8, phase: 0.09 },
  { cx: 20.6, cy: 36.4, r: 0.22, drift: 1.5, dur: 38.2, phase: 0.72 },
  { cx: 20.4, cy: 3.6, r: 0.26, drift: 1.7, dur: 30.5, phase: 0.15 },
  { cx: 4.6, cy: 22.2, r: 0.24, drift: 1.2, dur: 32.8, phase: 0.58 },
  { cx: 35.4, cy: 27.4, r: 0.3, drift: 1.8, dur: 20.7, phase: 0.94 },
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
                  style={
                    p.dim === undefined
                      ? undefined
                      : ({ ["--bead-dim" as string]: `${p.dim}` } as CSSProperties)
                  }
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
      {MOTES.map((m) => (
        <circle
          key={`mote-${m.cx}-${m.cy}`}
          className="qubit-mark__mote"
          cx={m.cx}
          cy={m.cy}
          r={m.r}
          style={{
            animationDuration: `${m.dur}s`,
            animationDelay: `${-m.phase * m.dur}s`,
            ["--mote-drift" as string]: `${m.drift}`,
          }}
        />
      ))}
      {PAINT_ORDER.slice(0, 3).map(ring)}
      <circle className="qubit-mark__nucleus" cx={CENTRE} cy={CENTRE} r="2.1" />
      {PAINT_ORDER.slice(3).map(ring)}
    </svg>
  );
}
