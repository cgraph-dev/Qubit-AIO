# Qubit Documentation Index — 2026-09-21

**Status:** Authoritative navigation index for the 2026-09-21 Qubit
documentation set. Read this after
[QUBIT_ACTIVE_EXECUTION_BRIEF_2026-09-21.md](QUBIT_ACTIVE_EXECUTION_BRIEF_2026-09-21.md).
This index does not report status; it points to where status lives.

## 1. The active brief (read first)

- [docs/QUBIT_ACTIVE_EXECUTION_BRIEF_2026-09-21.md](QUBIT_ACTIVE_EXECUTION_BRIEF_2026-09-21.md)
  — the primary document every future agent reads first. Contains the current
  architecture, behavior of each surface, parity status, blockers, canonical
  commands, and the current-truth table.

## 2. Authoritative contracts (2026-09-21 set)

All in `docs/`:

- [QUBIT_CAPABILITY_PARITY_CONTRACT_2026-09-21.md](QUBIT_CAPABILITY_PARITY_CONTRACT_2026-09-21.md)
  — behavioral parity definition, dispositions, four comparison targets,
  correspondence evidence.
- [QUBIT_VOICE_ACTIONABILITY_CONTRACT_2026-09-21.md](QUBIT_VOICE_ACTIONABILITY_CONTRACT_2026-09-21.md)
  — every capability must be voice-actionable through the canonical operations.
- [QUBIT_REBRAND_AND_PUBLIC_SURFACE_POLICY_2026-09-21.md](QUBIT_REBRAND_AND_PUBLIC_SURFACE_POLICY_2026-09-21.md)
  — deep-rebrand and public-surface rules, allowlist, branding scan.
- [QUBIT_UPSTREAM_SYNC_AND_ADAPTER_POLICY_2026-09-21.md](QUBIT_UPSTREAM_SYNC_AND_ADAPTER_POLICY_2026-09-21.md)
  — how future Hermes/OpenClaw updates are fetched, adapted, and admitted.
- [QUBIT_FINAL_VERIFICATION_AND_RELEASE_GATE_2026-09-21.md](QUBIT_FINAL_VERIFICATION_AND_RELEASE_GATE_2026-09-21.md)
  — the final gates, the three verdicts, and the evidence rules.
- [QUBIT_DOCUMENTATION_INDEX_2026-09-21.md](QUBIT_DOCUMENTATION_INDEX_2026-09-21.md)
  — this file.

## 3. Frozen upstream contracts (authoritative by content)

Physically preserved under `docs/archive/2026-09-21/` (see its `README.md`):

- `QUBIT_FROZEN_ACCEPTANCE_CONTRACT.md`
- `QUBIT_FULL_PARITY_CONTRACT.md`
- `QUBIT_EXECUTION_DIRECTIVE_2026-09-12.md`
- `QUBIT_NATIVE_COMPLETION_CHECKLIST.md`
- `8TF_SOURCE_OF_TRUTH.md`
- `QUBIT_LATEST_UPSTREAM_1TO1_EXECUTION_PLAN_2026-09-16.md`
- `QUBIT_OWNER_AMENDMENT_2026-09-11.md`
- `QUBIT_OWNER_AMENDMENT_2026-09-11_HERMES_RUNTIME.md`
- `QUBIT_OWNER_AMENDMENT_2026-09-13_TERMINAL_STATUSES.md`
- `QUBIT_OWNER_AMENDMENT_2026-09-15_PROVIDER_AND_VOICE_PLUGGABILITY.md`
- `QUBIT_OWNER_AMENDMENT_2026-09-18_IMPLEMENTATION_FIRST_VERIFICATION.md`
- `QUBIT_OWNER_AMENDMENT_2026-09-18_OWNER_DECISIONS_D1_D2_D3.md`
- `QUBIT_OWNER_AMENDMENT_2026-09-18_SOURCE_REBRAND_PROVENANCE.md`
- `QUBIT_OWNER_AMENDMENT_2026-09-19_DEEP_REBRAND.md`
- `QUBIT_OWNER_POLICY_REFUSED_DIALECT_2026-09-17.md`
- `QUBIT_PTY_POLICY_EXCEPTION_2026-09-17.md`
- `QUBIT_REBRAND_ALLOWLIST.md`
- `QUBIT_SAFE_EQUIVALENTS.md`

The **frozen differential interface** remains at
[docs/differential/INTERFACE.md](differential/INTERFACE.md) (with
[docs/differential/README.md](differential/README.md)).

## 4. Active ledgers (status authority)

Completion authority is the current gap ledger. The live copies are preserved
under `docs/archive/2026-09-21/` (moved from `docs/` during the 2026-09-21
reorganization):

- `QUBIT_GAP_LEDGER.md` — merged authoritative view.
- `QUBIT_GAP_LEDGER.hermes.json` — 573 rows.
- `QUBIT_GAP_LEDGER.openclaw.json` — 104 rows.
- `QUBIT_GAP_LEDGER.qubit.json` — 746 rows.
- `QUBIT_OPEN_GAP_LEDGER_2026-09-17.md` — earlier open-gap snapshot; historical
  status, superseded by the live ledger for counts.

Read counts from these files, not from this index. Note the ledger's embedded
`generated` date (`2026-09-12`) is stale metadata versus the 2026-09-21 content.

## 5. Manifests and inventories

Preserved under `docs/archive/2026-09-21/`:

- `QUBIT_SOURCE_INVENTORY.json` — pinned upstream source inventory.
- `QUBIT_SOURCE_OMISSIONS.md` — recorded omissions.
- `QUBIT_HERMES_VENDOR_MANIFEST.json` — vendor pristine/patch manifest.
- `QUBIT_REBRAND_MANIFEST.json` — rename/patch manifest (1646 files changed).
- `QUBIT_DEBRAND_MANIFEST.json` — debrand classification and preserved tokens.
- `QUBIT_DEBRAND_CLASSIFICATION.json` — occurrence classification.
- `QUBIT_IDENTIFIER_RENAME.json` — identifier and file renames.
- `QUBIT_OPENCLAW_UPGRADE_MANIFEST.json` — OpenClaw pins and
  `pinned_surface_disposition` (434/434 disposed: 252 IMPLEMENTED, 87
  SAFE_EQUIVALENT, 95 OPERATOR_ONLY, 0 OPEN).
- `QUBIT_OPENCLAW_CONFIG_SURFACE.json`, `QUBIT_OPENCLAW_ENV_NAMES.json`.
- `QUBIT_CONFIG_BINDING_MANIFEST.json` — config keys / env vars.
- `QUBIT_ROUTE_OWNERSHIP.json` — route ownership classification.
- `QUBIT_COMPAT_HANDLER_REACHABILITY.json` — handler reachability.
- `QUBIT_WS_DIALECT_DISPOSITION.json` — refused upstream WS dialects.
- `HERMES_FRONTEND_MAP.json`, `HERMES_SOURCE_COMPARISON.json` (and `.wave3.diff`,
  `.wave4.diff`), `HERMES_SURFACE.json`, `PARITY_HERMES.json`.
- `INVENTORY_HERMES.json`, `INVENTORY_OPENCLAW.json`.

Regenerate via the deterministic generators listed in the active brief (§14).
Do not hand-edit manifests.

## 5A. Analysis, coverage, and repair artifacts (2026-09-21)

Not status authorities by themselves; cite them as named evidence. Read the
artifacts, not this index.

- `QUBIT_HERMES_LATEST_DELTA_2026-09-21.{md,json}` — the latest-Hermes
  comparison (118 capabilities: 1 IMPLEMENTED, 104 PARTIAL, 13 MISSING in the
  delta vocabulary). A documented delta, **not** behavioral parity or VERIFIED.
- `QUBIT_AUTHORITY_MATRIX_2026-09-21.md` — read-only canonical-authority audit.
  Snapshot recorded 7 reachable GAPs (+1 latent, +1 root cause); later
  same-day repairs are recorded in its post-audit addendum.
- `QUBIT_PAGE_COVERAGE_2026-09-21.json` / `.md` — page/control coverage
  (3355 rows: 3199 OPEN, 156 SAFE_EQUIVALENT, 0 IMPLEMENTED; no E2E run).
- `QUBIT_OPENCLAW_METHOD_INVENTORY.json` — regenerated OpenClaw method
  inventory consumed by the coverage generator.
- `QUBIT_CHAT_AND_TERMINAL_MODEL_ROUTING_POLICY_2026-09-21.md` — the `/chat`
  vs `/qubit-chat` model-routing policy.
- `QUBIT_POST_PARITY_PIZZABOT_OPENVIKING_INTEGRATION_PLAN_2026-09-21.md` —
  post-parity integration plan; not a parity claim.
- `QUBIT_OWNER_RULING_REQUEST_2026-09-21.md` — the open owner decisions
  (branding allowlist vs policy; 95 OPERATOR_ONLY rows; `/` redirect target).
- Generators (deterministic, in-repo): `scripts/page_coverage.py`
  (`--check` clean at `f984911`) and `scripts/identifier_rename.py`
  (`--check` clean, 33 file renames; guard
  `tests/test_identifier_rename_generator9u.py`).

## 6. Verification reports

- `/tmp/qubit-final-release/FINAL_REPORT.md` (2026-09-20) —
  CODE_READY_OPERATOR_ACTIONS_REMAIN. External, not in-repo.
- `/tmp/qubit-reconciliation-final/FINAL_RECONCILIATION.md` (2026-09-21) —
  NOT_READY. External, not in-repo.
- In-repo evidence (historical unless regenerated): `QUBIT_BYTE_PARITY_EVIDENCE.md`,
  `QUBIT_COMPLETION_EVIDENCE.md`, `QUBIT_WAVE2_EVIDENCE.md`,
  `QUBIT_LEDGER_EVIDENCE_WAVE3.md` (all under `docs/archive/2026-09-21/`).
- The differential report directory `docs/differential/reports/` currently holds
  only a `README.md`; there is no current full differential report. The byte
  manifest `docs/differential/byte_manifest/hermes/HERMES_BYTE_MANIFEST.json` is
  ABSENT (operator prerequisite), so the byte-parity gate fails closed.

The two latest external reports contradict each other. Per the release-gate
contract, resolve by re-running the gates; do not average or cherry-pick them.

## 7. Archive directory

- `docs/archive/2026-09-21/` — historical evidence only, plus preserved frozen
  contracts and legal/provenance records classified in its `README.md`.
- `docs/archive/2026-09-21/CHANGE_REPORTS/` — historical packet change reports.

## 8. Historical documents — do NOT treat as current truth

Everything in `docs/archive/2026-09-21/` other than the frozen contracts and
legal/provenance records listed in its `README.md` is historical. In particular,
do not use these as current status:

- Wave-specific reports (`BRANDING_AUDIT_WAVE3.md`, `DASHBOARD_AUDIT_WAVE4.md`,
  `SECURITY_AUDIT_WAVE3/4.md`, `CLEAN_CLONE_AUDIT_WAVE5.md`, `*_WAVE*`,
  `OPERATIONS_*`, `REPORTS_8C.md`, `PACKET_*`, `FOUNDATION_8T_F.md`,
  `MATRIX_8T_F.*`, `FEATURE_MATRIX_8U.*`, `NATIVE_EQUIVALENTS_8U.*`).
- Earlier plans (`QUBIT_UNIFIED_AGENT_PLAN.md`, `UNIFIED_AGENT_PLAN.md`,
  `QUBIT_NATIVE_COMPLETION_PLAN.md`, `QUBIT_DASHBOARD_REBUILD_PLAN.md`,
  `QUBIT_PRODUCT_PARITY_AND_AUTONOMY_PLAN_2026-09-15.md`).
- Earlier evidence and gap snapshots (`QUBIT_OPEN_GAP_LEDGER_2026-09-17.md`,
  `QUBIT_BYTE_PARITY_EVIDENCE.md`, `QUBIT_COMPLETION_EVIDENCE.md`).
- Prior agent completion claims anywhere in the archive. Do not trust them
  without direct verification against the current worktree, ledgers, and
  manifests.

`AGENTS.md` at the repo root and the per-package `AGENTS.md` files remain
binding for engineering rules; this index governs documentation authority.
