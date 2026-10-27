# Qubit docs — the index

**Status:** index only. This file makes no status or completion claim; the evidence lives in the
documents it points at.

This is the entry point for `docs/`. Read it before any Qubit task, then follow the pointers that
match the work. It lists **every top-level** markdown file in `docs/`, generated from the tree —
if a doc is missing from this table it does not exist, and if a path in `AGENTS.md` is missing
from the tree, the pointer is broken (fix the pointer; do not re-create the doc from memory).
Subdirectories are listed at the end.

## Read these first

| Doc | Why |
|---|---|
| `IDENTITY.md` | what Qubit *is* (canonical; identity, not lineage) |
| `DECISIONS.md` | owner rulings still in force, each with its source |
| `STATUS.md` | current evidence-backed truth — update it every session |
| `ARCHITECTURE.md` | what layers exist and where they live |
| `BRAND.md` | zero-legacy-brand rule, its exceptions, the rename recipe |
| `FROZEN_TARGET.md` | the pinned finish line and the literal-port rule |
| `FROZEN_TARGET_WORKLIST.md` | production symbols still missing at that pin |
| `PLAN.md` / `EXECUTION_PLAN.md` / `CHECKLIST.md` | where the plan, sequencing and tick-list actually live |
| `PARITY.md` / `MIGRATION.md` | parity axes and their instruments; the three kinds of migration |
| `OPEN_THREADS.md` | durable "what was in flight" ledger |

## Review checklist (for any change to these docs)

1. **Every count has a command next to it.** If you cannot paste the command, remove the number.
2. **No invented milestone, date, or completion claim.** `VERIFIED` is the sole release verdict;
   `M1`/`M2` are progress markers only.
3. **A path cited must exist.** Verify before committing (see the check at the bottom).
4. **Do not restore purged docs from memory.** In 2026-09-21/22 material was retired on purpose;
   recovered text without a source is fabricated authority.
5. **Say what you did not verify.** An explicit "not verified" beats a silent assumption.

## Doc set history (read this before "fixing" a missing doc)

`eafb54e26` (2026-10-09) deleted the retired planning set under the owner's English-only ruling:
`docs/README.md`, `PARITY.md`, `MIGRATION.md`, `PLAN.md`, `EXECUTION_PLAN.md`, `CHECKLIST.md`,
`DECISIONS.md`, `ARCHITECTURE.md`, the owner-amendment files, and the 2026-09-21 archive markdown.
`IDENTITY.md` was kept deliberately; `FROZEN_TARGET.md` and `BRAND.md` were re-authored later.

The current files of those names are **re-authored from the worktree**, not restored: each one
states where the truth now lives and cites commands. Their pre-purge text is still recoverable for
reference (`git show eafb54e26^:docs/<name>.md`) and is stale by construction — do not present it
as current.

```bash
git show --no-patch --format='%h %ad%n%s%n%b' eafb54e26
git show --diff-filter=D --name-only --format='' eafb54e26 | grep -c '^docs/'   # 340
```

## Every markdown doc in `docs/`

| File | Title |
|---|---|
| `ARCHITECTURE.md` | Qubit ARCHITECTURE — the layers that exist, and where each lives |
| `ASSET_VERIFICATION.md` | Asset verification — skills, tools, MCPs (100% on the assets themselves) |
| `BRAND.md` | Qubit BRAND — the zero-legacy-brand rule, its exceptions, and how to rename |
| `BROWSER-PARITY-FINDINGS.md` | Habblaud-in-Qubit-Office — Browser Parity Findings |
| `CHECKLIST.md` | Qubit CHECKLIST — the tick-list that is machine-measured |
| `DECISIONS.md` | Qubit DECISIONS — owner rulings in force |
| `EXECUTION_PLAN.md` | Qubit EXECUTION_PLAN — how work is sequenced and dispatched |
| `FIREFOX-PARITY-FINDINGS.md` | Firefox parity — the Habblaud office page in Firefox |
| `FLAKY-TEST-FINDINGS.md` | Flaky-test findings — `test_office_vendor_habblaud.py` (OPEN_THREADS §4.15) |
| `FROZEN_TARGET.md` | FROZEN TARGET: upstream `43faacfb8de7` |
| `FROZEN_TARGET_WORKLIST.md` | FROZEN-TARGET WORKLIST — production symbols still missing |
| `HANDOFF_PROMPT.md` | Operational handoff prompt — current state and next work (not authority) |
| `HERMES_RESIDUE_INVENTORY.md` | Hermes-residue inventory (deliberate compat vs accidental half-rebrand) |
| `IDENTITY.md` | Qubit IDENTITY — what Qubit is |
| `LEFTOVER_UNVERIFIED_VERDICTS_2026-10-19.md` | Leftover unverified items — verdicts with literal evidence |
| `MIGRATION.md` | Qubit MIGRATION — the three different things called "migration" |
| `OFFICE-ACCEPTANCE-AND-RISK.md` | Office port — risk review and acceptance checklist |
| `OFFICE-ACCEPTANCE-CLOSEOUT.md` | Office acceptance closeout — A1-A20 and the risk findings, re-run against current HEAD |
| `OFFICE-ACCEPTANCE-VERIFIED-2026-10-16.md` | Office acceptance — VERIFIED checklist against live `:8890` |
| `OFFICE-ANIMATION-REACHABILITY.md` | Office animation reachability — why a full ledger can render an empty, still scene |
| `OFFICE-CLI-COMPLETENESS.md` | Office CLI — completeness and wiring contract |
| `OFFICE-DEPLOYMENT-CONTRACT.md` | The Qubit Office page — deployment contract |
| `OFFICE-DOC-RECONCILIATION.md` | OFFICE DOC RECONCILIATION — the Office documentation vs the current code tree |
| `OFFICE-GROWTH-REPORT.md` | Office growth report — can this grow into an agent-run business? |
| `OFFICE-INVARIANT-AUDIT.md` | Office engine — adversarial cross-module invariant audit |
| `OFFICE-INVARIANT-FIX-VERIFICATION.md` | Office invariant fixes — independent verification |
| `OFFICE-LIVE-STATE-VERIFICATION-SESSION2.md` | Office live-state verification (this session) |
| `OFFICE-LIVE-VERIFICATION.md` | Habblaud-in-Qubit-Office: live verification (this session) |
| `OFFICE-SURFACE-SECURITY-AUDIT.md` | Office REST/SSE Surface — Security + Correctness Audit |
| `OFFICE23_SPEC.md` | office2 / office3 integration spec — verified facts |
| `OFFICE_UNBLOCK_2026-10-17_1812.md` | Office unblock — 2026-10-17 18:12 tick (lead-quality) |
| `OMNIROUTE_CHAT_FALLBACK.md` | OmniRoute /chat fallback — diagnosis, fix, verification, rollback |
| `OPEN_THREADS.md` | OPEN_THREADS.md — durable handoff ledger |
| `OPENJARVIS-8-AGENT-ABSORPTION.md` | OpenJarvis 8-agent absorption — Qubit-native roles, mapping and audit |
| `ORB_FX_SPEC.md` | Orb FX Spec — Qubit-mark animation contract |
| `PARITY.md` | Qubit PARITY — the target, the instruments, the gaps |
| `PARITY_VERIFICATION_2026-10-09.md` | Parity Verification — post z-wave (2026-10-09) |
| `PHASE8_INVENTORY.md` | Phase 8 measured inventory — HERMES_*/.hermes/plugin-SDK surface and owner decision table |
| `PHASE8-SIGNOFF-PACK.md` | P8 owner sign-off pack — measured drift and the decision table |
| `MATRIX-BASELINE-2026-10-27.md` | Matrix baseline — pre-merge chunk runs, failures and per-file reruns |
| `PHASE8-D21-AUDIT.md` | P8 D21 — write-only env names re-derived and kept |
| `REBRAND-EVIDENCE-2026-10-27.md` | D19 evidence framing rebrand — raw captures preserved |
| `REBRAND-PROSE-2026-10-27.md` | D17/D18 prose cleanup — comments/docstrings only |
| `REBRAND-WEBSITE-2026-10-27.md` | D16 vendored website rebrand — counts and exceptions |
| `PLAN.md` | Qubit PLAN — where the real plan lives, and the rules for claiming progress |
| `PORT-PLAN.md` | PORT PLAN — closing the upstream gap in finishable chunks |
| `POST_RESTART_VERIFICATION_2026-10-10.md` | Post-Restart Verification + Health Baseline — 2026-10-10 |
| `REBRAND-FILENAMES-PLAN.md` | Filename rebrand — scope and ordering (measured 2026-10-17) |
| `REBRAND-SWEEP-PLAN.md` | Rebrand Sweep Plan — env/attr symbols HERMES_* → QUBIT_* |
| `REBRAND-SWEEP-STATUS.md` | Rebrand sweep status — CORRECTED 2026-10-17 |
| `REBRAND_AUDIT.md` | REBRAND AUDIT — Qubit Agent (assets) |
| `REBRAND_INVENTORY.md` | REBRAND INVENTORY — authoritative work list for the final rebrand pass |
| `REBRAND_MODULE_MIGRATION_PLAN.md` | REBRAND — MODULE-TREE MIGRATION PLAN |
| `RETRY_STORM_ROOT_CAUSE_2026-10-17.md` | Retry storm in the inference path — root cause |
| `SESSION_FIX_VERIFICATION_2026-10-09.md` | Session-Fix Verification + Regression Sweep (2026-10-09) |
| `SESSION_ROUTE_POLICY.md` | Conversation-only fallback opt-out |
| `SHELL-TEST-RECONCILIATION.md` | SHELL-TEST-RECONCILIATION |
| `SIDEBAR_UX_AUDIT_2026-10-11.md` | Web dashboard sidebar and page information architecture audit |
| `SKILL_PROSE_HERMES_INVENTORY.md` | Bundled-skill prose: stale `~/.hermes` / upstream-name inventory |
| `STARNET_VOICE_STUDY.md` | Starnet voice study → Qubit reuse (2026-10-17, branch fix/starnet-voice-study) |
| `STATUS.md` | Qubit Office — session status (2026-10-15) |
| `SYNC_REMAINING.md` | SYNC_REMAINING — upstream delta `69948c0057..bd0affe5`, measured state |
| `TEST-HONESTY-AUDIT.md` | Test Honesty Audit — a repo-wide hunt for tests that cannot fail |
| `TEST_PORT_RECOVERY_2026-10-19.md` | Test-port recovery: lane B + lane A — honest account |
| `VENDOR-ATOMIC-PUBLISH-FIX.md` | Vendor publish-order fix (scripts/vendor_habblaud.py) |
| `VENDOR-CONCURRENCY-EVIDENCE.md` | Vendor publish concurrency — measured evidence |
| `VENDOR-REPRODUCIBILITY.md` | Vendor reproducibility — can the vendored tree be re-produced? (A9) |
| `VOICE-BRIDGE-AUDIT.md` | Voice Bridge Audit — interrupt ordering, worker crash-resilience, routing lanes |
## Subdirectories

- `docs/archive/`
- `docs/e2e/`
- `docs/evidence/`
- `docs/game-office/`
- `docs/superpowers/`
- `docs/sync-w24/`
- `docs/archive/2026-09-21/` holds **frozen `*.json` data records only** — the archive markdown
  was retired in `eafb54e26`. Active tests and generators must never read or write `docs/archive/`.
- `docs/differential/` does **not** exist in this tree (`INTERFACE.md` was purged). Do not cite it.

## Verify every path this file and `AGENTS.md` cite

```bash
for f in docs/IDENTITY.md docs/DECISIONS.md docs/STATUS.md docs/ARCHITECTURE.md \
         docs/BRAND.md docs/FROZEN_TARGET.md docs/FROZEN_TARGET_WORKLIST.md \
         docs/PLAN.md docs/EXECUTION_PLAN.md docs/CHECKLIST.md docs/PARITY.md \
         docs/MIGRATION.md docs/OPEN_THREADS.md docs/REBRAND_INVENTORY.md \
         docs/PARITY_VERIFICATION_2026-10-09.md; do
  printf '%-45s %s\n' "$f" "$(test -f "$f" && echo present || echo MISSING)"
done
```
