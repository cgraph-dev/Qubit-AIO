import { mkdtempSync, readFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { turnController } from '../app/turnController.js'
import { getTurnState, resetTurnState } from '../app/turnStore.js'
import { patchUiState, resetUiState } from '../app/uiStore.js'
import {
  hydrateLiveSessionInflight,
  liveSessionInflightMessages,
  signalFreshSessionBoundary,
  writeActiveSessionFile
} from '../app/useSessionLifecycle.js'

describe('fresh session boundary', () => {
  it('signals only when a live session is replaced by a different session', () => {
    const onFreshSessionStarted = vi.fn()

    expect(signalFreshSessionBoundary('old-session', 'new-session', onFreshSessionStarted)).toBe(true)
    expect(signalFreshSessionBoundary(null, 'first-session', onFreshSessionStarted)).toBe(false)
    expect(signalFreshSessionBoundary('same-session', 'same-session', onFreshSessionStarted)).toBe(false)
    expect(signalFreshSessionBoundary('old-session', null, onFreshSessionStarted)).toBe(false)
    expect(signalFreshSessionBoundary('old-session', 'new-session')).toBe(false)
    expect(onFreshSessionStarted).toHaveBeenCalledOnce()
    expect(onFreshSessionStarted).toHaveBeenCalledWith('new-session')
  })
})

describe('writeActiveSessionFile', () => {
  let dir = ''

  afterEach(() => {
    if (dir) {
      rmSync(dir, { force: true, recursive: true })
      dir = ''
    }
  })

  it('writes the actual resumed session id for the shell exit summary', () => {
    dir = mkdtempSync(join(tmpdir(), 'hermes-tui-active-'))
    const path = join(dir, 'active.json')

    writeActiveSessionFile('actual_session', path)

    expect(JSON.parse(readFileSync(path, 'utf8'))).toEqual({ session_id: 'actual_session' })
  })
})

describe('live session activation in-flight state', () => {
  beforeEach(() => {
    resetUiState()
    resetTurnState()
    turnController.fullReset()
    patchUiState({ streaming: true })
  })

  it('keeps the in-flight user prompt in history and hydrates partial assistant text', () => {
    const inflight = { assistant: 'partial answer', streaming: true, user: 'write a long answer' }

    expect(liveSessionInflightMessages(inflight)).toEqual([{ role: 'user', text: 'write a long answer' }])

    hydrateLiveSessionInflight(inflight)

    expect(turnController.bufRef).toBe('partial answer')
    expect(getTurnState().streaming).toBe('partial answer')
  })

  it('preserves synthetic turn display metadata while rebuilding live history', () => {
    const inflight = {
      assistant: '',
      display_kind: 'process_complete',
      display_metadata: { display_text: 'Finished syncing the workspace' },
      streaming: true,
      user: 'process completed'
    }

    expect(liveSessionInflightMessages(inflight)).toEqual([
      {
        kind: 'event',
        role: 'system',
        text: 'Finished syncing the workspace'
      }
    ])
  })

  it('ignores empty in-flight payloads', () => {
    expect(liveSessionInflightMessages({ assistant: '', streaming: false, user: '   ' })).toEqual([])

    hydrateLiveSessionInflight({ assistant: '', streaming: false, user: '' })

    expect(turnController.bufRef).toBe('')
    expect(getTurnState().streaming).toBe('')
  })
})

describe('sessionCreateParams', () => {
  const PIN_VARS = [
    'HERMES_TUI_MODEL_PIN',
    'HERMES_MODEL',
    'HERMES_INFERENCE_MODEL',
    'HERMES_TUI_PROVIDER',
    'HERMES_INFERENCE_PROVIDER',
    'HERMES_TUI_CWD'
  ] as const

  let saved: Record<string, string | undefined>

  beforeEach(() => {
    saved = {}

    for (const key of PIN_VARS) {
      saved[key] = process.env[key]
      delete process.env[key]
    }

    vi.resetModules()
  })

  afterEach(() => {
    for (const key of PIN_VARS) {
      if (saved[key] === undefined) {
        delete process.env[key]
      } else {
        process.env[key] = saved[key]
      }
    }

    vi.resetModules()
  })

  async function createParams() {
    const m = await import('../app/useSessionLifecycle.js')

    return m.sessionCreateParams(100) as Record<string, unknown>
  }

  it('sends only cols and cwd by default', async () => {
    expect(await createParams()).toEqual({ cols: 100 })
  })

  it('pins the spawn model/provider when the dashboard spawn flag is set', async () => {
    process.env.HERMES_TUI_MODEL_PIN = '1'
    process.env.HERMES_MODEL = 'deepseek-v4.1-flash'
    process.env.HERMES_TUI_PROVIDER = 'opencode-go'

    expect(await createParams()).toEqual({
      cols: 100,
      model: 'deepseek-v4.1-flash',
      provider: 'opencode-go'
    })
  })

  it('falls back to the inference env spellings', async () => {
    process.env.HERMES_TUI_MODEL_PIN = 'yes'
    process.env.HERMES_INFERENCE_MODEL = 'm'
    process.env.HERMES_INFERENCE_PROVIDER = 'p'

    expect(await createParams()).toEqual({ cols: 100, model: 'm', provider: 'p' })
  })

  it('omits an auto provider sentinel instead of pinning it', async () => {
    process.env.HERMES_TUI_MODEL_PIN = '1'
    process.env.HERMES_MODEL = 'm'
    process.env.HERMES_TUI_PROVIDER = 'auto'

    expect(await createParams()).toEqual({ cols: 100, model: 'm' })
  })

  it('ignores launch seeds without the pin flag', async () => {
    process.env.HERMES_MODEL = 'm'
    process.env.HERMES_TUI_PROVIDER = 'p'

    expect(await createParams()).toEqual({ cols: 100 })
  })
})
