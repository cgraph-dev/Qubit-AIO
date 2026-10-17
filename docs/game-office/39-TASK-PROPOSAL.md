# 39 — The Proposal Layer (the ratchet, first half)

**Status:** specified and implemented. **Not wired to the CLI** — that is a separate,
deliberate step.

The work loop (`27-WORK-LOOP.md`, M2.5) proved the org can *execute* work. But every task in
it is created by the owner: `work.create()` takes a title, a brief and an `exit_check`, and
nothing else in the building can call it. Eleven seats and a working loop make a purely
**reactive** org — one that does anything you ask and nothing you don't.

M5 (`90-MILESTONES.md:472`, *"the ratchet"*) names the missing half. This is that half.

---

## What a proposal is

A **record that a seat wants a unit of work to exist.** It is not work. It carries what a task
carries — title, brief, exit condition — plus the one thing a task does not need: **why**
(`rationale`), because the approver's whole job is to judge the why, and the level above cannot
approve a wish.

It is *not* dispatchable, and that is structural rather than disciplinary: a proposal is **not a
row in `office_task`**. The dispatcher's only source of work is

```python
# voicebridge/office/dispatch.py:159-160
sql = ("SELECT * FROM office_task"
       " WHERE status IN ('open','fixing','running')")
```

so a proposal is invisible to it by construction. There is no flag to forget to check.

### API (`voicebridge/office/task_proposal.py`)

| Function | Does |
|---|---|
| `propose(cfg, proposer_id, title, brief="", exit_check="", rationale="")` | Mint a pending proposal. Returns `{id, status:"proposed", dispatchable: False}`. Refuses an empty title or an empty rationale. |
| `proposals(cfg, *, status="proposed", proposer_id=None)` | Pending proposals by default, with proposer/approver slugs. |
| `approve(cfg, proposal_id, *, approver_id)` | Materialize an ordinary task via `work.create`. Returns `{task_id, status:"open", dispatchable: True}`. |
| `reject(cfg, proposal_id, *, approver_id, reason)` | Mark `rejected` with the reason attached. Reason is mandatory. |
| `approve_level(cfg, proposer_id)` | Who may decide this seat's proposals — read from the live chain, not from `seat_type`. |
| `pending(cfg, proposer_id=None)` | The counts against the caps, for a status surface. |
| `PROPOSAL_LIMITS` | `{"per_seat_pending": 3, "org_pending": 12}`. |

---

## Who may approve what — the org rule, and where it comes from

**The approver is exactly one level above the proposer** — the proposer's `supervisor_id`, read
from the live chain.

This is not a new rule invented here. It is the same rule the org already enforces for
performance and for escalations:

* `11-ORG-CHARTER.md:54-56` — *"Every performance judgment is made by the level immediately above,
  and only that level."*
* `95-CONTRACT.md:653` (C31) — *"the reviewer's level must be exactly one above the reviewed
  seat's."*
* `org.py:57` (`_walk_up`) — walks `supervisor_id` up the tree; this is what defines "one level
  above."
* `escalation.py:328` — `if int(by_agent) != int(row["supervisor_id"]): … raise OFFICE_ESCALATION_WRONG_LEVEL`.
  The proposal layer uses the identical check for the identical reason.

| Proposer | Approver | Why |
|---|---|---|
| worker / analyst / researcher / scraper / support | its **manager** (`supervisor_id`) | the manager owns the unit of work |
| `manager` | its **portfolio director** | one level up |
| `director_portfolio` / `director_functional` | the **CEO** | one level up |
| `ceo` | **the owner** — `approver_id=None` | the owner is not an agent (D2); no seat is above the CEO |

A seat's own `office_report` is a *claim*; only the level above converts it into a record
(`11-ORG-CHARTER.md:66-68`). A proposal is a claim of the same kind, and it is converted by the
same rule.

### The director question, answered deliberately

The task brief suggested a director's proposal "probably needs the owner." **It does not, and it
must not.** A director's supervisor *is* the CEO; routing around the CEO to the owner would:

1. **break the one-level rule** C31 exists to hold — the same "exactly one above" that stops
   review becoming mutual back-scratching;
2. recreate the bottleneck `11-ORG-CHARTER.md:72-76` explicitly removes — *"at 18 departments, the
   CEO's queue is the org's throughput, and the org becomes a proposal machine aimed at one desk."*

`14-RND-FLOOR.md:162`'s *"may not hold a gate"* is about a floor's **incentives** (a floor that
wants interesting ideas must not decide which survive), not about who signs a director's idea. The
CEO is the director's level above; the CEO decides. Only the **CEO's own** proposal has no seat
above it, and that one is the owner's — modelled as `approver_id=None`.

**Refusals, by code:**

| Code | When |
|---|---|
| `OFFICE_PROPOSAL_SELF_APPROVAL` | the proposer tries to approve (or reject) its own proposal |
| `OFFICE_PROPOSAL_WRONG_LEVEL` | a seat that is not the immediate supervisor tries; or the owner is invoked when the supervisor is a seat; or a seat is invoked for a CEO proposal |
| `OFFICE_PROPOSAL_NEEDS_REASON` | a rejection with an empty reason |
| `OFFICE_PROPOSAL_CAP` | a pending cap is reached (the limit is named in the message) |

---

## The two contracts this stays inside

### C17 — "Done" is never the model's own verdict

`95-CONTRACT.md:398`. The proposal layer does not weaken it; it **relocates the gate to
approval**.

* `create` refuses a task with no `exit_check` (`work.py:84-88`, code
  `OFFICE_TASK_NEEDS_EXIT_CHECK`). A proposal may *float* an idea before it knows how to prove
  completion — that is honest, because an idea is not work — but **approval requires the exit
  condition**. `approve()` re-raises `OFFICE_TASK_NEEDS_EXIT_CHECK` for a proposal with none, and
  `work.create` would refuse it a second time regardless.
* Because approval delegates to `work.create`, **the C17 rule from M2.5 applies unchanged** to a
  task born from a proposal. There is no second, weaker path into the queue.

A proposal cannot become work whose completion only its proposer can judge, because there is no
such thing as work without a machine-checkable exit condition — in either path.

### C3 — no second store, no second loop

`95-CONTRACT.md:60`. The proposal layer adds **no new loop, bus, queue, or database**:

* It writes the **same** `office.db` (`store.db_path`), through the same `store.connect` /
  `store.migrate`. There is no `sqlite3.connect` outside `store.py`.
* It adds **no** dispatcher. Approval calls `work.create`; the existing loop then picks the task
  up exactly as it picks up an owner-created one. There is one queue and one loop.
* The new table `office_task_proposal` is created additively by `task_proposal.ensure()`
  (`CREATE TABLE IF NOT EXISTS`) — it does not migrate or alter any existing table.

**Verified:** a proposal is never picked by `dispatch()` before approval
(`tests/test_office_task_proposal.py::test_a_proposal_is_not_dispatchable_before_approval`), and
an approved one is picked exactly like an ordinary task
(`::test_an_approved_proposal_is_an_ordinary_task`).

---

## The per-seat and org caps

`14-RND-FLOOR.md:74-88` already fixes the org's no-backlog rule: *"the floor holds **at most 12
open items.** Past that, intake refuses new drops and says so."* An org that can queue unlimited
proposals is an org that **generates backlog instead of work** — a seat with a fistful of open
ideas has committed to none of them.

So the cap is one rule applied to the org's own intake, not a second rule:

| Cap | Value | Why |
|---|---|---|
| `per_seat_pending` | **3** | a seat must land or withdraw what it already floated before floating more |
| `org_pending` | **12** | the same number `14-RND-FLOOR.md:84` fixes for owner intake |

Both are enforced in `propose()` and refused with code `OFFICE_PROPOSAL_CAP`, **naming the limit**
(`tests/test_office_task_proposal.py::test_the_per_seat_pending_cap_is_enforced_with_the_limit_named`,
`::test_the_org_pending_cap_is_shared_no_backlog`).

---

## Where the existing schema could NOT express the concept

**`office_task.status` cannot express `proposed`.** Its `CHECK` constraint is
(`store.py:326-328`):

```sql
status TEXT NOT NULL DEFAULT 'open'
  CHECK(status IN ('open','running','review','fixing','accepted','rejected',
                   'abandoned')),
```

`'proposed'` is not in that list, and SQLite cannot widen a `CHECK` in place: `CREATE TABLE IF NOT
EXISTS` is a no-op on a table that already exists, so adding the state would require bumping
`store.SCHEMA` and **rebuilding `office_task`** — dropping and recreating a table that holds live
work on every deploy. That cost buys a state the dispatcher must not see anyway.

### The minimal change, and why it was not taken

*If* a future milestone wants the state *inside* `office_task`, the minimal honest change is:

1. add `'proposed'` to the `CHECK` list at `store.py:327`;
2. bump `SCHEMA` (currently `"qubit-office/3"`, `store.py:26`) to `/4`, with a one-time
   `CREATE TABLE office_task_new (…); INSERT … SELECT …; DROP; ALTER RENAME` in `migrate()`;
3. add `'proposed'` to nothing else — the dispatcher's `IN ('open','fixing','running')` already
   excludes it.

**Not taken**, because the proposal layer got the same guarantee structurally and non-destructively
instead: a separate table in the **same store** is invisible to the dispatcher by construction
(it is not in `office_task`), is additive-only, and costs no migration. If the CHECK is ever
widened, the public surface (`propose` / `proposals` / `approve` / `reject`) does not change — only
the storage underneath it moves.

---

## What is deliberately NOT built

| Not built | Why |
|---|---|
| **A seat that approves its own work** | the reason this module exists. Refused with `OFFICE_PROPOSAL_SELF_APPROVAL`; the approver is exactly one level up (C31). |
| **Unlimited backlog** | capped at 3 per seat and 12 org-wide, with the limit named (`14-RND-FLOOR.md:84`). |
| **Auto-execution without an owner-visible record** | approval is a decision by a seat or the owner, and it writes the approver's id, the decision time, and an append-only task-trail event carrying the proposer and rationale. Nothing runs that the record does not name. |
| **CLI wiring** | deliberate. The module is pure and additive, and pointing the CLI at it is a separate step (same as `consent.py` was deferred in M0 until it had a consumer). |
| **A second dispatcher or queue for proposals** | a proposal is not a queue; approval folds it into the one that exists (C3). |
| **A deletion path for a proposal** | `reject` is the only terminal outcome and it keeps the reason (C1 in spirit — the trail is append-only). A mis-typed proposal is superseded by a new one, not erased. |

---

## Tests

```
.venv/bin/python -m pytest tests/test_office_task_proposal.py -q
# 13 passed   (hermetic: tmp XDG state, stubbed executor, no provider call)
```

Proven, each by name:

* `test_a_proposal_is_not_dispatchable_before_approval` — the queue stays empty and no turn runs.
* `test_a_seat_may_not_approve_its_own_proposal` — code `OFFICE_PROPOSAL_SELF_APPROVAL`.
* `test_a_seat_may_not_reject_its_own_proposal_either` — the same code.
* `test_only_the_supervisor_may_approve` — a peer and the CEO are both `OFFICE_PROPOSAL_WRONG_LEVEL`.
* `test_a_director_proposes_to_the_ceo_not_the_owner` / `test_a_ceo_proposal_is_the_owner_only`.
* `test_a_rejection_without_a_reason_is_refused` — code `OFFICE_PROPOSAL_NEEDS_REASON`.
* `test_the_per_seat_pending_cap_is_enforced_with_the_limit_named` /
  `test_the_org_pending_cap_is_shared_no_backlog` — code `OFFICE_PROPOSAL_CAP`.
* `test_an_approved_proposal_is_an_ordinary_task` — `work.tasks()` shows it, the dispatcher picks it.
* `test_a_proposal_without_an_exit_check_cannot_be_approved` — `OFFICE_TASK_NEEDS_EXIT_CHECK`.
* `test_a_proposal_cannot_be_approved_twice` — `OFFICE_BAD_STATE`.
* `test_a_proposal_needs_a_rationale`.

---

## CLI (wired)

The layer is reachable from `qubit office`:

| verb | what it does | gate |
|---|---|---|
| `propose --by N --title … --exit-check …` | a seat files a proposal; **not** queueable | `--confirm`; a proposal with no exit check can never be approved (C17) |
| `proposals [--status proposed] [--by N]` | list proposals | read-only |
| `approve ID [--by N]` | a superior moves it into the real queue | self-approval → `OFFICE_PROPOSAL_SELF_APPROVAL` (rc 2); wrong level → `OFFICE_PROPOSAL_WRONG_LEVEL` (rc 2); `--by` omitted = the owner, valid only when the proposer's superior IS the owner (D2) |
| `reject-proposal ID --reason …` | decline, carrying the reason | `--reason` is required by argparse; a reasonless rejection is refused |

**Exit codes match every other office verb:** `0` on success, `2` on a typed
refusal. A refusal is never returned as `0` — a script or the voice layer must be
able to tell "the org declined" from "the org did it".

**Why `approve` is the only gate that matters.** Everything else in this layer is
additive: a proposal sits in its own table, invisible to the dispatcher, and
`propose` cannot queue anything. `approve` is the single place where a seat could
authorise its own work, so it is the one that carries the chain check and is the
one covered by the negative tests.
