# Execution Prompt — Qubit Provider, Voice, and Full Local Parity

You are implementing Qubit. Work in `/home/trick/Projects/Repos/qubit-voicebridge`.
Do not claim completion from a ledger count, a route returning 200, a rendered
page, or a focused test alone.

Read, in this order, before changing code:

1. `AGENTS.md`
2. `docs/QUBIT_FROZEN_ACCEPTANCE_CONTRACT.md`
3. `docs/QUBIT_OWNER_AMENDMENT_2026-09-11_HERMES_RUNTIME.md`
4. `docs/QUBIT_OWNER_AMENDMENT_2026-09-13_TERMINAL_STATUSES.md`
5. `docs/QUBIT_OWNER_AMENDMENT_2026-09-15_PROVIDER_AND_VOICE_PLUGGABILITY.md`
6. `docs/QUBIT_EXECUTION_DIRECTIVE_2026-09-12.md`
7. `docs/QUBIT_PRODUCT_PARITY_AND_AUTONOMY_PLAN_2026-09-15.md`
8. `docs/QUBIT_PROVIDER_VOICE_PARITY_PLAN_2026-09-15.md`
9. `docs/QUBIT_PLAN_INTEGRITY_AUDIT_2026-09-15.md`
10. the current gap ledger, source inventory, byte-parity evidence, and the
   pinned Hermes/OpenClaw provenance records.

## Mission

Finish the local Qubit product: pinned Hermes runtime foundation; every
supported local Hermes workflow; every approved local OpenClaw capability;
Qubit CLI, dashboard, and VoiceBridge over the same operation/broker/session
path; a complete Qubit rebrand; and configurable model/provider/voice profiles.

Qubit-Brain4 is a default local model profile, not the product backend. A user
must be able to select, validate, activate, roll back, replace, or remove it;
select a documented permitted API model for difficult work; or select local or
external/realtime speech providers. No provider may bypass Qubit policy,
session, audit, e-stop, consent, redaction, budgets, or cancellation. The owner
also authorizes advanced locally held Brain4 candidates and scoped autonomous
operation, as long as all activity stays within an explicitly configured,
visible, revocable autonomy profile.

## Required method

1. Snapshot `git status --short`, hash unrelated dirty/untracked files, and
   identify the existing canonical state/config/operation registry before edits.
   Preserve all unrelated changes. Never use reset, clean, broad checkout,
   destructive deletion, or mass formatting.
2. Run P0 from `QUBIT_PROVIDER_VOICE_PARITY_PLAN_2026-09-15.md` first. Produce
   an evidence-backed source/caller/control inventory. Include all routes,
   buttons, forms, modals, CLI leaves, voice entries, provider callers,
   hard-coded model aliases, fallback paths, stores, and upstream parity rows.
3. Divide agents by exclusive file ownership, not vague themes. Recommended
   independent assignments:
   - A: provider/profile contracts, migration, security, secrets, egress;
   - B: local/API/realtime adapters and router/cancellation/budgets;
   - C: CLI and VoiceBridge command parity;
   - D: dashboard Models/Providers and Setup/Voice UX plus full controls;
   - E: Hermes/OpenClaw source/control/behavior differential audit;
   - F: dashboard route/control Playwright, responsive/a11y, branding/dead code;
   - G: packaging, clean-install, lifecycle, provenance, independent evidence.
   Give each agent a concrete path list and a handoff file. Do not allow two
   agents to edit the same file. Integrator resolves conflicts and runs a
   production-caller test after merging each workstream.
4. Implement functions, not labels: a page/control is complete only when the
   authenticated production UI invokes a real operation, the operation reaches
   the shared backend, mutations persist/recover, and CLI/dashboard/voice
   receipts agree. Fix duplicate stores and direct provider calls rather than
   documenting them away.
5. Treat Qubit rebranding as a normal-surface requirement. No `/hermes/`
   normal route, upstream product name, service label, title, path, CLI help,
   generated artifact, or runtime log remains outside legal/provenance/fixture
   boundaries. Do not modify source licenses or provenance to hide origin.
6. For every autonomous action, prove the pre-authorized scope, target,
   expiry, budget, audit receipt, cancellation, and e-stop behavior. An agent
   model or untrusted content must not be able to grant or expand authority.
7. Do not test the full suite after every edit. Batch one coherent workstream,
   then run focused affected tests. Run full hermetic/UI/package/clean-install
   gates only when implementation is ready. Do not weaken tests, skip a
   failure, flip ledger state by hand, or classify a code defect as manual.
8. Re-run the inventory and create an explicit residual list. A verifier who
   did not edit the implementation must reproduce the final evidence.

## Required acceptance evidence

- exact source/revision and every upstream feature/control disposition;
- provider profile CRUD/test/activation/rollback/removal, secret hygiene,
  local-only egress proof, named API routing proof, typed fallback proof;
- local Brain4 plus at least one synthetic adapter and, when credentials are
  supplied, a bounded external API/realtime canary;
- ASR/TTS/realtime voice profiles, devices, PTT/wake/cancel, confirmation,
  status, and fallback through the single Qubit operation/session path;
- all CLI/dashboard/voice operations run against the same backend;
- every dashboard route, visible control, form, menu, dialog, live stream,
  failure state, mobile state, keyboard and accessibility path exercised;
- no visual drift, dead button, placeholder result, duplicate store, stale
  alias, direct external provider call, or normal upstream branding;
- full final tests, clean install, package/service/migration/rollback/uninstall,
  secret/provenance/dead-code/branding/source inventory checks, and independent
  verification.

If a requirement conflicts with the frozen contract, stop with concrete source
and test evidence and request an owner amendment. Otherwise keep implementing.
At handoff report only: exact commits, evidence commands/output, genuine
operator-only qualification steps, preservation proof, rollback, and remaining
unimplemented rows. Do not call the product 100%, complete, or 1:1 unless the
independent verifier has reproduced all final gates.

Mandatory voice-input preservation rule: read
`docs/QUBIT_VOICE_TO_TEXT_WORKING_PROFILE_2026-09-16.md`. Preserve the current
Chrome/Chromium `SpeechRecognition` dashboard path as Qubit’s preferred online
provider (`en-US`, interim results, one alternative, final transcript to the
canonical Qubit backend). Do not replace it with Hermes ASR or claim the
Chrome engine is locally embedded. Keep local Sherpa Parakeet as the explicit
offline/private fallback and test both providers.
