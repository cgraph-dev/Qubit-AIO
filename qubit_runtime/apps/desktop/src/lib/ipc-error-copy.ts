/**
 * Brand-clean copy for an error that crossed the IPC bridge.
 *
 * Electron renders a rejected `ipcRenderer.invoke` as
 *   Error invoking remote method '<channel>': Error: <message>
 * and that string reaches the user verbatim in any surface that prints
 * `error.message`. The channel name is an internal contract identifier
 * (`hermes:connection`, `hermes:api`, …) — legacy brand on a user-facing
 * surface, and meaningless to a person. The channel itself must NOT be
 * renamed: `preload.ts` and `main.ts` agree on the literal, so renaming it is a
 * contract break.
 *
 * So strip the wrapper here, once, for every channel. This is the single seam:
 * `readableError` / `notifyError` in `src/store/notifications.ts` route every
 * renderer error through `unwrapIpcErrorMessage`, and the other inline
 * placements call it or the same helper.
 *
 * Two shapes must both be handled, which is why the previous one-off regexes
 * leaked — they required an inner `Error: ` and let the bare form through:
 *
 *   "Error invoking remote method 'hermes:connection': Error: boom"   → "boom"
 *   "Error invoking remote method 'hermes:connection': boom"          → "boom"
 *   "... 'hermes:connection': Setting up Qubit stopped …"             → "Setting up…"
 *
 * Pure module: no Electron imports, unit-tested next to it.
 */

/** `Error invoking remote method '<channel>': ` — with or without the inner `Error: `. */
const IPC_WRAPPER = /^\s*Error invoking remote method '[^']*':\s*(?:Error:\s*)?/i

/** A leading bare `Error: ` from a stringified throw. */
const LEADING_ERROR_LABEL = /^Error:\s*/i

/**
 * Remove the IPC wrapper (and therefore the channel name) from a message.
 * Returns the inner text, trimmed. Idempotent; leaves a message that never had
 * the wrapper untouched apart from its leading `Error: ` label.
 */
export function unwrapIpcErrorMessage(raw: string): string {
  let value = raw.trim()

  // Loop: a doubled wrapper ("…': Error: Error invoking remote method …") can
  // appear when a handler rethrows an already-unwrapped error. Bounded so a
  // pathological string cannot spin.
  for (let i = 0; i < 4 && IPC_WRAPPER.test(value); i++) {
    value = value.replace(IPC_WRAPPER, '').trim()
  }

  return value.replace(LEADING_ERROR_LABEL, '').trim()
}

/**
 * The same unwrap for a thrown value. A `string` throw is unwrapped; an `Error`
 * yields its unwrapped `.message`; anything else falls back.
 *
 * This is the function every error-to-copy path should call instead of matching
 * the wrapper itself.
 */
export function ipcErrorMessage(error: unknown, fallback: string): string {
  if (error instanceof Error) {
    return unwrapIpcErrorMessage(error.message) || fallback
  }

  if (typeof error === 'string') {
    return unwrapIpcErrorMessage(error) || fallback
  }

  return fallback
}
