# Session survival across restart — exact remaining fix

Status: **root cause fully identified, fix NOT applied.** Hand this to the next session.

## The system, as it actually is

```
qubit-voicebridge.service  (cgroup; KillMode=process drop-in applied)
 └─ voicebridge run                    ← backend, stable PID
     └─ node ui-tui/dist/entry.js      ← spawned per /api/pty connection
         └─ python -m tui_gateway.entry ← THE SESSION (this conversation)
```

Env on the session proves the path: `HERMES_PTY_HOST=dashboard`,
`HERMES_TUI_RESUME=20261003_093120_d09dc4`.

## The mechanism that already exists (do not rebuild it)

`qubit_runtime/qubit_cli/pty_session.py` — a keyed registry that ALREADY implements
detach/reattach with output replay:

- `PtySessionRegistry.attach_or_spawn(key, spawn=...)` (line ~187) returns
  `(existing, False)` when a live session already owns `key` — reuse, not a new fork.
- `PtySession.attach / .detach` (lines ~102 / ~126), `WS_CLOSE_SUPERSEDED = 4409`.
- `RingBuffer` replays output the client missed while detached.
- `reap_idle()` retires a detached session after a TTL.

## Why it does not fire today (the two gaps)

1. **No `?attach=`: `qubit_runtime/qubit_cli/web_routers/chat_ws.py:511-533`**

   ```python
   attach_token = ws.query_params.get("attach") or None
   ...
   if attach_token is None:
       # Legacy path: 1:1 socket<->PTY, killed on disconnect.
       bridge = _spawn()
       await _legacy_pump(ws, bridge)
       return
   ```

   The dashboard does not send `?attach=`, so every connect forks a NEW PTY and
   the socket/PTY pair dies on disconnect. The frontend helper
   `frontend/qubit-web/src/lib/pty-attach-token.ts` (`ptyAttachToken()`, storage key
   `qubit.pty.token.chat`, Web-Lock claimed per tab) EXISTS but has **no callers** —
   it was ported and never wired.

2. **Shutdown kills it anyway: `qubit_runtime/qubit_cli/web_server.py:285`** calls
   `await PTY_REGISTRY.close_all()`, which closes every session including detached
   ones (see `close_all`, `pty_session.py:~233`).

## The fix (small; both files are vendored upstream — treat with care)

- **Client:** make the PTY connect always include a stable per-tab `?attach=` from
  `ptyAttachToken()` (it already mints/claims a token; a reload of the same tab
  reuses it, a second live tab mints its own). Then `attach_or_spawn` reuses the
  running terminal and the ring buffer replays the missed output.
- **Server:** on shutdown, preserve *detached* sessions rather than closing all of
  them — or accept that the process is going away regardless and rely on the
  idle-TTL reaper plus a fresh spawn for the next connect.
- NOTE: `web/AGENTS.md` says the PTY/chat path is the embedded real TUI — do not
  re-implement it; only wire the existing attach token and the shutdown policy.

## Already fixed and verified this session (do not redo)

- `voicebridge/chat_transport.py` — a browser refresh no longer loses the in-flight
  turn. The reply is persisted in the model worker thread (`_persist_reply`) before
  the client is told anything, guarded by `saved_box` so a turn saves exactly once,
  and a STOPPED (cancel / e-stop) turn is NOT persisted as if complete.
  Verified live: abandon the SSE stream at byte 200 → reply present; full drain →
  exactly one assistant message.
- Frontend build was broken (orphan `<MonitorCard />` in
  `frontend/qubit-web/src/components/om-agent-ui.tsx`; a `never` inference in
  `frontend/qubit-web/src/lib/voice/local-streaming-stt-adapter.test.ts`), so
  `frontend/qubit-dist` had been frozen and the served page ran stale code. Built.
- `scripts/detached_run.py` — `--timeout` was never forwarded to the detached
  (inner) run, so every detached job ran unbounded; and reusing a live label fell
  back to an inline run that clobbered the running job's `result.json`. Both fixed
  and verified (`--timeout 4` on `sleep 30` → `status: timeout, exit 124`; duplicate
  label → exit 2, prior result uncorrupted).
- `KillMode=process` drop-in — proven in the journal: on restart systemd logged
  "Unit process … remains running after unit stopped" for 8 processes.
- `voicebridge/pty_ws.py` detach/reattach (`_detach`, `_reattach`, `detach_all`,
  `?attach=` threading) — correct code, but this module is NOT the path the
  dashboard session uses. Its tests are real; the wiring does not reach the session.

## Evidence commands

```bash
# which process hosts the session, and how it was launched
for p in $(pgrep -f tui_gateway.entry); do tr '\0' '\n' < /proc/$p/environ | grep -E 'TUI_RESUME|PTY_HOST'; done

# did the PTY registry ever log attach/detach on this service?
journalctl --user -u qubit-voicebridge --since "30 min ago" | grep -iE 'pty|attach'

# the reuse/spawn decision
sed -n '511,540p' qubit_runtime/qubit_cli/web_routers/chat_ws.py
sed -n '187,205p' qubit_runtime/qubit_cli/pty_session.py
```
