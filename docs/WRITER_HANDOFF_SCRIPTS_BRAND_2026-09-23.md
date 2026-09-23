# Writer handoff — stale-artifact generators + scripts/docs brand

Scope of this note: writer task on `scripts/` + non-frozen `docs/` only. No git
writes. Frozen records (`docs/archive/**`, `docs/differential/INTERFACE.md`) and
`docs/STATUS.md`/`docs/CHECKLIST.md` were not edited; `tests/` was **not**
edited (see §2, the required test change is documented here instead).

## 1. page_coverage stale artifact

- **Root cause:** `docs/generated/QUBIT_PAGE_COVERAGE_2026-09-21.json` was stale
  vs the live worktree (summary/rows lagged the current frontend controls:
  3366 rows vs 3382). The generator already wrote to `docs/generated/`; it only
  needed regeneration.
- **Change:** `scripts/page_coverage.py` — the input resolver is now
  `_canonical_artifact()`, canonical-`docs/generated/`-first with a **read-only**
  frozen fallback and a hard error if neither exists. The docstring and the
  artifact `method`/`sources` metadata now name canonical generated inputs; the
  frozen `docs/archive/2026-09-21/HERMES_FRONTEND_MAP.json` is recorded as
  read-only provenance that is never opened or written.
- **Regenerated artifact:** `docs/generated/QUBIT_PAGE_COVERAGE_2026-09-21.json`
  (sha256 `00504d20f46b928c91395db73be76472f30fc39d0d17350040b8836aca8e3cbd`,
  byte-identical ×2 at generation time).
- **Test:** `tests/test_generator_determinism9u.py` → **6 passed**.

## 2. source_inventory inconsistency (required TEST change — not applied)

- **Root cause:** `tests/test_source_inventory.py::test_byte_diff_is_reported`
  reads two **frozen archive** artifacts and compares them:
  `docs/archive/2026-09-21/QUBIT_SOURCE_INVENTORY.json`
  (`hermes_byte_diff.changed`, 5840 from an older fork) vs
  `docs/archive/2026-09-21/QUBIT_HERMES_VENDOR_MANIFEST.json`
  (`patched`, 5893). They are mutually inconsistent because the fork has since
  gained Qubit-added files (W4–W11 ports). The canonical `docs/generated/`
  artifacts are now consistent: inventory `changed` == manifest `patched`
  (5894) and inventory `extra` == manifest `extra_local` (33).
- **Change (scripts only):** `scripts/source_inventory.py` now
  - defaults `--out`/`--omissions` to `docs/generated/`;
  - adds `--vendor-manifest` (default canonical
    `docs/generated/QUBIT_HERMES_VENDOR_MANIFEST.json`) and records a
    `hermes_byte_diff.manifest_reconciliation` block
    (`changed_equals_patched`, `extra_equals_extra_local`).
- **Regenerated canonical artifacts:**
  - `docs/generated/QUBIT_SOURCE_INVENTORY.json`
    sha256 `c868cab77d26db44122ffb33a7a47b5b15e7b90428bcc32a78dbd8b0ebb593e8` (×2)
  - `docs/generated/QUBIT_SOURCE_OMISSIONS.md`
    sha256 `61590344265be585d7804e893e0d636171e5b6bd486ca227807347096bbff522` (×2)
  - Provenance: `--hermes /tmp/opencode/pinned/hermes-agent-2237be`
    (`2237be…`), `--openclaw /tmp/opencode/pinned/openclaw`.
- **Required test change (out of writer scope, for the orchestrator):**
  `tests/test_source_inventory.py` still hardcodes the frozen archive paths, so
  the test cannot pass without editing `tests/`. Repoint:
  ```python
  INV_JSON = REPO_ROOT / "docs/generated" / "QUBIT_SOURCE_INVENTORY.json"
  OM_MD     = REPO_ROOT / "docs/generated" / "QUBIT_SOURCE_OMISSIONS.md"
  # in test_byte_diff_is_reported:
  manifest = json.loads(
      (REPO_ROOT / "docs/generated" / "QUBIT_HERMES_VENDOR_MANIFEST.json")
      .read_text(encoding="utf-8"))
  ```
  Also reconcile the expectation `bd["counts"]["extra"] == 0` with the canonical
  `len(manifest["extra_local"])` (33) — the canonical inventory legitimately
  lists Qubit-added files; `changed == recorded` then holds exactly.
  (`QUBIT_PIN` in that test also points at `/tmp/opencode/pinned/hermes-agent`,
  which is absent; the regeneration tests skip. The real clone is
  `…-2237be`.)
- **Current test result:** 1 failed (the archive-path assertion above),
  9 passed, 3 skipped.

## 3. scripts/docs de-brand classification

Scanned `scripts/` and non-frozen `docs/` with the `scripts/qubit_debrand.py`
regexes. **De-branded (Qubit-owned product/runtime labels):**

| File | Change |
|---|---|
| `scripts/hatch_build_qubit.py` | build prune prefixes `hermes-web`/`hermes-shared` → `qubit-web`/`qubit-shared` |
| `scripts/dashboard_stress.py` | lock `/tmp/openclaw-dashboard-stress.lock` → `/tmp/qubit-dashboard-stress.lock` |
| `scripts/seed_demo_state.py` | isolated home key/dir `hermes` → `qubit_home` (`qubit-home`); report key `hermes_home` → `qubit_home` |
| `scripts/clean_install_check.sh` | required entrypoints `… qubit-hermes` → `… qubit-agent` (legacy entrypoint was removed in W0; the old check would fail) |
| `scripts/release_qualify.py` | check label/target `entrypoint.qubit-hermes` → `entrypoint.qubit-agent`; local resource key `hermes_dist` → `qubit_dist` |

**Kept (load-bearing, legal/provenance, or wire/compat):** the rest of
`scripts/` (provenance tooling: pinned upstream names, `NousResearch` repo URL,
`hermes-agent`/`openclaw` project + commit ids, historical archive paths, plugin
slugs such as `hermes-achievements`, `HERMES_*`/wire identifiers) and all
non-frozen `docs/` hits (they name the parity reference install `~/.hermes` /
`hermes-agent`, the frozen archive records, the owner amendments, or the legal
attribution). No product-surface leak was found in non-frozen docs that is not
load-bearing. `docs/BRAND.md` §footprint is itself the policy definition and is
left intact.

**Tests:** 88 branding-focused tests passed (see report); the only failures are
pre-existing and unrelated to scripts/docs:
`test_branding_parity_wave3` stale `ChatPage.voice9u.test.tsx` hash (STATUS §18)
and stale `/`→`/sessions` expectations (`test_qbpv2_rebrand_*`) superseded by
owner Ruling 3 (`/`→`/chat`, STATUS §14/§20).

## 4. Concurrency caveat

The worktree is being edited by other writer agents during this task
(`frontend/**` controls shift line numbers minute-to-minute). The
`test_page_coverage_check_matches_on_disk_artifact` test regenerates from the
live tree, so it is only green while the canonical artifact is fresh relative to
those concurrent edits. Regenerate at freeze time; the generator path fix here is
what removes the frozen-archive dependency.
