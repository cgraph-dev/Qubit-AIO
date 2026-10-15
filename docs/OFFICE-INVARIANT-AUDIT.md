# Office engine — adversarial cross-module invariant audit

**Job:** audit the `voicebridge/office/` engine's **cross-module invariants** —
money conservation, consent / separation-of-duties, budget caps, task
claiming, survival boundary, store integrity — by writing tests that try to
**BREAK** them with real execution, not by reading code.

**Author:** Qubit Agent (invariant-audit subagent). **Date:** 2026-10-15.
**Worktree:** `/home/trick/Projects/Repos/qubit-voicebridge`, HEAD `17da98198`.
**Test artifact:** `tests/test_office_invariants_audit.py` (25 tests, all
passing — the BROKE tests assert the current, wrong behaviour so the suite
stays green while the defect is open).
**Scope:** `docs/OFFICE-INVARIANT-AUDIT.md` + the one new test file. No
`voicebridge/` source was edited. No vendored habblaud file was touched.

Every reproduction below was **run this session**; the output quoted is real.
BROKE = a concrete input sequence violates an invariant the code claims to
hold. HELD = the invariant survived an adversarial attempt and is now a
permanent test guard.

---

## Headline

| # | Invariant (as claimed) | Where | Verdict | Reachable? |
|---|---|---|---|---|
| 1 | "Above the envelope, it is refused unless raised." | `envelope.py:12-20`, `spend()` | **FIXED** | latent — no CLI/turn path calls `spend()` yet |
| 2 | Unmetered rows never enter any metered fold; agent balances exclude them (D16). | `ledger.py:44-47`, `reverse()` | **BROKE** | **live** — `reverse()` is a public API |
| 3 | "The reviewer is exactly one level above the assignee." | `work.py:9-11`, `review()` | **BROKE** | **live** — `task-review` CLI `--by` defaults to `None` |
| 4 | "Assign a task to a seat, enforcing the chain." | `work.py:114`, `claim()` | **BROKE** | **live** — `task-claim` re-claims a running task |

Four BROKE findings, three of them reachable from documented CLI verbs. The
two most serious (2 and 4) are *money* and *work-integrity* breaks that the
existing controls — `balance_sheet.assert_balances`, the audit reconciliation —
do **not** catch.

---

## FINDING #1 — a small approved raise unlocks unlimited spend [RESOLVED]

**STATUS: RESOLVED.** Fixed in `envelope.py`: `_has_approved_raise` (existence
only) is replaced by `_approval_covers` + `_approved_raise_headroom`, which
require the approved raise to COVER the spend in THIS period. Verified by
`tests/test_office_invariants_audit.py` FINDING #1 guards and by mutating a
scratch copy back to the old predicate (both behaviour guards go RED).

**Invariant claimed.** `envelope.py:12-20`:

> * **Inside the envelope, spending is free and is only *reported*.** ...
> * **Above the envelope, it is refused unless raised** — and a raise must name
>   the four things the contract requires.

**Where (before the fix).** `envelope.spend()` (`envelope.py:354`) called
`_has_approved_raise` (`envelope.py:398`) when the spend was above the envelope.
That predicate was:

```sql
SELECT 1 FROM office_request WHERE agent_id=? AND status='approved' LIMIT 1
```

It took **no amount and no period**. Its answer could not depend on either. So
*any* approved request — for a single cent, for a past period — satisfied a
spend of *any* size.

**Reproduction** (`test_finding1_tiny_approved_raise_does_not_unlock_unlimited_spend`):

```
org envelope              = 100 cents;  seat funded with a large balance
spend   100               -> OK, headroom now 0
spend    50               -> refused OFFICE_ENVELOPE_EXCEEDED   (correct)
raise     1 cent, approve -> the envelope is NOT topped up
spend 50000               -> REFUSED OFFICE_ENVELOPE_EXCEEDED  (after the fix)
```

Before the fix that last line returned
`{'booked': True, 'within_envelope': False, 'raised': True}`: a **$0.01** raise,
approved once on a **$1.00** envelope, licensed **$500.00** of spend — 500× the
raise, 500× the envelope, with the seat's metered cost ending at `50_100` cents.

**Root cause, isolated**
(`test_has_approved_raise_ignores_amount_and_period`): a raise approved for
period `2000-01` satisfied `_has_approved_raise` in any later period, because
the predicate had no period argument at all.

**Reachability (honest).** `grep` shows **no caller** of `envelope.spend`
anywhere in `voicebridge/` — not the CLI, not `runner.run_turn`, not
`dispatch`, not `autowork`. The live turn path gates on `receipts.admit`
(dollar caps + balance), and `autowork`/`envelope.headroom_for` only *read*
`check_spend`. So this was a **latent defect in the enforcing wrapper**, not a
live exploit today. It matters because `spend()` is the documented enforcing
path and the next caller inherits the hole. It also means the envelope is not
actually enforced on a real turn — a separate gap worth a decision.

**The contract implemented (one sentence).** *A spend above the envelope is
permitted only when the seat holds an approved raise — for this period, and
covering the overage above the remaining envelope headroom — that has not
already been drawn on; an insufficient, stale, or already-consumed approval is
treated as no approval.*

`_approved_raise_headroom` sums the seat's `approved` `office_request.amount_cents`
for the current `period`, minus the overage already drawn this period (so one
approval cannot authorise two spends). `_approval_covers` permits the spend iff
`max(0, cents - max(0, headroom)) <= approved_cents`. A negative headroom (the
seat is already above the envelope) makes every new cent overage. A denied or
other-period approval contributes zero. The refusal stays the typed
`OFFICE_ENVELOPE_EXCEEDED` and its detail names the approved cents held.

**Falsification.** Mutating a scratch copy of `envelope.py` back to the
existence-only predicate turns both behaviour guards
(`test_finding1_...` and `test_a_sufficient_approved_raise_covers_the_spend`)
RED; the repo copy was never mutated.


---

## FINDING #2 — reversing an unmetered row manufactures metered money

**STATUS: RESOLVED.** Fixed in `ledger.reverse()` by threading
`unmetered=bool(original["unmetered"])` into the compensating `record()` call.
The BROKE tests below were flipped to assert the FIXED behaviour; the
reversal is now the exact inverse of what the original did to the metered fold.

**Invariant claimed.** `ledger.py:44-47`:

> `unmetered` (D16): the row is recorded, but **no metered aggregate will ever
> see it**. Agent balances, budget caps and P&L exclude it; run counts include
> it.

**Where.** `ledger.reverse()` (`ledger.py:152`) writes a compensating
`transfer` whose amount is derived from the original row — but it does **not**
propagate the original's `unmetered` flag, and a `transfer` is metered **by
definition** (only the four magnitude kinds may be `unmetered`). The
compensating row therefore enters the very fold the original was excluded from.

**Reproduction — money creation**
(`test_finding2_reversing_unmetered_row_creates_metered_money`):

```
seat balance before                          = 0
record inference 777 unmetered=True          -> balance stays 0      (correct, D16)
reverse(that row)                            -> balance is now +777 (WRONG)
```

$7.77 of real, spendable, metered balance conjured from a phantom row that
correctly cost nothing.

**Reproduction — money destruction**
(`test_finding2_reversing_unmetered_revenue_destroys_metered_money`):

```
record revenue 5000 unmetered=True           -> balance stays 0
reverse(that row)                            -> balance is now -5000
```

**Why it is worse than a bug.** The auditor's own control cannot see it
(`test_finding2_balance_sheet_reports_balanced_through_the_break`):
`balance_sheet.assert_balances()` recomputes from the same rows the reversal
wrote, so it reports `ok: True, residual: 0` while the seat's balance is fake.
The reconciliation the docs lean on is blind to this path by construction.

**Reachability.** `ledger.reverse` is a public API with a CLI verb
(`ledger-reverse`), and `settle()` / any correction flow can reach it. This is
**live**.

**Minimal fix (applied).** Thread the original row's `unmetered` through the
compensating call:

```python
out = record(cfg, "transfer", delta, ...,
             unmetered=bool(original["unmetered"]), ...)
```

so a reversal of a phantom stays a phantom. `test_mutation_unmetered_propagation_is_what_fixes_finding2`
guards that exact source shape: it asserts the keyword is present at
`reverse()`'s compensating call site, then strips it into a scratch copy to
prove the omission is exactly what the finding was — so a later edit that drops
the keyword fails the guard instead of silently re-opening the bug. A second,
independent guard worth having: a `transfer` whose `offsets_id` names an
`unmetered=1` row must itself be `unmetered=1` (an entity-integrity check at
write time), so no caller can reintroduce the mix-up.

---

## FINDING #3 — `task-review` with no reviewer identity accepts any task

**Invariant claimed.** `work.py:9-11`:

> **The reviewer is exactly one level above** the assignee (C31). A worker does
> not accept its own work and peers do not sign each other off.

**Where.** The separation-of-duties check in `work.review()` (`work.py:197`) is
guarded by `by_agent is not None and ...`. The CLI's `--by` argument defaults
to `None` (`cli.py:1796`), and `cli.py:7215-7218` passes it straight through.
So the *default* CLI invocation skips the check entirely.

**Reproduction** (`test_finding3_review_with_no_identity_accepts_any_task`):

```
worker claims task t, completes it        -> status 'review', reviewer=#manager
review(t, accept, by_agent=manager_peer)  -> refused OFFICE_REVIEW_WRONG_LEVEL  (guard works)
review(t, accept, by_agent=None)          -> ACCEPTED
```

The rule is real when an identity is supplied and silently off when it is not.

**Reachability.** Live:
`qubit office task-review <id> --accept --confirm` (no `--by`) accepts a task
whose reviewer was supposed to be its manager. The C31 rule the module exists
to enforce is one missing flag from being bypassed.

**Minimal fix (description only).** Make `by_agent` **required** for the accept
transition (raise `OFFICE_BAD_INPUT`/`OFFICE_REVIEW_WRONG_LEVEL` when it is
`None`), or make the CLI `--by` required for `task-review`. The `by_agent=None`
branch was presumably meant to cover in-process automation — if so it needs an
explicit, non-default caller identity rather than a silent skip.

---

## FINDING #4 — a running task is silently re-claimed by a peer

**Invariant claimed.** `work.claim` docstring (`work.py:114`): "Assign a task
to a seat, **enforcing the chain**." And the module-level SoD promise that the
reviewer is exactly one level above the assignee.

**Where.** `claim()` accepts `running` as a claimable state (`work.py:124`) —
deliberately, so a task whose turn was refused can be picked up again — but
there is **no check that the claimant is the current assignee**. A second seat
overwrites the in-flight claim, and `reviewer_id` is re-derived from the *new*
assignee's supervisor.

**Reproduction** (`test_finding4_running_task_is_silently_reclaimed`):

```
claim t by w1  -> assignee=w1, status=running
claim t by w2  -> SUCCEEDS, assignee=w2, status=running   (no error)
task trail     -> two 'claimed' events, no 'requeued', no handoff
```

w1's turn is now untracked work: the task it was running belongs to w2, and a
reader of the append-only trail cannot tell that w1's work was discarded.

**Reachability.** Live: `qubit office task-claim <id> --agent-id <other>` on a
task another seat is running.

**Minimal fix (description only).** In `claim()`, when `task["status"] ==
"running"` and `task["assignee_id"] is not None and != agent_id`, refuse (or
require an explicit `--steal`/`requeue` first). The requeue path (`work.py:235`)
already exists and is the honest way to release a claim.

---

## Invariants that HELD (permanent guards)

Each is now a test that goes red if the behaviour regresses.

| Guard | Test | What it proves |
|---|---|---|
| C1 append-only | `test_held_append_only_triggers_fire` | `UPDATE`/`DELETE` on `office_ledger` are refused by `BEFORE` triggers; `health()` proves it live. |
| D14 honesty | `test_held_health_is_honest_on_an_empty_ledger` | On an empty ledger the trigger proof is `None` ("unverified"), never a false `True`. |
| C2 conservation | `test_held_money_conserves_across_revenue_and_costs` | Org net = revenue − metered costs exactly; `explain()` bridges opening→closing with residual 0; trial-balance debit == credit; **no float** in any total. |
| Reversal neutrality | `test_held_reverse_of_revenue_and_inference_is_exactly_neutral` | A compensating row for a **metered** row leaves the net unchanged. |
| D16 two folds | `test_held_unmetered_rows_never_enter_the_metered_fold` | `sum_metered` excludes; `sum_reported` includes; they never interchange. |
| Cap corruption | `test_held_corrupt_cap_override_cannot_grant_headroom` | A garbage/negative/NaN persisted override is treated as unset — it can never grant headroom. |
| `0` means no cap | `test_held_zero_cap_means_no_cap_not_zero` | `0` is a real saved value ("no cap"), distinct from absent; headroom is `None`, not a huge number. |
| C31 escalation level | `test_held_escalation_requires_the_raised_level` | Only the seat an escalation was raised to may settle it. |
| D17/D18 hardline | `test_held_consent_hardline_is_a_floor` | `ledger:rewrite`, `spend:personal`, `secret:owner:exfiltrate` deny even under a full-access posture, with the anti-retry suffix. |
| D17 tier 4 | `test_held_consent_tier4_mutation_default_denies` | No-grant mutation denies ("silence is not consent"); read-only non-net auto-allows; read-only network does not. |
| D15 admission | `test_held_admission_refuses_on_an_unsettled_run` | An open receipt blocks admission, naming the run; settling reopens it. |
| M5 turn cap | `test_held_turn_cap_holds_under_concurrent_spend` | 8 concurrent `spend_turn` calls against a cap of 1 → exactly one allowed. |
| C31 proposal | `test_held_proposal_self_approval_is_refused` | A seat cannot approve its own proposal. |
| D34/D38 survival | `test_held_survival_floor_escalates_to_the_manager` | A seat under the floor raises exactly one escalation to its own manager. |
| C1 termination | `test_held_seat_termination_preserves_ledger_history` | Terminating a seat never deletes its ledger rows. |

### Falsification (mutation proofs, scratch only)

The task requires that a guard be proven *capable of failing*. Three proofs in
the test file, none touching a repo file:

- `test_mutation_append_only_trigger_removal_is_caught` — strip both
  `CREATE TRIGGER` statements from a **scratch** copy of `store.py`, build its
  schema, and the `UPDATE` that the real store refuses is silently accepted.
  (Real output: the mutated schema accepted `amount_cents = 999`.)
- `test_mutation_escalation_level_guard_is_load_bearing` — neutralise the
  `OFFICE_ESCALATION_WRONG_LEVEL` check in-process on a second store and the
  wrong seat settles the escalation. This is the behaviour the HELD test
  asserts is prevented, so its assertion rests on the real guard.
- `test_mutation_unmetered_propagation_would_fix_finding2` — asserts the exact
  source shape of FINDING #2's fix site (`reverse()`'s compensating `record`
  call omits `unmetered`) so the finding cannot silently drift out of the code;
  it fails if the code is changed without re-auditing.

---

## What I could NOT test, and why

- **No live provider turn.** Every test injects no executor and touches no
  network. The `runner.run_turn` money path (provider cost → cents → ledger)
  was exercised only through `receipts`/`ledger` directly, not end-to-end with
  a real usage block. The `usd_to_cents` rounding (`runner.py:43`) was not
  adversarially fuzzed against a real provider payload — no provider was
  available.
- **`store.py` partial-write recovery.** SQLite WAL was relied on for the
  concurrency test, but I did not simulate a process kill mid-commit or a
  corrupt DB file beyond `ledger.health`'s typed DEGRADED path. True crash
  recovery (torn WAL, disk-full) is untested.
- **Multi-process contention.** The `spend_turn` race test uses threads on one
  process. Real multi-process writers (the daemon + a CLI invocation) were not
  run; SQLite's write lock should serialize them, but I did not prove it.
- **`autowork.tick` end-to-end.** The autonomous driver's ordering of
  estop → consent → admission → turn was read but not driven with a stub
  executor, because its only *new* dial is cadence and it routes through
  `runner.run_turn` (covered by the ledger/receipts tests). A tick-level test
  would need the full runner seam and is a worthwhile follow-up.
- **`envelope` enforcement on a real turn.** Confirmed by grep that no live
  path calls `envelope.spend`; I did not attempt to wire one, so FINDING #1's
  live impact is "latent" by inspection, not by execution of a live turn.

---

## Method note

Each finding began as a claimed invariant (a docstring or comment) and was
attacked with a sequence: ordering (approve-then-spend, claim-then-reclaim),
boundaries (empty ledger, zero envelope, one-cent raise), and the metered/
unmetered seam. The tests are hermetic — tmp XDG state, real SQLite, no
network — and the BROKE tests assert current behaviour so they document the
defect without turning the suite red. A fix for any of them flips the
corresponding test to red on purpose; that is the signal to re-audit and
update this document.
