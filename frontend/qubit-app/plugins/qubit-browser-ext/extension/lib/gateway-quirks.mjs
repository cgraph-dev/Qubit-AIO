// Remembers which optional request shapes a given Qubit gateway has refused.
//
// Current Qubit cores validate request fields strictly: a session PATCH that
// carries `source`, or a prompt.submit that carries `display_text`, is rejected
// outright. Both fields are cosmetic for the extension, so the right response
// is to try once, remember the refusal for that gateway, and stop sending the
// field. The answer is kept in storage with an expiry so a gateway that is
// later upgraded gets another chance instead of being muted forever.

export const GATEWAY_QUIRK = Object.freeze({
  SESSION_SOURCE_PATCH: 'session-source-patch',
  PROMPT_DISPLAY_TEXT: 'prompt-display-text',
});

export const GATEWAY_QUIRKS_STORAGE_KEY = 'qubitGatewayQuirks';
export const GATEWAY_QUIRK_TTL_MS = 7 * 24 * 60 * 60 * 1000;
const MAX_GATEWAYS = 32;

const KNOWN_QUIRKS = new Set(Object.values(GATEWAY_QUIRK));
const SESSION_NOT_FOUND_CODE = 'session_not_found';

export function gatewayQuirkScope(gatewayUrl = '') {
  const raw = String(gatewayUrl || '').trim();
  if (!raw) return '';
  try {
    return new URL(raw).origin.toLowerCase();
  } catch {
    return raw.replace(/\/+$/, '').toLowerCase();
  }
}

function probeKey(gatewayUrl, quirk) {
  return `${gatewayQuirkScope(gatewayUrl)}\n${quirk}`;
}

// Session PATCH refusals: 400 unsupported_session_field from current Qubit,
// or a route/method that does not exist on older builds. A PATCH for a session
// that is gone also answers 404, but with its own code, and says nothing about
// whether the gateway accepts the field, so it is not remembered.
export function isSessionPatchRejection(response, payload = null) {
  const status = Number(response?.status);
  const code = String(payload?.error?.code || payload?.code || '').toLowerCase();
  if (code === SESSION_NOT_FOUND_CODE) return false;
  if (status === 404 || status === 405 || status === 501) return true;
  if (status !== 400 && status !== 422) return false;
  if (code === 'unsupported_session_field') return true;
  const text = String(payload?.error?.message || payload?.error || payload?.message || '');
  return /unsupported session field/i.test(text);
}

function cleanEntries(raw, now, ttlMs) {
  const entries = {};
  if (!raw || typeof raw !== 'object') return entries;
  for (const [scope, quirks] of Object.entries(raw)) {
    if (!scope || !quirks || typeof quirks !== 'object') continue;
    for (const [quirk, markedAt] of Object.entries(quirks)) {
      const at = Number(markedAt);
      if (!KNOWN_QUIRKS.has(quirk) || !Number.isFinite(at) || now - at > ttlMs || at > now + ttlMs) continue;
      (entries[scope] ||= {})[quirk] = at;
    }
  }
  return entries;
}

export function createGatewayQuirkStore({
  storageArea = null,
  key = GATEWAY_QUIRKS_STORAGE_KEY,
  now = () => Date.now(),
  ttlMs = GATEWAY_QUIRK_TTL_MS,
} = {}) {
  let entries = {};
  let loading = null;
  // Keys of probes still waiting for their answer. Only one request per gateway
  // and field may find out whether the gateway refuses it.
  const probing = new Set();

  function load() {
    loading ||= (async () => {
      try {
        const stored = await storageArea?.get?.(key);
        const persisted = cleanEntries(stored?.[key], now(), ttlMs);
        // Anything marked while loading wins over what was already stored.
        for (const [scope, quirks] of Object.entries(entries)) {
          persisted[scope] = { ...persisted[scope], ...quirks };
        }
        entries = persisted;
      } catch {
        // Storage can be unavailable; the in-memory answer still applies.
      }
      return entries;
    })();
    return loading;
  }

  function isUnsupported(gatewayUrl, quirk) {
    const scope = gatewayQuirkScope(gatewayUrl);
    if (!scope || !KNOWN_QUIRKS.has(quirk)) return false;
    const at = Number(entries[scope]?.[quirk]);
    if (!Number.isFinite(at)) return false;
    if (now() - at > ttlMs) {
      delete entries[scope][quirk];
      return false;
    }
    return true;
  }

  // Claims the one request allowed to discover a refusal for this gateway and
  // field. Returns false once the gateway has refused the field, or while
  // another request is still waiting for its answer, so the caller leaves the
  // field out instead of sending it again. Every true claim must be released
  // with endProbe.
  function beginProbe(gatewayUrl, quirk) {
    if (!gatewayQuirkScope(gatewayUrl) || !KNOWN_QUIRKS.has(quirk)) return false;
    if (isUnsupported(gatewayUrl, quirk)) return false;
    const key = probeKey(gatewayUrl, quirk);
    if (probing.has(key)) return false;
    probing.add(key);
    return true;
  }

  function endProbe(gatewayUrl, quirk) {
    probing.delete(probeKey(gatewayUrl, quirk));
  }

  async function markUnsupported(gatewayUrl, quirk) {
    const scope = gatewayQuirkScope(gatewayUrl);
    if (!scope || !KNOWN_QUIRKS.has(quirk)) return false;
    (entries[scope] ||= {})[quirk] = now();
    const scopes = Object.keys(entries);
    if (scopes.length > MAX_GATEWAYS) {
      const oldest = scopes
        .map((name) => [name, Math.max(...Object.values(entries[name]).map(Number))])
        .sort((a, b) => a[1] - b[1])[0]?.[0];
      if (oldest && oldest !== scope) delete entries[oldest];
    }
    try {
      await storageArea?.set?.({ [key]: entries });
    } catch {
      // Remembered for this page even when storage refuses the write.
    }
    return true;
  }

  return Object.freeze({ load, isUnsupported, beginProbe, endProbe, markUnsupported });
}
