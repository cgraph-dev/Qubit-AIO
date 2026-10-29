import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import React from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({ openWorkspace: vi.fn(), notify: vi.fn() }))

vi.mock('@qubit/plugin-sdk', () => ({
  PALETTE_AREA: 'palette',
  TITLEBAR_AREAS: { left: 'titleBar.left', center: 'titleBar.center', right: 'titleBar.right' },
  host: { openWorkspace: mocks.openWorkspace, notify: mocks.notify }
}))

// @ts-expect-error - the bundled plugin is plain ESM (no TS declarations by design)
import plugin from './plugin'

type Contribution = {
  id: string
  area: string
  data?: { id: string; label: string; keywords: string[]; run: () => void }
  render?: () => React.ReactNode
}

type FakeMedia = {
  usePlayer: () => Record<string, unknown>
  Surface: (props: { className?: string; hidden?: boolean }) => React.ReactNode
}

function snapshot(patch: Record<string, unknown> = {}) {
  return {
    status: 'ready',
    source: 'youtube',
    presentation: 'docked',
    pinned: false,
    pinStatus: 'observed',
    pinMode: 'topmost',
    fullscreen: false,
    currentTime: 0,
    duration: 100,
    paused: true,
    muted: false,
    canGoBack: true,
    canGoForward: false,
    awarenessLinked: true,
    safeOrigin: 'https://www.youtube.com',
    guestId: 7,
    runtime: { state: 'not-required' },
    error: null,
    ...patch
  }
}

function controller(overrides: Record<string, unknown> = {}) {
  return {
    available: true,
    canLink: true,
    linked: true,
    snapshot: snapshot(),
    command: vi.fn(async () => snapshot()),
    link: vi.fn(async () => undefined),
    unlink: vi.fn(async () => undefined),
    request: vi.fn(async (operation: { type: string }) => ({ operation: operation.type })),
    seedPrompt: vi.fn(async () => true),
    setupMediaHelpers: vi.fn(async () => ({ available: true, reason: null })),
    revealMediaJob: vi.fn(async () => true),
    clearLocalData: vi.fn(async () => snapshot()),
    ...overrides
  }
}

function media(player: Record<string, unknown>): FakeMedia {
  return {
    usePlayer: () => player,
    Surface: (props: { className?: string; hidden?: boolean }) =>
      React.createElement('div', { 'data-testid': 'native-surface', hidden: props.hidden }, 'native surface')
  }
}

function setup(ctxMedia?: unknown) {
  const contributions: Contribution[] = []
  const disposers: Array<() => void> = []
  const ctx = {
    media: ctxMedia,
    storage: { get: (_key: string, fallback: unknown) => fallback, set: vi.fn() },
    register: (contribution: Contribution) => {
      contributions.push(contribution)
      return vi.fn()
    },
    onDispose: (dispose: () => void) => disposers.push(dispose)
  }
  plugin.register(ctx as never)
  return { contributions, disposers }
}

function mount(ctxMedia?: unknown) {
  const close = vi.fn()
  mocks.openWorkspace.mockReturnValue(close)
  const { contributions, disposers } = setup(ctxMedia)
  const palette = contributions.find(item => item.area === 'palette')
  if (!palette?.data) throw new Error('QubitTV did not register its palette command')
  palette.data.run()
  const options = mocks.openWorkspace.mock.calls.at(-1)?.[1] as { render: () => React.ReactNode }
  render(options.render())
  return { contributions, disposers, close }
}

beforeEach(() => {
  vi.clearAllMocks()
  mocks.openWorkspace.mockReturnValue(vi.fn())
})

afterEach(cleanup)

describe('bundled QubitTV plugin', () => {
  it('declares the opt-in media capability and Qubit branding', () => {
    expect(plugin.id).toBe('qubit-tv')
    expect(plugin.name).toBe('QubitTV')
    expect(plugin.capabilities).toEqual(['media.player'])
    expect(plugin.defaultEnabled).toBe(false)
  })

  it('registers the palette command and persistent titlebar button', () => {
    const { contributions } = setup(media(controller()))

    const palette = contributions.find(item => item.area === 'palette')
    expect(palette?.data?.label).toBe('Open QubitTV')
    expect(palette?.data?.keywords).toEqual(expect.arrayContaining(['qubit-tv', 'streaming', 'player']))

    const titlebar = contributions.find(item => item.area === 'titleBar.right')
    expect(titlebar).toBeTruthy()
    expect(render(titlebar!.render!()).getByRole('button', { name: 'Open QubitTV' })).toBeTruthy()
  })

  it('opens one right-docked workspace and closes it on dispose', () => {
    const close = vi.fn()
    mocks.openWorkspace.mockReturnValue(close)
    const { contributions, disposers } = setup(media(controller()))

    expect(mocks.openWorkspace).not.toHaveBeenCalled()
    contributions.find(item => item.area === 'palette')!.data!.run()

    expect(mocks.openWorkspace).toHaveBeenCalledTimes(1)
    const [id, options] = mocks.openWorkspace.mock.calls[0] as [string, Record<string, unknown>]
    expect(id).toBe('qubit-tv:dashboard')
    expect(options.title).toBe('QubitTV')
    expect(options.minWidth).toBe('28rem')
    expect(options.dock).toEqual({ pane: 'workspace', pos: 'right' })
    expect(close).not.toHaveBeenCalled()

    disposers.forEach(dispose => dispose())
    expect(close).toHaveBeenCalledTimes(1)
  })

  it('renders the ten streaming services and the native surface', () => {
    mount(media(controller()))

    for (const label of [
      'YouTube',
      'YouTube TV',
      'Netflix',
      'Prime Video',
      'Disney+',
      'Hulu',
      'HBO Max',
      'Apple TV+',
      'Peacock',
      'Paramount+'
    ]) {
      expect(screen.getByRole('button', { name: label })).toBeTruthy()
    }
    expect(screen.getByTestId('native-surface')).toBeTruthy()
    expect(screen.getByText('Streaming services')).toBeTruthy()
  })

  it('sends open and navigation commands through ctx.media', async () => {
    const player = controller({ snapshot: snapshot({ canGoForward: true }) })
    mount(media(player))

    fireEvent.click(screen.getByRole('button', { name: 'YouTube' }))
    await waitFor(() => expect(player.command).toHaveBeenCalledWith({ type: 'open', source: 'youtube' }))

    fireEvent.click(screen.getByRole('button', { name: 'Back' }))
    fireEvent.click(screen.getByRole('button', { name: 'Forward' }))
    await waitFor(() => expect(player.command).toHaveBeenCalledWith({ type: 'back' }))
    expect(player.command).toHaveBeenCalledWith({ type: 'forward' })
  })

  it('states that protected Widevine/CDM playback is unavailable', () => {
    mount(media(controller({ snapshot: snapshot({ source: 'netflix', runtime: { state: 'unsupported' } }) })))

    expect(screen.getByText(/Protected playback \(Widevine\/CDM\) is unavailable in this build/)).toBeTruthy()
    expect(screen.queryByText(/DRM playback (is )?(supported|works)/i)).toBeNull()
  })

  it('renders a typed placeholder when the host has no media capability', () => {
    mount(undefined)

    expect(screen.getByText('QubitTV needs host media support')).toBeTruthy()
  })
})
