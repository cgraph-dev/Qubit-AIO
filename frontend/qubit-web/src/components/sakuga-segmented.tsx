/* A horizontal segmented filter built from the Sakuga button.
 *
 * The upstream `sakuga-radio` is a one-column list, so swapping it into a
 * toolbar changes the layout and the key model. This keeps the compact row
 * shape (the design-system `Segmented` it replaces) while every segment is a
 * real Sakuga button, so the press reaction is unchanged.
 *
 * Accessibility is the standard radio-group contract: the host is a
 * `radiogroup`, each segment is `role="radio"` with `aria-checked`, only the
 * checked segment is in the tab order, and the arrow / Home / End keys move
 * both selection and focus (wrapping at the ends).
 */
import type { KeyboardEvent } from "react";
import { Button } from "@/sakuga/ui";
import { cn } from "@/lib/utils";
import "./sakuga-segmented.css";

export interface SakugaSegmentedOption<T extends string = string> {
  value: T;
  label: string;
  disabled?: boolean;
}

export interface SakugaSegmentedProps<T extends string = string> {
  options: readonly SakugaSegmentedOption<T>[];
  value: T;
  onChange: (value: T) => void;
  className?: string;
  size?: "sm" | "md";
  disabled?: boolean;
  "aria-label"?: string;
  "aria-labelledby"?: string;
}

function segmentsOf(event: KeyboardEvent<HTMLElement>): HTMLElement[] {
  const group = event.currentTarget.parentElement;
  if (!group) return [];
  return Array.from(group.querySelectorAll<HTMLElement>('[role="radio"]:not([disabled])'));
}

export function SakugaSegmented<T extends string>({
  options,
  value,
  onChange,
  className,
  size = "sm",
  disabled = false,
  "aria-label": ariaLabel,
  "aria-labelledby": ariaLabelledby,
}: SakugaSegmentedProps<T>) {
  const choose = (next: HTMLElement | undefined) => {
    const nextValue = next?.dataset.value as T | undefined;
    if (!next || !nextValue) return;
    onChange(nextValue);
    next.focus();
  };

  const step = (event: KeyboardEvent<HTMLElement>, delta: 1 | -1) => {
    if (disabled) return;
    const segments = segmentsOf(event);
    if (segments.length === 0) return;
    const at = segments.indexOf(event.currentTarget as HTMLElement);
    choose(segments[(at + delta + segments.length) % segments.length]);
  };

  const onKeyDown = (event: KeyboardEvent<HTMLElement>, optionDisabled: boolean) => {
    if (disabled || optionDisabled) return;
    switch (event.key) {
      case "ArrowRight":
      case "ArrowDown":
        event.preventDefault();
        step(event, 1);
        break;
      case "ArrowLeft":
      case "ArrowUp":
        event.preventDefault();
        step(event, -1);
        break;
      case "Home": {
        event.preventDefault();
        const segments = segmentsOf(event);
        choose(segments[0]);
        break;
      }
      case "End": {
        event.preventDefault();
        const segments = segmentsOf(event);
        choose(segments[segments.length - 1]);
        break;
      }
      default:
        break;
    }
  };

  return (
    <div
      role="radiogroup"
      aria-label={ariaLabel}
      aria-labelledby={ariaLabelledby}
      className={cn("qsk-segmented", `qsk-segmented--${size}`, className)}
    >
      {options.map((option) => {
        const active = option.value === value;
        const optionDisabled = disabled || option.disabled;
        return (
          <Button
            key={option.value}
            variant={active ? "primary" : "secondary"}
            role="radio"
            {...({ "aria-checked": active ? "true" : "false" } as Record<string, string>)}
            {...({ "data-value": option.value } as Record<string, string>)}
            tabIndex={active ? 0 : -1}
            disabled={optionDisabled}
            onClick={() => {
              if (!active && !optionDisabled) onChange(option.value);
            }}
            onKeyDown={(event) => onKeyDown(event, Boolean(optionDisabled))}
          >
            {option.label}
          </Button>
        );
      })}
    </div>
  );
}
