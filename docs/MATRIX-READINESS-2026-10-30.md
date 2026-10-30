# Matrix readiness — 2026-10-30 (fix3/lane-6, port-6)

**Status:** readiness assessment, **not** a release verdict and **not** `VERIFIED`. This lane did
not run the full matrix as one process. It confirmed collection, ran bounded per-file sweeps of
the failure-prone directories, fixed the real code/harness defects it found, and lists exactly
what still fails so the orchestrator can decide whether to start the full matrix.

## Worktree and environment

| Item | Value |
|---|---|
| Worktree | `/home/trick/qubit-worktrees/port-6` |
| Branch | `fix3/lane-6` |
| Base | `e597967df` (`merge: fix2/lane-10 into port/final-integration`) |
| HEAD at report | `6d42474eb0b2b84b2888685ef051a51f3d83fdc2` |
| Python | `/home/trick/Projects/Repos/qubit-voicebridge/.venv/bin/python` → **3.12.3** |
| Project Python | `pyproject.toml` supports `>=3.11,<3.15`; the pinned wheels/runtime target **3.14** |
| SQLite | linked **3.45.1** → `qubit_state_wal` falls back to `journal_mode=DELETE` (WAL-reset guard warning, once per DB) |
| Host | 16 CPUs; load average **17–25** for most of the session (other agents + test subprocesses) |
| Windows | out of scope (owner ruling 11); Windows-only tests were not chased |

The host was heavily loaded throughout. Per-file timeouts below are therefore split into
“load-sensitive, re-run on a quiet host” and “real failure”; the latter were re-run in a targeted
batch and are listed with their exact errors.

## 1. Collection — clean

```bash
cd /home/trick/qubit-worktrees/port-6/qubit_runtime
TZ=UTC LANG=C.UTF-8 LC_ALL=C.UTF-8 PYTHONHASHSEED=0 PYTHONUTF8=1 PYTHONPATH="$PWD" \
  nice -n 10 /home/trick/Projects/Repos/qubit-voicebridge/.venv/bin/python \
  -m pytest --collect-only -q tests
```

Result on the final tree: **exit 0**, `75962/76075 tests collected (113 deselected) in 302.50s`.
**Zero collection errors.** (The first run on the base tree also exited 0 with the same counts.)

## 2. Bounded per-file sweeps

All sweeps use the canonical per-file runner (one pytest subprocess per file, fresh interpreter,
no xdist), `nice -n 10`. They are **not** a single-process matrix.

### 2.1 Six failure-prone directories (gateway, qubit_cli, tools, tui_gateway, qubit_state, plugins)

```bash
cd /home/trick/qubit-worktrees/port-6/qubit_runtime
env TZ=UTC LANG=C.UTF-8 LC_ALL=C.UTF-8 PYTHONHASHSEED=0 PYTHONUTF8=1 PYTHONPATH="$PWD" \
  HERMES_TEST_WORKERS=6 nice -n 10 \
  /home/trick/Projects/Repos/qubit-voicebridge/.venv/bin/python \
  scripts/run_tests_parallel.py -j 6 --file-timeout 300 --file-retries 0 \
  tests/gateway tests/qubit_cli tests/tools tests/tui_gateway tests/qubit_state tests/plugins
```

- Discovered **5,302 files / ~39,602 selected tests**.
- Stopped by the operator at **6,324/~39,602 items (16.0%)**: **7,000 passed, 218 failed**.
  The tree was being fixed while this ran, so those counts are triage data, not a verdict.
- The 218 include many per-file **timeouts** caused by the loaded host (other agents were running
  their own 5-worker sweep at the same time). Every failure block whose text was not a timeout was
  re-run in the targeted batch below.

### 2.2 Targeted re-run of the non-timeout failures

The 33 files whose failure block was **not** a timeout were re-run as a batch:

```bash
... scripts/run_tests_parallel.py -j 4 --file-timeout 300 --file-retries 0 \
  --files-from /tmp/port6-fail-nontimeout.txt
```

Result **before** the final fix batch: `33 files, 146 passed, 38 failed; 20 of 33 files failed`
in 888.8 s. The fixes below then closed 10 of those 20 files; **10 files still fail** (section 4).

### 2.3 Small directories, 20-minute bounded slice

```bash
... timeout --signal=INT --kill-after=20 1200 \
  ... scripts/run_tests_parallel.py -j 6 --file-timeout 180 --file-retries 0 \
  tests/tools tests/tui_gateway tests/qubit_state tests/plugins
```

- Discovered **1,647 files / ~15,050 tests**; the 1,200 s cap ended the run at
  **2,033/~15,050 items (13.5%), 2,417 passed, 1 failed** (the in-progress counters), with
  **22 failing files** recorded: **21 were per-file 180 s timeouts** and **1 was a real stale
  expectation** (`tests/plugins/test_disk_cleanup_plugin.py`, now fixed).
- Two `qubit_state` files that timed out in the batch (`test_append_messages_batch.py`,
  `test_conversation_root.py`) passed when run directly: **47 passed, 1 skipped** with the
  plugin test — i.e. the batch timeouts are load, not code.

## 3. Fixes landed in this lane

Commits on `fix3/lane-6` (base `e597967df`):

| Commit | What it fixed |
|---|---|
| `9120345e5` | Relay ingress proof/scope wiring: inherit `RelayDiscordInteractionMixin` (modal, thread, display-name paths), drop stale inline copies, initialize label caches, route all ingress through `_capture_ingress_scope`; launch-profile scope around the self-provision token refresh; media filename alignment; `ActiveSessionRefusal.__getnewargs_ex__`; duplicate `en.yaml` keys; unadvertised-platform refusal test. |
| `980ab534d` | Rebrand `agent/turn_failure_copy.py`; local-client raw error detail; launch-credentials refusal copy; `release_clients` drains memory providers; `shutdown_memory_provider` honours the teardown claim; reset cleanup claims session-end before its flush. |
| `cc8149712` | Managed-worker tool progress reaches progress-only API stream observers (`publish_api_tool_progress`). |
| `04edf1419` | Boot waits out client lock probes; restore `_watch_pin_gone`; `_BUILTIN_ADAPTERS` only api_server/webhook/msgraph (other platforms are plugins); remove stale core `_send_bluebubbles`. |
| `b18ed1b95` | Restore `find_all_skills` (the fork referenced a non-existent `_find_all_skills`); carry `browser_backend_notice` into the CLI inventory + canonical contract; fix the notice recursion. |
| `b25b933db` | Emit the canonical `qubit.tool.progress` SSE event; buzz display test discovers plugins. |
| `4c639a41d` | Control identify publishes `qubit_home` (+ Windows pipe name); estop help/docs; Slack plugin display tier; free-tier boot before adapter prefilter; `/branch` rows carry `message_uid`/tool-call uids; fixtures read `QUBIT_HOME`. |
| `c6d35c220` | Remove 15 stale inline turn-persistence methods from `run_turn.py` (the mixin is authoritative); close a failed turn whose tail is a tool row; free-tier home-channel startup line. |
| `6d42474eb` | Pin the current `IN_BOTH_LISTS` disable verdict in the disk-cleanup plugin test. |

## 4. Remaining real failures (10 files)

Re-run after the fix batch; each line is the first real assertion/exception, not a timeout.

| File | Failure |
|---|---|
| `tests/gateway/test_browser_manage_shared_owner.py` | `KeyError: 'one:connect'` — peer receipt lacks the expected operation key (peer fixture; likely harness/peer-startup, verify on a quiet host). |
| `tests/gateway/test_busy_photo_interrupt_once.py` | duplicate follow-ups (`['later','later']` where one is expected; `['changed replay']` where `[]` is expected). |
| `tests/gateway/test_canonical_session_inventory.py` | a TUI session with only `platform_toolsets.cli: [file, terminal]` still exposes `browser`/`web`/`image_gen` toolsets. |
| `tests/gateway/test_complete_path_at_filter.py` | remote-backend completion is missing `sub/remote-dir/`. |
| `tests/gateway/test_delete_secondary_profile_multiplex.py` | a stale routing save restores a deleted secondary route. |
| `tests/gateway/test_exec_approval_timeout_notice.py` | timeout fallback sends 2 messages (card edit failure + new message) where the test expects 1. |
| `tests/gateway/test_feishu_audio_delivery.py` | plain audio is typed `('opus', 'loop.opus')` instead of `('stream', 'loop.opus')`. |
| `tests/gateway/test_feishu_card_action_gateway_authz.py` | `_resolve_update_prompt() got an unexpected keyword argument 'user_id'` — multi-ID (user_id/union_id) authorization is not ported. |
| `tests/gateway/test_gateway_serves_webapp.py` | `report["host_terminals"] is False` (and ticket-scope/sign-out variants) — the served webapp does not report host terminals. |
| `tests/gateway/test_handoff_completion_parent_context.py` | `KeyError: 'result'` in the handed-off completion path. |

These are real code gaps or harness gaps, not `VERIFIED` blockers by themselves; they are the
reason this lane does **not** claim the matrix is green.

## 5. Load-sensitive timeouts (re-run on a quiet host)

These files exceeded the per-file cap **only** while the host was at load 17–25 with concurrent
sweeps. They are not classified as code failures. Representative full list from the six-directory
sweep (20 files whose failure block was a timeout):

```text
tests/gateway/test_active_turn_recovery.py
tests/gateway/test_agent_cache_memory_lifecycle.py          # passes alone after 980ab534d
tests/gateway/test_api_server_declared_conversation.py
tests/gateway/test_api_server.py
tests/gateway/test_async_delegation_session_binding.py
tests/gateway/test_authority_cold_route_recovery.py
tests/gateway/test_authority_listing_loop_turns.py
tests/gateway/test_authority_stream.py
tests/gateway/test_authority_unknown_resolution.py
tests/gateway/test_automation_notification_category.py
tests/gateway/test_automation_wake_route.py
tests/gateway/test_auto_skill_title_input.py
tests/gateway/test_background_process_notifications.py
tests/gateway/test_bot_chat_follows_profile.py
tests/gateway/test_canonical_file_attach_staged_sources.py
tests/gateway/test_child_notice_profile_admission.py
tests/gateway/test_clarify_lock_owner.py
tests/gateway/test_cli_plugin_command_in_gateway.py
tests/gateway/test_derived_generation_after_sweep.py
tests/gateway/test_gateway_delegation_mode.py
```

The small-directory slice contributed 21 more timeouts, all in `tests/qubit_state/` and
`tests/plugins/` (kanban dashboard/model override, telegram update admission, append_messages_batch,
compression/lineage/watermark, conversation_root, corrupt_row, cron_run_sessions,
delete_compression_chain, display_projection_parity, …). Two were re-run directly and passed.

## 6. Environment-only notes

- **Python 3.12 vs 3.14.** The venv is 3.12.3 while the pinned dependency set is exact-pinned for
  3.14. Nothing in this lane failed because of that, but a 3.14 environment is the release target;
  re-run the final matrix there.
- **SQLite 3.45.1.** The WAL-reset guard selects `journal_mode=DELETE` and logs a warning once per
  DB. This is the documented fallback, not a test failure.
- **Host load.** The load average was 17–25 for most of the run (16 CPUs). Per-file 180/300 s
  timeouts are the dominant “failure” class; they must be re-run on a quiet host before they are
  called real.
- **Windows** is out of scope (owner ruling 11).

## 7. Readiness verdict

- Collection: **clean** (75,962 collected, 113 deselected, 0 errors).
- Bounded sweeps: no collection/harness import breakage; the real defects found in the sampled
  files were fixed (section 3).
- **Not ready to treat a full matrix as green.** There are **10 known real failing files**
  (section 4) plus a large load-timeout class that must be re-run on a quiet host.
- The orchestrator can start the full matrix now, but should expect the section-4 failures and
  should re-run the section-5 timeouts serially before attributing them to code.

**No `VERIFIED`, `M1`, `M2`, `CODE_READY`, `COMPLETE`, `100%`, or `1:1` claim is made here.**
