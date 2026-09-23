# Owner Amendment 2026-09-13 — Terminal statuses for code readiness

Status: **APPROVED by owner 2026-09-13. Append-only. This amendment records an
owner terminal-status decision. It does not alter, weaken, reorder, or
reinterpret any requirement of `docs/QUBIT_FROZEN_ACCEPTANCE_CONTRACT.md`.**

This is the terminal-status companion to
`docs/QUBIT_OWNER_AMENDMENT_2026-09-11_HERMES_RUNTIME.md`. The runtime amendment
changed the runtime architecture; this amendment records how code readiness is
declared while genuine live prerequisites remain outstanding. Every other
frozen requirement (backend + CLI + dashboard + voice for every capability,
safety gating, licensing/provenance, “no 100% from a tile”) remains in force.

## Owner decision (verbatim)

> The code path is complete and tested; execution requires live credentials,
> hardware, accounts, OAuth, CAPTCHA/2FA, or another genuine operator
> prerequisite; the exact manual procedure and typed unavailable state are
> documented.

The owner recognises two terminal statuses for code readiness and states their
criteria as follows (verbatim):

> **SAFE_EQUIVALENT** = a real Qubit backend path exists and the unsafe raw
> upstream behavior is explicitly refused under full
> policy/audit/redaction/confirmation/e-stop/resource limits, with
> positive/negative/regression tests.
>
> **OPERATOR_ONLY** = the code path is complete and tested but execution
> requires a genuine live prerequisite (credentials, hardware, accounts,
> OAuth, CAPTCHA/2FA) with a documented manual procedure and typed unavailable
> state.
>
> Code readiness closes both terminal statuses. OPERATOR_ONLY rows stay visible
> in the ledger and are not deleted or reclassified to IMPLEMENTED. The product
> is not 100% complete until every OPERATOR_ONLY row has been manually
> qualified and independently verified. Do not weaken safety.

## Recorded meaning

1. **Code readiness.** The code-side completion gate now passes when every row
   is `IMPLEMENTED`, `SAFE_EQUIVALENT`, or `OPERATOR_ONLY`. No row may remain
   `PARTIAL`, `GATED`, `MISSING`, `UNVERIFIED`, `CLI_ONLY`, `DASHBOARD_ONLY`, or
   `VOICE_ONLY` at the code-ready gate.
2. **SAFE_EQUIVALENT is a refusal, not a bypass.** The unsafe raw upstream
   operation is explicitly refused with a typed code under the Qubit
   permission, confirmation, audit, redaction, resource, and emergency-stop
   contracts. Positive, negative, and regression tests prove the refusal.
3. **OPERATOR_ONLY is a live-prerequisite gate, not missing code.** The code
   path is complete and hermetically tested through an injected transport,
   actuator, or probe seam, and it returns a typed unavailable/refusal result
   when the live dependency is absent. Execution needs a genuine operator
   prerequisite: live credentials, hardware, an account, OAuth, CAPTCHA/2FA,
   an operating-system bridge, or another live dependency that cannot be
   synthesized hermetically.
4. **OPERATOR_ONLY stays visible.** The rows remain in the gap ledgers and in
   `docs/QUBIT_GAP_LEDGER.md`, listed under **Terminal gate rows
   (SAFE_EQUIVALENT / OPERATOR_ONLY)**. They are never deleted, hidden, or
   flipped to `IMPLEMENTED` to make the totals look finished.
5. **The product is not 100% complete.** Code readiness is not product
   completion. Until every `OPERATOR_ONLY` row has been manually qualified with
   the runbook procedure in `docs/OPERATOR_QUALIFICATION.md` and the evidence
   has been independently verified, no agent may claim `100%`, `complete`,
   `VERIFIED`, or `1:1`.
6. **Safety is not weakened.** This amendment changes only the bookkeeping of
   completion. It does not permit an unrestricted bypass, a fabricated success,
   a disabled safety control, or a weakened acceptance test. The frozen
   acceptance contract’s prohibition on turning code failures into manual-only
   limitations is unchanged.

## Relationship to the frozen acceptance contract

- The frozen contract’s final gate already permits a residual operator set:
  “Only physical hardware, live accounts, CAPTCHA/2FA, release signing, and
  clean-machine qualification may remain operator-only after code completion”
  (`docs/QUBIT_FROZEN_ACCEPTANCE_CONTRACT.md:84`).
- This amendment records the exact status names and criteria for that residual
  set. It does not add an operator-only category, and it does not move any
  code-side row into it. A row is `OPERATOR_ONLY` only when its code path is
  complete, typed, and hermetically tested.
- Full-parity rule unchanged (`docs/QUBIT_FULL_PARITY_CONTRACT.md:144`):
  genuine operator-only tests may remain only when the code path is complete,
  typed, tested hermetically, and explicitly listed for manual qualification.
- Requirement changes require explicit owner approval and a separate dated
  amendment; this is that separate dated owner amendment.

## Implementation record

- Ledger schema (`scripts/gap_ledger.py`):
  - `TERMINAL_STATUSES = ("IMPLEMENTED", "SAFE_EQUIVALENT", "OPERATOR_ONLY")`
  - `OPERATOR_STATUSES = ("OPERATOR_ONLY",)`
  - `OPEN_ROWS = status not in TERMINAL_STATUSES`
  - `code_ready = not open_rows`
  - `complete = code_ready and not operator_rows`
  - `needs` remains constrained to `("none", "creds", "hardware", "owner")`.
- Regenerators classify gate rows per the owner criteria:
  `scripts/rebuild_ledger_qubit.py`, `scripts/rebuild_ledger_openclaw.py`, and
  `scripts/rebuild_ledger_hermes.py`.
- Evidence test: `tests/test_operator_only_evidence.py`.
- Manual qualification runbook: `docs/OPERATOR_QUALIFICATION.md`.

## Independent verification

`VERIFIED` is allowed only after an independent verifier reproduces the code
evidence (the proving tests on disk, the typed unavailable codes, the ledger
counts) **and** after the manual qualification evidence for every
`OPERATOR_ONLY` row is independently reproduced. Code readiness alone is
reported as `CODE-READY`, never as `VERIFIED` or complete.
