# Milestone: upstream `ee8dd6c886`

**Pinned 2026-10-07.** This is the upstream revision Qubit is aiming at. When our tree
matches this commit's production code, hermes can be retired.

```
milestone commit : ee8dd6c886326db4b468c2ba18d9409f123da827
subject          : fix(agent): a credit-limited 402 retries once with the affordable output
date             : 2026-10-07 01:12:55 +0530

from our baseline a036b13793:
  commits        : 966
  .py touched    : 666
```

## Why a pinned milestone matters

Every figure in this campaign was measured against a moving target. Upstream added
commits *while* the audits ran, so "how much is left" changed under the measurement —
one audit's frozen JSON disagreed with a re-run two minutes later by 49 files, and the
short-port baseline I published was off by 10x because I measured in a dirty tree.

Convention from here:

- **Measure everything against `ee8dd6c886`**, never `origin/main`. `origin/main` moves;
  the milestone does not.
- The audit tools take `--rev`; pass the milestone explicitly:
  `python3 scripts/audit_sync_completeness.py --rev ee8dd6c886`
- Measure in a CLEAN detached worktree, never a checkout with uncommitted work.
- When the milestone is reached and verified, pin a new one. Retiring hermes needs a
  frozen target that was actually met, not a moving one that was approximated.

## Two jobs, do not conflate them

1. **Baseline completeness** (`a036b13793`) — DONE. The tree matches the revision we
   originally aimed at, in production code.
2. **Milestone completeness** (`ee8dd6c886`) — NOT DONE. 966 commits / 666 `.py` files,
   of which the analysis estimates ~415 files carry real work (~783 new symbols),
   dominated by the `hermes_cli` self-update/custody rebuild (~47%).

Only job 2 gates deleting hermes.
