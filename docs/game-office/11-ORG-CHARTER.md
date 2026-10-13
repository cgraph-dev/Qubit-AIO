# 11 — Org Charter

The corporate structure: who reports to whom, who decides what, and where the money stops.

`12-DEPARTMENT-CATALOG.md` says **what businesses exist**. This file says **who runs them and who is
accountable for what**. `19-REVIEWS.md` says **how performance is judged**. `17-AUDIT.md` says **how
the org is prevented from lying to itself.**

---

## The shape

```
                         OWNER  (outside the org — shareholder, legal counterparty, off-switch)
                           │  capital in, reports up, profit out
                           ▼
                          CEO  (1)
        ┌──────────────────┼──────────────────────────┐
        ▼                  ▼                          ▼
  DIRECTOR          DIRECTOR (FUNCTIONAL)        DIRECTOR
  (portfolio)       Marketing / Engineering /    (portfolio)
  owns a P&L        Finance / Legal / Ops        owns a P&L
        │           advisory only, no veto             │
        ▼                                             ▼
     MANAGER  (one business unit each)             MANAGER
        │                                             │
        ▼                                             ▼
  WORKER · ANALYST · RESEARCHER                 WORKER · SCRAPER
```

Five levels inside the org. The owner is a sixth position **outside** it: not in the chain of
command, party to everything, reporting from nobody (D2/D24).

| # | Level | Seat | Owns | Reports to | At full corp (D21) |
|---|---|---|---|---|---|
| 0 | — | owner | capital, legal entity, off-switch | — | 1 (outside the org) |
| 1 | C-suite | `ceo` | the whole org; capital allocation and sequencing | owner | 1 |
| 2 | directorate | `director_portfolio` | one portfolio of businesses **and its P&L** | CEO | 5 |
| 2 | directorate | `director_functional` | one discipline across the org — **advisory, no P&L, no veto** | CEO | 5 |
| 3 | management | `manager` | one business unit (department) | its portfolio director | 9 |
| 4 | execution | `worker`, `analyst`, `researcher`, `scraper`, `support` | their assigned tasks | their manager | 55–70 |

**Two hard structural limits:**

1. **One named supervisor per seat.** `office_agent.supervisor_id` is mandatory for every seat except
   the CEO's (the owner's). There is no seat in the org without an accountable superior — an agent
   with no supervisor is an agent nobody can rate, and rating is how an org corrects itself.
2. **A manager carries at most 7 direct reports; a portfolio director at most 7 managers.** Exceed
   the span and the unit splits. This is not an HR convention, it is a legibility limit: past seven,
   a manager reviews on vibes rather than on artifacts, and the weekly one-on-one stops being real.

---

## The structural principle: you are reviewed by the level above

Every performance judgment is made by the level immediately above, and only that level.

| Who is judged | Judge | Record |
|---|---|---|
| worker / scraper / analyst | their manager | `office_review.verdict` |
| manager | their portfolio director | `office_review.verdict` |
| director (both kinds) | the CEO | `office_review.verdict` |
| CEO | the owner | `office_report` + owner ruling |
| functional director | CEO **and** every portfolio director it advised | advisory record, not a verdict |

**Nobody reviews themselves. Nothing in this org is self-assessed.** An agent's own
`office_report` is a *claim*; only the level above converts a claim into a record. This is the
same law the ledger already enforces for money (C1/C2), applied to performance.

---

## Budget envelopes — the thing that makes it scale

Today the CEO gates every funded idea. That is a single point of failure: at 18 departments, the
CEO's queue is the org's throughput, and the org becomes a proposal machine aimed at one desk.

**Fix (D37): every level holds a monthly envelope, allocated by the level above.**

```
owner          →  sets the org envelope (the only capital that comes from outside)
   CEO         →  splits it into per-portfolio envelopes + one org reserve
      director →  splits its portfolio envelope into per-department envelopes
         manager → spends its department envelope
```

| Rule | Detail |
|---|---|
| **Inside the envelope** | a level spends without asking, and only **reports** it. A manager rebranding a storefront, a director refilling a running business — no approval, no wait. |
| **Above the envelope** | it raises to the level above with the same records a review needs: the amount, the thesis, the expected return, what it displaces. |
| **Unspent is not lost** | an envelope accumulates up to one month, so an agent can save for a larger bet. Two months unspent and a third is noticed in the quarterly review — a team that hoards is a team that is not deploying capital. |
| **Reserve** | the CEO keeps an unallocated org reserve (~20% of the org envelope) for the fast-track case: a time-limited market, a regulatory deadline. |
| **Overspend** | impossible as a silent act: the runner refuses at the cap (C8), and a repeat attempt inside one period is a finding for the internal auditor (`17-AUDIT.md`). |

**This is the whole answer to "one CEO but multiple directors for each business".** The CEO does not
adjudicate eight departments' worth of small decisions; it allocates envelopes, sequences the
org, and argues the portfolio to the owner once a week. Directors spend inside their envelope and
decide which businesses in their portfolio live and die. Managers run one business and are rated on
its P&L.

---

## The review chain — requests escalate; they do not park at the owner

`57-BOARDROOM.md` defines `office_review` ("is this good?"). As written, every review request
defaults to the owner. At scale that recreates the bottleneck the funnel just removed.

**Fix (D38): a review request is addressed to the correct level in the chain first.**

| The question | Goes to | Escalates to the owner only when |
|---|---|---|
| is this artifact correct / complete? | the agent's **manager** | never — a manager's call is final |
| is this good enough to ship? | the **director** that owns the portfolio | the artifact will publish publicly (D22 mandatory review) |
| is this worth funding? | the **director** (inside envelope) or **CEO** (above envelope) | the amount is above the CEO's discretionary limit |
| is this legal / does it name the owner? | **Director of Legal Liaison** prepares the package | always — the owner is the counterparty |

The owner's inbox receives **only**: capital above the CEO's limit, anything publishing publicly,
anything that names the owner as a party, and the weekly CEO report. Everything else has a home in
the chain, and every escalation carries the level-below's reasoning so the owner reads conclusions
rather than raw traffic (D24).

---

## Authority — one table, no ambiguity

| Decision | Made by | Reviewed by | Owner sees it |
|---|---|---|---|
| found a department | portfolio director proposes, CEO funds | CEO | only if above the CEO's limit |
| kill a department | portfolio director | CEO | in the weekly report |
| hire / fire a worker | manager | director | no |
| hire / fire a manager | portfolio director | CEO | no |
| hire / fire a director | CEO | owner | yes |
| change a risk model (D21) | CEO proposes | owner | yes — always |
| publish anything public | any seat | director, then owner (D22) | yes — always |
| spend inside envelope | that level | level above (in the quarterly) | yes — in the ledger, not as a request |
| spend above envelope | level above | next level up | above the CEO's limit only |
| sign anything naming the owner | nobody alone | owner, per instance | yes — always |
| create an outbound account | manager proposes | director, owner stages (D23) | yes — always |
| stop the org | owner | — | it is the owner's act |

---

## Culture, as mechanics (not decoration)

An org of agents is only worth watching if the agents are different from each other and can be
seen to change. Four mechanisms, each traceable to a source:

**1. Goals with deadlines and outcomes.** Every seat carries an `office_goal`: what it wants, by
when, measured against an artifact. A goal that lands **bends the agent's profile**; a near miss
buys one extension; a resolved goal makes room for a new one or the agent stalls. *(from
`rsanandres/aphae` — "goals that resolve … landing one bends the personality that earned it.")*

**2. The confessional.** Once per week, each seat writes one private paragraph — first person, in
its own voice — about what it did, what went wrong, and what it wants. It is not a report, it is not
scored, and the owner is the only reader. *(from `aphae`'s confessional cam.)* Its real function is
diagnostic: the owner hears about a failing business from the agent running it before the numbers
make it undeniable.

**3. The audit reel.** The "one mole" mechanic from `aphae`, run as internal audit rather than
social deduction. Once per period the **internal auditor** (`17-AUDIT.md`) samples a business and
checks whether the story matches the ledger. Nobody knows which business is sampled. The point is
not to catch a liar, it is that **a system under intermittent audit keeps its reports honest** —
the same reason a real company reconciles cash without suspecting its cashiers.

**4. The daily reflection.** Already in the plan (D4/D5, `60-EVOLUTION.md`). Keep it, and keep it
strictly separate from the *performance* review: the reflection changes the agent's **method**, the
review changes the agent's **standing**. Merging them would let an agent's confidence about its own
improvement stand in for evidence of it — exactly the failure the ratchet boundary (D5) exists to
prevent.

**Recipes carry the lessons** *(from `starnet`)*: when a business proves a sequence of steps that
works, it is promoted to a **recipe** — a versioned, reusable procedure the next department starts
from. Recipes are how the org gets better at business, rather than each department re-deriving
print-on-demand from nothing.

---

## Provenance

| Idea | Taken from | What we changed |
|---|---|---|
| A room is a capability-scoped team; a placed object is a real grant; the layout *is* the workflow | `androoAGI/starnet` | already in the plan as object=capability (`75-EDITOR.md`); reused here as the reason a floorplan edit is an org mutation |
| Bounded concurrent runs, each with its own workspace and permissions | `starnet` | becomes one seat = one isolated run per turn, per department |
| Asks before it guesses: one question, explicit options | `starnet` (Task Briefs) | becomes the `office_review`/`office_request` format (`57-BOARDROOM.md`) |
| Night Shift: agents work inside an explicit, adjustable leash; every away-action logged | `starnet` | becomes the "org works overnight, every action lands in the ledger, nothing happens unseen" posture (D28 + C1) |
| Finished work gets a front door, not scrollback | `starnet` (OUTBOX) | becomes one deliverables shelf per department; the manager's weekly review reads the shelf, not a transcript |
| Recipes: proven multi-step procedures as reusable assets | `starnet` | becomes the recipe promotion path above |
| Goals that resolve, with a deadline that bends the personality | `rsanandres/aphae` | becomes `office_goal` + profile drift (D41) |
| Drama Director / storyteller pacing events | `aphae` | becomes a bounded set of org events (merger, audit, market shock) that land as records, never as scripted animation |
| Confessional cam | `aphae` | becomes the weekly private journal, owner-read only |
| Secrets / mole | `aphae` | becomes intermittent internal audit (`17-AUDIT.md`) |
| Roles walk to the building their activity implies | `FulAppiOS/Agent-Quest` | becomes the desk↔room walk, already in `40-RUNTIME.md`; reinforces that a room is a real scope, not a skin |
| Shared world state, agent conversations, relationship drift | `a16z-infra/ai-town` | becomes `office_message` (already in the schema) as the gossip substrate between agents, and the relationship graph behind the confessional |
| Autowork ticker with per-session policies and a run-now action | `ruiqili2/agent-monitor` | becomes the manager's "run this now" on a blocked department, bounded by the same caps |
| Office layout editor persisted across windows | `pixel-agents` | becomes M7, already in scope (`75-EDITOR.md`) |
| `aphae` code | **none** — All rights reserved | ideas only; no code, no assets, no sprites (`80-RISKS.md`) |

---

## What this changes elsewhere

- `30-DATA-MODEL.md`: `office_agent.supervisor_id`, `office_goal`, `office_review` (already exists,
  gains `level`, `period`, `verdict`), `office_envelope`, `office_audit_finding`.
- `13-PIPELINE.md`: gate 3 (CEO) is reached only for ideas **above the portfolio envelope**; inside
  it, a director funds without the CEO's gate.
- `45-ECONOMY.md`: one new line per level — envelope size, and who sets it.
- `90-MILESTONES.md`: M2 gains the supervisor edge and the envelope; M3 gains the first review only
  after the first real dollar.
- `17-AUDIT.md`, `19-REVIEWS.md`: new, and the load-bearing half of this file.
