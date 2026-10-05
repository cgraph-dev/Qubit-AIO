/**
 * Qubit surface design tokens — the shared geometry/spacing/typography layer.
 *
 * OWNER RULING: keep the Qubit DARK theme, but adopt the pinned upstream
 * mobile surface's EXACT component shapes, spacing and radii. So this module
 * is the SOURCE OF TRUTH for *geometry* only (radii, paddings, sizes, gaps,
 * font sizes, letter-spacings, weights).
 *
 * COLOURS ARE NOT DEFINED HERE. Components must never hardcode the upstream
 * light palette. A platform implementation supplies a `SurfaceColors` object
 * resolved from the Qubit theme:
 *   • web  -> `web-colors.ts` reads the dashboard theme CSS custom properties
 *             (`--background-base`, `--midground-base`, `--foreground-base`,
 *             `--color-card`, ...) that ThemeProvider already emits.
 *   • RN   -> the future mobile app supplies the same shape from its own
 *             theme resolver (skins over JSON-RPC).
 *
 * Framework-neutral: no React / DOM / RN imports.
 */

/* ------------------------------------------------------------------ */
/* Geometry — ported 1:1 from the upstream surface's StyleSheet.       */
/* ------------------------------------------------------------------ */

/** Corner radii, in px. */
export const radii = {
  /** Card surface. */
  card: 23,
  /** Icon box. */
  iconBox: 13,
  /** Larger empty-state icon box. */
  emptyIconBox: 18,
  /** Text input. */
  input: 19,
  /** Button pill. */
  button: 24,
  /** Small button pill. */
  buttonSmall: 21,
  /** Chip pill. */
  chip: 20,
  /** Modal sheet. */
  sheet: 26,
  /** Icon button circle (44 / 2). */
  iconButton: 22,
  /** Checkbox. */
  checkbox: 5,
  /** Pressable link row. */
  linkRow: 10,
  /** Inline error notice. */
  error: 14,
  /** Sheet drag handle. */
  dragHandle: 3,
} as const;

/** Fixed element sizes, in px. */
export const sizes = {
  /** Icon box (default). */
  iconBox: 42,
  /** Empty-state icon box. */
  emptyIconBox: 55,
  /** Icon button hit target. */
  iconButton: 44,
  /** Minimum button height. */
  buttonMinHeight: 42,
  /** Minimum small-button height. */
  buttonSmallMinHeight: 38,
  /** Minimum input height. */
  inputMinHeight: 45,
  /** Multiline input minimum height. */
  inputMultilineMinHeight: 120,
  /** Checkbox. */
  checkbox: 19,
  /** Sheet drag handle. */
  dragHandleWidth: 34,
  /** Sheet drag handle. */
  dragHandleHeight: 4,
  /** Empty-state detail max width. */
  emptyDetailMaxWidth: 360,
  /** Modal sheet max width. */
  sheetMaxWidth: 790,
  /** Wide modal sheet max width. */
  sheetWideMaxWidth: 1050,
  /** Modal sheet max height. */
  sheetMaxHeight: "94%",
  /** Breakpoint under which the sheet slides up instead of fading. */
  compactBreakpoint: 600,
} as const;

/** Padding / spacing, in px. */
export const spacing = {
  /** Card inner padding. */
  cardPadding: 20,
  /** Input horizontal padding. */
  inputPaddingX: 16,
  /** Input vertical padding. */
  inputPaddingY: 12,
  /** Button horizontal padding. */
  buttonPaddingX: 17,
  /** Small button horizontal padding. */
  buttonSmallPaddingX: 13,
  /** Button vertical padding. */
  buttonPaddingY: 10,
  /** Small button vertical padding. */
  buttonSmallPaddingY: 7,
  /** Chip horizontal padding. */
  chipPaddingX: 10,
  /** Chip vertical padding. */
  chipPaddingY: 4,
  /** Gap between a field label and its input. */
  fieldGap: 7,
  /** Field bottom margin. */
  fieldMarginBottom: 16,
  /** Button icon/label gap. */
  buttonGap: 8,
  /** Row gap (icon buttons, link rows, check rows). */
  rowGap: 10,
  /** Link row internal gap. */
  linkRowGap: 14,
  /** Link row vertical padding. */
  linkRowPaddingY: 13,
  /** Check row vertical padding. */
  checkRowPaddingY: 9,
  /** Empty state outer padding. */
  emptyPadding: 40,
  /** Empty state internal gap. */
  emptyGap: 13,
  /** Error notice padding. */
  errorPadding: 16,
  /** Error notice vertical margin. */
  errorMarginY: 10,
  /** Error notice internal gap. */
  errorGap: 4,
  /** Divider vertical margin. */
  dividerMarginY: 18,
  /** Sheet header/body padding (wide layout). */
  sheetPadding: 24,
  /** Sheet header/body padding (compact layout). */
  sheetPaddingCompact: 20,
  /** Section heading bottom margin. */
  sectionHeadingMarginBottom: 19,
  /** Section heading action gap. */
  sectionActionGap: 5,
  /** Link row text gap. */
  linkRowTextGap: 3,
  /** Sheet drag handle top margin. */
  dragHandleMarginTop: 10,
  /** Modal shade padding. */
  modalShadePadding: 20,
  /** Header text gap. */
  sheetTitleGap: 4,
} as const;

/** Font metrics, in px (line-heights as number px; weights as strings). */
export const typography = {
  text: { fontSize: 15, lineHeight: 23, fontWeight: "400" },
  muted: { fontSize: 14, lineHeight: 21, fontWeight: "400" },
  small: { fontSize: 11, lineHeight: 17, fontWeight: "400" },
  label: { fontSize: 10, letterSpacing: 1.4, fontWeight: "700" },
  title: { fontSize: 23, fontWeight: "600", letterSpacing: -0.7 },
  heading: { fontSize: 16, fontWeight: "600", letterSpacing: -0.25 },
  buttonText: { fontSize: 14, fontWeight: "600" },
  buttonTextSmall: { fontSize: 13, fontWeight: "600" },
  chipText: { fontSize: 10, fontWeight: "600" },
  linkRowTitleWeight: "500",
  fieldLabelWeight: "600",
  input: { fontSize: 16, fontWeight: "400" },
} as const;

/** Z-order for the modal shade. */
export const zIndex = {
  modalShade: 1000,
} as const;

/* ------------------------------------------------------------------ */
/* Colour contract — implemented per platform from the Qubit theme.    */
/* ------------------------------------------------------------------ */

/**
 * The colour slots every ported component reads. An implementation maps the
 * Qubit dark theme onto these slots; no component reaches past this shape.
 */
export interface SurfaceColors {
  /** App canvas behind cards. */
  canvas: string;
  /** Card / raised surface. */
  card: string;
  /** Primary text. */
  text: string;
  /** Secondary / dimmed text. */
  muted: string;
  /** Hairline borders and dividers. */
  line: string;
  /** Primary accent (buttons, active chrome). */
  accent: string;
  /** Strong or hovered form of `accent`. */
  accentStrong: string;
  /** Accent tint background (icon boxes, subtle fills). */
  accentTint: string;
  /** On-accent text/icon colour. */
  onAccent: string;
  /** Neutral button background (secondary buttons). */
  neutral: string;
  /** Inline icon and text colour on `neutral` and card surfaces. */
  icon: string;
  /** Hairline colour seen when a surface is pressed. */
  pressed: string;
  /** Destructive text/icon colour. */
  danger: string;
  /** Destructive tint background (error notice). */
  dangerTint: string;
  /** Run status/positive tint. */
  successTint: string;
  /** Sheet/row drag-handle grey. */
  handle: string;
  /** Modal scrim colour. */
  scrim: string;
  /** Checkbox tick colour drawn on the checked box. */
  onCheck: string;
  /** The "moon" tint for want-to tones. */
  accentTintAlt: string;
}

/** The upstream reference light palette (documentation/fixtures only). */
export const upstreamReferenceColors: SurfaceColors = {
  canvas: "#FCFCFC",
  card: "#FFFFFF",
  text: "#11191C",
  muted: "#697176",
  line: "#EEEEF0",
  accent: "#C8E7FF",
  accentStrong: "#1473C8",
  accentTint: "#EDF7FD",
  onAccent: "#11191C",
  neutral: "#F1F2F3",
  icon: "#11191C",
  pressed: "#EEEEF0",
  danger: "#AA4A45",
  dangerTint: "#FBEFED",
  successTint: "#E3F3E8",
  handle: "#D8DBDE",
  scrim: "rgba(35,48,44,0.25)",
  onCheck: "#FFFFFF",
  accentTintAlt: "#F0EEFA",
};
