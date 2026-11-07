// Decides when a panel on a plain API connection (no dashboard socket) should
// re-read the session it is showing. The gateway's session record reports a
// message count, so a cheap count check tells us whether a run that outlived
// the panel's own stream has written anything new.

export const REST_FOLLOW_ACTIVE_MS = 3_000;
export const REST_FOLLOW_IDLE_MS = 15_000;
export const REST_FOLLOW_ACTIVE_WINDOW_MS = 120_000;

export function createRestFollowState() {
  return { sessionId: '', count: null, lastChangeAt: 0, lastCheckAt: 0 };
}

export function messageCountFromSessionPayload(payload) {
  const session = payload && typeof payload === 'object' && payload.session && typeof payload.session === 'object'
    ? payload.session
    : payload;
  const count = Number(session?.message_count ?? session?.messageCount);
  return Number.isFinite(count) && count >= 0 ? count : null;
}

export function restFollowDue(state, sessionId, now = Date.now()) {
  if (!sessionId) return false;
  if (state.sessionId !== sessionId) return true;
  const recentlyChanged = now - state.lastChangeAt < REST_FOLLOW_ACTIVE_WINDOW_MS;
  return now - state.lastCheckAt >= (recentlyChanged ? REST_FOLLOW_ACTIVE_MS : REST_FOLLOW_IDLE_MS);
}

// Returns the next state and whether the caller should refresh the transcript.
// The first look at a session only records a baseline: the panel has just
// loaded that history itself, so refreshing again would only flicker.
export function restFollowStep(state, sessionId, count, now = Date.now()) {
  if (!sessionId || count == null) return { state, refresh: false };
  if (state.sessionId !== sessionId) {
    return { state: { sessionId, count, lastChangeAt: now, lastCheckAt: now }, refresh: false };
  }
  if (count !== state.count) {
    return { state: { sessionId, count, lastChangeAt: now, lastCheckAt: now }, refresh: true };
  }
  return { state: { ...state, lastCheckAt: now }, refresh: false };
}
