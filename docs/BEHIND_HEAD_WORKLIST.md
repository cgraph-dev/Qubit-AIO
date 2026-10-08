# BEHIND_HEAD WORKLIST — what the 313 really is, and the honest production gap

**Measured** 2026-10-08, worktree `/tmp/qb-m4` @ `e5d75e6d` (clean tree, branch `m4),
audit `--rev ad12263a5ed4`. Raw audit JSON: `/tmp/qb-m4-audit.json`.

Reproduce:

```
.venv/bin/python scripts/audit_sync_completeness.py --rev ad12263a5ed4 \
    --json /tmp/audit.json --show-symbols --top 200
```

## TL;DR

- The hypothesis under test — *"the 313 behind_head symbols are mostly rebrand/cosmetic
  noise, and the real work is ~16 symbols"* — is **REFUTED**.
- A rebrand-normalized AST comparison (hermes→qubit via the rebrand engine's token map,
  docstrings/comments/whitespace stripped) finds **all 112 behind_head files carry a real
  structural delta**. Zero are rebrand-only, zero are cosmetic. `behind_head` is measuring
  the right thing.
- The **"~16 genuinely-missing symbols" claim is a mis-citation**, not a competing
  measurement (see below). It never described the same quantity as the 313.
- The one genuine defect in the instrument is the **production/test split**: it charges all
  313 behind_head symbols to *production*, but 250 of them live in *test* files. The honest
  production gap is **72**, not 322.

## 1. What the `behind_head` branch actually counts

`scripts/audit_sync_completeness.py`, `main()`, per upstream `.py` file at the reference rev:

1. **L349–354** — route the upstream path to ours; skip (→ `absent`) if our file is missing.
2. **L359–360** — `our_syms = top_defs(our_src)`, `head_syms = top_defs(blob(rev, up_path))`.
   `top_defs` (L220–238) is an **AST walk of TOP-LEVEL `def`/`class`/assigned names only.**
   Nested methods and class-body attributes are invisible to it.
3. **L365–367** — `missing_head` = head symbols not in our file **and** not creditably
   resolved by the rename subtraction (`resolve`, L294–312: same-file def → rebranded twin
   in-file → twin/own name as a top-level def **anywhere** in `qubit_runtime/`).
4. **L368–370** — if `missing_head` is empty, the file is `current` (7960 files).
5. **L372–382** — otherwise compute `missing_base` against our own baseline `a036b13793`:
   - `missing_base` non-empty → **`short_port`** (we never reached our own baseline).
   - `missing_base` empty → **`behind_head`** (we match our baseline; upstream moved on
     between `a036b13793` and `ad12263a5ed4` and we did not follow).

So `behind_head` = *"a file where we are current with our own sync baseline, but upstream
added top-level symbols in it that we never ported."* It counts **symbols**, not bytes —
rebrand churn is already subtracted by `resolve`/`rebrand` (using the real rebrand engine's
token maps, loaded live from `scripts/qubit_rebrand.py`, L120–147).

**Its blind spot** (matters for the worklist): it only sees the symbols the audit *already
names*. Real content added to a file *without* new top-level names (a new class method, a new
nested helper, a rewritten function body) is **not** reflected in the symbol count. So the
symbol count is a **lower bound** on the true per-file delta.

## 2. Per-file classification — top 15 behind_head files

Method: `git show ad12263a5ed4:<up_path>` vs our routed file; upstream rebrand-normalized
per-identifier with the engine's token map; docstrings/comments/whitespace stripped; bodies
compared both as canonical text and as a docstring-stripped `ast.dump`.

| # | audit miss | call | file (upstream path) | why |
|---|---|---|---|---|
| 1 | 36 | **TRULY BEHIND** | `tests/hermes_cli/test_update_interrupted_pull.py` | our copy lacks ~30 whole test functions + helpers; upstream 8210 tok vs ours 2553 |
| 2 | 15 | **TRULY BEHIND** | `hermes_platform/resolver/app.py` | upstream added `AppLocation`,`LocationSpec`,`LOCATION_KINDS`,`_Locator`,`_Hit` + hit/uninstall helpers; ours is 323 lines vs upstream 439 |
| 3 | 13 | **TRULY BEHIND** | `tests/tools/test_known_file_write_baseline.py` | ~13 test funcs absent; ours 578 tok vs upstream 2310 |
| 4 | 11 | **TRULY BEHIND** | `tests/hermes_cli/test_pty_session.py` | `PidBridge` + 10 close-orphaned/lease tests absent |
| 5 | 9 | **TRULY BEHIND** | `tests/hermes_cli/test_early_recovery.py` | 9 tests + `_symlink_or_skip` absent |
| 6 | 8 | **TRULY BEHIND** | `hermes_cli/observability/shared_metrics_contract.py` | upstream added `INSTALL_RUN_*` + `MEMORY_OP_FAILURE_CLASSES`; install-run metrics were split into `shared_metrics_install_run.py` (which we are missing outright) |
| 7 | 8 | **TRULY BEHIND** | `tests/hermes_cli/test_gitlock.py` | 8 tests/helpers absent |
| 8 | 7 | **TRULY BEHIND** | `tests/ci/test_classify_changes.py` | `_UPDATE_PATH_FILES`/`_DESKTOP_UPDATE_PATH_FILES` + 4 tests absent |
| 9 | 7 | **TRULY BEHIND** | `tests/hermes_cli/test_model_picker_excluded_providers.py` | 7 tests absent |
| 10 | 7 | **TRULY BEHIND** | `tests/hermes_cli/test_web_server_files.py` | 7 tests absent |
| 11 | 6 | **TRULY BEHIND** | `hermes_constants_scratch.py` | `_PruneLogHandler`,`_open_prune_log`,`_prune_idle_entries`,`_tree_bytes`,`_PRUNE_LOG_*`; ours 198 lines vs upstream 296 |
| 12 | 6 | **TRULY BEHIND** | `tests/hermes_cli/test_pty_keepalive_ws.py` | 6 tests/helper absent |
| 13 | 6 | **TRULY BEHIND** | `tests/hermes_cli/test_update_parked_branch_guard.py` | 5 tests + `_treeless_repo_pair` absent (1 name `_pack_count` is present elsewhere — still a real file delta) |
| 14 | 6 | **TRULY BEHIND** | `tests/hermes_cli/test_update_zip_completion.py` | 6 tests/helper absent |
| 15 | 6 | **TRULY BEHIND** | `tests/tools/test_connectors_catalog.py` | 6 tests/helper absent |

Every "missing" symbol above was independently confirmed **absent tree-wide** by a raw-text
grep of `qubit_runtime/**/*.py` and by an any-nesting AST index — they are not rebrand
ghosts hidden under a different spelling. Classification result over the sample:
**15/15 TRULY BEHIND.**

## 3. Whole-set normalization — honest burndown

Same method run over **all 112** behind_head files:

| classification | files |
|---|---|
| REBRAND-ONLY (identical after normalization) | **0** |
| COSMETIC (deltas are comments/docstrings only) | **0** |
| **TRULY BEHIND (real AST delta)** | **112** |

**112 / 112 genuinely behind.** The 313 symbol count is real work, not noise.

> Note on a false-negative near-miss: an early pass of my own classifier marked
> `tests/e2e/core/windows_update/_machine.py` "cosmetic" because it only re-checked the
> symbols the audit named (`descendants`). The normalized body diff showed upstream also
> added a `mint()` helper (~700 tokens) and `taskkill_tree` — **class methods the
> top-level-only `top_defs` walker cannot see.** Corrected to TRULY BEHIND. This is exactly
> why the worklist below carries a *token-delta* column alongside the symbol count: the
> symbol count under-reports files whose growth is in methods/bodies.

## 4. Worklist — the GENUINELY-BEHIND production files

25 production files, **63 missing top-level symbols**, real body delta measured in tokens
(normalized). Sorted by symbol count.

| missing | token Δ | file (upstream path) |
|---|---|---|
| 15 | 1206 | `hermes_platform/resolver/app.py` |
| 8 | 5524 | `hermes_cli/observability/shared_metrics_contract.py` |
| 6 | 569 | `hermes_constants_scratch.py` |
| 5 | 277 | `agent/bedrock_adapter.py` |
| 4 | 212 | `tools/approval_detection.py` |
| 3 | 87 | `hermes_platform/resolver/known_dirs.py` |
| 2 | 784 | `hermes_platform/declaration.py` |
| 2 | 537 | `scripts/smoke_nemo_relay_shared_metrics.py` |
| 2 | 209 | `hermes_bootstrap.py` |
| 1 | 1437 | `hermes_cli/update_cmd_fleet.py` |
| 1 | 576 | `tools/connectors/catalog.py` |
| 1 | 544 | `hermes_state_sessions.py` |
| 1 | 377 | `agent/conversation_compression.py` |
| 1 | 361 | `hermes_platform/resolver/availability.py` |
| 1 | 277 | `agent/prompt_caching.py` |
| 1 | 226 | `tools/skill_manager_tool.py` |
| 1 | 170 | `agent/skill_preprocessing.py` |
| 1 | 126 | `scripts/ci/required_results.py` |
| 1 | 123 | `agent/turn_failure_copy.py` |
| 1 | 112 | `agent/ssl_verify.py` |
| 1 | 99 | `tools/memory_tool_store.py` |
| 1 | 80 | `gateway/platforms/webhook.py` |
| 1 | 62 | `hermes_cli/tools_config_post_setup.py` |
| 1 | 33 | `hermes_cli/update_cmd_zip.py` |
| 1 | 32 | `plugins/web/keyless_mcp.py` |

The remaining behind_head work is **87 test files / 250 symbols** — real, but non-blocking
for retiring hermes (`is_test_path`: a path starting `tests/` or containing `/tests/`).

### Also production, from the other two branches

- **Absent (7 production files)** — port outright:
  `hermes_cli/cli_commands_session_tools.py`, `hermes_cli/main_desktop_tcc.py`,
  `hermes_cli/observability/shared_metrics_install_run.py`, `hermes_cli/update_cmd_fleet_verify.py`,
  `plugins/hermes-achievements/dashboard/plugin_api.py`,
  `skills/productivity/google-workspace/scripts/_hermes_home.py`,
  `skills/research/grounded-citations/scripts/_hermes_home.py`.
- **Short-port (1 production file, 2 symbols)** — we trail our *own* baseline:
  `hermes_cli/web_routers/analytics.py`: `_fold_session_only_rows`, `_has_usage`.

## 5. Instrument baseline — needs correcting

`scripts/frozen_target_progress.py` L175–177 computes:

```python
prod_missing = (s["absent_prod"] + s["sp_symbols_prod"]
                + tot_bh_syms)   # behind-head is production-only by nature
```

The comment is **false**: behind_head is *not* production-only. `split()` (L118–142) buckets
`absent_paths` and `worst_short_ports` by `is_test_path`, but **never splits behind_head** —
so all 313 symbols (250 of them in test files) are charged to production. Against
`ad12263a5ed4` that yields `VERDICT: NOT AT TARGET -- 322 production missing`.

**Corrected production gap:**

| bucket | corrected | instrument |
|---|---|---|
| absent files | 7 | 7 |
| short_port symbols | 2 | 2 |
| behind_head symbols | **63** (production only) | 313 (all) |
| **production missing** | **72** | 322 |
| test missing (non-blocking) | 172 absent + 195 sp + 250 bh | 367 |

So the instrument **overstates production by 250 symbols**. The corrected finish line is
**72 production symbols** — still work, but 4.5× smaller than the headline.

**Fix required** (not applied here — this task is measurement only):

1. The audit JSON must emit behind_head **with per-file paths and a production/test flag**
   (it already emits `detail` keys under `--show-symbols`; `frozen_target_progress.py` calls
   the audit **without** `--show-symbols`, so it never sees them). Add `--show-symbols` to
   the audit invocation in `run_audit()` (L91–96) and split `symbols` by `is_test_path` in
   `split()`, exactly as `absent_paths` already is.
2. `BASE` (L70–77) constants are correct as raw-audit baselines (they match the freeze
   measurement), but the **verdict math** they feed is wrong. Fix the split, then the frozen
   `behind_head_files=214 / behind_head_symbols=703` baselines still reproduce (they are
   whole-set numbers), while the *production* readout becomes honest.

## 6. Provenance

- Worktree `/tmp/qb-m4` @ `e5d75e6d`, `git status --porcelain` empty at start.
- Audit `--rev ad12263a5ed4`: EXISTS 8147, ABSENT 179, CURRENT 7960,
  SHORT-PORT 75 files / 197 sym, BEHIND-HEAD 112 files / 313 sym.
- `scripts/gate_all.py` → **PASS (4/4)**: import gate 29/0, entry-point HARD prod=0 tests=0,
  lazy imports 0 unguarded (1 guarded), capability audit 2042 missing (informational).
- Classification is reproduced by `git show ad12263a5ed4:<path>` + the rebrand engine's token
  maps + a docstring-stripped `ast.dump` compare; no hand-tuned thresholds.
