# Qubit Wave 5 — packaging, lifecycle, security, and provenance audit

Status: **owned surface code-ready; cross-boundary gaps reported (not merged)**

Base commit: `a4795b90baf8edcc6d4680bdf94a01e7146c2908` (frozen). Owner:
Agent 5 (roles H+I). Scope: `pyproject.toml`, `scripts/build_dashboard.sh`,
`scripts/verify_installed_dashboard.sh`, `scripts/migration_rehearsal.sh`,
`scripts/clean_install_check.sh`, `scripts/clean_venv_check.py`, new
`tests/test_qbpv_pkg5_*.py`, and this report. No `voicebridge/**`,
`frontend/**`, `qubit_hermes/**`, or existing contract was edited. No commit
was made.

Typed verdict: **CODE-READY for the packaging/lifecycle + security surfaces I
own**, with three exact cross-boundary repairs recorded in §5 for the
orchestrator. No `BLOCKED — LICENSE REVIEW REQUIRED` condition applies: no
third-party code was copied or adapted by this work.

## 1. Packaging findings and repairs

| # | Severity | Surface | Evidence | Disposition |
|---|----------|---------|----------|-------------|
| F1 | High | `pyproject.toml` sdist exclude | `docs/OPERATOR_QUALIFICATION.md` is tracked, contains the developer's real home path twice, and was swept into the sdist. The frozen test `tests/test_packaging_wave3.py:390` (`test_sdist_excludes_every_tracked_doc_with_a_developer_home_path`) **failed at the frozen commit** with `missed: ['docs/OPERATOR_QUALIFICATION.md']`. | **Fixed** (excluded from sdist). Cross-boundary sanitize recommended (§5.1). |
| F2 | Medium | `pyproject.toml` sdist exclude | The untracked wave prompt `docs/QUBIT_BYTE_PARITY_VERIFICATION_PROMPT.md` was swept into the sdist by the working-tree build and carries a developer home path. | **Fixed** (excluded from sdist). |
| F3 | High | wheel runtime docs | `voicebridge/capability_catalog.py:20` reads `parents[1]/docs/PARITY_HERMES.json` and `MATRIX_8T_F.json`; the wheel force-included neither, so `qubit capability` raised `FileNotFoundError` in a clean install (reproduced). | **Packaging half fixed**: both files now ship under `voicebridge/_data/docs/` (packaging-contract-legal). **Code half cross-boundary** (§5.2). |
| F4 | Medium | wheel runtime docs | `voicebridge/portable.py:193` reads `parents[1]/docs/MIGRATIONS.md` for the migration inventory; the wheel shipped it nowhere (returns `[]` in installs). | **Packaging half fixed** (`_data/docs/MIGRATIONS.md`). **Code half cross-boundary** (§5.2). |
| F5 | High | clean-install CLI | `voicebridge/integration_status.py:90-94` resolves `parents[1]/scripts/release_qualify.py`; `scripts/` is not packaged, so `qubit verify-release` returns `state: ERROR`, `[Errno 2] ... site-packages/scripts/release_qualify.py` in a clean venv (reproduced). Works from the checkout. | **Cross-boundary** (§5.3). |
| F6 | Low | dirty-tree build | A wheel built directly with `uv build` while `frontend/hermes-web/node_modules` exists sweeps 21,615 node_modules members (~405 MB). `scripts/build_dashboard.sh:71-74` removes it after a build and `scripts/release_qualify.py` blocks artifact builds from a dirty tree; a clean-tree artifact has 0 node_modules members. | **Documented** (no owned-file change needed). |

### F1/F2 fix (exact `pyproject.toml` change, sdist `exclude`)

```toml
  "docs/OPERATOR_QUALIFICATION.md",
  "docs/QUBIT_BYTE_PARITY_VERIFICATION_PROMPT.md",
```

The repository remains the provenance record; the release sdist carries
neither file. This is the same Packet-8K mitigation already used for the other
internal docs (the source docs still need the owner/`scripts/sanitize_docs.py`
sanitize; see §5.1).

### F3/F4 fix (exact `pyproject.toml` change, wheel `force-include`)

```toml
"docs/PARITY_HERMES.json" = "voicebridge/_data/docs/PARITY_HERMES.json"
"docs/MATRIX_8T_F.json" = "voicebridge/_data/docs/MATRIX_8T_F.json"
"docs/MIGRATIONS.md" = "voicebridge/_data/docs/MIGRATIONS.md"
```

These destinations stay under `voicebridge/_data/`, so
`tests/test_packaging_wave3.py::test_force_include_destinations_live_under_package_data`
still holds. The code must resolve them through `voicebridge.resources`
(§5.2).

## 2. Package contents evidence

Built from a clean source copy (`TMPDIR=/tmp/opencode/qbpv/tmp5`,
`rsync`-excluding `.git`, `.venv`, `node_modules`, `__pycache__`) so the
archives reflect a node_modules-free tree:

```text
$ uv build --out-dir /tmp/opencode/qbpv/tmp5/dist_clean
qubit_voicebridge-1.0.0-py3-none-any.whl   27,659,596 bytes
qubit_voicebridge-1.0.0.tar.gz             29,680,959 bytes

# wheel (74,143,940 uncompressed bytes / 4,012 files)
  voicebridge/_data/qubit_hermes/...            3,436 members  (fork runtime)
  voicebridge/_data/frontend/hermes-dist/...        60 members  (built dashboard)
  voicebridge/_data/frontend/hermes-qubit/...        22 members  (Qubit plugin bundles)
  voicebridge/_data/docs/{PARITY_HERMES,MATRIX_8T_F}.json, MIGRATIONS.md
  docs/QUBIT_HERMES_VENDOR_MANIFEST.json             (provenance)
  qubit_voicebridge-1.0.0.dist-info/{METADATA,entry_points.txt,
      licenses/LICENSE,licenses/THIRD_PARTY_NOTICES.md,RECORD}
  node_modules members: 0     /home/trick members: 0

# sdist (4,703 files)
  qubit_hermes/...                              3,436 members
  frontend/hermes-dist/...                         60 members
  internal docs (OPERATOR_QUALIFICATION | BYTE_PARITY | FINAL_CODE_READY): 0
```

`entry_points.txt` declares `qubit`, `voicebridge`, `qubit-dev`,
`qubit-hermes`. `METADATA` carries the Qubit license text plus
`License-File: LICENSE` and `License-File: THIRD_PARTY_NOTICES.md`.

## 3. Clean-install + real CLI evidence

```text
$ TMPDIR=/tmp/opencode/qbpv/tmp5 bash scripts/clean_install_check.sh
== toolchain: uv ==
== build wheel ==            dashboard bundle shipped in wheel
== throwaway venv (under /tmp/opencode/qbpv/tmp5/.../venv) ==
== install wheel + declared deps (network) ==
  ok: qubit --help | voicebridge --help | qubit-dev --help | qubit-hermes --help
OK dashboard plugins=10 runtime=<venv>/site-packages/voicebridge/_data/qubit_hermes
clean install check: PASS                                            # exit 0

$ TMPDIR=/tmp/opencode/qbpv/tmp5 bash scripts/verify_installed_dashboard.sh
OK root->/hermes/  asset=assets/index-BFKNJjki.js  plugins=10  ops=510
OK runtime=<venv>/site-packages/voicebridge/_data/qubit_hermes module=.../hermes_cli/main.py
installed dashboard gate: PASS
installed Hermes runtime gate: PASS                                  # exit 0
```

Real CLI from the isolated venv (`/tmp/opencode/qbpv/tmp5/venv`, parent
outside the checkout), with a throwaway `HOME`/XDG:

```text
$ qubit status        -> "agent runtime : qubit-native", e-stop "clear",
                         ASR/TTS/voice-ID typed FAILED (models not downloaded)
$ qubit product migrate --json  -> state "ABSENT" pairs (dry-run; nothing moved)
$ qubit package uninstall       -> requires_confirmation true; preserves state/config
$ qubit auth          -> {"providers": []} (credential-reference names only)
$ qubit runtime status          -> {"daemon": "none-required", ...}
$ qubit voice commands          -> 11,436 lines of typed command registry
$ qubit voice --dry-run --mute say "list voice commands"  -> "Done."
$ qubit capability              -> REPRODUCED FAILURE in a stock install
                                   (FileNotFoundError .../site-packages/docs/
                                    PARITY_HERMES.json) — packaging half now
                                   ships the file; code half is §5.2
$ qubit verify-release          -> REPRODUCED FAILURE in a clean install
                                   (state ERROR .../site-packages/scripts/
                                    release_qualify.py); PASS from the checkout
```

`qubit webui` proves the single-listener rule: with no bridge running it
refuses to start a second one — `dashboard: UNAVAILABLE ... the shortcut never
starts a second bridge`.

## 4. Lifecycle evidence

```text
$ PYTHON=.venv/bin/python bash scripts/migration_rehearsal.sh
   migration: 7 synthetic state file(s) preserved byte-for-byte
   canonical trees present; legacy compat aliases point at them
   validated backup present; credentials excluded from the backup
   upgrade: 7 preserved; upgrade preserves user config, user state,
            memory/reports/cursors, model artifacts, browser profiles,
            permissions
   rollback: 7 preserved; canonical trees removed; legacy trees restored whole
   uninstall: requires confirmation and preserves state/config
migration rehearsal: PASS                                            # exit 0

$ .venv/bin/python scripts/rollback_drill.py
   ... 6 flags flipped/restored, user data preserved
rollback drill: PASS                                                 # exit 0
```

Read-only / disk-full / path-traversal / symlink cases are covered by the new
hermetic tests (no owned script change required; the product paths already
fail closed):

- `test_migration_refuses_a_backup_inside_the_source` — path traversal
  (backup nested in the migration source) → `FAILED`, nothing moved.
- `test_rollback_does_not_follow_a_symlink_out_of_the_root` — symlink backup
  entry restored **as a symlink**, never dereferenced.
- `test_rollback_disk_full_fails_closed` — injected `OSError(ENOSPC)` →
  `FAILED`, `blocks_resume: True`, detail names disk-full.
- `test_rollback_read_only_parent_fails_closed` — read-only parent → typed
  `FAILED` + `blocks_resume: True`.
- `test_ensure_owned_dirs_dry_run_creates_nothing` — read-only planning.

## 5. Cross-boundary gaps (exact changes; orchestrator-owned files)

### 5.1 Sanitize the tracked source docs (owner)

`docs/OPERATOR_QUALIFICATION.md:90` and `:460` still contain the developer's
real home path. Run the existing sanitizer:

```sh
python3 scripts/sanitize_docs.py        # replaces /home/<user> with $HOME
python3 scripts/sanitize_docs.py --check
```

The sdist exclusion (F1) is the packaging mitigation; this is the source
repair. `scripts/sanitize_docs.py` is unowned and already implements it.

### 5.2 Resolve install-time docs through `voicebridge.resources`

The packaging half (F3/F4) ships the files under `voicebridge/_data/docs/`.
The code must prefer package data. Proposed exact change:

```python
# voicebridge/capability_catalog.py  (replace _load, lines 19-21)
from . import resources

def _load(name: str) -> Dict[str, Any]:
    path = resources.resource_path(f"docs/{name}")
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)
```

```python
# voicebridge/portable.py  (_collect_migrations, line ~193)
from . import resources
mig = resources.resource_path("docs/MIGRATIONS.md")
```

`resources.resource_path` already prefers `<pkg>/_data/<name>` and falls back
to the checkout (`voicebridge/resources.py:24-33`), matching the pattern at
`voicebridge/webui.py:3168`. After this change `qubit capability` and the
portable migration inventory work from a clean install and from the repo.

### 5.3 Degrade `qubit verify-release` in an installed package

`voicebridge/integration_status.py:90-94` unconditionally `runpy`s
`parents[1]/scripts/release_qualify.py`, which is not packaged. Proposed
change (keep it fail-closed and typed, never a raw `ERROR`):

```python
script = Path(__file__).resolve().parents[1] / "scripts" / "release_qualify.py"
# An installed package has no scripts/ tree:
if not script.is_file():
    return {"schema": "qubit-verify/1", "state": "UNAVAILABLE",
            "detail": "release qualification requires a source checkout "
                      "(scripts/release_qualify.py is not packaged)"}
```

Alternatively package `scripts/release_qualify.py` (and the `pyproject.toml`
it inspects) as an explicit release-tooling data set and resolve it from
package data. `scripts/` is deliberately not in the wheel, so the typed
degradation is the smaller, safer change and keeps one authority.

### 5.4 Follow-up audit of other `parents[1]/scripts` users

`voicebridge/release.py:903` and `voicebridge/local/actions.py:217,313,324,347`
also reach into a repo `scripts/` tree. They are not exercised by the current
clean-install gate; the orchestrator should add them to
`scripts/clean_install_check.sh` coverage or confirm they degrade typed.

## 6. Security and provenance findings (role I)

Single-authority audit (all hermetic; no duplicate safety authority found for
the owned scope):

- **One product listener**: `voicebridge/webui.py:3426` creates the single
  `ThreadingHTTPServer`. `voicebridge/plugin_runtime.py:874`'s
  `_LoopbackServer` is an ephemeral, loopback-only MCP endpoint for a plugin
  invocation, not a second product front door or dashboard.
- **One durable e-stop authority**: `voicebridge/power.py:1332`
  (`power_control_store`) → `PowerControlStore`; `voicebridge/broker.py:131`
  (`estop_engaged`) and `voicebridge/plugins.py:126` read it and fail closed.
  `voicebridge/ghost.py:1168` and `voicebridge/opencode/runner.py:151`
  delegate to that shared control; neither owns a second latch.
  `PowerControl.emergency_stop`/`reset` require an authenticated human
  (`voicebridge/power.py:1494` → `actions.require_human_principal`).
- **One confirmation/audit journal**: `approvals.jsonl` via
  `ApprovalJournal` (`voicebridge/cli.py:3682`, `voicebridge/webui.py:3337`).
  Module-local `_audit` helpers (e.g. `voicebridge/files.py:54`,
  `voicebridge/pairing.py:192`) are domain logs, not policy authorities.
- **One resource guard**: `voicebridge/vram_guard.py` (VRAM headroom; fail
  closed). `voicebridge/resources.py` is package-data resolution, not policy.

Egress: `voicebridge/hermes_egress_guard.py` denies any non-loopback outbound
connect/resolve while an in-process fork request is in flight; `_host_allowed`
is exact-match so `localhost.evil.com` is denied. Guarded by
`test_egress_guard_denies_non_loopback_inside_fork_scope`.

Redaction: `voicebridge/redaction.py` masks bearer/JWT/`sk-`/URL-userinfo/
secret-key material and secret-named object keys. Guarded by
`test_redaction_masks_credentials_and_keeps_benign_text`.

Secret scan: a high-confidence live-credential scan over
`voicebridge/ frontend/ scripts/ templates/ config/ systemd/` (tracked and
untracked, build dirs/vendored data skipped) found **no live secret**. One
synthetic `sk-…` fixture exists at `voicebridge/evaluation/eval_set.py:58`
(obviously fake; used to exercise redaction) and is intentionally not a
live-credential shape in the scanner. Guarded, with a planted-token
self-test, by `test_secret_scanner_catches_planted_credentials` and
`test_no_live_secrets_in_product_surfaces`.

Licenses/attribution: `THIRD_PARTY_NOTICES.md` retains the required
attributions (Nous Research, Alibaba Group Holding Ltd., Atai Barkai, AG UI
Protocol Contributors, Northtek, Kristian Baer); the vendored
`qubit_hermes/LICENSE` is the untouched MIT notice. The `pyproject.toml`
license text scopes Qubit's license to original material only and does not
relicense upstream code. Dependency locks: the fork's runtime core stays
exactly `==`-pinned. Guarded by
`test_legal_notices_and_attribution_are_intact`,
`test_project_license_metadata_does_not_relicense_upstream`,
`test_vendored_runtime_dependencies_are_exact_pinned`, and
`test_vendored_runtime_provenance_manifest_declares_the_pin`.

## 7. Tests

```text
$ python3 -m pytest tests/test_qbpv_pkg5_packaging.py tests/test_qbpv_pkg5_security.py -q
24 passed, 1 skipped        # skip = archive scan; set QBPV_ARTIFACT_DIR to run it

$ QBPV_ARTIFACT_DIR=/tmp/opencode/qbpv/tmp5/dist_clean \
    python3 -m pytest tests/test_qbpv_pkg5_packaging.py tests/test_qbpv_pkg5_security.py -q
25 passed

$ python3 -m pytest tests/test_qbpv_pkg5_packaging.py tests/test_qbpv_pkg5_security.py \
    tests/test_packaging_wave3.py tests/test_security_parity_wave3.py \
    tests/test_security_parity_wave4.py -q
100 passed
```

`tests/test_packaging_wave3.py` was 24 passed / 1 failed at the frozen commit
(F1); it is now 25 passed.

## 8. Untested / hardware-gated

- Live mic/speaker, ASR/TTS/voice-ID models, VRAM budget, and Ollama pulls
  (operator-only; `qubit status` types them `FAILED`).
- Release signing (`gpg` keyring) and clean-machine qualification
  (`docs/RELEASE_SIGNING.md`).
- Rendered Playwright desktop/tablet/mobile visual diff (static archive +
  served-SPA probes only).
- `install.sh` systemd unit install/restart is never run (operator action).

## 9. Rollback of this wave

```sh
git checkout -- pyproject.toml
rm -f tests/test_qbpv_pkg5_packaging.py tests/test_qbpv_pkg5_security.py \
      docs/QUBIT_QBPV_PACKAGING_SECURITY.md
```

No user state, config, systemd unit, service, model, browser profile, or
provenance record was created, enabled, restarted, or removed. All live
rehearsals ran inside a throwaway `/tmp/opencode/qbpv/tmp5` workspace that is
disposable.
