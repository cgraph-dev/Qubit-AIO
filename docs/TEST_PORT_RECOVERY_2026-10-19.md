# Test-port recovery: lane B + lane A — honest account

Base: `main` @ `419fb3257` (branches were rebased from `b47686ff2`, still an ancestor).
Integration branch: **`test-port/integration`** (local, 2 commits, no force-push).

## Method

Per-file isolated subprocess run (project convention: `scripts/run_tests_parallel.py`),
`PYTHONPATH` cleared, `TZ=UTC`, Python = the repo's `.venv`. Tests run with cwd = the
lane's runtime root. Worktree `.venv` was materialised as a hardlink copy of the repo
`.venv` (a symlink caused `home_io_guard` to see a foreign interpreter prefix).

**Env artifacts that were NOT failures and are called out as such:**
- `QUBIT_HOME` was set to the session runtime root `/home/trick/.local/state/qubit-voicebridge/runtime`.
  `qubit_runtime/tests/conftest.py` guards a pre-sandbox *custom* `QUBIT_HOME` as a real root,
  so every test that touched a path under it died with
  `AssertionError: TEST BUG: file I/O against the REAL hermes home`. This produced **20 false
  failures in lane B**. Unsetting `QUBIT_HOME` clears all of them. This is my shell's env, not a
  test defect.
- `test_detached_loop_keepalive.py` "timed out" only because my runner's 300 s per-file budget
  covered 6 concurrent slow files; run alone it is **9 passed in 25.6 s**.
- A relative `.venv/bin/python` in the runner resolved against the wrong cwd for one run (3
  consecutive harness failures). Fixed with absolute paths; unrelated to the tests.

## Counts (reproducible)

| lane | test files | PASS (correct env) | non-passing |
|------|-----------:|-------------------:|-------------|
| B (`qubit_runtime/tests/`) | 98 | **97** | 1 collection error |
| A (`tests/`) | 64 test files (133 files incl. support) | **62** | 2 genuine failures |

- Lane B raw isolated runs: `laneB_iso.json` = 76 PASS / 20 FAIL / 1 COLLECT_ERROR / 1 TIMEOUT
  (all 20 FAIL = the `QUBIT_HOME` artifact above); `laneB_iso2.json` (QUBIT_HOME unset) =
  96 PASS / 1 COLLECT_ERROR / 1 TIMEOUT. The TIMEOUT re-runs green alone → **97**.
- Lane A: `laneA_iso2.json` = 62 PASS / 45 FAIL / 26 COLLECT_ERROR over 133 files. Of the 133,
  only **64 are `test_*.py`**; 62 pass and the other 2 are the genuine failures below. The 69
  remainder are *support modules* (conftests, fixtures, `waiting.py`) that contain no test
  functions — pytest reports "no tests ran" for them, which is not a failure.

## The 116-vs-98 reconciliation (lane B)

**98 is true. 116 is false.** `git diff --name-only main...test/port-laneb-files` returns exactly
**98** added files, all under `qubit_runtime/tests/`, all `test_*.py`. No deletions. The "116" in
the branch's own claim does not correspond to any git query on the branch — most likely an
in-flight count before the last two batches were rebased/truncated. The commit messages themselves
sum to 18 + 68 + 8 + 4 = **98**.

## Deliberately NOT landed — by category

### GENUINE ASSERTION FAILURE (real bugs the ported tests caught) — 2 files
1. **`tests/gateway/test_admission_results.py`** (4 of 6 tests fail) —
   `TypeError: FanoutTransport.write() got an unexpected keyword argument 'overflow'`
   at `qubit_runtime/gateway/session_events.py:64`.
   Evidence: on **current main**, the caller `session_events.py:64` does
   `self.fanout.write(frame, overflow=overflow)`, but every `FanoutTransport.write`
   in `qubit_runtime/tui_gateway/transport.py` (lines 41/108/255/301) is
   `def write(self, obj: dict) -> bool`. `session_events.py` was added wholesale by the port
   commit `f0a32e596`; the `transport.py` it paired with has no matching signature. This breaks
   the live session-event publish path (any session emitting an event on a fanout with a detached
   peer raises). **Production bug, not a test defect.**
2. **`tests/gateway/test_session_bot_target_lifecycle.py`** (1 of 1 fails) —
   `gateway.session_local_recovery._ReceiptRefused: storage_unavailable`, rooted at
   `_checked_entry` → `ValueError: stored identity mismatch`
   (`qubit_runtime/gateway/session_local_recovery.py:95`).
   Reproduced deterministically: the test's *first two* `_target_id` calls pass (creation and
   title-convergence); it fails only on the third, after the Bot Chat is deleted — the
   `_bot_chat_creation_id` → `stage_local_session` → `restore_local_session` re-creation path
   refuses the freshly-staged row because its `session_key/chat_id/user_id` no longer match the
   stored receipt. **Recovering a renamed/deleted Bot Chat fails in production.**

### MISSING PRODUCTION CODE / MISSING DEPENDENCY (lane B) — 1 file
3. **`qubit_runtime/tests/qubit_cli/test_update_downgrade_guard_named_profile.py`** —
   `ImportError: cannot import name 'test_update_downgrade_guard' from 'tests.qubit_cli'`.
   It imports the base module `tests.qubit_cli.test_update_downgrade_guard`, which was never
   ported (not on the branch, not on main). Collection error; not landable standalone.

### COLLECTION ERROR (lane A) — 26 support modules — not imported by any of the 62 green tests
These are helper/daemon modules that pytest tries to collect and cannot:
- `SyntaxError: from __future__ imports must occur at the beginning of the file` —
  `tests/gateway/_plugin_adapter_loader.py:43`, `tests/gateway/feishu_helpers.py:19` (both place
  `from __future__ import annotations` after executable bootstrap code). `_plugin_adapter_loader`
  also does `from voicebridge import qubit_runtime`, which resolves, but the file is unreachable.
- `ModuleNotFoundError: No module named 'tests.gateway.test_normal_runtime_boot'` — 12 fixture
  peers import a test module that was never ported (`automation_peer`, `bot_authority_peer`,
  `cli_launch_policy_probe`, `config_policy_recovery_probe`, `hosted_auth_probe`,
  `kanban_owner_probe`, `kanban_recovery_probe`, `legacy_migration_probe`, `local_automation_peer`,
  `local_lineage_probe`, `local_recovery_probe`, …).
- Run their own `pytest.main()` and reject args (`unrecognized arguments`) — 9 daemon fixtures
  (`api_daemon_witness`, `api_recovery_daemon`, `local_automation_daemon`, `local_lineage_daemon`,
  `local_recovery_daemon`, `native_reply_crash_daemon`, `native_telegram_recovery_daemon`,
  `stack_dump_daemon`, `terminal_commit_daemon`). These are subprocess targets, not test files.
- Misc: `tests/fakes/cron_owner.py` (`cannot import name 'executions' from 'cron'`),
  `tests/gateway/fixtures/legacy_bot_mailbox.py` (`owner belongs to a different profile home`),
  `tests/gateway/fixtures/persistence_worker.py` (reads stdin), `trampoline_daemon.py`
  (`KeyError: 'UGW_TRAMPOLINE'`).

### ENV ARTIFACT — 0 net
All 20 lane-B results that looked like failures in the first run (`REAL hermes home` guard) plus
the 1 runner timeout were artifacts; documented above. Nothing is classified here that is
actually a test defect.

### IMPORT MISMATCH — 0
No ported file failed due to a rebrand rename not being applied. `from voicebridge import
qubit_runtime` resolves (the `voicebridge/` shim package is committed on main).

### UNCLASSIFIED — 0
Every non-passing file is classified above.
