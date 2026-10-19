# Qubit PLAN — where the real plan lives, and the rules for claiming progress

**Status:** index + claim rules. **Contains no milestones of its own and no completion claims.**
A date, milestone, or percentage invented here would be a second, unsourced authority — which is
exactly the failure this file replaces.

The previous `docs/PLAN.md` (two owner-phased milestones, 2026-09-22) was deleted in
`eafb54e26`. Its milestone framing is not restated: it was written against a pre-reset state, and
`M1`/`M2` are implementation-progress markers only, never release verdicts.

## Where the plan actually is

| Question | Authoritative answer |
|---|---|
| What is the finish line? | `docs/FROZEN_TARGET.md` — pinned revision, port rule, declared exclusions |
| What is left to port? | `docs/FROZEN_TARGET_WORKLIST.md` + `scripts/frozen_target_progress.py` (re-run; never quote the doc) |
| What is true right now? | `docs/STATUS.md` — evidence-backed, updated each session |
| What was in flight when the last agent stopped? | `docs/OPEN_THREADS.md` — durable handoff ledger |
| How is one port chunk executed? | `PORT-METHOD.md` (root) — the method, verbatim |
| Which chunks, and how were they sized? | `docs/PORT-PLAN.md` — **historical**: status `RUNNING` at the superseded pin `517b5e10febd`; read for the chunking approach only |
| What is measurably broken in rebrand? | `docs/REBRAND_INVENTORY.md` — every count carries its command |

## Claim rules (these are the enforcements, not aspirations)

1. **`VERIFIED` is the sole release verdict.** `M1` and `M2` are progress markers. Stated as the
   standing rule in `AGENTS.md` §"Documentation authority".
2. **Evidence beats prose.** Status comes from re-running the generators, not from reading a
   previous completion claim. Prior claims are untrusted until verified against the worktree.
3. **A typed refusal is not a working capability.** Neither is a route that renders, a catalog
   row, or a ledger entry.
4. **Never claim `VERIFIED`, `M1`, `M2`, `CODE_READY`, `COMPLETE`, `100%`, or `1:1`** without the
   behavioral evidence required by the archived release gate and `docs/FROZEN_TARGET.md`.
5. **Port literally, prune afterwards.** No symbol is skipped as "out of scope" mid-port, and no
   typed refusal is written in a symbol's place; exclusions are the owner's to declare (see the
   `docs/FROZEN_TARGET.md` exclusion table).

Machine-checkable procedures for rules 1 and 3 live in the `completion-gate-design` and
`capability-audit` skills — use them rather than re-deriving the gate by hand.

## Why there is no critical path here

A critical path is only honest if something measures it. The port gap is measured
(`scripts/frozen_target_progress.py`); the behavioral gap against the reference dashboard is not
measured by any single instrument. Writing an ordering that implies the second is tracked would be
fabrication. Sequence work from the measured list, and record the result in `docs/STATUS.md`.
