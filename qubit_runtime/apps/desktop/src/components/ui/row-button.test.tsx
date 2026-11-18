import { cleanup, fireEvent, render } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { RowButton } from './row-button'

afterEach(cleanup)

describe('RowButton', () => {
  it('renders a non-submitting button control so an enclosing form is never sent', () => {
    const onSubmit = vi.fn((event: React.FormEvent) => event.preventDefault())
    const { getByText } = render(
      <form onSubmit={onSubmit}>
        <RowButton>Row</RowButton>
      </form>
    )
    const el = getByText('Row')

    // The vendored host's inner button is always type="button"; the host is
    // what the row exposes, and it never emits a submit.
    expect(el.getAttribute('role')).toBe('button')
    expect(el.getAttribute('type')).not.toBe('submit')
    fireEvent.click(el)
    expect(onSubmit).not.toHaveBeenCalled()
  })
})
