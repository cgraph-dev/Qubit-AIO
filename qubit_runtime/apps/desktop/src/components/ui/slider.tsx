import type { ChangeEvent, ComponentProps } from 'react'

import { Slider as SakugaSlider } from '@/sakuga/ui'

/**
 * The one range control, now the upstream Sakuga slider.
 *
 * Consumers keep the native-input call shape they always had (`onChange` gets
 * an event-like object with `target.value`), so a settings row does not have to
 * know which renderer draws the lever. The wrapper owns the drawing and the
 * real inner `<input type="range">`; this adapter only maps the value back into
 * the legacy event shape.
 */
export function Slider({
  className,
  style,
  onChange,
  value,
  defaultValue,
  min,
  max,
  step,
  disabled,
  name,
  'aria-label': ariaLabel,
  onKeyDown,
  onBlur,
  onFocus,
  id,
  title
}: Omit<ComponentProps<'input'>, 'type' | 'size'>) {
  const label = typeof ariaLabel === 'string' && ariaLabel.trim() ? ariaLabel : name || 'Slider'

  return (
    <SakugaSlider
      className={className}
      defaultValue={typeof defaultValue === 'number' || typeof defaultValue === 'string' ? defaultValue : undefined}
      disabled={disabled}
      label={label}
      max={typeof max === 'number' ? max : max === undefined ? undefined : Number(max)}
      min={typeof min === 'number' ? min : min === undefined ? undefined : Number(min)}
      name={name}
      onBlur={onBlur}
      onChange={next =>
        onChange?.({ target: { value: String(next) } } as ChangeEvent<HTMLInputElement>)
      }
      onFocus={onFocus}
      onKeyDown={onKeyDown}
      step={typeof step === 'number' ? step : step === undefined ? undefined : Number(step)}
      style={style}
      value={typeof value === 'number' || typeof value === 'string' ? value : undefined}
      id={id}
      title={title}
    />
  )
}
