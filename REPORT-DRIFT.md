# REPORT — Upstream Drift: how far behind are we, in COMMITS and in REAL WORK

**Branch:** `agent/catchup-4`  **Base:** `1c740851e`
**Frozen pin:** `517b5e10febd619ce30bb22580e29b160266eb43` (2026-10-08)
**Measured upstream HEAD:** `4e59ac2b883b4133ea8a2d299abac45189781c09` (2026-10-15)
**Tool:** `scripts/upstream_drift_report.py` (committed this branch)

---

## HEADLINE

> **We are 3,887 commits behind upstream, across 5,314 changed files — but only
> 298 of those files are production Python we do not have at all; the other
> 1,114 production-Python files upstream changed are files we already carry, so
> the genuine porting backlog is ~298 production files (231 in the core dirs),
> not 3,887 of anything.**

The apparent "3,887 commits / 5,314 files" overstates the porting work by roughly
5×. Commit count answers *how much upstream moved*; it is not a work estimate.
The overlap lens is what turns the git facts into the work number.

---

## The numbers, each answering a DIFFERENT question

| metric | value | what it answers |
|---|---|---|
| `commits_behind` | **3,887** | how many commits upstream landed since our pin |
| `files_changed` | **5,314** | how many paths differ across that range |
| `files_changed` (all `.py`) | 3,659 | …of which Python |
| `production_py_changed` | **1,412** | …excluding `tests/` (production Python) |
| core-dirs production `.py` | **997** | …inside `agent/ gateway/ tools/ cron/ hermes_cli/` |

Why prior reports variously said 3,842 / 11,066 / 4,473 / 584 and all were "true":
they quoted different questions. Reconciliation of the range endpoints:

| range | commits | meaning |
|---|---|---|
| `517b5e10..origin/main` | **3,887** | **the answer** — frozen pin → HEAD |
| `69948c0057..a036b13793` | 5,441 | old Sept sync window (Sept 25 → Oct 6) — a waypoint, not the pin |
| `a036b13793..517b5e10` | 1,783 | pin move after the sync baseline |
| `69948c0057..origin/main` | 11,066 | from the ORIGINAL vendoring commit (v0.21.5) to now |

`584` and `4,473` came from branch-only-line and changed-file counts applied to
short windows; `3,842` was a near-miss on the true commit count. None was a lie;
quoting one as another is what misled the owner.

---

## The overlap lens, applied UPSTREAM

Adapted from this repo's branch-vs-HEAD lens. Upstream shares no merge base with
our vendored tree, so the common ancestor is the FROZEN PIN and the "head" side
is our tree (existence tested through the audits' own `route()` rename map):

```
base            = FROZEN_REV (517b5e10)   the last revision we vendored from
branch          = upstream origin/main    what upstream has now
branch_changed  = git diff --name-only FROZEN_REV..origin/main
our_tree        = our vendored paths, mapped hermes_* -> qubit_*
both_changed    = branch_changed that ALSO exist here  -> upstream newer (drift)
only_branch     = branch_changed absent here           -> genuine additions
```

| bucket | files | production `.py` | core production `.py` |
|---|---|---|---|
| **genuine additions** (absent here) | 1,969 | **298** | **231** |
| **upstream merely newer** (we carry it) | 3,205 | 1,114 | 766 |
| ↳ of which plain modifications | 118 | **0** | 0 |
| deleted upstream | 127 | — | — |

The decisive row is *plain modifications = 118 files / **0** production `.py`*:
**every production Python file upstream modified also exists here** — so 100% of
the "upstream is just newer" production mass is drift on files we already have,
and it is not new work. All 298 genuine production additions are files we do not
have at all.

### Genuine production additions by dir (the real backlog)

| dir | new production `.py` |
|---|---|
| `gateway/` | 127 |
| `hermes_cli/` | 61 |
| `agent/` | 28 |
| `tui_gateway/` | 12 |
| `evals/` | 10 |
| `scripts/` | 10 |
| `tools/` | 9 |
| `cron/` | 6 |
| `acp_adapter/` | 5 |
| `ui-tui/` | 4 |
| `qubit_state_*` root split modules (keys, local, media, mutations, runtime, …) | ~19 × 1 |
| `hermes_platform/`, `plugins/`, `pm/` | 2 / 2 / 1 |

### What this means for porting effort

- **Scale:** ~298 production files to add, ~1,114 to re-sync (mostly mechanical,
  rebrand-aware merges). The commit count is irrelevant to the plan.
- **Hotspot:** `gateway/` alone is 127 of the 298 new files — over 40% of the
  genuine additions. `hermes_cli/` (61) + `agent/` (28) are the next tranches.
- **Risk:** the 1,114 "upstream newer" production files are where merges conflict
  with our rebrand; they cost review time, not authorship.
- **Concentration:** 231 of 298 new production files sit in the five core dirs, so
  the work is focused, not scattered.

---

## Pin reconciliation — what changed and what was left alone

### Fixed (drift-MEASURING call sites only)

| file | before | after | why |
|---|---|---|---|
| `scripts/audit_capability_gaps.py:93` | `FROM,TO = "69948c0057","a036b13793"` | `FROM = FROZEN_REV`, `TO = "origin/main"` | **the named defect** — measured a stale Sept window we had already closed; now measures the frozen-pin finish line |
| `scripts/audit_missing_symbols.py:34` | `BASE = "a036b13793"` | `BASE = FROZEN_REV` | compared our symbols against an Oct-06 waypoint, hiding everything upstream added since |

Both now read `FROZEN_REV` from its single declared home
(`scripts/frozen_target_progress.py`) rather than hardcoding a second, stale copy —
that duplication is the root cause of the disagreement.

`scripts/upstream_drift_report.py` (new) also reads `FROZEN_REV` from that same
home, so all three drift instruments and the burndown agree by construction.

### Deliberately NOT touched (provenance anchors)

These carry `69948c0057…` as an **immutable record of what was vendored from**
(upstream v0.21.5 @ 2026-09-25), not as a drift target. Rewriting any of them
would falsify the vendoring record and break the rebrand-determinism tests
(`tests/test_rebrand_determinism.py`, `tests/test_rebase_candidate.py`,
`tests/test_qubit_vendor8tf.py`):

| file:line | constant | role |
|---|---|---|
| `scripts/hermes_vendor_manifest.py:36` | `PIN` | per-file provenance `"rev"` + LICENSE attribution |
| `scripts/hermes_byte_manifest.py:49` | `PIN` | byte-manifest blob source of record |
| `scripts/merge_v0215/freeze.py:26` | `PIN` | the rebase target revision (owner amendment #25) |
| `scripts/rebase_v0215.sh:39` | `QUBIT_PIN` | generate the v0.21.5 rebrand candidate |
| `scripts/rebuild_ledger_hermes.py:60` | `PIN` | ledger provenance |
| `scripts/differential_parity.py:337` | `CONTRACTUAL_PINS["hermes"]` | owner amendment #25 contractual pin |
| `scripts/source_inventory.py:46` | `HERMES_PIN` | inventory of the pinned tree |
| `scripts/sync_upstream.py`, `sync_driver.py`, `sync_dryrun_one.py`, `sync_derive_rename_map.py` | `PIN_SHA`/`BASE_SHA` | document the historical sync window in their docstrings; changing the constants would misstate that history |
| `sandbox/scripts/*` | various | a frozen sandbox snapshot; not live code |
| root `tests/*.py` (≈20 files) | `PIN`/`QUBIT_REV`/`EXPECTED_REVISION` | assert the vendored digest; the anchor IS the assertion |

Only `hermes_vendor_manifest.py` received an edit — a **comment** disambiguating
the two constants (documented provenance anchor vs drift pin), with the constant
value unchanged. All other anchors above are byte-identical to the base revision.

### Why `hermes_vendor_manifest.PIN` ≠ `frozen_target_progress.FROZEN_REV` is correct

They answer different questions and *should* differ:
`hermes_vendor_manifest.PIN` (69948c0057) is the revision the code was copied
FROM; `frozen_target_progress.FROZEN_REV` (517b5e10) is the revision the burndown
measures TO. Collapsing them to one value would either falsify provenance or move
the finish line. The script and report now state which constant means which.

---

## Reproduce

```
.venv/bin/python scripts/upstream_drift_report.py
.venv/bin/python scripts/upstream_drift_report.py --json out.json
.venv/bin/python -m pytest tests/test_upstream_drift_report.py -o addopts='' -q
```

Output is deterministic (no timestamps) so a cron monitor can use it as a change
detector. Measured on the clone at `~/upstream/hermes-agent` (tree:0 partial clone;
only count/name-only queries are used).
