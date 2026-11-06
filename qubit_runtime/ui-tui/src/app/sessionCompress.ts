import { randomUUID } from 'node:crypto'

import type { TranscriptMessage, Usage } from '@qubit/shared/gateway-events'

import type { GatewayClient } from '../gatewayClient.js'
import type { SessionCompressResponse } from '../gatewayTypes.js'
import type { SessionInfo } from '../types.js'
import { isVersionSkewError } from './userMessages.js'

/** The authority-advertised flag on the first `gateway.ready` frame. The TUI's stdio
 *  sidecar does not serve canonical methods, so it never advertises this. */
export const CANONICAL_SESSION_CAPABILITY = 'session_authority'

export type CanonicalSessionGateway = Pick<GatewayClient, 'hasSessionAuthority' | 'recordLog' | 'request'>

/** Fields the canonical `session.resume` / `session.create` snapshot carries beyond the
 *  legacy TUI response type: the fences `session.mutate` requires. */
export interface CanonicalSessionSnapshot {
  execution_generation?: number
  info?: SessionInfo
  message_count?: number
  messages?: TranscriptMessage[]
  revision?: number
  session_id?: string
}

/** `tui_gateway/contracts/canonical.py::SessionMutateResult`, plus the legacy-shaped fields a
 *  compatibility backend may project onto the same reply. */
export interface CanonicalSessionMutateResult {
  after_messages?: number
  before_messages?: number
  execution_generation?: number
  info?: SessionInfo
  lines?: string[]
  message_count?: number
  messages?: TranscriptMessage[]
  operation?: string
  previous_target_session_id?: string
  removed?: number
  revision?: number
  session_id?: string
  status?: string
  summary?: SessionCompressResponse['summary']
  target_session_id?: string
  usage?: Usage
}

export class CanonicalCompressError extends Error {
  readonly code?: number
  readonly fallback: boolean
  readonly stage: 'mutate' | 'resume'

  constructor(stage: 'mutate' | 'resume', cause: unknown, fallback = false) {
    super(cause instanceof Error ? cause.message : String(cause))
    this.name = 'CanonicalCompressError'
    this.stage = stage
    this.code = (cause as { code?: number } | null)?.code
    this.fallback = fallback
  }
}

export const canUseCanonicalSessionCompress = (gw: Pick<GatewayClient, 'hasSessionAuthority'>): boolean =>
  gw.hasSessionAuthority === true

/** Canonical mutations are revision- and generation-fenced; an older/capability-less
 *  snapshot is an out-of-sync backend, not a reason to invent a fence. */
export const buildCanonicalCompressParams = (
  sessionId: string,
  arg: string,
  snapshot: CanonicalSessionSnapshot,
  requestId: string = randomUUID()
): Record<string, unknown> => {
  if (typeof snapshot.revision !== 'number' || typeof snapshot.execution_generation !== 'number') {
    throw new CanonicalCompressError(
      'resume',
      new Error('canonical session snapshot is missing revision/execution_generation'),
      true
    )
  }

  const focus = arg.trim()

  return {
    expected_generation: snapshot.execution_generation,
    expected_revision: snapshot.revision,
    operation: 'compress',
    payload: focus ? { focus } : {},
    request_id: requestId,
    session_id: sessionId
  }
}

/** Adapt the canonical receipt to the TUI's `SessionCompressResponse`. Every value comes
 *  from the gateway (the receipt, or the authoritative snapshot resumed after it): a field
 *  the backend did not return stays absent rather than being guessed. */
export const adaptCanonicalCompressResult = (
  receipt: CanonicalSessionMutateResult,
  snapshot: CanonicalSessionSnapshot | null,
  before: CanonicalSessionSnapshot | null = null
): SessionCompressResponse => {
  const messages = receipt.messages ?? snapshot?.messages
  const info = receipt.info ?? snapshot?.info
  const usage = receipt.usage ?? info?.usage
  const beforeMessages = receipt.before_messages ?? before?.message_count
  const afterMessages = receipt.after_messages ?? snapshot?.message_count ?? receipt.message_count
  const removed =
    receipt.removed ??
    (typeof beforeMessages === 'number' && typeof afterMessages === 'number'
      ? Math.max(0, beforeMessages - afterMessages)
      : undefined)

  return {
    ...(typeof afterMessages === 'number' ? { after_messages: afterMessages } : {}),
    ...(typeof beforeMessages === 'number' ? { before_messages: beforeMessages } : {}),
    ...(info ? { info } : {}),
    ...(receipt.lines ? { lines: receipt.lines } : {}),
    ...(messages ? { messages } : {}),
    ...(typeof removed === 'number' ? { removed } : {}),
    ...(receipt.summary ? { summary: receipt.summary } : {}),
    ...(usage ? { usage } : {})
  }
}

const fetchSnapshot = async (
  gw: Pick<GatewayClient, 'request'>,
  sessionId: string
): Promise<CanonicalSessionSnapshot> =>
  (await gw.request<CanonicalSessionSnapshot>('session.resume', { session_id: sessionId })) ?? {}

/**
 * The canonical `/compress`: resume for the fence, mutate, then resume again for the
 * authoritative compacted transcript. A failed post-mutation resume degrades to the
 * receipt's own projection instead of re-running (or fallback-compressing) a commit that
 * already landed.
 */
export const runCanonicalCompress = async (
  gw: Pick<GatewayClient, 'recordLog' | 'request'>,
  sessionId: string,
  arg: string,
  requestId?: string
): Promise<SessionCompressResponse> => {
  let before: CanonicalSessionSnapshot

  try {
    before = await fetchSnapshot(gw, sessionId)
  } catch (err) {
    throw new CanonicalCompressError('resume', err)
  }

  let params: Record<string, unknown>

  try {
    params = buildCanonicalCompressParams(sessionId, arg, before, requestId)
  } catch (err) {
    if (err instanceof CanonicalCompressError) {
      throw err
    }

    throw new CanonicalCompressError('resume', err)
  }

  let receipt: CanonicalSessionMutateResult

  try {
    receipt = (await gw.request<CanonicalSessionMutateResult>('session.mutate', params)) ?? {}
  } catch (err) {
    throw new CanonicalCompressError('mutate', err)
  }

  // A preview/noop returns before any commit; the receipt is the whole answer.
  if (receipt.status === 'preview' || receipt.status === 'noop') {
    return adaptCanonicalCompressResult(receipt, null, before)
  }

  try {
    const after = await fetchSnapshot(gw, sessionId)

    return adaptCanonicalCompressResult(receipt, after, before)
  } catch (err) {
    gw.recordLog(`[compress] canonical post-compress resume failed after a committed mutate: ${String(err)}`)

    return adaptCanonicalCompressResult(receipt, null, before)
  }
}

export const isCanonicalCompressFallback = (err: unknown): boolean => {
  if (!(err instanceof CanonicalCompressError)) {
    return false
  }

  if (err.fallback || err.code === -32601) {
    return true
  }

  return isVersionSkewError(err)
}
