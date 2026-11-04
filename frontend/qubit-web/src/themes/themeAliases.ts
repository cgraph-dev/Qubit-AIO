/**
 * Read-old aliases for built-in theme ids we have shipped previously.
 *
 * Without this, users who saved one of the old names in localStorage (or had
 * it persisted server-side) silently fall back to `defaultTheme`, because the
 * lookup in `resolveTheme` no longer finds the stale key. Every alias must
 * point at a theme that still exists; the retired themes (rose, ember, blue)
 * have no successor and intentionally fall back to the default.
 *
 * This module is deliberately pure (no React, no DOM) so it can be unit-tested
 * in the node environment. Keep in sync with `_THEME_NAME_ALIASES` in
 * `qubit_cli/web_server_dashboard.py`.
 */
export const THEME_NAME_ALIASES: Record<string, string> = {
  // Pre-Qubit ids for light blue, now Q2.
  "nous-blue": "q2",
  "lens-5i": "q2",
  "qubit-blue": "q2",
  // The old roomier Q1 is now a real theme: Q1-L.
  "default-large": "q1-l",
  midnight: "q3",
  mono: "q4",
  cyberpunk: "q5",
  "qubit-contrast": "q6",
};

export function migrateThemeName(name: string): string {
  return THEME_NAME_ALIASES[name] ?? name;
}
