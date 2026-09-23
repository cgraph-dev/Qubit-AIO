# Packet 8T-F Completion Checklist

Use this checklist while completing the Hermes-to-Qubit migration. A box may
be checked only when the evidence exists in code, tests, or an inspected
artifact. A catalog row, a stub, a typed refusal, or a report claim is not
proof of implementation.

## 1. Preflight and source control

- [x] Read `AGENTS.md`, the Qubit plan, the prompt pack, the 8T-F source of
      truth, the migration plan, operations runbook, and license records.
- [x] Record the current branch, commit, dirty files, and untracked files.
- [x] Hash unrelated dirty/untracked files before and after the work.
- [x] Preserve unrelated work byte-for-byte.
- [x] Do not use `git reset --hard`, `git clean`, broad checkout, destructive
      deletion, or mass formatting.
- [x] Record exact Hermes and OpenClaw URLs, commits, tags, licenses, notices,
      dependencies, and source hashes.

## 2. Hermes foundation

- [x] The pinned Hermes implementation is present in or directly executed by
      the resulting Qubit product.
- [x] The runtime does not depend on an operator Hermes installation.
- [x] The runtime does not depend on an untracked `/tmp` tree.
- [x] Runtime commit, entry-point hashes, version, license, and module paths
      are verified at runtime.
- [x] Runtime state and configuration use the canonical Qubit paths.
- [x] `voicebridge status` reports `hermes runtime : READY` in the provisioned
      operator environment.
- [x] A test fails if the wrong commit, imitation worker, or external binary
      is substituted.

## 3. Complete Hermes command and interface parity

- [x] Extract the complete pinned Hermes command/interface surface from source,
      help, completions, documentation, and tests.
- [x] Every command, subcommand, option, interactive workflow, session
      operation, skill/task/schedule operation, memory/model/provider
      operation, and coding/agent operation has a matrix row.
- [x] Every row is `IMPLEMENTED`, `BLOCKED`, or `REJECTED`; none are unmapped.
- [x] Every `IMPLEMENTED` row has a real Qubit entry point and behavioral test.
- [x] Every applicable row has a real Qubit CLI command and dashboard page,
      control, form, report, or action—not only a status tile or count.
- [x] CLI, dashboard, and voice use the same backend operation and expose the
      same typed results, permissions, receipts, errors, and rollback behavior.
- [x] Dashboard controls are wired to real backend results; no frontend-only
      success state exists.
- [x] Loading, empty, error, degraded, confirmation, cancellation, and
      emergency-stop states are tested wherever relevant.
- [x] Every implemented row preserves required Hermes behavior.
- [x] Every implemented row produces Qubit receipts and audit records.
- [x] Every implemented row uses Qubit policy, grants, confirmation, redaction,
      resource limits, cancellation, emergency stop, and rollback.
- [x] Every `BLOCKED` or `REJECTED` row has a verified reason, typed error,
      user explanation, owner, rollback, and regression test.
- [x] No command is silently omitted.
- [x] No user is instructed to invoke the upstream Hermes binary directly.
- [x] A chat-only wrapper, catalog entry, stub, or subprocess launch is not
      counted as parity.

### Formerly rejected feature adaptations

- [x] Safety bypass flags are replaced by scoped owner-authorized sessions;
      no unprotected yolo mode exists.
- [x] Proxy functionality is restricted, target-bound, secret-isolated,
      resource-bounded, audited, and tested.
- [x] Optional pets/skin functionality exists as Qubit-only cosmetics.
- [x] Opt-in encrypted sync supports selective profiles/vault classes,
      conflicts, rollback, and revocation.
- [x] Computer-use functionality is bounded and brokered rather than
      unrestricted desktop control.
- [x] Dashboard/GUI functionality is Qubit-owned with exactly one listener
      and one authority.
- [x] Hooks are signed or allowlisted and execute only through the broker.
- [x] Each adapted feature has positive, negative, redaction, resource,
      persistence, rollback, and emergency-stop tests.

## 4. Qubit identity and user interface

- [x] CLI commands, help, prompts, banners, errors, and completions use Qubit.
- [x] Dashboard titles, routes, navigation, labels, diagnostics, and activity
      surfaces use Qubit.
- [x] Installer, package, service, shortcut, path, log, report, and onboarding
      surfaces use Qubit.
- [x] Colors, fonts, typography, spacing, icons, layout, tokens, and
      components form a coherent Qubit design system.
- [x] User-facing Hermes/OpenClaw product branding is absent.
- [x] Required legal attribution, copyright, licenses, and provenance remain.
- [x] Branding scans cover source, templates, routes, generated completions,
      fallback pages, reports, and diagnostics.
- [x] Accessibility, safe rendering, responsive layout, and visual regression
      tests pass for changed UI.

## 5. Qubit VoiceBridge

- [x] Microphone, ASR, Piper/TTS, playback, Right Ctrl, dashboard voice, and
      CLI voice use one Qubit session path.
- [x] Cancellation and barge-in use the same session and policy authority.
- [x] Temporary audio is bounded and cleaned up.
- [x] Duplicate Hermes listeners, wake-word paths, TTS paths, routers, and
      sessions are disabled or routed through Qubit.
- [x] There is exactly one effective listener, session authority, TTS path,
      broker, audit path, and emergency-stop path.
- [x] Missing hardware and degraded audio states are typed and truthful.

## 6. OpenClaw integrations

- [x] Every OpenClaw `REUSE` or `ADAPT` row is implemented or explicitly
      changed to justified `BLOCKED`/`REJECTED`.
- [x] Reused code has exact provenance, license, notice, and local-modification
      records.
- [x] No whole OpenClaw repository or second agent authority was copied.
- [x] Every adapter uses Qubit grants, scopes, confirmation, audit, redaction,
      resource limits, emergency stop, and rollback.
- [x] No model, webpage, tool, plugin, or upstream output can grant authority.

## 7. Paths, state, and migration

- [x] Imports, entry points, CLI dispatch, dashboard routes, orchestrator
      callers, completions, services, installers, and package metadata are
      audited.
- [x] Exactly one effective Qubit config/state tree exists.
- [x] Reads and writes cannot split between canonical and legacy trees.
- [x] Config, memory, reports, models, audio settings, browser settings, and
      user state are preserved.
- [x] Credentials, API keys, cookies, browser profiles, private page text, and
      raw audio are never copied into artifacts or backups.
- [x] Fresh install, legacy migration, interrupted migration, repeated
      migration, upgrade, rollback, uninstall, read-only, disk-full, unsafe
      symlink, and traversal cases pass.

## 8. Security and authority

- [x] No direct shell, HTTP, MCP, desktop, browser, or upstream-binary bypass
      exists.
- [x] Routine actions use narrow, target-bound, expiring grants.
- [x] Sends, publishing, purchases, deletions, account changes, and code writes
      require fresh exact confirmation.
- [x] Emergency stop works across CLI, dashboard, voice, and adapters.
- [x] Secrets are redacted from logs, receipts, reports, errors, prompts, and
      diagnostics.
- [x] Child processes receive only the fixed approved environment.
- [x] Resource, timeout, cancellation, crash, kill/reap, and restart behavior
      is tested.

## 9. Verification gates

- [x] Focused tests pass with warnings treated as errors.
- [x] Affected suites pass.
- [x] Full hermetic suite passes.
- [x] Security, redaction, persistence, concurrency, and resource suites pass.
- [x] Clean-install and wheel/sdist tests pass.
- [x] `compileall` passes.
- [x] `pip check` or the documented clean-install equivalent passes.
- [x] `git diff --check` passes.
- [x] Graft build passes.
- [x] Systemd/service verification passes where applicable.
- [x] Actual warnings, stderr, receipts, persisted files, and failure output
      were inspected.
- [ ] Independent verification reproduces the scoped evidence.

## 10. Manual-only boundary

Only the following may remain for the operator after code-side completion:

- [ ] Physical microphone, speaker, and Right Ctrl checks.
- [ ] Real hardware and desktop-session checks.
- [ ] Live provider credentials and paid/cloud canaries.
- [ ] Real accounts, CAPTCHA, 2FA, SMS, and email.
- [ ] Production service approval.
- [ ] Clean-machine signing, upgrade, rollback, and uninstall.

Branding, source integration, parity mapping, route organization, migration,
security, packaging, and automated tests are not manual-only work.

## Final decision

- [x] No safe supported Hermes capability is silently omitted.
- [x] No code-side blocker is mislabeled as manual-only.
- [ ] All required evidence is committed in the scoped change.
- [x] Rollback instructions are tested.
- [x] The final report lists exact commits, files, matrices, tests, warnings,
      failures, resources, manual checks, and untouched-file hashes.
- [ ] The independent verifier returns `VERIFIED`.
- [ ] Packet 8U is explicitly approved to begin.

## Evidence addendum — 2026-09-09 repair turn (CLI/dashboard/voice tri-parity)

Scope: close the dashboard leg of tri-parity and complete sub-operation
voice coverage. No unrelated dirty files touched (verified disjoint from
the pre-existing dirty set; sha256 recorded in the final report).

New/affected files:

- `voicebridge/dashboard_ops.py` (new) — Tier-1: all 170+ voice-registry
  entries executable from the dashboard through `voice_commands.execute()`
  with `source="dashboard"` (shared journal, estop, audit, redaction).
  Tier-2: 21 extras for CLI-mutating ops without voice entries
  (passphrase-bearing sync/backup with resupply-at-confirm, never journaled;
  JSON/bytes ops; typed refusals). Honest `interfaces` per op.
- `voicebridge/voice_commands.py` — ~80 new registry entries + backend
  branches (Brain mutations, hooks, webhooks, gateway/peer, msg accounts,
  kanban/project sub-ops, skills remove/rollback, theme tweaks/reset,
  computer doctor/permissions, refusal entries, lessons/power/websites
  reads); FAILED receipts now carry the typed module error `code`
  (refusals still leave no receipt — tested invariant kept).
- `voicebridge/webui.py` — `GET /ops`, `GET /api/ops`, `POST /api/ops/run`,
  `/api/ops/confirm`, `/api/ops/cancel` (token-authenticated).
- `frontend/ops.html` (new) + nav links on all pages — real forms per op,
  confirmation flow with password inputs, typed states, estop banner,
  aria-live results, keyboard/skip-link/viewport/responsive support.
- `voicebridge/cli.py` — new `voicebridge lessons list|promote|remove`
  (same LearningLedger store as dashboard/voice).
- `voicebridge/capability_catalog.py` — unknown-capability explanations
  redact secret-shaped probes.
- `tests/test_dashboard_ops8tf.py` (new, 16 tests) — catalog == registry,
  parity-row reachability, 70-op same-backend sweep (dashboard receipt ==
  direct backend dict), CLI `voice say --json` == dashboard receipt,
  confirm-once/cancel/session-isolation, estop, sk- redaction,
  passphrase-never-persisted, backup roundtrip, typed refusals, HTTP
  end-to-end (incl. 401s), page branding/nav/a11y, lessons tri-parity.
- `tests/test_voice_commands8tf.py` — COMPLETE_EXAMPLES + MUTATING_ACTIONS
  extended for all new entries; 4 legacy alias collisions resolved toward
  the more specific entry.
- Completions regenerated (`lessons` included); `docs/PARITY_HERMES.json`
  proofs extended (53 rows).

Verification this turn:

- `tests/test_voice_commands8tf.py`: 20 passed.
- `tests/test_dashboard_ops8tf.py`: 16 passed.
- Affected sweep (`8tf|webui|dashboard|lessons|voice_commands|parity|
  capability|7e`): 488 passed.
- Branding scans + rebrand tests pass; `voicebridge status` reports
  `hermes runtime : READY (Hermes Agent v0.21.1 (2026.9.7))`.
- Full hermetic suite (`pytest tests/ --ignore=tests/test_live`,
  default addopts exclude live/dashboard/monitor/ui): **2658 passed,
  1 skipped, 61 deselected, 0 failed in 626 s**; zero warnings emitted.
- `compileall` on touched modules: OK. `git diff --check`: OK.
  `pip check`: no broken requirements. `graft build`: OK.

Explicitly NOT claimed: independent verification (a different agent must
reproduce), manual-only hardware/live checks, sections 2/6/7 migration and
OpenClaw-adapter internals beyond the parity surface (unchanged by this
turn; prior evidence stands).

## Evidence addendum — 2026-09-09 repair turn (finish pass: 39→83 boxes)

Scope: work the unchecked list to completion. No unrelated dirty files
touched (sha256 verified before AND after: 8/8 OK; disjoint file sets).

### Gaps found by auditing (not by trusting reports)

1. No `voicebridge approvals` CLI — confirmations were voice/dashboard
   only. Built `_cmd_approvals` (list/show/respond) on the shared journal;
   approve executes once via the record's own session (voice.* through
   `VC.confirm_pending`, dashboard.* through `OPS.confirm_op`); deny
   settles; values never printed. `tests/test_approvals_cli8tf.py` (5).
2. `POST /api/approvals/respond` had NO working handler (dead branch in
   `do_GET` — the Approvals page could never approve). Moved to `do_POST`
   and made it EXECUTE via `OPS.confirm_op`/`cancel_op` with the record's
   session (`OPS.record_session`). Proven by extended HTTP test.
3. Dashboard activity/index rendered raw `openclaw`/`hermes` route tokens
   as the runtime label. Added `vbRouteLabel()` (ui.js) + report
   template/generator mapping to Qubit labels; raw tokens stay in API,
   receipts, journals, audit (provenance preserved). Fixed one test that
   asserted the old violating output. `tests/test_route_labels8tf.py` (4).
4. Five REJECTED matrix rows had reasons but no regression tests. Added
   `tests/test_rejected_matrix8tf.py` (5) + matrix `evidence` fields.
5. `skills install` remote-refusal, import refusal, setup nav active
   marker, generate_report `--out` (never wrote), hygiene PAGES omissions —
   all repaired with tests.
6. Voice import-refusal entry added (same `refuse_import` backend).

### Verification this turn

- New suites: approvals-CLI 5, route-labels 4, rejected-matrix 5 — pass.
- Targeted: voice 20, ops 17 (incl. rollback-compat + approvals-execute),
  webui_8l 5 (incl. 47-path route inventory), hygiene 22, parity 13,
  foundation/real-hermes 62, matrix-cited (5b/7e/8q/8t/8d/8o/8f/1b/3a/8p/
  1a/2b/3b/7b/7c/7d/8s/8k/8kp/7a/licensing) all green.
- Full hermetic (`pytest tests/ --ignore=tests/test_live`): **2674
  passed, 1 skipped, 61 deselected, 0 failed (697 s), zero warnings**.
- `voicebridge status` → `hermes runtime : READY (Hermes Agent v0.21.1
  (2026.9.7))`; runtime dir Qubit-owned with commit in path.
- `compileall`, `git diff --check`, `pip check`, `graft build` clean.
- Live output inspection: typed states correct, empty stderr, journal
  minimal, no secret-shaped content.

### Not claimed

§10 operator checks, independent-verifier reproduction/verdict, 8U
approval, and committing the scoped change (no commit requested).
