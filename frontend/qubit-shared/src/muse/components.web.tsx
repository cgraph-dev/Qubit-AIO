/**
 * Qubit shared surface components — web implementation.
 *
 * Ported from the pinned upstream mobile surface's `ui.tsx`, with that
 * surface's EXACT geometry (see `./tokens`) but the Qubit DARK theme's
 * colours (see `./web-colors`). Every size/radius/padding below reads from
 * the token module, never a literal, so an RN implementation can mirror this
 * file against the same tokens.
 *
 * Depends only on `react` + `lucide-react` (both already in the web app) and
 * the tokens; it does NOT import from `qubit-web`, so the future RN app can
 * swap this file for its own without touching shared logic.
 */
import { ArrowUpRight, Check, ChevronRight, type LucideIcon, X } from "lucide-react";
import {
  type CSSProperties,
  type InputHTMLAttributes,
  type ReactNode,
  useEffect,
  useState,
} from "react";
import {
  radii,
  sizes,
  spacing,
  typography,
  zIndex,
  type SurfaceColors,
} from "./tokens";
import { webSurfaceColors } from "./web-colors";

/* ------------------------------------------------------------------ */
/* Shared style fragments                                              */
/* ------------------------------------------------------------------ */

const row: CSSProperties = { display: "flex", flexDirection: "row", alignItems: "center" };
const between: CSSProperties = { ...row, justifyContent: "space-between" };

/** Base text styles mirroring the upstream `s.text` / `s.muted` / `s.small`. */
export const textStyle = (colors: SurfaceColors): CSSProperties => ({
  color: colors.text,
  fontSize: typography.text.fontSize,
  lineHeight: `${typography.text.lineHeight}px`,
});
export const mutedTextStyle = (colors: SurfaceColors): CSSProperties => ({
  color: colors.muted,
  fontSize: typography.muted.fontSize,
  lineHeight: `${typography.muted.lineHeight}px`,
});
export const smallTextStyle = (colors: SurfaceColors): CSSProperties => ({
  color: colors.muted,
  fontSize: typography.small.fontSize,
  lineHeight: `${typography.small.lineHeight}px`,
});
export const labelStyle = (colors: SurfaceColors): CSSProperties => ({
  color: colors.muted,
  fontSize: typography.label.fontSize,
  fontWeight: typography.label.fontWeight as CSSProperties["fontWeight"],
  letterSpacing: typography.label.letterSpacing,
  textTransform: "uppercase",
});
export const titleStyle = (colors: SurfaceColors): CSSProperties => ({
  color: colors.text,
  fontSize: typography.title.fontSize,
  fontWeight: typography.title.fontWeight as CSSProperties["fontWeight"],
  letterSpacing: typography.title.letterSpacing,
});
export const headingStyle = (colors: SurfaceColors): CSSProperties => ({
  color: colors.text,
  fontSize: typography.heading.fontSize,
  fontWeight: typography.heading.fontWeight as CSSProperties["fontWeight"],
  letterSpacing: typography.heading.letterSpacing,
});

/* ------------------------------------------------------------------ */
/* Button                                                              */
/* ------------------------------------------------------------------ */

export interface ButtonProps {
  children: ReactNode;
  onClick: () => void;
  icon?: LucideIcon;
  /** Primary (accent) vs secondary (neutral) styling. */
  primary?: boolean;
  disabled?: boolean;
  /** Shows a spinner and blocks interaction. */
  busy?: boolean;
  small?: boolean;
  danger?: boolean;
  style?: CSSProperties;
  colors?: SurfaceColors;
  type?: "button" | "submit";
}

/** Pill button with spinner, pressed scale 0.98 and disabled dimming. */
export function Button({
  children,
  onClick,
  icon: Icon,
  primary,
  disabled,
  busy,
  small,
  danger,
  style,
  colors = webSurfaceColors,
  type = "button",
}: ButtonProps) {
  const [pressed, setPressed] = useState(false);
  const color = danger ? colors.danger : colors.text;
  const blocked = disabled || busy;
  return (
    <button
      type={type}
      disabled={blocked}
      aria-busy={busy || undefined}
      onClick={onClick}
      onPointerDown={() => setPressed(true)}
      onPointerUp={() => setPressed(false)}
      onPointerLeave={() => setPressed(false)}
      style={{
        ...row,
        justifyContent: "center",
        gap: spacing.buttonGap,
        paddingInline: small ? spacing.buttonSmallPaddingX : spacing.buttonPaddingX,
        minHeight: small ? sizes.buttonSmallMinHeight : sizes.buttonMinHeight,
        paddingBlock: small ? spacing.buttonSmallPaddingY : spacing.buttonPaddingY,
        borderRadius: small ? radii.buttonSmall : radii.button,
        border: "none",
        cursor: blocked ? "default" : "pointer",
        background: primary ? colors.accent : colors.neutral,
        color,
        opacity: blocked ? 0.5 : 1,
        transform: pressed && !blocked ? "scale(0.98)" : undefined,
        transition: "transform 90ms ease, opacity 90ms ease",
        ...style,
      }}
    >
      {busy ? (
        <Spinner color={color} />
      ) : Icon ? (
        <Icon size={15} color={color} />
      ) : null}
      <span
        style={{
          color,
          fontSize: small ? typography.buttonTextSmall.fontSize : typography.buttonText.fontSize,
          fontWeight: typography.buttonText.fontWeight as CSSProperties["fontWeight"],
        }}
      >
        {children}
      </span>
    </button>
  );
}

/* ------------------------------------------------------------------ */
/* IconButton                                                          */
/* ------------------------------------------------------------------ */

export interface IconButtonProps {
  icon: LucideIcon;
  label: string;
  onClick: () => void;
  colors?: SurfaceColors;
  style?: CSSProperties;
}

/** 44x44 circular icon button; fills with the pressed tone on hover-down. */
export function IconButton({ icon: Icon, label, onClick, colors = webSurfaceColors, style }: IconButtonProps) {
  const [pressed, setPressed] = useState(false);
  return (
    <button
      type="button"
      aria-label={label}
      onClick={onClick}
      onPointerDown={() => setPressed(true)}
      onPointerUp={() => setPressed(false)}
      onPointerLeave={() => setPressed(false)}
      style={{
        width: sizes.iconButton,
        height: sizes.iconButton,
        minWidth: sizes.iconButton,
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
        borderRadius: radii.iconButton,
        border: "none",
        cursor: "pointer",
        background: pressed ? colors.pressed : colors.card,
        transition: "background 90ms ease",
        ...style,
      }}
    >
      <Icon size={20} strokeWidth={1.8} color={colors.text} />
    </button>
  );
}

/* ------------------------------------------------------------------ */
/* Card                                                                */
/* ------------------------------------------------------------------ */

export interface CardProps {
  children: ReactNode;
  style?: CSSProperties;
  className?: string;
  colors?: SurfaceColors;
}

/** Card surface: radius 23, padding 20, hairline border. */
export function Card({ children, style, className, colors = webSurfaceColors }: CardProps) {
  return (
    <div
      className={className}
      style={{
        background: colors.card,
        borderRadius: radii.card,
        border: `1px solid ${colors.line}`,
        padding: spacing.cardPadding,
        ...style,
      }}
    >
      {children}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Chip                                                                */
/* ------------------------------------------------------------------ */

export interface ChipProps {
  children: ReactNode;
  /** Optional explicit background tint (a token colour). */
  tint?: string;
  colors?: SurfaceColors;
  style?: CSSProperties;
}

/** Small status pill: radius 20, uppercase-free 10px text. */
export function Chip({ children, tint, colors = webSurfaceColors, style }: ChipProps) {
  return (
    <span
      style={{
        display: "inline-flex",
        alignSelf: "flex-start",
        paddingInline: spacing.chipPaddingX,
        paddingBlock: spacing.chipPaddingY,
        borderRadius: radii.chip,
        background: tint || colors.canvas,
        ...style,
      }}
    >
      <span
        style={{
          fontSize: typography.chipText.fontSize,
          fontWeight: typography.chipText.fontWeight as CSSProperties["fontWeight"],
          color: colors.muted,
        }}
      >
        {children}
      </span>
    </span>
  );
}

/* ------------------------------------------------------------------ */
/* Field                                                               */
/* ------------------------------------------------------------------ */

export interface FieldProps extends InputHTMLAttributes<HTMLInputElement> {
  label: string;
  colors?: SurfaceColors;
}

/** Label + text input: radius 19, min height 45, 16px text. */
export function Field({ label, colors = webSurfaceColors, style, ...props }: FieldProps) {
  return (
    <label style={{ display: "flex", flexDirection: "column", gap: spacing.fieldGap, marginBottom: spacing.fieldMarginBottom }}>
      <span
        style={{
          fontSize: typography.small.fontSize,
          fontWeight: typography.fieldLabelWeight as CSSProperties["fontWeight"],
          color: colors.text,
        }}
      >
        {label}
      </span>
      <input
        aria-label={label}
        {...props}
        style={{
          border: `1px solid ${colors.line}`,
          borderRadius: radii.input,
          paddingInline: spacing.inputPaddingX,
          paddingBlock: spacing.inputPaddingY,
          color: colors.text,
          fontSize: typography.input.fontSize,
          background: colors.card,
          minHeight: sizes.inputMinHeight,
          outline: "none",
          ...style,
        }}
      />
    </label>
  );
}

/* ------------------------------------------------------------------ */
/* Empty                                                               */
/* ------------------------------------------------------------------ */

export interface EmptyProps {
  icon: LucideIcon;
  title: string;
  detail: string;
  children?: ReactNode;
  colors?: SurfaceColors;
}

/** Centered empty state: 55x55 icon box, radius 18. */
export function Empty({ icon: Icon, title, detail, children, colors = webSurfaceColors }: EmptyProps) {
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        padding: spacing.emptyPadding,
        gap: spacing.emptyGap,
      }}
    >
      <div
        style={{
          width: sizes.emptyIconBox,
          height: sizes.emptyIconBox,
          borderRadius: radii.emptyIconBox,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          background: colors.accentTint,
        }}
      >
        <Icon size={24} color={colors.accentStrong} />
      </div>
      <div style={headingStyle(colors)}>{title}</div>
      <div
        style={{
          ...mutedTextStyle(colors),
          textAlign: "center",
          maxWidth: sizes.emptyDetailMaxWidth,
        }}
      >
        {detail}
      </div>
      {children}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* ErrorNotice                                                         */
/* ------------------------------------------------------------------ */

export interface ErrorNoticeProps {
  error?: string;
  colors?: SurfaceColors;
}

/** Inline error banner. Renders nothing when `error` is falsy. */
export function ErrorNotice({ error, colors = webSurfaceColors }: ErrorNoticeProps) {
  if (!error) return null;
  return (
    <div
      role="alert"
      style={{
        padding: spacing.errorPadding,
        borderRadius: radii.error,
        background: colors.dangerTint,
        marginBlock: spacing.errorMarginY,
        display: "flex",
        flexDirection: "column",
        gap: spacing.errorGap,
      }}
    >
      <span style={{ ...textStyle(colors), color: colors.danger }}>{error}</span>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Sheet                                                               */
/* ------------------------------------------------------------------ */

export interface SheetProps {
  title: string;
  subtitle?: string;
  children: ReactNode;
  onClose: () => void;
  /** Wider sheet (max width 1050 instead of 790). */
  wide?: boolean;
  colors?: SurfaceColors;
}

/**
 * Modal sheet: fades in on wide viewports, slides up from the bottom under
 * 600px with a drag handle. Radius 26, width 100%, max width 790 (1050 wide).
 */
export function Sheet({ title, subtitle, children, onClose, wide, colors = webSurfaceColors }: SheetProps) {
  const compact = useCompactViewport();
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div
      onClick={onClose}
      style={{
        position: "fixed",
        inset: 0,
        zIndex: zIndex.modalShade,
        display: "flex",
        justifyContent: compact ? "flex-end" : "center",
        alignItems: compact ? "stretch" : "center",
        padding: compact ? 0 : spacing.modalShadePadding,
        background: colors.scrim,
        animation: "fade-in 160ms ease",
        flexDirection: "column",
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label={title}
        onClick={(event) => event.stopPropagation()}
        style={{
          background: colors.canvas,
          borderRadius: radii.sheet,
          width: "100%",
          maxWidth: wide ? sizes.sheetWideMaxWidth : sizes.sheetMaxWidth,
          maxHeight: sizes.sheetMaxHeight,
          overflow: "hidden",
          display: "flex",
          flexDirection: "column",
          border: `1px solid ${colors.line}`,
          margin: compact ? "0" : "0 auto",
          ...(compact
            ? {
                borderBottomLeftRadius: 0,
                borderBottomRightRadius: 0,
                animation: "slide-up 220ms ease",
              }
            : null),
        }}
      >
        {compact && (
          <div
            style={{
              alignSelf: "center",
              width: sizes.dragHandleWidth,
              height: sizes.dragHandleHeight,
              borderRadius: radii.dragHandle,
              background: colors.handle,
              marginTop: spacing.dragHandleMarginTop,
              flexShrink: 0,
            }}
          />
        )}
        <div
          style={{
            ...between,
            padding: compact ? spacing.sheetPaddingCompact : spacing.sheetPadding,
            borderBottom: `1px solid ${colors.line}`,
            flexShrink: 0,
          }}
        >
          <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: spacing.sheetTitleGap }}>
            <div style={titleStyle(colors)}>{title}</div>
            {!!subtitle && <div style={mutedTextStyle(colors)}>{subtitle}</div>}
          </div>
          <IconButton icon={X} label="Close details" onClick={onClose} colors={colors} />
        </div>
        <div style={{ padding: compact ? spacing.sheetPaddingCompact : spacing.sheetPadding, overflowY: "auto" }}>
          {children}
        </div>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* CheckRow                                                            */
/* ------------------------------------------------------------------ */

export interface CheckRowProps {
  label: string;
  checked: boolean;
  onPress: () => void;
  colors?: SurfaceColors;
}

/** Checkbox row: 19x19 box (radius 5) + label, whole row pressable. */
export function CheckRow({ label, checked, onPress, colors = webSurfaceColors }: CheckRowProps) {
  return (
    <button
      type="button"
      role="checkbox"
      aria-checked={checked}
      onClick={onPress}
      style={{
        ...row,
        gap: spacing.rowGap,
        paddingBlock: spacing.checkRowPaddingY,
        width: "100%",
        border: "none",
        background: "transparent",
        cursor: "pointer",
        textAlign: "left",
      }}
    >
      <span
        style={{
          width: sizes.checkbox,
          height: sizes.checkbox,
          borderRadius: radii.checkbox,
          border: `1px solid ${checked ? colors.text : colors.line}`,
          background: checked ? colors.text : colors.card,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          flexShrink: 0,
        }}
      >
        {checked && <Check size={13} color={colors.onCheck} />}
      </span>
      <span style={{ ...textStyle(colors), flex: 1 }}>{label}</span>
    </button>
  );
}

/* ------------------------------------------------------------------ */
/* SectionHeading                                                      */
/* ------------------------------------------------------------------ */

export interface SectionHeadingProps {
  title: string;
  action?: string;
  onPress?: () => void;
  colors?: SurfaceColors;
}

/** Heading + optional text action with an ArrowUpRight affordance. */
export function SectionHeading({ title, action, onPress, colors = webSurfaceColors }: SectionHeadingProps) {
  return (
    <div style={{ ...between, marginBottom: spacing.sectionHeadingMarginBottom }}>
      <div style={headingStyle(colors)}>{title}</div>
      {action && onPress && (
        <button
          type="button"
          onClick={onPress}
          style={{
            ...row,
            gap: spacing.sectionActionGap,
            border: "none",
            background: "transparent",
            cursor: "pointer",
          }}
        >
          <span style={{ ...smallTextStyle(colors), color: colors.text }}>{action}</span>
          <ArrowUpRight size={13} color={colors.muted} />
        </button>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* LinkRow                                                             */
/* ------------------------------------------------------------------ */

export interface LinkRowProps {
  title: string;
  detail?: string;
  onPress: () => void;
  icon: LucideIcon;
  /** Icon box tint (a token colour); defaults to the accent tint. */
  tint?: string;
  colors?: SurfaceColors;
}

/** Icon box + title/detail + chevron. Fills faintly on hover/press. */
export function LinkRow({ title, detail, onPress, icon: Icon, tint, colors = webSurfaceColors }: LinkRowProps) {
  const [pressed, setPressed] = useState(false);
  return (
    <button
      type="button"
      onClick={onPress}
      onPointerDown={() => setPressed(true)}
      onPointerUp={() => setPressed(false)}
      onPointerLeave={() => setPressed(false)}
      style={{
        ...row,
        paddingBlock: spacing.linkRowPaddingY,
        gap: spacing.linkRowGap,
        borderRadius: radii.linkRow,
        width: "100%",
        border: "none",
        background: pressed ? colors.canvas : "transparent",
        cursor: "pointer",
        textAlign: "left",
      }}
    >
      <span
        style={{
          width: sizes.iconBox,
          height: sizes.iconBox,
          borderRadius: radii.iconBox,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          background: tint || colors.accentTint,
          flexShrink: 0,
        }}
      >
        <Icon size={19} color={colors.text} />
      </span>
      <span style={{ flex: 1, display: "flex", flexDirection: "column", gap: spacing.linkRowTextGap }}>
        <span
          style={{
            ...textStyle(colors),
            fontWeight: typography.linkRowTitleWeight as CSSProperties["fontWeight"],
          }}
        >
          {title}
        </span>
        {!!detail && <span style={smallTextStyle(colors)}>{detail}</span>}
      </span>
      <ChevronRight size={15} color={colors.muted} />
    </button>
  );
}

/* ------------------------------------------------------------------ */
/* Internal helpers                                                    */
/* ------------------------------------------------------------------ */

/** Rotating arc used by busy buttons. */
function Spinner({ color }: { color: string }) {
  return (
    <span
      aria-hidden="true"
      style={{
        width: 14,
        height: 14,
        borderRadius: "50%",
        border: `2px solid ${color}`,
        borderTopColor: "transparent",
        display: "inline-block",
        animation: "qubit-spin 700ms linear infinite",
      }}
    />
  );
}

/** True when the viewport is under the sheet's compact breakpoint. */
function useCompactViewport(): boolean {
  const query = `(max-width: ${sizes.compactBreakpoint - 1}px)`;
  const [compact, setCompact] = useState(() =>
    typeof window === "undefined" ? false : window.matchMedia(query).matches,
  );
  useEffect(() => {
    const list = window.matchMedia(query);
    const onChange = () => setCompact(list.matches);
    list.addEventListener("change", onChange);
    return () => list.removeEventListener("change", onChange);
  }, [query]);
  return compact;
}
