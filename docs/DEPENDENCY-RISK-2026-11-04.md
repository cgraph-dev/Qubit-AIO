# Dependency-risk triage — 2026-11-04

**Status:** measured snapshot + safe-fix record. **Not `VERIFIED`, not a release verdict.**
No live service/config was changed, no full matrix was run, and nothing was pushed.

This report triages the open Dependabot alerts on `cgraph-dev/Qubit` production and the
package manifests in this tree, then records the non-major fixes applied in this lane.

## 1. Snapshot and commands

Dependabot is enabled on `cgraph-dev/Qubit` (not on `Qubit-AIO`, which reports
`Dependabot alerts are disabled`). The alerts were read with the REST API:

```bash
gh api --paginate "repos/cgraph-dev/Qubit/dependabot/alerts?per_page=100&state=open" \
  -H 'Accept: application/vnd.github+json' > /tmp/dep-triage/alerts-all.json
```

`page=` is rejected by this endpoint; `--paginate` is required. The snapshot is against the
default branch `cccc698e2`. The lane base `8d5de4acf` has byte-identical dependency manifests
(`git diff 8d5de4acf..origin/main -- '*package-lock.json' '*package.json' pyproject.toml
qubit_runtime/uv.lock qubit_runtime/pyproject.toml` is empty), so the alert set applies to this
tree.

The npm instruments were run from each lockfile directory:

```bash
npm audit --package-lock-only --omit=dev --json   # shipped/runtime view
npm audit --package-lock-only --json              # full tree (includes dev/build)
```

The four shipped npm lockfiles are `frontend/qubit-web/package-lock.json` (dashboard build),
`qubit_runtime/package-lock.json` (workspace lock covering runtime deps plus the desktop/TUI/web
workspaces), `qubit_runtime/scripts/whatsapp-bridge/package-lock.json` (runtime sidecar), and
`qubit_runtime/plugins/platforms/photon/sidecar/package-lock.json` (runtime sidecar). The fifth
lockfile, `qubit_runtime/website/package-lock.json`, builds the separately deployed docs site and
is not in the product wheel.

Both instruments disagree by design: Dependabot counts an alert per manifest and uses its own
dependency graph; `npm audit` rolls advisories up the tree and can flag build-only packages in
`--omit=dev` when a prod dependency reaches them. Counts below name which instrument produced
them.

## 2. Headline counts (Dependabot, 474 open alerts)

| Slice | Count |
|---|---:|
| Total open alerts | **474** |
| Critical | 32 |
| High | 205 |
| Medium | 185 |
| Low | 52 |
| Runtime scope (Dependabot's field) | 342 |
| Development scope (Dependabot's field) | 132 |
| npm alerts | 264 |
| pip alerts | 210 |
| Distinct GHSAs | **239** (npm 128, pip 111) |

The 474 alerts are not 474 independent defects: `Pillow` alone produces 123 alerts (41 distinct
advisories × 3 Python manifests) and `undici` 50 alerts across four manifests.

### By manifest

| Manifest | Alerts | Critical | High | Medium | Low | Role |
|---|---:|---:|---:|---:|---:|---|
| `qubit_runtime/package-lock.json` | 108 | 4 | 47 | 39 | 18 | npm workspace: runtime deps + desktop/TUI/web build |
| `qubit_runtime/uv.lock` | 107 | 8 | 51 | 43 | 5 | vendored runtime dev/test lock |
| `pyproject.toml` (root) | 61 | 7 | 29 | 25 | 0 | shipped `qubit-voicebridge` wheel |
| `qubit_runtime/website/package-lock.json` | 53 | 5 | 22 | 16 | 10 | public docs site (not in product wheel) |
| `qubit_runtime/pyproject.toml` | 42 | 6 | 23 | 13 | 0 | vendored runtime declarations |
| `qubit_runtime/apps/desktop/package.json` | 31 | 2 | 10 | 11 | 8 | desktop app workspace |
| `frontend/qubit-web/package-lock.json` | 22 | 0 | 6 | 13 | 3 | dashboard build |
| `qubit_runtime/plugins/platforms/photon/sidecar/package-lock.json` | 21 | 0 | 8 | 9 | 4 | shipped Photon sidecar |
| `qubit_runtime/ui-tui/package.json` | 9 | 0 | 4 | 4 | 1 | TUI (committed `dist/entry.js`) |
| `qubit_runtime/apps/bootstrap-installer/package.json` | 7 | 0 | 1 | 4 | 2 | installer build |
| `qubit_runtime/web/package.json` | 6 | 0 | 1 | 4 | 1 | dashboard build |
| `qubit_runtime/scripts/whatsapp-bridge/package-lock.json` | 5 | 0 | 2 | 3 | 0 | shipped WhatsApp sidecar |
| `qubit_runtime/tests-js/package.json` | 2 | 0 | 1 | 1 | 0 | JS tests |

## 3. What this lane fixed

172 of the 474 alerts (108 distinct GHSAs) are resolved by the changes below, measured by
re-evaluating each alert's vulnerable range against the updated manifests/lockfiles. No alert was
dismissed in GitHub; the Dependabot list refreshes after merge to the default branch. The distinct
counts overlap (108 fixed + 154 remaining ≠ 239) because one advisory can be fixed in one manifest
and still present in another, e.g. `dompurify` in the product vs the docs site.

### Python (root `pyproject.toml`, vendored `qubit_runtime/pyproject.toml`, `uv.lock`)

| Package | Before | After | Alerts |
|---|---|---|---|
| `certifi` | 2026.5.20 | 2026.10.18 | 3 |
| `PyJWT[crypto]` | 2.13.0 | 2.15.0 | 18 |
| `pypdf` | 6.21.2 | 6.21.4 | 7 |
| `urllib3` floor | `>=2.7.0` | `>=2.8.0` (lock 2.8.0) | 3 |
| `aiohttp` | 3.14.3 | 3.14.4 | 8 |
| `brotlicffi` | 1.2.0.2 | 1.2.1 (lock 1.2.1.0) | 1 |
| `mem0ai` | 2.0.10 | 2.0.18 | 1 |
| `pyasn1` | 0.6.4 | 0.6.5 | 4 |
| `tornado` | 6.5.8 | 6.6 | 6 |
| `msgpack` | 1.2.1 | 1.2.4 | 1 |
| `multidict` | 6.7.1 | 6.9.1 | 1 |
| `yarl` (aiohttp transitively) | 1.24.5 | 1.25.1 | — |

`cd qubit_runtime && uv lock --upgrade-package ...` regenerated the lock; `uv lock --check`
passes. Root and vendored pins were kept in sync.

### npm

| Lockfile | Package | Before → After | Audit result |
|---|---|---|---|
| `qubit_runtime/scripts/whatsapp-bridge` | `express` / `body-parser` override | 4.21.2 → 4.22.3 / 1.20.8 → 1.20.9 | prod **5 → 0** |
| | `qs`, `sharp`, `music-metadata` | 6.16.0→6.16.2, 0.35.4→0.35.5, 11.15.0→11.16.1 | |
| `qubit_runtime/plugins/platforms/photon/sidecar` | `undici` / `@grpc/grpc-js` / `zod` | 7.29.0→7.30.1 / 1.14.4→1.14.7 / 4.5.4→4.6.7 | prod **3 → 0** |
| `frontend/qubit-web` | `sanitize-html`, `source-map-js`, `deepmerge` | 2.17.7→2.18.1, 1.2.1→1.2.2, 4.3.1 removed from that tree | prod **3 → 0** |
| `qubit_runtime` (workspace) | `js-yaml`, `dompurify`, `yaml` | 4.3.1→4.3.2, 3.4.13→3.4.17, 2.8.1→2.9.1 | prod **21 → 13** |
| | `undici` (6.x/7.x), `zod`, `ip-address`, `brace-expansion` | 6.28.0→6.29.1, 7.29.0→7.30.1, 4.4.3→4.6.7, 10.3.1→10.7.1, 5.0.9→5.0.12 | |
| | `@xmldom/xmldom`, `fast-uri`, `follow-redirects`, `source-map-js` | 0.8.13→0.8.15, 0.9.10→0.9.12, 3.1.6→3.1.9, 1.16.0→1.16.2, 1.2.1→1.2.2 | |
| | `sanitize-html` chain | 2.17.7→2.18.1 (`launder` 1.7.2, `is-plain-object` 5.1.0, `dayjs` 1.11.23; the workspace `postcss` override stays at 8.5.23) | |
| `qubit_runtime/ui-tui` | `dist/entry.js` | rebuilt with `undici` 6.29.1 | bundle now carries the patched `undici` |

The workspace lock was updated in place (not regenerated wholesale) so undeclared historical
entries were not pruned. `npm ci --dry-run --legacy-peer-deps` validates the result.

### Verification for the fixes

- Python: throwaway Python 3.14 venv with the new pins — `PyJWT` 2.15.0 (`HS256` + `RS256` with
  `cryptography` 50.0.1), `certifi` 2026.10.18, `pypdf` 6.21.4, `aiohttp` 3.14.4,
  `brotlicffi` 1.2.1.0, `pyasn1` 0.6.5, `tornado` 6.6, `msgpack` 1.2.4, `multidict` 6.9.1,
  `mem0ai` 2.0.18 all import and pass functional smoke checks.
- Focused repo tests: `tests/test_doc_extract8u.py` **18 passed, 1 skipped**;
  `qubit_runtime/tests/plugins/memory/test_mem0_backend.py` +
  `qubit_runtime/tests/qubit_cli/test_proxy.py` **37 passed**.
- WhatsApp: `node --test` security/allowlist/helpers/outbound/gate tests **25 passed**;
  `qubit_runtime/tests/plugins/platforms/test_whatsapp_bridge_security.py` **9 passed**.
- Photon: `npm ci` clean; all sidecar `.mjs` pass `node --check`; photon tests **150 passed**.
- Frontend: `npm ci --legacy-peer-deps`, `npm run typecheck` clean, `npm run test`
  **157 files / 1255 tests passed**, `npm run build` succeeded.
- TUI: `npm ci --legacy-peer-deps` clean; `npm run build` regenerated `dist/entry.js`; the
  rebuilt bundle passes `node --check`; `npm test` **1636 passed, 1 failed** — the failure
  (`modelPicker.test.ts`) is in a file byte-identical to the lane base and is pre-existing.
  `npm run typecheck` reports one pre-existing error in `createGatewayEventHandler.ts` (also
  unchanged).
- `git diff --check` clean. No full matrix was run (per lane instructions).

## 4. Remaining: 302 alerts (154 distinct GHSAs)

After the fixes, Dependabot's split would be **24 critical / 125 high / 118 medium / 31 low**
(remaining), with 212 runtime-scope and 90 development-scope by Dependabot's field. The
runtime-scope number overstates product exposure because Dependabot labels the docs-site build
and npm build tooling as `runtime`.

### A. Shipped product runtime — real risk, not safely fixable here

| Package | Current | Fixed | Why not applied |
|---|---|---|---|
| `Pillow` | 12.3.0 | 13.0.0 | **Major.** 41 advisories (5 critical) × 3 Python manifests = 123 alerts. Needs image/vision compatibility testing and an owner decision. |
| `simple-git` + `@simple-git/argv-parser` | 3.36.0 / 1.1.1 | 4.0.2 / 2.0.1 | **Major.** 3 advisories (1 critical) in the desktop app; the 4.x API changes need a desktop test pass. |
| `filelock` | 3.32.4 | 4.2.1 / 4.0.9 | **Major.** 12 medium advisories; transitive in the runtime lock. |
| `oauthlib` | 3.3.1 | 4.0.0 | **Major.** 4 advisories (2 high). |
| `opentelemetry-api` | 1.39.1 | 1.45.1 | Supply-chain constrained (`uv lock` will not move it); needs a dependency review. |
| `PyJWT` | 2.15.0 | — | `GHSA-gvp8-978c-rx2q` has **no patched version** (2 alerts). |
| `katex` | 0.16.47 | 0.18.2+ | Low; 0.x minor bump in desktop/installer math rendering. |
| `postcss-selector-parser` | 6.0.10 | 6.1.5 | The offered fix is a **downgrade** of `@tailwindcss/typography` to 0.5.4; build-time. |
| `extract-zip` | 2.0.1 | — | **No patched version** (2 high, dev tooling). |
| `deepmerge` | 4.3.1 | — | **No patched version** (`GHSA-gcqx-w48m-w2h4`, 3 low). |

### B. Dev/build-only (not in the running product)

`vite` 30 alerts (fix 8.3.5 is a safe minor, but re-resolves the whole workspace graph);
`electron` 28 (major upgrade; see `docs/ELECTRON-44-MIGRATION.md`); `axios` 9; `vitest` /
`@vitest/mocker` 9; `brace-expansion` 5; `mcp` 4 (test extra); `setuptools` 1 (dev/build;
deliberate 83.0.0 floor for torch); frontend dev `undici` 5. These are tracked but were not
bumped in this lane because a lock re-resolution would sweep large unrelated graphs or require
a major toolchain decision.

### C. Public docs website (`qubit_runtime/website/package-lock.json`, 53 alerts)

Docusaurus 3.10.2 (5 critical by Dependabot; `npm audit --omit=dev` reports 20 critical because
it rolls the tree up) plus `fast-uri`, `joi`, `dompurify`, `follow-redirects`, `qs`,
`launch-editor`, `brace-expansion`, `svgo`, `browserslist`, `tinypool`, `shell-quote`,
`proxy-addr`, `compression`, `nanoid`, and others. This tree is **not in the product wheel**; it
is the separately deployed docs site. Options are pinning Docusaurus 3.7.0 (a downgrade) or
waiting for a patched 3.10.x; that is an owner/site decision.

## 5. Owner decisions

| # | Decision | Evidence |
|---|---|---|
| 1 | Approve or reject **Pillow 12.3.0 → 13.0.0**. | 123 alerts / 41 advisories / 5 critical; root + vendored pins; run image/vision tests before merging. |
| 2 | Approve or reject **simple-git 3.36.0 → 4.0.2** (and `@simple-git/argv-parser` 2.0.1). | 6 alerts + 1 critical chain in the desktop app; 4.x API migration. |
| 3 | Choose the **Electron** upgrade line (42.11.6 / 45.x). | 28 alerts; desktop shell; `docs/ELECTRON-44-MIGRATION.md` records the Widevine/ABI blockers. |
| 4 | Approve **filelock 4.x** and **oauthlib 4.0.0** majors. | 16 advisories across the runtime lock. |
| 5 | Accept the **no-fix advisories** (`PyJWT GHSA-gvp8-978c-rx2q`, `deepmerge GHSA-gcqx-w48m-w2h4`, `extract-zip` × 2). | No patched version exists; document as accepted or remove the dependency. |
| 6 | Decide **`opentelemetry-api`**: lift the transitive constraint or accept 1 medium advisory. | `uv lock --upgrade-package opentelemetry-api` stays at 1.39.1. |
| 7 | Decide the **docs site**: pin Docusaurus 3.7.0, wait for 3.10.x, or accept the build-only risk. | 53 alerts, 5 critical by Dependabot. |
| 8 | Optional: bump test extra `mcp` 2.0.0 → 2.2.0 and dev `setuptools` 83 → 84.1.0. | Test/dev only; no product runtime effect. |
| 9 | Optional: bump `vite` 8.2.0 → 8.3.5 and `vitest` 4.1.10 → 4.1.12 in the workspace. | Dev/build only; safe minor/patch, but a full workspace lock re-resolution. |

## 6. How to re-measure

```bash
# Alerts
gh api --paginate "repos/cgraph-dev/Qubit/dependabot/alerts?per_page=100&state=open" \
  -H 'Accept: application/vnd.github+json' | jq 'length'

# npm shipped view (after the fixes: frontend 0, whatsapp 0, photon 0, runtime 13)
for d in frontend/qubit-web qubit_runtime qubit_runtime/scripts/whatsapp-bridge \
         qubit_runtime/plugins/platforms/photon/sidecar; do
  (cd "$d" && npm audit --package-lock-only --omit=dev --json | jq '.metadata.vulnerabilities')
done

# Python lock consistency
(cd qubit_runtime && uv lock --check)
```

**Honest limits:** alerts were not dismissed in GitHub; the list updates on merge. `npm audit` and
Dependabot disagree on the docs site (56 vs 53 total alerts, 20 vs 5 critical) and on some
build-only packages; both are reported rather than reconciled. No full matrix, no live
service/config change, no publish, and no `VERIFIED` claim.

## 7. Addendum — Python criticals applied on `fix5/pydeps` (2026-11-05)

Branch `fix5/pydeps` (base `b257b1b08`) applied the remaining Python critical/major
fixes without publishing. Files changed: root `pyproject.toml`,
`qubit_runtime/pyproject.toml`, `qubit_runtime/uv.lock`.

| Package | Before | After | Notes |
|---|---|---|---|
| `Pillow` | 12.3.0 | **13.0.0** | Root and vendored pins. `[tool.uv] override-dependencies` forces it because `browser-harness==0.1.13` (newest published) hard-pins `pillow==12.3.0`; browser-harness only uses stable public Pillow APIs. |
| `filelock` | 3.32.4 | **4.2.6** | New explicit floor `>=4.2.6,<5` in both pyprojects. 4.2.6 is the newest 4.x that clears the repo's 3-day `exclude-newer` quarantine (4.2.7–4.2.9 are too new). |
| `oauthlib` | 3.3.1 | **4.0.1** | New explicit floor `>=4.0.1,<5` in both pyprojects. |
| `opentelemetry-api` | 1.39.1 | 1.39.1 | **Not moved.** `opentelemetry-sdk==1.39.1` and `opentelemetry-exporter-otlp-proto-http==1.39.1` pin `opentelemetry-api==1.39.1` exactly; `uv lock --upgrade-package opentelemetry-api` reproduces 1.39.1. Lifting it requires a coordinated bump of the pinned SDK/exporter set. |
| `PyJWT` | 2.15.0 | 2.15.0 | No patched release exists for `GHSA-gvp8-978c-rx2q`; left as a documented accepted no-fix advisory (2 alerts). |

Verification (focused, no full matrix):

- `(cd qubit_runtime && uv lock --check)` → `LOCK_CHECK_OK`.
- Temp Python 3.14 venv: Pillow resize/filter/PNG/WebP/thumbnail, pillow-heif 1.6.0
  HEIF encode/decode, filelock exclusive acquire + timeout, oauthlib 4.0.1 OAuth1/OAuth2,
  `requests-oauthlib` 2.0.0, and `google-auth-oauthlib` 1.3.1 flow construction all passed.
  `browser_harness.helpers`/`video_render` import and use Pillow 13 successfully.
- Focused tests with the new libraries on `PYTHONPATH`: runtime vision/image set
  **216 passed, 6 skipped**; oauth/provider-auth + filelock set **227 passed**;
  root MCP-OAuth/auth set **34 passed**; `tests/pm/test_pip_check.py` (reads the real lock)
  **16 passed**. Pre-existing vision failures in `tests/test_vision8tf.py` and
  `tests/test_vision_role_resolution9u.py` reproduce byte-identically with the old libraries,
  so they are not caused by this upgrade.
- Pillow 13 emits a deprecation warning for `Image.getdata` (removal in Pillow 14) in
  `qubit_runtime/tests/scripts/test_icon_flavors.py`; tests still pass.

## 8. Follow-up (2026-11-05, branch `fix5/nodedeps`, base `b257b1b08`)

- **simple-git decision executed.** `qubit_runtime/apps/desktop` now uses `simple-git` **4.0.2**
  and `@simple-git/argv-parser` **2.0.1** (`@simple-git/args-pathspec` 1.0.4), via
  `qubit_runtime/package-lock.json`. The v4 port uses the named `simpleGit` import, allowlists
  `GIT_TERMINAL_PROMPT` for the Windows no-console wrapper, mirrors v4's filtering of
  git-impacting env keys, and drops the obsolete v3 `console.warn` filter.
- **Docs-site criticals cleared.** `qubit_runtime/website` overrides `dompurify` 3.4.17,
  `proxy-addr` 2.0.8, `shell-quote` 1.12.0 and `tinypool` 2.2.0. Audit (`npm 11.17.0`,
  `--package-lock-only --omit=dev`) went **20 critical → 0**. Docusaurus remains **3.10.2**:
  no patched 3.10.x exists and the npm-proposed downgrade was not applied; its remaining
  advisories reclassify to high.
- **Workspace criticals cleared.** A root `shell-quote` 1.12.0 override removes the
  `concurrently` → `shell-quote` critical chain. Workspace audit went **4 critical → 0**.
- **Verification:** desktop focused Vitest 22 passed; `tsc -p tsconfig.electron.json` reports no
  error in the touched files (unrelated pre-existing errors remain); website `npm ci --dry-run`,
  `npm run typecheck`, `check_doc_links.py` and `docusaurus build --locale en` (with
  `DOCUSAURUS_SSG_WORKER_THREAD_COUNT=2`, exercising tinypool 2.2.0) all pass; both workspace
  locks pass `npm ci --dry-run`. No full matrix, no live service/config change, no publish, no
  `VERIFIED` claim.

## 9. Final cleanup — 0 open Dependabot alerts (2026-11-05, Qubit 0.0.5)

The second dependency pass cleared every remaining high and medium alert and all but one low:

- Node workspace: `electron` 42.11.16, `vite` 8.3.5, `axios` 1.21.0, `glob` 14.0.3,
  `joi` 18.3.2, `zod` 4.6.7, `minimatch` 11.0.4, `@vitest/mocker`/`vitest` 4.1.12;
  `npm audit --package-lock-only` → **0 vulnerabilities**.
- Frontend: `undici` 8.12.3, `vite` 8.3.5, `vitest` 4.1.12, `zod` 4.6.7;
  `npm audit --package-lock-only` → **0 vulnerabilities**.
- Website: Docusaurus remains 3.10.2 with fixed transitive overrides; ``npm audit
  --package-lock-only --omit=dev`` → **0 high / 0 critical**, 5 low (all one
  `deepmerge` advisory with no patched release).
- Python: `mcp` 2.3.0, `setuptools` 84.1.0, `mistralai` 3.7.0, and the OpenTelemetry
  API/SDK/OTLP exporter 1.45.2; `uv lock --check` passes.
- GitHub Dependabot on `cgraph-dev/Qubit`: **0 open alerts**. The sole remaining low
  `deepmerge` alert (GHSA-gcqx-w48m-w2h4, docs-site build-only, no patched release) was
  dismissed with the explicit reason `tolerable_risk`.
- Verification: workspace/frontend/website lockfile dry-runs pass; website typecheck and
  Docusaurus build pass; frontend typecheck, 1,254 tests, and build pass (5 loaded-host
  flakes passed individually: ChatPage voice 8/8, SystemPage 10/10); public `v0.0.5`
  release verification **PASS**; public POSIX bootstrap installed `qubit 0.0.5`; Windows
  and POSIX bootstrap dry-runs PASS.

No published artifact was edited in place; `v0.0.5` is a new release. `VERIFIED` is still
not claimed because the full matrix and real Windows/macOS E2E remain unrun.
