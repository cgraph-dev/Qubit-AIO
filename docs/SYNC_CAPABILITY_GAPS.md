# Capability audit — upstream 69948c0057..a036b13793

delta files: 4876  (python: 2492)
symbols checked: 7165   MISSING in Qubit: 1833
files with gaps: 454

## missing symbols by subsystem

   1029  tests
    317  qubit_cli
    162  scripts
     90  agent
     61  plugins
     48  tools
     41  tui_gateway
     23  cron
     20  pm
     11  gateway
     10  evals
      9  qubit_state_pidns.py
      2  qubit_platform
      2  qubit_state_ids.py
      2  utils.py
      1  apps
      1  qubit_state_coverage.py
      1  qubit_state_identity.py
      1  qubit_state_maintenance.py
      1  qubit_state_titles.py
      1  providers

## detail


### qubit_runtime/scripts/code_health/py_rules.py  (55 missing)
  - CHECKERS  <- scripts/code_health/py_rules.py
  - Ctx  <- scripts/code_health/py_rules.py
  - _Binder  <- scripts/code_health/py_rules.py
  - _HandleScope  <- scripts/code_health/py_rules.py
  - _Scope  <- scripts/code_health/py_rules.py
  - _annotated_kind  <- scripts/code_health/py_rules.py
  - _annotations  <- scripts/code_health/py_rules.py
  - _awaited_calls  <- scripts/code_health/py_rules.py
  - _binding_pairs  <- scripts/code_health/py_rules.py
  - _bounded_calls  <- scripts/code_health/py_rules.py
  - _call_name  <- scripts/code_health/py_rules.py
  - _capture_lines  <- scripts/code_health/py_rules.py
  - _context_names  <- scripts/code_health/py_rules.py
  - _deadline  <- scripts/code_health/py_rules.py
  - _deferred_parts  <- scripts/code_health/py_rules.py
  - _dotted  <- scripts/code_health/py_rules.py
  - _eager  <- scripts/code_health/py_rules.py
  - _env_read_name  <- scripts/code_health/py_rules.py
  - _env_write_names  <- scripts/code_health/py_rules.py
  - _finite  <- scripts/code_health/py_rules.py
  - _from_results  <- scripts/code_health/py_rules.py
  - _hermes_home_path  <- scripts/code_health/py_rules.py
  - _import_time_blocks  <- scripts/code_health/py_rules.py
  - _import_time_exprs  <- scripts/code_health/py_rules.py
  - _import_time_statements  <- scripts/code_health/py_rules.py

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

### qubit_runtime/tests/scripts/test_code_health_rules.py  (35 missing)
  - _depth  <- tests/scripts/test_code_health_rules.py
  - _elif  <- tests/scripts/test_code_health_rules.py
  - _else_if  <- tests/scripts/test_code_health_rules.py
  - _hash  <- tests/scripts/test_code_health_rules.py
  - _hits  <- tests/scripts/test_code_health_rules.py
  - _judge  <- tests/scripts/test_code_health_rules.py
  - _recursive  <- tests/scripts/test_code_health_rules.py
  - test_added_capture_in_its_own_assignment_is_new  <- tests/scripts/test_code_health_rules.py
  - test_added_capture_inside_an_existing_expression_is_new  <- tests/scripts/test_code_health_rules.py
  - test_adding_a_bounded_async_helper_keeps_the_sync_one_clean  <- tests/scripts/test_code_health_rules.py
  - test_annotation_evaluation_time  <- tests/scripts/test_code_health_rules.py
  - test_argv_identity_ignores_docstrings  <- tests/scripts/test_code_health_rules.py
  - test_assignment_refactored_into_return_after_kill_passes  <- tests/scripts/test_code_health_rules.py
  - test_async_wait_after_kill_stays_flagged  <- tests/scripts/test_code_health_rules.py
  - test_ble001_fix_text_matches_the_engine  <- tests/scripts/test_code_health_rules.py
  - test_capture_lines_reports_each_independent_capture_once  <- tests/scripts/test_code_health_rules.py
  - test_fix_texts  <- tests/scripts/test_code_health_rules.py
  - test_gather_results_projection  <- tests/scripts/test_code_health_rules.py
  - test_global_and_nonlocal_select_the_outer_binding  <- tests/scripts/test_code_health_rules.py
  - test_hardcoded_home_matches_the_exact_component  <- tests/scripts/test_code_health_rules.py
  - test_import_alias_parameter_of_consuming_function_shadows  <- tests/scripts/test_code_health_rules.py
  - test_import_alias_resolves_per_scope  <- tests/scripts/test_code_health_rules.py
  - test_import_alias_with_unrelated_parameter_blocks_through_the_verdict  <- tests/scripts/test_code_health_rules.py
  - test_nesting_counts_physically_nested_else_if  <- tests/scripts/test_code_health_rules.py
  - test_process_handle_follows_reassignment  <- tests/scripts/test_code_health_rules.py

### qubit_runtime/tests/scripts/test_code_health_cli.py  (32 missing)
  - REPO  <- tests/scripts/test_code_health_cli.py
  - _add_engine  <- tests/scripts/test_code_health_cli.py
  - _branch_behind_main  <- tests/scripts/test_code_health_cli.py
  - _branch_with_checker  <- tests/scripts/test_code_health_cli.py
  - _check  <- tests/scripts/test_code_health_cli.py
  - _commit  <- tests/scripts/test_code_health_cli.py
  - _engine_repo  <- tests/scripts/test_code_health_cli.py
  - _env  <- tests/scripts/test_code_health_cli.py
  - _git  <- tests/scripts/test_code_health_cli.py
  - _init  <- tests/scripts/test_code_health_cli.py
  - _pushable  <- tests/scripts/test_code_health_cli.py
  - _ratchet_repo  <- tests/scripts/test_code_health_cli.py
  - _remote_ref  <- tests/scripts/test_code_health_cli.py
  - _sh  <- tests/scripts/test_code_health_cli.py
  - _timeless  <- tests/scripts/test_code_health_cli.py
  - _write  <- tests/scripts/test_code_health_cli.py
  - test_a_branch_cannot_relax_its_own_switch  <- tests/scripts/test_code_health_cli.py
  - test_allow_comment_lists_rules_by_comma_or_space  <- tests/scripts/test_code_health_cli.py
  - test_check_refuses_old_python_before_importing_anything  <- tests/scripts/test_code_health_cli.py
  - test_ci_only_guards_run_in_scripts_check  <- tests/scripts/test_code_health_cli.py
  - test_enforcement_reads_this_trees_own_switch  <- tests/scripts/test_code_health_cli.py
  - test_enforcement_switch_parses_annotated_and_unknown_values  <- tests/scripts/test_code_health_cli.py
  - test_hooks_prefer_the_repo_virtualenv_over_path_python  <- tests/scripts/test_code_health_cli.py
  - test_hooks_run_with_msys_path_conversion_disabled  <- tests/scripts/test_code_health_cli.py
  - test_json_stdout_is_json_on_every_path  <- tests/scripts/test_code_health_cli.py

### qubit_runtime/tests/tools/test_process_registry_completion_races.py  (26 missing)
  - _ReleaseHookLock  <- tests/tools/test_process_registry_completion_races.py
  - _SandboxEnv  <- tests/tools/test_process_registry_completion_races.py
  - _failed_scope_stop  <- tests/tools/test_process_registry_completion_races.py
  - _finalizing_session  <- tests/tools/test_process_registry_completion_races.py
  - _poll_sandbox  <- tests/tools/test_process_registry_completion_races.py
  - _sandbox_session  <- tests/tools/test_process_registry_completion_races.py
  - _scoped_session  <- tests/tools/test_process_registry_completion_races.py
  - _session  <- tests/tools/test_process_registry_completion_races.py
  - test_a_log_rotation_seen_after_a_kill_does_not_clear_its_output  <- tests/tools/test_process_registry_completion_races.py
  - test_a_lost_sandbox_without_a_kill_is_reported_lost  <- tests/tools/test_process_registry_completion_races.py
  - test_a_reader_exit_during_a_failed_scope_stop_is_not_reported_as_killed  <- tests/tools/test_process_registry_completion_races.py
  - test_a_recovered_pid_that_dies_mid_kill_reports_the_failed_scope_stop  <- tests/tools/test_process_registry_completion_races.py
  - test_a_sandbox_exit_read_after_a_kill_keeps_the_kill  <- tests/tools/test_process_registry_completion_races.py
  - test_a_sandbox_lost_after_a_kill_keeps_the_kill  <- tests/tools/test_process_registry_completion_races.py
  - test_a_snapshot_taken_before_the_reader_finishes_does_not_consume_it  <- tests/tools/test_process_registry_completion_races.py
  - test_bulk_cleanup_does_not_count_a_failed_scope_stop_and_retries_it  <- tests/tools/test_process_registry_completion_races.py
  - test_capacity_pruning_keeps_a_session_whose_scope_stop_is_pending  <- tests/tools/test_process_registry_completion_races.py
  - test_duplicate_finisher_leaves_completion_to_the_owner  <- tests/tools/test_process_registry_completion_races.py
  - test_kill_of_an_exited_session_reports_a_failed_scope_stop  <- tests/tools/test_process_registry_completion_races.py
  - test_kill_with_a_failed_scope_stop_keeps_the_session_running  <- tests/tools/test_process_registry_completion_races.py
  - test_owner_that_fails_mid_publish_still_releases_waiters  <- tests/tools/test_process_registry_completion_races.py
  - test_reader_exit_cannot_overwrite_a_committed_kill  <- tests/tools/test_process_registry_completion_races.py
  - test_sandbox_output_fetched_before_a_kill_is_not_added_after_it  <- tests/tools/test_process_registry_completion_races.py
  - test_status_reads_do_not_consume_a_completion_still_being_finalized  <- tests/tools/test_process_registry_completion_races.py
  - test_ttl_pruning_keeps_a_session_whose_scope_stop_is_pending  <- tests/tools/test_process_registry_completion_races.py

### qubit_runtime/qubit_cli/left_core_migration.py  (25 missing)
  - LEFT_CORE  <- hermes_cli/left_core_migration.py
  - LeftCoreFeature  <- hermes_cli/left_core_migration.py
  - STARTUP_RETRY_SECONDS  <- hermes_cli/left_core_migration.py
  - _core_carried  <- hermes_cli/left_core_migration.py
  - _failed_recently  <- hermes_cli/left_core_migration.py
  - _failure_stamp  <- hermes_cli/left_core_migration.py
  - _first_cause  <- hermes_cli/left_core_migration.py
  - _gateway_serves  <- hermes_cli/left_core_migration.py
  - _install_into  <- hermes_cli/left_core_migration.py
  - _install_one  <- hermes_cli/left_core_migration.py
  - _mark  <- hermes_cli/left_core_migration.py
  - _marked  <- hermes_cli/left_core_migration.py
  - _note_failure  <- hermes_cli/left_core_migration.py
  - _pending  <- hermes_cli/left_core_migration.py
  - _read_config  <- hermes_cli/left_core_migration.py
  - _record_migration  <- hermes_cli/left_core_migration.py
  - _scope_kept  <- hermes_cli/left_core_migration.py
  - _toolset_listed  <- hermes_cli/left_core_migration.py
  - homeassistant_in_use  <- hermes_cli/left_core_migration.py
  - migrate_all_homes  <- hermes_cli/left_core_migration.py
  - migrate_home  <- hermes_cli/left_core_migration.py
  - platform_configured_on  <- hermes_cli/left_core_migration.py
  - platform_install_hint  <- hermes_cli/left_core_migration.py
  - plugin_present  <- hermes_cli/left_core_migration.py
  - recover_at_startup  <- hermes_cli/left_core_migration.py

### qubit_runtime/tests/tui_gateway/test_ssh_session_cwd_persistence.py  (24 missing)
  - KEY  <- tests/tui_gateway/test_ssh_session_cwd_persistence.py
  - _ImmediateThread  <- tests/tui_gateway/test_ssh_session_cwd_persistence.py
  - _hermes_home_subdir  <- tests/tui_gateway/test_ssh_session_cwd_persistence.py
  - _hydrate  <- tests/tui_gateway/test_ssh_session_cwd_persistence.py
  - _resume_live  <- tests/tui_gateway/test_ssh_session_cwd_persistence.py
  - backend  <- tests/tui_gateway/test_ssh_session_cwd_persistence.py
  - db  <- tests/tui_gateway/test_ssh_session_cwd_persistence.py
  - resume_db  <- tests/tui_gateway/test_ssh_session_cwd_persistence.py
  - test_hermes_owned_cwd  <- tests/tui_gateway/test_ssh_session_cwd_persistence.py
  - test_hermes_owned_cwd_includes_the_root_for_a_named_profile  <- tests/tui_gateway/test_ssh_session_cwd_persistence.py
  - test_host_backends_resume_unchanged  <- tests/tui_gateway/test_ssh_session_cwd_persistence.py
  - test_host_backends_still_persist_the_launch_dir  <- tests/tui_gateway/test_ssh_session_cwd_persistence.py
  - test_named_profile_resume_does_not_adopt_a_root_launch_dir  <- tests/tui_gateway/test_ssh_session_cwd_persistence.py
  - test_resume_does_not_adopt_a_stored_hermes_home_cwd  <- tests/tui_gateway/test_ssh_session_cwd_persistence.py
  - test_resume_keeps_a_remote_tilde_cwd_when_home_is_inside_hermes_home  <- tests/tui_gateway/test_ssh_session_cwd_persistence.py
  - test_resume_keeps_a_stored_remote_workspace  <- tests/tui_gateway/test_ssh_session_cwd_persistence.py
  - test_resume_leaves_a_set_aside_row_unchanged  <- tests/tui_gateway/test_ssh_session_cwd_persistence.py
  - test_resume_without_a_stored_cwd_does_not_persist_the_launch_dir  <- tests/tui_gateway/test_ssh_session_cwd_persistence.py
  - test_session_resume_does_not_adopt_a_stored_hermes_home_cwd  <- tests/tui_gateway/test_ssh_session_cwd_persistence.py
  - test_session_resume_keeps_a_stored_remote_workspace  <- tests/tui_gateway/test_ssh_session_cwd_persistence.py
  - test_session_resume_on_host_backends_unchanged  <- tests/tui_gateway/test_ssh_session_cwd_persistence.py
  - test_ssh_branch_seed_skips_the_launch_dir  <- tests/tui_gateway/test_ssh_session_cwd_persistence.py
  - test_ssh_explicit_cwd_is_persisted  <- tests/tui_gateway/test_ssh_session_cwd_persistence.py
  - test_ssh_launch_dir_is_not_persisted  <- tests/tui_gateway/test_ssh_session_cwd_persistence.py

### qubit_runtime/tests/hermes_cli/test_model_options_pool_usage.py  (21 missing)
  - _clear_cache  <- tests/hermes_cli/test_model_options_pool_usage.py
  - _entry  <- tests/hermes_cli/test_model_options_pool_usage.py
  - _fp  <- tests/hermes_cli/test_model_options_pool_usage.py
  - _install_fetcher  <- tests/hermes_cli/test_model_options_pool_usage.py
  - _join_refresh_workers  <- tests/hermes_cli/test_model_options_pool_usage.py
  - _join_refresh_workers_autouse  <- tests/hermes_cli/test_model_options_pool_usage.py
  - _openrouter_row  <- tests/hermes_cli/test_model_options_pool_usage.py
  - _snapshot  <- tests/hermes_cli/test_model_options_pool_usage.py
  - _write_pool  <- tests/hermes_cli/test_model_options_pool_usage.py
  - test_cache_isolation_a_to_b_to_a  <- tests/hermes_cli/test_model_options_pool_usage.py
  - test_dead_row_stays_visible_as_unavailable  <- tests/hermes_cli/test_model_options_pool_usage.py
  - test_failed_fetch_keeps_sibling_visible_and_does_not_repair  <- tests/hermes_cli/test_model_options_pool_usage.py
  - test_late_background_fetch_cannot_replace_fresher_data  <- tests/hermes_cli/test_model_options_pool_usage.py
  - test_limited_account_resets_at_is_latest_exhausted_window  <- tests/hermes_cli/test_model_options_pool_usage.py
  - test_live_cooldown_marks_account_limited_without_mutating_pool  <- tests/hermes_cli/test_model_options_pool_usage.py
  - test_model_scoped_window_exhaustion_does_not_limit_account  <- tests/hermes_cli/test_model_options_pool_usage.py
  - test_multi_entry_pool_reports_per_account_rows  <- tests/hermes_cli/test_model_options_pool_usage.py
  - test_non_supporting_pool_provider_reports_unknown_without_fetching  <- tests/hermes_cli/test_model_options_pool_usage.py
  - test_replaced_credential_never_reuses_stale_quota  <- tests/hermes_cli/test_model_options_pool_usage.py
  - test_same_codex_account_two_credentials_counts_one_account  <- tests/hermes_cli/test_model_options_pool_usage.py
  - test_single_entry_pool_keeps_legacy_gauge  <- tests/hermes_cli/test_model_options_pool_usage.py

### qubit_runtime/scripts/code_health/compare.py  (19 missing)
  - SIMILAR  <- scripts/code_health/compare.py
  - _Departures  <- scripts/code_health/compare.py
  - _Matcher  <- scripts/code_health/compare.py
  - _base_name  <- scripts/code_health/compare.py
  - _code  <- scripts/code_health/compare.py
  - _detail  <- scripts/code_health/compare.py
  - _file_findings  <- scripts/code_health/compare.py
  - _hit_findings  <- scripts/code_health/compare.py
  - _line_survival  <- scripts/code_health/compare.py
  - _module_statement  <- scripts/code_health/compare.py
  - _moved_unit_lines  <- scripts/code_health/compare.py
  - _owned_base_hits  <- scripts/code_health/compare.py
  - _owners  <- scripts/code_health/compare.py
  - _similarity  <- scripts/code_health/compare.py
  - _span  <- scripts/code_health/compare.py
  - _spend_departures  <- scripts/code_health/compare.py
  - _split_hits  <- scripts/code_health/compare.py
  - _unit_findings  <- scripts/code_health/compare.py
  - compare  <- scripts/code_health/compare.py

### qubit_runtime/tests/hermes_cli/test_nous_picker_on_sale.py  (19 missing)
  - _login_picker_ids  <- tests/hermes_cli/test_nous_picker_on_sale.py
  - _sale  <- tests/hermes_cli/test_nous_picker_on_sale.py
  - _serve  <- tests/hermes_cli/test_nous_picker_on_sale.py
  - _stub_portal  <- tests/hermes_cli/test_nous_picker_on_sale.py
  - test_all_three_paid_pickers_agree  <- tests/hermes_cli/test_nous_picker_on_sale.py
  - test_appends_discounted_models_missing_from_list_deepest_first  <- tests/hermes_cli/test_nous_picker_on_sale.py
  - test_cli_setup_flow_and_gui_picker_agree  <- tests/hermes_cli/test_nous_picker_on_sale.py
  - test_gui_picker_free_tier_does_not_list_paid_sales  <- tests/hermes_cli/test_nous_picker_on_sale.py
  - test_gui_picker_org_policy_still_narrows_on_sale_models  <- tests/hermes_cli/test_nous_picker_on_sale.py
  - test_gui_picker_paid_tier_shows_on_sale_model  <- tests/hermes_cli/test_nous_picker_on_sale.py
  - test_no_pricing_leaves_list_unchanged  <- tests/hermes_cli/test_nous_picker_on_sale.py
  - test_nonzero_sale_rounding_to_full_discount_is_still_paid  <- tests/hermes_cli/test_nous_picker_on_sale.py
  - test_nous_fetch_flags_tool_less_rows_so_the_union_can_skip_them  <- tests/hermes_cli/test_nous_picker_on_sale.py
  - test_post_login_picker_free_tier_does_not_list_paid_sales  <- tests/hermes_cli/test_nous_picker_on_sale.py
  - test_post_login_picker_org_policy_still_narrows_on_sale_models  <- tests/hermes_cli/test_nous_picker_on_sale.py
  - test_post_login_picker_paid_tier_shows_on_sale_model  <- tests/hermes_cli/test_nous_picker_on_sale.py
  - test_skips_generation_models_on_sale  <- tests/hermes_cli/test_nous_picker_on_sale.py
  - test_skips_tool_less_and_free_rows  <- tests/hermes_cli/test_nous_picker_on_sale.py
  - test_zero_prompt_with_missing_or_invalid_completion_is_not_a_paid_sale  <- tests/hermes_cli/test_nous_picker_on_sale.py

### qubit_runtime/qubit_cli/git_pack_tidy.py  (18 missing)
  - PACK_COUNT_TARGET  <- hermes_cli/git_pack_tidy.py
  - TIDY_BUDGET_SECONDS  <- hermes_cli/git_pack_tidy.py
  - TidyResult  <- hermes_cli/git_pack_tidy.py
  - _Index  <- hermes_cli/git_pack_tidy.py
  - _drop_midx  <- hermes_cli/git_pack_tidy.py
  - _erase_redundant_packs  <- hermes_cli/git_pack_tidy.py
  - _git_env  <- hermes_cli/git_pack_tidy.py
  - _is_redundant  <- hermes_cli/git_pack_tidy.py
  - _load_state  <- hermes_cli/git_pack_tidy.py
  - _merge_smallest_packs  <- hermes_cli/git_pack_tidy.py
  - _pinned  <- hermes_cli/git_pack_tidy.py
  - _promisor_packs  <- hermes_cli/git_pack_tidy.py
  - _remove_pack  <- hermes_cli/git_pack_tidy.py
  - _save_state  <- hermes_cli/git_pack_tidy.py
  - _sweep_remnants  <- hermes_cli/git_pack_tidy.py
  - _take_lock  <- hermes_cli/git_pack_tidy.py
  - _unlink  <- hermes_cli/git_pack_tidy.py
  - tidy_partial_clone_packs  <- hermes_cli/git_pack_tidy.py

### qubit_runtime/tests/hermes_cli/test_left_core_migration.py  (18 missing)
  - _fresh  <- tests/hermes_cli/test_left_core_migration.py
  - _home  <- tests/hermes_cli/test_left_core_migration.py
  - test_a_disabled_or_present_plugin_is_left_alone  <- tests/hermes_cli/test_left_core_migration.py
  - test_a_failed_startup_install_backs_off_and_reports_one_line  <- tests/hermes_cli/test_left_core_migration.py
  - test_a_preinstalled_plugin_gets_the_scope_conversion_once  <- tests/hermes_cli/test_left_core_migration.py
  - test_catalog_miss_is_reported_not_installed  <- tests/hermes_cli/test_left_core_migration.py
  - test_gateway_start_outcome_waits_for_the_first_agent  <- tests/hermes_cli/test_left_core_migration.py
  - test_home_without_homeassistant_gets_nothing_and_no_notice  <- tests/hermes_cli/test_left_core_migration.py
  - test_homeassistant_in_use_matches_what_core_activated  <- tests/hermes_cli/test_left_core_migration.py
  - test_homeassistant_not_in_use  <- tests/hermes_cli/test_left_core_migration.py
  - test_in_use_reads_the_platform_config_the_gateway_loader_merged  <- tests/hermes_cli/test_left_core_migration.py
  - test_migrate_all_homes_migrates_only_the_profile_that_used_it  <- tests/hermes_cli/test_left_core_migration.py
  - test_migrate_home_installs_the_catalog_plugin_once  <- tests/hermes_cli/test_left_core_migration.py
  - test_migrate_home_reports_a_failed_install_with_the_command  <- tests/hermes_cli/test_left_core_migration.py
  - test_migration_keeps_a_toolset_off_where_core_had_it_off  <- tests/hermes_cli/test_left_core_migration.py
  - test_process_env_token_counts_only_for_the_active_home  <- tests/hermes_cli/test_left_core_migration.py
  - test_startup_honours_lazy_install_opt_out  <- tests/hermes_cli/test_left_core_migration.py
  - test_the_migration_installs_once_so_a_removal_sticks  <- tests/hermes_cli/test_left_core_migration.py

### qubit_runtime/tests/hermes_cli/test_plugin_host.py  (17 missing)
  - ASYNC_PLUGIN  <- tests/hermes_cli/test_plugin_host.py
  - HostModel  <- tests/hermes_cli/test_plugin_host.py
  - MEMORY_PLUGIN  <- tests/hermes_cli/test_plugin_host.py
  - MODEL_PROVIDER_PLUGIN  <- tests/hermes_cli/test_plugin_host.py
  - PLATFORM_PLUGIN  <- tests/hermes_cli/test_plugin_host.py
  - PROBE_PLUGIN  <- tests/hermes_cli/test_plugin_host.py
  - Painter  <- tests/hermes_cli/test_plugin_host.py
  - Probe  <- tests/hermes_cli/test_plugin_host.py
  - SEEN  <- tests/hermes_cli/test_plugin_host.py
  - _home_with_plugins  <- tests/hermes_cli/test_plugin_host.py
  - register  <- tests/hermes_cli/test_plugin_host.py
  - test_async_plugin_code_calls_back_in_the_callers_session  <- tests/hermes_cli/test_plugin_host.py
  - test_hosted_memory_provider_stays_live_across_a_host_crash  <- tests/hermes_cli/test_plugin_host.py
  - test_isolation_host_keeps_every_user_import_path_out_of_process  <- tests/hermes_cli/test_plugin_host.py
  - test_managed_scope_pins_host_isolation_over_the_profiles_own_config  <- tests/hermes_cli/test_plugin_host.py
  - test_model_provider_profile_data_is_local_and_overrides_run_in_the_host  <- tests/hermes_cli/test_plugin_host.py
  - test_plugin_runs_out_of_process_with_ctx_round_trips_and_survives_host_crash  <- tests/hermes_cli/test_plugin_host.py

### qubit_runtime/scripts/code_health/config.py  (16 missing)
  - ALLOW_SYNTAX  <- scripts/code_health/config.py
  - ENFORCEMENT  <- scripts/code_health/config.py
  - METRIC_FIX  <- scripts/code_health/config.py
  - PY_EXCLUDE  <- scripts/code_health/config.py
  - RUFF_CODES  <- scripts/code_health/config.py
  - RULES  <- scripts/code_health/config.py
  - RULES_BY_ID  <- scripts/code_health/config.py
  - Rule  <- scripts/code_health/config.py
  - SINGLE_PROFILE  <- scripts/code_health/config.py
  - TARGETS  <- scripts/code_health/config.py
  - TESTS  <- scripts/code_health/config.py
  - TS_EXCLUDE  <- scripts/code_health/config.py
  - WARN_UNTIL_REPLAYED  <- scripts/code_health/config.py
  - _matches  <- scripts/code_health/config.py
  - in_scope  <- scripts/code_health/config.py
  - rule_applies  <- scripts/code_health/config.py

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

### qubit_runtime/tests/hermes_state/test_session_id_recognition_recovery.py  (16 missing)
  - API_CHAT_ID  <- tests/hermes_state/test_session_id_recognition_recovery.py
  - API_ID  <- tests/hermes_state/test_session_id_recognition_recovery.py
  - BG_ID  <- tests/hermes_state/test_session_id_recognition_recovery.py
  - CRON_ID  <- tests/hermes_state/test_session_id_recognition_recovery.py
  - CRON_SLUG_ID  <- tests/hermes_state/test_session_id_recognition_recovery.py
  - DERIVED_IDS  <- tests/hermes_state/test_session_id_recognition_recovery.py
  - NOT_SESSION_IDS  <- tests/hermes_state/test_session_id_recognition_recovery.py
  - ROOM_ID  <- tests/hermes_state/test_session_id_recognition_recovery.py
  - RUN_ID  <- tests/hermes_state/test_session_id_recognition_recovery.py
  - TUI_BG_ID  <- tests/hermes_state/test_session_id_recognition_recovery.py
  - UUID_ID  <- tests/hermes_state/test_session_id_recognition_recovery.py
  - _map_as_salvage  <- tests/hermes_state/test_session_id_recognition_recovery.py
  - _sessions_rows  <- tests/hermes_state/test_session_id_recognition_recovery.py
  - store  <- tests/hermes_state/test_session_id_recognition_recovery.py
  - test_layout_inference_survives_every_derived_id_shape  <- tests/hermes_state/test_session_id_recognition_recovery.py
  - test_salvage_recognizes_minted_ids_and_rejects_arbitrary_cells  <- tests/hermes_state/test_session_id_recognition_recovery.py

### qubit_runtime/tests/scripts/test_code_health_identity.py  (16 missing)
  - _base  <- tests/scripts/test_code_health_identity.py
  - _handler  <- tests/scripts/test_code_health_identity.py
  - _ifs  <- tests/scripts/test_code_health_identity.py
  - _obj  <- tests/scripts/test_code_health_identity.py
  - _ts_base  <- tests/scripts/test_code_health_identity.py
  - test_function_moved_and_edited_keeps_its_cap  <- tests/scripts/test_code_health_identity.py
  - test_module_level_constant_moved_to_another_file  <- tests/scripts/test_code_health_identity.py
  - test_module_level_hits_follow_code_moved_to_another_file  <- tests/scripts/test_code_health_identity.py
  - test_repeated_python_names_pair_by_body  <- tests/scripts/test_code_health_identity.py
  - test_ts_anonymous_callbacks_pair_by_body  <- tests/scripts/test_code_health_identity.py
  - test_ts_identity_ignores_comments  <- tests/scripts/test_code_health_identity.py
  - test_ts_object_literal_methods_are_named_by_their_object  <- tests/scripts/test_code_health_identity.py
  - test_ts_overload_signatures_are_not_units  <- tests/scripts/test_code_health_identity.py
  - test_ts_recursive_rename_ignores_nested_bindings  <- tests/scripts/test_code_health_identity.py
  - test_ts_syntax_error_fails_closed  <- tests/scripts/test_code_health_identity.py
  - test_typescript_install_uses_repo_npmrc_and_fails_cleanly  <- tests/scripts/test_code_health_identity.py

### qubit_runtime/qubit_cli/plugin_host_wire.py  (15 missing)
  - Channel  <- hermes_cli/plugin_host_wire.py
  - Opaque  <- hermes_cli/plugin_host_wire.py
  - PROTOCOL_VERSION  <- hermes_cli/plugin_host_wire.py
  - PluginHostError  <- hermes_cli/plugin_host_wire.py
  - PluginHostUnavailable  <- hermes_cli/plugin_host_wire.py
  - PluginHostUnsupported  <- hermes_cli/plugin_host_wire.py
  - Record  <- hermes_cli/plugin_host_wire.py
  - bind_serving_request  <- hermes_cli/plugin_host_wire.py
  - decode  <- hermes_cli/plugin_host_wire.py
  - describe_signature  <- hermes_cli/plugin_host_wire.py
  - encode  <- hermes_cli/plugin_host_wire.py
  - is_async_callable  <- hermes_cli/plugin_host_wire.py
  - raise_remote  <- hermes_cli/plugin_host_wire.py
  - serving_request  <- hermes_cli/plugin_host_wire.py
  - signature_from  <- hermes_cli/plugin_host_wire.py

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

### qubit_runtime/agent/account_usage_cache.py  (14 missing)
  - REFRESH_AFTER_S  <- agent/account_usage_cache.py
  - SNAPSHOT_STALE_AFTER_S  <- agent/account_usage_cache.py
  - _claim_refresh  <- agent/account_usage_cache.py
  - _identity_id_for  <- agent/account_usage_cache.py
  - _key  <- agent/account_usage_cache.py
  - _refresh_entries  <- agent/account_usage_cache.py
  - _refresh_legacy  <- agent/account_usage_cache.py
  - _release_refresh  <- agent/account_usage_cache.py
  - cached_account_usage  <- agent/account_usage_cache.py
  - has_account_usage  <- agent/account_usage_cache.py
  - refresh_account_usage_async  <- agent/account_usage_cache.py
  - refresh_account_usage_entries_async  <- agent/account_usage_cache.py
  - remember_account_usage  <- agent/account_usage_cache.py
  - snapshot_is_stale  <- agent/account_usage_cache.py

### qubit_runtime/plugins/platforms/discord/onboarding.py  (14 missing)
  - DiscordBotCheck  <- plugins/platforms/discord/onboarding.py
  - INVITE_PERMISSIONS  <- plugins/platforms/discord/onboarding.py
  - INVITE_PERMISSION_BITS  <- plugins/platforms/discord/onboarding.py
  - _check_saved_token  <- plugins/platforms/discord/onboarding.py
  - _clean_discord_user_ids  <- plugins/platforms/discord/onboarding.py
  - _discord_token_shape_error  <- plugins/platforms/discord/onboarding.py
  - _ensure_message_content_intent  <- plugins/platforms/discord/onboarding.py
  - _owners  <- plugins/platforms/discord/onboarding.py
  - _print_invite  <- plugins/platforms/discord/onboarding.py
  - _prompt_allowlist  <- plugins/platforms/discord/onboarding.py
  - _prompt_checked_token  <- plugins/platforms/discord/onboarding.py
  - _prompt_discord_bot_token  <- plugins/platforms/discord/onboarding.py
  - check_bot_token  <- plugins/platforms/discord/onboarding.py
  - interactive_setup  <- plugins/platforms/discord/onboarding.py

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

### qubit_runtime/qubit_cli/plugin_host_child.py  (13 missing)
  - HostRuntime  <- hermes_cli/plugin_host_child.py
  - RemoteFacade  <- hermes_cli/plugin_host_child.py
  - RemotePluginContext  <- hermes_cli/plugin_host_child.py
  - RemoteRegistration  <- hermes_cli/plugin_host_child.py
  - _CapturingContext  <- hermes_cli/plugin_host_child.py
  - _import_base  <- hermes_cli/plugin_host_child.py
  - _import_plugin  <- hermes_cli/plugin_host_child.py
  - _is_provider_object  <- hermes_cli/plugin_host_child.py
  - _take_protocol_streams  <- hermes_cli/plugin_host_child.py
  - capture_profiles  <- hermes_cli/plugin_host_child.py
  - describe_profile  <- hermes_cli/plugin_host_child.py
  - extract_profiles_main  <- hermes_cli/plugin_host_child.py
  - main  <- hermes_cli/plugin_host_child.py

### qubit_runtime/qubit_cli/plugin_isolation.py  (13 missing)
  - HOST_DEGRADED_HOOKS  <- hermes_cli/plugin_isolation.py
  - HOST_OBJECT_BASES  <- hermes_cli/plugin_isolation.py
  - HOST_PROCESS_ENV  <- hermes_cli/plugin_isolation.py
  - HOST_REMOTE_FACADES  <- hermes_cli/plugin_isolation.py
  - HOST_SKIPPED_CTX_METHODS  <- hermes_cli/plugin_isolation.py
  - HOST_UNSUPPORTED_CTX_METHODS  <- hermes_cli/plugin_isolation.py
  - ISOLATION_HOST  <- hermes_cli/plugin_isolation.py
  - ISOLATION_IN_PROCESS  <- hermes_cli/plugin_isolation.py
  - _plugins_config  <- hermes_cli/plugin_isolation.py
  - host_launcher  <- hermes_cli/plugin_isolation.py
  - in_process_import_refusal  <- hermes_cli/plugin_isolation.py
  - isolation_mode  <- hermes_cli/plugin_isolation.py
  - user_plugin_host  <- hermes_cli/plugin_isolation.py

### qubit_runtime/scripts/code_health/py_structure.py  (13 missing)
  - ScopeIndex  <- scripts/code_health/py_structure.py
  - Span  <- scripts/code_health/py_structure.py
  - _UnitCollector  <- scripts/code_health/py_structure.py
  - _binds  <- scripts/code_health/py_structure.py
  - _bound_names  <- scripts/code_health/py_structure.py
  - _child_blocks  <- scripts/code_health/py_structure.py
  - _first_line  <- scripts/code_health/py_structure.py
  - _own_scope  <- scripts/code_health/py_structure.py
  - _rename  <- scripts/code_health/py_structure.py
  - _self_references  <- scripts/code_health/py_structure.py
  - body_hash  <- scripts/code_health/py_structure.py
  - measure_structure  <- scripts/code_health/py_structure.py
  - nesting_depth  <- scripts/code_health/py_structure.py

### qubit_runtime/tests/gateway/test_discord_voice_rekey.py  (13 missing)
  - KEY_A  <- tests/gateway/test_discord_voice_rekey.py
  - KEY_B  <- tests/gateway/test_discord_voice_rekey.py
  - TestCredentialResolution  <- tests/gateway/test_discord_voice_rekey.py
  - TestDaveUnmappedDrop  <- tests/gateway/test_discord_voice_rekey.py
  - TestFailureStreakRefresh  <- tests/gateway/test_discord_voice_rekey.py
  - TestTeardownHealthLine  <- tests/gateway/test_discord_voice_rekey.py
  - TestWebsocketHookTrigger  <- tests/gateway/test_discord_voice_rekey.py
  - _FakeAead  <- tests/gateway/test_discord_voice_rekey.py
  - _make_conn  <- tests/gateway/test_discord_voice_rekey.py
  - _make_receiver  <- tests/gateway/test_discord_voice_rekey.py
  - _make_vc  <- tests/gateway/test_discord_voice_rekey.py
  - _rtp_packet  <- tests/gateway/test_discord_voice_rekey.py
  - fake_nacl  <- tests/gateway/test_discord_voice_rekey.py

### qubit_runtime/qubit_cli/observability/relay_shared_metrics.py  (12 missing)
  - _PeakLineage  <- hermes_cli/observability/relay_shared_metrics.py
  - _absorb_peak  <- hermes_cli/observability/relay_shared_metrics.py
  - _with_launch_entrypoint  <- hermes_cli/observability/relay_shared_metrics.py
  - close_session_run  <- hermes_cli/observability/relay_shared_metrics.py
  - record_known_cache_break  <- hermes_cli/observability/relay_shared_metrics.py
  - record_model_switch_after  <- hermes_cli/observability/relay_shared_metrics.py
  - record_process_mark  <- hermes_cli/observability/relay_shared_metrics.py
  - record_process_marks_saved  <- hermes_cli/observability/relay_shared_metrics.py
  - record_session_friction  <- hermes_cli/observability/relay_shared_metrics.py
  - record_session_tools  <- hermes_cli/observability/relay_shared_metrics.py
  - rotate_segment  <- hermes_cli/observability/relay_shared_metrics.py
  - shutdown_runtimes  <- hermes_cli/observability/relay_shared_metrics.py

### qubit_runtime/scripts/code_health/gitio.py  (12 missing)
  - Change  <- scripts/code_health/gitio.py
  - _parse_name_status  <- scripts/code_health/gitio.py
  - changed_files  <- scripts/code_health/gitio.py
  - commit_or_none  <- scripts/code_health/gitio.py
  - default_base  <- scripts/code_health/gitio.py
  - git  <- scripts/code_health/gitio.py
  - known_env_names  <- scripts/code_health/gitio.py
  - read_file  <- scripts/code_health/gitio.py
  - repo_root  <- scripts/code_health/gitio.py
  - resolve_rev  <- scripts/code_health/gitio.py
  - resolve_tree  <- scripts/code_health/gitio.py
  - tracked_files  <- scripts/code_health/gitio.py

### qubit_runtime/tests/scripts/test_code_health.py  (12 missing)
  - REPO  <- tests/scripts/test_code_health.py
  - _commit  <- tests/scripts/test_code_health.py
  - _git  <- tests/scripts/test_code_health.py
  - _repo  <- tests/scripts/test_code_health.py
  - _verdict  <- tests/scripts/test_code_health.py
  - _windows_write_text  <- tests/scripts/test_code_health.py
  - test_crlf_blob_measures_like_lf  <- tests/scripts/test_code_health.py
  - test_enforcement_switch_is_read_from_the_base  <- tests/scripts/test_code_health.py
  - test_moved_code_keeps_its_cap  <- tests/scripts/test_code_health.py
  - test_ratchet_blocks_growth_new_and_swapped_violations  <- tests/scripts/test_code_health.py
  - test_replay_reports_unmeasured_prs_and_fails  <- tests/scripts/test_code_health.py
  - test_verdicts_follow_ownership_deadlines_and_import_execution  <- tests/scripts/test_code_health.py

### qubit_runtime/tests/tools/test_computer_use_backend_plugins.py  (12 missing)
  - CALLS  <- tests/tools/test_computer_use_backend_plugins.py
  - FakeBackend  <- tests/tools/test_computer_use_backend_plugins.py
  - FakeProvider  <- tests/tools/test_computer_use_backend_plugins.py
  - TAG  <- tests/tools/test_computer_use_backend_plugins.py
  - _clean  <- tests/tools/test_computer_use_backend_plugins.py
  - _home  <- tests/tools/test_computer_use_backend_plugins.py
  - _list_apps  <- tests/tools/test_computer_use_backend_plugins.py
  - register  <- tests/tools/test_computer_use_backend_plugins.py
  - test_builtin_cua_is_default_and_plugin_never_self_activates  <- tests/tools/test_computer_use_backend_plugins.py
  - test_each_profile_gets_its_own_configured_backend  <- tests/tools/test_computer_use_backend_plugins.py
  - test_selected_plugin_receives_tool_calls_and_unselected_stays_dormant  <- tests/tools/test_computer_use_backend_plugins.py
  - test_unknown_backend_errors_naming_configured_and_available  <- tests/tools/test_computer_use_backend_plugins.py

### qubit_runtime/pm/libatomic.py  (11 missing)
  - _attempt  <- pm/libatomic.py
  - _command_plan  <- pm/libatomic.py
  - _has_terminal  <- pm/libatomic.py
  - _host_install_command  <- pm/libatomic.py
  - _install  <- pm/libatomic.py
  - _is_root  <- pm/libatomic.py
  - _manager_order  <- pm/libatomic.py
  - _release_tokens  <- pm/libatomic.py
  - _run  <- pm/libatomic.py
  - install_before_lock  <- pm/libatomic.py
  - try_install_libatomic  <- pm/libatomic.py

### qubit_runtime/tests/agent/test_split_turn_compaction.py  (11 missing)
  - _actionable_user_text  <- tests/agent/test_split_turn_compaction.py
  - _gateway_reply  <- tests/agent/test_split_turn_compaction.py
  - _plain_text_oversized_region  <- tests/agent/test_split_turn_compaction.py
  - _reasoning_heavy_small_turn  <- tests/agent/test_split_turn_compaction.py
  - _tail_group  <- tests/agent/test_split_turn_compaction.py
  - _textless_oversized_turn  <- tests/agent/test_split_turn_compaction.py
  - test_a_long_active_request_still_splits_and_survives_verbatim  <- tests/agent/test_split_turn_compaction.py
  - test_assistant_anchor_cannot_retain_a_textless_oversized_turn  <- tests/agent/test_split_turn_compaction.py
  - test_assistant_anchor_still_binds  <- tests/agent/test_split_turn_compaction.py
  - test_reply_pointer_does_not_count_toward_the_request_size  <- tests/agent/test_split_turn_compaction.py
  - test_restated_reply_keeps_splitting_on_later_compactions  <- tests/agent/test_split_turn_compaction.py

### qubit_runtime/tests/tools/test_human_input_hooks.py  (11 missing)
  - HOOKS  <- tests/tools/test_human_input_hooks.py
  - PASSWORD  <- tests/tools/test_human_input_hooks.py
  - SECRET_COMMAND  <- tests/tools/test_human_input_hooks.py
  - TOKEN  <- tests/tools/test_human_input_hooks.py
  - TestApproval  <- tests/tools/test_human_input_hooks.py
  - TestClarify  <- tests/tools/test_human_input_hooks.py
  - TestSudo  <- tests/tools/test_human_input_hooks.py
  - _assert_no_secret  <- tests/tools/test_human_input_hooks.py
  - _single_pair  <- tests/tools/test_human_input_hooks.py
  - captured  <- tests/tools/test_human_input_hooks.py
  - test_hooks_are_registered  <- tests/tools/test_human_input_hooks.py

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

### qubit_runtime/qubit_cli/plugin_isolation_audit.py  (10 missing)
  - HERMES_PACKAGES  <- hermes_cli/plugin_isolation_audit.py
  - IsolationReport  <- hermes_cli/plugin_isolation_audit.py
  - VERDICT_HOST  <- hermes_cli/plugin_isolation_audit.py
  - VERDICT_IN_PROCESS  <- hermes_cli/plugin_isolation_audit.py
  - VERDICT_PORTABLE  <- hermes_cli/plugin_isolation_audit.py
  - _SourceVisitor  <- hermes_cli/plugin_isolation_audit.py
  - _dedupe  <- hermes_cli/plugin_isolation_audit.py
  - _python_files  <- hermes_cli/plugin_isolation_audit.py
  - _read_manifest  <- hermes_cli/plugin_isolation_audit.py
  - audit_plugin_dir  <- hermes_cli/plugin_isolation_audit.py

### qubit_runtime/plugins/computer_use/__init__.py  (10 missing)
  - DEFAULT_BACKEND  <- plugins/computer_use/__init__.py
  - _ProviderCollector  <- plugins/computer_use/__init__.py
  - _is_computer_use_dir  <- plugins/computer_use/__init__.py
  - _iter_provider_dirs  <- plugins/computer_use/__init__.py
  - _load_provider_from_dir  <- plugins/computer_use/__init__.py
  - configured_backend_name  <- plugins/computer_use/__init__.py
  - discover_computer_use_providers  <- plugins/computer_use/__init__.py
  - find_provider_dir  <- plugins/computer_use/__init__.py
  - get_active_provider  <- plugins/computer_use/__init__.py
  - load_computer_use_provider  <- plugins/computer_use/__init__.py

### qubit_runtime/tests/hermes_cli/test_borrowed_home_launch.py  (10 missing)
  - _checkout  <- tests/hermes_cli/test_borrowed_home_launch.py
  - _home  <- tests/hermes_cli/test_borrowed_home_launch.py
  - _no_tool_downloads  <- tests/hermes_cli/test_borrowed_home_launch.py
  - _state  <- tests/hermes_cli/test_borrowed_home_launch.py
  - completion_tail  <- tests/hermes_cli/test_borrowed_home_launch.py
  - test_a_borrower_holding_the_checkout_never_becomes_its_owner  <- tests/hermes_cli/test_borrowed_home_launch.py
  - test_a_borrowers_own_dependency_state_never_makes_it_the_owner  <- tests/hermes_cli/test_borrowed_home_launch.py
  - test_a_borrowing_launch_syncs_its_own_dependencies_and_nothing_of_the_checkout  <- tests/hermes_cli/test_borrowed_home_launch.py
  - test_a_fresh_install_and_a_custom_root_keep_their_own_store  <- tests/hermes_cli/test_borrowed_home_launch.py
  - test_the_owner_still_owes_and_runs_its_tail  <- tests/hermes_cli/test_borrowed_home_launch.py

### qubit_runtime/tests/hermes_cli/test_git_pack_tidy.py  (10 missing)
  - _age  <- tests/hermes_cli/test_git_pack_tidy.py
  - _git  <- tests/hermes_cli/test_git_pack_tidy.py
  - _objects  <- tests/hermes_cli/test_git_pack_tidy.py
  - _packs  <- tests/hermes_cli/test_git_pack_tidy.py
  - _upstream  <- tests/hermes_cli/test_git_pack_tidy.py
  - clone  <- tests/hermes_cli/test_git_pack_tidy.py
  - test_a_refused_or_killed_erase_leaves_git_reading_and_is_finished_later  <- tests/hermes_cli/test_git_pack_tidy.py
  - test_erases_only_packs_whose_every_object_has_another_copy  <- tests/hermes_cli/test_git_pack_tidy.py
  - test_merges_down_to_the_target_into_one_pack_whatever_the_size_limit  <- tests/hermes_cli/test_git_pack_tidy.py
  - unlink  <- tests/hermes_cli/test_git_pack_tidy.py

### qubit_runtime/tests/tui_gateway/test_resume_readonly_mount.py  (10 missing)
  - _finalized_db  <- tests/tui_gateway/test_resume_readonly_mount.py
  - _frames  <- tests/tui_gateway/test_resume_readonly_mount.py
  - _mount  <- tests/tui_gateway/test_resume_readonly_mount.py
  - _wait_for  <- tests/tui_gateway/test_resume_readonly_mount.py
  - test_compute_host_child_turn_reopens_a_finalized_row  <- tests/tui_gateway/test_resume_readonly_mount.py
  - test_first_real_turn_reopens_a_finalized_row  <- tests/tui_gateway/test_resume_readonly_mount.py
  - test_isolated_dispatch_reopens_a_finalized_row  <- tests/tui_gateway/test_resume_readonly_mount.py
  - test_lazy_watch_mount_keeps_finalized_row_ended  <- tests/tui_gateway/test_resume_readonly_mount.py
  - test_reopen_if_finalized_leaves_live_rows_untouched  <- tests/tui_gateway/test_resume_readonly_mount.py
  - test_resume_mount_keeps_finalized_row_ended  <- tests/tui_gateway/test_resume_readonly_mount.py

### qubit_runtime/cron/scheduler.py  (9 missing)
  - _ExternalWorkerPostHandoffError  <- cron/scheduler.py
  - _fire_secret_scope  <- cron/scheduler.py
  - _install_fire_secret_scope  <- cron/scheduler.py
  - _normalize_newlines  <- cron/scheduler.py
  - _record_external_cron_worker  <- cron/scheduler.py
  - _reset_fire_secret_scope  <- cron/scheduler.py
  - _start_owned_run  <- cron/scheduler.py
  - _wedged_inflight_keys  <- cron/scheduler.py
  - get_restart_wait_cron_counts  <- cron/scheduler.py

### qubit_runtime/qubit_state_pidns.py  (9 missing)
  - LocalPidNamespace  <- hermes_state_pidns.py
  - _local_pid_namespace  <- hermes_state_pidns.py
  - _qualify  <- hermes_state_pidns.py
  - _recorded_namespace  <- hermes_state_pidns.py
  - _resolve_local_pid_namespace  <- hermes_state_pidns.py
  - holder_namespace_token  <- hermes_state_pidns.py
  - holder_pid_checkable  <- hermes_state_pidns.py
  - persistent_record_pidns_checkable  <- hermes_state_pidns.py
  - pid_namespace_id  <- hermes_state_pidns.py

### qubit_runtime/tests/hermes_cli/test_hermes_home_shared_launchers.py  (9 missing)
  - _isolate  <- tests/hermes_cli/test_hermes_home_shared_launchers.py
  - _make_home  <- tests/hermes_cli/test_hermes_home_shared_launchers.py
  - _make_repo  <- tests/hermes_cli/test_hermes_home_shared_launchers.py
  - _publish  <- tests/hermes_cli/test_hermes_home_shared_launchers.py
  - _repoint_store  <- tests/hermes_cli/test_hermes_home_shared_launchers.py
  - _write_dead_exe  <- tests/hermes_cli/test_hermes_home_shared_launchers.py
  - test_dead_exe_never_shadows_kept_cmd  <- tests/hermes_cli/test_hermes_home_shared_launchers.py
  - test_missing_dead_and_same_store_launchers_still_publish  <- tests/hermes_cli/test_hermes_home_shared_launchers.py
  - test_temp_home_prepare_launch_relaunches_without_rebinding  <- tests/hermes_cli/test_hermes_home_shared_launchers.py

### qubit_runtime/tests/hermes_cli/test_plugin_aux_tasks_dashboard.py  (9 missing)
  - _resolved_model  <- tests/hermes_cli/test_plugin_aux_tasks_dashboard.py
  - _rows  <- tests/hermes_cli/test_plugin_aux_tasks_dashboard.py
  - _write_home  <- tests/hermes_cli/test_plugin_aux_tasks_dashboard.py
  - _write_plugin  <- tests/hermes_cli/test_plugin_aux_tasks_dashboard.py
  - test_assignment_reset_and_stale_pins_cover_plugin_tasks  <- tests/hermes_cli/test_plugin_aux_tasks_dashboard.py
  - test_listing_is_per_profile_and_carries_inheritance  <- tests/hermes_cli/test_plugin_aux_tasks_dashboard.py
  - test_pin_persists_per_profile_and_is_honored_by_resolution  <- tests/hermes_cli/test_plugin_aux_tasks_dashboard.py
  - test_plugin_discovery_failure_leaves_builtins_working  <- tests/hermes_cli/test_plugin_aux_tasks_dashboard.py
  - two_profile_homes  <- tests/hermes_cli/test_plugin_aux_tasks_dashboard.py

### qubit_runtime/tests/hermes_cli/test_process_identity.py  (9 missing)
  - _reap  <- tests/hermes_cli/test_process_identity.py
  - test_reclaim_ignores_unknown_pid_and_never_raises  <- tests/hermes_cli/test_process_identity.py
  - test_reclaim_reaps_dead_spawner_owner  <- tests/hermes_cli/test_process_identity.py
  - test_reclaim_reaps_null_spawner_orphan_to_init  <- tests/hermes_cli/test_process_identity.py
  - test_reclaim_spares_interactive_purposes_and_self  <- tests/hermes_cli/test_process_identity.py
  - test_reclaim_spares_live_spawner_owner  <- tests/hermes_cli/test_process_identity.py
  - test_reclaim_spares_lock_claimed_orphan  <- tests/hermes_cli/test_process_identity.py
  - test_reclaim_spares_null_spawner_with_live_parent  <- tests/hermes_cli/test_process_identity.py
  - test_reclaim_spares_unprovable_spawner  <- tests/hermes_cli/test_process_identity.py

### qubit_runtime/tests/tools/test_process_registry_pty_kill.py  (9 missing)
  - _kill_within_deadline  <- tests/tools/test_process_registry_pty_kill.py
  - _spawn_with_escapee  <- tests/tools/test_process_registry_pty_kill.py
  - _terminate_owned  <- tests/tools/test_process_registry_pty_kill.py
  - _wait_for  <- tests/tools/test_process_registry_pty_kill.py
  - _wait_for_reader_close  <- tests/tools/test_process_registry_pty_kill.py
  - test_chunk_read_before_the_kill_is_not_added_after_it  <- tests/tools/test_process_registry_pty_kill.py
  - test_kill_returns_while_escaped_descendant_holds_the_pty  <- tests/tools/test_process_registry_pty_kill.py
  - test_output_after_the_kill_is_not_added_to_the_killed_session  <- tests/tools/test_process_registry_pty_kill.py
  - unscoped  <- tests/tools/test_process_registry_pty_kill.py

### qubit_runtime/agent/context_compressor.py  (8 missing)
  - _authored_request_text  <- agent/context_compressor.py
  - _failure_suffix  <- agent/context_compressor.py
  - _payload_failure_suffix  <- agent/context_compressor.py
  - _restated_request_text  <- agent/context_compressor.py
  - _result_failure_suffix  <- agent/context_compressor.py
  - _sum_cronjob_manage  <- agent/context_compressor.py
  - _sum_process_manage  <- agent/context_compressor.py
  - _summarize_refused_tool_result  <- agent/context_compressor.py

### qubit_runtime/agent/skill_commands.py  (8 missing)
  - _merge_interactive_skill_commands  <- agent/skill_commands.py
  - _missing_skill_label  <- agent/skill_commands.py
  - _scan_plugin_skill_commands  <- agent/skill_commands.py
  - ambiguous_skill_label  <- agent/skill_commands.py
  - format_missing_skills  <- agent/skill_commands.py
  - get_interactive_skill_commands  <- agent/skill_commands.py
  - get_plugin_skill_commands  <- agent/skill_commands.py
  - invalidate_plugin_skill_commands  <- agent/skill_commands.py

### qubit_runtime/qubit_cli/model_setup_flows_local.py  (8 missing)
  - _fmt_eta  <- hermes_cli/model_setup_flows_local.py
  - _follow_job  <- hermes_cli/model_setup_flows_local.py
  - _model_flow_local  <- hermes_cli/model_setup_flows_local.py
  - _progress_line  <- hermes_cli/model_setup_flows_local.py
  - _rate  <- hermes_cli/model_setup_flows_local.py
  - _row_label  <- hermes_cli/model_setup_flows_local.py
  - _start  <- hermes_cli/model_setup_flows_local.py
  - local_models_available  <- hermes_cli/model_setup_flows_local.py

### qubit_runtime/qubit_cli/status.py  (8 missing)
  - _authenticated_provider_names  <- hermes_cli/status.py
  - _connected_platform_labels  <- hermes_cli/status.py
  - _connected_platforms  <- hermes_cli/status.py
  - _cron_summary  <- hermes_cli/status.py
  - _gateway_state  <- hermes_cli/status.py
  - _load_ctx_config  <- hermes_cli/status.py
  - _render_summary  <- hermes_cli/status.py
  - _summary_row  <- hermes_cli/status.py

### qubit_runtime/plugins/platforms/whatsapp/bridge_ownership.py  (8 missing)
  - SECONDARY_PORT_FIRST  <- plugins/platforms/whatsapp/bridge_ownership.py
  - SECONDARY_PORT_LAST  <- plugins/platforms/whatsapp/bridge_ownership.py
  - _read_port_record  <- plugins/platforms/whatsapp/bridge_ownership.py
  - _sibling_claims  <- plugins/platforms/whatsapp/bridge_ownership.py
  - check_secondary_ownership  <- plugins/platforms/whatsapp/bridge_ownership.py
  - port_is_free  <- plugins/platforms/whatsapp/bridge_ownership.py
  - secondary_bridge_port  <- plugins/platforms/whatsapp/bridge_ownership.py
  - standalone_bridge_port  <- plugins/platforms/whatsapp/bridge_ownership.py

### qubit_runtime/tests/agent/test_account_usage_pool_fetch.py  (8 missing)
  - _LoopbackUsageServer  <- tests/agent/test_account_usage_pool_fetch.py
  - _b64  <- tests/agent/test_account_usage_pool_fetch.py
  - _codex_jwt  <- tests/agent/test_account_usage_pool_fetch.py
  - _usage_payload  <- tests/agent/test_account_usage_pool_fetch.py
  - test_anthropic_fetch_honors_explicit_api_key_and_marks_model_scoped_windows  <- tests/agent/test_account_usage_pool_fetch.py
  - test_read_only_401_raises_and_never_retries_with_rotation  <- tests/agent/test_account_usage_pool_fetch.py
  - test_read_only_fetch_remembered_under_identity_slot  <- tests/agent/test_account_usage_pool_fetch.py
  - test_read_only_fetch_routes_to_entries_own_host_and_parses  <- tests/agent/test_account_usage_pool_fetch.py

### qubit_runtime/tests/scripts/desktop_update/test_desktop_update_runtime_unicode.py  (8 missing)
  - CLI  <- tests/scripts/desktop_update/test_desktop_update_runtime_unicode.py
  - HELPER  <- tests/scripts/desktop_update/test_desktop_update_runtime_unicode.py
  - PROFILE  <- tests/scripts/desktop_update/test_desktop_update_runtime_unicode.py
  - ROOT  <- tests/scripts/desktop_update/test_desktop_update_runtime_unicode.py
  - _run_helper  <- tests/scripts/desktop_update/test_desktop_update_runtime_unicode.py
  - main  <- tests/scripts/desktop_update/test_desktop_update_runtime_unicode.py
  - test_legacy_version_identity_check_matches_a_non_ascii_install  <- tests/scripts/desktop_update/test_desktop_update_runtime_unicode.py
  - test_runtime_command_survives_a_non_ascii_profile_path  <- tests/scripts/desktop_update/test_desktop_update_runtime_unicode.py

### qubit_runtime/tests/tools/test_delegate_stale_heartbeat_collect.py  (8 missing)
  - _Child  <- tests/tools/test_delegate_stale_heartbeat_collect.py
  - _CompletingChild  <- tests/tools/test_delegate_stale_heartbeat_collect.py
  - _fast_watchdog  <- tests/tools/test_delegate_stale_heartbeat_collect.py
  - _finish_soon  <- tests/tools/test_delegate_stale_heartbeat_collect.py
  - _parent  <- tests/tools/test_delegate_stale_heartbeat_collect.py
  - test_async_batch_reports_when_the_last_child_settles_after_its_verdict  <- tests/tools/test_delegate_stale_heartbeat_collect.py
  - test_stale_verdict_collects_a_result_that_lands_within_the_grace_window  <- tests/tools/test_delegate_stale_heartbeat_collect.py
  - test_stale_verdict_without_a_result_in_the_grace_window_is_a_timeout  <- tests/tools/test_delegate_stale_heartbeat_collect.py

### qubit_runtime/tests/tools/test_qqbot_send_message_media.py  (8 missing)
  - TestQqbotMediaFailureFallsBackToText  <- tests/tools/test_qqbot_send_message_media.py
  - TestQqbotMediaFileType  <- tests/tools/test_qqbot_send_message_media.py
  - TestQqbotSendMediaMessageBody  <- tests/tools/test_qqbot_send_message_media.py
  - TestSendQqbotMedia  <- tests/tools/test_qqbot_send_message_media.py
  - TestSendToPlatformQqbotMediaRouting  <- tests/tools/test_qqbot_send_message_media.py
  - _pconfig  <- tests/tools/test_qqbot_send_message_media.py
  - _token_client  <- tests/tools/test_qqbot_send_message_media.py
  - _token_ok  <- tests/tools/test_qqbot_send_message_media.py

### qubit_runtime/tests/tools/test_tts_provider_authority.py  (8 missing)
  - TestRegisteredHandler  <- tests/tools/test_tts_provider_authority.py
  - TestResolverAuthority  <- tests/tools/test_tts_provider_authority.py
  - TestSchema  <- tests/tools/test_tts_provider_authority.py
  - TestToolLevelProviderAuthority  <- tests/tools/test_tts_provider_authority.py
  - _OpenaiBackendStub  <- tests/tools/test_tts_provider_authority.py
  - _fake_key  <- tests/tools/test_tts_provider_authority.py
  - clean_env  <- tests/tools/test_tts_provider_authority.py
  - openai_backend  <- tests/tools/test_tts_provider_authority.py

### qubit_runtime/agent/skill_utils.py  (7 missing)
  - AMBIGUOUS_SKILL_PREFIX  <- agent/skill_utils.py
  - get_skill_search_roots  <- agent/skill_utils.py
  - is_disabled_entry  <- agent/skill_utils.py
  - pick_skill_candidate  <- agent/skill_utils.py
  - provably_same_skill  <- agent/skill_utils.py
  - resolve_skill_catalog  <- agent/skill_utils.py
  - skill_candidate_rank  <- agent/skill_utils.py

### qubit_runtime/cron/blueprint_plugins.py  (7 missing)
  - _placeholders  <- cron/blueprint_plugins.py
  - _slot  <- cron/blueprint_plugins.py
  - _strings  <- cron/blueprint_plugins.py
  - _text  <- cron/blueprint_plugins.py
  - _validate  <- cron/blueprint_plugins.py
  - build_plugin_blueprint  <- cron/blueprint_plugins.py
  - plugin_blueprints  <- cron/blueprint_plugins.py

### qubit_runtime/qubit_cli/kanban_db_workspace.py  (7 missing)
  - _conn_uses_path  <- hermes_cli/kanban_db_workspace.py
  - _defer_shared_workspace_cleanup  <- hermes_cli/kanban_db_workspace.py
  - _defer_shared_worktree_cleanup  <- hermes_cli/kanban_db_workspace.py
  - _lexical_path  <- hermes_cli/kanban_db_workspace.py
  - _other_board_uses_path  <- hermes_cli/kanban_db_workspace.py
  - _sibling_board_db_files  <- hermes_cli/kanban_db_workspace.py
  - _workspace_in_use_by_other  <- hermes_cli/kanban_db_workspace.py

### qubit_runtime/qubit_cli/plugin_host.py  (7 missing)
  - PluginHost  <- hermes_cli/plugin_host.py
  - _ObjectSlot  <- hermes_cli/plugin_host.py
  - _holds_live_handle  <- hermes_cli/plugin_host.py
  - _import_ref  <- hermes_cli/plugin_host.py
  - _method_proxy  <- hermes_cli/plugin_host.py
  - _pump_stderr  <- hermes_cli/plugin_host.py
  - _run_on_gateway_loop  <- hermes_cli/plugin_host.py

### qubit_runtime/qubit_cli/plugin_host_profiles.py  (7 missing)
  - _as_method  <- hermes_cli/plugin_host_profiles.py
  - _cached_extraction  <- hermes_cli/plugin_host_profiles.py
  - _extract  <- hermes_cli/plugin_host_profiles.py
  - _fingerprint  <- hermes_cli/plugin_host_profiles.py
  - _host_call  <- hermes_cli/plugin_host_profiles.py
  - _profile_proxy  <- hermes_cli/plugin_host_profiles.py
  - load_hosted_profiles  <- hermes_cli/plugin_host_profiles.py

### qubit_runtime/qubit_cli/uninstall.py  (7 missing)
  - _launchd_gateway_plists  <- hermes_cli/uninstall.py
  - _macos_cache_leftover_dirs  <- hermes_cli/uninstall.py
  - _remove_path_or_tree  <- hermes_cli/uninstall.py
  - _rmtree_if_exists  <- hermes_cli/uninstall.py
  - _xdg_leftover_paths  <- hermes_cli/uninstall.py
  - remove_dashboard_launchd_jobs  <- hermes_cli/uninstall.py
  - remove_desktop_app_leftovers  <- hermes_cli/uninstall.py

### qubit_runtime/scripts/code_health/cli.py  (7 missing)
  - _parser  <- scripts/code_health/cli.py
  - _report  <- scripts/code_health/cli.py
  - _skipped  <- scripts/code_health/cli.py
  - enforcement  <- scripts/code_health/cli.py
  - main  <- scripts/code_health/cli.py
  - parse_switch  <- scripts/code_health/cli.py
  - run  <- scripts/code_health/cli.py

### qubit_runtime/scripts/code_health/report.py  (7 missing)
  - _allow_lines  <- scripts/code_health/report.py
  - _fix_text  <- scripts/code_health/report.py
  - _track  <- scripts/code_health/report.py
  - apply_allows  <- scripts/code_health/report.py
  - format_findings  <- scripts/code_health/report.py
  - summarize_tree  <- scripts/code_health/report.py
  - verdict  <- scripts/code_health/report.py

### qubit_runtime/tests/agent/test_auxiliary_relay.py  (7 missing)
  - _ProviderStatusError  <- tests/agent/test_auxiliary_relay.py
  - _aux_attempts  <- tests/agent/test_auxiliary_relay.py
  - _aux_call  <- tests/agent/test_auxiliary_relay.py
  - _aux_route_rows  <- tests/agent/test_auxiliary_relay.py
  - test_aborted_auxiliary_call_is_cancelled_not_failed  <- tests/agent/test_auxiliary_relay.py
  - test_auxiliary_call_finishing_under_the_turns_live_scope_is_counted  <- tests/agent/test_auxiliary_relay.py
  - test_auxiliary_model_routes_carry_classified_error_class  <- tests/agent/test_auxiliary_relay.py

### qubit_runtime/tests/agent/test_turn_end_stall_diagnostics.py  (7 missing)
  - _StubAgent  <- tests/agent/test_turn_end_stall_diagnostics.py
  - _StubBudget  <- tests/agent/test_turn_end_stall_diagnostics.py
  - _StubCompressor  <- tests/agent/test_turn_end_stall_diagnostics.py
  - _finalize  <- tests/agent/test_turn_end_stall_diagnostics.py
  - test_turn_end_logs_stamp_the_activity_clock  <- tests/agent/test_turn_end_stall_diagnostics.py
  - test_turn_end_stamp_failure_never_breaks_the_finalizer  <- tests/agent/test_turn_end_stall_diagnostics.py
  - test_watchdog_dump_the_turn_thread_stack_when_it_fires  <- tests/agent/test_turn_end_stall_diagnostics.py

### qubit_runtime/tests/gateway/test_discord_turn_prompt_parity.py  (7 missing)
  - _DM  <- tests/gateway/test_discord_turn_prompt_parity.py
  - _Text  <- tests/gateway/test_discord_turn_prompt_parity.py
  - _Thread  <- tests/gateway/test_discord_turn_prompt_parity.py
  - _adapter  <- tests/gateway/test_discord_turn_prompt_parity.py
  - _assert_same_prompt_inputs  <- tests/gateway/test_discord_turn_prompt_parity.py
  - _interaction  <- tests/gateway/test_discord_turn_prompt_parity.py
  - _parent  <- tests/gateway/test_discord_turn_prompt_parity.py

### qubit_runtime/tests/gateway/test_platform_entry_plugin_seams.py  (7 missing)
  - _event_source  <- tests/gateway/test_platform_entry_plugin_seams.py
  - _runner  <- tests/gateway/test_platform_entry_plugin_seams.py
  - plugin_platform  <- tests/gateway/test_platform_entry_plugin_seams.py
  - test_display_tier_supplies_builtin_defaults_below_user_overrides  <- tests/gateway/test_platform_entry_plugin_seams.py
  - test_platform_entry_seam_defaults_are_inert  <- tests/gateway/test_platform_entry_plugin_seams.py
  - test_trusted_inbound_platform_bypasses_allowlists  <- tests/gateway/test_platform_entry_plugin_seams.py
  - test_user_plugin_cannot_mark_a_core_platform_trusted_inbound  <- tests/gateway/test_platform_entry_plugin_seams.py

### qubit_runtime/tests/hermes_cli/test_shared_metrics_signals.py  (7 missing)
  - _codex_start  <- tests/hermes_cli/test_shared_metrics_signals.py
  - _guest_sign_in  <- tests/hermes_cli/test_shared_metrics_signals.py
  - _poll  <- tests/hermes_cli/test_shared_metrics_signals.py
  - _wire  <- tests/hermes_cli/test_shared_metrics_signals.py
  - test_cancel_or_lapsed_code_is_never_a_failure_and_poller_errors_keep_their_class  <- tests/hermes_cli/test_shared_metrics_signals.py
  - test_hermes_internal_work_never_latches_adoption  <- tests/hermes_cli/test_shared_metrics_signals.py
  - test_wire_failures_keep_the_producers_class_to_the_recorded_end  <- tests/hermes_cli/test_shared_metrics_signals.py

### qubit_runtime/tests/hermes_cli/test_update_shallow_heal.py  (7 missing)
  - _check_out_branch_outside_the_refspec  <- tests/hermes_cli/test_update_shallow_heal.py
  - _check_out_main  <- tests/hermes_cli/test_update_shallow_heal.py
  - _check_out_release_under_a_clashing_local_tag  <- tests/hermes_cli/test_update_shallow_heal.py
  - _check_out_tag_only_release  <- tests/hermes_cli/test_update_shallow_heal.py
  - _side_history  <- tests/hermes_cli/test_update_shallow_heal.py
  - test_failed_conversion_keeps_the_checkout_treeless_for_a_retry  <- tests/hermes_cli/test_update_shallow_heal.py
  - test_treeless_checkout_gets_its_whole_history_once_so_walks_stay_offline  <- tests/hermes_cli/test_update_shallow_heal.py

### qubit_runtime/tests/tools/test_bot_mode_dm.py  (7 missing)
  - test_live_dm_wait_keeps_claimed_turn_until_it_settles  <- tests/tools/test_bot_mode_dm.py
  - test_live_dm_wait_rechecks_receipt_when_owner_disappears_at_claim  <- tests/tools/test_bot_mode_dm.py
  - test_live_dm_wait_releases_claimed_runner_after_owner_is_gone  <- tests/tools/test_bot_mode_dm.py
  - test_live_dm_wait_releases_queued_runner_when_pinned_owner_is_gone  <- tests/tools/test_bot_mode_dm.py
  - test_live_dm_wait_releases_runner_when_owner_lookup_keeps_failing  <- tests/tools/test_bot_mode_dm.py
  - test_live_dm_wait_reports_reply_after_initial_wait_budget  <- tests/tools/test_bot_mode_dm.py
  - test_live_dm_wait_stops_at_its_deadline_while_the_owner_holds_the_receipt  <- tests/tools/test_bot_mode_dm.py

### qubit_runtime/tests/tools/test_browser_supervisor_capture.py  (7 missing)
  - _FakeCDP  <- tests/tools/test_browser_supervisor_capture.py
  - _raises  <- tests/tools/test_browser_supervisor_capture.py
  - _wait  <- tests/tools/test_browser_supervisor_capture.py
  - cdp  <- tests/tools/test_browser_supervisor_capture.py
  - registry  <- tests/tools/test_browser_supervisor_capture.py
  - test_call_round_trips_on_captured_connection  <- tests/tools/test_browser_supervisor_capture.py
  - test_handle_never_follows_a_reconnect_or_replacement  <- tests/tools/test_browser_supervisor_capture.py

### qubit_runtime/tests/tools/test_connectors_import_deadlock.py  (7 missing)
  - ITER  <- tests/tools/test_connectors_import_deadlock.py
  - REPO_ROOT  <- tests/tools/test_connectors_import_deadlock.py
  - _run_snippet  <- tests/tools/test_connectors_import_deadlock.py
  - purge  <- tests/tools/test_connectors_import_deadlock.py
  - run  <- tests/tools/test_connectors_import_deadlock.py
  - test_concurrent_package_and_submodule_import_never_deadlocks  <- tests/tools/test_connectors_import_deadlock.py
  - test_package_import_does_not_load_submodules  <- tests/tools/test_connectors_import_deadlock.py

### qubit_runtime/agent/agent_runtime_helpers.py  (6 missing)
  - _count_unnamed_row  <- agent/agent_runtime_helpers.py
  - _normalize_sentinel_encoded_content  <- agent/agent_runtime_helpers.py
  - _remember_own_row  <- agent/agent_runtime_helpers.py
  - _retire_dropped_row  <- agent/agent_runtime_helpers.py
  - _retire_leading_drops  <- agent/agent_runtime_helpers.py
  - _socket_from_response  <- agent/agent_runtime_helpers.py

### qubit_runtime/agent/conversation_compression_archive.py  (6 missing)
  - MERGED_DURABLE_ROWS  <- agent/conversation_compression_archive.py
  - OWN_ROW  <- agent/conversation_compression_archive.py
  - RETIRED_DURABLE_ROWS  <- agent/conversation_compression_archive.py
  - RETIRED_ROW  <- agent/conversation_compression_archive.py
  - UNNAMED_DURABLE_ROWS  <- agent/conversation_compression_archive.py
  - retired_row_payload  <- agent/conversation_compression_archive.py

### qubit_runtime/evals/plugin_isolation/audit_catalog.py  (6 missing)
  - REPO_ROOT  <- evals/plugin_isolation/audit_catalog.py
  - _audit  <- evals/plugin_isolation/audit_catalog.py
  - _fetch  <- evals/plugin_isolation/audit_catalog.py
  - _git  <- evals/plugin_isolation/audit_catalog.py
  - _reason_class  <- evals/plugin_isolation/audit_catalog.py
  - main  <- evals/plugin_isolation/audit_catalog.py

### qubit_runtime/qubit_cli/inventory.py  (6 missing)
  - _apply_limits  <- hermes_cli/inventory.py
  - _apply_usage  <- hermes_cli/inventory.py
  - _iso_from_epoch  <- hermes_cli/inventory.py
  - _pool_entry_host  <- hermes_cli/inventory.py
  - _pool_entry_route_base_url  <- hermes_cli/inventory.py
  - _pool_usage_accounts  <- hermes_cli/inventory.py

### qubit_runtime/qubit_cli/tips.py  (6 missing)
  - PLACEHOLDER_KEY_PREFIX  <- hermes_cli/tips.py
  - TIP_KEY_PREFIX  <- hermes_cli/tips.py
  - _catalog_keys  <- hermes_cli/tips.py
  - composer_placeholder_keys  <- hermes_cli/tips.py
  - reset_tips_cache  <- hermes_cli/tips.py
  - tip_keys  <- hermes_cli/tips.py

### qubit_runtime/scripts/code_health/measure.py  (6 missing)
  - Measurer  <- scripts/code_health/measure.py
  - _executable_lines  <- scripts/code_health/measure.py
  - _own_complexity  <- scripts/code_health/measure.py
  - _python_comments  <- scripts/code_health/measure.py
  - _regex_rules  <- scripts/code_health/measure.py
  - _string_statements  <- scripts/code_health/measure.py

### qubit_runtime/scripts/code_health/ruff_runner.py  (6 missing)
  - RuffFile  <- scripts/code_health/ruff_runner.py
  - _record  <- scripts/code_health/ruff_runner.py
  - _version_of  <- scripts/code_health/ruff_runner.py
  - pinned_version  <- scripts/code_health/ruff_runner.py
  - resolve_ruff  <- scripts/code_health/ruff_runner.py
  - run_ruff  <- scripts/code_health/ruff_runner.py

### qubit_runtime/scripts/code_health/ts_measure.py  (6 missing)
  - _cache_root  <- scripts/code_health/ts_measure.py
  - _install  <- scripts/code_health/ts_measure.py
  - _version_at  <- scripts/code_health/ts_measure.py
  - measure_ts  <- scripts/code_health/ts_measure.py
  - pinned_typescript  <- scripts/code_health/ts_measure.py
  - resolve_typescript  <- scripts/code_health/ts_measure.py

### qubit_runtime/tests/agent/test_auxiliary_client_minimax_oauth.py  (6 missing)
  - INFERENCE_BASE_URL  <- tests/agent/test_auxiliary_client_minimax_oauth.py
  - _runtime_creds  <- tests/agent/test_auxiliary_client_minimax_oauth.py
  - test_resolve_minimax_oauth_builds_anthropic_wrapper_with_third_party_wire  <- tests/agent/test_auxiliary_client_minimax_oauth.py
  - test_resolve_minimax_oauth_missing_credentials_log_once_debug  <- tests/agent/test_auxiliary_client_minimax_oauth.py
  - test_resolve_minimax_oauth_probe_reads_raw_state_no_refresh  <- tests/agent/test_auxiliary_client_minimax_oauth.py
  - test_resolve_minimax_oauth_tool_names_unprefixed_on_wire  <- tests/agent/test_auxiliary_client_minimax_oauth.py

### qubit_runtime/tests/gateway/test_plugin_origin_injection.py  (6 missing)
  - PLUGIN  <- tests/gateway/test_plugin_origin_injection.py
  - _RecordingAdapter  <- tests/gateway/test_plugin_origin_injection.py
  - _context  <- tests/gateway/test_plugin_origin_injection.py
  - _origin  <- tests/gateway/test_plugin_origin_injection.py
  - _write_home  <- tests/gateway/test_plugin_origin_injection.py
  - gateway  <- tests/gateway/test_plugin_origin_injection.py

### qubit_runtime/tests/hermes_cli/test_minimax_oauth.py  (6 missing)
  - _RotatingPortal  <- tests/hermes_cli/test_minimax_oauth.py
  - _minimax_expiry_fields_for_test  <- tests/hermes_cli/test_minimax_oauth.py
  - _minimax_state  <- tests/hermes_cli/test_minimax_oauth.py
  - test_aux_refresh_preserves_active_provider  <- tests/hermes_cli/test_minimax_oauth.py
  - test_concurrent_refresh_rotates_token_once_and_keeps_login  <- tests/hermes_cli/test_minimax_oauth.py
  - test_minimax_oauth_login_sets_active_provider  <- tests/hermes_cli/test_minimax_oauth.py

### qubit_runtime/tests/hermes_cli/test_picker_probe_budget_decoupling.py  (6 missing)
  - _custom_row  <- tests/hermes_cli/test_picker_probe_budget_decoupling.py
  - _no_builtin_catalog_fetches  <- tests/hermes_cli/test_picker_probe_budget_decoupling.py
  - _slow_endpoint_rows  <- tests/hermes_cli/test_picker_probe_budget_decoupling.py
  - test_default_callers_keep_the_fast_budget_coupling  <- tests/hermes_cli/test_picker_probe_budget_decoupling.py
  - test_explicit_fast_probe_true_still_cuts_the_budget  <- tests/hermes_cli/test_picker_probe_budget_decoupling.py
  - test_full_budget_keeps_slow_endpoint_models  <- tests/hermes_cli/test_picker_probe_budget_decoupling.py

### qubit_runtime/tests/hermes_cli/test_plugin_auxiliary_tasks.py  (6 missing)
  - _register  <- tests/hermes_cli/test_plugin_auxiliary_tasks.py
  - _set_aux  <- tests/hermes_cli/test_plugin_auxiliary_tasks.py
  - aux_home  <- tests/hermes_cli/test_plugin_auxiliary_tasks.py
  - test_bad_inherit_from_warns_and_registers_without_inheritance  <- tests/hermes_cli/test_plugin_auxiliary_tasks.py
  - test_inheritance_cycle_does_not_recurse  <- tests/hermes_cli/test_plugin_auxiliary_tasks.py
  - test_inheriting_task_follows_base_until_pinned  <- tests/hermes_cli/test_plugin_auxiliary_tasks.py

### qubit_runtime/tests/hermes_cli/test_sessions_export_backup_round_trip.py  (6 missing)
  - SID  <- tests/hermes_cli/test_sessions_export_backup_round_trip.py
  - _export  <- tests/hermes_cli/test_sessions_export_backup_round_trip.py
  - _seed_compacted_session  <- tests/hermes_cli/test_sessions_export_backup_round_trip.py
  - _shape  <- tests/hermes_cli/test_sessions_export_backup_round_trip.py
  - test_jsonl_backup_refuses_a_session_over_max_export_messages  <- tests/hermes_cli/test_sessions_export_backup_round_trip.py
  - test_jsonl_backup_round_trips_compaction_archived_turns  <- tests/hermes_cli/test_sessions_export_backup_round_trip.py

### qubit_runtime/tests/hermes_cli/test_source_launcher_publication.py  (6 missing)
  - _record_store_python  <- tests/hermes_cli/test_source_launcher_publication.py
  - test_install_launchers_never_bind_a_fixture_store_under_scratch  <- tests/hermes_cli/test_source_launcher_publication.py
  - test_publication_uses_the_runtime_store_when_the_tree_records_none  <- tests/hermes_cli/test_source_launcher_publication.py
  - test_runtime_override_still_selects_the_runtime_python  <- tests/hermes_cli/test_source_launcher_publication.py
  - test_service_launcher_binds_the_tree_store_despite_inherited_runtime_override  <- tests/hermes_cli/test_source_launcher_publication.py
  - test_stage_launcher_ignores_an_inherited_runtime_directory  <- tests/hermes_cli/test_source_launcher_publication.py

### qubit_runtime/tests/hermes_cli/test_web_server_owner_exit.py  (6 missing)
  - _Fence  <- tests/hermes_cli/test_web_server_owner_exit.py
  - _Server  <- tests/hermes_cli/test_web_server_owner_exit.py
  - _lock  <- tests/hermes_cli/test_web_server_owner_exit.py
  - _run  <- tests/hermes_cli/test_web_server_owner_exit.py
  - test_retires_only_on_a_valid_lock_naming_another_spawn  <- tests/hermes_cli/test_web_server_owner_exit.py
  - test_superseded_backend_retires_through_the_fence_only_when_idle  <- tests/hermes_cli/test_web_server_owner_exit.py

### qubit_runtime/tests/plugins/test_plugin_automation_blueprints.py  (6 missing)
  - _plugin_keys  <- tests/plugins/test_plugin_automation_blueprints.py
  - _write_plugin  <- tests/plugins/test_plugin_automation_blueprints.py
  - homes  <- tests/plugins/test_plugin_automation_blueprints.py
  - test_malformed_plugin_blueprint_is_warned_and_ignored  <- tests/plugins/test_plugin_automation_blueprints.py
  - test_plugin_blueprint_is_listed_by_slash_command_and_matches  <- tests/plugins/test_plugin_automation_blueprints.py
  - test_plugin_cannot_shadow_builtin_and_bad_keys_are_rejected  <- tests/plugins/test_plugin_automation_blueprints.py

### qubit_runtime/tests/tools/test_mcp_tool_issue_948.py  (6 missing)
  - _bare_exe  <- tests/tools/test_mcp_tool_issue_948.py
  - test_bare_launcher_commands_keep_the_managed_first_resolution  <- tests/tools/test_mcp_tool_issue_948.py
  - test_bare_python3_keeps_the_managed_hit_when_the_user_has_none  <- tests/tools/test_mcp_tool_issue_948.py
  - test_bare_python3_steps_past_the_managed_runtime_to_the_user_hit  <- tests/tools/test_mcp_tool_issue_948.py
  - test_first_user_which_hit_follows_the_child_env_pathext  <- tests/tools/test_mcp_tool_issue_948.py
  - test_tail_server_stderr_scopes_to_the_named_server  <- tests/tools/test_mcp_tool_issue_948.py

### qubit_runtime/tests/tools/test_ssh_session_cwd.py  (6 missing)
  - CASES  <- tests/tools/test_ssh_session_cwd.py
  - HOST_HOME  <- tests/tools/test_ssh_session_cwd.py
  - RecordingEnvironment  <- tests/tools/test_ssh_session_cwd.py
  - isolated_session_state  <- tests/tools/test_ssh_session_cwd.py
  - test_late_registration_keeps_file_execution_in_backend_namespace  <- tests/tools/test_ssh_session_cwd.py
  - test_recorded_session_cwd_is_coerced_without_changing_explicit_remote_workdir  <- tests/tools/test_ssh_session_cwd.py

### qubit_runtime/agent/auxiliary_call_outcome.py  (5 missing)
  - PROVIDER_ERROR_ATTR  <- agent/auxiliary_call_outcome.py
  - _EmbeddedProviderError  <- agent/auxiliary_call_outcome.py
  - embedded_provider_error  <- agent/auxiliary_call_outcome.py
  - error_class  <- agent/auxiliary_call_outcome.py
  - is_cancellation  <- agent/auxiliary_call_outcome.py

### qubit_runtime/agent/auxiliary_client.py  (5 missing)
  - _attempt_stream_socket  <- agent/auxiliary_client.py
  - _borrowed_main_credential_key  <- agent/auxiliary_client.py
  - _note_relay_auxiliary_error  <- agent/auxiliary_client.py
  - _read_main_api_key_if_same_origin  <- agent/auxiliary_client.py
  - _unwrap_data_envelope  <- agent/auxiliary_client.py

### qubit_runtime/agent/auxiliary_client_registry.py  (5 missing)
  - REGISTRY_AUTHTYPE_ARMS  <- agent/auxiliary_client_registry.py
  - _log_once_debug  <- agent/auxiliary_client_registry.py
  - _resolve_bedrock_arm  <- agent/auxiliary_client_registry.py
  - _resolve_minimax_oauth_arm  <- agent/auxiliary_client_registry.py
  - _resolve_vertex_arm  <- agent/auxiliary_client_registry.py

### qubit_runtime/agent/auxiliary_stream_watchdog.py  (5 missing)
  - ChatStreamWatchdog  <- agent/auxiliary_stream_watchdog.py
  - _close_chunk_stream  <- agent/auxiliary_stream_watchdog.py
  - _stream_socket  <- agent/auxiliary_stream_watchdog.py
  - chat_stream_windows  <- agent/auxiliary_stream_watchdog.py
  - consume_chat_stream  <- agent/auxiliary_stream_watchdog.py

### qubit_runtime/qubit_cli/_launchers.py  (5 missing)
  - _first_token  <- hermes_cli/_launchers.py
  - _inline_string_literal  <- hermes_cli/_launchers.py
  - _kept_shared_launcher  <- hermes_cli/_launchers.py
  - _launcher_python  <- hermes_cli/_launchers.py
  - _store_python  <- hermes_cli/_launchers.py

### qubit_runtime/qubit_cli/cli_commands_mixin.py  (5 missing)
  - _gt  <- hermes_cli/cli_commands_mixin.py
  - _lines  <- hermes_cli/cli_commands_mixin.py
  - _local_engine_lines  <- hermes_cli/cli_commands_mixin.py
  - _t  <- hermes_cli/cli_commands_mixin.py
  - _tn  <- hermes_cli/cli_commands_mixin.py

### qubit_runtime/qubit_cli/gitlock.py  (5 missing)
  - _git_version  <- hermes_cli/gitlock.py
  - _history_trees_complete  <- hermes_cli/gitlock.py
  - convert_treeless_checkout  <- hermes_cli/gitlock.py
  - convert_treeless_checkout_first  <- hermes_cli/gitlock.py
  - settle_partial_clone_maintenance  <- hermes_cli/gitlock.py

### qubit_runtime/qubit_cli/local_runtime/devices.py  (5 missing)
  - GGML_DEVICE_GPU  <- hermes_cli/local_runtime/devices.py
  - GGML_DEVICE_IGPU  <- hermes_cli/local_runtime/devices.py
  - _library  <- hermes_cli/local_runtime/devices.py
  - _read_devices  <- hermes_cli/local_runtime/devices.py
  - probe_devices  <- hermes_cli/local_runtime/devices.py

### qubit_runtime/qubit_cli/plugins_cmd_install.py  (5 missing)
  - _ask_yes_no  <- hermes_cli/plugins_cmd_install.py
  - _known_issue_warnings  <- hermes_cli/plugins_cmd_install.py
  - _select_memory_provider  <- hermes_cli/plugins_cmd_install.py
  - recorded_install  <- hermes_cli/plugins_cmd_install.py
  - shared_dependency_answers  <- hermes_cli/plugins_cmd_install.py

### qubit_runtime/qubit_cli/profiles.py  (5 missing)
  - PROFILE_CREDENTIAL_PATHS  <- hermes_cli/profiles.py
  - _export_credential_entries  <- hermes_cli/profiles.py
  - _fold  <- hermes_cli/profiles.py
  - profile_path_contains_private_store  <- hermes_cli/profiles.py
  - profile_path_is_private  <- hermes_cli/profiles.py

### qubit_runtime/qubit_cli/update_cmd.py  (5 missing)
  - _heal_stale_shallow_checkout  <- hermes_cli/update_cmd.py
  - _map_ssl_cert_file_for_git  <- hermes_cli/update_cmd.py
  - _record_snapshot_stage  <- hermes_cli/update_cmd.py
  - _record_update_initiator  <- hermes_cli/update_cmd.py
  - _update_movement_baseline  <- hermes_cli/update_cmd.py

### qubit_runtime/plugins/platforms/whatsapp/adapter.py  (5 missing)
  - _import_aiohttp  <- plugins/platforms/whatsapp/adapter.py
  - _port_listener_pids  <- plugins/platforms/whatsapp/adapter.py
  - _read_bridge_pidfile  <- plugins/platforms/whatsapp/adapter.py
  - _session_dir  <- plugins/platforms/whatsapp/adapter.py
  - ensure_aiohttp  <- plugins/platforms/whatsapp/adapter.py

### qubit_runtime/plugins/plugin_loader.py  (5 missing)
  - _load_plugin_module_locked  <- plugins/plugin_loader.py
  - _load_would_deadlock  <- plugins/plugin_loader.py
  - _module_load_lock  <- plugins/plugin_loader.py
  - _waits_for  <- plugins/plugin_loader.py
  - bounded_load_wait  <- plugins/plugin_loader.py

### qubit_runtime/scripts/ci/check_agents_md_size.py  (5 missing)
  - CHAIN_MAX_CHARS  <- scripts/ci/check_agents_md_size.py
  - ROOT_MAX_CHARS  <- scripts/ci/check_agents_md_size.py
  - _chain  <- scripts/ci/check_agents_md_size.py
  - _int_constant  <- scripts/ci/check_agents_md_size.py
  - main  <- scripts/ci/check_agents_md_size.py

### qubit_runtime/scripts/code_health/model.py  (5 missing)
  - FileMeasure  <- scripts/code_health/model.py
  - Finding  <- scripts/code_health/model.py
  - Hit  <- scripts/code_health/model.py
  - MODULE_SCOPE  <- scripts/code_health/model.py
  - Unit  <- scripts/code_health/model.py

### qubit_runtime/scripts/code_health/replay.py  (5 missing)
  - build_manifest  <- scripts/code_health/replay.py
  - main  <- scripts/code_health/replay.py
  - merged_prs  <- scripts/code_health/replay.py
  - pr_range  <- scripts/code_health/replay.py
  - replay_one  <- scripts/code_health/replay.py

### qubit_runtime/tests/cron/test_cron_execution_identity.py  (5 missing)
  - CTX  <- tests/cron/test_cron_execution_identity.py
  - _record  <- tests/cron/test_cron_execution_identity.py
  - register  <- tests/cron/test_cron_execution_identity.py
  - test_identity_is_scoped_and_not_inherited_by_delegated_children  <- tests/cron/test_cron_execution_identity.py
  - test_tool_hook_in_a_ticked_job_sees_its_execution_and_none_outside  <- tests/cron/test_cron_execution_identity.py

### qubit_runtime/tests/gateway/test_discord_voice_stale_binding.py  (5 missing)
  - _ConnectedVoiceClient  <- tests/gateway/test_discord_voice_stale_binding.py
  - _OneBatchReceiver  <- tests/gateway/test_discord_voice_stale_binding.py
  - _fall_silent  <- tests/gateway/test_discord_voice_stale_binding.py
  - _listening_adapter  <- tests/gateway/test_discord_voice_stale_binding.py
  - _speak  <- tests/gateway/test_discord_voice_stale_binding.py

### qubit_runtime/tests/gateway/test_post_gateway_admission.py  (5 missing)
  - HOOK  <- tests/gateway/test_post_gateway_admission.py
  - _allow_all  <- tests/gateway/test_post_gateway_admission.py
  - _event  <- tests/gateway/test_post_gateway_admission.py
  - _register  <- tests/gateway/test_post_gateway_admission.py
  - _runner  <- tests/gateway/test_post_gateway_admission.py

### qubit_runtime/tests/hermes_cli/test_doctor_live.py  (5 missing)
  - _Probes  <- tests/hermes_cli/test_doctor_live.py
  - clash_env  <- tests/hermes_cli/test_doctor_live.py
  - test_http_get_sanitizes_bracketed_ipv6_no_proxy  <- tests/hermes_cli/test_doctor_live.py
  - test_run_live_checks_leaves_a_clean_environment_untouched  <- tests/hermes_cli/test_doctor_live.py
  - test_run_live_checks_sanitizes_clash_env_and_probes_pass  <- tests/hermes_cli/test_doctor_live.py

### qubit_runtime/tests/hermes_cli/test_env_write_race.py  (5 missing)
  - _env_keys  <- tests/hermes_cli/test_env_write_race.py
  - _race_two_writers  <- tests/hermes_cli/test_env_write_race.py
  - race_env_home  <- tests/hermes_cli/test_env_write_race.py
  - test_concurrent_remove_and_save_do_not_lose_keys  <- tests/hermes_cli/test_env_write_race.py
  - test_concurrent_save_env_value_writers_do_not_lose_keys  <- tests/hermes_cli/test_env_write_race.py

### qubit_runtime/tests/hermes_cli/test_inventory_featured.py  (5 missing)
  - test_builtin_flat_namespace_reseller_gets_no_featured_shortlist  <- tests/hermes_cli/test_inventory_featured.py
  - test_builtin_non_aggregator_row_gets_no_featured_shortlist  <- tests/hermes_cli/test_inventory_featured.py
  - test_builtin_routing_aggregator_keeps_featured_shortlist  <- tests/hermes_cli/test_inventory_featured.py
  - test_custom_proxy_row_gets_no_featured_shortlist  <- tests/hermes_cli/test_inventory_featured.py
  - test_user_defined_row_gets_no_featured_shortlist  <- tests/hermes_cli/test_inventory_featured.py

### qubit_runtime/tests/hermes_cli/test_model_options_rate_limited_pool.py  (5 missing)
  - _pool_entry  <- tests/hermes_cli/test_model_options_rate_limited_pool.py
  - pooled_home  <- tests/hermes_cli/test_model_options_rate_limited_pool.py
  - test_kept_row_says_which_limit_and_when_it_resets  <- tests/hermes_cli/test_model_options_rate_limited_pool.py
  - test_picker_visibility_keeps_the_full_custom_probe_budget  <- tests/hermes_cli/test_model_options_rate_limited_pool.py
  - test_rate_limited_pool_keeps_its_provider_row  <- tests/hermes_cli/test_model_options_rate_limited_pool.py

### qubit_runtime/tests/hermes_cli/test_plugin_message_injection.py  (5 missing)
  - ORIGIN  <- tests/hermes_cli/test_plugin_message_injection.py
  - test_origin_injection_never_lands_in_the_attached_cli  <- tests/hermes_cli/test_plugin_message_injection.py
  - test_origin_injection_permission_comes_from_the_plugins_own_profile  <- tests/hermes_cli/test_plugin_message_injection.py
  - test_origin_injection_rejects_ambiguous_or_malformed_targets  <- tests/hermes_cli/test_plugin_message_injection.py
  - test_origin_injection_targets_the_plugins_own_home  <- tests/hermes_cli/test_plugin_message_injection.py

### qubit_runtime/tests/hermes_cli/test_plugins_middleware_chain.py  (5 missing)
  - SEEN  <- tests/hermes_cli/test_plugins_middleware_chain.py
  - register  <- tests/hermes_cli/test_plugins_middleware_chain.py
  - seen  <- tests/hermes_cli/test_plugins_middleware_chain.py
  - test_request_middleware_in_place_mutation_does_not_leak  <- tests/hermes_cli/test_plugins_middleware_chain.py
  - test_request_middleware_rewrites_compose  <- tests/hermes_cli/test_plugins_middleware_chain.py

### qubit_runtime/tests/hermes_cli/test_skills_hub.py  (5 missing)
  - _meta  <- tests/hermes_cli/test_skills_hub.py
  - _sink_console  <- tests/hermes_cli/test_skills_hub.py
  - _stub_search  <- tests/hermes_cli/test_skills_hub.py
  - test_fetch_failure_names_rejected_github_credential  <- tests/hermes_cli/test_skills_hub.py
  - test_install_by_name_resolves_to_the_skill_it_names  <- tests/hermes_cli/test_skills_hub.py

### qubit_runtime/tests/hermes_state/test_repaired_history_commit_coverage.py  (5 missing)
  - CASES  <- tests/hermes_state/test_repaired_history_commit_coverage.py
  - SUMMARY  <- tests/hermes_state/test_repaired_history_commit_coverage.py
  - UNSEEN  <- tests/hermes_state/test_repaired_history_commit_coverage.py
  - _call  <- tests/hermes_state/test_repaired_history_commit_coverage.py
  - test_repaired_reload_commit_archives_only_held_rows  <- tests/hermes_state/test_repaired_history_commit_coverage.py

### qubit_runtime/tests/pm/test_node_libatomic_install_boundary.py  (5 missing)
  - _node_script  <- tests/pm/test_node_libatomic_install_boundary.py
  - test_auto_repair_never_reads_a_tty  <- tests/pm/test_node_libatomic_install_boundary.py
  - test_installer_sudo_warmup_honours_non_interactive  <- tests/pm/test_node_libatomic_install_boundary.py
  - test_staged_repair_installs_libatomic_then_reprobes  <- tests/pm/test_node_libatomic_install_boundary.py
  - test_update_completion_installs_before_detaching  <- tests/pm/test_node_libatomic_install_boundary.py

### qubit_runtime/tests/pm/test_update_pin_isolation.py  (5 missing)
  - PinFixture  <- tests/pm/test_update_pin_isolation.py
  - _decision  <- tests/pm/test_update_pin_isolation.py
  - prepare  <- tests/pm/test_update_pin_isolation.py
  - test_all_pins_failing_leaves_lockfile_untouched  <- tests/pm/test_update_pin_isolation.py
  - test_one_failing_pin_does_not_block_the_rest  <- tests/pm/test_update_pin_isolation.py

### qubit_runtime/tests/scripts/test_release_entrypoint.py  (5 missing)
  - test_abandon_force_cancels_in_progress_runs_before_the_draft_and_the_marker  <- tests/scripts/test_release_entrypoint.py
  - test_abandon_that_cannot_cancel_a_run_writes_no_marker_so_it_can_be_retried  <- tests/scripts/test_release_entrypoint.py
  - test_an_unlisted_run_points_at_the_workflow_page  <- tests/scripts/test_release_entrypoint.py
  - test_release_looks_for_the_dispatched_run_only_within_its_wait  <- tests/scripts/test_release_entrypoint.py
  - test_release_output_names_the_ci_run_and_the_publish_step  <- tests/scripts/test_release_entrypoint.py

### qubit_runtime/tests/skills/test_bundled_skill_doc_placeholders.py  (5 missing)
  - REPO_ROOT  <- tests/skills/test_bundled_skill_doc_placeholders.py
  - ROLE_TAG  <- tests/skills/test_bundled_skill_doc_placeholders.py
  - bundled_skill_texts  <- tests/skills/test_bundled_skill_doc_placeholders.py
  - test_bundled_skills_have_no_role_tag_placeholder  <- tests/skills/test_bundled_skill_doc_placeholders.py
  - test_tool_error_strings_have_no_role_tag  <- tests/skills/test_bundled_skill_doc_placeholders.py

### qubit_runtime/tests/test_utils_no_proxy_sanitize.py  (5 missing)
  - TestNormalizeProxyEnvVars  <- tests/test_utils_no_proxy_sanitize.py
  - TestSanitizeNoProxyEntries  <- tests/test_utils_no_proxy_sanitize.py
  - TestSanitizedEnvYieldsAWorkingHttpxClient  <- tests/test_utils_no_proxy_sanitize.py
  - os_get  <- tests/test_utils_no_proxy_sanitize.py
  - proxy_env  <- tests/test_utils_no_proxy_sanitize.py

### qubit_runtime/tests/tools/test_docker_environment.py  (5 missing)
  - test_labels_attribute_populated_after_init  <- tests/tools/test_docker_environment.py
  - test_reuse_environment_fingerprint_ignores_volatile_temp_mounts  <- tests/tools/test_docker_environment.py
  - test_reuse_environment_fingerprint_tracks_immutable_configuration  <- tests/tools/test_docker_environment.py
  - test_reuse_probe_filters_on_environment_fingerprint  <- tests/tools/test_docker_environment.py
  - test_symlinked_skills_tree_reuses_container_across_processes  <- tests/tools/test_docker_environment.py

### qubit_runtime/tests/tools/test_skills_hub_generic_dir.py  (5 missing)
  - ROW  <- tests/tools/test_skills_hub_generic_dir.py
  - _generic_bundle  <- tests/tools/test_skills_hub_generic_dir.py
  - _index_source  <- tests/tools/test_skills_hub_generic_dir.py
  - test_fetch_resolves_generic_skill_directory_from_index_slug  <- tests/tools/test_skills_hub_generic_dir.py
  - test_finds_single_generic_skills_directory_when_slug_is_stale  <- tests/tools/test_skills_hub_generic_dir.py

### qubit_runtime/tests/tools/test_transcription_command_providers.py  (5 missing)
  - TestNormalizeCommandSTTInput  <- tests/tools/test_transcription_command_providers.py
  - TestNormalizeCommandSTTRealFFmpeg  <- tests/tools/test_transcription_command_providers.py
  - _make_opus_webm  <- tests/tools/test_transcription_command_providers.py
  - _python_copy_input_command  <- tests/tools/test_transcription_command_providers.py
  - _python_echo_input_command  <- tests/tools/test_transcription_command_providers.py

### qubit_runtime/tests/tui_gateway/test_completion_cwd_profile_fallback.py  (5 missing)
  - test_named_profile_configured_cwd_still_wins  <- tests/tui_gateway/test_completion_cwd_profile_fallback.py
  - test_named_profile_explicit_pick_still_wins  <- tests/tui_gateway/test_completion_cwd_profile_fallback.py
  - test_named_profile_placeholder_cwd_falls_back_to_own_home  <- tests/tui_gateway/test_completion_cwd_profile_fallback.py
  - test_named_profile_unset_cwd_falls_back_to_own_home  <- tests/tui_gateway/test_completion_cwd_profile_fallback.py
  - test_terminal_task_cwd_named_profile_placeholder_uses_own_home  <- tests/tui_gateway/test_completion_cwd_profile_fallback.py

### qubit_runtime/tools/human_input_hooks.py  (5 missing)
  - HumanInputRequest  <- tools/human_input_hooks.py
  - _fire  <- tools/human_input_hooks.py
  - _identity  <- tools/human_input_hooks.py
  - _redacted  <- tools/human_input_hooks.py
  - human_input_request  <- tools/human_input_hooks.py

### qubit_runtime/tui_gateway/methods_session.py  (5 missing)
  - _auto_resume_denied_source  <- tui_gateway/methods_session.py
  - _branch_idempotent_hit  <- tui_gateway/methods_session.py
  - _branch_title_for  <- tui_gateway/methods_session.py
  - _resume_materialize_minted  <- tui_gateway/methods_session.py
  - _resume_wake_after_interrupt  <- tui_gateway/methods_session.py

### qubit_runtime/tui_gateway/server.py  (5 missing)
  - _any_live_session_claims_key  <- tui_gateway/server.py
  - _fast_tier_applies  <- tui_gateway/server.py
  - _is_server_minted_key  <- tui_gateway/server.py
  - _preview_action_request  <- tui_gateway/server.py
  - _session_show_reasoning  <- tui_gateway/server.py

### qubit_runtime/cron/execution_identity.py  (4 missing)
  - CronExecution  <- cron/execution_identity.py
  - current_cron_execution  <- cron/execution_identity.py
  - enter_cron_execution  <- cron/execution_identity.py
  - exit_cron_execution  <- cron/execution_identity.py

### qubit_runtime/qubit_cli/commands.py  (4 missing)
  - _localized  <- hermes_cli/commands.py
  - build_commands  <- hermes_cli/commands.py
  - build_commands_by_category  <- hermes_cli/commands.py
  - category_label  <- hermes_cli/commands.py

### qubit_runtime/qubit_cli/debug_redaction.py  (4 missing)
  - _decoded_compact_name  <- hermes_cli/debug_redaction.py
  - _redact_headers_and_argv  <- hermes_cli/debug_redaction.py
  - _redact_url_params  <- hermes_cli/debug_redaction.py
  - redact_debug_support_text  <- hermes_cli/debug_redaction.py

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

### qubit_runtime/qubit_cli/proxy/server.py  (4 missing)
  - _canonical_host  <- hermes_cli/proxy/server.py
  - _is_wildcard  <- hermes_cli/proxy/server.py
  - _local_request_error  <- hermes_cli/proxy/server.py
  - _parse_authority  <- hermes_cli/proxy/server.py

### qubit_runtime/qubit_cli/skills_hub.py  (4 missing)
  - _exact_name_hits  <- hermes_cli/skills_hub.py
  - _identifier_slug  <- hermes_cli/skills_hub.py
  - _install_bundled  <- hermes_cli/skills_hub.py
  - _slug_key  <- hermes_cli/skills_hub.py

### qubit_runtime/tests/agent/test_auxiliary_codex_completion_status.py  (4 missing)
  - _adapter_response  <- tests/agent/test_auxiliary_codex_completion_status.py
  - _message  <- tests/agent/test_auxiliary_codex_completion_status.py
  - test_actual_adapter_preserves_completion_contract  <- tests/agent/test_auxiliary_codex_completion_status.py
  - test_compressor_rejects_actual_adapter_partial_summary  <- tests/agent/test_auxiliary_codex_completion_status.py

### qubit_runtime/tests/agent/test_compression_aux_fallback_route.py  (4 missing)
  - _access_error  <- tests/agent/test_compression_aux_fallback_route.py
  - _compressor  <- tests/agent/test_compression_aux_fallback_route.py
  - test_access_failed_aux_falls_back_to_named_main_route  <- tests/agent/test_compression_aux_fallback_route.py
  - test_fallen_back_route_names_main_for_micro_and_never_rides_into_a_pin  <- tests/agent/test_compression_aux_fallback_route.py

### qubit_runtime/tests/agent/test_native_compaction_typed_user_retention.py  (4 missing)
  - _text  <- tests/agent/test_native_compaction_typed_user_retention.py
  - _user  <- tests/agent/test_native_compaction_typed_user_retention.py
  - test_converter_retains_newest_oversized_typed_ask_not_older_ask  <- tests/agent/test_native_compaction_typed_user_retention.py
  - test_typed_boundary_is_budgeted_and_never_substitutes_older_ask  <- tests/agent/test_native_compaction_typed_user_retention.py

### qubit_runtime/tests/agent/test_run_agent_codex_responses.py  (4 missing)
  - test_run_codex_stream_owner_close_does_not_retry_raw_when_managed_close_raises  <- tests/agent/test_run_agent_codex_responses.py
  - test_run_codex_stream_post_terminal_clean_drain_never_shutdowns  <- tests/agent/test_run_agent_codex_responses.py
  - test_run_codex_stream_post_terminal_timeout_keeps_close_on_reader_thread  <- tests/agent/test_run_agent_codex_responses.py
  - test_run_codex_stream_skips_post_terminal_drain_without_socket  <- tests/agent/test_run_agent_codex_responses.py

### qubit_runtime/tests/agent/test_verification_evidence.py  (4 missing)
  - test_failed_verify_does_not_erase_other_session_edit_record  <- tests/agent/test_verification_evidence.py
  - test_older_workspace_verify_does_not_clear_later_edit  <- tests/agent/test_verification_evidence.py
  - test_targeted_partial_verify_does_not_clear_other_session  <- tests/agent/test_verification_evidence.py
  - test_workspace_verify_clears_stale_on_other_session  <- tests/agent/test_verification_evidence.py

### qubit_runtime/tests/cron/test_external_worker_config_hooks.py  (4 missing)
  - HOOK_CMD  <- tests/cron/test_external_worker_config_hooks.py
  - OUTBOUND_URL  <- tests/cron/test_external_worker_config_hooks.py
  - homes  <- tests/cron/test_external_worker_config_hooks.py
  - test_worker_registers_owning_profile_config_hooks  <- tests/cron/test_external_worker_config_hooks.py

### qubit_runtime/tests/gateway/test_discord_username_allowlist_env_reload.py  (4 missing)
  - PAIRED_ID  <- tests/gateway/test_discord_username_allowlist_env_reload.py
  - TestRemovedNumericEntryIsNotResurrected  <- tests/gateway/test_discord_username_allowlist_env_reload.py
  - _member  <- tests/gateway/test_discord_username_allowlist_env_reload.py
  - _resolved_discord_adapter  <- tests/gateway/test_discord_username_allowlist_env_reload.py

### qubit_runtime/tests/gateway/test_skill_slash_native_boundary.py  (4 missing)
  - _make_local_skill  <- tests/gateway/test_skill_slash_native_boundary.py
  - _make_plugin_skill  <- tests/gateway/test_skill_slash_native_boundary.py
  - _rewrite  <- tests/gateway/test_skill_slash_native_boundary.py
  - test_messaging_stacked_skills_never_load_plugin_skill_bodies  <- tests/gateway/test_skill_slash_native_boundary.py

### qubit_runtime/tests/gateway/test_whatsapp_multiplex.py  (4 missing)
  - _hold_port  <- tests/gateway/test_whatsapp_multiplex.py
  - test_allocation_skips_ports_recorded_by_siblings  <- tests/gateway/test_whatsapp_multiplex.py
  - test_credentials_follow_profile_home  <- tests/gateway/test_whatsapp_multiplex.py
  - test_ownership_verdicts  <- tests/gateway/test_whatsapp_multiplex.py

### qubit_runtime/tests/hermes_cli/test_external_process_cli_prefixes.py  (4 missing)
  - _install  <- tests/hermes_cli/test_external_process_cli_prefixes.py
  - service_launch  <- tests/hermes_cli/test_external_process_cli_prefixes.py
  - test_external_process_provider_finds_claude_in_an_install_prefix  <- tests/hermes_cli/test_external_process_cli_prefixes.py
  - test_setup_token_runs_claude_from_an_install_prefix  <- tests/hermes_cli/test_external_process_cli_prefixes.py

### qubit_runtime/tests/hermes_cli/test_kanban_gc_retention.py  (4 missing)
  - _deferred_reasons  <- tests/hermes_cli/test_kanban_gc_retention.py
  - _shared_scratch  <- tests/hermes_cli/test_kanban_gc_retention.py
  - test_cleanup_workspace_keeps_scratch_when_other_boards_cannot_be_listed  <- tests/hermes_cli/test_kanban_gc_retention.py
  - test_cmd_gc_keeps_a_scratch_dir_a_live_task_still_uses  <- tests/hermes_cli/test_kanban_gc_retention.py

### qubit_runtime/tests/hermes_cli/test_local_runtime_recovery.py  (4 missing)
  - test_a_stepped_clock_never_buries_a_live_owner_of_an_older_record  <- tests/hermes_cli/test_local_runtime_recovery.py
  - test_clock_step_keeps_the_recorded_router  <- tests/hermes_cli/test_local_runtime_recovery.py
  - test_replacement_stops_only_an_orphaned_router  <- tests/hermes_cli/test_local_runtime_recovery.py
  - test_the_process_that_boots_the_server_stops_it_on_a_clean_exit  <- tests/hermes_cli/test_local_runtime_recovery.py

### qubit_runtime/tests/hermes_cli/test_sessions_export_retention.py  (4 missing)
  - _run  <- tests/hermes_cli/test_sessions_export_retention.py
  - store  <- tests/hermes_cli/test_sessions_export_retention.py
  - test_explicit_missing_export_fails_without_creating_an_output  <- tests/hermes_cli/test_sessions_export_retention.py
  - test_filtered_backup_covers_protected_sessions_before_retention_prune  <- tests/hermes_cli/test_sessions_export_retention.py

### qubit_runtime/tests/hermes_state/test_display_dedupe_pruned_tool_payload.py  (4 missing)
  - _seed_session  <- tests/hermes_state/test_display_dedupe_pruned_tool_payload.py
  - db  <- tests/hermes_state/test_display_dedupe_pruned_tool_payload.py
  - test_distinct_idless_assistant_calls_are_not_merged  <- tests/hermes_state/test_display_dedupe_pruned_tool_payload.py
  - test_pruned_tool_payload_keeps_one_display_identity  <- tests/hermes_state/test_display_dedupe_pruned_tool_payload.py

### qubit_runtime/tests/hermes_state/test_lease_pid_namespace.py  (4 missing)
  - _dead_pid  <- tests/hermes_state/test_lease_pid_namespace.py
  - _pinned_namespace  <- tests/hermes_state/test_lease_pid_namespace.py
  - test_flock_holder_record_qualifies_pid_namespaces  <- tests/hermes_state/test_lease_pid_namespace.py
  - test_turn_lease_of_sibling_namespace_is_not_stolen  <- tests/hermes_state/test_lease_pid_namespace.py

### qubit_runtime/tests/providers/test_named_custom_reasoning.py  (4 missing)
  - test_bare_named_custom_memo_caches_the_helper_result  <- tests/providers/test_named_custom_reasoning.py
  - test_bare_named_custom_memo_invalidates_on_config_change  <- tests/providers/test_named_custom_reasoning.py
  - test_bare_named_custom_memo_is_keyed_by_home  <- tests/providers/test_named_custom_reasoning.py
  - test_bare_named_custom_provider_gets_custom_profile  <- tests/providers/test_named_custom_reasoning.py

### qubit_runtime/tests/scripts/test_check_agents_md_size.py  (4 missing)
  - SCRIPT  <- tests/scripts/test_check_agents_md_size.py
  - _check  <- tests/scripts/test_check_agents_md_size.py
  - _repo  <- tests/scripts/test_check_agents_md_size.py
  - test_budget_is_per_chain_from_the_root_down  <- tests/scripts/test_check_agents_md_size.py

### qubit_runtime/tests/tools/test_browser_process_tree.py  (4 missing)
  - test_posix_shared_group_is_never_killpgd  <- tests/tools/test_browser_process_tree.py
  - test_real_group_leader_child_is_tree_killed  <- tests/tools/test_browser_process_tree.py
  - test_real_shared_group_child_descendants_are_killed  <- tests/tools/test_browser_process_tree.py
  - test_real_shared_group_child_does_not_signal_us  <- tests/tools/test_browser_process_tree.py

### qubit_runtime/tools/mcp_tool_config.py  (4 missing)
  - _first_user_which_hit  <- tools/mcp_tool_config.py
  - _is_hermes_managed_bin_dir  <- tools/mcp_tool_config.py
  - _pathext_suffixes  <- tools/mcp_tool_config.py
  - _tail_server_stderr  <- tools/mcp_tool_config.py

### qubit_runtime/tools/memory_tool_store.py  (4 missing)
  - _no_match_error  <- tools/memory_tool_store.py
  - _normalize_for_match  <- tools/memory_tool_store.py
  - _over_limit  <- tools/memory_tool_store.py
  - _substring_matches  <- tools/memory_tool_store.py

### qubit_runtime/tools/transcription_whisper_cpp.py  (4 missing)
  - _file_signature  <- tools/transcription_whisper_cpp.py
  - _model_dir  <- tools/transcription_whisper_cpp.py
  - ensure_whisper_cpp_models  <- tools/transcription_whisper_cpp.py
  - whisper_cpp_command  <- tools/transcription_whisper_cpp.py

### qubit_runtime/tui_gateway/contracts/config_free_tier_control.py  (4 missing)
  - ProviderLimit  <- tui_gateway/contracts/config_free_tier_control.py
  - ProviderUsage  <- tui_gateway/contracts/config_free_tier_control.py
  - ProviderUsageAccount  <- tui_gateway/contracts/config_free_tier_control.py
  - ProviderUsageWindow  <- tui_gateway/contracts/config_free_tier_control.py

### qubit_runtime/tui_gateway/slash_worker.py  (4 missing)
  - SkillSlashRefused  <- tui_gateway/slash_worker.py
  - _refuse_skill_slash  <- tui_gateway/slash_worker.py
  - _slash_base  <- tui_gateway/slash_worker.py
  - _watchdog_parent  <- tui_gateway/slash_worker.py

### qubit_runtime/agent/account_usage.py  (3 missing)
  - _codex_snapshot_identity  <- agent/account_usage.py
  - _fetch_codex_account_usage_impl  <- agent/account_usage.py
  - _fetch_codex_account_usage_read_only  <- agent/account_usage.py

### qubit_runtime/agent/native_compaction.py  (3 missing)
  - _head_within_budget  <- agent/native_compaction.py
  - _input_text_parts_cost  <- agent/native_compaction.py
  - _truncate_input_text_parts  <- agent/native_compaction.py

### qubit_runtime/qubit_cli/cli_info_mixin.py  (3 missing)
  - _help_section_title  <- hermes_cli/cli_info_mixin.py
  - _reload_mcp_choices  <- hermes_cli/cli_info_mixin.py
  - _tool_progress_label  <- hermes_cli/cli_info_mixin.py

### qubit_runtime/qubit_cli/cli_tui_mixin.py  (3 missing)
  - _agent_name  <- hermes_cli/cli_tui_mixin.py
  - _approval_choice_label  <- hermes_cli/cli_tui_mixin.py
  - _tn  <- hermes_cli/cli_tui_mixin.py

### qubit_runtime/qubit_cli/dashboard_auth/body_limit.py  (3 missing)
  - AUTH_BODY_LIMIT  <- hermes_cli/dashboard_auth/body_limit.py
  - AuthBodyLimitMiddleware  <- hermes_cli/dashboard_auth/body_limit.py
  - _AuthBodyTooLarge  <- hermes_cli/dashboard_auth/body_limit.py

### qubit_runtime/qubit_cli/dashboard_procs.py  (3 missing)
  - BACKEND_LOCK_NAME  <- hermes_cli/dashboard_procs.py
  - _ledger_serve_binds  <- hermes_cli/dashboard_procs.py
  - read_valid_backend_lock  <- hermes_cli/dashboard_procs.py

### qubit_runtime/qubit_cli/process_identity.py  (3 missing)
  - _reparented_orphan  <- hermes_cli/process_identity.py
  - _terminate_then_kill  <- hermes_cli/process_identity.py
  - reap_orphaned_backend_owner  <- hermes_cli/process_identity.py

### qubit_runtime/qubit_cli/session_export.py  (3 missing)
  - drop_undone_rows  <- hermes_cli/session_export.py
  - export_projection  <- hermes_cli/session_export.py
  - load_save_snapshot  <- hermes_cli/session_export.py

### qubit_runtime/qubit_cli/update_cmd_windows.py  (3 missing)
  - _owned_gateway_pids  <- hermes_cli/update_cmd_windows.py
  - _pending_relaunch_pids  <- hermes_cli/update_cmd_windows.py
  - _relaunch_verify_timeout_s  <- hermes_cli/update_cmd_windows.py

### qubit_runtime/qubit_cli/web_server_owner_exit.py  (3 missing)
  - DEFAULT_OWNER_POLL_S  <- hermes_cli/web_server_owner_exit.py
  - should_retire_superseded  <- hermes_cli/web_server_owner_exit.py
  - start_owner_watchdog  <- hermes_cli/web_server_owner_exit.py

### qubit_runtime/plugins/platforms/discord/adapter_thread_titles.py  (3 missing)
  - DiscordThreadTitlesMixin  <- plugins/platforms/discord/adapter_thread_titles.py
  - SemanticThreadRenames  <- plugins/platforms/discord/adapter_thread_titles.py
  - _TitleRename  <- plugins/platforms/discord/adapter_thread_titles.py

### qubit_runtime/plugins/platforms/matrix/adapter.py  (3 missing)
  - _content_bytes  <- plugins/platforms/matrix/adapter.py
  - _standalone_payloads  <- plugins/platforms/matrix/adapter.py
  - _utf8_len  <- plugins/platforms/matrix/adapter.py

### qubit_runtime/plugins/platforms/telegram/update_admission.py  (3 missing)
  - DEFAULT_MAX_CONCURRENT_UPDATES  <- plugins/platforms/telegram/update_admission.py
  - PerChatUpdateProcessor  <- plugins/platforms/telegram/update_admission.py
  - build_update_processor  <- plugins/platforms/telegram/update_admission.py

### qubit_runtime/pm/environments.py  (3 missing)
  - _launcher_bound_root  <- pm/environments.py
  - _unstamped_store  <- pm/environments.py
  - owning_home_root  <- pm/environments.py

### qubit_runtime/pm/package.py  (3 missing)
  - _untar_deb_payload  <- pm/package.py
  - deb_data_tar  <- pm/package.py
  - unpack_deb  <- pm/package.py

### qubit_runtime/tests/agent/test_compression_adoption_preserves_live_tail.py  (3 missing)
  - _keep_protected_tail  <- tests/agent/test_compression_adoption_preserves_live_tail.py
  - test_adopted_snapshot_rows_are_not_recloned_as_foreign_tail  <- tests/agent/test_compression_adoption_preserves_live_tail.py
  - test_row_committed_after_adopted_snapshot_read_is_cloned_once  <- tests/agent/test_compression_adoption_preserves_live_tail.py

### qubit_runtime/tests/agent/test_context_compressor.py  (3 missing)
  - TestSummarizeToolResultOutcome  <- tests/agent/test_context_compressor.py
  - TestSummarizeToolResultRefusals  <- tests/agent/test_context_compressor.py
  - _refusals  <- tests/agent/test_context_compressor.py

### qubit_runtime/tests/agent/test_empty_response_recovery_persistence.py  (3 missing)
  - _call  <- tests/agent/test_empty_response_recovery_persistence.py
  - test_housekeeping_fallback_names_its_final_a_reused_response  <- tests/agent/test_empty_response_recovery_persistence.py
  - test_same_words_in_a_second_response_are_not_a_reused_response  <- tests/agent/test_empty_response_recovery_persistence.py

### qubit_runtime/tests/agent/test_in_place_merged_user_rows.py  (3 missing)
  - KILLED_TURN_LEFT  <- tests/agent/test_in_place_merged_user_rows.py
  - _call  <- tests/agent/test_in_place_merged_user_rows.py
  - test_compaction_over_an_unanswered_prompt_keeps_one_copy_of_every_row  <- tests/agent/test_in_place_merged_user_rows.py

### qubit_runtime/tests/agent/test_intent_ack_continuation.py  (3 missing)
  - test_codex_only_path_accepts_pronounless_workspace_announcement  <- tests/agent/test_intent_ack_continuation.py
  - test_pronounless_action_announcements_continue_when_opted_in  <- tests/agent/test_intent_ack_continuation.py
  - test_pronounless_action_guardrails_reject_questions_and_finals  <- tests/agent/test_intent_ack_continuation.py

### qubit_runtime/tests/agent/test_kimi_coding_openai_base_url.py  (3 missing)
  - _fake_openai_factory  <- tests/agent/test_kimi_coding_openai_base_url.py
  - test_create_openai_client_normalizes_kimi_coding_base_url  <- tests/agent/test_kimi_coding_openai_base_url.py
  - test_create_openai_client_preserves_already_suffixed_kimi_url  <- tests/agent/test_kimi_coding_openai_base_url.py

### qubit_runtime/tests/agent/test_message_sequence_repair.py  (3 missing)
  - test_repair_decode_of_durable_sentinel_row_does_not_reappend  <- tests/agent/test_message_sequence_repair.py
  - test_repair_decodes_sentinel_multimodal_and_skips_text_merge  <- tests/agent/test_message_sequence_repair.py
  - test_repair_records_dropped_tool_row_on_survivor  <- tests/agent/test_message_sequence_repair.py

### qubit_runtime/tests/agent/test_reasoning_only_stop_persistence.py  (3 missing)
  - SUMMARY  <- tests/agent/test_reasoning_only_stop_persistence.py
  - test_anthropic_signed_thinking_only_stop_continues_instead_of_promoting  <- tests/agent/test_reasoning_only_stop_persistence.py
  - test_native_claude_carrier_thinking_only_stop_continues_instead_of_promoting  <- tests/agent/test_reasoning_only_stop_persistence.py

### qubit_runtime/tests/gateway/test_discord_auto_thread_rename_pin.py  (3 missing)
  - _Thread  <- tests/gateway/test_discord_auto_thread_rename_pin.py
  - conversation  <- tests/gateway/test_discord_auto_thread_rename_pin.py
  - test_an_unfinished_attempt_keeps_a_newer_attempts_record  <- tests/gateway/test_discord_auto_thread_rename_pin.py

### qubit_runtime/tests/hermes_cli/test_cli_clarify_panel_height.py  (3 missing)
  - TestClarifyBatchPanelHeight  <- tests/hermes_cli/test_cli_clarify_panel_height.py
  - _make_cli  <- tests/hermes_cli/test_cli_clarify_panel_height.py
  - _rendered  <- tests/hermes_cli/test_cli_clarify_panel_height.py

### qubit_runtime/tests/hermes_cli/test_doctor_no_proxy_ipv6.py  (3 missing)
  - clash_env  <- tests/hermes_cli/test_doctor_no_proxy_ipv6.py
  - test_run_probes_leaves_a_clean_environment_untouched  <- tests/hermes_cli/test_doctor_no_proxy_ipv6.py
  - test_run_probes_sanitizes_env_before_any_worker_builds_a_client  <- tests/hermes_cli/test_doctor_no_proxy_ipv6.py

### qubit_runtime/tests/hermes_cli/test_goal_dispatch.py  (3 missing)
  - _dispatch  <- tests/hermes_cli/test_goal_dispatch.py
  - test_control_verb_with_trailing_words_never_replaces_the_goal  <- tests/hermes_cli/test_goal_dispatch.py
  - test_double_dash_sets_control_word_goal_and_announces_the_replace  <- tests/hermes_cli/test_goal_dispatch.py

### qubit_runtime/tests/hermes_cli/test_kanban_goal_mode.py  (3 missing)
  - test_loop_stops_on_judge_transport_failure  <- tests/hermes_cli/test_kanban_goal_mode.py
  - test_loop_stops_on_worker_failed_flag  <- tests/hermes_cli/test_kanban_goal_mode.py
  - test_loop_stops_on_worker_failed_flag_default_reason  <- tests/hermes_cli/test_kanban_goal_mode.py

### qubit_runtime/tests/hermes_cli/test_model_flow_local.py  (3 missing)
  - _picker_keys  <- tests/hermes_cli/test_model_flow_local.py
  - test_local_models_row_follows_the_engine_pin_and_exclusions  <- tests/hermes_cli/test_model_flow_local.py
  - test_picked_model_runs_the_quickstart_job_and_reports_how_it_ended  <- tests/hermes_cli/test_model_flow_local.py

### qubit_runtime/tests/hermes_cli/test_nous_picker_cached_only_key.py  (3 missing)
  - _serve_nous_catalog  <- tests/hermes_cli/test_nous_picker_cached_only_key.py
  - test_anonymous_reads_still_resolve  <- tests/hermes_cli/test_nous_picker_cached_only_key.py
  - test_picker_on_sale_union_reads_the_authenticated_prewarm  <- tests/hermes_cli/test_nous_picker_cached_only_key.py

### qubit_runtime/tests/hermes_cli/test_plugin_validate.py  (3 missing)
  - test_core_override_through_a_method_patch_helper  <- tests/hermes_cli/test_plugin_validate.py
  - test_install_deps_probe_imports_from_the_synced_environment  <- tests/hermes_cli/test_plugin_validate.py
  - test_runtime_rebind_of_hermes_core_fails_admission  <- tests/hermes_cli/test_plugin_validate.py

### qubit_runtime/tests/hermes_cli/test_relay_shared_metrics_runtime.py  (3 missing)
  - _cli_turn  <- tests/hermes_cli/test_relay_shared_metrics_runtime.py
  - test_finite_cli_runs_report_their_own_entrypoint  <- tests/hermes_cli/test_relay_shared_metrics_runtime.py
  - test_oneshot_hard_exit_cleanup_records_the_session_summary  <- tests/hermes_cli/test_relay_shared_metrics_runtime.py

### qubit_runtime/tests/hermes_cli/test_runtime_provider_resolution.py  (3 missing)
  - test_openai_env_file_key_used_as_fallback_without_openrouter_key  <- tests/hermes_cli/test_runtime_provider_resolution.py
  - test_openrouter_env_file_key_survives_exhausted_pool  <- tests/hermes_cli/test_runtime_provider_resolution.py
  - test_openrouter_raw_op_ref_in_dotenv_loses_to_resolved_env  <- tests/hermes_cli/test_runtime_provider_resolution.py

### qubit_runtime/tests/hermes_cli/test_show_model_picker_exhausted_pool.py  (3 missing)
  - _run_picker  <- tests/hermes_cli/test_show_model_picker_exhausted_pool.py
  - test_show_model_picker_requests_for_picker  <- tests/hermes_cli/test_show_model_picker_exhausted_pool.py
  - test_show_model_picker_usage_fallback_still_works  <- tests/hermes_cli/test_show_model_picker_exhausted_pool.py

### qubit_runtime/tests/hermes_cli/test_snapshot_restore_oauth.py  (3 missing)
  - _oauth_row  <- tests/hermes_cli/test_snapshot_restore_oauth.py
  - test_auth_refusal_is_not_hidden_by_another_restored_file  <- tests/hermes_cli/test_snapshot_restore_oauth.py
  - test_quick_snapshot_restore_keeps_live_oauth_and_restores_static_auth  <- tests/hermes_cli/test_snapshot_restore_oauth.py

### qubit_runtime/tests/hermes_state/test_pinned_lineage.py  (3 missing)
  - _pinned_then_rotated  <- tests/hermes_state/test_pinned_lineage.py
  - test_a_pin_follows_the_chat_through_compression_and_bulk_cleanup_spares_it  <- tests/hermes_state/test_pinned_lineage.py
  - test_orphan_sweep_spares_the_open_tip_of_a_chat_pinned_before_it_rotated  <- tests/hermes_state/test_pinned_lineage.py

### qubit_runtime/tests/hermes_state/test_session_import_keep_flags.py  (3 missing)
  - SESSION_ID  <- tests/hermes_state/test_session_import_keep_flags.py
  - test_adopted_bot_chat_stays_the_hidden_canonical_chat  <- tests/hermes_state/test_session_import_keep_flags.py
  - test_restored_sessions_keep_the_pin_and_the_sweeps_archive_provenance  <- tests/hermes_state/test_session_import_keep_flags.py

### qubit_runtime/tests/plugins/platforms/matrix/test_event_size.py  (3 missing)
  - _fits_one_event  <- tests/plugins/platforms/matrix/test_event_size.py
  - _make_adapter  <- tests/plugins/platforms/matrix/test_event_size.py
  - _texts  <- tests/plugins/platforms/matrix/test_event_size.py

### qubit_runtime/tests/scripts/install/test_install_clone_throttle_fallback.py  (3 missing)
  - POWERSHELL  <- tests/scripts/install/test_install_clone_throttle_fallback.py
  - _run_repository_stage  <- tests/scripts/install/test_install_clone_throttle_fallback.py
  - test_existing_treeless_checkout_is_refetched_before_update  <- tests/scripts/install/test_install_clone_throttle_fallback.py

### qubit_runtime/tests/tools/test_protected_instruction_cli_channel.py  (3 missing)
  - _gate_on  <- tests/tools/test_protected_instruction_cli_channel.py
  - test_registered_callback_is_never_asked_without_a_user  <- tests/tools/test_protected_instruction_cli_channel.py
  - test_single_query_mode_never_auto_approves_this_gate  <- tests/tools/test_protected_instruction_cli_channel.py

### qubit_runtime/tests/tools/test_schema_sanitizer.py  (3 missing)
  - _walk_schema  <- tests/tools/test_schema_sanitizer.py
  - test_clarify_schema_has_no_unbounded_repetition_keyword  <- tests/tools/test_schema_sanitizer.py
  - test_registered_tool_schemas_stay_inside_llama_cpp_repetition_limit  <- tests/tools/test_schema_sanitizer.py

### qubit_runtime/tests/tools/test_whatsapp_send_message_media.py  (3 missing)
  - test_secondary_standalone_sends_use_active_profile_port_for_text_media_and_mentions  <- tests/tools/test_whatsapp_send_message_media.py
  - test_standalone_send_posts_only_through_this_profiles_bridge  <- tests/tools/test_whatsapp_send_message_media.py
  - test_standalone_send_uses_persisted_secondary_bridge_port  <- tests/tools/test_whatsapp_send_message_media.py

### qubit_runtime/tests/tui_gateway/test_manual_compress_history_fence.py  (3 missing)
  - _agent_with_history  <- tests/tui_gateway/test_manual_compress_history_fence.py
  - test_manual_compress_rejects_history_rewritten_before_admission  <- tests/tui_gateway/test_manual_compress_history_fence.py
  - test_manual_compress_releases_lease_when_snapshot_check_raises  <- tests/tui_gateway/test_manual_compress_history_fence.py

### qubit_runtime/tools/browser_supervisor_capture.py  (3 missing)
  - CapturedCDP  <- tools/browser_supervisor_capture.py
  - CapturedCDPInvalid  <- tools/browser_supervisor_capture.py
  - capture  <- tools/browser_supervisor_capture.py

### qubit_runtime/tools/cronjob_job_args.py  (3 missing)
  - _base_url_refused  <- tools/cronjob_job_args.py
  - _custom_stored_key_error  <- tools/cronjob_job_args.py
  - _same_origin  <- tools/cronjob_job_args.py

### qubit_runtime/tools/skills_tool.py  (3 missing)
  - _owned_relative  <- tools/skills_tool.py
  - _skill_catalog  <- tools/skills_tool.py
  - clear_skills_cache  <- tools/skills_tool.py

### qubit_runtime/tui_gateway/methods_config.py  (3 missing)
  - _readiness_cleared  <- tui_gateway/methods_config.py
  - _readiness_share  <- tui_gateway/methods_config.py
  - _redact_client_text  <- tui_gateway/methods_config.py

### qubit_runtime/tui_gateway/session_workdir.py  (3 missing)
  - _is_hermes_owned_cwd  <- tui_gateway/session_workdir.py
  - _is_remote_launch_cwd  <- tui_gateway/session_workdir.py
  - _resumable_stored_cwd  <- tui_gateway/session_workdir.py

### qubit_runtime/agent/auxiliary_task_config.py  (2 missing)
  - _get_auxiliary_task_config  <- agent/auxiliary_task_config.py
  - _layer_over_inherited  <- agent/auxiliary_task_config.py

### qubit_runtime/agent/codex_runtime.py  (2 missing)
  - _codex_turn_effort  <- agent/codex_runtime.py
  - _codex_turn_service_tier  <- agent/codex_runtime.py

### qubit_runtime/agent/conversation_compression_telemetry.py  (2 missing)
  - _emit_aborted_attempt_telemetry  <- agent/conversation_compression_telemetry.py
  - _emit_compression_attempt_telemetry  <- agent/conversation_compression_telemetry.py

### qubit_runtime/agent/fast_mode.py  (2 missing)
  - CODEX_TIER_WORDS  <- agent/fast_mode.py
  - parse_exact_service_tier  <- agent/fast_mode.py

### qubit_runtime/agent/prompt_builder.py  (2 missing)
  - _omitted_headings  <- agent/prompt_builder.py
  - _scan_extra_root  <- agent/prompt_builder.py

### qubit_runtime/evals/core_tool_deferral/sandbox.py  (2 missing)
  - CRONTAB_FILE_ENV  <- evals/core_tool_deferral/sandbox.py
  - isolate_host  <- evals/core_tool_deferral/sandbox.py

### qubit_runtime/evals/gateway_failure_ownership/probe.py  (2 missing)
  - _external  <- evals/gateway_failure_ownership/probe.py
  - local_connect_ex  <- evals/gateway_failure_ownership/probe.py

### qubit_runtime/gateway/run_inbound_consumer.py  (2 missing)
  - POST_ADMISSION_HOOK  <- gateway/run_inbound_consumer.py
  - _routed_scope  <- gateway/run_inbound_consumer.py

### qubit_runtime/gateway/run_startup.py  (2 missing)
  - recover_left_core_at_gateway_start  <- gateway/run_startup.py
  - recover_left_core_in  <- gateway/run_startup.py

### qubit_runtime/qubit_cli/auth_error_copy.py  (2 missing)
  - DeviceCodeExpired  <- hermes_cli/auth_error_copy.py
  - is_device_code_expired  <- hermes_cli/auth_error_copy.py

### qubit_runtime/qubit_cli/backup_restore.py  (2 missing)
  - _auth_restore_target  <- hermes_cli/backup_restore.py
  - _restore_auth_json  <- hermes_cli/backup_restore.py

### qubit_runtime/qubit_cli/config.py  (2 missing)
  - _env_write_lock  <- hermes_cli/config.py
  - _env_write_lock_holder  <- hermes_cli/config.py

### qubit_runtime/qubit_cli/config_migrations.py  (2 missing)
  - _migrate_to_48  <- hermes_cli/config_migrations.py
  - _migrate_to_49  <- hermes_cli/config_migrations.py

### qubit_runtime/qubit_cli/local_runtime/bootstrap.py  (2 missing)
  - _stop_at_exit  <- hermes_cli/local_runtime/bootstrap.py
  - serving_engine  <- hermes_cli/local_runtime/bootstrap.py

### qubit_runtime/qubit_cli/local_runtime/hardware.py  (2 missing)
  - _accelerator_device  <- hermes_cli/local_runtime/hardware.py
  - _configured_engine  <- hermes_cli/local_runtime/hardware.py

### qubit_runtime/qubit_cli/main.py  (2 missing)
  - _require_dashboard_web_deps  <- hermes_cli/main.py
  - explicit_cli_profile  <- hermes_cli/main.py

### qubit_runtime/qubit_cli/main_dashboard.py  (2 missing)
  - _respawnable_command_for_current_install  <- hermes_cli/main_dashboard.py
  - _unit_main_pid_is  <- hermes_cli/main_dashboard.py

### qubit_runtime/qubit_cli/models.py  (2 missing)
  - _is_anthropic_frontier_tier  <- hermes_cli/models.py
  - union_with_nous_on_sale_models  <- hermes_cli/models.py

### qubit_runtime/qubit_cli/plugin_validate_desktop.py  (2 missing)
  - _desktop_surface_files  <- hermes_cli/plugin_validate_desktop.py
  - _has_root_entry  <- hermes_cli/plugin_validate_desktop.py

### qubit_runtime/qubit_cli/plugins_content.py  (2 missing)
  - register_automation_blueprint  <- hermes_cli/plugins_content.py
  - register_skill  <- hermes_cli/plugins_content.py

### qubit_runtime/qubit_cli/profile_channels.py  (2 missing)
  - _left_core_platforms  <- hermes_cli/profile_channels.py
  - _shared_with_tools  <- hermes_cli/profile_channels.py

### qubit_runtime/qubit_cli/profile_distribution.py  (2 missing)
  - _is_installer_owned_path  <- hermes_cli/profile_distribution.py
  - _refuse_store_ancestor_replacement  <- hermes_cli/profile_distribution.py

### qubit_runtime/qubit_cli/web_routers/cron.py  (2 missing)
  - _blueprint_catalog_sync  <- hermes_cli/web_routers/cron.py
  - _blueprint_for_profile  <- hermes_cli/web_routers/cron.py

### qubit_runtime/qubit_cli/web_server.py  (2 missing)
  - _gateway_owns_cron  <- hermes_cli/web_server.py
  - _reclaim_host_from_orphaned_owner  <- hermes_cli/web_server.py

### qubit_runtime/qubit_cli/web_server_config.py  (2 missing)
  - _aux_task_slots  <- hermes_cli/web_server_config.py
  - _plugin_aux_tasks  <- hermes_cli/web_server_config.py

### qubit_runtime/qubit_cli/web_server_dashboard.py  (2 missing)
  - _hosted_plugin_for_request  <- hermes_cli/web_server_dashboard.py
  - _mount_hosted_plugin_api  <- hermes_cli/web_server_dashboard.py

### qubit_runtime/qubit_platform/declaration.py  (2 missing)
  - GPU_LABELS  <- hermes_platform/declaration.py
  - gpu_label  <- hermes_platform/declaration.py

### qubit_runtime/qubit_state_ids.py  (2 missing)
  - SESSION_ID_RECOGNIZERS  <- hermes_state_ids.py
  - is_known_session_id  <- hermes_state_ids.py

### qubit_runtime/plugins/computer_use/cua/__init__.py  (2 missing)
  - CuaDriverProvider  <- plugins/computer_use/cua/__init__.py
  - register  <- plugins/computer_use/cua/__init__.py

### qubit_runtime/pm/filesystem.py  (2 missing)
  - remove_tree  <- pm/filesystem.py
  - retry_held  <- pm/filesystem.py

### qubit_runtime/tests/agent/lsp/test_workspace_trust.py  (2 missing)
  - _hermes_side_tree  <- tests/agent/lsp/test_workspace_trust.py
  - test_untrusted_checkout_starts_only_allowlisted_servers_pinned_to_hermes_code  <- tests/agent/lsp/test_workspace_trust.py

### qubit_runtime/tests/agent/test_compression_attempt_telemetry.py  (2 missing)
  - test_generic_caller_verdicts_defer_to_the_recorded_failure_class  <- tests/agent/test_compression_attempt_telemetry.py
  - test_structural_no_op_is_counted_skipped_with_the_compressors_class  <- tests/agent/test_compression_attempt_telemetry.py

### qubit_runtime/tests/agent/test_run_budget.py  (2 missing)
  - test_cloud_stream_budget_preserves_explicit_deadlines  <- tests/agent/test_run_budget.py
  - test_local_stream_patience_is_independent_of_run_budget  <- tests/agent/test_run_budget.py

### qubit_runtime/tests/cron/test_jobs_shrink_merge.py  (2 missing)
  - test_save_over_corrupt_store_fails_closed  <- tests/cron/test_jobs_shrink_merge.py
  - test_symlinked_store_saves_by_rename_not_in_place_copy  <- tests/cron/test_jobs_shrink_merge.py

### qubit_runtime/tests/cron/test_registration_ownership.py  (2 missing)
  - test_dispatch_cleanup_preserves_successor  <- tests/cron/test_registration_ownership.py
  - test_live_direct_run_survives_stale_sweep  <- tests/cron/test_registration_ownership.py

### qubit_runtime/tests/desktop/test_appinstaller_check_script.py  (2 missing)
  - MODULE  <- tests/desktop/test_appinstaller_check_script.py
  - REPO  <- tests/desktop/test_appinstaller_check_script.py

### qubit_runtime/tests/e2e/core/upgrade/pm/test_stamps_and_doctor.py  (2 missing)
  - test_managed_env_hermes_can_check_for_updates  <- tests/e2e/core/upgrade/pm/test_stamps_and_doctor.py
  - test_managed_env_hermes_reports_the_checkout_as_the_install  <- tests/e2e/core/upgrade/pm/test_stamps_and_doctor.py

### qubit_runtime/tests/gateway/test_discord_auto_thread_prompt_parity.py  (2 missing)
  - _Thread  <- tests/gateway/test_discord_auto_thread_prompt_parity.py
  - _message  <- tests/gateway/test_discord_auto_thread_prompt_parity.py

### qubit_runtime/tests/gateway/test_discord_plugin_setup.py  (2 missing)
  - TestDiscordTokenCheckedWithDiscord  <- tests/gateway/test_discord_plugin_setup.py
  - _offline  <- tests/gateway/test_discord_plugin_setup.py

### qubit_runtime/tests/gateway/test_housekeeping_profile_scope.py  (2 missing)
  - test_multiplexed_credential_chores_run_once_per_profile_in_its_own_scope  <- tests/gateway/test_housekeeping_profile_scope.py
  - test_single_profile_credential_chores_run_once_against_the_process_home  <- tests/gateway/test_housekeeping_profile_scope.py

### qubit_runtime/tests/gateway/test_matrix_encrypted_drop_warning.py  (2 missing)
  - TestMatrixEncryptedDropWarning  <- tests/gateway/test_matrix_encrypted_drop_warning.py
  - _make_adapter  <- tests/gateway/test_matrix_encrypted_drop_warning.py

### qubit_runtime/tests/hermes_cli/test_anon_surfaces.py  (2 missing)
  - test_anonymous_tier_matches_case_and_whitespace_insensitively  <- tests/hermes_cli/test_anon_surfaces.py
  - test_guest_whose_lookup_fails_still_gets_the_sign_in_copy  <- tests/hermes_cli/test_anon_surfaces.py

### qubit_runtime/tests/hermes_cli/test_auth_codex_no_proxy_ipv6.py  (2 missing)
  - clash_env  <- tests/hermes_cli/test_auth_codex_no_proxy_ipv6.py
  - test_codex_http_client_constructs_under_clash_ipv6_no_proxy  <- tests/hermes_cli/test_auth_codex_no_proxy_ipv6.py

### qubit_runtime/tests/hermes_cli/test_gitlock.py  (2 missing)
  - test_a_checkout_the_first_cut_configured_folds_again  <- tests/hermes_cli/test_gitlock.py
  - test_gits_own_gc_does_not_lazy_fetch_the_trees_of_commits_a_bloom_graph_has_not_seen  <- tests/hermes_cli/test_gitlock.py

### qubit_runtime/tests/hermes_cli/test_local_launch_budget.py  (2 missing)
  - hermes_home  <- tests/hermes_cli/test_local_launch_budget.py
  - test_launch_budget_reads_free_memory_on_a_vulkan_card  <- tests/hermes_cli/test_local_launch_budget.py

### qubit_runtime/tests/hermes_cli/test_local_runtime.py  (2 missing)
  - test_auto_vendor_detection_uses_host_gpu_class  <- tests/hermes_cli/test_local_runtime.py
  - test_boot_prices_the_engine_it_serves  <- tests/hermes_cli/test_local_runtime.py

### qubit_runtime/tests/hermes_cli/test_merge_profile_tree.py  (2 missing)
  - TestMergeProfileTree  <- tests/hermes_cli/test_merge_profile_tree.py
  - _make_project  <- tests/hermes_cli/test_merge_profile_tree.py

### qubit_runtime/tests/hermes_cli/test_model_options_account_usage.py  (2 missing)
  - _snapshot  <- tests/hermes_cli/test_model_options_account_usage.py
  - test_picker_reads_cached_usage_and_refreshes_without_waiting  <- tests/hermes_cli/test_model_options_account_usage.py

### qubit_runtime/tests/hermes_cli/test_plugins_cmd_activation_keys.py  (2 missing)
  - test_status_enables_a_user_model_provider_only_where_provider_discovery_imports_it  <- tests/hermes_cli/test_plugins_cmd_activation_keys.py
  - test_status_matches_the_loader_for_a_user_installed_model_provider  <- tests/hermes_cli/test_plugins_cmd_activation_keys.py

### qubit_runtime/tests/hermes_cli/test_plugins_cmd_catalog.py  (2 missing)
  - test_memory_category_install_activates_via_memory_provider_not_plugins_enable  <- tests/hermes_cli/test_plugins_cmd_catalog.py
  - test_url_install_of_a_memory_provider_dir_gets_the_provider_hint  <- tests/hermes_cli/test_plugins_cmd_catalog.py

### qubit_runtime/tests/hermes_cli/test_profile_clone_channels.py  (2 missing)
  - test_left_core_platform_keeps_channel_ownership_while_its_plugin_is_absent  <- tests/hermes_cli/test_profile_clone_channels.py
  - test_plugin_platform_shared_env_prefixes_follow_the_ownership_rule  <- tests/hermes_cli/test_profile_clone_channels.py

### qubit_runtime/tests/hermes_cli/test_save_transcript_history.py  (2 missing)
  - _tui_save  <- tests/hermes_cli/test_save_transcript_history.py
  - test_save_json_restores_compacted_history_as_archived  <- tests/hermes_cli/test_save_transcript_history.py

### qubit_runtime/tests/hermes_cli/test_skills_failure_exit_code.py  (2 missing)
  - _scan_gate_env  <- tests/hermes_cli/test_skills_failure_exit_code.py
  - test_cli_exit_code_follows_the_action_outcome  <- tests/hermes_cli/test_skills_failure_exit_code.py

### qubit_runtime/tests/hermes_cli/test_tools_config.py  (2 missing)
  - test_get_platform_tools_legacy_hermes_alias_alone  <- tests/hermes_cli/test_tools_config.py
  - test_get_platform_tools_legacy_hermes_alias_expands  <- tests/hermes_cli/test_tools_config.py

### qubit_runtime/tests/hermes_cli/test_tui_resume_flow.py  (2 missing)
  - test_launch_tui_prefers_launch_cwd_over_inherited_hermes_cwd  <- tests/hermes_cli/test_tui_resume_flow.py
  - test_oneshot_closes_its_relay_root_before_agent_teardown  <- tests/hermes_cli/test_tui_resume_flow.py

### qubit_runtime/tests/hermes_cli/test_web_server.py  (2 missing)
  - TestNousRecommendedDefaultCostSafePolicy  <- tests/hermes_cli/test_web_server.py
  - TestOrphanedOwnerReclaim  <- tests/hermes_cli/test_web_server.py

### qubit_runtime/tests/hermes_cli/test_web_server_fs.py  (2 missing)
  - test_fs_media_posix_paths_are_not_interpreted_as_windows_drives  <- tests/hermes_cli/test_web_server_fs.py
  - test_fs_media_shell_paths_deliver_files_and_preserve_access_checks  <- tests/hermes_cli/test_web_server_fs.py

### qubit_runtime/tests/plugins/platforms/telegram/test_notification_kwargs.py  (2 missing)
  - test_clarify_text_fallback_pushes_in_important_mode  <- tests/plugins/platforms/telegram/test_notification_kwargs.py
  - test_notification_kwargs  <- tests/plugins/platforms/telegram/test_notification_kwargs.py

### qubit_runtime/tests/pm/activation_support.py  (2 missing)
  - SETUP_HERMES_PS1  <- tests/pm/activation_support.py
  - SETUP_HERMES_SH  <- tests/pm/activation_support.py

### qubit_runtime/tests/tools/test_approval_plugin_hooks.py  (2 missing)
  - TestClassicCliPromptsFireGatewayTwinHooks  <- tests/tools/test_approval_plugin_hooks.py
  - _capture_hooks  <- tests/tools/test_approval_plugin_hooks.py

### qubit_runtime/tests/tools/test_bot_relay.py  (2 missing)
  - test_delivery_env_single_profile_host_passes_the_process_env_through  <- tests/tools/test_bot_relay.py
  - test_delivery_env_under_multiplex_names_the_pinned_launch_home  <- tests/tools/test_bot_relay.py

### qubit_runtime/tests/tools/test_file_operations.py  (2 missing)
  - TestSearchFilesIncludesDirectories  <- tests/tools/test_file_operations.py
  - _fenced_base64_reply  <- tests/tools/test_file_operations.py

### qubit_runtime/tests/tools/test_spawn_site_child_env.py  (2 missing)
  - test_compute_host_is_hermes_and_keeps_its_full_environment  <- tests/tools/test_spawn_site_child_env.py
  - test_third_party_children_never_see_hermes_credentials  <- tests/tools/test_spawn_site_child_env.py

### qubit_runtime/tests/tools/test_subagent_sudo_prompt.py  (2 missing)
  - test_single_query_never_prompts  <- tests/tools/test_subagent_sudo_prompt.py
  - test_single_query_sudo_failure_gets_the_headless_tip_and_no_reprompt_promise  <- tests/tools/test_subagent_sudo_prompt.py

### qubit_runtime/tests/tools/test_terminal_config_env_sync.py  (2 missing)
  - _cli_env_map  <- tests/tools/test_terminal_config_env_sync.py
  - _gateway_env_map  <- tests/tools/test_terminal_config_env_sync.py

### qubit_runtime/tests/tools/test_tool_result_storage.py  (2 missing)
  - TestMcpEnvelopeSpillover  <- tests/tools/test_tool_result_storage.py
  - TestPageableText  <- tests/tools/test_tool_result_storage.py

### qubit_runtime/tests/tools/test_transcription_whisper_cpp.py  (2 missing)
  - test_local_resolution_is_passive_and_command_override_wins  <- tests/tools/test_transcription_whisper_cpp.py
  - test_models_download_once_per_profile_and_reject_bad_bytes  <- tests/tools/test_transcription_whisper_cpp.py

### qubit_runtime/tests/tools/test_web_managed_routing.py  (2 missing)
  - test_managed_search_without_identity_names_the_gateway  <- tests/tools/test_web_managed_routing.py
  - test_unentitled_managed_search_gets_fast_search_but_no_billed_fallback  <- tests/tools/test_web_managed_routing.py

### qubit_runtime/tests/tui_gateway/test_human_input_hooks_tui.py  (2 missing)
  - PASSWORD  <- tests/tui_gateway/test_human_input_hooks_tui.py
  - test_tui_sudo_request_fires_human_input_hooks  <- tests/tui_gateway/test_human_input_hooks_tui.py

### qubit_runtime/tests/tui_gateway/test_prompt_accept_logging.py  (2 missing)
  - test_turn_settles_before_post_turn_trim  <- tests/tui_gateway/test_prompt_accept_logging.py
  - turn_stubs  <- tests/tui_gateway/test_prompt_accept_logging.py

### qubit_runtime/tests/tui_gateway/test_tui_gateway_event_replay.py  (2 missing)
  - test_each_replay_read_returns_an_independent_event_graph  <- tests/tui_gateway/test_tui_gateway_event_replay.py
  - test_replay_snapshot_is_detached_from_live_event_mutation  <- tests/tui_gateway/test_tui_gateway_event_replay.py

### qubit_runtime/tests/tui_gateway/test_turn_payload_reused_final.py  (2 missing)
  - _payload  <- tests/tui_gateway/test_turn_payload_reused_final.py
  - test_reused_final_is_named_on_the_wire  <- tests/tui_gateway/test_turn_payload_reused_final.py

### qubit_runtime/tools/connectors/__init__.py  (2 missing)
  - __dir__  <- tools/connectors/__init__.py
  - __getattr__  <- tools/connectors/__init__.py

### qubit_runtime/tools/file_operations.py  (2 missing)
  - _json_nonstandard_constant  <- tools/file_operations.py
  - _refuse_introduced_json_constant  <- tools/file_operations.py

### qubit_runtime/tools/send_message_senders.py  (2 missing)
  - _qqbot_extract_file_info  <- tools/send_message_senders.py
  - _qqbot_media_file_type  <- tools/send_message_senders.py

### qubit_runtime/tools/skills_sync_bundled_ops.py  (2 missing)
  - bundled_skill_for_install  <- tools/skills_sync_bundled_ops.py
  - ensure_bundled_skill  <- tools/skills_sync_bundled_ops.py

### qubit_runtime/tui_gateway/event_replay.py  (2 missing)
  - _freeze_params  <- tui_gateway/event_replay.py
  - _thaw_params  <- tui_gateway/event_replay.py

### qubit_runtime/tui_gateway/prompt_turn.py  (2 missing)
  - _post_turn_housekeeping  <- tui_gateway/prompt_turn.py
  - _release_turn_scopes  <- tui_gateway/prompt_turn.py

### qubit_runtime/utils.py  (2 missing)
  - _bare_ipv6_literal  <- utils.py
  - sanitize_no_proxy_entries  <- utils.py

### qubit_runtime/agent/anthropic_adapter.py  (1 missing)
  - find_claude_code_cli  <- agent/anthropic_adapter.py

### qubit_runtime/agent/auxiliary_codex_response.py  (1 missing)
  - _parse_codex_final_response  <- agent/auxiliary_codex_response.py

### qubit_runtime/agent/file_safety.py  (1 missing)
  - HOME_CREDENTIAL_DIRS  <- agent/file_safety.py

### qubit_runtime/agent/redact.py  (1 missing)
  - SECRET_HEADER_NAME_LIST  <- agent/redact.py

### qubit_runtime/agent/verification_evidence.py  (1 missing)
  - _newest_workspace_verify  <- agent/verification_evidence.py

### qubit_runtime/apps/desktop/e2e/update/seed.py  (1 missing)
  - HERMES_E2E_UPDATE_INSTALL_REF  <- apps/desktop/e2e/update/seed.py

### qubit_runtime/cron/blueprint_catalog.py  (1 missing)
  - list_blueprints  <- cron/blueprint_catalog.py

### qubit_runtime/cron/scheduler_worker_env.py  (1 missing)
  - _committed_dependency_site_packages  <- cron/scheduler_worker_env.py

### qubit_runtime/cron/unreachable_retry.py  (1 missing)
  - is_retry_run  <- cron/unreachable_retry.py

### qubit_runtime/gateway/control_socket.py  (1 missing)
  - _windows_pipe_exchange  <- gateway/control_socket.py

### qubit_runtime/gateway/display_config.py  (1 missing)
  - _platform_defaults  <- gateway/display_config.py

### qubit_runtime/gateway/platform_registry.py  (1 missing)
  - core_ships_platform  <- gateway/platform_registry.py

### qubit_runtime/gateway/platforms/helpers.py  (1 missing)
  - hand_over_held_inbound  <- gateway/platforms/helpers.py

### qubit_runtime/gateway/run_adapters.py  (1 missing)
  - _adapter_unavailable_message  <- gateway/run_adapters.py

### qubit_runtime/gateway/run_inbound_media.py  (1 missing)
  - rehome_inbound_media  <- gateway/run_inbound_media.py

### qubit_runtime/gateway/run_plugin_injection.py  (1 missing)
  - GatewayPluginInjectionMixin  <- gateway/run_plugin_injection.py

### qubit_runtime/qubit_cli/_parser.py  (1 missing)
  - _plugin_command_install_hint  <- hermes_cli/_parser.py

### qubit_runtime/qubit_cli/_subprocess_compat.py  (1 missing)
  - no_prompt_git_kwargs  <- hermes_cli/_subprocess_compat.py

### qubit_runtime/qubit_cli/auth_oauth_grants.py  (1 missing)
  - merge_snapshot_auth_preserving_live_single_use_grants  <- hermes_cli/auth_oauth_grants.py

### qubit_runtime/qubit_cli/backup.py  (1 missing)
  - _is_trusted_root_auth_alias  <- hermes_cli/backup.py

### qubit_runtime/qubit_cli/cli_agent_setup_mixin.py  (1 missing)
  - _resume_counts  <- hermes_cli/cli_agent_setup_mixin.py

### qubit_runtime/qubit_cli/config_section_guard.py  (1 missing)
  - _guard_section_overwrite  <- hermes_cli/config_section_guard.py

### qubit_runtime/qubit_cli/dashboard_auth/audit.py  (1 missing)
  - _bounded_value  <- hermes_cli/dashboard_auth/audit.py

### qubit_runtime/qubit_cli/debug.py  (1 missing)
  - _truncate_complete_lines  <- hermes_cli/debug.py

### qubit_runtime/qubit_cli/dump.py  (1 missing)
  - _fallback_debug_projection  <- hermes_cli/dump.py

### qubit_runtime/qubit_cli/gateway.py  (1 missing)
  - _refuse_restart_of_service_managed_gateway  <- hermes_cli/gateway.py

### qubit_runtime/qubit_cli/goal_command.py  (1 missing)
  - _bare_control  <- hermes_cli/goal_command.py

### qubit_runtime/qubit_cli/gui_uninstall.py  (1 missing)
  - desktop_install_record  <- hermes_cli/gui_uninstall.py

### qubit_runtime/qubit_cli/local_runtime/recovery.py  (1 missing)
  - _same_birth  <- hermes_cli/local_runtime/recovery.py

### qubit_runtime/qubit_cli/main_provider_setup.py  (1 missing)
  - _aux_task_cfg_for_display  <- hermes_cli/main_provider_setup.py

### qubit_runtime/qubit_cli/memory_setup.py  (1 missing)
  - _catalog_install_hint  <- hermes_cli/memory_setup.py

### qubit_runtime/qubit_cli/nous_account.py  (1 missing)
  - _normalize_tier  <- hermes_cli/nous_account.py

### qubit_runtime/qubit_cli/observability/shared_metrics_disabled.py  (1 missing)
  - hermes_applied_write  <- hermes_cli/observability/shared_metrics_disabled.py

### qubit_runtime/qubit_cli/observability/shared_metrics_gateway.py  (1 missing)
  - _record_connect  <- hermes_cli/observability/shared_metrics_gateway.py

### qubit_runtime/qubit_cli/observability/shared_metrics_setup.py  (1 missing)
  - note_oauth_failure  <- hermes_cli/observability/shared_metrics_setup.py

### qubit_runtime/qubit_cli/plugin_catalog.py  (1 missing)
  - _catalog_worktree_mtime  <- hermes_cli/plugin_catalog.py

### qubit_runtime/qubit_cli/plugin_validate.py  (1 missing)
  - _check_trusted_inbound  <- hermes_cli/plugin_validate.py

### qubit_runtime/qubit_cli/plugin_validate_core_override.py  (1 missing)
  - _hermes_top_level  <- hermes_cli/plugin_validate_core_override.py

### qubit_runtime/qubit_cli/plugins_aux_tasks.py  (1 missing)
  - build_auxiliary_task_entry  <- hermes_cli/plugins_aux_tasks.py

### qubit_runtime/qubit_cli/plugins_cmd.py  (1 missing)
  - _default_on  <- hermes_cli/plugins_cmd.py

### qubit_runtime/qubit_cli/plugins_injection.py  (1 missing)
  - inject_plugin_message  <- hermes_cli/plugins_injection.py

### qubit_runtime/qubit_cli/pty_bridge.py  (1 missing)
  - _psutil_alive  <- hermes_cli/pty_bridge.py

### qubit_runtime/qubit_cli/setup.py  (1 missing)
  - _record_setup_completed  <- hermes_cli/setup.py

### qubit_runtime/qubit_cli/subcommands/backup.py  (1 missing)
  - _non_negative_keep  <- hermes_cli/subcommands/backup.py

### qubit_runtime/qubit_cli/subcommands/computer_use.py  (1 missing)
  - _exit_if_plugin_backend  <- hermes_cli/subcommands/computer_use.py

### qubit_runtime/qubit_cli/tools_config_providers.py  (1 missing)
  - _computer_use_provider_rows  <- hermes_cli/tools_config_providers.py

### qubit_runtime/qubit_cli/update_cmd_check.py  (1 missing)
  - report_pack_tidy  <- hermes_cli/update_cmd_check.py

### qubit_runtime/qubit_cli/update_cmd_fleet.py  (1 missing)
  - _live_gateway_pids_from_fleet  <- hermes_cli/update_cmd_fleet.py

### qubit_runtime/qubit_cli/update_cmd_git.py  (1 missing)
  - _park_detached_head  <- hermes_cli/update_cmd_git.py

### qubit_runtime/qubit_cli/update_cmd_maint.py  (1 missing)
  - _invalidate_live_plugin_catalog_caches  <- hermes_cli/update_cmd_maint.py

### qubit_runtime/qubit_cli/venv_sync.py  (1 missing)
  - _prepare_borrowed_launch  <- hermes_cli/venv_sync.py

### qubit_runtime/qubit_cli/web_routers/local_models.py  (1 missing)
  - _repriced_quickstart  <- hermes_cli/web_routers/local_models.py

### qubit_runtime/qubit_cli/web_routers/oauth.py  (1 missing)
  - _codex_http_error  <- hermes_cli/web_routers/oauth.py

### qubit_runtime/qubit_cli/web_server_skew_exit.py  (1 missing)
  - _run_retirement_watchdog  <- hermes_cli/web_server_skew_exit.py

### qubit_runtime/qubit_state_coverage.py  (1 missing)
  - SessionCoverageMixin  <- hermes_state_coverage.py

### qubit_runtime/qubit_state_identity.py  (1 missing)
  - _stable_tool_key  <- hermes_state_identity.py

### qubit_runtime/qubit_state_maintenance.py  (1 missing)
  - _not_pinned_sql  <- hermes_state_maintenance.py

### qubit_runtime/qubit_state_titles.py  (1 missing)
  - next_title_in_lineage  <- hermes_state_titles.py

### qubit_runtime/plugins/memory/__init__.py  (1 missing)
  - _defines_memory_provider  <- plugins/memory/__init__.py

### qubit_runtime/plugins/memory/config_schema.py  (1 missing)
  - _schema_from_record  <- plugins/memory/config_schema.py

### qubit_runtime/plugins/platforms/discord/adapter_component_auth.py  (1 missing)
  - _component_check_auth  <- plugins/platforms/discord/adapter_component_auth.py

### qubit_runtime/plugins/platforms/discord/adapter_slash_auth.py  (1 missing)
  - DiscordSlashAuthMixin  <- plugins/platforms/discord/adapter_slash_auth.py

### qubit_runtime/plugins/platforms/discord/adapter_voice_info.py  (1 missing)
  - DiscordVoiceInfoMixin  <- plugins/platforms/discord/adapter_voice_info.py

### qubit_runtime/plugins/platforms/email/adapter.py  (1 missing)
  - _imap_payload  <- plugins/platforms/email/adapter.py

### qubit_runtime/plugins/platforms/matrix/adapter_crypto.py  (1 missing)
  - _CryptoStateStore  <- plugins/platforms/matrix/adapter_crypto.py

### qubit_runtime/plugins/platforms/telegram/telegram_held_inbound.py  (1 missing)
  - TelegramHeldInboundMixin  <- plugins/platforms/telegram/telegram_held_inbound.py

### qubit_runtime/pm/packages.py  (1 missing)
  - WhisperCppCpu  <- pm/packages.py

### qubit_runtime/providers/__init__.py  (1 missing)
  - _has_named_custom_provider  <- providers/__init__.py

### qubit_runtime/tests/agent/test_auxiliary_client.py  (1 missing)
  - TestDataEnvelopeUnwrap  <- tests/agent/test_auxiliary_client.py

### qubit_runtime/tests/agent/test_codex_runtime_prompt_handoff.py  (1 missing)
  - test_runtime_gives_no_approval_callback_when_nobody_can_answer  <- tests/agent/test_codex_runtime_prompt_handoff.py

### qubit_runtime/tests/agent/test_context_file_sources.py  (1 missing)
  - test_truncation_marker_names_the_omitted_sections_only  <- tests/agent/test_context_file_sources.py

### qubit_runtime/tests/agent/test_conversation_compression_manual.py  (1 missing)
  - test_rotated_here_n_publishes_the_kept_exchanges  <- tests/agent/test_conversation_compression_manual.py

### qubit_runtime/tests/agent/test_cron_inflight_prompt_reappend.py  (1 missing)
  - test_replay_replaces_surviving_user_row_with_same_message_uid  <- tests/agent/test_cron_inflight_prompt_reappend.py

### qubit_runtime/tests/agent/test_models_dev_provider_capabilities.py  (1 missing)
  - test_provider_wide_vision_flag_gates_tool_results_not_attachments  <- tests/agent/test_models_dev_provider_capabilities.py

### qubit_runtime/tests/agent/test_openai_ultrafast.py  (1 missing)
  - test_desktop_surfaces_carry_the_exact_tier_ultrafast_is_never_plain_fast  <- tests/agent/test_openai_ultrafast.py

### qubit_runtime/tests/agent/test_pre_compress_checkpoint_contract.py  (1 missing)
  - test_persistence_isolated_fork_skips_the_checkpoint_gate_but_durable_agents_do_not  <- tests/agent/test_pre_compress_checkpoint_contract.py

### qubit_runtime/tests/agent/test_reasoning_effort_labels.py  (1 missing)
  - test_codex_app_server_sends_ultra_verbatim_only_where_the_model_reaches_max  <- tests/agent/test_reasoning_effort_labels.py

### qubit_runtime/tests/agent/test_skill_commands.py  (1 missing)
  - TestDuplicateNamesAgreeAcrossSurfaces  <- tests/agent/test_skill_commands.py

### qubit_runtime/tests/agent/test_skill_invocation_description.py  (1 missing)
  - TestGatewayAutoLoadScaffold  <- tests/agent/test_skill_invocation_description.py

### qubit_runtime/tests/agent/transports/test_chat_completions_reasoning_details_replay.py  (1 missing)
  - test_openrouter_keeps_and_nous_strips_reasoning_details  <- tests/agent/transports/test_chat_completions_reasoning_details_replay.py

### qubit_runtime/tests/cron/test_monitor_kind.py  (1 missing)
  - test_monitor_script_uses_configured_interpreter  <- tests/cron/test_monitor_kind.py

### qubit_runtime/tests/cron/test_restart_safe_worker.py  (1 missing)
  - test_dispatch_failure_notice_resolves_the_owning_profiles_home_channel  <- tests/cron/test_restart_safe_worker.py

### qubit_runtime/tests/cron/test_unreachable_retry.py  (1 missing)
  - test_ladder_reruns_do_not_spend_extra_repeat_budget  <- tests/cron/test_unreachable_retry.py

### qubit_runtime/tests/e2e/core/upgrade/git/test_truthful_outcome.py  (1 missing)
  - _release_file  <- tests/e2e/core/upgrade/git/test_truthful_outcome.py

### qubit_runtime/tests/e2e/core/windows_update/test_paths_and_acls.py  (1 missing)
  - test_hermes_works_and_updates_from_that_profile  <- tests/e2e/core/windows_update/test_paths_and_acls.py

### qubit_runtime/tests/evals/test_core_tool_deferral_sandbox.py  (1 missing)
  - test_child_shell_crontab_and_home_stay_inside_the_run_dir  <- tests/evals/test_core_tool_deferral_sandbox.py

### qubit_runtime/tests/gateway/test_compress_command.py  (1 missing)
  - test_rotated_compress_keeps_atomically_published_foreign_tail  <- tests/gateway/test_compress_command.py

### qubit_runtime/tests/gateway/test_control_socket.py  (1 missing)
  - test_windows_pipe_query_is_bounded_when_the_peer_never_answers  <- tests/gateway/test_control_socket.py

### qubit_runtime/tests/gateway/test_discord_component_auth.py  (1 missing)
  - test_snapshot_user_is_confirmed_with_the_live_check  <- tests/gateway/test_discord_component_auth.py

### qubit_runtime/tests/gateway/test_discord_connect.py  (1 missing)
  - _member  <- tests/gateway/test_discord_connect.py

### qubit_runtime/tests/gateway/test_discord_slash_auth.py  (1 missing)
  - _gateway_authorizes  <- tests/gateway/test_discord_slash_auth.py

### qubit_runtime/tests/gateway/test_email.py  (1 missing)
  - test_oversized_cron_output_is_delivered_as_one_whole_email  <- tests/gateway/test_email.py

### qubit_runtime/tests/gateway/test_exec_approval_timeout_notice.py  (1 missing)
  - _PlainAdapter  <- tests/gateway/test_exec_approval_timeout_notice.py

### qubit_runtime/tests/gateway/test_platform_registry.py  (1 missing)
  - test_registry_walk_from_plugin_load_worker_does_not_wait_on_parent  <- tests/gateway/test_platform_registry.py

### qubit_runtime/tests/gateway/test_platform_shared_metrics.py  (1 missing)
  - test_a_reconnect_backoff_loop_is_one_failed_connect_until_it_recovers  <- tests/gateway/test_platform_shared_metrics.py

### qubit_runtime/tests/gateway/test_startup_failed_platform_reports_degraded.py  (1 missing)
  - _runner  <- tests/gateway/test_startup_failed_platform_reports_degraded.py

### qubit_runtime/tests/gateway/test_telegram_ingress_delivery_gap.py  (1 missing)
  - _stall_reports  <- tests/gateway/test_telegram_ingress_delivery_gap.py

### qubit_runtime/tests/gateway/test_voice_command.py  (1 missing)
  - _unbound_voice_adapter  <- tests/gateway/test_voice_command.py

### qubit_runtime/tests/gateway/test_voice_transcript_dedup_scope.py  (1 missing)
  - _Bot  <- tests/gateway/test_voice_transcript_dedup_scope.py

### qubit_runtime/tests/gateway/test_whatsapp_connect.py  (1 missing)
  - TestNamedConnectFailures  <- tests/gateway/test_whatsapp_connect.py

### qubit_runtime/tests/hermes_cli/test_dashboard_auth_audit.py  (1 missing)
  - test_audit_bounds_string_values  <- tests/hermes_cli/test_dashboard_auth_audit.py

### qubit_runtime/tests/hermes_cli/test_dashboard_auth_body_limit.py  (1 missing)
  - app  <- tests/hermes_cli/test_dashboard_auth_body_limit.py

### qubit_runtime/tests/hermes_cli/test_dashboard_auth_native_flow.py  (1 missing)
  - test_native_authorize_spoofed_forwarded_headers_cannot_bypass_pending_cap  <- tests/hermes_cli/test_dashboard_auth_native_flow.py

### qubit_runtime/tests/hermes_cli/test_dashboard_auth_password_login.py  (1 missing)
  - test_client_ip_uses_asgi_peer_not_forwarded_header  <- tests/hermes_cli/test_dashboard_auth_password_login.py

### qubit_runtime/tests/hermes_cli/test_gateway_restart_external_supervisor.py  (1 missing)
  - test_service_managed_gateway_refuses_fallback_takeover  <- tests/hermes_cli/test_gateway_restart_external_supervisor.py

### qubit_runtime/tests/hermes_cli/test_local_legacy_engine.py  (1 missing)
  - legacy_digests  <- tests/hermes_cli/test_local_legacy_engine.py

### qubit_runtime/tests/hermes_cli/test_local_quickstart.py  (1 missing)
  - test_quickstart_prices_after_installing_the_engine  <- tests/hermes_cli/test_local_quickstart.py

### qubit_runtime/tests/hermes_cli/test_local_runtime_pm.py  (1 missing)
  - test_linux_cuda_runtime_libraries_land_beside_the_server  <- tests/hermes_cli/test_local_runtime_pm.py

### qubit_runtime/tests/hermes_cli/test_logs.py  (1 missing)
  - test_every_log_file_writes_a_stamp_hermes_logs_since_can_read  <- tests/hermes_cli/test_logs.py

### qubit_runtime/tests/hermes_cli/test_memory_provider_migration.py  (1 missing)
  - test_startup_recovery_never_asks_a_dependency_question  <- tests/hermes_cli/test_memory_provider_migration.py

### qubit_runtime/tests/hermes_cli/test_plugin_dependency_consent.py  (1 missing)
  - test_dashboard_install_publishes_a_plugin_the_config_already_selects  <- tests/hermes_cli/test_plugin_dependency_consent.py

### qubit_runtime/tests/hermes_cli/test_plugin_install_ref.py  (1 missing)
  - test_force_reinstall_of_the_same_source_keeps_the_users_files  <- tests/hermes_cli/test_plugin_install_ref.py

### qubit_runtime/tests/hermes_cli/test_plugins_cmd_list.py  (1 missing)
  - test_entrypoint_spec_does_not_abort_plugin_status  <- tests/hermes_cli/test_plugins_cmd_list.py

### qubit_runtime/tests/hermes_cli/test_plugins_loader.py  (1 missing)
  - test_nested_plugin_load_runs_inline_on_deadline_worker  <- tests/hermes_cli/test_plugins_loader.py

### qubit_runtime/tests/hermes_cli/test_profile_export_credentials.py  (1 missing)
  - _seed_stores  <- tests/hermes_cli/test_profile_export_credentials.py

### qubit_runtime/tests/hermes_cli/test_proxy.py  (1 missing)
  - test_loopback_proxy_serves_only_local_non_browser_requests  <- tests/hermes_cli/test_proxy.py

### qubit_runtime/tests/hermes_cli/test_security_audit.py  (1 missing)
  - TestVenvDiscovery  <- tests/hermes_cli/test_security_audit.py

### qubit_runtime/tests/hermes_cli/test_setup_telemetry.py  (1 missing)
  - test_offer_drops_keys_typed_before_it_appeared  <- tests/hermes_cli/test_setup_telemetry.py

### qubit_runtime/tests/hermes_cli/test_shared_metrics_harness.py  (1 missing)
  - test_terminal_outcome_trusts_hermes_flags_not_exit_124  <- tests/hermes_cli/test_shared_metrics_harness.py

### qubit_runtime/tests/hermes_cli/test_subprocess_text_encoding.py  (1 missing)
  - HERMES_CLI  <- tests/hermes_cli/test_subprocess_text_encoding.py

### qubit_runtime/tests/hermes_cli/test_unified_pool_quirk.py  (1 missing)
  - test_vulkan_device_type_decides_discrete_vs_unified  <- tests/hermes_cli/test_unified_pool_quirk.py

### qubit_runtime/tests/hermes_cli/test_uninstall_leftovers.py  (1 missing)
  - test_remove_desktop_app_leftovers_removes_hermes_library_entries  <- tests/hermes_cli/test_uninstall_leftovers.py

### qubit_runtime/tests/hermes_cli/test_update_fleet_checkout_build_stamp.py  (1 missing)
  - test_partial_clone_never_downloads_history_for_a_sha_it_lacks  <- tests/hermes_cli/test_update_fleet_checkout_build_stamp.py

### qubit_runtime/tests/hermes_platform/test_declaration.py  (1 missing)
  - test_requires_gpu_gates_on_the_host_gpu_class  <- tests/hermes_platform/test_declaration.py

### qubit_runtime/tests/hermes_state/test_delete_session_write_guards.py  (1 missing)
  - test_delete_session_if_empty_refuses_when_write_guard_active  <- tests/hermes_state/test_delete_session_write_guards.py

### qubit_runtime/tests/hermes_state/test_never_active_keyed_prune.py  (1 missing)
  - TestPruneSkipsLiveTurns  <- tests/hermes_state/test_never_active_keyed_prune.py

### qubit_runtime/tests/plugins/test_plugin_loader.py  (1 missing)
  - test_concurrent_load_waits_for_the_first_load  <- tests/plugins/test_plugin_loader.py

### qubit_runtime/tests/pm/test_archive_safety.py  (1 missing)
  - TestTransientHolds  <- tests/pm/test_archive_safety.py

### qubit_runtime/tests/pm/test_deb_safety.py  (1 missing)
  - test_zstd_payload_unpacks_under_the_same_containment  <- tests/pm/test_deb_safety.py

### qubit_runtime/tests/pm/test_held_entry_windows_live.py  (1 missing)
  - test_read_only_file_in_replaced_entry_is_removed_on_reinstall  <- tests/pm/test_held_entry_windows_live.py

### qubit_runtime/tests/pm/test_node_resolution.py  (1 missing)
  - test_windows_path_prefers_launchable_cmd_but_never_for_pm_tools  <- tests/pm/test_node_resolution.py

### qubit_runtime/tests/pm/test_pm_core.py  (1 missing)
  - test_unremovable_replaced_entry_on_posix_is_set_aside_without_touching_outside  <- tests/pm/test_pm_core.py

### qubit_runtime/tests/skills/test_google_workspace_api.py  (1 missing)
  - test_gmail_search_empty_result_prints_json_array  <- tests/skills/test_google_workspace_api.py

### qubit_runtime/tests/tools/test_code_execution.py  (1 missing)
  - TestRemoteSharedHostLockdown  <- tests/tools/test_code_execution.py

### qubit_runtime/tests/tools/test_env_passthrough.py  (1 missing)
  - TestRegistrationVisibleAcrossToolContextSnapshots  <- tests/tools/test_env_passthrough.py

### qubit_runtime/tests/tools/test_local_setsid_descendant_sweep.py  (1 missing)
  - TestReapUntrackedGroupGuard  <- tests/tools/test_local_setsid_descendant_sweep.py

### qubit_runtime/tests/tools/test_mcp_liveness.py  (1 missing)
  - test_gpu_requirement_hides_a_connected_server_and_says_which_gpu  <- tests/tools/test_mcp_liveness.py

### qubit_runtime/tests/tools/test_mcp_node_abi.py  (1 missing)
  - test_stdio_server_dying_on_a_node_abi_mismatch_names_the_rebuild_under_hermes_node  <- tests/tools/test_mcp_node_abi.py

### qubit_runtime/tests/tools/test_mcp_stderr_lifecycle.py  (1 missing)
  - test_every_mcp_log_line_carries_a_stamp_hermes_logs_since_reads  <- tests/tools/test_mcp_stderr_lifecycle.py

### qubit_runtime/tests/tools/test_modal_bulk_upload.py  (1 missing)
  - TestModalCommandStdin  <- tests/tools/test_modal_bulk_upload.py

### qubit_runtime/tests/tools/test_process_heartbeat.py  (1 missing)
  - test_first_heartbeat_carries_output_produced_before_arming  <- tests/tools/test_process_heartbeat.py

### qubit_runtime/tests/tools/test_process_registry_list_exit.py  (1 missing)
  - test_poll_during_the_readers_final_drain_keeps_the_whole_output  <- tests/tools/test_process_registry_list_exit.py

### qubit_runtime/tests/tools/test_send_message_target_parse.py  (1 missing)
  - test_unknown_platform_that_left_core_names_its_install_command  <- tests/tools/test_send_message_target_parse.py

### qubit_runtime/tests/tools/test_web_free_fast_search.py  (1 missing)
  - test_unentitled_nous_identity_autodetects_free_fast_search  <- tests/tools/test_web_free_fast_search.py

### qubit_runtime/tests/tools/test_write_approval.py  (1 missing)
  - test_headless_memory_write_stages_without_inline_prompt  <- tests/tools/test_write_approval.py

### qubit_runtime/tests/tui_gateway/test_diagnostics_share_nous.py  (1 missing)
  - test_redacted_support_egress_scrubs_structured_values_and_errors  <- tests/tui_gateway/test_diagnostics_share_nous.py

### qubit_runtime/tests/tui_gateway/test_plugin_skill_slash.py  (1 missing)
  - test_plugin_skills_reach_tui_slash_menu_and_stack_per_profile  <- tests/tui_gateway/test_plugin_skill_slash.py

### qubit_runtime/tests/tui_gateway/test_queued_prompt_restart_retire.py  (1 missing)
  - test_readonly_resume_after_restart_retires_marked_row_without_reopen  <- tests/tui_gateway/test_queued_prompt_restart_retire.py

### qubit_runtime/tests/tui_gateway/test_slash_worker_provider.py  (1 missing)
  - test_slash_worker_child_forwards_provider_to_hermes_cli  <- tests/tui_gateway/test_slash_worker_provider.py

### qubit_runtime/tests/verify/test_environment_and_runner.py  (1 missing)
  - TestTerminateProcessGroupGuard  <- tests/verify/test_environment_and_runner.py

### qubit_runtime/tests/verify/test_ledger_and_nudge_integration.py  (1 missing)
  - test_cli_verify_clears_stale_on_editing_session  <- tests/verify/test_ledger_and_nudge_integration.py

### qubit_runtime/tools/approval_context.py  (1 missing)
  - _no_user_can_answer  <- tools/approval_context.py

### qubit_runtime/tools/approval_gateway_wait.py  (1 missing)
  - _hook_choice  <- tools/approval_gateway_wait.py

### qubit_runtime/tools/code_execution_tool.py  (1 missing)
  - _ship_env_file_and_launch  <- tools/code_execution_tool.py

### qubit_runtime/tools/computer_use/backend.py  (1 missing)
  - ComputerUseProvider  <- tools/computer_use/backend.py

### qubit_runtime/tools/environments/local_env_policy.py  (1 missing)
  - _left_core_env  <- tools/environments/local_env_policy.py

### qubit_runtime/tools/mcp_tool_discovery.py  (1 missing)
  - _stderr_tail_note  <- tools/mcp_tool_discovery.py

### qubit_runtime/tools/mcp_tool_lifecycle.py  (1 missing)
  - _group_alive  <- tools/mcp_tool_lifecycle.py

### qubit_runtime/tools/plugin_guard_context.py  (1 missing)
  - is_google_installed_app_secret  <- tools/plugin_guard_context.py

### qubit_runtime/tools/process_registry_env_log.py  (1 missing)
  - log_delta_command  <- tools/process_registry_env_log.py

### qubit_runtime/tools/process_registry_termination.py  (1 missing)
  - ProcessTerminationMixin  <- tools/process_registry_termination.py

### qubit_runtime/tools/send_message_targets.py  (1 missing)
  - unknown_platform_error  <- tools/send_message_targets.py

### qubit_runtime/tools/terminal_tool_sudo.py  (1 missing)
  - _no_sudo_user  <- tools/terminal_tool_sudo.py

### qubit_runtime/tools/transcription_command.py  (1 missing)
  - _normalize_command_stt_input  <- tools/transcription_command.py

### qubit_runtime/tools/tts_tool.py  (1 missing)
  - _warn_ignored_tts_provider_override  <- tools/tts_tool.py

### qubit_runtime/tui_gateway/methods_prompt.py  (1 missing)
  - _reopen_if_finalized  <- tui_gateway/methods_prompt.py

### qubit_runtime/tui_gateway/methods_session_model_guard.py  (1 missing)
  - create_overrides  <- tui_gateway/methods_session_model_guard.py
