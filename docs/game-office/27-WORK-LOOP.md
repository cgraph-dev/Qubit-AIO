# 27 — The work loop (inserted milestone M2.5)

**Why this file exists.** `90-MILESTONES.md` defines M0–M8. None of them gives
the org a way to *do anything*. M2 builds the seats and the chain of command;
M3 builds a business; M4 draws the tower — but between "there are eleven seats"
and "the seats produce work" the plan had nothing. That gap is why this
milestone was built out of order, and it is why it is numbered **M2.5** rather
than renumbering the owner's milestones.

Without it, nine hired seats are nine spectators. That is exactly the failure
mode the owner named: *"the agents moving … where is it?"*.

---

## What it is

```
pick a task  →  claim it for a seat  →  check the exit condition
             →  (if already satisfied: stop, spend nothing)
             →  spend one turn of the budget  →  run the turn
             →  check the exit condition  →  review by the level above
             →  accept | reject-with-an-instruction → back to the same seat
```

Files: `voicebridge/office/work.py` (queue, budget), `dispatch.py` (the loop).
Tables: `office_task`, `office_task_event`, `office_turn_budget`.

## The five rules, each enforced rather than hoped for

**1. A task without a machine-checkable exit condition cannot exist (C17).**
`work.create()` refuses with `OFFICE_TASK_NEEDS_EXIT_CHECK` unless
`exit_check` is a non-empty shell command. "Done" is never the model's own
verdict: the command is written by *whoever created the task*, run against real
state, and its exit code alone decides completion. An agent asked to judge its
own completion always complies, which is why it is never asked.

**2. A rejection is an instruction, not a restart.** `work.review(reject)`
requires a non-empty note and refuses without one — *"a rejection without an
instruction is just a delay."* The task returns to the seat that wrote the
thing (never a fresh seat: that would throw away the context that made the
instruction meaningful), with the note attached to the next brief and one
attempt spent. Attempts cap out to `abandoned`, never to a silent pass.

**3. Nobody accepts their own work, and no peers sign each other off (C31).**
`claim()` sets `reviewer_id = the assignee's supervisor`. `review()` refuses
with `OFFICE_REVIEW_WRONG_LEVEL` if any other seat tries. A seat with no
supervisor cannot be given work at all, because nothing could review it (C30).

**4. Silence is never acceptance.** The reviewer's reply must carry a parseable
`VERDICT: accept|reject` line. A reply without one is recorded as `no_verdict`
and the task *stays in review*. Treating an unparseable reply as an accept is
how nine agents produce a green board and nothing that works.

**5. A task is only `accepted` when the level above says so — and the level
above is a turn, not a rubber stamp.**

## States

| state | meaning |
|---|---|
| `open` | created, unassigned |
| `running` | claimed by a seat; a turn has been attempted |
| `review` | the exit check passed; awaiting its reviewer |
| `fixing` | rejected with an instruction; going back to its author |
| `accepted` | the reviewer accepted |
| `rejected` / `abandoned` | attempts exhausted, with the reason recorded |

`running` stays **eligible** for dispatch. A task whose turn was refused or
whose check failed is still in progress, and leaving it out stranded it forever.

## The two bounds (and why dollars are the wrong one here)

**Turn budget** (`office_turn_budget`, `turn-cap`/`turn-budgets`) — the bound
that means something on a flat-rate provider. A subscription-backed turn costs
no marginal dollar, so a *dollar* cap cannot bound it; a seat's turns/day can.
`turn_budget()` reports `unbounded: true` when no cap is set rather than
implying safety, because no cap is not the same as a safe cap.

**The ledger's admission path** still applies, unchanged: no unknown spend
(`complete` must be true), and D1's solvency gate — a seat with a non-positive
balance idles until it earns. A zero-cost turn still has to be funded; that
surprised the implementation and is the rule working, not an obstacle.

## Funding a seat (the honest mechanism)

`transfer` is **reserved for compensating entries** (C1) — it must name the row
it offsets, so it cannot be used to move capital. `revenue` would inflate the
org's top line with money the business never earned. The correct kind is
`adjustment` (signed), used as a double entry: the treasury seat pays out, the
seat receives. Org revenue is unchanged and each seat's balance is real.

## Verify before spending

`dispatch` runs the exit check **before** the turn budget is touched. If the
work is already done — a previous attempt got there, or the owner finished it by
hand — the task goes straight to `review` with `turns_spent: 0`. Paying a model
to rediscover that is the most expensive possible way to read a file. This is
the one place a task completes without spending, and it is the honest kind of
free: **a check that passed, not a charge that was skipped.**

## Proof

`tests/test_office_work_loop.py` — 20 tests, all against a stubbed executor, so
the whole loop is provable without a provider call. `exit_check` is a real shell
command against real files, which is what makes completion machine-checkable.
Hermetic: tmp `XDG_*` only, no network, no live service.

**Live proof (2026-10-12).** Task #1, *"Pin the tower walk model with tests"*,
exit check = the owner's own pytest command. Seat #4 (`wren`) ran a real
provider turn and wrote `tests/test_office_tower_walk.py` (5,228 bytes); the
owner's check returned **exit 0, six tests passing**. The task moved to
`review` with reviewer seat #2 — its supervisor. The turn overran its watchdog
and was killed mid-flight, which left an open receipt: `complete: false` and
every admission in the org refused. That is D15 working. It was settled at 0¢
with the reason recorded, because opencode-go is a flat-rate gateway.

## Open

- `adjudicate` (the reviewer's verdict turn) had not run at the time of writing.
- The org has **no work to do that it can find for itself**: tasks are created
  by the owner. Self-directed task generation is M5's ratchet, not this.
