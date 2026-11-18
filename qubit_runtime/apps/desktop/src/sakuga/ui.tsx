/* One import surface for the Sakuga controls in the Electron renderer.
 *
 * The wrappers themselves live in `@qubit/shared/sakuga` (mirrored from
 * `frontend/qubit-shared`). This file only renames them and adds the small
 * desktop-specific mapping helpers so a future wrapper change lands in one
 * place. It contains no drawing code and no palette of its own — the upstream
 * `--sk-*` colours are used unchanged.
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

import { emitFxAt, loadSakugaComponent, type FxCue } from '@qubit/shared/sakuga'

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


/**
 * Focus a Sakuga control's real inner native control.
 *
 * The host is a custom element and is not itself focusable; `element.focus()`
 * on the host does nothing. Anything that used to keep a `useRef` on a native
 * `<button>`/`<input>` must call this instead so focus still lands (dialogs
 * that open focused on a confirm button, search fields, …).
 */
export function focusSakuga(host: Element | null | undefined): void {
  if (!host) return
  const root = (host as HTMLElement).shadowRoot
  const target = root?.querySelector<HTMLElement>('button, input, [tabindex]')
  ;(target ?? (host as HTMLElement)).focus()
}

/* Backwards-compatible Button.
 *
 * The renderer's previous Button accepted `variant`, `size` and sometimes
 * `asChild`. The sweep is therefore an import swap: unknown/quiet variants map
 * to the secondary look, `size` is ignored (the upstream control has one size),
 * and `asChild` renders the supplied child (links stay links).
 *
 * Unlike the shared wrapper this one forwards every native prop straight to
 * the custom element, because desktop buttons carry drag/pointer/double-click
 * handlers the shared host whitelist would otherwise drop. */
import {
  cloneElement,
  createElement,
  forwardRef,
  isValidElement,
  useEffect,
  useLayoutEffect,
  useRef,
  type ComponentPropsWithoutRef,
  type ReactElement,
  type ReactNode
} from 'react'

export interface QubitButtonProps extends Omit<ComponentPropsWithoutRef<'button'>, 'color'> {
  variant?: string | null
  size?: string
  asChild?: boolean
  /** Lands the upstream pink check. Call only when the action worked. */
  done?: boolean
  /** Effect fired on press. Defaults to `impact`. */
  fx?: FxCue | false
  children?: ReactNode
}

function mergeClass(a: string | undefined, b: string | undefined): string | undefined {
  if (!a) return b
  if (!b) return a
  return `${a} ${b}`
}

export const Button = forwardRef<HTMLElement, QubitButtonProps>(function Button(
  {
    variant,
    size: _size,
    asChild,
    done,
    fx = 'impact',
    children,
    className,
    disabled,
    onClick,
    type = 'button',
    ...rest
  },
  ref
) {
  const host = useRef<HTMLElement | null>(null)

  useEffect(() => {
    void loadSakugaComponent('button').catch(() => {
      // A failed component must leave the rest of the page usable; the host
      // still carries its label/role and ordinary clicks still work.
    })
  }, [])

  useLayoutEffect(() => {
    const el = host.current
    if (!el) return
    if (type && type !== 'button') el.setAttribute('type', type)
    else el.removeAttribute('type')
  }, [type])

  useEffect(() => {
    if (!done) return
    ;(host.current as unknown as { done?: () => void } | null)?.done?.()
  }, [done])

  if (asChild && isValidElement(children)) {
    const child = children as ReactElement<{ className?: string }>
    return cloneElement(child, {
      className: mergeClass(className, child.props.className),
      ...rest
    } as Record<string, unknown>)
  }

  const setRef = (node: HTMLElement | null) => {
    host.current = node
    if (typeof ref === 'function') ref(node)
    else if (ref) (ref as { current: HTMLElement | null }).current = node
  }

  return createElement(
    'sakuga-button',
    {
      ...rest,
      ref: setRef,
      class: className,
      role: (rest as { role?: string }).role ?? 'button',
      disabled: disabled || undefined,
      variant: sakugaButtonVariant(variant),
      onClick: (event: React.MouseEvent<HTMLElement>) => {
        if (fx) void emitFxAt(fx, event.currentTarget)
        onClick?.(event as unknown as React.MouseEvent<HTMLButtonElement>)
      }
    },
    children
  )
})
