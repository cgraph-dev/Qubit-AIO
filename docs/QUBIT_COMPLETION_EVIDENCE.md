# Qubit Completion Evidence Report (append-only)

Date: 2026-09-10. Author: implementation agent (voice-parity close-out).
Frozen contracts untouched: `docs/QUBIT_FROZEN_ACCEPTANCE_CONTRACT.md`,
`docs/QUBIT_NATIVE_COMPLETION_PLAN.md`,
`docs/QUBIT_NATIVE_COMPLETION_CHECKLIST.md`,
`docs/QUBIT_DASHBOARD_REBUILD_PLAN.md`,
`docs/QUBIT_FULL_PARITY_CONTRACT.md`, `docs/AGENT_PROMPT_PACK.md`.

## 0. Baseline

- Branch `main` at `c1e78ce` (native completion part 2) plus this packet.
- Dirty/untracked tree hashed before work (`/tmp/opencode/dirty_before.txt`);
  after work only 5 intended paths differ (4 modified + 1 new test);
  every pre-existing dirty/untracked path preserved byte-for-byte
  (diff of status lists shows additions only, no removals).
- Full hermetic suite at baseline: 2846 passed, 1 skipped, 63 deselected.

## 1. Voice-parity gap found and closed

Audit method: `dashboard_ops.catalog()` Tier-2 extras (EXTRA_OPS) with
`"voice" in interfaces` but no Tier-1 `voice_commands.REGISTRY` entry.

- Principled exclusions kept (typed `interface_reason`, dashboard/CLI-only):
  passphrase-sealed ops (`backup_create/restore`, `export_state`,
  `import_state`, `sync_offer/accept`), nested-JSON envelopes
  (`actions_preview/prepare/retry/recover`, `ghost_preview`,
  `brain_onboard_apply`, `kanban_import`), preview-hash binding
  (`opencode_prepare/run_confirmed`), raw-byte payloads
  (`webhook_verify`). Voice carries refusal/guidance entries where a
  spoken attempt is possible (`export_state_voice`,
  `import_state_voice`, `backup_info`).
- Family-covered (same backend module function, verified): `brain_export`
  / `brain_sweep` via `brain_profile_export` / `brain_profile_sweep`
  (`brain.profile_export/profile_sweep`, matching confirm flags);
  `import_agent_import` via `import_refusal` (`import_agent.refuse_import`);
  `acp_serve` / `acp_accept_hooks` / `lsp_install` / `computer_install` /
  `proxy_start` / `browser_close` via `*_refusal` entries hitting the same
  refuse functions as CLI/dashboard.
- New same-backend voice paths added in `voicebridge/voice_commands.py`:
  - `_b_kanban`: `role_grant` (`kanban.role_grant`),
    `role_revoke` (`kanban.role_revoke`), `widget_move`
    (`kanban.widget_move`, bounded geometry extraction),
    `snapshot_restore` (`kanban.snapshot_restore`), `delegated`
    (`kanban.refuse_delegated`, clamped to known `DELEGATED` keys).
  - `_b_sync`: `revoke` (`sync.revoke`).
  - `_b_gateway`: `supervision` (`gateway_peers.refuse_supervision`).
  - `_b_themes`: `pets` (`themes.refuse_pet_downloads`).
  - `_b_plugins_install`: `remote` (`plugins.classify_install_source` +
    `plugins.refuse_remote`, same classifier the CLI uses).
  - New Tier-1 ids (Tier-2 ids kept disjoint per catalog invariant):
    `kanban_role_grant_voice`, `kanban_role_revoke_voice`,
    `kanban_widget_move_voice`, `kanban_snapshot_restore_voice`,
    `kanban_swarm`, `kanban_watch`, `kanban_dispatch`,
    `sync_revoke_voice`, `gateway_supervision_refusal`,
    `pets_downloads_refusal`, `plugins_remote_refusal`
    (registry 304 -> 315 entries; catalog 338 -> 349 ops).
  - `plugins_remote` backend key registered for the refusal entry so the
    `plugins_install` heavy-op confirmation invariant is untouched.
  - `CLI_COVERAGE` extended (kanban/sync/gateway/theme/skills tuples);
    no `VOICE_IMPOSSIBLE` change needed.
  - Bounded board/principal heuristic `_voice_board_principal`
    (board slugs are single tokens; multi-word capture defaults board to
    `main` and strips role/preposition from the principal); the
    NEEDS_CONFIRM echo still shows the resolved pair before mutation.

## 2. Provenance repairs (stale native-runtime references)

- `docs/MATRIX_8T_F.md`: replaced the stale "What actually executes
  pinned Hermes code" section (deleted `hermes_ansi_strip.py`,
  `hermes_sqlite_util.py`, `RealHermesRuntime`, `test_real_hermes.py`)
  with the Qubit-native runtime statement pointing at
  `voicebridge/upstream/ATTRIBUTION.md`, `voicebridge/textutil.py`,
  `voicebridge/sqlstore.py`, `voicebridge/agent.py`,
  `tests/test_textutil8u.py`, `tests/test_agent8u.py`.
- `docs/PARITY_HERMES.json` `chat` row: `RealHermesRuntime.turn` ->
  `agent.turn/run_session`; proof `test_real_hermes.py` ->
  `test_agent8u.py`.
- Known-stale historical reference left untouched (planning doc, not
  provenance): `docs/FEATURE_MATRIX_8U.md:59` cites deleted
  `voicebridge/hermes/backend.py`.

## 3. Tests

- New `tests/test_voice_parity_gap8u.py` (10 tests): Tier-2 voice-claim
  registry coverage incl. parity-overlap check; role grant/revoke
  roundtrip; snapshot restore roundtrip (revoke-time snapshot);
  widget geometry move; sync revoke tombstone; immediate typed refusals
  (gateway/pets/plugins-remote/swarm/watch/dispatch); cancellation;
  alias non-hijack battery; schema hygiene (incl. no secret-shaped args).
- Extended `tests/test_voice_commands8tf.py`: `MUTATING_ACTIONS` gains
  kanban `move/widget_add/widget_remove/widget_move/layout_set/role_grant/
  role_revoke/snapshot_restore` + `sync: {revoke}` (additive enforcement);
  `COMPLETE_EXAMPLES` gains all 11 new entries.
- Focused suites: 117 passed, 2 deselected (`test_voice_commands8tf`,
  `test_voice_parity_gap8u`, `test_dashboard_ops8tf`,
  `test_dashboard_families8u`, `test_capability_catalog`,
  `test_parity_hermes`, `test_branding_scan8tf`, `test_rebrand8tf`,
  `test_webui`, `test_native_equivalents8u`) with `-W error`.

## 4. Build / hygiene gates

- `compileall` on `voicebridge` + `tests`: clean.
- `git diff --check`: clean.
- Secret scan (sk-/ghp_/AKIA/private-key): only synthetic eval fixture,
  redaction regexes, and `--task-file` substring false positives.
- Import/caller audit: no `hermes`/`openclaw` imports, no `shell=True`,
  no `/tmp` Hermes runtime, no upstream-home paths in normal execution
  (remaining `hermes`/`openclaw` strings are migration-compat aliases,
  legacy `~/.hermes` fallbacks, SSE compat event names, and legal records
  covered by the branding scans, which pass).
- `graft build`: OK (8264 nodes, 20147 edges).
- Services: `qubit-*.service/timer` Qubit-branded, enabled, no
  Hermes/OpenClaw references.
- Repo-owned launcher `.venv/bin/qubit` (`voicebridge.cli:main`):
  `status` reports `agent runtime : qubit-native (qubit-brain4)`.
  Environment note (operator-owned, not modified):
  `/home/trick/.local/bin/qubit` is a stale user shim pointing at
  `~/Projects/Repos/Qubit (2)` + `voice-dashboard/open.sh` and shadows
  the repo launcher on PATH; removal/relink is an operator action.

## 5. Remaining for the final gate (not done in this packet)

- Full hermetic suite re-run after these edits (last full run: 2846
  passed pre-edit; focused: 117 passed post-edit).
- Wheel rebuild + clean-install test (`python -m build` unavailable in
  `.venv`; `dist/` artifacts predate this packet).
- Live/dashboard/UI, Playwright, hardware, account, CAPTCHA/2FA, release
  signing, clean-machine qualification remain operator-only per contract.
- Rollback of this packet: `git checkout -- voicebridge/voice_commands.py
  tests/test_voice_commands8tf.py docs/MATRIX_8T_F.md
  docs/PARITY_HERMES.json && rm tests/test_voice_parity_gap8u.py`
  (unrelated dirty/untracked files unaffected).

## 6. Final full-suite verification (2026-09-10, post-edit)

- `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider -W error`:
  **2856 passed, 1 skipped, 63 deselected, 0 failed** in ~10m20s
  (baseline 2846 passed + 10 new voice-parity tests).
- Registry: 315 voice entries; dashboard catalog: 349 ops.
- `git diff --check`: clean. `compileall`: clean. `graft build`: OK.

## 7. Dashboard rebuild + Hermes-gap close-out (2026-09-10, second packet)

Inventory vs pinned Hermes (`docs/INVENTORY_HERMES.json` @ 2237be3):
routers chat_ws/sessions/files/models/skills/mcp/messaging/cron/profiles/
config/tools/status/audio/analytics/memory/oauth/dashboard_ui/git/gui all
have Qubit family homes. Three routers had no Qubit surface:

- git (web_git.py + web_routers/git.py, ADAPT): implemented as Qubit-safe
  read-only equivalent `voicebridge/git_scope.py` (fixed argv, no shell,
  project-root confinement + symlink resolution, no network verbs,
  32 KiB caps, timeouts, redaction, typed codes). `qubit git
  status|log|diff|show` + dashboard `git` group on /projects + voice
  `git_status/log/diff/show` (read-only, immediate). Writes stay refused
  (`GIT_WRITE_REFUSED`) behind the brokered opencode worktree flow.
  Proven in `tests/test_git_scope8u.py` (9 tests incl. traversal,
  symlink-escape, option-injection, redaction, CLI/dashboard/voice
  same-result).
- oauth (31 KiB router, ADAPT): adopted as the key-reference flow —
  `auth_login.login_plan` (portal URL + reference status, secrets never
  stored/shown), same fn for CLI/dashboard(/security)/voice; live token
  dance stays operator-side by redaction policy.
- analytics (ADAPT): covered by insights + activity feed + reports +
  logs on /memory /activity /reports /system (no separate analytics
  surface; usage stats are journal-derived, never tracked externally).

Orphan-group assignment (41 ops in 25 groups previously reachable only
via /ops console, now on family pages wired to the same backend):
/devices += acp, editor, lsp, vision_status, vision_describe;
/models += capability, mcp, proxy; /skills += hooks, plugins_remote;
/security += pause, resume; /projects += worktree, git;
/system += agent, branding, commands, completions, console_guidance,
flags, logs, loop, reports, themes, tools, tui_guidance, worker, webui.
`test_family_groups_match_catalog_and_cover_everything` strengthened to
require full bidirectional group coverage (no /ops-only group).

Stale-reference sweep: production source carries no deleted runtime
paths (only the `ATTRIBUTION.md` retirement record); status/dashboard
labels are Qubit-native; legacy `~/.hermes` reads are explicit loud
migration fallbacks; `docs/FOUNDATION_8T_F.md` §2b marked SUPERSEDED
with pointer to the journal/ATTRIBUTION (history preserved, not
rewritten); completions regenerated for the new `git` family
(69 commands, parity-tested).

## 8. Flaky-test repair (2026-09-10, test-only, no production change)

`test_dashboard_matches_direct_backend_for_every_read_op` failed
intermittently (~1/8 runs): `vision_status` embeds live GPU gauges —
`detail: 'free VRAM 1157 MiB (ceiling 5120)'` text and the numeric
`free_vram_mib` field shift between any two consecutive backend calls,
so exact-equality across calls is over-strict (unrelated to this
packet; production code untouched). `_scrub` in
`tests/test_dashboard_ops8tf.py` now normalizes exactly those two
volatile gauge values; every other key/code/state/ceiling still
compares exactly. Verified 10/10 consecutive file-context passes
post-repair (plus isolation passes).

## 9. Final full-suite verification (2026-09-10, post git + scrub repair)

- `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider -W error`:
  **2865 passed, 1 skipped, 63 deselected, 0 failed** (~10m41s).
- Registry: 319 voice entries (315 + 4 git); dashboard catalog: 353 ops
  (349 + 4 git); CLI: 70 families (69 + git).
- `git diff --check`: clean. `compileall`: clean. `graft build`: OK.
- Preservation: status diff vs pre-packet hashes shows only intended
  additions (no unrelated modification/removal; no reset/clean/checkout).

## 10. Hermes source-parity packet (2026-09-10/11)

Oracle: /tmp/pinned-upstream/hermes-agent @2237be3 (rev-parse verified,
read-only, untouched); OpenClaw @3928bad. Both MIT (Nous Research /
OpenClaw Foundation); notices retained in THIRD_PARTY_NOTICES.md.
Hermes web/src = 170 files (React/TS — cannot execute in Qubit's
Python/static-JS stack, so adaptation, not vendoring; R34 in
docs/UPSTREAM_REUSE.md).

- `docs/HERMES_FRONTEND_MAP.json` (via `scripts/hermes_frontend_map.py`,
  pin-asserting): 170 files + 19 routes + 73 endpoint prefixes, every
  entry with an explicit disposition (adapted / tested refusal /
  documented omission). Validity enforced by
  `tests/test_hermes_frontend_map8u.py` (5 tests).
- New workflows on real backends: `/docs` page (allowlisted user guide,
  DOM-only safe render, header-token auth) + Talk adapters — recent
  sessions drawer (`sessions` op), slash popover (`commands_list` +
  per-command `example` now in backend output), reasoning-profile select
  (`config_profiles`/`profile_select` kind=brain, approvals flow).
  Docs link added to all 19 navs.
- OpenClaw control-ui (session boards/tabs/widgets) maps to the Qubit
  kanban canvas (boards/widget/layout/snapshot/roles + voice); no new
  code needed.
- Omitted with reason+owner (map records): browser PTY spawn, file
  mkdir/upload, env/config-raw/credential reveals, state dump,
  debug-share, gateway process control, remote plugin/skill/MCP-hub
  fetch, token-billing charts (no local token economy), 23 non-English
  locales, external billing portal, live OAuth token dance
  (provider-side; Qubit keeps key references).
- UI evidence: `tests/test_dashboard_ui8u.py` (`-m ui`, 4 passed) —
  19 routes load clean with landmarks, Talk adapters interact, docs
  renders safely, 12 family screenshots captured
  (/tmp/opencode/shots/*.png, 87–324 KiB, zero page errors).

## 11. Final verification (2026-09-11, source-parity packet)

- Full hermetic: `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider
  -W error` → **2875 passed, 1 skipped, 67 deselected, 0 failed**.
- UI-gated: `-m ui` → **4 passed** (19 routes, Talk adapters, docs
  render, 12 screenshots in /tmp/opencode/shots, 87–324 KiB each,
  zero page errors).
- New suites: test_git_scope8u (9), test_docs8u (5),
  test_hermes_frontend_map8u (5). One repaired failure during the run:
  rebrand scan tripped by a "Hermes …" code comment in
  frontend/index.html — reworded (production comment only, no test
  touched); 14/14 rebrand+branding green after.
- Wheel: `uv build` → qubit_voicebridge-1.0.0 wheel (248 files;
  git_scope.py, docs.html, _data/docs/*, regen completions verified
  inside). Isolated `uv venv` + `--no-deps` install: version +
  `qubit`/`qubit-dev`/`voicebridge` console entry points verified.
  Full-dependency live install remains manual (native stack).
- `compileall`, `git diff --check`, `graft build`: clean. Rollback:
  `git checkout -- <M paths from §12> && rm <new files from §12>`.
  Unrelated dirty/untracked files preserved (status diff shows only
  intended additions; pre-existing `.adal` deletion untouched).

## 12. Exact change list (source-parity packet)

Modified: voicebridge/cli.py, voicebridge/webui.py,
voicebridge/voice_commands.py, voicebridge/_data/completions/qubit.{bash,fish,zsh},
voicebridge/_data/completions/voicebridge.{bash,fish,zsh},
frontend/{index,activity,approvals,devices,memory,messages,models,ops,projects,reports,security,sessions,settings,setup,skills,system,tasks,token-setup}.html,
pyproject.toml, docs/FOUNDATION_8T_F.md, docs/UPSTREAM_REUSE.md (R34),
docs/HERMES_FRONTEND_MAP.json (regen-capable),
tests/test_dashboard_families8u.py, tests/test_dashboard_ops8tf.py,
tests/test_voice_commands8tf.py, tests/test_webui_8l.py.
New: voicebridge/git_scope.py, frontend/docs.html,
scripts/hermes_frontend_map.py, tests/test_git_scope8u.py,
tests/test_docs8u.py, tests/test_hermes_frontend_map8u.py,
tests/test_dashboard_ui8u.py, docs/HERMES_FRONTEND_MAP.json.
Frozen plans/checklists/PARITY_HERMES.json: untouched this packet.

## 13. Hermes-first active dashboard + dashboard packaging (owner directive, 2026-09-11)

Status: code-side work for this change group is implemented and tested, but the
overall product-parity verdict remains INCOMPLETE (see §13.8). Frozen plans and
`docs/QUBIT_FROZEN_ACCEPTANCE_CONTRACT.md` were not modified.

Pinned sources (verified with `git rev-parse`):
- Hermes `2237be355906fbe6065ce1815711eee52b2d646e` (tag v2026.9.7)
- OpenClaw `3928bad9badfcb6c7d140530435e806fb8092190` (tag v2026.9.2)
- Oracle clones read-only at `/tmp/opencode/pinned/{hermes-agent,openclaw}`;
  `/tmp/upstream-8tfv3/hermes-agent` symlinked to the Hermes oracle for the
  native text/store differentials.

### 13.1 Hermes-first routing (no normal route serves static Qubit UI)
- `voicebridge/webui.py`: `HERMES_ROUTE_ALIASES` + `hermes_alias_target()`
  redirect every normal route (incl. `.html`/trailing-slash) into the Hermes
  React shell; `/classic/<page>` is the explicitly labelled static fallback;
  `/` -> `/hermes/`.
- `voicebridge/hermes_compat.py`: added `GET /api/ops` (full catalog) and
  `POST /api/ops/run` (any op on the shared `dashboard_ops` broker) so the
  Hermes-shell operations console drives the same backend as CLI and voice.
- Overlay pages (frontend/hermes-qubit/plugins/): new `qubit-ops`,
  `qubit-devices`, `qubit-memory`, `qubit-reports`; `qubit-approvals` gained a
  real page. All call the compat backend; no frontend-only success.

### 13.2 Real dashboard packaging (clean install serves it)
- `pyproject.toml`: `frontend/hermes-dist` declared under
  `[tool.hatch.build.targets.{wheel,sdist}].artifacts`; the `frontend` tree
  (source + overlay) is force-included under `voicebridge/_data/frontend`.
- `voicebridge/webui.py` `_hermes_dist()` / `_serve_hermes_plugin()` and
  `voicebridge/hermes_compat.py` `OVERLAY_DIR` now resolve through
  `voicebridge.resources.resource_path` (package data first, then checkout).
- `scripts/build_dashboard.sh` (new, executable): reproducible build
  (workspace link + `npm install --legacy-peer-deps` + `tsc` + `vite build
  --base=/hermes/`). `frontend/hermes-qubit/README.md` documents it.
- `frontend/hermes-web/package.json` is a DOCUMENTED Qubit patch (recorded in
  `docs/HERMES_SOURCE_COMPARISON.json`): declares the build/lint/test
  devDependencies the upstream monorepo kept in its (unvendored) root
  package.json — `jsdom`, `eslint`, `@eslint/js`, `globals`,
  `eslint-plugin-react-hooks`, `typescript-eslint`. No runtime/Hermes behavior
  changes. `package-lock.json` is a generated lockfile (excluded from the
  vendor hash comparison). Every other vendored file remains byte-identical.

### 13.3 Repaired failures (code-side)
- `voicebridge/release.py`: segment-aware `_is_skipped` so nested
  `frontend/hermes-web/node_modules` is excluded from secret/machine-path scans
  (release qualification returned PASS again).
- `voicebridge/voice_commands.py`: an optional scalar after a `remainder` arg
  is no longer captured from the free text (`create task buy milk` keeps
  title `buy milk`, board defaults `main`); dashboard `cards-create` still
  passes an explicit board. `release.py` and voice parity suites green.

### 13.4 Verification (real counts)
- Full hermetic: `.venv/bin/python -m pytest tests/ -q -W error` ->
  2917 passed, 3 skipped, 48 deselected, 0 failed (~14m).
- UI (`-m ui`, Playwright/Chromium): 11 passed (`test_dashboard_ui8u.py`,
  `test_ui_browser_8l.py`, `test_hermes_playwright8tf.py`,
  `test_dashboard_families8u.py`).
- Frontend: `frontend/hermes-web` `tsc -p . --noEmit` clean; `vitest run`
  295 passed / 40 files; `eslint .` 0 errors (27 warnings).
- Live route probe (active service `http://127.0.0.1:8890`): all 33 required
  routes render the Hermes shell, zero legacy `page-nav`/`main#main-content`
  markers, 0 fatal page errors; 37 screenshots in
  `/tmp/opencode/shots/hermes-first` (desktop + mobile).
- Packaging: `uv build` wheel+sdist both contain 55 `hermes-dist` files and the
  overlay plugins. Isolated `uv venv` + `wheel --no-deps` install serves
  `/` -> `/hermes/`, `/activity` -> `/hermes/logs`, shell `200` with
  token/base-path injection, built asset `200`, 10 plugin manifests, plugin
  entry `200` — with no checkout present.
- `compileall` clean; `git diff --check` clean.

### 13.5 Change inventory (this group)
Modified: voicebridge/webui.py, voicebridge/hermes_compat.py,
voicebridge/release.py, voicebridge/voice_commands.py, pyproject.toml,
tests/test_hermes_serve8tf.py, tests/test_hermes_vendor8tf.py,
tests/test_webui_8l.py, tests/test_dashboard_families8u.py,
tests/test_dashboard_ui8u.py, tests/test_ui_browser_8l.py,
tests/test_branding_scan8tf.py, tests/test_dashboard_ops8tf.py,
tests/test_docs8u.py, tests/test_packet7d.py, tests/test_packet8t.py,
tests/test_packet8e.py, docs/HERMES_SOURCE_COMPARISON.json,
frontend/hermes-qubit/README.md, frontend/hermes-qubit/plugins/qubit-approvals/qubit-approvals.js,
frontend/hermes-web/package.json (documented patch).
New: voicebridge/webui.py alias table, tests/test_hermes_package8u.py,
scripts/build_dashboard.sh, frontend/hermes-qubit/plugins/{qubit-ops,
qubit-devices,qubit-memory,qubit-reports}/.
Generated (VCS-ignored): frontend/hermes-dist/, frontend/hermes-web/package-lock.json.

### 13.6 Rollback (this group)
- Code: `git checkout -- voicebridge/webui.py voicebridge/hermes_compat.py
  voicebridge/release.py voicebridge/voice_commands.py pyproject.toml
  tests/test_hermes_serve8tf.py tests/test_hermes_vendor8tf.py
  tests/test_webui_8l.py tests/test_dashboard_families8u.py
  tests/test_dashboard_ui8u.py tests/test_ui_browser_8l.py
  tests/test_branding_scan8tf.py tests/test_dashboard_ops8tf.py
  tests/test_docs8u.py tests/test_packet7d.py tests/test_packet8t.py
  tests/test_packet8e.py`
  (pre-existing uncommitted edits to webui.py/voice_commands.py by the prior
  packet are NOT distinguishable from these via git; use the exact file list
  above and inspect the diff before reverting.)
- New files: `rm tests/test_hermes_package8u.py scripts/build_dashboard.sh`
  and `rm -rf frontend/hermes-qubit/plugins/{qubit-ops,qubit-devices,
  qubit-memory,qubit-reports}`.
- Regenerate: `git checkout -- frontend/hermes-qubit/plugins/qubit-approvals/qubit-approvals.js`
  only if the prior version is tracked (it is untracked; keep a copy first).
- Service: `systemctl --user restart qubit-voicebridge.service`.
- Build artifacts are regenerable: `scripts/build_dashboard.sh`.

### 13.7 Manual-only / operator checks (not claimed)
- Release signing, live accounts, CAPTCHA/2FA, clean-machine qualification.
- `qubit-voicebridge.service` restart was performed (authorized Qubit service);
  no external Hermes/OpenClaw service was modified.

### 13.8 Honest verdict
This change group makes the Hermes React/Vite dashboard the active application
for every normal route, packages the real dashboard for clean installs, and
keeps CLI/dashboard/voice on one backend for the covered surfaces. It does NOT
establish complete Hermes 1:1 parity, complete OpenClaw capability integration,
or complete voice coverage for every operation; those remain as previously
documented matrices and are not re-verified as complete here. Verdict:
INCOMPLETE.

## 14. Voice surface closure — every catalog op has a real spoken surface (owner directive, 2026-09-11)

Status: implemented and tested. Registry 319 -> 333; catalog 353 -> 367.
Every catalog op now has a real spoken surface: 333 registry entries are
voice-executable, and the 34 Tier-2 extras are either voice-executable (18,
via same-backend voice entries) or CLI/dashboard-only (16, each with a typed
refusal: 14 new `guard_<op>` entries + the 2 pre-existing export/import voice
refusals).

- `voicebridge/voice_commands.py`: new `_b_voice_unsupported` backend +
  `VOICE_GUARDS` (14 ops) + `_register_voice_guards()` registering a
  `guard_<op>` entry per CLI/dashboard-only op. A spoken attempt executes the
  Qubit safety policy (real, audited, redacted) and returns a typed
  `state=refused` with a code and guidance naming the exact safe counterpart;
  it never binds the passphrase / nested-JSON / preview-hash / raw-byte
  payload. Codes: `PASSPHRASE_NEVER_SPOKEN`, `VOICE_NESTED_UNSUPPORTED`,
  `VOICE_PREVIEW_HASH_ONLY`, `VOICE_BYTES_UNSUPPORTED`.
- Guarded ops: actions_prepare/preview/retry/recover, ghost_preview,
  backup_create/restore, brain_onboard_apply, kanban_import,
  opencode_prepare/run_confirmed, sync_offer/accept, webhook_verify.
  (export_state/import_state already had `export_state_voice` /
  `import_state_voice`.)
- `voicebridge/dashboard_ops.py` `catalog()` exposes `voice_guard` for
  extras so the dashboard/ops console can surface the boundary.
- New `tests/test_voice_surface_closure8u.py` (3 tests): every catalog op is
  voice-executable or documented + guarded; each guard really executes a typed
  refusal and stages nothing; guards never bind forbidden args; guards do not
  hijack existing commands. `tests/test_voice_commands8tf.py`
  `COMPLETE_EXAMPLES` extended with the 14 guard examples.

### 14.1 Verification
- Full hermetic: `.venv/bin/python -m pytest tests/ -q -W error` ->
  2920 passed, 3 skipped, 48 deselected, 0 failed (~13m).
- Focused: voice/dashboard/parity/catalog/parity-gap suites 145+ passed.
- `compileall` clean.

### 14.2 Rollback (this group)
- `git checkout -- voicebridge/voice_commands.py voicebridge/dashboard_ops.py
  tests/test_voice_commands8tf.py` and
  `rm tests/test_voice_surface_closure8u.py` (voice_commands.py also carries
  the §13 optional-arg fix; inspect before reverting).
- `systemctl --user restart qubit-voicebridge.service`.

### 14.3 Honest boundary
The 16 guarded ops are NOT voice-executable by design — speaking the
passphrase/nested/hash/byte payload is unsafe. Their voice surface is a typed
refusal plus the CLI/dashboard execution path on the same backend. This is the
Qubit-safe equivalent the frozen contract requires, not silent omission.

## 15. Installed-dashboard acceptance gate (owner directive, 2026-09-11)

- New `scripts/verify_installed_dashboard.sh`: builds the wheel with `uv`,
  installs it into a throwaway `uv venv` with `--no-deps` (+ pyyaml only), and
  boots the WebUI handler to prove the real dashboard is served from package
  data with no checkout present: `/` -> `/hermes/`, `/activity` ->
  `/hermes/logs`, shell 200 with token/base-path injection, a built asset 200,
  plugins list (10), `qubit-ops` entry 200, and the ops catalog (>300 ops).
- The gate script is secret-scan clean (config is built with `yaml.safe_dump`,
  so no `key: value` assignment literal lives in the shell source).
- Run: `scripts/verify_installed_dashboard.sh` -> `installed dashboard gate:
  PASS` (ops=367).

### 15.1 Final gates (this session)
- Full hermetic: 2920 passed, 3 skipped, 48 deselected, 0 failed.
- `compileall` clean; `git diff --check` clean; release qualification PASS.

## 16. Multi-agent completion wave (owner directive 2026-09-11)

Six sub-agents were deployed: two exhaustive read-only audits and four
implementation tasks on disjoint files. Full inventory reports persisted as
`docs/AUDIT_HERMES_2026-09-11.md` and `docs/AUDIT_OPENCLAW_2026-09-11.md`.

### 16.1 New capabilities (all on the shared backend, tested)
- **Autonomous scheduler** (`voicebridge/schedules.py:autofire`, wired in
  `voicebridge/communications/monitor.py` run_hourly after the power/estop
  gate): bounded, idempotent, fail-closed tick that fires due jobs exactly
  once per window with the same receipt/audit as manual `run-due`; estop
  refuses without consuming the window; restart cannot double-fire.
- **Session lifecycle** (`voicebridge/auth_status.py:SessionStore` + `cli.py`
  `sessions search|show|rename|archive|delete --confirm|resume`): private
  0600 metadata tombstones over append-only receipts (redacted, audited,
  recoverable); dashboard `PATCH/DELETE/…/resume` and `?q=` search wired in
  `hermes_compat.py`; voice entries `sessions_search`/`session_show`/
  `session_rename`/`session_archive`/`session_delete`/`session_resume`.
- **Browser screenshot** (`voicebridge/browser_tabs.py:screenshot`): bounded
  (<=2 MiB), clip-clamped, URL/title-redacted CDP `Page.captureScreenshot`
  reusing the existing loopback session; typed `BROWSER_UNAVAILABLE` with no
  browser; wired to CLI `browser screenshot` and voice `browser_screenshot`
  (voice carries metadata only — pixels never enter the receipt).
- **Messaging breadth** (`voicebridge/messaging.py`): real provider rows for
  discord/telegram/signal/matrix/msteams/imessage with honest
  `UNAVAILABLE`/`MSG_TRANSPORT_UNAVAILABLE` states; CLI provider choices now
  derive from `messaging.PROVIDERS`; dashboard `h_platforms` no longer reports
  a fabricated "connected" (derives typed link state).
- **Wrong-verb gates** closed: `PUT /api/config/raw`, `POST /api/env/reveal`,
  `POST|PUT|DELETE /api/credentials/pool`, `POST /api/mcp/servers/{id}/auth`,
  `PUT /api/mcp/servers/{id}/enabled`, `PUT /api/mcp/servers`,
  `GET /api/tools/toolsets/{id}/models` all return their typed refusal instead
  of 404. Catalog 367 -> 374 ops.

### 16.2 Verification (real)
- Full hermetic: **2965 passed, 3 skipped, 48 deselected, 0 failed** (~15m).
- UI (`-m ui`): **11 passed**. Installed-dashboard gate
  `scripts/verify_installed_dashboard.sh`: **PASS** (ops=374).
- `release_qualify.py --static`: **PASS**; `compileall` clean;
  `git diff --check` clean.

### 16.3 Audit gaps still open (honest; see the two audit docs)
- Hermes: 275 API routes -> 164 mapped / 111 unmapped (many gated-policy
  routes missing the right verb, now partly closed); genuinely absent surfaces
  remain for Git (21), local models (16), Files/FS (12), Audio (6), plus small
  readers. WebSocket endpoints remain intentionally absent (single SSE path).
- OpenClaw: 72 capability rows -> 23 implemented / 9 partial / 8 gated /
  32 missing (control-ui widgets, inbound webhook endpoint, paired node host,
  background task ledger, session delivery, web search, provider plugin
  runtime, media generation, ACP client, file apply_patch, etc.).

### 16.4 Rollback (this wave)
- `git checkout -- voicebridge/communications/monitor.py voicebridge/cli.py
  voicebridge/hermes_compat.py voicebridge/messaging.py
  voicebridge/voice_commands.py tests/test_voice_commands8tf.py` (note:
  auth_status.py, schedules.py, browser_tabs.py were modified by agents and are
  otherwise uncommitted; inspect before reverting).
- `rm tests/test_scheduler_autofire8u.py tests/test_session_lifecycle8u.py
  tests/test_browser_screenshot8u.py tests/test_messaging_breadth8u.py
  tests/test_hermes_command_parity8u.py tests/test_release_gates8u.py`
- `rm docs/AUDIT_HERMES_2026-09-11.md docs/AUDIT_OPENCLAW_2026-09-11.md`
- `systemctl --user restart qubit-voicebridge.service`.

## 17. Third multi-agent wave — ingress, read routes, brokered file patch (2026-09-11)

Three more sub-agents (11 total this session) on disjoint files; the
file-patch CLI/voice wiring was integrated here.

### 17.1 New capabilities (shared backend, tested)
- **Inbound webhook ingress** (`voicebridge/webhooks.py:ingest`, mounted
  `POST /hooks/<sub_id>` + `POST /hermes/hooks/<sub_id>` in
  `voicebridge/webui.py`): signature-authenticated (not the dashboard token),
  body/timestamp caps, signature + delivery-id replay cache, subscription
  resolution, redacted/audited receipts; missing/bad signature, stale
  timestamp, replay, unknown sub and oversized body each return a typed code.
- **Hermes small read routes** (`voicebridge/hermes_compat.py`):
  `/api/health` (lifecycle health, typed DEGRADED allowed),
  `/api/cron/jobs/{id}/runs` (schedules.history),
  `/api/model/recommended-default` (_qubit_model),
  `/api/egress/status` (proxy/policy), `/api/ssh/ownership` (typed gate).
- **Brokered file patch** (`voicebridge/file_patch.py`): confined
  (approved-root realpath + symlink-escape rejection), strict safe unified-diff
  subset (no eval/shell), atomic write with 0600 backup + rollback, before/after
  hashes, estop-aware, confirmation-gated, redacted; wired to CLI
  `files patch --patch-file|--patch --confirm` and `files rollback --confirm`
  and voice `file_patch_rollback` (confirmed); applying a diff by voice is a
  typed refusal (`guard_file_patch`). Catalog 374 -> 376; registry 342.

### 17.2 Verification (real)
- Full hermetic: **2997 passed, 3 skipped, 48 deselected, 0 failed** (~14m).
- UI (`-m ui`): **11 passed**. Installed-dashboard gate: **PASS** (ops=376).
- `release_qualify.py --static`: **PASS**; `compileall` clean;
  `git diff --check` clean.
- Live probe on :8890: `/api/health`, `/api/egress/status`,
  `/api/model/recommended-default` -> 200; unsigned `/hooks/nope` -> typed 404.

### 17.3 Rollback (this wave)
- `git checkout -- voicebridge/voice_commands.py voicebridge/cli.py
  voicebridge/hermes_compat.py voicebridge/webui.py voicebridge/webhooks.py
  tests/test_voice_commands8tf.py` (webui.py/webhooks.py/hermes_compat.py also
  carry §16 work; inspect before reverting).
- `rm voicebridge/file_patch.py tests/test_webhook_ingress8u.py
  tests/test_hermes_read_routes8u.py tests/test_file_patch8u.py`
- `systemctl --user restart qubit-voicebridge.service`.

### 17.4 Remaining (unchanged, audit-backed)
Hermes still has genuinely absent surfaces (Git review, local models,
Files/FS breadth, Audio) and OpenClaw still has missing capabilities (control-ui
widgets, paired node host, background task ledger, schedule delivery, web
search/fetch, provider plugin runtime, media generation, ACP client, etc.).
These are enumerated in `docs/AUDIT_HERMES_2026-09-11.md` and
`docs/AUDIT_OPENCLAW_2026-09-11.md`. Verdict remains INCOMPLETE.

## 18. Fourth/fifth multi-agent waves — read surfaces, delivery, web tool, boards (2026-09-11)

Five more sub-agents (16 total this session) on disjoint files; all reported
blockers were integrated here.

### 18.1 New capabilities (shared backend, tested)
- **Hermes read surfaces** (`voicebridge/hermes_compat.py`): real read-only
  `GET /api/git/status|log|diff|show|review/diff|file-diff` (git_scope) and
  `GET /api/fs/list|tree|stat|read|read-text|read-data-url|git-root|default-cwd`
  (files/git_scope), plus `GET /api/audio/status|list|devices` (audio.settings).
  Binary-stream/media and all writes return typed gates, never fabricated 200s.
- **Schedule delivery** (`voicebridge/schedules.py`): jobs may declare
  `deliver_to` channel/webhook targets (config-time validation; run-time
  exactly-once delivery via the existing messaging outbox; typed
  `SCHEDULE_DELIVERY_*` when no transport/credential; estop-aware; redacted;
  delivery rows separate from the run receipt). Wired CLI `schedules create
  --deliver-to` + `schedules deliveries`, and cron wire shape `deliver`/
  `last_delivery_error`.
- **Web fetch/search tool** (`voicebridge/web_tool.py`): brokered, SSRF-guarded
  (scheme allowlist, private/loopback/link-local/metadata block, redirect
  re-validation, size/time caps, redaction, estop) `fetch` and `search`;
  typed `WEB_SEARCH_UNAVAILABLE` without a provider. Wired into the planner
  (`web.fetch`/`web.search`) and orchestrator `_agent_tool`, CLI
  `websites fetch|search`, and voice `web_fetch`/`web_search`.
- **Board tabs + rich widgets** (`voicebridge/kanban.py`): ordered tabs,
  widget content kinds (note/text/link/inert html/svg), tab-scoped grants as a
  ceiling, additive migration, snapshots include tabs/roles. Wired to voice
  `kanban_tab_list|create|rename|delete|move` and `kanban_widget_add` (with
  `tab`), and the `qubit-boards` overlay plugin now renders tabs + widgets with
  inert html/svg in a sandboxed iframe (no innerHTML).

### 18.2 Verification (real)
- Full hermetic: **3063 passed, 3 skipped, 48 deselected, 0 failed** (~14m).
- UI (`-m ui`): **11 passed**; release qualification: **PASS**;
  `compileall` clean; `git diff --check` clean.
- Live probe on :8890: qubit-boards plugin 200;
  `kanban_tab_list`/`kanban_widget_list` execute; ops catalog **383**.

### 18.3 Rollback (this group)
- `rm voicebridge/web_tool.py tests/test_hermes_readsurfaces8u.py
  tests/test_schedule_delivery8u.py tests/test_web_tool8u.py
  tests/test_boards_tabs8u.py`
- `git checkout -- voicebridge/hermes_compat.py voicebridge/schedules.py
  voicebridge/kanban.py voicebridge/planner.py voicebridge/orchestrator.py
  voicebridge/cli.py voicebridge/voice_commands.py voicebridge/dashboard_ops.py
  tests/test_voice_commands8tf.py tests/test_packet4d.py` (these files also
  carry earlier waves; inspect before reverting).
- Frontend: restore `frontend/hermes-qubit/plugins/qubit-boards/qubit-boards.js`
  from your pre-wave copy (untracked). `systemctl --user restart
  qubit-voicebridge.service`.

### 18.4 Remaining (audit-backed)
Hermes still has absent read surfaces (local models, audio streaming, media)
and OpenClaw still has missing capabilities (paired node host, background task
ledger, provider plugin runtime, media generation, ACP client, richer inbound
channels). See `docs/AUDIT_HERMES_2026-09-11.md`,
`docs/AUDIT_OPENCLAW_2026-09-11.md`. Verdict remains INCOMPLETE.

## 19. Qubit-owned Hermes runtime foundation + OpenClaw capability wave (owner amendment 2026-09-11)

Status: foundation and four capabilities implemented and tested; overall
platform parity remains INCOMPLETE (see §19.6). Frozen plan files were not
rewritten; the runtime requirement change is recorded as an owner amendment
(`docs/QUBIT_OWNER_AMENDMENT_2026-09-11_HERMES_RUNTIME.md`, appended to the
frozen contract).

### 19.1 Hermes runtime foundation (Qubit-owned fork)
- Vendored the exact pinned Hermes source `2237be355906fbe6065ce1815711eee52b2d646e`
  (tag `v2026.9.7`, MIT, Copyright (c) 2025 Nous Research) into `qubit_hermes/`
  (12173 files; 188 MiB) with the upstream `LICENSE` retained.
- Per-file provenance: `docs/QUBIT_HERMES_VENDOR_MANIFEST.json` +
  `scripts/hermes_vendor_manifest.py` (revision, upstream blob, local sha256,
  patched flag). Result: 12173 files, **0 patched**, 0 missing, 0 extra.
- `voicebridge/hermes_runtime.py`: single bootstrap (`runtime_root`,
  `ensure_path`, `run`, `status`, `entry`). The real Hermes CLI runs in-process
  from the Qubit tree; `run`/`status` restore `sys.path`/`os.environ` so the
  fork's top-level `tests/`/`tools/` and dotenv cannot shadow/leak into Qubit.
- CLI: `qubit agent <args…>` (product-facing) dispatches verbatim to the
  vendored runtime; `hermes` remains an undeclared migration alias. Added
  console entry `qubit-hermes = voicebridge.hermes_runtime:entry`.
- Dependencies: the 34 pinned upstream core dependencies added to
  `pyproject.toml`; installed in the venv.
- Scans: `qubit_hermes` excluded (with provenance rationale) from the Qubit
  secret/machine-path scans (`voicebridge/release.py`) and from pytest
  collection (`pytest.ini`); `hermes_cli` registered as a local (non-PyPI)
  import in `release.py` and `scripts/config_doctor.py`.
- Proof: `tests/test_hermes_runtime_vendor8u.py` (7 tests) — fork present +
  LICENSE, manifest pins the exact revision with 0 patches, manifest covers
  every on-disk fork file, runtime imports from the fork (subprocess), status
  reports the pin, and `qubit agent --version` runs the vendored CLI.

### 19.2 Four OpenClaw capabilities (new backends, shared backend path)
- `voicebridge/node_host.py` — paired node host registry/enroll/revoke/
  capabilities/dispatch with typed `NODE_*` gates and estop/confirmation.
- `voicebridge/task_ledger.py` — read-only unified ledger over the real
  opencode/schedules/kanban run stores; typed `TASK_*` states.
- `voicebridge/media_gen.py` — image/music/video/speech provider registry with
  typed `MEDIA_*` gates; only a real OpenAI-image transport is wired, all else
  honest UNAVAILABLE without credentials.
- `voicebridge/local_models.py` — local-models reads (status/hardware/catalog/
  jobs) + typed `LOCAL_MODEL_*` gates for install/download/sideload/activate.
- Backends/tests: `tests/test_node_host8u.py` (10),
  `tests/test_task_ledger8u.py` (12), `tests/test_media_gen8u.py` (15),
  `tests/test_local_models8u.py` (10).

### 19.3 CLI + dashboard + voice wiring
- `voicebridge/voice_commands.py`: 4 backend dispatchers, 25 registry entries,
  `CLI_COVERAGE`/`VOICE_IMPOSSIBLE` extended. Registry 349 -> **374**.
- `voicebridge/cli.py`: new families `node`, `tasks`, `media`, `local-models`
  (+ `agent`), each dispatching to the same module. CLI families 69 -> **74**.
- Dashboard Tier-1 auto-exposes every registry entry (same backend); catalog
  383 -> **408**.
- Proof: `tests/test_capability_wiring8u.py` (17 tests) — voice resolve+execute/
  typed gate, CLI parse+dispatch, dashboard catalog for all four modules.

### 19.4 Audit honesty repairs
- `voicebridge/capability_catalog.py`: a row's explicit `status` now wins;
  bare verdict `MISSING` resolves to `NOT_UPSTREAM` (never silently
  IMPLEMENTED). Regression test added.
- `tests/test_native_equivalents8u.py`: strengthened from presence-only to
  real routing (voice backend registered + documented example resolves) and
  real assertions in cited test files.
- Repaired 5 latent registry entries whose `examples=` was a bare string
  (missing comma) — `ghost_action_preview/click/read_tab/retry/recover`.
- Regenerated shell completions for the new families.

### 19.5 Verification (real)
- Full hermetic: `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider
  -W error` -> **3136 passed, 3 skipped, 48 deselected, 0 failed** (15m16s).
- `compileall` clean; `git diff --check` clean.
- Fixes during the run: `hermes_cli` local-import registration
  (release.py/config_doctor.py), fork path/env isolation in
  `hermes_runtime`, media-test credential isolation, completions regeneration,
  and rebuilding free disk (pytest temp) so recovery tests met their 2 GiB
  floor.
- Upstream baseline recorded in `docs/UPSTREAM_BASELINE_2026-09-11.md`
  (Hermes pin is already latest; OpenClaw audited pin v2026.9.2 is two stable
  releases behind v2026.9.4 — re-audit outstanding).

### 19.6 Still incomplete (honest; directive not satisfied)
- **Full Hermes parity is NOT complete**: the vendored Hermes backend/dashboard
  is not yet the active front door (Qubit's `webui.py` still serves the
  frontend through `hermes_compat`); many Hermes routes/CLI leaves remain
  unmapped or gated (see `docs/AUDIT_HERMES_2026-09-11.md`).
- **OpenClaw integration is NOT complete**: provider-backed plugin runtime,
  richer inbound channels, ACP external harness client, gateway server, remote
  node transport, media providers, and the v2026.9.4 re-audit remain.
- **Final Qubit rebrand is NOT complete** (upstream identity remains in the
  vendored fork by design; product-surface rebrand of the active dashboard is
  outstanding).
- **Packaging is NOT complete**: `qubit_hermes/` is not yet shipped in
  wheel/sdist; clean-install serving of the fork is unproven.
- UI (`-m ui`), frontend build/typecheck/lint, and clean-install gates were not
  re-run this wave.
- **Verdict: INCOMPLETE.**

### 19.7 Rollback (this wave)
- `rm -rf qubit_hermes/` (vendored fork) and
  `rm docs/QUBIT_HERMES_VENDOR_MANIFEST.json docs/UPSTREAM_BASELINE_2026-09-11.md
  docs/QUBIT_OWNER_AMENDMENT_2026-09-11_HERMES_RUNTIME.md
  scripts/hermes_vendor_manifest.py voicebridge/hermes_runtime.py
  voicebridge/node_host.py voicebridge/task_ledger.py voicebridge/media_gen.py
  voicebridge/local_models.py tests/test_hermes_runtime_vendor8u.py
  tests/test_node_host8u.py tests/test_task_ledger8u.py tests/test_media_gen8u.py
  tests/test_local_models8u.py tests/test_capability_wiring8u.py`
- `git checkout -- voicebridge/cli.py voicebridge/voice_commands.py
  voicebridge/release.py voicebridge/capability_catalog.py
  scripts/config_doctor.py pytest.ini pyproject.toml tests/test_voice_commands8tf.py
  tests/test_native_equivalents8u.py tests/test_capability_catalog.py
  tests/test_hermes_runtime_vendor8u.py voicebridge/_data/completions`
  (several of these also carry earlier-wave edits; inspect before reverting).
- `systemctl --user restart qubit-voicebridge.service`.
