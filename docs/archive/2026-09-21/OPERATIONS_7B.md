# Packet 7B — Flags, rollback drills, smoke coverage, change reports, reconciliation

Implemented in `voicebridge/feature_flags.py`, `voicebridge/cli.py` (`flags`,
`flag-drill`), `scripts/rollback_drill.py`, `scripts/change_report.py`,
`scripts/run_tests.py` (`smoke`, `reconcile`), `scripts/config_doctor.py`
(`--reconcile`), `tests/test_packet7b.py`, and `tests/test_smoke.py`. No new
capability or scheduling is enabled; every command is read-only unless an
explicit `--apply` writes a config with a timestamped `.bak`.

## Feature flags

Every reversible Phase 0–7A gate is registered in `voicebridge/feature_flags.py`:

| flag | config path | default | rollback | category |
|---|---|---|---|---|
| `broker` | `safety.tool_broker` | `True` | `True` | phase3 |
| `compiler_5c` | `intent.compiler.mode` | `5c` | `4b` | phase5 |
| `canary_q5` | `intent.canary.q5_enabled` | `False` | `False` | phase4 |
| `router_l0` | `intent.router.enabled` | `False` | `False` | phase4 |
| `router_l1` | `intent.router.l1_enabled` | `False` | `False` | phase4 |
| `llm_classifier` | `intent.llm_classifier` | `True` | `True` | phase0 |

`voicebridge config.py::DEFAULTS` now declares `intent.router.*`,
`intent.canary.q5_enabled`, and `intent.compiler.*` so the registry has an
explicit single source of truth (values equal the pre-existing per-reader
defaults; behavior is unchanged).

CLI (dry-run by default; `--apply` writes the config with a backup):

```bash
voicebridge flags list                 # table of every flag
voicebridge flags list --json          # machine-readable
voicebridge flags get broker
voicebridge flags set broker false     # prints the change + env override, writes nothing
voicebridge flags set broker false --apply --config ~/.config/openclaw-voice-bridge/voice.yaml
voicebridge flags rollback broker --apply --config ~/.config/openclaw-voice-bridge/voice.yaml
```

`set --apply` and `rollback --apply` keep `<config>.flags.<ts>.bak` next to the
config; restore that file to roll back. Flag writes never touch the state dir.

## Rollback drills

`scripts/rollback_drill.py` toggles every registered flag on a throwaway config
and state, verifies the effective value changed, restores the documented
rollback value, restores the default, and proves seeded user data (memory DB,
report manifest, cursor file) is byte-identical before and after.

```bash
python scripts/rollback_drill.py          # human report, exit 0/2
python scripts/rollback_drill.py --json   # machine-readable
voicebridge flag-drill                    # same drill via the CLI
```

## Smoke coverage

`tests/test_smoke.py` (6 tests, hermetic; no Ollama/audio/live bridge):
- **startup** — WebUI binds loopback on a random port;
- **dashboard** — `/api/status` reports bridge up, `/api/activity` returns JSON,
  `/` serves HTML;
- **task** — typed task through the webui fake-orch path reaches `done`;
- **cancellation** — a typed run cancel emits exactly one `RUN_CANCELLED`
  terminal event and a duplicate cancel is idempotent;
- **voice** — real piper synthesizes a valid mono 16-bit WAV at the playback
  boundary when the voice is installed; a missing voice is a **DEGRADED** skip,
  never a pass.

```bash
python scripts/run_tests.py smoke        # smoke category; skips reported DEGRADED
```

## Change reports

`scripts/change_report.py` writes a timestamped Markdown report (files changed
from `git status`, diff summary, tests, benchmarks, risks, rollback) under
`docs/CHANGE_REPORTS/`.

```bash
python scripts/change_report.py --title "Packet 7B" \
    --tests "tests/test_packet7b.py tests/test_smoke.py green" \
    --bench "flag drill: 6 flags, user data preserved" \
    --risks "flag --apply writes config; keep the .bak" \
    --rollback "restore the .bak; no state data touched"
```

## Final runtime/documentation reconciliation

`config_doctor.py --reconcile` (or `python scripts/run_tests.py reconcile`)
cross-checks Phase 0–7A claims read-only:
- feature-flag registry defaults agree with `config.py::DEFAULTS`;
- every flag resolves to an effective value;
- the model alias agrees across docs, config, and the runner contract;
- AUDIO_SETUP rules (mono mic, sane gain, empty output pins) match config;
- documented test categories (unit/contract/integration/smoke/live/monitor)
  exist in `run_tests.py`;
- MIGRATIONS.md has the feature-flag migration entry.

## Tests

`tests/test_packet7b.py` (19): registry completeness/type/defaults, config
resolution + env-override key format, atomic write + backup + state
preservation, missing-config write rejection, rollback drill (script + CLI
exit codes), smoke category/marker/paths/live-service isolation, change-report
generation + secret scan, doctor reconcile PASS, CLI list/get/dry-run/apply,
and the flag-drill CLI.

## Rollback

Revert the Packet 7B commit and remove `voicebridge/feature_flags.py`, the
`flags`/`flag-drill` CLI, `scripts/rollback_drill.py`,
`scripts/change_report.py`, `tests/test_packet7b.py`, `tests/test_smoke.py`,
`docs/CHANGE_REPORTS/`, and `docs/OPERATIONS_7B.md`; restore
`scripts/run_tests.py`, `scripts/config_doctor.py`, `pytest.ini`,
`voicebridge/config.py`, and `voicebridge/cli.py`. No service, timer, model,
audio, or state data is changed by any 7B command. Flag writes keep backups for
a one-command restore.