import { JSON_RPC_METHOD_NOT_FOUND, JsonRpcGatewayError } from '@qubit/shared/json-rpc-channel'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { SessionCompressResponse } from '../gatewayTypes.js'
import { sessionCommands } from '../app/slash/commands/session.js'
import type { SlashRunCtx } from '../app/slash/types.js'
import {
  adaptCanonicalCompressResult,
  buildCanonicalCompressParams,
  canUseCanonicalSessionCompress,
  CanonicalCompressError,
  isCanonicalCompressFallback,
  runCanonicalCompress
} from '../app/sessionCompress.js'
import { resetUiState } from '../app/uiStore.js'

const compressCommand = sessionCommands.find(cmd => cmd.name === 'compress')!

const snapshot = (overrides: Record<string, unknown> = {}) => ({
  execution_generation: 3,
  message_count: 5,
  messages: [{ content: 'before', role: 'user' }],
  revision: 7,
  session_id: 'sess-1',
  ...overrides
})

const buildCtx = (
  gw: {
    hasSessionAuthority: boolean
    recordLog: (line: string) => void
    request: (method: string, params?: Record<string, unknown>) => Promise<unknown>
  },
  sid: null | string = 'sess-1'
) => {
  const sys: string[] = []
  const errors: unknown[] = []
  const history: unknown[][] = []
  const rpc = vi.fn()

  const ctx = {
    gateway: { gw, rpc },
    guarded:
      <T>(fn: (r: T) => void) =>
      (r: null | T) => {
        if (r) {
          fn(r)
        }
      },
    guardedErr: (err: unknown) => errors.push(err),
    sid,
    transcript: {
      setHistoryItems: (items: unknown[]) => history.push(items),
      sys: (line: string) => sys.push(line)
    }
  } as unknown as SlashRunCtx

  return { ctx, errors, history, rpc, sys }
}

afterEach(() => {
  resetUiState()
  vi.restoreAllMocks()
})

describe('canonical /compress method selection', () => {
  it('selects the canonical capability only when the authority advertises it', () => {
    expect(canUseCanonicalSessionCompress({ hasSessionAuthority: true })).toBe(true)
    expect(canUseCanonicalSessionCompress({ hasSessionAuthority: false })).toBe(false)
  })

  it('builds a fenced session.mutate compress payload', () => {
    const params = buildCanonicalCompressParams('sess-1', '  release notes  ', snapshot(), 'req-1')

    expect(params).toEqual({
      expected_generation: 3,
      expected_revision: 7,
      operation: 'compress',
      payload: { focus: 'release notes' },
      request_id: 'req-1',
      session_id: 'sess-1'
    })
  })

  it('refuses to invent a fence when the canonical snapshot omits it', () => {
    expect(() => buildCanonicalCompressParams('sess-1', '', { session_id: 'sess-1' })).toThrow(
      /missing revision\/execution_generation/
    )
  })
})

describe('canonical receipt adaptation', () => {
  it('maps authoritative receipt and post-compress snapshot fields', () => {
    const usage = { total: 42 }
    const summary = { headline: 'Compressed: 5 → 2 messages', token_line: '5 → 2' }
    const after = snapshot({
      info: { model: 'deepseek-v4.1-flash', usage },
      message_count: 2,
      messages: [{ content: 'after', role: 'user' }]
    })
    const result = adaptCanonicalCompressResult(
      { execution_generation: 4, message_count: 2, operation: 'compress', revision: 8, summary },
      after,
      snapshot()
    )

    expect(result).toEqual({
      after_messages: 2,
      before_messages: 5,
      info: after.info,
      messages: after.messages,
      removed: 3,
      summary,
      usage
    })
  })

  it('uses legacy-shaped fields a compatibility backend projects onto the receipt', () => {
    const usage = { total: 9 }
    const info = { model: 'compat', usage }
    const messages = [{ content: 'compat rows', role: 'assistant' }]
    const result = adaptCanonicalCompressResult(
      { info, messages, removed: 4, summary: { headline: 'Compressed' }, usage },
      null,
      snapshot()
    )

    expect(result.removed).toBe(4)
    expect(result.messages).toBe(messages)
    expect(result.info).toBe(info)
    expect(result.usage).toBe(usage)
  })

  it('does not fabricate removed/messages when the backend returns none', () => {
    const result = adaptCanonicalCompressResult({ operation: 'compress' }, null, null)

    expect(result).toEqual({})
  })
})

describe('runCanonicalCompress', () => {
  it('resumes for the fence, mutates, then resumes for the committed transcript', async () => {
    const calls: string[] = []
    let resume = 0
    const gw = {
      recordLog: vi.fn(),
      request: vi.fn(async (method: string) => {
        calls.push(method)

        if (method === 'session.resume') {
          return resume++ === 0
            ? snapshot()
            : snapshot({ message_count: 2, messages: [{ content: 'after', role: 'user' }] })
        }

        return {
          execution_generation: 4,
          message_count: 2,
          operation: 'compress',
          revision: 8,
          summary: { headline: 'Compressed: 5 → 2 messages' }
        }
      })
    }

    const result = await runCanonicalCompress(gw, 'sess-1', 'focus', 'req-1')

    expect(calls).toEqual(['session.resume', 'session.mutate', 'session.resume'])
    expect(gw.request).toHaveBeenNthCalledWith(2, 'session.mutate', {
      expected_generation: 3,
      expected_revision: 7,
      operation: 'compress',
      payload: { focus: 'focus' },
      request_id: 'req-1',
      session_id: 'sess-1'
    })
    expect(result.removed).toBe(3)
    expect(result.summary?.headline).toBe('Compressed: 5 → 2 messages')
  })

  it('returns the receipt projection and logs (never re-compresses) when the post-resume fails', async () => {
    const gw = {
      recordLog: vi.fn(),
      request: vi.fn(async (method: string) => {
        if (method === 'session.resume') {
          return snapshot()
        }

        return { operation: 'compress', summary: { headline: 'Compressed: 5 → 2 messages' } }
      })
    }
    let resume = 0

    gw.request.mockImplementation(async (method: string) => {
      if (method === 'session.resume') {
        if (resume++ > 0) {
          throw new Error('gateway not connected')
        }

        return snapshot()
      }

      return { operation: 'compress', summary: { headline: 'Compressed: 5 → 2 messages' } }
    })

    const result = await runCanonicalCompress(gw, 'sess-1', '')

    expect(gw.recordLog).toHaveBeenCalledWith(expect.stringContaining('post-compress resume failed'))
    expect(result.summary?.headline).toBe('Compressed: 5 → 2 messages')
    expect(result.messages).toBeUndefined()
  })
})

describe('/compress slash command', () => {
  it('uses session.mutate on an authority-advertised gateway', async () => {
    const gw = {
      hasSessionAuthority: true,
      recordLog: vi.fn(),
      request: vi.fn(async (method: string) => {
        if (method === 'session.resume') {
          return snapshot()
        }

        return { operation: 'compress', summary: { headline: 'Compressed: 5 → 2 messages' } }
      })
    }
    const { ctx, rpc, sys } = buildCtx(gw)

    compressCommand.run('', ctx, 'compress')

    await vi.waitFor(() => expect(sys).toContain('✓ Compressed: 5 → 2 messages'))
    expect(gw.request.mock.calls.map(call => call[0])).toEqual([
      'session.resume',
      'session.mutate',
      'session.resume'
    ])
    expect(rpc).not.toHaveBeenCalled()
  })

  it('keeps session.compress for a sidecar/older backend that does not advertise the capability', async () => {
    const gw = {
      hasSessionAuthority: false,
      recordLog: vi.fn(),
      request: vi.fn()
    }
    const { ctx, rpc, sys } = buildCtx(gw)
    const legacy: SessionCompressResponse = { removed: 2, usage: { total: 12 } }

    rpc.mockResolvedValue(legacy)
    compressCommand.run('topic', ctx, 'compress')

    await vi.waitFor(() => expect(rpc).toHaveBeenCalledTimes(1))
    expect(rpc).toHaveBeenCalledWith('session.compress', {
      focus_topic: 'topic',
      session_id: 'sess-1'
    })
    expect(gw.request).not.toHaveBeenCalled()
    expect(sys.join('\n')).toContain('compressed')
  })

  for (const [label, error] of [
    ['-32601 unknown method', new JsonRpcGatewayError('unknown method: session.mutate', {
      code: JSON_RPC_METHOD_NOT_FOUND
    })],
    ['out-of-sync params', new JsonRpcGatewayError('Extra inputs are not permitted', { code: 4000 })]
  ] as const) {
    it(`falls back once to session.compress on ${label} and logs the reason`, async () => {
      const gw = {
        hasSessionAuthority: true,
        recordLog: vi.fn(),
        request: vi.fn(async (method: string) => {
          if (method === 'session.resume') {
            return snapshot()
          }

          throw error
        })
      }
      const { ctx, errors, rpc, sys } = buildCtx(gw)

      rpc.mockResolvedValue({ removed: 1 })
      compressCommand.run('', ctx, 'compress')

      await vi.waitFor(() => expect(rpc).toHaveBeenCalledTimes(1))
      expect(rpc).toHaveBeenCalledWith('session.compress', { session_id: 'sess-1' })
      expect(gw.recordLog).toHaveBeenCalledWith(expect.stringContaining('retrying session.compress'))
      expect(errors).toHaveLength(0)
      expect(sys.join('\n')).toContain('compressed 1')
    })
  }

  it('uses the legacy verb when no session id is available', async () => {
    const gw = {
      hasSessionAuthority: true,
      recordLog: vi.fn(),
      request: vi.fn()
    }
    const { ctx, rpc } = buildCtx(gw, null)

    rpc.mockResolvedValue({ removed: 0 })
    compressCommand.run('', ctx, 'compress')

    await vi.waitFor(() => expect(rpc).toHaveBeenCalledTimes(1))
    expect(gw.request).not.toHaveBeenCalled()
  })

  it('does not fall back after a committed mutate whose post-resume failed', async () => {
    let resume = 0
    const gw = {
      hasSessionAuthority: true,
      recordLog: vi.fn(),
      request: vi.fn(async (method: string) => {
        if (method === 'session.resume') {
          if (resume++ > 0) {
            throw new Error('gateway not connected')
          }

          return snapshot()
        }

        return { operation: 'compress', summary: { headline: 'Compressed: 5 → 2 messages' } }
      })
    }
    const { ctx, rpc, sys } = buildCtx(gw)

    compressCommand.run('', ctx, 'compress')

    await vi.waitFor(() => expect(sys).toContain('✓ Compressed: 5 → 2 messages'))
    expect(rpc).not.toHaveBeenCalled()
    expect(gw.recordLog).toHaveBeenCalledWith(expect.stringContaining('post-compress resume failed'))
  })

  it('classifies only unknown-method/out-of-sync failures as fallback-eligible', () => {
    expect(
      isCanonicalCompressFallback(
        new CanonicalCompressError(
          'mutate',
          new JsonRpcGatewayError('unknown method: session.mutate', { code: JSON_RPC_METHOD_NOT_FOUND })
        )
      )
    ).toBe(true)
    expect(
      isCanonicalCompressFallback(
        new CanonicalCompressError('mutate', new JsonRpcGatewayError('Extra inputs are not permitted', { code: 4000 }))
      )
    ).toBe(true)
    expect(isCanonicalCompressFallback(new CanonicalCompressError('mutate', new Error('gateway not connected')))).toBe(
      false
    )
    expect(isCanonicalCompressFallback(new Error('gateway not connected'))).toBe(false)
  })
})
