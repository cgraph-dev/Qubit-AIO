// Every IPC channel the preload bridge invokes must have a main-process handler,
// and every channel it listens on must actually be emitted.
//
// The renderer never touches ipcRenderer directly; it calls the typed surface that
// `preload.ts` exposes over contextBridge. That splits each channel into two string
// literals in two files — the one in preload.ts and the one in the main process —
// and nothing type-checks the pair. A rename on one side alone compiles cleanly and
// fails only at runtime, where a missing handler surfaces as an unhandled invoke
// rejection deep inside the renderer.
//
// That is not hypothetical: the rebrand renamed the main-process handlers for
// `fs:logsRoot` and `minimize-to-tray:{get,set}` to `qubit:` while preload still
// invoked `hermes:`, leaving both capabilities dead in a build that passed every
// compile.
//
// Channels are compared in FULL, brand prefix included. An earlier version of this
// file compared only the part after the prefix, on the theory that the prefix is a
// deliberately mid-migration rename surface — but that made `hermes:fs:logsRoot` and
// `qubit:fs:logsRoot` compare equal, which is precisely the breakage being tested
// for. The two sides are a wire contract: they must agree exactly.
//
// The two directions use different mechanisms and fail differently, so they are
// checked separately:
//   renderer -> main : `ipcRenderer.invoke/send` needs `ipcMain.handle/on`
//   main -> renderer : `ipcRenderer.on` needs `webContents.send`
import { readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { expect, test } from 'vitest'

const ELECTRON_DIR = join(fileURLToPath(new URL('.', import.meta.url)))

/** Every channel literal in `source` matching `pattern`. */
function channels(source: string, pattern: RegExp): string[] {
  return [...source.matchAll(pattern)].map(match => match[1])
}

function mainProcessSources(): { file: string; source: string }[] {
  return readdirSync(ELECTRON_DIR)
    .filter(name => name.endsWith('.ts') && !name.endsWith('.test.ts'))
    .map(name => ({ file: name, source: readFileSync(join(ELECTRON_DIR, name), 'utf8') }))
}

/**
 * Channels assembled at runtime from a loop over a handler table, which a literal
 * scan cannot see. Listed by hand because a test that silently skipped them would
 * be the very false-green this file exists to prevent; the
 * `every table-driven registration is accounted for` test fails when a source file
 * starts building channels but is not named here, so the list cannot drift.
 */
const TABLE_DRIVEN_REGISTRATIONS: Record<string, string[]> = {
  // hud-modifier.ts: for (const [name] of Object.entries(handlers))
  //   ipcMain.handle(`hermes:hud-modifier:${name}`, ...)
  // handler keys: 'settings:get', 'settings:set', permission
  'hud-modifier.ts': [
    'hermes:hud-modifier:settings:get',
    'hermes:hud-modifier:settings:set',
    'hermes:hud-modifier:permission',
  ],
  // command-screenshot.ts: const channel = `hermes:screenshot:${name}`
  // then ipcMain.handle(channel, ...)
  // handler keys: 'settings:get', 'settings:set', permission, capture
  'command-screenshot.ts': [
    'hermes:screenshot:settings:get',
    'hermes:screenshot:settings:set',
    'hermes:screenshot:permission',
    'hermes:screenshot:capture',
  ],
}

const preload = readFileSync(join(ELECTRON_DIR, 'preload.ts'), 'utf8')

/** Channels the renderer calls and expects a reply from. */
const invoked = new Set(channels(preload, /ipcRenderer\.invoke\(\s*'([^']+)'/g))
/** Channels the renderer listens on. */
const listened = new Set(channels(preload, /ipcRenderer\.on\(\s*'([^']+)'/g))

// Registrations arrive two ways, both real: the `ipcMain` global in modules that
// import Electron directly, and an injected `ipc` object in modules kept free of
// Electron imports so they stay unit-testable (`player-ipc.ts` takes `ipc` as a
// parameter). Scanning only `ipcMain` misses half of them.
const HANDLE = /\b(?:ipcMain|ipc)\.(?:handle|handleOnce)\(\s*'([^']+)'/g
const EMIT = /\bwebContents\.send\(\s*'([^']+)'/g

const mainSources = mainProcessSources()
const handled = new Set([
  ...mainSources.flatMap(({ source }) => channels(source, HANDLE)),
  ...Object.values(TABLE_DRIVEN_REGISTRATIONS).flat(),
])
const emitted = new Set(mainSources.flatMap(({ source }) => channels(source, EMIT)))

test('the channel scan sees all four sides (guards against a silently empty comparison)', () => {
  // Without this, a refactor of the call syntax would leave a set empty and the
  // assertions below would pass while proving nothing.
  expect(invoked.size).toBeGreaterThan(100)
  expect(listened.size).toBeGreaterThan(10)
  expect(handled.size).toBeGreaterThan(100)
  expect(emitted.size).toBeGreaterThan(10)
})

test('every channel the preload invokes has a main-process handler', () => {
  const missing = [...invoked].filter(name => !handled.has(name)).sort()
  expect(missing).toEqual([])
})

test('every channel the preload listens on is emitted by the main process', () => {
  const neverSent = [...listened].filter(name => !emitted.has(name)).sort()
  expect(neverSent).toEqual([])
})

test('every table-driven registration is accounted for', () => {
  // A source file that interpolates a channel into a handle() call registers
  // something a literal scan cannot see. Requiring it to be named above keeps the
  // blind spot visible: the next one fails here instead of silently escaping.
  const buildingChannels = mainSources
    .filter(({ source }) => /\b(?:ipcMain|ipc)\.?handle\(\s*(?:`|channel|name)/.test(source))
    .filter(({ source }) => /`(?:hermes|qubit):[^`]*\$\{/.test(source))
    .map(({ file }) => file)
    .filter(file => !(file in TABLE_DRIVEN_REGISTRATIONS))
    .sort()
  expect(buildingChannels).toEqual([])
})

test('no capability is registered under both brand prefixes', () => {
  // Two live spellings for one capability means one of them is unreachable and the
  // migration stopped halfway. Compared after the prefix here on purpose: this is
  // the one assertion where collapsing the prefix is what is being checked.
  const byCapability = new Map<string, Set<string>>()
  for (const channel of handled) {
    const key = channel.replace(/^(?:hermes|qubit):/, '')
    const names = byCapability.get(key) ?? new Set<string>()
    names.add(channel)
    byCapability.set(key, names)
  }
  const doubled = [...byCapability]
    .filter(([, names]) => names.size > 1)
    .map(([name, names]) => `${name} -> ${[...names].sort().join(', ')}`)
    .sort()
  expect(doubled).toEqual([])
})
