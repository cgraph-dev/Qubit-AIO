import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { HoverTooltip } from '@/sakuga/ui'

describe('HoverTooltip', () => {
  it('opens on hover and closes on Escape', () => {
    render(
      <HoverTooltip content="Refresh the list">
        <button type="button">Refresh</button>
      </HoverTooltip>,
    )

    expect(screen.queryByRole('tooltip')).toBeNull()
    fireEvent.mouseEnter(screen.getByRole('button', { name: 'Refresh' }))
    expect(screen.getByRole('tooltip').textContent).toContain('Refresh the list')

    fireEvent.keyDown(window, { key: 'Escape' })
    expect(screen.queryByRole('tooltip')).toBeNull()
  })

  it('opens on focus and on tap', () => {
    render(
      <HoverTooltip content="Upload a file">
        <button type="button">Upload</button>
      </HoverTooltip>,
    )
    const trigger = screen.getByRole('button', { name: 'Upload' })

    fireEvent.focus(trigger)
    expect(screen.getByRole('tooltip').textContent).toContain('Upload a file')
    fireEvent.blur(trigger)

    fireEvent.click(trigger)
    expect(screen.getAllByRole('tooltip').length).toBeGreaterThan(0)
  })
})
