/**
 * Shared, DOM-free ordering for the chat session switcher.
 *
 * WHY this exists: the session you are currently on must always be highlighted
 * and shown at the TOP of `ChatSessionList`, but the id you are on is often a
 * canonical/worker id that the Qubit-native chat-store list does not contain.
 * In that case the row simply disappears — no highlight, no way to see where you
 * are. Keeping the merge logic here (pure, no React) lets it be unit-tested
 * without a DOM and reused by any list surface that needs the same guarantee.
 */

export interface SessionLike {
  id: string;
  last_active?: number;
  started_at?: number;
}

/**
 * Active session first, then the rest in their given order.
 * - null/undefined/non-array or empty input -> [].
 * - When `activeId` is a non-empty string and present in the list, that row is
 *   moved to the front (others keep order).
 * - When `activeId` is present but NOT in the list, a synthetic row is prepended:
 *   { id: activeId, name: activeId, title: activeId, preview: "", source: "",
 *     message_count: 0, last_active: now } (now defaults to Date.now()/1000).
 * - Never mutates the input; never throws.
 */
export function mergeActiveFirst<T extends SessionLike>(
  sessions: T[] | null | undefined,
  activeId: string | null | undefined,
  now?: number,
): T[] {
  if (!Array.isArray(sessions) || sessions.length === 0) return [];

  const id = typeof activeId === "string" && activeId.trim() ? activeId : null;
  if (!id) return sessions.slice();

  const index = sessions.findIndex((s) => s?.id === id);
  if (index === 0) return sessions.slice();

  if (index > 0) {
    const active = sessions[index];
    return [active, ...sessions.slice(0, index), ...sessions.slice(index + 1)];
  }

  // Active id is not in the list: prepend a synthetic row so the surface can
  // still highlight and label where the user is.
  const synthetic = {
    id,
    name: id,
    title: id,
    preview: "",
    source: "",
    message_count: 0,
    last_active: now ?? Date.now() / 1000,
  } as unknown as T;
  return [synthetic, ...sessions];
}
