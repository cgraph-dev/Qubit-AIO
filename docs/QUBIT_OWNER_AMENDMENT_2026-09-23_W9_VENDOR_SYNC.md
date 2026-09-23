# Qubit Owner Amendment (DRAFT) — W9 Vendor Sync Pin Change

Date: 2026-09-23
Status: **OWNER-SELECTED TARGET `d337b736aa…` (2026-09-23). PIN MOVE PENDING WORKTREE FREEZE (see §8).**

This document is a **request for owner approval**, prepared read-only. It does
**not** change any pin, does not authorize a sync, and does not claim any
milestone. Until the OWNER SIGN-OFF block in §8 is completed, the contractual
pin in force is unchanged and the rules of
`docs/archive/2026-09-22-pre-reset/QUBIT_UPSTREAM_SYNC_AND_ADAPTER_POLICY_2026-09-21.md`
§2 apply without exception.

## 1. Why an amendment is required

The upstream sync and adapter policy (2026-09-21) §2 states:

> Changing the pin requires explicit owner approval recorded as a dated owner
> amendment.

and §2 rule 2:

> The pin MUST NOT be replaced as a side effect of a sync, a merge, a generator
> run, or a dependency bump.

Qubit's vendored runtime (`qubit_runtime/`) is 0.21.1. The behavioral reference
install has moved to 0.21.4. Closing that gap requires moving the contractual
pin, which is an owner decision, not an implementation detail.

## 2. Current contractual pin (in force until sign-off)

| Field | Value |
|---|---|
| Commit | `2237be355906fbe6065ce1815711eee52b2d646e` |
| Commit subject | `chore: release v0.21.1 (2026.9.7)` |
| Commit date | 2026-09-07 15:16:44 -0700 |
| `pyproject.toml` version at pin | `0.21.1` |
| Recorded tag (policy §2) | `v2026.9.7` — **not resolvable in the local clone (0 tags present)** |
| Repository | `git@github.com:NousResearch/hermes-agent.git` |
| License at pin | MIT, `Copyright (c) 2025 Nous Research` |
| Vendored as | `qubit_runtime/` (renamed from `qubit_hermes/`) |

## 3. New pin — OWNER-SELECTED: `d337b736aa…` (2026-09-23)

> **Owner decision (recorded 2026-09-23, via chat):** pin the **stable v0.21.4
> release commit** `d337b736aa1e8ebecfab043842d13e4a2d2f48a3`, chosen for
> stability over the moving `main` HEAD. This selects the **code** version only;
> **data** (sessions, skills, plugins, MCP, cron, history, memories, config) is
> migrated separately from the live reference home `~/.hermes` (W-MIG) and is not
> carried by any commit. Moving the pin remains gated on the worktree freeze.

| Field | Value |
|---|---|
| **Owner-selected commit** | `d337b736aa1e8ebecfab043842d13e4a2d2f48a3` (v0.21.4 release, 2026-09-21) |
| Commit | `400f11bd35fae0da91c396a1424315da81729982` |
| Commit subject | `fmt(js): npm run fix on merge (#119581)` |
| Commit date | 2026-09-22 22:08:11 +0000 |
| `pyproject.toml` version at candidate | `0.21.4` |
| Branch / ref | `main` (worktree not detached) |
| Repository | `git@github.com:NousResearch/hermes-agent.git` |
| License | MIT, `Copyright (c) 2025 Nous Research` |
| Evidence command | `git -C /home/trick/.hermes/hermes-agent rev-parse HEAD` → `400f11bd35fae0da91c396a1424315da81729982` |

**Owner ruling:** pin the **stable release commit** `d337b736aa`, NOT the moving
`main` HEAD. The reference dashboard at `http://127.0.0.1:9119/?profile=default`
is the behavioral oracle; stability of the pinned CODE revision is preferred over
exact-HEAD match. (The draft's original recommendation — HEAD — is superseded by
the owner's selection.)

### 3a. Version/tag caveats (read before signing)

- The official 0.21.4 release commit is
  `d337b736aa1e8ebecfab043842d13e4a2d2f48a3` — `chore(release): v0.21.4 (v2026.9.21)`,
  2026-09-21 11:10:32 -0700. That commit is an **ancestor** of the candidate HEAD,
  **461 commits earlier**.
- The candidate HEAD is a **post-release** 0.21.4 commit on `main`, not the
  release tag itself.
- The local reference clone has **0 git tags** (`git tag | wc -l` → `0`), so
  policy §2's tag references (`v2026.9.7`, `v2026.9.14`) **cannot be verified
  locally**. Version identity is therefore established from commit subjects and
  `pyproject.toml`, not from tags. An owner may prefer to re-pin to the exact
  release commit `d337b736aa` instead of the moving `main` HEAD if tag-stability
  is preferred; this draft recommends HEAD, with `d337b736aa` as the fallback.

### 3b. Other observed SHAs from the prior W9 record (for reconciliation)

| SHA | Relationship | Note |
|---|---|---|
| `07610317671ca161c2e530cdb2858560375ba979` (`07610317`) | 33 commits before candidate HEAD | Called "0.21.3" in `docs/STATUS.md` §17. **Differs from HEAD.** Not the reference HEAD. |
| `7ce190446e548e2e1c0ae6ba0225be0490d12fc2` (`7ce19044`) | 9 commits before candidate HEAD | Called "0.21.4" in `docs/STATUS.md` §17; **stale** — the reference has since advanced 9 commits. |

## 4. Version delta

| | Pin | Candidate |
|---|---|---|
| Version | 0.21.1 | 0.21.4 |
| Released | 2026-09-07 | 2026-09-21 (release commit), HEAD 2026-09-22 |
| Delta | — | **3 patch releases (0.21.1 → 0.21.2 → 0.21.3 → 0.21.4)** |

## 5. Drift summary (pin → candidate HEAD)

Measured read-only with `git -C /home/trick/.hermes/hermes-agent`:

| Metric | Measured | Prior record (`STATUS.md` §17) |
|---|---:|---:|
| Commits (`git rev-list --count PIN..HEAD`) | **7,659** | 7,650 |
| Commits where HEAD is *behind* pin | 0 | — |
| Diff files changed | **7,594** | — |
| Insertions / deletions | **+582,686 / −108,219** | — |
| Added (A) | **2,938** | 2,927 |
| Modified (M) | **3,847** | 3,843 |
| Deleted (D) | **145** | 145 |
| Renamed (R) | **664** | 664 |

Discrepancy note: the prior 7,650 figure and its A/M counts are **stale** (the
reference advanced ~9 commits since that record). The measured values above are
authoritative as of 2026-09-23. All 3,588 "upstream files absent from the fork"
noted in `STATUS.md` §17 are 0.21.4 additions and remain to be re-verified
against this exact HEAD during the sync.

## 6. Blockers (both must be cleared before any sync work starts)

### B1 — Pin amendment (this document)
Moving the pin requires this draft to become a signed, dated owner amendment.
Not yet granted. No sync may begin on the strength of this draft.

### B2 — Uncommitted mid-flight worktree
The Qubit worktree is mid-rename and dirty by design. Read-only observation on
2026-09-23: **14,954 entries in `git status --porcelain`** (the `qubit_hermes/`
→ `qubit_runtime/` path-rename wave plus unstaged edits); `HEAD` is `f984911c`.
A vendor sync applied onto this state would be **unreviewable and
unrollbackable** (a 0.21.1→0.21.4 sync cannot be distinguished from the rename
wave; `git` cannot produce a clean three-way base). Freeze first.

## 7. Mandatory preconditions (ordered; do not skip)

1. **Freeze the worktree.** Commit (or otherwise record) the staged
   `qubit_hermes/` → `qubit_runtime/` rename and the unstaged edits so there is a
   single, revertible base. No sync step may run while `B2` holds.
2. **Provider-sync a pristine candidate source outside `~/.hermes`.**
   Provision a read-only comparison tree under a disposable path (e.g.
   `/tmp/qubit-upstream/hermes-agent-400f11bd/`), per the workflow rule that
   parity agents read the exact pinned source and never edit reference trees.
   Verify `git rev-parse HEAD`, clean status, license, and source manifest there.
   **Never** build from, fetch into, or mutate `~/.hermes` or the 9119 service.
3. **Amend the pin** (this document signed and dated) **before** step 4.
4. **Run the ordered sync process** of the upstream-sync policy §3 (fetch/record,
   preserve pin, compare surfaces, classify delta, adapters-not-copies, preserve
   policy/architecture, add voice metadata, add migration, run generators,
   focused verification).
5. **Re-run the deterministic generators** (policy §7/§8) to `docs/generated/`
   and prove byte-identical output; keep the branding scan and the final release
   gate in the admission path.
6. **Independent verifier** before release (policy §3 step 11). No milestone or
   `VERIFIED` claim is made or implied by this draft.

## 8. OWNER SIGN-OFF — target selected via chat 2026-09-23; pin move PENDING freeze

```
OWNER DECISION:            [x] APPROVED (pin target)  [ ] REJECTED  [ ] DEFERRED
NEW PIN APPROVED:          d337b736aa1e8ebecfab043842d13e4a2d2f48a3  (v0.21.4 release)
TAG STABILITY ACCEPTED:    [x] yes (release commit preferred; no local tags)
PRECONDITION 1 (freeze):   [ ] acknowledged — STILL REQUIRED before any sync
PRECONDITION 2 (pristine): [x] done (/tmp/opencode/pinned/hermes-agent-0.21.4/, 14,966 files)
DATA MIGRATION:            separate (W-MIG from the live ~/.hermes) — not a code pin
SCOPE LIMITS:              code pin to d337b736aa; deep identifier rename folded into the sync
RECORD:                    owner chat 2026-09-23 — "d337b736 we will choose this cos its more stable"
```

> Recorded from the owner's chat instruction; the owner may replace this with a
> signed/dated copy. The pin move itself does not happen until PRECONDITION 1
> (worktree freeze) is satisfied.

## 9. Explicit non-claims

- The pin **target** is owner-selected (`d337b736aa`, stable v0.21.4 release);
  the pin move is **not yet performed** and requires the worktree freeze (§8).
- No `M1`, `M2`, `CODE_READY`, `VERIFIED`, or `COMPLETE` claim is made.
- No pin has been changed; no sync has been performed; no reference tree was
  modified; no secret was read or written.
- The `7,650`-commit / A/M counts in `STATUS.md` §17 are **stale** and recorded
  above only for reconciliation; the measured drift is authoritative.

## 10. References

- `docs/archive/2026-09-22-pre-reset/QUBIT_UPSTREAM_SYNC_AND_ADAPTER_POLICY_2026-09-21.md` §2, §3, §7, §8.
- `docs/STATUS.md` §17 (W9 vendor sync — planned, blocked).
- `docs/DECISIONS.md` (owner rulings in force).
- `docs/archive/2026-09-21/OPENCODE_WORKFLOW.md` (direct upstream source access).
