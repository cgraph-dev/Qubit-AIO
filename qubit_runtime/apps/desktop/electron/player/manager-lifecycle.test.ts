import { EventEmitter } from 'node:events'

import type { BrowserWindow, Session, WebContentsView } from 'electron'
import { describe, expect, it, vi } from 'vitest'

import { PlayerManager } from './manager'

vi.mock('electron', () => ({
  app: { getLocale: () => 'en-US' },
  components: undefined,
  BrowserWindow: class {},
  dialog: {},
  Menu: {},
  screen: {
    getDisplayMatching: () => ({ workArea: { x: 0, y: 0, width: 1920, height: 1080 } })
  },
  session: {},
  WebContentsView: class {}
}))

function fixture() {
  const webContents = Object.assign(new EventEmitter(), {
    id: 41,
    setWindowOpenHandler: vi.fn(),
    loadURL: vi.fn(async () => undefined),
    isDestroyed: vi.fn(() => false),
    getURL: vi.fn(() => 'https://www.youtube.com/'),
    canGoBack: vi.fn(() => false),
    canGoForward: vi.fn(() => false),
    executeJavaScript: vi.fn(async () => true),
    close: vi.fn()
  })
  const guest = { webContents, setBounds: vi.fn() } as unknown as WebContentsView
  const ownerWebContents = Object.assign(new EventEmitter(), {
    on: EventEmitter.prototype.on,
    send: vi.fn()
  })
  const owner = Object.assign(new EventEmitter(), {
    id: 7,
    isDestroyed: vi.fn(() => false),
    contentView: { addChildView: vi.fn(), removeChildView: vi.fn() },
    webContents: ownerWebContents,
    getContentSize: vi.fn(() => [1280, 720] as [number, number])
  }) as unknown as BrowserWindow
  const ses = {
    setPermissionRequestHandler: vi.fn(),
    setPermissionCheckHandler: vi.fn()
  } as unknown as Session
  const manager = new PlayerManager({
    allowFixture: false,
    createView: () => guest,
    getSession: () => ses
  })

  return { manager, owner, guest, webContents }
}

describe('PlayerManager native lifecycle', () => {
  it('opens YouTube, applies media controls, pins, navigates home and closes', async () => {
    const { manager, owner, guest, webContents } = fixture()

    try {
      const opened = await manager.request(owner, { type: 'open', source: 'youtube' })
      expect(opened.status).toBe('loading')
      expect(opened.source).toBe('youtube')
      expect(opened.runtime.state).toBe('not-required')
      expect(webContents.loadURL).toHaveBeenCalledWith('https://www.youtube.com/')
      expect(guest.setBounds).toHaveBeenCalled()

      webContents.emit('did-finish-load')
      const ready = await manager.request(owner, { type: 'status' })
      expect(ready.status).toBe('ready')
      expect(ready.guestId).toBe(41)

      const paused = await manager.request(owner, {
        type: 'media-control',
        action: 'pause'
      })
      expect(paused.paused).toBe(true)
      expect(webContents.executeJavaScript).toHaveBeenCalled()

      const muted = await manager.request(owner, { type: 'media-control', action: 'mute' })
      expect(muted.muted).toBe(true)

      const pinned = await manager.request(owner, { type: 'pin', pinned: true })
      expect(pinned.pinned).toBe(true)

      await manager.request(owner, { type: 'home' })
      expect(webContents.loadURL).toHaveBeenCalledTimes(2)

      const closed = await manager.request(owner, { type: 'close' })
      expect(closed.status).toBe('closed')
      expect(closed.source).toBeNull()
    } finally {
      manager.dispose()
    }
  })

  it('refuses media controls before the guest is ready', async () => {
    const { manager, owner } = fixture()

    try {
      await manager.request(owner, { type: 'open', source: 'youtube' })

      await expect(
        manager.request(owner, { type: 'media-control', action: 'play' })
      ).rejects.toThrow('player-media-control-unavailable')
    } finally {
      manager.dispose()
    }
  })

  it('opens a protected service with the typed runtime refusal when Widevine is unavailable', async () => {
    const { manager, owner, webContents } = fixture()

    try {
      const snapshot = await manager.request(owner, { type: 'open', source: 'netflix' })
      expect(snapshot.status).toBe('error')
      expect(snapshot.runtime.state).toBe('unsupported')
      expect(snapshot.error).toBe('protected-playback-runtime-unavailable')
      expect(webContents.loadURL).not.toHaveBeenCalled()
    } finally {
      manager.dispose()
    }
  })
})
