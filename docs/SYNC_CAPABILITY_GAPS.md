# Capability audit — upstream 69948c0057..bd0affe5

delta files: 4236  (python: 2116)
symbols checked: 5572   MISSING in Qubit: 346
files with gaps: 92

## missing symbols by subsystem

    152  qubit_cli
    150  tests
     28  tui_gateway
     10  cron
      3  tools
      2  evals
      1  apps

## detail


### qubit_runtime/tests/tui_gateway/test_tui_gateway_server.py  (43 missing)
  - _join_turn_thread  <- tests/tui_gateway/test_tui_gateway_server.py
  - _mk_merge_fake_db  <- tests/tui_gateway/test_tui_gateway_server.py
  - _wake_lease_double  <- tests/tui_gateway/test_tui_gateway_server.py
  - test_accepted_interrupt_resumes_wake_mismatch_does_not  <- tests/tui_gateway/test_tui_gateway_server.py
  - test_background_agent_kwargs_falls_back_to_config_disabled_toolsets  <- tests/tui_gateway/test_tui_gateway_server.py
  - test_background_agent_kwargs_forwards_agent_disabled_toolsets  <- tests/tui_gateway/test_tui_gateway_server.py
  - test_browser_manage_use_swaps_the_profiles_browser_tools_for_new_agents  <- tests/tui_gateway/test_tui_gateway_server.py
  - test_command_dispatch_memory_pending_reviews_staged_writes  <- tests/tui_gateway/test_tui_gateway_server.py
  - test_command_dispatch_reviews_staged_skill_writes  <- tests/tui_gateway/test_tui_gateway_server.py
  - test_command_dispatch_skills_gate_off_answers_instead_of_refusing  <- tests/tui_gateway/test_tui_gateway_server.py
  - test_completion_cwd_explicit_pick_wins_over_profile  <- tests/tui_gateway/test_tui_gateway_server.py
  - test_completion_cwd_inherited_workspace_without_profile_cfg_kept  <- tests/tui_gateway/test_tui_gateway_server.py
  - test_completion_cwd_profile_overrides_inherited_workspace  <- tests/tui_gateway/test_tui_gateway_server.py
  - test_file_attach_workspace_storage_falls_back_for_non_local_workspace  <- tests/tui_gateway/test_tui_gateway_server.py
  - test_file_attach_workspace_storage_follows_the_owning_profiles_config  <- tests/tui_gateway/test_tui_gateway_server.py
  - test_get_usage_projects_live_compression_count  <- tests/tui_gateway/test_tui_gateway_server.py
  - test_goal_continuation_starts_no_turn_after_stop  <- tests/tui_gateway/test_tui_gateway_server.py
  - test_interrupt_error_after_tts_stop_still_resumes_wake  <- tests/tui_gateway/test_tui_gateway_server.py
  - test_interrupt_resumes_voice_wake_owner_not_a_foreign_lease  <- tests/tui_gateway/test_tui_gateway_server.py
  - test_load_disabled_toolsets_none_when_unset_or_config_fails  <- tests/tui_gateway/test_tui_gateway_server.py
  - test_load_disabled_toolsets_reads_agent_config  <- tests/tui_gateway/test_tui_gateway_server.py
  - test_load_enabled_toolsets_honors_disabled_project_on_configured_fallback  <- tests/tui_gateway/test_tui_gateway_server.py
  - test_load_enabled_toolsets_honors_disabled_project_on_focus_path  <- tests/tui_gateway/test_tui_gateway_server.py
  - test_make_agent_disabled_toolsets_none_by_default  <- tests/tui_gateway/test_tui_gateway_server.py
  - test_make_agent_forwards_agent_disabled_toolsets  <- tests/tui_gateway/test_tui_gateway_server.py

### qubit_runtime/tests/agent/test_relay_runtime_plugins.py  (16 missing)
  - ACTIVE_ACTIVATION_REPORT  <- tests/agent/test_relay_runtime_plugins.py
  - EMPTY_ACTIVATION_REPORT  <- tests/agent/test_relay_runtime_plugins.py
  - HOST_CONFLICT  <- tests/agent/test_relay_runtime_plugins.py
  - test_activation_report_controls_managed_execution  <- tests/agent/test_relay_runtime_plugins.py
  - test_active_log_names_every_loaded_configuration_file  <- tests/agent/test_relay_runtime_plugins.py
  - test_dynamic_host_conflict_is_foreign_too  <- tests/agent/test_relay_runtime_plugins.py
  - test_dynamic_records_are_handed_to_relay_unparsed  <- tests/agent/test_relay_runtime_plugins.py
  - test_empty_ambient_discovery_does_not_enable_managed_execution  <- tests/agent/test_relay_runtime_plugins.py
  - test_first_profile_ambient_plugin_decision_applies_to_later_profile  <- tests/agent/test_relay_runtime_plugins.py
  - test_legacy_exporter_env_warns_without_disabling_ambient_discovery  <- tests/agent/test_relay_runtime_plugins.py
  - test_plugin_error_containing_conflict_text_is_not_misclassified  <- tests/agent/test_relay_runtime_plugins.py
  - test_real_binding_discovers_user_and_ignores_project_config  <- tests/agent/test_relay_runtime_plugins.py
  - test_real_binding_explicit_config_replaces_user_and_ignores_project  <- tests/agent/test_relay_runtime_plugins.py
  - test_real_binding_hands_dynamic_records_to_relay_and_fails_open_on_rejection  <- tests/agent/test_relay_runtime_plugins.py
  - test_real_binding_leaves_foreign_plugin_host_unchanged  <- tests/agent/test_relay_runtime_plugins.py
  - test_unset_config_uses_relay_discovery  <- tests/agent/test_relay_runtime_plugins.py

### qubit_runtime/qubit_cli/main_desktop.py  (15 missing)
  - _desktop_ancestor_in  <- hermes_cli/main_desktop.py
  - _diagnose_esbuild_ignore_scripts  <- hermes_cli/main_desktop.py
  - _explicit_profile_args  <- hermes_cli/main_desktop.py
  - _installed_desktop_apps  <- hermes_cli/main_desktop.py
  - _installed_desktop_launch_target  <- hermes_cli/main_desktop.py
  - _launch_installed_macos_desktop_app  <- hermes_cli/main_desktop.py
  - _macos_signature_summary  <- hermes_cli/main_desktop.py
  - _macos_signing_downgrade_error  <- hermes_cli/main_desktop.py
  - _owns_installed_desktop_apps  <- hermes_cli/main_desktop.py
  - _packaged_desktop_current_for_head  <- hermes_cli/main_desktop.py
  - _packaged_resources_dir  <- hermes_cli/main_desktop.py
  - _refresh_installed_desktop_apps  <- hermes_cli/main_desktop.py
  - _runs_from  <- hermes_cli/main_desktop.py
  - _site_packages_install_kind  <- hermes_cli/main_desktop.py
  - _update_owned_macos_bundles  <- hermes_cli/main_desktop.py

### qubit_runtime/tests/tools/test_kanban_tools.py  (15 missing)
  - TestDefaultTaskId  <- tests/tools/test_kanban_tools.py
  - _expire_claim  <- tests/tools/test_kanban_tools.py
  - test_comment_bare_call_outside_worker_names_kanban_list  <- tests/tools/test_kanban_tools.py
  - test_create_tui_subscription_binds_to_live_session_after_compaction_fork  <- tests/tools/test_kanban_tools.py
  - test_kanban_task_id_descriptions_state_the_non_worker_case  <- tests/tools/test_kanban_tools.py
  - test_live_tui_session_key_fails_open_to_original_key  <- tests/tools/test_kanban_tools.py
  - test_live_tui_session_key_uses_profile_store  <- tests/tools/test_kanban_tools.py
  - test_reclaim_loses_to_a_worker_registering_mid_sweep  <- tests/tools/test_kanban_tools.py
  - test_schedule_exposed_to_codex_runtime_workers  <- tests/tools/test_kanban_tools.py
  - test_schedule_goal_mode_refused  <- tests/tools/test_kanban_tools.py
  - test_schedule_parks_current_worker_with_reason  <- tests/tools/test_kanban_tools.py
  - test_schedule_rejects_invalid_reason_and_unowned_contexts  <- tests/tools/test_kanban_tools.py
  - test_show_bare_call_outside_worker_returns_orientation_not_error  <- tests/tools/test_kanban_tools.py
  - test_worker_the_dispatcher_never_recorded_keeps_its_claim_or_never_starts  <- tests/tools/test_kanban_tools.py
  - test_worker_tool_bare_call_outside_worker_error_is_actionable  <- tests/tools/test_kanban_tools.py

### qubit_runtime/tests/tools/test_local_env_blocklist.py  (14 missing)
  - ADAPTER_SECRETS  <- tests/tools/test_local_env_blocklist.py
  - OPERATOR_SECRETS  <- tests/tools/test_local_env_blocklist.py
  - _user_platform_plugin  <- tests/tools/test_local_env_blocklist.py
  - test_a_failed_rescan_keeps_the_denials_it_already_knew  <- tests/tools/test_local_env_blocklist.py
  - test_a_partial_scan_is_rescanned_on_the_next_spawn  <- tests/tools/test_local_env_blocklist.py
  - test_a_symlinked_home_alias_shares_its_profiles_plugin_declarations  <- tests/tools/test_local_env_blocklist.py
  - test_adapter_and_provider_profile_secrets_never_reach_children  <- tests/tools/test_local_env_blocklist.py
  - test_inheriting_child_gets_provider_keys_but_never_adapter_secrets  <- tests/tools/test_local_env_blocklist.py
  - test_manifest_scan_follows_plugin_discovery_and_sees_mtime_preserving_edits  <- tests/tools/test_local_env_blocklist.py
  - test_passthrough_accepted_before_an_adapter_owns_the_name_stops_forwarding_it  <- tests/tools/test_local_env_blocklist.py
  - test_runtime_adapter_secret_follows_the_bound_profiles_registry  <- tests/tools/test_local_env_blocklist.py
  - test_unreadable_bundled_manifest_fails_the_policy_instead_of_dropping_it  <- tests/tools/test_local_env_blocklist.py
  - test_unreadable_platform_manifest_fails_closed  <- tests/tools/test_local_env_blocklist.py
  - test_user_platform_plugin_secrets_belong_to_their_own_profile  <- tests/tools/test_local_env_blocklist.py

### qubit_runtime/qubit_cli/gitlock.py  (11 missing)
  - LAZY_FETCH_GC_TIMEOUT_SECONDS  <- hermes_cli/gitlock.py
  - _gc_auto_pack_limit  <- hermes_cli/gitlock.py
  - _migrate_earlier_maintenance_keys  <- hermes_cli/gitlock.py
  - _pack_dir  <- hermes_cli/gitlock.py
  - _partial_clone_filter  <- hermes_cli/gitlock.py
  - consolidate_lazy_fetch_packs  <- hermes_cli/gitlock.py
  - disable_tree0_auto_maintenance  <- hermes_cli/gitlock.py
  - fetch_with_partial_clone_recovery  <- hermes_cli/gitlock.py
  - heal_shallow_history  <- hermes_cli/gitlock.py
  - is_partial_clone_pack_objects_crash  <- hermes_cli/gitlock.py
  - mark_unmarked_packs_promisor  <- hermes_cli/gitlock.py

### qubit_runtime/qubit_cli/linux_desktop_entry.py  (11 missing)
  - APP_ID  <- hermes_cli/linux_desktop_entry.py
  - LEGACY_DESKTOP_ENTRY_NAME  <- hermes_cli/linux_desktop_entry.py
  - _alias_legacy_desktop_entry  <- hermes_cli/linux_desktop_entry.py
  - _can_serve_desktop  <- hermes_cli/linux_desktop_entry.py
  - _desktop_argv_tail  <- hermes_cli/linux_desktop_entry.py
  - _embedded_launcher_target  <- hermes_cli/linux_desktop_entry.py
  - _is_this_checkout_managed_cli  <- hermes_cli/linux_desktop_entry.py
  - _launcher_tree  <- hermes_cli/linux_desktop_entry.py
  - _persisted_exec_serves_desktop  <- hermes_cli/linux_desktop_entry.py
  - _render_legacy_alias_entry  <- hermes_cli/linux_desktop_entry.py
  - _tree_desktop_state  <- hermes_cli/linux_desktop_entry.py

### qubit_runtime/tui_gateway/methods_tools.py  (11 missing)
  - _cmd_memory  <- tui_gateway/methods_tools.py
  - _cmd_skills  <- tui_gateway/methods_tools.py
  - _is_registry_command  <- tui_gateway/methods_tools.py
  - _pending_subcommand_store  <- tui_gateway/methods_tools.py
  - _profile_skill_command  <- tui_gateway/methods_tools.py
  - _run_pending_review  <- tui_gateway/methods_tools.py
  - _session_toolsets  <- tui_gateway/methods_tools.py
  - _skill_dispatch_or_refuse  <- tui_gateway/methods_tools.py
  - _tui_model_friction  <- tui_gateway/methods_tools.py
  - _worker_refused_skill  <- tui_gateway/methods_tools.py
  - _write_approval_mode_setter  <- tui_gateway/methods_tools.py

### qubit_runtime/qubit_cli/cli_billing_mixin.py  (10 missing)
  - _add_card_choices  <- hermes_cli/cli_billing_mixin.py
  - _allow_remote_spending_choices  <- hermes_cli/cli_billing_mixin.py
  - _auto_reload_agree_choices  <- hermes_cli/cli_billing_mixin.py
  - _auto_reload_top_choices  <- hermes_cli/cli_billing_mixin.py
  - _cancel_row  <- hermes_cli/cli_billing_mixin.py
  - _cancel_sub_choices  <- hermes_cli/cli_billing_mixin.py
  - _change_menu_tail  <- hermes_cli/cli_billing_mixin.py
  - _change_plan_row  <- hermes_cli/cli_billing_mixin.py
  - _portal_row  <- hermes_cli/cli_billing_mixin.py
  - _topup_menu_choices  <- hermes_cli/cli_billing_mixin.py

### qubit_runtime/qubit_cli/observability/relay_shared_metrics.py  (10 missing)
  - _PeakLineage  <- hermes_cli/observability/relay_shared_metrics.py
  - _absorb_peak  <- hermes_cli/observability/relay_shared_metrics.py
  - close_session_run  <- hermes_cli/observability/relay_shared_metrics.py
  - record_known_cache_break  <- hermes_cli/observability/relay_shared_metrics.py
  - record_model_switch_after  <- hermes_cli/observability/relay_shared_metrics.py
  - record_process_mark  <- hermes_cli/observability/relay_shared_metrics.py
  - record_process_marks_saved  <- hermes_cli/observability/relay_shared_metrics.py
  - record_session_friction  <- hermes_cli/observability/relay_shared_metrics.py
  - record_session_tools  <- hermes_cli/observability/relay_shared_metrics.py
  - rotate_segment  <- hermes_cli/observability/relay_shared_metrics.py

### qubit_runtime/qubit_cli/backup.py  (8 missing)
  - _cron_jobs_list  <- hermes_cli/backup.py
  - _discard_failed_zip_members  <- hermes_cli/backup.py
  - _load_cron_jobs_doc  <- hermes_cli/backup.py
  - _prompt_degraded  <- hermes_cli/backup.py
  - _prune_incomplete_zips  <- hermes_cli/backup.py
  - _write_zip_file  <- hermes_cli/backup.py
  - restore_cron_prompt_fields_all_profiles  <- hermes_cli/backup.py
  - restore_cron_prompt_fields_if_degraded  <- hermes_cli/backup.py

### qubit_runtime/qubit_cli/status.py  (8 missing)
  - _authenticated_provider_names  <- hermes_cli/status.py
  - _connected_platform_labels  <- hermes_cli/status.py
  - _connected_platforms  <- hermes_cli/status.py
  - _cron_summary  <- hermes_cli/status.py
  - _gateway_state  <- hermes_cli/status.py
  - _load_ctx_config  <- hermes_cli/status.py
  - _render_summary  <- hermes_cli/status.py
  - _summary_row  <- hermes_cli/status.py

### qubit_runtime/cron/scheduler.py  (7 missing)
  - _ExternalWorkerPostHandoffError  <- cron/scheduler.py
  - _install_fire_secret_scope  <- cron/scheduler.py
  - _normalize_newlines  <- cron/scheduler.py
  - _record_external_cron_worker  <- cron/scheduler.py
  - _reset_fire_secret_scope  <- cron/scheduler.py
  - _wedged_inflight_keys  <- cron/scheduler.py
  - get_restart_wait_cron_counts  <- cron/scheduler.py

### qubit_runtime/qubit_cli/uninstall.py  (7 missing)
  - _launchd_gateway_plists  <- hermes_cli/uninstall.py
  - _macos_cache_leftover_dirs  <- hermes_cli/uninstall.py
  - _remove_path_or_tree  <- hermes_cli/uninstall.py
  - _rmtree_if_exists  <- hermes_cli/uninstall.py
  - _xdg_leftover_paths  <- hermes_cli/uninstall.py
  - remove_dashboard_launchd_jobs  <- hermes_cli/uninstall.py
  - remove_desktop_app_leftovers  <- hermes_cli/uninstall.py

### qubit_runtime/tests/tools/test_bot_mode_dm.py  (7 missing)
  - test_live_dm_wait_keeps_claimed_turn_until_it_settles  <- tests/tools/test_bot_mode_dm.py
  - test_live_dm_wait_rechecks_receipt_when_owner_disappears_at_claim  <- tests/tools/test_bot_mode_dm.py
  - test_live_dm_wait_releases_claimed_runner_after_owner_is_gone  <- tests/tools/test_bot_mode_dm.py
  - test_live_dm_wait_releases_queued_runner_when_pinned_owner_is_gone  <- tests/tools/test_bot_mode_dm.py
  - test_live_dm_wait_releases_runner_when_owner_lookup_keeps_failing  <- tests/tools/test_bot_mode_dm.py
  - test_live_dm_wait_reports_reply_after_initial_wait_budget  <- tests/tools/test_bot_mode_dm.py
  - test_live_dm_wait_stops_at_its_deadline_while_the_owner_holds_the_receipt  <- tests/tools/test_bot_mode_dm.py

### qubit_runtime/qubit_cli/tips.py  (6 missing)
  - PLACEHOLDER_KEY_PREFIX  <- hermes_cli/tips.py
  - TIP_KEY_PREFIX  <- hermes_cli/tips.py
  - _catalog_keys  <- hermes_cli/tips.py
  - composer_placeholder_keys  <- hermes_cli/tips.py
  - reset_tips_cache  <- hermes_cli/tips.py
  - tip_keys  <- hermes_cli/tips.py

### qubit_runtime/tui_gateway/server.py  (6 missing)
  - _any_live_session_claims_key  <- tui_gateway/server.py
  - _fast_tier_applies  <- tui_gateway/server.py
  - _is_server_minted_key  <- tui_gateway/server.py
  - _load_disabled_toolsets  <- tui_gateway/server.py
  - _preview_action_request  <- tui_gateway/server.py
  - _session_show_reasoning  <- tui_gateway/server.py

### qubit_runtime/qubit_cli/cli_commands_mixin.py  (5 missing)
  - _gt  <- hermes_cli/cli_commands_mixin.py
  - _lines  <- hermes_cli/cli_commands_mixin.py
  - _local_engine_lines  <- hermes_cli/cli_commands_mixin.py
  - _t  <- hermes_cli/cli_commands_mixin.py
  - _tn  <- hermes_cli/cli_commands_mixin.py

### qubit_runtime/qubit_cli/update_cmd.py  (5 missing)
  - _heal_stale_shallow_checkout  <- hermes_cli/update_cmd.py
  - _map_ssl_cert_file_for_git  <- hermes_cli/update_cmd.py
  - _record_snapshot_stage  <- hermes_cli/update_cmd.py
  - _record_update_initiator  <- hermes_cli/update_cmd.py
  - _update_movement_baseline  <- hermes_cli/update_cmd.py

### qubit_runtime/tests/scripts/test_release_entrypoint.py  (5 missing)
  - test_abandon_force_cancels_in_progress_runs_before_the_draft_and_the_marker  <- tests/scripts/test_release_entrypoint.py
  - test_abandon_that_cannot_cancel_a_run_writes_no_marker_so_it_can_be_retried  <- tests/scripts/test_release_entrypoint.py
  - test_an_unlisted_run_points_at_the_workflow_page  <- tests/scripts/test_release_entrypoint.py
  - test_release_looks_for_the_dispatched_run_only_within_its_wait  <- tests/scripts/test_release_entrypoint.py
  - test_release_output_names_the_ci_run_and_the_publish_step  <- tests/scripts/test_release_entrypoint.py

### qubit_runtime/tests/tools/test_docker_environment.py  (5 missing)
  - test_labels_attribute_populated_after_init  <- tests/tools/test_docker_environment.py
  - test_reuse_environment_fingerprint_ignores_volatile_temp_mounts  <- tests/tools/test_docker_environment.py
  - test_reuse_environment_fingerprint_tracks_immutable_configuration  <- tests/tools/test_docker_environment.py
  - test_reuse_probe_filters_on_environment_fingerprint  <- tests/tools/test_docker_environment.py
  - test_symlinked_skills_tree_reuses_container_across_processes  <- tests/tools/test_docker_environment.py

### qubit_runtime/tui_gateway/methods_session.py  (5 missing)
  - _auto_resume_denied_source  <- tui_gateway/methods_session.py
  - _branch_idempotent_hit  <- tui_gateway/methods_session.py
  - _branch_title_for  <- tui_gateway/methods_session.py
  - _resume_materialize_minted  <- tui_gateway/methods_session.py
  - _resume_wake_after_interrupt  <- tui_gateway/methods_session.py

### qubit_runtime/qubit_cli/commands.py  (4 missing)
  - _localized  <- hermes_cli/commands.py
  - build_commands  <- hermes_cli/commands.py
  - build_commands_by_category  <- hermes_cli/commands.py
  - category_label  <- hermes_cli/commands.py

### qubit_runtime/qubit_cli/kanban_db.py  (4 missing)
  - _explicit_board_intent_pinned  <- hermes_cli/kanban_db.py
  - _explicit_board_slug  <- hermes_cli/kanban_db.py
  - current_run_started_ats  <- hermes_cli/kanban_db.py
  - pin_first_board_resolution  <- hermes_cli/kanban_db.py

### qubit_runtime/qubit_cli/kanban_db_dispatch.py  (4 missing)
  - _exit_summary_marker  <- hermes_cli/kanban_db_dispatch.py
  - _log_noise_prefixes  <- hermes_cli/kanban_db_dispatch.py
  - _propagate_module_import_root  <- hermes_cli/kanban_db_dispatch.py
  - adopt_worker_pid  <- hermes_cli/kanban_db_dispatch.py

### qubit_runtime/qubit_cli/plugins_cmd_install.py  (4 missing)
  - _ask_yes_no  <- hermes_cli/plugins_cmd_install.py
  - _known_issue_warnings  <- hermes_cli/plugins_cmd_install.py
  - recorded_install  <- hermes_cli/plugins_cmd_install.py
  - shared_dependency_answers  <- hermes_cli/plugins_cmd_install.py

### qubit_runtime/tui_gateway/slash_worker.py  (4 missing)
  - SkillSlashRefused  <- tui_gateway/slash_worker.py
  - _refuse_skill_slash  <- tui_gateway/slash_worker.py
  - _slash_base  <- tui_gateway/slash_worker.py
  - _watchdog_parent  <- tui_gateway/slash_worker.py

### qubit_runtime/qubit_cli/cli_info_mixin.py  (3 missing)
  - _help_section_title  <- hermes_cli/cli_info_mixin.py
  - _reload_mcp_choices  <- hermes_cli/cli_info_mixin.py
  - _tool_progress_label  <- hermes_cli/cli_info_mixin.py

### qubit_runtime/qubit_cli/cli_tui_mixin.py  (3 missing)
  - _agent_name  <- hermes_cli/cli_tui_mixin.py
  - _approval_choice_label  <- hermes_cli/cli_tui_mixin.py
  - _tn  <- hermes_cli/cli_tui_mixin.py

### qubit_runtime/qubit_cli/plugins.py  (3 missing)
  - _attach_published_gateway_host  <- hermes_cli/plugins.py
  - clear_published_gateway_message_host  <- hermes_cli/plugins.py
  - publish_gateway_message_host  <- hermes_cli/plugins.py

### qubit_runtime/qubit_cli/update_cmd_windows.py  (3 missing)
  - _owned_gateway_pids  <- hermes_cli/update_cmd_windows.py
  - _pending_relaunch_pids  <- hermes_cli/update_cmd_windows.py
  - _relaunch_verify_timeout_s  <- hermes_cli/update_cmd_windows.py

### qubit_runtime/cron/jobs.py  (2 missing)
  - _read_marker_fields  <- cron/jobs.py
  - ticker_heartbeat_writer_alive  <- cron/jobs.py

### qubit_runtime/evals/gateway_failure_ownership/probe.py  (2 missing)
  - _external  <- evals/gateway_failure_ownership/probe.py
  - local_connect_ex  <- evals/gateway_failure_ownership/probe.py

### qubit_runtime/qubit_cli/anon_auth.py  (2 missing)
  - free_tier_route  <- hermes_cli/anon_auth.py
  - has_free_tier_account  <- hermes_cli/anon_auth.py

### qubit_runtime/qubit_cli/config_migrations.py  (2 missing)
  - _migrate_to_48  <- hermes_cli/config_migrations.py
  - _migrate_to_49  <- hermes_cli/config_migrations.py

### qubit_runtime/qubit_cli/main.py  (2 missing)
  - _require_dashboard_web_deps  <- hermes_cli/main.py
  - explicit_cli_profile  <- hermes_cli/main.py

### qubit_runtime/qubit_cli/main_dashboard.py  (2 missing)
  - _respawnable_command_for_current_install  <- hermes_cli/main_dashboard.py
  - _unit_main_pid_is  <- hermes_cli/main_dashboard.py

### qubit_runtime/qubit_cli/plugin_catalog.py  (2 missing)
  - _catalog_worktree_mtime  <- hermes_cli/plugin_catalog.py
  - invalidate_live_cache_for_home  <- hermes_cli/plugin_catalog.py

### qubit_runtime/qubit_cli/plugins_discovery.py  (2 missing)
  - plugin_discovery_suppressed  <- hermes_cli/plugins_discovery.py
  - suppress_plugin_discovery  <- hermes_cli/plugins_discovery.py

### qubit_runtime/tests/agent/lsp/test_workspace_trust.py  (2 missing)
  - _hermes_side_tree  <- tests/agent/lsp/test_workspace_trust.py
  - test_untrusted_checkout_starts_only_allowlisted_servers_pinned_to_hermes_code  <- tests/agent/lsp/test_workspace_trust.py

### qubit_runtime/tests/agent/test_run_budget.py  (2 missing)
  - test_cloud_stream_budget_preserves_explicit_deadlines  <- tests/agent/test_run_budget.py
  - test_local_stream_patience_is_independent_of_run_budget  <- tests/agent/test_run_budget.py

### qubit_runtime/tests/cron/test_jobs_shrink_merge.py  (2 missing)
  - test_save_over_corrupt_store_fails_closed  <- tests/cron/test_jobs_shrink_merge.py
  - test_symlinked_store_saves_by_rename_not_in_place_copy  <- tests/cron/test_jobs_shrink_merge.py

### qubit_runtime/tests/desktop/test_appinstaller_check_script.py  (2 missing)
  - MODULE  <- tests/desktop/test_appinstaller_check_script.py
  - REPO  <- tests/desktop/test_appinstaller_check_script.py

### qubit_runtime/tests/e2e/core/upgrade/pm/test_stamps_and_doctor.py  (2 missing)
  - test_managed_env_hermes_can_check_for_updates  <- tests/e2e/core/upgrade/pm/test_stamps_and_doctor.py
  - test_managed_env_hermes_reports_the_checkout_as_the_install  <- tests/e2e/core/upgrade/pm/test_stamps_and_doctor.py

### qubit_runtime/tests/hermes_cli/test_tools_config.py  (2 missing)
  - test_get_platform_tools_legacy_hermes_alias_alone  <- tests/hermes_cli/test_tools_config.py
  - test_get_platform_tools_legacy_hermes_alias_expands  <- tests/hermes_cli/test_tools_config.py

### qubit_runtime/tests/pm/activation_support.py  (2 missing)
  - SETUP_HERMES_PS1  <- tests/pm/activation_support.py
  - SETUP_HERMES_SH  <- tests/pm/activation_support.py

### qubit_runtime/tests/tools/test_bot_relay.py  (2 missing)
  - test_delivery_env_single_profile_host_passes_the_process_env_through  <- tests/tools/test_bot_relay.py
  - test_delivery_env_under_multiplex_names_the_pinned_launch_home  <- tests/tools/test_bot_relay.py

### qubit_runtime/tests/tools/test_file_operations.py  (2 missing)
  - TestSearchFilesIncludesDirectories  <- tests/tools/test_file_operations.py
  - _fenced_base64_reply  <- tests/tools/test_file_operations.py

### qubit_runtime/tests/tools/test_spawn_site_child_env.py  (2 missing)
  - test_compute_host_is_hermes_and_keeps_its_full_environment  <- tests/tools/test_spawn_site_child_env.py
  - test_third_party_children_never_see_hermes_credentials  <- tests/tools/test_spawn_site_child_env.py

### qubit_runtime/tests/tools/test_terminal_config_env_sync.py  (2 missing)
  - _cli_env_map  <- tests/tools/test_terminal_config_env_sync.py
  - _gateway_env_map  <- tests/tools/test_terminal_config_env_sync.py

### qubit_runtime/tests/tools/test_tool_result_storage.py  (2 missing)
  - TestMcpEnvelopeSpillover  <- tests/tools/test_tool_result_storage.py
  - TestPageableText  <- tests/tools/test_tool_result_storage.py

### qubit_runtime/tests/tools/test_web_managed_routing.py  (2 missing)
  - test_managed_search_without_identity_names_the_gateway  <- tests/tools/test_web_managed_routing.py
  - test_unentitled_managed_search_gets_fast_search_but_no_billed_fallback  <- tests/tools/test_web_managed_routing.py

### qubit_runtime/tools/file_operations.py  (2 missing)
  - _json_nonstandard_constant  <- tools/file_operations.py
  - _refuse_introduced_json_constant  <- tools/file_operations.py

### qubit_runtime/tui_gateway/methods_config.py  (2 missing)
  - _readiness_cleared  <- tui_gateway/methods_config.py
  - _readiness_share  <- tui_gateway/methods_config.py

### qubit_runtime/apps/desktop/e2e/update/seed.py  (1 missing)
  - HERMES_E2E_UPDATE_INSTALL_REF  <- apps/desktop/e2e/update/seed.py

### qubit_runtime/cron/scheduler_worker_env.py  (1 missing)
  - _committed_dependency_site_packages  <- cron/scheduler_worker_env.py

### qubit_runtime/qubit_cli/_launchers.py  (1 missing)
  - _inline_string_literal  <- hermes_cli/_launchers.py

### qubit_runtime/qubit_cli/_parser.py  (1 missing)
  - _plugin_command_install_hint  <- hermes_cli/_parser.py

### qubit_runtime/qubit_cli/cli_agent_setup_mixin.py  (1 missing)
  - _resume_counts  <- hermes_cli/cli_agent_setup_mixin.py

### qubit_runtime/qubit_cli/dashboard_procs.py  (1 missing)
  - _ledger_serve_binds  <- hermes_cli/dashboard_procs.py

### qubit_runtime/qubit_cli/gui_uninstall.py  (1 missing)
  - desktop_install_record  <- hermes_cli/gui_uninstall.py

### qubit_runtime/qubit_cli/kanban_db_workspace.py  (1 missing)
  - _lexical_path  <- hermes_cli/kanban_db_workspace.py

### qubit_runtime/qubit_cli/memory_setup.py  (1 missing)
  - _catalog_install_hint  <- hermes_cli/memory_setup.py

### qubit_runtime/qubit_cli/nous_account.py  (1 missing)
  - _normalize_tier  <- hermes_cli/nous_account.py

### qubit_runtime/qubit_cli/observability/shared_metrics_disabled.py  (1 missing)
  - hermes_applied_write  <- hermes_cli/observability/shared_metrics_disabled.py

### qubit_runtime/qubit_cli/plugin_validate_core_override.py  (1 missing)
  - _hermes_top_level  <- hermes_cli/plugin_validate_core_override.py

### qubit_runtime/qubit_cli/setup.py  (1 missing)
  - _record_setup_completed  <- hermes_cli/setup.py

### qubit_runtime/qubit_cli/subcommands/backup.py  (1 missing)
  - _non_negative_keep  <- hermes_cli/subcommands/backup.py

### qubit_runtime/qubit_cli/update_cmd_fleet.py  (1 missing)
  - _live_gateway_pids_from_fleet  <- hermes_cli/update_cmd_fleet.py

### qubit_runtime/qubit_cli/update_cmd_git.py  (1 missing)
  - _park_detached_head  <- hermes_cli/update_cmd_git.py

### qubit_runtime/qubit_cli/update_cmd_maint.py  (1 missing)
  - _invalidate_live_plugin_catalog_caches  <- hermes_cli/update_cmd_maint.py

### qubit_runtime/qubit_cli/web_server.py  (1 missing)
  - _gateway_owns_cron  <- hermes_cli/web_server.py

### qubit_runtime/tests/agent/test_skill_invocation_description.py  (1 missing)
  - TestGatewayAutoLoadScaffold  <- tests/agent/test_skill_invocation_description.py

### qubit_runtime/tests/agent/transports/test_chat_completions_reasoning_details_replay.py  (1 missing)
  - test_openrouter_keeps_and_nous_strips_reasoning_details  <- tests/agent/transports/test_chat_completions_reasoning_details_replay.py

### qubit_runtime/tests/cron/test_monitor_kind.py  (1 missing)
  - test_monitor_script_uses_configured_interpreter  <- tests/cron/test_monitor_kind.py

### qubit_runtime/tests/e2e/core/windows_update/test_paths_and_acls.py  (1 missing)
  - test_hermes_works_and_updates_from_that_profile  <- tests/e2e/core/windows_update/test_paths_and_acls.py

### qubit_runtime/tests/gateway/test_email.py  (1 missing)
  - test_oversized_cron_output_is_delivered_as_one_whole_email  <- tests/gateway/test_email.py

### qubit_runtime/tests/hermes_cli/test_local_launch_budget.py  (1 missing)
  - hermes_home  <- tests/hermes_cli/test_local_launch_budget.py

### qubit_runtime/tests/hermes_cli/test_logs.py  (1 missing)
  - test_every_log_file_writes_a_stamp_hermes_logs_since_can_read  <- tests/hermes_cli/test_logs.py

### qubit_runtime/tests/hermes_cli/test_plugin_validate.py  (1 missing)
  - test_runtime_rebind_of_hermes_core_fails_admission  <- tests/hermes_cli/test_plugin_validate.py

### qubit_runtime/tests/hermes_cli/test_shared_metrics_harness.py  (1 missing)
  - test_terminal_outcome_trusts_hermes_flags_not_exit_124  <- tests/hermes_cli/test_shared_metrics_harness.py

### qubit_runtime/tests/hermes_cli/test_shared_metrics_signals.py  (1 missing)
  - test_hermes_internal_work_never_latches_adoption  <- tests/hermes_cli/test_shared_metrics_signals.py

### qubit_runtime/tests/hermes_cli/test_subprocess_text_encoding.py  (1 missing)
  - HERMES_CLI  <- tests/hermes_cli/test_subprocess_text_encoding.py

### qubit_runtime/tests/hermes_cli/test_tui_resume_flow.py  (1 missing)
  - test_launch_tui_prefers_launch_cwd_over_inherited_hermes_cwd  <- tests/hermes_cli/test_tui_resume_flow.py

### qubit_runtime/tests/hermes_cli/test_uninstall_leftovers.py  (1 missing)
  - test_remove_desktop_app_leftovers_removes_hermes_library_entries  <- tests/hermes_cli/test_uninstall_leftovers.py

### qubit_runtime/tests/pm/test_node_resolution.py  (1 missing)
  - test_windows_path_prefers_launchable_cmd_but_never_for_pm_tools  <- tests/pm/test_node_resolution.py

### qubit_runtime/tests/tools/test_code_execution.py  (1 missing)
  - TestRemoteSharedHostLockdown  <- tests/tools/test_code_execution.py

### qubit_runtime/tests/tools/test_mcp_node_abi.py  (1 missing)
  - test_stdio_server_dying_on_a_node_abi_mismatch_names_the_rebuild_under_hermes_node  <- tests/tools/test_mcp_node_abi.py

### qubit_runtime/tests/tools/test_mcp_stderr_lifecycle.py  (1 missing)
  - test_every_mcp_log_line_carries_a_stamp_hermes_logs_since_reads  <- tests/tools/test_mcp_stderr_lifecycle.py

### qubit_runtime/tests/tools/test_modal_bulk_upload.py  (1 missing)
  - TestModalCommandStdin  <- tests/tools/test_modal_bulk_upload.py

### qubit_runtime/tests/tui_gateway/test_slash_worker_provider.py  (1 missing)
  - test_slash_worker_child_forwards_provider_to_hermes_cli  <- tests/tui_gateway/test_slash_worker_provider.py

### qubit_runtime/tools/code_execution_tool.py  (1 missing)
  - _ship_env_file_and_launch  <- tools/code_execution_tool.py
