import type { DashboardTheme, ThemeTypography, ThemeLayout } from "./types";

/**
 * Built-in dashboard themes.
 *
 * Each theme defines its own palette, typography, and layout so switching
 * themes produces visible changes beyond just color — fonts, density, and
 * corner-radius all shift to match the theme's personality.
 *
 * Theme ids are Qubit-owned (`q2`..`q6`); the picker presents them in normal
 * order as Q1..Q4. Q1, Q1-L, Q5 and Q5-L were retired at owner request (the
 * old IDs still migrate when they are read). Every remaining theme ships a "-L" large variant (Q1-L .. Q4-L)
 * that keeps the same look with a bigger root size and roomier spacing. Theme names must stay in sync
 * with the backend's `_BUILTIN_DASHBOARD_THEMES` list in
 * `qubit_cli/web_server_dashboard.py`.
 *
 * The fallback is `midnightTheme` (internal q2, shown as Q1).
 */

// ---------------------------------------------------------------------------
// Shared typography / layout presets
// ---------------------------------------------------------------------------

/** Default system stack — neutral, safe fallback for every platform. */
const SYSTEM_SANS =
  'system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif';
const SYSTEM_MONO =
  'ui-monospace, "SF Mono", "Cascadia Mono", Menlo, Consolas, monospace';

const DEFAULT_TYPOGRAPHY: ThemeTypography = {
  fontSans: SYSTEM_SANS,
  fontMono: SYSTEM_MONO,
  baseSize: "15px",
  lineHeight: "1.55",
  letterSpacing: "0",
};

const DEFAULT_LAYOUT: ThemeLayout = {
  radius: "0.5rem",
  density: "comfortable",
};

/** The "large" treatment applied to every theme. */
const LARGE_TYPOGRAPHY: Partial<ThemeTypography> = {
  baseSize: "18px",
  lineHeight: "1.65",
};
const LARGE_LAYOUT: Partial<ThemeLayout> = {
  density: "spacious",
};

// ---------------------------------------------------------------------------
// Themes
// ---------------------------------------------------------------------------

/** Q2 — deep blue-violet night canvas. */
export const midnightTheme: DashboardTheme = {
  name: "q2",
  label: "Q1",
  description: "Near-black navy with lilac text and a violet glow",
  palette: {
    background: { hex: "#0a0a1f", alpha: 1 },
    midground: { hex: "#d4c8ff", alpha: 1 },
    foreground: { hex: "#ffffff", alpha: 0 },
    warmGlow: "rgba(167, 139, 250, 0.32)",
    noiseOpacity: 0.8,
  },
  typography: {
    ...DEFAULT_TYPOGRAPHY,
    fontSans: `"Inter", ${SYSTEM_SANS}`,
    fontMono: `"JetBrains Mono", ${SYSTEM_MONO}`,
    fontUrl:
      "https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;700&display=swap",
    letterSpacing: "-0.005em",
  },
  layout: {
    ...DEFAULT_LAYOUT,
    radius: "0.75rem",
  },
};

/** Q3 — grayscale, square corners. */
export const monoTheme: DashboardTheme = {
  name: "q3",
  label: "Q2",
  description: "Neutral grayscale with square corners",
  palette: {
    background: { hex: "#0e0e0e", alpha: 1 },
    midground: { hex: "#eaeaea", alpha: 1 },
    foreground: { hex: "#ffffff", alpha: 0 },
    warmGlow: "rgba(255, 255, 255, 0.1)",
    noiseOpacity: 0.6,
  },
  typography: {
    ...DEFAULT_TYPOGRAPHY,
    fontSans: `"IBM Plex Sans", ${SYSTEM_SANS}`,
    fontMono: `"IBM Plex Mono", ${SYSTEM_MONO}`,
    fontUrl:
      "https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap",
  },
  layout: {
    ...DEFAULT_LAYOUT,
    radius: "0",
  },
};

/** Q4 — high-chroma terminal look, monospace throughout. */
export const cyberpunkTheme: DashboardTheme = {
  name: "q4",
  label: "Q3",
  description: "Black with monospace text and bright mint accents",
  palette: {
    background: { hex: "#040608", alpha: 1 },
    midground: { hex: "#9bffcf", alpha: 1 },
    foreground: { hex: "#ffffff", alpha: 0 },
    warmGlow: "rgba(0, 255, 136, 0.22)",
    noiseOpacity: 1.2,
  },
  typography: {
    ...DEFAULT_TYPOGRAPHY,
    fontSans: `"Share Tech Mono", "JetBrains Mono", ${SYSTEM_MONO}`,
    fontMono: `"Share Tech Mono", "JetBrains Mono", ${SYSTEM_MONO}`,
    fontUrl:
      "https://fonts.googleapis.com/css2?family=Share+Tech+Mono&family=JetBrains+Mono:wght@400;700&display=swap",
  },
  layout: {
    ...DEFAULT_LAYOUT,
    radius: "0",
  },
  colorOverrides: {
    success: "#00ff88",
    warning: "#ffd700",
    destructive: "#ff0055",
  },
};

/** Q6 — the opt-in manga skin as a first-class theme. Selecting Q6 (or
 *  Q6-L) enables the scoped `html[data-qubit-manga]` visual layer; selecting
 *  any other theme turns it back off. The palette below is the fallback for
 *  the brief frame before the layer applies and for the theme-picker swatch. */
export const mangaTheme: DashboardTheme = {
  name: "q6",
  label: "Q4",
  description:
    "Manga — magenta frames, deep navy surfaces, and yellow accents",
  palette: {
    background: { hex: "#08061a", alpha: 1 },
    midground: { hex: "#f7e9ff", alpha: 1 },
    foreground: { hex: "#ffffff", alpha: 0 },
    warmGlow: "rgba(255, 38, 154, 0.42)",
    noiseOpacity: 1,
  },
  typography: {
    ...DEFAULT_TYPOGRAPHY,
    fontSans: `"Inter", ${SYSTEM_SANS}`,
    fontMono: `"JetBrains Mono", ${SYSTEM_MONO}`,
    fontDisplay: '"Rajdhani", "Arial Narrow", "Segoe UI", system-ui, sans-serif',

    letterSpacing: "0.002em",
  },
  layout: {
    ...DEFAULT_LAYOUT,
    radius: "3px",
    density: "comfortable",
  },
  colorOverrides: {
    success: "#4ade80",
    warning: "#ffe500",
    destructive: "#ff4d80",
  },
  swatchColors: ["#08061a", "#ff269a", "#ffe500"],
  terminalBackground: "#08061a",
  terminalForeground: "#f7e9ff",
};

export const MANGA_THEME_NAME = "q6";
export const MANGA_THEME_LARGE_NAME = "q6-l";

/** Theme ids retired at owner request. They are filtered out of a served
 *  list even if an older/rolling backend still returns them. */
export const RETIRED_THEME_IDS: ReadonlySet<string> = new Set([
  "default",
  "q1",
  "q1-l",
  "q5",
  "q5-l",
  "qubit",
]);

export function isRetiredThemeName(name: string | null | undefined): boolean {
  return Boolean(name && RETIRED_THEME_IDS.has(name));
}

/** True for the manga base and large theme ids. */
export function isMangaThemeName(name: string | null | undefined): boolean {
  return name === MANGA_THEME_NAME || name === MANGA_THEME_LARGE_NAME;
}

// ---------------------------------------------------------------------------
// Large variants — same look, bigger type and roomier spacing
// ---------------------------------------------------------------------------

/**
 * Derive a theme's large variant. The palette and every other look-defining
 * field are inherited by reference so a base-theme edit flows into its large
 * sibling; only typography, layout, and the Q-number label change.
 */
function largeVariant(base: DashboardTheme, name: string, num: number): DashboardTheme {
  return {
    ...base,
    name,
    label: `Q${num}-L`,
    description: "Larger text and roomier spacing",
    typography: { ...base.typography, ...LARGE_TYPOGRAPHY },
    layout: { ...base.layout, ...LARGE_LAYOUT },
  };
}

export const bigMidnightTheme = largeVariant(midnightTheme, "q2-l", 1);
export const bigMonoTheme = largeVariant(monoTheme, "q3-l", 2);
export const bigCyberpunkTheme = largeVariant(cyberpunkTheme, "q4-l", 3);
export const bigMangaTheme = largeVariant(mangaTheme, "q6-l", 4);

export const BUILTIN_THEMES: Record<string, DashboardTheme> = {
  q2: midnightTheme,
  "q2-l": bigMidnightTheme,
  q3: monoTheme,
  "q3-l": bigMonoTheme,
  q4: cyberpunkTheme,
  "q4-l": bigCyberpunkTheme,
  q6: mangaTheme,
  "q6-l": bigMangaTheme,
};
