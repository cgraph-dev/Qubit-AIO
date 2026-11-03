import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

// Attach-first launch: without an explicit HERMES_TUI_GATEWAY_URL the client
// runs ui-tui/scripts/gateway_bootstrap.py --start and attaches to the
// gateway it reports, instead of spawning a stdio child that the home lock
// refuses beside a running gateway (exit 1, "could not be restarted").
// Dashboard launches keep their stdio behaviour; any bootstrap failure falls
// back to the spawned gateway.

const FAKE_URL = 'ws://127.0.0.1:9999/api/ws'
const FAKE_PROTOCOLS = ['hermes-gateway-v1', 'hermes-gateway-ticket.abc123']

const { FakeChild, FakeWebSocket, fakeSpawn, existsMock } = vi.hoisted(() => {
  class FakeStream {
    private listeners = new Map<string, Array<(event: any) => void>>()

    resume() {
      return this
    }

    pause() {
      return this
    }

    write(_text: string) {
      return true
    }

    on(type: string, callback: (event: any) => void) {
      const entries = this.listeners.get(type) ?? []

      entries.push(callback)
      this.listeners.set(type, entries)
    }

    emit(type: string, ...args: unknown[]) {
      for (const callback of [...(this.listeners.get(type) ?? [])]) {
        callback(...args)
      }
    }
  }

  class FakeChild {
    static instances: FakeChild[] = []

    killed = false
    exitCode: null | number = null
    pid = 4242
    stdin: FakeStream | null = null
    stdout = new FakeStream()
    stderr = new FakeStream()
    argv: string[] = []
    private listeners = new Map<string, Array<(event: any) => void>>()

    constructor(argv: string[]) {
      this.argv = argv
      FakeChild.instances.push(this)
    }

    kill() {
      this.killed = true

      return true
    }

    on(type: string, callback: (event: any) => void) {
      const entries = this.listeners.get(type) ?? []

      entries.push(callback)
      this.listeners.set(type, entries)
    }

    emit(type: string, ...args: unknown[]) {
      for (const callback of [...(this.listeners.get(type) ?? [])]) {
        callback(...args)
      }
    }
  }

  class FakeWebSocket {
    static instances: FakeWebSocket[] = []

    url: string
    protocols: unknown

    constructor(url: string, protocols?: unknown) {
      this.url = url
      this.protocols = protocols
      FakeWebSocket.instances.push(this)
    }

    addEventListener() {}

    close() {}
  }

  const fakeSpawn = vi.fn(
    ((_python: string, argv: string[], _opts: unknown) => new FakeChild(argv)) as never
  )
  const existsMock = vi.fn(() => true)

  return { FakeChild, FakeWebSocket, fakeSpawn, existsMock }
})

vi.mock('node:child_process', () => ({ spawn: fakeSpawn }))
vi.mock('node:fs', () => ({ existsSync: (...args: unknown[]) => existsMock(...args) }))
vi.mock('undici', () => ({ WebSocket: FakeWebSocket }))

import { GatewayClient, resolveBootstrapScript } from '../gatewayClient.js'

describe('GatewayClient bootstrap attach-first launch', () => {
  const originalWebSocket = (globalThis as { WebSocket?: unknown }).WebSocket
  let originalGatewayUrl: string | undefined
  let originalDashboard: string | undefined

  const finishBootstrap = (child: FakeChild, stdout: string, stderr: string, code: number | null) => {
    if (stdout) {
      child.stdout.emit('data', stdout)
    }

    if (stderr) {
      child.stderr.emit('data', stderr)
    }

    child.exitCode = code
    child.emit('exit', code, null)
  }

  beforeEach(() => {
    originalGatewayUrl = process.env.HERMES_TUI_GATEWAY_URL
    originalDashboard = process.env.HERMES_TUI_DASHBOARD
    delete process.env.HERMES_TUI_GATEWAY_URL
    delete process.env.HERMES_TUI_DASHBOARD
    delete process.env.QUBIT_TUI_DASHBOARD
    ;(globalThis as { WebSocket?: unknown }).WebSocket = FakeWebSocket as unknown as typeof WebSocket
    fakeSpawn.mockClear()
    FakeChild.instances.length = 0
    FakeWebSocket.instances.length = 0
    existsMock.mockReset()
    existsMock.mockReturnValue(true)
  })

  afterEach(() => {
    if (originalGatewayUrl === undefined) {
      delete process.env.HERMES_TUI_GATEWAY_URL
    } else {
      process.env.HERMES_TUI_GATEWAY_URL = originalGatewayUrl
    }

    if (originalDashboard === undefined) {
      delete process.env.HERMES_TUI_DASHBOARD
    } else {
      process.env.HERMES_TUI_DASHBOARD = originalDashboard
    }

    fakeSpawn.mockClear()
    FakeChild.instances.length = 0
    FakeWebSocket.instances.length = 0

    if (originalWebSocket) {
      ;(globalThis as { WebSocket?: unknown }).WebSocket = originalWebSocket
    } else {
      delete (globalThis as { WebSocket?: unknown }).WebSocket
    }
  })

  it('attaches to the bootstrapped gateway with its ticket protocols and never spawns stdio', async () => {
    const gw = new GatewayClient()

    gw.start()
    await Promise.resolve()

    expect(fakeSpawn).toHaveBeenCalledTimes(1)

    const argv = fakeSpawn.mock.calls[0]![1] as string[]

    expect(argv[0]).toMatch(/gateway_bootstrap\.py$/)
    expect(argv).toContain('--start')

    finishBootstrap(
      FakeChild.instances[0]!,
      JSON.stringify({ url: FAKE_URL, protocols: FAKE_PROTOCOLS }),
      '',
      0
    )
    await Promise.resolve()

    expect(FakeWebSocket.instances).toHaveLength(1)
    expect(FakeWebSocket.instances[0]!.url).toBe(FAKE_URL)
    expect(FakeWebSocket.instances[0]!.protocols).toEqual(FAKE_PROTOCOLS)
    expect(fakeSpawn).toHaveBeenCalledTimes(1)
    expect(gw.attached).toBe(true)
    gw.kill()
  })

  it('falls back to the spawned gateway when the bootstrap fails', async () => {
    const gw = new GatewayClient()

    gw.start()
    await Promise.resolve()

    expect(fakeSpawn).toHaveBeenCalledTimes(1)

    finishBootstrap(FakeChild.instances[0]!, '', 'gateway owner_unavailable: no owner running', 1)
    await Promise.resolve()

    expect(fakeSpawn).toHaveBeenCalledTimes(2)

    const fallbackArgv = fakeSpawn.mock.calls[1]![1] as string[]

    expect(fallbackArgv).toContain('tui_gateway.entry')
    gw.kill()
  })

  it('falls back to the spawned gateway when the bootstrap result is not JSON', async () => {
    const gw = new GatewayClient()

    gw.start()
    await Promise.resolve()

    finishBootstrap(FakeChild.instances[0]!, 'not json\n', '', 0)
    await Promise.resolve()

    expect(fakeSpawn).toHaveBeenCalledTimes(2)
    expect((fakeSpawn.mock.calls[1]![1] as string[])).toContain('tui_gateway.entry')
    gw.kill()
  })

  it('dashboard launches keep the stdio behaviour without running the bootstrap', async () => {
    process.env.HERMES_TUI_DASHBOARD = '1'
    const gw = new GatewayClient()

    gw.start()
    await Promise.resolve()

    expect(fakeSpawn).toHaveBeenCalledTimes(1)
    expect((fakeSpawn.mock.calls[0]![1] as string[])).toContain('tui_gateway.entry')
    expect(FakeWebSocket.instances).toHaveLength(0)
    gw.kill()
  })

  it('a missing helper falls back to the spawned gateway', async () => {
    existsMock.mockReturnValue(false)
    const gw = new GatewayClient()

    gw.start()
    await Promise.resolve()

    expect(fakeSpawn).toHaveBeenCalledTimes(1)
    expect((fakeSpawn.mock.calls[0]![1] as string[])).toContain('tui_gateway.entry')
    gw.kill()
  })

  it('resolveBootstrapScript prefers the packaged layout, then source, then null', () => {
    expect(resolveBootstrapScript('/root')).toMatch(/scripts[\\/]gateway_bootstrap\.py$/)

    existsMock.mockImplementation((candidate: unknown) =>
      String(candidate).includes(`ui-tui${'/'}scripts`)
    )
    expect(resolveBootstrapScript('/root')).toContain('ui-tui')

    existsMock.mockReturnValue(false)
    expect(resolveBootstrapScript('/root')).toBeNull()
  })
})
