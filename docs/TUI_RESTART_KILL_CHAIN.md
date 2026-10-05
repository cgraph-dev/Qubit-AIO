# Why a `systemctl --user restart` killed the dashboard /chat turn

Scope: the dashboard terminal (`/chat` → `TerminalChatPage.tsx`) embedding the real TUI
over a PTY. Written 2026-10-04 after the fix was applied but **before** it was proven by a
live restart. Read the status line at the bottom before trusting any claim here.

## The two surfaces

```
systemd (qubit-voicebridge.service)
  └─ voicebridge run            ← ExecStart, Type=simple, KillMode=process
       ├─ node ui-tui/dist/entry.js         (Node parent, "the TUI")
       └─ python -m tui_gateway.entry       (its child, hosts the session)
```

## What systemd does on restart — measured, not assumed

`systemctl --user show qubit-voicebridge` reports:

```
Type=simple   KillMode=process   KillSignal=15 (SIGTERM)   SendSIGKILL=yes   Delegate=no
```

- **`KillMode=process` IS applied** (drop-in
  `~/.config/systemd/user/qubit-voicebridge.service.d/10-killmode-process.conf`).
  On stop, systemd signals **only the main PID** (`voicebridge run`) with SIGTERM. It does
  *not* signal the cgroup.
- The journal's `Found left-over process <pid> (<cmd>) in control group while starting unit.
  Ignoring.` lines are systemd **noticing** the surviving TUI children on the next start —
  they are *not* evidence of a kill. `Ignoring.` is literal.

**Therefore systemd is not what signalled the TUI.** The SIGTERM that reached
`tui_gateway.entry` (its crash log shows `=== SIGTERM received ===` on every restart) came
from inside the TUI process tree.

## The actual kill chain (matched every observation)

1. `voicebridge run` dies → the PTY's write end closes.
2. systemd's stop-time SIGTERM also reaches the **Node TUI**, because the TUI was started as
   a child in the same cgroup... but `KillMode=process` spares it. So the TUI's death came
   from its **own** handling, not from the signal:
   - the cgroup sweep / the PTY teardown delivered **SIGHUP** to the Node TUI;
   - `setupGracefulExit` treated SIGHUP as an exit → ran `cleanups` →
     `gw.kill('graceful-exit-cleanup')` → **forwarded SIGTERM to `tui_gateway.entry`** →
     that child logged `=== SIGTERM received ===`;
   - then `process.exit()`.
3. Separately (or concurrently), the severed PTY write end made every render write throw
   **EIO**; 5 of them landed in ~150 ms, and the `onError` handler ran
   `dead output stream (EIO x5) → exiting` → `gw.kill('dead-output-stream')` →
   `process.exit(1)`.

Either path bypasses `GatewayClient`'s own recovery (`scheduleReconnect` /
`planGatewayRecovery`), because `kill()` sets `disposed = true` and `start()` is a documented
no-op after `kill()`. **That is why the session died and the user had to type `continue`.**

## The fix (applied to `qubit_runtime/ui-tui/src/entry.tsx` only)

```ts
// (a) a restart's SIGHUP must not trigger graceful-exit under the dashboard
ignoredSignals: DASHBOARD_TUI_MODE ? ['SIGINT', 'SIGHUP'] : []

// (b) an EIO burst must not exit the process under the dashboard
if (++consecutiveDeadStreamErrors >= 5 && !DASHBOARD_TUI_MODE) { … process.exit(1) }
```

`SIGTERM` deliberately still exits — only SIGHUP (restart) and SIGINT (raw-mode Ctrl+C) are
ignored, and only when `HERMES_TUI_DASHBOARD=1`. A plain terminal SIGHUP keeps the old
behaviour.

With (a) in force, `graceful-exit-cleanup` never runs on a restart, so `gw.kill()` is never
called and `tui_gateway.entry` is never SIGTERMed. With (b), the EIO burst no longer exits
before `scheduleReconnect()` can land.

## Evidence status

- **PROVEN:** the SIGHUP half. At the 19:05:15 restart the log contained **zero**
  `graceful-exit received signal=SIGHUP` lines — the handler no longer fires.
- **PROVEN:** systemd's semantics above (`KillMode=process` applied; `Ignoring.` ≠ killed).
- **UNPROVEN:** that the TUI now *survives* a restart end-to-end. No TUI process has yet run
  the 19:06:19 bundle *through* a restart — every restart so far killed a process that had
  loaded an older bundle. Needs one restart to load, then a second to test.

## Residual risk (honest)

With SIGHUP ignored and EIO non-fatal, the TUI's remaining exit paths under the dashboard are
SIGTERM, memory-critical `process.exit(137)`, and stdin EOF. If the browser tab closes without
those, the process could linger. Not yet measured; the PTY/`attach` reaper should collect it.

## The recovery budget caveat

`qubit_runtime/ui-tui/src/app/gatewayRecovery.ts` caps respawn+resume at
`GATEWAY_RECOVERY_LIMIT = 3` per `GATEWAY_RECOVERY_WINDOW_MS = 60_000`. An agent restarting
voicebridge **more than 3 times in 60 seconds** exhausts the budget and the client shows its
"gave up" state — the one realistic way to still lose the session after this fix.
