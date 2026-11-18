/* One import surface for the Sakuga controls in the dashboard.
 *
 * Pages import from here rather than from `@qubit/shared/sakuga` directly, so
 * a future decision (a wrapper API change, a themed variant) has one place to
 * land. The wrappers are the shared layer; this file adds no behaviour.
 */
export {
  SakugaCheckbox as Checkbox,
  SakugaDate as DateField,
  SakugaFile as FileField,
  SakugaFormRow as FormRow,
  SakugaInput as Input,
  SakugaLoading,
  SakugaMultiSelect as MultiSelect,
  SakugaNumber as NumberField,
  SakugaPagination as Pagination,
  SakugaProgress as Progress,
  SakugaRadio as Radio,
  SakugaRating as Rating,
  SakugaSelect as Select,
  SakugaSlider as Slider,
  SakugaTabs as Tabs,
  SakugaTextarea as Textarea,
  SakugaTime as TimeField,
  SakugaToastHost,
  SakugaToggle as Toggle,
  SakugaTooltip as Tooltip,
  emitFx,
  emitFxAt,
  fireFx,
  installFxBridge,
  loadSakugaComponent,
  loadSakugaCore,
  showSakugaToast,
  type FxCue,
  type SakugaButtonProps,
  type SakugaCheckboxProps,
  type SakugaDateTimeProps,
  type SakugaFileProps,
  type SakugaFormRowProps,
  type SakugaInputProps,
  type SakugaMultiSelectOption,
  type SakugaMultiSelectProps,
  type SakugaNumberProps,
  type SakugaPaginationProps,
  type SakugaTextareaProps,
  SakugaHoverTooltip as HoverTooltip,
  type SakugaHoverTooltipProps,
  type SakugaLoadingProps,
  type SakugaProgressProps,
  type SakugaRadioOption,
  type SakugaRadioProps,
  type SakugaRatingProps,
  type SakugaSelectOption,
  type SakugaSelectProps,
  type SakugaSliderProps,
  type SakugaTabOption,
  type SakugaTabsProps,
  type SakugaToggleProps,
  type SakugaTooltipProps,
} from '@qubit/shared/sakuga';

/* Backwards-compatible Button.
 *
 * The dashboard's previous `@nous-research/ui` Button accepted `variant`
 * strings, `size` and `asChild`. The sweep can therefore be an import swap:
 * unknown/quiet variants map to the secondary look, sizes are ignored because
 * the upstream control has one size, and `asChild` renders the supplied child
 * (links stay links) instead of wrapping it in a button. */
import { cloneElement, createElement, forwardRef, isValidElement, type ReactElement, type ReactNode } from 'react';
import { SakugaButton, type SakugaButtonProps } from '@qubit/shared/sakuga';

export type SakugaButtonVariant = 'primary' | 'secondary';

export function sakugaButtonVariant(variant: string | undefined | null): SakugaButtonVariant {
  return variant === 'default' || variant === 'primary' || variant == null ? 'primary' : 'secondary';
}

export interface QubitButtonProps extends Omit<SakugaButtonProps, 'variant'> {
  variant?: string | null;
  size?: string;
  asChild?: boolean;
  children?: ReactNode;
}

export function Button({ variant, size: _size, asChild, children, ...rest }: QubitButtonProps) {
  if (asChild && isValidElement(children)) {
    return cloneElement(children as ReactElement<Record<string, unknown>>, rest as Record<string, unknown>);
  }
  return createElement(SakugaButton, { ...rest, variant: sakugaButtonVariant(variant) }, children);
}

/* Backwards-compatible ListItem.
 *
 * `@nous-research/ui`'s ListItem is a styled native <button>. Replacing it with
 * the same Sakuga Button keeps every call site's props (active/disabled/ref)
 * and gives the row the anime press reaction; the stylesheet flattens the host
 * so dense lists keep their layout. */
export interface QubitListItemProps extends QubitButtonProps {
  active?: boolean;
}

export const ListItem = forwardRef<HTMLElement, QubitListItemProps>(function ListItem(
  { active, className, children, type = 'button', ...rest },
  ref,
) {
  const ariaCurrent =
    active === undefined
      ? (rest as { 'aria-current'?: string })['aria-current']
      : active
        ? 'true'
        : undefined;
  return createElement(
    Button,
    {
      ...rest,
      ref,
      type,
      variant: 'secondary',
      className: ['qsk-list-item', active ? 'is-active' : '', className].filter(Boolean).join(' '),
      'data-active': active || undefined,
      'aria-current': ariaCurrent,
    } as QubitButtonProps,
    children,
  );
});
