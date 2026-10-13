# 19 — Reviews and Performance

How the org decides who is doing well, who is not, and what happens next. Real corporate review
mechanics — cadences, ratings, promotion, termination — expressed as records, not vibes.

Read `11-ORG-CHARTER.md` first: this file assumes the five-level chain and the rule that **you are
reviewed by the level above**.

---

## The four cadences

| Cadence | Artifact | Who produces it | Who reads it | Blocks work? |
|---|---|---|---|---|
| **daily** | `office_report` (`kind='daily'`) | every seat | its supervisor | no |
| **weekly** | 1:1 note + department review | seat + supervisor | supervisor, then director | no |
| **quarterly** | `office_review` (`kind='performance'`) | the level above | the level above that | **no — but it is the record** |
| **annual** | portfolio review + ratchet audit | CEO | owner | no |

**None of these block work.** Same principle as D22: the org does not stop to be judged. A review
that blocks is a queue, and a queue is what D19 deleted.

---

## Daily report — the atom

Every seat files one at the end of its last turn. Five fields, all mandatory, all machine-checkable
against the ledger:

| Field | Rule |
|---|---|
| `did` | what it worked on — each item names an artifact or a task id |
| `shipped` | the artifacts it produced, by id. **An artifact claimed and not present is refused at write time** (C17/C30) |
| `spent` | tokens + cents, **copied from the ledger, never typed** (C2) |
| `blocked_on` | what stopped it, and **who** it is waiting on. Empty is allowed and common |
| `next` | what it intends next, in one line |

A daily report with a claim the ledger contradicts is not "optimistic" — it is a **finding**, and it
routes to the internal auditor (`17-AUDIT.md`).

---

## Weekly 1:1 — the supervisor's turn

The supervisor meets each direct report **once a week**, for a bounded exchange:

1. reads the week's daily reports and the deliverables shelf — not the transcript;
2. states one thing that worked, with the artifact that proves it;
3. states one thing to change, with the artifact that shows why;
4. resolves what the report says it is `blocked_on` — either unblocks it or escalates it;
5. writes a **1:1 note**: `{praise, fix, unblocked, escalated}`, three lines, no essay.

**Span limit 7 (D34)** exists for this step: past seven reports, the 1:1 becomes a status read-out
and the supervisor stops being able to name the artifact behind a claim.

## Weekly department review

The manager compiles the department's week for its director: P&L for the week, the shelf, the
blocked list, the burn against envelope. The director's response is one of four, and it is recorded:

| Verdict | Meaning |
|---|---|
| `on_track` | nothing changes |
| `intervene` | the director assigns a specific change, with a deadline |
| `reallocate` | budget moves between departments in the portfolio |
| `sunset` | the department goes to `sunset` (the manager runs the wind-down) |

## Quarterly performance review — the record

The only review that becomes part of an agent's permanent record. Produced by the level above,
**about the period, from artifacts**, never from the agent's own summary.

| Field | Rule |
|---|---|
| `period` | `2026-Q4` — one review per seat per quarter, no more |
| `level` | the reviewing level (`manager`, `director`, `ceo`) |
| `rating` | `exceeds` \| `meets` \| `below` \| `failing` |
| `evidence` | **mandatory**: artifact ids, ledger figures, funnel metrics. A rating with no evidence is refused at write time |
| `notes` | the argument, in one paragraph |
| `action` | `continue` \| `promote` \| `pip` \| `terminate` |
| `ack` | the reviewed agent's one-line acknowledgement, or an appeal (`19-REVIEWS.md` § Appeal) |

**The rating is an output of evidence, not an input to it.** An agent that had a great quarter and
cannot show it is rated `meets`, and that is the correct answer.

### The measurable inputs, by seat

| Seat | Rated on | Source |
|---|---|---|
| `worker` / `support` | artifacts shipped, rework rate, cost per artifact | `office_run_receipts`, `office_ledger` |
| `scraper` | signal quality, not volume — the share of its ideas that survive gate 1 | `office_idea` |
| `analyst` / `researcher` | predictions that held, theses that survived gate 2 | `office_idea` |
| `manager` | the business's contribution margin vs its envelope, **and** its filter rate (~10%) | department P&L view, `office_idea` |
| `director_portfolio` | portfolio P&L, businesses killed on time vs too late, envelope utilisation | portfolio P&L view |
| `director_functional` | whether its advice held up — calls it made that later proved right or wrong | advisory notes vs outcomes |
| `ceo` | org contribution margin, runway, and the accuracy of its own weekly reports | `office_report` vs ledger |

**A note on the functional director.** It has no P&L, so it cannot be rated on money. It is rated on
**the calls it made**: a `marketing` note that said "this will not market" is a win if the business
then failed on demand. This is why the advisory note must carry a *position* rather than an
observation — an observation cannot be wrong, and what cannot be wrong cannot be rated.

---

## Ratings, and what each one triggers

| Rating | Meaning | Action |
|---|---|---|
| `exceeds` | beat its evidence-based target | eligible for promotion or an envelope raise |
| `meets` | hit it | continue |
| `below` | missed it, cause understood | one month of `pip`, same metrics, written plan |
| `failing` | missed it, no recoverable cause | `terminate` after the PIP window |

### PIP — the performance improvement plan

A `below` is not a death sentence, and it is not a mood. It is a contract with a clock:

1. the review records the **specific metric** missed and the **target** to hit again;
2. the agent writes the plan (root cause → change → how it will be measured). A supervisor-written
   plan is not a plan, it is an instruction, and it does not test the agent's judgment;
3. one month, checked at the same cadence, against the same metric;
4. met → `meets`, and the failure stays on the record. Missed → `terminate`.

**Two PIPs in a rolling year is termination regardless of the third quarter's number.** An org that
grants unlimited chances to the same failure is not generous, it is failing to notice.

### Termination — already specified, now sourced

`40-RUNTIME.md` has the runway formula and the death condition. The review chain adds one thing:
**a manager may not terminate a seat its director has not seen in a quarterly review.** The
paperwork is the point — it is what makes the decision reviewable by the owner, and what stops a
manager from shedding a seat to hit its own envelope.

---

## Promotion — the ladder is real

| From | To | Requires |
|---|---|---|
| `worker` | `manager` | `exceeds` twice, plus a funded department to run |
| `manager` | `director_portfolio` | a profitable department, plus a portfolio with no director |
| `director_portfolio` | `ceo` | **never automatic.** The CEO seat is the owner's appointment (D2) |
| any | `researcher` / `analyst` | evidence of thesis quality surviving gate 2 |
| any | `director_functional` | evidence of lane expertise, and 3 correct calls |

**A promotion is a change of seat, and seat type fixes the model tier (D3).** Promoting a worker to
manager is therefore also a raise in inference spend, and the envelope must absorb it — which is why
promotion requires a funded position, not just a good quarter.

**What this fixes:** the current plan has no path between levels. Without one, every agent is
interchangeable and the hierarchy is decoration. With one, the org has internal mobility, and the
quarterly review becomes the mechanism that reveals who should move.

---

## Appeal

Any agent may appeal a rating or a `terminate`, once, within one period of the review.

| Rule | Detail |
|---|---|
| **Who hears it** | the level above the reviewer — a manager's rating is appealed to the director |
| **Grounds** | a factual error: an artifact miscounted, a ledger figure wrong, a metric not applied as specified |
| **Not grounds** | disagreement with the target, or with the strategy behind it |
| **Record** | the appeal and its outcome are appended to the review, never replace it (the same append-only logic as C1) |
| **CEO rating** | appealed to the **owner**, and this is the only review the owner writes |

An appeal that is upheld changes the rating and is itself a **finding about the reviewer** — a
manager whose reviews are overturned is rated on that at its own next review.

---

## What the owner sees

The owner does not read 120 dailies. It sees:

| Surface | From |
|---|---|
| the weekly CEO report | `office_report` `kind='weekly'`, with the org's numbers verified against the ledger |
| the quarterly people summary | every `rating` change, every `pip`, every `terminate`, every upheld appeal |
| the confessional | the weekly private journal — the only first-person writing the owner reads |
| the audit findings | `office_audit_finding` — see `17-AUDIT.md` |

**One standing rule:** the owner may overturn any rating or any termination, and the overturn is
recorded as an owner ruling on that seat. The org is autonomous in judgment (D19) and accountable in
that judgment to its shareholder (D24) — this is where those two meet.

---

## Anti-patterns this file exists to prevent

1. **Self-assessment as evidence.** An agent's own summary is a claim. Only the level above makes it
   a record. (The ledger rule, applied to people.)
2. **Ratings without evidence.** Refused at write time. A rating nobody can check is a mood.
3. **Flat review load.** If the owner is the reviewer for everything, the org has one reviewer and
   no chain — the failure `11-ORG-CHARTER.md` § review chain exists to remove.
4. **Review as a blocker.** Reviews never gate work; money and publishing gate work (D22).
5. **Punitive termination.** Explicit runway, manager notification, the director's sign-off, the
   appeal, and the owner's overturn. Deterministic, not a mood (R11).
6. **Unlimited chances.** Two PIPs in a year, then out.
7. **Promotion without a seat.** A promotion that does not come with a funded position is a title.
