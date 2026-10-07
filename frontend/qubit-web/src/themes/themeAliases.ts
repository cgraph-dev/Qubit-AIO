/**
 * Read-old aliases for built-in theme ids we have shipped previously.
 *
 * Without this, users who saved one of the old names in localStorage (or had
 * it persisted server-side) silently fall back to `defaultTheme`, because the
 * lookup in `resolveTheme` no longer finds the stale key. Keep entries here
 * until enough release cycles have passed that we can reasonably assume nobody
 * still has the old value persisted.
 *
 * This module is deliberately pure (no React, no DOM) so it can be unit-tested
 * in the node environment.
 */
export const THEME_NAME_ALIASES: Record<string, string> = {
  // LENS_5I port introduced the theme; the Qubit-blue rebrand renamed the id
  // from `nous-blue` to `qubit-blue`.
  "lens-5i": "qubit-blue",
  // Pre-rebrand id — read-old so a persisted `nous-blue` preference still
  // resolves to the canonical Qubit id.
  "nous-blue": "qubit-blue",
};

export function migrateThemeName(name: string): string {
  return THEME_NAME_ALIASES[name] ?? name;
}
