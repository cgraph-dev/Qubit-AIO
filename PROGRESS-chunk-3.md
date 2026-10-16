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

# PROGRESS — lane port/chunk-3 (agent/ + cron/ port from pin 517b5e10f)

Base `9c2f782e1`. Status: COMPLETE (12 clean, 1 blocked-on-deps). Deliverable: `REPORT-port-3.md`, `PORTED.json`.

## Trail
- 13/13 upstream files carry real bodies — 0 shims skipped.
- 12 import clean: persisted_turn_receipt, plugin_live_notes, reasoning_carriers, relay_compaction,
  runtime_session_compression, runtime_session_lifecycle, runtime_session_store, safe_worker_policy,
  skills_index_delta, turn_recovery_codex, cron/bot_chat_legacy, cron/delivery_outcome.
- 1 BLOCKED: agent/onboarding_text.py — needs agent.onboarding_first_tasks, agent.onboarding_flow,
  tools.set_theme_tool (all in the 300 list). Ported + committed, not stubbed.
- Brand: no env var introduced (nothing to reconcile). Module renames via tree convention
  (hermes_cli->qubit_cli, hermes_state_*->qubit_state_*, hermes_constants->qubit_constants).
  Protocol strings kept (hermes.native_assistant kind, hermes.compaction mark). User-facing
  identity in onboarding_text.py renamed Hermes->Qubit.
- Cron invariants: neither cron file engages pending_slot/next_run_at/tick lock/_inflight_key/
  _profile_cron_scope. delivery_outcome writes last_status/last_delivery_error via cron.jobs._with_job
  (fenced, idempotent). bot_chat_legacy only touches <QUBIT_HOME>/cron/bot_chat_pending/.
- Flagged: cron/executions.py is behind the pin (562 vs 653 lines) — settle_delivery_outcome absent,
  so delivery_outcome.py is inert until executions.py is refreshed.
