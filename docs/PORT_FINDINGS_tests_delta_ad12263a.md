# Test-tree delta port — findings (upstream ad12263a5ed4, 363 commits past milestone ee8dd6c886)

Worktree `/tmp/qb-f4` branch `f4`. Scope: the 106-file test delta
(`git diff ee8dd6c886 ad12263a5ed4 -- 'tests/*.py'`) + the short-port test files
the audit still lists at the milestone.

## Source-of-truth (confirmed, matches the prior n3 agent's correction)
Absent test files were ported from **upstream `git show ad12263a5ed4:<path>`** and
renamed (the in-tree `qubit_runtime/tests/` vendored copy does NOT contain these
newer files — 0/33 present there). Rename applied from
`docs/archive/2026-09-21/QUBIT_REBRAND_MANIFEST.json` module/class maps plus the
verified in-tree spellings:

    hermes_<mod>      -> qubit_<mod>      (hermes_cli, hermes_state, hermes_yaml, hermes_platform, ...)
    <x>_hermes_home   -> <x>_qubit_home   (write_qubit_home, get_routing_process_qubit_home)
    HermesTokenStorage-> QubitTokenStorage
    HERMES_HOME env   -> QUBIT_HOME       (all OTHER HERMES_* compat vars preserved)
    .hermes paths, HermesMCPOAuthProvider, hermes_* wire tokens -> PRESERVED

**Directory trap (important):** upstream `tests/hermes_cli/` maps to our
`tests/qubit_cli/` (the vendored test DIR was renamed, not just modules). The first
port wrote 7 files to `tests/hermes_cli/`; they were moved to `tests/qubit_cli/`.

## Outcome
- **33 absent whole-file delta files ported** (all symbol-complete).
- **4 short-port present files finished**: test_tool_result_storage (+2 classes),
  test_restart_safe_worker (+4 symbols), test_mcp_oauth_bidirectional (+3 tests),
  test_doctor (+2 tests).
- **4 support files ported**: tests/ci/_gha_expr.py, tests/fixtures/provider_wire_snapshot.json,
  tests/qubit_cli/test_update_pause_record.py, tests/scripts/desktop_update/{legacy_desktop_reader,lineage_rule_cases}.py.
- **test_tui_gateway_server.py FINISHED**: 681 defs vs upstream 679 (symbol-complete).
  Full run: **632 passed / 39 failed**. The single audit "missing" symbol is the
  rebrand FP `test_session_save_writes_under_hermes_home_with_system_prompt`,
  present as `..._qubit_home_...` at line 19473 (proving grep below).
- gate_all.py PASS 4/4 (after every commit). Capability burndown 2123/437 -> 2120/435.

## FALSE POSITIVES REJECTED (with proving grep)
1. `tests/tui_gateway/test_tui_gateway_server.py` audit lists
   `test_session_save_writes_under_hermes_home_with_system_prompt` missing.
   PROVEN present under renamed spelling:
       grep -n 'def test_session_save_writes_under_qubit_home_with_system_prompt' \
         qubit_runtime/tests/tui_gateway/test_tui_gateway_server.py
       -> 19473:def test_session_save_writes_under_qubit_home_with_system_prompt(...)
2. `tests/tools/test_mcp_oauth_bidirectional.py` audit reports "0 missing" (name-level),
   but the file was genuinely short (236 vs 510 lines): 3 tests were absent, caught by
   rename-aware method diff, not by the audit's name compare.

## NEW PRODUCT GAPS exposed (product NOT edited)
Format: file:line — failing assertion — expected vs actual.

### Agent / runtime
- `tests/agent/test_memory_sync_redaction.py:79` — `assert _SECRET not in seen` —
  memory-sync fan-out payloads are NOT redacted; `sk-proj-...`/`opaque...` secrets
  reach the provider. Expected: redacted. (security)
- `tests/agent/test_run_agent_interrupt_hook.py:59` — string equality —
  expected `"Tool execution cancelled. User interrupt"`, actual
  `"Tool execution cancelled by user interrupt"`.
- `tests/agent/test_prompt_cache_prefix_contract.py:109` — `_prefix_breaks(main)` —
  skill-scaffold user message sent as plain string `"content":"..."` on turn 1 but
  structured `"content":[{"text":...}]` on turn 2; prompt-cache prefix bytes change
  mid-loop. Expected: identical shape across the tool loop.
- `tests/agent/transports/test_codex_transport_github_reasoning.py:25` &
  `tests/agent/transports/test_provider_wire_snapshot.py:210` (copilot, base-url:github-models)
  — `reasoning == {"effort":"medium","summary":"auto"}` — actual omits `"summary":"auto"`.

### Cron / worker
- `tests/cron/test_due_scan_save_failure.py` (9 fails) — store-failure degradation
  path unimplemented: `cron.scheduler.note_cron_execution` missing (line 161);
  `cron.jobs.py:2874 AttributeError: 'int' object has no attribute 'get'`;
  `gateway.cron_store_notices` not importable (line 312).
- `tests/cron/test_restart_safe_worker.py` (5 NEW fails after port) —
  `runpy` relaunch never writes the `record` file (line 849, "worker never
  relaunched"); dispatch failure notice resolves no home channel (line 1051,
  `assert [] == ['111111111']`).

### qubit_cli / update custody
- `tests/qubit_cli/test_update_posix_pause.py` (9 fails) — `qubit_cli.update_cmd_posix_pause`
  module absent entirely (`ModuleNotFoundError`).
- `tests/qubit_cli/test_shared_metrics_install_failures.py` (15 fails) — schema
  `qubit_cli/observability/schemas/hermes.shared_metrics.v4.schema.json` missing;
  `shared_metrics_contract.EXTENSION_REGISTRY_IDS` missing; `PluginOperationError()`
  takes no kwargs; `qubit_cli.mcp_config.probe_failure_class` missing;
  `shared_metrics_fields.tagged_failure_class` missing;
  `qubit_cli.skills_hub._registry_from_identifier` missing.
- `tests/qubit_cli/test_shared_metrics_update_stop_reasons.py` (8 fails) — same family.
- `tests/qubit_cli/test_update_custody.py` (9 fails) — update-custody family gaps.
- `tests/qubit_cli/test_copilot_runtime_api_mode_stale.py:19,53` —
  `assert 'codex_responses' == 'chat_completions'` — stale copilot mode not vetoed.
- `tests/qubit_cli/test_web_server_owner_exit.py:99` —
  `qubit_cli.windows_ssh_runtime._lock_record` missing; `should_retire_superseded`
  returns False (line 36).
- `tests/qubit_cli/test_kanban_workflow.py:38` — agent-tool status enum does not
  cover every kanban status.
- `tests/qubit_cli/test_doctor.py` (1 NEW fail) — `qubit doctor` does not report
  mem0 as a catalog plugin not installed; still emits `"Mem0 API key not set"`
  instead of `"mem0 plugin not found" + "plugins install mem0"`.

### Gateway / plugins / pm / e2e
- `tests/gateway/test_cron_fire_startup_gate.py` — `gateway.platforms.api_server_fire_startup`
  not importable (module/attr absent).
- `tests/pm/test_install_states_gc.py` — `pm.install_states` module absent.
- `tests/e2e/core/upgrade/git/test_hostile_pause.py` + `test_hostile_post.py`
  (9+6 errors) — install harness fails: `pm.cli` module absent.
- `tests/plugins/platforms/photon/test_threaded_replies.py` (11 fails) — Photon
  adapter returns placeholder `'[Photon cont...ndled: reply]'` instead of the
  threaded reply text.
- `tests/plugins/test_kanban_workflow_matrix.py:98` — dashboard route 404s where
  200 expected.
- `tests/tools/test_computer_use_zero_delivery_verdict.py:25` — `KeyError: 'recommended'`.
- `tests/tools/test_mcp_oauth_bidirectional.py` (2 NEW fails) —
  `HermesMCPOAuthProvider._maybe_reject_fabricated_registration` absent; fabricated
  DCR registration not rejected before network.
- `tests/hermes_cli/test_shared_metrics_*` etc. as above.
- `tests/tools/test_tool_result_storage.py::TestWriteToSandbox::test_size_probe_decides_lossless
  [heredoc-171-True]` (pre-existing) — `_write_to_sandbox` lossless probe: 171 bytes
  in sandbox, expected 170.

### Dependency gaps (out of test scope — absent production plugins/modules)
- `plugins/model-providers/solstice/` (provider plugin) absent -> makes
  `test_provider_wire_snapshot.py::test_snapshot_covers_every_provider` fail
  ("snapshot entries for providers that no longer exist: ['solstice']").

## test_tui_gateway_server.py — 39 failures (all product-side)
Representative, with cause: `agent.skill_commands` lacks the
`expand_skill_invocation_for_replay` symbol (line 3058); `make_agent` does not
forward `disabled_toolsets` (line 4628 KeyError); project/toolset filter returns
`['coding','figma','project']` vs expected `['coding','figma']` (line 2477);
setup runtime check names the wrong provider (line 9575 `'openrouter' == 'zai'`).
None are test faults; the file is verbatim-portable and complete.

## Divergence (not a product gap) — record, do not chase
- `tests/ci/test_update_ci_routing.py` (97 fails) validates our repo's
  `.github/workflows/*.yml` layout against upstream's expectations. Our fork's CI
  YAML differs; the test grades CI configuration, not product runtime.
