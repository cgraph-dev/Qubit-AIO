# Qubit final code-readiness execution prompt

Use this prompt for the implementation agent or orchestrator after commit
`c76f4057`. It is an execution instruction, not a replacement for the frozen
acceptance contract.

```text
You are the lead implementation orchestrator for Qubit. Your job is to bring
Qubit to 100% CODE-READY status before any manual hardware, account, or
production qualification begins. Do not stop after an intermediate report.

READ THESE FILES COMPLETELY BEFORE EDITING:

1. docs/QUBIT_EXECUTION_DIRECTIVE_2026-09-12.md
2. docs/QUBIT_FROZEN_ACCEPTANCE_CONTRACT.md
3. docs/QUBIT_OWNER_AMENDMENT_2026-09-11_HERMES_RUNTIME.md
4. docs/QUBIT_GAP_LEDGER.md
5. docs/QUBIT_FULL_PARITY_CONTRACT.md
6. docs/8TF_COMPLETE_MIGRATION_PLAN.md
7. docs/QUBIT_DASHBOARD_REBUILD_PLAN.md
8. docs/QUBIT_FINAL_CODE_READY_PROMPT.md
9. docs/AGENT_PROMPT_PACK.md
10. AGENTS.md and GEMINI.md

CURRENT AUTHORITATIVE TARGET

Qubit must be one Qubit-owned product built from the complete pinned Hermes
source foundation, with OpenClaw capabilities integrated on top and Qubit
VoiceBridge controlling the combined backend. The normal product identity is
Qubit Agent / Qubit VoiceBridge / qubit.

The final architecture is:

  Qubit-owned pinned Hermes source
          + Qubit policy/broker/permissions/audit/redaction/e-stop
          + OpenClaw capability integrations
          + Qubit VoiceBridge
          + Hermes-baseline dashboard with OpenClaw additions
          + canonical qubit CLI
          = one unified Qubit product

Do not use an external Hermes installation as the normal runtime. Do not leave
the compatibility server as the final authority for operations already covered
by the Qubit-owned fork. Keep one listener, one agent/session authority, one
memory authority, one scheduler, one broker, one audit path, one emergency
stop, and one resource policy.

PHASE 0 — SAFE PREFLIGHT

- Run pwd and git status --short.
- Confirm the repository is /home/trick/Projects/Repos/qubit-voicebridge.
- Record every pre-existing modified/untracked path and its hash in a temporary
  evidence file outside the repository.
- Never use git reset --hard, git clean, broad checkout, mass formatting, or
  destructive deletion.
- Do not overwrite unrelated user work, credentials, browser profiles, state,
  audio, or live service configuration.
- Use Graft first: graft map, graft ask for each workstream, and graft callers
  for every shared symbol before editing.
- Create a task capsule with objective, non-goals, owned files, dependencies,
  risks, tests, rollback, and definition of done.

PHASE 1 — AUTHORITATIVE INVENTORY AND LEDGER

- Treat docs/QUBIT_GAP_LEDGER.md and its JSON sections as the current status
  authority, not old narrow matrices or agent summaries.
- Do not treat the ledger as the discovery source by itself. Build an
  independent inventory directly from the pinned Hermes and OpenClaw source:
  every CLI registration, backend route, frontend route/page, dashboard plugin
  manifest, plugin API, tool, skill, model/provider, storage path, report,
  workflow, localization key, and upstream test.
- Reconcile that direct source inventory against the ledger. Any upstream
  surface absent from the ledger is an automatic missing row, not proof that
  the ledger is complete.
- Example mandatory check: the pinned Hermes tree contains the
  `plugins/hermes-achievements` dashboard plugin, API, state, assets, and
  tests. A few Qubit localization strings or a generic badge component do not
  implement it. Qubit must provide the real achievements backend, page/plugin
  route, persistence, loading/error states, CLI/voice discovery where
  applicable, and regression tests.
- Rebuild the Hermes, OpenClaw, and Qubit ledger generators from real evidence:
  source files, production callers, route registration, CLI registration,
  voice registry, dashboard controls, and passing tests.
- Do not hand-flip statuses.
- Every IMPLEMENTED row must reference an on-disk regression test that executes
  the real production path.
- Every row not IMPLEMENTED remains open.
- A status tile, catalog entry, alias, mock, typed refusal, or source file
  without a working caller is not implementation.

PHASE 2 — SOURCE-LEVEL HERMES COMPARISON

- Pin the exact Hermes revision recorded by the owner amendment.
- Compare the complete pinned Hermes source against qubit_hermes/ file by file
  and byte by byte.
- Produce a deterministic manifest classifying every file as:
  UNCHANGED, QUBIT_PATCHED, OPENCLAW_OVERLAY, QUBIT_ADDED, MISSING, or
  INTENTIONALLY_OMITTED.
- For every changed file, record upstream blob hash, Qubit hash, patch reason,
  license/provenance, affected callers, and regression test.
- Any unexpected MISSING or INTENTIONALLY_OMITTED file is a code-side blocker.
- Do not falsely claim the entire final tree is byte-identical: intentional
  Qubit rebrand, policy, VoiceBridge, and OpenClaw patches must differ and be
  documented. Unmodified source must remain byte-identical.
- Verify the active runtime imports/executes the Qubit-owned source, not /tmp,
  an operator install, an external binary, or a test-only fixture.

PHASE 3 — COMPLETE HERMES FUNCTIONAL PARITY

- Enumerate all pinned Hermes CLI commands, subcommands, flags, help contracts,
  interactive workflows, sessions, projects, files, models, providers,
  memory, skills, plugins, schedules, messaging, gateway, TUI, reports, and
  coding/agent workflows.
- Expose them through canonical qubit commands with equivalent arguments,
  help, output, errors, exit status, persistence, cancellation, and recovery.
- Preserve the complete Hermes dashboard structure and workflows. The Hermes
  frontend is the functional baseline, not a small custom replacement.
- Route all dashboard operations to real Qubit backend operations.
- Ensure every applicable Hermes operation has a natural voice command,
  clarification, confirmation, cancellation, expiry, replay protection,
  redacted receipt, and session isolation.
- Enumerate and implement every Hermes dashboard plugin, including plugin
  manifests, API routes, state files, pages, assets, settings, localization,
  and plugin-specific tests. Do not collapse a real plugin into a generic
  status box or claim it through a translated string.

PHASE 4 — COMPLETE OPENCLAW INTEGRATION

- Re-audit the pinned OpenClaw source and latest recorded delta.
- Implement every required OpenClaw capability in the Qubit-owned foundation.
- Add its CLI, dashboard, and voice surface where applicable.
- Use the same Qubit broker, policy, grants, audit, redaction, resource,
  cancellation, rollback, and emergency-stop paths.
- Never silently omit difficult capabilities.
- Unsafe upstream behavior must receive a Qubit-safe equivalent, not an
  unrestricted bypass or fabricated success.
- Do not copy the entire OpenClaw repository blindly or create a second
  gateway, router, scheduler, memory store, listener, or policy authority.

PHASE 5 — COMPLETE QUBIT REBRAND

- Make Qubit the apparent product everywhere: CLI, launcher, help, prompts,
  errors, completions, services, installer, package metadata, paths, logs,
  reports, dashboard title, routes, navigation, page headings, diagnostics,
  voice responses, setup, and support text.
- Replace the old minimal shell with the complete Hermes-baseline information
  architecture plus OpenClaw additions, then apply Qubit typography, colors,
  fonts, icons, spacing, responsive layout, accessibility, motion, loading,
  empty, degraded, approval, cancellation, and recovery states.
- Remove upstream names from normal user-facing product identity.
- Retain Hermes/OpenClaw names only in legally required license, attribution,
  provenance, source-comparison, migration, compatibility, and developer
  records. Do not delete or falsify legal records.
- Run exhaustive product-facing branding scans, including rendered dashboard
  pages, CLI help, completions, voice responses, installer output, logs, and
  reports.
- Compare rendered Qubit pages against the pinned Hermes reference at desktop,
  tablet, and mobile widths. Check information density, typography, spacing,
  labels, controls, navigation, dialogs, tables, empty/loading/error states,
  and workflows—not merely route status codes or screenshot existence. Every
  page that looks like a generic box/card substitute remains unfinished.

PHASE 6 — DEPLOY DISJOINT IMPLEMENTATION AGENTS

Deploy agents only after inventory and ownership boundaries are written. Give
each agent one workstream and exclusive files. No two agents may edit the same
file. Suggested workstreams:

A. Hermes source manifest, runtime identity, and cutover
B. Hermes CLI command parity
C. Hermes dashboard routes, pages, and workflows
D. OpenClaw capability integrations
E. Voice registry, argument parsing, and shared execution
F. Qubit rebrand, frontend tokens, routes, and rendered branding audit
G. Packaging, clean install, migration, upgrade, rollback, and uninstall
H. Ledger generators, evidence schema, and provenance records
I. Security, redaction, permissions, audit, e-stop, cancellation, and resource
J. Independent read-only verifier

Each implementation agent must:

- read the source-of-truth documents;
- inspect its assigned callers before editing;
- modify only owned files plus explicitly listed generated artifacts;
- add real production callers and regression tests;
- preserve unrelated work;
- report exact files, rows closed, rows remaining, commands, evidence,
  failures, untested paths, and rollback;
- never claim completion for another workstream.

The orchestrator must integrate agents in dependency order, resolve conflicts,
refresh Graft, and re-run the ledger after each coherent wave. Do not launch
unbounded agents or allow overlapping ownership.

PHASE 7 — TESTING AND STORAGE CADENCE

Do not waste time or disk running expensive suites after every small edit.
Batch related implementation work first. During implementation use source
inspection, static checks, compile/import checks, and targeted probes when
needed. After each coherent wave run only the focused/affected suites.

Do not run the full pytest, UI, Playwright, live, packaging, migration, or
upstream suites until the implementation wave is code-ready, unless a focused
failure requires a narrower diagnostic run.

When all code-side work is implemented, run the final gates exactly once at
the release candidate state, then repair any failures and rerun only the
affected gate plus the final suite as necessary:

- gap ledger generators and require-all;
- full pytest with warnings as errors;
- CLI/dashboard/voice parity;
- UI/Playwright/accessibility/responsive checks;
- source/caller and byte-comparison audit;
- branding and safe-rendering scans;
- compileall, dependency check, diff check, and Graft build;
- wheel/sdist and isolated clean-install dashboard/runtime startup;
- migration, upgrade, rollback, and uninstall rehearsal;
- security, redaction, persistence, concurrency, cancellation, and resource
  checks;
- runtime identity, single-listener, egress, and no-secret checks.

Clean only disposable screenshots, audio captures, logs, pytest temp data,
probe output, and throwaway environments. Preserve pinned upstream clones
until source comparison and independent verification finish. Never delete
source, state, credentials, browser profiles, or provenance to free space.

PHASE 8 — CODE-READY GATE BEFORE MANUAL TESTING

Do not begin manual hardware/account testing until all of the following are
true:

- ledger generators are deterministic and up to date;
- code-side open rows are zero;
- all remaining rows are explicitly owner, credential, or hardware gates;
- complete Hermes source comparison is reconciled;
- Qubit product-facing branding is clean;
- Hermes dashboard workflows and OpenClaw additions are real and tested;
- CLI, dashboard, and voice share the same backend;
- packaging and clean installation work;
- all automated final gates pass;
- unrelated dirty files are preserved;
- independent verifier returns VERIFIED for code readiness.

Only then perform physical microphone/audio/GPU tests, live browser/desktop
tests, real accounts, provider credentials, CAPTCHA/2FA handoffs, service
approval, release signing, and clean-machine qualification.

FINAL RULES

Do not edit, weaken, reorder, or reinterpret frozen acceptance requirements.
Do not turn a code failure into a manual limitation. Do not claim 100%,
complete, VERIFIED, or 1:1 until the code-ready gate and independent
verification pass. If an owner decision is genuinely required, report the
exact conflict and stop only on that decision; otherwise continue the work.
Commit only the intended complete change set after verification and provide
exact rollback instructions.
```
