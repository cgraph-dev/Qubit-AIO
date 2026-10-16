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

# PROGRESS — lane port-5 (eval-harness chunk + PORT METHOD)

Branch `port/chunk-5`, worktree `/tmp/port-5`, base `9c2f782e1`.
Upstream read at `origin/main` = `53bec2a311979ad6f77580ba491eca261ab988e0` (2026-10-16).

## Deliverable

`PORT-METHOD.md` (repo root) — the reusable porting method for the 299 absent
production files, with exact commands. `REPORT-port-5.md` + `PORTED.json`.
This lane's real output is the method; the 5-file chunk is the worked example.

## Status

- [x] Mapping verified: upstream root -> `qubit_runtime/` (`gateway/run.py`
      331139B upstream vs 329378B ours).
- [x] Chunk 5/5 eval files ported, committed `df9007e7e`; companions
      (`cases.json`, 2 READMEs) committed `9c8ea8cb5`.
- [x] Proof: 4/5 import clean; the 5th (`session_readers_cli_ab.py`) is a
      top-level script (argparse at import) -> `py_compile` + `--help` + exec.
- [x] End-to-end: `verify_receipt.py` PASS on 47-case receipt, FAIL on tampered.
- [x] Every literal claim in `PORT-METHOD.md` re-verified against the tree.

## Method summary (full text in PORT-METHOD.md)

- Brand: port NEW files with canonical `QUBIT_HOME` / `~/.qubit` / `qubit_state`
  / `qubit_cli`; never blanket-rename; leave the frozen pin and `hermes_state_keys`
  (a module name) alone; grep the tree and reconcile rather than adding a spelling.
- Shim detection: <20-line re-export stub whose source module is absent ->
  `shim-skipped`, dependency named. Worked example: `gateway/session_admission.py`
  needs `hermes_state_keys.py`, which is itself absent (line 242 of /tmp/prod-files.txt).
- Dep resolution: rename-map -> `test -e` -> `grep /tmp/prod-files.txt` -> name it.
- Proof: `PYTHONPATH=qubit_runtime .venv/bin/python -c 'import <dotted>'`.
- Commit: small, named upstream path + short SHA.

## Next

Sibling lanes follow `PORT-METHOD.md` §8. If a chunk reaches
`gateway/session_admission.py`, it stays `shim-skipped` until
`hermes_state_keys.py` lands — order it first.

