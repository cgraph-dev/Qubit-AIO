# Qubit owner amendment: implementation-first verification cadence

Date: 2026-09-18

Status: additive workflow amendment. This document changes execution cadence,
not the product acceptance criteria, safety policy, terminal statuses, or
completion requirements in the frozen contracts.

## Purpose

Large Qubit implementation waves must not repeatedly spend 30–40 minutes on
full-suite or broad UI verification while code-side requirements are still
open. Verification remains mandatory; its timing is moved to the end of the
implementation wave so the evidence describes one coherent tree.

## Implementation phase

Until the integration lead declares `IMPLEMENTATION_COMPLETE`, agents must:

- implement and inventory code-side requirements first;
- use Graft, upstream source comparison, AST/reachability analysis, manifests,
  and written handoffs for discovery and coordination;
- preserve existing user changes and frozen contracts;
- maintain an implementation ledger with exact files, capabilities, and open
  code-side items;
- avoid broad verification commands.

During this phase agents must not run:

- the full Python test suite or batch equivalents;
- full Vitest, Playwright, or dashboard-control sweeps;
- the differential parity runner over the complete inventory;
- packaging, clean-install, release, migration, or independent-verifier waves.

Minimal syntax, type, or focused checks are permitted only when needed to
prevent obvious damage to the specific files being edited. They are not a
completion signal and must not become a repeated test/fix loop.

## Final verification phase

After all code-side implementation and inventory work is complete, the lead
declares `IMPLEMENTATION_COMPLETE` and runs the final verification matrix:

1. upstream byte and Qubit patch/provenance manifests;
2. complete capability, route, CLI, voice, and dashboard inventories;
3. every page and interactive control through the real application;
4. CLI/dashboard/voice/provider/model differential checks;
5. full Python and frontend suites;
6. packaging, clean install, migration, restart, rollback, and recovery;
7. dead-code, stale-config, duplicate-authority, secret, and branding audits;
8. independent read-only verification.

If final verification exposes a failure, agents may run only the affected
checks while repairing that failure. They must not restart broad verification
loops during repair. Once repairs are complete, the lead reruns the final
verification matrix and reports the exact result.

## Commit and push

No commit or push is allowed before the independent verifier passes. A failed
or interrupted verification is not a passing result. The lead stages only
intended paths, preserves unrelated dirty work, and records the commit,
remote, evidence, and rollback after the final gate succeeds.

## Exceptions

An exception to this cadence requires an explicit owner decision and a written
reason in the handoff. Safety-critical focused checks are always permitted;
this amendment does not authorize unsafe implementation or removal of a
required safety gate.
