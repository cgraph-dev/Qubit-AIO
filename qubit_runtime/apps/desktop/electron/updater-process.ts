import { spawn, type SpawnOptions, spawnSync, type SpawnSyncOptions, type SpawnSyncReturns } from 'node:child_process'
import { existsSync, realpathSync, statSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'

/** Historical layouts still matter when uninstalling or migrating a source install. */
export function resolveVenvDir(updateRoot: string): string {
  for (const name of ['venv', '.venv']) {
    const candidate: string = path.join(updateRoot, name)

    try {
      if (statSync(candidate).isDirectory()) {
        return candidate
      }
    } catch {
      // Try the other supported layout, then retain the legacy diagnostic path.
    }
  }

  return path.join(updateRoot, 'venv')
}

import { platformDefaultHermesHome } from './data-paths'
import { hiddenWindowsChildOptions } from './windows-child-options'

/**
 * The home children spawned straight from here (updater scripts, launcher
 * probes) search for their user-bin launchers. main.ts sets `QUBIT_HOME` to the
 * home this launch actually resolved, so a plain launch and an explicit env var
 * both reach the same dirs; never blank, because that would silently drop the
 * `<home>/bin` probe on the default home.
 */
function launcherSearchHome(): string {
  return process.env.QUBIT_HOME || platformDefaultHermesHome(os.homedir())
}

/** Exact installation identity; PATH may refer to another checkout. */
export function resolveInstallationLauncher(
  updateRoot: string,
  isWindows: boolean = process.platform === 'win32',
  hermesHome: string = launcherSearchHome()
): string | null {
  const names: string[] = launcherNames(isWindows)

  // The published launcher sits beside the runtime, which for a Qubit checkout
  // is the vendored qubit_runtime/ — the installer itself invokes
  // "$(runtime_dir)/.qubit/bin/qubit", and runtime_dir() is that directory.
  // Checking only <root>/.qubit/bin missed every Qubit install and reported
  // "no usable Qubit install" for one that had just completed successfully.
  for (const base of [updateRoot, path.join(updateRoot, 'qubit_runtime')]) {
    for (const dir of ['.qubit', '.hermes']) {
      for (const name of names) {
        const candidate: string = path.join(base, dir, 'bin', name)

        if (stagedFileExists(candidate)) {
          return candidate
        }
      }
    }
  }

  // Earlier PM installers published only to user-bin. Trust that historical
  // launcher only after its existing version surface proves exact source identity.
  //
  // The marker lives with the runtime: an upstream (hermes) checkout has
  // qubit_cli/ at its root, while a Qubit checkout vendors it under
  // qubit_runtime/. Checking only the root made every Qubit install look like it
  // had no launcher, so the desktop reported "no usable Qubit install" for a
  // complete install and fell back to first-run setup forever.
  const runtimeRoot: string = stagedFileExists(path.join(updateRoot, 'qubit_runtime', 'qubit_cli', '_launchers.py'))
    ? path.join(updateRoot, 'qubit_runtime')
    : updateRoot

  if (stagedFileExists(path.join(runtimeRoot, 'qubit_cli', '_launchers.py'))) {
    const defaultHome: string = platformDefaultHermesHome(os.homedir(), process.env, isWindows ? 'win32' : 'linux')

    const dirs: string[] = isWindows
      ? [
          path.join(hermesHome || defaultHome, 'bin'),
          path.join(defaultHome, 'bin'),
          path.join(path.dirname(updateRoot), 'bin')
        ]
      : [path.join(os.homedir(), '.local', 'bin'), path.join(hermesHome || defaultHome, 'bin')]

    for (const dir of new Set(dirs)) {
      for (const name of names) {
        const candidate: string = path.join(dir, name)

        if (stagedFileExists(candidate) && launcherTargetsInstallation(candidate, updateRoot)) {
          return candidate
        }
      }
    }
  }

  // An old shim is a migration rung, never a damaged PM install fallback.
  if (!existsSync(path.join(updateRoot, 'pm'))) {
    for (const name of names) {
      const legacy: string = path.join(updateRoot, 'venv', isWindows ? 'Scripts' : 'bin', name)

      if (stagedFileExists(legacy)) {
        return legacy
      }
    }
  }

  return null
}

// Canonical launcher names first, then the pre-rebrand aliases: an install
// published before the rename is still a valid installation identity.
function launcherNames(isWindows: boolean): string[] {
  return isWindows ? ['qubit.exe', 'qubit.cmd', 'hermes.exe', 'hermes.cmd'] : ['qubit', 'hermes']
}

// cmd.exe re-parses its command line, so a launcher path carrying any of
// these would change the command instead of naming a file.
const CMD_UNSAFE_PATH: RegExp = /["%&|<>^\r\n]/

/** The exact argv a launcher `--version` probe runs, screened for cmd.exe. */
function launcherVersionProbe(
  launcher: string,
  cwd?: string,
  versionArgs: string[] = ['--version']
): { command: string; args: string[]; options: SpawnSyncOptions } | null {
  // Node refuses to exec a .cmd directly (CVE-2024-27980), and `shell:true`
  // would hand an interpolated path to cmd.exe wholesale. Invoke cmd.exe
  // explicitly instead: a fixed argv, the path quoted verbatim and screened
  // for cmd metacharacters, and nothing else for the shell to interpret.
  const viaCmd: boolean = process.platform === 'win32' && /\.cmd$/i.test(launcher)

  if (viaCmd && CMD_UNSAFE_PATH.test(launcher)) {
    return null
  }

  return {
    command: viaCmd ? (process.env.ComSpec ?? 'cmd.exe') : launcher,
    args: viaCmd
      ? ['/d', '/s', '/c', `""${launcher}" ${versionArgs.join(' ')}"`]
      : versionArgs,
    options: {
      cwd,
      encoding: 'utf8' as const,
      timeout: 15000,
      windowsHide: true,
      windowsVerbatimArguments: viaCmd,
      env: cwd ? { ...process.env, HERMES_INSTALL_ROOT: cwd } : process.env
    }
  }
}

/** The install directory a launcher's `--version` surface reports, if any. */
function launcherInstallDirectory(launcher: string, cwd?: string): string | null {
  // Two launcher shapes answer --version differently, and both are valid:
  //   * the vendored-runtime launcher (`qubit-agent`, upstream-derived) prints
  //     "Install directory: <path>" from --version directly;
  //   * the full CLI (`qubit`, from [project.scripts]) prints only its product
  //     version for --version, and carries the same block on `qubit agent
  //     --version`.
  // Probing only the first shape made every install reached through the full
  // CLI look launcher-less, so the desktop reported "no usable Qubit install"
  // for a complete install. Try the plain form, then the agent form.
  const attempts: string[][] = [['--version'], ['agent', '--version']]

  for (const extraArgs of attempts) {
    const probe = launcherVersionProbe(launcher, cwd, extraArgs)

    if (!probe) {
      return null
    }

    try {
      const result: SpawnSyncReturns<string | Buffer> = spawnSync(probe.command, probe.args, probe.options)

      if (result.error || result.status !== 0) {
        continue
      }

      const reported = /^Install directory: (.+)$/m.exec(String(result.stdout))?.[1]?.trim()

      if (reported) {
        return reported
      }
    } catch {
      continue
    }
  }

  return null
}

export function launcherTargetsInstallation(launcher: string, root: string): boolean {
  try {
    const reported: string | null = launcherInstallDirectory(launcher, root)

    return reported !== null && realpathSync(reported) === realpathSync(root)
  } catch {
    return false
  }
}

/**
 * A source install outside the canonical root (install.sh --dir, a setup-qubit.sh
 * clone) is reachable only through the user-bin launcher it published. A
 * Finder/Dock launch inherits a PATH without ~/.local/bin, so the fixed
 * locations must be probed directly. The returned root is still resolved and
 * probed by the caller like any other installed runtime.
 */
export function userLauncherInstallRoot(
  isWindows: boolean = process.platform === 'win32',
  hermesHome: string = launcherSearchHome()
): { launcher: string; root: string } | null {
  for (const launcher of userBinLaunchers(isWindows, hermesHome)) {
    const root: string | null = launcherInstallDirectory(launcher)

    if (root && (existsSync(path.join(root, 'qubit_cli', 'main.py')) || existsSync(path.join(root, 'hermes_cli', 'main.py')))) {
      return { launcher, root }
    }
  }

  return null
}

/** Published user-bin launchers at fixed locations: a GUI launch's PATH may omit them. */
function userBinLaunchers(isWindows: boolean, hermesHome: string, extraDirs: string[] = []): string[] {
  const defaultHome: string = platformDefaultHermesHome(os.homedir(), process.env, isWindows ? 'win32' : 'linux')

  const dirs: string[] = isWindows
    ? [path.join(hermesHome || defaultHome, 'bin'), path.join(defaultHome, 'bin'), ...extraDirs]
    : [path.join(os.homedir(), '.local', 'bin'), path.join(hermesHome || defaultHome, 'bin'), ...extraDirs]

  return [...new Set(dirs)]
    .flatMap((dir: string): string[] => launcherNames(isWindows).map((name: string): string => path.join(dir, name)))
    .filter(stagedFileExists)
}

/** File prerequisites only: dependency recovery remains reachable through update. */
export function windowsUpdatePrerequisiteError(updateRoot: string, hermesHome?: string): string | null {
  if (!resolveInstallationLauncher(updateRoot, true, hermesHome)) {
    return `Update aborted: the installation launcher under ${updateRoot} is missing. Repair this installation before retrying.`
  }

  const maintainedDir: string = path.join(updateRoot, 'scripts', 'desktop-update')

  if (existsSync(maintainedDir)) {
    for (const name of ['windows.ps1']) {
      const candidate: string = path.join(maintainedDir, name)

      if (!stagedFileExists(candidate)) {
        return `Update aborted: ${candidate} is missing or unreadable. Repair the installation and review antivirus quarantine before retrying.`
      }
    }
  }

  return null
}

export interface UpdaterChild {
  pid?: number
  unref: () => void
}

export interface ResolveUpdateScriptHandoffDeps {
  isWindows?: boolean
  fileExists?: (candidate: string) => boolean
}

export interface UpdateScriptHandoff {
  command: string
  args: string[]
  scriptPath: string
}

/**
 * Repo-owned Windows update hand-off (frozen-binary escape hatch).
 *
 * The staged Tauri `hermes-setup.exe` has no self-update path, so every
 * updater-side fix only reaches users when a new binary is built, signed and
 * published — which historically lags main by months and strands users on
 * long-fixed bugs (cache resolver #67369, marker self-adopt #74782; the
 * 2026-08-09 incident chain). `scripts/desktop-update/windows.ps1` lives in the repo
 * checkout instead: every `hermes update` refreshes the code that drives the
 * NEXT update, and only PowerShell itself is frozen.
 *
 * Returns the spawn recipe when the script exists in the checkout, or null
 * (caller falls back to the staged binary — old checkouts that predate the
 * script keep working unchanged). Windows-only by the same policy as
 * resolveStagedUpdaterBinary: POSIX updates in place via
 * applyUpdatesPosixInApp and needs no hand-off at all.
 */
export function resolveUpdateScriptHandoff(
  updateRoot: string,
  deps: ResolveUpdateScriptHandoffDeps = {}
): UpdateScriptHandoff | null {
  const isWindows = deps.isWindows ?? process.platform === 'win32'

  if (!isWindows) {
    return null
  }

  const exists = deps.fileExists ?? stagedFileExists

  // Current layout first, then the pre-reorg flat path — an updated asar can
  // meet a checkout from either side of the move (the checkout also ships a
  // forwarder at the legacy path for the inverse skew).
  for (const candidate of [
    path.join(updateRoot, 'scripts', 'desktop-update', 'windows.ps1'),
    path.join(updateRoot, 'scripts', 'desktop-update.ps1')
  ]) {
    if (exists(candidate)) {
      return {
        command: 'powershell',
        args: ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', candidate],
        scriptPath: candidate
      }
    }
  }

  return null
}

/**
 * Repo-owned POSIX update hand-off (the mac/linux twin of the above).
 *
 * Replaces the in-app posix updater: the Desktop spawns the script detached
 * and QUITS, the script waits it out, runs `hermes update`, swaps/relaunches
 * the app, and writes .hermes-update-result.json. With the app gone before
 * the update starts, the HERMES_DESKTOP_CHILD_PID reaper-exclusion dance is
 * unnecessary — there are no live desktop backends to spare.
 *
 * Null when the checkout predates the script (caller surfaces the manual
 * `hermes update` card — old checkouts pull the script on their next update).
 */
export function resolvePosixScriptHandoff(
  updateRoot: string,
  deps: ResolveUpdateScriptHandoffDeps = {}
): UpdateScriptHandoff | null {
  const isWindows = deps.isWindows ?? process.platform === 'win32'

  if (isWindows) {
    return null
  }

  const scriptPath = path.join(updateRoot, 'scripts', 'desktop-update', 'posix.sh')
  const exists = deps.fileExists ?? stagedFileExists

  if (!exists(scriptPath)) {
    return null
  }

  return {
    command: '/bin/bash',
    args: [scriptPath],
    scriptPath
  }
}

/**
 * Wrap a PowerShell hand-off invocation so it survives a hidden spawn from
 * Electron without ever showing a console window (#116161).
 *
 * Verified empirically (2026-08-09, Windows 11): `spawn('powershell', [...,
 * '-File', script], { detached: true, stdio: 'ignore', windowsHide: true })`
 * exits 0 WITHOUT executing a single line of the script. powershell.exe is a
 * console-subsystem binary; libuv maps `detached: true` to DETACHED_PROCESS,
 * which gives the child NO console (and makes the OS ignore CREATE_NO_WINDOW),
 * and Windows PowerShell 5.1 dies during console init before -File processing
 * (the same class of failure as #54220's conhost work, on the launch side).
 *
 * The parent-console model that follows from that (and that every other
 * hidden spawn in this app relies on): a console child inherits its parent's
 * console; only a console-LESS parent forces the OS to allocate a new,
 * visible one. So the wrapper cmd.exe is spawned NON-detached — libuv then
 * honours `windowsHide` (CREATE_NO_WINDOW) and cmd.exe owns one hidden
 * console — and `start "" /b powershell ...` runs the script inside that
 * hidden console (`/min` would tell `start` to allocate a NEW console for the
 * child, which is what flashed a minimized PowerShell window on every
 * hand-off; `/b` shares the wrapper's). The console outlives cmd.exe for as
 * long as powershell is attached to it.
 *
 * Survival past our own exit does not need `detached`: libuv's per-process
 * job object has JOB_OBJECT_LIMIT_SILENT_BREAKAWAY_OK, so grandchildren
 * (`start`'s powershell) are never members and Windows does not tie a
 * process's lifetime to its parent. `start` returns immediately, so the
 * spawned pid is the WRAPPER's — callers must not use it as a marker owner
 * (the script claims the marker itself with its own $PID).
 */
export function wrapHandoffForDetachedConsole(
  handoff: UpdateScriptHandoff,
  extraArgs: string[]
): {
  command: string
  args: string[]
  /** Spawn NON-detached so the wrapper gets a hidden console the script inherits. */
  detached: false
} {
  return {
    command: 'cmd.exe',
    args: ['/d', '/s', '/c', 'start', '', '/b', handoff.command, ...handoff.args, ...extraArgs],
    detached: false
  }
}

/**
 * Electron/Chromium internal switches that must NOT be replayed on re-exec:
 * runtime artifacts of THIS launch, not user intent (ported from the deleted
 * update-relaunch.ts; #45205). `--no-sandbox` is deliberately kept — it is
 * the user's sandbox opt-out and the signal that makes a relaunch safe when
 * chrome-sandbox isn't setuid.
 */
export const INTERNAL_ARG_PREFIXES = [
  '--type=',
  '--user-data-dir=',
  '--enable-features=',
  '--disable-features=',
  '--field-trial-handle=',
  '--enable-logging',
  '--log-file=',
  '--disable-gpu-sandbox',
  '--lang=',
  '--inspect',
  '--remote-debugging-port='
]

/** Filter Electron internals from process.argv.slice(1) so the relaunched
 * app replays only user/launcher intent (deep links, app flags). */
export function collectRelaunchArgs(argv: unknown): string[] {
  if (!Array.isArray(argv)) {
    return []
  }

  return argv.filter((arg): arg is string => {
    if (typeof arg !== 'string' || arg.length === 0) {
      return false
    }

    return !INTERNAL_ARG_PREFIXES.some(prefix =>
      prefix.endsWith('=') ? arg.startsWith(prefix) : arg === prefix || arg.startsWith(prefix + '=')
    )
  })
}

/** True when the user has opted out of the SUID sandbox — the relaunch is
 * safe even if chrome-sandbox fails preflight (ported from update-relaunch.ts). */
export function sandboxFallbackFromEnv(env: Record<string, string | undefined>, launchArgs: string[]): boolean {
  const disable = String(env?.ELECTRON_DISABLE_SANDBOX || '').trim()

  if (disable === '1' || disable.toLowerCase() === 'true') {
    return true
  }

  return Array.isArray(launchArgs) && launchArgs.includes('--no-sandbox')
}

export interface ResolveStagedUpdaterBinaryDeps {
  isWindows?: boolean
  fileExists?: (candidate: string) => boolean
  stagedMtimeMs?: (candidate: string) => number | null
}

/**
 * Staged installers older than this have no self-PID exclusion in
 * `UpdateMarkerGuard::acquire` and will refuse an update whose marker was
 * pre-written on their behalf.
 *
 * The self-adopt fix landed in #74782 / 160586ff8 (2026-07-30 17:57 +0700).
 * We compare against the start of 2026-07-31 UTC so the boundary is
 * unambiguous for binaries staged that same day.
 */
export const MARKER_SELF_ADOPT_EPOCH_MS = Date.UTC(2026, 6, 31)

function stagedFileExists(candidate: string): boolean {
  try {
    return statSync(candidate).isFile()
  } catch {
    return false
  }
}

function stagedFileMtimeMs(candidate: string): number | null {
  try {
    return statSync(candidate).mtimeMs
  } catch {
    return null
  }
}

/**
 * Decide which staged installer binary — if any — may be handed an update.
 *
 * The Tauri installer self-copies into QUBIT_HOME on *every* platform
 * (`hermes-setup.exe` on Windows, `hermes-setup` elsewhere — see
 * apps/bootstrap-installer `paths::installer_dest` and
 * `bootstrap::copy_self_to_qubit_home`), so finding that binary on macOS or
 * Linux is expected, not leftover junk.
 *
 * Handing an update to it is nonetheless a Windows-only policy. Windows needs
 * the quit -> hand-off -> rebuild dance because a venv shim file lock keeps the
 * running desktop from rewriting its own bits; macOS and Linux have no such
 * lock and update in place through applyUpdatesPosixInApp(). Off Windows the
 * hand-off therefore buys nothing and costs a great deal: a staged binary older
 * than the hand-off protocol holds the update marker, spawns `hermes update`,
 * and that child refuses its own parent — wedging the in-app Update button for
 * good, with no route (update, re-download, reinstall) to a newer binary
 * (#74836). Returning null off Windows is what routes those platforms to the
 * in-app updater.
 *
 * Null on Windows too when nothing is staged (a dev/source run, or a CLI
 * install that never went through the installer); callers degrade gracefully.
 */
export function resolveStagedUpdaterBinary(
  hermesHome: string,
  deps: ResolveStagedUpdaterBinaryDeps = {}
): string | null {
  const isWindows = deps.isWindows ?? process.platform === 'win32'

  if (!isWindows) {
    return null
  }

  const fileExists = deps.fileExists ?? stagedFileExists
  const candidate = path.join(hermesHome, 'hermes-setup.exe')

  return fileExists(candidate) ? candidate : null
}

/**
 * True when the staged installer is new enough to survive a pre-written marker.
 *
 * `copy_self_to_qubit_home` deliberately no-ops during `--update`
 * (apps/bootstrap-installer/src-tauri/src/paths.rs), so the binary staged by a
 * user's ORIGINAL install orchestrates every later update — forever. Installers
 * predating #74782 have no self-PID exclusion in `UpdateMarkerGuard::acquire`,
 * so when the desktop pre-writes the marker naming that very updater, the
 * updater reads its own claim as a foreign live owner and aborts with
 * "Another Qubit update is already running (PID <itself>, started 1s ago)" —
 * the observed infinite "Install didn't finish" loop. Skipping the pre-write
 * for those binaries lets them acquire cleanly and run `hermes update`, which
 * pulls the permanent fixes. See shouldPrewriteUpdateMarker.
 *
 * We cannot ask the binary its version without executing it, so use its mtime:
 * the installer is written to QUBIT_HOME at install/repair time, making mtime
 * a faithful stamp of which installer generation produced it.
 *
 * Unreadable mtime counts as UNSUPPORTED — the pre-write is a best-effort
 * hardening, while a wedged updater is unrecoverable, so we bias toward the
 * path that can always make progress.
 */
export function stagedUpdaterSupportsPrewrittenMarker(
  candidate: string,
  deps: ResolveStagedUpdaterBinaryDeps = {}
): boolean {
  const mtimeMs = (deps.stagedMtimeMs ?? stagedFileMtimeMs)(candidate)

  return typeof mtimeMs === 'number' && Number.isFinite(mtimeMs) && mtimeMs >= MARKER_SELF_ADOPT_EPOCH_MS
}

export interface SpawnUpdaterProcessDeps {
  isWindows?: boolean
  spawnProcess?: (command: string, args: string[], options: SpawnOptions) => UpdaterChild
}

/**
 * Spawn the detached installer used for update and bootstrap-recovery handoffs.
 * The helper owns both hidden-console selection and unref semantics so every
 * updater handoff follows the same behavior and can be tested without Electron.
 */
export function spawnUpdaterProcess(
  updater: string,
  updaterArgs: string[],
  options: SpawnOptions,
  deps: SpawnUpdaterProcessDeps = {}
): UpdaterChild {
  const isWindows = deps.isWindows ?? process.platform === 'win32'
  const spawnOptions = hiddenWindowsChildOptions(options, isWindows) as SpawnOptions

  const child = deps.spawnProcess
    ? deps.spawnProcess(updater, updaterArgs, spawnOptions)
    : spawn(updater, updaterArgs, spawnOptions)

  child.unref()

  return child
}

export interface UpdaterHandoffOutcome {
  ok: boolean
  /** Set when ok is false. */
  reason?: 'spawn-error' | 'early-exit'
  /** Human-readable detail for logs (never contains argv secrets). */
  message?: string
  /** Exit code when the child exited inside the settle window. */
  code?: number | null
  /** Signal when the child was killed inside the settle window. */
  signal?: string | null
}

export interface ObserveUpdaterHandoffDeps {
  setTimeoutFn?: (callback: () => void, ms: number) => unknown
  clearTimeoutFn?: (timer: unknown) => void
}

/**
 * User-facing copy for a hand-off that did not take (spawn error or early exit).
 * The lead sentence is plain: nothing changed and Qubit keeps running. The raw
 * outcome message (exit code / signal / spawn error) stays on a trailing
 * "Details:" line for logs and support.
 */
export function describeUpdaterHandoffFailure(outcome: Pick<UpdaterHandoffOutcome, 'message'>): string {
  const lead =
    "The updater couldn't start, so nothing was changed and Qubit keeps running as before. " +
    'Try again; if it keeps failing, open the logs and send them to support.'

  return outcome.message ? `${lead}\n\nDetails: ${outcome.message}` : lead
}

/**
 * Watch a just-spawned detached updater for the duration of the quit dwell
 * and report whether the hand-off actually became viable (#66753).
 *
 * Before this, the Desktop called `unref()` and quit after a fixed dwell
 * without ever observing the child's async `error` event (ENOENT/EACCES —
 * Node reports exec failures asynchronously) or an early `exit`. A failed
 * spawn therefore looked identical to a successful one: the app vanished, no
 * updater appeared, and nothing relaunched. Worse, an unhandled `'error'`
 * event on the detached child would crash the Electron main process outright.
 *
 * Success is: no `error` event AND either the child survives the settle
 * window or it exits 0 inside it (the Windows `cmd start` wrapper exits 0
 * immediately by design — see wrapHandoffForDetachedConsole). Failure is a
 * spawn `error`, a non-zero exit, or a signal death inside the window.
 *
 * Children that expose no event interface (bare test doubles) settle as ok
 * after the window — the observation is a best-effort hardening, never a new
 * way to wedge an update.
 */
export function observeUpdaterHandoff(
  child: UpdaterChild,
  settleMs: number,
  deps: ObserveUpdaterHandoffDeps = {}
): Promise<UpdaterHandoffOutcome> {
  const setTimeoutFn = deps.setTimeoutFn ?? setTimeout

  const clearTimeoutFn =
    deps.clearTimeoutFn ?? ((timer: unknown) => clearTimeout(timer as ReturnType<typeof setTimeout>))

  const observable = child as UpdaterChild & {
    once?: (event: string, listener: (...args: unknown[]) => void) => unknown
    removeListener?: (event: string, listener: (...args: unknown[]) => void) => unknown
  }

  if (typeof observable.once !== 'function') {
    return new Promise(resolve => {
      setTimeoutFn(() => resolve({ ok: true }), settleMs)
    })
  }

  return new Promise(resolve => {
    let settled = false

    const finish = (outcome: UpdaterHandoffOutcome) => {
      if (settled) {
        return
      }

      settled = true
      clearTimeoutFn(timer)
      observable.removeListener?.('error', onError)
      observable.removeListener?.('exit', onExit)
      resolve(outcome)
    }

    const onError = (...args: unknown[]) => {
      const error = args[0] as (Error & { code?: string }) | undefined

      finish({
        ok: false,
        reason: 'spawn-error',
        message: `updater spawn failed: ${error?.code || error?.message || 'unknown error'}`
      })
    }

    const onExit = (...args: unknown[]) => {
      const code = args[0] as number | null
      const signal = args[1] as string | null

      if (signal || (typeof code === 'number' && code !== 0)) {
        finish({
          ok: false,
          reason: 'early-exit',
          message: signal
            ? `updater died from signal ${signal} before the settle window elapsed`
            : `updater exited ${code} before the settle window elapsed`,
          code: code ?? null,
          signal: signal ?? null
        })

        return
      }

      // Clean exit 0 inside the window is expected for wrapper shapes
      // (cmd.exe `start` on Windows exits immediately after launching the
      // real script in its own console).
      finish({ ok: true, code: code ?? 0, signal: null })
    }

    const timer = setTimeoutFn(() => finish({ ok: true }), settleMs)

    observable.once('error', onError)
    observable.once('exit', onExit)
  })
}
