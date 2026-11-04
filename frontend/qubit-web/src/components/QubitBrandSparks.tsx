/**
 * An orbital particle cloud threaded through the "Qubit AiO" wordmark.
 *
 * The mark on the left of the header reads as a qubit: particles travelling
 * tilted ellipses around a nucleus. This component continues that language
 * across the text — each spark rides a tilted ellipse whose centre is the
 * wordmark itself, so some arcs sweep over the letters and some sweep behind
 * them. The label is sandwiched between two of those arcs: the near arc is
 * painted on top of the text, the far arc behind it, which is what makes the
 * wordmark read as sitting inside a rotating cloud rather than in front of a
 * flat field of dots.
 *
 * HOW THE 3D IS FAKED (these are plain 2D transforms — no perspective — so the
 * depth comes from three cheap compositor-friendly pieces):
 *
 *  1. ORBIT. Three nested spans, one job each, because CSS animations on the
 *     same property do not stack:
 *       .qbrand-sparks__plane  <- static ellipse TILT (opposite sign per plane)
 *       .qbrand-sparks__orbit  <- the SPIN (one revolution per period)
 *       .qbrand-sparks__dot    <- placed at its SEED ANGLE on the ellipse and
 *                                 runs the DEPTH cycle (scale + opacity)
 *     Placing the dot at the seed angle is essential: a dot pinned at
 *     x = centre + radius inside a rotating group lands every spark on the same
 *     coordinate at t=0 and the group just spins around the pile. With a seed
 *     per spark the ellipse stays populated at all times.
 *
 *  2. DEPTH. The dot's own animation shrinks and dims it as the orbit carries
 *     it to the far half of the ellipse and grows and brightens it on the near
 *     half, at exactly half the orbit period so the two stay in lockstep. Near
 *     particles are given larger base sizes and higher peak opacity; far ones
 *     are smaller and faint.
 *
 *  3. LAYERING. The far plane sits at z-index -1 (behind the text fill) and the
 *     near plane at z-index 2 (in front of it), so the arcs visibly thread
 *     through the letters instead of floating in a single flat layer.
 *
 * Everything is derived from the index or the fixed table below — no
 * Math.random() — so the render is identical between server and client and
 * between reloads.
 *
 * Decorative only: the root is `aria-hidden`, inherits `pointer-events: none`
 * from the stylesheet, and the whole field is `display: none` under
 * `prefers-reduced-motion` (see `.qbrand-sparks` in qubit-mark.css).
 */

/** Orbit planes. "near" arcs pass in front of the label, "far" behind it. */
type OrbitPlane = "near" | "far";

interface Spark {
  /** Orbit plane; also fixes the z-index relative to the wordmark. */
  plane: OrbitPlane;
  /**
   * Orbital seed in degrees — where on the ellipse the spark sits at t=0.
   * This is the value that distributes the particles; see the header note.
   */
  seed: number;
  /** Semi-axis along the orbit plane's local x, in px. */
  rx: number;
  /** Semi-axis along the orbit plane's local y, in px. */
  ry: number;
  /** Diameter in px at the near side of the orbit. */
  size: number;
  /** Seconds for one full revolution. Non-harmonic between sparks. */
  dur: number;
}

/**
 * Hand-tuned rather than random. Ellipses are wide and shallow so the arcs
 * read as a ring lying at an angle across the text (an electron orbit seen in
 * perspective), and the radii alternate a little so the cloud is not a single
 * perfect ring. Periods share no common multiple, so the system only re-aligns
 * after minutes and never pulses.
 */
const SPARKS: Spark[] = [
  // Near plane: in front of the letters, drawn larger and brighter.
  { plane: "near", seed: 312, rx: 26, ry: 9, size: 2.6, dur: 9.4 },
  { plane: "near", seed: 22, rx: 30, ry: 11, size: 2.2, dur: 12.7 },
  { plane: "near", seed: 88, rx: 24, ry: 8, size: 2.4, dur: 8.3 },
  { plane: "near", seed: 147, rx: 33, ry: 12, size: 2.0, dur: 14.1 },
  { plane: "near", seed: 205, rx: 24, ry: 9, size: 2.6, dur: 10.6 },
  { plane: "near", seed: 261, rx: 29, ry: 10, size: 2.1, dur: 11.9 },
  // Second near band: a tighter, faster inner ring so the cloud has density
  // near the letters as well as a wide sweep — one radius alone reads as a
  // single hoop, not a cloud.
  { plane: "near", seed: 34, rx: 17, ry: 6, size: 2.2, dur: 7.1 },
  { plane: "near", seed: 96, rx: 20, ry: 7, size: 2.5, dur: 9.7 },
  { plane: "near", seed: 158, rx: 16, ry: 5.5, size: 2.0, dur: 8.9 },
  { plane: "near", seed: 214, rx: 21, ry: 7.5, size: 2.4, dur: 10.3 },
  { plane: "near", seed: 287, rx: 18, ry: 6.5, size: 2.1, dur: 12.3 },
  { plane: "near", seed: 331, rx: 22, ry: 8, size: 2.7, dur: 13.1 },
  // Far plane: behind the letters, drawn smaller and dimmer.
  { plane: "far", seed: 48, rx: 38, ry: 14, size: 1.9, dur: 18.6 },
  { plane: "far", seed: 103, rx: 34, ry: 12, size: 2.1, dur: 16.2 },
  { plane: "far", seed: 171, rx: 40, ry: 15, size: 1.7, dur: 21.4 },
  { plane: "far", seed: 228, rx: 35, ry: 13, size: 2.0, dur: 17.7 },
  { plane: "far", seed: 292, rx: 37, ry: 14, size: 1.8, dur: 19.8 },
  { plane: "far", seed: 340, rx: 33, ry: 11, size: 2.1, dur: 15.3 },
  // Second far band: a wide, slow halo that keeps the corners of the wordmark
  // populated once the inner bands are busy.
  { plane: "far", seed: 14, rx: 44, ry: 16, size: 1.6, dur: 23.7 },
  { plane: "far", seed: 76, rx: 46, ry: 17, size: 1.5, dur: 25.9 },
  { plane: "far", seed: 137, rx: 42, ry: 15, size: 1.7, dur: 22.3 },
  { plane: "far", seed: 198, rx: 47, ry: 17, size: 1.5, dur: 27.1 },
  { plane: "far", seed: 256, rx: 43, ry: 16, size: 1.6, dur: 24.4 },
  { plane: "far", seed: 314, rx: 45, ry: 16, size: 1.7, dur: 26.3 },
  // Third near band: very tight, very fast motes hugging the glyph edges. These
  // are the "bright dust" that makes the cloud feel dense right at the text
  // rather than only in wide arcs; their periods are the shortest in the table.
  { plane: "near", seed: 51, rx: 13, ry: 4.5, size: 1.5, dur: 5.9 },
  { plane: "near", seed: 117, rx: 12, ry: 4, size: 1.4, dur: 6.7 },
  { plane: "near", seed: 183, rx: 14, ry: 5, size: 1.6, dur: 7.3 },
  { plane: "near", seed: 249, rx: 12.5, ry: 4.5, size: 1.3, dur: 6.2 },
  { plane: "near", seed: 303, rx: 13.5, ry: 4.8, size: 1.5, dur: 7.9 },
  // Third far band: the outermost halo, period 30s+. Reads as the slow drift of
  // a large orbital shell enclosing the whole wordmark.
  { plane: "far", seed: 31, rx: 52, ry: 19, size: 1.3, dur: 31.7 },
  { plane: "far", seed: 99, rx: 55, ry: 20, size: 1.2, dur: 34.9 },
  { plane: "far", seed: 163, rx: 50, ry: 18, size: 1.4, dur: 29.3 },
  { plane: "far", seed: 231, rx: 54, ry: 20, size: 1.2, dur: 33.1 },
  { plane: "far", seed: 277, rx: 51, ry: 19, size: 1.3, dur: 37.3 },
  { plane: "far", seed: 341, rx: 53, ry: 19, size: 1.2, dur: 30.2 },
];

/**
 * Static tilt per plane, in degrees. Opposite tilts (one descending left to
 * right, one ascending) is what stops the two planes reading as one flat ring;
 * it also echoes the ±30deg guide ellipses on the mark to the left.
 *
 * The tilt is applied to the PLANE, not to the spinning orbit group: an inline
 * transform on an element that also has a running transform animation is
 * overridden for the whole animation, so the tilt and the spin need separate
 * elements. The value is passed down as `--spark-tilt` and read by CSS.
 */
const TILT: Record<OrbitPlane, number> = { near: -18, far: 22 };

/**
 * Fixed micro-rotations (degrees) that break the perfect symmetry of the two
 * planes, derived from the index with no randomness. Kept small so each orbit
 * still reads as an ellipse.
 */
function jitter(index: number): number {
  return ((index * 37) % 9) - 4;
}

/** Custom-property bag; React's CSSProperties does not admit `--*` keys. */
type SparkVars = Record<string, string>;

export function QubitBrandSparks() {
  /** All sparks of one plane. The far plane renders before the label, near after. */
  const sparks = (plane: OrbitPlane) =>
    SPARKS.map((s, index) => ({ s, index }))
      .filter(({ s }) => s.plane === plane)
      .map(({ s, index }) => {
        // One full revolution. Non-harmonic across sparks so the cloud never
        // pulses back into a single recognizable pattern.
        const orbit = s.dur;
        // Negative partial delay so each spark is already mid-flight on mount
        // and the cloud is populated on the first painted frame.
        const delay = -((s.seed / 360) * orbit);
        // The depth cycle is exactly HALF the orbit: one cycle = one half-turn,
        // so the fade-to-far bottoms out as the spark crosses behind the text
        // and it grows again as it swings forward. Tying the duration to the
        // period (rather than to a separate table) is what keeps scale and
        // opacity locked to the orbit at any speed.
        const depthDur = orbit / 2;
        // Geometry, all here rather than spread across wrappers:
        //   seed  -> where on the orbit circle this spark starts (degrees)
        //   rx/ry -> the ellipse is a CIRCLE of radius rx, squashed on y by
        //           ry/rx. The squash lives on the plane; the dot undoes it.
        const squash = s.ry / s.rx;
        const style: SparkVars = {
          width: `${s.size}px`,
          height: `${s.size}px`,
          animationDuration: `${depthDur}s`,
          animationDelay: `${delay / 2}s`,
          // Counter-scale handed to the depth keyframes (they own `transform`,
          // so it must ride inside them). Exact inverse of the path squash,
          // which is what keeps the particle circular.
          "--spark-unsquash": `${1 / squash}`,
          // Read by the depth keyframes: near sparks peak brighter than far
          // ones and never dim as far, so the two planes stay legible as
          // distinct depths rather than merging into one flat cloud.
          "--spark-peak": plane === "near" ? "0.96" : "0.62",
          "--spark-floor": plane === "near" ? "0.16" : "0.06",
        };
        return (
          <span
            key={`${plane}-${s.seed}`}
            className={`qbrand-sparks__orbit qbrand-sparks__orbit--${plane}`}
            style={{
              animationDuration: `${orbit}s`,
              animationDelay: `${delay}s`,
            } as SparkVars}
          >
            {/* Place at the seed angle on the orbit circle; the spin on the
                parent group carries it around. `translateX` then `rotate`
                ordering puts the dot on the rim at that angle. */}
            <span
              className="qbrand-sparks__place"
              style={{
                // Squash comes AFTER translateX so it deforms the path, not the
              // offset direction. The dot inverts it to stay round.
              transform: `rotate(${s.seed + jitter(index)}deg) translateX(${s.rx}px) scaleY(${squash})`,
              } as SparkVars}
            >
              <span
                className={`qbrand-sparks__dot qbrand-sparks__dot--${plane}`}
                style={style}
              />
            </span>
          </span>
        );
      });

  return (
    <span className="qbrand-sparks" aria-hidden="true">
      {/* Behind the label: its z-index sits below the gradient text fill. */}
      <span
        className="qbrand-sparks__plane qbrand-sparks__plane--far"
        style={{ "--spark-tilt": `${TILT.far}deg` } as SparkVars}
      >
        {sparks("far")}
      </span>
      {/* In front of the label: its z-index climbs above the text fill so some
          arcs visibly thread over the letters. */}
      <span
        className="qbrand-sparks__plane qbrand-sparks__plane--near"
        style={{ "--spark-tilt": `${TILT.near}deg` } as SparkVars}
      >
        {sparks("near")}
      </span>
    </span>
  );
}
