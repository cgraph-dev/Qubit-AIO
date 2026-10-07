# update_pause_record.py is NOT a single-file port — it is the atomic "custody family"

Date: 2026-10-07   Milestone: `ee8dd6c886`   Base: `a1431b1d` (branch c1)
Author: c1 agent. Verified by command; every claim below has a reproducer.

## TL;DR

The task premise — "`update_pause_record.py` is stdlib-only, so it is a clean
self-contained port" — is **FALSE**. The file imports `qubit_cli.update_custody`
(absent) and calls six `update_lock` helpers that **do not exist** in our tree
because our `update_lock.py` is the *baseline* version, not the milestone one.
A 1:1 port compiles and imports, but **crashes at runtime** and **fails the gate**.
It cannot be landed alone. Port the family, in order, as its own wave.

## EVIDENCE

### 1. Our `update_lock.py` is the baseline, not the milestone

    git show a036b13793:hermes_cli/update_lock.py | wc -l   ->  361   (== our 366, rebranded)
    git show ee8dd6c886:hermes_cli/update_lock.py | wc -l   ->  1589  (+73 top-level symbols)

`update_pause_record.py` calls these, NONE of which exist in our tree:

    update_lock.process_create_time   update_lock.incarnation_live
    update_lock._try_lock             update_lock._unlock
    update_lock.holds_checkout_lock   update_lock.update_in_progress
    UpdateLock.acquire_checkout

Reproducer (with a pause record on disk — the ONLY path that matters):

    with update_pause_record._mutex(2.0): ...
    # AttributeError: module 'qubit_cli.update_lock' has no attribute '_try_lock'

`identity()` / `identity_is_live()` fail the same way
(`process_create_time` / `incarnation_live`).

### 2. `update_custody.py` is absent and is a hard dependency

    update_pause_record.py:172   from qubit_cli.update_custody import run_git

`update_custody.py` (813 lines, also absent at base) imports seven MORE
milestone-only `update_lock` internals (`_HELD`, `custody_spawn_kwargs`,
`_bind_to_kill_on_close_job`, `resume_suspended_child`,
`bind_child_to_update_tree`, `_git_common_dir`, `checkout_lock_fds`,
`update_tree_job`). So `update_pause_record -> update_custody -> update_lock`.

### 3. A 1:1 port FAILS THE GATE

With the verbatim port landed (`entry-point resolution` hard-fails):

    [PASS] import gate                29 OK / 0 FAIL
    [FAIL] entry-point resolution     HARD prod=1 tests=0
      qubit_cli/update_pause_record.py  L172: unresolved-module: qubit_cli.update_custody.run_git [lazy]
    VERDICT: NOT READY

Base `a1431b1d` in a clean worktree: **PASS 4/4, HARD prod=0**. The regression is
100% the ported file's missing-module reference.

### 4. Porting the milestone `update_lock.py` breaks 4 v1 tests

Whole-file 1:1 port of the milestone `update_lock.py` compiles and imports, but:

    pytest tests/qubit_cli/test_update_lock.py -> 4 failed, 32 passed

(the milestone marker format is v2; our v1 tests assert the v1 format). Upstream
ships three NEW test files for it (`test_update_lock_protocol.py`,
`test_update_lock_fd_provenance.py`, `test_update_lock_windows_live.py`) — all
absent here. So the file port also requires its test port.

## THE FAMILY (port together, in this order)

    hermes_cli/update_lock.py        (1589)  + its 3 new test files + update 1 old
    hermes_cli/update_custody.py     ( 813)
    hermes_cli/update_pause_record.py(1022)  + tests/hermes_cli/test_update_pause*.py (12 files)
    hermes_cli/update_cmd_commit.py  ( 606)
    hermes_cli/update_cmd_pause_gate.py

This matches `docs/PRODUCTION_SHORT_PORT_CLOSED_2026-10-07.md` §"DEFERRED":
"update_pause_record.py, update_custody.py, update_cmd_commit.py, anon_challenge.py
are ALL genuinely ABSENT. -> OUT OF SHORT-PORT SCOPE. Defer as its own port wave
(custody family), do NOT stub."

## CONTRACTS THAT MUST SURVIVE THE PORT (do NOT rebrand)

    RECORD_STEM              ".hermes-update-paused-gateways"   (Python/Rust/Electron lock)
    MUTEX_NAME               RECORD_STEM + ".lock"
    MARKER_NAME              ".hermes-update-in-progress"        (update_lock)
    CHECKOUT_LOCK_NAME       ".hermes-update.lock"
    GIT_CHECKOUT_LOCK_NAME   "hermes-update.lock"
    HERMES_DISABLE_LAZY_INSTALLS   (compat env var; still read by qubit_cli/venv_sync.py:364)
    HERMES_HOME / HERMES_UPDATE_HANDOFF_PID   (compat env vars)

Confirmed live across the tree:
`qubit_runtime/apps/bootstrap-installer/src-tauri/src/{paths.rs,update.rs}`,
`qubit_runtime/apps/desktop/electron/{update-marker.ts,update-gate.ts,main.ts}`,
`qubit_runtime/scripts/desktop-update/{posix.sh,windows.ps1}`.

## USER-VISIBLE `hermes` STRINGS IN THE PORTED FILE (report; do not rename in a port wave)

    update_pause_record.py:937  "→ Restarting gateway(s) paused by an interrupted `hermes update`..."
    update_pause_record.py:945  "Run `hermes update` or `hermes gateway start`."
    (docstrings mention `hermes update` / `hermes gateway stop` / ROOT Hermes home)
Already rebranded elsewhere (e.g. `auth_error_copy.py:29` says "Run `qubit update`"),
so these are inconsistent — a rebrand-wave target, not a port-wave target.

## READY ARTIFACTS (drafted, verified to compile/import; NOT landed — gate would go red)

    scratch/c1-custody-port/update_pause_record.ported.py   (1022 lines, rebrand applied)
    scratch/c1-custody-port/update_lock.ported.py           (1590 lines, rebrand applied)
    scratch dir: $QUBIT_HOME/runtime/cache/scratch/c1-custody-port/

The `update_pause_record.ported.py` diff vs upstream is EXACTLY the intended
translation (13 hunks: hermes_cli->qubit_cli, hermes_constants->qubit_constants,
get_default_hermes_root->get_default_qubit_root, _same_hermes_home->_same_qubit_home).

## SEPARATE PRE-EXISTING DEFECT FOUND (not caused by this work)

`tests/qubit_cli/test_update_lock.py` FAILS at base `a1431b1d`:
it asserts `update_marker_path() == <home>/.qubit-update-in-progress`, but the
shipped `MARKER_NAME` is `.hermes-update-in-progress` (correctly kept as the
cross-language contract). 1 failed / 35 passed. The test was rebranded; the
constant was not — either the test is wrong or the contract must be migrated
deliberately (with Rust + Electron). Do NOT "fix" it by renaming the constant.
