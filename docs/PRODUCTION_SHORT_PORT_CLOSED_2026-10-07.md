# PRODUCTION SHORT-PORT BURNDOWN — CLOSED (all false positives)

Date: 2026-10-07   Milestone: `ee8dd6c886`   Base: `5a734b66`
Audit: `.venv/bin/python scripts/audit_sync_completeness.py --rev ee8dd6c886 --json /tmp/n4.json`

## RESULT

The audit reported **37 production short-port symbols across 21 files**.
**0 are genuinely absent.** All 37 are false positives, each proven by grep below.
The audit's `short_port` branch (line 193, `base_syms - our_syms`) does NOT apply the
`rebrand()` subtraction that the `missing` branch (line 185) does — so every
rebranded symbol reappears as a short-port. That is the entire over-report.

Gates unchanged (I made ZERO source edits): `gate_all.py` PASS 4/4;
`check_lazy_imports.py` 0 unguarded / 1 guarded (pre-existing at base, unrelated).

## PROVEN FALSE POSITIVES (grep evidence)

Rebrand rename `hermes_*` -> `qubit_*`, plus a few semantic renames the generic
`rebrand()` cannot map:

| file | reported symbol(s) | present as | evidence |
|---|---|---|---|
| tools/environments/local.py | _HERMES_BIN_DIR, _hermes_repo_root, _hermes_repo_root_aliases, _hermes_site_packages, _prepend_hermes_bin_dir, _resolve_hermes_bin_dir, hermes_subprocess_env | _QUBIT_*/qubit_* | grep _QUBIT_BIN_DIR etc. present |
| gateway/status.py | _HERMES_HOME_ASSIGNMENT_RE, _canonical_hermes_home, _get_process_hermes_home, _same_hermes_home, command_line_names_hermes_home, hermes_home_assignments | _QUBIT_*/qubit_* | present |
| qubit_cli/web_routers/actions.py | _spawn_hermes_action, check_hermes_update, get_hermes_home | _spawn_qubit_action, check_qubit_update, get_qubit_home | present |
| qubit_cli/banner.py | HERMES_AGENT_LOGO, HERMES_CADUCEUS | QUBIT_AGENT_LOGO, QUBIT_HERO | banner.py:74, banner.py:80; used at :702,:764 |
| tools/mcp_oauth.py | HermesOAuthClientProvider, HermesTokenStorage | QubitOAuthClientProvider, QubitTokenStorage | :915,:1250 / :413 |
| toolsets.py | _HERMES_CORE_TOOLS, _HERMES_WEBHOOK_SAFE_TOOLS | _QUBIT_* | present |
| qubit_cli/_parser.py | HermesArgumentParser | QubitArgumentParser | :337 |
| qubit_cli/claw.py | _SCRIPT_REL | _CANONICAL_SCRIPT_REL + _LEGACY_SCRIPT_REL | SUPERSEDED by owner decision #21 (2026-09-23), documented in-code :24-33 |
| qubit_cli/main_install_repair.py | _hermes_exe_shims | _qubit_exe_shims | present |
| qubit_cli/update_cmd.py | get_default_hermes_root | get_default_qubit_root | present |
| qubit_cli/update_cmd_fleet.py | _is_hermes_gateway_unit | _is_qubit_gateway_unit | present |
| qubit_cli/update_cmd_windows.py | _hermes_holder_subcommand | _qubit_holder_subcommand | present |
| qubit_cli/update_fleet_scope.py | systemd_unit_hermes_home | systemd_unit_qubit_home | present |
| qubit_cli/web_server_files.py | _default_hermes_root_is_opt_data | _default_qubit_root_is_opt_data | present |
| qubit_cli/web_server_gateway.py | _spawn_hermes_action | _spawn_qubit_action | present |
| plugins/model-providers/nous/__init__.py | nous | module instance renamed `nous`->`qubit` (:69, `qubit = NousProfile(...)`), `name="nous"` preserved, `register_provider(qubit)` :76 | REBRAND ARTIFACT confirmed |
| scripts/smoke_nemo_relay_shared_metrics.py | _resolve_hermes_executable | _resolve_qubit_executable | present |
| tools/approval_detection.py | _rewrite_resolved_hermes_home | _rewrite_resolved_qubit_home | present |
| tools/mcp_oauth_provider.py | HermesProviderMixin | QubitProviderMixin | :75 |
| tools/skill_manager_tool.py | HERMES_HOME | QUBIT_HOME | present |
| tools/skills_hub_official.py | HermesIndexSource | QubitIndexSource | :289 |

## `nous` VERDICT (task asked explicitly)

**CONFIRMED rebrand artifact, not a gap.** The tree named the module-level instance
`qubit` while preserving `name="nous"` and `register_provider(qubit)`.
`rebrand("nous")` returns `"nous"` unchanged (no "hermes" substring), which is exactly
why the audit could not self-suppress it.

## AST HALF-REBRAND CHECK

Scope-aware AST Name-Load checker (bound names resolved through the full enclosing
function chain + module scope + builtins) run over all 21 production files:
**0 unbound Name-Loads.** No half-finished rename present. (A naive per-function
collector shows 135 false hits — all nested closures whose names live in the enclosing
scope. Use the scope-aware variant.)

## DEFERRED — gateway/status.py planned-stop family (NOT short-port)

A prior agent claimed status.py's 6 missing symbols need `qubit_cli/update_pause_record.py`.
The DIRECTIVE is fine but the diagnosis conflated two sets:
  * the 6 reported short-port symbols are rebrand artifacts (proven present above) -> CLOSED
  * `_checkpoint_planned_stop`, `_update_pause_on_disk`, `_PLANNED_STOP_MUTEX_WAIT_S` are
    upstream-NEWER (`base=N head=Y`), i.e. **behind_head**, and DO require
    `update_pause_record._mutex`. `update_pause_record.py`, `update_custody.py`,
    `update_cmd_commit.py`, `anon_challenge.py` are ALL genuinely ABSENT.
    -> OUT OF SHORT-PORT SCOPE. Defer as its own port wave (custody family), do NOT stub.

## METHOD NOTE FOR THE NEXT AGENT

`short_port` in audit_sync_completeness.py over-counts by design: it never applies
`rebrand()`. Before porting any short-port symbol, grep BOTH the upstream spelling AND
`rebrand(...)`; if either hits, it is present. Porting shadows creates duplicate
objects (hazard #1).
