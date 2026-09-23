# Packet 7A — Operations: separated commands, health checks, no implicit restarts

Implemented in `pytest.ini`, `scripts/run_tests.py`, `scripts/config_doctor.py`
(health checks), `scripts/dashboard_stress.py` (`--allow-restart` watchdog
guard), `scripts/clean_venv_check.py`, `scripts/check_deployment.py`, and
`tests/test_packet7a.py`. No monitoring daemon is added and no production model
behavior is changed.

## Separated test/monitor commands

`scripts/run_tests.py` provides independent category commands:

```bash
python scripts/run_tests.py unit            # hermetic unit/contract; live+monitor excluded
python scripts/run_tests.py contract        # pinned packet contract tests
python scripts/run_tests.py integration     # HTTP/webui integration tests
python scripts/run_tests.py live-model      # opt-in real-model tests (DEGRADED if skipped)
python scripts/run_tests.py live-dashboard  # opt-in real-bridge dashboard probes
python scripts/run_tests.py monitor         # opt-in monitor/clean-venv checks
python scripts/run_tests.py all             # full suite
python scripts/run_tests.py health          # config doctor --runtime
```

`unit`/`contract`/`integration` never include the live files
(`test_live_stress.py`, `test_dashboard_stress.py`) or the `AirLLMLive` class.
Live/monitor categories are OPT-IN and report any skipped check as **DEGRADED**
— never as a silent pass. Markers are registered in `pytest.ini`
(`unit`, `contract`, `integration`, `live`, `monitor`, `dashboard`).

## No implicit watchdog/service restarts

`scripts/dashboard_stress.py --watch` is the OPERATOR watchdog (a separate
process, never started by pytest). Packet 7A makes it **monitor-only by
default**:

- `--allow-restart` is required to enable the bridge self-heal
  (`systemctl --user restart openclaw-voice-bridge.service`) and the VRAM
  model-recycle. Without the flag, a stuck bridge is reported, never restarted
  (`_restart_decision` gates both actions).
- `tests/test_packet7a.py` proves pytest never invokes the watchdog or
  restarts the bridge service (an argv-precise source scan). Managing a test's
  OWN transient systemd unit (Packet 3B isolation lifecycle) is not a service
  restart and remains allowed.

## Health checks

`config_doctor.py --runtime` now reports typed states for:
config, model, gateway TCP, Chromium CDP, audio devices, Bubblewrap/systemd
isolation, **disk** (`health.disk`, threshold 1 GiB free), **VRAM**
(`health.vram`, free MiB via nvidia-smi, threshold 512 MiB), and
**event-journal writeability** (`health.journal_write`, a transient probe file
that is removed). Every check degrades to `DEGRADED`/`MISSING` instead of a
false pass, and never prints secrets.

## Clean-venv installation / dependency contract

`scripts/clean_venv_check.py`:
- `--check` (default, hermetic): AST dependency-contract scan (no undeclared
  third-party imports) + bytecode-compile of `voicebridge/` and `scripts/`.
- `--install` (opt-in, slow, network): creates a throwaway venv, installs the
  package with declared deps, and re-runs the scan inside it so
  installed-but-undeclared packages cannot hide a missing dependency. If venv,
  pip, or network is unavailable the result is `DEGRADED`, never a pass.

## Deployment-template checks

`scripts/check_deployment.py` renders the repository systemd template and
validates: `Restart=on-failure` with bounded `RestartSec`, `__PROJECT_DIR__`
resolved (no leftover placeholders), portable resource guardrails documented as
comments (never hardcoded machine limits), no machine-specific home paths, no
secret-shaped values, and secret-safe `Environment` lines. It is read-only —
it never installs or edits the installed unit.

## Tests

`tests/test_packet7a.py` (16 tests): category definitions and live/monitor
opt-in separation, skip-count DEGRADED labeling, pytest.ini marker
registration, watchdog `--allow-restart` decision logic and flag plumbing,
pytest-never-starts-watchdog source scan, typed doctor health checks
(disk/VRAM/journal), transient journal-probe cleanup, deployment-template
contract (pass + hardcoded-resource rejection), and the hermetic clean-venv
`--check` (with the full `--install` marked `monitor`, opt-in).

## Rollback

Revert the Packet 7A commit(s) and remove `pytest.ini`,
`scripts/run_tests.py`, `scripts/clean_venv_check.py`,
`scripts/check_deployment.py`, `tests/test_packet7a.py`, and
`docs/OPERATIONS_7A.md`; restore `scripts/dashboard_stress.py` (removes the
`--allow-restart` guard) and `scripts/config_doctor.py` (removes the health
checks). No service, timer, config, model, or audio state is changed by any 7A
command.