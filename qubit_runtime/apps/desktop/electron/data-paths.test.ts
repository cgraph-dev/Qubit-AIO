import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'

import { afterEach, test, vi } from 'vitest'

import {
  desktopOwnHomePlan,
  platformDefaultHermesHome,
  prepareOwnHomeRoot,
  resolveDesktopHermesHome,
  resolveDesktopUserData
} from './data-paths'
import { controlSocketPath } from './ssh-connection'

afterEach((): void => {
  vi.unstubAllEnvs()
})

test.skipIf(process.platform === 'win32')('local SSH sockets use the suffixed default root', (): void => {
  vi.stubEnv('HERMES_DATA_DIR_SUFFIX', 'magic-test')
  const socket: string = controlSocketPath('user', 'host', 22)

  assert.equal(path.dirname(socket), path.join(platformDefaultHermesHome(os.homedir()), 'desktop-ssh'))
})

test('default data roots append the suffix literally on each platform', (): void => {
  for (const platform of ['linux', 'darwin', 'win32'] as const) {
    const paths: typeof path = platform === 'win32' ? path.win32 : path.posix
    const home: string = platform === 'win32' ? 'C:\\Users\\test' : '/home/test'
    const local: string = paths.join(home, 'AppData', 'Local')
    const userData: string = paths.join(home, 'app-data', 'Hermes')
    const base: string = platform === 'win32' ? paths.join(local, 'qubit') : paths.join(home, '.qubit')

    for (const suffix of ['', '-asdfasdf', 'magic-test', ' spaced ']) {
      const env: NodeJS.ProcessEnv = { LOCALAPPDATA: local, HERMES_DATA_DIR_SUFFIX: suffix }

      assert.equal(platformDefaultHermesHome(home, env, platform), base + suffix)
      assert.equal(resolveDesktopUserData(userData, env), userData + suffix)
      assert.equal(
        resolveDesktopHermesHome({ home, env, platform, directoryExists: (): boolean => false }),
        base + suffix
      )
    }
  }
})

test('explicit homes and userData retain precedence, and suffixed Windows homes never use legacy state', (): void => {
  const home: string = '/home/test'

  const env: NodeJS.ProcessEnv = {
    HERMES_DATA_DIR_SUFFIX: 'magic-test',
    QUBIT_HOME: '/explicit/home',
    HERMES_DESKTOP_USER_DATA_DIR: '/explicit/electron'
  }

  assert.equal(resolveDesktopUserData('/default/electron', env), path.resolve(env.HERMES_DESKTOP_USER_DATA_DIR!))
  assert.equal(resolveDesktopHermesHome({ home, env, platform: 'linux' }), env.QUBIT_HOME)
  delete env.QUBIT_HOME
  assert.equal(resolveDesktopHermesHome({ home, env, platform: 'linux' }), '/explicit/electron/hermes-home')

  const windowsHome: string = 'C:\\Users\\test'
  const windowsEnv: NodeJS.ProcessEnv = { HERMES_DATA_DIR_SUFFIX: 'magic-test' }
  const expected: string = path.win32.join(windowsHome, 'AppData', 'Local', 'qubitmagic-test')

  assert.equal(
    resolveDesktopHermesHome({
      home: windowsHome,
      env: windowsEnv,
      platform: 'win32',
      directoryExists: (): boolean => true
    }),
    expected
  )
  assert.equal(
    resolveDesktopHermesHome({
      home: windowsHome,
      env: windowsEnv,
      platform: 'win32',
      readWindowsHome: (): string => 'C:\\custom'
    }),
    'C:\\custom'
  )
})

test('a pre-rebrand home is read-old only when the canonical root is absent', (): void => {
  const home: string = '/home/test'
  const legacy: string = path.join(home, '.hermes')

  assert.equal(
    resolveDesktopHermesHome({
      home,
      env: {},
      platform: 'linux',
      directoryExists: (directory: string): boolean => directory === legacy
    }),
    legacy
  )
  assert.equal(
    resolveDesktopHermesHome({
      home,
      env: {},
      platform: 'linux',
      directoryExists: (): boolean => true
    }),
    path.join(home, '.qubit')
  )
})

test('Windows reads a legacy LOCALAPPDATA\\hermes root without writing to it', (): void => {
  const home: string = 'C:\\Users\\test'
  const local: string = 'C:\\Users\\test\\AppData\\Local'
  const legacy: string = path.win32.join(local, 'hermes')

  assert.equal(
    resolveDesktopHermesHome({
      home,
      env: { LOCALAPPDATA: local },
      platform: 'win32',
      directoryExists: (directory: string): boolean => directory === legacy
    }),
    legacy
  )
})

test('suffixed runs never adopt legacy state', (): void => {
  const home: string = '/home/test'
  const legacy: string = path.join(home, '.hermesmagic-test')

  assert.equal(
    resolveDesktopHermesHome({
      home,
      env: { HERMES_DATA_DIR_SUFFIX: 'magic-test' },
      platform: 'linux',
      directoryExists: (directory: string): boolean => directory === legacy
    }),
    path.join(home, '.qubitmagic-test')
  )
})

// ── The desktop's own home (real filesystem: the ladder creates links) ───────
const scratchRoots: string[] = []

const scratchUser = (): { home: string; shared: string } => {
  const home: string = fs.mkdtempSync(path.join(os.tmpdir(), 'qubit-own-home-'))
  scratchRoots.push(home)

  return { home, shared: path.join(home, '.qubit') }
}

afterEach((): void => {
  for (const root of scratchRoots.splice(0)) {
    fs.rmSync(root, { force: true, recursive: true })
  }
})

/** The root the desktop owns, beside the shared `~/.qubit`. */
const ownRootOf = (shared: string): string =>
  path.join(path.dirname(shared), `${path.basename(shared)}-desktop`)

const isLink = (target: string): boolean => fs.lstatSync(target).isSymbolicLink()

const planFor = (home: string, env: NodeJS.ProcessEnv = {}) =>
  desktopOwnHomePlan({
    home,
    env,
    platform: 'linux',
    directoryExists: (directory: string): boolean => fs.existsSync(directory) && fs.statSync(directory).isDirectory()
  })

/** Silence the deliberate stderr diagnostic a failed prepare emits. */
const quietly = <T>(run: () => T): T => {
  const stderr = vi.spyOn(process.stderr, 'write').mockImplementation(() => true)

  try {
    return run()
  } finally {
    stderr.mockRestore()
  }
}

test('a first run keeps the documented home and creates nothing', (): void => {
  const { home, shared } = scratchUser()
  const plan = planFor(home)

  assert.equal(plan.status, 'first')
  assert.equal(plan.home, shared)
  assert.equal(prepareOwnHomeRoot(plan), shared)
  // No directory, no link: a single-home user gets exactly the documented default.
  assert.equal(fs.existsSync(shared), false)
  assert.equal(fs.existsSync(ownRootOf(shared)), false)
})

test('an explicit home is used verbatim and nothing is created', (): void => {
  const { home, shared } = scratchUser()
  fs.mkdirSync(path.join(shared, 'qubit-desktop'), { recursive: true })

  const plan = planFor(home, { QUBIT_HOME: shared })

  assert.equal(plan.status, 'explicit')
  assert.equal(plan.home, shared)
  assert.equal(prepareOwnHomeRoot(plan), shared)
  assert.equal(fs.existsSync(ownRootOf(shared)), false)
})

test('a rehearsal userData is never reached out of', (): void => {
  const { home } = scratchUser()
  const userData = path.join(home, 'rehearsal')

  const plan = planFor(home, { HERMES_DESKTOP_USER_DATA_DIR: userData })

  assert.equal(plan.status, 'explicit')
  assert.equal(plan.home, path.join(userData, 'hermes-home'))
  assert.equal(fs.existsSync(ownRootOf(plan.sharedHome)), false)
})

test('a user with a shared home gets its own, and `~/.qubit` keeps working', (): void => {
  const { home, shared } = scratchUser()
  const install = path.join(shared, 'qubit-agent')
  fs.mkdirSync(install, { recursive: true })

  const plan = planFor(home)
  assert.equal(plan.status, 'link')
  assert.equal(plan.ownRoot, ownRootOf(shared))

  const backendHome = prepareOwnHomeRoot(plan)
  assert.equal(backendHome, path.join(ownRootOf(shared), 'qubit-desktop'))
  assert.equal(fs.statSync(backendHome).isDirectory(), true)
  // `~/.qubit` is a link to the data dir this desktop now owns. The CLI's
  // install tree was moved inside the own root — never deleted or rewritten.
  assert.equal(isLink(shared), true)
  assert.equal(fs.realpathSync(shared), fs.realpathSync(path.join(ownRootOf(shared), '.qubit')))
  // The old home's contents came along, install tree included.
  assert.equal(fs.statSync(path.join(ownRootOf(shared), '.qubit', 'qubit-agent')).isDirectory(), true)

  // The marker decides every later launch, and preparing again is a no-op.
  assert.equal(planFor(home).status, 'own')
  assert.equal(fs.readFileSync(planFor(home).configPath, 'utf8').includes(`desktop-home: ${backendHome}`), true)
  assert.equal(prepareOwnHomeRoot(planFor(home)), backendHome)
  assert.equal(isLink(shared), true)
})

test('an owned root survives a CLI putting a real home back at the shared name', (): void => {
  const { home, shared } = scratchUser()
  fs.mkdirSync(shared)
  fs.mkdirSync(path.join(ownRootOf(shared), 'qubit-desktop'), { recursive: true })
  fs.mkdirSync(path.join(ownRootOf(shared), '.qubit'), { recursive: true })
  fs.symlinkSync(path.join(ownRootOf(shared), '.qubit'), path.join(ownRootOf(shared), 'profile'), 'dir')
  fs.writeFileSync(path.join(ownRootOf(shared), 'profile', 'config.yaml'), 'version: 1\n')

  // The marker is the authority: use the own root, and leave the shared name as
  // the CLI's now.
  const plan = planFor(home)
  assert.equal(plan.status, 'own')
  assert.equal(prepareOwnHomeRoot(plan), path.join(ownRootOf(shared), 'qubit-desktop'))
  assert.equal(isLink(shared), false)
})

test('a collision in the own root leaves the shared home where it was and spawns nothing', (): void => {
  const { home, shared } = scratchUser()
  fs.mkdirSync(shared)
  // The destination name is taken: the rename must be refused, not clobbered.
  fs.mkdirSync(path.join(ownRootOf(shared), '.qubit'), { recursive: true })
  fs.writeFileSync(path.join(ownRootOf(shared), '.qubit', 'not-yours.txt'), 'keep')
  fs.writeFileSync(path.join(shared, 'not-yours.txt'), 'keep')

  assert.equal(quietly(() => prepareOwnHomeRoot(planFor(home))), null)
  assert.equal(fs.readFileSync(path.join(ownRootOf(shared), '.qubit', 'not-yours.txt'), 'utf8'), 'keep')
  assert.equal(fs.readFileSync(path.join(shared, 'not-yours.txt'), 'utf8'), 'keep')
  assert.equal(isLink(shared), false)
  assert.equal(fs.statSync(shared).isDirectory(), true)
})

test('a shared name that is a symlink elsewhere is never followed or rewritten', (): void => {
  const { home, shared } = scratchUser()
  fs.mkdirSync(path.join(home, 'other'))
  fs.symlinkSync(path.join(home, 'other'), shared, 'dir')

  assert.equal(planFor(home).status, 'link')
  assert.equal(quietly(() => prepareOwnHomeRoot(planFor(home))), null)
  assert.equal(fs.realpathSync(shared), fs.realpathSync(path.join(home, 'other')))
})
