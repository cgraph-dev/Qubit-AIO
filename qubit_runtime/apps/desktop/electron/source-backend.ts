import { existsSync } from 'node:fs'
import path from 'node:path'

import { buildDesktopBackendEnv } from './backend-env'
import { execProbe, PROBE_TIMEOUT_MS } from './backend-probes'
import { resolveInstallationLauncher } from './updater-process'

export interface SourceBackend {
  kind: 'command' | 'python'
  label: string
  command: string
  args: string[]
  env: NodeJS.ProcessEnv
  root: string
  bootstrap: false
  shell: boolean
  local: 'installed'
}

interface SourceOptions {
  isWindows?: boolean
  env?: NodeJS.ProcessEnv
  /** Diagnostic sink: why a candidate root was rejected (used by boot logging). */
  onReject?: (reason: string) => void
}

/** Keep the validated command. PM owns interpreter and generation selection. */
export async function resolveSourceInstallationBackend(
  root: string,
  args: string[],
  options: SourceOptions & { hermesHome?: string } = {}
): Promise<SourceBackend | null> {
  // A Qubit checkout vendors the runtime under qubit_runtime/, while an upstream
  // (hermes) checkout has qubit_cli/ at the root. Accept either layout: the
  // installer's own runtime_dir() draws the same distinction, and a checkout that
  // only satisfies the upstream shape made every Qubit install look unusable —
  // the app logged "no usable Qubit install" for a complete, working install and
  // dropped the user back to first-run setup.
  const runtimeRoot: string = existsSync(path.join(root, 'qubit_runtime', 'qubit_cli', 'main.py'))
    ? path.join(root, 'qubit_runtime')
    : root

  if (!existsSync(path.join(runtimeRoot, 'qubit_cli', 'main.py'))) {
    if (typeof options.onReject === 'function') options.onReject(`no qubit_cli/main.py under ${runtimeRoot}`)
    return null
  }

  const isWindows: boolean = options.isWindows ?? process.platform === 'win32'
  const launcher: string | null = resolveInstallationLauncher(root, isWindows, options.hermesHome)

  if (!launcher) {
    if (typeof options.onReject === 'function') options.onReject(`no launcher resolved for ${root}`)
    return null
  }

  const shell: boolean = isWindows && /\.(cmd|bat)$/i.test(launcher)
  const command: string = shell ? `"${launcher}"` : launcher
  const env: NodeJS.ProcessEnv = buildDesktopBackendEnv({ currentEnv: options.env ?? process.env })

  try {
    await execProbe(command, ['--version'], {
      cwd: root,
      env: { ...process.env, ...options.env, ...env },
      shell,
      stdio: 'ignore',
      timeout: PROBE_TIMEOUT_MS,
      windowsHide: true
    })
  } catch (error) {
    if (typeof options.onReject === 'function') {
      options.onReject(`launcher ${launcher} failed its --version probe: ${(error as Error)?.message ?? error}`)
    }
    return null
  }

  return {
    kind: 'command',
    label: `Qubit at ${root}`,
    command,
    args: [...args],
    env,
    root,
    bootstrap: false,
    shell,
    local: 'installed'
  }
}

/** Developer overrides retain their interpreter, even outside the checkout. */
export function createSourcePythonBackend(
  root: string,
  python: string | null,
  args: string[],
  options: SourceOptions = {}
): SourceBackend | null {
  if (!python) {
    return null
  }

  let command: string = python

  if ((options.isWindows ?? process.platform === 'win32') && /[\\/]pythonw\.exe$/i.test(python)) {
    // Use only the console interpreter beside the selected windowless one.
    const consolePython: string = python.replace(/pythonw\.exe$/i, 'python.exe')

    if (existsSync(consolePython)) {
      command = consolePython
    }
  }

  return {
    kind: 'python',
    label: `Qubit source at ${root}`,
    command,
    args: ['-m', 'qubit_cli.main', ...args],
    // The backend runs in the user's workspace cwd, and the selected
    // interpreter need not have this checkout installed: name it explicitly.
    // (The scrubbed inherited value could point at another checkout.)
    //
    // PYTHONPATH must name the directory that CONTAINS qubit_cli/, which in a
    // Qubit checkout is the vendored qubit_runtime/ — pointing it at the
    // checkout root leaves `-m qubit_cli.main` unresolvable. Same layout rule
    // as resolveSourceInstallationBackend above.
    env: {
      ...buildDesktopBackendEnv({ currentEnv: options.env ?? process.env }),
      PYTHONPATH: existsSync(path.join(root, 'qubit_runtime', 'qubit_cli', 'main.py'))
        ? path.join(root, 'qubit_runtime')
        : root
    },
    root,
    bootstrap: false,
    shell: false,
    local: 'installed'
  }
}
