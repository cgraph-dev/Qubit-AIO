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
/** The theme a fresh install starts on: Lofi SciFi (g4-l, large manga). */
export const DEFAULT_THEME_NAME = "g4-l";

export const THEME_NAME_ALIASES: Record<string, string> = {
  // `default` is the owner's chosen default theme: Lofi SciFi (g4-l, the large
  // manga variant). It is an explicit default, not a migration fallback — the
  // stale ids below still resolve to Cyber Ghost (g3), which is the safe
  // landing spot for a theme this install no longer ships.
  default: "g4-l",
  "default-large": "g4-l",
  // Q1 (navy) and Q2 (grayscale) were removed at owner request; stored ids
  // from either family resolve to Cyber Ghost (g3).
  "nous-blue": "g3",
  "lens-5i": "g3",
  "qubit-blue": "g3",
  q1: "g3",
  "q1-l": "g3-l",
  q2: "g3",
  "q2-l": "g3-l",
  q3: "g3",
  "q3-l": "g3-l",
  q5: "g3",
  "q5-l": "g3-l",
  qubit: "g3",
  "qubit-contrast": "g3",
  midnight: "g3",
  mono: "g3",
  cyberpunk: "g3",
  // The pre-rename ids of the two SURVIVING themes. These were live until the
  // id/label offset was removed, so a stored preference can still carry them.
  q4: "g3",
  "q4-l": "g3-l",
  q6: "g4",
  "q6-l": "g4-l",
};

export function migrateThemeName(name: string): string {
  return Object.hasOwn(THEME_NAME_ALIASES, name) ? THEME_NAME_ALIASES[name] : name;
}
