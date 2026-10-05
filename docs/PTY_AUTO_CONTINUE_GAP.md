# Why the dashboard terminal still needs `continue` after a restart

Status: **root cause MEASURED by instrumented run; fix NOT applied.**
Written 2026-10-04. The earlier draft of this file was inference; this version records what was
actually observed.

## The two surfaces (the original confusion)

| Route | Page | Transport | Auto-continue? |
|---|---|---|---|
| `/chat` | `TerminalChatPage.tsx` | **PTY** — embeds the real `qubit --tui` | ✗ never fires |
| `/qubit-chat` | `ChatRouteSink` → `ChatPage.tsx` | SSE (`voicebridge/chat_transport.py`) | added earlier, different page |

The operator uses the **terminal at `/chat`** (PTY). Earlier auto-resume work landed on the **SSE**
path, which is a different page — that is why nothing changed for the operator.

## Facts established by evidence

1. **The session id resumes correctly.** The live Node TUI carries
   `HERMES_TUI_RESUME=20261003_093120_d09dc4`, and the session exists in `state.db`
   (`sessions.id`, `source='tui'`). History survives the restart. This was never the problem.

2. **The interrupted-turn marker is written correctly.**
   `runtime/desktop/interrupted_turns.json` holds
   `{"20261003_093120_d09dc4": {"attempts": 0, "prompt": "continue", "started_at": ..., "auto_continue": true}}`
   — keyed to the exact session id.

3. **The config gate would pass.** `_auto_continue_config()` resolves to
   `(enabled=True, freshness=15min, max_attempts=2)`. A 1-second-old marker is well inside the window.

4. **`STARTUP_RESUME_ID` is wired on the client.** `config/env.ts:28` reads `HERMES_TUI_RESUME`;
   `useMainApp.ts:920` passes it into `createGatewayEventHandler`;
   `createGatewayEventHandler.ts:743` calls `resumeById(STARTUP_RESUME_ID)` on `gateway.ready`.

5. **The server gate exists and runs the turn itself.** `_maybe_schedule_auto_continue`
   (`tui_gateway/session_auto_continue.py:57`) calls `_run_prompt_submit(... display_kind="auto_continue")`
   on a worker thread, then returns `{attempt, interrupted_at}`. When it fires, the server re-runs the
   turn; the client resends nothing.

6. **MEASURED: the gate is NEVER reached.** A temporary INFO probe was inserted at
   `session_auto_continue.py:65` (before the marker read) logging `[ACPROBE] gate reached`.
   The backend was restarted at 11:00:54 with a fresh valid marker present.
   `journalctl --user -u qubit-voicebridge --since "4 min ago" | grep ACPROBE` returned **nothing**.
   The probe was then removed; `git diff` on that file is clean.

## Conclusion

`_maybe_schedule_auto_continue` is wired into the `session.resume` RPC handler
(`methods_session.py:853`, `:914`). Since the gate is never entered, **`session.resume` is not being
called on the dashboard PTY boot path.** Per `tui_gateway/AGENTS.md`, `session.resume` is the
*session-picker* method; the dashboard's boot recovery takes a different route.

The fix therefore belongs at whichever path the dashboard TUI actually uses to restore its session
on boot — not in the gate, and not in the client's resend behaviour. Pinning the exact boot RPC is
the next step, and needs the probe on the RPC dispatcher rather than on the gate.

## Rejected hypotheses (each disproved by evidence, recorded so they are not re-tried)

- Session id lost after restart — FALSE; `HERMES_TUI_RESUME` is preserved.
- Marker keyed to a wrong/transient id — FALSE; it matches `sessions.id` exactly.
- Marker stale or config disabled — FALSE; 1s old, gate enabled.
- Marker home-path mismatch — FALSE; `_marker_path(home)` is the live file's location.
- TUI bundle stale — FALSE; `dist/entry.js` is newer than the source and contains the resume branch.
- Active-session lease refused the claim — FALSE; that refusal log never appears, because the gate is
  never entered at all.

## Verification commands

```
cat ~/.local/state/qubit-voicebridge/runtime/desktop/interrupted_turns.json
sqlite3 ~/.local/state/qubit-voicebridge/runtime/state.db \
  "select id,source from sessions where id='20261003_093120_d09dc4'"
tr '\0' '\n' < /proc/$(pgrep -f 'ui-tui/dist/entry.js'|head -1)/environ | grep HERMES_TUI_RESUME
```

## What IS shipped and tested (but does NOT address this symptom)

The `/chat` SSE surface (`voicebridge/chat_transport.py`) — a different page — gained: a durable
interrupted-turn marker with freshness/attempt gating, `interrupted_turn` on the session-show route,
client auto-resume on load, bounded retry/backoff on the load + ticket paths, and a stable
active-session file path (`qubit_runtime/qubit_cli/web_server_chat.py`, 3 tests pass). Those are real
but were aimed at the wrong surface for this report.

## Also unfixed (independent)

The TUI is not handed `HERMES_TUI_ACTIVE_SESSION_FILE`, so it cannot write the resume id itself.
Since `HERMES_TUI_RESUME` already carries the id this is currently harmless — but it is why no
`/tmp/hermes-pty-active*` file exists, and the stable-path change in `web_server_chat.py` is therefore
not yet exercised live.
