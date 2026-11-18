import * as React from 'react'

import { Button } from './button'

/**
 * A full-row / region click target: bakes in `type="button"` + a stable
 * `data-slot`, and leaves the caller's layout classes alone. Use for
 * row/region targets; use `Button` for ordinary compact actions.
 */
function RowButton({ className, type = 'button', ...props }: React.ComponentProps<'button'>) {
  return <Button className={className} data-slot="row-button" type={type} {...props} />
}

export { RowButton }
