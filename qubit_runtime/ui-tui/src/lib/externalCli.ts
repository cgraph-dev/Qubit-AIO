import { spawn } from 'node:child_process'

export interface LaunchResult {
  code: null | number
  error?: string
}

// The installed product launcher is `qubit`; HERMES_BIN remains honoured as
// the legacy override (an existing install may still publish it).
const resolveHermesBin = () =>
  process.env.QUBIT_BIN?.trim() || process.env.HERMES_BIN?.trim() || 'qubit'

export const launchHermesCommand = (args: string[]): Promise<LaunchResult> =>
  new Promise(resolve => {
    const child = spawn(resolveHermesBin(), args, { stdio: 'inherit' })

    child.on('error', err => resolve({ code: null, error: err.message }))
    child.on('exit', code => resolve({ code }))
  })
