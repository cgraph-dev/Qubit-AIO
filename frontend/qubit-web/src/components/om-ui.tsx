/**
 * Agent-surface UI primitives — React + Tailwind port of the upstream
 * project's mobile `ui.tsx` render helpers (MIT, structural port only).
 *
 * Geometry is the authoritative part of this file: the upstream console's
 * card/button/input/chip/icon-box radii and paddings are reproduced EXACTLY,
 * while every colour routes through the Qubit dark theme tokens in
 * `src/index.css` (`--background-base`, `--midground`, `--foreground`, ...).
 * The upstream light palette is deliberately NOT ported.
 *
 * Upstream geometry (ui.tsx `s`):
 *   card      radius 23, padding 20
 *   input     radius 19, minHeight 45, padding 16/12
 *   button    radius 24, minHeight 42, padding 17/10, gap 8
 *   chip      radius 20, padding 10/4
 *   iconBox   42x42 radius 13
 *   sheet     radius 26, maxWidth 790 (wide 1050)
 *
 * `Button` mirrors the upstream disabled/busy contract: a `busy` or
 * `disabled` control sets `aria-disabled` + `data-state` and never fires.
 */
import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

/* ------------------------------------------------------------- geometry */

export const OM = {
  cardRadius: 23,
  cardPadding: 20,
  inputRadius: 19,
  inputMinHeight: 45,
  buttonRadius: 24,
  buttonMinHeight: 42,
  buttonSmallMinHeight: 38,
  chipRadius: 20,
  iconBox: 42,
  iconBoxRadius: 13,
  sheetRadius: 26,
  sheetMaxWidth: 790,
  sheetWideMaxWidth: 1050,
} as const;

/* ------------------------------------------------------------------ card */

export function OmCard({
  children,
  className,
  tone = "card",
}: {
  children: ReactNode;
  className?: string;
  tone?: "card" | "raised" | "sky";
}) {
  const toneClass =
    tone === "raised" || tone === "sky" ? "bg-card" : "bg-background";
  return (
    <div
      className={cn(
        "border border-border text-foreground",
        toneClass,
        className,
      )}
      style={{ borderRadius: OM.cardRadius, padding: OM.cardPadding }}
    >
      {children}
    </div>
  );
}

/* ---------------------------------------------------------------- button */

export function OmButton({
  children,
  onClick,
  icon: Icon,
  primary = false,
  disabled = false,
  busy = false,
  small = false,
  danger = false,
  title,
  className,
  type = "button",
  ariaLabel,
}: {
  children: ReactNode;
  onClick?: () => void;
  icon?: React.ComponentType<{ size?: number; className?: string }>;
  primary?: boolean;
  disabled?: boolean;
  busy?: boolean;
  small?: boolean;
  danger?: boolean;
  title?: string;
  className?: string;
  type?: "button" | "submit";
  ariaLabel?: string;
}) {
  const inert = disabled || busy;
  const tone = danger
    ? "text-destructive border-destructive/40"
    : primary
      ? "text-primary-foreground bg-primary border-transparent"
      : "text-foreground bg-secondary border-border";
  return (
    <button
      type={type}
      onClick={inert ? undefined : onClick}
      disabled={inert}
      aria-disabled={inert || undefined}
      aria-busy={busy || undefined}
      aria-label={ariaLabel}
      title={title}
      data-state={inert ? "disabled" : "ready"}
      className={cn(
        "inline-flex items-center justify-center gap-2 border font-semibold transition-opacity",
        small ? "text-[11px]" : "text-[13px]",
        tone,
        inert ? "cursor-not-allowed opacity-50" : "hover:opacity-90",
        className,
      )}
      style={{
        borderRadius: OM.buttonRadius,
        minHeight: small ? OM.buttonSmallMinHeight : OM.buttonMinHeight,
        paddingInline: small ? 13 : 17,
        paddingBlock: small ? 7 : 10,
      }}
    >
      {busy ? (
        <span
          aria-hidden="true"
          className="inline-block size-[13px] animate-spin rounded-full border-2 border-current border-t-transparent"
        />
      ) : Icon ? (
        <Icon size={15} />
      ) : null}
      <span>{children}</span>
    </button>
  );
}

/* ----------------------------------------------------------------- field */

export function OmField({
  label,
  value,
  onChange,
  placeholder,
  multiline = false,
  mono = false,
  autoCapitalize,
  ariaLabel,
  disabled = false,
  minHeight,
  type = "text",
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  multiline?: boolean;
  mono?: boolean;
  autoCapitalize?: "none" | "sentences";
  ariaLabel?: string;
  disabled?: boolean;
  minHeight?: number;
  type?: "text" | "url" | "number";
}) {
  const shared = {
    value,
    placeholder,
    disabled,
    onChange: (
      e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>,
    ) => onChange(e.target.value),
    "aria-label": ariaLabel ?? label,
    autoCapitalize,
    className: cn(
      "w-full border border-border bg-background text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-primary",
      mono && "font-mono text-[13px]",
    ),
    style: {
      borderRadius: OM.inputRadius,
      paddingInline: 16,
      paddingBlock: 12,
      minHeight: minHeight ?? OM.inputMinHeight,
    },
  } as const;
  return (
    <label className="flex flex-col gap-[7px]">
      <span className="text-[11px] font-semibold text-foreground">
        {label}
      </span>
      {multiline ? (
        <textarea {...shared} rows={4} />
      ) : (
        <input {...shared} type={type} />
      )}
    </label>
  );
}

/* ------------------------------------------------------------------ chip */

export function OmChip({
  children,
  tone = "neutral",
}: {
  children: ReactNode;
  tone?: "neutral" | "sky" | "green" | "orange";
}) {
  const toneClass =
    tone === "green"
      ? "text-success"
      : tone === "orange"
        ? "text-warning"
        : tone === "sky"
          ? "text-primary"
          : "text-muted-foreground";
  return (
    <span
      className={cn(
        "self-start bg-secondary text-[10px] font-semibold",
        toneClass,
      )}
      style={{ borderRadius: OM.chipRadius, paddingInline: 10, paddingBlock: 4 }}
    >
      {children}
    </span>
  );
}

/* --------------------------------------------------------------- iconBox */

export function OmIconBox({
  children,
  className,
  size = OM.iconBox,
}: {
  children: ReactNode;
  className?: string;
  size?: number;
}) {
  return (
    <span
      aria-hidden="true"
      className={cn(
        "inline-flex shrink-0 items-center justify-center bg-secondary text-primary",
        className,
      )}
      style={{
        width: size,
        height: size,
        borderRadius: size === OM.iconBox ? OM.iconBoxRadius : Math.round(size / 3.2),
      }}
    >
      {children}
    </span>
  );
}

/* ------------------------------------------------------------ error/empty */

export function OmErrorNotice({ error }: { error?: string }) {
  if (!error) return null;
  return (
    <div
      role="alert"
      className="border border-destructive/40 bg-destructive/10 text-destructive"
      style={{ borderRadius: 14, padding: 16 }}
    >
      {error}
    </div>
  );
}

export function OmEmpty({
  icon: Icon,
  title,
  detail,
}: {
  icon?: React.ComponentType<{ size?: number; className?: string }>;
  title: string;
  detail: string;
}) {
  return (
    <div className="flex flex-col items-center gap-[13px] px-0 py-10 text-center">
      {Icon ? (
        <OmIconBox size={55}>
          <Icon size={24} />
        </OmIconBox>
      ) : null}
      <p className="text-[15px] font-semibold text-foreground">
        {title}
      </p>
      <p className="max-w-[360px] text-[13px] text-muted-foreground">
        {detail}
      </p>
    </div>
  );
}

/* -------------------------------------------------------------- link row */

export function OmLinkRow({
  icon: Icon,
  title,
  detail,
  onClick,
  disabled = false,
  disabledReason,
}: {
  icon: React.ComponentType<{ size?: number; className?: string }>;
  title: string;
  detail?: string;
  onClick?: () => void;
  disabled?: boolean;
  disabledReason?: string;
}) {
  return (
    <button
      type="button"
      onClick={disabled ? undefined : onClick}
      disabled={disabled}
      title={disabled ? disabledReason : undefined}
      aria-disabled={disabled || undefined}
      className={cn(
        "flex w-full items-center gap-[14px] rounded-[10px] px-0 py-[13px] text-left",
        disabled ? "cursor-not-allowed opacity-55" : "hover:bg-secondary",
      )}
    >
      <OmIconBox>
        <Icon size={19} />
      </OmIconBox>
      <span className="flex min-w-0 flex-1 flex-col gap-[3px]">
        <span className="truncate text-[14px] font-medium text-foreground">
          {title}
        </span>
        {detail ? (
          <span className="text-[11px] text-muted-foreground">
            {detail}
          </span>
        ) : null}
        {disabled && disabledReason ? (
          <span className="text-[11px] text-muted-foreground">
            {disabledReason}
          </span>
        ) : null}
      </span>
      <span aria-hidden="true" className="text-muted-foreground">
        ›
      </span>
    </button>
  );
}

/* ---------------------------------------------------------------- helpers */

export function omStatusLabel(value: string) {
  return value.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
}

export function omStamp(value?: string | number) {
  if (value === undefined || value === null || value === "") return "Not checked yet";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? String(value)
    : date.toLocaleString(undefined, {
        month: "short",
        day: "numeric",
        hour: "numeric",
        minute: "2-digit",
      });
}

export function omTimeLabel(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" });
}
