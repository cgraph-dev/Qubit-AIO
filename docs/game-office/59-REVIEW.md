# 59 — Review Requests and Account Requests

Two mechanisms, separate from each other and from permissions. Together they replace the approval
queue that D19 killed.

**D19 removed permission prompts** — agents do not ask "may I?". **This file adds back the two
questions that are genuinely the owner's:** *"is this good?"* and *"can I have what only you have?"*

---

## Why this is not the old approval queue

The queue D19 killed asked **"may I do this?"** forty times a day and blocked on every answer. That
is not a company; it is a suggestion box.

| | Permission prompt (removed) | Review request (this file) |
|---|---|---|
| Question | "May I do this?" | "Is this any good?" |
| Blocks work? | yes | **no** — the team continues |
| Frequency | every action | at decision points |
| Owner's answer | yes/no | an opinion, or a pick from options |
| If unanswered | nothing happens | **ships with the team's best judgment, flagged unreviewed** |
| Who is asking | an agent covering itself | a team that genuinely cannot judge its own taste |

The last row is the honest reason this exists. **An agent cannot tell whether its own t-shirt design
is good.** It can tell whether the file is valid, the listing is complete, the price is above cost,
the trademark check passed. It cannot tell whether a human will want to buy it. So it asks — and it
should ask, because that is the one input it structurally lacks.

---

## Review request

### `office_review`

| column | type | notes |
|---|---|---|
| `id` | INTEGER PK | |
| `ts` | REAL | |
| `raised_by` | INTEGER | FK `office_agent` |
| `department_id` / `business_id` | INTEGER | who it belongs to |
| `kind` | TEXT | `creative` \| `publish` \| `spend` \| `strategy` \| `pricing` |
| `title` | TEXT | one line: "12 t-shirt designs for the Q3 drop" |
| `artifact_paths` | TEXT | JSON — **the actual things to look at** |
| `preview_path` | TEXT | a rendered contact sheet / single image / document preview |
| `options` | TEXT | JSON `[{label, consequence}]` — the team's proposed choices |
| `recommendation` | TEXT | what the team would do if nobody answered |
| `deadline_hours` | INTEGER | after which the recommendation auto-applies (default 24) |
| `state` | TEXT | `open` \| `answered` \| `auto_applied` \| `withdrawn` |
| `answer` | TEXT | the owner's words |
| `answer_choice` | TEXT | which option |
| `answered_at` | REAL | |

**Rules:**

1. **Artifacts are mandatory.** A review request with no `artifact_paths` is rejected at write time.
   *"Is this design good?"* with no design attached is not a question.
2. **A preview is mandatory.** The owner is not going to open a raw SVG. The request carries a
   rendered preview (contact sheet, PNG, PDF).
3. **A recommendation is mandatory.** The team must state what it would do alone. This stops the
   request from being a way to avoid deciding.
4. **Non-blocking, with a deadline.** The org keeps working. At `deadline_hours` the recommendation
   **auto-applies** and the request is marked `auto_applied` with a flag on the artifact
   (`reviewed: false`). The owner's silence is not a veto; it is a deferral to the team.
5. **Auto-applied decisions are tracked.** A department whose reviews are mostly `auto_applied` is
   operating without the taste input it asked for. That is visible in the boardroom panel, and it
   is a signal — not a failure.

### When a review is mandatory (owner's ruling: 1b)

**Review is required on money and publishing.** Everything else is free.

| Action | Review? |
|---|---|
| Publishing anything public (listing, post, book, course, gig) | **Yes — mandatory** |
| Spending above the department's allowance | **Yes — mandatory** |
| Any trade or bet above the risk model's per-event cap | **Yes — mandatory** |
| Creating an outbound account | **Yes — mandatory** (it involves the owner's identity) |
| Design/creative work | Optional — the team chooses to ask |
| Strategy, pricing, positioning within a stated band | Optional |
| Internal work (code, research, notes, builds) | Never |

So the org runs freely all day and asks you about the two things that are irreversible: **money
leaving** and **something going public under your name**.

---

## Account & information request

The other question: *"I need something only you have."*

### `office_account_request`

| column | type | notes |
|---|---|---|
| `id` | INTEGER PK | |
| `ts` | REAL | |
| `raised_by` | INTEGER | FK `office_agent` |
| `department_id` | INTEGER | |
| `platform` | TEXT | `etsy`, `upwork`, `kdp`, `polymarket`, `<broker>`, … |
| `purpose` | TEXT | why, and the expected first-dollar path |
| `test_mode` | TEXT | the test path being used instead, and why it is insufficient |
| `needs` | TEXT | JSON list from: `legal_name`, `entity`, `address`, `tax_id`, `payout_account`, `email`, `phone_otp`, `api_key` |
| `state` | TEXT | `open` \| `supplied` \| `created` \| `denied` \| `expired` |
| `credential_refs` | TEXT | JSON `{"platform": "env:ETSY_KEY"}` — **handles only, never values** |
| `created_by` | TEXT | `agent` \| `owner` |

**The four structural guarantees:**

1. **Credentials belong to the org, not the agent.** They live in the owner's credential store and
   are referenced by handle (`env:`, `file:`). Nothing lands in the office DB, and nothing lands in
   an agent's workspace or context.
2. **The agent never sees the raw secret.** A platform tool resolves the handle at call time. An
   agent that could read the key could also write it somewhere, and with the bypass on, that is
   the whole ballgame.
3. **Asking is the only channel.** An agent may *request* the owner's legal name. It may not go
   read it from a file, a browser profile, a cookie store, or another agent's context. A request
   with `state='supplied'` is the only place that data exists for the org.
4. **Contract terms are never auto-accepted.** An agent may create an account; creating an account
   is not the same as signing as the owner. Any term that names the owner as a party is a
   per-instance owner approval (hardline item 2).

**Email hygiene:** one email alias per department. A platform suspension then contains itself to
one department instead of taking the whole org's accounts with it.

---

## Test-first, real-money-capable (owner's ruling: 2)

The owner's ruling: **real money in v1, on everything — but test accounts available from day one,
because the budget is low.**

Both are true at once, and the mechanism is that a department starts **real-capable but
test-default**:

```
department founded
  └─► provisioned with the platform's TEST path (sandbox / draft / paper / dev store)
      └─► proves the loop end-to-end: produce → list → checkout → payout → ledger row
          └─► requests the REAL account + funding (this is a review, and it is the owner's call)
              └─► funded; real money from here on, under the risk model
```

**Why this is not contradicting "real revenue":** the survival pressure is real the moment the org
spends real money, and it will. The test-first step exists for one specific reason — **a suspended
account is a permanent loss.** A brand-new department on a real account with an unproven thesis is
how you lose the account before you ever learn whether the business works. Prove the loop in the
sandbox, then fund.

**Which platforms have no test mode** (and therefore need owner approval before anything real):
Redbubble, Merch by Amazon, most print-on-demand, most sportsbooks. Those are flagged in
`12-DEPARTMENT-CATALOG.md`.

---

## Where these live in the UI

Both extend the **boardroom** (`57-BOARDROOM.md`), which becomes the owner's single decision surface:

| Boardroom panel | Content |
|---|---|
| **Reviews** | open review requests, each with its preview, options, the team's recommendation, and hours until auto-apply |
| **Account requests** | what platform, why, which of your details are needed, and the test path that was already tried |
| **Blocking requests** | the Class B items from `57-BOARDROOM.md` — things no agent can proceed past |
| **Recent answers** | the last N rulings, so the org's history of owner decisions is visible |

**Ranking:** blocking requests first (the org is stopped), then reviews by hours-to-auto-apply,
then account requests.

## Voice

Read aloud on request, ranked: *"Two reviews waiting. A t-shirt drop, 12 designs, auto-applies in
six hours, they'd shelve four of them. And a course cover, 18 hours left. No blocking requests."*

Answering by voice sets `answer` + `answer_choice` through the same path as the panel.

## The honest limitation, stated once

An auto-applied review means **the org shipped something you did not look at.** That is the deal
you chose: free movement, non-blocking reviews. The mitigations are that (a) publishing is still
gated, so nothing goes public without you, (b) the artifact is flagged `reviewed: false` forever,
and (c) the ledger and event stream record it. If the auto-apply rate gets high, the fix is more of
your attention at the founder level or a smaller org — not a tighter gate.
