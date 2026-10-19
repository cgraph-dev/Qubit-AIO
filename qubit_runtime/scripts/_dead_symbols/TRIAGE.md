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

