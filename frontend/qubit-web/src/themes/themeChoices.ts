import { BUILTIN_THEMES, isRetiredThemeName } from "./presets";
import type { ThemeListEntry } from "./types";

/** Built-ins have a stable display order and names owned by the web client.
 * Custom themes follow in server order, with one row per ID. */
export function mergeThemeChoices(served: readonly ThemeListEntry[]): ThemeListEntry[] {
  const choices = new Map<string, ThemeListEntry>();
  for (const { name, label, description } of Object.values(BUILTIN_THEMES)) {
    choices.set(name, { name, label, description });
  }
  for (const entry of served) {
    if (!isRetiredThemeName(entry.name) && !choices.has(entry.name)) {
      choices.set(entry.name, entry);
    }
  }
  return [...choices.values()];
}
