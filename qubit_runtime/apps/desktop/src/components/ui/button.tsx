import { cva, type VariantProps } from 'class-variance-authority'
import * as React from 'react'

import { Loader2 } from '@/lib/icons'
import { cn } from '@/lib/utils'
import { Button as SakugaButton, emitFx, sakugaButtonVariant } from '@/sakuga/ui'

// Text+icon actions underline the label on hover, not the glyph.
const TEXT_ACTION_ICON = '[&_.codicon]:no-underline [&_svg]:no-underline'

// The variant table is kept for call sites that need the legacy classes (the
// link form rendered through `asChild`). The button itself is the vendored
// Sakuga control, which owns its own look.
const buttonVariants = cva(
  "inline-flex shrink-0 cursor-pointer items-center justify-center gap-1.5 rounded-[2.5px] text-xs leading-4 font-medium whitespace-nowrap shadow-none transition-all duration-100 outline-none focus-visible:border-ring focus-visible:ring-[0.1875rem] focus-visible:ring-ring/50 disabled:pointer-events-none disabled:cursor-default disabled:opacity-50 aria-invalid:border-destructive aria-invalid:ring-destructive/20 dark:aria-invalid:ring-destructive/40 [&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-3.5",
  {
    variants: {
      variant: {
        default: 'bg-primary text-primary-foreground hover:bg-primary/90',
        destructive:
          'bg-destructive text-white hover:bg-destructive/90 focus-visible:ring-destructive/20 dark:bg-destructive/60 dark:focus-visible:ring-destructive/40',
        outline:
          'bg-transparent text-(--ui-text-primary) shadow-[inset_0_0_0_1px_color-mix(in_srgb,var(--ui-stroke-secondary)_50%,transparent)] hover:bg-(--chrome-action-hover) hover:text-(--ui-text-primary)',
        secondary:
          'bg-(--ui-bg-quaternary) text-(--ui-text-primary) hover:bg-(--chrome-action-hover) hover:text-(--ui-text-primary)',
        ghost: 'text-(--ui-text-secondary) hover:bg-(--chrome-action-hover) hover:text-(--ui-text-primary)',
        grip: 'bg-transparent text-(--ui-text-tertiary) transition-colors hover:text-(--ui-text-secondary) focus-visible:text-(--ui-text-primary)',
        floating: 'bg-popover text-(--ui-text-secondary) shadow-md hover:text-(--ui-text-primary)',
        link: `text-primary underline-offset-4 decoration-current/20 hover:underline ${TEXT_ACTION_ICON}`,
        text: `text-muted-foreground underline-offset-4 hover:text-foreground hover:underline ${TEXT_ACTION_ICON}`,
        textStrong: `font-semibold text-muted-foreground underline underline-offset-4 hover:text-foreground ${TEXT_ACTION_ICON}`
      },
      size: {
        default: 'px-3 py-1.5 has-[>svg]:px-2.5',
        xs: "gap-1 px-2 py-0.5 text-[0.6875rem] leading-4 has-[>svg]:px-1.5 [&_svg:not([class*='size-'])]:size-3",
        sm: 'px-2.5 py-1 has-[>svg]:px-2',
        lg: 'px-5 py-2 text-sm leading-5 has-[>svg]:px-4',
        inline: 'h-auto gap-1 p-0 has-[>svg]:px-0',
        micro:
          "h-auto gap-0.5 px-1 py-0 text-xs leading-4 font-normal has-[>svg]:px-0.5 [&_svg:not([class*='size-'])]:size-3",
        icon: 'size-9 rounded-[4px]',
        'icon-xs': "size-6 rounded-[4px] [&_svg:not([class*='size-'])]:size-3",
        'icon-sm': 'size-8 rounded-[4px]',
        'icon-lg': 'size-10 rounded-[4px]',
        grip: 'h-4 w-12 rounded-full p-0',
        'icon-titlebar':
          'titlebar-icon-button h-(--titlebar-control-height) w-(--titlebar-control-size) rounded-[4px] [&_svg:not([class*="size-"])]:size-(--titlebar-icon-size)'
      }
    },
    compoundVariants: [
      {
        variant: 'textStrong',
        class: 'px-0 has-[>svg]:px-0'
      }
    ],
    defaultVariants: {
      variant: 'default',
      size: 'default'
    }
  }
)

type LegacyButtonProps = React.ComponentProps<'button'> &
  VariantProps<typeof buttonVariants> & {
    asChild?: boolean
    /** Working. The label stays in flow but goes invisible and a spinner
     *  sits over it, so the button keeps its exact width and height — a
     *  label swapped for a glyph reflows every sibling on the row. */
    loading?: boolean
  }

/**
 * The app's button, rendered as the vendored Sakuga control.
 *
 * `variant`/`size` keep their old names so no call site changes: the filled
 * looks (`default`, `primary`) are the upstream primary look and every quiet
 * variant is the secondary look. `asChild` still renders the supplied child —
 * a router `<a>` stays a link and keeps the legacy button classes.
 */
const Button = React.forwardRef<HTMLElement, LegacyButtonProps>(function Button(
  {
    className,
    variant = 'default',
    size,
    asChild = false,
    loading = false,
    children,
    disabled,
    onClick,
    type = 'button',
    ...props
  },
  ref
) {
  if (asChild && React.isValidElement(children)) {
    const child = children as React.ReactElement<{ className?: string }>
    return React.cloneElement(child, {
      className: cn(buttonVariants({ variant, size }), className, child.props.className),
      ...props
    })
  }

  const submits = type === 'submit'

  return (
    <SakugaButton
      aria-busy={loading ? 'true' : undefined}
      className={cn(className, loading && 'relative')}
      disabled={disabled || loading}
      fx={variant === 'destructive' ? false : 'impact'}
      onClick={event => {
        // A destructive action is a real warning outcome; the native element
        // still owns the behaviour, this is a presentation cue only.
        if (variant === 'destructive' && !disabled && !loading) void emitFx('warning')
        // The inner control lives in a shadow root, so it is not associated
        // with the outer form. Submit from the host instead (and only from
        // here — the upstream `type="submit"` path would double-submit).
        if (submits && !disabled && !loading) {
          ;(event.currentTarget as HTMLElement).closest('form')?.requestSubmit()
        }
        onClick?.(event as unknown as React.MouseEvent<HTMLButtonElement>)
      }}
      ref={ref}
      {...props}
      data-sakuga-submit={submits ? '' : undefined}
      type="button"
      variant={sakugaButtonVariant(variant)}
    >
      {loading ? (
        <>
          <span aria-hidden className="invisible inline-flex items-center gap-[inherit]">
            {children}
          </span>
          <span aria-hidden className="absolute inset-0 grid place-items-center">
            <Loader2 className="animate-spin" />
          </span>
        </>
      ) : (
        children
      )}
    </SakugaButton>
  )
})

export { Button, buttonVariants }
