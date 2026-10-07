# FINAL reconciliation of the completeness figures

**I have been quoting a stale baseline.** This document corrects it. Read this one.

## The corrected baseline

Measured in a CLEAN detached worktree at `b4e6bdb2` with the identical tool:

```
short-port : 272 files / 874 symbols
production :  21 files /  42 symbols
current    : 7683
```

## What I had been reporting, and why it was wrong

| | I reported | **Truth** | inflation |
|---|---|---|---|
| short-port files | 376 | **272** | 1.4x |
| production files | 125 | **21** | 6x |
| production symbols | 422 | **42** | **10x** |

Cause: the "376 / 125 / 422" numbers were measured against the **main checkout**,
which carries ~67 uncommitted files from a live voice/orb workstream plus untracked
tooling. A dirty tree inflates the short-port list (hazard class 9 -- the register
warns about exactly this, and I hit it anyway while auditing an audit of it).

This is the FOURTH figure produced for the same question:

| # | method | production symbols |
|---|---|---|
| 1 | audited against the main checkout (dirty) | 422 |
| 2 | symbol names compared within the routed file only | 584 |
| 3 | grep the rebranded name in the same file | ~32 |
| 4 | **AST + global name index, clean worktree** | **42** |

## The number to use

**~42 missing production symbols across 21 files.**

That is small enough to finish. It is NOT 422 -- my earlier figure was a dirty-tree
artifact, and I repeated it several times before a clean re-measure caught it.

## What the agents actually achieved

Their assigned scopes were drawn from the inflated list (24 / 113 / 81 files), so
they spent most of a 727s budget on files that were already complete. They landed 8
files of real work between them:

- agent1 (4 commits): `tui_gateway/server.py`, `tui_gateway/user_messages.py`,
  `plugins/memory/config_schema.py`, `plugins/platforms/telegram/{adapter,update_admission}.py`
- agent2 (1 commit): `qubit_cli/portal_cli.py`
- agent3 (2 commits): `cron/scheduler.py`, `gateway/run_adapters.py`

Agent1's tree passes all four gates. The work is real and worth keeping -- but it is
a fraction of what the inflated list implied, and the wasted effort is my fault for
handing out the inflated list.

## The lesson, stated plainly

An audit is not exempt from the hazards it audits for. I built a tool to catch
"presence is not currency", ran it against a dirty tree, and published the result --
reproducing hazard #9 in the act of measuring for it. Every figure in this campaign
that was wrong was produced by a method that was *plausible* and *unverified against
a second arbiter*. The fix that worked, every time, was re-measuring in a clean
checkout with the identity of the inputs pinned.
