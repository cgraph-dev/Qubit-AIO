# 45 — Economy (real revenue, real cost)

This is the plan's center. Everything else is instrumentation for these numbers.

## The loop

```
capital (owner)  ──►  agent balance  ──►  inference + tools  ──►  artifacts
      ▲                    │                                        │
      │                    ▼                                        ▼
   profit  ◄────  business revenue  ◄────  sale  ◄────  offer published
```

An agent starts with a capital allocation. It burns that allocation doing work. The work produces
artifacts. Artifacts are sold by a business. Revenue refills the agent's balance. If the balance
runs out before revenue arrives, the agent is terminated and its seat is freed.

## Honest unit costs (what the org is actually paying)

Inference is billed per token by the provider. The plan does not use a flat estimate — the runner
records the **real** `usage` block from every provider response into `office_ledger` with
`kind='inference'`. The estimates below are for budgeting only.

| Work | Model tier | Rough cost |
|---|---|---|
| Worker turn (read, edit, test, write) | cheap/fast | $0.004 – $0.02 |
| Manager turn (plan, review, decide) | best available | $0.05 – $0.30 |
| Research turn — `researcher` / `analyst` (Perplexity browser + synthesis) | best available | $0.03 – $0.15 |
| Daily self-improvement reflection | cheap, capped | $0.01 – $0.05 |
| 3D agent asset (Tripo, one-time) | — | ~$0.78 (see `70-ASSETS.md`) |

## Daily run cost, by headcount

**Revised by D21.** These figures were written when the org was modeled as a startup; the owner's
scope is a corporation (D21), so the table is recomputed for a many-department org. The per-turn
estimates are unchanged — only the headcount scenarios are.

Assumes every agent does ~15 worker turns/day, managers do ~10 manager turns/day, and every agent
does exactly one reflection turn (D4). Cheap tier priced at $0.01/turn, best tier at $0.15/turn.

Seats are counted from `11-ORG-CHARTER.md`'s five levels: CEO + directors + managers + execution.
Every figure below is **computed from the price card above**, not estimated — worker turns at the
cheap rate, every management turn (CEO, director, manager) at the best rate, one reflection turn per
seat at the cheap rate.

| Org size | Composition (seats) | Per day | Per month |
|---|---|---|---|
| **Bootstrap** | 1 CEO, 1 manager, 3 workers — **5 seats** | ~$3.45 | **~$105** |
| **First department** | 1 CEO, 1 director, 2 managers, 7 workers — **11 seats** | ~$7.05 | **~$215** |
| **Small corp** | 1 CEO, 3+3 directors, 1 auditor, 5 managers, 17 workers — **30 seats** | ~$20.55 | **~$630** |
| **Corp** | 1 CEO, 5+4 directors, 1 auditor, 9 managers, 40 workers — **60 seats** | ~$34.50 | **~$1,035** |
| **Full corp (D21 ceiling: 9 businesses)** | 1 CEO, 5+5 directors, 1 auditor, 9 managers, 50 workers — **71 seats** | ~$37.50 | **~$1,150** |

**These are before revenue.** A full corporation of nine businesses and 71 seats must clear roughly
**$1,150/month** just to stand still — and that is the number every department's P&L has to beat
collectively.

**What changed on 2026-10-12:** the earlier table (~$20/$57/$210/$870) was arithmetically wrong
against this file's own price card. Working backwards from the old totals at 15 worker turns/day and
$0.01, the best-tier seats would have had to average **0.7 turns/day at bootstrap rising to 3.3 at
full corp** — for seats described as doing ~10. Two errors compounded: the assumption line and the
totals disagreed with each other, and the headcount left out the directors D32/D35 had just added
(plus the auditor from D40). The table above is generated from the assumptions stated in the line
directly above it.

**If an assumption changes, recompute the table. Never adjust a total to keep a narrative.**

**Cost is not the reason to grow slowly.** The reason is that a department on a real account with
an unproven thesis loses an account permanently (D23). Start at bootstrap, prove one department to
positive P&L, then add the next.

**The reflection tax, isolated:** at the full corp (71 seats) ≈ $0.71–$3.55/day
(≈ $21–$107/month, ~$27 at the mid rate). It is the cheapest part of the bill and the part most
likely to compound, which is why it survives.

**Realistic staging** (what the plan actually expects to happen):

| Stage | When | Monthly run cost |
|---|---|---|
| 1 | Bootstrap, one department on test credentials | ~$105 |
| 2 | That department earns its first real dollar | ~$215 |
| 3 | 2–3 departments, at least one profitable | ~$500 |
| 4 | Funded trading desk + scraping floor | ~$1,035 |

Stage 4 should not be attempted before Stage 2 proves the loop, regardless of how good the ideas
are.

## Budget envelopes — who holds what (D37)

The four caps below are the **hard stops**. The envelopes are the **plan** the org spends against,
allocated top-down at the start of each month:

```
owner  →  the org envelope (the only capital that enters from outside)
  CEO  →  per-portfolio envelopes + ~20% reserve
   director  →  per-department envelopes
     manager  →  spends its department envelope
```

Spending inside an envelope is free and is only **reported**; spending above it raises to the level
above with an amount, a thesis, an expected return, and what it displaces. One month of carry-over;
two months of hoarding shows up in the quarterly review as poor envelope utilisation
(`11-ORG-CHARTER.md`, `19-REVIEWS.md`). The envelope never overrides a cap — the runner still refuses
a turn that would break one (C8).

## Budget caps — four keys, owner-dialable (from `starnet/sidecar/budgetcaps.js`)

| Key | Scope |
|---|---|
| `perRun` | one turn — the tightest and the one that saved StarNet from "one simple prompt drained a $10 wallet" |
| `perAgent` | one agent |
| `perDay` | the whole org, per day |
| `global` | lifetime ceiling |

Resolution: `effective = persisted_override ?? default` per key, and the rule that matters —
**`0` is a real saved value meaning "no cap"**, while an *absent* key means "use the default".
Clearing (`null`/`''`) reverts to the default; it does not mean zero.

A stored override is honoured **only if it is a finite number ≥ 0**. Anything else is treated as
not set — *"a corrupt persisted value can never grant unintended headroom."* There is a sane
ceiling (StarNet uses $10M) purely to catch a fat-fingered or overflowing value.

## Admission: unknown spend blocks (D15)

Before any budget check the runner reads ledger health. If any run receipt is `open`, or the
ledger file was unreadable, **every admission refuses** with a typed reason naming the unsettled
run. Unknown spend is not zero, and it is not headroom.

Settlement is manual and owner-attested: the owner states what the provider actually charged,
recorded as `attested_as: 'entered'` (typed from the provider dashboard) or `'limit'` (counted at
the run's own cap). The two are never conflated — a limit booking must not claim to be a real
figure.

## Metered vs unmetered (D16)

| Row type | Metered aggregates (caps, balance, P&L) | Run/token counts |
|---|---|---|
| Paid API turn | counted | counted |
| Subscription-backed turn (e.g. Perplexity Pro research) | **excluded** | counted |
| Owner-supplied free tier | **excluded** | counted |

So the scout floor's true marginal dollar cost is near zero, and the HUD must show it that way.

## Cost controls — enforced by the runner, not by convention

1. **`budget_cents_day` per agent.** The runner checks the agent's spend before each turn and
   refuses the turn past the cap with a typed response. Not a soft warning.
1b. **`perRun` cap.** Every turn carries its own ceiling, so one pathological prompt cannot drain
   an agent's balance before anyone notices.
2. **`OFFICE_DAILY_BUDGET_CENTS` org-wide.** When hit, the org stops starting new work and
   finishes what is in flight. It does not silently keep spending.
3. **Tier pinning (D3).** The cheap tier is the default; a best-tier turn requires the agent to
   occupy a best-tier seat. There is no "just this once" escalation path in the runner.
4. **Scout concurrency cap.** Perplexity is free, so the risk is not per-query cost — it is
   having 40 scouts each burn a best-tier synthesis turn on the same question. Cap concurrent
   scouts and require a manager to approve a new discovery wave.
5. **Reflection is one turn per agent per day.** Hard-coded cadence, no "reflect again" path.

## Revenue — what the org actually sells

The org sells **digital output produced by agents**, in this order of how fast it can start:

1. **Services** — deliverables under the owner's name (research, audits, small builds). Fastest
   to first dollar, lowest capex, highest per-unit margin variance.
2. **Digital products** — templates, tools, datasets, generated assets where the license permits
   resale. Slow to first dollar, near-zero marginal cost at scale.
3. **Software** — the same kind of thing this plan describes: a small tool that solves a real
   problem, priced and sold. Highest ceiling, highest cost to build.

**Not sold:** anything requiring the owner to personally be the product, anything with an
unbounded liability surface, and anything whose sale would put the owner on the wrong side of a
platform's terms. The owner handles legal and compliance, so every business thesis must state
what its regulatory exposure is — an empty exposure field blocks `status = live`.

## The money gate — now a posture, plus two mandatory reviews (D19→D28 supersedes D7; D22 adds reviews)

Under the default posture (`unattended_full`) **nothing stages** — agents spend from their own
balance and act. The per-run cap, the hardline floor, and the agent's balance are the only bounds
(D19/D28).

**Except the two mandatory reviews (D22)**, which apply under every posture: publishing anything
public, and spending above the department's allowance. Those are staged because they are the two
irreversible acts — money leaving, and something going public under the owner's name.

Under `supervised` (a new agent's first days, or an agent being reined in), the additional triggers
stage too:

| Trigger | Why |
|---|---|
| Spend above the agent's allowance | It is the owner's money |
| Create a financial or legal commitment (contract, invoice, subscription, account signup) | It binds the owner |
| Publish under the owner's name (post, listing, email, site) | Reputation is the owner's |

Implementation: the existing confirmation journal. Agent action → `409 NEEDS_CONFIRMATION` with a
real `confirmation_id` → the owner settles it from the voice dashboard, the approvals banner, or
the tower. One journal, one settle path, same as the CLI and voice (D22, and the repo's existing
contract).

**Approval has a cost.** Every staged action records what it was *for* (business, expected
value). An agent whose staged actions get rejected more often than approved is burning capital on
proposals nobody wants — that is a signal the manager sees, and eventually the ledger does too.
That signal is the reason `supervised` exists as a *recoverable* posture rather than a punishment:
it is how the org brings an agent back after a bad week.

**The audit moves to the ledger.** With prompts off, nobody approved anything, so the ledger and
the event stream are the entire record. Every turn already writes a run receipt (D15) before it
starts and a ledger row (with cost, model, artifact, and run id) when it lands. "What did the org
do last night" therefore has a checkable answer that does not depend on anyone having been asked.

## Attribution — the thing that makes "smarter" measurable

Every artifact carries: `agent_id`, `run_id`, `business_id`, `cost_cents`, `revenue_cents`.
Attribution is what turns the ratchet (D5) from a story into a number. An agent can only claim a
ratchet entry if the entry names the artifacts it came from and those artifacts have ledger rows.

## Who pays for what

| Cost | Paid by |
|---|---|
| Inference | The agent that consumed it, from its own balance |
| Shared tools/infra | Split across departments by usage, recorded as `transfer` rows |
| Fixed fees (hosting, subscriptions, domains) | Owner, recorded as `fee` rows, optionally recharged to a department |
| Tripo asset generation | Owner at first (capex); later recharged to the department that needed the art |
| Legal and compliance | Owner, forever, never charged to the org |

The last line matters: legal cost is the owner's, because the owner is the counterparty. Charging
it to the org would make compliance look like a department's overspend instead of a cost of doing
business.
