import * as React from 'react'

import { cn } from '@/lib/utils'
import { Input as SakugaInput, focusSakuga } from '@/sakuga/ui'

import { type ControlVariantProps, controlVariants } from './control'

// `prefix`/`suffix` are DOM string attributes on the native element; we shadow
// them with ReactNode adornments, so they're omitted from the base props.
type InputProps = Omit<React.ComponentProps<'input'>, 'size' | 'prefix' | 'suffix'> &
  ControlVariantProps & {
    /** Leading adornment rendered inside the field (e.g. a `$` for money). */
    prefix?: React.ReactNode
    /** Trailing adornment rendered inside the field (e.g. a unit label). */
    suffix?: React.ReactNode
    /** Applied to the wrapper when an adornment promotes the field to a group. */
    containerClassName?: string
  }

// The upstream field renders a real text input inside a shadow root, so only
// the text-like types are supported. Everything else (numbers, dates, files,
// hidden fields, colour swatches) keeps the native element: the upstream has
// no equivalent and silently demoting them would change their behaviour.
const SAKUGA_TYPES = new Set(['email', 'password', 'search', 'tel', 'text', 'url'])

const Input = React.forwardRef<HTMLElement, InputProps>(function Input(
  { className, containerClassName, prefix, suffix, size, type = 'text', onChange, placeholder, autoFocus, ...props },
  ref
) {
  const grouped = prefix != null || suffix != null
  const hostRef = React.useRef<HTMLElement | null>(null)

  const setRefs = React.useCallback(
    (node: HTMLElement | null) => {
      hostRef.current = node
      if (typeof ref === 'function') ref(node)
      else if (ref) (ref as React.MutableRefObject<HTMLElement | null>).current = node
    },
    [ref]
  )

  // Mirror the host-level attributes the shared wrapper does not carry. The
  // placeholder is on the inner native input; `getByPlaceholderText` and plain
  // DOM lookups expect it on the element the rest of the tree can see.
  React.useLayoutEffect(() => {
    const el = hostRef.current
    if (!el) return
    if (placeholder != null) el.setAttribute('placeholder', placeholder)
    else el.removeAttribute('placeholder')
    if (type) el.setAttribute('type', type)
  }, [placeholder, type])

  React.useEffect(() => {
    if (autoFocus) focusSakuga(hostRef.current)
  }, [autoFocus])

  // `fireEvent.change(host, …)` and the upstream component's own `change`
  // dispatch both land on the host, not the inner native input. Keep the
  // event-shaped `onChange` contract every call site already uses.
  const emitChange = React.useCallback(
    (value: string) => {
      onChange?.({
        currentTarget: { value },
        target: { value }
      } as unknown as React.ChangeEvent<HTMLInputElement>)
    },
    [onChange]
  )

  React.useEffect(() => {
    const el = hostRef.current
    if (!el) return
    const handler = () => {
      const value = (el as unknown as { value?: string }).value ?? ''
      emitChange(value)
    }
    el.addEventListener('change', handler)
    return () => el.removeEventListener('change', handler)
  }, [emitChange])

  const label =
    (props['aria-label'] as string | undefined) ?? placeholder ?? (props.id as string | undefined) ?? 'Text'

  if (!SAKUGA_TYPES.has(type)) {
    return (
      <input
        autoCapitalize="off"
        autoComplete="off"
        autoCorrect="off"
        className={cn(controlVariants({ size }), className)}
        data-slot="input"
        placeholder={placeholder}
        ref={ref as React.Ref<HTMLInputElement>}
        spellCheck={false}
        type={type}
        onChange={onChange}
        {...props}
      />
    )
  }

  const field = (
    <SakugaInput
      aria-label={label}
      aria-labelledby={props.id ? `${props.id}-label` : undefined}
      autoComplete={props.autoComplete}
      data-slot="input"
      className={cn(grouped ? 'min-w-0 flex-1' : undefined, className)}
      defaultValue={props.defaultValue as string | undefined}
      disabled={props.disabled}
      label={label}
      id={props.id}
      name={props.name}
      onBlur={props.onBlur}
      onChange={emitChange}
      onFocus={props.onFocus}
      onKeyDown={props.onKeyDown}
      placeholder={placeholder}
      ref={setRefs}
      style={props.style}
      title={props.title}
      type={type as 'email' | 'password' | 'search' | 'tel' | 'text' | 'url'}
      value={props.value === undefined ? undefined : String(props.value)}
      {...(props as Record<string, unknown>)}
    />
  )

  if (!grouped) {
    return field
  }

  return (
    <div
      className={cn(
        controlVariants({ size }),
        'inline-flex items-center gap-1',
        props.disabled && 'cursor-not-allowed opacity-50',
        containerClassName
      )}
      data-slot="input-group"
    >
      {prefix != null && (
        <span className="pointer-events-none shrink-0 select-none text-muted-foreground">{prefix}</span>
      )}
      {field}
      {suffix != null && (
        <span className="pointer-events-none shrink-0 select-none text-muted-foreground">{suffix}</span>
      )}
    </div>
  )
})

export { Input }
