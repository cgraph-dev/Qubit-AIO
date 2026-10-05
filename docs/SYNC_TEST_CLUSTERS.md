## tests/agent failure clustering — 2026-10-05 (sharded run)

Real totals: **263 failed / 9,625 passed / 29 skipped** across 4 shards.
(An earlier single-process run reported 1,236 failed; sharding shows that was
test ORDER pollution, not 1,236 defects.)

### Cluster 1 — FIXED: stale lazy import (largest cluster)
`agent/chat_completion_helpers.py:2186`
    from hermes_cli.observability.shared_metrics_events import record_fallback
`hermes_cli` does not exist (renamed qubit_cli). Every provider fallback raised
`No module named 'hermes_cli'`; the surrounding except swallowed it as
`Outer loop error in API call #N`. Symptoms seen 160+ times across shards and
implicated: test_provider_fallback, test_fallback_api_mode_preservation,
test_primary_runtime_restore, test_provider_reset_cooldown, test_run_agent, ...
Fix: qubit_cli.observability... -> 60 passed on the three main files.
Root cause of the miss: the translation scan did not reach LAZY in-function imports.

### Cluster 2 — OPEN: kanban turn recovery (42 failures)
test_kanban_turn_recovery.py (byte-identical to upstream, 727 lines) fails with
`assert 75 == 1` at :432 — an exit-code mismatch for a rate-limited single query.
Implementation: `qubit_cli/cli_single_query.py:145 _single_query_exit_code`.
This is a genuine behavioral gap, NOT stale-test drift.

### Cluster 3 — OPEN: unclassified remainder
Other failing files (test_relay_runtime_plugins 21, test_run_agent 13,
test_subagent_stop_hook 7, test_reset_aware_primary_restore 6,
test_provider_reset_cooldown 6, test_i18n_layers 6,
test_compression_last_assistant_anchor 6, test_auxiliary_main_first 6, ...)
need individual diagnosis. Several may share Cluster 1's fixed cause and clear
on re-measure.

### Method note
The full-suite number was misleading twice: once from my clusterer dropping
setup-error lines, once from order pollution. Sharding into 4 parallel pytest
processes gave both a stable number and a usable runtime.
