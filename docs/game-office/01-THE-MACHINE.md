# 01 — The Machine

**Read this file and nothing else, and you know what we are building and where it stands.**

Everything else in this directory is detail. This is the whole thing on one page.

---

## What it is, in one breath

**You are a shareholder. The org is a company staffed by AI agents. It has to earn real money to
stay alive, and you watch it work as a game.**

You put in capital. The agents decide what businesses to start, build them, sell things, pay their
own AI bills, and get fired when they can't. You take the profit. You never tell them what to do —
you watch the tower, and you read the reports they send up.

---

## The machine, end to end

```
  YOU
   │ 1. capital, and sometimes an idea (a video, an article)
   ▼
 ┌─────────────────────────────────────────────────────────────┐
 │  THE INTAKE          two doors into the same room           │
 │    • scrapers hunt the web all day        (volume)          │
 │    • you drop a video / article           (depth)           │
 └─────────────────────────────────────────────────────────────┘
   │ 2. every idea must prove itself: cited, deduped, has a mechanism
   ▼
 ┌─────────────────────────────────────────────────────────────┐
 │  THE FUNNEL          most ideas die here, on purpose        │
 │    manager kills ~90% → director picks → CEO funds           │
 └─────────────────────────────────────────────────────────────┘
   │ 3. a funded idea becomes a business with its own floor, team, budget
   ▼
 ┌─────────────────────────────────────────────────────────────┐
 │  THE ORG             5 levels, one CEO, directors per business│
 │    CEO → directors → managers → workers                      │
 │    each level holds a budget envelope and answers to the one above
 └─────────────────────────────────────────────────────────────┘
   │ 4. the business sells something, and the money is real
   ▼
 ┌─────────────────────────────────────────────────────────────┐
 │  THE LEDGER          every cent in and out, append-only      │
 │    an agent that can't prove a number is caught by an auditor│
 └─────────────────────────────────────────────────────────────┘
   │ 5. the numbers decide who lives
   ▼
 ┌─────────────────────────────────────────────────────────────┐
 │  THE REVIEW          quarterly, evidence-only, done by the    │
 │  level above. Two bad quarters and the seat is terminated     │
 └─────────────────────────────────────────────────────────────┘
   │ 6. what survives gets better; what fails gets killed and the
   │    failure is attributed to the gate that let it through
   └──────────────────────► back to the intake, smarter
```

**Six stages. One loop.** The loop is the product.

---

## The game part

You do not play. You are the shareholder watching your company through the glass.

| You see | Because |
|---|---|
| an isometric office, one floor per group of businesses | the floorplan **is** the org chart, not a skin over it |
| agents walk to the room their current task belongs to | the building tells you what they're doing without opening a panel |
| a speech bubble when someone is blocked | the tower never asserts state it can't prove |
| the HUD: burn, runway, who is carrying the company, who is a cost center | ten-second read, no digging |
| a weekly report from the CEO, argued, with the numbers checked against the ledger | an agent cannot be optimistic about money |

**The rule the whole game rests on:** the tower never shows work that isn't happening. No event, no
animation. A beautiful office over a fake economy is the failure every project we borrowed from
already made.

---

## What the seven reference repos became

Every one of them contributes a *part*. None of them is the product — the product is the combination.

| Repo | What we took | Where it lives |
|---|---|---|
| **StarNet** | "the interface never asserts state the harness can't prove"; real ledgers; one question with options; recipes; the OUTBOX; Night Shift | the product law · the ledger · reviews · reusable procedures · deliverables shelf |
| **AI Town** (Generative Agents) | memory stream, importance-scored memories, **three-phase reflection**, conversation→memory | how agents remember · **the R&D digest** · the confessional |
| **Aphae** | goals with deadlines that bend the personality; confessional cam; the mole; rumour propagation; one living doc | profile drift · the weekly journal · **internal audit** · the panel's information asymmetry |
| **PepeClaw** | **task rooms map work to visible space**; demo-mode fallback; provenance vault | the R&D floor's five rooms · honest empty states |
| **Pixel Agents** | one typed boundary for many integrations; layout editor; speech bubbles | five intake kinds, one table · the M7 editor · blocked-agent bubbles |
| **Agent Quest** | activity → building mapping; a village you glance at | why a room is a real scope, not decoration |
| **agent-monitor** | per-session policy, run-now, live activity feed | the manager's override on a stalled department |

**The synthesis none of them has:** all seven draw agents working, or simulate a town, or run one
funnel. **This one runs a company and draws it running** — with real money, real cost, and real
survival pressure, and an org chart you can walk through.

---

## Where it stands right now

| | |
|---|---|
| **Plan** | complete. 29 files, D1–D43, C1–C33, 8 milestones. Internally consistent (verified). |
| **Code** | **M0–M4 built and proven** (2026-10-12). Ledger · money folds · chain of command · owner-attested receipts · caps · consent · the attributed turn · envelopes with raises · escalation on the shared journal · survival and the daily tick · one-level reviews · the business lifecycle with its gates · idempotent revenue · the audit layer · **the Office tower page**. **16 modules / 5,288 lines, 221 tests**, `qubit office` 65 subcommands, plus a sidebar tab at `/qubit-office`. **⚠** three boundaries: this provider reports no usage (paid turns settle `UNKNOWN_SPEND`); **no real payment has been taken** (M3's last proof needs the owner); M4 is a flat plan view — no isometric camera, no sprite art, no WebSocket. |
| **Page in the dashboard** | not yet — the tower is **M4**. It will be a **"Qubit Office" tab** in the sidebar, exactly like Boards and Ops (`55-DASHBOARD-PAGE.md`). |
| **Blocked on** | nothing, for M0. You said "start" — that switch is flipped. |

**"Deploy 6 agents" is not the next step and cannot be.** Six agents before a ledger means six
agents spending money with no record of it, which is the one thing this design forbids. The order is
fixed: the ledger (M0) → **one** agent doing one attributed turn (M1) → the org and its seats (M2).
Six agents is M2, and at that size it costs real money every month (`45-ECONOMY.md`).

---

## What you do next

Say **"start"** → I start M0, the money book. No decisions needed from you, no money spent.

Or say **"change things"** and tell me what's wrong with it.

Three questions are waiting, and **none of them block anything today.** They only come up when a
business first tries to take money:

1. What should the first business be?
2. Where does the money land? (a payment account)
3. Whose legal name is on the sale?

You will answer those when an agent brings you a proposal with a price tag. Not now.
