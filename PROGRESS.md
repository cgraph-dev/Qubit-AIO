# PROGRESS — agent/catchup-7 (tests/hermes_cli lane)

Baseline (HEAD 9facd1ca9, measured 2026-10-16 via run_lane.sh JOBS=6):
  === Summary: 303 files, 4982 tests passed, 532 failed, 157 skipped (678.4s) ===
  107 files with >=1 failure.

Direction ruling applied: where a test asserts QUBIT_* and code reads HERMES_*,
change the PRODUCT CODE (tests are the spec). QUBIT_HOME already correct.

## Trail
- test_mcp_catalog.py 32->0 failures (QUBIT_OPTIONAL_MCPS env read in qubit_constants._packaged_dir;
  +1 stale-policy test updated for record_extension_install failure_class/error).
- test_config.py TestEnvWriteDenylist 15->0 (denylist in config.py now denies QUBIT_* names too).

---

# PROGRESS — lane agent/catchup-14 (audit of landed fixes not yet audited)

Base `ad2e66acc`. Falsify-first. Status: COMPLETE. Deliverable: `REPORT-audit-landed.md`.

## Verdicts

1. `080514a2d` SUPERVISED_CHILD dual-read — **HOLDS**. Every product reader
   accepts both spellings via `gateway/restart.py` helpers; every writer emits
   canonical only (verified by generating each launcher). No reader-only site;
   no reader-migrated-writer-not mismatch.
2. `63b458a41` pin reconciliation — **PARTIAL → fixed**. Two call sites were
   reconciled but the instrument they depend on,
   `scripts/audit_sync_completeness.py`, still hardcoded `BASELINE =
   "a036b13793"` (1783 commits behind the frozen pin) for its `short_port`
   branch. Fixed to read `FROZEN_REV` (`069223321`).
3. `6ee2adf0c` memory receipt — **HOLDS**. `autowork._remember()` requires
   `state=="OK" and stored is True`; no sibling call site treats a non-raising
   call as persisted success.
4. `check_commit_hygiene.py` — **PARTIAL → fixed**. It double-classified a
   live-writer foreign worktree as BOTH violation(s) and hands-off, contradicting
   its own docstring and driving exit 1 for work it says it must ignore
   (`5c6c69d55`).

## Commits in this lane

- `5c6c69d55` fix(hygiene): live-writer foreign worktree is hands-off only
- `069223321` fix(drift): audit_sync_completeness reads the frozen pin

## Baselines (before → after)

- check_commit_hygiene.py: 27 → 27 passed (assertions added, 1-RED before)
- test_upstream_drift_report.py: 4 → 5 passed (new test, 1-RED before)
- supervised dual-read + restart detection: 26 → 26
- office memory receipts: 24 → 24

No count decreased. Both fixes are falsified (1-red without, green with).

## Reported, not fixed (owner decisions)

- `frozen_target_progress.py` frozen BASE dict measured at a third revision
  `ad12263a5ed4`; re-freezing it alongside the new BASELINE is an owner call.
- `docs/FROZEN_TARGET.md` is referenced by two scripts but does not exist
  (deleted by owner directive `eafb54e26`); `upstream_drift.py`'s last-resort
  pin source is dead.

## anon_auth-shape hunt

No reader-migrated-writer-not mismatch remains; the `ad2e66acc` revert is clean
(single canonical constant, all readers through it).

---

# PROGRESS — port/chunk-1 (agent/ + acp_adapter/, 11 files)

Status: **DONE** — 5 ported (imports clean), 6 dep-missing, 0 shims.

- Base `9c2f782e1`; upstream `origin/main` @ `53bec2a311979ad6f77580ba491eca261ab988e0`.
- Deliverables in `/tmp/port-1`: `PORTED.json`, `REPORT-port-1.md`.

## Committed (ported, imports clean)

- [x] `agent/agent_init_params.py`
- [x] `agent/agent_init_ollama.py`
- [x] `agent/codex_service_tiers.py`
- [x] `acp_adapter/elicitation.py`
- [x] `acp_adapter/gateway_session_meta.py` (`hermes_state`->`qubit_state`; `_meta.hermes` wire key kept)

## Blocked (dep-missing — dep absent AND in the 299-list; another chunk owns it)

- [ ] `acp_adapter/catalog.py` — needs `hermes_state_local` (`POLICY_PREFIX`)
- [ ] `acp_adapter/gateway_commands.py` — needs `hermes_cli.gateway_client`, `hermes_cli.gateway_mutations`
- [ ] `acp_adapter/gateway_server.py` — needs `acp_adapter.catalog`, `acp_adapter.gateway_commands` (same chunk), `gateway.session_ingress_media`, `hermes_cli.gateway_chat|gateway_client|gateway_mutations`
- [ ] `agent/chat_completion_helpers_tool_calls.py` — needs `agent.reasoning_carriers` (ported on `port/chunk-3`)
- [ ] `agent/compaction_events.py` — needs `agent.relay_compaction` (ported on `port/chunk-3`)
- [ ] `agent/compression_automation.py` — needs `agent.runtime_session_store` (ported on `port/chunk-3`)

## Next (for the merge driver)

After `port/chunk-3` merges: re-port the 3 `agent/*` blocked files (verbatim).
The `acp_adapter/*` trio needs the gateway_/state_local chunks first; `gateway_server.py`
last (two-level: it imports two files from this same chunk).
