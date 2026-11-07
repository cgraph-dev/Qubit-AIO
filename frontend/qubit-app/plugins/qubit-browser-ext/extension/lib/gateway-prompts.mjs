// Approval and clarify prompts raised by a running Qubit turn.
//
// Qubit pauses a turn when a tool needs the user (a flagged shell command, a
// file edit, the clarify tool). The gateway keeps waiting until the user
// answers or its timeout (300 s by default) fails the request closed, so the
// browser has to surface the prompt and send the answer back.
//
// Everything in this file is pure: it normalizes the several wire shapes into
// one entry, keeps a request-keyed queue, and builds the exact response bodies
// the gateway accepts. The stateful wiring is in prompt-controller.mjs and the
// DOM is in prompt-card.mjs.
//
// Wire shapes handled (verified against the Qubit gateway):
//   REST/SSE   approval.request / clarify.request events, and the entries of
//              GET /v1/runs/{id}  pending_controls  (kind approval | clarify,
//              prompt_id, execution_generation, command, description, choices,
//              timeout_s, edit, question, multi_select). The session chat
//              stream spells the approval id request_id.
//   REST reply POST /v1/runs/{id}/approval  {request_id, execution_generation, choice}
//              POST /v1/runs/{id}/clarify   {request_id, execution_generation, answer}
//              Gateways that predate execution generations accept
//              {choice, request_id} and resolve the matching entry only.
//   WebSocket  server requests "approval" (params request_id, command,
//              description, choices, edit) answered with {choice}, and
//              "clarify" (params questions[{qid, question, choices,
//              multi_select}]) answered with {answers: {qid: text | null}}.
//              A request.cancel event withdraws a request.

export const PROMPT_KINDS = Object.freeze({ approval: 'approval', clarify: 'clarify' });

// Canonical order: the least powerful grant first, deny last.
export const APPROVAL_CHOICES = Object.freeze(['once', 'session', 'always', 'deny']);

export const PROMPT_EVENT_NAMES = Object.freeze({
  approvalRequest: 'approval.request',
  approvalSettled: 'approval.settled',
  approvalResponded: 'approval.responded',
  approvalCancelled: 'approval.cancelled',
  clarifyRequest: 'clarify.request',
  clarifySettled: 'clarify.settled',
});

const REQUEST_EVENTS = new Set([PROMPT_EVENT_NAMES.approvalRequest, PROMPT_EVENT_NAMES.clarifyRequest]);
const SETTLED_EVENTS = new Set([
  PROMPT_EVENT_NAMES.approvalSettled,
  PROMPT_EVENT_NAMES.approvalResponded,
  PROMPT_EVENT_NAMES.approvalCancelled,
  PROMPT_EVENT_NAMES.clarifySettled,
]);

export function isPromptEventName(name = '') {
  return REQUEST_EVENTS.has(name) || SETTLED_EVENTS.has(name);
}

export function isPromptRequestEvent(name = '') {
  return REQUEST_EVENTS.has(name);
}

export function isPromptSettledEvent(name = '') {
  return SETTLED_EVENTS.has(name);
}

// The clarify tool starting is the one hint a REST stream gives that a question
// is about to be raised; the question itself only shows up in run status.
export function isClarifyToolEvent(tool = {}) {
  const name = String(tool?.toolName || tool?.tool_name || tool?.name || '').trim().toLowerCase();
  return name === 'clarify';
}

// The gateway fails an unanswered request closed after its own timeout. When a
// request carries no timeout_s the card still must not outlive any sane wait.
export const MAX_PROMPT_AGE_MS = 30 * 60 * 1000;
const SETTLED_MEMORY_MS = 10 * 60 * 1000;
const MAX_QUEUED_PROMPTS = 50;
const MAX_FIELD_CHARS = 20_000;
const MAX_ANSWER_CHARS = 16_384;
const MAX_QUESTIONS = 5;
const MAX_CHOICES = 24;

function isRecord(value) {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
}

function cleanText(value, max = MAX_FIELD_CHARS) {
  if (typeof value === 'string') return value.length > max ? value.slice(0, max) : value;
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  return '';
}

function cleanId(value) {
  const text = cleanText(value, 256).trim();
  return text;
}

function positiveSeconds(value) {
  const seconds = Number(value);
  if (!Number.isFinite(seconds) || seconds <= 0) return null;
  return Math.min(seconds, 24 * 60 * 60);
}

// The gateway sends the full list it will accept. Anything it did not offer is
// never rendered, and Deny is always present so the user can refuse even when
// the list is empty or holds only values this build does not know.
export function normalizeApprovalChoices(choices) {
  if (!Array.isArray(choices) || choices.length === 0) return [...APPROVAL_CHOICES];
  const offered = new Set(choices
    .map((choice) => (typeof choice === 'string' ? choice.trim().toLowerCase() : ''))
    .filter((choice) => APPROVAL_CHOICES.includes(choice)));
  offered.add('deny');
  return APPROVAL_CHOICES.filter((choice) => offered.has(choice));
}

const MAX_EDIT_CHARS = 200_000;

// True when a text field is longer than the card keeps of it.
function exceeds(value, max) {
  return typeof value === 'string' && value.length > max;
}

function normalizeEdit(value) {
  if (!isRecord(value)) return null;
  const paths = Array.isArray(value.paths) ? value.paths.map((item) => cleanText(item, 1_000)).filter(Boolean) : [];
  const path = cleanText(value.path, 2_000) || paths.join(', ');
  const newText = cleanText(value.new_text ?? value.newText, MAX_EDIT_CHARS);
  const oldText = value.old_text === null || value.old_text === undefined
    ? null
    : cleanText(value.old_text ?? value.oldText, MAX_EDIT_CHARS);
  if (!path && !newText && oldText === null) return null;
  return {
    path,
    toolName: cleanText(value.tool_name ?? value.toolName, 200),
    oldText,
    newText,
    // The change is longer than what is kept here, so the diff is only its start.
    truncated: exceeds(value.new_text ?? value.newText, MAX_EDIT_CHARS) || exceeds(value.old_text ?? value.oldText, MAX_EDIT_CHARS),
  };
}

export function promptKey({ transport = 'rest', scope = '', id = '' } = {}) {
  return `${transport}:${scope}:${id}`;
}

// null and '' read as 0 through Number(); a generation the gateway did not send
// must stay unknown, never become the first generation.
function parseGeneration(value) {
  if (typeof value === 'number') return Number.isInteger(value) && value >= 0 ? value : null;
  if (typeof value === 'string' && /^\d{1,15}$/.test(value.trim())) return Number(value.trim());
  return null;
}

function baseEntry({ kind, transport, scope, id, sessionId, now, data, wsRequestId }) {
  const timeoutS = positiveSeconds(data.timeout_s ?? data.timeoutS);
  const generation = parseGeneration(data.execution_generation);
  return {
    key: promptKey({ transport, scope, id }),
    kind,
    transport,
    scope,
    requestId: id,
    wsRequestId: wsRequestId || '',
    sessionId: cleanId(sessionId),
    executionGeneration: generation,
    timeoutS,
    receivedAt: now,
    // An approval always has a gateway timeout, so one that did not say so still
    // gets a generous cap. A clarify wait can be unlimited, and a card that vanished
    // while the agent still waits would strand it: it ends with its run instead.
    deadline: timeoutS ? now + timeoutS * 1000 : (kind === PROMPT_KINDS.clarify ? Infinity : now + MAX_PROMPT_AGE_MS),
    hasTimeout: Boolean(timeoutS),
    status: 'pending',
    error: '',
  };
}

// raw: an approval.request payload, a pending_controls entry, or WS params.
export function normalizeApprovalPrompt(raw, {
  transport = 'rest', scope = '', sessionId = '', now = Date.now(), wsRequestId = '',
} = {}) {
  if (!isRecord(raw)) return null;
  const requestId = cleanId(raw.request_id ?? raw.prompt_id);
  const id = transport === 'ws' ? cleanId(wsRequestId) : (requestId || 'default');
  if (!id) return null;
  const edit = normalizeEdit(raw.edit);
  const command = cleanText(raw.command) || (edit?.path ? `Edit ${edit.path}` : '');
  return {
    ...baseEntry({ kind: PROMPT_KINDS.approval, transport, scope, id, sessionId, now, data: raw, wsRequestId }),
    requestId,
    command,
    // What the person approves is what the card shows: a command cut short for
    // display says so, instead of looking complete.
    commandTruncated: exceeds(raw.command, MAX_FIELD_CHARS),
    description: cleanText(raw.description ?? raw.reason),
    // An edit approval is a one-time consent, never a session or permanent grant.
    choices: normalizeApprovalChoices(edit ? ['once', 'deny'] : raw.choices),
    edit,
    smartDenied: raw.smart_denied === true,
  };
}

function normalizeQuestion(raw, fallbackQid = '') {
  if (!isRecord(raw)) return null;
  const question = cleanText(raw.question);
  if (!question) return null;
  const choices = Array.isArray(raw.choices)
    ? raw.choices.map((choice) => cleanText(choice, 500)).filter(Boolean).slice(0, MAX_CHOICES)
    : [];
  const qid = cleanId(raw.qid) || fallbackQid;
  // qid is used as an object key for the answers; never let it name the prototype.
  if (qid === '__proto__') return null;
  return {
    qid,
    question,
    choices,
    multiSelect: raw.multi_select === true && choices.length > 0,
  };
}

// raw: a clarify.request payload / pending_controls entry (one question, answer
// is a string) or WS params ({questions: [...]}, answers keyed by qid).
export function normalizeClarifyPrompt(raw, {
  transport = 'rest', scope = '', sessionId = '', now = Date.now(), wsRequestId = '',
} = {}) {
  if (!isRecord(raw)) return null;
  const requestId = cleanId(raw.prompt_id ?? raw.request_id ?? raw.clarify_id);
  const id = transport === 'ws' ? cleanId(wsRequestId) : requestId;
  if (!id) return null;
  const questions = Array.isArray(raw.questions)
    ? raw.questions.slice(0, MAX_QUESTIONS).map((item, index) => normalizeQuestion(item, `q${index + 1}`)).filter(Boolean)
    : [normalizeQuestion(raw, '')].filter(Boolean);
  if (!questions.length) return null;
  return {
    ...baseEntry({ kind: PROMPT_KINDS.clarify, transport, scope, id, sessionId, now, data: raw, wsRequestId }),
    requestId,
    questions,
  };
}

// Entries of GET /v1/runs/{id}. pending_controls is the authoritative list; a
// run still waiting_for_approval on a gateway without it carries the original
// request as `approval`.
export function promptsFromRunStatus(payload, { runId = '', sessionId = '', now = Date.now() } = {}) {
  if (!isRecord(payload)) return { entries: [], authoritative: false, terminal: false };
  const scope = cleanId(payload.run_id) || cleanId(runId);
  const ctx = { transport: 'rest', scope, sessionId, now };
  const entries = [];
  const controls = Array.isArray(payload.pending_controls) ? payload.pending_controls : null;
  for (const control of controls || []) {
    if (!isRecord(control)) continue;
    const entry = control.kind === PROMPT_KINDS.clarify
      ? normalizeClarifyPrompt(control, ctx)
      : control.kind === PROMPT_KINDS.approval ? normalizeApprovalPrompt(control, ctx) : null;
    if (entry) entries.push(entry);
  }
  if (!controls && String(payload.status || '').toLowerCase() === 'waiting_for_approval' && isRecord(payload.approval)) {
    const entry = normalizeApprovalPrompt(payload.approval, ctx);
    if (entry) entries.push(entry);
  }
  const status = String(payload.status || '').trim().toLowerCase();
  return {
    entries,
    authoritative: Boolean(controls),
    terminal: TERMINAL_RUN_STATUSES.includes(status),
  };
}

const TERMINAL_RUN_STATUSES = ['completed', 'failed', 'cancelled', 'canceled', 'interrupted', 'stopped'];

// True once GET /v1/runs/{id} says the run is over: nothing more will be asked.
export function isRunStatusTerminal(payload) {
  return isRecord(payload) && TERMINAL_RUN_STATUSES.includes(String(payload.status || '').trim().toLowerCase());
}

// A session-authority gateway announces a prompt as a session event whose
// payload is the same entry GET /v1/runs/{id} lists in pending_controls, and
// lists the open ones in the resume snapshot. The id the answer must carry is
// the prompt id, with the session id and the execution generation beside it.
export function promptFromAuthorityEvent(type, event, { sessionId = '', now = Date.now() } = {}) {
  const payload = isRecord(event?.payload) ? event.payload : null;
  const scope = cleanId(event?.sessionId ?? event?.session_id);
  if (!payload || !scope) return null;
  const ctx = { transport: 'ws-authority', scope, sessionId: sessionId || scope, now };
  if (type === PROMPT_EVENT_NAMES.approvalRequest) return normalizeApprovalPrompt(payload, ctx);
  if (type === PROMPT_EVENT_NAMES.clarifyRequest) return normalizeClarifyPrompt(payload, ctx);
  return null;
}

export function authorityPromptsFromSnapshot(prompts, { sessionId = '', storedSessionId = '', now = Date.now() } = {}) {
  const scope = cleanId(sessionId);
  if (!scope || !Array.isArray(prompts)) return [];
  const ctx = { transport: 'ws-authority', scope, sessionId: cleanId(storedSessionId) || scope, now };
  const entries = [];
  for (const prompt of prompts.slice(0, MAX_QUEUED_PROMPTS)) {
    if (!isRecord(prompt)) continue;
    const entry = prompt.kind === PROMPT_KINDS.clarify
      ? normalizeClarifyPrompt(prompt, ctx)
      : prompt.kind === PROMPT_KINDS.approval ? normalizeApprovalPrompt(prompt, ctx) : null;
    if (entry) entries.push(entry);
  }
  return entries;
}

// A server→client request frame {id, method, params}.
export function promptFromServerRequest(request, { sessionId = '', now = Date.now() } = {}) {
  if (!isRecord(request) || !isRecord(request.params)) return null;
  const id = cleanId(request.id);
  const scope = cleanId(request.params.session_id);
  const ctx = { transport: 'ws', scope, sessionId: sessionId || scope, now, wsRequestId: id };
  if (request.method === 'approval') return normalizeApprovalPrompt(request.params, ctx);
  if (request.method === 'clarify') return normalizeClarifyPrompt(request.params, ctx);
  return null;
}

// ---------------------------------------------------------------------------
// Queue (pure reducer)

export function createPromptQueueState() {
  return { entries: [], settled: {} };
}

function pruneSettled(settled, now) {
  const kept = {};
  for (const [key, at] of Object.entries(settled)) {
    if (now - at < SETTLED_MEMORY_MS) kept[key] = at;
  }
  return kept;
}

function settleKeys(state, keys, now) {
  if (!keys.length) return state;
  const drop = new Set(keys);
  const settled = { ...pruneSettled(state.settled, now) };
  for (const key of keys) settled[key] = now;
  return { entries: state.entries.filter((entry) => !drop.has(entry.key)), settled };
}

// Actions:
//   add        {entry, now}            idempotent per key; a settled key stays settled
//   answering  {key}                   buttons disabled while the reply is in flight
//   failed     {key, error}            reply failed, the prompt is answerable again
//   settle     {key, now}              answered, withdrawn or retired
//   discard    {key}                   dropped without being remembered as settled
//                                      (the connection that carried it closed)
//   settleScope{transport, scope, now} every prompt of one run or live session
//   expire     {now}                   drop prompts past their deadline
export function reducePromptQueue(state = createPromptQueueState(), action = {}) {
  const now = Number.isFinite(action.now) ? action.now : Date.now();
  switch (action.type) {
    case 'add': {
      const { entry } = action;
      if (!entry?.key || state.entries.some((item) => item.key === entry.key)) return state;
      if (Number.isFinite(state.settled[entry.key]) && now - state.settled[entry.key] < SETTLED_MEMORY_MS) return state;
      const settled = pruneSettled(state.settled, now);
      const entries = [...state.entries, entry];
      // A flood of requests must not grow without bound: the oldest are the
      // ones the gateway has most likely already failed closed.
      const overflow = entries.length - MAX_QUEUED_PROMPTS;
      if (overflow > 0) {
        const dropped = entries.splice(0, overflow);
        for (const item of dropped) settled[item.key] = now;
      }
      return { entries, settled };
    }
    case 'answering':
      return {
        ...state,
        entries: state.entries.map((entry) => (entry.key === action.key ? { ...entry, status: 'answering', error: '' } : entry)),
      };
    case 'failed':
      return {
        ...state,
        entries: state.entries.map((entry) => (entry.key === action.key
          ? { ...entry, status: 'pending', error: cleanText(action.error, 500) || 'failed' }
          : entry)),
      };
    case 'settle':
      return settleKeys(state, state.entries.filter((entry) => entry.key === action.key).map((entry) => entry.key), now);
    case 'discard':
      return { ...state, entries: state.entries.filter((entry) => entry.key !== action.key) };
    case 'settleScope':
      return settleKeys(state, state.entries
        .filter((entry) => entry.transport === action.transport && entry.scope === action.scope)
        .map((entry) => entry.key), now);
    case 'expire':
      return settleKeys(state, state.entries.filter((entry) => entry.deadline <= now).map((entry) => entry.key), now);
    default:
      return state;
  }
}

export function expiredPrompts(state, now = Date.now()) {
  return state.entries.filter((entry) => entry.deadline <= now);
}

export function findPrompt(state, key) {
  return state.entries.find((entry) => entry.key === key) || null;
}

// A prompt belongs to the conversation it was raised in. `aliases` maps a live
// gateway session id to the id the browser knows the conversation by, so a
// prompt raised for a live id still follows the session it belongs to.
export function visiblePrompts(state, viewedSessionIds = [], aliases = new Map()) {
  const viewed = new Set((Array.isArray(viewedSessionIds) ? viewedSessionIds : [viewedSessionIds])
    .map((id) => cleanId(id)).filter(Boolean));
  if (!viewed.size) return [];
  return state.entries.filter((entry) => {
    const owner = aliases.get(entry.sessionId) || entry.sessionId;
    return viewed.has(owner) || viewed.has(entry.sessionId) || viewed.has(entry.scope);
  });
}

// ---------------------------------------------------------------------------
// Capability gating

function capabilitiesUnprobed(caps) {
  return !isRecord(caps) || !caps.source || caps.source === 'legacy' || caps.source === 'unknown';
}

// WebSocket requests only arrive because this client advertised it answers
// them, so they are always actionable. On REST the answer needs the run
// approval route. A gateway that advertises capabilities without it, or one
// that emitted the prompt without advertising anything we can read, still gets
// the card, but informational: the user is told where to answer instead of
// being offered buttons that would fail.
export function isPromptActionable({ transport = 'rest', kind = PROMPT_KINDS.approval, capabilities = null } = {}) {
  if (transport === 'ws' || transport === 'ws-authority') return true;
  if (capabilitiesUnprobed(capabilities)) return true;
  if (kind === PROMPT_KINDS.clarify) {
    return capabilities.runClarify === true || capabilities.runApproval === true;
  }
  return capabilities.runApproval === true || capabilities.approvalEvents === true;
}

// ---------------------------------------------------------------------------
// Reply bodies

// Only a choice the gateway offered is ever sent, and never an unlisted word.
export function buildApprovalBody(entry, choice) {
  const picked = typeof choice === 'string' ? choice.trim().toLowerCase() : '';
  if (!entry || entry.kind !== PROMPT_KINDS.approval || !APPROVAL_CHOICES.includes(picked)) return null;
  if (!entry.choices.includes(picked)) return null;
  const requestId = entry.requestId || '';
  if (Number.isInteger(entry.executionGeneration)) {
    return { request_id: requestId, execution_generation: entry.executionGeneration, choice: picked };
  }
  return requestId ? { choice: picked, request_id: requestId } : { choice: picked };
}

export function buildClarifyBody(entry, answer) {
  if (!entry || entry.kind !== PROMPT_KINDS.clarify) return null;
  const text = typeof answer === 'string' ? answer : '';
  if (text.length > MAX_ANSWER_CHARS) return null;
  const body = { request_id: entry.requestId || '', answer: text };
  if (Number.isInteger(entry.executionGeneration)) body.execution_generation = entry.executionGeneration;
  return body;
}

export function buildWsApprovalResult(entry, choice) {
  const picked = typeof choice === 'string' ? choice.trim().toLowerCase() : '';
  if (!entry || entry.kind !== PROMPT_KINDS.approval || !entry.choices.includes(picked)) return null;
  return { choice: picked };
}

// A session authority takes an answer as `approval.respond` / `clarify.respond`
// with exactly these four keys; it refuses any other set, and a generation that
// is not the turn's current one comes back `stale_generation`.
export const AUTHORITY_RESPOND_METHODS = Object.freeze({
  approval: 'approval.respond',
  clarify: 'clarify.respond',
});

export function buildAuthorityApprovalRequest(entry, choice) {
  const picked = typeof choice === 'string' ? choice.trim().toLowerCase() : '';
  if (!entry || entry.kind !== PROMPT_KINDS.approval || entry.transport !== 'ws-authority') return null;
  if (!APPROVAL_CHOICES.includes(picked) || !entry.choices.includes(picked)) return null;
  if (!entry.requestId || !entry.scope || !Number.isInteger(entry.executionGeneration)) return null;
  return {
    method: AUTHORITY_RESPOND_METHODS.approval,
    params: {
      session_id: entry.scope,
      execution_generation: entry.executionGeneration,
      prompt_id: entry.requestId,
      choice: picked,
    },
  };
}

export function buildAuthorityClarifyRequest(entry, answer) {
  if (!entry || entry.kind !== PROMPT_KINDS.clarify || entry.transport !== 'ws-authority') return null;
  const text = typeof answer === 'string' ? answer : '';
  if (text.length > MAX_ANSWER_CHARS) return null;
  if (!entry.requestId || !entry.scope || !Number.isInteger(entry.executionGeneration)) return null;
  return {
    method: AUTHORITY_RESPOND_METHODS.clarify,
    params: {
      session_id: entry.scope,
      execution_generation: entry.executionGeneration,
      prompt_id: entry.requestId,
      answer: text,
    },
  };
}

// The error a refused respond call carries: the store's reason, in data.reason.
export function authorityRespondFailureCode(error) {
  return String(error?.data?.reason || error?.message || '').trim().toLowerCase();
}

// answers: {qid: string | null}. null skips a question. A result without
// answers cancels the whole request.
export function buildWsClarifyResult(entry, answers) {
  if (!entry || entry.kind !== PROMPT_KINDS.clarify) return null;
  const known = new Set(entry.questions.map((question) => question.qid));
  const clean = {};
  for (const [qid, value] of Object.entries(isRecord(answers) ? answers : {})) {
    if (!known.has(qid)) continue;
    if (value === null) clean[qid] = null;
    else if (typeof value === 'string' && value.length <= MAX_ANSWER_CHARS) clean[qid] = value;
  }
  return { answers: clean, outcome: 'submitted' };
}

// A multi-select answer travels as the JSON array of the chosen labels.
export function encodeMultiSelectAnswer(labels = []) {
  return JSON.stringify([...new Set(labels.filter((label) => typeof label === 'string' && label))]);
}

// ---------------------------------------------------------------------------
// Reply outcome

const RETIRED_CODES = new Set([
  'approval_not_pending',
  'approval_not_active',
  'already_resolved',
  'stale_generation',
  'not_found',
  'run_not_found',
]);

// The prompt was answered elsewhere, timed out or its run ended: nothing left
// to send, so the card closes instead of offering a retry.
export function isPromptRetired(status, code = '') {
  const http = Number(status);
  if (http === 404 || http === 409) return true;
  return RETIRED_CODES.has(String(code || '').trim().toLowerCase());
}

export function respondFailureCode(payload) {
  const error = isRecord(payload?.error) ? payload.error : null;
  return String(error?.code || payload?.code || '').trim().toLowerCase();
}

export function respondFailureMessage(payload, status = 0) {
  const error = payload?.error;
  const message = isRecord(error) ? error.message : (typeof error === 'string' ? error : payload?.message);
  return cleanText(message, 300) || `HTTP ${status || 'error'}`;
}

// A 2xx whose body says the prompt was not pending any more.
export function isAlreadyResolvedBody(payload) {
  const status = String(payload?.status || '').trim().toLowerCase();
  return status === 'already_resolved';
}

// ---------------------------------------------------------------------------
// Presentation helpers

export function formatCountdown(ms) {
  const total = Math.max(0, Math.ceil(Number(ms) / 1000));
  const minutes = Math.floor(total / 60);
  const seconds = total % 60;
  return `${minutes}:${String(seconds).padStart(2, '0')}`;
}

function splitLines(text) {
  if (!text) return [];
  const lines = String(text).split(/\r\n|\r|\n/);
  if (lines.length && lines[lines.length - 1] === '') lines.pop();
  return lines;
}

// A compact before/after view of an edit proposal: the unchanged head and tail
// are trimmed to a little context, the changed middle is shown in full up to a
// line budget. Rendered with textContent, so file content is never parsed.
export function buildEditDiff(oldText, newText, { context = 2, maxLines = 120, maxLineLength = 300 } = {}) {
  const before = oldText === null || oldText === undefined ? [] : splitLines(oldText);
  const after = splitLines(newText);
  let head = 0;
  while (head < before.length && head < after.length && before[head] === after[head]) head += 1;
  let tail = 0;
  while (tail < before.length - head && tail < after.length - head
    && before[before.length - 1 - tail] === after[after.length - 1 - tail]) tail += 1;
  const rows = [];
  const clip = (line) => (line.length > maxLineLength ? `${line.slice(0, maxLineLength)}…` : line);
  const headContext = Math.min(context, head);
  if (head > headContext) rows.push({ kind: 'gap', text: '…' });
  for (let index = head - headContext; index < head; index += 1) rows.push({ kind: 'context', text: clip(before[index]) });
  for (let index = head; index < before.length - tail; index += 1) rows.push({ kind: 'removed', text: clip(before[index]) });
  for (let index = head; index < after.length - tail; index += 1) rows.push({ kind: 'added', text: clip(after[index]) });
  const tailContext = Math.min(context, tail);
  for (let index = 0; index < tailContext; index += 1) rows.push({ kind: 'context', text: clip(after[after.length - tail + index]) });
  if (tail > tailContext) rows.push({ kind: 'gap', text: '…' });
  const added = rows.filter((row) => row.kind === 'added').length;
  const removed = rows.filter((row) => row.kind === 'removed').length;
  const truncated = rows.length > maxLines;
  return { rows: truncated ? rows.slice(0, maxLines) : rows, truncated, added, removed };
}

export function diffRowPrefix(kind) {
  if (kind === 'added') return '+ ';
  if (kind === 'removed') return '- ';
  if (kind === 'gap') return '';
  return '  ';
}
