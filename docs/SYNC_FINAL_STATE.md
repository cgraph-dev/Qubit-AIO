# Sync completeness — final state

## Verified

Merged tree (`sync/completeness-merged` → `a473a56c`), measured in a clean worktree:

```
[PASS] import gate            29 OK / 0 FAIL
[PASS] entry-point resolution HARD prod=0 tests=0
[PASS] lazy imports           0 unguarded; 2 guarded (silent no-op)
[PASS] capability audit       1039 missing (burndown, never blocks)
VERDICT: PASS -- all blocking checks green
```

Completeness against the baseline `a036b13793`:

| | clean baseline `b4e6bdb2` | merged `a473a56c` |
|---|---|---|
| short-port files | 272 | **265** |
| short-port symbols | 874 | **860** |
| **production files** | 21 | **14** |
| **production symbols** | **42** | **28** |
| current | 7683 | 7690 |

## The remaining 28 production symbols, by file

```
9  plugins/platforms/email/adapter.py
4  plugins/platforms/matrix/adapter.py
2  cron/jobs.py
2  hermes_cli/banner.py
2  tools/mcp_oauth.py
1  agent/skill_commands.py
1  gateway/control_socket.py
1  gateway/run_profile_reconcile.py
1  hermes_cli/_parser.py
1  hermes_cli/claw.py
1  hermes_cli/update_cmd_git.py
1  plugins/model-providers/nous/__init__.py
1  tools/mcp_oauth_provider.py
1  tools/skills_hub_official.py
```

This is finishable in a single agent run. It is not a campaign.

## How the figures moved, and why

| attempt | method | production symbols |
|---|---|---|
| 1 | audited against the main checkout (dirty tree) | 422 |
| 2 | symbol names compared within the routed file only | 584 |
| 3 | grep rebranded name in the same file | ~32 |
| 4 | AST + global index, **clean** worktree | **42** |

Then finished down to **28** by the porting agents' 8 files of real work.

Every wrong figure came from a method that was plausible and unverified against a
second arbiter in a pinned environment. The one that held up was the one where the
inputs were pinned (clean detached worktree, upstream revision named) and the arbiter
matched the question being asked ("does any file in the tree define this?"), confirmed
by a runtime import rather than name matching.
