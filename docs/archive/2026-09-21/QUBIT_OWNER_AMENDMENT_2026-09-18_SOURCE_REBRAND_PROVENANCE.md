# Owner amendment 2026-09-18 — source-level rebrand provenance

Status: additive amendment. It does **not** edit, weaken, reorder, or reinterpret
any frozen acceptance contract, checklist, execution directive, or prior owner
amendment. All product, parity, safety, licensing, and completion requirements
remain in force. This file only corrects the **provenance/verification model**
for the shipped runtime, which had drifted from the owner-approved architecture.

## Why this amendment exists

The owner-approved runtime architecture (`docs/QUBIT_OWNER_AMENDMENT_2026-09-11_HERMES_RUNTIME.md`,
`docs/QUBIT_EXECUTION_DIRECTIVE_2026-09-12.md`) is a **Qubit-owned, source-level
rebrand/adaptation** of the pinned upstream implementation, executed from this
tree. The product surface must be Qubit-branded; upstream names may remain only
in licenses, provenance, comparison manifests, migration code, and internal
audit evidence (`docs/QUBIT_FROZEN_ACCEPTANCE_CONTRACT.md`, Identity promise).

An earlier vendor guard encoded the opposite assumption: that `qubit_hermes/`
must be byte-identical to the pinned upstream, with `patched == []`. That
assumption is **architecturally wrong** for a shipped source-level rebrand. It
also mis-enumerated disposable `node_modules`/build trees as `extra_local`, so
it failed for reasons unrelated to product correctness.

## Amended provenance model

Keep two clearly separated source layers:

1. **Pristine upstream baselines** (read-only): the latest Hermes/OpenClaw
   revisions and the contractual pinned revisions. Used for exact commit
   verification, byte manifests, future upgrades, source comparison, licenses,
   and provenance. Proven **byte-identical** to the pin/observed revision.
2. **Shipped Qubit runtime** (`qubit_hermes/` + `voicebridge/` + frontend):
   the maintained source-level Qubit rebrand/adaptation. It may contain
   intentional Qubit patches (branding, policy, providers, Brain4, OpenClaw
   adapters, UI). Behavioural/API/CLI/voice/UI parity is proven against the
   pristine baseline; it is **not** claimed to be byte-identical.

Replace the obsolete rule “`patched` must equal zero” with:

- pristine baseline byte identity;
- a deterministic **Qubit patch manifest**;
- every patch explained and owned (`patch`, `reason`,
  `affected_regression_test`);
- no unexplained `MISSING` or `extra_local` files;
- behavioural parity against upstream;
- a proven upgrade/import procedure from a future upstream revision.

## Recorded patches

`docs/QUBIT_HERMES_VENDOR_MANIFEST.json` now carries:

- `patched`: the sorted list of Qubit-patched fork files (currently 22);
- `patch_manifest`: per-file `{patch, reason, affected_regression_test}`;
- `classification_counts`: `UNEXPLAINED == 0`, `MISSING == 0`,
  `QUBIT_ADDED == 0`;
- `extra_local == []`, with untracked disposable `node_modules`/build/cache
  trees excluded (tracked upstream files under the same directory names, such
  as plugin dashboard `dist/*` bundles, remain in the manifest);
- `identical + eol_normalized` for byte-faithful files.

The current 22 entries are all **product-facing rebrand strings only** (no
route, logic, API, or wire-shape change): `hermes_cli/commands.py`,
`hermes_cli/skin_engine.py`, and the `ui-tui/src/**` banner/branding/theme and
slash-command copy. The executable TUI artifact `ui-tui/dist/entry.js` is a
gitignored build output and is therefore not part of the source manifest.

## Verification-model changes (guards)

These guards were updated from the obsolete rule to the amended rule; no
assertion was weakened to hide a defect:

- `tests/test_hermes_runtime_vendor8u.py`
  - `test_manifest_pins_exact_revision_and_is_clean`: still requires
    `missing_from_local == []`, `extra_local == []`, `UNEXPLAINED == 0`, and
    now requires `set(patched) == set(patch_manifest)` with a non-empty
    `patch`/`reason`/`affected_regression_test` on every entry (instead of
    `patched == []`).
  - `test_manifest_covers_every_file_on_disk`: skips untracked disposable
    dependency/build/cache trees exactly as the generator does.
  - `test_runtime_status_reports_fork_and_pin`: asserts the runtime status
    patch count equals the recorded patch manifest.
- `tests/test_surface_reachability8u.py`
  - `test_vendor_manifest_documents_eol_normalized_fork_files`: `MISSING == 0`,
    `UNEXPLAINED == 0`, every patched file explained, and
    `UNCHANGED + EOL_NORMALIZED + QUBIT_PATCHED == file_count`.

## Scope of this amendment

- No frozen acceptance criterion, safety policy, terminal status, or product
  requirement is changed.
- Pristine-baseline byte identity remains mandatory.
- The rebranded shipped tree is never claimed byte-identical to upstream.
- Requirement changes still require separate explicit owner approval.
