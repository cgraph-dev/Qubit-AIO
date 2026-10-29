import { describe, expect, it, vi } from 'vitest'

const host = vi.hoisted(() => ({
  exposeInMainWorld: vi.fn(),
  invoke: vi.fn(async (_channel: string, _value?: unknown): Promise<unknown> => null),
  on: vi.fn(),
  removeListener: vi.fn(),
  send: vi.fn(),
  sendSync: vi.fn((): unknown => ({}))
}))

vi.mock('electron', () => ({
  contextBridge: { exposeInMainWorld: host.exposeInMainWorld },
  ipcRenderer: {
    invoke: host.invoke,
    on: host.on,
    removeListener: host.removeListener,
    send: host.send,
    sendSync: host.sendSync
  },
  webFrame: {},
  webUtils: {}
}))

describe('preload native player bridge', () => {
  it('exposes every player verb on the qubit:player channels', async () => {
    await import('./preload')
    const registration = host.exposeInMainWorld.mock.calls.find(([name]) => name === 'hermesDesktop')
    const player = (registration?.[1] as { player?: Record<string, (...args: never[]) => unknown> } | undefined)
      ?.player

    expect(player).toBeDefined()
    expect(Object.keys(player ?? {}).sort()).toEqual([
      'agentRequest',
      'clearLocalData',
      'linkAwareness',
      'onState',
      'request',
      'revealMediaJob',
      'setupMediaHelpers',
      'unlinkAwareness'
    ])

    await player!.request({ type: 'status' } as never)
    expect(host.invoke).toHaveBeenCalledWith('qubit:player:request', { type: 'status' })

    const scope = { connectionId: 'local', profileId: 'default', sessionId: 's1' }
    await player!.linkAwareness(scope as never)
    expect(host.invoke).toHaveBeenCalledWith('qubit:player:link-awareness', scope)

    await player!.unlinkAwareness()
    expect(host.invoke).toHaveBeenCalledWith('qubit:player:unlink-awareness')

    await player!.agentRequest({ scope, operation: { type: 'context' } } as never)
    expect(host.invoke).toHaveBeenCalledWith('qubit:player:agent-request', {
      scope,
      operation: { type: 'context' }
    })

    await player!.setupMediaHelpers()
    expect(host.invoke).toHaveBeenCalledWith('qubit:player:setup-media-helpers')

    await player!.revealMediaJob(scope as never, 'job_1')
    expect(host.invoke).toHaveBeenCalledWith('qubit:player:reveal-media-job', { scope, jobId: 'job_1' })

    await player!.clearLocalData()
    expect(host.invoke).toHaveBeenCalledWith('qubit:player:clear-local-data')

    const listener = vi.fn()
    const dispose = player!.onState(listener) as () => void
    expect(host.on).toHaveBeenCalledWith('qubit:player:state', expect.any(Function))
    const forward = host.on.mock.calls.find(([channel]) => channel === 'qubit:player:state')?.[1] as (
      event: unknown,
      snapshot: unknown
    ) => void
    forward({}, { status: 'ready' })
    expect(listener).toHaveBeenCalledWith({ status: 'ready' })
    dispose()
    expect(host.removeListener).toHaveBeenCalledWith('qubit:player:state', expect.any(Function))
  })
})
