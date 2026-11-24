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
  // Q1 (navy) and Q2 (grayscale) were removed at owner request; stored ids
  // from either family resolve to Q3 (q4) — the closest surviving dark look.
  // Q4 (manga) is never used as a migration target: it is a deliberate choice,
  // not a safe default for a stale id.
  "nous-blue": "q4",
  "lens-5i": "q4",
  "qubit-blue": "q4",
  default: "q4",
  q1: "q4",
  "q1-l": "q4-l",
  q2: "q4",
  "q2-l": "q4-l",
  q3: "q4",
  "q3-l": "q4-l",
  q5: "q4",
  "q5-l": "q4-l",
  qubit: "q4",
  "qubit-contrast": "q4",
  "default-large": "q4-l",
  midnight: "q4",
  mono: "q4",
  cyberpunk: "q4",
};

export function migrateThemeName(name: string): string {
  return Object.hasOwn(THEME_NAME_ALIASES, name) ? THEME_NAME_ALIASES[name] : name;
}
