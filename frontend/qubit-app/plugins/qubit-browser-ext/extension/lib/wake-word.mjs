export const DEFAULT_WAKE_WORD_PHRASE = 'hey qubit';
export const WAKE_TURN_TTL_MS = 2 * 60 * 1000;
export const WAKE_STORAGE_KEYS = Object.freeze({
  state: 'qubitBrowserWakeState',
  turn: 'qubitBrowserWakeTurn',
});
export const WAKE_MESSAGES = Object.freeze({
  configure: 'QUBIT_WAKE_CONFIGURE',
  claimTurn: 'QUBIT_WAKE_CLAIM_TURN',
  getState: 'QUBIT_WAKE_GET_STATE',
  localDetected: 'QUBIT_WAKE_LOCAL_DETECTED',
  localState: 'QUBIT_WAKE_LOCAL_STATE',
  pauseLocal: 'QUBIT_WAKE_LOCAL_PAUSE',
  resumeLocal: 'QUBIT_WAKE_LOCAL_RESUME',
  setEnabled: 'QUBIT_WAKE_SET_ENABLED',
  speak: 'QUBIT_WAKE_SPEAK',
  turnReady: 'QUBIT_WAKE_TURN_READY',
  turnReply: 'QUBIT_WAKE_TURN_REPLY',
});

// The only settings a WAKE_SET_ENABLED message may change. Everything else in
// qubitBrowserSettings (gateway URL, API key, browser-control flags, ...) is
// owned by its own settings paths and is never taken from a message payload.
export const WAKE_SETTING_KEYS = Object.freeze([
  'wakeWordPhrase',
  'wakeWordPreferNative',
  'wakeWordBrowserFallback',
  'wakeWordSpeakReplies',
]);

export function pickWakeSettings(settings = {}) {
  const source = settings && typeof settings === 'object' ? settings : {};
  const picked = {};
  for (const key of WAKE_SETTING_KEYS) {
    if (Object.hasOwn(source, key)) picked[key] = source[key];
  }
  return picked;
}

function booleanSetting(value, fallback) {
  return typeof value === 'boolean' ? value : fallback;
}

export function normalizeWakePhrase(value = DEFAULT_WAKE_WORD_PHRASE) {
  const phrase = String(value || DEFAULT_WAKE_WORD_PHRASE)
    .trim()
    .replace(/\s+/g, ' ')
    .toLocaleLowerCase()
    .slice(0, 64)
    .trim();
  return phrase || DEFAULT_WAKE_WORD_PHRASE;
}

export function normalizeWakeWordSettings(settings = {}) {
  return {
    enabled: Boolean(settings?.wakeWordEnabled),
    phrase: normalizeWakePhrase(settings?.wakeWordPhrase),
    preferNative: booleanSetting(settings?.wakeWordPreferNative, true),
    browserFallback: booleanSetting(settings?.wakeWordBrowserFallback, true),
    speakReplies: booleanSetting(settings?.wakeWordSpeakReplies, true),
  };
}

function escapeRegex(value = '') {
  return String(value).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

export function matchWakePhrase(transcript = '', phrase = DEFAULT_WAKE_WORD_PHRASE) {
  const normalizedTranscript = String(transcript || '').trim().toLocaleLowerCase();
  const normalizedPhrase = normalizeWakePhrase(phrase);
  if (!normalizedTranscript) return { matched: false, command: '' };
  const matcher = new RegExp(`(?:^|[^\\p{L}\\p{N}])${escapeRegex(normalizedPhrase)}(?=$|[^\\p{L}\\p{N}])`, 'iu');
  const match = matcher.exec(normalizedTranscript);
  if (!match) return { matched: false, command: '' };
  const phraseStart = match.index + match[0].length - normalizedPhrase.length;
  const command = normalizedTranscript
    .slice(phraseStart + normalizedPhrase.length)
    .replace(/^[\s,.:;!?—–-]+/u, '')
    .trim();
  return { matched: true, command };
}

export function isLoopbackDashboardUrl(value = '') {
  try {
    const url = new URL(String(value || ''));
    if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password) return false;
    const hostname = url.hostname.replace(/^\[|\]$/g, '').toLocaleLowerCase();
    return hostname === 'localhost' || hostname === '127.0.0.1' || hostname === '::1';
  } catch {
    return false;
  }
}

export function normalizeWakeStatus(payload = {}) {
  return {
    enabled: Boolean(payload?.enabled),
    listening: Boolean(payload?.listening),
    available: Boolean(payload?.available),
    ownedByCaller: Boolean(payload?.owned_by_caller),
    ownerSurface: String(payload?.owner_surface || '').trim(),
    phrase: normalizeWakePhrase(payload?.phrase),
    provider: String(payload?.provider || '').trim().toLocaleLowerCase(),
    hint: String(payload?.hint || '').trim(),
    audioSilent: Boolean(payload?.audio_silent),
  };
}

export function wakeTurnIsFresh(turn = {}, now = Date.now()) {
  const createdAt = Number(turn?.createdAt);
  return Boolean(String(turn?.text || '').trim())
    && Number.isFinite(createdAt)
    && createdAt > Number(now) - WAKE_TURN_TTL_MS
    && createdAt <= Number(now) + 5000;
}
