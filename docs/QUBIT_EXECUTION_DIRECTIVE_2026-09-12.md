# Qubit execution directive — Hermes fork, OpenClaw integration, VoiceBridge

Status: **OWNER-DIRECTED EXECUTION RULE — final parity not complete**

This document operationalizes the approved runtime amendment in
`docs/QUBIT_OWNER_AMENDMENT_2026-09-11_HERMES_RUNTIME.md`. It does not weaken,
replace, or mark complete any requirement in
`docs/QUBIT_FROZEN_ACCEPTANCE_CONTRACT.md`.

## Required end state

Qubit is a Qubit-owned fork of the exact pinned Hermes source, with the full
supported Hermes backend, CLI, dashboard, sessions, tools, skills, models,
memory, tasks, reports, and runtime executed from the Qubit source tree.
OpenClaw capabilities are integrated into that same foundation. Qubit
VoiceBridge provides the shared voice surface for the union of Hermes,
OpenClaw, and Qubit operations.

```text
Qubit-owned Hermes source tree
        + Qubit broker / policy / audit / e-stop
        + OpenClaw capability integrations
        + Qubit VoiceBridge
        + Qubit CLI and Hermes-baseline dashboard
        = one Qubit Agent product
```

An external Hermes installation, upstream subprocess, status tile,
capability catalog, voice refusal, or compatibility route may be used for
migration evidence, but none may be counted as the final implementation.

## Authority order

When documents appear to disagree, use this order:

1. Explicit owner amendments and the frozen acceptance contract.
2. This execution directive.
3. `docs/QUBIT_GAP_LEDGER.md` and its machine-readable sections for current
   implementation status.
4. `docs/8TF_COMPLETE_MIGRATION_PLAN.md` for execution order.
5. `docs/QUBIT_UNIFIED_AGENT_PLAN.md`, `docs/QUBIT_NATIVE_COMPLETION_PLAN.md`,
   `docs/QUBIT_DASHBOARD_REBUILD_PLAN.md`, and the prompt pack.
6. Historical reports, old matrices, and agent handoffs.

The gap ledger is authoritative for status. Older narrow matrices are not
completion evidence when the ledger identifies open rows.

## Non-negotiable implementation rules

1. Vendor the complete pinned Hermes source baseline into `qubit_hermes/` and
   execute it from the Qubit installation. Keep upstream hashes, licenses,
   and provenance records; keep Qubit patches in separately auditable files or
   hunks.
2. Make the Qubit-owned Hermes backend the effective runtime and front door.
   `hermes_compat` is a migration boundary only and may not remain the final
   authority for operations already implemented by the fork.
3. Keep one listener, one backend authority, one session/memory/scheduler,
   one broker, one audit path, one emergency stop, and one resource policy.
   The existing Qubit WebUI listener may invoke the fork in-process; do not
   start a second server.
4. Route CLI, dashboard, and voice to the same backend operation. A route,
   tile, alias, refusal, or mock without a real caller and behavior test is
   open work.
5. Preserve the complete Hermes dashboard structure and workflows first.
   Add OpenClaw pages/workflows into that information architecture, then apply
   the Qubit visual system. Do not replace the Hermes baseline with a smaller
   custom shell.
6. Integrate OpenClaw capabilities from the latest pinned audit into Qubit
   contracts. Do not copy OpenClaw blindly, but do not omit a capability merely
   because it is difficult.
7. Every supported operation must have CLI, dashboard, and voice coverage
   where applicable, with equivalent arguments, results, errors, persistence,
   cancellation, recovery, confirmation, redaction, and audit behavior.
8. Unsafe upstream behavior receives a Qubit-safe equivalent. It may not be
   exposed as an unrestricted bypass.
9. `GATED`, `PARTIAL`, `UNVERIFIED`, `MISSING`, `DASHBOARD_ONLY`, and
   `REJECTED` remain open unless real implementation plus regression evidence
   closes the row. Only genuinely operator-only execution may remain manual.
10. Rebrand normal product surfaces as Qubit. Keep upstream names only in
    required legal, attribution, provenance, migration, compatibility, and
    developer records.

## Required work order

1. Reconcile the pinned Hermes source, OpenClaw source, Qubit source, and gap
   ledger. Do not rely on a narrow parity matrix.
2. Finish Hermes fork packaging and make the in-process fork the live backend
   authority. Complete cutover egress and policy gates; do not leave the
   default on a compat-only path.
3. Close every Hermes CLI/backend/dashboard/workflow row, including real
   state transitions and recovery, not only read routes.
4. Integrate every OpenClaw capability into the same backend and policy path.
5. Wire every operation to CLI, dashboard, and VoiceBridge. Teach voice the
   real command arguments and confirmation/cancellation behavior.
6. Complete the Qubit rebrand and Hermes-baseline dashboard enhancement only
   after functional coverage exists; preserve all original workflows.
7. Prove clean install, package contents, service startup, migration,
   upgrade, rollback, uninstall, and runtime identity.
8. Regenerate the ledger and run independent verification. Completion requires
   zero code-side open rows and a final green evidence set.

## Test and storage cadence

During a large implementation wave, do not run the full test suite, UI suite,
Playwright suite, or expensive upstream/live checks after every small edit.
Batch related implementation work first, then run the focused and affected
suites once at the end of that coherent batch. Run the complete release gates
only after the code is implementation-ready and before handoff. If a focused
check exposes a blocking integration error, repair it before continuing; do
not rerun unrelated expensive suites.

Use static inspection, targeted import/compile checks, and source/caller
audits during batching when they are sufficient. Never use the cadence rule to
skip a required final gate, hide a failure, weaken a test, or claim completion.

Clean disposable artifacts during the work: screenshots, audio captures,
pytest temp directories, logs, generated probe output, and throwaway virtual
environments. Preserve pinned upstream source clones until the final ledger
and independent verification finish, then remove them only if the evidence
does not require them. Never delete Qubit source, user state, credentials,
browser profiles, or provenance records to free space.

## Prohibited shortcuts

- Do not claim 1:1 from source presence, a vendor manifest, a matrix count, a
  status endpoint, or tests that never execute the production caller.
- Do not turn implementation failures into `GATED` or `MANUAL_ONLY` labels.
- Do not weaken, skip, delete, or rewrite acceptance tests to fit the current
  implementation.
- Do not change frozen requirements to resolve an implementation conflict.
- Do not use an external Hermes/OpenClaw installation as the normal runtime.
- Do not create duplicate listeners, dashboards, routers, schedulers, memory
  stores, brokers, voice listeners, or policy authorities.
- Do not copy secrets, credentials, cookies, browser profiles, or private data.

## Current status rule

The latest gap ledger is open, so the product is not 1:1 or complete. Agents
must report the actual ledger counts and exact remaining rows. `VERIFIED` is
allowed only after an independent verifier reproduces the final evidence and
the ledger has no code-side open rows.
