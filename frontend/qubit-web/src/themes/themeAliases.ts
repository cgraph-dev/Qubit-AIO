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
/** The theme a fresh install starts on: Lofi SciFi (q6-l, large manga). */
export const DEFAULT_THEME_NAME = "q6-l";

export const THEME_NAME_ALIASES: Record<string, string> = {
  // `default` is the owner's chosen default theme: Lofi SciFi (q6-l, the large
  // manga variant). It is an explicit default, not a migration fallback — the
  // stale ids below still resolve to Cyber Ghost (q4), which is the safe
  // landing spot for a theme this install no longer ships.
  default: "q6-l",
  "default-large": "q6-l",
  // Q1 (navy) and Q2 (grayscale) were removed at owner request; stored ids
  // from either family resolve to Cyber Ghost (q4).
  "nous-blue": "q4",
  "lens-5i": "q4",
  "qubit-blue": "q4",
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
  midnight: "q4",
  mono: "q4",
  cyberpunk: "q4",
};

export function migrateThemeName(name: string): string {
  return Object.hasOwn(THEME_NAME_ALIASES, name) ? THEME_NAME_ALIASES[name] : name;
}
