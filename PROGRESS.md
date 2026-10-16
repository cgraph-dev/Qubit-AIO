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
