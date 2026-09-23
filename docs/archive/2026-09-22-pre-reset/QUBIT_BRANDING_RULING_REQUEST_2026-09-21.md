# Qubit Branding Ruling Request — 2026-09-21

**Status:** OPEN DECISIONS (branding workstream). This document decides nothing
and is not evidence that any requirement is met. It inventories the residual
upstream/third-party strings that remain on shipped **public surfaces** after
the 2026-09-21 deep-rebrand audits, classifies each against the frozen
allowlist and the active policy, and requests the owner's ruling on the
conflict cluster. Read with
[QUBIT_OWNER_RULING_REQUEST_2026-09-21.md](QUBIT_OWNER_RULING_REQUEST_2026-09-21.md)
(its Ruling 1 is the same conflict in broader form) and
[QUBIT_REBRAND_AND_PUBLIC_SURFACE_POLICY_2026-09-21.md](QUBIT_REBRAND_AND_PUBLIC_SURFACE_POLICY_2026-09-21.md).

**Repo / revision:** `/home/trick/Projects/Repos/qubit-voicebridge`
at `HEAD = f984911c81e119fe6fe1220510b8ac324c1224a4` (branch `main`, worktree
dirty mid deep-rebrand).

**Method:** deterministic scans run against the current worktree:

- `tests/test_branding_bundle_scan9u.py` (shipped SPA bundle + rendered CLI
  help; the only fail-closed scanner that walks `dist/plugins/**`);
- `tests/test_branding_parity_wave3.py` (assets, shipped fleet bundles,
  provenance hashes);
- a read-only lexical sweep of `frontend/**` (excluding `node_modules`,
  `qubit-dist`) for standalone `Hermes` / `Nous` / `OpenClaw` prose words.

**Counts by class (shipped bundle, current worktree):**

| Class | Count | Where |
| --- | --- | --- |
| Allowlisted-frozen conflict (owner ruling required) | 4 | DocsPage URL, kanban URL ×2, achievements `@NousResearch` |
| Unambiguous policy-forbidden leak (uncovered by allowlist) | 1 | kanban docs-link **label** containing the word `Nous` |
| Provenance / legal (allowed, out of public-surface scope) | 5 rows | see §3 below |
| Allowlisted wire identifiers (allowed) | 42 refs | `X-Hermes-Session-Token` (8 `src/**`, 4 dist assets) + `__HERMES_SESSION_TOKEN__`/`__HERMES_BASE_PATH__` (23 `src/**`, 7 dist assets) |
| **Total fail-closed public-surface offenders** | **5** | shipped `frontend/qubit-dist/**` |

The fail-closed scan currently reports exactly these five offenders; no other
frontend public surface carries a standalone upstream product word.

---

## 1. The four allowlisted-frozen residuals (ruling requested)

Each of these is permitted by the frozen allowlist
`docs/archive/2026-09-21/QUBIT_REBRAND_ALLOWLIST.md` ("Additional explicit
allowances", rows 72–73, physical lines 72–73) and forbidden by the 2026-09-21
policy §1.2 / §2.1. The scans contradict each other by design; the owner must
pick one.

### 1a. DocsPage upstream docs URL

- **String:** `https://hermes-agent.nousresearch.com/docs/`
- **Source:** `frontend/qubit-web/src/pages/DocsPage.tsx:14`
  (`export const UPSTREAM_DOCS_URL = ...`), rendered as an outbound link at
  `:151-155` (page-header action) and `:216-220` (inline attribution).
- **Shipped:** `frontend/qubit-dist/assets/DocsPage-BZKwhVWv.js` embeds the
  same URL.
- **Allowlist row:** 72 — "`DocsPage` help link
  `https://hermes-agent.nousresearch.com/docs/`, kanban plugin docs links" —
  class *attribution/provenance (upstream documentation link)*.
- **Policy conflict:** §1.2 forbids `Hermes`/`Nous` as a URL on a public
  surface; §2.1 lists the built bundle and the shell `href`s it points at as
  public surfaces; §3.1 allowlists only the three wire tokens.
- **Test conflict:** `tests/test_branding_leaks_fixes.py:30,41-45` **requires**
  the URL present and rendered twice; `tests/test_branding_bundle_scan9u.py`
  **fails** while it ships.
- **Requested decision:** keep as labelled provenance (allowlist wins), or
  remove from the rendered surface and keep the URL only in non-rendered
  provenance metadata (policy wins).

### 1b–1c. Kanban upstream docs URLs

- **Strings:**
  `https://hermes-agent.nousresearch.com/docs/user-guide/features/kanban` and
  `https://hermes-agent.nousresearch.com/docs/user-guide/features/kanban-tutorial`
- **Source:** `frontend/qubit-web/public/plugins/kanban/dist/index.js:272-273`
  (`DOCS_URL`, `DOCS_TUTORIAL_URL`); `DOCS_URL` is rendered by the `DocsLink`
  affordance at `:1755-1760`.
- **Shipped:** identical copy at
  `frontend/qubit-dist/plugins/kanban/dist/index.js:272-273`.
- **Allowlist row:** 72 (kanban plugin docs links — *attribution/provenance*).
- **Policy conflict:** §1.2 (URL), §2.1 (bundle), §3.1 (only three wire tokens).
- **Test conflict:** `tests/test_branding_leaks_fixes.py:53-56` requires the
  kanban docs URL present; the bundle scan fails on it.
- **Requested decision:** same as 1a.

### 1d. Achievements share-handle

- **String:** `@NousResearch`
- **Source:** `frontend/qubit-web/public/plugins/hermes-achievements/dist/index.js:389`
  (`return tmpl + "\n\n@NousResearch";`) — appended to the tweet text of the
  achievements "Share on X" action.
- **Shipped:** identical copy at
  `frontend/qubit-dist/plugins/hermes-achievements/dist/index.js:389`.
- **Allowlist row:** 73 — "achievements bundle `@NousResearch` attribution and
  MIT/upstream repo comment" — class *legal attribution*.
- **Policy conflict:** §1.2 forbids `Nous` as a user-facing label; the handle is
  emitted into shared product output.
- **Test conflict:** `tests/test_branding_leaks_fixes.py` asserts the plugin
  surface; the bundle scan (`_UPSTREAM_HANDLE`) fails on it.
- **Requested decision:** keep as dormant legal attribution (allowlist wins), or
  remove the handle from the shipped share text (policy wins).

---

## 2. The unambiguous uncovered leak (fix requested, outside granted path scope)

### 2a. Kanban docs-link label renders the word `Nous`

- **String:** `"Upstream vendor Kanban docs (Nous Research)"`
- **Source:** `frontend/qubit-web/public/plugins/kanban/dist/index.js:274`
  (`const DOCS_UPSTREAM_LABEL = ...`), rendered as the `title` and `aria-label`
  of the docs-link affordance at `:1759-1760` — i.e. a **navigation label** a
  user reads.
- **Shipped:** identical copy at
  `frontend/qubit-dist/plugins/kanban/dist/index.js:274`.
- **Allowlist coverage:** **none.** Row 72 covers the kanban *docs links*
  (URLs); row 73 covers the achievements `@NousResearch`. No row permits a
  rendered prose label carrying `Nous`. Per policy §3.2, an allowed token in a
  disallowed context (a rendered label) is a violation, not an exemption.
- **Provenance is not lost by the fix:** the row-72 URL remains the provenance
  record; the label only needs to say the docs are upstream/vendor docs.
- **Minimal patch (one line, in both the source and its `dist` copy):**
  `"Upstream vendor Kanban docs (Nous Research)"` →
  `"Upstream vendor Kanban documentation"`.
- **Why it is not applied here:** the edit target
  (`frontend/qubit-web/public/plugins/**`) is outside this workstream's granted
  path scope (`frontend/qubit-web/src/**`, `frontend/qubit-dist/**`), and the
  bundle is hash-pinned in the frozen provenance manifest
  `docs/archive/2026-09-21/HERMES_SOURCE_COMPARISON.json` (row for
  `frontend/qubit-web/public/plugins/kanban/dist/index.js`,
  `qubit_sha256 = 2f8a654e0a677f3597bb995de6a42303b24b8fe758d2083f3a58cdc0af05b0c0`).
  Changing it requires regenerating that frozen manifest with
  `scripts/hermes_vendor_manifest.py --comparison-out …`, which in this dirty
  worktree would also rewrite three unrelated stale-but-frozen hashes
  (`frontend/qubit-web/src/App.tsx`, `…/lib/api.ts`, `…/pages/SystemPage.tsx`).
  That is a broader frozen-file change than this request should make
  unilaterally.
- **Tests to reconcile when applied:**
  `tests/test_branding_leaks_fixes.py:56` asserts the old string;
  `tests/test_dashboard_plugin_fleet.py:237` and
  `tests/test_branding_parity_wave3.py:391` validate the pinned hash.

**Requested decision:** authorize the one-line de-brand above, and authorize
regeneration of `HERMES_SOURCE_COMPARISON.json` (accepting the three unrelated
stale-hash refreshes) **or** grant path scope for
`frontend/qubit-web/public/plugins/**` under this workstream.

---

## 3. Provenance / legal residuals — allowed, not public-surface

These retain upstream names for legal/provenance reasons and are out of scope
for the public-surface scan per policy §3.3 and allowlist rows 73–74. Listed
for completeness; **no ruling requested.**

| String | Location | Basis |
| --- | --- | --- |
| `Nous Research` (company name) | `tests/test_branding_parity_wave3.py` `LEGAL_ATTRIBUTION_PHRASES`, `THIRD_PARTY_NOTICES.md` | legal attribution |
| `https://github.com/NousResearch/hermes-agent` | `THIRD_PARTY_NOTICES.md`, docs/archive provenance manifests | provenance |
| `https://github.com/openclaw/openclaw` / `OpenClaw Foundation` | `THIRD_PARTY_NOTICES.md` | legal attribution |
| `https://github.com/CyberSamuraiX/hermes-n8n-mcp` | `qubit_runtime/optional-mcps/n8n/manifest.yaml` | third-party provenance (allowlist row 74) |
| MIT/upstream repo comments | `frontend/qubit-web/public/plugins/hermes-achievements/dist/{index.js,style.css}` | legal attribution (comments; stripped before product-text scan) |

## 4. Allowlisted wire identifiers observed on the SPA surface — allowed

`X-Hermes-Session-Token` (`frontend/qubit-web/src/lib/api.ts:71,550`,
`…/lib/events-sse.ts:26,28`, `…/pages/DocsPage.tsx:110`,
`…/pages/SessionsPage.tsx:1513`, plus tests), `__HERMES_SESSION_TOKEN__`,
`__HERMES_BASE_PATH__`. Allowlist rows 75 / policy §2.2. **No ruling requested.**

Internal-only (not shipped, not a public surface): `HermesPluginSDK` in
`frontend/qubit-web/src/plugins/sdk.d.ts:22`; `nous-blue` theme registry key in
`frontend/qubit-web/src/themes/presets.ts:189`; `Nousnet's hermes-agent` CSS
comment in `frontend/qubit-web/src/index.css:144`.

---

## 5. Decision summary (owner to complete)

| # | Item | Requested decision |
| --- | --- | --- |
| 1a | DocsPage URL (allowlist row 72 vs policy §1.2) | keep as labelled provenance / remove from rendered surface |
| 1b–c | Kanban docs URLs (allowlist row 72 vs policy §1.2) | keep / remove from rendered surface |
| 1d | Achievements `@NousResearch` (allowlist row 73 vs policy §1.2) | keep as legal attribution / remove from share text |
| 2a | Kanban label `(Nous Research)` (uncovered) | authorize one-line de-brand + path scope / manifest regeneration |

**Owner decision:** ____________________
