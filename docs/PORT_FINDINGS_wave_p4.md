# Wave p4 — remaining update-path test port (findings)

Branch `p4`, base `a9fb9ce3`, target `ad12263a5ed4`. 9 commits, 50 test files + 1 fixture
(anon_portal upgrade). Gate `scripts/gate_all.py` = PASS 4/4. Tree clean.

## Files ported (before -> after test counts)

| area | files | result |
|---|---|---|
| update lock Windows live cells (C1.7/R5b/R2/D2/F03/N11/C3) | 1 | 14 tests, all skip on linux (`platforms(windows)`) |
| post-commit safety (R8) | 1 | 9P / 2F (product gaps #1, #2) |
| MoA preset deletion (#58819/#89184) | 1 | 2P / 13F (product gap #3) |
| anon challenge + anon_portal fixture upgrade | 2 | 40P |
| desktop_update (11 absent) | 11 | 32P / 86S / 61F (product gaps #4–#8) |
| post-commit follow-ups (C3) | 1 | 14P / 1F (product gap #9) |
| receipt run-record / owe-followup / profile-store, completion-lease, recovery-custody | 5 | 11P |
| update-pause family + recovery-closure + fork-sync + venv-sync + web-status + relaunch-poll + swap-crash + moa-preset-config-warnings | 17 | 71P / 3F (product gaps #10–#12) |
| git-pack-tidy + home/install/migration (12) | 12 | ~64P / 1 xfail / 6F (product gaps #13–#15) |

## xfail (environment, cited)

- `test_git_pack_tidy.py::test_a_refused_or_killed_erase_leaves_git_reading_and_is_finished_later[midx_layout1]`
  — param `["--incremental"]` needs git >= 2.44 for `multi-pack-index write --incremental`;
  this host runs git 2.43.0 (`error: unknown option 'incremental'`, exit 129). Marked on the param.

## Product gaps found (recorded; product untouched — a sibling owns product)

1. `qubit_cli/update_cmd_maint.py::_run_post_update_maintenance` lacks the frozen trailing
   `if not update_complete and (completion_message or "✓").startswith("✓"): record_followup("sqlite_runtime", "the selected Python links an unsafe SQLite runtime", retry="run the installer again")`
   (frozen `/tmp/up_maint.py:1081`). Test `test_update_post_commit_safety.py::test_unsafe_sqlite_runtime_keeps_exit_zero_but_vetoes_topology_migration`: expected `followups == ["sqlite_runtime"]`, actual `[]`.
2. `qubit_cli/update_cmd_config.py::_check_and_apply_config_migration` swallows the migration
   error (`except Exception: print("⚠️ Config format update failed")`, no re-raise). Frozen re-raises
   (`raise owed_error`, frozen update_cmd_config.py:251), so `owed_step("config_migration", ...)`
   records the debt. Test `test_config_format_write_failure_is_owed_not_reported_complete`: expected
   `followups == ["config_migration"]`, `pending True`, `stamp False`; actual `[]`, `False`, `True`.
3. `qubit_cli/web_routers/models.py::set_moa_models` still uses the pre-fix body
   (`moa_section = dict(cfg["moa"]); moa_section.update(normalized); save_config({"moa": moa_section}, merge_existing=True)`),
   so the deep-merge restores an omitted preset. Frozen (models.py:303+) uses
   `require_readable_config_before_write()` + `config_mod._merge_partial_save(raw_cfg, {"moa": normalized})`
   + an authoritative post-merge filter `updated["moa"]["presets"] = {name: merged_presets[name] for name in normalized["presets"]}`.
   13 failures in `test_moa_preset_deletion.py`. Helpers the frozen fix needs already exist.
4. `scripts/desktop-update/{marker.sh,marker.ps1,marker-claim.ps1,swap-selftest.sh,update-job.ps1}`
   ABSENT; `posix.sh` 898 vs frozen 1313 lines.
5. `scripts/desktop-update/posix.sh` rejects `--self-test-swap-interrupt`, `--marker-op`,
   `--handoff-run` (`unknown arg`, rc=64); lacks `owed_followup_steps()`, and the marker-lock
   helpers `marker_claim`/`marker_locked`/`marker_release_locked` (rc=127, sourced from marker.sh).
6. `scripts/desktop-update/runtime.ps1` lacks `Invoke-HermesProbe` (the #124526 UTF-8 stdout decode).
7. `scripts/desktop-update/posix.sh` swaps `Hermes.app` while `apps/desktop/package.json`
   `productName` is `Qubit` (rebrand miss).
8. checkout-lock verdict reads `free` where the frozen reports `held` (desktop_update).
9. `qubit_cli/update_cmd_git.py::_discard_lockfile_churn` lacks the `checkout_move` kwarg the frozen
   signature has (frozen update_cmd_git.py:641) -> `test_update_pause_step_interruptions.py`.
10. `qubit_cli/config.py::get_missing_config_fields` reports `moa.presets.<name>` for user-named
    presets -> `test_moa_preset_config_warnings.py:19` (same MoA map family as #3).
11. `qubit_cli/venv_sync.py` launch path raises `'source update has no managed Python; run `qubit pm install`'`
    where the frozen path refuses `'an update is still running'` -> `test_venv_sync_update_tree.py`.
12. `test_update_pause_step_interruptions.py::test_an_interrupted_stash_push_stays_held` — product does
    not raise through the injected git wrapper (uncertain root; recorded).
13. `qubit_cli/observability/schemas/hermes.shared_metrics.v4.schema.json` ABSENT (product has v1..v3).
14. `scripts/install.sh` Ctrl-C ladder leaves `stage complete ok` where the frozen script aborts
    (3 tests in `test_shared_metrics_install_run.py`).
15. `pm/environments.py::owning_home_root` returns `None` where the frozen resolves the platform-default
    owner -> `test_borrowed_home_launch.py` (2 tests).

## Rename notes (contracts preserved)

- `hermes_cli`->`qubit_cli`; `hermes_constants`->`qubit_constants`; `hermes_state`->`qubit_state`;
  `hermes_bootstrap`->`qubit_bootstrap`; `get_hermes_home`->`get_qubit_home`;
  `_spawn_hermes_action`->`_spawn_qubit_action`; `hermes_home_override`->`qubit_home_override`;
  `hermes_version`->`qubit_version`; `_isolate_hermes_home`->`_isolate_qubit_home`.
- KEPT byte-for-byte: `.hermes-update-in-progress`, `.hermes-update-result.json`,
  `.hermes-update.lock`, `.git/hermes-update-pull`, `.git/hermes-update-recovery`,
  `hermes-update.log`, the `hermes-update` action + `/api/actions/hermes-update/status`,
  `HERMES_ACTION_ID`, `HERMES_GATEWAY_LOCK_DIR`, `HERMES_DISABLE_LAZY_INSTALLS`,
  `HERMES_RUNTIME_DIR`, the `hermes` launcher token / `hermes-agent` install dir / `hermes.exe`,
  `hermes.shared_metrics.v4.schema.json`, and the `hermes-agent` wire client name.

## Remaining absent tests (not in this scope)

`scripts/audit_sync_completeness.py --rev ad12263a5ed4` still lists 151 absent `tests/` paths
(the `tests/hermes_cli/` remainder = web_server*, plugin*, local_runtime*, model_options*,
sessions_export_retention, nous_picker*, etc.), plus `tests/agent/`, `tests/tools/`,
`tests/gateway/`, `tests/e2e/`, `tests/hermes_state/`, `tests/pm/`.
