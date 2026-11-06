// Stateful wiring for approval and clarify prompts. The side panel and the full
// view each create one controller; it owns the request-keyed queue
// (gateway-prompts.mjs), the expiry clock, and delivery of the user's answer
// over whichever transport raised the prompt.
//
// A controller never answers on its own. Every reply starts from a button the
// user pressed, and a prompt that expires is dropped, not approved.

import {
  authorityPromptsFromSnapshot,
  authorityRespondFailureCode,
  buildApprovalBody,
  buildAuthorityApprovalRequest,
  buildAuthorityClarifyRequest,
  buildClarifyBody,
  buildWsApprovalResult,
  buildWsClarifyResult,
  createPromptQueueState,
  expiredPrompts,
  findPrompt,
  isAlreadyResolvedBody,
  isPromptActionable,
  isPromptRetired,
  isPromptRequestEvent,
  isPromptSettledEvent,
  normalizeApprovalPrompt,
  normalizeClarifyPrompt,
  PROMPT_EVENT_NAMES,
  PROMPT_KINDS,
  promptFromAuthorityEvent,
  promptFromServerRequest,
  promptKey,
  promptsFromRunStatus,
  reducePromptQueue,
  respondFailureCode,
  respondFailureMessage,
  visiblePrompts,
} from './gateway-prompts.mjs';

// A status read that races the event that raised a prompt must not retire it.
const STATUS_GRACE_MS = 3_000;

export function createPromptController({
  now = () => Date.now(),
  setIntervalFn = globalThis.setInterval?.bind(globalThis),
  clearIntervalFn = globalThis.clearInterval?.bind(globalThis),
  getViewedSessionIds = () => [],
  getCapabilities = () => null,
  // async (entry, body) => ({ status, payload }): POST the reply for a REST prompt.
  respondRest = async () => { throw new Error('REST replies are not wired'); },
  onNotice = () => {},
  tickMs = 1000,
} = {}) {
  let state = createPromptQueueState();
  const aliases = new Map();
  const channels = new Map();
  const listeners = new Set();
  let timer = null;

  const aliasFor = (id) => aliases.get(id) || id;
  const actionable = (entry) => isPromptActionable({
    transport: entry.transport,
    kind: entry.kind,
    capabilities: getCapabilities(),
  });
  const view = (entry) => ({ ...entry, actionable: actionable(entry) });

  function snapshot() {
    const visible = visiblePrompts(state, getViewedSessionIds(), aliases).map(view);
    return { visible, hiddenCount: state.entries.length - visible.length, total: state.entries.length, now: now() };
  }

  function notify() {
    const current = snapshot();
    for (const listener of listeners) {
      try {
        listener(current);
      } catch {
        // A listener throwing must not stop the others.
      }
    }
  }

  function notice(type, entry, extra = {}) {
    try {
      onNotice({ type, entry: entry ? view(entry) : null, ...extra });
    } catch {
      // Status copy is advisory; never let it break prompt handling.
    }
  }

  function tick() {
    const current = now();
    const expired = expiredPrompts(state, current);
    if (expired.length) {
      state = reducePromptQueue(state, { type: 'expire', now: current });
      afterChange();
      for (const entry of expired) notice('expired', entry);
      return;
    }
    notify();
  }

  function syncTimer() {
    if (state.entries.length && !timer && typeof setIntervalFn === 'function') {
      timer = setIntervalFn(tick, tickMs);
    } else if (!state.entries.length && timer) {
      clearIntervalFn?.(timer);
      timer = null;
    }
  }

  function afterChange() {
    const present = new Set(state.entries.map((entry) => entry.key));
    for (const key of [...channels.keys()]) if (!present.has(key)) channels.delete(key);
    syncTimer();
    notify();
  }

  function dispatch(action) {
    state = reducePromptQueue(state, { now: now(), ...action });
    afterChange();
  }

  function isViewed(entry) {
    return visiblePrompts({ entries: [entry], settled: {} }, getViewedSessionIds(), aliases).length > 0;
  }

  function addEntry(entry, channel = null) {
    if (!entry) return false;
    const existing = findPrompt(state, entry.key);
    if (existing) {
      // A reconnect redelivers the same request over a new socket: the answer
      // must travel on the live one.
      if (channel) channels.set(entry.key, channel);
      return false;
    }
    dispatch({ type: 'add', entry });
    const added = findPrompt(state, entry.key);
    if (!added) return false;
    if (channel) channels.set(added.key, channel);
    notice('requested', added, { viewed: isViewed(added) });
    return true;
  }

  function settle(key, type, extra = {}) {
    const entry = findPrompt(state, key);
    if (!entry) return;
    dispatch({ type: 'settle', key });
    if (type) notice(type, entry, extra);
  }

  // ---- REST: SSE events and run status ------------------------------------

  function ingestRestEvent(type, data, { runId = '', sessionId = '' } = {}) {
    if (!data || typeof data !== 'object') return false;
    const scope = String(data.run_id || runId || '').trim();
    const ctx = { transport: 'rest', scope, sessionId, now: now() };
    if (isPromptRequestEvent(type)) {
      const entry = type === PROMPT_EVENT_NAMES.clarifyRequest
        ? normalizeClarifyPrompt(data, ctx)
        : normalizeApprovalPrompt(data, ctx);
      return addEntry(entry);
    }
    if (isPromptSettledEvent(type)) {
      const id = String(data.prompt_id || data.request_id || '').trim()
        || (type.startsWith('approval.') ? 'default' : '');
      if (id) settle(promptKey({ transport: 'rest', scope, id }), 'cancelled', { reason: 'resolved' });
      return true;
    }
    return false;
  }

  // requestedAt: when the status read was sent. The answer describes the run as it
  // was then, so a prompt that arrived after that cannot be missing from it, however
  // late the answer is processed: dropping it would also keep it from coming back.
  function ingestRunStatus(runId, payload, { sessionId = '', requestedAt = Number.NaN } = {}) {
    const result = promptsFromRunStatus(payload, { runId, sessionId, now: now() });
    const scope = String(payload?.run_id || runId || '').trim();
    if (result.terminal) {
      settleScope('rest', scope);
      return;
    }
    for (const entry of result.entries) addEntry(entry);
    if (!result.authoritative) return;
    const live = new Set(result.entries.map((entry) => entry.key));
    const readAt = Number.isFinite(requestedAt) ? Math.min(requestedAt, now()) : now();
    const stale = state.entries.filter((entry) => entry.transport === 'rest' && entry.scope === scope
      && !live.has(entry.key) && readAt - entry.receivedAt > STATUS_GRACE_MS);
    for (const entry of stale) settle(entry.key, 'cancelled', { reason: 'resolved' });
  }

  function settleScope(transport, scope) {
    if (!state.entries.some((entry) => entry.transport === transport && entry.scope === scope)) return;
    dispatch({ type: 'settleScope', transport, scope });
  }

  // ---- WebSocket server requests ------------------------------------------

  // request: {id, method, params}. reply(result) / replyError(code, message)
  // answer the JSON-RPC request. Returns true when the method is one this
  // controller owns, so the caller can decline everything else.
  function ingestServerRequest(request, { reply, replyError, client = null, sessionId = '' } = {}) {
    if (request?.method !== PROMPT_KINDS.approval && request?.method !== PROMPT_KINDS.clarify) return false;
    const scope = String(request.params?.session_id || '').trim();
    const entry = promptFromServerRequest(request, { sessionId: sessionId || aliasFor(scope), now: now() });
    if (!entry) {
      replyError?.(-32602, 'Invalid request parameters');
      return true;
    }
    addEntry(entry, { reply, replyError, client });
    return true;
  }

  // request.cancel {id, method, reason}: the gateway withdrew a request.
  function cancelServerRequest(id, { reason = '' } = {}) {
    const wsId = String(id || '').trim();
    const entry = state.entries.find((item) => item.transport === 'ws' && item.wsRequestId === wsId);
    if (entry) settle(entry.key, 'cancelled', { reason });
  }

  // The connection that carried these requests closed. The gateway re-lists
  // still-open requests when the session is resumed, so nothing is remembered.
  function dropClient(client) {
    for (const [key, channel] of [...channels.entries()]) {
      if (channel.client === client) dispatch({ type: 'discard', key });
    }
  }

  // ---- Session-authority WebSocket: events and the resume snapshot ----------

  // A session-authority gateway raises prompts as session events instead of
  // server requests. Only a connection that said so in gateway.ready is
  // believed, so a legacy gateway cannot show the same prompt twice. A turn
  // that ends takes every prompt it was waiting on with it.
  function ingestAuthorityEvent(type, event, { client = null } = {}) {
    if (client?.readyPayload?.session_authority !== true) return false;
    const sessionId = String(event?.sessionId ?? event?.session_id ?? '').trim();
    if (!sessionId) return false;
    if (type === PROMPT_EVENT_NAMES.approvalRequest || type === PROMPT_EVENT_NAMES.clarifyRequest) {
      const entry = promptFromAuthorityEvent(type, event, { sessionId: aliasFor(sessionId), now: now() });
      return addEntry(entry, entry ? { client } : null);
    }
    if (type === PROMPT_EVENT_NAMES.approvalSettled || type === PROMPT_EVENT_NAMES.clarifySettled) {
      const id = String(event?.payload?.prompt_id || '').trim();
      if (id) settle(promptKey({ transport: 'ws-authority', scope: sessionId, id }), 'cancelled', { reason: 'resolved' });
      return true;
    }
    if (type === 'message.complete' || type === 'error') {
      settleScope('ws-authority', sessionId);
      return true;
    }
    return false;
  }

  // The prompts a resumed session still has open.
  function ingestAuthorityPrompts(prompts, { sessionId = '', client = null } = {}) {
    if (client?.readyPayload?.session_authority !== true) return 0;
    const live = String(sessionId || '').trim();
    let added = 0;
    for (const entry of authorityPromptsFromSnapshot(prompts, { sessionId: live, storedSessionId: aliasFor(live), now: now() })) {
      if (addEntry(entry, { client })) added += 1;
    }
    return added;
  }

  // ---- Answers --------------------------------------------------------------

  async function deliver(entry, { restBody, wsResult, authorityRequest = null }) {
    dispatch({ type: 'answering', key: entry.key });
    try {
      if (entry.transport === 'ws') {
        const channel = channels.get(entry.key);
        if (!channel?.reply) throw new Error('The Qubit connection closed.');
        await channel.reply(wsResult);
        settle(entry.key, 'sent');
        return { ok: true };
      }
      if (entry.transport === 'ws-authority') {
        const channel = channels.get(entry.key);
        if (!channel?.client?.request) throw new Error('The Qubit connection closed.');
        let result;
        try {
          result = await channel.client.request(authorityRequest.method, authorityRequest.params);
        } catch (error) {
          // The turn moved on, or the prompt was answered elsewhere: nothing left to send.
          if (isPromptRetired(0, authorityRespondFailureCode(error))) {
            settle(entry.key, 'retired');
            return { ok: false, retired: true };
          }
          throw error;
        }
        const retired = isAlreadyResolvedBody(result);
        settle(entry.key, retired ? 'retired' : 'sent');
        return { ok: true, retired };
      }
      const response = await respondRest(entry, restBody);
      const status = Number(response?.status);
      const payload = response?.payload;
      if (status >= 200 && status < 300) {
        const retired = isAlreadyResolvedBody(payload);
        settle(entry.key, retired ? 'retired' : 'sent');
        return { ok: true, retired };
      }
      if (isPromptRetired(status, respondFailureCode(payload))) {
        settle(entry.key, 'retired');
        return { ok: false, retired: true };
      }
      throw new Error(respondFailureMessage(payload, status));
    } catch (error) {
      const message = String(error?.message || error || 'failed');
      dispatch({ type: 'failed', key: entry.key, error: message });
      const failed = findPrompt(state, entry.key);
      if (failed) notice('failed', failed, { detail: message });
      return { ok: false, error: message };
    }
  }

  function answerable(key, kind) {
    const entry = findPrompt(state, key);
    if (!entry || entry.kind !== kind || entry.status !== 'pending') return null;
    if (!actionable(entry)) return null;
    if (entry.deadline <= now()) {
      tick();
      return null;
    }
    return entry;
  }

  async function answerApproval(key, choice) {
    const entry = answerable(key, PROMPT_KINDS.approval);
    if (!entry) return { ok: false, reason: 'unavailable' };
    const restBody = entry.transport === 'rest' ? buildApprovalBody(entry, choice) : null;
    const wsResult = entry.transport === 'ws' ? buildWsApprovalResult(entry, choice) : null;
    const authorityRequest = entry.transport === 'ws-authority' ? buildAuthorityApprovalRequest(entry, choice) : null;
    // A choice the gateway did not offer is never sent, and an authority prompt
    // that does not name its turn's generation is not answered blind.
    if (!restBody && !wsResult && !authorityRequest) return { ok: false, reason: 'invalid-choice' };
    return deliver(entry, { restBody, wsResult, authorityRequest });
  }

  // answers: {qid: string | null}. A REST prompt has one question and carries
  // its answer as plain text ('' skips it).
  async function answerClarify(key, answers) {
    const entry = answerable(key, PROMPT_KINDS.clarify);
    if (!entry) return { ok: false, reason: 'unavailable' };
    if (entry.transport === 'ws') {
      return deliver(entry, { wsResult: buildWsClarifyResult(entry, answers) });
    }
    const qid = entry.questions[0]?.qid ?? '';
    const value = answers?.[qid];
    const text = typeof value === 'string' ? value : '';
    if (entry.transport === 'ws-authority') {
      const authorityRequest = buildAuthorityClarifyRequest(entry, text);
      if (!authorityRequest) return { ok: false, reason: 'invalid-answer' };
      return deliver(entry, { authorityRequest });
    }
    const restBody = buildClarifyBody(entry, text);
    if (!restBody) return { ok: false, reason: 'invalid-answer' };
    return deliver(entry, { restBody });
  }

  function dismiss(key) {
    settle(key, null);
  }

  // ---- Observation ---------------------------------------------------------

  // Tell the controller which browser session a live gateway session id is.
  function bindSession(liveId, sessionId) {
    const live = String(liveId || '').trim();
    const stored = String(sessionId || '').trim();
    if (live && stored && aliases.get(live) !== stored) {
      aliases.set(live, stored);
      notify();
      // A request redelivered on resume arrived before its session was known;
      // now that it belongs to the session on screen, say so.
      const arrived = state.entries.find((entry) => entry.sessionId === live && isViewed(entry));
      if (arrived) notice('requested', arrived, { viewed: true });
    }
  }

  function hasPending(sessionIds = []) {
    return visiblePrompts(state, sessionIds, aliases).length > 0;
  }

  function subscribe(listener) {
    listeners.add(listener);
    listener(snapshot());
    return () => listeners.delete(listener);
  }

  function destroy() {
    if (timer) clearIntervalFn?.(timer);
    timer = null;
    listeners.clear();
    channels.clear();
    state = createPromptQueueState();
  }

  return {
    ingestRestEvent,
    ingestRunStatus,
    ingestServerRequest,
    ingestAuthorityEvent,
    ingestAuthorityPrompts,
    cancelServerRequest,
    dropClient,
    settleScope,
    answerApproval,
    answerClarify,
    dismiss,
    bindSession,
    hasPending,
    refresh: notify,
    getSnapshot: snapshot,
    subscribe,
    destroy,
  };
}

// While a REST run is quiet, ask the gateway what it is waiting for. The run
// stream carries approval requests, but a clarify question only shows up in the
// run's pending_controls, and a client that reattached mid-wait missed the
// event for either. Any stream activity postpones the next read, so a run that
// is streaming normally is never polled.
export function createPendingControlsPoller({
  fetchStatus,
  onStatus,
  // (payload) => true once the run is over: nothing more will be asked, so the
  // reads stop instead of running until the stream finally closes.
  isTerminal = () => false,
  quietMs = 8_000,
  pollMs = 4_000,
  // A run that stays quiet is read less and less often: every `backoffEvery`
  // reads without any stream activity adds one `pollMs` to the wait, up to this.
  maxPollMs = 20_000,
  backoffEvery = 5,
  maxFailures = 5,
  now = () => Date.now(),
  setTimeoutFn = globalThis.setTimeout?.bind(globalThis),
  clearTimeoutFn = globalThis.clearTimeout?.bind(globalThis),
} = {}) {
  let timer = null;
  let active = false;
  let inFlight = false;
  let failures = 0;
  let quietReads = 0;

  const nextPollMs = () => Math.min(Math.max(maxPollMs, pollMs), pollMs * (1 + Math.floor(quietReads / backoffEvery)));

  const clear = () => {
    if (timer !== null) clearTimeoutFn?.(timer);
    timer = null;
  };
  const arm = (delay) => {
    clear();
    if (active && typeof setTimeoutFn === 'function') timer = setTimeoutFn(run, delay);
  };
  async function run() {
    timer = null;
    if (!active) return;
    if (inFlight) {
      arm(nextPollMs());
      return;
    }
    inFlight = true;
    quietReads += 1;
    try {
      const requestedAt = now();
      const payload = await fetchStatus();
      if (!active) return;
      if (payload) {
        failures = 0;
        onStatus(payload, { requestedAt });
        if (isTerminal(payload)) active = false;
      } else {
        failures += 1;
      }
    } catch {
      failures += 1;
    } finally {
      inFlight = false;
    }
    if (active && failures >= maxFailures) {
      active = false;
      return;
    }
    arm(nextPollMs());
  }

  return {
    start() {
      active = true;
      failures = 0;
      quietReads = 0;
      arm(quietMs);
    },
    activity() {
      quietReads = 0;
      if (active && !inFlight) arm(quietMs);
    },
    // Read status soon: something just happened that a prompt usually follows.
    poke(delayMs = 750) {
      quietReads = 0;
      if (active && !inFlight) arm(delayMs);
    },
    stop() {
      active = false;
      clear();
    },
    get active() {
      return active;
    },
  };
}
