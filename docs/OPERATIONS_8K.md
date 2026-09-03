# Packet 8K — portability, packaging, security, and release certification

Status: **IMPLEMENTED** — hermetic release certification. Packet 8K is
hermetic-only: it builds and installs into disposable directories under the
report output dir and never touches a live service, timer, browser, account,
model, the operator's real HOME/config, or production state.
Source contract: `docs/UNIFIED_AGENT_PLAN.md` §8K, Packet 8K prompt-pack prompt.

## 1. What this packet certifies

A clean checkout can be installed, configured, upgraded, restarted, rolled
back, and uninstalled with documented dependencies, service units,
permissions, secret handling, state migration, and uninstall behavior — with
no hidden machine-specific paths, no accidental dirty files, and no dependence
on the developer's private state.

| 8K requirement | Where it is proven |
|---|---|
| **Clean-checkout install / config / upgrade / restart / rollback / uninstall** | `scripts/install.sh`, `scripts/install_systemd_unit.sh` (atomic + `.bak` rollback), `voicebridge/distribution.py` `upgrade_plan`/`uninstall_plan` — reused, never weakened |
| **Uninstall preserves state by default; data deletion needs separate confirmation** | `distribution.uninstall_plan` (`requires_confirmation: true`, `preserves_by_default`), asserted by `release.upgrade_uninstall_contract` and its tests |
| **Wheel + sdist build, inspect, clean-HOME install outside the checkout** | `scripts/release_qualify.py --build` / `--install` — imports, entry points, resources, packaged units, and smoke checks run with `-I` (no `PYTHONPATH`), a clean `HOME`, and no source-tree `sys.path` insertion |
| **Clean-venv check cannot mask missing package contents (finding #7)** | `scripts/clean_venv_check.py --install` now BUILDS the wheel, installs it, scans the INSTALLED package, and fails on a mask guard if `voicebridge` resolves inside the checkout |
| **No private developer state / machine paths / dirty files shipped** | `voicebridge/release.py` machine-path + secret scans; sdist excludes (`pyproject.toml`); artifact member/content scan rejects `browser_profiles`, live configs, untracked dirty files, secret-shaped values, machine paths |
| **Provenance, dependency constraints, licenses/notices, SBOM, hashes, reproducibility** | `release.certify` + `--build`/`--reproduce` records version/commit/dirty state, declared deps, THIRD_PARTY_NOTICES status, project license, artifact sha256, second-build comparison |
| **Artifact signature status (honest, never fabricated)** | `release.artifact_signature_status` reports STATIC capability only (`SIGNING_CAPABLE`/`NOT_CONFIGURED`); `release.verify_artifact_signature` verifies ACTUAL detached signatures against the approved full 40-hex fingerprint via gpg `VALIDSIG` and returns `SIGNED`/`INVALID`/`UNTRUSTED_SIGNER`/`NOT_SIGNED`/`DEGRADED`. Unsigned artifacts BLOCK qualification unless `--unsigned-ok` records an explicit approved unsigned-release policy |
| **`systemd-analyze verify` + security review** | `release.unit_verify` renders `__PROJECT_DIR__`, stubs ExecStart, runs `systemd-analyze verify`, and reviews unit security |
| **Python/platform/display/audio support documented** | `docs/AUDIO_SETUP.md`, `docs/VOICE_BRIDGE_SYSTEM.md`, `distribution.SUPPORTED_TARGET` (Ubuntu x86_64, Python ≥ 3.11), runbook check |
| **Upgrade from previous release / interrupted migration / newer-schema refusal / downgrade/rollback** | `docs/MIGRATIONS.md` + `portable.UnsupportedSchemaError` + 8J `_guard_newer_schema` + atomic restore — asserted by `backup_migration_check` |
| **Bounded logs / resource ceilings** | `resource_ceilings` records disk/RAM/VRAM ceilings and the monitor oneshot runtime ceiling |

## 2. The certification harness

- `voicebridge/release.py` — hermetic certification logic (scans, contracts,
  provenance, artifact inspection, unit verification, reproducibility).
- `voicebridge/resources.py` — portable resource resolution (installed package
  `_data` first, checkout second).
- `scripts/release_qualify.py` — orchestrator CLI.
- `scripts/clean_venv_check.py` — repaired clean-venv install check.
- `tests/test_packet8k.py` — hermetic focused tests.
- `docs/OPERATIONS_8K.md` — this runbook.

## 3. Run the certification

```bash
# hermetic static certification (no build, no network, no install)
python scripts/release_qualify.py --static --json

# build + inspect wheel and sdist (network: build backend only)
python scripts/release_qualify.py --build --out dist/release-8k

# install the wheel into a CLEAN venv + CLEAN HOME and run the smoke checks
# (network: dependencies; large download)
python scripts/release_qualify.py --install --out dist/release-8k

# rebuild once more and compare artifact hashes
python scripts/release_qualify.py --reproduce --out dist/release-8k

# everything
python scripts/release_qualify.py --all --out dist/release-8k --json
```

Exit codes: `0` PASS, `2` FAIL (release blocker), `4` DEGRADED (a required step
could not run and is typed — a skipped `--install` is DEGRADED, never a pass),
`8` BLOCKED (a release blocker that must be resolved first: a dirty tree for
the artifact steps, or unsigned artifacts without an approved unsigned-release
policy).

Release-artifact boundary (Packet 8K closure):

- `--build` / `--install` / `--reproduce` FAIL CLOSED on a dirty tree
  (`git status` modified/untracked) because an sdist built from a dirty
  checkout sweeps untracked personal files (for example a benchmark report
  carrying an absolute user-home path) into the artifact and would misreport
  them as a clean release. `--static` remains available on a dirty tree as a
  hermetic diagnostic. `--allow-dirty` explicitly opts into a diagnostic-only
  artifact build — it can never produce a clean-release PASS.
- Artifact content scan treats non-generic personal paths in docs/tests/UI as
  FAIL (only the documented generic placeholders such as the `example` user
  home are accepted), so personal paths are never silently classified harmless
  merely because they appear in non-runtime files.
- Actual detached signatures are verified per artifact (`<artifact>.asc` /
  `<artifact>.sig` via `gpg --status-fd` and `VALIDSIG`). A signature only
  counts as `SIGNED` when it cryptographically verifies AND the signer's FULL
  40-hex fingerprint exactly equals the approved signer fingerprint
  (`RELEASE_SIGNER_FINGERPRINT` env or `config/release-signer.fingerprint`).
  Short key IDs are never trusted. A valid signature from the wrong key →
  `UNTRUSTED_SIGNER`; a tampered/changed artifact or malformed signature →
  `INVALID`; no signature file → `NOT_SIGNED`; gpg or approved fingerprint
  missing → `DEGRADED`. `artifact_signature_status` is STATIC capability
  detection only (`SIGNING_CAPABLE` / `NOT_CONFIGURED`) and can never claim an
  artifact is signed. Unsigned artifacts BLOCK the qualification unless
  `--unsigned-ok` records an explicit approved unsigned-release policy
  (reported as `UNSIGNED_POLICY_APPROVED`, never a cryptographic claim).
- Provenance lists are parsed from `git status --porcelain=v1 -z` so the first
  modified/untracked path is reported exactly (a plain `.strip()` truncated the
  first path character). Paths with spaces, Unicode, and renames are preserved.
A faster static smoke is available from the CLI:

```bash
voicebridge package certify            # human summary (read-only)
voicebridge package certify --json     # machine-readable report
```

## 4. Clean-venv install check (repaired)

```bash
python scripts/clean_venv_check.py --check     # hermetic, fast
python scripts/clean_venv_check.py --install   # opt-in, network: build wheel,
                                               # install into a throwaway venv,
                                               # scan the INSTALLED package
```

The `--install` path no longer inserts the source checkout into `sys.path`
inside the venv. It runs the dependency-contract scan against the installed
`voicebridge` package with `python -I` (isolated: no `PYTHONPATH`, no CWD, no
user site) and asserts a mask guard — if the installed package resolves inside
the checkout, the check FAILS instead of hiding a missing wheel member.

## 5. What is never done

- No live service, account, browser, timer, model, or operator config/state is
  touched; every artifact/install lives under the report output dir.
- No secret value is printed; scans report shape names / redacted tokens.
- The developer's personal `config/task-templates.yaml` values are recorded as
  documented personalization, never shipped in the wheel and never a core gate
  failure.
- Personal launcher targets and project roots are NOT hardcoded in shipped
  code: `voicebridge/local/actions.py` now reads operator-local overrides from
  `~/.config/openclaw-voice-bridge/local-actions.yaml` (honouring
  `$XDG_CONFIG_HOME`) with portable generic defaults (launchers by name,
  home-directory file/screenshot dirs). The shipped
  `config/local-actions.example.yaml` documents the format and carries no
  machine-specific paths; `mode.portable_local_actions` verifies that.
- The gate is not weakened: core machine paths, shipped secrets, forbidden
  artifact members, broken units, missing runbooks, or missing migration
  guards all fail the certification.
- Artifact signatures are never fabricated: when no release signing key or
  tool is configured the certification records `NOT_SIGNED` (an explicit
  limitation), and the project license is the educational-project declaration
  in the root `LICENSE` file (applies only to original Qubit material;
  third-party material keeps its original license per THIRD_PARTY_NOTICES.md
  and docs/UPSTREAM_REUSE.md).
- The webui event stream is shut down cleanly: `WebUI.stop()` closes every
  event-stream subscriber so in-flight SSE handler threads exit their drain
  loop and `server_close()` never blocks on a leaked handler (a clean-clone
  suite-stall cause in `selectors.py`).

## 6. Rollback of the certification itself

The certification only writes under its `--out` dir (default `dist/release-8k`).
Deleting that directory and the new files (`voicebridge/release.py`,
`voicebridge/resources.py`, `scripts/release_qualify.py`,
`scripts/clean_venv_check.py` changes, `pyproject.toml` edits, `webui.py` /
`local_llm.py` / `orchestrator.py` resource-path edits, `cli.py` certify
subcommand, `tests/test_packet8k.py`, `docs/OPERATIONS_8K.md`,
`config/task-templates.example.yaml`,
`config/local-actions.example.yaml`, `voicebridge/local/actions.py` portability
fix) reverts the packet. The operator-local override file
`~/.config/openclaw-voice-bridge/local-actions.yaml` is outside the repository
and preserves the personal launcher targets; removing it reverts to portable
generic defaults. The machine-path cleanups in `scripts/review_*.py`,
`scripts/web_search.py`, and the executable-bit changes are individually
revertible via git. No live state is changed, so no further rollback is needed.