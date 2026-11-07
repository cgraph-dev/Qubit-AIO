// One-time notice for long sessions (PRs #70 and #76, issue #69). A session
// that other Qubit clients also write to can fall out of sync once it grows
// long, so the side panel says so once per session when the open transcript
// passes the threshold. The seen list is bounded so storage stays small.

export const LONG_SESSION_NOTICE_THRESHOLD = 100;
export const LONG_SESSION_NOTICE_SEEN_LIMIT = 200;

function cleanId(value = '') {
  return String(value || '').trim();
}

export function longSessionNoticeDue({
  sessionId = '',
  messageCount = 0,
  seenSessionIds = [],
  threshold = LONG_SESSION_NOTICE_THRESHOLD,
} = {}) {
  const id = cleanId(sessionId);
  const count = Number(messageCount);
  if (!id || !Number.isFinite(count) || count <= threshold) return false;
  const seen = Array.isArray(seenSessionIds) ? seenSessionIds : [];
  return !seen.some((value) => cleanId(value) === id);
}

// Returns the next seen list with this session first, without duplicates and
// capped at LONG_SESSION_NOTICE_SEEN_LIMIT entries.
export function rememberLongSessionNotice(seenSessionIds = [], sessionId = '') {
  const id = cleanId(sessionId);
  const seen = Array.isArray(seenSessionIds) ? seenSessionIds.map(cleanId).filter(Boolean) : [];
  if (!id) return seen.slice(0, LONG_SESSION_NOTICE_SEEN_LIMIT);
  return [id, ...seen.filter((value) => value !== id)].slice(0, LONG_SESSION_NOTICE_SEEN_LIMIT);
}
