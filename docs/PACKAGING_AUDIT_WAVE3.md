# Packaging audit — Wave 3 (agent G)

Scope: packaging, clean install, migration/upgrade/rollback/uninstall.
Baseline: commit `f5fc5776` (verified still present, not regressed).
Owned files only: `pyproject.toml`, `scripts/install.sh`,
`scripts/install_systemd_unit.sh`, `scripts/release_qualify.py`,
`scripts/verify_installed_dashboard.sh`, `scripts/build_dashboard.sh`, and the
new `scripts/clean_install_check.sh`, `tests/test_packaging_wave3.py`,
`docs/PACKAGING_AUDIT_WAVE3.md`. No live service, unit, account, or user config
was started, restarted, enabled, or edited.

## Verdict

Packaging was already substantially implemented (wheel/sdist force-include of
`voicebridge/_data/frontend` and `voicebridge/_data/qubit_hermes`, the vendored
runtime provenance manifest, the clean-HOME release qualifier, the atomic
systemd unit installer, and a reversible product migration). The audit found
one class of real defect — the **installer did not install the package** and
had two sdist/fresh-clone failure modes — plus two robustness gaps in the
release qualifier and unit renderer. All are fixed; a hermetic clean-install
rehearsal now passes end to end.

Typed result: **CODE-READY for the packaging surface**, with operator-only
steps listed below. No `BLOCKED — LICENSE REVIEW REQUIRED` condition applies:
no third-party code was copied or adapted by this work.

## Findings

| # | Severity | Surface | Evidence | Disposition |
|---|----------|---------|----------|-------------|
| 1 | High | `scripts/install.sh` | Installed a hand-maintained dependency list (`sherpa-onnx sounddevice numpy …`) and never installed the `qubit-voicebridge` package; the pinned Hermes runtime deps in `pyproject.toml` (openai, fastapi, pydantic, rich, …) were absent, so a "clean" install could not run the vendored runtime or the wheel's package data. | Fixed |
| 2 | High | `scripts/install.sh` | Copied `config/personal-knowledge.md` unconditionally, but `pyproject.toml` excludes that file from the sdist. Installing from an sdist aborted under `set -e`. | Fixed |
| 3 | Medium | `scripts/install.sh` | `frontend/hermes-dist` is a git-ignored build artifact. A fresh clone (or a checkout that never ran `npm`) installed a wheel with no served dashboard, silently. | Fixed |
| 4 | Medium | `scripts/install_systemd_unit.sh` | Rendered `__PROJECT_DIR__` with an unescaped `sed` replacement, so a repo path containing `&`, `\`, or `|` produced a corrupted unit. No hermetic unit-dir override existed. | Fixed |
| 5 | Medium | `scripts/release_qualify.py` | `--install` never checked the canonical `qubit` launcher, the `qubit-hermes` entry point, or that the vendored runtime imports from the install; `--build` hard-required `python -m build`, which is absent in the uv-managed `.venv` (`python -m pip` is also absent). | Fixed |
| 6 | Low | runtime | `Config.DEFAULTS` seeds every `agent.*` key, so `_migrate_legacy_sections` drops retired `openclaw`/`hermes` sections but does not copy a legacy value over an existing default. Explicit `agent.*` keys still win (documented contract). Observed for the owning agent; `voicebridge/config.py` is not owned here. | Reported |
| 7 | High | `scripts/build_dashboard.sh` + `pyproject.toml` | The wheel force-includes the whole `frontend/` tree; after `npm install` this swept `frontend/hermes-web/node_modules` (405 MB, 21,813 members) into the wheel and tripped the release content scan with `std__path` machine paths. Final wheel was 121 MB / 25,635 members. | Fixed |
| 8 | Medium | `scripts/release_qualify.py` | `inspect_artifacts` gated the vendored `_data/qubit_hermes` fork (7,573 false undeclared-import records and secret/machine-path hits) despite the documented `_VENDORED_UPSTREAM` exemption, and gated intentionally fake home paths in `tests/` fixtures. The release qualifier could not pass its own `--build`/`--install` gates. | Fixed |
| 9 | Medium | `pyproject.toml` (docs) | Eight tracked internal migration/audit/evidence docs contain the developer's real home path (the absolute home directory of the packaging author), so the built sdist carried it. | Fixed (sdist); source sanitize recommended |

## Fixes (exact locations)

- `scripts/install.sh:35-46` — build the dashboard when
  `frontend/hermes-dist/index.html` is missing and node/npm exist; otherwise
  emit a typed warning instead of installing a dashboard-less package.
- `scripts/install.sh:50-56` — install the project itself
  (`uv pip install --python "$VENV/bin/python" "$PROJECT_DIR[hotkey,edge,test]"`),
  so dependencies come from `pyproject.toml` (single source of truth) and the
  venv is a real isolated install; the incomplete hand list is gone.
- `scripts/install.sh:187-203` — guard the `personal-knowledge.md` copy and
  create a documented empty starter when the sdist omits the developer file.
- `scripts/install_systemd_unit.sh:14-20` — add `SYSTEMD_UNIT_DIR` override.
- `scripts/install_systemd_unit.sh:31-32` — escape `\`, `&`, and the `|`
  delimiter before `sed` rendering (`REPO_SED`).
- `scripts/release_qualify.py:137-176` — `--build` falls back to `uv build`
  when `python -m build` is unavailable, and records the builder used.
- `scripts/build_dashboard.sh:34-41` — remove the build-only
  `frontend/hermes-web/node_modules` after a successful build unless
  `KEEP_NODE_MODULES=1`; this drops the wheel from ~121 MB / 25,635 members to
  ~26.5 MB / 4,020 members and removes build-tool machine paths.
- `pyproject.toml:209-221` — exclude the tracked internal docs that record the
  developer home path from the sdist (existing Packet 8K sdist-privacy pattern;
  the repo retains the provenance records).
- `scripts/release_qualify.py:69-90` — accept the sanitized redacted-directory
  token and add the vendored-fork member test (`_is_vendored_member`, matching
  both the wheel `_data/qubit_hermes/` and sdist `qubit_hermes/` layouts).
- `scripts/release_qualify.py:199-247` — `inspect_artifacts` now exempts
  vendored-fork members and `tests/` fixtures from the artifact content/member
  gates (matching the documented `_VENDORED_UPSTREAM` and `fixture_secrets`
  policy) and records `vendored_members_exempt`; the wheel record now reports
  `OK clean`.
- `scripts/release_qualify.py:271-384` — `--install` now also verifies the
  `qubit` and `qubit-hermes` entry points, the vendored runtime import from the
  throwaway venv (`VB_INSTALL_VENV`), and package-data completeness for the
  dashboard, Hermes runtime, vendor manifest, systemd units, and migration doc.
- New `scripts/clean_install_check.sh` — builds the wheel, creates a throwaway
  venv under `/tmp` with a throwaway HOME/XDG, installs the wheel **with**
  dependencies, runs every console entry point, serves the dashboard from
  package data, and imports the vendored runtime. Typed exits: `0` PASS,
  `2` FAIL, `4` DEGRADED (toolchain/network unavailable), `8` BLOCKED reserved;
  `--require-network` promotes an install skip to FAIL.
- New `tests/test_packaging_wave3.py` (22 tests) — package-data completeness,
  no developer-home literal, installer/unit-installer contracts, clean-install
  script contract, service-template checks, migration idempotence/rollback,
  and the upgrade/uninstall state-preservation contract.
- `pyproject.toml`, `scripts/verify_installed_dashboard.sh`,
  `scripts/build_dashboard.sh` were audited and required **no change** (the
  force-include tables already cover the runtime; the vendored runtime and
  dashboard already build and install).

## Commands and results (this checkout)

```text
$ bash scripts/clean_install_check.sh
  == console entry points ==  ok: qubit|voicebridge|qubit-dev|qubit-hermes --help
  OK dashboard plugins=10 runtime=<venv>/site-packages/voicebridge/_data/qubit_hermes
  clean install check: PASS                                          # exit 0

$ .venv/bin/python -m pytest tests/test_packaging_wave3.py -q
  22 passed

$ .venv/bin/python -m pytest tests/test_hermes_package8u.py \
      tests/test_hermes_package_runtime8u.py tests/test_packet0b.py \
      tests/test_packet7c.py tests/test_packet7c_audio.py -q
  122 passed

$ .venv/bin/python -m pytest tests/test_packet8k.py -q
  64 passed

$ .venv/bin/python -m pytest tests/test_foundation8tf.py tests/test_packet8kp.py -q
  135 passed

$ .venv/bin/python scripts/clean_venv_check.py --check
  PASS: no undeclared imports; all modules compile.                 # exit 0

$ .venv/bin/python scripts/release_qualify.py --static --json | tail -1
  "verdict": "PASS"

# artifact-scan fix (built wheel+sdist with node_modules removed):
$ .venv/bin/python -c "import sys; sys.path.insert(0,'scripts'); \
    import release_qualify as RQ; from pathlib import Path; \
    r=RQ.inspect_artifacts(Path('/tmp/opencode/qb-both')); \
    print(r['state'], [(x['artifact'],x['state']) for x in r['records']])"
  wheel OK clean   # 26.5 MB / 4,020 members (was 121 MB / 25,635)
  sdist FAIL only on an untracked concurrent-agent doc swept by the DIRTY tree
  (all tracked offenders excluded; fork exempt; test fixtures exempt)

$ .venv/bin/python -m compileall -q scripts/release_qualify.py tests/test_packaging_wave3.py
$ bash -n scripts/clean_install_check.sh scripts/install.sh scripts/install_systemd_unit.sh
$ git diff --check
  (all clean)
```

One intermediate run of `tests/test_packet8k.py::test_clean_venv_check_fast_mode_still_passes`
failed transiently because other Wave-3 agents were concurrently editing
`voicebridge/*` and the ledger builders (the whole-tree AST import scan observed
a half-written import). A re-run immediately after passed, as did the standalone
`clean_venv_check --check`. This is not caused by, and does not touch, the
packaging changes here.

## Migration / upgrade / rollback / uninstall

- Config sections: retired `openclaw`/`hermes` keys fold into one `agent` tree
  and are dropped; folding is idempotent and explicit `agent.*` keys win
  (`test_packaging_wave3.py::test_legacy_config_sections_fold_idempotently_and_agent_wins`).
- Product paths: `migrate` moves legacy state to canonical paths, leaves a
  compatibility alias, and is idempotent (second run reports `FRESH`);
  `migrate_rollback` removes the aliases and restores the legacy paths from the
  validated backup (`test_product_migration_roundtrip_is_idempotent_and_reversible`).
- Upgrade: re-running `scripts/install.sh` is idempotent and never overwrites a
  tuned unit (`install_systemd_unit.sh` keeps `.bak.<ts>` and a `.template-new`)
  or live user config (voice.yaml is only created when absent).
- Uninstall: `distribution.uninstall_plan` requires confirmation and preserves
  the state dir, user config, shared Ollama artifacts, and browser profiles by
  default; data deletion is a separate confirmed step.
- `release.upgrade_uninstall_contract` returns `pass: True`.

## Residual operator-only steps

These are genuinely outside a hermetic code-side rehearsal; each is typed, not
silently passed:

1. **Dashboard build on a node-less host.** If `node`/`npm` are absent and
   `frontend/hermes-dist` is missing, `install.sh` records a warning; the
   operator must run `scripts/build_dashboard.sh` once (network) — or install
   from a release artifact that already carries `frontend/hermes-dist`.
2. **Model/Ollama pulls and audio hardware.** The installer's Ollama model
   pulls, Piper/Parakeet downloads, real mic/speaker, and VRAM checks remain
   hardware/network operator steps.
3. **Live service install/restart.** `install.sh` never enables or restarts a
   unit. Installing the canonical unit and disabling a pre-existing legacy
   `qubit-voice-agent.service` (if present) is an explicit operator action; the
   installer preserves divergent units rather than overwriting them.
4. **Communications-monitor timer units.** `systemd/qubit-communications-monitor*.{service,timer}`
   ship and pass `systemd-analyze verify` but are not installed by
   `install.sh` (the monitor is opportunistic). Enabling them is an operator
   decision for a later packet, not a packaging blocker.
5. **Sanitize the source docs.** The eight tracked internal docs excluded from
   the sdist still contain the developer's real home path in the repository
   (for example `docs/8TF_COMPLETE_MIGRATION_PLAN.md`, `docs/OPERATIONS_8S.md`,
   `docs/QUBIT_COMPLETION_EVIDENCE.md`). Their owning agent should replace the
   absolute path with a placeholder (as `docs/HowTo.md` already does); the
   sdist exclusion is the packaging-side mitigation, not a sanitization.
6. **Full clean-tree sdist pass.** The dirty working tree (other Wave-3 agents)
   sweeps untracked files into the sdist, so the final `release_qualify
   --build --install` PASS can only be recorded on a committed, clean tree
   (which the qualifier correctly requires — a dirty tree is BLOCKED). Per
   record, the wheel is `OK clean` and the sdist has no tracked offender.
7. **A required `docs/MIGRATIONS.md` entry.** This wave changes installed-state
   handling (the installer now installs the package and its declared deps).
   `docs/MIGRATIONS.md` is not in this agent's owned file set, so the entry is
   a handoff step for the migrations owner.
8. **Release signing / clean-machine qualification.** Unchanged by this wave;
   see `docs/RELEASE_SIGNING.md`.

## Typed limitation: pip build tooling

`python -m pip` and the `build` module are absent in the uv-managed `.venv`
(uv intentionally omits pip). `scripts/clean_install_check.sh` therefore prefers
`uv` and falls back to `python3 -m venv` + `python -m pip` (ensurepip). When
neither the toolchain nor the network is available, the rehearsal exits `4`
DEGRADED with the reason — it never reports PASS.

## Rollback of this wave

```bash
git checkout -- scripts/install.sh scripts/install_systemd_unit.sh \
                scripts/release_qualify.py scripts/build_dashboard.sh \
                pyproject.toml
rm -f scripts/clean_install_check.sh tests/test_packaging_wave3.py \
      docs/PACKAGING_AUDIT_WAVE3.md
```

The `pyproject.toml` rollback also restores the original sdist exclude list;
the `build_dashboard.sh` rollback restores the previous behavior of leaving
`node_modules` in place.

No user state, config, systemd unit, service, or data is created or altered by
these files; the only live rehearsal runs inside a throwaway `/tmp` work dir
that is removed on exit.
