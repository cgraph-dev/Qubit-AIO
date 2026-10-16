/**
 * Which chat-live frames must remount the session switcher.
 *
 * The switcher is keyed on a nonce, so bumping it REMOUNTS `ChatSessionList`
 * and throws away its local state: the search box, the scroll position, the
 * "See more" paging offset, and the row order it had just fetched. That is
 * correct only when the frame can actually change the list's MEMBERSHIP or
 * ORDER.
 *
 * The single Qubit SSE feed carries every session mutation for every surface
 * (a background voice session, another tab, a subagent). A per-session MODEL
 * PICK publishes `session.updated` for that one row — it changes no membership
 * and no order — yet the host used to bump the nonce for ANY `session.*` frame,
 * remounting the list and re-ordering rows under the operator mid-scroll. This
 * is the decision, isolated as a pure function so it is unit-testable and
 * cannot drift back into the component.
 *
 * Rules:
 *   - `session.created` / `session.deleted` -> membership changed -> remount.
 *   - `session.updated` for OUR active session -> may re-order (the active row
 *     floats to the top when its model changes) -> remount.
 *   - `session.updated` for any OTHER session -> no membership/order change ->
 *     do NOT remount; the row's own live refresh (focus / interval) picks it up.
 *   - `model.changed` -> a global config write, no session row touched -> no.
 */
export function shouldRemountSessionList(frame: {
  name?: string;
  isSession?: boolean;
  isModelChange?: boolean;
  sessionId?: string;
} | null, activeSessionId: string | null): boolean {
  if (!frame || !frame.isSession) return false;
  const name = frame.name ?? "";
  if (name === "session.created" || name === "session.deleted") return true;
  if (name !== "session.updated") return false;
  // An update to ANY row can re-order the list only when that row is the one
  // the host has selected (it is floated to the top). A foreign row's update —
  // the reported model-pick-on-a-background-session case — leaves order and
  // membership intact.
  return !!activeSessionId && frame.sessionId === activeSessionId;
}
