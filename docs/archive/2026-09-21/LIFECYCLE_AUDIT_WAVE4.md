# Qubit lifecycle audit — Wave 4 (packaging + migration evidence)

Agent: packaging + migration/upgrade/rollback/uninstall (workstream G).
Base commit: `539649d4` (waves 1–3 not regressed).
Scope: wheel/sdist build, isolated clean-venv install with dashboard/runtime
startup, and a hermetic migration → upgrade → rollback → uninstall rehearsal.
Every command below was run from the repository root with the repository venv
unless noted. No operator config, state, service, model, browser, or audio was
modified; all lifecycle state lived in a throwaway `/tmp` tree.

`$REPO` = repository root (working directory). `$WORK` = a throwaway
`mktemp -d` directory removed by the command's own trap.

## Gate summary

| # | Gate | Command | Observed | Verdict |
|---|------|---------|----------|---------|
| 1 | wheel + sdist build | `uv build --wheel --sdist --out-dir "$WORK/dist" .` | both artifacts built | PASS |
| 2 | isolated clean install (declared deps) + dashboard/runtime startup | `bash scripts/clean_install_check.sh --require-network` | 4 entry points, plugins=10, runtime in site-packages | PASS |
| 3 | fast isolated dashboard/runtime gate | `bash scripts/verify_installed_dashboard.sh` | asset served, plugins=10, ops=510, runtime OK | PASS |
| 4 | migration → upgrade → rollback → uninstall rehearsal | `bash scripts/migration_rehearsal.sh` | 7/7 synthetic files preserved each stage | PASS |
| 5 | hermetic release certification | `.venv/bin/python scripts/release_qualify.py --static` | `verdict: PASS` | PASS |
| 6 | focused regression + new lifecycle suite | `.venv/bin/python -m pytest -q tests/test_lifecycle_wave4.py tests/test_packaging_wave3.py tests/test_hermes_package8u.py tests/test_hermes_package_runtime8u.py tests/test_packet7c.py tests/test_packet7c_audio.py tests/test_release_gates8u.py` | 130 passed | PASS |
| 7 | syntax + hygiene | `bash -n scripts/migration_rehearsal.sh`; `python -m compileall -q tests/test_lifecycle_wave4.py`; `git diff --check` | all clean | PASS |

## 1. Wheel + sdist build

```
$ uv build --wheel --sdist --out-dir "$WORK/dist" .
Successfully built .../qubit_voicebridge-1.0.0.tar.gz
Successfully built .../qubit_voicebridge-1.0.0-py3-none-any.whl
```

Observed artifacts (this run):

- `qubit_voicebridge-1.0.0-py3-none-any.whl` — 126,854,557 bytes,
  sha256 `046c262e95a20564a7dbce418849c2b9d798b9cbcce2ae59bcc5bbf4bd24c174`
- `qubit_voicebridge-1.0.0.tar.gz` — 29,638,480 bytes,
  sha256 `55b30fd4cf9c3be64285b0b3de1732e6fa1093da61c2223070bd9633c8f69595`

These hashes are recorded as observed; they are build-environment dependent, not
a reproducibility promise. Byte-level reproducibility is the separate
`scripts/release_qualify.py --reproduce` gate.

## 2. Isolated clean-venv install + dashboard/runtime startup (full deps)

```
$ bash scripts/clean_install_check.sh --require-network
== toolchain: uv ==
== build wheel ==
== throwaway venv (under $WORK/venv) ==
== install wheel + declared deps (network) ==
== console entry points ==
  ok: qubit --help
  ok: voicebridge --help
  ok: qubit-dev --help
  ok: qubit-hermes --help
== installed dashboard + runtime probe ==
OK dashboard plugins=10 runtime=$WORK/venv/lib/python3.11/site-packages/voicebridge/_data/qubit_hermes
clean install check: PASS
```

This installs the wheel **with its declared dependencies** into a venv and
throwaway HOME outside the checkout, then proves the installed package (never
the checkout) serves the dashboard and imports the vendored Hermes runtime.

## 3. Fast isolated dashboard/runtime gate

```
$ bash scripts/verify_installed_dashboard.sh
wheel: qubit_voicebridge-1.0.0-py3-none-any.whl
OK root->/hermes/  asset=assets/index-BFKNJjki.js  plugins=10  ops=510
OK runtime=.../site-packages/voicebridge/_data/qubit_hermes module=.../hermes_cli/main.py
installed dashboard gate: PASS
installed Hermes runtime gate: PASS
```

`--no-deps` variant; complements gate 2 with a fast import-from-package-data
proof and no checkout on `sys.path`.

## 4. Migration → upgrade → rollback → uninstall rehearsal

```
$ bash scripts/migration_rehearsal.sh
== migration rehearsal (hermetic) ==
== 1. migration ==
   migration: 7 synthetic state file(s) preserved byte-for-byte
   canonical trees present; legacy compat aliases point at them
   validated backup present; credentials excluded from the backup
== 2. upgrade ==
   upgrade: 7 synthetic state file(s) preserved byte-for-byte
   upgrade preserves: user config, user state, memory/reports/cursors, ...
== 3. rollback ==
   rollback: 7 synthetic state file(s) preserved byte-for-byte
   canonical trees removed; legacy trees restored whole
== 4. uninstall ==
   uninstall: 7 synthetic state file(s) preserved byte-for-byte
   uninstall requires confirmation and preserves state/config
migration rehearsal: PASS
```

The synthetic legacy bundle covers config (`voice.yaml`,
`personal-knowledge.md`, a credential-named file), state (`events.jsonl`,
`memory/memory.db`, `reports/report.json`), and data (`models/weights.bin`).
The rehearsal asserts: migration preserves every byte and leaves canonical
trees + compat aliases; the credential file is preserved by the live move while
being excluded from the safety backup; the upgrade plan mutates nothing;
rollback removes the canonical trees and restores the legacy trees whole; and
the uninstall plan is confirmation-gated, read-only, and preserves state.
Typed exit codes: `0` PASS, `2` FAIL, `4` DEGRADED (tooling), `8` BLOCKED
(workspace).

No new store/config schema migration is introduced by this packet (see
`MIG-025`); the migration exercised is the documented legacy → canonical path
migration in `docs/QUBIT_MIGRATION.md`.

## 5. Hermetic release certification

```
$ .venv/bin/python scripts/release_qualify.py --static
verdict: PASS
  static: OK
```

## 6. Focused regression + new lifecycle suite

```
$ .venv/bin/python -m pytest -q \
    tests/test_lifecycle_wave4.py \
    tests/test_packaging_wave3.py \
    tests/test_hermes_package8u.py \
    tests/test_hermes_package_runtime8u.py \
    tests/test_packet7c.py tests/test_packet7c_audio.py \
    tests/test_release_gates8u.py
130 passed
```

## 7. Syntax + hygiene

- `bash -n scripts/migration_rehearsal.sh` → clean.
- `.venv/bin/python -m compileall -q tests/test_lifecycle_wave4.py` → clean.
- `git diff --check` → clean (tracked changes); new files contain no trailing
  whitespace.

## Residual operator-only steps (typed, not silently passed)

1. **Release signing** — optional and operator-only: it needs a private gpg
   signing key that is never generated or stored in this repository
   (`docs/RELEASE_SIGNING.md`). Unsigned artifacts are reported `NOT_SIGNED`;
   this packet makes no signing claim.
2. **Full uninstall execution** — removing the `.venv`/checkout and the user
   unit is confirmation-gated and operator-only by design. The rehearsal
   verifies the read-only uninstall plan and that user state is preserved; it
   deliberately does not delete any live path.
3. **Network-dependent install** — gate 2 was run with network available and
   `--require-network`. On an offline host, a failed dependency install is a
   typed `DEGRADED` (`exit 4`), never a silent pass. The rehearsal itself
   (gate 4) needs no network.
4. **Tooling dependency** — the rehearsal needs a Python that can import
   `voicebridge` (the repo venv or an installed package). Otherwise it exits
   `4` (`DEGRADED`) with the interpreter named, never a false PASS.
5. **Physical hardware/account qualification** — microphone, speaker, GPU,
   provider accounts, and clean-machine qualification remain out of scope.

## Rollback

Remove `scripts/migration_rehearsal.sh`, `tests/test_lifecycle_wave4.py`,
`docs/LIFECYCLE_AUDIT_WAVE4.md`, and `MIG-025` from `docs/MIGRATIONS.md`. No
user config, state, service, or model is altered by any command in this packet;
the rehearsal's throwaway `/tmp` tree is removed by its own `trap`.
