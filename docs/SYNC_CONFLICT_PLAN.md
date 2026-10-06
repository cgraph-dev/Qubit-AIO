# SYNC_CONFLICT_PLAN.md

Resolution plan for the `qubit_cli` sync slice (upstream pin `69948c0057` -> `bd0affe5`, 4,128 commits).
Analysis only — no `sync --apply` was run and `qubit_runtime/` was not modified.

## Executive summary

- The slice merged **203** files, added **36**, and left **90** as CONFLICT (179 conflict hunks).
- The prior classifier (`scripts/sync_classify_conflicts.py`) split these into `9 spurious / 81 genuine`. **Both numbers are wrong.** The spurious bucket is entirely mis-bucketed: every one of the 90 conflicts is genuine, so the plan below covers **90** files, not 81.
- Verdicts: **41 TAKE-UPSTREAM**, **43 MANUAL-MERGE**, **6 KEEP-QUBIT**.
- The slice as applied is **NOT internally consistent**: 10 modules now import upstream symbols whose providers live outside the slice (`utils.mkstemp_beside`, `qubit_constants.get_hermes_home`, `qubit_state_errors.SessionActiveWriteGuardError`, `qubit_cli.config_defaults.DEFAULT_SANDBOX_IMAGE`, `qubit_platform.host.products.is_nvidia_n1x_pci_id`, `qubit_cli.auth.looks_like_openrouter_key`). See *Recommendation*.

---

## 1. Audit of the normalizer in `scripts/sync_classify_conflicts.py`

### What it does

`normalize()` collapses **both** brand directions to one neutral token before re-merging:

```python
text = re.sub(r"\b(?:HERMES_|QUBIT_)([A-Z0-9_]+)", r"X_\1", text)  # both -> X_
text = re.sub(r"\b(?:hermes_|qubit_)([a-z0-9_]+)", r"x_\1", text)    # both -> x_
text = re.sub(r"\b(?:Hermes|Qubit)(?=[A-Z_])", "X", text)           # both -> X
```

A conflict is bucketed **spurious** when the *normalized* 3-way merge returns rc=0. The theory is: if the only bar is the rename itself, neutralising the rename makes it clean.

### Why it is too eager

The normalizer is a two-sided, many-to-one collapse. It maps `Hermes` **and** `Qubit` to the same token `X`. That is valid only when one side's `Hermes*` is provably the *same identifier* as the other side's `Qubit*` (which is exactly what `translate()` guarantees for the upstream blobs). But Qubit's own working file (`cur`) is **not** translated — it legitimately contains `Hermes`-spelled identifiers that Qubit **never renamed**. Collapsing `Hermes` -> `X` on the `cur` side folds two *distinct* identifiers together.

**Worked example — `qubit_cli/_parser.py` (the file reported as spurious):**

| side | text |
|---|---|
| cur (Qubit) | `class HermesArgumentParser(argparse.ArgumentParser):` |
| base (pin, translated) | `class QubitArgumentParser(argparse.ArgumentParser):` |
| new (upstream) | `def _plugin_command_install_hint(...)` … then `class QubitArgumentParser` |

Qubit's class is named `HermesArgumentParser` and is used as `parser = HermesArgumentParser(` in its own file. `translate()` rewrites the *upstream* blobs' `HermesArgumentParser` -> `QubitArgumentParser`, but `cur` is untouched. So the 3-way merge conflicts: **cur says `HermesArgumentParser`, base says `QubitArgumentParser`, new inserts an unrelated function** — a genuine overlap between Qubit's local class name and upstream's unrelated addition.

`normalize()` erases the difference: both `HermesArgumentParser` and `QubitArgumentParser` collapse to `XArgumentParser`. The unrelated insertion in `new` no longer overlaps an identical-looking line, so the normalized merge returns rc=0 and the file is stamped **spurious**. The underlying divergence — a class-name clash that only a human can resolve — is still there.

Proof it is still genuine: re-running the merge with `cur` left **as-is** (only the translated `base`/`new` normalised, which is the correct one-sided transform) still returns rc=1.

### Does the mis-bucketing generalise?

Re-classifying all 90 conflicts with a **one-sided** normalizer that only collapses the `QUBIT_`/`qubit_`/`Qubit` forms that `translate()` actually produced (leaving `Hermes`/`HERMES_` intact) yields **0 spurious**. A/B result:

```
OLD (two-sided) normalizer  -> 9 spurious / 81 genuine
SMART (one-sided) normalizer -> 0 spurious / 90 genuine
files in OLD-not-SMART: all 9 (the whole 'spurious' bucket)
files in SMART-not-OLD: none
```

The 9 mis-bucketed files are:
- `hermes_cli/_parser.py`
- `hermes_cli/cli_single_query.py`
- `hermes_cli/config_migrations.py`
- `hermes_cli/kanban_db.py`
- `hermes_cli/kanban_db_workspace.py`
- `hermes_cli/relay_plugin_migrate.py`
- `hermes_cli/skin_engine.py`
- `hermes_cli/subcommands/migrate.py`
- `hermes_cli/web_routers/display.py`

Each of these has `Hermes`-spelled identifiers in `cur` that Qubit never renamed (e.g. `_parser.py`'s `HermesArgumentParser`, `cli_single_query.py`'s `HERMES_KANBAN_*`, `web_routers/display.py`'s `info["hermes_home"]`). The two-sided collapse treats those as equivalent to their translated upstream twin and hides the real clash.

### Verdict on the spurious bucket

**It cannot be trusted.** As written it produces false negatives (marks genuine conflicts as auto-mergeable) and, empirically on this slice, contains no true positives at all. Two consequences:

1. Any pipeline that auto-applies the `spurious` bucket would silently apply a normalizer-resolved merge that may contradict Qubit's local intent (e.g. it would adopt `QubitArgumentParser` over Qubit's `HermesArgumentParser` without the conscious decision).
2. The stated "81 genuine" undercounts by 9. This plan classifies all **90**.

### Fix

Make the normalizer one-sided — collapse only the identifiers `translate()` *creates* (`QUBIT_`/`qubit_`/`Qubit`), never `HERMES_`/`hermes_`/`Hermes` — and require `cur == normalize(translate(base))` as a precondition before the normalised re-merge is allowed to be trusted. Even then the bucket should be treated as *"needs a quick confirm"*, not *"auto-apply"*, because a normalizer pass can never prove semantic equivalence of identifiers it erased.

## 2. Per-file resolution for all 90 genuine conflicts

`clash` = merged-output line of the first conflict hunk (line numbers are positions in the merge result, `git merge-file --diff3`). `h` = number of conflict hunks in the file. Every file below was read hunk-by-hunk; nothing is sampled.

### 2.1 TAKE-UPSTREAM (41 files)

| file | h | clash | reason |
|---|---|---|---|
| `hermes_cli/auth_nous.py` | 1 | L379 | Upstream adds lock_path to the timeout message. |
| `hermes_cli/backup.py` | 2 | L1152 | Qubit left hermes_root/get_default_qubit_root; upstream added incomplete-zip retention + prune. |
| `hermes_cli/callbacks.py` | 1 | L41 | Upstream added ValueError handling for locked/denylisted keys. |
| `hermes_cli/cli_agent_setup_mixin.py` | 2 | L475 | Brand copy superseded by upstream i18n keys cli.startup.*. |
| `hermes_cli/cli_billing_mixin.py` | 7 | L1 | Qubit copy is brand-only; upstream moved all copy to the i18n catalog (cli.billing.*). |
| `hermes_cli/cli_chat_error_copy.py` | 1 | L68 | Brand copy superseded by upstream error-catalog key cli.error.agent_init_failed. |
| `hermes_cli/cli_commands_mixin.py` | 9 | L187 | Qubit brand copy superseded by upstream's i18n catalog (_t(...)); one hunk (worktree) upstream also added /worktree remove. |
| `hermes_cli/cli_info_mixin.py` | 4 | L185 | Brand copy superseded by upstream i18n keys (cli.banner.*, cli.gateway_status.*). |
| `hermes_cli/cli_loops_mixin.py` | 1 | L292 | Brand copy superseded by upstream i18n keys (cli.plugins.*). |
| `hermes_cli/cli_render.py` | 2 | L922 | Brand copy + version line superseded by upstream i18n keys cli.render.*. |
| `hermes_cli/cli_tui_mixin.py` | 4 | L477 | Brand copy superseded by upstream i18n keys (cli.tui.*). |
| `hermes_cli/cli_tui_runtime_mixin.py` | 3 | L268 | Brand copy + redaction banner superseded by upstream i18n keys (cli.tui.*). |
| `hermes_cli/cli_voice_mixin.py` | 2 | L80 | Brand copy superseded by upstream i18n keys (cli.voice.*) incl. state-id refactor. |
| `hermes_cli/commands.py` | 1 | L148 | Upstream added aliases=("s",) to /goal. |
| `hermes_cli/commands_platforms.py` | 1 | L406 | Qubit's stale ("hermes", ...) entry superseded by upstream ("qubit", t(...)). |
| `hermes_cli/config_defaults.py` | 1 | L1526 | Comment-only; upstream comment supersedes. |
| `hermes_cli/config_migrations.py` | 1 | L506 | Qubit left HERMES_NEMO_RELAY_PLUGINS_TOML (partial rename); upstream reworded + QUBIT_ token. |
| `hermes_cli/doctor_state.py` | 1 | L595 | Upstream brand wording; Qubit's block replaced. |
| `hermes_cli/kanban_db.py` | 1 | L5 | Qubit left HERMES_KANBAN_ docs; upstream QUBIT_KANBAN_ + expanded semantics. |
| `hermes_cli/memory_setup.py` | 1 | L428 | Upstream added catalog install hint. |
| `hermes_cli/observability/relay_shared_metrics.py` | 2 | L558 | Upstream restructured flush into _schedule_flush_and_export + retirement. |
| `hermes_cli/observability/shared_metrics.py` | 1 | L34 | Upstream bumped schema v2->v3. |
| `hermes_cli/observability/shared_metrics_contract.py` | 3 | L18 | Upstream qubit.* keys + COMMIT_TICKET_KEY + v4/v5 metrics. |
| `hermes_cli/observability/shared_metrics_subscriber.py` | 1 | L170 | Upstream rewrote the persist path (classification list, commit tickets, milestones). |
| `hermes_cli/plugin_catalog.py` | 1 | L529 | Upstream added known_issues surfacing. |
| `hermes_cli/plugins_cmd.py` | 1 | L650 | Upstream reworded the Relay plugins.toml guidance. |
| `hermes_cli/plugins_cmd_install.py` | 2 | L140 | Upstream added _ask_yes_no helper and _known_issue_warnings. |
| `hermes_cli/plugins_discovery.py` | 1 | L298 | Same Relay guidance rewording upstream. |
| `hermes_cli/relay_plugin_migrate.py` | 2 | L3 | Qubit left HERMES_ tokens; upstream reworded + QUBIT_ tokens. |
| `hermes_cli/setup_profile.py` | 1 | L55 | Qubit brand docstring; upstream prose. |
| `hermes_cli/skin_engine.py` | 1 | L28 | Upstream adds custom_css field. |
| `hermes_cli/subcommands/logs.py` | 1 | L12 | Upstream renamed command members + added update.log to desc. |
| `hermes_cli/subcommands/migrate.py` | 1 | L31 | Qubit left HERMES_ tokens; upstream reworded + QUBIT_ tokens. |
| `hermes_cli/tips.py` | 2 | L14 | Whole-file TIPS list replaced upstream by the i18n catalog. |
| `hermes_cli/update_abort_recovery.py` | 1 | L18 | Qubit left hermes-serve; upstream qubit-serve*/dashboard. |
| `hermes_cli/update_cmd.py` | 1 | L798 | Comment-only; upstream rewrote it. |
| `hermes_cli/update_cmd_fleet.py` | 2 | L39 | Qubit left hermes-gateway/serve; upstream qubit-* + dashboard. |
| `hermes_cli/update_cmd_windows.py` | 1 | L256 | Qubit left hermes/hermes.exe; upstream qubit + qubit_cli/main.py suffix. |
| `hermes_cli/update_restart_recovery.py` | 3 | L66 | Qubit left hermes-serve; upstream qubit-(serve\|dashboard). |
| `hermes_cli/web_server_gateway.py` | 1 | L515 | Qubit get_process_qubit_home; upstream renamed + simplified return. |
| `hermes_cli/web_server_oauth.py` | 1 | L46 | Upstream renamed the card "Anthropic Account". |

### 2.2 KEEP-QUBIT (6 files)

| file | h | clash | reason |
|---|---|---|---|
| `hermes_cli/agent_import.py` | 1 | L668 | Upstream removed the PLUGIN-COMPAT block; Qubit keeps it. |
| `hermes_cli/dashboard_procs.py` | 1 | L15 | Qubit kept the launcher tuple and upstream deleted the block; Qubit needs the tuple. Re-brand "hermes"->"qubit". |
| `hermes_cli/local_runtime/binaries.py` | 1 | L250 | Upstream removed the PLUGIN-COMPAT block Qubit still needs. |
| `hermes_cli/local_runtime/bootstrap.py` | 1 | L488 | Upstream removed the PLUGIN-COMPAT block Qubit still needs. |
| `hermes_cli/plugin_compat.py` | 4 | L3 | Upstream DELETED the compat layer; Qubit still ships PLUGIN-COMPAT blocks and must keep the scanner. |
| `hermes_cli/telegram_managed_bot.py` | 1 | L189 | Upstream removed the PLUGIN-COMPAT block; Qubit keeps it (renamed QubitSetupBot). |

### 2.3 MANUAL-MERGE (43 files)

| file | h | clash | reason |
|---|---|---|---|
| `hermes_cli/_launchers.py` | 2 | L45 | H1 upstream adds _inline_string_literal; Qubit default_home refs get_default_qubit_root; H2 Qubit left .hermes paths. |
| `hermes_cli/_parser.py` | 1 | L322 | Canonical mis-bucket: Qubit's `class HermesArgumentParser` (never renamed) vs upstream's unrelated _plugin_command_install_hint insertion + a differently-named class. |
| `hermes_cli/anon_auth.py` | 3 | L6 | Qubit brand docstrings vs upstream real changes (providers.nous, guest_carries_inference->has_free_tier_account). |
| `hermes_cli/auth.py` | 3 | L261 | H1 atomic_config_write vs upstream atomic_config_replace; H2 docstring/sk-or- logic; H3 Qubit's PLUGIN-COMPAT block vs upstream removal. |
| `hermes_cli/cli_config_load.py` | 2 | L27 | Qubit _qubit_home/_hermes_home; upstream added base_dir param. |
| `hermes_cli/cli_model_switch_mixin.py` | 2 | L257 | H1 Qubit's HermesCLI import (unrenamed) vs upstream's old_provider capture; H2 brand->i18n. |
| `hermes_cli/cli_session_mixin.py` | 5 | L15 | H1 upstream adds `from agent.i18n import t`; H2-H5 brand->i18n; H5 also HERMES_YOLO_MODE->QUBIT_YOLO_MODE. |
| `hermes_cli/cli_single_query.py` | 4 | L171 | Qubit left HERMES_KANBAN_* env names; upstream kanban_task_id() helper + goal-mode exit-code gating. |
| `hermes_cli/config.py` | 3 | L48 | H1 mkstemp_beside; H2 recording_raw_config_write; H3 _platform_manifest_paths refactor; Qubit keeps get_qubit_home. |
| `hermes_cli/env_loader.py` | 3 | L53 | H1 upstream adds _SECRET_SOURCE_WRITES_BY_HOME; H2/H3 Qubit get_qubit_home_override vs upstream get_hermes_home_override + multiplex guard. |
| `hermes_cli/free_tier_bootstrap.py` | 1 | L37 | Qubit comment; upstream renamed free_tier->free_tier_account. |
| `hermes_cli/gateway.py` | 5 | L582 | Qubit get_qubit_home vs upstream get_hermes_home + new owner_home/unit_dir/_home_owns_bare_service_name logic. |
| `hermes_cli/gateway_launchd.py` | 1 | L287 | Qubit get_qubit_home vs upstream get_hermes_home + served_profile_child_env import. |
| `hermes_cli/gateway_migrate.py` | 3 | L215 | Qubit set_qubit_home_override vs upstream pin_process_hermes_home feature. |
| `hermes_cli/gitlock.py` | 1 | L465 | Qubit .hermes-prune; upstream .qubit-prune + reflog-expire feature. |
| `hermes_cli/gui_uninstall.py` | 2 | L38 | H1 Qubit kept hermes-agent/ layout path vs upstream qubit/; H2 Qubit kept Hermes.desktop vs upstream LEGACY_DESKTOP_ENTRY_NAME. |
| `hermes_cli/kanban_db_dispatch.py` | 2 | L978 | H1 brand->i18n (take); H2 upstream adds _propagate_module_import_root AND keeps _absolute_hermes_path rename. Combine. |
| `hermes_cli/kanban_db_workspace.py` | 1 | L101 | Qubit left HERMES_KANBAN_WORKSPACES_ROOT; upstream renamed + restructured roots with _add_root. |
| `hermes_cli/linux_desktop_entry.py` | 8 | L1 | Upstream rewrote the whole app-id/entry system (APP_ID, LEGACY_DESKTOP_ENTRY_NAME, _is_this_checkout_managed_cli); Qubit's file still uses hermes* names. |
| `hermes_cli/main.py` | 2 | L292 | H1 Qubit HERMES_TUI vs upstream QUBIT_TUI + --native/--tui-native; H2 brand vs _hermes_current_updater_frame + retarget. |
| `hermes_cli/main_dashboard.py` | 2 | L188 | H1 upstream rewrote unit-ownership docstring logic; H2 upstream added spawned list. |
| `hermes_cli/main_desktop.py` | 5 | L54 | MacOS binary string, hermes-update-*, with_qubit_node_path vs upstream socket_safe_tmpdir feature, brand. |
| `hermes_cli/model_setup_flows.py` | 1 | L210 | Qubit brand copy vs upstream brand + _note_setup_failure("no_models"). |
| `hermes_cli/nous_account.py` | 1 | L125 | Qubit brand docstring; upstream added _normalize_tier comparison. |
| `hermes_cli/plugin_validate.py` | 1 | L541 | Qubit brand docstring; upstream added a `manifest` param + "declared language pack" clause (real signature change). |
| `hermes_cli/plugins.py` | 3 | L1454 | H1 Qubit load_qubit_dotenv vs upstream refresh_installed_secret_scope; H2 brand; H3 upstream removed PLUGIN-COMPAT block. |
| `hermes_cli/plugins_manifest.py` | 1 | L37 | Qubit has "hermes"/"requires_qubit"; upstream dropped "hermes" + added "provides_locales". |
| `hermes_cli/pty_session.py` | 1 | L269 | Qubit keep_detached feature vs upstream concurrent-gather rewrite. |
| `hermes_cli/setup.py` | 2 | L529 | H1 upstream expands the metrics disclosure + save_consent import; H2 replaces raw write with save_consent(). Qubit's only divergence is brand copy; re-brand after. |
| `hermes_cli/setup_quick.py` | 1 | L58 | Qubit local "Qubit Labs" one-shot + install_source base_url import; upstream "Nous Portal" + _record_setup_completed. |
| `hermes_cli/status.py` | 3 | L15 | H1 get_qubit_home vs get_hermes_home + image imports; H2 _render_cron vs _cron_summary refactor; H3 brand. |
| `hermes_cli/status_report.py` | 1 | L96 | Qubit display_qubit_home() vs upstream display_hermes_home(Path(home) if home else None). |
| `hermes_cli/subcommands/backup.py` | 1 | L32 | Qubit left hermes-backup-*.zip help; upstream qubit-* + _non_negative_keep validator. |
| `hermes_cli/uninstall.py` | 3 | L370 | H1 Qubit _qubit_path_markers vs upstream _hermes_path_markers r-string; H2/H3 brand copy vs upstream added macOS/userdata lines. |
| `hermes_cli/update_cmd_config.py` | 2 | L49 | Qubit get_process_qubit_home/set_qubit_home_override vs upstream get_process_hermes_home + _sibling_profile_homes refactor (real feature). |
| `hermes_cli/update_cmd_git.py` | 2 | L16 | H1 Qubit keeps `from qubit_cli import install_source`, upstream adds windows_hide_flags import; H2 Qubit refs/hermes-update-backups, upstream refs/qubit-* + detached kind. |
| `hermes_cli/update_cmd_maint.py` | 2 | L299 | H1 Qubit hermes-dashboard vs upstream qubit-dashboard + changed return contract (set()); H2 Qubit left HERMES_ token. |
| `hermes_cli/web_routers/display.py` | 1 | L128 | Qubit kept info["hermes_home"]; upstream renamed to info["qubit_home"] and dropped the sock line Qubit needs. |
| `hermes_cli/web_routers/messaging.py` | 1 | L299 | Qubit get_process_qubit_home vs upstream get_process_hermes_home + trimmed comment. |
| `hermes_cli/web_routers/status.py` | 1 | L311 | Qubit profile_dir.name vs upstream profile_name_for_home bugfix #123088. |
| `hermes_cli/web_server.py` | 3 | L1399 | H1 Qubit HERMES_ sentinels vs upstream neutral+legacy dual sentinel; H2 brand; H3 upstream removed the PLUGIN-COMPAT block Qubit keeps. |
| `hermes_cli/web_server_chat.py` | 1 | L348 | Qubit _qubit_ws_subprotocol rename; upstream added the _verify_access_token token leg. |
| `hermes_cli/worktree_ops.py` | 1 | L22 | Qubit get_qubit_home vs upstream get_hermes_home + noninteractive_git_env import. |

## 3. MANUAL-MERGE: concrete per-hunk decisions

For each MANUAL-MERGE file, the decision per hunk. `T`=take upstream side, `K`=keep Qubit side, `C`=combine both. Hunk line numbers are the merged-output positions from `--diff3`.

**`hermes_cli/_launchers.py`** (2 hunks)

- H1: TAKE upstream `_inline_string_literal(...)` (real hardening); KEEP Qubit's `get_default_qubit_root` identifier (Qubit's rename is intentional).
- H2: TAKE upstream scan-list (`venv/bin/qubit` added); reconcile the `hermes`/`.hermes` spellings to `qubit`/`.qubit` — Qubit left them unrenamed.

**`hermes_cli/_parser.py`** (1 hunks)

- H1: COMBINE — insert upstream's `_plugin_command_install_hint(...)` function; keep Qubit's `class HermesArgumentParser`, or adopt upstream's `QubitArgumentParser` + update the `parser = ...` call site. This is the exact clash the old classifier hid.

**`hermes_cli/anon_auth.py`** (3 hunks)

- H1: TAKE upstream docstring (`providers.nous` singleton wording).
- H2: COMBINE — adopt upstream's `has_free_tier_account` rename (real API change) but keep Qubit's docstring brand where surfaced.
- H3: TAKE upstream's trimmed wording; reconcile `Qubit`/`Nous` in the user-facing line.

**`hermes_cli/auth.py`** (3 hunks)

- H1: COMBINE — take upstream's `atomic_config_replace`; keep the qubit-home accessor name Qubit uses (`get_qubit_home` if it exists, else align).
- H2: TAKE upstream's `sk-or-`/`OPENROUTER_API_KEY` provider-resolution logic (real behavior).
- H3: COMBINE — take upstream's `Qubit`/`Nous` brand line AND KEEP the PLUGIN-COMPAT block (upstream deleted it; Qubit still ships it).

**`hermes_cli/cli_config_load.py`** (2 hunks)

- H1: TAKE upstream's `base_dir` parameter + docstring (real feature).
- H2: COMBINE — use upstream's `(base_dir or _cli()._hermes_home)`; align the accessor name to Qubit's.

**`hermes_cli/cli_model_switch_mixin.py`** (2 hunks)

- H1: COMBINE — take upstream's `old_provider = getattr(cli, "provider", None)` (real feature); keep Qubit's `from cli import HermesCLI` (unrenamed) or align.
- H2: TAKE upstream i18n keys `cli.model.*`; re-brand via the catalog.

**`hermes_cli/cli_session_mixin.py`** (5 hunks)

- H1: TAKE upstream's `from agent.i18n import t` import; align the qubit-home accessor.
- H2: TAKE upstream i18n + `QUBIT_DEFER_AGENT_STARTUP`.
- H3: TAKE upstream i18n `cli.session.status_title`.
- H4: TAKE upstream i18n (agent_name='Hermes' -> Qubit's brand value).
- H5: TAKE upstream i18n `cli.session.yolo_locked`; fix the local `HERMES_YOLO_MODE` literal to `QUBIT_YOLO_MODE`.

**`hermes_cli/cli_single_query.py`** (4 hunks)

- H1: COMBINE — take upstream's `kanban_task_id()` helper call; Qubit's `HERMES_KANBAN_TASK` literal must become the helper.
- H2: COMBINE — same: `kanban_task_id()`.
- H3: COMBINE — take upstream's goal-mode `and _exit_code == 0` gating (real safety fix).
- H4: COMBINE — take upstream's `_single_query_exit_code(...) == 0` gating.

**`hermes_cli/config.py`** (3 hunks)

- H1: COMBINE — take upstream's `mkstemp_beside` import ONLY if `utils.mkstemp_beside` is synced (it is not — see Recommendation); keep Qubit's `get_qubit_home`.
- H2: COMBINE — take upstream's `recording_raw_config_write(...)`; keep `ensure_qubit_home`/`atomic_config_write` names per Qubit.
- H3: COMBINE — take upstream's `_platform_manifest_paths` refactor (real feature); rename `get_hermes_home`->Qubit's accessor.

**`hermes_cli/env_loader.py`** (3 hunks)

- H1: TAKE upstream's `_SECRET_SOURCE_WRITES_BY_HOME` tracking (real fix).
- H2: COMBINE — take upstream's extra `get_hermes_home` import; align the override accessor name.
- H3: COMBINE — take upstream's multiplex guard (real fix); align accessor names.

**`hermes_cli/free_tier_bootstrap.py`** (1 hunks)

- H1: COMBINE — take upstream's `free_tier_account` field rename (real API change); keep Qubit's comment brand.

**`hermes_cli/gateway.py`** (5 hunks)

- H1: COMBINE — take upstream's `current_home_path` split + `_hermes_home_for_pid` import; align accessor to Qubit.
- H2: COMBINE — take upstream's environment-based ownership resolution (real fix); rename `command_line_names_hermes_home` to Qubit's name.
- H3: TAKE upstream's `from qubit_constants import get_hermes_home` if that symbol is synced (it is not — see Recommendation).
- H4: COMBINE — take upstream's `unit_dir` (root vs user) selection (real fix).
- H5: COMBINE — take upstream's `_home_owns_bare_service_name(home)` (real fix); align `get_default_*` name.

**`hermes_cli/gateway_launchd.py`** (1 hunks)

- H1: COMBINE — take upstream's `served_profile_child_env` import (real feature); align accessor.

**`hermes_cli/gateway_migrate.py`** (3 hunks)

- H1: COMBINE — take upstream's `pin_process_hermes_home` import block; align override accessor names.
- H2: COMBINE — take upstream's pin/unpin logic (real fix); align accessor.
- H3: COMBINE — take upstream's unpin on exit; align accessor.

**`hermes_cli/gitlock.py`** (1 hunks)

- H1: COMBINE — take upstream's reflog-expire feature (real fix); rename `.hermes-prune`->`.qubit-prune` (Qubit left it unrenamed).

**`hermes_cli/gui_uninstall.py`** (2 hunks)

- H1: TAKE upstream's `qubit/` layout path + node_modules doc (real fix).
- H2: COMBINE — take upstream's `LEGACY_DESKTOP_ENTRY_NAME`; keep the extra `hermes.png` icon entries Qubit retained if still needed.

**`hermes_cli/kanban_db_dispatch.py`** (2 hunks)

- H1: TAKE upstream i18n (`_exit_summary_marker`, `_log_noise_prefixes`).
- H2: COMBINE — take upstream's `_propagate_module_import_root` (real fix); keep Qubit's `_absolute_hermes_path` rename (or align).

**`hermes_cli/kanban_db_workspace.py`** (1 hunks)

- H1: COMBINE — take upstream's `_add_root`/lexical-root refactor (real fix); rename `QUBIT_KANBAN_WORKSPACES_ROOT` (Qubit left `HERMES_`).

**`hermes_cli/linux_desktop_entry.py`** (8 hunks)

- H1: TAKE upstream `<app_id>.desktop` docstring.
- H2: COMBINE — take upstream's `APP_ID`/`DESKTOP_ENTRY_NAME`/`LEGACY_DESKTOP_ENTRY_NAME` (real app-id fix); Qubit's `hermes.desktop` must be re-pointed.
- H3: COMBINE — insert upstream's `_is_this_checkout_managed_cli`; keep Qubit's `_resolve_*_bin_for_desktop_entry` name (align).
- H4: TAKE upstream's generic 'the entry' wording.
- H5: TAKE upstream's generic wording.
- H6: COMBINE — take upstream's `StartupWMClass={APP_ID}`; Qubit's `Qubit` literal becomes the app-id.
- H7: TAKE upstream's clobber/retirement wording + logic.
- H8: COMBINE — take upstream's `exec_command` variable fix; keep the icon value Qubit needs.

**`hermes_cli/main.py`** (2 hunks)

- H1: COMBINE — take upstream's `QUBIT_TUI` + `--native`/`--tui-native` flags (real feature); fix Qubit's `HERMES_TUI` literal.
- H2: COMBINE — take upstream's `_hermes_current_updater_frame` marker + `retarget_to_owning_install` (real feature); keep Qubit's docstring brand.

**`hermes_cli/main_dashboard.py`** (2 hunks)

- H1: TAKE upstream's unit-ownership docstring/logic (real fix).
- H2: COMBINE — take upstream's `spawned` list + 3-tuple types (real feature); align accessor.

**`hermes_cli/main_desktop.py`** (5 hunks)

- H1: KEEP Qubit's `Qubit` binary-name comment (local decision) unless adopting upstream verbatim.
- H2: same as H1 for the `/Contents/MacOS/...` comment.
- H3: COMBINE — take upstream's codesign downgrade check (real fix) AND upstream's `qubit-update-new/old` names; Qubit's `hermes-update-*` must align.
- H4: COMBINE — take upstream's `socket_safe_tmpdir` TMPDIR fix (real feature); align `with_hermes_node_path` accessor name.
- H5: COMBINE — take upstream's build-lock release (real fix); keep Qubit's 'Qubit Desktop' brand string.

**`hermes_cli/model_setup_flows.py`** (1 hunks)

- H1: COMBINE — take upstream's `_note_setup_failure("no_models")` (real feature); keep Qubit's 'Qubit Labs' brand string.

**`hermes_cli/nous_account.py`** (1 hunks)

- H1: COMBINE — take upstream's `_normalize_tier(...)` comparison (real fix); keep Qubit's docstring brand.

**`hermes_cli/plugin_validate.py`** (1 hunks)

- H1: COMBINE — take upstream's `manifest` param + 'declared language pack' clause (real feature); keep Qubit's docstring brand.

**`hermes_cli/plugins.py`** (3 hunks)

- H1: COMBINE — take upstream's `refresh_installed_secret_scope` (real fix); align `load_*_dotenv` name.
- H2: TAKE upstream's Relay plugins.toml rewording + brand line.
- H3: KEEP Qubit's PLUGIN-COMPAT block (upstream deleted it; Qubit still ships it).

**`hermes_cli/plugins_manifest.py`** (1 hunks)

- H1: COMBINE — take upstream's `provides_locales`; KEEP Qubit's `hermes`/`requires_qubit` compat keys.

**`hermes_cli/pty_session.py`** (1 hunks)

- H1: COMBINE — keep Qubit's `keep_detached` param + viewer-survival guard AND adopt upstream's concurrent `asyncio.gather(..., _background_closes)` rewrite. Both are meaningful; the combined signature should keep `keep_detached` and gather the non-detached sessions.

**`hermes_cli/setup.py`** (2 hunks)

- H1: TAKE upstream's expanded disclosure + `save_consent` import (real change).
- H2: TAKE upstream's `save_consent(...)` write (real change); keep Qubit's brand in the prompt string.

**`hermes_cli/setup_quick.py`** (1 hunks)

- H1: COMBINE — keep Qubit's local `install_source.base_url` import AND take upstream's `_record_setup_completed` import; resolve the banner brand string.

**`hermes_cli/status.py`** (3 hunks)

- H1: COMBINE — take upstream's `DEFAULT_SANDBOX_IMAGE`/`DEFAULT_VERCEL_IMAGE` import ONLY if `config_defaults` is synced (it is not — see Recommendation); align accessor.
- H2: COMBINE — take upstream's `_cron_summary` refactor (real feature); align accessor.
- H3: TAKE upstream's `--full/--deep` docstring; keep Qubit's brand in the comment.

**`hermes_cli/status_report.py`** (1 hunks)

- H1: COMBINE — take upstream's `display_hermes_home(Path(home) if home else None)` signature (real fix); align the display-name helper to Qubit's.

**`hermes_cli/subcommands/backup.py`** (1 hunks)

- H1: COMBINE — take upstream's `_non_negative_keep` validator (real fix); rename `hermes-backup-*` help to Qubit's.

**`hermes_cli/uninstall.py`** (3 hunks)

- H1: COMBINE — take upstream's raw-string prefix; reconcile `_qubit_path_markers` vs `_hermes_path_markers` naming.
- H2: COMBINE — keep Qubit's 'Keep Qubit config/data' brand AND take upstream's `userdata` line (real feature).
- H3: COMBINE — take upstream's macOS/desktop lines (real feature); align accessor.

**`hermes_cli/update_cmd_config.py`** (2 hunks)

- H1: COMBINE — take upstream's `_sibling_profile_homes` refactor (real feature); align accessor.
- H2: COMBINE — take upstream's `set_hermes_home_override(profile_home)`; align accessor.

**`hermes_cli/update_cmd_git.py`** (2 hunks)

- H1: COMBINE — keep Qubit's `from qubit_cli import install_source` (used locally) AND take upstream's `windows_hide_flags` import.
- H2: COMBINE — take upstream's `detached` kind + `refs/qubit-update-backups`; Qubit's `refs/hermes-update-backups` must align.

**`hermes_cli/update_cmd_maint.py`** (2 hunks)

- H1: COMBINE — take upstream's `qubit-dashboard` unit name AND its changed return contract (`set()`); Qubit's `hermes-dashboard` literal must align.
- H2: TAKE upstream's `QUBIT_NEMO_RELAY_ATIF_*` wording.

**`hermes_cli/web_routers/display.py`** (1 hunks)

- H1: COMBINE — take upstream's `info["qubit_home"]` rename; KEEP the `sock = Path(...) / "bot-desktop" / "rfb.sock"` line Qubit still uses (upstream dropped it).

**`hermes_cli/web_routers/messaging.py`** (1 hunks)

- H1: COMBINE — take upstream's trimmed comment; align `get_process_*_home` accessor.

**`hermes_cli/web_routers/status.py`** (1 hunks)

- H1: TAKE upstream's `profile_name_for_home(profile_dir or ...)` fix (#123088, real behavior); align accessor name.

**`hermes_cli/web_server.py`** (3 hunks)

- H1: TAKE upstream's neutral+legacy dual-sentinel block (real fix).
- H2: TAKE upstream's `QUBIT_SERVE_HEADLESS` + `isolated` ledger (real feature).
- H3: KEEP Qubit's large PLUGIN-COMPAT block (upstream deleted it; Qubit still ships it).

**`hermes_cli/web_server_chat.py`** (1 hunks)

- H1: COMBINE — keep Qubit's `_qubit_ws_subprotocol` rename AND take upstream's `_verify_access_token` token leg (real feature).

**`hermes_cli/worktree_ops.py`** (1 hunks)

- H1: COMBINE — take upstream's `noninteractive_git_env` import (real feature); align accessor.

## 4. Recommendation

### Verdict: **PARTIALLY REVERT**

Do **not** keep the slice as-is and do **not** fully revert it. Revert the subset that is unsound, keep the rest.

#### Why not KEEP

The applied slice is internally inconsistent. Ten merged modules now import upstream symbols whose providers live **outside** the slice (other subsystems were not synced), so those modules fail to import on the current tree. Reproduced with the project venv (`.venv/bin/python`) doing an import smoke of the 201 merged `qubit_cli` modules (`QUBIT_HOME` isolated):

```
OK 191  FAIL 10
```

| broken importer (in slice) | missing symbol | provider | status of provider |
|---|---|---|---|
| `qubit_cli.backup_restore` | `utils.mkstemp_beside` | `qubit_runtime/utils.py` | NOT in slice — symbol absent |
| `qubit_cli.claw` | `qubit_cli.config_defaults.DEFAULT_SANDBOX_IMAGE` | `qubit_cli/config_defaults.py` | in slice, but symbol absent (upstream adds it in a non-synced region) |
| `qubit_cli.setup_terminal` | `qubit_cli.config_defaults.DEFAULT_SANDBOX_IMAGE` | same | same |
| `qubit_cli.runtime_provider` | `qubit_cli.auth.looks_like_openrouter_key` | `qubit_cli/auth.py` | CONFLICT (untouched) — the new `auth.py` never landed |
| `qubit_cli.sessions_cmd` | `qubit_state_errors.SessionActiveWriteGuardError` | `qubit_runtime/qubit_state_errors.py` | NOT in slice — symbol absent |
| `qubit_cli.sessions_cmd_browse` | `qubit_state_errors.SessionActiveWriteGuardError` | same | same |
| `qubit_cli.web_routers.sessions` | `qubit_state_errors.SessionActiveWriteGuardError` | same | same |
| `qubit_cli.web_routers.cron` | `qubit_constants.get_hermes_home` | `qubit_runtime/qubit_constants.py` | NOT in slice — symbol absent |
| `qubit_cli.local_runtime.catalog` | `qubit_platform.host.products.is_nvidia_n1x_pci_id` | `qubit_platform/` | NOT in slice — symbol absent |
| `qubit_cli.web_routers.local_models` | `qubit_platform.host.products.is_nvidia_n1x_pci_id` | same | same |

Each break is a `git diff HEAD` addition from the slice (verified, e.g. `backup_restore.py` gained `mkstemp_beside` from `utils`; `web_routers/cron.py` gained `from qubit_constants import get_hermes_home as _get_hermes_home`). This is the classic vertical-slice hazard: a merged file adopts upstream's new import, but the module that must export it sits in a bucket that was not synced. The merge is textually clean; the tree is not importable.

Note this second class of breakage (`get_hermes_home` from `qubit_constants`) is the **rename mismatch** in the opposite direction: upstream renamed `get_hermes_home` -> `get_qubit_home`, but `qubit_constants.py` was never translated, so taking upstream's `get_hermes_home` reference resolves to nothing. The same two-sided rename confusion that broke the classifier also drives these runtime breaks.

#### Why not full REVERT

The 41 TAKE-UPSTREAM files and the non-conflicting 203 MERGED files carry real upstream fixes (i18n catalog migration, kanban goal-mode exit-code gating, concurrency in `pty_session.close_all`, `_propagate_module_import_root`, app-id desktop entry, secret-scope refresh, egress/multiplex fixes). Wholesale revert throws those away and puts the pin 4,128 commits further behind.

#### Recommended path

1. **Revert the 10 broken importers' offending import statements only** (the slice additions listed above) — or, cleaner, land the provider module each one needs (`utils.py`, `qubit_constants.py`, `qubit_state_errors.py`, `qubit_platform/`) in the same sync step, since each is a same-pin upstream change.
2. **Resolve the 6 KEEP-QUBIT files** by keeping Qubit's side verbatim (they are PLUGIN-COMPAT blocks upstream deleted; Qubit still ships the compat layer).
3. **Apply the 41 TAKE-UPSTREAM files** as-is (their Qubit divergence is only brand copy the upstream i18n catalog now owns).
4. **Hand-merge the 43 MANUAL-MERGE files** per §3.
5. **Fix the classifier** to a one-sided normalizer + the `cur == normalize(translate(base))` precondition, and treat its output as advisory, never auto-apply (§1).

### Test command that proves the slice did not break anything

The `qubit_runtime/` tree is excluded from the repo's hermetic pytest gate (`pytest.ini: norecursedirs = ... qubit_runtime`), so the repo unit suite does **not** exercise it. The correct proof has two legs:

**Leg A — every merged module still imports** (catches the 10 breaks above and any future bucket-boundary break):

```bash
cd /home/trick/Projects/Repos/qubit-voicebridge
.venv/bin/python - <<'PY'
import json, os, importlib, tempfile
os.environ['QUBIT_HOME'] = tempfile.mkdtemp()      # isolated home, no user state
import sys; sys.path.insert(0, 'qubit_runtime')
merged = json.load(open('/tmp/slice_cli.json'))
mods = sorted({r['upstream'][:-3].replace('/', '.') for r in merged
               if r['status'] == 'MERGED' and r['upstream'].endswith('.py')
               and r['upstream'].startswith('hermes_cli/')})
bad = []
for m in mods:
    m = 'qubit_cli.' + m[len('qubit_cli.'):]            # route hermes_cli -> qubit_cli
    try: importlib.import_module(m)
    except BaseException as e: bad.append((m, type(e).__name__, str(e)))
print(f'{len(mods)-len(bad)}/{len(mods)} import OK; {len(bad)} FAIL')
for b in bad: print(' FAIL', b)
assert not bad, 'slice left unimportable modules'
PY
```

**Leg B — the repo's own hermetic gate still passes** (proves the slice did not disturb the Qubit-owned surface):

```bash
cd /home/trick/Projects/Repos/qubit-voicebridge
python scripts/run_tests.py unit
```

Leg A must report `201/201 import OK` (after step 1 above). On the current tree it reports `191/201` — the concrete evidence that KEEP-as-is is unsafe.

### Status of the analysis

Per the constraints: no `sync --apply` was run, `qubit_runtime/` was not modified, and the applied slice was treated as evidence. All 90 conflicts / 179 hunks were read; the plan is exhaustive.