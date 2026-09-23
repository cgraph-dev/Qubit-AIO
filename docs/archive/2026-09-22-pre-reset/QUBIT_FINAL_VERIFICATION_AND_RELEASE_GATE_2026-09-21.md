# Qubit Final Verification and Release Gate — 2026-09-21

**Date:** 2026-09-21
**Status:** Authoritative Qubit release-gate contract. This document defines the
gates, verdicts, and evidence rules that govern a Qubit release decision. It is
NOT a status report and NOT a claim of completion. Current-state observations in
the final section are dated facts, not certification.

**Sibling contracts (same directory):**

- [QUBIT_ACTIVE_EXECUTION_BRIEF_2026-09-21.md](QUBIT_ACTIVE_EXECUTION_BRIEF_2026-09-21.md)
- [QUBIT_CAPABILITY_PARITY_CONTRACT_2026-09-21.md](QUBIT_CAPABILITY_PARITY_CONTRACT_2026-09-21.md)
- [QUBIT_VOICE_ACTIONABILITY_CONTRACT_2026-09-21.md](QUBIT_VOICE_ACTIONABILITY_CONTRACT_2026-09-21.md)
- [QUBIT_REBRAND_AND_PUBLIC_SURFACE_POLICY_2026-09-21.md](QUBIT_REBRAND_AND_PUBLIC_SURFACE_POLICY_2026-09-21.md)
- [QUBIT_UPSTREAM_SYNC_AND_ADAPTER_POLICY_2026-09-21.md](QUBIT_UPSTREAM_SYNC_AND_ADAPTER_POLICY_2026-09-21.md)
- [QUBIT_DOCUMENTATION_INDEX_2026-09-21.md](QUBIT_DOCUMENTATION_INDEX_2026-09-21.md)

---

## 1. Verdicts

Exactly three verdicts exist. No other verdict may be issued, and no synonym,
qualifier, or partial form is permitted.

### `NOT_READY`
A required gate is unexecuted, failed, or blocked by an unresolved code-side or
operator-side item. `NOT_READY` is the default verdict whenever the release
decision is in doubt. It is required when any required gate has not been run at
all, when any gate fails, when any required operator prerequisite is unnamed,
when any artifact or evidence is missing, or when the independent verifier has
not reproduced the evidence.

### `CODE_READY_OPERATOR_ACTIONS_REMAIN`
Every code-side gate passes and every code-side item is closed, but at least one
gate cannot complete without a named operator action (credentials, hardware,
host command, or operator-supplied artifact). No gate may have failed on its
merits. Every remaining item must be enumerated with the exact operator action
required and the gate it unblocks. If any code-side item is OPEN, this verdict is
forbidden.

### `VERIFIED`
All required gates have executed and passed, zero code-side OPEN items remain,
all artifacts are real (none fabricated), and a separate independent read-only
verifier has reproduced the evidence. `VERIFIED` is the only verdict that
constitutes a release approval.

---

## 2. `VERIFIED` is forbidden if any of the following hold

`VERIFIED` must not be issued, and any issuance is void, if:

1. ANY required gate is unexecuted.
2. ANY code-side OPEN item exists.
3. ANY artifact or evidence is fabricated, placeholder, or synthesized.
4. The independent read-only verifier has not reproduced the evidence.

Additional prohibitions:

- `VERIFIED` may never be inferred from partial runs, focused runs, or an
  implementation-first cadence run.
- `VERIFIED` may never be issued from a report that trusts another report
  instead of re-running the gates (see Section 6, current contradiction).
- The words "verified", "100%", "complete", or "1:1" must not be used as a
  claim in any gate result unless that exact gate's evidence supports it.

---

## 3. Release gates (ordered)

Each gate states what it proves, the command or artifact that produces evidence,
and its pass criteria. Gates are ordered; a later gate may not be summarized as
covering an earlier one. Every gate result must cite a raw log or artifact path
and the exact command used (Section 4).

### Gate 1 — Direct comparison against pinned AND latest upstream source
- **Proves:** Qubit's claimed behavior matches both the contractual pinned
  upstream trees and the latest available upstream source, and any divergence is
  a declared SAFE_EQUIVALENT or OPERATOR_ONLY disposition.
- **Command/artifact:** `scripts/differential_parity.py` and
  `scripts/tri_surface_parity.py` run against both the pinned trees and the
  latest upstream checkout; raw logs and comparison JSON under an evidence
  directory. Contract surface frozen in `docs/differential/INTERFACE.md`.
- **Pass criteria:** Every contract row has a disposition of IMPLEMENTED,
  SAFE_EQUIVALENT, or OPERATOR_ONLY. Zero rows OPEN. Any pinned-vs-latest delta
  is enumerated and dispositioned. Raw logs retained.

### Gate 2 — Exact source/path/provenance manifests
- **Proves:** Every upstream-derived file, adaptation, and vendor tree is
  recorded with exact upstream repository, commit, source path, license, and
  local changes.
- **Command/artifact:** Source inventory, vendor manifest, and provenance
  manifests regenerated and compared; results checked against
  `THIRD_PARTY_NOTICES.md`, `docs/archive/2026-09-21/LICENSING.md`, and
  `docs/archive/2026-09-21/UPSTREAM_REUSE.md`.
- **Pass criteria:** Every derived path has an exact commit plus source path and
  a license entry. No unrecorded copy. Divergence from a manifest is a failure.

### Gate 3 — Complete capability inventory
- **Proves:** The full capability surface is inventoried and every entry is
  dispositioned.
- **Command/artifact:** Capability inventory plus gap ledger artifacts, one per
  surface (Qubit, Hermes, OpenClaw), with counts and row-level dispositions.
- **Pass criteria:** Every capability row is IMPLEMENTED, SAFE_EQUIVALENT,
  OPERATOR_ONLY, or OPEN. Counts reconcile across the ledgers. Any OPEN row
  bars `VERIFIED`.

### Gate 4 — Full CLI coverage
- **Proves:** Every contractual CLI command and subcommand is registered and
  behaviorally covered.
- **Command/artifact:** CLI inventory diffed against the frozen interface; CLI
  test logs and coverage output.
- **Pass criteria:** Registered commands equal the contractual set; zero
  unaccounted commands; each command either passing or dispositioned with the
  exact reason.

### Gate 5 — Full dashboard/control coverage
- **Proves:** Every contractual dashboard control is present and wired.
- **Command/artifact:** Dashboard control inventory plus a live Playwright
  control audit (see Gate 17); raw run log.
- **Pass criteria:** Every control claimed IMPLEMENTED is exercised live and
  passes. Unwired controls are OPEN and bar `VERIFIED`.

### Gate 6 — Full voice coverage
- **Proves:** Every contractual voice path works across the supported
  STT/TTS/realtime matrix.
- **Command/artifact:** Voice provider/voice logs plus `scripts/tri_surface_parity.py`
  voice results; `docs/archive/2026-09-21/VOICE_BRIDGE_SYSTEM.md` as the
  pipeline reference.
- **Pass criteria:** Each supported voice configuration has a passing result or
  a named operator prerequisite. Untested configuration is OPEN.

### Gate 7 — `/chat` terminal verification
- **Proves:** The terminal chat surface works end-to-end against a real
  provider.
- **Command/artifact:** Real-provider E2E run log for `/chat`.
- **Pass criteria:** A live provider session produces a correct response with no
  fallback/mock. A mock-only run does not pass this gate.

### Gate 8 — `/qubit-chat` Brain4 voice verification
- **Proves:** The `/qubit-chat` voice surface works through the Brain4 voice
  path.
- **Command/artifact:** Live `/qubit-chat` voice run log.
- **Pass criteria:** Real speech-in/speech-out through Brain4 completes. Any
  escalation path is exercised and logged.

### Gate 9 — Local/API model switching
- **Proves:** The model backend can switch between local and API providers at
  runtime.
- **Command/artifact:** Switching E2E log covering each supported provider pair.
- **Pass criteria:** Each switch completes and a subsequent request is served by
  the newly selected backend, with no stale backend use.

### Gate 10 — STT/TTS/realtime switching
- **Proves:** Speech components can switch STT, TTS, and realtime modes at
  runtime.
- **Command/artifact:** Voice switching E2E log across the supported matrix.
- **Pass criteria:** Each supported switch completes and audio output matches
  the selected configuration.

### Gate 11 — Migration and rollback
- **Proves:** Migration from the prior state succeeds and rollback restores the
  prior state.
- **Command/artifact:** Migration rehearsal log and rollback rehearsal log.
- **Pass criteria:** Forward migration succeeds; rollback restores prior
  behavior; both are reproduced from a clean starting state.

### Gate 12 — Packaging and clean install
- **Proves:** The packaged artifact installs and runs in a clean environment.
- **Command/artifact:** Packaging log plus clean-install log from a fresh
  environment (e.g. a clean virtual environment / container).
- **Pass criteria:** Clean install completes; installed entry points start and
  serve. Building from a dirty tree without a clean-install run does not pass.

### Gate 13 — Security and secret scanning
- **Proves:** No secrets or credentials are present in source or artifacts.
- **Command/artifact:** Security scan log and secret-scan log over the release
  tree.
- **Pass criteria:** Zero real secrets. Every scan hit is proven to be a false
  positive with cited evidence.

### Gate 14 — Branding scan
- **Proves:** No unintended legacy or third-party branding remains on the public
  surface.
- **Command/artifact:** Branding scan log; allowlist per
  [QUBIT_REBRAND_AND_PUBLIC_SURFACE_POLICY_2026-09-21.md](QUBIT_REBRAND_AND_PUBLIC_SURFACE_POLICY_2026-09-21.md).
- **Pass criteria:** Every residual legacy string is on the allowlist (runtime
  strings, desktop app, and vendor trees included). Unallowlisted leakage is a
  failure.

### Gate 15 — Deterministic artifacts
- **Proves:** Generated artifacts are reproducible.
- **Command/artifact:** Two-run byte comparison of every generated artifact
  (for example the byte manifest and regenerated JSON manifests).
- **Pass criteria:** Two independent generation runs produce byte-identical
  output. Any byte difference is a failure.

### Gate 16 — Full tests
- **Proves:** The complete automated suite passes.
- **Command/artifact:** Full `pytest` run (config at `pytest.ini`) plus full
  frontend `vitest`; raw logs.
- **Pass criteria:** Zero failures and zero errors. Skips must be enumerated and
  each skip justified. A focused or partial run is not this gate.

### Gate 17 — Complete Playwright controls
- **Proves:** Every dashboard control is verified through the live UI.
- **Command/artifact:** Playwright run covering the full control set; raw run
  log and trace.
- **Pass criteria:** Every contractual control is exercised and passes. A
  partial control sweep is not this gate.

### Gate 18 — Independent read-only verifier
- **Proves:** The evidence survives reproduction by an independent party.
- **Command/artifact:** Verifier report produced by a separate read-only process
  (Section 5).
- **Pass criteria:** The verifier re-runs the gates and reproduces the same
  results. Failure to reproduce voids the release.

---

## 4. Evidence rules

1. **Cite raw evidence.** Every gate result must cite a raw log or artifact path
   AND the exact command that produced it. A summary without a raw path is not
   evidence.
2. **Deterministic generated artifacts.** Every generated artifact must be
   produced twice and byte-compared. Non-deterministic output fails Gate 15.
3. **Name operator prerequisites.** Every operator-supplied artifact,
   credential, hardware, or host command must be named exactly, with the gate it
   unblocks.
4. **No partial run as full run.** A focused run, an implementation-first run,
   or a subset sweep must never be summarized as a full gate run.
5. **No fabrication.** A placeholder, stub, or synthesized artifact is a
   failure, not a pass. Missing required artifacts fail closed.

---

## 5. Independent verifier requirement

The independent verifier is a separate process with read-only access (no edit
permission) that re-runs the proofs and reproduces the evidence. The verifier
must not be the same agent that wrote the code under verification, and it must
not modify application source, tests, runtime config, generated bundles,
upstream trees, or frozen contracts. If the verifier cannot reproduce a gate
result, that gate is unverified and the release is `NOT_READY`. The verifier
report is the artifact for Gate 18.

---

## 6. Current-state observations (dated 2026-09-20 / 2026-09-21)

These are observations, not certification. Every item below is subject to
re-running Gates 1–18.

### Latest available reports contradict each other
- `/tmp/qubit-final-release/FINAL_REPORT.md` (2026-09-20) — verdict
  `CODE_READY_OPERATOR_ACTIONS_REMAIN`.
- `/tmp/qubit-reconciliation-final/FINAL_RECONCILIATION.md` (2026-09-21) —
  verdict `NOT_READY`.
- The two reports contradict. The contradiction is unresolved. Resolution
  requires re-running the gates from a single evidence root; neither report may
  be trusted as-is.

### Test and build state
- `unit.log`: **2 failed, 5514 passed, 7 skipped**. The 2 failures are (a) the
  differential selftest (`byte_parity` manifest missing) and (b) the
  operator-only exit-code check.
  Disposition: **OPEN** (fails closed on the missing operator manifest).
- Frontend: `tsc` EXIT 0; `eslint` EXIT 0 (0 errors, 29 warnings); `vitest`
  358 passed (51 files); build EXIT 0.

### Differential / byte-parity
- Canonical byte manifest path
  `docs/differential/byte_manifest/hermes/HERMES_BYTE_MANIFEST.json` is
  **ABSENT**. Only a `README.md` placeholder exists at that path. Disposition:
  **OPERATOR_ONLY** — the operator must supply the pinned manifest (the runner
  also accepts it via `QUBIT_DIFFERENTIAL_BYTE_MANIFEST`). Until supplied, Gate
  15/byte-parity fails closed.

### Final matrix
- The complete final matrix was **NOT run**. Disposition: **OPEN**.
- Outstanding: live Playwright control audit (Gate 17), `/chat` real-provider
  E2E (Gate 7), Brain4 to API escalation (Gate 8/9), live differential parity
  (Gate 1), packaging/clean-install (Gate 12), and the independent verifier
  (Gate 18). Status of each: **NOT VERIFIED**.

### Existing verification tooling (present)
- `scripts/differential_parity.py`
- `scripts/tri_surface_parity.py`
- `docs/differential/INTERFACE.md` (frozen)
- `pytest.ini`
- frontend `vitest` and `playwright`

### Unknowns
- Whether latest upstream contains additional contractual methods beyond the
  pinned trees: **UNKNOWN / NOT VERIFIED**.
- Whether all dashboard controls are wired: **UNKNOWN / NOT VERIFIED**.
- Whether packaged clean install passes: **UNKNOWN / NOT VERIFIED**.
- Whether the independent verifier can reproduce any prior result: **UNKNOWN /
  NOT VERIFIED**.

---

## 7. Release decision procedure

1. Freeze implementation and declare the candidate.
2. Run Gates 1–17 from a single evidence root, retaining every raw log.
3. Have a separate read-only verifier run Gate 18 independently.
4. Apply Section 2 forbiddens, then choose exactly one verdict from Section 1.
5. If unresolved code-side or operator-side items exist, the verdict is
   `NOT_READY` or `CODE_READY_OPERATOR_ACTIONS_REMAIN` respectively; otherwise,
   and only with Gate 18 reproduction, the verdict is `VERIFIED`.
