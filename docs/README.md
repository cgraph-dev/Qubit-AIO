# Qubit documentation — fresh set (2026-09-22)

This is the **new, single docs authority**. It replaces the pre-reset set, which
was archived (not deleted) to `docs/archive/2026-09-22-pre-reset/`. Frozen
records and the differential interface were **not touched**:
`docs/archive/2026-09-21/` and `docs/differential/INTERFACE.md` remain in place.

Nothing here is `VERIFIED`, `M1`, `M2`, `CODE_READY`, or `COMPLETE`. This set is
a working truth + plan, meant to be reviewed and corrected repeatedly.

**`docs/archive/` is historical-only (Owner Decision #6, 2026-09-23): active
tests and generators must target `docs/generated/` (or `docs/differential/` for
the frozen contract), and must never read or write the archive.**

## The set

| Doc | Purpose |
|---|---|
| [STATUS.md](STATUS.md) | Current, evidence-backed truth. Update every session. |
| [FINDINGS_2026-09-22.md](FINDINGS_2026-09-22.md) | Audit: doc corrections + real misconfigurations/errors. |
| [PARITY.md](PARITY.md) | The parity target: the live reference dashboard, route/control/tool/voice parity. |
| [MIGRATION.md](MIGRATION.md) | Porting credentials/skills/cron/sessions from the read-only reference home. |
| [BRAND.md](BRAND.md) | Zero legacy brand on product surfaces; the legal-attribution exception. |
| [PLAN.md](PLAN.md) | Milestones M1–M4, critical path, owner gates. |
| [EXECUTION_PLAN.md](EXECUTION_PLAN.md) | The sequenced workstreams (W0–W9) that make everything work. |
| [CHECKLIST.md](CHECKLIST.md) | The master checklist: every capability + visual parity, tickable. |
| [DECISIONS.md](DECISIONS.md) | Owner rulings in force; pointers to the preserved contracts. |
| [ARCHITECTURE.md](ARCHITECTURE.md) | What Qubit is: one unified agent, layered. |
| This file | Index, review checklist, provenance. |

## Session sources (kept)

- `QUBIT_RESET_IMPLEMENTATION_STRATEGY_PROMPT_2026-09-22.md` — the reset strategy.
- `QUBIT_VENDOR_DELTA_2026-09-22.md` — `qubit_runtime/` vs the working upstream install.
- `QUBIT_OWNER_AMENDMENT_2026-09-22_STAGED_GATES_AND_RULINGS.md` — staged gates, the `OPERATOR_ONLY` split, the three rulings.
- `QUBIT_HERMES_RUNTIME_CATALOG_2026-09-22.json` — **UNVERIFIED** (not created by the docs session; review before trusting).
- `QUBIT_RUNTIME_CATALOG_RECONCILIATION_2026-09-22.json` — **UNVERIFIED**.
- `QUBIT_TOOLSET_RECONCILIATION_2026-09-22.json` — **UNVERIFIED**.

## Review checklist (run every time)

1. Does `STATUS.md` match a fresh run of the generators? (counts drift silently.)
2. Is every claim backed by a command + observed output, or explicitly marked unverified?
3. Does the boot baseline still fail? If so, what changed?
4. Are the 20 `OPERATOR_ONLY_ARCHITECTURE` rows still the right 20?
5. Do the branding scans and route-ownership tests still hold?
6. Is any milestone being claimed without behavioral evidence? (If yes, delete the claim.)
7. Does any doc reference a path that no longer exists?
8. **Parity:** is every route/control/tool in `PARITY.md` `PORTED` with evidence,
   and does every capability have a voice path? (GATED = not done.)
9. **Brand:** does the branding scan show zero unallowlisted legacy tokens on
   product surfaces, with legal attribution intact? (`BRAND.md`.)

## Provenance

- Pre-reset docs: `docs/archive/2026-09-22-pre-reset/` (31 files).
- Earlier frozen records: `docs/archive/2026-09-21/` (untouched).
- The staged change bundle from this session lives outside the repo at
  `/tmp/opencode/qubit-staged-gates-2026-09-22/` and is **not applied**. Its
  seven doc patches now target archived files and are superseded by this set;
  its five generator patches remain valid and are still unapplied.
