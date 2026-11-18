/* One import surface for the Sakuga controls in the Electron renderer.
 *
 * The wrappers themselves live in `@qubit/shared/sakuga` (mirrored from
 * `frontend/qubit-shared`). This file only renames them and adds the small
 * desktop-specific mapping helpers so a future wrapper change lands in one
 * place. It contains no drawing code and no palette of its own — the upstream
 * `--sk-*` colours are used unchanged.
 */
export {
  SakugaButton as Button,
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
  configureSakuga,
  emitFx,
  emitFxAt,
  fireFx,
  installFxBridge,
  loadSakugaComponent,
  loadSakugaCore,
  loadSakugaComponents,
  onFx,
  sakugaBase,
  showSakugaToast,
  clickSakuga,
  sakugaInner,
  sakugaInnerAll,
  sakugaShadow,
  sakugaValue,
  typeIntoSakuga,
  waitForSakuga,
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
} from '@qubit/shared/sakuga'

import { emitFxAt, type FxCue } from '@qubit/shared/sakuga'

/**
 * Map the renderer's own button variants onto the two upstream looks.
 * `default`/`primary` are the filled look; every quiet variant is the
 * secondary look. Unknown variants fall back to secondary.
 */
export type SakugaButtonVariant = 'primary' | 'secondary'
export function sakugaButtonVariant(variant: string | undefined | null): SakugaButtonVariant {
  return variant === 'default' || variant === 'primary' || variant == null ? 'primary' : 'secondary'
}

/** Fire a cue from a DOM `currentTarget` without letting presentation throw. */
export function cueAt(cue: FxCue | false | undefined, target: Element | null): void {
  if (!cue) return
  void emitFxAt(cue, target)
}
