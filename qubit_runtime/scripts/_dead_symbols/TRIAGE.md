# Dead-symbol detector — run on main + 32-symbol triage

Instrument: `qubit_runtime/scripts/check_dead_symbols.py` (rescued from a prunable
scratch dir, made clean-checkout-portable, committed on `feat/dead-symbol-detector`).

## How to reproduce the headline (exact command)

```bash
cd /home/trick/Projects/Repos/qubit-voicebridge
.venv/bin/python qubit_runtime/scripts/check_dead_symbols.py --json /tmp/dead.json -v
```

No scratch path, no `--root`, no `--rev`: the pin is read from
`scripts/frozen_target_progress.py::FROZEN_REV` (via AST, relative to the script)
and the upstream clone resolves to the canonical `~/upstream/hermes-agent`.

Timing on this host: **cold 6m8s, warm 8.3s** (the AST fact pass is ~0.85s per
1k nodes here). Facts are cached by content hash + detector digest under
`<repo>/.dead_symbols_cache` (gitignored). Cold and warm reports are
**byte-identical**: `dead_count=301`, `definitions_examined=446317`,
`owned_files_parsed=9102`, `upstream_files_read=9503`, `excluded_count=7`.

### Headline (current main @ b47686ff2, pin 43faacfb8de7)

```
  owned .py parsed     : 9102
  upstream .py at pin  : 9503
  defs examined        : 446317
  defs upstream never  : 34816  (invented here / renamed -- out of scope)
  DEAD (upstream reads): 301  (5 are test-release seams)
```

313 was the earlier number on an older tree; it is now **301**. The 12 previously-
known dead symbols are **all gone** — the wiring wave holds:

`_persisted_turn`, `_endpoint_owner`, `_needs_extra`, `_HOME_TARGET_ENV_VARS`,
`_watch_notification_metadata`, `_trial_claimable`, `_claim_launch_home`,
`_profile_default_cwd`, `_is_other_profiles_cwd`, `_finite_viewer_turn`,
`_settle_api_runtime_lock`, `_NoStreamConsumer` — **none reported**.

### Top 20 modules by dead count (201 modules affected, 301 total)

| module | dead | | module | dead |
|---|--:|---|---|--:|
| gateway/session_api_turn.py | 8 | | qubit_cli/anon_auth.py | 4 |
| gateway/run_runtime.py | 7 | | qubit_cli/observability/shared_metrics_connectors.py | 4 |
| scripts/releases/lanes.py | 7 | | scripts/releases/entrypoint.py | 4 |
| gateway/platforms/api_server_runs.py | 6 | | tui_gateway/methods_onboarding.py | 4 |
| scripts/releases/stable.py | 5 | | acp_adapter/gateway_server.py | 3 |
| tools/process_registry_output_log.py | 5 | | agent/compaction_events.py | 3 |
| agent/conversation_compression_telemetry.py | 3 | | gateway/session_cli_turn.py | 3 |
| gateway/platforms/api_server_bot_chat.py | 3 | | gateway/session_ingress_burst.py | 3 |
| gateway/platforms/api_server_openai_routes.py | 3 | | gateway/session_setting_commands.py | 3 |
| plugins/platforms/a2a/protocol.py | 3 | | qubit_cli/backup.py | 3 |

By top-level area: gateway 91, qubit_cli 73, agent 34, tools 19, scripts 18,
tui_gateway 18, tests 13, plugins 11, cron 8, acp_adapter 3, pm 3, top-level
`qubit_state_*.py` 8, `cli.py` 1, `qubit_logging.py` 1.

## Method for the triage

Two independent passes, both written for this triage (not the detector's own
code), so agreement is not circular:

1. **AST re-scan.** Parse every owned `.py`, strip docstrings, and count
   `Name(Load)`, `Attribute`, `Import/ImportFrom` bindings and
   `getattr/setattr/patch/delattr` string names. Result: **all 301 have ZERO AST
   uses** — the detector's central claim reproduces exactly under a separate
   implementation.
2. **Mechanism check.** For each sampled symbol, search the raw tree for prose vs
   code references, quoted-name tables, pytest-fixture parameter requests, and
   pytest test classes, then check the upstream side for where the counterpart is
   *called from*.

Verdicts are evidence-based, per symbol. The number was **not** tuned down.

## 32-symbol triage

| # | symbol | module | verdict | evidence |
|--:|---|---|---|---|
| 1 | `_stage_user_content` | acp_adapter/gateway_server.py | GENUINE DEAD | only its def here; upstream calls it at `gateway_server.py:558` + tests |
| 2 | `_submit_fingerprint` | acp_adapter/gateway_server.py | GENUINE DEAD | only its def; upstream awaits it at `gateway_server.py:576` |
| 3 | `GatewayACPAgent` | acp_adapter/gateway_server.py | GENUINE DEAD | only a docstring mention in `gateway_reply.py:2`; upstream instantiates it at `server.py:257` (55 uses) |
| 4 | `_is_pre_thinking_haiku` | agent/anthropic_adapter.py | GENUINE DEAD | sole def; upstream calls it at `anthropic_adapter.py:629` |
| 5 | `is_native_anthropic_host` | agent/anthropic_credentials.py | GENUINE DEAD | imported+called by upstream `anthropic_adapter.py:19,418` |
| 6 | `session_auxiliary_scope` | agent/auxiliary_task_config.py | GENUINE DEAD | upstream calls it in `gateway/session_policy.py:841-845` |
| 7 | `relay_chat_metadata` | agent/auxiliary_wire.py | GENUINE DEAD | upstream calls it in `agent/auxiliary_client.py:2651` |
| 8 | `is_openai_bedrock_model` | agent/bedrock_adapter.py | GENUINE DEAD | upstream test+code call it (`test_bedrock_adapter.py:1080`) |
| 9 | `codex_request_compression_hooks` | agent/codex_headers.py | GENUINE DEAD | upstream calls it in `agent/process_bootstrap.py:297` |
| 10 | `strip_unverified_reasoning_items` | agent/codex_responses_adapter.py | GENUINE DEAD | upstream calls it in `agent/transports/codex.py:663` |
| 11 | `remember_catalog` | agent/codex_service_tiers.py | GENUINE DEAD | upstream calls it in `agent/model_metadata.py:1882` |
| 12 | `negotiate_request_tier` | agent/codex_service_tiers.py | GENUINE DEAD | upstream calls it in `agent/fast_mode.py:92` |
| 13 | `publish_micro` | agent/compaction_events.py | GENUINE DEAD | upstream calls it in `agent/micro_compaction.py:377` |
| 14 | `publish_prune` | agent/compaction_events.py | GENUINE DEAD | upstream calls it in `agent/turn_preflight.py:390` |
| 15 | `publish_provider_native` | agent/compaction_events.py | GENUINE DEAD | upstream calls it in `agent/codex_runtime.py:257` |
| 16 | `carry_compression_automation` | agent/compression_automation.py | GENUINE DEAD | upstream calls it in `agent/conversation_compression.py:3276` |
| 17 | `is_demoted_stub` | agent/context_compressor_tail.py | GENUINE DEAD | upstream imports+calls it from `context_compressor.py:37,836` |
| 18 | `give_up_execution` | cron/executions.py | GENUINE DEAD | upstream calls it from `cron/scheduler.py` (2 sites) |
| 19 | `_fire_secret_scope` | cron/scheduler.py | GENUINE DEAD | sole real ref is its def (`_install_`/`_reset_fire_secret_scope` are different live names); upstream calls it in `cron/scheduler.py` |
| 20 | `_normalize_newlines` | cron/scheduler.py | GENUINE DEAD | only its def; upstream calls it from `cron/scheduler.py` |
| 21 | `run_canonical_job` | cron/scheduler_authority.py | GENUINE DEAD | only a docstring mention in `scheduler_gateway_gate.py:3`; upstream uses it 15× |
| 22 | `_prepare_deferred_agent_startup` | cli.py | GENUINE DEAD | only a comment in `cli_agent_setup_mixin.py:636`; upstream calls it from `hermes_cli/cli_agent_setup_mixin.py` + tests |
| 23 | `admit_api_turn` | gateway/session_api_turn.py | GENUINE DEAD | only a docstring mention in the same file:160; upstream uses it 58× |
| 24 | `run_api_turn` | gateway/session_api_turn.py | GENUINE DEAD | only a docstring mention in `api_server_bot_chat.py:8`; upstream calls it from another file |
| 25 | `recover_api_turns` | gateway/session_api_turn.py | GENUINE DEAD | only a docstring mention in `run_runtime.py:375`; upstream calls it |
| 26 | `_import_members` | qubit_cli/backup.py | GENUINE DEAD | only a docstring mention at `backup.py:725`; upstream calls it |
| 27 | `handoffs` | scripts/releases/lanes.py | GENUINE DEAD | 84 "refs" are all unrelated prose (compaction/kanban handoffs); no call to `lanes.handoffs()`; upstream calls it from `entrypoint.py` |
| 28 | `loopback_app` | tests/qubit_cli/test_dashboard_auth_ws_auth.py | **FALSE POSITIVE** | pytest fixture: requested as a parameter at `:208,:390,:503` in the same file |
| 29 | `existing_install` | tests/qubit_cli/test_setup_reconfigure.py | **FALSE POSITIVE** | pytest fixture, requested at `:103,:137`; also shadows the production `install_state.existing_install` |
| 30 | `cron_home` | tests/qubit_cli/test_update_stale_gateway_yield.py | **FALSE POSITIVE** | pytest fixture, requested at `:37,:55,:67` |
| 31 | `TestRateLimit` | tests/qubit_cli/test_dashboard_auth_password_login.py | **FALSE POSITIVE** | pytest test class — collected by name, never called |
| 32 | `launch_supervisor` | gateway/control_socket.py | **FALSE POSITIVE** | read via a string key `for key in ("launch_supervisor", "supervisor")` at `:157` and emitted at `:173` |

### Mechanisms seen (the false-positive taxonomy)

* **pytest fixtures** — collected/requested by parameter name, never called
  (`loopback_app`, `existing_install`, `cron_home`, `_seed_stores`, `legacy_digests`).
* **pytest test classes** — collected by `pytest` by name (`TestRateLimit`,
  `TestPatchReplace`, `TestPasswordLoginRoute`).
* **string-bound dict keys / `getattr`** — the symbol is a value in a dispatch
  table (`launch_supervisor`, `drop_completion_delivery`, `completion_admission`).
* **plugin/mixin registration hooks** — `TelegramHeldInboundMixin` appears in a
  registration table.

The detector already tags pyttest **fixtures** and `http-handler` methods as
`seam_class` (5 reported), but a **test class**, a **dict-key** and a
**mixin-registered-on-a-subclass** fall through, because they have no name-level
call site either. Those are the honest false positives.

## Measured false-positive rate

Of the **32** symbols triaged, **5 are FALSE POSITIVE** and **27 GENUINE DEAD**.

* **Measured FP rate on this sample: 5/32 = 15.6%.**
* Applied to the whole 301, that implies roughly **45–50** false positives and
  on the order of **250 genuine gaps** — a lot, but the 313 → 301 drop and the
  clean 12-symbol check confirm the instrument reports real asymmetry, not noise.

The FP rate is *not* zero and the detector should not be treated as a hard
fail-gate until the test-class / string-table / mixin mechanisms are either
tagged as seams or the gate is run in report-only mode.

## Negative control — the detector is falsifiable

A detector that never fires proves nothing, so the control injects a **fresh,
uniquely-named** definition with exactly one call site into a scratch copy of the
tree, runs the detector, then deletes the call site and re-runs:

```bash
# 1. copy the tree to scratch, add a def + one call site
#    qubit_runtime/_negctl_target.py:  def _abort_at_the_commit_fence(reason): ...
#    qubit_runtime/_negctl_caller.py:  from _negctl_target import _abort_at_the_commit_fence
#                                      def use_it(): return _abort_at_the_commit_fence("smoke")
# 2. run with --root <scratch>
#    -> DEAD (upstream reads): 301   (symbol NOT flagged: it has a call site)
# 3. delete the call, re-run
#    -> DEAD (upstream reads): 302
#       _abort_at_the_commit_fence   _negctl_target.py   1   4
```

Deleting the single call site flips the count 301 → 302 and the symbol appears in
the list with its upstream read count. `_abort_at_the_commit_fence` was chosen
because it is read upstream (`tests/agent/test_compression_attempt_effect.py`)
and absent from our tree, so the only variable is the call site.

An earlier attempt at this control used `_claim_launch_home` and *failed to
flag it* — because a probe file (`scripts/_probe_laneA_dead_symbols.py`) still
reads the symbol. That was a contaminated control, not an instrument defect, and
is recorded here because it is exactly the trap a negative control exists to
catch.

## Wave 2 verdicts — CORRECTLY UNWIRED (do not "fix" these by wiring)

The detector reports a symbol whose *name* has no call site. A renamed sibling
that already does the job is not a gap, and wiring the upstream spelling
alongside it can be actively harmful. Each entry below was checked by reading
the upstream call site and the local sibling.

| symbol | module | verdict | evidence |
|---|---|---|---|
| `_replay_unanswered` | tools/bot_relay.py | **CORRECTLY UNWIRED** | Upstream re-offers `claimed/` envelopes unanswered on EVERY drain (bot_relay.py:358 merges them into the fresh rows). This fork's `_reoffer_unanswered` (bot_relay.py:430) is the same job with a *bound* upstream lacks: it re-offers at most once per envelope (`reoffered_at`), after `REOFFER_AFTER_SECONDS`, and expires against the outbox TTL. Wiring the upstream pass as well would hand out every unanswered envelope twice per drain — a double delivery of a DM, which is the bug the bound exists to prevent. `drain_outbox` already calls the local sibling at bot_relay.py:355. |
| `admit_message` | gateway/session_ingress.py | **CORRECTLY UNWIRED (for now) — architectural gap, not an alias** | Upstream reaches it from `run_inbound._hm_authority_admission`, a method this fork does not have: native messaging ingress runs `_hm_admit_event` (gateway/run_inbound.py:205) straight to the in-process path, and no native path calls `authority.admit_native` (only `platforms/webhook_ingress.py` and `admit_message` itself do). Making the symbol live means porting the whole `_hm_authority_admission` ordering (`_hm_busy_preempt`, `_hm_post_admission_consume`, `executing_admission` semantics) — a partial port would double-admit or drop messages. Recorded so the next wave starts from the real shape instead of adding one call. |

Wave 2 also corrected two stale fork-side copies rather than wiring new paths:

* `cron/scheduler._finish_interrupted_run` was a private duplicate of
  `cron/scheduler_bookkeeping.finish_interrupted_run` (verified identical); it
  now delegates, so the upstream symbol is live and there is one implementation.
* `plugins/platforms/telegram/adapter.py` carried inlined copies of the held-inbound
  methods but never inherited `TelegramHeldInboundMixin`; the class now inherits
  it (adding `adopt_held_inbound` / `_adopt_held_event`) and the inlined copies
  are gone.

The three secondary-profile `hand_over_held_inbound` sites (upstream
run_adapters.py:1458,1484,1502) stay unwired: this fork's secondary reconnect
carries `inbound_dedup_caches()` rather than the predecessor adapter, so there
is no held queue in hand at that site.

## Wave 5 (branch `fix/dead-symbols-wave5`) — measured before/after

Integrated the unmerged `fix/dead-symbols-tranche3` (6 commits) and triaged the
remainder. Commands, from the worktree root:

```bash
.venv/bin/python qubit_runtime/scripts/check_dead_symbols.py --json /tmp/dead-wave5.json -v
```

| state | dead_count |
|---|--:|
| main `6e70aea31` (owner-reported) | 251 |
| branch after the tranche3 cherry-picks | 241 |
| branch after the wave-5 batches | **224** |

The 17 symbols resolved by the wave-5 batches were diffed against the
before-file (0 new): `publish_micro`, `publish_prune`, `publish_provider_native`,
`carry_compression_automation`, `drop_context_cache_rows`, `is_demoted_stub`,
`demote_stale_tail_tools`, `observe_tool_result`, `keep_after_turn_budget`,
`stage_skills_index_note`, `outage_covers`, `_fire_secret_scope`,
`boundary_note`, `current_session_tool_scope`, `streaming_available`,
`_llamacpp_context` (deleted), `_normalize_newlines` (deleted).

### Wave 5 triage (33 symbols, not covered above)

| # | symbol | module | verdict | evidence |
|--:|---|---|---|---|
| 1 | `publish_micro` | agent/compaction_events.py | GENUINE DEAD → WIRED | upstream `agent/micro_compaction.py:377`; local `_emit_micro_compaction_telemetry` emitted the JSON line with no publish |
| 2 | `publish_prune` | agent/compaction_events.py | GENUINE DEAD → WIRED | upstream `agent/turn_preflight.py:390`; local proactive-prune commit had no publish |
| 3 | `publish_provider_native` | agent/compaction_events.py | GENUINE DEAD → WIRED | upstream `agent/codex_runtime.py:257`; local `_record_codex_app_server_compaction` had no publish |
| 4 | `carry_compression_automation` | agent/compression_automation.py | GENUINE DEAD → WIRED | upstream `agent/conversation_compression.py:3275`; local `_carry_session_state_to_child` inlined the 3 migrations and lost the `RuntimeSessionStore` guard |
| 5 | `drop_context_cache_rows` | agent/model_metadata_cache.py | GENUINE DEAD → WIRED | upstream `agent/model_metadata.py:1199,1212`; local `_invalidate_cached_context_length` inlined the write without the cross-process lock |
| 6 | `is_demoted_stub` | agent/context_compressor_tail.py | GENUINE DEAD → WIRED | upstream `agent/context_compressor.py:836,895` |
| 7 | `demote_stale_tail_tools` | agent/context_compressor_tail.py | GENUINE DEAD → WIRED | upstream `agent/context_compressor.py:3662`; local method was a weaker inline copy (no skill reload marker) |
| 8 | `observe_tool_result` | agent/onboarding_record.py | GENUINE DEAD → WIRED | upstream `agent/tool_executor.py:1095` (as `observe_onboarding_result`) |
| 9 | `keep_after_turn_budget` | agent/plugin_live_notes.py | GENUINE DEAD → WIRED | upstream `agent/tool_executor.py:1217` |
| 10 | `stage_skills_index_note` | agent/skills_index_delta.py | GENUINE DEAD → WIRED | upstream `agent/turn_context.py:1180`; local `build_turn_context` never called it |
| 11 | `outage_covers` | cron/store_health.py | GENUINE DEAD → WIRED | upstream `cron/jobs.py:3139`; local `_retire_expired_oneshot` retired an outage-skipped one-shot as missed |
| 12 | `_fire_secret_scope` | cron/scheduler.py | GENUINE DEAD → WIRED | upstream `cron/scheduler.py:2855`; local crash path called `_deliver_crash_failure` without the scope |
| 13 | `boundary_note` | tools/fuzzy_match.py | GENUINE DEAD → WIRED | upstream `tools/file_operations.py:1621,1642`; local patch-replace returned no approximate-match note |
| 14 | `current_session_tool_scope` | tools/registry.py | GENUINE DEAD → WIRED | upstream `tools/mcp_tool.py:700`; local `_mcp_registry_scope` skipped the session scope |
| 15 | `streaming_available` | tools/transcription_streaming.py | GENUINE DEAD → WIRED | upstream `tools/voice_client_config.py:250`; local config resolver omitted the STT streaming verdict |
| 16 | `_llamacpp_context` | agent/model_metadata.py | GENUINE DEAD → DELETED | superseded by `agent/model_metadata_llamacpp.py::llamacpp_context` (live probe table calls it); the in-file copy had no call site |
| 17 | `_normalize_newlines` | cron/scheduler.py | GENUINE DEAD → DELETED | fork's run-doc framing diverged (no `_PROMPT_FRAME`), so its only upstream call site does not exist; unused |
| 18 | `carrier_record` | agent/reasoning_carriers.py | GENUINE DEAD (deferred) | upstream `agent/chat_completion_helpers.py:1764`; local `build_assistant_message` preserves SDK `reasoning_details` but never captures Gemini thought_signature / Copilot reasoning_opaque |
| 19 | `StreamCarriers` | agent/reasoning_carriers.py | GENUINE DEAD (deferred) | upstream `agent/chat_completion_helpers.py:3050`; local stream accumulator does not feed it |
| 20 | `run_canonical_job` | cron/scheduler_authority.py | GENUINE DEAD (deferred) | upstream `cron/scheduler.py:2578`; local `scheduler.py` has no reference to `cron.scheduler_authority` at all — the whole authority transport is unported |
| 21 | `pause_for_unknown` | cron/scheduler_authority.py | GENUINE DEAD (deferred) | upstream `cron/scheduler.py:3425`; same authority transport gap |
| 22 | `external_worker_exited_reason` | cron/scheduler_worker_failure.py | GENUINE DEAD (deferred) | upstream `cron/scheduler.py:3551`; local worker-exit path diverges |
| 23 | `give_up_execution` | cron/executions.py | GENUINE DEAD (deferred) | upstream `cron/scheduler.py:3424`; local executor path diverges |
| 24 | `worker_output_log` + `open_output_log` + `record_exit_command` + `adoptable_log` + `ProcessOutputLogMixin` | tools/process_registry_output_log.py | GENUINE DEAD (deferred) | upstream `tools/process_registry.py:37,674,1195,1214,1224`; local `ProcessRegistry` does not inherit `ProcessOutputLogMixin` and the whole module is unreferenced |
| 25 | `_session_slot_key` | tools/registry.py | GENUINE DEAD (deferred) | upstream `tools/registry.py:510,546`; local registry keys overlays by `qubit_home_key` and never reads the session slot |
| 26 | `GatewayInboundHooksMixin` | gateway/run_inbound_hooks.py | GENUINE DEAD (deferred) | upstream `gateway/run_inbound.py:71`; local `GatewayInboundMixin` inlines the hook methods and does not inherit the mixin (same shape as the Wave-2 telegram fix) |
| 27 | `GatewayPluginInjectionMixin` | gateway/run_plugin_injection.py | GENUINE DEAD (deferred) | upstream `gateway/run_inbound.py:71`; same inline-vs-inherit divergence |
| 28 | `relay_chat_metadata` | agent/auxiliary_wire.py | GENUINE DEAD (deferred) | upstream `agent/auxiliary_client.py:2627,2651`; local auxiliary client does not wire relay metadata |
| 29 | `_emit_blocked_attempt_telemetry` (and `_snapshot_compression_effect_input`, `_record_committed_attempt_effect`) | agent/conversation_compression_telemetry.py | GENUINE DEAD (deferred) | upstream `agent/conversation_compression.py:4163,4234,4255,4344`; local facade lacks those call sites |
| 30 | `CatalogApproved` / `CatalogServerError` | tui_gateway/contracts/connectors_operation.py | GENUINE DEAD (deferred) | upstream `connectors_operation.py:163,167` use them as `CatalogRow` fields; local `CatalogRow` dropped `approved`/`server_errors` along with `enabled`/`missing_env`/`already_installed` |
| 31 | `OnboardingStateParams` | tui_gateway/contracts/profiles_vault_complete_foreign_subagents.py | CORRECTLY UNWIRED | the file's own comment (`:379`) records the deliberate replacement by Qubit's setup-profile state; `onboarding.state` is registered with `Params` at `:521` |
| 32 | `explicit_static_config` | tests/agent/test_relay_runtime_plugins.py | FALSE POSITIVE | pytest fixture requested as a parameter at `:235,250,273,318,335,454,503,518,758` |
| 33 | `TestTerminalResultsCarryTheFreeTierBlock` | tests/agent/test_welcome_tier_recovery.py | FALSE POSITIVE | pytest test class collected by name (`:198`), never called |

Additional test-only genuine-dead noted, not wired: `_metric`
(`tests/monitoring/test_cron_health_export.py:5`, defined, never called),
`TestPatchReplace` (`tests/tools/test_file_tools_live.py:133`, test class — FP).


## Wave 6 (branch `fix/dead-symbols-wave6`, based on main `0b5ebc401`)

Measured with the documented command (from the repo root):

```bash
.venv/bin/python qubit_runtime/scripts/check_dead_symbols.py --json /tmp/dead-wave6-final.json -v
```

| state | dead_count |
|---|---:|
| main `0b5ebc401` | 216 |
| branch after 9 batches | **170** |

**46 symbols resolved, 0 new** (set diff of `/tmp/dead-wave6.json` vs
`/tmp/dead-wave6-final.json`). One of the 13 deferred architectural ports was taken
end-to-end: the **worker output-log family** (`worker_output_log`, `open_output_log`,
`record_exit_command`, `adoptable_log`, `ProcessOutputLogMixin`) plus the
`ProcessSession.output_log` field, checkpoint merge/adoption and the log reader.
No symbol was deleted, so **no Declared-exclusions entry was added**.

### Wired in this wave (46)

| # | symbol | module | upstream call site (pin) |
|--:|---|---|---|
| 1 | `ProcessOutputLogMixin` | tools/process_registry_output_log.py | `tools/process_registry.py:674` (base class) |
| 2 | `worker_output_log` | tools/process_registry_output_log.py | `tools/process_registry.py:1195` |
| 3 | `open_output_log` | tools/process_registry_output_log.py | `tools/process_registry.py:1224` |
| 4 | `record_exit_command` | tools/process_registry_output_log.py | `tools/process_registry.py:1214` |
| 5 | `adoptable_log` | tools/process_registry_output_log.py | `tools/process_registry_checkpoint.py:62,79,113` |
| 6 | `_bot_chat_tip` | tools/bot_live_delivery.py | same file `:59`; local `find_canonical_owner` duplicated it |
| 7 | `_freeze_params` | tui_gateway/event_replay.py | same file `:81`; the local ring stored live dicts |
| 8 | `_thaw_params` | tui_gateway/event_replay.py | same file `:141` |
| 9 | `_is_bytecode_compile_failure` | pm/environment.py | same file `:354,429`; `LiveTail.discard` added |
| 10 | `_keep_after_sweep` | qubit_cli/debug.py | same file `:101`; `_pending_lock` added |
| 11 | `_parse_chat_messages` | gateway/platforms/api_server_openai_routes.py | same file `:834`; handler inlined a copy |
| 12 | `_responses_fingerprint_keys` | gateway/platforms/api_server_openai_routes.py | same file `:1358` |
| 13 | `_session_answering_clients` | tui_gateway/session_transports.py | `tui_gateway/server.py:734` (`bind_sinks` 4th sink) |
| 14 | `_session_slot_key` | tools/registry.py | same file `:510,546` (implicit-scope reads) |
| 15 | `_terminal_env` | tools/environments/local.py | `tools/process_registry.py:1133` (`_spawn_env`) |
| 16 | `_todo_state_from_db` | tui_gateway/tool_progress.py | `tui_gateway/methods_session.py:951` (deferred resume) |
| 17 | `_tui_model_friction` | tui_gateway/methods_tools.py | same file `:771,870`; `methods_session.py:2037` |
| 18 | `active_sink` | gateway/session_command_sink.py | `gateway/run_busy.py:945`, `gateway/slash_commands_goals.py:80` |
| 19 | `api_storage_source` | gateway/session_api.py | `gateway/session_recovery.py:334` |
| 20 | `background_counts` | qubit_cli/gateway_tools.py | `qubit_cli/cli_status_bar_mixin.py:277` |
| 21 | `bound_interpreter_gone` | qubit_cli/kanban_owner_recovery.py | `qubit_cli/kanban_db_dispatch.py:1185` |
| 22 | `chat_catalog_ids` | qubit_cli/chat_catalog.py | `providers/base.py:430`; local listed ids without filtering |
| 23 | `clear_restart_failure_count` | gateway/stuck_loop_counts.py | `gateway/run_shutdown.py:1355` |
| 24 | `connection_profile_home` | tui_gateway/ws_legacy_fallback.py | `tui_gateway/methods_complete.py:45`, `server.py:644` |
| 25 | `create_overrides` | tui_gateway/methods_session_model_guard.py | `tui_gateway/methods_session.py:401` |
| 26 | `edge_block` | pm/network.py | `pm/downloader.py:140`, `qubit_cli/debug.py:626` |
| 27 | `increment_restart_failure_counts` | gateway/stuck_loop_counts.py | `gateway/run_shutdown.py:1318` |
| 28 | `legacy_plugin_alias` | qubit_cli/left_core_migration.py | `qubit_cli/plugins_cmd.py:676` |
| 29 | `legacy_plugin_keys` | qubit_cli/left_core_migration.py | `qubit_cli/plugins_cmd.py:558`, `plugins_discovery.py:264` |
| 30 | `local_liveness_pid` | gateway/status_served.py | `qubit_cli/web_routers/status.py:326` |
| 31 | `managed_run_exit_code` | qubit_cli/kanban_owner_recovery.py | `qubit_cli/kanban_db_dispatch.py:1190` |
| 32 | `moved_plugin_hint` | qubit_cli/left_core_migration.py | `qubit_cli/plugins_cmd.py:466` |
| 33 | `open_restart_wait` | gateway/drain_control.py | `gateway/run_shutdown.py:1687` |
| 34 | `owner_reclaim_paused` | qubit_cli/kanban_owner_recovery.py | `qubit_cli/kanban_db_dispatch.py:1185` + 4 more |
| 35 | `owns_user_path` | qubit_cli/_launchers.py | `qubit_cli/_install_repair.py:305` |
| 36 | `record_terminal_failure` | gateway/session_results.py | `gateway/run_turn.py:1622` |
| 37 | `redact_detached_stdio` | qubit_logging.py | `qubit_cli/gateway.py:4586` |
| 38 | `runtime_downgrade_watcher` | gateway/runtime_downgrade_exit.py | `gateway/run_startup.py:1595` |
| 39 | `say_queue_lock` | plugins/google_meet/_jsonfile.py | `node/server.py:48`, `process_manager.py:178`, `realtime/openai_client.py:194` |
| 40 | `selected_session_db` | gateway/platforms/api_server_store.py | `gateway/platforms/api_server.py:1798,1843,1859` |
| 41 | `served_profile_runtime` | gateway/status_served.py | `qubit_cli/web_routers/status.py:334`, `messaging.py:305` |
| 42 | `session_tools` | qubit_cli/gateway_tools.py | `qubit_cli/cli_info_mixin.py:406` |
| 43 | `skip_deep_merge` | qubit_cli/moa_config.py | `qubit_cli/config.py:912` |
| 44 | `theme_request` | tui_gateway/theme_bridge.py | `tui_gateway/agent_callbacks.py:143,183` |
| 45 | `turn_references` | gateway/session_context_refs.py | `gateway/run_inbound.py:1651` |
| 46 | `viewer_driven` | gateway/session_viewer_automation.py | `gateway/run_goals.py:351` |

### Triage of remaining symbols (not wired)

| symbol | module | verdict | evidence |
|---|---|---|---|
| `completion_admission` | gateway/session_automation.py | **FALSE POSITIVE** | live as a JSON metadata key: `qubit_state_detached_results.py:37-38` stores/reads `display_metadata.completion_admission` |
| `drop_completion_delivery` | tools/async_delegation.py | **FALSE POSITIVE** | value in `gateway/run_notifications.py:88` `_DURABLE_CLAIM_OPS["drop"]`, invoked via `getattr(_ad, fn_name)` at `:1501` |
| `launch_supervisor` | gateway/control_socket.py | **FALSE POSITIVE** | read via string key `for key in ("launch_supervisor", "supervisor")` `:157`, emitted `:173` (Wave 1 taxonomy) |
| `gateway_spawn_intent_subcommand` | gateway/status.py | **FALSE POSITIVE** | imported as `spawn_intent` and called at `tests/gateway/test_gateway_command_line_matcher.py:129-149` |
| `loopback_app`, `_seed_stores`, `legacy_digests`, `cron_home`, `existing_install`, `explicit_static_config` | tests/** | **FALSE POSITIVE** | pytest fixtures requested by parameter name; never called |
| `TestRateLimit`, `TestPasswordLoginRoute`, `TestPatchReplace`, `TestTerminalResultsCarryTheFreeTierBlock` | tests/** | **FALSE POSITIVE** | pytest test classes collected by name; never called |
| `_metric` | tests/monitoring/test_cron_health_export.py | test-only dead | defined at `:5`, no caller in any test; no product surface |
| `has_pending` | tools/clarify_gateway.py | **CORRECTLY UNWIRED** | upstream product code never calls it (only `tests/tools/test_clarify_gateway.py`); the session path reads `_entries` directly |
| `snapshot_native` | gateway/session_envelope.py | **CORRECTLY UNWIRED** | upstream product code uses `prepare_native`→`_snapshot_native`; only tests/fixtures call the wrapper |
| `_replay_unanswered` | tools/bot_relay.py | **CORRECTLY UNWIRED** | Wave 2: local `_reoffer_unanswered` is the same job with a once-per-envelope bound; wiring both double-delivers |
| `OnboardingStateParams`, `_setup_beat_request`, `_carry_onboarding_toolset`, `_onboarding_prelude`, `_stage_onboarding_turn_note` | tui_gateway/** | **CORRECTLY UNWIRED** | the local onboarding flow is the `setup_choose` replacement (`agent_callbacks.py:187`), documented at `OnboardingStateParams`' own comment |
| `admit_message` | gateway/session_ingress.py | **DEFERRED** (Wave 2) | needs `_hm_authority_admission` ordering (`_hm_busy_preempt`, `_hm_post_admission_consume`, `executing_admission`); a partial port double-admits |
| `admit_route_wake` | gateway/session_automation.py | **DEFERRED** | upstream goal/loop/kanban wake admission (`run_goals.py:331,491`, `wake.py:105`); local `_enqueue_fifo` path is a different (pre-admission) mechanism |
| `require_ledger_owner` | tools/async_delegation_worker.py | **DEFERRED** | blocker is the whole worker-ledger routing set: `_persist_dispatch` lacks the `worker_ledger().dispatch` branch and 6 delivery transitions still write SQLite directly |
| `CompressionTelemetryMixin` | agent/context_compressor_telemetry.py | **DEFERRED** | `ContextCompressor` has 3 of its 6 methods inlined; mixing in without deleting them would leave the stale copies authoritative and change telemetry fields |
| `GatewayInboundHooksMixin`, `GatewayPluginInjectionMixin` | gateway/run_inbound*.py | **DEFERRED** | `GatewayInboundMixin` inlines the hook methods; needs the Wave-2 telegram-style "inherit + delete inlined copies" refactor over ~1.4k lines |
| `GatewayRuntimeInitMixin`, `GatewayTurnPrepareMixin`, `GatewayTurnHygieneMixin`, `GatewayTurnPersistenceMixin` | gateway/run_*.py | **DEFERRED** | same inline-vs-inherit split; each mixin's methods exist as local copies and must be deleted in one verified move |
| `carrier_record`, `StreamCarriers` | agent/reasoning_carriers.py | **DEFERRED** (Wave 5) | local stream accumulator does not capture Gemini thought_signature / Copilot reasoning_opaque |
| `run_canonical_job`, `pause_for_unknown` | cron/scheduler_authority.py | **DEFERRED** (Wave 5) | the authority transport is unported: `cron/scheduler.py` has no reference to `cron.scheduler_authority` |
| `give_up_execution`, `external_worker_exited_reason` | cron/** | **DEFERRED** (Wave 5) | local executor/worker-exit paths diverge from upstream |
| `message_context_id`, `extract_message_id`, `logged_input` | plugins/platforms/a2a/protocol.py | **DEFERRED** | upstream's retry admission (`_note_forwarded_input`, `logged_input`, `rec.input_id`) is absent from the local adapter; context naming alone would not make retries safe |
| `prepare_context`, `identify_output`, `_response_identity` | gateway/platforms/api_server_response_*.py | **DEFERRED** | the durable Responses admission (`durable_key`, request fingerprint, replay) is absent from the local `/v1/responses` handler |
| `_handle_run_clarify`, `_handle_resolve_unknown_run`, `_steer_refused`, `_run_idempotency_identity`, `_run_user_message`, `_admit_run_to_authority` | gateway/platforms/api_server_runs.py | **DEFERRED** | local `_http_routes` does not register the clarify/resolve-unknown routes and the run handler inlines a different admission path |
| `bind_api_session`, `admit_api_turn`, `run_api_turn`, `recover_api_turns`, `owns_api_run` | gateway/session_api*.py | **DEFERRED** | the API-turn authority flow is only partially ported; wiring one entry point would not make the transaction complete |
| `_session_slot_key` neighbours are not listed here; remaining 170 are the measured residual | — | — | re-run the detector; this table covers 46 wired + ~55 triaged-not-wired (grouped rows expand to more symbols) |

### Focused verification for the wave

| batch | command (cwd `qubit_runtime/`) | result |
|---|---|---|
| 1 | `pytest -q tests/tui_gateway/test_tui_gateway_event_replay.py tests/providers/test_fetch_models_base_url.py tests/tools/test_bot_live_owner_delivery.py tests/qubit_cli/test_moa_config.py` | 45 passed |
| 1 | `pytest -q tests/pm/test_environment_build.py tests/pm/test_progress.py tests/qubit_cli/test_config.py tests/qubit_cli/test_moa_preset_config_warnings.py` | 195 passed, 21 skipped, 1 pre-existing fail (baseline `test_upgrades_pre_rewrite_default_soul_md`) |
| 2 | `pytest -q tests/gateway/test_stuck_loop.py tests/gateway/test_context_ref_expansion_runtime.py tests/gateway/test_local_viewer_prompt_cluster.py tests/gateway/test_empty_sentinel_rewrite_copy.py` | 11 passed |
| 2 | `pytest -q tests/gateway/test_api_server_bind_guard.py tests/gateway/test_busy_command.py tests/gateway/test_gateway_command_dispatch_minimal.py` | 16 passed, 1 skipped |
| 3 | `pytest -q tests/plugins/test_google_meet_node.py tests/plugins/test_google_meet_plugin.py tests/plugins/test_google_meet_realtime.py tests/qubit_cli/test_debug.py tests/tools/test_registry.py tests/gateway/test_goal_continuation_drain.py tests/gateway/test_goal_verdict_send.py` | 126 passed |
| 3 | `pytest -q tests/pm/test_downloader.py tests/pm/test_downloader_integrity.py tests/pm/test_download_retry_limits.py tests/qubit_cli/test_device_poll_waf_backoff.py` | 49 passed |
| 3 | `pytest -q tests/qubit_cli/test_served_profile_mirror_platforms.py tests/qubit_cli/test_gateway_multiplex_served_record.py tests/qubit_cli/test_web_server_messaging_stale_runtime.py` | 24 passed |
| 3 | `pytest -q tests/qubit_cli/test_debug.py tests/qubit_cli/test_debug_share_update_logs.py tests/qubit_cli/test_ensure_windows_bin_launchers.py tests/qubit_cli/test_legacy_launchers_windows_live.py` | 66 passed, 20 skipped |
| worker port | `pytest -q tests/tools/test_process_checkpoint_worker_handoff.py` | 2 passed (new: owner/worker checkpoint merge + live worker log/exit) |
| worker port | `pytest -q tests/tools/test_process_registry.py tests/tools/test_process_checkpoint_readopt.py tests/tools/test_process_registry_lazy_restore.py tests/tools/test_process_registry_completion_races.py` (deselect the baseline-failing `TestPopenLeakOnSetupFailure`) | 139 passed, 4 skipped, 1 deselected |
| 4 | `pytest -q tests/tui_gateway/test_todo_state_events.py tests/gateway/test_chat_completions_final_fallback.py tests/gateway/test_chat_completions_approval.py tests/tui_gateway/test_resume_live_lazy_session.py tests/tui_gateway/test_session_resume_db_ownership.py tests/gateway/test_warning_notifications.py` | 61 passed, 1 pre-existing fail (baseline `test_resume_closes_profile_db_when_reopen_fails`) |
| 5 | `pytest -q tests/tui_gateway/test_undo_command.py tests/tui_gateway/test_legacy_fallback_profile_scope.py tests/agent/test_openai_ultrafast.py tests/tui_gateway/test_profiles_configure_model_guard.py` | 16 passed, 1 pre-existing fail (baseline `test_every_config_loader_parses_tiers_through_the_same_table`) |
| 6 | `pytest -q tests/qubit_cli/test_plugins_cmd_activation_keys.py tests/qubit_cli/test_plugins_cmd_enable_disable_nested.py tests/qubit_cli/test_plugins_cmd.py tests/qubit_cli/test_kanban_reclaim_claim_lock_guard.py tests/qubit_cli/test_kanban_dispatch_tick_hook.py tests/qubit_cli/test_kanban_dispatch_unknown_assignee_event.py` | 54 passed, 15 skipped |
| 6 | `pytest -q tests/qubit_cli/test_left_core_migration.py` | 25 passed, 4 pre-existing fails (byte-identical on baseline) |
| 7 | `pytest -q tests/qubit_cli/test_cli_status_bar.py tests/qubit_cli/test_cli_background_status_indicator.py tests/qubit_cli/test_cli_status_bar_goal.py` | 43 passed |
| 7 | `pytest -q tests/qubit_cli/test_cli_tools_command.py tests/gateway/test_external_drain_control.py` | 19 passed |
| 7 | `pytest -q tests/gateway/test_restart_after_turn.py tests/gateway/test_restart_drain.py` | 26 passed, 2 skipped |
| 8 | `pytest -q tests/gateway/test_session_recovery.py tests/gateway/test_reaped_session_recovery.py` | 5 passed |
| 8 | `pytest -q tests/tui_gateway/test_protocol.py` | 71 passed, 7 pre-existing fails (baseline: 70 passed, 8 fails — `test_window_owned_request_refuses_at_once_when_no_window_shows_the_session` now passes) |

Pre-existing failures were confirmed byte-identical on a detached baseline worktree
(`0b5ebc401`), not inferred.

### Architectural-port decision

**Done: worker output-log family.** `ProcessSession` gained `output_log`; the
registry inherits `ProcessOutputLogMixin`; `spawn_local` writes the child's stdout
to the profile log only under a registered worker (`is_worker_process()`), wraps
the command with `record_exit_command`, and closes the parent's duplicate fd;
checkpoint writes merge foreign handoffs under the cross-process file lock and
recovery adopts a logged entry even when the PID is gone/recycled; `poll` no
longer reports "output history unavailable" for a log-backed adoption. It cannot
double-deliver: outside a worker the code path is byte-for-byte the old pipe path
(`log is None`), and one reader owns each adoption (`_track_started`).

**Still deferred, with blockers:** see the triage table above; the closest
remaining candidates are the run-turn/run-inbound mixin inheritance (blocked on
deleting ~1.4k lines of inlined copies in one verified move) and
`require_ledger_owner` (blocked on completing the worker-ledger routing set).

## Wave 7 (branch `fix/dead-symbols-wave7`, based on main `1ee45b700`)

Measured with the documented command from the worktree root (warm cache symlinked
from the primary checkout):

```bash
.venv/bin/python qubit_runtime/scripts/check_dead_symbols.py --json /tmp/dead-wave7-before.json -v
```

| state | dead_count |
|---|---:|
| main `1ee45b700` | 167 |
| after batch 1 `04406cb34` | 161 |
| after batch 2 `30a65f3fe` | 148 |
| after batch 3 `a22b3dcdb` | 139 |
| after batch 4 `bb414b27f` | **129** |

**38 resolved, 0 new** (set diff of the before file and the final file). No
upstream-defined symbol was deleted, so **no Declared-exclusions entry was added**
to `docs/FROZEN_TARGET.md`. The only removals were fork-private duplicates whose
bodies were replaced by the module implementation (`_finish_profile_layout`,
`_print_standalone_by_config`'s body, the two inlined notification-ownership
methods); none of those names is read upstream, so none appears in the dead list.

### Wired in this wave (38)

| symbol | module | upstream call site (pin) |
|---|---|---|
| `start_turn_worker` | gateway/session_runtime_workers.py | `gateway/run_turn.py:1551` (register `_turn_workers`) |
| `prompt_timeout_s` | gateway/local_viewer_events.py | `gateway/run_turn_runner.py:499,635` |
| `ask_clarify_batch` | gateway/session_clarify_batch.py | `gateway/run_turn_runner.py:517` |
| `clarify_batch_turn` | gateway/session_cli_turn.py | `gateway/run_turn_runner.py:514` |
| `_keeps_profile_route` | gateway/session_policy.py | `gateway/session_policy.py:408` |
| `disable_dropped_secrets` | gateway/session_policy_credentials.py | `gateway/session_policy.py:100` |
| `_cmd_config_show` | qubit_cli/config.py | its own subcommand table (`None`/`show`) |
| `rebase_for_save` | qubit_cli/config_worker_save.py | `qubit_cli/config.py:2500` |
| `apply_user_moa_presets` | qubit_cli/moa_config.py | `qubit_cli/config.py:2262,2375` |
| `write_under_cache_lock` | qubit_cli/models_cache_lock.py | `qubit_cli/models.py:2071,2172` |
| `_krea_image_default` | qubit_cli/nous_subscription.py | `qubit_cli/nous_subscription.py:517,585` |
| `_forget_host_gateway_probe` | qubit_cli/gateway_host_record.py | `qubit_cli/gateway.py:5418,5556` |
| `report_incompatible_owner` | qubit_cli/gateway_status_notes.py | `qubit_cli/gateway.py:5631,5636` |
| `print_standalone_by_config` | qubit_cli/gateway_status_notes.py | `qubit_cli/gateway.py:5663` |
| `queue_admitted_turn` | qubit_cli/gateway_tools_notify.py | `qubit_cli/cli_process_notifications.py:37,42` |
| `_stop_background_work` | qubit_cli/cli_commands_session_tools.py | its own `/stop` handler:108,114 |
| `_print_local_delegations` | qubit_cli/cli_commands_session_tools.py | its own `/agents` handler:134 |
| `_bypass_chat_launch` | qubit_cli/main_tui_launch.py | `qubit_cli/main.py:1702,2861,2912` |
| `_refuse_unforwarded_tui_flags` | qubit_cli/main_tui_launch.py | `qubit_cli/main.py:1710` |
| `_auto_resume_denied_source` | tui_gateway/methods_session.py | `tui_gateway/methods_session.py:576` |
| `admit_heartbeat` | gateway/run_heartbeat_acceptance.py | `gateway/run_goals.py:147` |
| `GatewayNotificationOwnershipMixin` | gateway/run_notifications_ownership.py | `gateway/run_notifications.py:155` (base class) |
| `get_task_for_turn` | gateway/hosted_room_driver.py | `tui_gateway/hosted_room_service.py:442` |
| `append_user_event` | gateway/session_hosted_attachments.py | `tui_gateway/hosted_room_service.py:490` |
| `DiscordAuthorizationMixin` | plugins/platforms/discord/adapter_authorization.py | `plugins/platforms/discord/adapter.py:1221` (base) |
| `DiscordSlashAuthMixin` | plugins/platforms/discord/adapter_slash_auth.py | same base list |
| `DiscordThreadTitlesMixin` | plugins/platforms/discord/adapter_thread_titles.py | same base list |
| `DiscordVoiceInfoMixin` | plugins/platforms/discord/adapter_voice_info.py | same base list |
| `unattended_turn` | gateway/session_finite.py | `tools/approval.py:333` |
| `check_private_imports` | qubit_cli/plugin_validate_private_imports.py | `qubit_cli/plugin_validate.py:539` |
| `sweep_pending_plugin_deletes` | qubit_cli/plugins_cmd_remove.py | `gateway/run_startup.py:1070` |
| `_plugin_enable_sets` | qubit_cli/web_routers/dashboard_ui.py | same file `:137,400` |
| `_plugin_activated` | qubit_cli/web_routers/dashboard_ui.py | same file `:145,400` |
| `finish_profile_layout` | qubit_cli/profiles_initialization.py | `qubit_cli/profiles.py:1455` |
| `_find_our_wrapper` | qubit_cli/profiles.py | same file `:576,1020,1768` |
| `load_hosted_profiles` | qubit_cli/plugin_host_profiles.py | `providers/__init__.py:476` |
| `guest_without_free_tier` | qubit_cli/anon_auth.py | `agent/credential_pool.py:2620`, `qubit_cli/auth_nous.py:1111` |
| `set_guest_flag` | qubit_cli/anon_auth.py | `qubit_cli/anon_auth.py:978` |

### Triaged but not wired (new in this wave, with evidence)

| symbol | module | verdict | evidence |
|---|---|---|---|
| `_message_manifest`, `_bounded_task_delta` | gateway/hosted_room_discussion.py | **CORRECTLY UNWIRED** | this fork's boundary is text-only by test: `tests/gateway/test_hosted_room_discussion.py::test_attachment_payload_is_rejected_by_local_text_only_boundary`. Upstream reads them only on the attachment path (`hosted_room_discussion.py:204,214,390,722`); wiring needs attachments restored end-to-end, not one call. |
| `_kill_process_tree` | tools/browser_tool_lifecycle.py | test-only | upstream product code never calls it either (only `tests/tools/test_browser_process_tree.py`); it exists as the local fallback body of `_legacy_kill_process_tree`. |
| `retain_result`, `worker_exit_code`, `prepare_delivery_locked`, `acknowledge_locked`, `api_auth_ws_ticket` | gateway/session_results.py, gateway/session_kanban.py, gateway/hosted_room_work_records.py, qubit_cli/dashboard_auth/routes.py | test-only | every upstream read is a test/fixture (`test_admission_results`, `test_kanban_result_exit`, `test_room_work_evidence`, `test_authenticated_session_identity`); no product call site exists upstream at the pin. |
| `forward_to_owner` | gateway/session_a2a.py | **DEFERRED** | the local `_forward_to_profile` forwards in-process through its own session/DB route (`plugins/platforms/a2a/adapter.py:584`), not by dialing the destination owner's gateway. Adopting `forward_to_owner` changes the delivery contract (`input_id`, timeout, receipt, `a2a.forward` admission); a partial port would forward twice. |
| `_gateway_launch`, `_record_shell_hook_consent`, `exit_on_resume_refusal` | qubit_cli/cli_init_mixin.py, qubit_cli/gateway_chat.py | **CORRECTLY UNWIRED in this fork** | their upstream callers `_ensure_gateway_session`/`_new_session_id` do not exist locally; this CLI builds `run_agent.AIAgent` in-process (`cli_agent_setup_mixin._init_agent`) and `GatewaySessionStore.open_session` is never called. |
| `one_turn_route` | gateway/session_cli_turn.py | **DEFERRED** | upstream passes the session-policy object (`policy.config(active_authority(...))`); this tree has no session-policy layer at the turn site and already applies committed one-turn model overrides through `_apply_session_model_override`. Wiring both could double-apply. |
| `configured_plugin_entries` | gateway/platform_activation.py | **DEFERRED** | `PlatformRegistry` here lacks `configured_entries()` and `activation_env()`/`note_activation_env()`; `config_env._enable_plugin_platforms_from_env` uses `plugin_entries()`. The filter cannot be ported without those registry methods. |
| `migrate_honcho_profile_host` | qubit_cli/profiles_honcho.py | **CORRECTLY UNWIRED** | the module still writes `hermes_<profile>` hosts; the local `_migrate_honcho_profile_host` was rebranded to write `qubit_<profile>` (accepting `hermes_`/`qubit.` as legacy sources). Wiring the module would regress the host key. |
| `_resolve_source_target` | qubit_cli/source_releases.py | **CORRECTLY UNWIRED** | the local public `resolve_source_target` deliberately resolves every subscription through R2 (its docstring), while the private upstream copy resolves `stable` from GitHub releases. Replacing the public path would change the release source. |
| `select_apply_target`, `downgrade_refusal` | qubit_cli/update_cmd_check.py, qubit_cli/update_downgrade_guard.py | **DEFERRED** | local `update_cmd` runs the older inline update flow; upstream's `_check.select_apply_target` belongs with the newer preflight/commit-obligation ordering (`_refuse_before_commit_point`). Wiring it into the old flow would skip that ordering. |
| `app_lifespan` | qubit_cli/web_server_app.py | **DEFERRED** | adopting it replaces the single local `_lifespan` with `standalone_lifespan` + `http_lifespan`; the two bodies have diverged (install probe, desktop-owned backend) and the HTTP arm needs an owner `gateway_runner`. Not a one-line wire. |
| `GatewayInboundHooksMixin`, `GatewayPluginInjectionMixin` | gateway/run_inbound_hooks.py, gateway/run_plugin_injection.py | **DEFERRED** | `GatewayInboundMixin` inlines several of the mixin's methods with divergent bodies (`_hm_pre_gateway_dispatch_hook` 1784 vs 2732 chars; `_hm_dispatch_quick_and_plugin_commands` 3975 vs 2204). The Wave-2 telegram-style "inherit + delete every inlined copy" move must be done in one verified change. |
| `GatewayRuntimeInitMixin`, `GatewayTurnHygieneMixin`, `GatewayTurnPersistenceMixin`, `GatewayTurnPrepareMixin` | gateway/run_*.py | **DEFERRED** | same inline-vs-inherit split; the mixins' methods exist as local copies and must be deleted together with the inheritance. |
| `CatalogApproved`, `CatalogServerError` | tui_gateway/contracts/connectors_operation.py | **DEFERRED** | local `CatalogRow` dropped `approved`/`server_errors` (and `enabled`/`missing_env`/`already_installed`); the types need those fields and their producers. |
| `OnboardingEndedPayload` | tui_gateway/contracts/events.py | **CORRECTLY UNWIRED** | local onboarding is the `setup_choose` replacement; `OnboardingStateParams`' own comment records the deliberate replacement and `onboarding.state` is registered with `Params`. |
| `GatewayLoopManager`, `GatewayHeartbeatManager` | qubit_cli/gateway_automation.py | **CORRECTLY UNWIRED in this fork** | the remote managers are only used by upstream's `_automation_connection`; `GatewaySessionStore` is never constructed here, so the local in-process managers are the live path. |
| `_prepare_deferred_agent_startup` | cli.py | **CORRECTLY UNWIRED** | the call site was deliberately replaced by the in-process agent; the local comment (`cli_agent_setup_mixin.py:636`) records "used to run here; it is a no-op in this tree". |

### Focused verification for the wave

| batch | command (cwd `qubit_runtime/`) | result |
|---|---|---|
| 1 | `pytest -q tests/gateway/test_local_viewer_prompt_cluster.py tests/gateway/test_approval_send_timeout_ambiguity.py tests/gateway/test_clarify_delivery_fallback.py tests/gateway/test_clarify_card_retire_on_timeout.py tests/gateway/test_approval_prompt_redaction.py` | 30 passed |
| 1 | `pytest -q tests/gateway/test_session_create_retry.py tests/gateway/test_session_auxiliary_scope.py tests/gateway/test_session_model_override_routing.py tests/gateway/test_custom_provider_request_overrides.py tests/gateway/test_session_model_override_credential_pool.py` | 13 passed |
| 2 | `pytest -q tests/qubit_cli/test_config_lkg_backup.py tests/qubit_cli/test_config_worker_save_deletion.py tests/qubit_cli/test_model_cache_parallel_prefetch.py tests/qubit_cli/test_model_cache_swr.py tests/qubit_cli/test_nous_subscription.py tests/qubit_cli/test_show_config_credential.py tests/qubit_cli/test_managed_scope_surfacing.py` | 60 passed |
| 2 | `pytest -q tests/qubit_cli/test_config_save_integrity.py tests/qubit_cli/test_config.py` | 187 passed, 1 pre-existing fail (`test_upgrades_pre_rewrite_default_soul_md`) |
| 2 | `pytest -q tests/qubit_cli/test_moa_preset_deletion.py` | 11 failed, 4 passed — baseline `1ee45b700` is 13 failed, 2 passed (the two load-path tests now pass) |
| 2 | `pytest -q tests/qubit_cli/test_classic_cli_routing.py tests/qubit_cli/test_gateway_multiplex_status.py` | all pass |
| 2 | `pytest -q tests/qubit_cli/test_cli_interrupt_drain_regression.py tests/qubit_cli/test_process_notification_display.py tests/qubit_cli/test_busy_policy_invariants.py` | 11 passed |
| 3 | `pytest -q tests/gateway/test_heartbeat_acceptance.py tests/gateway/test_heartbeat_poller.py tests/gateway/test_heartbeat_execution_ownership.py tests/gateway/test_background_process_notifications.py tests/gateway/test_api_notification_outcomes.py tests/gateway/test_warning_notifications.py tests/tui_gateway/test_hosted_room_service.py tests/tui_gateway/test_hosted_room_server_rpc.py tests/plugins/test_discord_runtime_failure.py` | 140 passed, 7 pre-existing fails (byte-identical on baseline: hosted-room compression `IndexError`, heartbeat compression case) |
| 3 | `pytest -q tests/tui_gateway/test_tui_gateway_server.py -k "most_recent or session_list"` | 9 passed |
| 4 | `pytest -q tests/tools/test_approval_unattended_presence.py tests/tools/test_approval_yolo.py tests/tools/test_approval.py tests/qubit_cli/test_plugin_validate.py tests/qubit_cli/test_plugin_host.py tests/qubit_cli/test_anon_connectors_only.py tests/qubit_cli/test_anon_first_notice.py tests/qubit_cli/test_profile_delete_wrapper.py tests/qubit_cli/test_dashboard_plugin_routes_secret_scope.py` | 212 passed, 4 pre-existing fails (byte-identical on baseline) |
| 4 | `pytest -q tests/qubit_cli/test_profile_clone_channels.py tests/qubit_cli/test_profiles.py tests/qubit_cli/test_apply_profile_override.py tests/qubit_cli/test_profile_listing_cache.py tests/qubit_cli/test_models_catalog_late_plugin_provider.py tests/agent/test_credential_pool_nous_refresh_stampede.py tests/qubit_cli/test_anon_auth_core.py` | 193 passed, 2 pre-existing fails (byte-identical on baseline) |

Pre-existing failures were confirmed by stashing the wave-7 files and re-running
the same tests on `1ee45b700`; no failure was inferred.

## Wave 8 (branch `fix/dead-symbols-wave8`, based on main `6aaf931f1`)

Measured with the documented command from the worktree root (warm cache symlinked
from the primary checkout):

```bash
.venv/bin/python qubit_runtime/scripts/check_dead_symbols.py --json /tmp/dead-wave8-before.json -v
# ... 13 batches ...
.venv/bin/python qubit_runtime/scripts/check_dead_symbols.py --json /tmp/dead-wave8-after.json -v
```

| state | dead_count |
|---|---:|
| main `6aaf931f1` | 128 |
| after 13 batches | **109** |

**19 symbols resolved, 0 new** (set diff of `/tmp/dead-wave8-before.json` vs
`/tmp/dead-wave8-after.json`). **No symbol was deleted**, so no Declared-exclusions
entry was added to `docs/FROZEN_TARGET.md`.

### Wired in this wave (19 measured + 1 string-bound)

| # | symbol | module | upstream call site (pin) |
|--:|---|---|---|
| 1 | `_source_match_clause` | qubit_state_common.py | `hermes_state_sessions.py:138,1548` |
| 2 | `exclude_superseded_segments` | qubit_state_local.py | `hermes_state_sessions.py:1352,1432,1576,1594` |
| 3 | `annotate_local_lineages` | qubit_state_local.py | `hermes_state_sessions.py:1449` |
| 4 | `retire_prunable` | qubit_state_mutation_retirement.py | `hermes_state_maintenance.py:128,346`, `hermes_state_sessions.py:1930` |
| 5 | `prune_retired_tombstones` | qubit_state_mutation_retirement.py | `hermes_state_maintenance.py:347` |
| 6 | `SessionLedgeredError` | qubit_state_profile_repair.py | `hermes_cli/sessions_repair_profiles.py:409-414`, `hermes_state_profile_repair.py:230` |
| 7 | `_ledgered_lineages` | qubit_cli/sessions_repair_profiles.py | same file `:196` |
| 8 | `managed_turn_count` | gateway/run_runtime.py | `gateway/run_shutdown.py:197,807,1562,1606,1925,2075` |
| 9 | `retained_destination` | gateway/platforms/webhook_delivery.py | `gateway/platforms/webhook.py:319,321` |
| 10 | `recover_gateway_native_sessions` | gateway/run_runtime.py | `gateway/run_startup.py:1548-1549` |
| 11 | `stop_run` | gateway/platforms/api_server_authority_runs.py | `gateway/platforms/api_server_runs.py:1438-1441` |
| 12 | `ensure_aiohttp` | plugins/platforms/whatsapp/adapter.py | same file `:594` |
| 13 | `is_managed_route` | plugins/web/firecrawl/provider.py | `hermes_cli/web_routers/tools.py:412-419` |
| 14 | `is_connector_batch` | qubit_cli/observability/shared_metrics_connectors.py | `relay_shared_metrics.py:473,560` |
| 15 | `batch_entry_ran` | qubit_cli/observability/shared_metrics_connectors.py | `relay_shared_metrics.py:564` |
| 16 | `ConnectorSessionState` | qubit_cli/observability/shared_metrics_connectors.py | `relay_shared_metrics.py:193,211` |
| 17 | `tool_batch_fields` | qubit_cli/observability/shared_metrics_connectors.py | `shared_metrics_model.py:169-174` |
| 18 | `counter_dimensions_shape_is_known` | qubit_cli/observability/shared_metrics_drift.py | `shared_metrics.py:678` |
| 19 | `conform_counter_dimensions` | qubit_cli/observability/shared_metrics_drift.py | `shared_metrics.py:681` |
| — | `merge_endpoint_seed` (string-bound) | qubit_cli/profile_model_seed.py | `tui_gateway/methods_profiles.py:529` — wired through `_lazy(...)`, which the detector cannot see; it remains in the list but is live at runtime. |

### Batches and focused verification

| batch | commit | command (cwd `qubit_runtime/`) | result |
|---|---|---|---|
| 1 | `66aef7dab` | `pytest -q tests/qubit_state/test_session_list_created_source_lineage.py tests/qubit_state/test_hermes_state.py tests/qubit_state/test_pinned_lineage.py tests/qubit_state/test_session_list_index_choice.py tests/qubit_state/test_compression_lineage_guard.py` | 3 new pass; 301 passed, 10 pre-existing fails (byte-identical on baseline) |
| 2 | `7a163c978` | `pytest -q tests/qubit_state/test_prune_activity_recency.py tests/qubit_state/test_empty_sweep_archived_transcript.py tests/qubit_state/test_sweep_orphaned_sessions.py tests/qubit_state/test_never_active_keyed_prune.py tests/qubit_cli/test_prune_spares_pinned.py tests/qubit_cli/test_sessions_owner_prune_wiring.py` | 56 passed, 4 pre-existing fails (byte-identical on baseline) |
| 3 | `cb8758eac` | `pytest -q tests/gateway/test_restart_after_turn.py tests/gateway/test_restart_drain.py tests/gateway/test_external_drain_control.py tests/gateway/test_stuck_loop.py` and `...test_drain_active_work_report.py test_gateway_shutdown.py test_api_server_active_work_drain.py test_cron_active_work_drain.py` | 45 passed + 41 passed |
| 4 | `73cecba8a` | `pytest -q tests/gateway/test_webhook_adapter.py tests/gateway/test_webhook_deliver_only.py tests/gateway/test_webhook_integration.py tests/gateway/test_delivery_ledger_producer.py` | 56 passed |
| 5 | `62f4397a4` | `pytest -q tests/tui_gateway/test_profiles_inherit_launch_model.py tests/tui_gateway/test_profiles_configure.py tests/tui_gateway/test_profiles_configure_model_guard.py` | 8 passed (new custom_providers case) |
| 6 | `7cf616722` | `pytest -q tests/qubit_cli/test_sessions_repair_profiles.py` | 6 passed |
| 7 | `690c0d0d1` | `pytest -q tests/qubit_cli/test_sessions_repair_profiles.py` | 7 passed (new ledgered-lineage case) |
| 8 | `1794dffa2` | `pytest -q tests/qubit_cli/test_relay_shared_metrics.py tests/qubit_cli/test_shared_metrics_reliability.py tests/qubit_cli/test_shared_metrics_harness.py` | 130 passed, 2 pre-existing fails (byte-identical on baseline) |
| 9 | `d28be1181` | `pytest -q tests/qubit_cli/test_shared_metrics_connector_errors.py tests/qubit_cli/test_shared_metrics_signals.py tests/qubit_cli/test_shared_metrics_surface_attribution.py` | 66 passed, 4 pre-existing fails (byte-identical on baseline) |
| 10 | `dc6ca48fe` | `pytest -q tests/qubit_cli/test_relay_shared_metrics.py tests/qubit_cli/test_relay_shared_metrics_runtime.py tests/qubit_cli/test_shared_metrics_connectors.py` | 164 passed (4 new) |
| 11 | `5741f2e33` | `pytest -q tests/gateway/test_whatsapp_connect.py tests/gateway/test_whatsapp_bridge_dir_resolution.py` and web-server toolset tests | 14 passed, 3 skipped; 3 passed |
| 12 | `99fc3525f` | `pytest -q tests/gateway/test_session_db_recovery.py test_active_turn_recovery.py test_reaped_session_recovery.py test_hosted_room_gateway_lifecycle.py test_profile_spool_recovery.py test_gateway_inline_bootstrap_identity.py test_runner_startup_failures.py test_boot_runtime_lock_reservation.py` | 41 + 34 passed |
| 13 | `81dc63d8e` | `pytest -q tests/gateway/test_api_server_runs.py tests/gateway/test_api_server_runs_import_isolation.py` and stop-filtered `test_peer_dm_into_open_bot_chat.py`/`test_api_server.py` | 76 passed; 4 passed |

Every pre-existing failure was re-run on a detached baseline before being called
pre-existing. No service was restarted.

### New triage (not wired) — evidence at the pin

| # | symbol | module | verdict | evidence |
|--:|---|---|---|---|
| 1 | `validate_task_input` | gateway/hosted_room_task_input.py | GENUINE GAP, DEFERRED | upstream reads it at `hosted_room_discussion.py:22,732,777`, `hosted_room_driver.py:178`, `hosted_room_policy_checkpoint.py:17,362`; the local callers carry no `input_context` at all, so wiring one call would accept unvalidated checkpoint input. |
| 2 | `is_openai_bedrock_model` | agent/bedrock_adapter.py | TEST-ONLY at the pin | product code never calls it; only `tests/agent/test_bedrock_adapter.py:1079-1095`. (Corrects the Wave-1 "genuine" label.) |
| 3 | `terminal_worker_receipt` | qubit_state_terminal.py | TEST-ONLY at the pin | only `tests/hermes_state/test_terminal_retirement.py` reads it; no upstream product call site. |
| 4 | `prepare_delivery_locked` | gateway/hosted_room_work_records.py | TEST-ONLY at the pin | only `tests/gateway/test_room_work_evidence.py`. |
| 5 | `acknowledge_locked` | gateway/hosted_room_work_records.py | TEST-ONLY at the pin | only `tests/gateway/test_room_work_evidence.py`. |
| 6 | `_tool_prefix` | qubit_cli/plugins_activation_live.py | CORRECTLY UNWIRED | local activation note lists each tool via `_tool_listing`; upstream's prefix+count line is a different message shape, not a dropped call. |
| 7 | `reset_local_session` | gateway/session_local_recovery.py | BLOCKED — FILE OWNED BY ANOTHER LANE | its only upstream call site is `gateway/session.py:1145-1146`; `gateway/session.py` is out of scope for this lane. |
| 8 | `_submit_fingerprint` | acp_adapter/gateway_server.py | BLOCKED — FILE OWNED BY ANOTHER LANE | `acp_adapter/**` is out of scope; upstream calls it at `acp_adapter/gateway_server.py:558`. |
| 9 | `unpark_profile` | gateway/run_runtime.py | DEFERRED | upstream hot-serve reconcile calls it at `run_profile_reconcile.py:157-197`; this fork's reconcile serves adapters only and has no runtime reservation/serve phase (`_apply_profile_changes` never calls `reserve_profile_home`/`serve_profile_runtime`), so parking state cannot be maintained partially. |
| 10 | `reserve_profile_home` | gateway/run_runtime.py | DEFERRED | same hot-serve runtime phase (`run_profile_reconcile.py:270-273`). |
| 11 | `serve_profile_runtime` | gateway/run_runtime.py | DEFERRED | same (`run_profile_reconcile.py:270-280`); needs the per-profile authority build wired into reconcile. |
| 12 | `unserve_profile_runtime` | gateway/run_runtime.py | DEFERRED | same (`run_profile_reconcile.py:339-341`); teardown would otherwise retire nothing while the adapter teardown proceeds. |
| 13 | `register_transport_home` | gateway/session_ingress_context.py | DEFERRED | upstream registers it from `run_adapters.py:1713-1748` where the message handlers also open `native_callback`; the local handlers open no callback, so wiring the registration alone leaves provenance half-bound. |
| 14 | `create_peer_bot_chat` | gateway/platforms/api_server_bot_chat.py | DEFERRED | upstream endpoint is wired at `api_server.py:3074-3075`; this fork's api_server registers no peer-DM create route. |
| 15 | `admit_peer_turn` | gateway/platforms/api_server_bot_chat.py | DEFERRED | same peer-DM route (`api_server.py:3426-3427`). |
| 16 | `await_live_delivery` | gateway/platforms/api_server_bot_chat.py | DEFERRED | same peer-DM delivery (`api_server.py:3482-3490`, `api_server_runs.py:955-971`). |
| 17 | `wall_clock_sleep` | scripts/code_health/py_rules_sleep.py | DEFERRED | upstream registers it as HX013 (`py_rules.py:17,876`, `config.py:143-150`); this fork's rule table stops at HX012 and registration needs a baseline allow-comment sweep. |
| 18 | `_require_release_line` | scripts/releases/entrypoint.py | DEFERRED | upstream calls it at `entrypoint.py:243`; the local release flow uses `_require_remote_main` and a different outstanding/abandon path, so wiring the upstream helper changes release admission. |
| 19 | `_is_claim_run` | scripts/releases/entrypoint.py | DEFERRED | upstream call sites `:136,453` belong to the diverged release-admission flow. |
| 20 | `_abandon_confirmed` / `_abandon_prompt` | scripts/releases/entrypoint.py | DEFERRED | upstream `:282,587`; local abandon flow differs. |
| 21 | `release_line_refspecs` | scripts/releases/stable.py | DEFERRED | upstream `entrypoint.py:67`, `stable.py:566,615`; the local release scripts have diverged and call site wiring is release-critical. |
| 22 | `require_targets_match_claim` | scripts/releases/stable.py | DEFERRED | upstream `stable.py:1016`; needs the full claim/manifest flow. |
| 23 | `baseline_covers` | scripts/releases/stable.py | DEFERRED | upstream `stable.py:773`. |
| 24 | `check_controller` | scripts/releases/stable.py | DEFERRED | upstream `stable.py:548`. |
| 25 | `authenticated_push` | scripts/releases/stable.py | DEFERRED | upstream `stable.py:907`. |
| 26 | `prepare_context`, `identify_output`, `_response_identity` | gateway/platforms/api_server_response_*.py | DEFERRED | the durable Responses admission (`durable_key`/fingerprint/replay) is absent locally. |
| 27 | `_handle_run_clarify`, `_handle_resolve_unknown_run`, `_steer_refused`, `_run_idempotency_identity`, `_run_user_message`, `_admit_run_to_authority` | gateway/platforms/api_server_runs.py | DEFERRED | clarify/resolve-unknown routes are not registered locally and the run handler inlines a different admission path. |
| 28 | `bind_api_session`, `admit_api_turn`, `run_api_turn`, `recover_api_turns`, `owns_api_run` | gateway/session_api*.py | DEFERRED | API-turn authority transaction is only partially ported. |
| 29 | `GatewayInboundHooksMixin`, `GatewayPluginInjectionMixin` | gateway/run_inbound_hooks.py, gateway/run_plugin_injection.py | DEFERRED | inline-vs-inherit split over ~1.4k lines; needs the Wave-2 telegram-style "inherit + delete every inlined copy" move. |
| 30 | `GatewayRuntimeInitMixin`, `GatewayTurnHygieneMixin`, `GatewayTurnPersistenceMixin`, `GatewayTurnPrepareMixin` | gateway/run_*.py | DEFERRED | same inline-vs-inherit split. |
| 31 | `CatalogApproved`, `CatalogServerError` | tui_gateway/contracts/connectors_operation.py | DEFERRED | local `CatalogRow` dropped `approved`/`server_errors` (and `enabled`/`missing_env`/`already_installed`). |
| 32 | `forward_to_owner` | gateway/session_a2a.py | DEFERRED | local `_forward_to_profile` forwards in-process; adopting the owner-dial path changes the delivery contract and could forward twice. |
| 33 | `configured_plugin_entries` | gateway/platform_activation.py | DEFERRED | local `PlatformRegistry` lacks `configured_entries()`/`activation_env()`. |
| 34 | `app_lifespan` | qubit_cli/web_server_app.py | DEFERRED | replacing the single `_lifespan` with standby/http lifespans needs the owner `gateway_runner`. |
| 35 | `completion_admission`, `drop_completion_delivery`, `launch_supervisor`, `gateway_spawn_intent_subcommand`, `has_pending`, `snapshot_native`, `_replay_unanswered` | various | FALSE POSITIVE / CORRECTLY UNWIRED | Wave 1-7 verdicts still hold (string-bound keys, test-only call sites, or a bounded local sibling that would double-deliver). |
| 36 | test fixtures and test classes (`loopback_app`, `legacy_digests`, `_seed_stores`, `cron_home`, `TestRateLimit`, `TestPatchReplace`, `TestTerminalResultsCarryTheFreeTierBlock`, `explicit_static_config`, `_metric`) | tests/** | FALSE POSITIVE / TEST-ONLY | pytest collects/requests these by name; no product call site. |

### Declared exclusions

**None.** No upstream-defined symbol was deleted; every removal in the batches was
a replaced/duplicated fork-private body, and no `docs/FROZEN_TARGET.md` entry was
needed.

### Explicit not-done

- The hot-serve runtime family (reservation/serve/unserve/park) is **not** ported;
  the local reconcile still serves adapters without building per-profile session
  authorities. Its four blockers are named above.
- The release-script helpers, the API-turn/Responses admission, the inbound/turn
  mixin inheritance, and the `CatalogRow` fields remain deferred with the exact
  upstream paths above.
- `merge_endpoint_seed` is wired at runtime but still reported by the detector
  because its consumer is string-bound through `_lazy`.
- No service was restarted, nothing was applied live, and no global E2E/release
  verdict is claimed.

## Wave 9 (branch `fix/dead-symbols-wave9`, based on main `60e6b28f6`)

Measured with the documented command from the worktree root (warm cache symlinked
from the primary checkout):

```bash
.venv/bin/python qubit_runtime/scripts/check_dead_symbols.py --json /tmp/dead-wave9-before.json -v
# ... 9 batches ...
.venv/bin/python qubit_runtime/scripts/check_dead_symbols.py --json /tmp/dead-wave9-after.json -v
```

| state | dead_count |
|---|---:|
| main `60e6b28f6` | 103 |
| after 9 batches | **90** |

**13 symbols resolved, 0 new** (set diff of `/tmp/dead-wave9-before.json` vs
`/tmp/dead-wave9-after.json`). One batch deleted two superseded upstream helpers,
recorded as Declared exclusions in `docs/FROZEN_TARGET.md`; every other batch
wired a live call site.

### Wired / deleted in this wave

| # | symbol | module | upstream call site (pin) |
|--:|---|---|---|
| 1 | `CompressionTelemetryMixin` | agent/context_compressor_telemetry.py | `agent/context_compressor.py:38,2153` (mixin base); the local class had 3 inline copies and lacked `_compression_method`/`_record_compression_effect`/`_record_committed_compression_effect` |
| 2 | `relay_chat_metadata` | agent/auxiliary_wire.py | `agent/auxiliary_client.py:2627,2651,2666,2682,2695,2711` (sync/async/stream relay metadata) |
| 3 | `downgrade_refusal` | qubit_cli/update_downgrade_guard.py | `qubit_cli/update_cmd.py:1268-1269` (pre-commit-point refusal) |
| 4 | `migrate_honcho_profile_host` | qubit_cli/profiles_honcho.py | `qubit_cli/profiles.py:2400-2401` (rename step 3) |
| 5 | `_setup_beat_request` | tui_gateway/methods_onboarding.py | `tui_gateway/agent_callbacks.py:184` (`setup_beat_callback`) |
| 6 | `_carry_onboarding_toolset` | tui_gateway/methods_onboarding.py | `tui_gateway/methods_tools.py:331` (live tool refresh) |
| 7 | `_onboarding_prelude` | tui_gateway/methods_onboarding.py | `tui_gateway/prompt_turn.py:797` (setup_beat opening) |
| 8 | `_stage_onboarding_turn_note` | tui_gateway/methods_onboarding.py | `tui_gateway/prompt_turn.py:700` (before a user turn) |
| 9 | `_replay_unanswered` | tools/bot_relay.py | DELETED — fork replaces it with the bounded `_reoffer_unanswered`; declared exclusion |
| 10 | `_settle_pre_upgrade_claim` | tools/bot_relay.py | DELETED — reachable only from `_replay_unanswered`; declared exclusion |
| 11 | `admit_route_wake` | gateway/session_automation.py | `gateway/run_goals.py:331-333,491-494`, `gateway/wake.py:105-109` (local goal continuation + loop tick) |
| 12 | `_emit_blocked_attempt_telemetry` | agent/conversation_compression_telemetry.py | `agent/conversation_compression.py:4163,4234` (breaker gate) |
| 13 | `_snapshot_compression_effect_input`, `_record_committed_attempt_effect` | agent/conversation_compression_telemetry.py | `agent/conversation_compression.py:4234,4255,4344` (commit path) |

### Batches and focused verification

| batch | commit | command (cwd `qubit_runtime/`) | result |
|---|---|---|---|
| 1 | `89d8e094b` | `pytest -q tests/agent/test_compression_attempt_telemetry.py` + `test_compression_attempt_lifecycle.py test_compression_boundary_hook.py` | 4 passed; 14 passed, 4 pre-existing fails (byte-identical on baseline) |
| 2 | `07565c2e0` | `pytest -q tests/agent/test_auxiliary_relay.py tests/agent/test_auxiliary_wire_messages.py tests/agent/test_aux_relay_progress_seam.py` | 9 passed, 5 pre-existing fails (byte-identical on baseline) |
| 3 | `38350b7a1` | `pytest -q tests/qubit_cli/test_update_downgrade_wiring.py tests/qubit_cli/test_cmd_update.py` | 2 passed (new wiring pins); 2 passed, 3 skipped |
| 4 | `a89507a3b` | `pytest -q tests/qubit_cli/test_profiles.py::TestRenameProfile` | 9 passed (incl. the Honcho host rename case) |
| 5 | `2bc0b761e` | `pytest -q tests/tui_gateway/test_tui_mcp_late_refresh.py tests/tui_gateway/test_first_contact_onboarding.py` and the full `test_tui_gateway_server.py` | 13 passed; 650 passed, 19 pre-existing fails (byte-identical on baseline) |
| 6 | `9e1e06ad2` | `pytest -q tests/tools/test_bot_relay.py tests/tui_gateway/test_bot_relay_methods.py` | 61 passed |
| 7 | `c6e6816a3` | `pytest -q tests/gateway/test_goal_continuation_drain.py tests/gateway/test_loop_command.py tests/gateway/test_busy_wake_admission.py tests/gateway/test_kanban_wake_acceptance.py tests/gateway/test_kanban_wake_scope.py` | 12 + 14 passed |
| 8 | `c0b7af194` | `pytest -q tests/agent/test_compression_attempt_telemetry.py tests/agent/test_compression_attempt_lifecycle.py tests/agent/test_idle_compaction_lock_and_guards.py` | 19 passed |
| 9 | `35c885ecc` | `pytest -q tests/agent/test_compression_attempt_telemetry.py` (new committed-effect pin) | 5 passed |

### Triage (30+ symbols re-verified this wave) — evidence at the pin

| # | symbol | module | verdict | evidence |
|--:|---|---|---|---|
| 1 | `_submit_fingerprint` | acp_adapter/gateway_server.py | BLOCKED — lane owns `acp_adapter/**` | upstream awaits it at `gateway_server.py:576` |
| 2 | `is_openai_bedrock_model` | agent/bedrock_adapter.py | TEST-ONLY at the pin | only `tests/agent/test_bedrock_adapter.py:1079-1095` |
| 3 | `carrier_record` | agent/reasoning_carriers.py | DEFERRED | upstream `agent/chat_completion_helpers.py:1764`; local preserves SDK `reasoning_details` but captures no Gemini thought_signature / Copilot reasoning_opaque |
| 4 | `StreamCarriers` | agent/reasoning_carriers.py | DEFERRED | upstream `chat_completion_helpers.py:3050`; local stream accumulator never feeds it |
| 5 | `_prepare_deferred_agent_startup` | cli.py | CORRECTLY UNWIRED | local `cli_agent_setup_mixin.py:636` records that it is a no-op in this tree; the fork builds in-process |
| 6 | `give_up_execution` | cron/executions.py | BLOCKED — lane owns `cron/**` | upstream `cron/scheduler.py:3424` |
| 7 | `pause_for_unknown` | cron/scheduler_authority.py | BLOCKED — lane owns `cron/**` | upstream `cron/scheduler.py:3425` |
| 8 | `external_worker_exited_reason` | cron/scheduler_worker_failure.py | BLOCKED — lane owns `cron/**` | upstream `cron/scheduler.py:3551` |
| 9 | `launch_supervisor` | gateway/control_socket.py | FALSE POSITIVE | read through a string key `for key in ("launch_supervisor", "supervisor")` and re-emitted as a field |
| 10 | `_message_manifest` | gateway/hosted_room_discussion.py | DEFERRED — attachment port incomplete | upstream validates `message.user` attachments at `:204,390`; local `validate_user_payload` never carries attachments, so wiring the manifest alone validates nothing |
| 11 | `_bounded_task_delta` | gateway/hosted_room_discussion.py | DEFERRED — same attachment port | upstream `:722`; local `_build_prompt`/`_make_task_plan` do not carry attachments |
| 12 | `validate_task_input` | gateway/hosted_room_task_input.py | DEFERRED | upstream `hosted_room_discussion.py:22,732,777`, `hosted_room_driver.py:178`, `hosted_room_policy_checkpoint.py:17,362`; local callers carry no `input_context` |
| 13 | `prepare_delivery_locked` | gateway/hosted_room_work_records.py | TEST-ONLY at the pin | only `tests/gateway/test_room_work_evidence.py` |
| 14 | `acknowledge_locked` | gateway/hosted_room_work_records.py | TEST-ONLY at the pin | only `tests/gateway/test_room_work_evidence.py` |
| 15 | `configured_plugin_entries` | gateway/platform_activation.py | DEFERRED | calls `registry.configured_entries`, which `PlatformRegistry` does not define anywhere in the tree |
| 16 | `create_peer_bot_chat` | gateway/platforms/api_server_bot_chat.py | DEFERRED | upstream route at `api_server.py:3074-3075`; local api_server registers no peer-DM create route |
| 17 | `admit_peer_turn` | gateway/platforms/api_server_bot_chat.py | DEFERRED | same peer-DM route (`api_server.py:3426-3427`) |
| 18 | `await_live_delivery` | gateway/platforms/api_server_bot_chat.py | DEFERRED | same peer-DM delivery (`api_server.py:3482-3490`, `api_server_runs.py:955-971`) |
| 19 | `_response_identity`, `prepare_context`, `identify_output` | api_server_openai_routes.py / response_admissions.py / response_identity.py | DEFERRED | the durable Responses admission (`durable_key`/fingerprint/replay) is absent locally |
| 20 | `_run_idempotency_identity`, `_run_user_message`, `_admit_run_to_authority`, `_handle_run_clarify`, `_steer_refused`, `_handle_resolve_unknown_run` | gateway/platforms/api_server_runs.py | DEFERRED | clarify/resolve-unknown routes are not registered and the run handler inlines a different admission path |
| 21 | `GatewayInboundHooksMixin` | gateway/run_inbound_hooks.py | DEFERRED | upstream `gateway/run_inbound.py:71`; local `GatewayInboundMixin` inlines the hooks (same inline-vs-inherit split as Wave 2 telegram) |
| 22 | `GatewayPluginInjectionMixin` | gateway/run_plugin_injection.py | DEFERRED | upstream `run_inbound.py:71`; same split |
| 23 | `unpark_profile`, `reserve_profile_home`, `serve_profile_runtime` | gateway/run_runtime.py | DEFERRED | upstream hot-serve reconcile `run_profile_reconcile.py:157-197,270-280`; local reconcile serves adapters only and has no per-profile authority build |
| 24 | `GatewayTurnHygieneMixin`, `GatewayTurnPersistenceMixin`, `GatewayTurnPrepareMixin` | gateway/run_turn_*.py | BLOCKED — `gateway/run_turn.py` is another lane's file | upstream inherits them in `run_turn.py`; adopting them needs the inline-vs-inherit move in that file |
| 25 | `forward_to_owner` | gateway/session_a2a.py | DEFERRED | upstream `plugins/platforms/a2a/adapter.py`; local `_forward_to_profile` forwards in-process, and adopting the owner-dial path could forward twice |
| 26 | `bind_api_session`, `admit_api_turn`, `owns_api_run`, `recover_api_turns`, `run_api_turn` | gateway/session_api*.py | DEFERRED | the API-turn authority transaction is only partially ported; upstream call sites include `run_adapters.py`, `api_server.py`, `api_server_runs.py` |
| 27 | `completion_admission` | gateway/session_automation.py | DEFERRED | upstream `run_notifications.py:1172,1267,1413,1755`; the local notification path uses the durable delivery ledger, so adding the admission check beside it could suppress twice |
| 28 | `one_turn_route` | gateway/session_cli_turn.py | DEFERRED | upstream `run_turn_runner.py:1180`; the local turn site already applies committed one-turn overrides (`_apply_session_model_override`), so wiring both can double-apply |
| 29 | `worker_exit_code` | gateway/session_kanban.py | TEST-ONLY at the pin | upstream product code never calls it; only kanban tests/fixtures |
| 30 | `reset_local_session` | gateway/session_local_recovery.py | BLOCKED — `gateway/session.py` is another lane's file | its only upstream call site is `gateway/session.py:1145-1146` |
| 31 | `gateway_spawn_intent_subcommand` | gateway/status.py | TEST-ONLY | only `tests/_fixtures/live_system_guard.py` |
| 32 | `message_context_id`, `extract_message_id`, `logged_input` | plugins/platforms/a2a/protocol.py | DEFERRED | upstream `plugins/platforms/a2a/adapter.py:530-542`; retry dedup requires the owner admission (`forward_to_owner`) so a retry is not run twice, and that path is deferred |
| 33 | `_gateway_launch`, `_record_shell_hook_consent` | qubit_cli/cli_init_mixin.py | CORRECTLY UNWIRED | the fork's CLI opens `qubit_state_registry` in `_init_session_store` and registers hooks through `register_from_config`; it never uses upstream's `GatewaySessionStore`/`_ensure_gateway_session` |
| 34 | `api_auth_ws_ticket` | qubit_cli/dashboard_auth/routes.py | FALSE POSITIVE | a FastAPI `@router.post("/api/auth/ws-ticket")` handler; framework-wired by decorator, called directly by `tests/gateway/test_authenticated_session_identity.py:59-66` |
| 35 | `GatewayLoopManager`, `GatewayHeartbeatManager` | qubit_cli/gateway_automation.py | CORRECTLY UNWIRED | upstream `hermes_cli/cli_loops_mixin.py:554-566` picks the remote manager for a gateway session; this fork's classic CLI never selects it |
| 36 | `exit_on_resume_refusal` | qubit_cli/gateway_chat.py | CORRECTLY UNWIRED / SUPERSEDED | the gateway `launch_from_args` error path owns the failure and must keep the stream-json emitter contract; raising `SystemExit` would bypass the protocol |
| 37 | `_tool_prefix` | qubit_cli/plugins_activation_live.py | CORRECTLY UNWIRED | the fork's activation note lists each tool via `_tool_listing`; upstream's prefix+count line is a different message shape |
| 38 | `merge_endpoint_seed` | qubit_cli/profile_model_seed.py | FALSE POSITIVE | string-bound through `_lazy(...)` at `tui_gateway/methods_profiles.py:529`; live at runtime |
| 39 | `_resolve_source_target` | qubit_cli/source_releases.py | CORRECTLY UNWIRED | local `resolve_source_target` is the authoritative R2-every-channel superset; the dead copy delegates stable to GitHub-only `_resolve_stable` (which would strand `_resolve_release`), so wiring would change resolution |
| 40 | `select_apply_target` | qubit_cli/update_cmd_check.py | DEFERRED | upstream `update_cmd.py:1862`; the local update flow inlines the block and drops `forward_only`/`rides_default_channel`, so adopting the helper changes update admission |
| 41 | `app_lifespan` | qubit_cli/web_server_app.py | DEFERRED | upstream `web_server.py`; replacing the single `_lifespan` with standby/http lifespans needs the owner `gateway_runner` |
| 42 | `terminal_worker_receipt` | qubit_state_terminal.py | TEST-ONLY at the pin | only `tests/hermes_state/test_terminal_retirement.py` |
| 43 | `wall_clock_sleep` | scripts/code_health/py_rules_sleep.py | DEFERRED | upstream registers HX013 (`py_rules.py:17,876`, `config.py:143-150`); the fork's rule table stops at HX012 and needs a baseline allow-comment sweep |
| 44 | `_require_release_line`, `_is_claim_run`, `_abandon_confirmed`, `_abandon_prompt` | scripts/releases/entrypoint.py | DEFERRED | the local release flow uses `_require_remote_main` and a different abandon path; wiring changes release admission |
| 45 | `release_line_refspecs`, `require_targets_match_claim`, `baseline_covers`, `check_controller`, `authenticated_push` | scripts/releases/stable.py | DEFERRED | upstream `entrypoint.py:67`, `stable.py:548,566,615,773,907,1016`; the fork's release scripts have diverged |
| 46 | `drop_completion_delivery` | tools/async_delegation.py | FALSE POSITIVE | string-bound dispatch: `gateway/run_notifications.py:89` names it, `_settle_durable_claim` (`:1358-1365`) resolves it with `getattr` |
| 47 | `require_ledger_owner` | tools/async_delegation_worker.py | DEFERRED | blocker is the worker-ledger routing set: `_persist_dispatch` lacks the `worker_ledger().dispatch` branch and delivery transitions write SQLite directly |
| 48 | `_kill_process_tree` | tools/browser_tool_lifecycle.py | TEST-ONLY at the pin | only `tests/tools/test_browser_process_tree.py`/`test_treekill_consolidation.py` import it; no product call site |
| 49 | `has_pending` | tools/clarify_gateway.py | TEST-ONLY at the pin | only gateway fixtures/tests request it; upstream product code calls neither |
| 50 | `CatalogApproved`, `CatalogServerError` | tui_gateway/contracts/connectors_operation.py | DEFERRED | local `CatalogRow` dropped `approved`/`server_errors` (and `enabled`/`missing_env`/`already_installed`) |
| 51 | `OnboardingEndedPayload` | tui_gateway/contracts/events.py | CORRECTLY UNWIRED | the local contract registry removed `event("onboarding.ended", …)`; the fork's setup-profile state owns that transition |
| 52 | `OnboardingStateParams` | tui_gateway/contracts/profiles_vault_complete_foreign_subagents.py | CORRECTLY UNWIRED | the local registry removed `method("onboarding.state", …)`; the fork carries onboarding state in `setup_profile` |
| 53 | test fixtures/classes (`loopback_app`, `legacy_digests`, `_seed_stores`, `cron_home`, `TestRateLimit`, `TestPatchReplace`, `TestPasswordLoginRoute`, `TestTerminalResultsCarryTheFreeTierBlock`, `explicit_static_config`, `_metric`) | tests/** | FALSE POSITIVE / TEST-ONLY | pytest collects/requests these by name; no product call site |

### Declared exclusions

`docs/FROZEN_TARGET.md` gains two rows for the deletion batch:

- `_replay_unanswered` (`tools/bot_relay.py`) — the fork's bounded `_reoffer_unanswered` replaces upstream's unbounded replay.
- `_settle_pre_upgrade_claim` (`tools/bot_relay.py`) — reachable only from the deleted helper; the fork's envelope schema has no `canonical_delivery_v1` marker.

### Explicit not-done

- No service was restarted, nothing was applied live, and no global E2E/release
  verdict is claimed.
- The 40 remaining deferred/blocked symbols above keep their named blockers. The
  largest are the durable Responses/API-turn admission, the hot-serve runtime
  family, the inbound/turn mixin inheritance (blocked by `gateway/run_turn.py`),
  the release-script admission flow, and the A2A retry-dedup path (blocked by
  `forward_to_owner`).
- `completion_admission`, `one_turn_route`, `select_apply_target`, and the A2A
  helpers are deliberately not wired: each would double-suppress or double-apply
  beside the fork's live equivalent.
- The detector still reports 3 test-release seams (`fixture`/`http-handler`
  classes) that pytest binds by name; they are not product capability gaps.

## Wave 10 (branch `fix/dead-symbols-wave10`, based on main `92a6bcc69`)

Measured with the documented command from the worktree root (warm cache symlinked
from the primary checkout):

```bash
.venv/bin/python qubit_runtime/scripts/check_dead_symbols.py --json /tmp/dead-wave10-before.json -v
# ... 6 batches ...
.venv/bin/python qubit_runtime/scripts/check_dead_symbols.py --json /tmp/dead-wave10-after.json -v
```

| state | dead_count |
|---|---:|
| main `92a6bcc69` | 85 |
| after 6 batches | **78** |

**7 symbols resolved, 0 new** (set diff of the before file and the final file).
One superseded helper was deleted (`_submit_fingerprint`) and recorded as a
Declared exclusion in `docs/FROZEN_TARGET.md`; the other six are live call sites.
One deferred architectural port was taken: activation-evidence gating of bundled
plugin-platform imports (it changes *which adapters are imported*, not delivery,
so it cannot double-deliver or double-admit).

### Wired / deleted in this wave (7)

| # | symbol | module | upstream call site (pin) |
|--:|---|---|---|
| 1 | `_submit_fingerprint` | acp_adapter/gateway_server.py | DELETED — superseded by `acp_adapter/gateway_media.py::submit_fingerprint`, called at `gateway_server.py:723-725`; declared exclusion |
| 2 | `carrier_record` | agent/reasoning_carriers.py | `agent/chat_completion_helpers.py:1764-1765` (capture into `build_assistant_message`) |
| 3 | `StreamCarriers` | agent/reasoning_carriers.py | `agent/chat_completion_helpers.py:3050-3051,3224` (stream feed + apply); `normalize_response` now carries message-level carriers |
| 4 | `launch_supervisor` | gateway/control_socket.py | `hermes_cli/data_cleanup_holders.py:28`, `gateway_supervised_restart.py:42`, `update_inventory.py:208` |
| 5 | `require_ledger_owner` | tools/async_delegation_worker.py | `tools/async_delegation.py:189,274,454` (owner-only prune/recover/mark-delivered) |
| 6 | `reset_local_session` | gateway/session_local_recovery.py | `gateway/session.py:1145-1146` (LOCAL-origin `/reset`) |
| 7 | `configured_plugin_entries` | gateway/platform_activation.py | `gateway/config_env.py:451-456`; registry gains `note_activation_env`/`activation_env`/`configured_entries` + `_resolve_all(wanted)` and `plugins_loader` records `requires_env` |

This wave corrects one earlier label: `launch_supervisor` was called a
FALSE POSITIVE in waves 1-9 because the local copy *also* reads a
`"launch_supervisor"` string key. Upstream product code calls the function from
three modules, and the fork's direct `identity.get("supervisor")` reads lose the
launch value (the protocol folds manual/desktop into `"none"`), so it was a
genuine gap and is now wired.

### Batches and focused verification

| batch | commit | command (cwd `qubit_runtime/`) | result |
|---|---|---|---|
| 1 | `25c118e76` | `pytest -q tests/acp_adapter/test_acp_images.py tests/acp_adapter/test_acp_image_staging_boundary.py tests/acp_adapter/test_acp_image_strict_reject.py tests/acp_adapter/test_gateway_prompt_content.py` | 15 passed, 1 skipped |
| 2 | `8527990c6` | `pytest -q tests/qubit_cli/test_update_inventory.py tests/qubit_cli/test_gateway_external_supervisor.py tests/qubit_cli/test_gateway_restart_external_supervisor.py tests/qubit_cli/test_pending_supervisor_recovery.py` | 27 passed |
| 3 | `f4323e0d2` | `pytest -q tests/tools/test_async_delegation.py tests/tools/test_async_delegation_worker.py tests/tools/test_async_delegation_orphan_sweep.py tests/tools/test_async_delegation_requeue.py tests/tools/test_async_delegation_retirement.py` | 53 passed, 1 skipped, 1 pre-existing fail (`test_offer_dropped_by_a_session_that_cannot_own_it_is_re_offered_to_the_owner`, byte-identical on base) |
| 4 | `ee18da12f` | `pytest -q tests/agent/test_reasoning_carriers_wiring.py tests/agent/test_stream_reasoning_details.py` | 8 passed |
| 4 | `ee18da12f` | `pytest -q tests/agent/transports/test_chat_completions.py tests/agent/test_provider_parity.py tests/agent/test_model_extra_type_guard.py tests/agent/test_bedrock_converse_reasoning_replay.py` | 104 passed |
| 5 | `1900bff4e` | `pytest -q tests/gateway/test_session_local_reset_ledger.py tests/gateway/test_switch_session_keeps_model_override.py tests/gateway/test_auto_reset_clean_context.py tests/gateway/test_heartbeat_session_boundaries.py tests/gateway/test_session_time_persistence.py tests/gateway/test_fresh_reset_skill_injection.py` | 11 passed, 1 pre-existing fail (heartbeat boundary setup, byte-identical on base) |
| 6 | `9e5a3e8d3` | `pytest -q tests/gateway/test_platform_activation_evidence.py tests/gateway/test_platform_registry.py` | 29 passed |
| 6 | `9e5a3e8d3` | `pytest -q tests/gateway/test_sms.py tests/gateway/test_platform_entry_plugin_seams.py tests/gateway/test_platform_plugin_handlers.py tests/gateway/test_startup_no_eager_platform_install.py tests/qubit_cli/test_deferred_platform_client_tools.py tests/qubit_cli/test_gateway_platform_gating.py` | 45 passed, 1 skipped |
| 6 | `9e5a3e8d3` | `pytest -q tests/qubit_cli/test_plugin_ownership_ledger.py tests/qubit_cli/test_deferred_platform_client_tools.py` | 39 passed |

Every pre-existing failure was re-run on `92a6bcc69` before being called
pre-existing (a stash of only the batch file, then the same test). No service
was restarted.

### Triage of the remaining 78 (30+ re-verified at the pin)

| # | symbol | module | verdict | evidence at `43faacfb8de7` |
|--:|---|---|---|---|
| 1 | `is_openai_bedrock_model` | agent/bedrock_adapter.py | TEST-ONLY | only `tests/agent/test_bedrock_adapter.py:1079-1095`; no product call site |
| 2 | `_prepare_deferred_agent_startup` | cli.py | CORRECTLY UNWIRED | upstream `hermes_cli/cli_agent_setup_mixin.py:614,622`; local `cli_agent_setup_mixin.py:636` records it is a no-op here (in-process agent) |
| 3 | `external_worker_exited_reason` | cron/scheduler_worker_failure.py | BLOCKED — `cron/**` lane | upstream `cron/scheduler.py:3551-3553` |
| 4 | `_bounded_task_delta` | gateway/hosted_room_discussion.py | DEFERRED — attachment port | upstream `hosted_room_discussion.py:722` |
| 5 | `_message_manifest` | gateway/hosted_room_discussion.py | DEFERRED — attachment port | upstream `hosted_room_discussion.py:204,390`; local `validate_user_payload` carries no attachments |
| 6 | `validate_task_input` | gateway/hosted_room_task_input.py | DEFERRED | upstream `hosted_room_discussion.py:22,732,777`, `hosted_room_driver.py:178`, `hosted_room_policy_checkpoint.py:17,362`; local callers carry no `input_context` |
| 7 | `acknowledge_locked` | gateway/hosted_room_work_records.py | TEST-ONLY | only `tests/gateway/test_room_work_evidence.py` |
| 8 | `prepare_delivery_locked` | gateway/hosted_room_work_records.py | TEST-ONLY | only `tests/gateway/test_room_work_evidence.py` |
| 9 | `admit_peer_turn` | gateway/platforms/api_server_bot_chat.py | DEFERRED — peer-DM route | upstream `api_server.py:3426-3427` |
| 10 | `await_live_delivery` | gateway/platforms/api_server_bot_chat.py | DEFERRED — peer-DM route | upstream `api_server.py:3482-3490`, `api_server_runs.py:955-971` |
| 11 | `create_peer_bot_chat` | gateway/platforms/api_server_bot_chat.py | DEFERRED — peer-DM route | upstream `api_server.py:3074-3075` |
| 12 | `_response_identity` | gateway/platforms/api_server_openai_routes.py | DEFERRED — durable Responses | local handler has no durable key/fingerprint/replay |
| 13 | `prepare_context` | gateway/platforms/api_server_response_admissions.py | DEFERRED — durable Responses | same |
| 14 | `identify_output` | gateway/platforms/api_server_response_identity.py | DEFERRED — durable Responses | same |
| 15 | `_admit_run_to_authority` | gateway/platforms/api_server_runs.py | DEFERRED — run admission | local run handler inlines a different path |
| 16 | `_handle_resolve_unknown_run` | gateway/platforms/api_server_runs.py | DEFERRED | clarify/resolve-unknown routes are not registered locally |
| 17 | `_handle_run_clarify` | gateway/platforms/api_server_runs.py | DEFERRED | same |
| 18 | `_run_idempotency_identity` | gateway/platforms/api_server_runs.py | DEFERRED | same |
| 19 | `_run_user_message` | gateway/platforms/api_server_runs.py | DEFERRED | same |
| 20 | `_steer_refused` | gateway/platforms/api_server_runs.py | DEFERRED | same |
| 21 | `GatewayPluginInjectionMixin` | gateway/run_plugin_injection.py | DEFERRED — inline-vs-inherit | upstream `gateway/run_inbound.py:71`; local mixin methods are inlined over ~1.4k lines |
| 22 | `GatewayTurnHygieneMixin` | gateway/run_turn_hygiene.py | DEFERRED — inline-vs-inherit | upstream `gateway/run_turn.py` base list |
| 23 | `GatewayTurnPersistenceMixin` | gateway/run_turn_persistence.py | DEFERRED — inline-vs-inherit | same |
| 24 | `GatewayTurnPrepareMixin` | gateway/run_turn_prepare.py | DEFERRED — inline-vs-inherit | same |
| 25 | `reserve_profile_home` | gateway/run_runtime.py | DEFERRED — hot-serve | upstream `run_profile_reconcile.py:270-273` |
| 26 | `serve_profile_runtime` | gateway/run_runtime.py | DEFERRED — hot-serve | same |
| 27 | `unpark_profile` | gateway/run_runtime.py | DEFERRED — hot-serve | upstream `run_profile_reconcile.py:157-197` |
| 28 | `forward_to_owner` | gateway/session_a2a.py | DEFERRED (a2a) | local `_forward_to_profile` forwards in-process; adopting the owner-dial path can forward twice |
| 29 | `admit_api_turn` | gateway/session_api_turn.py | DEFERRED | upstream uses it 58× in the API-turn authority flow; only partially ported here |
| 30 | `run_api_turn` | gateway/session_api_turn.py | DEFERRED | same |
| 31 | `recover_api_turns` | gateway/session_api_turn.py | DEFERRED | same |
| 32 | `owns_api_run` | gateway/session_api_turn.py | DEFERRED | same |
| 33 | `completion_admission` | gateway/session_automation.py | DEFERRED — double-suppress risk | upstream `run_notifications.py:1172,1267,1413,1755`; local durable delivery ledger already gates |
| 34 | `one_turn_route` | gateway/session_cli_turn.py | DEFERRED — double-apply risk | upstream `run_turn_runner.py:1180`; local `_apply_session_model_override` already applies |
| 35 | `gateway_spawn_intent_subcommand` | gateway/status.py | TEST-ONLY | `tests/_fixtures/live_system_guard.py:417` and `tests/gateway/test_gateway_command_line_matcher.py:15` |
| 36 | `extract_message_id` | plugins/platforms/a2a/protocol.py | BLOCKED — `plugins/platforms/a2a/**` lane | upstream `plugins/platforms/a2a/adapter.py:530` |
| 37 | `logged_input` | plugins/platforms/a2a/protocol.py | BLOCKED — same lane | upstream `adapter.py:542` |
| 38 | `message_context_id` | plugins/platforms/a2a/protocol.py | BLOCKED — same lane | upstream protocol/adapter retry admission |
| 39 | `_gateway_launch` | qubit_cli/cli_init_mixin.py | CORRECTLY UNWIRED | upstream callers `_ensure_gateway_session`/`_new_session_id` do not exist here; the fork builds the agent in-process |
| 40 | `_record_shell_hook_consent` | qubit_cli/cli_init_mixin.py | CORRECTLY UNWIRED | same upstream path |
| 41 | `api_auth_ws_ticket` | qubit_cli/dashboard_auth/routes.py | FALSE POSITIVE | FastAPI handler wired by decorator; upstream `hermes_cli/dashboard_auth/routes.py:557` |
| 42 | `GatewayHeartbeatManager` | qubit_cli/gateway_automation.py | CORRECTLY UNWIRED | upstream `cli_loops_mixin.py:554-555`; this fork never selects the remote manager |
| 43 | `GatewayLoopManager` | qubit_cli/gateway_automation.py | CORRECTLY UNWIRED | upstream `cli_loops_mixin.py:565-566` |
| 44 | `exit_on_resume_refusal` | qubit_cli/gateway_chat.py | CORRECTLY UNWIRED | the gateway launch error path owns the stream-json contract; raising `SystemExit` would bypass it |
| 45 | `_tool_prefix` | qubit_cli/plugins_activation_live.py | CORRECTLY UNWIRED | the fork lists each tool via `_tool_listing`; upstream's prefix+count line is a different message |
| 46 | `merge_endpoint_seed` | qubit_cli/profile_model_seed.py | FALSE POSITIVE | string-bound `_lazy("qubit_cli.profile_model_seed", "merge_endpoint_seed")` at `tui_gateway/methods_profiles.py:529` |
| 47 | `_resolve_source_target` | qubit_cli/source_releases.py | CORRECTLY UNWIRED | local public resolver is the deliberate R2-every-channel superset; upstream `source_releases.py:144` |
| 48 | `select_apply_target` | qubit_cli/update_cmd_check.py | DEFERRED | upstream `update_cmd.py:1862`; local flow inlines the block and drops `forward_only`/`rides_default_channel` |
| 49 | `app_lifespan` | qubit_cli/web_server_app.py | DEFERRED | needs the owner `gateway_runner`; local single `_lifespan` diverged |
| 50 | `terminal_worker_receipt` | qubit_state_terminal.py | TEST-ONLY | only `tests/hermes_state/test_terminal_retirement.py` |
| 51 | `wall_clock_sleep` | scripts/code_health/py_rules_sleep.py | DEFERRED | upstream registers HX013 (`py_rules.py:17,876`, `config.py:143-150`); local rule table stops at HX012 |
| 52 | `_abandon_confirmed`, `_abandon_prompt`, `_is_claim_run`, `_require_release_line` | scripts/releases/entrypoint.py | DEFERRED | local release flow uses `_require_remote_main` and a different abandon path |
| 53 | `release_line_refspecs`, `require_targets_match_claim`, `baseline_covers`, `check_controller`, `authenticated_push` | scripts/releases/stable.py | DEFERRED | local release scripts have diverged; release-critical admission |
| 54 | `drop_completion_delivery` | tools/async_delegation.py | FALSE POSITIVE | string-bound dispatch (`gateway/run_notifications.py:73`, local `:89`, resolved with `getattr`) |
| 55 | `_kill_process_tree` | tools/browser_tool_lifecycle.py | TEST-ONLY | upstream product code calls `_legacy_kill_process_tree`; only `tests/tools/test_browser_process_tree.py` names this one |
| 56 | `has_pending` | tools/clarify_gateway.py | TEST-ONLY | upstream product code never calls it; only fixtures/tests |
| 57 | `CatalogApproved`, `CatalogServerError` | tui_gateway/contracts/connectors_operation.py | DEFERRED | local `ConnectionOperationTarget` dropped `approved`/`server_errors` (and `enabled`/`missing_env`/`already_installed`) |
| 58 | `OnboardingEndedPayload` | tui_gateway/contracts/events.py | CORRECTLY UNWIRED | the local registry removed `onboarding.ended`; setup-profile state owns the transition |
| 59 | `OnboardingStateParams` | tui_gateway/contracts/profiles_vault_complete_foreign_subagents.py | CORRECTLY UNWIRED | the file's own comment records the deliberate replacement |
| 60 | `explicit_static_config`, `loopback_app`, `cron_home`, `legacy_digests`, `_seed_stores`, `_metric`, `TestRateLimit`, `TestPasswordLoginRoute`, `TestPatchReplace`, `TestTerminalResultsCarryTheFreeTierBlock` | tests/** | FALSE POSITIVE / TEST-ONLY | pytest fixtures/classes collected or requested by name; `_metric` has no caller in any test |

### Declared exclusions

`docs/FROZEN_TARGET.md` gains one row for the deletion batch:

- `_submit_fingerprint` (`acp_adapter/gateway_server.py`) — superseded by
  `acp_adapter/gateway_media.py::submit_fingerprint`; the split-module version
  fingerprints the editor content before staging so a retry does not refetch a
  remote image.

### Explicit not-done

- No service was restarted, nothing was applied live, and no global E2E/release
  verdict is claimed.
- The remaining 78 keep the blockers in the table above (33 rows group more than
  one symbol). The largest are the durable Responses/API-turn admission, the
  hot-serve runtime family, the inbound/turn mixin inheritance, the release
  admission flow, the A2A retry-dedup path (blocked by `forward_to_owner` and the
  lane-owned `plugins/platforms/a2a/**`), and the peer-DM routes.
- `completion_admission` and `one_turn_route` stay unwired: each would
  double-suppress or double-apply beside the fork's live equivalent.
- The detector still reports 3 test-release seams (`fixture`/`http-handler`
  classes) and several test fixtures; they are not product capability gaps.
