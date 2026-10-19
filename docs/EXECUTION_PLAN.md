# Qubit EXECUTION_PLAN — how work is sequenced and dispatched

**Status:** process. **Contains no workstream status and no completion claims.**

The previous `docs/EXECUTION_PLAN.md` defined sequenced workstreams `W0`–`W9` and was deleted in
`eafb54e26`. **No current document defines what `W0`–`W9` meant**, and the identifiers now appear
only in `AGENTS.md`. Do not use them as task names — they resolve to nothing. Name work by the
measured unit below instead.

## The unit of work

The only unit that is measured end-to-end is a **port chunk**: an ordered set of upstream files
totalling a bounded number of lines, executed with one method and verified against one pin.

- Method: `PORT-METHOD.md` (root) — follow verbatim, do not improvise per-file decisions
- Sizing and ordering: `docs/PORT-PLAN.md` (historical; chunking approach only — its status line
  and pin are superseded)
- Current remaining set: `scripts/frozen_target_progress.py` → `docs/FROZEN_TARGET_WORKLIST.md`
- The pin and the literal-port rule: `docs/FROZEN_TARGET.md`

For anything that is not a port (rebrand fixes), the unit is a measured defect row from
`docs/REBRAND_INVENTORY.md`, which carries writer and reader `file:line` for every claim.

## Dispatch rules that have cost work before

1. **One worktree per agent.** Two agents committing in one worktree corrupt each other —
   `git add -A` in one sweeps the other's files. Stated in `AGENTS.md` §"COMMIT-AS-YOU-GO".
2. **Commit each coherent unit as it lands**; never end a turn holding uncommitted work. Use
   `wip:` when unfinished — a dirty tree is not revertable, attributable, or visible to the next
   agent.
3. **Record in-flight work in `docs/OPEN_THREADS.md`** at session end. That file exists because
   sessions repeatedly died mid-work and left no record.
4. **Implementation-first cadence:** finish implementation and inventory work before expensive
   verification; run the final matrix once after implementation freezes. Automated test sweeps are
   not proof of completion.
5. **Do not restart a running service** to pick up a change unless the task says to.

## Definition of done for a unit

A unit is done when its claim is backed by a command and its observed output, recorded in
`docs/STATUS.md` — not when the file exists, the route renders, or the ledger row appears.
`VERIFIED` remains the sole release verdict; `M1`/`M2` are progress markers only.
