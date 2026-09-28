# Qubit Gateway + Update Qualification — 2026-09-21

**Status:** ISOLATED QUALIFICATION ONLY. **NOT VERIFIED.** This document records
what was exercised inside a hermetic test process. It is **not** live-host
verification and makes no `VERIFIED`/100%/1:1 claim. Read alongside
[QUBIT_ACTIVE_EXECUTION_BRIEF_2026-09-21.md](QUBIT_ACTIVE_EXECUTION_BRIEF_2026-09-21.md),
[QUBIT_GATEWAY_OPERATOR_RUNBOOK_2026-09-21.md](QUBIT_GATEWAY_OPERATOR_RUNBOOK_2026-09-21.md),
and [QUBIT_FINAL_VERIFICATION_AND_RELEASE_GATE_2026-09-21.md](QUBIT_FINAL_VERIFICATION_AND_RELEASE_GATE_2026-09-21.md).

**Workstream:** gateway/service supervision (step 8) and update manager
(step 9). **Files owned by this qualification:**
`voicebridge/gateway_supervisor.py`, `voicebridge/update_manager.py`,
`tests/test_gateway_qualification9u.py`, `tests/test_update_qualification9u.py`,
and this document.

---

## 1. What "isolated" means here

No host service, real `systemctl`, real remote, product worktree, or `~/.hermes`
store is touched. Three mechanisms enforce that:

1. **Fake `systemctl` first on `PATH`.** A tiny `sh` script
   (`tests/test_gateway_qualification9u.py`) records every argv to a temp log and
   flips a temp state file (`active`/`inactive`). Modes `missing`, `unparsable`,
   and `fail-start` select the typed failure paths. It never consults systemd.
2. **Injected `runner` / `spawner` / `probe`.** At the unit level, all process
   execution is passed in as callables, so the tests drive the logic directly
   with a deterministic double — the fake script is not even executed. The
   fail-closed guard (`gateway_supervisor._real_control_disabled`) is lifted
   **only inside the test process** (`PYTEST_CURRENT_TEST`).
3. **Temp git repo + local bare remote.** `tests/test_update_qualification9u.py`
   builds a bare remote, a source repo, and an install clone (one commit behind)
   under `tmp_path`, with a hermetic git env (`GIT_CONFIG_GLOBAL`/`SYSTEM`
   pinned, identity/`GIT_TERMINAL_PROMPT=0`). `update.repo_root` points at the
   temp clone; the injected runner is a thin `subprocess.run` bound to that env,
   so the only real command executed is the one `update_manager` builds, in the
   temp repo.

The gateway integration cases use `voicebridge.qubit_compat.dispatch` against a
temp `Config` (its own `XDG_*`/`HOME`/`QUBIT_HOME`), so the real dashboard route
is exercised end to end without editing `qubit_compat`.

---

## 2. Exact commands

Focused gateway + update qualification (this workstream):

```sh
python -m pytest tests/test_gateway_qualification9u.py \
                 tests/test_gateway_supervisor9u.py \
                 tests/test_gateway_control_integration9u.py \
                 tests/test_update_qualification9u.py \
                 tests/test_update_manager9u.py \
                 tests/test_update_apply_rehearsal9u.py -q
```

Recorded result: **69 passed** (26 new gateway + 6 prior gateway unit + 7 prior
gateway integration + 15 new update + 10 prior update unit + 5 prior update
rehearsal). No failures, no skips.

To force the fail-closed guard on for any manual (non-pytest) exercise:

```sh
QUBIT_GATEWAY_SUPERVISION_DISABLED=1 python -c '...'   # gateway control
QUBIT_UPDATE_DISABLED=1             python -c '...'    # update apply/check
```

---

## 3. Gateway matrix (step 8)

| Required behavior | Test | Result |
| --- | --- | --- |
| status: active | `test_status_active_reports_running_pid_and_port` | PASS |
| status: inactive | `test_status_inactive_reports_not_running` | PASS |
| status: unknown (unparsable output) | `test_status_unknown_when_systemctl_output_is_unparsable` | PASS |
| status: not-installed (`could not be found`) | `test_status_not_installed_is_typed_not_a_crash` | PASS |
| stale process / port conflict (`port in use`, service inactive) | `test_status_flags_stale_process_port_conflict` | PASS |
| active without listener | `test_status_flags_active_without_listener` | PASS |
| "never raises" on a raising runner | `test_status_never_raises_on_a_raising_runner` | PASS |
| start on inactive | `test_start_on_inactive_runs_start` | PASS |
| start idempotent on active | `test_start_on_active_is_idempotent_noop` | PASS |
| stop on inactive is a no-op | `test_stop_on_inactive_is_idempotent_noop` | PASS |
| stop on active (detached `STOPPING`) | `test_stop_on_active_is_detached` | PASS |
| restart (detached, returns `RESTARTING`) | `test_restart_is_detached_and_returns_restarting` | PASS |
| failure `rc!=0` → typed FAILED + redacted | `test_start_failure_is_typed_and_redacted` | PASS |
| timeout: runner returns 124 | `test_start_timeout_returning_124_is_typed` | PASS |
| timeout: runner raises | `test_start_timeout_raised_by_runner_is_typed` | PASS |
| other raised exception → typed FAILED | `test_raised_non_timeout_exception_is_typed_failure` | PASS |
| confirmation required | `test_confirmation_is_required` | PASS |
| e-stop refused | `test_estop_refuses_control` | PASS |
| audit event emitted | `test_audit_event_is_emitted_on_start` | PASS |
| recovery: failed start → typed state → retry succeeds | `test_recovery_failed_start_leaves_typed_state_and_retry_succeeds` | PASS |
| partially-injected call cannot reach real defaults | `test_partially_injected_call_cannot_reach_real_defaults` | PASS |
| uninjected start under guard cannot reach real runner | `test_uninjected_start_under_guard_cannot_reach_real_runner` | PASS |
| dispatch integration (`GET /api/gateway`, start/stop/restart, confirm) | `test_dispatch_get_gateway_reports_real_process_state`, `test_dispatch_start_stop_restart_and_confirm` | PASS |
| dispatch integration (not-installed + FAILED + redaction) | `test_dispatch_maps_not_installed_and_failure` | PASS |
| dispatch integration (typed `GATEWAY_SUPERVISION_UNAVAILABLE`) | `test_dispatch_returns_typed_unavailable_without_systemctl` | PASS |

Typed gateway states/codes: `OK`, `TIMEOUT`, `FAILED`, `CONFIRM_REQUIRED`,
`UNAVAILABLE`, plus the existing `NOT_INSTALLED`/`DEGRADED` status states.
Codes: `GATEWAY_CONFIRM_REQUIRED` (409), `GATEWAY_ESTOP` (403),
`GATEWAY_SUPERVISION_UNAVAILABLE` (503), `GATEWAY_CONTROL_FAILED` (500),
`GATEWAY_BAD_INPUT` (400), and the new `GATEWAY_CONTROL_TIMEOUT` (500).

---

## 4. Update matrix (step 9)

| Required behavior | Test | Result |
| --- | --- | --- |
| update available | `test_check_reports_update_available` | PASS |
| up-to-date (`behind == 0`) | `test_check_up_to_date_after_apply` | PASS |
| configured remote/branch respected | `test_configured_remote_and_branch_are_respected` | PASS |
| unreachable remote is typed DEGRADED, never "up to date" | `test_check_unreachable_remote_is_typed_degraded` | PASS |
| injected runner works without a `git` binary | `test_check_accepts_injected_runner_without_git_binary` | PASS |
| confirmation required (no git call) | `test_apply_requires_confirmation_without_calling_git` | PASS |
| dirty tree refused, tree untouched | `test_apply_refuses_dirty_worktree_and_leaves_tree_untouched` | PASS |
| e-stop refused | `test_apply_estop_refuses` | PASS |
| post-update revision recorded and matches remote | `test_apply_fast_forwards_and_records_matching_remote_revision` | PASS |
| rollback plan restores the pre revision | `test_rollback_plan_restores_pre_revision` | PASS |
| no destructive verb/flag ever executed | `test_no_destructive_command_is_ever_executed` | PASS |
| dispatch `GET /api/qubit/update/check` | `test_dispatch_update_check_route` | PASS |
| dispatch `POST /api/qubit/update` confirm → dirty → success | `test_dispatch_update_apply_confirm_dirty_and_success` | PASS |
| dispatch e-stop (403) and fail-closed guard (503) | `test_dispatch_update_apply_estop_and_guard` | PASS |
| receipt carries no secret and a described rollback | `test_dispatch_update_receipt_has_no_secret_or_destructive_plan` | PASS |

The **only** command `update_manager` executes is
`git pull --ff-only <configured-remote> <configured-branch>`. The
`rollback_plan` string names an operator command (`git -C <repo> reset --hard
<pre-revision>`) that is **described, never executed** by Qubit; the
qualification suite executes it manually *in the temp repo* only, to prove it
restores the pre revision.

---

## 5. Bugs found and fixed (additive, within scope)

1. **Gateway fail-closed guard bypass on partial injection (safety).**
   `gateway_supervisor.control` only refused when **both** `runner` and
   `spawner` were absent. Injecting a `runner` for the status read let
   `restart`/`stop` fall through to the real detached `_default_spawner` — a
   test process could mutate the host service. Fixed: refuse when
   `runner is None` (cannot read `systemctl` safely), and refuse a real
   `stop`/`restart` transition when `spawner is None`; idempotent no-ops still
   succeed with only a runner. Regression:
   `test_partially_injected_call_cannot_reach_real_defaults`,
   `test_uninjected_start_under_guard_cannot_reach_real_runner`.
2. **Gateway output was never actually redacted.** The module docstring promises
   "failures are typed and redacted", but `status()` and `control()` surfaced
   raw `systemctl` stderr verbatim — a unit's stderr can carry a credential from
   an `EnvironmentFile` or a failing `ExecStart`. Added `_redact()` (the same
   `voicebridge.redaction` choke point `update_manager` uses) at the status
   detail, start-failure detail, and spawner-exception detail. Regression:
   `test_start_failure_is_typed_and_redacted`,
   `test_dispatch_maps_not_installed_and_failure`.
3. **Raised runner exceptions escaped `status`/`control`.** `status` is
   documented "never raises" and `control` must always return a typed receipt,
   but an injected runner that raised (e.g. a real `TimeoutExpired`) propagated.
   Added `_run()`, which converts any raise to the typed `(rc, out, err)`
   contract, mapping timeouts to rc 124 and the new `GATEWAY_CONTROL_TIMEOUT`
   code. Regression: `test_start_timeout_raised_by_runner_is_typed`,
   `test_raised_non_timeout_exception_is_typed_failure`,
   `test_status_never_raises_on_a_raising_runner`.
4. **`update_manager.check` ignored an injected `runner` when deciding
   availability.** It consulted only the `git` alias (`shutil.which("git") or
   git`), while `apply` and the guard treat `runner` and `git` as equivalents;
   a host without a `git` binary reported `DEGRADED` even with a usable
   injected executor. Fixed to `git_available = which("git") or git or runner`.
   Regression: `test_check_accepts_injected_runner_without_git_binary`.

No destructive git behavior was changed; the executed command is still the
single fast-forward `git pull`.

---

## 6. Operator-only steps required for a real live run — **NOT EXECUTED**

These are deliberately left to the operator and were **not** performed by this
qualification. They are the difference between isolated qualification and live
verification.

1. **Live service control.** Run `systemctl --user status|start|stop|restart
   qubit-voicebridge.service` on the real host and observe the unit's
   `ActiveState`/`MainPID`/journal. The dashboard equivalents
   (`POST /api/gateway/{start,stop,restart}`) must be issued from a real
   browser session; the test guard only applies inside a pytest process, and a
   real `restart` will replace the serving process ~2 s after the response.
2. **Real stale-process / port-conflict remediations.** Reproduce a stray
   `:8890` (or configured) listener while the unit is inactive, confirm the
   dashboard shows "port is in use but the service is not active (stale
   process?)", then perform the documented host-hygiene steps (stop the stray
   instance, single canonical unit). Not performed.
3. **Real update against a real remote.** Run the dashboard "Update now" (or
   `POST /api/qubit/update {confirm:true}`) against the product checkout pointed
   at a real, authenticated remote, and confirm the post revision equals the
   remote branch head. The qualification used a local file-path bare remote and
   a temp clone only.
4. **Update credential/auth flows.** A real remote using a credential helper,
   SSH agent, or token URL — and its redaction on failure — was not exercised.
5. **Packaging/unit install.** Installing and qualifying the shipped
   `systemd/qubit-voicebridge.service` template (and `daemon-reload`) on the
   host is an operator step; not performed.
6. **Independent verification.** Per
   [QUBIT_FINAL_VERIFICATION_AND_RELEASE_GATE_2026-09-21.md](QUBIT_FINAL_VERIFICATION_AND_RELEASE_GATE_2026-09-21.md),
   a read-only verifier must reproduce the evidence before any `VERIFIED`
   verdict. Not performed here.

---

## 7. Uncertainty and limits

- The fake `systemctl` is a deterministic double. It does **not** reproduce real
  systemd semantics: unit dependencies/ordering, `StartLimitBurst`, restart
  coalescing, polkit authorization for `--user` vs system scope, `daemon-reload`
  interactions, SELinux/AppArmor, or service-manager timeouts.
- The detached `restart`/`stop` path is verified only by asserting a detached
  argv was handed to an injected spawner. That a real
  `sh -c "sleep 2; systemctl --user restart <unit>"` survives the serving
  process being replaced was **not** observed.
- Timeouts are exercised with injected runners returning 124 or raising
  `TimeoutExpired`; the production `TIMEOUT_S = 15.0` wall-clock path was not
  waited out.
- The update qualification uses a local file-path remote; network transport,
  TLS, auth, and remote-side hooks are untested.
- Redaction is tested against `token=…` and URL-userinfo credential shapes; the
  central `voicebridge.redaction` patterns remain the authority and other
  shapes are out of scope.
- Platform: Linux with a `systemctl` on `PATH` (the fake) is assumed; no
  non-systemd service manager is qualified.
- These results are a qualification of the **code paths**, not a behavioral
  parity or release claim.
