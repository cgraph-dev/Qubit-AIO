# /qubit-chat capability plan (A + B, and C as an embedded-terminal panel)

Owner request, 2026-10-06. Scope: `/qubit-chat` ONLY. **`/chat` is not modified.**

## Standing constraints

- **Do not modify `/chat`.** It is the reference surface. `TerminalChatPage.tsx` is read-only
  for this work except where a change is purely additive and shared.
- The two routes are intentionally distinct (owner ruling 2026-09-17, `chat-routes.ts:1-10`).
  C does NOT make them identical: `/chat` stays the full-page terminal, `/qubit-chat` gains a
  *bounded, minimizable* terminal panel that deep-links into `/chat`.
- `/qubit-chat` renders `ChatPage` (the persistent host in `App.tsx:1013`). `ChatRouteSink`
  (`App.tsx:308`) is a `null` placeholder — the real UI is the persistent host.
- Verify with `npm run typecheck` + `npx vitest run` in `frontend/qubit-web`, then
  `npm run build` (outputs to `frontend/qubit-dist`).

## Deep-link mechanism (already exists — reuse, do not reinvent)

`/chat?resume=<sessionId>` is the canonical cross-route link. Already used at
`ChatPage.tsx:1493`, `:1904`, `:1978` and `ChatSessionList.tsx:280`.
`TerminalChatPage.tsx:183` reads `searchParams.get("resume")`.

## (A) Slash palette mid-sentence — REAL BUG, fix first

`ChatPage.tsx:909`:

```js
const slashToken = /^\/[A-Za-z0-9_-]*$/.test(input) ? input : null;
```

The `^` anchors to the start of the WHOLE input, so `/` typed after any other character
returns `null` and the palette never opens. `/chat`'s terminal completer finds the token
anywhere.

Fix: extract a pure helper `slashTokenAt(input, caret?) -> {token, start} | null` that finds
the last `/`-prefixed token at a word boundary (`(^|\s)\/[A-Za-z0-9_-]*$`), and filter with its
`token`. Must be a pure function with its own unit test (works on the string, returns the
token) — no DOM needed.

Acceptance:
- `/` alone → palette.
- `please run /` → palette.
- `please run /mod` → filtered to `/model`.
- `/model arg` (space) → palette hidden (existing behaviour preserved).
- A URL like `http://x` does NOT open the palette (the `\s` boundary requirement).

## (B) Panels that `/chat` shows and ChatPage never built

Currently **zero** rendering of these in `ChatPage.tsx` (verified by grep):
subagents/agents, todos, live tokens/time.

Add as React components around the transcript (NOT a second chat surface):

1. **Agents panel** — live subagents/delegations for the session.
   Data: `subagent.list({session_id})` RPC (`tui_gateway/AGENTS.md` § Shared subagent
   snapshots) → `{subagents, delegations}`; control via `subagent.steer/interrupt/tail`.
   Read-only first. Show: id, goal, status, last_tool, elapsed.
2. **Todo panel** — current todo list for the session.
   Data: the session's todo state (see `tools/todo_tool.py` / `INLINE_TOOL_EXECUTORS`);
   find the JSON-RPC method that returns it before inventing one.
3. **Status strip** — live token count + elapsed + model. `ChatPage.tsx` already parses
   `elapsed_ms` and `max_tokens` per message (`:149`, `:158`, `:192`) — extend that parse, do
   not create a second one.

Rule: every panel must fail **non-destructively** — a failed fetch shows a quiet line and
must never break the transcript or the composer (same rule the TUI sidebar follows).

## (C) Embedded terminal panel with minimize/maximize

Requested behaviour:
- A **panel with an animation** that says work is happening in `/chat`.
- **Clicking the panel redirects to that `/chat` session** (deep-link
  `/chat?resume=<sessionId>`).
- The panel's terminal view can be **maximized or minimized**. This is an intentional
  improvement over `/chat` (which is always full-page).

Design constraints:
- The panel embeds the same PTY backend (`/api/pty` + `/api/ws`), NOT a new one.
- Minimized = the animated status strip only (no terminal mounted). Mounting an xterm is
  expensive — do not mount until maximized.
- Maximize/minimize state persists per session so it survives navigation.
- Clicking the header (not the terminal body) navigates to `/chat?resume=<id>`; clicking
  inside the terminal must NOT navigate (it must keep normal terminal selection behaviour).

**This needs owner confirmation before implementation** — see Open Questions.

## Open questions for the owner (do not guess)

1. (C) contradicts owner ruling 2026-09-17 ("intentionally distinct surfaces", neither
   redirects). Confirm the ruling is amended, or the panel is read-only status + link only.
2. Does the embedded panel need to be *interactive* (type into it), or is it a live *view*?
   Interactive = effectively two live terminals on one session, which the session lease may
   refuse (`qubit_cli/active_sessions.py:166`).
3. Todo/agent payloads: confirm the exact RPC method names before implementation.

## Verification each agent must run

```
cd frontend/qubit-web && npm run typecheck
cd frontend/qubit-web && npx vitest run
cd frontend/qubit-web && npm run build
```

Restart to serve: `systemctl --user restart qubit-voicebridge.service` (port 8890).
