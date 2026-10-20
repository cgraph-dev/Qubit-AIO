# Retirement readiness — measured audit

**Status:** READ-ONLY audit at base `1cfb80174dc31582dff6baf82bf99083df92c37e`
(branch `audit/retirement-readiness`). **Not a verdict.** Nothing here is
`VERIFIED`, `M1`, `M2`, `CODE_READY`, or `COMPLETE`, and no service was
restarted and no product code was changed.

Worktree: `/tmp/opencode/wt-retire` (HEAD identical to the primary checkout at
the time of measurement). Commands below were run from that worktree with the
primary checkout's venv (`/home/trick/Projects/Repos/qubit-voicebridge/.venv/bin/python`).

---

## 1. The three instruments

### 1.1 Burndown — `scripts/frozen_target_progress.py`

```bash
.venv/bin/python scripts/frozen_target_progress.py
```

Observed (exit 0):

```
FROZEN TARGET PROGRESS  --  rev 43faacfb8de7b29548547a7fbdedad0cc6e7391c
  absent files                          639    974    -335
  short_port files                      376    610    -234
  short_port symbols                    904   1649    -745
  behind_head files                       0      0       0
  behind_head symbols                     0      0       0
  TOTAL missing symbols                 904   2623   -1719
  split: production vs tests
    absent:      prod    0   tests  639
    short_port:  prod   10 sym / 7 files   tests  894 sym / 369 files
    behind_head: prod    0 sym / 0 files   tests    0 sym / 0 files
  DELTA vs freeze: +1719 symbols closed
VERDICT: NOT AT TARGET -- 10 production missing (1533 test missing, non-blocking)
```

The 10 production symbols are (JSON from the audit, filtered to non-`tests/`
paths):

| # | path | symbol | declared exclusion? |
|--:|---|---|---|
| 1 | `acp_adapter/gateway_server.py` | `_submit_fingerprint` | yes |
| 2 | `agent/model_metadata.py` | `_llamacpp_context` | yes |
| 3 | `cron/scheduler.py` | `_normalize_newlines` | yes |
| 4 | `gateway/run.py` | `_housekeeping_media_caches` | yes |
| 5 | **`gateway/status.py`** | **`_gateway_lock_handle`** | **NO — not in `docs/FROZEN_TARGET.md`** |
| 6 | `hermes_cli/backup.py` | `_import_external_member` | yes |
| 7 | `hermes_cli/backup.py` | `_import_members` | yes |
| 8 | `hermes_cli/backup.py` | `_import_members_exclusive` | yes |
| 9 | `tools/bot_relay.py` | `_replay_unanswered` | yes |
| 10 | `tools/bot_relay.py` | `_settle_pre_upgrade_claim` | yes |

**Finding:** 9 of 10 are declared in `docs/FROZEN_TARGET.md` §"Declared
exclusions" (lines 50–58). `gateway/status.py::_gateway_lock_handle` is the one
production symbol that is neither ported nor declared. Upstream defines it as a
module-global lock handle (`~/upstream/hermes-agent/gateway/status.py:38,949-993,1011`);
this fork replaced that path with `gateway.runtime_ownership.process_ownership`
(`qubit_runtime/gateway/status.py:1019-1056`), so it is very likely a
behaviour-preserving omission — but that is a decision, not a declaration yet.

**Instrument caveat:** the audit does not subtract the exclusion table
(`grep -n -i exclusion scripts/audit_sync_completeness.py scripts/frozen_target_progress.py`
returns nothing). Therefore `frozen_target_progress.py` can never print
`AT TARGET` while declared exclusions exist; by design it reports them as
missing. Retirement readiness must be read as **gap minus declared exclusions**,
not as the raw verdict line.

### 1.2 Completeness — `scripts/audit_sync_completeness.py`

```bash
.venv/bin/python scripts/audit_sync_completeness.py \
  --rev 43faacfb8de7b29548547a7fbdedad0cc6e7391c --show-symbols \
  --json /tmp/opencode/audit-sync.json
```

Observed (exit 0; `--show-symbols` only emits per-file symbols with `--json`):

```
# upstream .py files: 9503
# EXISTS: 8864   ABSENT: 639   CURRENT: 8488
# SHORT-PORT (trails our own baseline): 376 files, 904 missing symbols
# BEHIND-HEAD: 0 files, 0 missing symbols
# total missing symbols across present files: 904
## ABSENT files by subsystem
    639  tests
```

Production split from the JSON: **0 absent production files, 10 missing
production symbols across 7 files**; tests: **639 absent files, 894 short-port
symbols across 369 files**. The 10 are the table in §1.1.

### 1.3 Capability drift — `scripts/audit_capability_gaps.py`

This audit measures **`FROZEN_REV..origin/main`**, i.e. post-freeze upstream
drift. Under `docs/DECISIONS.md` ruling 4 that drift is **out of scope** until
hermes is retired, so it is not a retirement blocker — recorded for completeness.

```bash
.venv/bin/python scripts/audit_capability_gaps.py
```

Observed (exit 0):

```
# Capability audit — upstream 43faacfb8de7b29548547a7fbdedad0cc6e7391c..origin/main
delta files: 1992  (python: 1424)
symbols checked: 4523   MISSING in Qubit: 3305
files with gaps: 670

ACTIONABLE (production-referenced): 7
test-only (referenced only by tests): 0
test-local (defined in a test file):  2495
dead (referenced by nothing):         803
deliberate omissions (doc'd):         0
classified total: 3305 (== MISSING: 3305)
```

The 7 actionable symbols (classified by re-running the audit's own functions,
read-only):

- `qubit_runtime/agent/ssl_verify.py::certifi_bundle_path` ← `agent/ssl_verify.py`
- `qubit_runtime/qubit_cli/gateway_setup_wizard.py::confirm_reconfigure` ← `hermes_cli/gateway_setup_wizard.py`
- `qubit_runtime/qubit_cli/gateway_setup_wizard.py::offer_home_channel` ← same
- `qubit_runtime/qubit_cli/gateway_setup_wizard.py::print_setup_header` ← same
- `qubit_runtime/qubit_cli/gateway_setup_wizard.py::prompt_csv` ← same
- `qubit_runtime/qubit_cli/gateway_setup_wizard.py::save_env_values` ← same
- `qubit_runtime/tools/code_execution_env.py::is_secret_env_name` ← `tools/code_execution_env.py`

It does **not** require `~/.hermes.pre-retirement`; it finds the clone through
`~/upstream/hermes-agent` first (see §4). A first foreground run exceeded 15
minutes; an unbuffered background run completed normally. No failure was
observed, only slowness (it shells `git diff` per changed upstream file against
a 7 GB clone).

---

## 2. Dead-symbol detector

```bash
.venv/bin/python qubit_runtime/scripts/check_dead_symbols.py \
  --root /tmp/opencode/wt-retire \
  --upstream /home/trick/upstream/hermes-agent \
  --json /tmp/opencode/dead-retire.json
```

Observed (exit 1 = dead symbols present):

```
pin=43faacfb8de7  root=/tmp/opencode/wt-retire
  owned .py parsed     : 9068
  upstream .py at pin  : 9503
  defs examined        : 434484
  defs upstream never  : 32476  (out of scope)
  DEAD (upstream reads): 73  (4 are test-release seams)
```

`dead_count: 73` in the JSON. This matches the wave-10 recorded 73 (no drift at
this base). TRIAGE §Wave 10 classification, joined to the measured set:

| class | count |
|---|---:|
| deferred architectural / blocked lanes | 41 |
| false positive or test-only | 21 |
| correctly unwired in this fork | 10 |
| detector false positive (`cron_owner` pytest fixture) | 1 |
| **total** | **73** |

Deferred groups named by `qubit_runtime/scripts/_dead_symbols/TRIAGE.md` §Wave 10:

- durable Responses admission: `_response_identity`, `prepare_context`, `identify_output` (3)
- API-turn / run admission: `_admit_run_to_authority`, `_run_user_message`,
  `_run_idempotency_identity`, `_handle_run_clarify`, `_handle_resolve_unknown_run`,
  `_steer_refused`, `owns_api_run`, `run_api_turn` + generic run-admission rows
- hot-serve runtime family: `reserve_profile_home`, `serve_profile_runtime`,
  `unpark_profile` (3)
- inbound/turn mixin inheritance: `GatewayPluginInjectionMixin`,
  `GatewayTurnHygieneMixin`, `GatewayTurnPersistenceMixin`,
  `GatewayTurnPrepareMixin` (4)
- peer-DM routes: `create_peer_bot_chat`, `admit_peer_turn`, `await_live_delivery` (3)
- hosted-room attachment port: `_bounded_task_delta`, `_message_manifest`,
  `validate_task_input` (3)
- double-suppress / double-apply risk: `completion_admission`, `one_turn_route` (2)
- release-script admission: 9 symbols under `scripts/releases/entrypoint.py` and
  `scripts/releases/stable.py`
- cron lane blocked: `external_worker_exited_reason` (1)
- remaining generic DEFERRED: `select_apply_target`, `app_lifespan`,
  `wall_clock_sleep`, `CatalogApproved`/`CatalogServerError`, `terminal_worker_receipt`,
  and related rows

None of the 73 was resolved by this read-only lane.

---

## 3. Live surfaces (read-only)

```bash
systemctl --user is-active qubit-gateway.service qubit-voicebridge.service \
  qubit-voice-typing.service qubit-voice-orb.service
# active
# active
# active
# active

curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8890/chat
# 200
```

`identify_gateway` against the daemon's actual `QUBIT_HOME`
(`systemctl --user show qubit-gateway.service -p Environment` →
`QUBIT_HOME=/home/trick/.local/state/qubit-voicebridge/runtime`):

```
runtime_protocol: 1
state: ready
capabilities: ['session-authority-v1', 'durable-admission-v1', 'event-replay-v1']
authority_epoch: 1
instance_id: 31d5c04e87b04113933700dd7aa2d192
pid: 790178
profile: default
qubit_home: /home/trick/.local/state/qubit-voicebridge/runtime
launch_supervisor / supervisor: systemd
code_sha: null
code_version: unknown
```

All three required fields are present: `runtime_protocol=1`, `state=ready`,
`session-authority-v1` in `capabilities`. Note `identify_gateway(get_qubit_home())`
(`~/.qubit`) returns `None`; the live daemon is pinned to the systemd
`QUBIT_HOME` above, not the CLI default.

**Live-surface warnings observed (none blocks retirement by itself):**

1. **Stale dashboard bundle (confirms the STATUS note).**
   `check_qubit_bundle(resources.resource_path("frontend/qubit-dist"))` →
   `ok: false`, sole issue:
   `bundle built from a2af5269d5f1 but frontend sources changed since (checkout 1cfb80174dc3): rebuild frontend/qubit-web`.
   `has_token_global: true`, `has_legacy_global: false`, `missing_assets: []`.
   The same warning is in the bridge journal.
2. **Running code is not commit-identifiable from the payload** (`code_sha: null`,
   `code_version: "unknown"`), so the live daemon cannot be proven to run HEAD
   from the identify response alone.
3. Host/config noise unrelated to retirement: MCP `airtable` 401/405 (parked),
   and the SQLite 3.45.1 WAL-reset warning. Both are pre-existing in the journal.
4. `hermes-dashboard.service` is not loaded (`systemctl --user list-unit-files |
   grep -i hermes` → no rows; `is-active` → `inactive`, exit 4). `~/.hermes`
   contains only `.hermes-update-in-progress.lock` (0 bytes).

---

## 4. The pre-retirement tree

Measured:

```bash
test -d ~/.hermes.pre-retirement && echo PRESENT
# PRESENT
readlink -f ~/.hermes.pre-retirement/hermes-agent
# /home/trick/upstream/hermes-agent
du -sh ~/upstream/hermes-agent
# 7.0G
```

`~/.hermes.pre-retirement` is a retired home directory; its `hermes-agent`
entry is a **symlink** to `~/upstream/hermes-agent`, which is the actual git
clone (`.git` present, objects include the frozen pin).

This is what the instruments measure:

- `scripts/audit_sync_completeness.py:105-124`, `scripts/frozen_target_progress.py`
  (via that audit), `scripts/audit_capability_gaps.py:71-91`,
  `scripts/audit_missing_symbols.py`, `scripts/sync_upstream.py`,
  `scripts/sync_driver.py`, `scripts/upstream_drift.py`,
  `scripts/upstream_drift_report.py`, `scripts/upstream_bugfeed.py` all resolve,
  in order: `~/upstream/hermes-agent`, `~/.hermes/hermes-agent`,
  `~/.hermes.pre-retirement/hermes-agent`.

**Consequence:** removing `~/.hermes.pre-retirement` **would not** break any of
these instruments, because every one prefers `~/upstream/hermes-agent` (present).
Removing **`~/upstream/hermes-agent`** would break all of them: the other two
candidates contain no clone (only the symlink lives under the retired home), and
the scripts fail loudly rather than measuring an empty tree. The pin
`43faacfb8de7…` is read from that clone's object database, so preserving
`~/upstream/hermes-agent` (or another clone with the pin) is the real requirement.

---

## 5. Remaining blockers and owners

| # | Blocker | Evidence | Owner |
|--:|---|---|---|
| B1 | `gateway/status.py::_gateway_lock_handle` is a production symbol with no port and no declared exclusion | §1.1 table; `docs/FROZEN_TARGET.md:48-58` | **Owner** (declare) or a port lane |
| B2 | F5 — the 9 declared exclusions were added by agent commits without owner sign-off | `git blame -L 48,58 docs/FROZEN_TARGET.md` → all `Qubit Agent`; `docs/OPEN_THREADS.md:59,70` | **Owner** |
| B3 | F4 — control-socket bind failure is fatal in `run_bootstrap.py:855-857`, while `control_socket.py:191-193` documents all failures as non-fatal | `docs/OPEN_THREADS.md:47,59,70` | **Owner** |
| B4 | 73 dead definitions, of which 41 are deferred architectural groups / blocked lanes (durable Responses, API-turn admission, hot-serve, mixin inheritance, peer-DM, attachment port, release admission, cron/a2a lanes) | §2; `TRIAGE.md` §Wave 10 | Next dead-symbol wave (agent); two lanes blocked on named upstream work |
| B5 | Test-side gap: 639 absent test files, 894 short-port symbols / 369 files | §1.1–1.2; `docs/FROZEN_TARGET_WORKLIST.md:8` "non-blocking and out of scope for retirement" | **No owner required** (explicitly non-blocking) |
| B6 | Live dashboard bundle is stale vs frontend sources | §3.1; journal warning | Operator/frontend lane (rebuild), not a retirement gate |
| B7 | Retirement execution itself: no document assigns an owner to the disable/move/restart/verify/delete steps | `docs/FROZEN_TARGET.md:86-96` (checklist has no owner field) | **Unassigned** |

Test-side gap is deliberately listed as non-blocking: the burndown splits
production from tests and drives its verdict on production only
(`scripts/frozen_target_progress.py:26-32,275-282`).

---

## 6. Retirement steps as written, and what is unowned

`docs/FROZEN_TARGET.md:86-96` (measured against this host):

1. Disable `hermes-dashboard.service` if enabled+active — **measured: not
   loaded, inactive** (done / nothing to do).
2. `mv ~/.hermes` aside — `~/.hermes` holds one 0-byte lock file; the real
   retired home is `~/.hermes.pre-retirement`, which the checklist does not
   mention. **Ambiguity, no owner.**
3. Restart the four units (`qubit-voicebridge`, `qubit-voice-typing`,
   `qubit-voice-orb`, `qubit-gateway`) — **not executed here (read-only lane);
   no named owner.**
4. Verify dashboard, `/qubit-chat`, `/chat`, voice (Right-Ctrl + wake) — only
   `/chat` (200) and the four unit states were checked here; voice/browser
   journeys were **not** measured. **No owner.**
5. Only then delete — **no owner.**

---

## 7. What this audit could not measure

- Whether the running services execute current HEAD: `identify` reports
  `code_sha: null` / `code_version: "unknown"`.
- Behavioral voice journeys (Right-Ctrl + wake), dashboard rendering, and
  `/qubit-chat`; the lane was read-only and did not drive the UI or mic.
- Whether removing `~/.hermes.pre-retirement` is safe in practice: the
  precedence was measured in code and the symlink target confirmed, but nothing
  was removed.
- The full pytest/Vitest/Playwright matrix (implementation-first cadence and
  read-only scope).
- Exact retirement ownership: no document names an owner for §6 steps.
- `audit_capability_gaps.py` completeness: its first foreground run exceeded
  15 minutes; the background rerun completed, but no independent cross-check of
  its 3,305 count was performed.

---

## 8. Single next action

**Obtain the owner ruling on B1+B2 (the declared-exclusions table), treating
`gateway/status.py::_gateway_lock_handle` as the one symbol to either declare or
port.** Once the production gap is fully accounted for by owner-approved
declarations, the remaining items are non-blocking (tests) or architectural
debt (dead symbols), and the retirement checklist in §6 can be assigned and
executed. Re-run `frozen_target_progress.py` first; expect it to keep printing
`NOT AT TARGET -- N production missing` while exclusions exist, so the ruling
must key on the exclusion table, not on the raw verdict line.
