import { readFileSync } from 'node:fs'
import path from 'node:path'

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
  const view = render(options.render())
  return { contributions, disposers, close, options, view }
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

  it('does not mount the native Surface when the player bridge is unavailable', () => {
    let surfaces = 0
    const player = controller({ available: false, snapshot: null, canLink: false, linked: false })
    const custom = {
      usePlayer: () => player,
      Surface: () => {
        surfaces += 1
        return React.createElement('div', null, 'native view')
      }
    }

    mount(custom)

    expect(screen.getByText(/player bridge is unavailable/)).toBeTruthy()
    expect(surfaces).toBe(0)
  })

  it('links the session and runs context, transcript, jobs, cancel and clear-data', async () => {
    const context = {
      source: 'youtube',
      contextToken: 'opaque-test-token',
      navigationGeneration: 3,
      expiresAt: new Date(Date.now() + 60_000).toISOString(),
      metadata: { identity: 'youtube-watch' }
    }
    const job = {
      id: 'job1',
      kind: 'transcript',
      state: 'running',
      progress: 0.2,
      error: null,
      transcript: null,
      artifact: null
    }
    const request = vi.fn(async (operation: { type: string }) => {
      if (operation.type === 'context') return { operation: 'context', context }
      if (operation.type === 'transcript.start') return { operation: operation.type, job }
      if (operation.type === 'jobs.list') return { operation: operation.type, jobs: [job] }
      if (operation.type === 'job.cancel') return { operation: operation.type, job: { ...job, state: 'cancelled' } }
      return { operation: operation.type }
    })
    const player = controller({
      linked: false,
      link: vi.fn(async () => {
        player.linked = true
      }),
      request
    })
    const { options, view } = mount(media(player))

    fireEvent.click(screen.getByRole('button', { name: 'Link session' }))
    await waitFor(() => expect(player.link).toHaveBeenCalled())
    view.rerender(options.render())

    fireEvent.click(screen.getByRole('button', { name: 'Refresh context' }))
    await waitFor(() => expect(request).toHaveBeenCalledWith({ type: 'context' }))
    await waitFor(() => expect(screen.getByText(/Context expires/)).toBeTruthy())

    fireEvent.click(screen.getByRole('button', { name: 'Transcript' }))
    await waitFor(() =>
      expect(request).toHaveBeenCalledWith({ type: 'transcript.start', contextToken: 'opaque-test-token' })
    )

    fireEvent.click(screen.getByRole('button', { name: 'List jobs' }))
    await waitFor(() => expect(screen.getByText(/transcript · running · 20%/)).toBeTruthy())

    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    await waitFor(() => expect(request).toHaveBeenCalledWith({ type: 'job.cancel', jobId: 'job1' }))

    fireEvent.click(screen.getByRole('button', { name: 'Clear local data' }))
    expect(screen.getByText('Clear QubitTV local data?')).toBeTruthy()
    const clear = screen
      .getAllByRole('button', { name: 'Clear local data' })
      .at(-1) as HTMLButtonElement
    fireEvent.click(clear)
    await waitFor(() => expect(player.clearLocalData).toHaveBeenCalled())
  })

  it('expires the context and blocks transcript until it is refreshed', async () => {
    const request = vi.fn(async (operation: { type: string }) => ({
      operation: operation.type,
      context: {
        source: 'youtube',
        contextToken: 'opaque-test-token',
        navigationGeneration: 1,
        expiresAt: new Date(Date.now() + 30).toISOString(),
        metadata: { identity: 'youtube-watch' }
      }
    }))
    mount(media(controller({ request })))

    fireEvent.click(screen.getByRole('button', { name: 'Refresh context' }))
    await waitFor(() => expect(request).toHaveBeenCalledWith({ type: 'context' }))

    await waitFor(
      () =>
        expect(
          (screen.getByRole('button', { name: 'Transcript' }) as HTMLButtonElement).disabled
        ).toBe(true),
      { timeout: 2_000 }
    )
    expect(screen.getByText(/Media context expired|Context expired/)).toBeTruthy()
  })

  it('runtime bundle imports only the SDK and React singletons', () => {
    const code = readFileSync(
      path.resolve(process.cwd(), '../../plugins/qubit-tv/desktop/plugin.js'),
      'utf8'
    )
    const imports = [...code.matchAll(/^\s*import\s+(?:[^'"\n]+\s+from\s+)?['"]([^'"]+)['"]/gm)].map(
      match => match[1]
    )

    expect([...new Set(imports)].sort()).toEqual(['@qubit/plugin-sdk', 'react', 'react/jsx-runtime'])
    expect(code).not.toMatch(/HermesTV/)
  })

  it('coalesces Ask Qubit while the prompt acknowledgement is pending', async () => {
    let resolvePrompt: ((value: boolean) => void) | undefined
    const seedPrompt = vi.fn(
      () =>
        new Promise<boolean>(resolve => {
          resolvePrompt = resolve
        })
    )
    mount(media(controller({ seedPrompt })))

    const ask = screen.getByRole('button', { name: 'Ask Qubit' }) as HTMLButtonElement
    fireEvent.click(ask)
    fireEvent.click(ask)

    expect(seedPrompt).toHaveBeenCalledTimes(1)
    expect(screen.getByText('Adding prompt…')).toBeTruthy()

    resolvePrompt?.(true)
    await waitFor(() => expect(screen.getByText('Prompt added to the chat.')).toBeTruthy())
  })
})
