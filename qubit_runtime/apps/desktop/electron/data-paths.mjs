// data-paths.mjs — the pure path-resolution core, shared by the desktop app
// (via data-paths.ts, a typed re-export) and the CI smoke driver (which runs
// under Node's type-stripping and therefore cannot import the app's
// extensionless TypeScript directly). No Electron imports here; only node:path.
//
// data-paths.ts re-exports these names and adds the TypeScript-facing
// `HermesHomeOptions` interface. Keep the two in lockstep: every behavior in
// this file is exercised by data-paths.test.ts through the re-export.

import fs from 'node:fs'
import path from 'node:path'

/** A QUBIT_HOME rooted inside a `profiles/` directory names the profile's
 * parent (the home), not the profile directory itself. */
function normalizeHermesHomeRoot(hermesHome, pathModule) {
  if (!hermesHome) {
    return hermesHome
  }
  const resolved = pathModule.resolve(String(hermesHome))
  const parent = pathModule.dirname(resolved)
  if (pathModule.basename(parent).toLowerCase() === 'profiles') {
    return pathModule.dirname(parent)
  }
  return resolved
}

function platformDefaultNamedHome(name, home, env, platform) {
  const suffix = env.HERMES_DATA_DIR_SUFFIX || ''
  if (platform === 'win32') {
    const base = (env.LOCALAPPDATA || '').trim() || path.win32.join(home, 'AppData', 'Local')
    return path.win32.join(base, name) + suffix
  }
  return path.posix.join(home, `.${name}`) + suffix
}

export function platformDefaultHermesHome(home, env = process.env, platform = process.platform) {
  return platformDefaultNamedHome('qubit', home, env, platform)
}

// Canonical rebrand spellings, declared HERE rather than only in the .ts shim:
// Vite resolves extensionless `./data-paths` to THIS file (the .mjs), so an
// alias declared only in data-paths.ts is undefined for every importer under
// vitest. Function declarations, not `const` aliases, so a hoisted
// `__vite_ssr_exportName__` thunk can read them before the module body runs.
export function platformDefaultQubitHome(home, env = process.env, platform = process.platform) {
  return platformDefaultHermesHome(home, env, platform)
}

/** The pre-rebrand default root, read-old only. Nothing writes here. */
function platformDefaultLegacyHome(home, env = process.env, platform = process.platform) {
  return platformDefaultNamedHome('hermes', home, env, platform)
}

export function resolveDesktopUserData(defaultPath, env = process.env) {
  return env.HERMES_DESKTOP_USER_DATA_DIR
    ? path.resolve(env.HERMES_DESKTOP_USER_DATA_DIR)
    : defaultPath + (env.HERMES_DATA_DIR_SUFFIX || '')
}

export function resolveDesktopHermesHome({ home, env = process.env, platform = process.platform, directoryExists = () => false, readWindowsHome = () => null }) {
  const paths = platform === 'win32' ? path.win32 : path.posix
  if (env.QUBIT_HOME) {
    return normalizeHermesHomeRoot(env.QUBIT_HOME, paths)
  }
  // Fresh-install rehearsals must not touch the real Qubit home.
  if (env.HERMES_DESKTOP_USER_DATA_DIR) {
    return paths.join(paths.resolve(env.HERMES_DESKTOP_USER_DATA_DIR), 'hermes-home')
  }
  if (platform === 'win32' && env.QUBIT_HOME === undefined) {
    // Explorer can miss setx changes. An explicit empty value opts out of that fallback.
    const registryHome = readWindowsHome()
    if (registryHome) {
      return normalizeHermesHomeRoot(registryHome, paths)
    }
  }
  const defaultHome = platformDefaultHermesHome(home, env, platform)
  // Read-old only: an ordinary install whose data still lives under the
  // pre-rebrand root keeps resolving there until it is migrated deliberately.
  // Isolated suffix runs never adopt legacy state.
  if (!env.HERMES_DATA_DIR_SUFFIX) {
    const legacy = platformDefaultLegacyHome(home, env, platform)
    if (!directoryExists(defaultHome) && directoryExists(legacy)) {
      return legacy
    }
  }
  return defaultHome
}

/** Canonical rebrand spelling of {@link resolveDesktopHermesHome}. */
export function resolveDesktopQubitHome(options) {
  return resolveDesktopHermesHome(options)
}

/**
 * WHERE THE DESKTOP'S OWN HOME LIVES — `<shared root>-desktop`, i.e. `~/.qubit-desktop`
 * beside `~/.qubit` (or `%LOCALAPPDATA%\qubit-desktop`).
 *
 * Why the desktop needs its own root at all: a gateway takes an exclusive flock
 * on `<home>/gateway.lock` per profile home, so two surfaces pointed at ONE
 * root can never run together — whichever starts second fails with
 * `Gateway runtime already owns profile <home>` (`gateway/runtime_ownership.py`).
 * The desktop is a *second front door to the same install*, not a second
 * install: this root holds only the desktop's own home (`qubit-desktop/`, the
 * backend home it spawns) and one marker file recording that it owns this
 * machine. The code tree (`<shared>/qubit-agent`) stays exactly where it is.
 *
 * BESIDE, not inside: a root nested in the shared home would make the shared
 * home exist by virtue of the desktop's own directories, so "is there already a
 * home here?" could never be answered honestly. Built from the textual path —
 * never `path.resolve`, which would follow a symlink at the same name.
 */
export function desktopOwnHomeRoot(sharedHome) {
  // Already a per-run root (rehearsals append `HERMES_DATA_DIR_SUFFIX` to the
  // shared name) — do not append it twice.
  const suffix = (process.env.HERMES_DATA_DIR_SUFFIX || '').trim()

  if (suffix && sharedHome.endsWith(suffix)) {
    return `${sharedHome.slice(0, -suffix.length)}-desktop${suffix}`
  }

  return path.join(path.dirname(sharedHome), `${path.basename(sharedHome)}-desktop`)
}

/** The shared root's own name: `qubit` for `~/.qubit` (POSIX) or `%LOCALAPPDATA%\qubit`. */
export function sharedHomeName(sharedHome) {
  return path.basename(sharedHome)
}

/** Path separator the shared root was spelled with (so Windows paths stay Windows paths). */
function separatorOf(sharedHome) {
  return String(sharedHome).includes('\\') && !String(sharedHome).includes('/') ? '\\' : path.sep
}

/**
 * The desktop's footprint, derived purely from the root a launch resolves.
 * `<shared root>/desktop` must never be used as a home: the install tree lives
 * there (`<shared>/qubit-agent`), so blessing it would name the wrong home.
 */
export function desktopHomeLayout(sharedHome) {
  const sep = separatorOf(sharedHome)
  const ownRoot = desktopOwnHomeRoot(sharedHome)
  const base = {
    ownHome: `${ownRoot}${sep}qubit-desktop`,
    ownRoot,
    // The province: one link through which the marker is written, never a
    // directory this desktop must own separately.
    profile: `${ownRoot}${sep}profile`,
    sharedHome
  }

  return { ...base, configPath: `${base.profile}${sep}config.yaml`, linkPath: base.profile }
}

/**
 * One rung of the own-home ladder, resolved WITHOUT side effects; the caller
 * creates whatever is missing.
 *
 * The rung is decided by the province marker (`<ownRoot>/profile/config.yaml`):
 * a desktop that already owns its root keeps it, forever after, and explicit
 * settings win before any of that.
 *
 * - `explicit` `QUBIT_HOME` / a rehearsal userData / Windows: the home is the
 *   caller's decision, used verbatim; nothing is created.
 * - `own`      the marker exists: this desktop owns `<ownRoot>`. Its backend home
 *   is `<ownRoot>/qubit-desktop`, and the shared name is left alone.
 * - `link`     no marker, and a shared home already exists (a CLI's, an
 *   installer's, legacy state) — or a symlink already stands at the shared name
 *   (a half-finished first run). The desktop links `<ownRoot>/profile` to the
 *   own data dir and then must point the shared name at that link.
 * - `first`    nothing anywhere: a single-home user. They keep the documented
 *   default and no directory or link is created — the first-launch bootstrap
 *   then installs into that home, exactly as an installer would.
 */
export function desktopOwnHomePlan({
  home,
  env = process.env,
  platform = process.platform,
  directoryExists = () => false,
  readWindowsHome = () => null
}) {
  const explicit = Boolean(env.QUBIT_HOME) || platform === 'win32' || Boolean(env.HERMES_DESKTOP_USER_DATA_DIR)
  const layout = desktopHomeLayout(
    resolveDesktopHermesHome({ home, env, platform, directoryExists, readWindowsHome })
  )

  if (explicit) {
    return { ...layout, home: layout.sharedHome, status: 'explicit' }
  }

  if (fs.existsSync(layout.configPath)) {
    return { ...layout, home: layout.ownHome, status: 'own' }
  }

  if (directoryExists(layout.sharedHome) || isSymlink(layout.linkPath)) {
    return { ...layout, home: layout.ownHome, status: 'link' }
  }

  return { ...layout, home: layout.sharedHome, status: 'first' }
}

function isSymlink(target) {
  try {
    return fs.lstatSync(target).isSymbolicLink()
  } catch {
    return false
  }
}

/**
 * Create what the plan's rung needs before the backend can be spawned, and
 * return the home to spawn it with — or null while the own root is not usable
 * yet (the caller still starts, so the user can read why, but no backend is
 * spawned against a home a terminal may own).
 *
 * Idempotent. Only `link` touches anything outside the own root, and only with
 * proven-at-run-time operations: a `rename` of the shared home INTO the own
 * root's province (one atomic move, same filesystem), the link that makes the
 * shared name resolve to the own data dir, then the marker that makes this
 * deterministic from the next launch on.
 */
export function prepareOwnHomeRoot(plan) {
  if (plan.status === 'explicit' || plan.status === 'first') {
    return plan.home
  }

  try {
    fs.mkdirSync(plan.ownRoot, { recursive: true })
    fs.mkdirSync(plan.ownHome, { recursive: true, mode: 0o700 })

    // The data this desktop owns, inside its own root:
    // `~/.qubit-desktop/qubit`.
    const ownData = path.join(plan.ownRoot, sharedHomeName(plan.sharedHome))

    // Fresh link: `<ownRoot>/profile` names the data this desktop will own.
    const created = !isSymlink(plan.linkPath)

    if (created) {
      fs.symlinkSync(ownData, plan.linkPath, process.platform === 'win32' ? 'junction' : 'dir')
    }

    const existing = resolveLink(plan.linkPath)

    // A link we did not just create must name the data this desktop owns. On a
    // first run its target does not exist yet, so a null answer is fine there.
    if (!created && existing !== null && existing !== resolveLink(ownData)) {
      throw new Error(`${plan.linkPath} does not name ${ownData}`)
    }

    if (plan.status === 'link') {
      adoptSharedHome(plan)
    }

    writeMarker(plan)
  } catch (error) {
    process.stderr.write(`[hermes] could not prepare ${plan.ownRoot}: ${error?.message ?? error}\n`)

    return null
  }

  return plan.ownHome
}

/** Where *target* leads, every link in the chain resolved. */
function resolveLink(target) {
  try {
    return fs.realpathSync.native(target)
  } catch {
    return null
  }
}

/**
 * Write the marker under the province link, without guessing whether the link
 * target exists yet: `link` resolves a directory of the data this desktop owns
 * (a target created now when missing), so writing stops at the LINK and never
 * at a path the link's target would have to exist for.
 */
function writeMarker(plan) {
  const marker = `${plan.linkPath}${path.sep}config.yaml`
  const temp = `${plan.linkPath}${path.sep}(config.yaml.desktop.new)`

  fs.writeFileSync(
    temp,
    [
      '# Written by Qubit Desktop: this root is the desktop\'s own home. Never edit by hand.',
      'version: 1',
      `qubit-home: ${plan.sharedHome}`,
      `desktop-home: ${plan.ownHome}`,
      ''
    ].join('\n'),
    'utf8'
  )
  fs.renameSync(temp, marker)
}

function adoptSharedHome(plan) {
  const shared = plan.sharedHome

  if (isSymlink(shared)) {
    const target = resolveLink(shared)

    if (target !== null && target === resolveLink(plan.linkPath)) {
      return
    }

    throw new Error(`${shared} is a symlink that does not name this desktop's home`)
  }

  if (!fs.existsSync(shared)) {
    return
  }

  // Move the whole shared home inside the province (same filesystem: one
  // rename) so the CLI's install tree and data stay reachable, then point its
  // old name at the data dir this desktop now owns.
  const destination = path.join(plan.ownRoot, sharedHomeName(shared))

  if (fs.existsSync(destination)) {
    throw new Error(`${destination} already exists; not overwriting the shared home`)
  }

  fs.renameSync(shared, destination)
  fs.symlinkSync(plan.linkPath, shared, process.platform === 'win32' ? 'junction' : 'dir')
}
