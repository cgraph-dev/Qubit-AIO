# COMMIT-AS-YOU-GO hygiene report

Machine-checkable guard `scripts/check_commit_hygiene.py` run against the live
repo state on branch `agent/catchup-5`. Exact command:

```
.venv/bin/python scripts/check_commit_hygiene.py \
    --repo /home/trick/Projects/Repos/qubit-voicebridge \
    --stale-minutes 30 --probe-seconds 45
```

- repo: `/home/trick/Projects/Repos/qubit-voicebridge`
- branch: `pre-sync-worktree-snapshot` @ `6ee2adf0c28a`
- trunk: `canonical/main` (`da19a480a`) — remote-tracking, freshest of
  `canonical/main` and `origin/main`. Note the local branch `main` is **stale**
  (last commit 2026-09-16, 1966 commits behind); the guard deliberately does not
  use it, because doing so reports a false "1966 stranded commits".
- writer probe: 45 s byte-stability window over the main worktree's own dirty
  paths; the main worktree was clean, so 0 paths needed hashing.
- **verdict: 22 violations, 4 hands-off, exit 1**

Two categories, deliberately kept apart:

- **VIOLATION** — the rule is being breached; someone must act.
- **HANDS-OFF** — dirty, but a live writer is mid-write. *Not* a violation,
  but nobody may commit, rebase, or prune over it.

---

## Violations

### V1 — Stranded commits: 70 unreachable on the main worktree's branch

```
VIOLATION [stranded_branch_commits] /home/trick/Projects/Repos/qubit-voicebridge@6ee2adf0c28a
  branch 'pre-sync-worktree-snapshot' is 70 commit(s) ahead of canonical/main;
  unreachable from the main line
```

The main checkout's branch is 70 commits ahead of `canonical/main` and those
commits are reachable from nothing else. If this worktree is pruned or the
branch deleted, 70 commits go with it. (History-level work; not something this
agent may merge on its own.)

*Remedy:* push `pre-sync-worktree-snapshot`, or merge it to the trunk.

### V2 — `qubit-b-fix`: staged work, writer abandoned

```
VIOLATION [foreign_worktree_dirty]      /home/trick/Projects/Repos/qubit-b-fix
VIOLATION [foreign_worktree_dirty_path] /home/trick/Projects/Repos/qubit-b-fix/tests/test_history_import_sanitized.py:1
```

`tests/test_history_import_sanitized.py` is **staged** (`M ` — index modified)
on `fix/audit-remaining`. Its mtime is **2026-09-29** (17 days old), no process
is working in that worktree, and the main repo's date is 2026-10-16. Staged work
that was never committed is exactly the loss mode the rule exists to prevent.

*Remedy:* commit it (`wip(test): ...`), attributed to whoever wrote it. **This
agent did not touch it** — it is outside this task's worktree and outside its
authority.

### V3 — `catchup-ag4`: dirty, no live writer

```
VIOLATION [foreign_worktree_dirty]      /tmp/catchup-ag4  (branch agent/catchup-4)
VIOLATION [foreign_worktree_dirty_path] /tmp/catchup-ag4/_measure/:1  ('??')
```

One untracked directory `_measure/`, no process inside the worktree. Peer agent
either finished and left it or stopped holding it.

*Remedy:* the peer commits it, or a human freezes it by re-hashing over ≥40 s and
commits it attributed.

### V4 — `catchup-ag1`, `catchup-ag2`, `sync-w24-ag2`: dirty, but see hands-off

```
VIOLATION [foreign_worktree_dirty] /tmp/catchup-ag1     (5 paths, agent/catchup-1)
VIOLATION [foreign_worktree_dirty] /tmp/catchup-ag2     (3 paths, agent/catchup-2)
VIOLATION [foreign_worktree_dirty] /tmp/sync-w24-ag2    (2 paths, agent/sync-w24-2)
```

These are dirty *and* each carries a hands-off finding below, because their
writers are live right now. Read the two together: real uncommitted work that is
currently being written, so nothing here is actionable by anyone else yet.

*(A `/tmp/catchup-ag5` self-finding also appears when this report is generated
from inside that worktree, because the guard is being edited there. It is not a
peer violation and disappears once this worktree is committed.)*

---

## Hands off — live writer, NOT a violation

```
HANDS-OFF [foreign_worktree_live_agent] /tmp/catchup-ag1
HANDS-OFF [foreign_worktree_live_agent] /tmp/catchup-ag2
HANDS-OFF [foreign_worktree_live_agent] /tmp/sync-w24-ag2
```

Detection is read-only: `a process's working directory is inside this worktree`
via `/proc/<pid>/cwd`. The guard deliberately does **not** hash another agent's
files — hashing a file mid-write manufactures a false "writer" reading, and
`/proc/<pid>/cwd` cannot perturb a writer.

Independent corroboration (this agent re-hashed the paths with a separate 45 s
interval, not through the guard):

- `catchup-ag1` — a live `run_lane.sh` pytest lane is running; `lane-baseline.txt`
  **changed bytes across the 45 s window**. Genuine active writer.
- `catchup-ag2` — 3 modified source/test paths; live agent process present.
- `sync-w24-ag2` — 2 untracked paths; live agent process present.

**Do not commit, rebase, or prune over these.** They become violations only if
the writer stops (proved by re-hashing over ≥40 s) and the tree is still dirty.

---

## Not found (checks that ran clean)

Stated so an absent category is a positive result, not a silent skip:

- **No uncommitted work in the main worktree itself** — `git status` empty there.
- **No staged secrets** — no `.env`, credential file, or private-key blob staged
  anywhere the guard could read.
- **No un-probed dirty tree** — every own-tree dirty path was hashable.
- **No detached HEAD holding work.**

## Method notes — the false-positive traps this guard does not fall into

- **No argv substrings.** Liveness never keys on `'serve'` matching
  `'observe'`/`'preserve'`. Where process identity is used it goes through the
  canonical `qubit_runtime.gateway.status.looks_like_gateway_command_line`
  matcher, and only as corroboration, never as the primary signal.
- **No bare PID existence.** PIDs are recycled; existence proves nothing.
- **No mtime-only inference.** A path is "live" only if its bytes changed across
  a real ≥40 s window *and* its mtime falls inside that window, so a commit
  landing mid-probe is not mistaken for a writer.
- **No stale trunk.** The trunk is the freshest remote-tracking `main`/`master`,
  not a stale local branch.
- **Foreign worktrees are never hashed.** Their dirtiness carries the un-hashed
  caveat; live-writer status comes from `/proc/<pid>/cwd` only.

## Reproduce

```
.venv/bin/python scripts/check_commit_hygiene.py --repo . --json      # machine readable
.venv/bin/python -m pytest tests/test_check_commit_hygiene.py -o addopts='' -q
```

Exit codes: `0` clean, `1` at least one violation, `2` the check could not run
(e.g. the path is not a git worktree — the guard refuses to print "clean" when it
could not ask git).
