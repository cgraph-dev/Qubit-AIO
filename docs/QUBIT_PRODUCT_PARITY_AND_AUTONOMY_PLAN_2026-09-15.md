# Qubit Product Parity, Autonomy, and Quality Plan

Status: **ACTIVE OWNER-DIRECTED MASTER PLAN — 2026-09-15**

This is the current implementation plan for the local Qubit product. It
consolidates the mistakes found across prior work into enforceable acceptance
work. Historical plans and evidence stay intact; this plan does not rewrite
their history or mark any prior claim as complete.

The controlling requirements are the frozen acceptance contract and the dated
owner amendments, including the Hermes-runtime, terminal-status, and
provider/voice-pluggability amendments. If this plan conflicts with one of
those documents, stop and report the exact conflict.

## Product target

Qubit is one local, Qubit-branded agent product built on the exact pinned
Hermes source foundation, with all supported local OpenClaw capabilities
integrated on top and Qubit VoiceBridge controlling the same operations. The
user uses `qubit`, a Qubit dashboard, text, or voice—not upstream launchers,
routes, or dashboards. Qubit-Brain4 is a replaceable local model profile;
other local, API, and realtime voice providers are selectable adapters.

The local product target excludes rebuilding upstream hosted account portals,
billing, hosted documentation, or third-party cloud account creation that is
not included in the pinned local source. Local clients/integrations for those
services still need truthful, working workflows where the source supports them.

## Corrections to prior execution failures

These are release blockers, not lessons learned to ignore:

1. **Source presence was mistaken for working parity.** A vendored file,
   manifest, compatibility route, tile, test stub, or 200 response does not
   prove a user workflow. Every source capability needs caller-to-result proof.
2. **A reduced custom shell replaced upstream workflows.** The complete Hermes
   dashboard structure, pages, controls, dialogs, states, and recovery flows
   must remain functional before Qubit visual enhancement. Do not call a
   centered Talk page or aliases to unrelated pages parity.
3. **Chat, achievements, boards/kanban, plugins, and sessions were reported
   before their real stores/transports worked.** All pages must be audited at
   control level through the production listener and persistent store.
4. **Compatibility code was treated as a final backend.** The pinned Qubit
   fork is the effective local foundation. Adapters remain only where they
   delegate to one canonical Qubit operation—not as parallel authorities.
5. **Hard-coded Brain4/Ollama assumptions were mistaken for model support.**
   Provider, model, ASR, TTS, realtime voice, routing, credentials references,
   budgets, and fallback must be profile-driven as specified in the provider
   plan.
6. **Voice refusals, status text, and command aliases were counted as control.**
   Real supported operations must execute through voice. A genuinely unsuitable
   spoken-secret/binary/import payload gets a clear safe handoff, never a false
   implementation claim.
7. **Tests were run repeatedly before implementation was coherent, while final
   end-to-end UI behavior was under-tested.** Batch by workstream; use targeted
   tests during implementation; reserve the costly full gates for the finished
   integrated tree. Final release requires every route/control workflow.
8. **Normal upstream branding and paths persisted.** `/hermes/`, upstream
   product labels, service names, help text, artifacts, and runtime paths are
   not acceptable normal Qubit surfaces. Legal/provenance/fixture boundaries
   are the only exception.
9. **Duplicate stores and configuration drift persisted.** For each domain
   (sessions, models, profiles, boards, memory, approvals, settings) there is
   exactly one canonical writable store and explicit tested migration/read-only
   compatibility only.
10. **Generated code, stale plans, and dead code were accepted.** Every normal
    source path must have an owner, caller, test, and purpose; otherwise remove
    it safely or document an intentional compatibility boundary.

## Required workstreams

### A. Source and behavioral parity audit

- Re-clone or verify the exact pinned Hermes/OpenClaw revisions used by the
  vendor manifests; do not rely only on old reports.
- Produce a machine-readable capability, route, CLI, control, and state-machine
  map. Include every page control, not only endpoint names.
- Compare local behavior against source fixtures and, when locally runnable,
  source behavior. Record `implemented`, `safe equivalent`, `operator only`,
  or a concrete open defect with source/caller/test references.
- Treat all unexplained byte differences, adapted files, omitted controls,
  unreachable handlers, and bypass paths as audit rows until resolved.

### B. Runtime, CLI, and state authority

- Make the pinned Qubit-owned Hermes foundation the effective local runtime.
- Preserve all local Hermes CLI families under `qubit`, with equivalent
  arguments, help, result schemas, state/recovery, and exit codes.
- Add OpenClaw capability families to the same `qubit` command hierarchy.
- Eliminate duplicate/legacy writable stores or perform a reversible migration
  to one canonical Qubit store. Test restart, import, migration, rollback,
  recovery, corruption, and concurrent access.

### C. Dashboard functional and visual parity

- Preserve every original local Hermes route/page/workspace/control, then add
  OpenClaw workflows in the same Qubit information architecture.
- Audit all forms, actions, menus, dialogs, panels, live streams, keyboard
  paths, empty/error/loading/degraded/recovery states, and persisted results.
- Use screenshot, DOM, and user-flow comparison at desktop/tablet/mobile. Fix
  layout/spacing/type/overflow/contrast issues rather than declaring a route
  “covered.” Qubit polish is allowed only after functional equivalence.
- Require a production listener test for each UI action. No static mock,
  frontend-only state, fake success, or dead control is accepted.

### D. Voice, browser, desktop, and device execution

- Map every applicable CLI/dashboard operation to a natural voice intent and
  the same backend operation, with profile-aware language/speech settings.
- Complete real browser/app controls where the source supports them: attach,
  open, read/analyze, navigate, tab/window control, media playback control,
  bounded input, screenshot, cancellation, recovery, and truthful unavailable
  state. Use explicit scopes and the active policy/autonomy profile.
- Validate microphone, audio output, ASR, TTS, PTT/wake, barge-in, device
  switching, and optional realtime speech on actual hardware only at the final
  operator-qualification phase.

### E. Models, providers, Brain4, and memory

- Execute `QUBIT_PROVIDER_VOICE_PARITY_PLAN_2026-09-15.md` completely.
- Keep Qubit-Brain4 optional; support local and approved external provider
  profiles, explicit hard-question routing, local-only mode, and visible
  fallback/cost/egress behavior.
- Complete Qubit-Brain4's separate candidate/training/evaluation/promotion/
  rollback lifecycle under the owner-approved advanced research policy.
- Retain one Qubit memory/context authority and make provider changes incapable
  of losing, duplicating, or exposing memory.

### F. Autonomy and permissions

- Implement user-owned autonomy profiles: selectable scope sets, targets,
  schedules, expiration, concurrency/resource/network/provider budgets,
  pause/revoke, audit, and emergency stop.
- Support a clear progression: observe, assisted, trusted scoped, and
  autonomous scoped. “Autonomous” means pre-authorized scope execution—not
  invisible behavior or a model self-granting privileges.
- Make the effective autonomy policy visible in CLI/dashboard/voice status and
  receipts. Test unattended recovery, stop, restart, scope expiry, and abuse
  attempts from untrusted content.

### G. Rebrand, cleanup, packaging, and lifecycle

- Remove upstream names from normal Qubit paths/text/services/artifacts while
  preserving licenses/provenance and source fixtures truthfully.
- Run dead-code, stale-config, duplicate-route, direct-provider, direct-store,
  stale-alias, and unused-asset audits; fix or remove with targeted tests.
- Prove build, wheel/sdist, clean install, upgrade, migration, rollback,
  uninstall, desktop/service launch, and one authoritative local listener.

## Agent deployment rules

Use independent agents only with an exclusive file ownership table and concrete
test/evidence deliverable. Recommended waves: audit first; runtime/state;
provider/voice; CLI/voice; dashboard/control UX; browser/desktop/OpenClaw;
packaging/rebrand; then an independent verifier. The integrator alone merges
overlapping work. No agent may rewrite frozen requirements, turn a defect into
a terminal status, suppress a failing test, or create broad formatter churn.

## Completion gate

Do not claim 1:1, 100%, or ready to replace Hermes/OpenClaw until:

- zero unexplained local-source inventory/control rows remain;
- all supported local upstream workflows work through Qubit CLI, dashboard,
  and voice as applicable;
- all dashboard controls and provider/voice profiles are production-tested;
- no normal upstream branding/paths, duplicate authority/store, dead code, or
  stale configuration remains;
- full final regression, UI, packaging, lifecycle, security, source audit, and
  independent-verifier gates pass; and
- remaining physical hardware, accounts, credentials, and release signing are
  explicitly qualified by the operator.
