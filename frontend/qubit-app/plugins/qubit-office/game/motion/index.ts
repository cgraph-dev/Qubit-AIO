/* Public surface of the Office ambient-motion layer.
 *
 * Nothing here is wired into `entry.ts` or `OfficeAppShell.tsx` yet; the
 * integration step imports from this barrel. Keeping the surface explicit
 * means the shell never reaches into implementation files.
 */

export {
  DEFAULT_LANTERN_POOLS,
  DEFAULT_PETAL_COUNT,
  PETAL_TOKEN_CYCLE,
  computeAmbientCameraDrift,
  createPetalPool,
  lanternAnimationDelay,
  petalCssVars,
  sampleLanternFlicker,
  sampleMoonGlow,
  type AmbientDriftOffset,
  type AmbientDriftOptions,
  type LanternPoolAnchor,
  type PetalSpec,
} from "./ambient-motion";

export {
  isReducedMotionActive,
  subscribeReducedMotion,
} from "./reduced-motion";

export {
  attachActivityTickerController,
  type ActivityTickerOptions,
} from "./activity-ticker";

export {
  createOfficeAmbientOverlay,
  type OfficeAmbientOverlayProps,
} from "./office-ambient-overlay";
