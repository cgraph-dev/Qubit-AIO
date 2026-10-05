# RESTART_SURVIVAL — how long work outlives a `qubit-voicebridge` restart

## The problem (measured, not inferred)

`systemctl --user restart qubit-voicebridge` killed the interactive agent session every time,
so work stopped until the owner typed `continue`. Process tree, verified with
`ps -o pid,ppid,cmd`:

```
systemd --user
└─ voicebridge run                  <- ExecStart / MainPID
   └─ ui-tui node (entry.js)
      └─ tui_gateway.entry         <- hosts the interactive agent session
```

All three sit in the SAME cgroup (`qubit-voicebridge.service`), and the systemd default is
`KillMode=control-group`, which SIGTERMs the whole cgroup on restart. The turn returned
`signal 15`.

Verify the topology at any time:

```bash
TUI=$(pgrep -f 'tui_gateway.entry' | head -1)
ps -o pid,ppid,cmd -p "$TUI"                    # its parent
cat /proc/$TUI/cgroup                           # which unit owns it
systemctl --user show qubit-voicebridge -p KillMode --value
```

## Three attempts — only the third works

1. **`KillMode=process` drop-in** — applied at
   `~/.config/systemd/user/qubit-voicebridge.service.d/10-killmode-process.conf`:

   ```ini
   [Service]
   KillMode=process
   ```

   Then `systemctl --user daemon-reload` (confirmed: `KillMode` -> `process`). **Necessary but
   NOT sufficient** — measured after applying it, a restart still produced a NEW
   `tui_gateway.entry` PID, because the backend tears down its own TUI children on shutdown.

2. **`setsid()` in the child** — **DOES NOT WORK.** cgroup membership is inherited at fork and
   `setsid()` only changes the session. Verified: a `setsid`-ed worker still reported
   `0::/user.slice/.../qubit-voicebridge.service` in `/proc/<pid>/cgroup`, so a restart killed
   it anyway.

3. **Transient systemd unit — WORKS.** `systemd-run --user --collect --no-block --unit=<name>`
   puts the work in its OWN unit, outside the bridge cgroup:

   ```
   qubit-detached-<label>.service -> 0::/user.slice/.../app.slice/qubit-detached-<label>.service
   ```

## Usage

```bash
# launch (returns immediately; survives a bridge restart)
.venv/bin/python scripts/detached_run.py --label <name> --timeout <secs> -- <command...>

# read results
.venv/bin/python scripts/detached_run.py --list
.venv/bin/python scripts/detached_run.py --show <name>
```

Artifacts: `<runtime>/detached/<label>/{result.json,out.log}`. `result.json` is written
atomically (tmp + rename) with `status` = `running | ok | failed | timeout`.

## Verified survival

Launched a 45-tick/45-second job, then ran `systemctl --user restart qubit-voicebridge` mid-run.
Result: `status: ok`, `exit: 0`, **all 45 ticks present through to `DONE`**, and the bridge came
back up. The restart did not interrupt the job.

## Rules for an agent that must restart its own backend

- **Prefer not restarting.** Only a Python source change requires it; the Vite build, route
  probes and DOM checks do not.
- **Run long work through `detached_run.py`.** Anything that must outlive the turn — agent
  batches, long builds, crawls — goes there, not in the foreground.
- **Never restart mid-task.** Finish the step, write remaining steps to a file, THEN restart.
- **`--no-block` is load-bearing.** Without it `systemd-run` blocks until the unit exits, which
  defeats the purpose. A 20 s probe of `systemd-run --user --scope` timed out for exactly this
  reason; `--no-block` returns in ~5 ms.
- **The self-restart is invisible while it happens.** Put the restart LAST on the command line —
  anything after it in the same line will not run if the session dies.
- After any restart, re-verify before declaring a page restored.

## Known limits

- `systemd-run` must be present and the user manager must allow transient units (it does here;
  `systemctl --user` lists `.scope` dirs for other apps).
- A fixed `--unit=` per label collides with a still-active unit of the same label; use a new
  label, or wait for `--collect` to clear the finished one.
- The detached job runs OUTSIDE the bridge: it sees the repo and the env it was given, but no
  in-process agent state.


## ROOT CAUSE of the "I had to write `continue`" loop

Two independent killers, both measured:

### 1. A dashboard page refresh / disconnect tears the TUI down

`voicebridge/pty_ws.py:805-808`:

```python
try:
    self._pump(handler, session)      # stream to the websocket
finally:
    self._teardown(session)           # <- kills the PTY + the TUI process
```

`_teardown` runs in a `finally` when the WebSocket closes, so **closing or reloading the
dashboard page kills the TUI that hosts the agent conversation.** The in-flight turn dies with
it. `pty_ws._default_spawn` (line ~406, `PtyBridge.spawn(...)`) builds a fresh `argv` and spawns
a NEW process per connection — there is **no reuse**.

A reuse registry DOES exist but is unused by this path:
`chat_transport.pty_session(cfg, session_id)` (line ~3808) keys live sessions by
`f"{state_dir()}\0{session_id}"` and only spawns when the key is absent. Its callers are the
chat-transport routes (`/api/qubit/chat/pty`, `gateway/qubit_handlers/sessions.py`), NOT the
`/api/pty` websocket the dashboard uses.

### 2. `systemctl restart` tears the whole cgroup down

See above — fixed for *work* by the detached runner, not for the session.

### Consequence

The session cannot be made immortal from inside itself. Two things follow:
- **Work must not depend on the session** — use `scripts/detached_run.py`.
- **A durable session needs the `/api/pty` path to reuse a live PTY** (attach to the existing
  session for the same session id instead of `teardown`-on-disconnect + fresh spawn). That is a
  real fix in `voicebridge/pty_ws.py`, not yet implemented — it needs care around the
  ticket/origin/estop checks and the `resume` semantics, so it should be done deliberately and
  verified with a refresh mid-turn.
