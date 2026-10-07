/**
 * Chat-session live-update frames (client half of upstream's live contract).
 *
 * The server announces every chat-session mutation on the single SSE event
 * feed as an AG-UI ``CUSTOM`` frame:
 *
 *   {"type":"CUSTOM","name":"session.created","value":{"session_id","session"}}
 *   {"type":"CUSTOM","name":"session.updated","value":{"session_id","session"}}
 *   {"type":"CUSTOM","name":"session.deleted","value":{"session_id","session"}}
 *   {"type":"CUSTOM","name":"model.changed","value":{"provider","model","scope"}}
 *
 * Before this producer/consumer pair existed, a create / model pick / rename /
 * delete mutated the store but broadcast nothing, so the open SPA had no
 * signal and only a full page reload showed the change. This module is the one
 * place that recognises those frames — every surface (sidebar model card,
 * session list, ChatPage model state) subscribes through it instead of
 * re-parsing the envelope.
 *
 * The feed wraps application events in ``CUSTOM``, so reading ``type`` alone
 * yields the literal ``"CUSTOM"``; the real name is ``rec.name`` and the
 * payload is ``rec.value`` (the same unwrap rule `useChatActivity` applies).
 */

export interface ChatSessionFrame {
  /** ``session.created`` | ``session.updated`` | ``session.deleted`` | ``model.changed``. */
  name: string;
  /** The session wire record (session events) — opaque here, shaped by the store. */
  session: Record<string, unknown> | null;
  sessionId: string;
  /** True for any of the three session.* names. */
  isSession: boolean;
  /** True for ``model.changed``. */
  isModelChange: boolean;
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object"
    ? (value as Record<string, unknown>)
    : null;
}

/**
 * Recognise a chat-live frame from one parsed wire record (already
 * ``JSON.parse``d from ``data:``). Returns null for any non-chat frame so a
 * caller can pass every frame through without branching.
 */
export function chatLiveFrame(rec: Record<string, unknown>): ChatSessionFrame | null {
  const type = typeof rec.type === "string" ? rec.type : "";
  // Application events ride a CUSTOM envelope; tolerate a bare top-level
  // `name`/`value` too so a shape tweak cannot silently kill live updates.
  const name =
    type === "CUSTOM"
      ? typeof rec.name === "string"
        ? rec.name
        : ""
      : "";
  const bareName = typeof rec.name === "string" ? rec.name : "";
  const resolved = name || bareName;
  if (!resolved) return null;

  const isSession =
    resolved === "session.created" ||
    resolved === "session.updated" ||
    resolved === "session.deleted";
  const isModelChange = resolved === "model.changed";
  if (!isSession && !isModelChange) return null;

  const value = asRecord(rec.value) ?? asRecord(rec.params) ?? {};
  const session = asRecord(value.session);
  const sid =
    (typeof value.session_id === "string" && value.session_id) ||
    (session && typeof session.id === "string" ? session.id : "") ||
    "";
  return {
    name: resolved,
    session,
    sessionId: sid,
    isSession,
    isModelChange,
  };
}
