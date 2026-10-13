# 13 — The Intake Pipeline

Where ideas come from, and the three gates an idea has to survive to become a business.

Before this file the org had no intake: a scout "proposed" from nowhere, which meant a proposal had
no provenance, no volume control, and no way to tell a good scout from a loud one. This fixes that.

---

## The funnel

```
SCRAPING FLOOR          raw signals. volume play. no judgment.
   │
   │  gate 0 — self-check: cited? deduped? above the quality floor?
   ▼
MANAGER                 filter ruthlessly. ~90% die here, each with a written reason.
   │
   │  gate 1 — the manager's pass. Survivors only cross.
   ▼
DIRECTOR                decide which become businesses, assign a portfolio,
   │                    commission the full thesis. Functional directors consulted.
   │
   │  gate 2 — the director's pick + the thesis bundle.
   ▼
CEO                     allocate budget, sequence against the org's capacity.
   │
   │  gate 3 — funding + priority.
   ▼
OWNER                   capital approval (the existing D22/D24 path).
```

**The funnel is a filter, not a queue.** Nothing waits for the owner until it has survived all
three gates. A scraper that finds 200 signals produces maybe 18 manager passes, maybe 4 director
picks, and 1–2 funding requests. That compression is the whole design — it is what keeps the owner's
inbox from becoming the org's bottleneck.

---

## The second way in — the owner's material (D42)

The funnel has **two intakes, one gate structure.**

```
  path A — AUTONOMOUS          floor 0 (intelligence)     scrapers, 200 signals/day, volume play
  path B — OWNER-FED (D42)     the R&D floor (rnd)        one item at a time, depth play
                                          │
                                          ▼
                        gate 0 — THE SAME GATE, no shortcut
                                          │  cite? dedupe? mechanism? permitted-use?
                                          ▼
                                    ... the funnel as below
```

An owner-fed playbook becomes an `office_idea` row with `source_channel='owner_intake'`, carrying the
same mandatory provenance as a scraped signal. **It gets no bypass.** The owner's material is treated
as authoritative about *what the material says* and irrelevant about *whether the business is good* —
the org must read it faithfully and is still free to kill the idea, with a reason, in writing.

`14-RND-FLOOR.md` holds the mechanics (the five rooms, the three-phase digest, the panel, the
no-backlog rule).

**Two intakes means two sets of numbers.** Every funnel metric splits by `source_channel`: the
manager's pass rate on scraped signals vs on owner-fed playbooks, and how many of each reach revenue.
That comparison is the only honest answer to *"is the owner's taste better than the firehose?"* —
and it is expected that **owner-fed items pass at a higher rate and are still not all good.**

---

## Floor 0 — the scraping floor

Reserved floor for intake. Highest volume, cheapest tier, no decision authority.

| | |
|---|---|
| Floor role | `intelligence` |
| Departments | scraping, market intel, research, analytics (all `intelligence` kind) |
| Seats | `scraper` (volume), `analyst` (structure), `researcher` (depth) |
| Model tier | `scraper` = cheap; `analyst`/`researcher` = best |
| Authority | **none.** They find and document. They do not rank business value. |

**Why scrapers get no judgment authority:** a scraper's incentive is volume. Give it a vote on
business value and it will find 400 "opportunities" a day. It collects; other people decide.

**Gate 0 — the self-check** (enforced at write time, not by review):
- a **citation** for every claim (source URL + when fetched)
- **dedupe** against existing ideas and live businesses
- a **quality floor**: the signal must have a stated mechanism ("why does this make money"),
  not just a trend observation
- **permitted-use check** on the source (D29 item 6 — if the source's terms forbid it, it does not
  enter)

## Floor 1 — managers

Existing `manager` seats, in their department rooms. This is the ruthless gate.

**Ruthless-first (owner's ruling):** managers kill **~90%**, and a kill requires a written reason
from a fixed vocabulary:

| Reason | Means |
|---|---|
| `no_mechanism` | the idea has no stated way to make money |
| `saturated` | the market is served; we bring nothing |
| `no_edge` | we would be one of a thousand doing it identically |
| `uncapitalised` | real money needed before any revenue |
| `outside_scope` | not something this org does |
| `bad_source` | the evidence is weak or unverifiable |

**A kill is not a delete.** Killed ideas stay with their reason, and that record is what teaches the
scrapers: repeated `no_mechanism` verdicts against one scraper's output is a ratchet signal against
its search patterns.

**Filtering conservatism is not a virtue here.** A manager who passes everything is not generous,
it is passing work up the chain to somebody else's desk — and the director's pick rate is what
exposes it (see *Metrics*).

## Floor 2 — directors

Two kinds, both on the director floor (owner's ruling: both).

### Portfolio directors — the structural decision-makers

Each owns a **group of departments** by `division_kind` or theme:

| Director | Portfolio |
|---|---|
| Director of Product | all `product` departments (print-on-demand, dropship…) |
| Director of Content | all `content` departments (ebooks, courses, writing) |
| Director of Services | all `service` departments (agency, freelance, research services) |
| Director of Capital | all `trading` departments (forex, prediction markets, arbitrage) |
| Director of Intelligence | the scraping/research/analytics floors |

A portfolio director decides **which surviving ideas become businesses in their portfolio**, and
owns those businesses' P&L.

### Functional directors — the specialists

Own a **discipline across the whole company**, not a portfolio:

Director of Marketing · Director of Engineering · Director of Finance · Director of Legal Liaison ·
Director of Operations

They do **not** own businesses. They are **consulted**: "can this actually be marketed?", "is this
buildable in a week?", "does the unit economics survive payment fees?", "does this need a license?"

**The rule that keeps this from becoming a committee:** a functional director produces a **review
note** (one paragraph, in their lane, with a clear position), and it is **advisory, not a veto**.
The portfolio director decides and must address any objection in their memo. Silence from a
functional director is not consent and not a block — it is simply an unanswered question, recorded.

## Floor 3 — the CEO

Allocates budget and sequence. The question the CEO answers is not "is this a good idea" (the
director answered that) but **"can the org afford to do this now, and what does it displace?"**

The CEO's gate:
- the capacity check — can this be staffed without gutting a running department?
- the sequencing check — does this conflict with or depend on another department?
- the capital request — what it needs, what it should return, when
- **the report to the owner** — the memo (D24), with mandatory `recommendation` and `reasoning`

## Floor 4 — the owner

Capital approval, per D22/D24. The owner sees the funnel's *output*, not its traffic:
"Director of Content wants to found a course business; here is the thesis, the maths, what it
displaces, and what it costs."

---

## Escape hatches (owner's ruling: strict, with two)

The funnel is strict by default. Two exceptions, both auditable:

**1. Bounce.** Any stage may send an idea **back** with a reason, rather than killing it. A bounce
returns it to the *previous* stage with the objection attached. This is what makes the funnel
feedback rather than a series of black boxes — and it is the only way a scraper ever learns what a
manager actually wants.

**2. Fast-track.** Any idea may **skip a stage** if the skipping agent states a justification. The
justification is recorded on the idea, and the skipped stage is notified and may object (which
forces a bounce, not a veto). Examples: a functional director flags a regulatory deadline; a
portfolio director sees a time-limited market opening; the CEO sees a strategic necessity.

**What fast-track is not:** a way to avoid the gates. A fast-tracked idea still faces the gates
below it, and every skip is visible in the funnel's metrics — a director who fast-tracks everything
has simply removed themselves from the process, and that shows up as a 100% skip rate.

---

## `office_idea` — the funnel's unit

Every proposal carries its **full provenance chain**. This is the table that makes the pipeline
verifiable rather than a story.

| column | type | notes |
|---|---|---|
| `id` | INTEGER PK | |
| `ts` | REAL | |
| `title` | TEXT | |
| `origin_agent_id` | INTEGER | the scraper/analyst who found it |
| `source_url` | TEXT | **mandatory** — where the signal came from |
| `fetched_at` | REAL | **mandatory** |
| `permitted_use_checked` | INTEGER | 1 = the source's terms were checked (gate 0) |
| `mechanism` | TEXT | **mandatory** — why this makes money |
| `evidence` | TEXT | JSON: cited facts with sources |
| `stage` | TEXT | `raw` \| `manager_review` \| `director_review` \| `ceo_review` \| `funded` \| `building` \| `dead` |
| `bounces` | TEXT | JSON `[{stage, reason, ts}]` — every bounce, kept |
| `skips` | TEXT | JSON `[{stage, justification, ts}]` — every fast-track, kept |
| `manager_verdict` | TEXT | `pass` \| `kill` + reason from the fixed vocabulary |
| `director_verdict` | TEXT | `pick` \| `kill` + reason; names the portfolio |
| `functional_notes` | TEXT | JSON `[{director, lane, note}]` — advisory, never a veto |
| `ceo_verdict` | TEXT | `fund` \| `defer` \| `kill` + the capacity/sequence reasoning |
| `business_id` | INTEGER | set when it becomes a business |

**Store constraints:** `source_url`, `fetched_at`, and `mechanism` are mandatory — an idea with no
mechanism is not an idea, it is a trend observation, and gate 0 refuses it.

---

## Metrics — the thing that keeps each gate honest

| Metric | Who it judges | Good |
|---|---|---|
| **scraper pass rate** | scrapers | low but rising — a scraper whose raw ideas survive further than average is learning |
| **manager pass rate** | managers | **should be ~10%.** A manager passing 40% is not filtering; they are forwarding |
| **director pick rate** | directors | should be a minority of passes. A director picking everything has no thesis |
| **business survival** | everyone | of funded ideas, how many reach positive P&L — and **which gate let the failures through** |
| **skip rate** | fast-trackers | near zero. A 100% skip rate means the stage does nothing |
| **bounce rate** | the funnel | healthy, not zero — a funnel with no bounces is not giving feedback |

**The kill-learning loop:** when a business dies, the pipeline walks back its chain and attributes
the failure to the gate that let it through. A director whose picks consistently die is replaced. A
manager whose passes consistently die is retrained. A scraper whose survivors die is searching the
wrong places. That attribution is the reason every stage writes a reason.

---

## What this changes elsewhere

| File | Change |
|---|---|
| `30-DATA-MODEL.md` | `office_floor.role` = `executive` \| `director` \| `intelligence` \| `operations`; new `office_idea` table; seat types `director`, `scraper` |
| `50-INTEGRATION.md` | capability table rows for `director` (portfolio) and `director` (functional), and `scraper` |
| `12-DEPARTMENT-CATALOG.md` | the intelligence departments become floor 0's intake |
| `55-DASHBOARD-PAGE.md` | a **pipeline view**: ideas in flight per stage, with the funnel's compression ratios |
| `45-ECONOMY.md` | intake is cheap (scrapers are the cheapest tier); the funnel is what makes a 120-agent org affordable — most agents are filtering, not building |
