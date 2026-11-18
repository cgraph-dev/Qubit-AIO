import { Switch as SwitchPrimitive } from 'radix-ui'
import * as React from 'react'

import { Toggle as SakugaToggle } from '@/sakuga/ui'

type SwitchProps = React.ComponentProps<typeof SwitchPrimitive.Root> & {
  /** Accessible name. Upstream calls it a label; the toggle draws no text. */
  label?: string
  /** Accepted for API compatibility; the upstream toggle has one size. */
  size?: string
}

/**
 * The app's switch, rendered as the vendored Sakuga toggle.
 *
 * `checked`/`onCheckedChange` keep the Radix names so no call site changes.
 * The upstream toggle has one size, so `size` is accepted and ignored.
 */
function Switch({ checked, onCheckedChange, disabled, className, label, ...props }: SwitchProps) {
  const accessible = label ?? (props['aria-label'] as string | undefined) ?? 'Toggle'

  return (
    <SakugaToggle
      checked={Boolean(checked)}
      className={className}
      disabled={disabled}
      label={accessible}
      onChange={value => onCheckedChange?.(value)}
      {...(props as Record<string, unknown>)}
    />
  )
}

export { Switch }
