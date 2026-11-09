/**
 * Desktop log line formatting shared by every desktop log surface:
 * `desktop.log`, the in-app "RECENT LOGS" view, and crash forensics.
 *
 * Each line is prefixed with `[qubit] ` and an ISO-8601 UTC timestamp, matching the
 * Python-side `agent.log` / `gateway.log` convention (`2026-07-12 16:22:17,540 INFO ...`).
 * See #84405.
 *
 * The brand segment is user-visible — it is what a person reads in RECENT LOGS and
 * in `desktop.log` — so it must not carry a retired name. Keep `[qubit]` here rather
 * than reintroducing a per-call-site literal: this function is the single place every
 * line passes through.
 */

/**
 * Format one desktop log line with an ISO-8601 UTC timestamp.
 *
 * `stamp` defaults to now; callers that batch multiple lines (a single
 * stdout chunk) pass one shared stamp so the group reads as one event.
 */
export function formatDesktopLogLine(text: string, stamp = new Date().toISOString()): string {
  return `[${stamp}] [qubit] ${text}`
}
