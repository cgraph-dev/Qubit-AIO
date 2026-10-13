# 57 — Boardroom, Decisions, and Requests to the Owner

Added because the plan modeled the owner's involvement as *a red marker on a desk* — a
notification. That is wrong. Decisions happen in a place, and the plan needs that place.

This file also reclassifies the four "M3 questions": two of them are not decisions the org makes
at all, they are **capabilities only the owner can provide**, and the org must be able to *ask*
for them in a way that blocks visibly instead of stalling silently.

---

## Two new room kinds

Added to the `kind` enum in `30-DATA-MODEL.md` (`office_room.kind`):

| Kind | Purpose | Occupants at a time |
|---|---|---|
| `boardroom` | Owner-facing decisions: capital allocation, business go/no-go, anything requiring the owner | CEO + the proposing manager + (remote) the owner |
| `review` | Internal decisions: a manager ruling on their own team's proposals, no owner needed | Manager + proposers |

A department has at most one `boardroom` (it is the CEO's room, on the executive floor) and may
have any number of `review` rooms. Both are ordinary rooms for rendering purposes — they are
places an agent walks to and sits in, and their `office_event` rows are `kind='meeting'`
(added to the event map in `50-INTEGRATION.md`).

**Why a room and not just a list.** A queue of pending approvals tells you *what* is waiting. It
does not tell you *who is waiting on it*, *what it costs to wait*, or *which decisions are stuck
behind other decisions*. A boardroom with named participants and a visible agenda does.

---

## Two classes of owner-facing item

These are different things and the plan was conflating them (D7 covers both; they behave
differently).

### Class A — Approval (the org can proceed either way)

An action is ready and only needs a yes/no. The org keeps working on other things meanwhile.

Examples: publish this listing, spend $40 on this ad test, sign this subscription, raise this
price.

- Mechanism: existing confirmation journal (`409` + `confirmation_id`).
- Consequence of no answer: **the action does not happen**; the org continues elsewhere.
- Rendered: red marker on the proposer's desk + the floor plate + the boardroom agenda.

### Class B — Capability request (the org is stuck)

The org has reached a point where **no agent can proceed**. Not "may not" — *cannot*. The
capability lives with the owner (their account, their identity, their money, their legal person).

Examples: the payment rail, the legal entity on the sale, which Tripo product line the credits are
on, opening a merchant account.

- Mechanism: a **blocking decision request** — a new record, not a journal entry (below).
- Consequence of no answer: **named work is blocked, and the org says so out loud.** It does not
  spin, does not improvise a substitute, and does not quietly pick a default.
- Rendered: the boardroom is occupied, the affected floor plate goes grey, and the request appears
  in the owner's queue with its cost-of-delay stated.

**The distinction matters because the failure modes differ.** An unanswered approval wastes an
opportunity. An unanswered capability request means the org is burning money to stay still, and
the owner needs to see that in days-of-runway, not as a badge count.

---

## Blocking decision request — the record

New table. Small, append-only in behavior (a request is answered once and then frozen).

### `office_request`

| column | type | notes |
|---|---|---|
| `id` | INTEGER PK | |
| `ts` | REAL | raised at |
| `raised_by` | INTEGER | FK `office_agent` — almost always the CEO |
| `class` | TEXT | `capability` \| `approval_referral` (the latter when an approval is too big for the CEO's grade) |
| `title` | TEXT | one line, the actual question |
| `why_now` | TEXT | what triggered it — the specific blocked work |
| `blocks` | TEXT | JSON: the department/business/agent ids that cannot proceed |
| `options` | TEXT | JSON array of `{label, consequence}` — **every request carries options, never an open question** (the Task Brief pattern from starnet) |
| `recommended` | TEXT | which option the CEO recommends and why |
| `cost_of_delay_cents_day` | INTEGER | runway the org burns per day while this is open |
| `needs` | TEXT | what physical/legal thing only the owner can supply (account, signature, key, decision) |
| `state` | TEXT | `open` \| `answered` \| `withdrawn` \| `expired` |
| `answer` | TEXT | the owner's ruling, free text |
| `answered_at` | REAL | |
| `ttl_days` | INTEGER | default 14; expiry escalates rather than silently closes |

Rules:

- **One question per request.** If the CEO has three questions, that is three requests, ranked.
- **Options are mandatory.** "Should we launch this?" is rejected at write time. The CEO must
  present two or more concrete options with stated consequences, plus its recommendation.
- **`cost_of_delay_cents_day` is mandatory** and is computed from the blocked work's burn, not
  guessed. This is what lets the owner rank by urgency instead of by shout volume.
- **Expiry escalates.** A request past its TTL moves to `expired` and re-raises at a higher
  `cost_of_delay` as the blocked work's daily burn accumulates. It does not vanish.

---

## What the org may decide alone vs what it must ask

| Decision | Decided by | Class |
|---|---|---|
| What to build, which offer, which positioning | Manager → CEO | none |
| Pricing within the department's stated band | Manager | none |
| Hiring within the department's headcount budget | Manager | none |
| Founding a new department | CEO | owner sign-off on capital (A) |
| Publishing anything under the owner's name | CEO | A |
| Spend above the agent/department allowance | CEO | A |
| **Choosing the payment rail / processor** | **Owner only** | **B** |
| **The legal entity that contracts with customers** | **Owner only** | **B** |
| **Which Tripo product line the credits are on** | **Owner only** | **B** |
| **Business vertical selection, from scout proposals** | Scout proposes, CEO recommends, **owner picks** | A, but blocking for that department |
| Legal/compliance questions | Owner only | B |

---

## How this changes the plan's open questions

The four M3 questions were listed as things *the owner must pre-decide* before the milestone can
start. That was the wrong shape — it put the org's design work in the owner's lap.

Corrected:

| # | Old framing | Correct framing |
|---|---|---|
| 1 | "Owner picks the vertical before M3" | **The org proposes three verticals with theses and citations, and asks.** Owner picks. This is org work that ends in a Class A referral. |
| 2 | "Owner pre-decides the payment rail" | **Class B.** The org cannot sell without it; it raises a blocking request the moment a business reaches `building` → `live`. |
| 3 | "Owner pre-decides the legal entity" | **Class B.** Same trigger — required before the first invoice exists. |
| 4 | "Owner pre-decides Tripo Studio vs API" | **Class B.** Only blocks the visual upgrade (M6), so it is the lowest-urgency of the three. |

**Consequence for M3:** M3 is no longer "blocked until the owner answers four questions." M3
starts, the org does the work, and it raises blocking requests when it hits the actual wall. The
milestone's proof is unchanged; only the sequencing improves.

**Consequence for the owner's time:** the owner's involvement is now a *queue with ranked urgency
and cost-of-delay*, not a checklist handed out in advance. He answers when the org asks, with the
question already framed and options already costed.

---

## The boardroom page

In the "Qubit Office" tab (`55-DASHBOARD-PAGE.md`), the boardroom is both a room on the executive
floor **and** a panel:

| Panel region | Shows |
|---|---|
| **Latest report** | the CEO's daily/weekly report (D24): headline, recommendation, reasoning, and a **verified** flag if the numbers matched the ledger |
| Open requests, ranked | title, class, `cost_of_delay_cents_day`, days open, what it blocks |
| The selected request | the question, the options with consequences, the CEO's recommendation, the `needs` line |
| Answer | free-text ruling + which option; settling writes to the request and unblocks the waiting work |
| Recent answers | the last N rulings, so the org's history of owner decisions is visible |

An open Class B request also greys the blocked floor(s) and marks the affected agents, so the
tower shows *where* the org is stopped — not just that something is pending.

## Voice

Blocking requests are read aloud on request, ranked by cost of delay: *"One blocking request: the
payment rail. Three options, four days open, costing about $2.10 a day in runway. The CEO
recommends Stripe."* Answering by voice is the same settle path as the panel — one journal, one
path.
