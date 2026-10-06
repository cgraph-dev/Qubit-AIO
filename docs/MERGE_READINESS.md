# Merge readiness — `b4e6bdb2` (aud8-u5) → `main`

Measured read-only against main's live checkout. Nothing in main was modified.
Main HEAD: `ded1a09a` · merge-base(main, b4e6bdb2) = `ded1a09a` (branch is a strict
descendant; a merge is a fast-forward-able, no-divergence merge).

Time of measurement: dirty count fluctuates around **98** files (task said ~94; it has grown).

---

## 1. Main's uncommitted files, by area (98 total: 60 modified, 38 untracked)

| Area | Count | Examples |
|---|---|---|
| `voicebridge/` (voice/orb live workstream) | 15 tracked M + 3 untracked | `orb/qt_app.py`, `orb/state.py`, `orb/ember.py`, `voice_edit/{service,settings,wake}.py`, `webui.py`, `chat_transport.py`; new `orb/talk_state.py`, `voice_edit/{session_fsm,voice_sessions}.py` |
| `frontend/` | 18 M + 5 untracked | `pages/ChatPage.tsx`, `pages/VoiceEditPage.tsx`, `lib/api.ts`, `om-agent-ui.tsx`; new `pages/ChatPage.*.test.tsx`, `VoiceEdit*.test.tsx` |
| `qubit_runtime/qubit_cli/` | 9 M | `main.py`, `cli_tui_mixin.py`, `cli_model_switch_mixin.py`, `plugins_cmd_install.py`, `observability/relay_shared_metrics.py` |
| `qubit_runtime/tui_gateway/` | 3 M | `server.py`, `slash_worker.py`, `agent_callbacks.py` |
| `qubit_runtime/tools/` | 7 M | `computer_use/{backend,permissions,tool}.py`, `skill_usage.py`, `vision_tools.py` |
| `qubit_runtime/` other (`agent/`, `cron/`, `gateway/`, `evals/`) | 8 M | `cron/scheduler.py`, `agent/skill_utils.py`, `agent/relay_runtime.py` |
| root `tests/` | 1 M + 16 untracked | new `test_voice_*.py`, `test_qubitchat_*.py`, `*9u.py` |
| `docs/` | 1 M + 1 untracked | `SYNC_CAPABILITY_GAPS.md`, new `QUBIT_CHAT_CAPABILITY_PLAN.md` |

The dirty tree is **not** a stale snapshot — it is the in-flight voice/orb/chat workstream
(`test_voice_*`, `ChatPage.voiceSync`, `VoiceEditWakeRouting`, `orb/talk_state.py`) plus a
second thread of core-CLI edits that overlap the branch.

---

## 2. Overlap — dirty files that b4e6bdb2 also modifies vs merge-base

b4e6bdb2 touches **267 files** (215 M, 52 A). Intersection with main's 98 dirty files = **12 files**:

```
qubit_runtime/agent/skill_utils.py
qubit_runtime/cron/scheduler.py
qubit_runtime/qubit_cli/cli_model_switch_mixin.py
qubit_runtime/qubit_cli/cli_single_query.py
qubit_runtime/qubit_cli/cli_tui_mixin.py
qubit_runtime/qubit_cli/gui_uninstall.py
qubit_runtime/qubit_cli/main.py
qubit_runtime/qubit_cli/observability/relay_shared_metrics.py
qubit_runtime/qubit_cli/plugins_cmd_install.py
qubit_runtime/tools/skill_usage.py
qubit_runtime/tui_gateway/server.py
qubit_runtime/tui_gateway/slash_worker.py
```

These 12 are the only files where main's in-flight work can collide with the branch. The other
86 dirty files (all of `voicebridge/`, all of `frontend/`, most tests/docs) are **not touched by
b4e6bdb2** and merge cleanly regardless.

---

## 3. Severity per overlapping file

Method: for each file, `git -C main diff -U0 HEAD` gives the dirty-added lines; a line is a
**lost-work risk** when it is *absent* from the `b4e6bdb2:path` blob. `git diff <main-worktree-file>
<b4e6bdb2:path>` confirms direction. Symbols checked by name in both trees.

| File | Dirty change (in-flight) | Branch also has it? | Verdict |
|---|---|---|---|
| `tools/skill_usage.py` | 9 lines | **Identical blob** (`6acf2281` both sides) | ✅ **No-op** — dirty == branch |
| `agent/skill_utils.py` | org-mirror constants + `read_active_org_id` | Yes — all 18 dirty lines present in branch; branch adds `_org_rel_parts`/`is_org_mirror_path`/`org_id_of_path` on top | ⚠️ Conflict, **branch is superset** |
| `cron/scheduler.py` | `_scope_isolated_job_ids`, `_wedged_inflight_keys`, `get_restart_wait_cron_counts` (partial) | Yes — **both sides independently implemented the same feature**; branch is *more complete* (adds `.add`/`.discard` at L823/868/3561 that dirty lacks) | 🔴 Conflict — **DOUBLE IMPLEMENTATION**; take branch |
| `qubit_cli/cli_model_switch_mixin.py` | `QubitCLI as HermesCLI` + `old_model = cli.model` | Same lines, different text: branch = `from cli import QubitCLI` + `old_model, old_provider = ...` | 🔴 Conflict — **rebrand-name collision**, branch adds `old_provider` |
| `qubit_cli/cli_single_query.py` | `QubitCLI as HermesCLI` | Branch has plain `from cli import HermesCLI` — dirty-added line **absent** | ⚠️ Auto-merges, dirty rebrand **survives**; branch does not rebrand this line |
| `qubit_cli/cli_tui_mixin.py` | `QubitCLI as HermesCLI` | Branch has `from cli import HermesCLI` (old) | ⚠️ Auto-merges, dirty rebrand survives |
| `qubit_cli/gui_uninstall.py` | `desktop_install_record()` | Yes — branch has the function (3 hits) | ⚠️ Auto-merges; near-duplicate |
| `qubit_cli/main.py` | `explicit_cli_profile()` / `_explicit_cli_profile` | Yes — 6/5 hits in branch | ⚠️ Auto-merges; near-duplicate |
| `observability/relay_shared_metrics.py` | `_with_launch_entrypoint`, dispatch sources, `_SNAPSHOT_RECHECK_NS` | Yes (+ branch adds `shutdown_runtimes`, `rotate_segment`) | ⚠️ Conflict at a *different* hunk; both sides additive |
| `plugins_cmd_install.py` | `_shared_answers` ContextVar, `shared_dependency_answers`, `recorded_install` | Dirty also edits the `--yes-deps` / `_ask_yes_no` area; branch defines `_ask_yes_no` | ⚠️ Conflict; both sides edit consent path |
| `tui_gateway/server.py` | `_read_bundled_names` rename over `_read_bundled_manifest_names` | Dirty-added lines **absent**; branch keeps `_read_bundled_manifest_names` | 🔴 **Directional divergence** — a rename on both sides; auto-merges but pick a winner |
| `tui_gateway/slash_worker.py` | `QubitCLI as HermesCLI` | Branch has plain `HermesCLI` — dirty line absent | ⚠️ Auto-merges, dirty rebrand survives |

### LOST-WORK RISK call
No dirty change *disappeared* under auto-merge (all dirty-only lines still present in the merged
result — verified by symbol grep on the merged worktree). The genuine risks are **not** silent
clobbers but **divergent double-implementation** of the same feature on both sides:

- **`cron/scheduler.py`** — highest risk. Main's dirty tree and the branch each added
  `get_restart_wait_cron_counts` + `_scope_isolated_job_ids` + `_wedged_inflight_keys`. A naive
  conflict resolution that keeps "ours" leaves main's **incomplete** copy (missing the
  `.add`/`.discard` bookkeeping the branch has at L823/868/3561) → the restart-wait count silently
  under-counts. **Take the branch side.**
- **`tools/skill_usage.py`** — the only true no-op; blob identical.
- **Rebrand trio (`cli_model_switch_mixin`, `cli_single_query`, `cli_tui_mixin`, `slash_worker`)** —
  cosmetic. `qubit_runtime/cli.py` defines `class QubitCLI` and aliases `HermesCLI = QubitCLI`
  identically in **both** trees, so `QubitCLI as HermesCLI` and `HermesCLI` resolve to the same
  class. Low functional risk; pick the branch's style for consistency.

---

## 4. Simulated merge (no commit) — result

Procedure: detached worktree at main HEAD `ded1a09a`; `git diff HEAD > main.patch` (6054 lines,
tracked changes only); `git apply --3way` → applied to **60 files cleanly**; committed the dirty
snapshot; `git merge b4e6bdb2 --no-commit --no-ff`.

- **First attempt with a dirty tree** → `error: Your local changes would be overwritten by merge
  … Merge with strategy ort failed.` (git refuses; exit 2). This is exactly what the user hits if
  they merge without committing/stashing first.
- **After committing the dirty snapshot** → `Automatic merge failed; fix conflicts and then commit`.

**5 conflicted files** (conflict-hunk counts):

```
qubit_runtime/agent/skill_utils.py                               1 hunk   both-added (branch superset)
qubit_runtime/cron/scheduler.py                                  1 hunk   DIVERGENT double-impl
qubit_runtime/qubit_cli/cli_model_switch_mixin.py                1 hunk   rebrand + old_provider
qubit_runtime/qubit_cli/observability/relay_shared_metrics.py     2 hunks  branch-only additive fns
qubit_runtime/qubit_cli/plugins_cmd_install.py                    1 hunk   consent-path, both-add
```

Both sides summarised:

- `skill_utils.py` — OURS: `_org_rel_parts` etc.; THEIRS(branch): the same org-mirror block plus
  `read_active_org_id`. Branch is the superset → **take theirs**.
- `cron/scheduler.py` — OURS: 2-line comment for `_scope_isolated_job_ids`; THEIRS: 5-line comment
  for the *same* set plus the complete bookkeeping. **Take theirs.**
- `cli_model_switch_mixin.py` — OURS: `QubitCLI as HermesCLI, _cprint` + `old_model = cli.model`;
  THEIRS: `QubitCLI, _cprint` + `old_model, old_provider = ...`. Branch carries an **extra
  behaviour** (`old_provider`) the dirty lacks → **take theirs** (or merge both: keep
  `old_provider` and the rebrand alias).
- `relay_shared_metrics.py` — OURS empty at both hunks; THEIRS adds `shutdown_runtimes` and
  `rotate_segment`. **Take theirs.**
- `plugins_cmd_install.py` — OURS empty; THEIRS defines `_ask_yes_no`. **Take theirs.**

**Auto-merged without conflict (11 of 12 overlap files minus the 5 conflicted = 7): all dirty-only
symbols survived** in the merged worktree (verified: `QubitCLI as HermesCLI` present in
`cli_single_query.py`/`cli_tui_mixin.py`/`slash_worker.py`; `desktop_install_record` ×4 in
`gui_uninstall.py`; `explicit_cli_profile` ×6 in `main.py`; `_read_bundled_names` in `server.py`).
No silent clobber found.

Cleanup: `git merge --abort`; test worktree removed. Main checkout **untouched** (`ded1a09a`).

---

## 5. Recommended sequence

The 5 conflicts are all resolvable by **taking the branch's side** (branch is a superset or carries
extra behaviour in every case). No dirty work is lost by doing so, because every dirty-only line is
also present in the branch for the conflicting files — verified. The one judgement call is
`cli_model_switch_mixin.py`, where the dirty rebrand alias and the branch's `old_provider` should
*both* be kept.

**The merge is NOT safe fully unattended** — 5 real conflicts plus a live voice/orb workstream still
landing files. Recommended:

1. **Freeze the voice/orb workstream** — ask whoever is editing `voicebridge/orb/*`,
   `voicebridge/voice_edit/*`, `frontend/qubit-web/**` to stop writing. Merge on a quiescent tree.
2. **Commit or park main's in-flight work — do NOT `git stash`** (hazard class 11: stash is
   repo-global across all worktrees; another agent can pop it). Prefer: commit the dirty work on
   main (e.g. `wip(voice-orb): snapshot before aud8 merge`) or move it to its own branch. A dirty
   tree makes git *refuse* the merge outright (proven above).
3. Verify the commit: `git -C <main> status --porcelain | wc -l` → expect **0** (untracked new test
   files may remain; those do not block a merge as long as they do not collide with branch-added
   paths — none of b4e6bdb2's 52 added files share a path with main's untracked files, checked).
4. `git -C <main> checkout main` (already on it) → **`git merge b4e6bdb2`**.
5. Resolve the 5 conflicts, **taking the branch version** for `skill_utils.py`, `cron/scheduler.py`,
   `relay_shared_metrics.py`, `plugins_cmd_install.py`; for `cli_model_switch_mixin.py` keep the
   branch body **and** add the `QubitCLI as HermesCLI` alias if the rebrand is wanted.
6. **Post-merge guards** (hazard class 1 — dropped symbol; class 2 — signature drift):
   - Danger symbol check: `get_restart_wait_cron_counts` must keep its `.add`/`.discard` bookkeeping
     (branch lines 823/868/3561) — confirm they survived.
   - Confirm `qubit_runtime/cli.py` still has `HermesCLI = QubitCLI` (both trees identical).
   - Run the import gate / `sync_import_gate.py` **in a clean worktree**, not the checkout
     (hazard class 9: never measure a baseline on a dirty tree; class 4: the gate needs the real
     interpreter/`.venv`).
7. Run `docs/generated/entry_point_baseline.json` comparison from a clean detached worktree.

**Bottom line:** cost = 5 conflict resolutions, all mechanical ("take branch"), plus a required
freeze-and-commit of main's ~98 dirty files. No lost work is expected *if* the scheduler conflict is
resolved toward the branch; resolving it toward "ours" silently drops the scheduler's scope-isolated
bookkeeping.
