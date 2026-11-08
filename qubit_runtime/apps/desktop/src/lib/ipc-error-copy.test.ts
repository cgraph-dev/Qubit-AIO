import { expect, test } from 'vitest'

import { ipcErrorMessage, unwrapIpcErrorMessage } from './ipc-error-copy'

/**
 * Electron renders a rejected `ipcRenderer.invoke` as
 *   Error invoking remote method '<channel>': Error: <message>
 * and any surface that prints `error.message` shows the channel name. The
 * channel literal is a preload/main contract (`hermes:connection`, `hermes:api`,
 * …) and must not be renamed — so the renderer must never surface it.
 *
 * These cases pin the two shapes that reach the renderer: the standard one with
 * an inner `Error: `, and the bare one (a handler that throws a non-Error, or a
 * rethrow) which the old `.match(… ': Error: ' …)` regexes let through intact.
 * That bare shape is exactly the dialog in the report:
 *   "Error invoking remote method 'hermes:connection': Error: Setting up Qubit stopped…"
 */

/** What Electron puts on `err.message` for a rejected invoke. */
function electronInvokeRejection(channel: string, thrown: unknown): Error {
  const detail =
    thrown instanceof Error
      ? `Error: ${thrown.message}`
      : typeof thrown === 'string'
        ? thrown
        : `Error: ${String(thrown)}`

  return new Error(`Error invoking remote method '${channel}': ${detail}`)
}

// The reported surface: a handler on `hermes:connection` throws the bootstrap
// failure, and the dialog showed the raw IPC method name above it.
test("a throw from ipcMain.handle('hermes:connection') surfaces no channel name", () => {
  const thrown = new Error('Setting up Qubit stopped before it could finish.')
  const surfaced = electronInvokeRejection('hermes:connection', thrown)

  // The raw IPC rejection really does carry the channel — that is the bug.
  expect(surfaced.message).toContain('hermes:connection')

  const copy = ipcErrorMessage(surfaced, 'Connection failed')

  expect(copy).toBe('Setting up Qubit stopped before it could finish.')
  expect(copy).not.toMatch(/hermes/i)
  expect(copy).not.toContain('remote method')
})

// The bare shape (no inner `Error: `) is the one the pre-existing one-off
// regexes failed to unwrap, which is how the channel reached the dialog.
test("a non-Error throw from ipcMain.handle('hermes:connection') is unwrapped too", () => {
  const surfaced = electronInvokeRejection('hermes:connection', 'Setting up Qubit stopped before it could finish.')

  expect(surfaced.message).toContain('hermes:connection')

  const copy = ipcErrorMessage(surfaced, 'Connection failed')

  expect(copy).toBe('Setting up Qubit stopped before it could finish.')
  expect(copy).not.toMatch(/hermes/i)
})

test('the unwrap covers every hermes:* channel, not just connection', () => {
  const channels = [
    'hermes:connection',
    'hermes:connection:for',
    'hermes:connection:revalidate',
    'hermes:api',
    'hermes:backend:touch',
    'hermes:bootstrap:get',
    'hermes:updates:apply',
    'hermes:find-in-page'
  ]

  for (const channel of channels) {
    const surfaced = electronInvokeRejection(channel, new Error('boom'))
    const copy = ipcErrorMessage(surfaced, 'Failed')

    expect(copy, channel).toBe('boom')
    expect(copy, channel).not.toMatch(/hermes/i)
  }
})

test('unwrapping does not mangle a message that never crossed IPC', () => {
  expect(unwrapIpcErrorMessage('Connection refused')).toBe('Connection refused')
  expect(unwrapIpcErrorMessage('Error: boom')).toBe('boom')
  // A user-facing sentence that merely mentions the wrapper text mid-string is
  // left alone — only a leading wrapper (which is all Electron emits) is cut.
  expect(unwrapIpcErrorMessage('could not invoke remote method')).toBe('could not invoke remote method')
})

test('a doubled wrapper collapses to the underlying message', () => {
  const inner = electronInvokeRejection('hermes:connection', new Error('boom'))
  const outer = electronInvokeRejection('hermes:api', inner)

  expect(unwrapIpcErrorMessage(outer.message)).toBe('boom')
})

test('non-string throws fall back instead of rendering an object', () => {
  expect(ipcErrorMessage(undefined, 'Connection failed')).toBe('Connection failed')
  expect(ipcErrorMessage({ detail: 'x' }, 'Connection failed')).toBe('Connection failed')
})
