# Packet 8E — Website integration onboarding and observability

Status: implemented, not yet independently verified (see handoff).
Date: 2026-08-29.
Parent phase: 8 — hourly communications monitor and professional evidence reports.

## What this is

A governed, **non-live** front door for future website, app, editor, and
marketplace skills. Pasting a URL into the CLI or the authenticated dashboard
creates only a **PENDING, disabled, versioned integration proposal**. It never
grants access, trains the model, executes page-provided instructions, imports
credentials, clones a profile, launches a Playwright personal profile, or
creates a hidden skill. There is no live site adapter in this packet — that
remains a separately authorized packet (8B/8F scope).

Proposal and observability only: `discover/propose` is separated from
`implement/enable`. Enabling marks a validated proposal active for
observability but grants nothing by itself; capability grants are explicit,
scoped to the integration origin, expiring, confirmed, and limited to the
validated read-only proposal.

## Effective behavior

### Lifecycle states

`PENDING` (just added) → `DISCOVERING` → `READY` | `AUTH_REQUIRED` | `DENIED`
| `SELECTOR_DRIFT` | `DEGRADED` | `FAILED` | `DISABLED` | `REMOVED`.

- Only a `READY` static page produces a capability proposal, and only the two
  READ_ONLY observation capabilities:
  - `web.observe.reachability`
  - `web.observe.metadata`
  scoped to `web:<origin>`. Every other outcome proposes nothing (fail closed).
- `AUTH_REQUIRED` sites (HTTP 401/403 or a known auth host) get no proposal and
  note `adapter_requirement=authenticated_cdp_adapter_required`.

### Record

Each integration is persisted atomically in `websites.json` (private `0700`/
`0600`, timestamped backups, bounded redacted audit) under the configured state
directory (`logging.jsonl_path`, override `websites.state_path`). Fields:
stable `integration_id`, normalized `origin` (credentials/query tokens
rejected), clean `url`, `display_name`, status, `enabled`, requested
capabilities/scopes, linked `grant_ids`, sensitivity, approval policy,
`auth_required`, `manifest_link` + provenance (`source_revision`/`license`/
`content_hash`), `limits`, `health`, timestamps, `rollback_to`, removal
metadata.

### Website → 7E manifest linkage (strict binding)

A website record stays **unlinked** until an explicit user action links a
valid, installed, **active** 7E skill (`websites manifest-link <id> <skill_id>`
or the dashboard "Link manifest" control). `link_manifest` is the ONLY writer
of `manifest_link`. `validate_manifest_link` enforces:

- present in the Packet 7E permission store (`SKILL_MISSING`);
- re-validated through `SkillManifest` at read time (`SKILL_MALFORMED` catches
  corrupted stored records, including bad/empty license or non-hex hash);
- status `active`, never staged/disabled/removed (`SKILL_NOT_ACTIVE`);
- a **recognized SPDX license** identifier or expression via a deterministic
  local allowlist/parser (`LICENSE_INVALID` / `LICENSE_UNKNOWN`; empty, fake,
  unknown, or malformed expressions including dangling/duplicate connectors and
  unbalanced parentheses are rejected);
- a **verified content hash** — `content_hash` must equal the SHA-256 of the
  canonical JSON encoding of the manifest payload (sorted keys, `(",",":")`
  separators, `content_hash` itself excluded) recomputed from the stored
  manifest (`SKILL_HASH_MISMATCH` for a well-shaped but wrong value; a
  well-shaped hash is never treated as proof of authenticity by itself);
- compatibility with this platform (`SKILL_INCOMPATIBLE`).

Linking grants nothing and enables nothing. When a proposal already exists,
`link_manifest` ALSO enforces the **binding gate** (`validate_website_binding`):
every proposal capability must be declared by the linked manifest
(`BINDING_CAPABILITY_MISMATCH`), every proposal scope must be authorized by it
— exact `web:<origin>` or an explicit `web` scope (`BINDING_SCOPE_MISMATCH`) —
and the manifest tier/sensitivity/approval must be compatible with read-only
website observation (`BINDING_TIER_MISMATCH` / `BINDING_SENSITIVITY_MISMATCH` /
`BINDING_APPROVAL_MISMATCH`). A manifest that only declares app-control,
terminal, editor, mail-send, or destructive capabilities cannot authorize
website observation. Provenance (source URL, pinned revision, license, content
hash, compatibility) is copied onto the record and preserved in audit output.

**Optional artifact-byte verification (RE-verified at every gate)**: `websites
manifest-link --artifact PATH --artifact-sha256 HEX` verifies a local,
operator-supplied artifact file: the file must exist, be readable, and its
bytes must SHA-256-hash to the supplied digest (computed locally with no
package hooks, installers, arbitrary code, or network). The verified digest AND
the resolved local path are recorded in PRIVATE state (the path is never
serialized to records, logs, dashboard, reports, or test output — it appears
only as `[local artifact]`). **Every enable/grant gate re-verifies the artifact
bytes**: a deleted/moved file fails closed `SKILL_ARTIFACT_MISSING`, a modified
file fails `SKILL_ARTIFACT_TAMPERED`, an unreadable file fails
`SKILL_ARTIFACT_UNREADABLE`, and `check` reports the live artifact status
(`verified` / `missing` / `tampered` / `unreadable` / `not_applicable`). If no
artifact is supplied, NO artifact claim is made and nothing external is
asserted.

`websites enable` and `websites grant` call ONE shared fail-closed gate
(`_check_linked_and_bound`) that re-runs all of the above PLUS the binding
checks at use time — a link is never a one-time pass. `grant` never calls
`PermissionsStore.grant` until every check passes. Removing/disabling/rolling
back a website never restores grants; removing or disabling the linked skill
immediately makes enable/grant fail closed.

### URL validation (fail closed)

- Only `http`/`https`; credential-bearing URLs rejected; query-token URLs
  (keys matching token/secret/password/api_key/…) rejected; query/fragment
  dropped from the stored URL.
- Private/out-of-scope origins rejected: literal private/loopback/link-local/
  reserved IPs, `localhost`, `*.local`/`*.lan`/`*.internal`/`*.home.arpa`, and
  any hostname that resolves to a private address (SSRF blocked, per-hop
  re-checked on redirects).
- Discovery probe bounds: `timeout_s` (default 8), `max_bytes` (default 64 KiB),
  `max_redirects` (default 3). Never sends cookies/authorization and never runs
  page JavaScript. The probe is injectable; tests are fully hermetic.

### Grants and permissions

- `restricted` is the default permission mode; page/model/tool text can never
  change the mode or widen a grant.
- `websites grant` requires (in order): confirmation, a READY proposal, the
  **shared fail-closed gate** (`_check_linked_and_bound`: active + SPDX + hash +
  compatible manifest, then capability/scope/tier/sensitivity/approval
  binding), capabilities limited to the validated proposal, scopes limited to
  `web:<origin>`, and an expiry (default TTL 7 days; allowed 1 s–90 d).
  `websites revoke` removes every linked grant.
- `websites remove` revokes linked grants then removes the record (backed up).
- `websites rollback` restores the record (removed → prior status, or prior
  status of a disabled record). **Grants are never auto-restored and skills are
  never reactivated/deactivated** — re-granting is an explicit, confirmed
  permission decision.
- `websites enable` marks a validated, linked, active proposal READY for
  observability; it grants nothing by itself.

## CLI

```
voicebridge websites add <url> [--name NAME] [--dry-run] [--human]
voicebridge websites list [--human]
voicebridge websites show <id|url> [--human]
voicebridge websites inspect <id|url> [--no-network | --fixture PATH] [--human]
voicebridge websites probe <url> --live [--human]
voicebridge websites check <id|url> [--human]
voicebridge websites manifest-link <id|url> <skill_id>
      [--artifact PATH --artifact-sha256 HEX] [--dry-run] [--confirm] [--human]
voicebridge websites logs <id|url> [--tail N] [--human]
voicebridge websites enable|disable|remove|rollback <id|url> [--dry-run] [--confirm] [--human]
voicebridge websites grant <id|url> [--capability CAP ...] [--ttl SECS] [--dry-run] [--confirm] [--human]
voicebridge websites revoke <id|url> [--dry-run] [--confirm] [--human]
```

Mutating commands are dry-run by default (`--dry-run` preview) and require
`--confirm` to persist. Output is machine-readable JSON by default and concise
human-readable text with `--human`. `add --dry-run` neither persists nor
contacts the network; `inspect --no-network` returns a typed
`NETWORK_DISABLED` offline result.

- **`websites probe <url> --live`** is a safe, bounded LIVE probe of a public
  non-authenticated static **HTTPS** site (`--live` mandatory; `http` is
  rejected; no credentials, query tokens, cookies, private accounts, or private
  networks). It validates the URL (scheme, credentials, query tokens,
  private/out-of-scope origins), runs one bounded read-only HTTP probe
  (timeout 8 s, ≤ 64 KiB, ≤ 3 redirects, HTML-ish content-type limit, no
  cookies, no page JS, SSRF blocked), redacts the output, PERSISTS NOTHING
  (no raw page text), and returns a typed result in the probe vocabulary:
  `SUCCESS` (READY), `UNAVAILABLE` (unreachable/timeout/offline/
  probe-unavailable), `DEGRADED` (incl. unsupported content-type), `PARTIAL`
  (selector drift), `AUTH_REQUIRED`, or `FAILED` (denied/other). The canonical
  discovery status (READY/AUTH_REQUIRED/DENIED/SELECTOR_DRIFT/DEGRADED/FAILED)
  is also present in the `probe` payload. A probe function that raises fails
  closed to `FAILED`/`PROBE_UNAVAILABLE` rather than crashing. It never launches
  Playwright and never alters Chromium.
- **`websites inspect <id> --fixture PATH`** is the explicitly
  operator-authorized READY-record smoke path: the operator supplies a
  synthetic, non-secret probe-result JSON file (no private data, no network).
  The fixture only controls the discovery outcome; the capability/scope
  proposal is always derived from the typed status by code, never from the
  fixture. `probe_source=fixture` is recorded in the record health and the
  audit so a synthetic result is never mistaken for a live observation. This
  path exercises manifest linkage, permission checks, scope, expiry,
  confirmation, audit, grant, revoke, and rollback through the real backend.

## Dashboard (authenticated Settings page)

`/settings` now includes:

- Beginner: **Add Website** (paste URL → PENDING proposal, restricted by
  default), integration list with status + manifest-link state, **Link skill**
  selector, Show/Inspect/Logs per record.
- Advanced: per-record List/Show/Inspect/Check/Logs/**Manifest-link**/Grant/
  Revoke/Enable/Disable/Remove/Rollback, manifest provenance (license, hash,
  compatibility), grant scopes/expiry, a **Permission mode** card
  (show/set Restricted, set Unrestricted with scopes+TTL, reset to restricted)
  wired to the same 7E backend as the `permissions` CLI, and redacted audit
  inspection.

Every control calls the real authenticated backend (`GET/POST /api/websites`
and the existing `/api/permissions` for mode/reset; header-token only —
query-string tokens rejected) and renders the typed result; there is no
frontend-only state or optimistic success. No secret, raw page text, cookie,
token, or secret URL appears in records, logs, fixtures, events, or test output.

### Dashboard serving and authentication integration (Packet 8E repair)

- **Serving the CURRENT frontend**: the repository `frontend/` is the canonical
  current UI and carries a `frontend/VERSION` marker (`1.0.0`). A configured
  `webui.frontend_dir` is honored ONLY when it serves `index.html` AND carries a
  `VERSION` marker equal to the repository version. A stale, unversioned, or
  version-mismatched external checkout is NOT served — the bridge safely falls
  back to the repository frontend and reports the mismatch (the external
  checkout is never modified or overwritten).
- **Stale detection is visible**: `frontend_status()` /
  `resolve_frontend_dir()` and `/api/status` expose
  `frontend: {version, active: "repo"|"external", stale, configured}`; the
  settings page shows a plain-language warning with the repair path, and
  `config doctor` reports `frontend.active` as DEGRADED with the reason.
- **Page fallback fixed**: `/`, `/settings`, `/token-setup`, `/activity`,
  `/reports`, `/approvals`, and `/static/*` resolve through one
  `_frontend_file()` helper that falls back to the repository file when absent.
- **One shared authenticated fetch helper** (`frontend/vb.js`): `vbToken()`,
  `vbHeaders()`, `vbApi()`, `vbPairHint()`. Every page loads it; the token is
  read ONLY from browser storage and sent ONLY as the `X-Api-Token` header —
  query-string tokens are never read or placed in URLs. `vbApi()` checks the
  response Content-Type BEFORE parsing JSON (a plain-text 401/404/500/429/409
  never reaches `JSON.parse`), returns typed `{ok,status,body,authRequired,
  error}` including network/timeout as `status:0, error:"network"`, and a 401
  shows a plain-language **"Dashboard not paired"** hint linking to
  `/token-setup` instead of a bare "Request failed: HTTP 401".
- **Talk page fixed**: every `fetch` in `frontend/index.html` (except the SSE
  stream, which uses `vbHeaders()` for the token and stops+shows the pairing
  hint on 401) now goes through `vbApi()`/`vbHeaders()`. In particular
  `/api/status` was previously fetched WITHOUT the token header (so it always
  failed with a JSON.parse error on a plain-text 401) — it now authenticates,
  and `/api/talk` (binary PCM body preserved), `/api/result/<id>` polling,
  cancellation, feedback, lessons, permissions, and reports all send the token
  and parse safely.
- **Backend auth remains fail-closed**: query-string tokens always 401; missing
  or wrong `X-Api-Token` headers always 401; correct headers succeed;
  non-loopback access still requires configured auth.
- Pre-existing frontend defects fixed while repairing: index.html's history
  block was missing its `</script>` (the following Packet 6A panel never ran)
  and activity.html's inline script was closed with an escaped `<\/script>` —
  both corrected so every dashboard script parses and runs.

### Controlled restart command (operator, after the repair)

The dashboard/serving change takes effect when the running bridge process
restarts. The exact controlled command depends on the installed unit (the
repository template and installer use `qubit-voice-agent.service`; the operator
should confirm with `systemctl --user list-units 'qubit-voice-agent*'
'openclaw*'`):

```bash
systemctl --user restart qubit-voice-agent.service   # or the actual installed unit
journalctl --user -u qubit-voice-agent.service --since "-2 min"   # verify
```

Then in the browser: open `/token-setup` and re-store the token (or clear the
stale `vdb_token` localStorage entry) so requests carry a valid `X-Api-Token`;
the Settings → Websites/Permissions UI will then be the current repository
frontend. If a stale external `webui.frontend_dir` is configured, either update
that checkout to carry a matching `frontend/VERSION` (and the current pages) or
remove the override — the external checkout itself is never modified by the
bridge.

## Security properties

- Adding a URL never grants, trains, executes page text, imports credentials,
  clones a profile, or creates a hidden skill.
- A website record stays unlinked until an explicit, confirmed user action
  links a valid, installed, ACTIVE 7E skill whose license is a recognized SPDX
  identifier/expression and whose content_hash matches the canonical artifact
  bytes; enable/grant fail closed without an active, bound link, and grant
  never calls the permission store before the shared gate passes.
- Binding is strict: every proposal capability must be declared by the linked
  manifest and every proposal scope must be authorized by it; app-control-only
  / terminal / editor / mail / destructive manifests cannot authorize website
  observation; capability/scope/tier/sensitivity/approval mismatches fail
  closed.
- Restricted is default; unrestricted cannot bypass hard-deny, validation,
  audit, isolation, or resource limits; page/model/tool text cannot alter
  policy or widen a grant.
- Invalid schemes, credential/query-token URLs, private origins, malformed/
  unlicensed/hash-mismatched manifests, missing/disabled/incompatible skills,
  and expired grants all fail closed with typed output.
- All audit events and logs pass through the project redaction boundary.
- The discovery probe returns only bounded, redacted metadata; page text is
  untrusted evidence, never instructions or executable code.
- A static page produces only a bounded read-only proposal; a launcher never
  claims application control (a linked skill that claims `app.control` still
  grants only the proposal capabilities).

## Resource impact

None material. The probe is bounded (≤ 64 KiB, ≤ 8 s, ≤ 3 redirects); state is
a small JSON file; no model, audio, browser, service, or timer is touched.

## Failure modes

Typed and visible: `AUTH_REQUIRED`, `DENIED` (private network / invalid scheme
/ credentials), `SELECTOR_DRIFT` (reachable but no metadata), `DEGRADED`
(timeout / HTTP error / no data / broken-or-revoked manifest link),
`FAILED` (unreachable / probe error / `NETWORK_DISABLED`), and the
manifest-link gate codes `SKILL_MISSING` / `SKILL_MALFORMED` /
`SKILL_NOT_ACTIVE` / `SKILL_HASH_MISMATCH` / `SKILL_INCOMPATIBLE` /
`LICENSE_INVALID` / `LICENSE_UNKNOWN` / `BINDING_NO_PROPOSAL` /
`BINDING_CAPABILITY_MISMATCH` / `BINDING_SCOPE_MISMATCH` /
`BINDING_TIER_MISMATCH` / `BINDING_SENSITIVITY_MISMATCH` /
`BINDING_APPROVAL_MISMATCH`, plus `PARTIAL` (READY with a valid link but no
active grants). A failure never advances into a grant or a fake success.

## Tests

`tests/test_packet8e.py` (117 tests) — synthetic/hermetic, injected probe; all
manifest fixtures hash REAL temporary payload bytes (never fake all-zero
hashes):

- URL/origin validation (schemes, credentials, query tokens, private origins).
- PENDING/disabled add that never grants and creates no hidden skill; duplicate
  add rejected; dry-run preview persists nothing.
- Discovery typed-state mapping; only READ_ONLY proposal on READY; injection
  text cannot widen capabilities or create a link; bounded counts; static
  proposal + launcher-only no-app-control.
- Explicit website→7E manifest linkage: valid link works; missing/disabled/
  malformed (bad license/hash corruption)/incompatible skills fail closed;
  fake/unknown SPDX licenses fail (LICENSE_UNKNOWN/INVALID); well-shaped but
  wrong hashes fail (SKILL_HASH_MISMATCH); canonical artifact-hash rule
  verified; linking never grants; audit records the link + provenance.
- Strict binding negatives: app.control-only, editor/terminal/mail,
  unrelated-scope, MUTATING tier, secret sensitivity, and deny-approval
  manifests all fail closed; enable/grant re-run the shared gate (tampering the
  artifact after linking fails at use time).
- Enable/disable: confirmation + proposal + **active manifest-link** required;
  enable grants nothing; a revoked link degrades check state.
- Grant: confirmation, active-manifest gate (before any permission call),
  scope/expiry, outside-proposal rejection, effective ALLOWED after grant /
  DENIED before, expired grant fails closed, revoke (skill stays active).
- Remove + rollback: grants never auto-restored; skills never
  deactivated/reactivated.
- Restart persistence (record + manifest link), redacted logs + audit, private
  file modes, duplicate inspect/check idempotence.
- Restricted default; unrestricted cannot widen beyond mode scopes or bypass
  hard-deny (e.g. mail.send).
- CLI parity (add/list/show/inspect --no-network/check/manifest-link/grant/
  revoke/remove/rollback, confirmation, dry-run).
- Dashboard parity via a real loopback server (add/list/show/logs/manifest-link/
  remove/rollback, typed denial without confirmation, active-skill dropdown
  data, 401 without header token, query-string token rejected).
- Full dashboard lifecycle against the SAME temporary backend/state store
  (enable/grant/check/revoke/disable/remove/rollback, mode reset, redacted
  audit, 401, manifest provenance, expiry) — no frontend-only state.
- `websites probe --live`: `--live` mandatory (LIVE_REQUIRED denied), blocked
  URLs rejected before network, human output, typed result vocabulary
  (SUCCESS/UNAVAILABLE/DEGRADED/PARTIAL/AUTH_REQUIRED/FAILED), probe-raises
  fails closed to UNAVAILABLE, redaction, SSRF denial, non-typed probe result
  fails closed, persists nothing.
- Operator fixture smoke path (`inspect --fixture`): READY proposal from
  synthetic data, `probe_source=fixture` recorded, then manifest-link (with
  real artifact bytes), enable, grant (expiry), check, revoke, disable,
  remove, rollback, audit, restart persistence — all through the real CLI.
- Optional artifact-byte hash: verified when supplied (real temp file bytes),
  correct digest recorded; wrong-hash/missing-file/unreadable/malformed-sha
  fail closed at LINK time; and RE-verified at every enable/grant gate
  (post-link deletion → SKILL_ARTIFACT_MISSING, post-link modification →
  SKILL_ARTIFACT_TAMPERED, check reports verified/missing/tampered/
  unreadable); the local artifact path is never serialized.
- HTTPS-only live probe (http rejected), fragments rejected by the probe (and
  dropped, never stored, by add/inspect), content-type support policy,
  redirect-limit guard (TOO_MANY_REDIRECTS), probe-raise fail-closed.
- Artifact movement (rename after link → MISSING) and unreadable (OSError →
  UNREADABLE) states at the use gate.
- No Playwright/Puppeteer/Selenium import and no cookie/credential/account
  handling in the 8E module (static `ast` import scan).

## Commands run

```bash
.venv/bin/python -m pytest -q -W error tests/test_packet8e.py            # 117 passed
.venv/bin/python -m pytest -q -W error tests/test_packet8e.py \
    tests/test_packet7e.py tests/test_packet8a.py tests/test_packet2b.py \
    tests/test_packet7d.py tests/test_webui.py \
    tests/test_webui_reports.py                                           # see handoff
.venv/bin/python -m pytest -q -W error tests/                            # 1140 passed (latest run)
.venv/bin/python -m pytest -q tests/test_packet0b.py                     # 19 passed (0B repro)
.venv/bin/python -m compileall -q voicebridge scripts
.venv/bin/python -m pip check
git diff --check
graft build
```

## Clean-checkout reproducibility (baseline fix)

Root cause: the checkpoint commit excluded two gitignored/machine-local files that
tests required — `Modelfile.qubit4` (the generated bake artifact, intentionally
never committed per plan P0-N / `scripts/bake_brain.py`) and
`tests/test_steal_list_strict.py` (which hardcoded `/home/trick` sibling paths).
A fresh checkout therefore could not run `test_packet4b` (two `Modelfile.qubit4`
reads + one read of `test_steal_list_strict.py`) or
`test_steal_list_strict` itself (five `Modelfile.qubit4` reads).

Chosen fix (no assertions weakened, no tests deleted, no PII committed):
- Tracked deterministic SANITIZED fixtures:
  - `tests/fixtures/modelfiles/qubit4.sanitized` — the canonical persona
    artifact: minimal sanitized SYSTEM (measured 1800–6000-char EOS-stability
    basin, trailing newline), `no_think` template, `num_ctx 4096`, and no
    private/forbidden markers. Verified by the unchanged sanitized-artifact and
    stability-basin tests.
  - `tests/fixtures/dashboard/polling_contract.html` — the delta-polling
    contract markers for the external dashboard integration
    (`setInterval(refreshStrip, 1500)`, `setInterval(pollConversationLive,
    1500)`, `'&since=' + encodeURIComponent(lastHistoryTs)`, `If-None-Match`).
- `tests/test_packet4b.py` and `tests/test_steal_list_strict.py` now read the
  tracked fixture instead of the gitignored `Modelfile.qubit4`; the sanitized +
  stability + doc-param assertions are unchanged.
- `tests/test_steal_list_strict.py` is included in the baseline; its two
  `/home/trick` sibling-dashboard reads now target the repository frontend
  (inline editor) and the tracked polling-contract fixture; the private test
  handle `@tr.cky` is sanitized to `@demohandle`.
- `voicebridge/benchmarking/harness.py` hashes the tracked fixture so a clean
  checkout records a typed `repo_modelfile_qubit4_sha256` during benchmarks.

Required artifacts / install behavior:
- Project install: `python3 -m venv .venv && .venv/bin/pip install -e '.[test]'`.
- The generated `Modelfile.qubit4` is NOT needed for tests (fixture covers it).
  Operators who bake the model run `scripts/bake_brain.py` after creating the
  (gitignored) `Modelfile.qubit4` from the tracked fixture content.
- Live opt-in tests (Ollama `qubit-brain4`, audio devices, VRAM) are documented
  prerequisites: when absent they skip with an explicit reason (e.g.
  `test_steal_list_strict.py:250` "only N MiB VRAM free -> partial offload
  expected"), never silently pass.
- Machine-local live config/state under the user HOME (`~/.config`,
  `~/.local/state`) is a documented prerequisite only for tests that exercise
  real user files; those tests assert bounded/safe behavior and tolerate absence.

Provenance/license: fixtures are derived from the repository's own Packet 4B
sanitized artifact and the Packet 4B/6A dashboard contracts; no upstream code is
copied (Apache-2.0/MIT project artifacts, see `THIRD_PARTY_NOTICES.md`).

Clean-checkout commands (from a fresh clone):
```bash
git clone <repo> freshclone && cd freshclone
python3 -m venv .venv && .venv/bin/pip install -e '.[test]'
.venv/bin/python -m pytest -q -W error tests/          # expect 1151 passed, 1 documented skip, 0 warnings
.venv/bin/python -m compileall -q voicebridge scripts
.venv/bin/python -m pip check
git diff --check
graft build
```

Rollback: the two fix commits (baseline + reproducibility) are additive. Revert
them (`git revert`) to restore the pre-commit state; the preserved excluded
files (`scripts/review_gmail.py`, `scripts/review_tiktok.py`,
`scripts/web_search.py`, `tests/test_new_features.py`,
`docs/BAKEOFF_4D.json`, and the stray `106`) remain machine-local and untouched;
no live service, config, Chromium, account, memory, report, cursor, model, or
audio state is ever modified.

## Known limitations / next safe step

- The 7E `skills install` CLI is the manifest installer; `websites
  manifest-link` consumes an already-installed, active skill. Records created
  by `add` keep `source_revision`/`license`/`content_hash` empty until a
  manifest is linked.
- The live `websites probe --live` path was GENUINELY EXERCISED in this
  environment (network available) against the canonical public non-authenticated
  site:
  `voicebridge websites probe https://example.com --live` -> `SUCCESS`,
  http 200, title `Example Domain`, origin `https://example.com`, persisted
  nothing, output redacted. Other public sites remain operator-selected; the
  hermetic injected-probe suite covers all typed states, bounds, redaction,
  SSRF, HTTPS-only, fragment rejection, and content-type/redirect limits.
- A Packet 4D live-model test (`tests/test_steal_list_strict.py:250`) is
  CONDITIONALLY skipped by its own hardware gate when < 2500 MiB VRAM is free
  ("partial offload expected"). It is unrelated to 8E and is left unchanged. In
  the most recent full-suite run (1140 passed, `-W error`) it did NOT trigger
  (0 skipped); when it does skip, it is an external Packet 4D hardware gate,
  never a suppressed 8E failure.
- Reported-but-unreproducible Packet 0B timeout (documented separately): an
  independent verifier reported `tests/test_packet0b.py::
  test_migration_idempotent_identical_unit` timing out on the second idempotent
  installer call after "unit already matches template". Reproduced 5× alone and
  3× as the full 0B module in this environment: it PASSES every time (module
  ~3.6–8.8 s; the install script `scripts/install_systemd_unit.sh` contains no
  `systemctl`, network, loops, or waits — `mktemp`/`sed`/`diff`/`cp`/`mv`/
  `exit`, ~0.00 s per call). The ~4 s seen once is Python/subprocess warmup, far
  under the 30 s test timeout. No genuine hang exists in the script or test by
  inspection. This is a Packet 0B test (uncommitted, pre-existing dirty work)
  and is NOT caused by or fixable within 8E scope; it was not modified.
  In THIS environment the full suite is clean (1140 passed, `-W error`), so 8E
  reports a clean suite here, with the verifier's 0B timeout recorded as an
  external environmental finding (e.g. a heavily loaded machine exceeding 30 s)
  that 8E cannot claim to resolve.
- `SPDX_LICENSES` is a curated local allowlist (not the full SPDX list);
  unknown identifiers and `LicenseRef-*` fail closed by design.
- No live site adapter exists; a static page yields only a bounded proposal and
  a launcher never claims control. Live adapter work remains separately
  authorized (8B/8F).
- Wait for independent verification before the next packet.

## Live-only / operator-only manual commands (never run automatically)

These paths require real network access and/or an explicit operator decision.
They are NOT exercised by the automated suite (which is hermetic via injected
probes); they are recorded here as UNTESTED-in-suite and must be run manually
by an authorized operator:

```bash
# Bounded, read-only, HTTPS-only live probe of a public non-authenticated page.
# Requires explicit --live; persists nothing; redacts output; enforces
# timeout/size/redirect/content-type/origin limits; returns a typed
# SUCCESS/UNAVAILABLE/DEGRADED/PARTIAL/AUTH_REQUIRED/FAILED result.
.venv/bin/python -m voicebridge websites probe https://example.com --live
.venv/bin/python -m voicebridge websites probe https://example.com --live --human

# Live (network) discovery of an existing integration record:
.venv/bin/python -m voicebridge websites inspect <id|url>

# Hermetic operator-authorized READY smoke (synthetic probe JSON, no network):
.venv/bin/python -m voicebridge websites inspect <id|url> --fixture probe.json
```

The dashboard never reads or mutates any real user account, browser profile,
cookie, credential, or message body. There is no automated account access in
Packet 8E at all. Do not run `--live` against private accounts, authenticated
pages, credentials, cookies, or anything requiring authorization.

## Typed-state vocabulary

- Command results: `OK` / `preview` (dry-run) / `denied` (confirmation
  required) / `failed` / `MISSING` / `degraded`; probe payloads additionally
  carry `SUCCESS` / `UNAVAILABLE` / `DEGRADED` / `PARTIAL` / `AUTH_REQUIRED` /
  `FAILED`; a denied command is a typed `DENIED`, an unknown record is
  `MISSING`, a blocked/tampered/missing artifact or skill is a typed `failed`
  with `SKILL_ARTIFACT_*` / `SKILL_*` / `BINDING_*` / `LICENSE_*` codes (the
  family includes BLOCKED-style hard failures such as `LIVE_REQUIRED`,
  `SKILL_ARTIFACT_MISSING`, `SKILL_ARTIFACT_TAMPERED`). Every failure is a
  typed result — never a traceback.

## Rollback

- Restore `websites.json` from its timestamped `.bak` backups (records +
  audit), or use `websites rollback` per record.
- Remove the `websites` CLI subcommand and the `/api/websites` routes by
  reverting this packet's edits to `voicebridge/cli.py`, `voicebridge/webui.py`,
  and `frontend/settings.html`; delete `voicebridge/websites.py` and
  `tests/test_packet8e.py`.
- Unrelated memory, reports, cursors, Chromium profiles, permissions, and user
  configuration are never touched by this packet's rollback.