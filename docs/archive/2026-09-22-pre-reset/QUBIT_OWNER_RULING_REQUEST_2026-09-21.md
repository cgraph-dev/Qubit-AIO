# Qubit Owner Ruling Request — 2026-09-21

**Status:** OPEN DECISIONS. This document does not decide anything and is not
evidence that any requirement is met. It collects the three decisions surfaced
during the 2026-09-21 implementation session that cannot be settled code-side
because they contradict a frozen record, a frozen contract, or the shipped
policy. Each item states the conflict, cites exact `file:line` evidence in the
current worktree, and lists options. No option is recommended here.

**Repo / revision:** `/home/trick/Projects/Repos/qubit-voicebridge`
at `HEAD = f984911c81e119fe6fe1220510b8ac324c1224a4` (branch `main`, worktree
dirty mid deep-rebrand).

**Authority note:** a frozen contract or owner amendment is authoritative by
content and must not be edited without owner sign-off. Changing a requirement
is exactly what this request asks the owner to do. Until the owner rules, none
of the affected dispositions is terminal.

---

## Ruling 1 — Frozen rebrand allowlist vs the 2026-09-21 rebrand policy

### The conflict

The frozen allowlist permits upstream docs URLs and the `@NousResearch`
attribution on shipped public surfaces, while the 2026-09-21 policy forbids
upstream names as URLs or labels and allowlists only three wire tokens.

**Frozen allowlist** — `docs/archive/2026-09-21/QUBIT_REBRAND_ALLOWLIST.md`,
rows 72–75 (lines 72–75), "Additional explicit allowances":

- line 72: `DocsPage` help link `https://hermes-agent.nousresearch.com/docs/`,
  kanban plugin docs links — class *attribution/provenance (upstream
  documentation link)*;
- line 73: achievements bundle `@NousResearch` attribution and the MIT/upstream
  repo comment — class *legal attribution*;
- line 74: optional-MCP `n8n` manifest third-party source URL — class
  *provenance*;
- line 75: internal wire identifiers (`__HERMES_*__`,
  `X-Hermes-Session-Token`, `hermes_kanban`, `HERMES_QWEN_`, JS identifiers).

**2026-09-21 policy** —
`docs/QUBIT_REBRAND_AND_PUBLIC_SURFACE_POLICY_2026-09-21.md`:

- §1.2 (line 39): the names **Hermes**, **Nous**, and **OpenClaw** "must not
  appear as user-facing product names … a URL, a page title, a navigation
  label, a CLI command, help text, an installer/service/log/report label, or a
  dashboard path."
- §2.1 (lines 95–111): the served SPA shell including the `href`/`src`/`action`
  URLs it points at, and the built frontend bundle, are public surfaces that
  "must contain no prohibited name except where an explicit allowlist entry
  applies."
- §3.1 (lines 140–155): the allowlist records **only** `__HERMES_BASE_PATH__`,
  `__HERMES_SESSION_TOKEN__`, and `X-Hermes-Session-Token` as removed tokens;
  the scan is fail-closed.
- §3.3 (lines 160–171): legal notices and provenance/comparison manifests are
  out of scope, but a *rendered* docs link in the product bundle is not
  listed as out of scope.

### Live shipped evidence (read from the worktree, not a test run)

- Source: `frontend/qubit-web/src/pages/DocsPage.tsx:14-15` defines
  `UPSTREAM_DOCS_URL = "https://hermes-agent.nousresearch.com/docs/"`, rendered
  as an outbound link at `:151-155` and `:216-220`.
- Source: `frontend/qubit-web/public/plugins/kanban/dist/index.js:272-274`
  defines the upstream docs URLs and the label
  `"Upstream vendor Kanban docs (Nous Research)"`.
- Source: `frontend/qubit-web/public/plugins/hermes-achievements/dist/index.js:389`
  appends `"\n\n@NousResearch"` to its share text.
- Shipped bundle: `frontend/qubit-dist/assets/DocsPage-C6Egsbh4.js` embeds the
  upstream URL; `frontend/qubit-dist/plugins/kanban/dist/index.js:272-273` and
  `frontend/qubit-dist/plugins/hermes-achievements/dist/index.js:389` embed the
  URL/label and the `@NousResearch` handle.

### Test conflict

- `tests/test_branding_bundle_scan9u.py` enforces the newer policy on the
  shipped bundle and the rendered CLI help. It flags upstream URLs
  (`_UPSTREAM_URL`, line 44-48) and the `@NousResearch` handle
  (`_UPSTREAM_HANDLE`, line 50). Its own docstring (lines 22–29) records the
  conflict as "reported, not resolved here." It fails while the shipped
  occurrences remain.
- `tests/test_branding_leaks_fixes.py` asserts the opposite: line 30 and
  `:53` require the upstream docs URLs to be present and rendered; line 56
  requires the label `"Upstream vendor Kanban docs (Nous Research)"` to be
  present. Its docstring frames the URL as required provenance.
- Related: `tests/test_qbpv2_rebrand_public_surface.py:57` lists `NousResearch`
  among prohibited tokens; `tests/test_branding_parity_wave3.py:60` and
  `tests/test_branding_runtime_surfaces8u.py:161-167` treat
  `github.com/NousResearch/hermes-agent` as required provenance on other
  surfaces. The conflict is therefore broader than the three shipped files.

### Options

- **A — Policy wins (de-brand the rendered surface).** Remove the upstream URL
  from rendered DocsPage/kanban/achievements labels and the `@NousResearch`
  handle from the shipped share text; keep provenance only in legal/notice and
  comparison-manifest surfaces that §3.3 exempts. Requires updating
  `test_branding_leaks_fixes.py` (which currently asserts them present) under
  owner authorization, and regenerating `frontend/qubit-dist`.
- **B — Frozen allowlist wins (exempt as attribution/provenance).** Confirm rows
  72–75 as terminal and scope the §1.2 URL/label prohibition so it does not
  cover labelled upstream attribution links; then the policy scan
  (`test_branding_bundle_scan9u.py`) must be amended to exempt the allowlisted
  occurrences rather than fail closed on them.
- **C — Narrow exemption (both partially).** Permit the URL only as
  non-rendered provenance metadata (e.g. an exported constant / manifest) and
  require every *rendered* label to be the neutral "Upstream vendor
  documentation", removing the `@NousResearch` share handle. Needs a policy
  edit plus both tests reconciled.
- **D — Defer.** Leave the conflict open and mark both branding scans
  `KNOWN_CONFLICT` (explicitly not passing) until a later ruling.

**Owner decision:** ____________________

---

## Ruling 2 — Confirmation of the 95 OPERATOR_ONLY OpenClaw rows

### The request

The OpenClaw contractual-pin method surface is code-side disposed
`434/434, 0 OPEN` (252 IMPLEMENTED, 87 SAFE_EQUIVALENT, 95 OPERATOR_ONLY) in
`docs/archive/2026-09-21/QUBIT_OPENCLAW_UPGRADE_MANIFEST.json`
`pinned_surface_disposition`. The 95 `OPERATOR_ONLY` rows are typed refusals
naming an exact prerequisite and a local counterpart. The owner is asked to
confirm that disposition — in particular whether the **architecture-gated**
rows are genuinely terminal, not merely rows whose prerequisite Qubit has chosen
not to run.

### Two classes inside the 95

- **External-prerequisite rows** require a genuinely external service,
  credential, device, or process: GitHub/OAuth device flows, an OpenAI Realtime
  (or equivalent) transport, paired node devices, systemd/launchd process
  ownership, a hosted portal service, a live native-harness relay, a remote
  verdict registry, cloud-worker transports.
- **Architecture-gated rows** are refused because Qubit intentionally runs no
  upstream server registry/transport/store. The active brief §11 names five such
  classes: per-job cron scratch (`cron.scratch.get/set`), the model-proposed
  task-suggestion registry (`taskSuggestions.accept/create/dismiss`), the
  human-mention inbox (`mentions.list/dismiss`), chunked skill-upload staging
  (`skills.upload.begin/chunk/commit`), and the wake-trigger store
  (`voicewake.routing.get`, `voicewake.set`). These 12 rows share a "Qubit runs
  none" basis.

The architecture-gated label is not a mechanically clean partition: some rows
mix an architecture reason with an external prerequisite (e.g. `portal.*`,
`talk.*`, `node.*`, `environments.*`, `agents.*`, `users.*` single-operator
rows), and the manifest does not carry a dedicated `architecture_gated` field.
An exact count is therefore **NOT independently established** here; the brief's
"~17" is a working approximation. Do not treat any specific row as terminal
before the owner rules.

### Evidence

- `docs/archive/2026-09-21/QUBIT_OPENCLAW_UPGRADE_MANIFEST.json`
  `pinned_surface_disposition.counts` = `{IMPLEMENTED: 252, SAFE_EQUIVALENT: 87,
  OPERATOR_ONLY: 95, OPEN: 0}`; `policy.terminal` =
  `[IMPLEMENTED, SAFE_EQUIVALENT, OPERATOR_ONLY]`.
- Per-row `operator_prerequisite`, `basis`, `code`, and `qubit_counterpart`
  fields carry the typed refusal and local counterpart; the handlers live in
  `voicebridge/gateway/openclaw_handlers/`.
- The manifest policy treats `OPERATOR_ONLY` as terminal, but the capability
  parity contract
  (`docs/QUBIT_CAPABILITY_PARITY_CONTRACT_2026-09-21.md` §3) defines
  `OPERATOR_ONLY` as "a genuine live prerequisite" — a qualification state, not
  exercised behavior. "Qubit intentionally runs no such transport" is a policy
  choice, not automatically a live-environment prerequisite, so it needs the
  owner's explicit confirmation.

### Options

- **A — Confirm all 95 as terminal OPERATOR_ONLY.** The disposition stands; only
  the hardware/credential qualification matrix remains for the
  external-prerequisite subset.
- **B — Confirm only the external-prerequisite rows as terminal.** Re-dispose
  the architecture-gated subset as `SAFE_EQUIVALENT` (real local counterpart,
  documented reason) or keep as `OPERATOR_ONLY` with an owner-recorded basis.
- **C — Re-open the architecture-gated subset.** Require a code-side adapter
  (e.g. a local cron-scratch store, a local suggestion registry) before it may
  be terminal.

**Owner decision:** ____________________

---

## Ruling 3 — The `/` redirect target

### The conflict

The frozen allowlist and the 2026-09-21 policy say `/` redirects to `/chat`.
The live server and its route test redirect `/` to `/sessions`.

- Policy `docs/QUBIT_REBRAND_AND_PUBLIC_SURFACE_POLICY_2026-09-21.md:194` and
  `:273`: "`/` redirects to `/chat`."
- Frozen allowlist `docs/archive/2026-09-21/QUBIT_REBRAND_ALLOWLIST.md:55`:
  "`/` redirects to `/chat`".
- Live: `voicebridge/webui.py:1850-1853` — `if path in ("/",
  "/index.html"): … self._redirect("/sessions")`; the adjacent comment says the
  upstream shell `RootRedirect` sends `/` to `/sessions`, "so the server-side
  redirect matches instead of diverging to `/chat`."
- Test: `tests/test_qbpv2_rebrand_routes.py:108-112`
  (`test_root_redirects_to_sessions`) asserts status 302 and `Location:
  /sessions`.

This is a real behavioral divergence from the written rule, not merely a stale
sentence. The earlier `/chat` policy text and the `/` landing behavior cannot
both be correct.

### Options

- **A — Change live behavior to `/chat`.** Redirect `/` to `/chat`, update the
  `voicebridge/webui.py` comment, and change
  `tests/test_qbpv2_rebrand_routes.py:108-112` to assert `/chat`. The dashboard
  shell's own `RootRedirect` (App.tsx) must also be reconciled so client and
  server agree.
- **B — Amend the policy and allowlist to `/sessions`.** Owner edits
  `QUBIT_REBRAND_AND_PUBLIC_SURFACE_POLICY_2026-09-21.md` (§5 lines 194, 273)
  and the frozen allowlist line 55 to record `/sessions` as the canonical
  workspace landing. No code change.
- **C — Leave as-is and treat the policy text as superseded.** Not recommended:
  the conflict would remain fail-visible and the allowlist is a frozen record.

**Owner decision:** ____________________

---

## Cross-references

- [QUBIT_ACTIVE_EXECUTION_BRIEF_2026-09-21.md](./QUBIT_ACTIVE_EXECUTION_BRIEF_2026-09-21.md)
  §12, §13 items 8–9, and the Current truth table.
- [QUBIT_REBRAND_AND_PUBLIC_SURFACE_POLICY_2026-09-21.md](./QUBIT_REBRAND_AND_PUBLIC_SURFACE_POLICY_2026-09-21.md)
- [QUBIT_AUTHORITY_MATRIX_2026-09-21.md](./QUBIT_AUTHORITY_MATRIX_2026-09-21.md)
- [QUBIT_CAPABILITY_PARITY_CONTRACT_2026-09-21.md](./QUBIT_CAPABILITY_PARITY_CONTRACT_2026-09-21.md)
- Frozen records: `docs/archive/2026-09-21/QUBIT_REBRAND_ALLOWLIST.md`,
  `docs/archive/2026-09-21/QUBIT_OPENCLAW_UPGRADE_MANIFEST.json`.

No item in this document is `VERIFIED`, complete, 100%, or 1:1.
