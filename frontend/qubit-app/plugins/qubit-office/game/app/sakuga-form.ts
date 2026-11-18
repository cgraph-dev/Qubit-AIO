/* Sakuga form compositions for the Office host shell.
 *
 * The panel factories are React-free by convention (they receive the host `R`
 * and build elements with `R.createElement`), but the composed controls
 * (`SakugaSelect`, `SakugaInput`, `SakugaTextarea`, `SakugaHoverTooltip`) are
 * React wrappers. Importing them here is safe: the office bundle externalises
 * `react` to the host global (`vite.office-game.config.ts`), so there is still
 * exactly one React runtime. The adapter components below keep the callers'
 * native-shaped props (`onChange({ target: { value } })`, `data-testid`,
 * `aria-label`) so existing tests and selectors keep working.
 */
import { Children, createElement, isValidElement, type ReactNode } from "react";
import { SakugaInput } from "../../../../../qubit-shared/src/sakuga/elements";
import { SakugaSelect, type SakugaSelectOption } from "../../../../../qubit-shared/src/sakuga/select";
import {
  SakugaHoverTooltip,
  SakugaTextarea,
} from "../../../../../qubit-shared/src/sakuga/extras";

type NativeChange = { target: { value: string } };

function textOf(node: unknown): string {
  if (node === null || node === undefined || typeof node === "boolean") return "";
  if (Array.isArray(node)) return node.map(textOf).join("");
  if (typeof node === "object" && "props" in (node as Record<string, unknown>)) {
    return textOf((node as { props?: { children?: unknown } }).props?.children);
  }
  return String(node);
}

/** Flatten the `<option>` children callers already build into select options. */
function optionsFrom(children: ReactNode | undefined): SakugaSelectOption[] {
  const out: SakugaSelectOption[] = [];
  for (const child of Children.toArray(children)) {
    if (!isValidElement(child)) continue;
    const props = child.props as { value?: unknown; children?: unknown };
    const value = props.value === undefined ? textOf(props.children) : String(props.value);
    out.push({ value, label: textOf(props.children) || value });
  }
  return out;
}

export interface OfficeInputProps {
  id?: string;
  label?: string;
  value?: string | number;
  placeholder?: string;
  type?: string;
  disabled?: boolean;
  autoComplete?: string;
  autoFocus?: boolean;
  className?: string;
  style?: Record<string, unknown>;
  ref?: unknown;
  onChange?: (event: NativeChange) => void;
  onInput?: (event: NativeChange) => void;
  onKeyDown?: (event: unknown) => void;
  "aria-label"?: string;
  "aria-describedby"?: string;
  "data-testid"?: string;
}

/** A native-shaped text/search input rendered as a `SakugaInput`. */
export function OfficeInput(props: OfficeInputProps) {
  const {
    value, placeholder, type, disabled, autoComplete, autoFocus, className,
    style, ref, onChange, onInput, onKeyDown, id,
  } = props;
  const label = props.label ?? props["aria-label"] ?? placeholder ?? "Search";
  return createElement(SakugaInput, {
    type: type === "search" ? "search" : "text",
    label,
    placeholder,
    value: value === undefined || value === null ? "" : String(value),
    id,
    disabled,
    autoComplete,
    autoFocus,
    className,
    style,
    ref,
    "aria-label": props["aria-label"],
    "aria-describedby": props["aria-describedby"],
    "data-testid": props["data-testid"],
    onChange: (next: string) => {
      const event = { target: { value: next } };
      onChange?.(event);
      onInput?.(event);
    },
    onKeyDown,
  } as never);
}

export interface OfficeSelectProps {
  id?: string;
  label?: string;
  value?: string | number;
  disabled?: boolean;
  className?: string;
  style?: Record<string, unknown>;
  children?: ReactNode;
  placeholder?: string;
  onChange?: (event: NativeChange) => void;
  "aria-label"?: string;
  "data-testid"?: string;
}

/** A native-shaped `<select>` rendered as the composed `SakugaSelect`. */
export function OfficeSelect(props: OfficeSelectProps) {
  const {
    value, disabled, className, style, children, placeholder, onChange, id,
  } = props;
  return createElement(SakugaSelect, {
    label: props.label ?? props["aria-label"] ?? id ?? "Select",
    options: optionsFrom(children),
    value: value === undefined || value === null ? "" : String(value),
    id,
    disabled,
    className,
    style,
    placeholder,
    "data-testid": props["data-testid"],
    onChange: (next: string) => onChange?.({ target: { value: next } }),
  } as never);
}

export interface OfficeTextareaProps {
  id?: string;
  value?: string;
  rows?: number;
  disabled?: boolean;
  placeholder?: string;
  className?: string;
  label?: string;
  onChange?: (event: NativeChange) => void;
  "aria-label"?: string;
  "data-testid"?: string;
}

/** A native-shaped textarea rendered as the composed `SakugaTextarea`. */
export function OfficeTextarea(props: OfficeTextareaProps) {
  const { value, rows = 3, disabled, placeholder, className, onChange, id } = props;
  return createElement(SakugaTextarea, {
    label: props.label || props["aria-label"] || "Text",
    value,
    rows,
    id,
    disabled,
    placeholder,
    className,
    onChange: (next: string) => onChange?.({ target: { value: next } }),
    "data-testid": props["data-testid"],
  } as never);
}

export interface OfficeTooltipProps {
  content: ReactNode;
  label?: string;
  placement?: "top" | "bottom";
  className?: string;
  children?: ReactNode;
}

/** The hover/focus/tap bubble around an arbitrary host-shell trigger. */
export function OfficeTooltip(props: OfficeTooltipProps) {
  return createElement(SakugaHoverTooltip, props as never);
}
