# FROZEN TARGET: upstream `43faacfb8de7`

**Re-frozen 2026-10-16** (owner ruling, in force until hermes is retired). This is the
revision Qubit must match before hermes is retired. It is NOT "upstream's latest" — that
moves. It is the finish line.

> **History:** the previous `docs/FROZEN_TARGET.md` (pin `ad12263a5ed4`, frozen 2026-10-07)
> was deleted in `eafb54e26` together with 673 other docs under the owner's English-only
> ruling. That deletion was meant to retire *reference material*, not the pin's own home,
> and it left `scripts/frozen_target_progress.py` carrying a dangling pointer to this file.
> This is the restored home, restated for the current pin. The single machine-readable
> source of the pin is `scripts/frozen_target_progress.py::FROZEN_REV` — if the two ever
> disagree, the script is authoritative and this note is stale.

```
target commit : 43faacfb8de7b29548547a7fbdedad0cc6e7391c
subject       : Merge pull request #139613 from NousResearch/fix/pkg2-provider-override-route
date          : 2026-10-16
previous pin  : 517b5e10febd (2026-10-08, drifted 4,218 commits / 5,434 files behind)
```

## Why a freeze, not "follow HEAD"

Chasing live HEAD is a treadmill: upstream moves (~66 commits/day measured) faster than this
campaign closes symbols, so "same commit as them" never arrives and hermes is never retired.
The freeze makes the finish line reachable.

**Post-freeze upstream commits get ported AFTER hermes is deleted.** Measured at the
re-freeze: the pin is 1,094 commits behind upstream `origin/main` as of 2026-10-17 — that
backlog is explicitly out of scope until retirement.

## The rule

- Measure EVERYTHING against `43faacfb8de7b29548547a7fbdedad0cc6e7391c`. Never `origin/main`.
- Every audit passes `--rev 43faacfb8de7b29548547a7fbdedad0cc6e7391c` explicitly.
- Ports are 1:1 and literal against this revision — including upstream features this fork
  dropped on purpose (owner ruling 2026-10-19): no symbol is skipped as "out of scope", and
  no typed refusal is written in a symbol's place. Exclusions are pruned by the owner after
  the gap is closed, not by an agent mid-port.
- When the production gap for THIS revision reaches zero, verify, then retire hermes.
- Do not move this pin until hermes is gone.

## Declared exclusions (deliberate, do NOT re-port)

Recorded so a future port wave does not re-add these as "missing symbols". Each leaves the
audit one symbol short of zero by design; the audit's job is to report that, not to erase it.

| symbol | file | why it is gone |
|---|---|---|
| `_housekeeping_media_caches` | `gateway/run.py` | Byte-identical DEAD DUPLICATE of the live `gateway/run_housekeeping_disk.py::housekeeping_media_caches`, which the registered chore (`run.py`) already calls. A 6-lane merge re-added the copy; it never executed. Deleting it is behaviour-preserving — re-adding it would restore a dead symbol, not a capability. |

## Numbers

Baseline at the re-freeze (2026-10-17, first complete-blob read of the pin):

| metric | at freeze | now (2026-10-19) |
|---|---|---|
| upstream `.py` files | 9,503 | 9,503 |
| absent files | 974 | 901 |
| short_port files / symbols | 610 / 1,649 | 453 / 1,189 |
| behind_head files / symbols | 0 / 0 | 0 / 0 |
| **total missing symbols** | **2,623** | **1,189** |
| — production | — | **296** (78 files) |
| — tests (non-blocking) | — | 1,794 |

Closed since the freeze: **1,434 symbols** (~55% of the gap). The verdict is driven by
PRODUCTION only — tests cannot block retiring hermes.

Read it in one command:

```bash
.venv/bin/python scripts/frozen_target_progress.py
```

which prints `NOT AT TARGET -- N production missing` or `AT TARGET`, plus the delta against
the frozen baseline.

## Retirement checklist

1. **Disable `hermes-dashboard.service` FIRST** if it exists and is enabled+active — with
   `Restart=always`/`RestartSec=5` a moved-aside home makes it restart-storm every 5s.
   *(Checked 2026-10-19: no hermes-dashboard unit is loaded on this host; it was already
   handled. Re-check before retiring.)*
2. `mv ~/.hermes` aside (do not delete — that is the rollback).
3. Restart the four units: qubit-voicebridge, qubit-voice-typing, qubit-voice-orb,
   qubit-gateway.
4. Verify each surface: the dashboard, /qubit-chat, /chat, voice (Right-Ctrl + wake).
5. Only then delete.
