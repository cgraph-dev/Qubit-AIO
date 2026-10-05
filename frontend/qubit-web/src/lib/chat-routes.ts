/**
 * Canonical Qubit chat route set (owner decision 2026-09-17).
 *
 * `/chat` renders the real Qubit terminal frontend (xterm over the single
 * Qubit PTY WebSocket). `/qubit-chat` keeps the Qubit-native ChatPage. They
 * share the Qubit backend, auth boundary, policy, audit, e-stop, and
 * VoiceBridge, but are intentionally distinct surfaces; neither redirects and
 * neither is a placeholder. Keep every chat-route decision on these predicates
 * so the two routes can never drift.
 */

export const TERMINAL_CHAT_PATH = "/chat";
export const NATIVE_CHAT_PATH = "/qubit-chat";
export const CHAT_ROUTE_PATHS = [TERMINAL_CHAT_PATH, NATIVE_CHAT_PATH] as const;

/** Strip a trailing slash (but keep the root) before comparing paths. */
export function normalizeRoutePath(pathname: string): string {
  return pathname.replace(/\/$/, "") || "/";
}

/** True for any Qubit chat route (`/chat` or `/qubit-chat`). */
export function isChatPath(pathname: string): boolean {
  return (CHAT_ROUTE_PATHS as readonly string[]).includes(
    normalizeRoutePath(pathname),
  );
}

/** True only for the real Qubit terminal route (`/chat`). */
export function isTerminalChatPath(pathname: string): boolean {
  return normalizeRoutePath(pathname) === TERMINAL_CHAT_PATH;
}

/** True only for the Qubit-native chat route (`/qubit-chat`). */
export function isNativeChatPath(pathname: string): boolean {
  return normalizeRoutePath(pathname) === NATIVE_CHAT_PATH;
}

/**
 * Full-bleed console routes: pages that own the whole viewport and manage their
 * own internal scrolling (a pinned composer, a fixed rail, a transcript that
 * scrolls on its own).
 *
 * These are NOT chat routes — the two predicates answer different questions and
 * must not be merged. This one only controls the layout chain in `App.tsx`.
 *
 * Why it exists: `App.tsx` wraps every route in a plain block `div`. A block
 * grows to its content, so a page relying on `height: 100%` resolves against an
 * auto-height ancestor and expands to the full content height instead of being
 * bounded — pushing its composer/controls off-screen (measured: a 104,305px
 * transcript in a 950px viewport). Any page with a fixed footer element needs
 * this flag, or it will reproduce that bug.
 */
export const FULL_HEIGHT_ROUTE_PATHS = [
  ...CHAT_ROUTE_PATHS,
  "/webchat",
  "/qubit-bot",
] as const;

export function isFullHeightPath(pathname: string): boolean {
  return (FULL_HEIGHT_ROUTE_PATHS as readonly string[]).includes(
    normalizeRoutePath(pathname),
  );
}
