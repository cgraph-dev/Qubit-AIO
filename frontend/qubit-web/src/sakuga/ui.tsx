/* One import surface for the Sakuga controls in the dashboard.
 *
 * Pages import from here rather than from `@qubit/shared/sakuga` directly, so
 * a future decision (a wrapper API change, a themed variant) has one place to
 * land. The wrappers are the shared layer; this file adds no behaviour.
 */
export {
  SakugaCheckbox as Checkbox,
  SakugaInput as Input,
  SakugaProgress as Progress,
  SakugaRadio as Radio,
  SakugaRating as Rating,
  SakugaSelect as Select,
  SakugaSlider as Slider,
  SakugaTabs as Tabs,
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
  type SakugaInputProps,
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
import { cloneElement, createElement, isValidElement, type ReactElement, type ReactNode } from 'react';
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
