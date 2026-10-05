/**
 * React Native portability contract for the shared surface components.
 *
 * Framework-neutral: this module imports NOTHING (no react-native, no react).
 * It maps the token module onto plain style objects whose keys/properties ARE
 * React Native style names (`borderRadius`, `paddingHorizontal`, `minHeight`,
 * `fontSize`, ...). A future RN app does:
 *
 *   import { StyleSheet } from "react-native";
 *   import { rnStyles } from "@qubit/shared/muse/styles.rn-contract";
 *   const styles = StyleSheet.create(rnStyles(rnColors));
 *
 * where `rnColors` is a `SurfaceColors` resolved from the same Qubit skin the
 * web console uses. Because both surfaces read the SAME token geometry, the
 * two implementations stay in lockstep — the only per-platform code is the
 * primitive (View/Text/Pressable/Modal vs div/span/button).
 */
import { radii, sizes, spacing, typography, type SurfaceColors } from "./tokens";

/** RN-compatible style object (subset of ViewStyle/TextStyle we use). */
export type RnStyle = {
  [key: string]: string | number | undefined;
};

/** Per-slot React Native style map for every shared component. */
export function rnStyles(colors: SurfaceColors): Record<string, RnStyle> {
  return {
    text: {
      color: colors.text,
      fontSize: typography.text.fontSize,
      lineHeight: typography.text.lineHeight,
    },
    muted: {
      color: colors.muted,
      fontSize: typography.muted.fontSize,
      lineHeight: typography.muted.lineHeight,
    },
    small: {
      color: colors.muted,
      fontSize: typography.small.fontSize,
      lineHeight: typography.small.lineHeight,
    },
    label: {
      color: colors.muted,
      fontSize: typography.label.fontSize,
      fontWeight: typography.label.fontWeight,
      letterSpacing: typography.label.letterSpacing,
      textTransform: "uppercase",
    },
    title: {
      color: colors.text,
      fontSize: typography.title.fontSize,
      fontWeight: typography.title.fontWeight,
      letterSpacing: typography.title.letterSpacing,
    },
    heading: {
      color: colors.text,
      fontSize: typography.heading.fontSize,
      fontWeight: typography.heading.fontWeight,
      letterSpacing: typography.heading.letterSpacing,
    },
    button: {
      flexDirection: "row",
      alignItems: "center",
      justifyContent: "center",
      gap: spacing.buttonGap,
      paddingHorizontal: spacing.buttonPaddingX,
      minHeight: sizes.buttonMinHeight,
      paddingVertical: spacing.buttonPaddingY,
      borderRadius: radii.button,
    },
    buttonSmall: {
      minHeight: sizes.buttonSmallMinHeight,
      paddingVertical: spacing.buttonSmallPaddingY,
      paddingHorizontal: spacing.buttonSmallPaddingX,
    },
    buttonText: { fontSize: typography.buttonText.fontSize, fontWeight: typography.buttonText.fontWeight },
    iconButton: {
      width: sizes.iconButton,
      height: sizes.iconButton,
      alignItems: "center",
      justifyContent: "center",
      borderRadius: radii.iconButton,
      backgroundColor: colors.card,
    },
    card: {
      backgroundColor: colors.card,
      borderRadius: radii.card,
      borderWidth: 1,
      borderColor: colors.line,
      padding: spacing.cardPadding,
    },
    chip: {
      paddingHorizontal: spacing.chipPaddingX,
      paddingVertical: spacing.chipPaddingY,
      borderRadius: radii.chip,
      alignSelf: "flex-start",
      backgroundColor: colors.canvas,
    },
    chipText: { fontSize: typography.chipText.fontSize, fontWeight: typography.chipText.fontWeight, color: colors.muted },
    field: { gap: spacing.fieldGap, marginBottom: spacing.fieldMarginBottom },
    input: {
      borderWidth: 1,
      borderColor: colors.line,
      borderRadius: radii.input,
      paddingHorizontal: spacing.inputPaddingX,
      paddingVertical: spacing.inputPaddingY,
      color: colors.text,
      fontSize: typography.input.fontSize,
      backgroundColor: colors.card,
      minHeight: sizes.inputMinHeight,
    },
    iconBox: {
      width: sizes.iconBox,
      height: sizes.iconBox,
      borderRadius: radii.iconBox,
      justifyContent: "center",
      alignItems: "center",
      backgroundColor: colors.accentTint,
    },
    emptyIconBox: {
      width: sizes.emptyIconBox,
      height: sizes.emptyIconBox,
      borderRadius: radii.emptyIconBox,
    },
    error: {
      padding: spacing.errorPadding,
      borderRadius: radii.error,
      backgroundColor: colors.dangerTint,
      marginVertical: spacing.errorMarginY,
      gap: spacing.errorGap,
    },
    sheet: {
      backgroundColor: colors.canvas,
      borderRadius: radii.sheet,
      width: "100%",
      maxWidth: sizes.sheetMaxWidth,
      maxHeight: sizes.sheetMaxHeight,
      borderWidth: 1,
      borderColor: colors.line,
    },
    checkbox: {
      width: sizes.checkbox,
      height: sizes.checkbox,
      borderRadius: radii.checkbox,
      borderWidth: 1,
      alignItems: "center",
      justifyContent: "center",
    },
    divider: { height: 1, backgroundColor: colors.line, marginVertical: spacing.dividerMarginY },
  };
}
