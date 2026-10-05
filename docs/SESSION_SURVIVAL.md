# Session-restart survival — consolidated record

Status: **NOT SOLVED.** The `/chat` PTY terminal does not auto-re-issue a turn
interrupted by a service restart; the operator must type `continue`. Written
2026-10-04. Measurement source: [PTY_AUTO_CONTINUE_GAP.md](PTY_AUTO_CONTINUE_GAP.md);
root cause: [RPC_BOOT_PATH_TRACE.md](RPC_BOOT_PATH_TRACE.md);
status entry: [STATUS.md](STATUS.md) §86. No `VERIFIED`, `M1`, `M2`,
`CODE_READY`, `COMPLETE`, `100%`, or `1:1` is claimed. The gap is **NOT FIXED**.

## The two surfaces (the original confusion)

| Route | Page | Transport | Auto-continue after restart? |
|---|---|---|---|
| `/chat` | `TerminalChatPage.tsx` | **PTY** — embeds the real `qubit --tui` | ✗ never fires (operator's surface) |
| `/qubit-chat` | `ChatRouteSink` → `ChatPage.tsx` | SSE (`voicebridge/chat_transport.py`) | added earlier, different page |

The operator uses the terminal at **`/chat`** (PTY). The earlier auto-resume
work landed on the **SSE** path (`/qubit-chat`) — a different page — which is why
nothing changed for the operator.

## What IS proven (measured, upstream of the break)

- **Session id resumes.** Live Node TUI carries
  `HERMES_TUI_RESUME=20261003_093120_d09dc4`; the session exists in `state.db`
  (`sessions.id`, `source='tui'`). History survives the restart.
- **The interrupted-turn marker is written and keyed correctly.**
  `runtime/desktop/interrupted_turns.json` holds
  `{"20261003_093120_d09dc4": {"attempts": 0, "prompt": "continue",
  "auto_continue": true, "started_at": …}}` — keyed to `sessions.id`.
- **The config gate would pass.** `_auto_continue_config()` resolves to
  `(enabled=True, freshness=15min, max_attempts=2)`; a 1-second-old marker is
  well inside the window.
- **The client resume wiring is present.** `config/env.ts:28` reads
  `HERMES_TUI_RESUME`; `useMainApp.ts:920` passes it into
  `createGatewayEventHandler`; `createGatewayEventHandler.ts:743` calls
  `resumeById(STARTUP_RESUME_ID)` on `gateway.ready`.
- **The server gate exists and would run the turn itself.**
  `_maybe_schedule_auto_continue` (`qubit_runtime/tui_gateway/session_auto_continue.py:57`)
  calls `_run_prompt_submit(… display_kind="auto_continue")` on a worker thread.

## What IS unproven / NOT solved (the actual defect)

- **MEASURED: the server gate is never entered on the dashboard PTY boot path.**
  A temporary probe at `session_auto_continue.py:65` (before the marker read)
  logging `[ACPROBE] gate reached` never fired across a restart with a fresh
  valid marker. `journalctl --user -u qubit-voicebridge --since "4 min ago" |
  grep ACPROBE` → empty. Probe removed; `git diff` on that file clean. So the
  break is **not** in the gate logic.
- **ROOT CAUSE (pinned by direct code read).** `session.resume` *is* the RPC the
  TUI uses (`ui-tui/src/app/useSessionLifecycle.ts:374` → `gw.request
  ('session.resume', {cols, session_id})`), and it *is* dispatched
  (`methods_session.py:917`; allowlist `server.py:174`). The handler routes to
  four branches (`methods_session.py:~934-942`); the gate is present on only two:
  `_resume_cold` (`:853`) and `_resume_eager` (`:914`). `_resume_lazy` (`:788`)
  and `_resume_reuse_live`/`_resume_reuse_live_locked` (`:752-766`, returns `_ok`
  at `:766`) have **no gate**. The dashboard TUI reconnect takes the live-reuse
  branch (`methods_session.py:937`, `return _resume_reuse_live(...)`), because
  `_find_live_session_by_key` (`server.py:2863`) still reports the session live
  **in that gateway process**. That branch returns a valid resume payload with
  intact history, so the client shows a healthy resumed chat while the gate is
  never reached — exactly matching the measured negative (`[ACPROBE]` never
  fired). Sibling early return `methods_session.py:848` (`return reused`, past a
  `ctx.claim` live hit) skips the gate on the cold path too. Full trace:
  [RPC_BOOT_PATH_TRACE.md](RPC_BOOT_PATH_TRACE.md). *(Supersedes the earlier
  "session.resume not called / boot RPC not pinned" wording.)*
- **Honest boundary:** *which* of the two gate-skipping returns fires on the
  operator's exact restart sequence is **not** proven from static code; both are
  consistent with the measured symptom. The fix is **NOT applied**.

## Shipped but aimed at the wrong surface (does NOT fix the symptom)

The `/qubit-chat` SSE surface (`voicebridge/chat_transport.py`) gained: a durable
interrupted-turn marker with freshness/attempt gating, `interrupted_turn` on the
session-show route, client auto-resume on load, bounded retry/backoff
(`api.ts` `withBackoff`, 6 vitest), and a deterministic active-session file path
(`qubit_runtime/qubit_cli/web_server_chat.py`, 3 tests). Real and tested, but on
a page the operator does not use for this symptom.

## Also unfixed (independent)

The TUI is not handed `HERMES_TUI_ACTIVE_SESSION_FILE`, so it cannot write the
resume id itself. Harmless today (`HERMES_TUI_RESUME` already carries the id), but
it is why no `/tmp/hermes-pty-active*` file exists and why the stable-path change
in `web_server_chat.py` is not yet exercised live.

## Second defect (independent): the >7s terminal dead-end

Separate from the auto-continue gap. If the backend stays down **longer than
~7s** with the `/chat` browser tab open, the ws-ticket retry ladder is exhausted
(`withBackoff`, `lib/api.ts:792` → 5 attempts; `.catch` at
`TerminalChatPage.tsx:270` schedules nothing), no socket exists to fire
`onclose`, and the page is **wedged until a manual reload / New chat**. The
`ptyRejectionBanner` / `lib/pty-close-copy.ts` copy (incl. the string "the
dashboard server was restarted") is **dead code — imported nowhere**. The ~7s
boundary itself is proven by code-reading, not yet by a timed live restart.
Detail: [VERIFY_TERMINAL_RECONNECT.md](VERIFY_TERMINAL_RECONNECT.md). **Not fixed.**

## Exact reproduction

1. Open the dashboard terminal at `/chat` (PTY embedding `qubit --tui`).
2. Send a prompt; interrupt mid-turn.
3. `systemctl --user restart qubit-voicebridge`.
4. Observe: the TUI resumes the session id but the interrupted turn is **not**
   re-issued — the operator must type `continue`.

## Verification commands

```
cat ~/.local/state/qubit-voicebridge/runtime/desktop/interrupted_turns.json
sqlite3 ~/.local/state/qubit-voicebridge/runtime/state.db \
  "select id,source from sessions where id='20261003_093120_d09dc4'"
tr '\0' '\n' < /proc/$(pgrep -f 'ui-tui/dist/entry.js'|head -1)/environ | grep HERMES_TUI_RESUME
journalctl --user -u qubit-voicebridge --since "4 min ago" | grep ACPROBE   # probe removed; empty
```

## Rejected hypotheses (each disproved by evidence — do not re-try)

- Session id lost after restart — FALSE; `HERMES_TUI_RESUME` is preserved.
- Marker keyed to a wrong/transient id — FALSE; it matches `sessions.id` exactly.
- Marker stale or config disabled — FALSE; 1 s old, gate enabled.
- Marker home-path mismatch — FALSE; `_marker_path(home)` is the live file's location.
- Stale TUI bundle — FALSE; `dist/entry.js` is newer than the source and contains the resume branch.
- Active-session lease refused the claim — FALSE; that refusal log never appears, because the gate is never entered at all.

## Next step (pinned, not yet applied)

The break is pinned — no further boot-path probing is needed. Wire the gate onto
the live-reuse path: in `_resume_reuse_live_locked`
(`qubit_runtime/tui_gateway/methods_session.py:752`), before `return _ok(...)`,
call `_maybe_schedule_auto_continue(sid, session, ctx.target)` and thread
`auto_continue` into the payload (mirroring `:853`); or hoist the gate into the
`session.resume` handler after the live/found session is known
(`methods_session.py:917`). Both are **NOT applied**. The client needs no change —
`resumeById` already sends the right RPC. See [RPC_BOOT_PATH_TRACE.md](RPC_BOOT_PATH_TRACE.md)
"Recommended minimal fix (NOT applied)".
