# /chat TerminalChatPage defects (worktree fix/chat-terminal-surface)

## Routing (confirmed)
- App.tsx:729 routes `/chat` -> TerminalChatPage; `/qubit-chat` -> ChatPage.
- lib/dashboard-flags.ts `isDashboardEmbeddedChatEnabled()` returns literal `true`.

## Defect A — panel lists the wrong store (fixable)
TerminalChatPage.tsx:1358 passes `source="canonical"` to ChatSessionList.
ChatSessionList defaults to `source="chat"` (GET /api/qubit/chat/sessions),
which is the store whose every row opens with a real transcript. The
`canonical` (/api/sessions) route is a merged superset that keeps 0-message
picker-noise rows. ChatPage.tsx:3028-3052 was fixed to DROP `source="canonical"`.

## Defect B — selecting a session does not reach the PTY (server-side)
The resume env IS wired end to end:
  pty_ws._default_spawn -> _resolve_chat_argv(resume=...) ->
  web_server_chat._resolve_chat_argv:498 env["HERMES_TUI_RESUME"]=resume,
  and ui-tui/src/config/env.ts:28 reads process.env.HERMES_TUI_RESUME.
So a spawn with a new resume DOES open that transcript.

BUT the row pick does not spawn. ChatSessionList.pick() only sets
`?resume=<id>`. TerminalChatPage reconnects via `connect()` which presents the
SAME stable per-tab `?attach=` token (ptyAttachToken(false); only New chat
rotates it). Server step-1b (pty_ws.py:890-903) then runs `_reattach(key)` on
that token, finds the still-LIVE detached PTY for this tab, and re-attaches it
verbatim -- `resume` is never consulted. The terminal keeps its old transcript
(the welcome splash / prior session). That is the observed bug.

### Fix
Rotate the attach token on a RESUME (not just a fresh chat) so the server
cannot re-adopt the old terminal, and send `?fresh=1` is NOT right for a
resume. Instead: pass a distinct `attach` per resume target so `_reattach(key)`
misses. The server then spawns a new PTY with HERMES_TUI_RESUME=<id>.
