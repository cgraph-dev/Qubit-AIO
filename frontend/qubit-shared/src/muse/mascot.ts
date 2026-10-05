/**
 * Mascot slot — intentionally empty.
 *
 * The pinned upstream surface ships its own capybara mascot asset
 * (`assets/capybara.png`) and a `Mascot` component. That is the upstream
 * product's own mascot, not Qubit's, so neither the component nor the image
 * was ported. This module exists purely as a named, framework-neutral slot so
 * a consumer can render a Qubit mascot later without inventing an API.
 */

/** Whether a Qubit mascot asset is available. Always `false` today. */
export const MASCOT_AVAILABLE = false;

export type MascotVariant = "sky" | "sand" | "lilac";

/** Default slot size, matching the upstream icon-box default. */
export const MASCOT_DEFAULT_SIZE = 42;

/** Returns `null` — no mascot asset has been ported. */
export function mascotAsset(): null {
  return null;
}
