import type { DashboardTheme, ThemeTypography, ThemeLayout } from "./types";

/**
 * Built-in dashboard themes.
 *
 * Each theme defines its own palette, typography, and layout so switching
 * themes produces visible changes beyond just color — fonts, density, and
 * corner-radius all shift to match the theme's personality.
 *
 * Theme ids are Qubit-owned (`q1`..`q6`); the picker shows Q1..Q6. Every
 * theme ships a "-L" large variant (Q1-L .. Q6-L) that keeps the same look
 * with a bigger root size and roomier spacing. Theme names must stay in sync
 * with the backend's `_BUILTIN_DASHBOARD_THEMES` list in
 * `qubit_cli/web_server_dashboard.py`.
 *
 * `default` is the canonical first theme and keeps its id (it is the
 * fallback in `resolveTheme`, the boot splash, and the persisted default);
 * it is presented as Q1.
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

/** Q1 — the canonical deep-teal look and the dashboard default. */
export const defaultTheme: DashboardTheme = {
  name: "default",
  label: "Q1",
  description: "Q1 — deep teal canvas with warm cream ink and an amber glow",
  palette: {
    background: { hex: "#041c1c", alpha: 1 },
    midground: { hex: "#ffe6cb", alpha: 1 },
    foreground: { hex: "#ffffff", alpha: 0 },
    warmGlow: "rgba(255, 189, 56, 0.35)",
    noiseOpacity: 1,
  },
  typography: DEFAULT_TYPOGRAPHY,
  layout: DEFAULT_LAYOUT,
  terminalBackground: "#000000",
};

/** Q2 — light mode: vivid blue accents on a pale canvas. */
export const qubitBlueTheme: DashboardTheme = {
  name: "q2",
  label: "Q2",
  description: "Q2 — pale blue canvas with a saturated blue accent",
  palette: {
    background: { hex: "#E8F2FD", alpha: 1 },
    midground: { hex: "#0053FD", alpha: 1 },
    foreground: { hex: "#170d02", alpha: 0 },
    warmGlow: "rgba(0, 83, 253, 0.12)",
    noiseOpacity: 0,
  },
  typography: DEFAULT_TYPOGRAPHY,
  layout: DEFAULT_LAYOUT,
  terminalBackground: "#f5f8fc",
  terminalForeground: "#170d02",
  seriesColors: {
    inputTokenAccent: "#001934",
    outputTokenAccent: "#0053fd",
  },
  swatchColors: ["#170d02", "#0053FD", "#E8F2FD"],
};

/** Q3 — deep blue-violet night canvas. */
export const midnightTheme: DashboardTheme = {
  name: "q3",
  label: "Q3",
  description: "Q3 — near-black navy canvas with a lilac tint and violet glow",
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

/** Q4 — grayscale, square corners. */
export const monoTheme: DashboardTheme = {
  name: "q4",
  label: "Q4",
  description: "Q4 — neutral greyscale canvas with square corners and no accent hue",
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

/** Q5 — high-chroma terminal look, monospace throughout. */
export const cyberpunkTheme: DashboardTheme = {
  name: "q5",
  label: "Q5",
  description: "Q5 — black canvas, monospace throughout, bright mint signal colour",
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

/** Q6 — maximum-contrast accessibility preset. */
export const contrastTheme: DashboardTheme = {
  name: "q6",
  label: "Q6",
  description: "Q6 — pure black canvas, pure white ink, maximum contrast",
  palette: {
    background: { hex: "#000000", alpha: 1 },
    midground: { hex: "#e8ff8f", alpha: 1 },
    foreground: { hex: "#ffffff", alpha: 0 },
    warmGlow: "rgba(232, 255, 143, 0.22)",
    noiseOpacity: 0,
  },
  typography: {
    ...DEFAULT_TYPOGRAPHY,
    lineHeight: "1.6",
  },
  layout: {
    ...DEFAULT_LAYOUT,
    radius: "0.25rem",
  },
  colorOverrides: {
    success: "#a5df7b",
    warning: "#f0c56b",
    destructive: "#ff8178",
  },
};

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
    description: `Q${num}-L — the Q${num} look with larger type and roomier spacing`,
    typography: { ...base.typography, ...LARGE_TYPOGRAPHY },
    layout: { ...base.layout, ...LARGE_LAYOUT },
  };
}

export const bigDefaultTheme = largeVariant(defaultTheme, "q1-l", 1);
export const bigQubitBlueTheme = largeVariant(qubitBlueTheme, "q2-l", 2);
export const bigMidnightTheme = largeVariant(midnightTheme, "q3-l", 3);
export const bigMonoTheme = largeVariant(monoTheme, "q4-l", 4);
export const bigCyberpunkTheme = largeVariant(cyberpunkTheme, "q5-l", 5);
export const bigContrastTheme = largeVariant(contrastTheme, "q6-l", 6);

export const BUILTIN_THEMES: Record<string, DashboardTheme> = {
  default: defaultTheme,
  "q1-l": bigDefaultTheme,
  q2: qubitBlueTheme,
  "q2-l": bigQubitBlueTheme,
  q3: midnightTheme,
  "q3-l": bigMidnightTheme,
  q4: monoTheme,
  "q4-l": bigMonoTheme,
  q5: cyberpunkTheme,
  "q5-l": bigCyberpunkTheme,
  q6: contrastTheme,
  "q6-l": bigContrastTheme,
};
