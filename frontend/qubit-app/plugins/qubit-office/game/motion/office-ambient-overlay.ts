/* The non-interactive ambient FX layer for Qubit Office.
 *
 * A `pointer-events: none` SVG mounted inside the office stage. It draws a
 * lunar halo, four warm lantern pools and a deterministic sakura petal drift.
 * It NEVER touches the room canvas, the game loop or the simulation.
 *
 * WHY NO RAF
 * ----------
 * The Stage 8 reference animated petals with `requestAnimationFrame`. Stage 9
 * and this port forbid a second frame loop in the office: the Canvas renderer
 * already owns the frame budget. Every trajectory here is expressed as CSS
 * custom properties + keyframes, so the browser compositor runs it and the
 * office keeps one loop. `motion-audit.test.ts` fails if `requestAnimationFrame`
 * appears under `game/motion/`.
 *
 * Reduced motion is honoured twice over: this component omits the petals and
 * sets a static attribute, and `styles/motion.css` freezes every ambient
 * keyframe under both the OS media query and the shell attribute.
 *
 * React and hooks arrive as arguments, as everywhere else in the plugin, so
 * the dashboard keeps exactly one React runtime. Not mounted by the shell yet:
 * integration into `OfficeAppShell` happens in a separate step.
 */

/* Local copies of the shell's injected-React contract. Importing the shell's
 * type would pull its whole scene graph (Phaser, Canvas host) into this
 * module's type-check for a hook signature; these mirror the same shape. */
type ReactArg = {
  createElement: (...args: unknown[]) => unknown;
};
type HooksArg = {
  useState: <T>(v: T) => [T, (next: T | ((prev: T) => T)) => void];
  useEffect: (fn: () => void | (() => void), deps?: unknown[]) => void;
};
import {
  DEFAULT_LANTERN_POOLS,
  DEFAULT_PETAL_COUNT,
  createPetalPool,
  lanternAnimationDelay,
  petalCssVars,
  type LanternPoolAnchor,
  type PetalSpec,
} from "./ambient-motion";
import { isReducedMotionActive, subscribeReducedMotion } from "./reduced-motion";

export interface OfficeAmbientOverlayProps {
  /** Viewport width in CSS px. Defaults to the 1679x959 reference layout. */
  readonly width?: number;
  /** Viewport height in CSS px. */
  readonly height?: number;
  /** Number of falling sakura petals. Defaults to a sparse 14. */
  readonly petalCount?: number;
  /** Optional host root inspected for `[data-office-reduced-motion="1"]`. */
  readonly hostRoot?: HTMLElement | null;
  /** Force the reduced state (tests / a settings dialog that already knows). */
  readonly reducedMotion?: boolean;
  /** True while the operator pans, drags or scrolls: drift pauses. */
  readonly isUserInteracting?: boolean;
}

const DEFAULT_WIDTH = 1679;
const DEFAULT_HEIGHT = 959;

function stop(
  R: ReactArg,
  key: string,
  offset: string,
  color: string,
  opacity: string,
) {
  return R.createElement("stop", {
    key,
    offset,
    stopColor: color,
    stopOpacity: opacity,
  });
}

/**
 * Build the overlay component with the host's React and hooks. The returned
 * function is a normal React function component.
 */
export function createOfficeAmbientOverlay(R: ReactArg, hooks: HooksArg) {
  const { useState, useEffect } = hooks;

  return function OfficeAmbientOverlay(props: OfficeAmbientOverlayProps) {
    const width = props.width && props.width > 0 ? props.width : DEFAULT_WIDTH;
    const height = props.height && props.height > 0
      ? props.height
      : DEFAULT_HEIGHT;
    const petalCount = props.petalCount ?? DEFAULT_PETAL_COUNT;
    const hostRoot = props.hostRoot ?? null;

    const [detectedReduced, setDetectedReduced] = useState<boolean>(
      isReducedMotionActive(hostRoot));
    const reduced = typeof props.reducedMotion === "boolean"
      ? props.reducedMotion
      : detectedReduced;

    useEffect(() => {
      if (typeof props.reducedMotion === "boolean") {
        return undefined;
      }
      return subscribeReducedMotion(setDetectedReduced, hostRoot);
    }, [hostRoot, props.reducedMotion]);

    const petals: readonly PetalSpec[] = createPetalPool(petalCount);
    const lanterns: readonly LanternPoolAnchor[] = DEFAULT_LANTERN_POOLS;

    const moonCx = Math.round(width * 0.78);
    const moonCy = 44;

    const svgProps: Record<string, unknown> = {
      "data-fx-layer": "ambient",
      viewBox: `0 0 ${width} ${height}`,
      preserveAspectRatio: "xMidYMid slice",
      "aria-hidden": "true",
      focusable: "false",
    };
    if (reduced) {
      svgProps["data-office-fx-reduced"] = "1";
    }
    if (props.isUserInteracting) {
      svgProps["data-office-fx-paused"] = "1";
    }

    return R.createElement(
      "svg",
      svgProps,
      R.createElement(
        "defs",
        { key: "defs" },
        R.createElement(
          "radialGradient",
          { key: "moon", id: "office-fx-moon-halo", cx: "50%", cy: "50%", r: "50%" },
          stop(R, "moon-0", "0%", "var(--office-warn)", "0.42"),
          stop(R, "moon-1", "48%", "var(--office-accent)", "0.16"),
          stop(R, "moon-2", "100%", "var(--office-void)", "0"),
        ),
        R.createElement(
          "radialGradient",
          { key: "lantern", id: "office-fx-lantern-pool", cx: "50%", cy: "50%", r: "50%" },
          stop(R, "lantern-0", "0%", "var(--office-warn)", "0.34"),
          stop(R, "lantern-1", "45%", "var(--office-warn)", "0.14"),
          stop(R, "lantern-2", "100%", "var(--office-void)", "0"),
        ),
      ),
      R.createElement(
        "g",
        { key: "moon-node", "data-fx-node": "moon" },
        R.createElement("circle", {
          key: "halo",
          cx: moonCx,
          cy: moonCy,
          r: 64,
          fill: "url(#office-fx-moon-halo)",
        }),
        R.createElement("circle", {
          key: "disc",
          cx: moonCx,
          cy: moonCy,
          r: 16,
          fill: "var(--office-warn)",
          opacity: "0.28",
        }),
      ),
      lanterns.map((anchor: LanternPoolAnchor, index: number) => R.createElement(
        "g",
        {
          key: anchor.id,
          "data-fx-node": "lantern",
          "data-fx-index": String(index),
          style: { "--fx-lantern-delay": lanternAnimationDelay(anchor) },
        },
        R.createElement("circle", {
          key: "pool",
          cx: Math.round(anchor.nx * width),
          cy: Math.round(anchor.ny * height),
          r: anchor.radiusPx,
          fill: "url(#office-fx-lantern-pool)",
        }),
      )),
      reduced
        ? null
        : petals.map((petal: PetalSpec) => R.createElement(
          "g",
          {
            key: `petal-${petal.id}`,
            "data-fx-node": "petal",
            "data-fx-part": "fall",
            style: petalCssVars(petal, width, height),
          },
          R.createElement(
            "g",
            { key: "sway", "data-fx-part": "sway" },
            R.createElement("path", {
              key: "glyph",
              d: "M0 -6 C3 -4 5 0 2 5 C0 3 -1 3 -2 5 C-5 0 -3 -4 0 -6 Z",
              fill: petal.fillToken,
            }),
          ),
        )),
    );
  };
}
