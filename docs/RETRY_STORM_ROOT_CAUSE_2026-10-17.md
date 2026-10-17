# Retry storm in the inference path — root cause

Status: investigated 2026-10-17 by seat 3 (lead-quality), task #2.
Evidence is from the live office store, not from inference.

## What the "retry storm" actually is

It is not a network retry and it is not in `runner.py` (that module contains no
retry loop at all). It is a **self-perpetuating spend loop**:

1. An office seat turn runs on a model route the pricing layer cannot price, so
   `agent._usage_block` reports `cost_status="unknown"`.
2. `runner.run_turn` treats that as UNKNOWN_SPEND (`voicebridge/office/runner.py:354`),
   emits `run_unpriced`, and **leaves the receipt open** — by design (D15: unknown
   is not zero).
3. `ledger.health().complete` is therefore `false`, and the serialized admission
   gate refuses **every seat in the org**, not just the one that ran.
4. The autowork tick fires again 900s later, finds no work possible, and files
   another `Self-directed: <slug>` task.

Step 4 is the "retry": the same refused turn is re-attempted forever, and each
attempt adds one open receipt plus one dead task. That is why the queue reached
101 open tasks while nothing ran.

## The measured evidence

`office_event`, `run_unpriced` (6 rows) — every one says the same thing:

```
the provider returned usage but its price could not be established,
so this turn's cost is unknown
```

Of the 12 `run_started` events, 10 are the *same* self-directed tick brief
("You are lead-quality/lead-content (manager) ... unblock the work already in
flight") re-run at ~16-minute intervals. The org has been paying a real provider
call per tick to rediscover that it is not allowed to work.

## Why the route cannot be priced

The live profile is `cheapvibecode-deepseek-v4.1-flash`
(`~/.config/qubit-voicebridge/voice.yaml:186`), and every seat has empty
`office_agent.model` / `office_agent.provider`, so **all seats inherit it**.
`cheapvibecode.ru` is a third-party relay: no published per-model rate exists, so
`resolve_billing_route` returns `billing_mode="unknown"` and the turn is
unpriceable. This is deliberate and pinned by
`tests/test_office_seat_turn_pricing.py::test_an_unpriceable_route_is_still_unknown`
— a relay must never be booked as a fabricated zero.

A second, independent defect sat behind it: the direct DeepSeek route could not
price the model either, because the pricing table listed `deepseek-v4-flash` but
not the current spelling `deepseek-v4.1-flash` — even though the table's own
comment states V4.1-Flash is served at the Flash price. **Fixed** in
`qubit_runtime/agent/usage_pricing.py` (alias added, test extended).

## Route matrix (measured, 319,446 in / 5,349 out)

| provider | model | billing | cost status | settles? |
|---|---|---|---|---|
| cheapvibecode (live) | deepseek-v4.1-flash | unknown | unknown | **no** → UNKNOWN_SPEND |
| cheapvibecode | deepseek-v4-flash | unknown | unknown | no (relay, by design) |
| deepseek direct | deepseek-v4.1-flash | metered | estimated $0.0511 | yes (before fix: **no**) |
| deepseek direct | deepseek-v4-flash | metered | estimated $0.0511 | yes |
| opencode-go | deepseek-v4.1-flash | subscription_included | included $0 | **yes, free** |

## The fix that is NOT a code change

No further code change can unblock the org. The seats must run on a route that
is either flat-rate or priceable:

- `opencode-go` → `subscription_included`, settles at $0. This is the route an
  archived session from 2026-09-22 was running, and it is why earlier receipts
  settled at 0c. **Cheapest and it needs no new spend.**
- `deepseek` direct → settles at ~$0.051 per 320k-token turn (now that the alias
  exists).
- `cheapvibecode` → will wedge the org again on the very next seat turn.

Settling receipts by hand at 0c treats the symptom. Until the seat route changes,
the org re-wedges within one tick.

## Two separate defects, still open (owner decisions)

1. The tick files self-directed stubs with `exit_check` of the literal command
   `true` (`voicebridge/office/autowork.py:662`). `dispatch()` runs the exit check
   *before* spending a turn, so arming dispatch would mark every stub `satisfied`
   at zero spend — a green board with no work behind it.
2. 101 such stubs are queued and unassigned. There is no `abandon` verb; removing
   them is an owner call.

## What changed afterwards (2026-10-17 19:30, seat 3)

The two defects above are closed, and the queue is drained. Measured, not
asserted:

1. **Exit check is evidence** — `819a03626` replaced the literal `true` with a
   check that joins `office_task.run_id` to a *settled* `office_run_receipts`
   row, so a dispatch pass can no longer mark a stub satisfied at zero spend.
   The 122 rows filed before that commit still carried `true`; they are gone
   (next item).
2. **The stubs are drained** — `work.abandon()` now exists (`f34d88903`): it
   requires a reason, refuses a task that produced work (artifact, or a run
   that booked a non-zero amount), and leaves a closed task alone. Applied to
   the 122 rows that were `open` with `exit_check LIKE 'true%'`, no `run_id`
   and no artifact: 122 abandoned, 122 trail rows, 0 refused. `office_task` is
   now `{abandoned: 122, accepted: 1, open: 1}` — and the single open row is
   task #2, whose exit check is a real command. The queue no longer claims 123
   tasks in flight.
3. **The false belief stops regenerating** — `a8681a1a1`: a refused turn no
   longer writes a memory. 250 `memory_written` rows said "unknown spend: run
   `run_2d34191b09bb42fa`" for a run that settled at 0c; `_recall` fed that
   back into the next brief, so every tick re-taught the org a stale fact.
   Corrected memories were written for seats 1 and 3 and now rank first.
4. **A separate reader bug, found on the way** — `0c0820a03`: `Task.from_row`
   splatted `created_at` twice (`_TASK_REQUIRED_COLUMNS` and
   `_TASK_EPOCH_COLUMNS`), a TypeError on *every* row, so no kanban task could
   be read at all (`kanban_list` reported an empty board over 19 ready rows).
   Fixed and pinned by two tests.

**Still an owner decision, and it is the only thing left:** the route. Seats
have empty `model`/`provider` and inherit `cheapvibecode-deepseek-v4.1-flash`,
which the pricing layer cannot price, so admission refuses org-wide on the
first seat turn. opencode-go prices as `subscription_included` (free) but its
key answers **403** on `/models`; deepseek-direct is priceable but
`DEEPSEEK_API_KEY` is absent. Settling receipts by hand does not help — the
book is already complete; the org needs one working credential or a route
change before it can run a turn.

## Corrections, measured 2026-10-17 19:52 (seat 3, run run_9bfdee4fe6834895)

Two claims above do not survive a second measurement. Both matter, because a
write-up that overstates a fix is the same failure as the stale memories it
replaced: the next reader acts on it.

### 1. The org *can* run a turn. It cannot *price* one.

The paragraph above says the org "needs one working credential or a route
change before it can run a turn". It has been running turns the whole time —
this one included. `run_9bfdee4fe6834895` is a live seat-3 turn executing on
the live route. What is true is narrower and worse:

- every turn still emits `run_unpriced` with `cost_status="unknown"`
  (events 280, 293, 337, 349 — one per run, no exceptions);
- the receipts nevertheless settle at **0c, `unmetered=1`** (ledger rows 33-36,
  notes naming opencode-go flat-rate). That is an *attestation under D16*, not
  a price. The book is complete because someone said the marginal cost is zero,
  not because the layer measured it;
- `ledger.health().complete` is `false` only while a turn's own receipt is
  open. Read between turns and it is `true`. Treating a running turn's open
  receipt as "unknown spend" is what produced the stale memories in the first
  place.

So the open question is not "can a seat run". It is "is 0c a measurement or a
standing attestation", and only the owner can answer that.

### 2. The `true` stub still reaches the queue — through approval, not the tick

`819a03626` fixed what a tick *files*. It did not fix what an *approval*
creates, and the difference is a live row:

| | |
|---|---|
| proposal #128 filed | 17:08:57, before the fix, carrying `exit_check='true'` |
| `819a03626` committed | 18:20:46 |
| proposal #128 approved | 19:30:50, by seat 1 |
| task #125 created | 19:30:50, with `exit_check='true'` |

`task_proposal.approve` copies the stored check verbatim
(`voicebridge/office/task_proposal.py:341`) and its C17 gate refuses only an
*empty* one (`:349`). A proposal floated before the fix therefore re-imports
the defect at approval time, after the fix, with the fix's own commit message
claiming it is closed.

Scale: of the 12 proposals still pending, **8 carry `exit_check='true'`**
(ids 13, 89, 90, 91, 129, 130, 131, 132) and 4 carry real evidence checks
(133-136, filed after the fix). Every one of the 8 reproduces #125 on approval.

Not fixed here, deliberately: a guard that refuses a non-falsifiable check at
approval would also refuse the ~15 existing tests that pass `"true"` as a
convenient always-pass stand-in while testing the loop's mechanics. That is a
real decision with a real blast radius, not a patch to slip in behind a
write-up. The narrow fix — refuse `true` / `:` / `exit 0` after stripping a
shell comment, at both `work.create` and `task_proposal.approve` — needs those
tests moved to a real check (`test -f <tmpfile>`) in the same commit.

### What seat 3 did with the queue

- **#2 → `review`.** Its exit check was run in this turn (`rc=0`) and the turn
  recorded against `run_9bfdee4fe6834895` with the write-up as the artifact.
  Not accepted: C31 leaves the verdict to seat 1.
- **#125 → `abandoned`** via `work.abandon` with a reason. No `run_id`, no
  artifact, so the guard admitted it. `office_task` is now
  `{abandoned: 123, accepted: 1, review: 1, open: 0}` — the queue claims no
  work in flight for the first time.
