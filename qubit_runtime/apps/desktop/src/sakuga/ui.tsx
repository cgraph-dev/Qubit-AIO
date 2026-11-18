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


/* Backwards-compatible Button.
 *
 * The renderer's previous Button accepted `variant`, `size` and sometimes
 * `asChild`. The sweep is therefore an import swap: unknown/quiet variants map
 * to the secondary look, `size` is ignored (the upstream control has one size),
 * and `asChild` renders the supplied child (links stay links).
 *
 * Radix triggers (`DropdownMenuTrigger asChild`) clone a Button and hand it
 * pointer/keydown/data-state props plus a ref. The adapter keeps those working:
 * the ref lands on the custom element, data-/aria- props are mirrored by the
 * shared wrapper, and the pointer/mouse handlers the shared wrapper does not
 * declare are attached natively. A `type="submit"` control also submits its
 * surrounding form — the shadow `<button>` is not form-associated, so a real
 * submit needs `requestSubmit()`. */
import {
  cloneElement,
  createElement,
  forwardRef,
  isValidElement,
  useImperativeHandle,
  useLayoutEffect,
  useMemo,
  useRef,
  type MouseEvent as ReactMouseEvent,
  type MouseEventHandler,
  type PointerEventHandler,
  type ReactElement,
  type ReactNode
} from 'react'
import { SakugaButton, type SakugaButtonProps } from '@qubit/shared/sakuga'

type DomHandler = (event: Event) => void

/** Events the shared wrapper does not forward; attach them to the host. */
const IMPERATIVE_EVENTS = {
  onMouseDown: 'mousedown',
  onMouseUp: 'mouseup',
  onMouseEnter: 'mouseenter',
  onMouseLeave: 'mouseleave',
  onPointerDown: 'pointerdown',
  onPointerMove: 'pointermove',
  onPointerCancel: 'pointercancel',
  onPointerUp: 'pointerup',
  onPointerEnter: 'pointerenter',
  onPointerLeave: 'pointerleave',
  onContextMenu: 'contextmenu',
  onDoubleClick: 'dblclick',
  onAuxClick: 'auxclick',
  onKeyUp: 'keyup'
} as const

type ImperativeEventName = keyof typeof IMPERATIVE_EVENTS

export interface QubitButtonProps extends Omit<SakugaButtonProps, 'variant' | 'value' | 'aria-hidden'> {
  variant?: string | null
  size?: string
  asChild?: boolean
  /** Working. The label stays; the control is disabled while the action runs. */
  loading?: boolean
  /** A form id for a submit control outside the form element. */
  form?: string
  value?: string | number | readonly string[]
  children?: ReactNode
  onMouseDown?: MouseEventHandler<HTMLElement>
  onMouseUp?: MouseEventHandler<HTMLElement>
  onMouseEnter?: MouseEventHandler<HTMLElement>
  onMouseLeave?: MouseEventHandler<HTMLElement>
  onPointerDown?: PointerEventHandler<HTMLElement>
  onPointerMove?: PointerEventHandler<HTMLElement>
  onPointerUp?: PointerEventHandler<HTMLElement>
  onPointerCancel?: PointerEventHandler<HTMLElement>
  onPointerEnter?: PointerEventHandler<HTMLElement>
  onPointerLeave?: PointerEventHandler<HTMLElement>
  onDoubleClick?: MouseEventHandler<HTMLElement>
  onAuxClick?: MouseEventHandler<HTMLElement>
  'aria-expanded'?: boolean | 'true' | 'false'
  'aria-controls'?: string
  'aria-haspopup'?: boolean | 'true' | 'false' | 'menu' | 'listbox' | 'tree' | 'grid' | 'dialog'
  'aria-busy'?: boolean | 'true' | 'false'
  'aria-hidden'?: boolean | 'true' | 'false'
  // Radix passes data-* state onto its trigger; the host mirrors it.
  [key: `data-${string}`]: unknown
}

export const Button = forwardRef<HTMLElement, QubitButtonProps>(function Button(props, ref) {
  const { variant, size: _size, asChild, loading, form, children, onClick, type, disabled, ...rest } = props
  const hostRef = useRef<HTMLElement | null>(null)
  useImperativeHandle(ref, () => hostRef.current as HTMLElement, [hostRef])

  const inert = Boolean(disabled || loading)
  const { imperative, forwarded } = useMemo(() => {
    const imperative: Partial<Record<ImperativeEventName, DomHandler>> = {}
    const forwarded: Record<string, unknown> = {}
    for (const [key, value] of Object.entries(rest)) {
      if (key in IMPERATIVE_EVENTS && typeof value === 'function') {
        imperative[key as ImperativeEventName] = value as DomHandler
      } else {
        // Custom elements receive boolean attributes as an empty string in
        // React 19; ARIA/data booleans must stay the literal "true"/"false"
        // because tests and CSS read their value, not their presence.
        forwarded[key] = (key.startsWith('aria-') || key.startsWith('data-')) && typeof value === 'boolean'
          ? String(value)
          : value
      }
    }
    return { imperative, forwarded }
  }, [rest])

  useLayoutEffect(() => {
    const element = hostRef.current
    if (!element) return
    const cleanups: Array<() => void> = []
    for (const [prop, event] of Object.entries(IMPERATIVE_EVENTS) as Array<[ImperativeEventName, string]>) {
      const handler = imperative[prop]
      if (!handler) continue
      const listener = (event_: Event) => {
        if (inert) return
        handler(event_)
      }
      element.addEventListener(event, listener)
      cleanups.push(() => element.removeEventListener(event, listener))
    }
    return () => {
      for (const cleanup of cleanups) cleanup()
    }
  })

  if (asChild && isValidElement(children)) {
    // Do not overwrite a child's own handler with `undefined`: cloneElement
    // treats an explicitly passed key as authoritative.
    const childProps: Record<string, unknown> = { ...forwarded }
    if (onClick) childProps.onClick = onClick
    if (inert) childProps['aria-disabled'] = 'true'
    return cloneElement(children as ReactElement<Record<string, unknown>>, childProps)
  }

  const handleClick = (event: ReactMouseEvent<HTMLElement>) => {
    if (inert) {
      event.preventDefault()
      return
    }
    onClick?.(event)
    if (type === 'submit' && !event.defaultPrevented) {
      // A click that starts inside the shadow button is already submitted by
      // the upstream component (button.js: type=submit -> requestSubmit), so
      // only submitting for a direct host click avoids a double submit.
      const path = (event.nativeEvent as Event).composedPath?.() ?? []
      const fromShadow = path.length > 0 && path[0] !== event.currentTarget
      if (!fromShadow) {
        const owner = event.currentTarget as HTMLElement
        const target = (form ? document.getElementById(form) : owner.closest('form')) as HTMLFormElement | null
        target?.requestSubmit?.()
      }
    }
  }

  return createElement(
    SakugaButton,
    {
      ...forwarded,
      tabIndex: (forwarded.tabIndex as number | undefined) ?? (inert ? -1 : 0),
      ref: hostRef,
      variant: sakugaButtonVariant(variant),
      type,
      disabled: inert,
      loading: undefined,
      'aria-disabled': inert ? 'true' : undefined,
      onClick: handleClick
    } as unknown as SakugaButtonProps,
    children
  )
})

/** Focus a custom element's host, the way a native control is focused.
 *
 * The upstream elements attach their shadow root with `delegatesFocus: true`,
 * so focusing the host forwards real keyboard focus into the shadow control
 * while `document.activeElement` still reports the host (the accessible
 * element the rest of the tree can query). Drilling into the shadow and
 * focusing the inner node directly bypasses that contract. */
export function focusSakuga(host: Element | null | undefined): void {
  if (!host) return
  ;(host as HTMLElement).focus()
}
