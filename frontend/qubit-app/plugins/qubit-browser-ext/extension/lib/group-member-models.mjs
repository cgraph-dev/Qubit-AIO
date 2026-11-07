// Per-room model bindings and display-only transcript notices for Bot Mode
// rooms. Bindings are keyed by room and member and always switch the member's
// room session only (never --global).
import { buildSessionModelSwitchRequest } from './gateway-ws.mjs';

export function roomModelKey(roomId, memberName) {
  return `${String(roomId ?? '')}::${String(memberName ?? '')}`;
}

function asStore(store) {
  return store && typeof store === 'object' && !Array.isArray(store) ? store : {};
}

// Model/provider identifiers cannot contain whitespace or look like CLI flags.
function validIdentifier(value) {
  const s = String(value ?? '').trim();
  return s.length > 0 && !/\s/.test(s) && !s.startsWith('-');
}

export function readRoomModelBinding(store, roomId, memberName) {
  const value = asStore(store)[roomModelKey(roomId, memberName)];
  if (!value || typeof value !== 'object') return null;
  return { model: value.model, provider: value.provider, setAt: value.setAt };
}

export function writeRoomModelBinding(store, roomId, memberName, binding = {}) {
  const model = String(binding?.model ?? '').trim();
  const provider = String(binding?.provider ?? '').trim();
  if (!validIdentifier(model)) throw new Error('A valid model identifier is required.');
  if (provider && !validIdentifier(provider)) throw new Error('A valid provider identifier is required.');
  const next = { ...asStore(store) };
  next[roomModelKey(roomId, memberName)] = {
    model,
    provider,
    setAt: Number.isFinite(binding?.setAt) ? binding.setAt : 0,
  };
  return next;
}

export function clearRoomModelBinding(store, roomId, memberName) {
  const next = { ...asStore(store) };
  delete next[roomModelKey(roomId, memberName)];
  return next;
}

// Display-only room transcript notices (per-member pass/failure, and the
// "now uses <model>" / "back on its profile default" lines) are local rows:
// never sent to a bot and never written to the synced projection. They are
// kept per room so they survive a room close/reopen (the v0.3.4 durable
// room-rows rider), bounded so an old room cannot grow without limit.
const ROOM_DISPLAY_EVENT_LIMIT = 200;

export function readRoomDisplayEvents(store, roomId) {
  const list = asStore(store)[String(roomId ?? '')];
  return Array.isArray(list) ? list.map((record) => ({ ...record })) : [];
}

export function appendRoomDisplayEvent(store, roomId, record, { limit = ROOM_DISPLAY_EVENT_LIMIT } = {}) {
  const key = String(roomId ?? '');
  if (!key) return asStore(store);
  const base = asStore(store);
  const existing = Array.isArray(base[key]) ? base[key] : [];
  const capped = Number.isFinite(limit) && limit > 0 ? Math.floor(limit) : ROOM_DISPLAY_EVENT_LIMIT;
  return { ...base, [key]: [...existing, { ...record }].slice(-capped) };
}

export function clearRoomDisplayEvents(store, roomId) {
  const next = { ...asStore(store) };
  delete next[String(roomId ?? '')];
  return next;
}

export function buildMemberModelSwitch({ liveSessionId, model, provider } = {}) {
  return buildSessionModelSwitchRequest({ sessionId: liveSessionId, model, provider });
}

// Model ids are case sensitive. A specified provider is a real routing pin,
// not advisory: a different or unreadable provider needs reapplication.
// No provider pin means model-only comparison; an unreadable live model is
// never treated as a verified mismatch.
export function needsReapply({ binding, statusModel } = {}) {
  if (!binding || !binding.model) return false;
  const live = statusModel && typeof statusModel === 'object'
    ? String(statusModel.model ?? '').trim()
    : String(statusModel ?? '').trim();
  if (!live) return false;
  if (binding.model !== live) return true;
  const wantedProvider = String(binding.provider ?? '').trim();
  if (!wantedProvider) return false;
  const liveProvider = statusModel && typeof statusModel === 'object'
    ? String(statusModel.provider ?? '').trim() : '';
  return wantedProvider !== liveProvider;
}