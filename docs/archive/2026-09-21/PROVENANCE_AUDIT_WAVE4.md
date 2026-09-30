# Qubit provenance audit — Wave 4 (per-file dashboard provenance + branding)

Status: **WAVE 4 — reconciled; zero unexplained vendored-frontend differences**
(machine-checked by `tests/test_provenance_completeness.py`).

Owner: agent H (provenance + branding). Base commit `539649d4`. This audit
completes the per-file provenance manifest required by
[`QUBIT_DASHBOARD_REBUILD_PLAN.md`](QUBIT_DASHBOARD_REBUILD_PLAN.md) §Acceptance
gate and records the exhaustive branding scan of the vendored dashboard.

## Scope and method

Upstream pin: `NousResearch/hermes-agent@2237be355906fbe6065ce1815711eee52b2d646e`
(tag `v2026.9.7`, MIT, Copyright (c) 2025 Nous Research).

Vendored trees compared byte-for-byte against the pinned read-only oracle
(`/tmp/opencode/pinned/hermes-agent`), excluding generated
`node_modules`/`dist`/`build` and package-manager lockfiles (Qubit build
artifacts with no upstream source):

| Vendored tree | Upstream | Files | Diverging | Additions | Missing |
|---|---|---:|---:|---:|---:|
| `frontend/qubit-web` | `hermes-agent/web` | 191 | 34 | 2 | 0 |
| `frontend/qubit-shared` | `hermes-agent/qubit-shared` | 18 | 2 | 0 | 0 |
| **Total** | | **209** | **36** | **2** | **0** |

Every one of the 36 divergences is a documented `rows[]` patch (exact
`patch` + `reason` + `upstream_blob_sha256` + `qubit_sha256` +
`affected_regression_test`) and the 2 additions are documented in
`wave3.added`. There are **no missing files and no undocumented edits**.

## 1. Per-file provenance manifest

`docs/HERMES_SOURCE_COMPARISON.json` now carries, for every vendored frontend
row (213 rows across `hermes-agent/web`, `hermes-agent/qubit-shared`,
`hermes-agent/plugins`):

- `upstream` — pinned upstream path at `upstream_rev`;
- `upstream_blob_sha256` — sha256 of the pinned upstream blob content;
- `qubit` / `qubit_sha256` — vendored path and on-disk sha256;
- `patch` + `reason` — the exact divergence and why it exists (null and a
  byte-equality reason when unmodified);
- `affected_regression_test` — the on-disk test that fails if the divergence or
  its provenance record regresses.

A new top-level `provenance_schema` block documents those field semantics, and
`wave3.added` entries now carry `affected_regression_test`.

### Documented divergences and their regression tests

| Group | Count | Patch family | Affected regression test |
|---|---:|---|---|
| Rebranded dashboard source (App, pages, components, i18n, themes, `index.html`) | 30 | Qubit product rebrand of product-facing strings | `tests/test_dashboard_branding_wave1.py` |
| Shared lib (`json-rpc-gateway.ts`, `websocket-url.ts`) | 2 | `Hermes` -> `Qubit` in product-facing error copy | `tests/test_dashboard_branding_wave1.py` |
| Plugin fleet source (`plugins/index.ts`, `types.ts`, `usePlugins.ts`) | 3 | wave3 static fleet merge | `tests/test_dashboard_plugin_fleet.py` |
| `package.json` | 1 | declare vendored build/lint/test devDependencies | `tests/test_release_gates8u.py` |
| SPA-shipped plugin bundles (`achievements`, `kanban`) | 4 | wave3 rebrand + byte-identical assets | `tests/test_dashboard_plugin_fleet.py` |
| **Total documented patches** | **40** | | |
| Qubit additions (`src/plugins/fleet.ts`, `public/plugins/fleet.json`) | 2 | `wave3.added` | `tests/test_dashboard_plugin_fleet.py` |

The 40 rebrand/fleet rows are validated hash-for-hash by
`tests/test_branding_parity_wave3.py::test_vendor_manifest_hashes_are_valid`
and reconciled against the pin by
`tests/test_provenance_completeness.py::test_vendored_frontend_reconciles_with_pinned_source`.

## 2. Stale frontend-map adaptation rule fixed

`docs/HERMES_FRONTEND_MAP.json` previously claimed *"React/TS cannot execute in
Qubit's Python/static-JS stack (no React/Node runtime per project
constraints)"* — false since the owner amendment 2026-09-11 §2 permits
React/Vite/Node. Changes:

- `adaptation_rule` rewritten: the pinned Hermes React/Vite/Node frontend is
  vendored (`frontend/qubit-web`, `frontend/qubit-shared`) and built with
  Vite as the Qubit dashboard; the withdrawn "no React/Node runtime" constraint
  no longer applies. Every file is byte-identical or a documented patch in
  `docs/HERMES_SOURCE_COMPARISON.json`; routes/endpoints additionally map to
  shared backend ops, typed refusals, or documented omissions.
- 116 per-file `disposition` strings replaced
  (`adapted (static-JS adapter ...)` -> `vendored Hermes React/Vite source
  (byte-identical or documented Qubit patch ...)`).
- New `vendoring` block records the two trees, the React/Vite/Node runtime,
  the provenance manifest, and the per-file field contract.

No other `"static-JS"` / `"cannot execute"` / `"no React/Node runtime"` string
remains anywhere in the map (`test_frontend_map_has_no_stale_react_runtime_rule`).

## 3. Exhaustive branding scan of the vendored dashboard

Scanner: all text source under `frontend/qubit-web` and
`frontend/qubit-shared` (TS/TSX/JS/CSS/HTML/JSON, comments stripped for JS/TS),
plus the SPA-shipped plugin bundles and the built `frontend/qubit-dist`.
Token regex: `\bHermes\b|\bOpenClaw\b`.

Result: **zero unreplaced **product-facing** occurrences.** The only surviving
tokens are protocol identifiers and developer/source comments, which are
permitted records (owner directive 2026-09-12 §10–11; frozen contract):

| file:line | token | classification | context |
|---|---|---|---|
| `frontend/qubit-web/src/index.css:46` (×2), `:52`, `:83` | `Hermes` | developer comment | upstream `Nous DS` / `Hermes teal` theme provenance in CSS comments (not rendered) |
| `frontend/qubit-web/src/lib/api.ts:38` | `Hermes` | protocol | `X-Hermes-Session-Token` wire header |
| `frontend/qubit-web/src/lib/api.test.ts:16` | `Hermes` | protocol | `X-Hermes-Session-Token` test constant |
| `frontend/qubit-web/src/pages/SessionsPage.tsx:1486` | `Hermes` | protocol | `X-Hermes-Session-Token` / `__HERMES_SESSION_TOKEN__` |
| `frontend/qubit-web/src/lib/reasoning-effort.test.ts:9` | `Hermes` | developer test description | `"the Hermes default (medium)"` |
| `frontend/qubit-web/public/plugins/kanban/dist/style.css:2` | `Hermes` | developer comment | `Hermes Kanban — dashboard plugin styles.` |
| `frontend/qubit-dist/assets/SessionsPage-*.js:4` | `Hermes` | protocol | bundled `X-Hermes-Session-Token`/`__HERMES_SESSION_TOKEN__` |
| `frontend/qubit-dist/plugins/kanban/dist/style.css:2` | `Hermes` | developer comment | same CSS comment as source |

The Wave 3 §A gap (Achievements/Kanban bundles rendering "Hermes Agent" /
"Hermes Achievements" / "Hermes profile") is **closed**: the bundles now read
Qubit and the `wave3` patch rows carry the rebrand text. See
[`BRANDING_AUDIT_WAVE3.md`](BRANDING_AUDIT_WAVE3.md) §A for the historical list.

No `OpenClaw` token remains on any scanned surface.

## 4. Wave 4 handoff diff

`docs/HERMES_SOURCE_COMPARISON.wave4.diff` (agent F) is **not present** at the
time of writing; `frontend/**` is clean at base `539649d4` and the 36
divergences above are the complete set. `tests/test_provenance_completeness.py`
detects the diff automatically and fails if any `"qubit"` path it references is
not merged into `rows[]` / `wave3.added` / `wave4.added` (no-op while absent).

## 5. Tests and commands

```sh
python -m pytest tests/test_provenance_completeness.py \
  tests/test_hermes_vendor8tf.py tests/test_branding_parity_wave3.py \
  tests/test_hermes_frontend_map8u.py -q
git diff --check
```

Results (this checkout):

```
31 passed, 2 skipped   # provenance + vendor8tf + branding-parity + frontend-map
git diff --check       # clean
```

The skips are `test_generator_reproduces_map_and_asserts_pin` (the map
generator's oracle `/tmp/pinned-upstream/hermes-agent` is absent) and
`test_wave4_handoff_diff_is_merged_when_present` (no Wave 4 diff produced yet).

The map was edited directly (owned artifact) and the generator script
(`scripts/hermes_frontend_map.py`) still emits the old `adaptation_rule` /
dispositions. Regenerating from a present oracle would revert this fix, so the
script is flagged here as a follow-up owned outside agent H's file list.

## 6. Untested / residual risk

- Rendered Playwright/desktop/tablet/mobile visual diff was not run in this
  environment; the branding evidence is static plus the served-SPA checks in
  `tests/test_branding_parity_wave3.py`.
- `frontend/qubit-dist` is an ignored build artifact; the bundle rows above
  are informational and regenerate from `public/plugins/**`.
- The frontend-map generator drift noted in §5.

## 7. Rollback

```sh
git checkout -- docs/HERMES_SOURCE_COMPARISON.json docs/HERMES_FRONTEND_MAP.json
rm -f docs/PROVENANCE_AUDIT_WAVE4.md tests/test_provenance_completeness.py
```

No `frontend/**` file, and no commit, was modified by agent H.
