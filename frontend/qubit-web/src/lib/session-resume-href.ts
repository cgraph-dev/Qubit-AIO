/**
 * Canonical `/chat` resume link builder.
 *
 * Every "open this session in chat" affordance (Sessions History rows, the
 * Overview recent-session cards, the chat side panel) must produce the same
 * route. Two fields matter to the terminal:
 *
 * - `resume` is the canonical session id the PTY seeds via
 *   `HERMES_TUI_RESUME`.
 * - `profile` scopes the PTY to the store that owns the row. The server's
 *   `/api/pty` resolves `QUBIT_HOME` from the *query* profile, not the
 *   ws-ticket, so omitting it makes a non-default row resume against the
 *   dashboard's launch home — the session is missing there and the terminal
 *   spawns a fresh chat instead.
 *
 * The profile is omitted when the row carries none (the dashboard's own
 * store), so a plain default-profile link stays `/chat?resume=<id>`.
 */
export function sessionResumeHref(
  id: string,
  profile?: string | null,
): string {
  const params = new URLSearchParams({ resume: id });
  const scope = (profile ?? "").trim();
  if (scope) params.set("profile", scope);
  return `/chat?${params.toString()}`;
}
