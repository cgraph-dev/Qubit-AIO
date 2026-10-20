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
