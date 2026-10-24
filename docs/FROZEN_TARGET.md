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
- When the production gap for THIS revision reaches zero **apart from declared exclusions**
  (currently exactly one, `_gateway_lock_handle`), verify, then retire hermes. The instrument
  always prints that one row, so literal zero is not the gate; the owner may prune the row
  instead of waiting for a zero it will never print.
- Do not move this pin until hermes is gone.

## Declared exclusions (deliberate, do NOT re-port)

**None remain (owner ruling 2026-10-24).** The B1 row was retired by adding the pin's inert
`_gateway_lock_handle = None` literally; no acquire/release machinery is ported, and runtime
ownership stays with `gateway.runtime_ownership.process_ownership`. See `docs/DECISIONS.md`
ruling 10.

Recorded so a future port wave does not re-add these as "missing symbols". Each leaves the
audit one symbol short of zero by design; the audit's job is to report that, not to erase it.

| symbol | file | why it is gone |
|---|---|---|

> **Owner ruling 2026-10-21 (F5):** the 21 rows previously in this table were REJECTED,
> not re-affirmed. The 20 production symbols among them were ported literally and wired
> in wave 16; `_tool_prefix` was already live at the base (the row was stale), and the
> `legacy_digests` test helper was restored and is now used by its test. See
> `docs/STATUS.md` (wave 16) and `docs/DECISIONS.md` ruling 8.

## Numbers

Baseline at the re-freeze (2026-10-17, first complete-blob read of the pin):

| metric | at freeze | now (2026-10-24, `1883818bd` + B1 retire) |
|---|---|---|
| upstream `.py` files | 9,503 | 9,503 |
| absent files | 974 | 0 |
| short_port files / symbols | 610 / 1,649 | 0 / 0 |
| behind_head files / symbols | 0 / 0 | 0 / 0 |
| **total missing symbols** | **2,623** | **0** |
| — production | — | **0** |
| — tests (non-blocking) | — | 0 |

Closed since the freeze: **2,623 symbols (100% of the gap)**. The B1 exclusion was retired on
2026-10-24 by adding the pin's inert literal; the short-port column is empty. The verdict is driven by
PRODUCTION only — tests cannot block retiring hermes. There is no remaining production symbol; the audit prints `AT TARGET`.

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
