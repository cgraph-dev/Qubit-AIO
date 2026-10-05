/**
 * Web colour resolver for {@link SurfaceColors}.
 *
 * Reads the Qubit dashboard dark theme straight off the CSS custom
 * properties that `ThemeProvider` already emits (`--background-base`,
 * `--midground-base`, `--foreground-base`, and the shadcn-compat
 * `--color-*` tokens declared in `src/index.css`). No new theming system —
 * these are the tokens the console already consumes.
 *
 * Every value is a `var(...)` reference, so colours follow the active theme
 * (including user themes) without a re-render. The palette is the Qubit DARK
 * theme: near-black teal canvas, warm cream accent/tint.
 */
import type { SurfaceColors } from "./tokens";

export const webSurfaceColors: SurfaceColors = {
  // Canvas = deepest layer; card = the same 4% lift the console's `bg-card`
  // uses, so ported cards sit flush with the existing chrome.
  canvas: "var(--background-base)",
  card: "var(--color-card)",

  // Text + dimmed text come off the midground-derived compat tokens.
  text: "var(--color-foreground)",
  muted: "var(--color-muted-foreground)",

  // Hairlines + dividers.
  line: "var(--color-border)",

  // Primary accent = the theme midground (the warm cream/teal accent the
  // dashboard already uses for buttons and active chrome).
  accent: "var(--color-primary)",
  accentStrong: "var(--color-primary)",
  accentTint: "var(--color-accent)",
  onAccent: "var(--color-primary-foreground)",

  // Neutral secondary button background.
  neutral: "var(--color-secondary)",
  icon: "var(--color-foreground)",

  // Pressed state for icon buttons / link rows.
  pressed: "var(--color-accent)",

  // Destructive text + inline error tint.
  danger: "var(--color-destructive)",
  dangerTint: "color-mix(in srgb, var(--color-destructive) 12%, transparent)",
  successTint: "color-mix(in srgb, var(--color-success) 14%, transparent)",

  // Sheet drag handle.
  handle: "color-mix(in srgb, var(--color-foreground) 28%, transparent)",

  // Modal scrim over the dark canvas.
  scrim: "color-mix(in srgb, var(--background-base) 62%, transparent)",

  // Checkbox tick colour drawn on the checked box.
  onCheck: "var(--color-primary-foreground)",

  // Secondary tint for alternate icon boxes (lavender in the upstream palette).
  accentTintAlt: "var(--color-secondary)",
};

/**
 * Build the CSS-variable block for a `<style>` tag when a consumer needs the
 * ported surface tokens under namespaced custom properties. Optional: the
 * components read `webSurfaceColors` directly, so this is only for markup
 * that needs the tokens as raw CSS.
 */
export function webSurfaceColorVars(colors: SurfaceColors = webSurfaceColors): string {
  return Object.entries(colors)
    .map(([slot, value]) => `  --surface-${kebab(slot)}: ${value};`)
    .join("\n");
}

function kebab(value: string): string {
  return value.replace(/[A-Z]/g, (character) => `-${character.toLowerCase()}`);
}
