# Wave 5 — OpenClaw pin reconciliation (v2026.9.2 vs v2026.9.4)

Status: **RECONCILED — documentation only; the frozen amendment and both
OpenClaw audits are not edited to change their pins.**
Owner: Agent 4 (transport redaction, privacy, pin reconciliation, MCP
classification).
Baseline: commit `b79089d5`.

This note explains why two OpenClaw revisions appear in the repository and
which artifact is authoritative for which question. It does not change a
requirement: the owner amendment is append-only and the audit findings stand.

## 1. The two pins

| Pin | Tag | Commit | Where it is recorded | Why |
| --- | --- | --- | --- | --- |
| Contractual baseline | `v2026.9.2` | `3928bad9badfcb6c7d140530435e806fb8092190` | `docs/QUBIT_OWNER_AMENDMENT_2026-09-11.md` (frontend-vendoring amendment, implementation record) | The revision the owner approved and recorded on 2026-09-11. Frozen/append-only; the exact OpenClaw tree the approval references. |
| Audited capability pin | `v2026.9.4` | `3a9d69db306cd7f081e06254cb89c4bcc14a7107` | `docs/QUBIT_GAP_LEDGER.openclaw.json` (`upstream_pin`, `upstream_tag`), `docs/AUDIT_OPENCLAW_V2026_9_4_DELTA.md` (pin table) | The latest stable release when the delta audit was performed (2026-09-10). It carries 32 capability deltas (D1–D32) beyond v2026.9.2 and is the pin the current ledger counts. |

An intermediate stable tag exists but is not a Qubit pin:
`v2026.9.3` / `1391f7cd2d40ab5bbcf2f5f831d3a64f520e72d7`
(`docs/UPSTREAM_BASELINE_2026-09-11.md`).

The pinned Hermes revision is separate and unchanged:
`2237be355906fbe6065ce1815711eee52b2d646e` (`v2026.9.7`, MIT) recorded in
`docs/QUBIT_OWNER_AMENDMENT_2026-09-11_HERMES_RUNTIME.md`.

## 2. Which artifact is authoritative for what

Authority order is the one in `docs/QUBIT_EXECUTION_DIRECTIVE_2026-09-12.md`:
owner amendments and the frozen contract, then the directive, then the gap
ledger for implementation status.

* **What did the owner approve?** `v2026.9.2`
  (`docs/QUBIT_OWNER_AMENDMENT_2026-09-11.md`). This is the pin the approval
  text names, so it remains the legal/contractual baseline.
* **What capabilities must Qubit cover?** The ledger
  (`upstream_pin = 3a9d69db…`, `upstream_tag = v2026.9.4`) is the status
  authority. Its embedded baseline is explicitly *"72 rows @v2026.9.2 + 32
  delta rows @v2026.9.4"*, so the v2026.9.2 inventory is not lost — it is the
  first 72 rows.
* **What changed between them?** `docs/AUDIT_OPENCLAW_V2026_9_4_DELTA.md`,
  anchored at v2026.9.4, with `docs/AUDIT_OPENCLAW_2026-09-11.md` as its
  v2026.9.2 predecessor.

## 3. Why there is no conflict

1. Both hashes are the same upstream repository (`openclaw/openclaw`); the
   v2026.9.4 pin is two stable releases newer, not a fork or a different
   project.
2. Qubit does not copy OpenClaw source. The reuse rule is *behavioral
   adaptation only* (`voicebridge/upstream/ATTRIBUTION.md`), so there is no
   vendored OpenClaw tree whose bytes must match one pin. The pin identifies
   the capability surface that was audited, not code shipped in this repo.
3. The owner amendment is append-only. Advancing its recorded pin would
   require a new dated owner amendment; Wave 5 does not grant itself one. The
   ledger is allowed to track a newer audit pin because the directive makes the
   ledger the current-status authority, not the amendment.
4. The delta audit is honest about its bounds: no OpenClaw code was executed,
   the clone is shallow, and line numbers are pinned to `3a9d69db`
   (`docs/AUDIT_OPENCLAW_V2026_9_4_DELTA.md` §5). Those caveats are retained.

## 4. What Wave 5 did and did not touch

* **Not edited to change pins:** `docs/QUBIT_OWNER_AMENDMENT_2026-09-11.md`,
  `docs/QUBIT_OWNER_AMENDMENT_2026-09-11_HERMES_RUNTIME.md`,
  `docs/QUBIT_FROZEN_ACCEPTANCE_CONTRACT.md`,
  `docs/QUBIT_EXECUTION_DIRECTIVE_2026-09-12.md`,
  `docs/AUDIT_OPENCLAW_2026-09-11.md`,
  `docs/AUDIT_OPENCLAW_V2026_9_4_DELTA.md`.
* **Privacy scrub only:** the same Wave 5 privacy pass replaced the developer
  home-path literal with `$HOME` in tracked docs (including the two OpenClaw
  audits, which contained the local checkout path). That is a path-privacy
  edit, not a change to any finding, pin, count, or citation.

## 5. If the pins must be unified

An owner amendment dated after 2026-09-11 that names `v2026.9.4` as the
approved OpenClaw baseline would unify the amendment and ledger pins. Until
then, treat `v2026.9.2` as the approved baseline and `v2026.9.4` as the
audited capability target.
