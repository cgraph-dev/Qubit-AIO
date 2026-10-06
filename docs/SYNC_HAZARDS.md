# Sync hazard register — silent-failure classes found while syncing upstream

Every entry here is a bug that looked fine and was quietly wrong. They are listed
because each one cost real debugging time and each will recur on the next slice.

Baseline for this campaign: pin `69948c0057` → `a036b13793` (5,441 commits / 5,142 files).

## 1. A dropped symbol leaves the call site behind

Upstream renames or deletes a symbol; the merge carries the *caller* to the new
tree while the definition stays behind (or vice versa). Import-time checks miss it
when the import is function-local.

Real instances:
- `tui_gateway/server.py` did not import `marker_writer_state` that
  `session_auto_continue.py` calls bare → `NameError` on every resume of a
  crash-interrupted session.
- `qubit_cli/plugins.py` lacked `publish_gateway_message_host` /
  `clear_published_gateway_message_host` / `_attach_published_gateway_host` that
  `gateway/run_inbound.py` imports → `ImportError` killed the gateway daemon;
  systemd hit its restart limit.
- `qubit_cli/kanban_db.py` lacked `pin_first_board_resolution` that the gateway
  kanban watchers call → failure every 5s, forever.

**Fix pattern:** define or alias at the *definition* site, never at the call site,
so exactly one object exists under both names. A second object (e.g. a duplicated
frozenset blocklist) drifts silently.

## 2. A signature gains a parameter; the definition does not

Upstream added `profile=` to `_session_home_scope` for a session-less Desktop
draft. The merged caller passed it; the definition (in a file outside that slice)
still had two args → `_session_home_scope() got an unexpected keyword argument
'profile'` on **every** `/slash` command.

**Guard:** after a merge, diff call-site kwargs against definition signatures for
every cross-file helper touched.

## 3. Upstream DELETED a layer our tree still needs

`qubit_cli/plugin_compat.py` was replaced by upstream's 3-stub version
(**16,586 → 1,945 bytes**). Upstream removed the plugin-compat layer on
2026-09-14; our tree deliberately keeps it and **15 files** import `warn_once`
from it. The merge treated a deliberate deletion as an edit.

**Rule (already in the plan, now with a corpse): never auto-apply upstream
deletions.** Check each against `docs/PARITY.md`.

Counter-example, so the rule is not over-applied: `qubit_cli/tips.py` genuinely
shrank 40,622 → 2,857 B because upstream moved the tip text into the i18n catalog
(`tips.tNNN` keys). That is a real refactor and must be KEPT. Size delta alone
does not tell you which case you are in — read the file.

## 4. A measurement harness that silently measures the wrong thing

`sync_import_gate.py` fell back to `sys.executable` when it found no `.venv`. A git
worktree has no `.venv`, so grading a worktree ran under the **system python**,
which cannot import the app: **8 OK / 21 FAIL** (all phantom `No module named
'ruamel'`) for a tree that measures **29 OK / 0 FAIL** under the real interpreter.
Five agents nearly reported ~21 phantom failures each.

**Fixed:** the gate now searches the repo *and its parents*, then raises
`SystemExit` rather than grading with an interpreter that cannot import the code.
A wrong number is worse than no number.

## 5. A label used as a filesystem path

`__root__` was passed to git as a pathspec (`-- __root__`), which matches nothing.
It reported **0 files**, and a run would have merged nothing while reporting
success — silently skipping the 49-file primitive layer whose absence caused
defect class #1 three times.

## 6. A stale baseline

`BASE_SHA` was `bd0affe5`, **1,313 commits** short of current upstream. Every
measurement understated the gap by ~5x (346 missing symbols reported vs 1,833 real).

## 7. The slice list omitted whole subsystems

`SUBSYSTEMS` in `sync_driver.py` had no `hermes_cli` (377 files — the largest) and
no `hermes_platform`, and no `tests` (1,319). Those files could never be scheduled,
so every symbol they define read as permanently missing.

## 8. A routing rule that does not fire under a prefix

For `tests/hermes_cli/...`, `route()` leaves the path unchanged because the
`hermes_cli` prefix rule never matches once upstream prefixes it with `tests/`.
362 planned files would land in `qubit_runtime/tests/hermes_cli/` — a
`hermes_cli`-**named** tree — instead of `qubit_runtime/tests/qubit_cli/`. A stray
303-file tree from a previous run already exists, so this re-infects pytest
collection. Caught by a pre-apply routing check before anything was written.

## 9. A baseline measured on a DIRTY tree

The worst one, because it corrupts every downstream judgement. The sweep printed a
bare `prod_hard` number and it was read as a committed baseline. Five agents were
told the floor was **0**; the same commit measured **19** in a clean worktree, and
**12** after one more fix landed. The `0` was real for the main checkout only
because its working tree carried ~79 uncommitted files that *delete the features*
whose symbols were missing — `tools/computer_use/permissions.py` had the entire
multi-backend `get_active_provider()` path stripped out, which is silencing a
metric by removing the code, not fixing it.

Cost: five agents spent iterations disputing the number instead of merging, and two
of them wrote "your stated baseline is not reproducible" into their reports.

**Fixed twice over:** the sweep now prints `HEAD`, the uncommitted-file count, and
an explicit `RESULTS REFLECT UNCOMMITTED WORK` warning; and the reproducible
baseline is committed as `docs/generated/entry_point_baseline.json`, generated from
a clean detached worktree. Measure baselines in a worktree, never in a checkout you
have been editing.

## 10. A tool that resolves paths relative to the wrong checkout

`sync_driver.py` hardcoded `REPO = Path("/home/trick/Projects/Repos/qubit-voicebridge")`
while its sibling `sync_upstream.py` derived `REPO` from `__file__`. In a worktree
the driver therefore read **main's** dirt (and refused), and would have applied the
merge **into main**, destroying worktree isolation — the exact failure the
per-worktree design exists to prevent. Three agents hit it and bypassed the driver
by invoking `sync_upstream.py --apply --subtree` directly; one fixed it in its own
worktree.

**Rule:** any script that writes into a repo derives its root from `__file__`. A
hardcoded absolute path is a latent cross-checkout write.

## 11. `git stash` is repo-global, not worktree-local

Worktrees share one stash stack. Two agents ran `git stash push -u` / `pop`
concurrently; one popped a *foreign* stash and briefly had another agent's
`tui_gateway` slice in its tree; another silently consumed the wrong entry. Both
recovered, but one had its applied slice briefly vanish.

**Rule:** never `git stash` in a shared-worktree campaign. To park work, use
`git worktree` isolation itself, or commit. Two agents independently wrote "do not
use git stash in this worktree" into their handoffs.

## 12. A guard that fires on the wrong token

`PROTECT_RULES[1]` (the bare-host mask) matches any `X.sh` token because `sh` is in
its TLD list, so `from hermes_cli.observability.shared_metrics_gateway import ...`
was masked and **never rebranded**, leaking `hermes_cli` into merged files. The
agent that found it patched its own slice by post-processing and deliberately left
the shared tool alone to avoid colliding with sibling worktrees. **Still unfixed.**

## 13. A guard that silently does not cover the new feature

Upstream's new plugin-isolation feature makes already-merged plugins code import
`qubit_cli.plugin_isolation` (`ISOLATION_HOST`, `isolation_mode`,
`in_process_import_refusal`, `user_plugin_host`). Those definitions live in a slice
that had not landed, so merging `plugins` *added* 8 findings while resolving 5. The
agent correctly **did not stub them** — a stub would be a second, silently
drifting object (hazard #1). It verified they are function-local imports and
therefore import-safe, and reported them as deferred, not fixed. That is the right
call: a cross-slice dependency is not a defect in the slice that exposes it.

## The throughline

Six of the eight are one shape: **a fallback or a default that produces a
plausible wrong answer instead of failing.** Prefer a loud refusal. When a tool
can measure the wrong thing, make it stop rather than guess.

## 14. An "import every module" walk executes side-effecting modules

A runtime-evidence pass swept the whole tree with ``importlib.import_module`` and
assumed import was safe. It is not. Importing ``qubit_cli/update_cmd_fleet.py``
*ran the updater*: the probe's stderr shows "Handing off to the new updater
(package manager)... Installing Python dependencies". The scan's own output is
therefore contaminated (it contains sandbox/tool-call JSON, not just import
results) and cannot be read as a clean pass/fail.

Contained in this instance only because the probe set an isolated ``QUBIT_HOME``;
the live installs dir was untouched and both services stayed active. Had it run
against the real home, importing one module would have started a dependency
install mid-verification.

**Rule:** never import-sweep a tree without (a) an isolated HOME/temp env AND
(b) a skip list for modules whose top level has side effects. Identify them by
``if __name__ == "__main__"`` plus module-level calls, or run each import in a
subprocess with a hard timeout and a scratch HOME. An import walk is code
execution and must be treated as such.
