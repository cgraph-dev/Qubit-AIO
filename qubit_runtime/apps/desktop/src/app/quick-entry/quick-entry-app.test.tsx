/** Regression coverage for #77429: native picker options must paint a readable surface. */

import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { sakugaInnerAll, waitForSakuga } from '@/sakuga/ui'

import type { QuickEntryStatePush } from '@/store/quick-entry'

import { QuickEntryApp } from './quick-entry-app'

const initialQubitDesktop = window.hermesDesktop

describe('QuickEntryApp', () => {
  let pushState: ((payload: QuickEntryStatePush) => void) | undefined

  beforeEach(() => {
    pushState = undefined
    window.hermesDesktop = {
      quickEntry: {
        dismiss: vi.fn(),
        onShown: vi.fn(() => vi.fn()),
        onState: vi.fn(callback => {
          pushState = callback

          return vi.fn()
        }),
        onLateResult: vi.fn(() => vi.fn()),
        submit: vi.fn()
      }
    } as never
  })

  afterEach(() => {
    cleanup()
    window.hermesDesktop = initialQubitDesktop
    vi.restoreAllMocks()
  })

  it('offers every target through the Sakuga select', async () => {
    render(<QuickEntryApp />)

    act(() => {
      pushState?.({
        connected: true,
        sessions: [{ id: 'session-1', title: 'A recent session' }]
      })
    })

    const trigger = screen.getByRole('combobox', { name: 'Target session' })
    expect(trigger.getAttribute('aria-expanded')).toBe('false')

    await act(async () => {
      fireEvent.click(trigger)
    })
    await waitForSakuga(screen.getByRole('dialog', { name: 'Target session options' }).querySelector('sakuga-radio'))

    const radios = sakugaInnerAll(screen.getByRole('dialog', { name: 'Target session options' }).querySelector('sakuga-radio'), 'input')
    expect(radios).toHaveLength(3)
    expect(radios.map(radio => (radio as HTMLInputElement).value)).toEqual([
      'current',
      'new',
      'session-1'
    ])
  })
})
