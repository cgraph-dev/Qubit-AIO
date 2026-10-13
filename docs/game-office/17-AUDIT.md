# 17 — Internal Audit

The org spends real money under the owner's name and reports on itself. This file is how it is kept
honest — not by trusting the agents, and not by suspecting them either, but by **reconciliation**.

The premise: **every agent's incentive points somewhere slightly different from the org's.** That is
not a defect, it is a design constraint, exactly like `per-seat model tier` is. The answer is not
virtue, it is arithmetic the agent cannot edit.

---

## The four ways an agent's numbers lie

These are the only four shapes worth defending against. Each one has a check that is mechanical.

| Failure | How it happens | The check |
|---|---|---|
| **1. Ghost work** | an artifact is claimed in a report or a review, but no run receipt backs it | `office_review.evidence` and every `office_report` claim resolve to an artifact id; an unresolvable id is refused at write time (C30) |
| **2. Cost shifting** | a business that is losing money attributes its burn to another department, or to `intelligence` (the internal cost center) | every `office_ledger` row names `agent_id` **and** `business_id`; a P&L is a `GROUP BY`, never a number an agent writes (C2) |
| **3. Sandbagging** | a manager under-spends its envelope to look efficient, or over-states a risk to justify not shipping | envelope utilisation is a rated metric for managers (`19-REVIEWS.md`); two months of hoarding is noticed in the quarterly |
| **4. Report inflation** | a report claims a metric the ledger does not support — "revenue up" when it is gross, or "profitable" before fees | every report claim is diffed against the ledger before it can be filed (C17); the diff failure is a finding, not a correction |

**Not on this list:** an agent being aggressive, competitive, or profit-maximizing. That is the job
(D25). The audit is about the *numbers being true*, not about the agent being nice.

---

## Who audits — the internal auditor seat

One seat, and it exists from M2.

| | |
|---|---|
| Seat | `auditor` — a `director_functional`-tier seat reporting to the **owner** (dotted) and to the CEO (administratively) |
| Cost | one best-tier turn per business per period. At 9 businesses, ~9 turns/period — the cheapest control in the plan |
| Authority | reads everything, writes findings, changes nothing. **It cannot kill a business, block a spend, or alter a record** |
| Independence | it does not report into the chain it audits. A manager cannot rate the auditor; the auditor's rating is written by the CEO with the owner's countersign |

An auditor with authority to act becomes a second CEO. An auditor with no authority is a reporter.
This one is a reporter whose findings are **on the record** — which is what makes them act.

---

## Four controls

### A. Reconciliation (continuous, automatic)

Every displayed total is recomputed from the ledger and diffed against the stored view (C2, already
in force). The audit adds one line: **a diff is never silently repaired.** It is recorded as a
finding, the view is corrected from the ledger, and the business that produced the wrong number is
sampled next period. Silently fixing a wrong number is how an org loses the ability to notice them.

### B. The spot audit (per period, sampled)

Once per period the auditor picks **one business at random** and asks three questions:

| Question | Evidence required |
|---|---|
| did it do what its reports say? | reports vs run receipts vs the deliverables shelf |
| did it spend what the ledger says? | ledger rows vs the department's own account statements (where a platform provides them) |
| does its P&L survive a fee-and-refund pass? | gross → net, and the refund rate, which is the number agents most often leave out |

The sample is not announced in advance, and the *selection* is not fixed by anyone in the chain —
the auditor draws it from the period's business list.

**Why sampling rather than auditing everything:** full audit is unaffordable at 18 departments and
unnecessary. Intermittent audit is enough, because the agent does not know which period is its
period. (The mechanism is from `rsanandres/aphae`'s mole — the social version of a spot audit. We
keep the mechanic and drop the drama: no voting, no exile, just a finding.)

### C. Separation of duties

| Rule | Why |
|---|---|
| the agent that **approves** a spend is not the agent that **records** it | a controller that also writes the ledger can make anything true |
| the agent that **runs** a business does not **rate** itself (`19-REVIEWS.md`) | self-assessment is not evidence |
| the agent that **scrapes** a signal does not **pass** it through a gate (D31) | its incentive is volume |
| the **auditor** reports outside the chain it audits | it must not be rated by the people it checks |
| the **CEO** proposes; the **owner** disposes, on anything naming the owner | the owner is the only counterparty (D2/D23) |

### D. The penalty ladder

| Finding | Consequence |
|---|---|
| first, minor (a rounding claim, a missing receipt) | recorded; the report is corrected forward, never edited (C1) |
| repeat, or a pattern across a manager's reports | the manager's quarter is capped at `below`; the department is sampled again next period |
| intentional misuse of the ledger, a false report, or a spend with no artifact | the seat is terminated and the finding is reported to the owner. **This is the only finding the owner is notified of immediately.** |
| an auditor that misses a finding three periods running | the auditor is rated on it. A control that never fires is a control that is not there |

**Deliberately missing:** no finding ever results in a ledger edit. The fix is always a compensating
row (C1). A punishment that rewrites history destroys the only evidence there was.

---

## What this adds to the schema

| Table | Purpose |
|---|---|
| `office_audit_finding` | `{id, ts, period, business_id, kind, severity, evidence_json, status, closed_ts}` — append-only |
| `office_agent.auditor_of` | which businesses this seat may sample (the auditor's scope) |

And two columns that make the checks cheap:

- `office_report.ledger_diff` — the result of diffing the report against the ledger at write time:
  `clean` \| `mismatch:<field>`.
- `office_ledger.recorded_by` — the seat that wrote the row, which is what makes separation of duties
  checkable rather than aspirational.

---

## Provenance

| Idea | From | What we changed |
|---|---|---|
| The mole / suspicion mechanic | `rsanandres/aphae` | became a random **spot audit**; no voting, no expulsion, findings not drama |
| Real ledgers shown as-is; the interface never asserts unprovable state | `androoAGI/starnet` | already the product law (`00-IDENTITY.md`); here it becomes the audit's first question — *can the report be proved?* |
| Explicit capability and consent checks on every tool call | `starnet` | supplies the `recorded_by` hook: who did it is already known at the call site |
| Live runtime state as the only thing rendered | `starnet` | the deliverables shelf (OUTBOX) is the artifact the auditor reads instead of a transcript |
| `aphae`'s secrets ("deflect on the floor, admit in the booth") | `aphae` | inverted into the owner-facing confessional (`11-ORG-CHARTER.md`); the audit is the arithmetic, the confessional is the narrative |
| Interactive review of many concurrent sessions | `Agent-Quest`, `agent-monitor` | the sampling surface: the auditor needs one screen that lists businesses and a sample button |

---

## Why this is the highest-value file in the set

The plan's central claim is that the org's numbers are real. Without reconciliation, separation of
duties, and a sampling audit, that claim rests on eight or nine agents choosing to be accurate —
which is not a control, it is a hope. This file is what makes `00-IDENTITY.md`'s success sentences
(especially "every dollar of that bill is traceable") **checkable by a third party** rather than
asserted by the org.
