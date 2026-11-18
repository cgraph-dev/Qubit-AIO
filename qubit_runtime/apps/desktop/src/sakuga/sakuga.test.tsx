import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import {
  Button as SakugaButton,
  Input as SakugaInput,
  Select as SakugaSelect,
  Toggle as SakugaToggle,
  clickSakuga,
  sakugaInner,
  typeIntoSakuga
} from '../sakuga/ui'
import { sakugaBaseUrl } from '../sakuga/base'
import { emitFx, installFxBridge, onFx } from '../sakuga/ui'

describe('sakuga base url', () => {
  it('resolves under the dev server root', () => {
    expect(sakugaBaseUrl('http://127.0.0.1:5174/')).toBe('http://127.0.0.1:5174/sakuga-vfx/')
  })

  it('resolves beside the document for a loadFile bundle', () => {
    expect(sakugaBaseUrl('file:///opt/qubit/resources/app/index.html')).toBe(
      'file:///opt/qubit/resources/app/sakuga-vfx/'
    )
  })

  it('keeps a nested renderer route relative to the document', () => {
    expect(sakugaBaseUrl('http://127.0.0.1:5174/chat/index.html')).toBe('http://127.0.0.1:5174/chat/sakuga-vfx/')
  })
})

describe('sakuga fx bus', () => {
  it('forwards a window qubit:fx event to subscribers', () => {
    installFxBridge()
    const seen = vi.fn()
    const off = onFx(seen)
    window.dispatchEvent(new CustomEvent('qubit:fx', { detail: { cue: 'success' } }))
    expect(seen).toHaveBeenCalledWith(expect.objectContaining({ cue: 'success' }))
    off()
  })

  it('never throws when the engine is unavailable', async () => {
    await expect(emitFx('error')).resolves.toBeUndefined()
  })
})

describe('sakuga wrappers', () => {
  it('renders a button host that carries its accessible role and label', () => {
    render(<SakugaButton aria-label="Send" variant="primary">Send</SakugaButton>)
    const button = screen.getByRole('button', { name: 'Send' })
    expect(button.tagName.toLowerCase()).toBe('sakuga-button')
    expect(sakugaInner(button, 'button')).not.toBeNull()
  })

  it('delivers typed text through the inner native input', () => {
    const onChange = vi.fn()
    render(<SakugaInput label="Model" value="" onChange={onChange} />)
    const host = screen.getByRole('textbox', { name: 'Model' })
    typeIntoSakuga(host, 'qubit-brain')
    expect(onChange).toHaveBeenCalledWith('qubit-brain')
  })

  it('reports a toggle flip through onChange', () => {
    const onChange = vi.fn()
    const { rerender } = render(<SakugaToggle label="Hearts" checked={false} onChange={onChange} />)
    clickSakuga(screen.getByRole('switch', { name: 'Hearts' }))
    expect(onChange).toHaveBeenCalledWith(true)
    rerender(<SakugaToggle label="Hearts" checked onChange={onChange} />)
    expect(screen.getByRole('switch', { name: 'Hearts' }).getAttribute('aria-checked')).toBe('true')
  })

  it('honours the select trigger label', () => {
    render(
      <SakugaSelect
        label="Target session"
        options={[
          { value: 'current', label: 'Current chat' },
          { value: 'new', label: 'New session' }
        ]}
        value="current"
      />
    )
    expect(screen.getByRole('combobox', { name: 'Target session' })).toBeTruthy()
    fireEvent.click(screen.getByRole('combobox', { name: 'Target session' }))
    expect(screen.getByRole('dialog', { name: 'Target session options' })).toBeTruthy()
  })
})
