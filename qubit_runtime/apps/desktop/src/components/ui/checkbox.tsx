import { Checkbox as CheckboxPrimitive } from 'radix-ui'
import * as React from 'react'

import { Codicon } from '@/components/ui/codicon'
import { cn } from '@/lib/utils'
import { Checkbox as SakugaCheckbox } from '@/sakuga/ui'

// Upstream has checked/unchecked only. The indeterminate state is a tri-state
// affordance the vendored checkbox does not draw, so it keeps the native
// control (documented exception) instead of silently flattening to checked.
function NativeCheckbox({ className, ...props }: React.ComponentProps<typeof CheckboxPrimitive.Root>) {
  return (
    <CheckboxPrimitive.Root
      className={cn(
        'group peer size-4 shrink-0 rounded-sm border border-input shadow-xs outline-none transition-shadow focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/50 disabled:cursor-not-allowed disabled:opacity-50 data-[state=checked]:border-primary data-[state=checked]:bg-primary data-[state=checked]:text-primary-foreground data-[state=indeterminate]:border-primary data-[state=indeterminate]:bg-primary data-[state=indeterminate]:text-primary-foreground aria-invalid:border-destructive aria-invalid:ring-destructive/20 dark:aria-invalid:ring-destructive/40',
        className
      )}
      data-slot="checkbox"
      {...props}
    >
      <CheckboxPrimitive.Indicator
        className="flex items-center justify-center text-current"
        data-slot="checkbox-indicator"
      >
        <Codicon className="hidden! group-data-[state=checked]:block!" name="check" size="0.875rem" />
        <Codicon className="hidden! group-data-[state=indeterminate]:block!" name="dash" size="0.875rem" />
      </CheckboxPrimitive.Indicator>
    </CheckboxPrimitive.Root>
  )
}

function Checkbox({
  checked,
  onCheckedChange,
  disabled,
  className,
  children,
  ...props
}: React.ComponentProps<typeof CheckboxPrimitive.Root>) {
  if (checked === 'indeterminate') {
    return (
      <NativeCheckbox checked={checked} className={className} disabled={disabled} onCheckedChange={onCheckedChange} {...props} />
    )
  }

  return (
    <SakugaCheckbox
      checked={Boolean(checked)}
      className={className}
      disabled={disabled}
      onChange={value => onCheckedChange?.(value)}
      {...(props as Record<string, unknown>)}
    >
      {children}
    </SakugaCheckbox>
  )
}

export { Checkbox }
