# Packet 8T-F — migration worktree journal

## Worktree

- Clean worktree: `/tmp/wt8tfv2` (created via `git worktree add --detach HEAD`).
- Base: `5ad3795` (Packet 8T-F close-out repair).
- Purpose: verification builds from a clean tree without touching the dirty
  checkout (wheel build proven from `/tmp/wt8tf` earlier; this worktree
  tracks the close-out tip).
- The worktree shares the repository object store: creating it copies no
  user data and deletes nothing. Removal: `git worktree remove --force
  /tmp/wt8tfv2` (no checkout data affected).

## Backup

- No file backup needed for worktree creation (read-only view of committed
  objects). The dirty checkout was never checked out over, reset, or
  cleaned; unrelated dirty/untracked files were hash-inventoried before
  (`/tmp/8tfv3-baseline.sha`) and after every change.
- User-data stores (config/memory/reports/models/audio/browser/service
  intent) were never migrated by 8T-F code changes; migration/rollback
  behavior is rehearsed hermetically in isolated HOME directories
  (`tests/test_foundation8tf.py`, `tests/test_packet8s.py`).

## Journal

1. `5ad3795` — vendored Hermes executes in-tree + matrix (prior turn).
2. (this turn) Curated Qubit Agent rebrand inside dirty CLI/dashboard/
   terminal files (existing lines only; unrelated setup/banner hunks
   preserved), orchestrator speech, nav-brand, activity-feed text.
3. (this turn) Matrix re-verdict: no REUSE/ADAPT row left unintegrated.
4. (this turn) Rebrand regression suite `tests/test_rebrand8tf.py`.
5. Full hermetic suite re-run before commit (see final report).

## Rollback

- Scoped revert of this turn's commit(s) only: `git revert <hash>`.
  Never `git reset --hard`, `git clean`, or broad checkout.
- Worktree removal does not affect the main checkout.
- Runtime/user-data rollback paths are unchanged (product
  migrate-rollback, profile rollback, receipted setup rollback).
