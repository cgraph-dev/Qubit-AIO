# /chat TerminalChatPage defects — findings (worktree fix/chat-terminal-surface)

Branch: fix/chat-terminal-surface, base 4b66ad103.

## Routing (confirmed in code)
- `App.tsx` builtinRoutes: with `embeddedChat` true, `/chat` -> TerminalChatPage,
  `/qubit-chat` -> ChatRouteSink (ChatPage).
- `lib/dashboard-flags.ts` `isDashboardEmbeddedChatEnabled()` returns literal `true`.

## Defect A — panel listed the wrong store (FIXED)
`TerminalChatPage.tsx` passed `source="canonical"` to `ChatSessionList`.
`canonical` reads `GET /agent/api/sessions` — a merged SUPERSET that keeps
0-message `dashboard-*` picker-noise rows. The default (`chat`) store
(`GET /agent/api/qubit/chat/sessions`) is the transcript store the PTY resumes.

Live server evidence (token-authenticated, :8890):
- canonical: total 541, 100 rows returned, 4 with message_count 0
  (`dashboard-7a401d98b0b6`, `dashboard-22c0259a0a0c`, `dashboard-1a32e95f0c2b`,
  `dashboard-7f00be7d9017`).
- chat store: total 212, 100 rows returned, 0 with message_count 0.
Those 4 canonical-only rows are exactly the ones that "open blank".

Fix: drop the prop in `TerminalChatPage.tsx` (ChatSessionList defaults to `chat`),
same as the ChatPage fix.

## Defect B — session pick not reaching the PTY (FIXED in source; NOT reproducible on the served bundle)
Mechanism (source at 4b66ad103):
- Resume env IS wired end to end: `pty_ws._default_spawn` ->
  `_resolve_chat_argv(resume=...)` -> `web_server_chat._resolve_chat_argv:498`
  sets `env["HERMES_TUI_RESUME"] = resume`, read by `ui-tui/src/config/env.ts:28`.
  So a SPAWN with a new resume does open its transcript.
- The pick did not spawn. `ChatSessionList.pick()` only sets `?resume=<id>`;
  TerminalChatPage reconnects presenting the SAME stable per-tab `?attach=`
  token (only New chat rotated it). Server step-1b (`pty_ws.py:890-903`)
  `_reattach(key)` then re-attached the still-live detached PTY for that tab
  VERBATIM — `resume` was never consulted, so the old transcript stayed.

Root cause of the miss in the first fix attempt: rotation was a caller-supplied
flag, and the resume-probe dial-gate effect
(`if (allowed && wasBlocked) connectRef.current()`) re-dialed with no args,
dropping it.

Fix: derive rotation in `connect` from the target — rotate whenever the target
differs from the session the current attach token owns (`attachSessionRef`), so
no caller can drop it. Record the owned session after each connect and on the
server control frame.

### Served bundle does NOT reproduce defect B
The bundle served at :8890 is a STALE pre-rebrand build (verified by fetching
the chunks):
- `TerminalChatPage`: no `source`, no `workspaceCwd`, no `rotateAttach`.
- `ChatSessionList`: destructures only `activeSessionId/className/onPicked/onNewChat`
  and calls `listChatSessions` directly — no `source` prop at all.
- `api` chunk reads `window.__HERMES_SESSION_TOKEN__` and sends
  `X-Hermes-Session-Token`, but the served HTML injects
  `window.__QUBIT_SESSION_TOKEN__` -> every API call 401s unless the legacy
  global is injected first.

That build sends NO `?attach=` on the PTY URL, so the server never re-adopts and
every resume spawns fresh. Live CDP click on the served page:
- before: `ws://.../agent/api/pty?ticket=...&cols=76&rows=8` (no attach)
- click "Old messages not loading, qubit-office2 and 3 missing" row
- after: URL `?resume=20261016_165455_ec6027`, a SECOND socket
  `ws://.../agent/api/pty?ticket=...&resume=20261016_165455_ec6027`, and the
  terminal text changed to that session's real transcript.
Screenshots: /tmp/chat-terminal-shots/served-authed.png,
/tmp/chat-terminal-shots/served-after-row-click.png

So on the current source the attach-based re-adoption is a real latent defect,
but it cannot be observed against the stale served bundle. The fix targets the
current source; a build + restart is required to serve it.
