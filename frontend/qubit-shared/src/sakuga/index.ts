/* Qubit × Sakuga VFX.
 *
 * The vendored engine and components live in each app's `public/sakuga-vfx/`
 * directory (byte-identical to upstream pin c3dabb17, MIT). This module is the
 * typed, React-facing surface shared by the dashboard, Qubit Office and the
 * Electron renderer.
 */
export type {
  SakugaComponentName,
  SakugaEffectOptions,
  SakugaFxApi,
  SakugaPalette,
  SakugaSfxOptions,
  SakugaSpring,
  SakugaWord,
} from './types';

export {
  SAKUGA_COMPONENTS,
  configureSakuga,
  loadSakugaComponent,
  loadSakugaComponents,
  loadSakugaCore,
  sakugaBase,
  sakugaComponentReady,
  sakugaTag,
} from './loader';

export {
  emitFx,
  emitFxAt,
  fireFx,
  installFxBridge,
  onFx,
  type FxCue,
  type FxEventDetail,
  type FxOptions,
  type FxTone,
} from './fx';

export {
  SakugaButton,
  SakugaCheckbox,
  SakugaInput,
  SakugaLoading,
  SakugaProgress,
  SakugaRadio,
  SakugaRating,
  SakugaSlider,
  SakugaTabs,
  SakugaToastHost,
  SakugaToggle,
  SakugaTooltip,
  showSakugaToast,
  type SakugaButtonProps,
  type SakugaCheckboxProps,
  type SakugaHostProps,
  type SakugaInputProps,
  type SakugaLoadingProps,
  type SakugaProgressProps,
  type SakugaRadioOption,
  type SakugaRadioProps,
  type SakugaRatingProps,
  type SakugaSliderProps,
  type SakugaTabOption,
  type SakugaTabsProps,
  type SakugaToastOptions,
  type SakugaToggleProps,
  type SakugaTooltipProps,
} from './elements';

export {
  SakugaSelect,
  type SakugaSelectOption,
  type SakugaSelectProps,
} from './select';

export {
  SakugaDate,
  SakugaFile,
  SakugaFormRow,
  SakugaMultiSelect,
  SakugaNumber,
  SakugaPagination,
  SakugaTextarea,
  SakugaTime,
  type SakugaDateTimeProps,
  type SakugaFileProps,
  type SakugaFormRowProps,
  type SakugaMultiSelectOption,
  type SakugaMultiSelectProps,
  type SakugaNumberProps,
  type SakugaPaginationProps,
  type SakugaTextareaProps,
  SakugaHoverTooltip,
  type SakugaHoverTooltipProps,
} from './extras';

export {
  clickSakuga,
  sakugaInner,
  sakugaInnerAll,
  sakugaShadow,
  sakugaValue,
  typeIntoSakuga,
  waitForSakuga,
} from './test-helpers';
