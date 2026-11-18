import * as React from 'react'

import { cn } from '@/lib/utils'
import { Textarea as SakugaTextarea } from '@/sakuga/ui'

import { type ControlVariantProps } from './control'

type NativeTextareaProps = React.ComponentProps<'textarea'> & ControlVariantProps

/**
 * The shared multi-line control.
 *
 * The vendored Sakuga set has no textarea, so this is the Qubit-composed
 * `SakugaTextarea`: a real `<textarea>` in the Sakuga frame with the engine's
 * type/focus cues. The previous public API is preserved — callers keep passing
 * native props (`onChange` receives an event-shaped `{ target: { value } }`,
 * `onBlur`/`onKeyDown`/`onPaste` are attached to the inner element) and a ref
 * lands on the real textarea. The frame renders an empty label when the caller
 * supplies none; surrounding `<Field>`/`aria-label` keeps the accessible name.
 */
const Textarea = React.forwardRef<HTMLTextAreaElement, NativeTextareaProps>(
  function Textarea(
    {
      className,
      size: _size,
      style,
      onChange,
      onBlur,
      onFocus,
      onKeyDown,
      onPaste,
      ...props
    },
    ref,
  ) {
    const inner = React.useRef<HTMLTextAreaElement | null>(null)

    React.useEffect(() => {
      const el = inner.current
      if (!el) return
      // Attributes the composed control does not model, mirrored onto the real
      // textarea so existing code/config semantics are unchanged.
      el.setAttribute('spellcheck', String(props.spellCheck ?? false))
      el.setAttribute('autocapitalize', 'off')
      el.setAttribute('autocomplete', 'off')
      el.setAttribute('autocorrect', 'off')
      const blur = (event: Event) => onBlur?.(event as unknown as React.FocusEvent<HTMLTextAreaElement>)
      const focus = (event: Event) => onFocus?.(event as unknown as React.FocusEvent<HTMLTextAreaElement>)
      const keydown = (event: Event) => onKeyDown?.(event as unknown as React.KeyboardEvent<HTMLTextAreaElement>)
      const paste = (event: Event) => onPaste?.(event as unknown as React.ClipboardEvent<HTMLTextAreaElement>)
      if (onBlur) el.addEventListener('blur', blur)
      if (onFocus) el.addEventListener('focus', focus)
      if (onKeyDown) el.addEventListener('keydown', keydown)
      if (onPaste) el.addEventListener('paste', paste)
      return () => {
        if (onBlur) el.removeEventListener('blur', blur)
        if (onFocus) el.removeEventListener('focus', focus)
        if (onKeyDown) el.removeEventListener('keydown', keydown)
        if (onPaste) el.removeEventListener('paste', paste)
      }
    }, [onBlur, onFocus, onKeyDown, onPaste, props.spellCheck])

    return (
      <SakugaTextarea
        className={cn('min-h-16', className)}
        label={(props['aria-label'] as string | undefined) ?? ''}
        onChange={next =>
          onChange?.({ target: { value: next } } as unknown as React.ChangeEvent<HTMLTextAreaElement>)
        }
        ref={node => {
          inner.current = node
          if (typeof ref === 'function') ref(node)
          else if (ref) (ref as React.MutableRefObject<HTMLTextAreaElement | null>).current = node
        }}
        style={style as React.CSSProperties}
        {...(props as Record<string, unknown>)}
      />
    )
  },
)

export { Textarea }
