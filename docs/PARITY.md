# Qubit PARITY — the target, the instruments, the gaps

**Status:** method + pointer index. **Not a verdict, and not a completion claim.** Nothing here
asserts parity; parity is whatever the instruments below measure on the day you run them.

This file replaces the purged `docs/PARITY.md` (deleted in `eafb54e26`). It states no counts of
its own — every number lives in the doc that measured it, next to the command that produced it.

## Two parity axes

| Axis | Target | Measured by |
|---|---|---|
| **Behavioral** — what a user can do | the local reference dashboard at `http://127.0.0.1:9119/?profile=default`, read-only, plus voice-commandability of every capability there | manual/scripted exercise against the live instance; **no single instrument** |
| **Code** — what the tree contains | upstream `43faacfb8de7b29548547a7fbdedad0cc6e7391c` (frozen 2026-10-16) | `scripts/audit_sync_completeness.py`, wrapped by `scripts/frozen_target_progress.py` |

The behavioral directive and the code freeze are different questions with different answers. Do
not convert one into the other: a symbol can be present and the capability still unreachable, and
a capability can work with symbols still absent.

## The code axis

The pin has exactly one machine-readable home. If any document disagrees with it, the script wins:

```bash
grep -n 'FROZEN_REV' scripts/frozen_target_progress.py   # line 71
```

The instruments and their arguments:

```bash
python scripts/frozen_target_progress.py                                  # pin + overall progress
python scripts/audit_sync_completeness.py --rev 43faacfb8de7b29548547a7fbdedad0cc6e7391c --show-symbols
```

Requirements and exclusions are `docs/FROZEN_TARGET.md` (read it before porting); the last
recorded gap list is `docs/FROZEN_TARGET_WORKLIST.md` — "296 production symbols across 78 files",
measured on 2026-10-19 against tree `35fdb1e33`. **That is a historical measurement, not the
current state**: HEAD has moved since. Re-run the command rather than quoting the worklist.

### Reading the numbers correctly

`docs/PARITY_VERIFICATION_2026-10-09.md` records the traps, and they still apply: both counting
instruments walk `.py` only (`audit_sync_completeness.py` filters `p.endswith(".py")`), and counts
must exclude `__pycache__`. Counts that ignore either rule are wrong by a large factor.

```bash
grep -n 'endswith(".py")' scripts/audit_sync_completeness.py   # line 545
```

Deliberate omissions are declared in `docs/FROZEN_TARGET.md` §"Declared exclusions" — a symbol
listed there is missing **on purpose** and must not be re-ported.

## What parity does not mean

- Not "matches upstream HEAD" — upstream moves (~66 commits/day, measured at the re-freeze);
  post-freeze commits are out of scope until hermes is retired.
- Not "`VERIFIED`" — that verdict requires behavioral evidence, per `AGENTS.md`.
- Not "every symbol present" — the audit's job is to report the gap, not to be driven to zero by
  weakening it.
