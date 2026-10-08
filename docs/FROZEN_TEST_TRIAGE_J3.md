# Frozen-target test re-port + triage (wave j3)

**Target frozen at `ad12263a5ed4`. All numbers measured with `--rev ad12263a5ed4`.**

## (A) Audit rebrand blind spot — VERIFIED ALREADY FIXED

`scripts/audit_sync_completeness.py` already carries the rename subtraction in BOTH
branches (committed at `d131dc01`). Verified against ground truth:

- **short_port PRODUCTION: 237 raw -> 0 post-fix** (the whole population, not a sample).
  Every raw production short-port symbol at the frozen rev is a rebrand ghost:
  `_hermes_dirs`->`_qubit_dirs` (agent/file_safety.py:55),
  `run_hermes_oauth_login_pure`->`run_qubit_oauth_login_pure`
  (agent/anthropic_credentials.py:754), `hermes_lsp_bin_dir`->`qubit_lsp_bin_dir`
  (agent/lsp/install.py:85), `_hermes_home`->`_qubit_home`, etc. 248 credited by the
  resolver; the 7 "still-missing" were symbols upstream REMOVED between the baseline
  and the frozen target (e.g. `_transcribe_wav_in_chunks`), correctly not counted.
- **`--show-symbols`** exists (audit_sync_completeness.py:322) and emits per-file symbol
  names into the JSON `symbols` map.

### The stated "32 -> 0" and "396 -> 251" ground truth could NOT be reproduced
Measured, honestly:
- short_port production raw -> post-fix: **237 -> 0** (parent said 32 -> 0). The parent's
  "32" is a curated subset; the true whole-population raw count is 237, still 0 genuine.
- behind_head top-45 (raw ordering): **359 raw -> 340 post-fix** on this tree
  (origin/main `865ba906c1` gives the same 360). The parent's "396 -> 251" is not
  reproducible against the frozen rev OR origin/main. **Borderline: reported as measured,
  not as claimed.**
- Current full audit at frozen rev: EXISTS 8144 / ABSENT 182 / CURRENT 7886;
  short_port 74 files/195 syms (ALL tests); behind_head 184 files/546 syms;
  total 741 missing symbols across present files.

## (B) Test tree

### `test_tui_gateway_server.py` — ALREADY a faithful frozen re-port
23496 lines, identical to the frozen upstream file. After case-aware
`qubit<->hermes` normalization there are **ZERO differing lines**. The earlier re-port
(commit `539fc8e4`) is byte-equivalent to `ad12263a5ed4`. Run: **18F / 651P** (down from
the 39F the campaign started at). The 18 failures are recorded as product gaps below;
no further re-port needed.

### Newly ported absent test files (24 files)
| file(s) | result |
|---|---|
| tests/tools/test_transcription_{chunking,streaming,whisper_cpp}.py | 6P |
| tests/tui_gateway/ (8 files) | 29P / 9 xfail |
| tests/cron/ (4 files) | 11P / 3 xfail |
| tests/gateway/ (3) + tests/tools/ (5) | 52P / 6 xfail |
| tests/qubit_cli/ update-path (4) | 77P / 33 xfail / 3 skip |

## Product bugs found (recorded, NOT fixed — siblings own product)

| # | file:line | assertion | class |
|---|---|---|---|
| 1 | `cron/jobs.py:1711` | `_normalize` maps `enabled_toolsets=[]` -> None (frozen:1701 `_normalize_enabled_toolsets` preserves `[]`). Test: `create_job(enabled_toolsets=[])["enabled_toolsets"] == []` (#82010) | PRODUCT |
| 2 | `cron/jobs.py` create_job signature | lacks `interpreter` kwarg that `tools/cronjob_tools.py:696` passes; `create_job() got an unexpected keyword argument 'interpreter'` | PRODUCT |
| 3 | `cron/scheduler.py` `_resolve_cron_enabled_toolsets` | `({"enabled_toolsets": []}, {})` returns 18 platform toolsets, not `[]` (fail-open, #82010) | PRODUCT |
| 4 | `tui_gateway/methods_session.py` (frozen:2) + `contracts/sessions.py` (frozen:1) | RPC `session.list` rows omit `_lineage_root_id` (#66663) | PRODUCT |
| 5 | `tui_gateway/prompt_turn.py` (frozen:2) + `contracts/events.py` (frozen:1) | turn payload omits `response_reused` | PRODUCT |
| 6 | `tui_gateway/server.py:2944` | calls `db.reopen_session(stored_id)` the frozen server did not | PRODUCT divergence |
| 7 | `tui_gateway/commands.catalog` | returns empty `skills` {} — plugin skills don't reach the TUI slash menu | PRODUCT |
| 8 | session resume path | mounting a finalized session reopens its row (`ended_at` -> None, #85303) | PRODUCT |
| 9 | `qubit_cli/_update_takeover.py` | lacks `_arm_fleet_obligation` (frozen has it) | PRODUCT short-port |
| 10 | update-path critical inventory | `update_custody.py`/`update_lock.py`/`_early_recovery.py` uncovered by critical-module inventory | PRODUCT short-port |
| 11 | `gateway/run.py` `_handle_message` | routed-profile admission returns agent reply where frozen expects consumer-scope-only None | PRODUCT (unconfirmed root) |
| 12 | `gateway/shutdown_flush.py` | boot-recovery ordering differs from frozen | PRODUCT (unconfirmed root) |

## ENVIRONMENT / TEST-DRIFT (not product)
- `test_plugin_origin_injection.py` (3): fixture resolves the real
  `~/.local/state/qubit-voicebridge/runtime/gateway_voice_mode.json` path; our
  `tests/home_io_guard.py:140` refuses I/O outside the isolated home.
- `test_old_updater_takeover.py` subprocess tests: child stdout uses a non-UTF8
  (`charmap`) codec, `→` (U+2192) fails to encode. Locale/encoding environment issue.

## How to re-run
```
.venv/bin/python scripts/audit_sync_completeness.py --rev ad12263a5ed4 --json /tmp/j3.json --show-symbols
.venv/bin/python scripts/gate_all.py         # stays PASS 4/4
```
