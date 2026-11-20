/**
 * Read-old aliases for built-in theme ids we have shipped previously.
 *
 * Without this, users who saved one of the old names in localStorage (or had
 * it persisted server-side) silently fall back to Q2, because the lookup in
 * `resolveTheme` no longer finds the stale key. Every alias must point at a
 * theme that still exists; retired ids with no successor fall back to Q2.
 *
 * This module is deliberately pure (no React, no DOM) so it can be unit-tested
 * in the node environment. Keep in sync with `_THEME_NAME_ALIASES` in
 * `qubit_cli/web_server_dashboard.py`.
 */
export const THEME_NAME_ALIASES: Record<string, string> = {
  // The old light-blue-ish ids were the greyscale/light family; they now
  // resolve to Q3 (greyscale), the closest surviving look.
  "nous-blue": "q3",
  "lens-5i": "q3",
  "qubit-blue": "q3",
  // Q1/Q1-L/Q5/Q5-L/Qubit Command were retired at owner request. Existing
  // stored ids move to the closest surviving theme; no hidden id is ever
  // re-listed in the picker.
  default: "q2",
  q1: "q2",
  "q1-l": "q2-l",
  q5: "q2",
  "q5-l": "q2-l",
  qubit: "q2",
  "qubit-contrast": "q2",
  "default-large": "q2-l",
  midnight: "q2",
  mono: "q3",
  cyberpunk: "q4",
};

export function migrateThemeName(name: string): string {
  return Object.hasOwn(THEME_NAME_ALIASES, name) ? THEME_NAME_ALIASES[name] : name;
}
