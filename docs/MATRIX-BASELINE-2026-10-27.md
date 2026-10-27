# Matrix baseline — 2026-10-27 (pre-merge, read-only)

**Status:** pre-merge baseline evidence, **not** a release gate and **not** a `VERIFIED` claim.
This lane ran the broad Python matrix on the pre-merge HEAD in four bounded chunks with a
3600 s cap per chunk, on a heavily loaded host. Nothing was fixed, no product code was edited,
no service was restarted, and no test was skipped/weakened by hand.

> **Post-merge rule:** failures recorded here must be **re-run after the merge** before they are
> treated as fixed or as regressions. A green-alone re-run is evidence about test isolation, not
> about product correctness.

## HEAD and environment

| Item | Value |
|---|---|
| Commit (`git rev-parse HEAD`) | `dfe2f82c56448499945bbacf7bbf0288aeb26e0b` |
| Branch / worktree | `feat/ready-matrix` at `/tmp/opencode/wt-ready-matrix` |
| Parent commit | `882d8b43d` — merge the OpenJarvis 8-agent absorption |
| Python | `/home/trick/Projects/Repos/qubit-voicebridge/.venv/bin/python` → 3.12.3 |
| pytest | 9.1.1 (plugins: asyncio 1.3.0, timeout 2.4.0, anyio 4.15.1) |
| Host | 16 CPUs, load average **23.63 / 20.77 / 20.99** at lane start (`uptime`) |
| Invocation | `nice -n 10`, `PYTHONPATH=.` (from `qubit_runtime/`) or `PYTHONPATH=qubit_runtime` (from repo root) |
| Outer cap | `timeout --signal=INT --kill-after=15 3600` per chunk |
| Windows | out of scope (owner ruling 11) |
| Combined orb/Qt suites | not run (explicitly excluded by the task) |

The host stayed loaded for the whole lane (a 3D game / Steam processes plus other agents),
which is why the two large chunks did not finish inside the cap. Wall-clock throughput was
roughly 2.7 tests/s in `tests/qubit_cli` and ~1.25 tests/s in `tests/gateway`.

## Summary

| # | Chunk | Exact command (see below for full text) | Outcome |
|---|---|---|---|
| 1 | runtime/roles/recipes/connectors/traces/evals | `pytest -q tests/runtime tests/roles tests/recipes tests/connectors tests/traces tests/evals` | **PASS** — 572 passed, 92.11 s, exit 0 |
| 2 | root absorption + voice/HUD set | `pytest -q <11 root test files>` | **PASS** — 138 passed, 107.41 s, exit 0 |
| 3 | non-gateway matrix | `pytest -q tests/qubit_cli tests/agent tests/tools tests/cron tests/qubit_state tests/skills tests/computer_use` | **TIMED OUT at 23%** — 9,523 passed / 361 failed / 2 teardown errors observed, then SIGKILL |
| 4 | gateway matrix | `pytest -q tests/gateway tests/tui_gateway tests/acp_adapter` | **TIMED OUT at 31%** — 4,482 passed / 21 failed observed, then exit 124 |

Chunk 3 collected **44,356** items (**44,350** selected after marker deselection); it reached
**23%** in 3,600 s. Chunk 4 collected **14,244** items; it reached **31%** in 3,600 s.
**The matrix did not complete.** No count below is extrapolated.

## Chunk 1 — runtime / roles / recipes / connectors / traces / evals

```bash
cd /tmp/opencode/wt-ready-matrix/qubit_runtime
PYTHONPATH=. nice -n 10 /home/trick/Projects/Repos/qubit-voicebridge/.venv/bin/python -m pytest -q \
  tests/runtime tests/roles tests/recipes tests/connectors tests/traces tests/evals
```
Result: **`572 passed in 92.11s (0:01:32)`**, exit 0. No failures.

## Chunk 2 — root absorption / voice-edit / HUD / recipes-voice set

```bash
cd /tmp/opencode/wt-ready-matrix
PYTHONPATH=qubit_runtime nice -n 10 /home/trick/Projects/Repos/qubit-voicebridge/.venv/bin/python -m pytest -q \
  tests/test_openjarvis_absorption.py tests/test_voice_edit_hud_actions9u.py \
  tests/test_voice_edit_hud9u.py tests/test_voice_edit_live_panel9u.py \
  tests/test_voice_edit_helper9u.py tests/test_voice_edit_integration9u.py \
  tests/test_voice_overlay_ui9u.py tests/test_voice_overlay_close9u.py \
  tests/test_stt_client9u.py tests/test_recipe_voice_wave4x.py \
  tests/test_voice_inventory_coverage.py
```
Result: **`138 passed in 107.41s (0:01:47)`**, exit 0. No failures.

## Chunk 3 — non-gateway matrix (TIMED OUT at 23%)

```bash
cd /tmp/opencode/wt-ready-matrix/qubit_runtime
PYTHONPATH=. nice -n 10 timeout --signal=INT --kill-after=15 3600 \
  /home/trick/Projects/Repos/qubit-voicebridge/.venv/bin/python -m pytest -q \
  tests/qubit_cli tests/agent tests/tools tests/cron tests/qubit_state tests/skills tests/computer_use
```

- Collected: **44,356** items, **44,350** selected (6 deselected, 1 skipped at collection).
- Wall clock: **3,600 s** (cap), last progress line **23%**.
- Observed before the kill: **9523 passed, 361 failed, 2 teardown errors**
  (the process was SIGKILLed 15 s after SIGINT and printed **no** final summary line).
- No `tests/agent`, `tests/tools`, `tests/cron`, `tests/qubit_state`, `tests/skills` or
  `tests/computer_use` test ran: the chunk never finished `tests/qubit_cli` (9884
  completed tests are all from `tests/qubit_cli`).

### Chunk 3 failures that also fail when run alone (29)

The 361 unique failed/error node ids from the combined chunk were re-run alone with
`--tb=line --timeout=60` (1,800 s cap): **332 passed, 29 failed in 135.39 s**. These 29
reproduce outside the combined run and are the actionable baseline failures. Errors are the
`reprcrash` one-liner, trimmed to 240 chars.

- `tests/qubit_cli/test_dashboard_param_clamps.py::TestAnalyticsDaysClamps::test_in_range_days_accepted`  
  ` assert 401 == 200 + where 401 = <Response [401 Unauthorized]>.status_code`
- `tests/qubit_cli/test_dashboard_param_clamps.py::TestAnalyticsDaysClamps::test_out_of_range_days_rejected[0]`  
  ` assert 401 == 422 + where 401 = <Response [401 Unauthorized]>.status_code`
- `tests/qubit_cli/test_dashboard_param_clamps.py::TestAnalyticsDaysClamps::test_out_of_range_days_rejected[100000]`  
  ` assert 401 == 422 + where 401 = <Response [401 Unauthorized]>.status_code`
- `tests/qubit_cli/test_dashboard_param_clamps.py::TestAnalyticsDaysClamps::test_out_of_range_days_rejected[-5]`  
  ` assert 401 == 422 + where 401 = <Response [401 Unauthorized]>.status_code`
- `tests/qubit_cli/test_dashboard_param_clamps.py::TestSessionPaginationClamps::test_in_range_limit_accepted`  
  ` assert 401 == 200 + where 401 = <Response [401 Unauthorized]>.status_code`
- `tests/qubit_cli/test_dashboard_param_clamps.py::TestSessionPaginationClamps::test_oversized_limit_rejected`  
  ` assert 401 == 422 + where 401 = <Response [401 Unauthorized]>.status_code`
- `tests/qubit_cli/test_dashboard_param_clamps.py::TestSessionPaginationClamps::test_profile_fanout_accepts_real_desktop_maximum`  
  ` assert 401 == 200 + where 401 = <Response [401 Unauthorized]>.status_code`
- `tests/qubit_cli/test_dashboard_param_clamps.py::TestSessionPaginationClamps::test_profile_fanout_limit_clamped`  
  ` assert 401 == 422 + where 401 = <Response [401 Unauthorized]>.status_code`
- `tests/qubit_cli/test_ipv6_url_authorities.py::TestBrowserOpenUrl::test_start_server_banner_prints_bracketed_ipv6_url`  
  ` AssertionError: assert 'Qubit Web UI → http://[::1]:9119' in '' + where '' = CaptureResult(out=' Qubit Web UI → http://[::1]:9119\n', err='').err + where CaptureResult(out=' Qubit Web UI → http://[::1]:9119\n', err='') = readouterr() + wher`
- `tests/qubit_cli/test_mcp_catalog_env_boundary.py::test_catalog_cannot_declare_reserved_control_key`  
  ` AssertionError: assert 'denylist' in 'Invalid Host header. Dashboard requests must use the bound hostname or the configured public hostname.'`
- `tests/qubit_cli/test_mcp_catalog_env_boundary.py::test_catalog_non_secret_env_never_lands_in_env_file`  
  ` assert 400 == 200 + where 400 = <Response [400 Bad Request]>.status_code`
- `tests/qubit_cli/test_mcp_catalog_env_boundary.py::test_catalog_rejects_undeclared_key_before_any_write_or_install`  
  ` AssertionError: assert 'UNRELATED_SETTING' in 'Invalid Host header. Dashboard requests must use the bound hostname or the configured public hostname.'`
- `tests/qubit_cli/test_mcp_dashboard_oauth.py::test_cancel_marks_the_flow_terminal_for_a_retrying_worker`  
  ` KeyError: 'ok'`
- `tests/qubit_cli/test_mcp_dashboard_oauth.py::test_flow_status_does_not_expose_authorization_code`  
  ` assert 400 == 200 + where 400 = <Response [400 Bad Request]>.status_code`
- `tests/qubit_cli/test_mcp_dashboard_oauth.py::test_hosted_auth_start_returns_public_authorization_url`  
  ` assert 400 == 200 + where 400 = <Response [400 Bad Request]>.status_code`
- `tests/qubit_cli/test_mcp_dashboard_oauth.py::test_hosted_callback_bypasses_gated_cookie_auth`  
  ` assert 400 == 200 + where 400 = <Response [400 Bad Request]>.status_code`
- `tests/qubit_cli/test_plugin_config_state_bridge.py::test_config_rejects_global_cross_plugin_and_traversal_paths[]`  
  ` AssertionError: assert 'Rejected config path' in '' + where '' = <_pytest.logging.LogCaptureFixture object at 0x7ca7e940a6f0>.text`
- `tests/qubit_cli/test_plugin_config_state_bridge.py::test_config_rejects_global_cross_plugin_and_traversal_paths[..\\..\\model.provider]`  
  ` AssertionError: assert 'Rejected config path' in '' + where '' = <_pytest.logging.LogCaptureFixture object at 0x7ca7e8e1a300>.text`
- `tests/qubit_cli/test_plugin_config_state_bridge.py::test_config_rejects_global_cross_plugin_and_traversal_paths[model.provider]`  
  ` AssertionError: assert 'Rejected config path' in '' + where '' = <_pytest.logging.LogCaptureFixture object at 0x7ca7e8e1b320>.text`
- `tests/qubit_cli/test_plugin_config_state_bridge.py::test_config_rejects_global_cross_plugin_and_traversal_paths[plugins.entries.other.settings.token]`  
  ` AssertionError: assert 'Rejected config path' in '' + where '' = <_pytest.logging.LogCaptureFixture object at 0x7ca7e916bd40>.text`
- `tests/qubit_cli/test_plugin_config_state_bridge.py::test_config_rejects_global_cross_plugin_and_traversal_paths[../../security.approval_mode]`  
  ` AssertionError: assert 'Rejected config path' in '' + where '' = <_pytest.logging.LogCaptureFixture object at 0x7ca7e8d7a180>.text`
- `tests/qubit_cli/test_plugin_config_state_bridge.py::test_config_rejects_global_cross_plugin_and_traversal_paths[security.approval_mode]`  
  ` AssertionError: assert 'Rejected config path' in '' + where '' = <_pytest.logging.LogCaptureFixture object at 0x7ca7e8d78aa0>.text`
- `tests/qubit_cli/test_plugin_config_state_bridge.py::test_config_rejects_global_cross_plugin_and_traversal_paths[settings.endpoint]`  
  ` AssertionError: assert 'Rejected config path' in '' + where '' = <_pytest.logging.LogCaptureFixture object at 0x7ca7e8d78fe0>.text`
- `tests/qubit_cli/test_plugin_runtime_disable_gate.py::TestBundledPluginAssetGate::test_bundled_asset_returns_404_when_disabled`  
  ` AssertionError: Sanity: bundled plugin asset should be served when not disabled assert 400 == 200 + where 400 = <Response [400 Bad Request]>.status_code`
- `tests/qubit_cli/test_plugins_cadence.py::test_auto_apply_selects_only_updateable_git_and_persists_receipt[False]`  
  ` AssertionError: assert ('broken' in '') + where '' = <_pytest.logging.LogCaptureFixture object at 0x7ca80b7ec530>.text`
- `tests/qubit_cli/test_plugins_cadence.py::test_auto_apply_selects_only_updateable_git_and_persists_receipt[True]`  
  ` AssertionError: assert ('broken' in '') + where '' = <_pytest.logging.LogCaptureFixture object at 0x7ca80b7ed100>.text`
- `tests/qubit_cli/test_plugins_cadence.py::test_housekeeping_runs_real_cadence_and_backs_off[True]`  
  ` AssertionError: assert 'plugin update check failed' in '' + where '' = <_pytest.logging.LogCaptureFixture object at 0x7ca80b7ee870>.text`
- `tests/qubit_cli/test_profile_delete_log_handlers.py::test_delete_profile_releases_every_routed_handler_for_that_home[explicit-routing]`  
  ` FileNotFoundError: [Errno 2] No such file or directory: '/tmp/pytest-of-trick/pytest-35/test_delete_profile_releases_e0/.hermes/profiles/routed-log-delete/logs/agent.log'`
- `tests/qubit_cli/test_profile_delete_log_handlers.py::test_delete_profile_releases_every_routed_handler_for_that_home[setup_logging-adoption]`  
  ` FileNotFoundError: [Errno 2] No such file or directory: '/tmp/pytest-of-trick/pytest-35/test_delete_profile_releases_e1/.hermes/profiles/routed-log-delete/logs/agent.log'`

### Chunk 3 failures seen only in the combined run (332)

These failed in the combined chunk but pass when the same node ids are run alone. That is
evidence of **order/state contamination** in the combined process (env, `QUBIT_HOME`, shared
singletons, temp dirs), not proof that the product is correct. They must be re-run after the
merge and should be triaged file-by-file.

Per-file concentration of the 361 combined failures:

| File | Failures |
|---|---|
| `tests/qubit_cli/test_manual_serve_deferral.py` | 53 |
| `tests/qubit_cli/test_plugin_config_state_bridge.py` | 13 |
| `tests/qubit_cli/test_nous_policy_surfaces.py` | 11 |
| `tests/qubit_cli/test_pricing_cache_auth_key.py` | 11 |
| `tests/qubit_cli/test_login_cli_command.py` | 9 |
| `tests/qubit_cli/test_dashboard_param_clamps.py` | 8 |
| `tests/qubit_cli/test_multiplex_cli_cache_scope.py` | 8 |
| `tests/qubit_cli/test_model_validation.py` | 7 |
| `tests/qubit_cli/test_models.py` | 7 |
| `tests/qubit_cli/test_nous_account.py` | 7 |
| `tests/qubit_cli/test_nous_picker_on_sale.py` | 7 |
| `tests/qubit_cli/test_env_export_line_lifecycle.py` | 6 |
| `tests/qubit_cli/test_picker_key_cmd_discovery.py` | 6 |
| `tests/qubit_cli/test_gateway_tools.py` | 5 |
| `tests/qubit_cli/test_gui_command.py` | 5 |

The complete list of all 361 node ids observed failing in the combined chunk is in
Appendix A.

## Chunk 4 — gateway / tui_gateway / acp_adapter (TIMED OUT at 31%)

```bash
cd /tmp/opencode/wt-ready-matrix/qubit_runtime
PYTHONPATH=. nice -n 10 timeout --signal=INT --kill-after=15 3600 \
  /home/trick/Projects/Repos/qubit-voicebridge/.venv/bin/python -m pytest -q \
  tests/gateway tests/tui_gateway tests/acp_adapter
```

- Collected: **14,244** items.
- Wall clock: **3,600 s** (cap), last progress line **31%**, exit 124.
- Observed before the kill: **4482 passed, 21 failed, 0 teardown errors**.
- Coverage reached ~4,503 of 14,244 tests (approx. one third of `tests/gateway`);
  `tests/tui_gateway` and `tests/acp_adapter` did not run at all.
- The process ended with repeated `AttributeError: '_Thread' object has no attribute 'is_alive'`
  from `gateway/runtime_ownership.py:209` in an `atexit` callback after the interrupt; the same
  error also appears as a test failure. This is recorded as observed, not diagnosed here.

All **21** chunk-4 failures **passed when run alone** (`--tb=line --timeout=90`, 900 s cap:
**21 passed in 13.88 s**). They are therefore combined-run contamination, not standalone
reproductions. Node ids and the one-line errors observed in the combined run:

- `tests/gateway/test_api_authority_retry.py::test_api_busy_fifo_retries_observe_one_durable_result`  
  ` AssertionError: state.db: linked SQLite 3.45.1 (interpreter /home/trick/Projects/Repos/qubit-voicebridge/.venv/bin/python) is vulnerable to the WAL-reset corruption bug (https://sqlite.org/wal.html#walresetbug) — using journal_mode=DELETE i`
- `tests/gateway/test_claim_reopen_off_loop.py::test_reopening_a_finalized_local_row_behind_a_contended_writer_keeps_the_loop_live`  
  ` AssertionError: the owner loop stalled 1.09s behind the reopen writer assert 1.0924968079998507 < 0.5 + where 1.0924968079998507 = max([0.020424138998350827, 0.02031728800284327, 0.020408177999343025, 0.02044815000044764, 0.0203971589980938`
- `tests/gateway/test_cron_fire_webhook.py::test_accepted_response_waits_for_durable_admission`  
  ` assert False`
- `tests/gateway/test_cron_fire_webhook.py::test_fire_does_not_require_api_server_key`  
  ` AssertionError: assert 503 == 202 + where 503 = <ClientResponse(http://127.0.0.1:44007/api/cron/fire) [503 Service Unavailable]>\n<CIMultiDictProxy('Retry-After': '60'...rset=utf-8', 'Content-Length': '55', 'Date': 'Tue, 27 Oct 2026 16:48:2`
- `tests/gateway/test_cron_fire_webhook.py::test_sync_verifier_runs_off_the_event_loop`  
  ` AssertionError: assert 503 == 202 + where 503 = <ClientResponse(http://127.0.0.1:39853/api/cron/fire) [503 Service Unavailable]>\n<CIMultiDictProxy('Retry-After': '60'...rset=utf-8', 'Content-Length': '61', 'Date': 'Tue, 27 Oct 2026 16:48:3`
- `tests/gateway/test_cron_fire_webhook.py::test_async_verifier_is_awaited`  
  ` AssertionError: assert 503 == 202 + where 503 = <ClientResponse(http://127.0.0.1:45099/api/cron/fire) [503 Service Unavailable]>\n<CIMultiDictProxy('Retry-After': '60'...rset=utf-8', 'Content-Length': '61', 'Date': 'Tue, 27 Oct 2026 16:48:4`
- `tests/gateway/test_cron_fire_webhook.py::test_estop_refuses_fire_before_admission_and_lifts_on_resume`  
  ` AssertionError: assert 503 == 202 + where 503 = <ClientResponse(http://127.0.0.1:39429/api/cron/fire) [503 Service Unavailable]>\n<CIMultiDictProxy('Retry-After': '60'...rset=utf-8', 'Content-Length': '59', 'Date': 'Tue, 27 Oct 2026 16:48:5`
- `tests/gateway/test_discord_attachment_download.py::TestCacheDiscordImage::test_falls_back_to_url_when_bytes_validator_rejects`  
  ` httpx.HTTPStatusError: Client error '404 Not Found' for url 'https://cdn.discordapp.com/attachments/fake/file.png' For more information check: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/404`
- `tests/gateway/test_discord_attachment_download.py::TestCacheDiscordAudio::test_prefers_att_read_over_url`  
  ` AssertionError: assert '/tmp/pytest-...a06b5b9f3.ogg' == '/tmp/voice.ogg' - /tmp/voice.ogg + /tmp/pytest-of-trick/pytest-33/test_prefers_att_read_over_url0/hermes_test/cache/audio/audio_1fda06b5b9f3.ogg`
- `tests/gateway/test_discord_attachment_download.py::TestCacheDiscordDocument::test_fallback_blocked_by_ssrf_guard`  
  ` TypeError: 'coroutine' object does not support the asynchronous context manager protocol`
- `tests/gateway/test_discord_attachment_download.py::TestHandleMessageUsesAuthenticatedRead::test_image_downloads_via_att_read_not_url`  
  ` AssertionError: assert ['/tmp/pytest...2edb2577.png'] == ['/tmp/img_from_read.png'] At index 0 diff: '/tmp/pytest-of-trick/pytest-33/test_image_downloads_via_att_r0/hermes_test/cache/images/img_c7fc2edb2577.png' != '/tmp/img_from_read.png' `
- `tests/gateway/test_discord_reactions.py::test_process_message_background_adds_and_swaps_reactions`  
  ` IndexError: list index out of range`
- `tests/gateway/test_discord_send.py::test_send_video_uses_path_based_files_kwarg`  
  ` AttributeError: None has no attribute 'File'`
- `tests/gateway/test_discord_send.py::test_send_video_fails_loud_when_message_has_no_attachments`  
  ` AttributeError: None has no attribute 'File'`
- `tests/gateway/test_discord_send.py::test_send_file_attachment_forum_uses_files_kwarg`  
  ` AttributeError: None has no attribute 'File'`
- `tests/gateway/test_discord_voice_stale_binding.py::test_speech_buffered_across_a_rebind_is_not_stamped_with_the_new_binding[700-reaching1]`  
  ` assert (1, []) == (1, [115200]) At index 1 diff: [] != [115200] Use -v to get more diff`
- `tests/gateway/test_discord_voice_stale_binding.py::test_speech_spanning_a_rebind_reaches_the_new_channel_without_the_earlier_samples`  
  ` assert (1, []) == (1, [115200]) At index 1 diff: [] != [115200] Use -v to get more diff`
- `tests/gateway/test_discord_voice_stale_binding.py::test_leave_after_a_rebind_does_not_flush_old_speech_into_the_new_channel[700-reaching1]`  
  ` assert (1, []) == (1, [115200]) At index 1 diff: [] != [115200] Use -v to get more diff`
- `tests/gateway/test_discord_voice_stale_binding.py::test_a_programmatic_join_while_connected_applies_the_requested_binding[same-text-channel]`  
  ` assert (1, []) == (1, [115200]) At index 1 diff: [] != [115200] Use -v to get more diff`
- `tests/gateway/test_host_gateway_lock_refusal.py::test_a_replace_unit_that_replaced_nothing_is_still_refused_when_it_loses_the_lock`  
  ` AttributeError: '_Thread' object has no attribute 'is_alive'`
- `tests/gateway/test_host_rendezvous.py::test_two_homes_take_their_own_per_home_lock_but_only_one_host_lock`  
  ` AttributeError: '_Thread' object has no attribute 'is_alive'`

## Deviations and anomalies (honest record)

1. **`-v` cannot be used on the non-gateway chunk.** A verbose run aborted with
   `INTERNALERROR> TypeError: ...<lambda>() got an unexpected keyword argument 'fallback'` in
   `_pytest/terminal.py::_write_progress_information_filling_space` after
   `tests/qubit_cli/test_cli_force_redraw.py::TestFirstSigwinchBaseline::test_install_resize_recovery_falls_back_to_shutil`
   monkeypatched the real `shutil.get_terminal_size` with a one-argument lambda during the
   call-report phase (before monkeypatch teardown). That run stopped at 3% with
   `3 failed, 1498 passed` (exit 3). All reported chunk runs therefore used `-q`.
2. **`-p` is intercepted by the Qubit CLI.** `pytest -p <name>` is parsed as the Qubit
   `--profile` flag (`qubit_cli/main.py::_apply_profile_override`) and fails with
   `Profile '<name>' does not exist`. Live node-id capture used the `PYTEST_PLUGINS` env var
   with a read-only `/tmp` plugin that appends `OUTCOME nodeid` lines, so a killed run still
   leaves exact node ids. The plugin is not in the repo and does not touch product code.
3. **SIGINT teardown is slow.** Both 3,600 s chunks needed the 15 s `--kill-after`; chunk 3
   printed no summary at all, chunk 4 exited 124 with an `atexit` traceback. Counts above come
   from the per-test status log, not from a final pytest summary.
4. **The 332 "pass alone" cases were re-run with `--timeout=60`**, which is a diagnostic
   harness change, not part of the original chunk command.
5. **No frontend (`vitest`/`tsc`) run** — the task scoped this lane to the Python matrix.
6. **No service was restarted; no product code was edited.**

## What this baseline does and does not support

- It supports: chunks 1 and 2 are green at `dfe2f82c5`; chunk 3 has 29 standalone-reproducing
  failures and 332 combined-run-only failures in its first 23%; chunk 4 has 21 combined-run-only
  failures in its first 31% and no standalone reproduction.
- It does **not** support: any claim that the full matrix is green, that the remaining 77% of
  chunk 3 or 69% of chunk 4 is clean, or any `VERIFIED`/release verdict.
- The `AttributeError: '_Thread' object has no attribute 'is_alive'` in
  `gateway/runtime_ownership.py:209` is worth a focused look during the merge — it fires during
  interpreter teardown on an interrupted gateway test run.

## Appendix A — all 361 node ids that failed in the combined chunk 3

```text
tests/qubit_cli/test_auth_nous_provider.py::TestResolveVerifyFallback::test_valid_ca_bundle_in_auth_state_is_returned
tests/qubit_cli/test_backup.py::TestSafeCopyDb::test_aborts_when_source_remains_busy_past_deadline
tests/qubit_cli/test_backup.py::TestImportHonorsQubitHomeOverride::test_import_targets_named_profile_home
tests/qubit_cli/test_cli_loading_indicator.py::TestCLILoadingIndicator::test_skills_command_sets_busy_state_and_prints_status
tests/qubit_cli/test_dashboard_globals_writer_reader_contract.py::test_desktop_extractor_finds_the_token_the_server_serves
tests/qubit_cli/test_dashboard_globals_writer_reader_contract.py::test_spa_readers_agree_with_writer_globals
tests/qubit_cli/test_dashboard_param_clamps.py::TestSessionPaginationClamps::test_oversized_limit_rejected
tests/qubit_cli/test_dashboard_param_clamps.py::TestSessionPaginationClamps::test_profile_fanout_limit_clamped
tests/qubit_cli/test_dashboard_param_clamps.py::TestSessionPaginationClamps::test_profile_fanout_accepts_real_desktop_maximum
tests/qubit_cli/test_dashboard_param_clamps.py::TestSessionPaginationClamps::test_in_range_limit_accepted
tests/qubit_cli/test_dashboard_param_clamps.py::TestAnalyticsDaysClamps::test_out_of_range_days_rejected[0]
tests/qubit_cli/test_dashboard_param_clamps.py::TestAnalyticsDaysClamps::test_out_of_range_days_rejected[-5]
tests/qubit_cli/test_dashboard_param_clamps.py::TestAnalyticsDaysClamps::test_out_of_range_days_rejected[100000]
tests/qubit_cli/test_dashboard_param_clamps.py::TestAnalyticsDaysClamps::test_in_range_days_accepted
tests/qubit_cli/test_dashboard_plugin_routes_secret_scope.py::test_install_route_reads_the_git_credential_in_profile_scope
tests/qubit_cli/test_dashboard_plugin_routes_secret_scope.py::test_update_route_reads_the_git_credential_in_profile_scope
tests/qubit_cli/test_dashboard_plugin_routes_secret_scope.py::test_failure_and_request_shape_errors_are_unchanged
tests/qubit_cli/test_diff_command.py::test_diff_clean_repo_reports_no_changes
tests/qubit_cli/test_diff_command.py::test_diff_session_empty_reports_no_changes
tests/qubit_cli/test_env_custom_keys.py::test_unknown_env_key_surfaces_as_custom
tests/qubit_cli/test_env_custom_keys.py::test_custom_key_is_password_masked
tests/qubit_cli/test_env_export_line_lifecycle.py::test_classic_pat_save_via_endpoint_succeeds
tests/qubit_cli/test_env_export_line_lifecycle.py::test_remove_export_prefixed_token
tests/qubit_cli/test_env_export_line_lifecycle.py::test_update_export_prefixed_token_does_not_duplicate
tests/qubit_cli/test_env_export_line_lifecycle.py::test_export_line_with_comment_untouched
tests/qubit_cli/test_env_export_line_lifecycle.py::test_remove_token_written_with_spaces_around_equals
tests/qubit_cli/test_env_export_line_lifecycle.py::test_spaced_token_rotate_then_delete_does_not_resurrect
tests/qubit_cli/test_gateway_cli_commands.py::test_diff_session_renders_the_gateway_sessions_checkpoint_diff
tests/qubit_cli/test_gateway_cli_commands.py::test_diff_session_reports_a_session_without_checkpoints
tests/qubit_cli/test_gateway_model_switch_cli.py::test_a_declined_owner_warning_switches_nothing
tests/qubit_cli/test_gateway_reload_mcp.py::test_reload_mcp_runs_on_the_gateway
tests/qubit_cli/test_gateway_runtime_service.py::test_ensure_checks_effective_service_binding_before_start[omitted_empty_exec_lists-deadline]
tests/qubit_cli/test_gateway_service.py::TestGatewaySystemServiceRouting::test_wait_records_a_short_lived_failed_replacement
tests/qubit_cli/test_gateway_session_store.py::test_new_session_is_created_on_the_gateway_and_its_id_adopted
tests/qubit_cli/test_gateway_tools.py::test_stop_kills_the_session_processes_through_the_gateway
tests/qubit_cli/test_gateway_tools.py::test_agents_lists_the_gateway_sessions_processes_and_subagents
tests/qubit_cli/test_gateway_tools.py::test_stop_typed_mid_turn_interrupts_the_turn_then_stops_processes
tests/qubit_cli/test_gateway_tools.py::test_tools_lists_the_gateway_session_catalog_with_main_rendering
tests/qubit_cli/test_gateway_tools.py::test_reload_skills_rescans_in_the_gateway_session_and_prints_main_lines
tests/qubit_cli/test_gui_command.py::test_source_launch_reads_bom_electron_path_without_provisioning[False]
tests/qubit_cli/test_gui_command.py::test_source_launch_reads_bom_electron_path_without_provisioning[True]
tests/qubit_cli/test_gui_command.py::test_gui_close_preview_flag_forwards_to_packaged_exe
tests/qubit_cli/test_gui_command.py::test_gui_launches_even_when_desktop_entry_install_fails
tests/qubit_cli/test_gui_command.py::test_gui_linux_source_launch_bridges_detected_password_store
tests/qubit_cli/test_handoff_slow_dispatch_timeout.py::TestCLIWaitLoop::test_slow_dispatch_beyond_60s_completes
tests/qubit_cli/test_hf_browse.py::test_search_route_requires_query_and_maps_errors
tests/qubit_cli/test_hf_browse.py::test_browsed_download_stages_and_bounces
tests/qubit_cli/test_hf_browse.py::test_browsed_download_rejects_non_gguf
tests/qubit_cli/test_hf_browse.py::test_sideload_links_and_bounces
tests/qubit_cli/test_hf_browse.py::test_sideload_rejects_non_gguf
tests/qubit_cli/test_indicator_command.py::TestHandleIndicatorCommand::test_invalid_style_prints_usage_and_does_not_save
tests/qubit_cli/test_ipv6_url_authorities.py::TestBrowserOpenUrl::test_start_server_banner_prints_bracketed_ipv6_url
tests/qubit_cli/test_kanban_core_functionality.py::test_dead_worker_reap_surfaces_the_workers_own_last_output[_drive_protocol_violation]
tests/qubit_cli/test_kanban_core_functionality.py::test_dead_worker_reap_surfaces_the_workers_own_last_output[_drive_nonzero_crash]
tests/qubit_cli/test_kanban_credential_startup_exit.py::test_credential_resolution_exit[revoked-78-True-False]
tests/qubit_cli/test_kanban_credential_startup_exit.py::test_credential_resolution_exit[revoked-78-True-True]
tests/qubit_cli/test_kanban_credential_startup_exit.py::test_credential_resolution_exit[quota-75-True-False]
tests/qubit_cli/test_kanban_credential_startup_exit.py::test_credential_resolution_exit[quota-75-True-True]
tests/qubit_cli/test_kanban_db.py::test_stale_claim_extend_live_worker_does_not_count_failure
tests/qubit_cli/test_kanban_db.py::test_rate_limit_exit_requeues_without_counting_failure
tests/qubit_cli/test_kanban_db.py::test_terminal_provider_exit_blocks_after_one_attempt_in_either_lane[ready]
tests/qubit_cli/test_kanban_db.py::test_terminal_provider_exit_blocks_after_one_attempt_in_either_lane[review]
tests/qubit_cli/test_kanban_db.py::test_connect_works_when_wal_is_silently_refused
tests/qubit_cli/test_kanban_decompose.py::test_decompose_with_fanout_creates_children
tests/qubit_cli/test_kanban_decompose.py::test_decompose_fanout_children_inherit_root_assignee_when_unrouted
tests/qubit_cli/test_kanban_decompose.py::test_decompose_explicit_default_assignee_wins_over_root_assignee
tests/qubit_cli/test_kanban_decompose.py::test_decompose_fanout_false_invalid_llm_assignee_uses_default
tests/qubit_cli/test_kanban_dispatch_tick_hook.py::test_active_tick_fires_hook_with_outcome_ok
tests/qubit_cli/test_kanban_dispatch_tick_hook.py::test_tick_hook_fires_after_dispatch_lock_released
tests/qubit_cli/test_kanban_lifecycle_hooks.py::test_claim_fires_hook
tests/qubit_cli/test_kanban_notify.py::test_gateway_create_autosubscribes_on_explicit_board
tests/qubit_cli/test_kanban_notify.py::test_gateway_autosubscribe_roundtrips_user_id_alt_for_session_key[channel-None-False]
tests/qubit_cli/test_kanban_notify.py::test_gateway_autosubscribe_roundtrips_user_id_alt_for_session_key[group-th-42-True]
tests/qubit_cli/test_kanban_swarm.py::test_create_swarm_graph_is_atomic_and_rolls_back_partial_build
tests/qubit_cli/test_kanban_task_updated_hook.py::test_assign_fires_updated_with_changed_fields
tests/qubit_cli/test_kanban_worker_lifecycle_hooks.py::test_dispatch_spawn_fires_worker_spawned
tests/qubit_cli/test_kanban_worker_lifecycle_hooks.py::test_crash_reclaim_fires_worker_exited
tests/qubit_cli/test_kanban_worker_lifecycle_hooks.py::test_stale_claim_reclaim_fires_hook
tests/qubit_cli/test_kanban_worker_owner_wait.py::test_worker_waits_out_a_draining_owner_and_submits_once
tests/qubit_cli/test_kanban_worker_owner_wait.py::test_worker_fails_at_once_when_the_owner_is_not_transiently_down
tests/qubit_cli/test_kanban_write_guard.py::test_connect_raises_when_kanban_home_is_real_root
tests/qubit_cli/test_kanban_write_guard.py::test_connect_raises_for_explicit_db_path_under_real_root
tests/qubit_cli/test_launchd_install_start_now.py::test_cli_no_start_now_loads_only_a_gateway_launchd_already_runs[fresh-install]
tests/qubit_cli/test_launchd_install_start_now.py::test_cli_no_start_now_loads_only_a_gateway_launchd_already_runs[repair-stopped-gateway]
tests/qubit_cli/test_launchd_install_start_now.py::test_cli_no_start_now_loads_only_a_gateway_launchd_already_runs[repair-running-gateway]
tests/qubit_cli/test_launchd_install_start_now.py::test_wizard_no_to_start_now_installs_without_loading
tests/qubit_cli/test_launchd_refresh_reporting.py::test_refresh_reports_registration_outcome
tests/qubit_cli/test_launchd_restart_refresh.py::test_stale_plist_is_refreshed_before_any_kickstart
tests/qubit_cli/test_launchd_restart_refresh.py::test_failed_refresh_routes_to_bounded_bootstrap_not_the_90s_kickstart
tests/qubit_cli/test_lazy_command_exports.py::test_frozen_updater_surface_resolves_to_real_objects
tests/qubit_cli/test_lifecycle.py::test_invoke_hook_notifies_builtin_observers_before_plugins
tests/qubit_cli/test_lifecycle.py::test_finalize_session_closes_core_before_plugin_export
tests/qubit_cli/test_list_picker_providers.py::test_passthrough_kwargs_to_base
tests/qubit_cli/test_list_picker_providers.py::test_curated_openrouter_row_keeps_free_tail_past_max_models
tests/qubit_cli/test_lmstudio_context_policy.py::test_missing_echo_refreshes_loaded_state
tests/qubit_cli/test_load_progress.py::test_load_notice_for_managed_model
tests/qubit_cli/test_load_progress.py::test_prefill_notice_for_managed_model
tests/qubit_cli/test_load_progress.py::test_load_notice_outranks_prefill
tests/qubit_cli/test_local_launch_budget.py::test_boot_writes_the_launch_window
tests/qubit_cli/test_local_launch_budget.py::test_boot_keeps_capacity_when_the_probe_fails
tests/qubit_cli/test_local_launch_budget.py::test_idle_router_gets_windows_for_the_current_desktop
tests/qubit_cli/test_local_launch_budget.py::test_growth_refuses_a_rung_other_programs_leave_no_room_for
tests/qubit_cli/test_local_launch_budget.py::test_residency_cap_prices_capacity
tests/qubit_cli/test_local_mtp_head.py::test_the_preset_drafts_from_the_head_once_it_is_on_disk
tests/qubit_cli/test_local_recommendation.py::test_measured_n1x_profile_changes_speed_eligibility_not_fit_or_quality[256-NVIDIA RTX Spark N1X]
tests/qubit_cli/test_local_recommendation.py::test_measured_n1x_profile_changes_speed_eligibility_not_fit_or_quality[256-NVIDIA RTX Spark N1X (5120-core Blackwell RTX GPU)]
tests/qubit_cli/test_local_recommendation.py::test_measured_n1x_profile_changes_speed_eligibility_not_fit_or_quality[256-NVIDIA RTX Spark N1X (updated device description)]
tests/qubit_cli/test_local_residency_admission.py::test_boot_hands_the_router_the_derived_cap[4-<lambda>-1]
tests/qubit_cli/test_local_windows_igpu_pool.py::test_an_integrated_gpu_gets_what_windows_lets_it_allocate[before-the-engine]
tests/qubit_cli/test_login_cli_command.py::test_the_cli_handler_prints_the_code_then_drains_off_thread
tests/qubit_cli/test_login_cli_command.py::test_the_drain_only_moves_the_free_tier_model_on_completion[terminal0-qubit/welcome-model-1]
tests/qubit_cli/test_login_cli_command.py::test_the_drain_only_moves_the_free_tier_model_on_completion[terminal1-openrouter/some-model-openrouter/some-model]
tests/qubit_cli/test_login_cli_command.py::test_the_drain_only_moves_the_free_tier_model_on_completion[terminal2-qubit/welcome-qubit/welcome]
tests/qubit_cli/test_login_cli_command.py::test_a_precondition_prints_without_starting_a_thread
tests/qubit_cli/test_login_cli_command.py::test_ctrl_c_during_the_first_advance_prints_the_cancelled_copy
tests/qubit_cli/test_login_cli_command.py::test_the_handler_never_calls_input_and_uses_the_short_timeout
tests/qubit_cli/test_login_cli_command.py::test_the_drain_writes_to_the_console_captured_at_start
tests/qubit_cli/test_login_cli_command.py::test_the_live_tui_drain_prints_through_cprint_instead_of_the_captured_console
tests/qubit_cli/test_macos_tcc_anchor.py::TestDoctorCheck::test_never_crashes_on_exception
tests/qubit_cli/test_manual_serve_deferral.py::test_manual_deferral_survives_receipt_rotation[alive-serve]
tests/qubit_cli/test_manual_serve_deferral.py::test_manual_deferral_survives_receipt_rotation[alive-dashboard]
tests/qubit_cli/test_manual_serve_deferral.py::test_manual_deferral_survives_receipt_rotation[unknown-serve]
tests/qubit_cli/test_manual_serve_deferral.py::test_manual_deferral_survives_receipt_rotation[unknown-dashboard]
tests/qubit_cli/test_manual_serve_deferral.py::test_manual_deferral_survives_receipt_rotation[write-error-serve]
tests/qubit_cli/test_manual_serve_deferral.py::test_manual_deferral_survives_receipt_rotation[write-error-dashboard]
tests/qubit_cli/test_manual_serve_deferral.py::test_manual_deferral_survives_receipt_rotation[missing-identity-serve]
tests/qubit_cli/test_manual_serve_deferral.py::test_manual_deferral_survives_receipt_rotation[missing-identity-dashboard]
tests/qubit_cli/test_manual_serve_deferral.py::test_manual_deferral_survives_receipt_rotation[failed-unit-serve]
tests/qubit_cli/test_manual_serve_deferral.py::test_manual_deferral_survives_receipt_rotation[failed-unit-dashboard]
tests/qubit_cli/test_manual_serve_deferral.py::test_historical_manual_obligation_does_not_block_healthy_gateway[True-True-stale-True]
tests/qubit_cli/test_manual_serve_deferral.py::test_historical_manual_obligation_does_not_block_healthy_gateway[True-True-stale-None]
tests/qubit_cli/test_manual_serve_deferral.py::test_historical_manual_obligation_does_not_block_healthy_gateway[True-True-stale-False]
tests/qubit_cli/test_manual_serve_deferral.py::test_historical_manual_obligation_does_not_block_healthy_gateway[True-True-current-True]
tests/qubit_cli/test_manual_serve_deferral.py::test_historical_manual_obligation_does_not_block_healthy_gateway[True-True-current-None]
tests/qubit_cli/test_manual_serve_deferral.py::test_historical_manual_obligation_does_not_block_healthy_gateway[True-True-empty-True]
tests/qubit_cli/test_manual_serve_deferral.py::test_historical_manual_obligation_does_not_block_healthy_gateway[True-True-empty-None]
tests/qubit_cli/test_manual_serve_deferral.py::test_historical_manual_obligation_does_not_block_healthy_gateway[True-False-stale-True]
tests/qubit_cli/test_manual_serve_deferral.py::test_historical_manual_obligation_does_not_block_healthy_gateway[True-False-stale-None]
tests/qubit_cli/test_manual_serve_deferral.py::test_historical_manual_obligation_does_not_block_healthy_gateway[True-False-stale-False]
tests/qubit_cli/test_manual_serve_deferral.py::test_historical_manual_obligation_does_not_block_healthy_gateway[True-False-current-True]
tests/qubit_cli/test_manual_serve_deferral.py::test_historical_manual_obligation_does_not_block_healthy_gateway[True-False-current-None]
tests/qubit_cli/test_manual_serve_deferral.py::test_historical_manual_obligation_does_not_block_healthy_gateway[True-False-empty-True]
tests/qubit_cli/test_manual_serve_deferral.py::test_historical_manual_obligation_does_not_block_healthy_gateway[True-False-empty-None]
tests/qubit_cli/test_manual_serve_deferral.py::test_historical_manual_obligation_does_not_block_healthy_gateway[False-True-stale-True]
tests/qubit_cli/test_manual_serve_deferral.py::test_historical_manual_obligation_does_not_block_healthy_gateway[False-True-stale-None]
tests/qubit_cli/test_manual_serve_deferral.py::test_historical_manual_obligation_does_not_block_healthy_gateway[False-True-current-True]
tests/qubit_cli/test_manual_serve_deferral.py::test_historical_manual_obligation_does_not_block_healthy_gateway[False-True-current-None]
tests/qubit_cli/test_manual_serve_deferral.py::test_historical_manual_obligation_does_not_block_healthy_gateway[False-True-empty-True]
tests/qubit_cli/test_manual_serve_deferral.py::test_historical_manual_obligation_does_not_block_healthy_gateway[False-True-empty-None]
tests/qubit_cli/test_manual_serve_deferral.py::test_historical_manual_obligation_does_not_block_healthy_gateway[False-False-stale-True]
tests/qubit_cli/test_manual_serve_deferral.py::test_historical_manual_obligation_does_not_block_healthy_gateway[False-False-stale-None]
tests/qubit_cli/test_manual_serve_deferral.py::test_historical_manual_obligation_does_not_block_healthy_gateway[False-False-current-True]
tests/qubit_cli/test_manual_serve_deferral.py::test_historical_manual_obligation_does_not_block_healthy_gateway[False-False-current-None]
tests/qubit_cli/test_manual_serve_deferral.py::test_historical_manual_obligation_does_not_block_healthy_gateway[False-False-empty-True]
tests/qubit_cli/test_manual_serve_deferral.py::test_historical_manual_obligation_does_not_block_healthy_gateway[False-False-empty-None]
tests/qubit_cli/test_manual_serve_deferral.py::test_stamped_manual_only_history_has_no_gateway_obligation[True]
tests/qubit_cli/test_manual_serve_deferral.py::test_stamped_manual_only_history_has_no_gateway_obligation[False]
tests/qubit_cli/test_manual_serve_deferral.py::test_historical_retention_is_independent_of_plan_order[unsupported0-True]
tests/qubit_cli/test_manual_serve_deferral.py::test_historical_retention_is_independent_of_plan_order[unsupported0-False]
tests/qubit_cli/test_manual_serve_deferral.py::test_historical_retention_is_independent_of_plan_order[unsupported1-True]
tests/qubit_cli/test_manual_serve_deferral.py::test_historical_retention_is_independent_of_plan_order[unsupported1-False]
tests/qubit_cli/test_manual_serve_deferral.py::test_historical_retention_is_independent_of_plan_order[None-True]
tests/qubit_cli/test_manual_serve_deferral.py::test_historical_retention_is_independent_of_plan_order[None-False]
tests/qubit_cli/test_manual_serve_deferral.py::test_historical_retention_failure_warns_and_survives_rotation[current-mkdir]
tests/qubit_cli/test_manual_serve_deferral.py::test_historical_retention_failure_warns_and_survives_rotation[current-write]
tests/qubit_cli/test_manual_serve_deferral.py::test_historical_retention_failure_warns_and_survives_rotation[current-replace]
tests/qubit_cli/test_manual_serve_deferral.py::test_historical_retention_failure_warns_and_survives_rotation[stale-mkdir]
tests/qubit_cli/test_manual_serve_deferral.py::test_historical_retention_failure_warns_and_survives_rotation[stale-write]
tests/qubit_cli/test_manual_serve_deferral.py::test_historical_retention_failure_warns_and_survives_rotation[stale-replace]
tests/qubit_cli/test_manual_serve_deferral.py::test_unreadable_create_time_discharges_only_a_proven_dead_pid[False-serve]
tests/qubit_cli/test_manual_serve_deferral.py::test_unreadable_create_time_discharges_only_a_proven_dead_pid[False-dashboard]
tests/qubit_cli/test_manual_serve_deferral.py::test_unsaved_manual_restart_warning_survives_a_running_and_a_killed_update
tests/qubit_cli/test_mcp_catalog_env_boundary.py::test_catalog_rejects_undeclared_key_before_any_write_or_install
tests/qubit_cli/test_mcp_catalog_env_boundary.py::test_catalog_cannot_declare_reserved_control_key
tests/qubit_cli/test_mcp_catalog_env_boundary.py::test_catalog_non_secret_env_never_lands_in_env_file
tests/qubit_cli/test_mcp_dashboard_oauth.py::test_hosted_auth_start_returns_public_authorization_url
tests/qubit_cli/test_mcp_dashboard_oauth.py::test_hosted_callback_bypasses_gated_cookie_auth
tests/qubit_cli/test_mcp_dashboard_oauth.py::test_flow_status_does_not_expose_authorization_code
tests/qubit_cli/test_mcp_dashboard_oauth.py::test_cancel_marks_the_flow_terminal_for_a_retrying_worker
tests/qubit_cli/test_mcp_startup.py::test_prepare_agent_startup_backgrounds_blocking_mcp_for_non_gateway_agent_command
tests/qubit_cli/test_mcp_startup.py::test_prepare_agent_startup_installs_server_filter
tests/qubit_cli/test_mcp_startup.py::test_prepare_agent_startup_backgrounds_blocking_mcp_for_rl
tests/qubit_cli/test_memory_dependency_admission.py::test_setup_requires_dependencies_and_keeps_the_existing_union[False]
tests/qubit_cli/test_memory_dependency_admission.py::test_setup_requires_dependencies_and_keeps_the_existing_union[True]
tests/qubit_cli/test_minimax_oauth.py::test_generic_auth_status_dispatches_minimax_oauth
tests/qubit_cli/test_minimax_oauth.py::test_token_provider_raises_not_logged_in_when_state_missing
tests/qubit_cli/test_minimax_oauth.py::test_token_provider_quarantines_state_on_terminal_refresh
tests/qubit_cli/test_minimax_oauth.py::test_refresh_error_body_bounded_and_readable_with_real_client
tests/qubit_cli/test_model_cache_fallback_rows.py::test_codex_cold_fallback_retries_after_short_ttl
tests/qubit_cli/test_model_options_read_path_catalogs.py::test_degraded_provider_cannot_stall_the_open
tests/qubit_cli/test_model_options_read_path_catalogs.py::test_explicit_refresh_still_probes_providers
tests/qubit_cli/test_model_selection_guards.py::test_combined_selection_warning_merges_multiple
tests/qubit_cli/test_model_switch_configured_provider_routing.py::test_legacy_duplicate_keeps_its_own_declared_models
tests/qubit_cli/test_model_switch_custom_providers.py::test_picker_native_catalog_is_admitted_to_the_shared_model_cache
tests/qubit_cli/test_model_switch_custom_providers.py::test_discovered_models_auto_saved_to_cache
tests/qubit_cli/test_model_validation.py::TestCuratedModelsForProvider::test_live_catalog_projected_to_tuples_else_static_fallback
tests/qubit_cli/test_model_validation.py::TestCuratedModelsForProvider::test_stepfun_merges_live_and_static
tests/qubit_cli/test_model_validation.py::TestProviderModelIds::test_anthropic_provider_uses_configured_base_url_for_live_catalog
tests/qubit_cli/test_model_validation.py::TestProviderModelIds::test_custom_provider_passes_anthropic_mode_for_versioned_proxy_catalog
tests/qubit_cli/test_model_validation.py::TestFetchApiModels::test_probe_api_models_uses_copilot_catalog
tests/qubit_cli/test_model_validation.py::TestProbeApiModelsUserAgent::test_probe_sends_qubit_user_agent
tests/qubit_cli/test_model_validation.py::TestProbeApiModelsUserAgent::test_probe_user_agent_sent_without_api_key
tests/qubit_cli/test_models.py::TestFetchOpenRouterModels::test_falls_back_to_static_snapshot_on_fetch_failure
tests/qubit_cli/test_models.py::TestUnionWithPortalFreeRecommendations::test_adds_portal_free_model_missing_from_curated
tests/qubit_cli/test_models.py::TestUnionWithPortalPaidRecommendations::test_preserves_relative_order_of_new_paid_models
tests/qubit_cli/test_models.py::TestLocalOllamaModelDiscovery::test_native_tags_cache_expires
tests/qubit_cli/test_models.py::TestLocalOllamaModelDiscovery::test_validation_with_nonmatching_ollama_root_does_not_forward_config_headers
tests/qubit_cli/test_models.py::TestOpenRouterCatalogDiskCache::test_fresh_snapshot_serves_cold_process_without_network
tests/qubit_cli/test_models.py::TestOpenRouterCatalogDiskCache::test_expired_or_corrupt_snapshot_falls_through_to_fetch
tests/qubit_cli/test_models_catalog_late_plugin_provider.py::test_late_registered_provider_reaches_picker_catalog
tests/qubit_cli/test_models_detect_live_catalog.py::TestCurrentProviderCatalogWins::test_bare_name_resolves_to_current_providers_full_slug
tests/qubit_cli/test_models_detect_vendor_ownership.py::test_non_owned_id_still_remaps_to_keyed_aggregator[openai-codex-claude-opus-4.7]
tests/qubit_cli/test_models_detect_vendor_ownership.py::test_non_owned_id_still_remaps_to_keyed_aggregator[bedrock-deepseek-v4-pro]
tests/qubit_cli/test_models_dev_preferred_merge.py::TestMergeHelper::test_deepseek_picker_ignores_models_dev_retired_ids
tests/qubit_cli/test_multiplex_cli_cache_scope.py::test_deepinfra_catalog_is_fetched_with_each_profiles_key
tests/qubit_cli/test_multiplex_cli_cache_scope.py::test_nous_reasoning_caps_follow_each_profiles_portal
tests/qubit_cli/test_multiplex_cli_cache_scope.py::test_swr_refresh_runs_as_the_profile_that_spawned_it
tests/qubit_cli/test_multiplex_cli_cache_scope.py::test_model_catalog_in_process_copy_is_bound_to_its_cache_file
tests/qubit_cli/test_multiplex_cli_cache_scope.py::test_openrouter_curated_list_is_per_profile
tests/qubit_cli/test_multiplex_cli_cache_scope.py::test_banner_skills_are_the_routed_profiles
tests/qubit_cli/test_multiplex_cli_cache_scope.py::test_failed_guest_mint_only_suppresses_that_profile
tests/qubit_cli/test_multiplex_cli_cache_scope.py::test_active_skin_is_per_profile_and_leaves_launch_slot_alone
tests/qubit_cli/test_nous_account.py::test_fresh_account_payload_normalization[payload0-True]
tests/qubit_cli/test_nous_account.py::test_fresh_account_payload_normalization[payload1-False]
tests/qubit_cli/test_nous_account.py::test_fresh_account_payload_normalization[payload2-True]
tests/qubit_cli/test_nous_account.py::test_fresh_account_payload_normalization[payload3-False]
tests/qubit_cli/test_nous_account.py::test_pool_oauth_entry_force_fresh_uses_account_api
tests/qubit_cli/test_nous_account.py::test_member_spend_cap_exceeded_message
tests/qubit_cli/test_nous_account.py::test_member_spend_cap_exceeded_without_amounts
tests/qubit_cli/test_nous_picker_cached_only_key.py::test_picker_on_sale_union_reads_the_authenticated_prewarm
tests/qubit_cli/test_nous_picker_cached_only_key.py::test_anonymous_reads_still_resolve
tests/qubit_cli/test_nous_picker_on_sale.py::test_nous_fetch_flags_tool_less_rows_so_the_union_can_skip_them
tests/qubit_cli/test_nous_picker_on_sale.py::test_gui_picker_paid_tier_shows_on_sale_model
tests/qubit_cli/test_nous_picker_on_sale.py::test_cli_setup_flow_and_gui_picker_agree
tests/qubit_cli/test_nous_picker_on_sale.py::test_post_login_picker_paid_tier_shows_on_sale_model
tests/qubit_cli/test_nous_picker_on_sale.py::test_post_login_picker_org_policy_still_narrows_on_sale_models
tests/qubit_cli/test_nous_picker_on_sale.py::test_all_three_paid_pickers_agree
tests/qubit_cli/test_nous_picker_on_sale.py::test_skips_generation_models_on_sale
tests/qubit_cli/test_nous_picker_pricing_cached_only.py::test_nous_picker_model_ids_reads_pricing_cache_only
tests/qubit_cli/test_nous_policy_filter.py::TestNousPolicyAllowedIds::test_returns_the_authenticated_catalog_keys
tests/qubit_cli/test_nous_policy_surfaces.py::TestLoginNous::test_hidden_model_is_not_offered
tests/qubit_cli/test_nous_policy_surfaces.py::TestLoginNous::test_unrestricted_org_sees_the_full_curated_list
tests/qubit_cli/test_nous_policy_surfaces.py::TestModelSwitchPicker::test_hidden_model_is_filtered
tests/qubit_cli/test_nous_policy_surfaces.py::TestModelSwitchPicker::test_unrestricted_org_keeps_both
tests/qubit_cli/test_nous_policy_surfaces.py::TestRecommendedDefaultEndpoint::test_hidden_model_is_never_the_silent_default
tests/qubit_cli/test_nous_policy_surfaces.py::TestRecommendedDefaultEndpoint::test_unrestricted_org_is_unaffected
tests/qubit_cli/test_nous_policy_surfaces.py::TestAuxiliaryFastModel::test_hidden_model_is_not_selected
tests/qubit_cli/test_nous_policy_surfaces.py::TestPolicyNoticeIsShown::test_login_prints_it
tests/qubit_cli/test_nous_policy_surfaces.py::TestAuxFallbackRespectsPolicy::test_blocked_recommendation_is_not_used
tests/qubit_cli/test_nous_policy_surfaces.py::TestAuxFallbackRespectsPolicy::test_allowed_recommendation_still_used
tests/qubit_cli/test_nous_policy_surfaces.py::TestAuxFallbackRespectsPolicy::test_ungoverned_org_is_unaffected
tests/qubit_cli/test_nous_session_validity.py::test_local_status_not_logged_in_after_terminal_quarantine
tests/qubit_cli/test_nous_session_validity.py::test_local_status_repeated_polling_never_uses_live_auth
tests/qubit_cli/test_npm_engine.py::test_retired_retry_handoffs_without_provisioning[False-unrelated failure]
tests/qubit_cli/test_oauth_status_pool_observation.py::test_status_snapshot_does_not_refresh_or_bench_an_expiring_pool_entry
tests/qubit_cli/test_oauth_status_pool_observation.py::test_read_only_resolver_never_probes_or_mutates_an_exhausted_pool
tests/qubit_cli/test_oauth_status_pool_observation.py::test_status_snapshot_never_adopts_codex_cli_tokens
tests/qubit_cli/test_oneshot_fallback.py::TestResolveRuntimeWithFallback::test_auth_error_walks_chain_in_order_and_re_raises_primary
tests/qubit_cli/test_oneshot_fallback.py::TestResolveRuntimeWithFallback::test_misconfiguration_is_never_rerouted
tests/qubit_cli/test_oneshot_fallback.py::test_run_agent_falls_back_when_primary_resolution_raises_auth_error
tests/qubit_cli/test_oneshot_resume.py::TestRunOneshotForwardsResume::test_resume_kwarg_reaches_run_agent
tests/qubit_cli/test_openai_listing_authority.py::TestOfficialHostAuthority::test_absent_model_rejected_on_official_hosts[https://api.openai.com/v1]
tests/qubit_cli/test_openai_listing_authority.py::TestOfficialHostAuthority::test_absent_model_rejected_on_official_hosts[https://us.api.openai.com/v1]
tests/qubit_cli/test_openai_listing_authority.py::TestOfficialHostAuthority::test_absent_model_rejected_on_official_hosts[https://eu.api.openai.com/v1]
tests/qubit_cli/test_orphan_desktop_serve_reap.py::test_reap_only_kills_ppid1_local_serves
tests/qubit_cli/test_orphan_desktop_serve_reap.py::test_reap_passes_child_pid_exclude_to_scan
tests/qubit_cli/test_orphan_desktop_serve_reap.py::test_reap_spare_lock_owned_ssh_remote_backend_of_foreign_client
tests/qubit_cli/test_orphan_desktop_serve_reap.py::test_reap_age_boundary_makes_180_second_orphan_eligible
tests/qubit_cli/test_orphan_desktop_serve_reap.py::test_reap_kills_descendants_of_killed_roots_but_spares_a_failed_roots_subtree
tests/qubit_cli/test_overlay_commands.py::test_unavailable_backend_prints_reason_not_traceback
tests/qubit_cli/test_pending_supervisor_recovery.py::test_pending_launchd_requires_complete_supervision[None]
tests/qubit_cli/test_picker_key_cmd_discovery.py::test_picker_command_auth_is_lazy_and_credential_scoped[static0-providers]
tests/qubit_cli/test_picker_key_cmd_discovery.py::test_picker_command_auth_is_lazy_and_credential_scoped[static0-custom_providers]
tests/qubit_cli/test_picker_key_cmd_discovery.py::test_picker_command_auth_is_lazy_and_credential_scoped[static1-providers]
tests/qubit_cli/test_picker_key_cmd_discovery.py::test_picker_command_auth_is_lazy_and_credential_scoped[static1-custom_providers]
tests/qubit_cli/test_picker_key_cmd_discovery.py::test_picker_command_auth_is_lazy_and_credential_scoped[static2-providers]
tests/qubit_cli/test_picker_key_cmd_discovery.py::test_picker_command_auth_is_lazy_and_credential_scoped[static2-custom_providers]
tests/qubit_cli/test_picker_probe_budget_decoupling.py::test_full_budget_keeps_slow_endpoint_models
tests/qubit_cli/test_platform_actions.py::TestPluginContextWiring::test_ctx_platform_actions_bound_to_plugin_id
tests/qubit_cli/test_plugin_aux_tasks_dashboard.py::test_plugin_discovery_failure_leaves_builtins_working
tests/qubit_cli/test_plugin_catalog_presence.py::test_onboarding_rows_are_curated_entries_this_os_runs
tests/qubit_cli/test_plugin_catalog_presence.py::test_app_state_comes_from_the_pinned_declaration
tests/qubit_cli/test_plugin_config_state_bridge.py::test_config_round_trip_uses_canonical_settings_namespace
tests/qubit_cli/test_plugin_config_state_bridge.py::test_config_reads_legacy_config_namespace_until_canonical_value_is_set
tests/qubit_cli/test_plugin_config_state_bridge.py::test_config_rejects_global_cross_plugin_and_traversal_paths[../../security.approval_mode]
tests/qubit_cli/test_plugin_config_state_bridge.py::test_config_rejects_global_cross_plugin_and_traversal_paths[..\\..\\model.provider]
tests/qubit_cli/test_plugin_config_state_bridge.py::test_config_rejects_global_cross_plugin_and_traversal_paths[security.approval_mode]
tests/qubit_cli/test_plugin_config_state_bridge.py::test_config_rejects_global_cross_plugin_and_traversal_paths[model.provider]
tests/qubit_cli/test_plugin_config_state_bridge.py::test_config_rejects_global_cross_plugin_and_traversal_paths[plugins.entries.other.settings.token]
tests/qubit_cli/test_plugin_config_state_bridge.py::test_config_rejects_global_cross_plugin_and_traversal_paths[settings.endpoint]
tests/qubit_cli/test_plugin_config_state_bridge.py::test_config_rejects_global_cross_plugin_and_traversal_paths[]
tests/qubit_cli/test_plugin_config_state_bridge.py::test_config_parse_failure_is_not_silently_overwritten
tests/qubit_cli/test_plugin_config_state_bridge.py::test_concurrent_config_writes_do_not_drop_sibling_settings
tests/qubit_cli/test_plugin_config_state_bridge.py::test_config_and_state_follow_context_local_profile_scope
tests/qubit_cli/test_plugin_config_state_bridge.py::test_fixture_plugin_round_trips_bridge_during_real_discovery
tests/qubit_cli/test_plugin_dependency_consent.py::test_publish_assume_consent_skips_replacement_veto
tests/qubit_cli/test_plugin_event_bus.py::test_pending_budget_drops_new_event_without_blocking
tests/qubit_cli/test_plugin_host.py::test_hosted_memory_provider_stays_live_across_a_host_crash
tests/qubit_cli/test_plugin_ownership_ledger.py::test_shared_entrypoint_module_uses_the_active_profile_scope
tests/qubit_cli/test_plugin_ownership_ledger.py::test_provider_overlay_switches_profiles_and_reveals_fresh_global_fallback
tests/qubit_cli/test_plugin_ownership_ledger.py::test_direct_plugin_platform_registration_infers_immutable_scope
tests/qubit_cli/test_plugin_ownership_ledger.py::test_same_name_tool_and_platform_are_isolated_by_qubit_home
tests/qubit_cli/test_plugin_packs.py::test_export_round_trips_through_parse
tests/qubit_cli/test_plugin_presence_by_name.py::test_rows_in_the_order_asked_without_removed_or_other_os_names
tests/qubit_cli/test_plugin_presence_by_name.py::test_row_shape_state_and_disclosure
tests/qubit_cli/test_plugin_presence_by_name.py::test_plugins_manage_presence_over_the_wire
tests/qubit_cli/test_plugin_private_repo_auth.py::test_private_clone_falls_back_to_auth_after_credential_required_error
tests/qubit_cli/test_plugin_private_repo_auth.py::test_ref_fetch_and_update_pull_attach_credential_only_after_anonymous_refusal[refused-fetch]
tests/qubit_cli/test_plugin_private_repo_auth.py::test_ref_fetch_and_update_pull_attach_credential_only_after_anonymous_refusal[refused-pull]
tests/qubit_cli/test_plugin_private_repo_auth.py::test_ref_fetch_and_update_pull_attach_credential_only_after_anonymous_refusal[not_found-fetch]
tests/qubit_cli/test_plugin_provider_requests.py::test_token_attached_for_allowlisted_origin_and_never_returned
tests/qubit_cli/test_plugin_provider_requests.py::test_disallowed_origin_and_redirect_never_carry_the_token
tests/qubit_cli/test_plugin_provider_requests.py::test_not_signed_in_names_the_sign_in_command
tests/qubit_cli/test_plugin_runtime_disable_gate.py::TestBundledPluginAssetGate::test_bundled_asset_returns_404_when_disabled
tests/qubit_cli/test_plugins.py::TestForceReloadSymmetry::test_worker_finishing_at_timeout_does_not_leave_phantom_abandoned_entry
tests/qubit_cli/test_plugins.py::TestForceReloadSymmetry::test_force_reload_of_one_profile_does_not_orphan_another
tests/qubit_cli/test_plugins.py::TestGetPreVerifyContinueMessage::test_forwards_scope_signals_to_hooks
tests/qubit_cli/test_plugins.py::TestThreadToolWhitelist::test_whitelist_is_thread_local
tests/qubit_cli/test_plugins.py::TestPluginCommandResultResolution::test_running_loop_timeout_does_not_hang_forever
tests/qubit_cli/test_plugins_cadence.py::test_auto_apply_selects_only_updateable_git_and_persists_receipt[False]
tests/qubit_cli/test_plugins_cadence.py::test_auto_apply_selects_only_updateable_git_and_persists_receipt[True]
tests/qubit_cli/test_plugins_cadence.py::test_housekeeping_runs_real_cadence_and_backs_off[False]
tests/qubit_cli/test_plugins_cadence.py::test_housekeeping_runs_real_cadence_and_backs_off[True]
tests/qubit_cli/test_plugins_cmd.py::test_portable_install_gate_accepts_present_app_and_refuses_missing
tests/qubit_cli/test_plugins_cmd_list.py::test_cmd_list_plain_compact_output
tests/qubit_cli/test_plugins_hub_live_catalog.py::test_hub_rebuild_and_plugins_list_resolve_the_kill_list_once
tests/qubit_cli/test_plugins_hub_perf_guard.py::test_plugins_hub_cold_cache_schedules_background_probe
tests/qubit_cli/test_plugins_hub_perf_guard.py::test_plugins_hub_uses_cached_failed_check_fn_verdict
tests/qubit_cli/test_plugins_hub_perf_guard.py::test_plugins_hub_route_builds_catalog_annotations_off_event_loop
tests/qubit_cli/test_plugins_hub_perf_guard.py::test_plugin_install_endpoint_invalidates_hub_cache
tests/qubit_cli/test_plugins_middleware_chain.py::test_request_middleware_rewrites_compose[llm_request]
tests/qubit_cli/test_plugins_middleware_chain.py::test_request_middleware_rewrites_compose[tool_request]
tests/qubit_cli/test_plugins_middleware_chain.py::test_request_middleware_in_place_mutation_does_not_leak[llm_request]
tests/qubit_cli/test_plugins_middleware_chain.py::test_request_middleware_in_place_mutation_does_not_leak[tool_request]
tests/qubit_cli/test_pricing_cache_auth_key.py::test_one_token_does_not_receive_another_tokens_catalog
tests/qubit_cli/test_pricing_cache_auth_key.py::test_anonymous_and_authenticated_reads_are_separate
tests/qubit_cli/test_pricing_cache_auth_key.py::test_repeated_read_still_hits_the_cache[sk-test]
tests/qubit_cli/test_pricing_cache_auth_key.py::test_repeated_read_still_hits_the_cache[]
tests/qubit_cli/test_pricing_cache_auth_key.py::test_force_refresh_replaces_only_its_own_entry
tests/qubit_cli/test_pricing_cache_auth_key.py::TestPeekCachedPricing::test_accepts_a_v1_suffixed_url
tests/qubit_cli/test_pricing_cache_auth_key.py::TestPeekCachedPricing::test_prefers_the_authenticated_catalog
tests/qubit_cli/test_pricing_cache_auth_key.py::TestPeekCachedPricing::test_falls_back_to_the_anonymous_catalog
tests/qubit_cli/test_pricing_cache_auth_key.py::TestNousCatalogExpiry::test_entry_expires_so_a_policy_change_is_picked_up
tests/qubit_cli/test_pricing_cache_auth_key.py::TestNousCatalogExpiry::test_no_ttl_keeps_the_entry_indefinitely
tests/qubit_cli/test_pricing_cache_auth_key.py::TestNousCatalogExpiry::test_peek_prefers_the_newest_credential
tests/qubit_cli/test_process_dock.py::test_dock_paints_processes_under_agents_and_retires_finished_rows
tests/qubit_cli/test_process_identity.py::test_updater_reaps_ledger_proven_orphans
tests/qubit_cli/test_profile_clone_channels.py::test_clone_strips_every_channel_credential_but_keeps_model_and_tool_keys
tests/qubit_cli/test_profile_clone_channels.py::test_clone_all_drops_pairing_and_platform_state
tests/qubit_cli/test_profile_delete_log_handlers.py::test_delete_profile_releases_every_routed_handler_for_that_home[explicit-routing]
tests/qubit_cli/test_profile_delete_log_handlers.py::test_delete_profile_releases_every_routed_handler_for_that_home[setup_logging-adoption]
tests/qubit_cli/test_profile_export_credentials.py::TestCredentialExclusion::test_named_profile_export_excludes_auth
tests/qubit_cli/test_profile_export_credentials.py::TestCredentialExclusion::test_named_export_ships_no_credential_store_or_copy_of_one
tests/qubit_cli/test_profile_export_credentials.py::TestExportSecretScrub::test_named_profile_export_redacts_secrets_in_text
tests/qubit_cli/test_profile_export_credentials.py::TestExportSecretScrub::test_export_redacts_through_symlink_without_touching_source
tests/qubit_cli/test_profile_export_special_files.py::test_named_profile_export_survives_unix_socket
tests/qubit_cli/test_profile_export_special_files.py::test_default_profile_export_survives_unix_socket
tests/qubit_cli/test_profile_model_seed.py::test_model_seed_keeps_runtime_endpoint_selection[providers0-legacy0]
tests/qubit_cli/test_profile_model_seed.py::test_model_seed_keeps_runtime_endpoint_selection[providers1-legacy1]
tests/qubit_cli/test_profile_service_cleanup_scopes.py::test_cleanup_handles_the_system_scope_unit[0]
tests/qubit_cli/test_profile_service_cleanup_scopes.py::test_cleanup_handles_the_system_scope_unit[1000]
```

## Appendix B — chunk 4 node ids that failed in the combined run (all passed alone)

```text
tests/gateway/test_api_authority_retry.py::test_api_busy_fifo_retries_observe_one_durable_result
tests/gateway/test_claim_reopen_off_loop.py::test_reopening_a_finalized_local_row_behind_a_contended_writer_keeps_the_loop_live
tests/gateway/test_cron_fire_webhook.py::test_accepted_response_waits_for_durable_admission
tests/gateway/test_cron_fire_webhook.py::test_fire_does_not_require_api_server_key
tests/gateway/test_cron_fire_webhook.py::test_sync_verifier_runs_off_the_event_loop
tests/gateway/test_cron_fire_webhook.py::test_async_verifier_is_awaited
tests/gateway/test_cron_fire_webhook.py::test_estop_refuses_fire_before_admission_and_lifts_on_resume
tests/gateway/test_discord_attachment_download.py::TestCacheDiscordImage::test_falls_back_to_url_when_bytes_validator_rejects
tests/gateway/test_discord_attachment_download.py::TestCacheDiscordAudio::test_prefers_att_read_over_url
tests/gateway/test_discord_attachment_download.py::TestCacheDiscordDocument::test_fallback_blocked_by_ssrf_guard
tests/gateway/test_discord_attachment_download.py::TestHandleMessageUsesAuthenticatedRead::test_image_downloads_via_att_read_not_url
tests/gateway/test_discord_reactions.py::test_process_message_background_adds_and_swaps_reactions
tests/gateway/test_discord_send.py::test_send_video_uses_path_based_files_kwarg
tests/gateway/test_discord_send.py::test_send_video_fails_loud_when_message_has_no_attachments
tests/gateway/test_discord_send.py::test_send_file_attachment_forum_uses_files_kwarg
tests/gateway/test_discord_voice_stale_binding.py::test_speech_buffered_across_a_rebind_is_not_stamped_with_the_new_binding[700-reaching1]
tests/gateway/test_discord_voice_stale_binding.py::test_speech_spanning_a_rebind_reaches_the_new_channel_without_the_earlier_samples
tests/gateway/test_discord_voice_stale_binding.py::test_leave_after_a_rebind_does_not_flush_old_speech_into_the_new_channel[700-reaching1]
tests/gateway/test_discord_voice_stale_binding.py::test_a_programmatic_join_while_connected_applies_the_requested_binding[same-text-channel]
tests/gateway/test_host_gateway_lock_refusal.py::test_a_replace_unit_that_replaced_nothing_is_still_refused_when_it_loses_the_lock
tests/gateway/test_host_rendezvous.py::test_two_homes_take_their_own_per_home_lock_but_only_one_host_lock
```

## Appendix C — artifacts

Raw logs and per-test status files are in `/tmp/opencode/ready-matrix/`:
`chunk1.log`, `chunk2.log`, `chunk3q.log`, `chunk3.status`, `chunk3v.log`,
`chunk4.log`, `chunk4.status`, `rerun3.log`, `rerun3.status`, `rerun4.log`, `rerun4.status`.
These are host-local and not committed.
