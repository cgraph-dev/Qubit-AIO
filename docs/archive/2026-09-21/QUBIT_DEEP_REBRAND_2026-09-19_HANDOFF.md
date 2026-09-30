# Deep rebrand — handoff for the final provenance/verification wave (2026-09-19)

Status: implementation wave complete for every safely-closable item. No commit
or push. No `DEEP_REBRAND_COMPLETE` claim. This document gives the exact,
ordered steps for the remaining items so the final wave is mechanical.

Governing records:
- `docs/QUBIT_OWNER_AMENDMENT_2026-09-19_DEEP_REBRAND.md`
- `docs/QUBIT_REBRAND_MANIFEST.json` (module/class/env/path renames)
- `docs/QUBIT_DEBRAND_MANIFEST.json` (content pass)
- `docs/QUBIT_DEBRAND_CLASSIFICATION.json` (every remaining occurrence, classed)
- `docs/QUBIT_IMPLEMENTATION_CHECKLIST_2026-09-18.md` (ledger, remaining items)

## Landed (verified this wave)

| Area | Result | Evidence |
|---|---|---|
| Package/module rename | `qubit_hermes` -> `qubit_runtime`; top-level `hermes_*` -> `qubit_*`; `voicebridge/hermes_*` -> `qubit_*` | `scripts/qubit_rebrand.py`, rebrand manifest |
| Content de-brand | prose-only, wire/legal/URL-safe; 1646 files | `scripts/qubit_debrand.py`, debrand manifest |
| Frontend | `frontend/qubit-{web,shared,app,dist}`; `@qubit/shared`; rebuilt | `scripts/build_dashboard.sh` (tsc+vite OK) |
| TUI | `@qubit/ink` + `packages/qubit-ink`; `ui-tui/dist/entry.js` rebuilt | `npm run build` OK |
| Desktop identity | `productName`/`executableName`/`artifactName`/`appId` -> Qubit; `Qubit.app`/`Qubit.exe`; `qubit.desktop`/`qubit.png` | `apps/desktop/package.json`, install/update scripts |
| Env namespace | `QUBIT_*` accepted; `HERMES_*` migration-only in one adapter | `voicebridge/qubit_legacy.py` |
| Entry points | `qubit-agent` primary; `qubit-hermes` hidden compat alias | `pyproject.toml` |
| Artifacts | route ownership (451/0), reachability, source inventory (0 omissions), config binding, OpenClaw upgrade, gap ledgers (0 code-side open), completions | generators under `scripts/` |
| Guards | public-surface 15/15, branding 5/5, routes 65/65, route-ownership+RPC 17/17, launcher/fork-import | `pytest` focused runs |

## RR2 — repair wave status (2026-09-19)

- Frontend repair class COMPLETE: the `HERMES_BASE_PATH is not defined`
  runtime defect was fixed in `frontend/qubit-web/src/plugins/usePlugins.ts`,
  `frontend/qubit-web/src/plugins/fleet.ts`, and
  `qubit_runtime/web/src/plugins/usePlugins.ts`; `frontend/qubit-dist` was
  rebuilt; `npx tsc -p . --noEmit` is clean; the focused frontend set that
  previously had 14 failures now reports 135 passed.
- Generated-artifact reconciliation: `scripts/source_inventory.py` extractors
  are now layout-aware (`hermes_cli` upstream vs `qubit_cli` fork), with an
  upstream `/api/hermes/update*` -> `/api/qubit/update*` coverage mapping
  added; `scripts/hermes_frontend_map.py` canonicalizes pre-rebrand Qubit
  paths; `scripts/hermes_vendor_manifest.py` comparison `upstream` prefix and
  regression-test refs corrected. The 27 `git_index_gaps` were force-added to
  the git index, so the vendor generator now exits 0 with `git_index_gaps=0`;
  `GIT_INDEX_GAP_COUNT` was updated to 0.
- Differential closure fixtures + `tests/test_differential_parity8u.py` +
  `docs/PARITY_HERMES.json` reconciled (135 passed); the differential
  `MANIFEST.json` fixture hashes were refreshed after the 3 `vendor_*`
  fixtures were renamed to the Qubit names.
- Stale test/comment refs reconciled; `tests/test_dashboard_control_fixes.py`
  updated to canonical `canUpdateQubit`.
- `docs/QUBIT_REBRAND_MANIFEST.json` `path_renames` completed (test-file,
  differential-fixture, and `voicebridge/hermes_*` -> `voicebridge/qubit_*`
  module renames added). `docs/QUBIT_IDENTIFIER_RENAME.json` reconciled to the
  actual on-disk renames; two false `rebuild_ledger_*`/`test_ledger_*` rename
  claims were removed because both the old and new files still exist (they are
  distinct Hermes- and Qubit-ledger generators, not a rename).
- All 13 generated artifacts were regenerated and produced byte-identical
  output across two consecutive runs.
- Focused independent verification (read-only `qubit-final-verifier`) returned
  VERIFIED for the frontend repair class and artifact coherence. It noted one
  documented, owner-authorized assertion update in
  `tests/test_source_inventory.py` (`changed == 0` -> `changed == recorded`,
  the manifest-pinned invariant after the owner-authorized deep rebrand); no
  test was deleted or disabled.
- Status is NOT `DEEP_REBRAND_COMPLETE`; full final verification (whole-suite,
  live differential, packaging, independent verifier of the whole product) has
  not run; no commit or push.

### Remaining failures / blockers

- Desktop Electron build end-to-end verification — not built in this
  environment (environmental).
- 11 OPERATOR_ONLY ledger rows (hardware/credential/operator qualification) —
  operator-only, not code failures.
- The full frozen verification matrix (whole-suite, live differential parity,
  packaging/clean-install, independent whole-product verifier) has not run and
  remains frozen until the integration lead declares `IMPLEMENTATION_COMPLETE`.
  The browser suites that were in the original failure set now pass:
  `tests/test_qubit_playwright8tf.py` 6/6 and the focused frontend set 135/135.

## Remaining items and exact steps

### R1 — Vendor/comparison provenance manifests (blocked on the rename being tracked)

`scripts/hermes_vendor_manifest.py` is now **path-rename-aware** (segment map,
`path_renames`, deep-rebrand-owned patches). A `/tmp` run reports
`extra=0`, `missing=4`, `unexplained=0`, `patched=4499`. It cannot be written in
place until `qubit_runtime/` is in the git index, because the manifest's
git-tracking checks key on `git ls-files`.

Ordered steps (owner gate; requires staging the rename, not committing):

```bash
# 1. Stage ONLY the package rename so the generator sees the tracked tree.
git add -A qubit_hermes qubit_runtime

# 2. Regenerate the manifests.
python scripts/hermes_vendor_manifest.py \
    --out docs/QUBIT_HERMES_VENDOR_MANIFEST.json \
    --comparison-out docs/HERMES_SOURCE_COMPARISON.json

# 3. Reconcile the hardcoded guard constants to the new truth:
#    tests/test_surface_reachability8u.py
#      EOL_NORMALIZED_COUNT (was 13; some eol-normalized files are now patched)
#      GIT_INDEX_GAP_COUNT  (was 26; re-measure)
#    tests/test_hermes_runtime_vendor8u.py: patch count is read from the
#      manifest, no constant change expected.
```

Acceptance: `UNEXPLAINED == 0`, `MISSING == 0`, `QUBIT_ADDED == 0`,
`set(patched) == set(patch_manifest)` with non-empty
`patch`/`reason`/`affected_regression_test`, and
`UNCHANGED + EOL_NORMALIZED + QUBIT_PATCHED == file_count`.

### R2 — Internal vendored module-graph identifiers

~6.5k `hermes*`/`Hermes*` identifiers (`get_hermes_home`, `hermes_home`,
`HermesCLI`, `hermesHome`, ...) in `qubit_runtime`. Deferred because the Qubit
guard suite and the byte manifest key on the current names, and some names are
persisted config/state keys.

Required approach (not a text replace):
1. AST/tokenizer pass over Python (NAME + Attribute + ImportFrom alias only,
   never string literals), and a TS/JS identifier pass that skips string
   literals and object keys.
2. A curated identifier map (snake/camel/SCREAMING) with collision checks
   against existing `qubit*` symbols (known collisions: `qubit_home`,
   `_qubit_home`, `QubitAgent`).
3. Persisted keys (config keys, dict keys, env) are handled only via a
   migration adapter, never renamed in place.
4. Update `tests/` and `docs/QUBIT_REBRAND_MANIFEST.json` in the same pass.
5. Re-run R1 afterwards (the patch set changes).

### R3 — Non-shipped fork material

`qubit_runtime/website/**` still carries upstream docs branding; the fork's
`tests/` now reference the rebranded source but a few wrapper-path expectations
remain upstream (`test_linux_desktop_entry` 2 failures). Neither is in the
shipped package (force-include excludes them). Either leave as provenance or
run the same classified pass with the fork's own test suite green.

### R4 — Final verification matrix (frozen until `IMPLEMENTATION_COMPLETE`)

```bash
# focused
scripts/run_tests.sh
# differential (needs pinned reference runtimes + QUBIT_DIFFERENTIAL_*_URL/TOKEN)
python scripts/differential_parity.py
# packaging / clean install / release
bash scripts/clean_install_check.sh
bash scripts/release_qualify.py
# independent verifier (separate agent), then commit/push
```

## Preservation / rollback

- Pre-rename snapshot: `/tmp/qubit-pre-rebrand-backup-20260919-184342.tar.gz`
- Pre-content snapshot: `/tmp/qubit-deep-rebrand-precontent-20260919-192329.tar.gz`
- Pristine upstream trees untouched (`/tmp/opencode/pinned/hermes-agent`,
  `/tmp/opencode/pinned/openclaw*`).
- Rollback: restore either snapshot; no commit was made, so `git checkout` of
  tracked paths plus snapshot extraction of `qubit_runtime/` restores the tree.
