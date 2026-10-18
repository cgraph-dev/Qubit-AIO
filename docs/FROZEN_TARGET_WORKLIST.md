# FROZEN-TARGET WORKLIST — production symbols still missing

Target: upstream `43faacfb8de7b29548547a7fbdedad0cc6e7391c` (frozen 2026-10-16).
Measured by `scripts/audit_sync_completeness.py --rev 43faacfb8de7... --show-symbols`
on 2026-10-19, tree at `35fdb1e33`.

**TOTAL: 296 production symbols across 78 files.** Tests (1,794 symbols / 901 absent files)
are non-blocking and out of scope for retirement.

Baseline at the freeze was 2,623 total / 296 production reached after a wave that closed
1,434. Re-measure after every wave with:

```bash
.venv/bin/python scripts/frozen_target_progress.py
```

## Lanes (2026-10-19 wave — owner ruling: port literally, prune afterwards)

Six port lanes, each in its own `git worktree` and branch, split by **disjoint file sets**,
plus one read-only verifier. Every lane commits as it goes.

| lane | branch | worktree | symbols | files |
|---|---|---|---|---|
| P1-A | `frozen/p1-A` | `/tmp/ft-p1-A` | 87 | 24 |
| P1-B | `frozen/p1-B` | `/tmp/ft-p1-B` | 86 | 24 |
| P2-A | `frozen/p2-A` | `/tmp/ft-p2-A` | 33 | 8 |
| P2-B | `frozen/p2-B` | `/tmp/ft-p2-B` | 32 | 4 |
| P3-A | `frozen/p3-A` | `/tmp/ft-p3-A` | 29 | 8 |
| P3-B | `frozen/p3-B` | `/tmp/ft-p3-B` | 29 | 10 |
| verify | `frozen/verify` | `/tmp/ft-verify` | — | read-only |

### P1-A — 87 sym / 24 files (tools + hermes_cli)

| missing | upstream path (ours under `qubit_runtime/`) | symbols to add |
|---|---|---|
| 24 | `tools/send_message_targets.py` | `_BUZZ_UUID_RE`, `_EMAIL_TARGET_RE`, `_FEISHU_TARGET_RE`, `_NUMERIC_TOPIC_RE`, `_PHOTON_DM_GUID_RE`, `_PLATFORM_PARSERS`, `_SLACK_FORMS`, `_SLACK_MENTION_RE`, `_SLACK_TARGET_RE`, `_SLACK_THREAD_TARGET_RE`, `_SLACK_USER_NAME_RE`, `_TELEGRAM_TOPIC_TARGET_RE`, `_UNRESOLVED`, `_WEIXIN_TARGET_RE`, `_WHATSAPP_JID_RE`, `_YUANBAO_TARGET_RE`, `_parse_matrix`, `_parse_nonempty`, `_parse_regex_groups`, `_parse_regex_stripped`, `_parse_signal`, `_parse_slack`, `_parse_telegram`, `_parse_yuanbao` |
| 9 | `hermes_cli/anon_auth.py` | `_save_guest_state`, `free_inference_active`, `guest_allowed`, `guest_without_free_tier`, `held_connectors_token`, `is_connectors_only`, `on_local_desktop_backend`, `set_guest_flag`, `stamp_legacy_guest_claim` |
| 6 | `tools/skills_guard.py` | `_CTX_DATIVE`, `_CTX_DEST`, `_CTX_SCOPE`, `_DOC_NOTE_IDS`, `_SUDO_PREFIX`, `_community_note` |
| 6 | `hermes_cli/observability/shared_metrics_contract.py` | `RETIRED_TOOLSET_NAMES`, `RETIRED_TOOL_NAMES`, `_CONNECTOR_CODE_CLASSES`, `_CONNECTOR_FAMILY_CLASSES`, `_LIVE_TOOL_NAMES`, `connector_error_class` |
| 5 | `hermes_cli/source_releases.py` | `_describe_release_failure`, `_head_containing`, `_resolve_release`, `_resolve_source_target`, `_resolve_stable` |
| 4 | `tools/send_message_senders.py` | `_gateway_platform_module`, `_send_bluebubbles`, `_send_weixin`, `_send_yuanbao` |
| 4 | `hermes_cli/cli_init_mixin.py` | `_ROUTE_FLAGS`, `_explicit_launch_skills`, `_gateway_launch`, `_record_shell_hook_consent` |
| 4 | `hermes_constants.py` | `_ENV_REFERENCE`, `_hermes_root_of`, `_platform_default_hermes_home`, `hermes_root_for_environment` |
| 2 | `tools/bot_live_delivery.py` | `_bot_chat_tip`, `_poll_receipt` |
| 2 | `tools/bot_relay.py` | `_replay_unanswered`, `_settle_pre_upgrade_claim` |
| 2 | `tools/environments/local.py` | `_terminal_env`, `_user_tools_first` |
| 2 | `tools/image_generation_tool.py` | `_STYLE_IMAGE_URL_PARAM`, `_STYLE_PROMPT_PARAM` |
| 2 | `tools/send_message_tool.py` | `_PLUGIN_STANDALONE_MEDIA`, `_TEXT_SENDERS` |
| 2 | `hermes_cli/cli_commands_session_tools.py` | `_print_local_delegations`, `_stop_background_work` |
| 2 | `hermes_cli/debug.py` | `_PENDING_LOCK_HOLDER`, `_keep_after_sweep` |
| 2 | `hermes_cli/nous_subscription.py` | `MANAGED_IMAGE_DEFAULT_KREA_MODEL`, `_krea_image_default` |
| 2 | `hermes_cli/web_routers/dashboard_ui.py` | `_plugin_activated`, `_plugin_enable_sets` |
| 1 | `tools/approval.py` | `_sync_permanent_from_disk` |
| 1 | `tools/browser_tool_real_profile.py` | `real_profile_fallback_note` |
| 1 | `tools/connectors/portal/client.py` | `_PORTAL_ROUTE_WORDS` |
| 1 | `tools/memory_tool.py` | `_REPLACE_REMOVED` |
| 1 | `tools/terminal_tool_backends.py` | `_local_config_from_config` |
| 1 | `hermes_cli/sessions_repair_profiles.py` | `_ledgered_lineages` |
| 1 | `hermes_cli/web_server.py` | `_probe_install_before_state_db` |

### P1-B — 86 sym / 24 files (tools/connectors, browser, hermes_cli backup/migration)

| missing | upstream path | symbols to add |
|---|---|---|
| 14 | `tools/connectors/gateway/client.py` | `STORAGE_TIMEOUT_SECONDS`, `StorageTransport`, `_EXECUTE_ROUTE`, `_LEGACY_EXECUTE_ORIGINS`, `_MAX_RETRY_AFTER_SECONDS`, `_RETRY_ONCE_STATUSES`, `_Reply`, `_STORAGE_CHUNK_BYTES`, `_STORAGE_GET_ROUTE`, `_STORAGE_PUT_ROUTE`, `_call_failure`, `_error_dict`, `_rejects_single_call_form`, `_retry_wait` |
| 12 | `tools/browser_use_cli.py` | `_CodeUrlVerdict`, `_LOCAL_FALLBACK_SENTINEL`, `_REAL_PROFILE_FALLBACK_SENTINEL`, `_browser_use_api_key_set`, `_code_url_verdict`, `_exec_route_is_sidecar`, `_export_real_profile_note`, `_real_profile_fallback`, `_real_profile_note_delivered`, `_route_exec`, `_sidecar_daemon_name`, `_take_real_profile_note` |
| 7 | `hermes_cli/backup.py` | `_import_external_member`, `_import_members`, `_import_members_exclusive`, `_restore_quick_snapshot_exclusive`, `_restore_snapshot_entry`, `_revive_gateway_after_import`, `_snapshot_epochs_restorable` |
| 6 | `hermes_cli/left_core_migration.py` | `_bundled_plugin_selected`, `langfuse_in_use`, `legacy_plugin_alias`, `legacy_plugin_keys`, `moved_plugin_hint`, `spotify_in_use` |
| 5 | `tools/memory_tool_store.py` | `PATCH_DROP_HINT`, `_patch_entry`, `_repeated_span_message`, `_span_occurrences`, `_store_target` |
| 5 | `hermes_cli/_launchers.py` | `_TOKEN_DUPLICATE`, `_new_console_environment`, `_read_environment_block`, `new_console_hermes_root`, `owns_user_path` |
| 5 | `hermes_cli/web_server_sessions.py` | `_mutate_offline_store`, `_mutate_session_request`, `_native_history_transport`, `_session_mutation_context`, `_with_session_maintenance` |
| 4 | `hermes_cli/worktree_ops.py` | `_HERMES_LOCK_RE`, `_retain_worktree_for_session`, `_retained_tree_is_live`, `_worktree_lock_reason` |
| 3 | `tools/connectors/gateway/bridge.py` | `NOT_SET_UP`, `NOT_SET_UP_MESSAGE`, `_execute_with_files` |
| 3 | `hermes_cli/main_tui_launch.py` | `_TUI_UNFORWARDED`, `_bypass_chat_launch`, `_refuse_unforwarded_tui_flags` |
| 2 | `tools/browser_vault_tool.py` | `BROWSER_VAULT_SCHEMA`, `_handle_vault` |
| 2 | `tools/fuzzy_match.py` | `_match_starts_mid_line`, `boundary_note` |
| 2 | `tools/mcp_tool_config.py` | `_outside_package`, `_packaged_npx_entry` |
| 2 | `hermes_cli/agent_plugins.py` | `_SERVER_DECLARATION_KEYS`, `_server_timeouts` |
| 2 | `hermes_cli/config.py` | `_cmd_config_show`, `_worker_config_snapshot` |
| 2 | `hermes_cli/free_tier_bootstrap.py` | `_due_changed`, `refresh_record` |
| 2 | `hermes_cli/web_routers/cron.py` | `_SESSION_DOC_MATCH_SECONDS`, `_doc_writer` |
| 2 | `hermes_cli/web_routers/sessions.py` | `_owner_db_for`, `get_session_mutation_snapshot` |
| 1 | `tools/browser_tool.py` | `_hybrid_routes_locally` |
| 1 | `tools/browser_tool_session.py` | `real_profile_note` |
| 1 | `tools/delegate_tool.py` | `_credential_overrides` |
| 1 | `tools/skills_tool_dedup.py` | `dedup_skill_view` |
| 1 | `hermes_cli/profiles.py` | `_find_our_wrapper` |
| 1 | `hermes_cli/update_cmd_check.py` | `select_apply_target` |

### P2-A — 33 sym / 8 files (tui_gateway)

| missing | upstream path | symbols to add |
|---|---|---|
| 19 | `tui_gateway/methods_onboarding.py` | `_NoModelForTheRun`, `_ONBOARDING_PROFILE`, `_active_run`, `_builds_onboarding`, `_carry_onboarding_toolset`, `_claim_before_run`, `_discard_unfinished_runs`, `_due_now`, `_due_stamp`, `_keeps_toolsets`, `_live_session_ids`, `_onboarding_prelude`, `_onboarding_start_lock`, `_onboarding_toolsets`, +5 more (take the exact list from the audit JSON) |
| 7 | `tui_gateway/change_watcher.py` | `_RACY_MTIME`, `_files_sig`, `_settled_mtime`, `_skin_home_for`, `_skin_peers_by_home`, `_skin_sig_by_home`, `_under_home` |
| 2 | `tui_gateway/session_workdir.py` | `_is_other_profiles_cwd`, `_profile_default_cwd` |
| 1 | `tui_gateway/compute_host_bridge.py` | `_cancel_session_requests` |
| 1 | `tui_gateway/entry.py` | `_claim_launch_home` |
| 1 | `tui_gateway/hosted_room_driver.py` | `AttachmentsUnsupportedError` |
| 1 | `tui_gateway/methods_free_tier.py` | `_trial_claimable` |
| 1 | `tui_gateway/methods_voice.py` | `_voice_event_transport` |

### P2-B — 32 sym / 4 files (tui_gateway contracts + transport)

| missing | upstream path | symbols to add |
|---|---|---|
| 16 | `tui_gateway/contracts/server_requests.py` | `SetupBeat`, `SetupBeatRequestParams`, `SetupBeatResult`, `SetupBuildAnswer`, `SetupConnectorsAnswer`, `SetupConnectorsCard`, `SetupLayoutAnswer`, `SetupNameAnswer`, `SetupNameCard`, `SetupOption`, `SetupOptionsCard`, `SetupPluginsAnswer`, `SetupPluginsCard`, `SetupThemeAnswer`, +2 more (audit JSON) |
| 8 | `tui_gateway/contracts/profiles_vault_complete_foreign_subagents.py` | `OnboardingFactsSource`, `OnboardingRunStateResult`, `OnboardingSetRunParams`, `OnboardingSkipParams`, `OnboardingSkipResult`, `OnboardingStartParams`, `OnboardingStartResult`, `OnboardingStateParams` |
| 7 | `tui_gateway/ws.py` | `_authority_connection`, `_dashboard_client_touch_pending`, `_dashboard_client_touch_pool`, `_dispatch_request`, `_is_unknown_method`, `_start_backend_liveness`, `_touch_dashboard_client_marker` |
| 1 | `tui_gateway/methods_session_interrupt.py` | `_recover_unreachable_compute_host_turn` |

### P3-A — 29 sym / 8 files (scripts + gateway + agent)

| missing | upstream path | symbols to add |
|---|---|---|
| 15 | `scripts/releases/stable.py` | `CONTROLLER_BRANCH`, `CONTROLLER_REF`, `CONTROLLER_WORKFLOW`, `PACKAGED_BY`, `RELEASE_LINE_BRANCHES`, `SMOKE_CALLS`, `SMOKE_TARGETS`, `authenticated_push`, `baseline_covers`, `check_controller`, `claim_run_title`, `is_claim_run`, `release_line_contains`, `release_line_refspecs`, +1 (audit JSON) |
| 4 | `gateway/run_notifications.py` | `_COMPLETION_INJECTION_RETRY_MAX_DELAY`, `_MAX_COMPLETION_INJECTION_ATTEMPTS`, `_local_completion_card`, `_watch_notification_metadata` |
| 3 | `gateway/authz_mixin.py` | `_ALLOWED_USERS_ENV`, `_ALLOW_ALL_ENV`, `_ALLOW_BOTS_ENV` |
| 2 | `gateway/run.py` | `_OWN_POLICY_OPEN_ENV`, `_housekeeping_media_caches` |
| 2 | `agent/tool_dispatch_helpers.py` | `_extend_tool_timeout`, `_requires_extended_tool_deadline` |
| 1 | `gateway/relay/adapter.py` | `_PERMANENT_REFUSALS` |
| 1 | `gateway/session_local_migration.py` | `_endpoint_owner` |
| 1 | `agent/model_metadata.py` | `_llamacpp_context` |

### P3-B — 29 sym / 10 files (gateway api, acp, cron, releases)

| missing | upstream path | symbols to add |
|---|---|---|
| 8 | `gateway/platforms/api_server_runs.py` | `_ROOM_CONTROL_PERMISSIONS`, `_admit_run_to_authority`, `_handle_resolve_unknown_run`, `_handle_run_clarify`, `_respond_authority_run`, `_run_idempotency_identity`, `_run_user_message`, `_steer_refused` |
| 6 | `scripts/releases/entrypoint.py` | `RUN_FIELDS`, `_RELEASE_LINE_PREFIX`, `_abandon_confirmed`, `_abandon_prompt`, `_is_claim_run`, `_require_release_line` |
| 5 | `gateway/run_turn_runner.py` | `_LEDGER_PASSTHROUGH_KEYS`, `_NoStreamConsumer`, `_finite_viewer_turn`, `_persisted_turn`, `_settle_api_runtime_lock` |
| 3 | `gateway/hosted_room_discussion.py` | `_bounded_task_delta`, `_event_attachments`, `_message_manifest` |
| 2 | `acp_adapter/gateway_server.py` | `_stage_user_content`, `_submit_fingerprint` |
| 1 | `gateway/config.py` | `_needs_extra` |
| 1 | `gateway/pairing.py` | `_PLATFORM_ALLOWLIST_ENV` |
| 1 | `gateway/session_api_turn.py` | `_recover_api_turns` |
| 1 | `acp_adapter/gateway_commands.py` | `_PROMPTS` |
| 1 | `cron/scheduler_delivery.py` | `_HOME_TARGET_ENV_VARS` |

## Known follow-ups outside this wave

- **`docs/FROZEN_TARGET.md` was deleted** in `eafb54e26` (English-only doc cull) while still
  holding the pin's only prose home; restored 2026-10-19 for the current pin.
- **1,794 test symbols / 901 absent test files** remain. Non-blocking for retirement, but
  they are real coverage for ported production code — schedule a test wave after the
  production gap closes.
- **`scripts/frozen_target_progress.py:64`** referenced `docs/FROZEN_TARGET.md` while it did
  not exist; the doc is back, so the pointer resolves again.
- **Tests that read source text are banned** upstream (`AGENTS.md`), so any test wave writes
  behaviour tests, not source-shape assertions.
