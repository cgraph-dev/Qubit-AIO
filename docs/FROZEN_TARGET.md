# FROZEN TARGET: upstream `ad12263a5ed4`

**Frozen 2026-10-07.** This is the revision Qubit must match before hermes is retired.
It is NOT "upstream's latest" — that moves. It is the finish line.

```
target commit : ad12263a5ed4
subject       : refactor(models): name the two canonical-endpoint sources; comment says
                what the code does
date          : 2026-10-08 00:18:12 +0530
from baseline : a036b13793   -> 966 + 363 = 1329 commits
from milestone: ee8dd6c886   -> 363 commits
```

## Why a freeze, not "follow HEAD"

Measured, not estimated: upstream moved **363 commits in the time this campaign closed
~43 symbols of work**. At the re-pin the gap WIDENED — absent files 233 -> 261,
behind-head symbols 592 -> 703, total missing **875 -> 975 (+100)**.

Chasing live HEAD is a treadmill: the target recedes faster than we close it, so "same
commit as them" never arrives and hermes is never retired. The freeze makes the finish
line reachable.

**Post-freeze upstream commits get ported AFTER hermes is deleted**, from a position where
we no longer depend on it. That is the port -> delete -> rebrand order in §7 of the
handoff, applied to the re-pin.

## The rule

- Measure EVERYTHING against `ad12263a5ed4`. Never `origin/main`.
- Every audit call passes `--rev ad12263a5ed4` explicitly. `origin/main` drifts (it moved
  11 commits DURING the audit that produced these numbers).
- When the gap for THIS revision reaches zero, verify, then retire hermes.
- Do not move this pin again until hermes is gone.

## Numbers at the freeze (clean tree, `--rev ad12263a5ed4`)

| metric | value |
|---|---|
| upstream .py files | 8296 |
| absent | 261 (234 are tests -> ~27 production) |
| short_port files / symbols | 111 / 272 |
| behind_head files / symbols | 214 / 703 |
| **total missing symbols** | **975** |

`short_port` is an UPPER BOUND — that branch of the audit skips the hermes->qubit rename
subtraction, so rebranded symbols are re-reported. Previous waves proved 109 of 142
symbols in one directory were false positives. Verify before porting.

## Retirement checklist (from agent 5's INDEPENDENCE_VERDICT)

1. **Disable `hermes-dashboard.service` FIRST.** It is enabled + active, and moving
   `~/.hermes` aside makes its env-file load fail AND its `~/.local/bin/hermes` shim exec
   a dead path (exit 127). With `Restart=always`/`RestartSec=5` that is a restart-storm
   every 5 seconds, forever. The four Qubit units are independent and unaffected.
2. `mv ~/.hermes` aside (do not delete — that is the rollback).
3. Restart the four units: qubit-voicebridge, qubit-voice-typing, qubit-voice-orb,
   qubit-gateway.
4. Verify each surface: the dashboard, /qubit-chat, /chat, voice (Right-Ctrl + wake).
5. Only then delete.

Evidence backing the verdict: deletion harness 16/16 PASS (incl. a PATH-stripped run and a
real local-model turn returning 'pong'); `gateway_daemon --check` under an empty HOME with
no `~/.hermes` -> OK; ZERO `.hermes` writes in gateway/run.py, gateway_daemon.py,
qubit_runtime.py; cron resolves every path through `get_qubit_home()`.
