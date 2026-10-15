# Office invariant fixes — independent verification

**Verifier:** Qubit Agent (independent verification subagent). **Date:** 2026-10-15.
**Worktree:** `/home/trick/Projects/Repos/qubit-voicebridge`. **Base HEAD:** `629c8de0f`.
**Test artifact:** `tests/test_office_invariant_fix_verification.py` (21 tests, all passing).
**Scope:** this doc + that test file. No production module and no other agent's
test file was edited.

This is the **adversarial** check on the four fixes to
`docs/OFFICE-INVARIANT-AUDIT.md`. Each finding was (1) reproduced
independently against the pre-fix tree, (2) re-tested against the fix with
assertions written from the *invariant* (not from the diff), and (3) attacked
with adjacent inputs, boundaries, sibling call paths, ordering, and a
compensated/reversed row.

**Method for the pre-fix reproduction.** A clean `git worktree` at HEAD
(`629c8de0f`, no fixes) was created in scratch; the same probes were run there
and in the live tree. The pre-fix output is quoted below and is real.

The fixes landed as **uncommitted working-tree edits** (`git status` shows
`voicebridge/office/{ledger,work,envelope}.py` + `voicebridge/cli.py` modified
on top of `629c8de0f`); no fix commit exists yet as of this report. The tree was
repeatedly reset/stashed mid-run by the coordinating process, so all evidence
here was captured from a live, fix-present tree and cross-checked by re-running.

---

## Verdicts

| # | Invariant | Where | Verdict |
|---|---|---|---|
| 1 | Above-envelope spend is refused unless a raise **covering this spend for this period** was approved | `envelope.py` `spend()` / `_approval_covers` / `_approved_raise_headroom` | **CONFIRMED-FIXED** (latent path; no CLI/turn caller) |
| 2 | Reversing an `unmetered` row stays unmetered — no metered money minted or destroyed | `ledger.py` `reverse()` | **CONFIRMED-FIXED** |
| 3 | `review()` level-checks the approver, including `by_agent=None` | `work.py` `review()` | **CONFIRMED-FIXED *but INCOMPLETE*** — breaks 2 existing callers (see §3) |
| 4 | A seat cannot silently re-claim another seat's `running` task | `work.py` `claim()` + `cli.py` `--handoff/--reason` | **CONFIRMED-FIXED** (handoff is unilateral-but-*recorded* by design) |

Also found, **not one of the four**: `tests/test_office_no_phantom_authority.py::test_no_unlisted_or_true_tautology`
is **red at the base HEAD too** (a pre-existing `assert … or True` at
`tests/test_office_game_art.py:596`). It is unrelated to these fixes and is not
a regression.

---

## FINDING #2 — unmetered reversal (money) — **CONFIRMED-FIXED**

**The fix.** `reverse()`'s compensating `record(...)` call now passes
`unmetered=bool(original["unmetered"])`.

**Pre-fix reproduction (clean HEAD worktree, real output):**

```
FINDING2: bal before = 0
  after unmetered inference 777: bal = 0
  after reverse: bal = 777 | reversal unmetered flag = False      <- money minted
FINDING2b: unmetered revenue 5000 -> bal 0 -> after reverse -5000  <- money destroyed
```

`reverse()` of a phantom row wrote a **metered** `transfer`; `assert_balances`
stayed `ok: True` because it recomputes from the same rows the reversal wrote.

**Post-fix (live tree):**

```
FINDING2: bal before = 0
  after unmetered inference 777: bal = 0
  after reverse: bal = 0 | reversal unmetered flag = True
FINDING2b: unmetered revenue 5000 -> bal 0 -> after reverse 0
```

**My independent tests** (`tests/test_office_invariant_fix_verification.py`):

- `test_v2_unmetered_reversal_stays_unmetered` — reversal is `unmetered=True`,
  balance unchanged, `sum_metered == 0`, row still counted (`runs_recorded`).
- `test_v2_unmetered_revenue_reversal_does_not_destroy` — no negative balance.
- `test_v2_reversal_of_metered_row_still_metered` — **adversarial**: a metered
  row's reversal must stay **metered** (the fix must not blanket-mark).
- `test_v2_reversal_flag_matches_original_for_every_kind` — **adversarial**:
  sweep `{revenue,inference,fees,capex} × {metered,unmetered}`; the flag must
  copy the *original row*, not key off the kind (a `transfer` is metered "by
  definition", so keying off the kind would have been wrong).
- `test_v2_reversal_of_a_reversal_preserves_flag` — **adversarial**: chain
  `reverse(reverse(x))`; the flag survives the hop.

**Adversarial results:**

- Reversing an **unmetered** row then reversing **that** reversal keeps both
  unmetered and leaves the balance at 0. ✅
- Reversing a **metered** `inference` is still a metered credit — the metered
  reversal-neutrality invariant (`test_held_reverse_of_revenue_and_inference_is_exactly_neutral`)
  still holds, verified live: net `700 → 0`. ✅
- Post-fix the fraud is no longer hidden by the control: `balance_sheet.assert_balances`
  `ok` stays `True` **and** health now reports `metered_entries: 0`,
  `unmetered_entries: 2` — the reversal is correctly classified as a phantom, so
  the auditor is no longer blind (there is no longer a fake metered row to be
  blind *to*). ✅

**No adversarial input defeated this fix.** It is minimal, correct, and complete.

---

## FINDING #3 — review level check — **CONFIRMED-FIXED but INCOMPLETE**

**The fix (as landed).** `review()` no longer skips the check on
`by_agent is None`. It **derives the required approver from the assignee's
supervisor** (mirroring `task_proposal.approve`), and interprets `None` as *the
owner*:

- `by_agent=None` (owner) approves **exactly** work whose assignee has **no
  supervisor** (the top seat). Everywhere else it is refused.
- A real seat must equal the **assignee's supervisor**; the assignee itself is
  refused (self-approval).
- The stored `reviewer_id` is treated as a *convenience copy*, not the
  authority.

**Pre-fix reproduction (clean HEAD):**

```
FINDING3: wrong-level(ceo,reviewer=mgr)=refused:OFFICE_REVIEW_WRONG_LEVEL | by=None=ACCEPTED(None bypass)
```

The rule held when an identity was supplied and was silently off when it was not.

**Post-fix (live tree):**

```
FINDING3: wrong-level(ceo,reviewer=mgr)=refused:OFFICE_REVIEW_WRONG_LEVEL | by=None=refused:OFFICE_REVIEW_WRONG_LEVEL
```

**My independent tests:**

- `test_v3_none_is_owner_and_is_level_checked` — `by_agent=None` on a worker
  task is refused; the task is **not** accepted; the manager still can.
- `test_v3_wrong_level_seat_still_refused` — self, peer, and skip-level seats
  are all refused.
- `test_v3_owner_may_judge_a_top_seat_task` — **completeness**: the owner path
  must remain *usable* (a blanket "None always refused" would be a regression);
  the owner (None) accepts the top seat's work and a seat cannot.
- `test_v3_owner_cannot_reach_over_the_level_above` — **adversarial**: the owner
  must not approve a normal seat's work directly.
- `test_v3_unknown_agent_id_is_refused_not_skipped` — **adversarial**: an
  unknown integer `by_agent` is not a wildcard.

**Adversarial successes:**

- **Tampered `reviewer_id`:** with `reviewer_id` rewritten to the CEO, the
  *manager* (the assignee's true supervisor) still succeeds and the CEO is
  refused — proving the authority is derived, not read from the stored column,
  so the copy cannot be used to widen the gate. ✅

**⚠️ INCOMPLETE — a sibling caller is broken.** The fix changes the
`by_agent=None` contract, but two existing, **legitimate** callers in
`tests/test_office_work_loop.py` still call `W.review(...)` with **no**
`by_agent` (lines 218, 280) to exercise the *reject-with-no-instruction* and
*attempts-capped → abandoned* contracts. They now fail **before** reaching the
reject logic:

```
FAILED tests/test_office_work_loop.py::test_a_rejection_without_an_instruction_is_refused
FAILED tests/test_office_work_loop.py::test_attempts_are_capped_and_the_task_is_abandoned
E  OfficeError: refused: task #1 was done by 'wk-0' and must be accepted by its
   level above, seat #2 (C31) — not by the owner directly.
```

**These two tests pass at the base HEAD** (verified in the clean worktree:
`2 passed`) and fail **only** with the fix. So the fix is correct on the
invariant and *incomplete* on the call path: the reject contract is
unreachable through the default CLI/`None` invocation. The fixing agent must
either pass the reviewer explicitly in those tests, or the work-loop contract
must be updated — a human decision (I did not edit that file).

> Note: the module docstring's "A rejection is a fix instruction" contract is
> unaffected for an *identified* reviewer; only the anonymous path is now
> closed. The safest repair is for the tests to call
> `W.review(cfg, t, verdict="reject", note=" ", by_agent=<manager>, confirmed=True)`.

---

## FINDING #4 — claim ownership — **CONFIRMED-FIXED**

**The fix (as landed).** `claim()` refuses a cross-seat take of an
already-assigned in-progress task with `OFFICE_TASK_NOT_YOURS`, **unless**
`handoff=True` *and* a non-empty `reason` are passed. A handoff updates the
assignee and appends a `reassigned` trail event naming **both** seats. The CLI
gained `--handoff` / `--reason`. Re-claiming by the **current assignee** is
still allowed silently (the ordinary recovery path). The reviewer is always
recomputed from the new assignee's supervisor.

**Pre-fix reproduction (clean HEAD):**

```
FINDING4: RECLAIM-OK assignee=4 reviewer=2 | final assignee=4 status=running | trail=['created', 'claimed', 'claimed']
```

A peer's claim overwrote the in-flight assignee; the trail could not tell a
reader that the first seat's work was discarded.

**Post-fix (live tree):**

```
FINDING4: refused:OFFICE_TASK_NOT_YOURS | final assignee=3 status=running | trail=['created', 'claimed']
```

**My independent tests:**

- `test_v4_peer_cannot_reclaim_running_task` — a peer is refused; assignee,
  reviewer and status untouched.
- `test_v4_self_reclaim_still_allowed` — **completeness**: the current assignee
  recovers silently.
- `test_v4_requeue_then_claim_by_peer_is_the_honest_path` — **adversarial**: the
  documented handoff (`requeue` → `claim`) works and records a `requeued` event.
- `test_v4_fixing_state_still_transferable` / `test_v4_running_task_with_no_assignee_is_claimable`
  — **adversarial boundaries**: a `running`/`fixing` task with **no** assignee is
  not blocked by a NULL-comparison bug.

**Adversarial results:**

- Plain cross-seat claim → `OFFICE_TASK_NOT_YOURS`. ✅
- Cross-seat claim with `handoff=True` and **no reason** → `OFFICE_BAD_INPUT`
  (a handoff must say why). ✅
- Cross-seat handoff with a reason → succeeds and the trail is
  `created → claimed(#w1) → reassigned(#w1→#w2, reason) → claimed(#w2)`, with
  payload `{from_agent_id, to_agent_id, reason, reviewer_id}`. The discarded
  work is now on the record. ✅
- **Sibling path — dispatch:** `pick_worker()` returns the *existing* assignee
  for a `running`/`fixing` task, so dispatch's re-claim is always by the current
  owner and never needs a handoff. The fix does **not** break the autonomous
  driver. ✅

**Design note (not a defect).** The handoff is *unilateral*: any seat can take
another's in-progress task with just a reason — there is no consent from the
current assignee. That is the fix the audit asked for ("refuse or require an
explicit `--steal`/`requeue` first"): the invariant is "no work changes hands
**unrecorded**", and a reason-bearing handoff with a both-seats trail satisfies
it. If the owner wants *consent* rather than *disclosure*, that is a contract
decision beyond this fix.

---

## FINDING #1 — envelope raise linkage — **CONFIRMED-FIXED**

**The fix (as landed).** `_has_approved_raise` (which returned `True` for *any*
approved request) was **removed**. `spend()` now computes
`_approved_raise_headroom(cfg, agent_id, check)` — the sum of `approved` request
amounts **for the current period** minus the overage the seat has already drawn
(read from `check["headroom_cents"]`, so no nested connection) — and
`_approval_covers(check, cents, approved_cents)` — the raise must cover the
**overage** (`cents - max(0, headroom)`).

**Pre-fix reproduction (clean HEAD):**

```
FINDING1: over-envelope-without-raise=refused:OFFICE_ENVELOPE_EXCEEDED | tiny-raise-then-50k=ALLOWED raised=True metered=50100
FINDING1b: _has_approved_raise(this_period) with only 2000-01 approval = True
```

A `$0.01` raise licensed `$500.00` of spend; a raise approved for `2000-01`
satisfied the check in any period.

**Post-fix (live tree):**

```
FINDING1: over-envelope-without-raise=refused:OFFICE_ENVELOPE_EXCEEDED | tiny-raise-then-50k=refused:OFFICE_ENVELOPE_EXCEEDED
FINDING1b: approved-raise headroom this period with only 2000-01 approval = 0
```

**My independent tests:**

- `test_v1_over_envelope_without_raise_is_refused` — baseline.
- `test_v1_tiny_raise_does_not_unlock_unlimited_spend` — the pre-fix exploit;
  the refused spend books no metered cost.
- `test_v1_raise_covering_the_overspend_is_honoured` — **completeness**: a raise
  that genuinely covers the overage still works.
- `test_v1_raise_for_another_period_does_not_cover_this_one` — **adversarial**:
  period linkage.
- `test_v1_raise_does_not_cover_a_later_larger_overspend_in_same_period` —
  **adversarial**: consumption — one raise must not license cumulative spend
  beyond its amount.
- `test_v1_denied_raise_is_not_a_raise` — **adversarial**: a denied request
  contributes nothing.

**Adversarial results (live, real output):**

- Boundary exact: envelope 100, raise 250; `spend 250 → OK`, `spend 251 →
  OFFICE_ENVELOPE_EXCEEDED`. ✅ (off-by-one correct)
- Full budget: envelope 100 + raise 500 = 600; `100 → OK`, `500 → OK`,
  `1 more → refused`; the raise is fully consumed by metered spend. ✅
- Positive headroom: envelope 1000, `spend 1100` with no raise → refused;
  with a 100-cent raise → OK; `1 more` → refused. The approval covers only the
  overage, not the whole spend. ✅
- Adding a **second** approved raise stacks correctly (500 then 100 → 600 of
  overage available), then refuses the 601st cent. ✅
- **Reversal interaction:** spending against a raise then reversing that spend
  does **not** free the raise for reuse (`sum_metered` does not drop for a
  `transfer`, so headroom stays negative) — a conservative, non-exploitable
  outcome. ✅
- **Unmetered raised spend** does not consume the raise (booked no metered
  cost) — consistent with D16 (unmetered spend is not real dollars). Not a
  defect. ✅
- **No envelope set** → uncapped, unchanged. ✅

**Reachability (honest, unchanged from the audit).** `grep` still shows no live
caller of `envelope.spend`; the fix closes a *latent* enforcing-path hole that
the next caller would inherit.

**No adversarial input defeated this fix.**

---

## Files

- `tests/test_office_invariant_fix_verification.py` — 21 independent tests
  (all passing against the fix-present tree; verified failing-meaningful by the
  pre-fix reproductions above).
- `docs/OFFICE-INVARIANT-FIX-VERIFICATION.md` — this report.
- Scratch (not committed, outside the repo tree): clean-HEAD `git worktree` and
  the reproduction/attack probes under the runtime cache.

## What the human should act on

1. **#3 INCOMPLETE:** `tests/test_office_work_loop.py` lines 218 and 280 call
   `W.review(...)` with no `by_agent` and now fail. Decide whether those tests
   pass the reviewer explicitly or the work-loop contract changes. **This is the
   only open defect in the four.**
2. **#4 design note:** handoff is unilateral-but-recorded. Confirm that
   *disclosure* (not *consent*) is the intended invariant.
3. The fixes are **uncommitted**; if a commit is required, it has not landed as
   of this report.
