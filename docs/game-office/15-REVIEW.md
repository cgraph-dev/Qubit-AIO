# 15 — Review: what both codebases change

A pass over the whole plan against the two real codebases. Four findings are material — each
fixes a way the plan as written would have failed in production, and each is taken from code that
was written *because* the failure already happened to somebody.

Read this alongside `10-DECISIONS.md`; the findings below add rulings D15–D18.

---

## Finding 1 — Settlement of interrupted runs (from `starnet/sidecar/ledger.js`)

**What we missed.** Our `office_ledger` records a cost when a turn *finishes*. If the process dies
mid-turn, the money the provider charged is real and our ledger never learns it. Worse, on the
next boot our ledger would look **healthier than reality** — the run happened and the spend is
unknown, but the books show nothing.

StarNet's ledger handles exactly this, and their comment states the stake:

> Keep complete history. Unreadable or truncated history is unknown, never fresh headroom.

Their mechanism:

| Piece | What it does |
|---|---|
| `beginRun(runId, agentId, meta)` | Writes a **dispatch receipt** *before* the run — so an interrupted run is findable at boot |
| `unsettledRuns()` | Receipts with no matching ledger row = spend-unknown |
| `settleUnsettled(runId, usd, how)` | The **only** way unknown spend becomes known: a human states what the provider charged |
| `attestedAs: 'entered' \| 'limit'` | Records *how* it was settled, and never claims `entered` for a limit booking |
| `health().complete` | False while any run is unsettled — a read surfaces the incompleteness instead of hiding it |
| `pendingRuns()` | Runs dispatched but not yet booked |

**The rule we adopt:** *an incomplete ledger is never read as headroom.* Our budget admission
check must refuse when `health().complete` is false, not proceed on a total that omits unknown
spend.

**Why this is critical for us specifically.** Our whole premise (D1) is that an agent's balance
determines whether it survives. If interrupted spend is silently dropped, agents get free runway,
the survival signal is wrong, and the org's P&L is optimistic precisely when something is broken.
This is the single most important thing in either codebase for our economy.

## Finding 2 — Unmetered runs must not count as dollars (from `starnet/sidecar/ledger.js`)

**What we missed.** Not every turn costs metered money. A subscription-backed provider reports a
figure that is an *estimate of nothing the owner was charged*. StarNet stamps `unmetered` at
record time, and their comment documents the exact bug we would have shipped:

> the same run read two different amounts depending on the surface: `/api/insights` said $0.00
> "subscription / unmetered" while `/api/budget`'s spentToday + lifetime and the budget governor's
> day and global pools counted the full figure — **and that phantom spend then BLOCKED a later
> real BYOK run** with reason `budget` and a CAP HIT note naming money nobody spent.

So phantom spend didn't just misreport — it **denied real work.**

**The rule we adopt:** `office_ledger.kind='inference'` rows carry an `unmetered` flag. Metered
aggregates (the caps, the agent balance, the P&L) **exclude** them. Run counts and token counts
still include them — the run happened. We keep two folds: `sum_metered()` (feeds caps) and
`sum_reported()` (feeds "what the provider said"), and they are never interchangeable.

This also matters for our tier model (D3): the owner's Perplexity Pro research (D8) is
subscription-backed — unmetered. Counting it as dollars would make the scout floor look like a
cost center when it is nearly free.

## Finding 3 — The four-tier consent ladder (from `starnet/sidecar/permissions.js`)

**What we under-specified.** D7 says "money-gated actions stage an approval." That is one tier.
StarNet's broker has four, in fixed order, each short-circuiting:

| # | Tier | Behaviour | Their note |
|---|---|---|---|
| 1 | **HARDLINE** | Unconditional deny floor, checked **first** so no flag can reach past it | reason carries an anti-retry suffix: *"do this yourself outside the agent; do not retry or rephrase"* |
| 2 | **BYPASS** | Full Access allows anything not on the hardline floor | injected as a *live predicate* so revocations take effect on the next call |
| 3 | **CACHE** | Allow if this `dangerKey` was granted for this session or permanently | |
| 4 | **RESOLVE** | read-only + non-network → auto-allow; a **mutation with no grant DEFAULT-DENIES** | *"silence is not consent"* |

Plus: `dangerKey = capability + ':' + scope` — the danger **class**, never args, paths, or keys.

**Two things we adopt verbatim:**

1. **`silence is not consent`.** Under an autonomous surface with no human present, an ungranted
   mutation is *denied*, not queued. Our overnight org must not be able to self-approve by waiting
   — and the denial carries the anti-retry suffix so the agent stops burning turns re-attempting it.
2. **The hardline tier.** We need a real unconditional deny floor for things no grant, promotion,
   or owner-approval can unlock — with our own list (see D18).

The tier ladder also fixes a hole in D7: we only gated on *money*, but the correct unit is the
**danger class** (capability + scope). A worker publishing under the owner's name and a worker
writing a file are different danger classes even when neither spends money.

## Finding 4 — Cap resolution is the same mechanism we need for the editor

Already adopted as C12/C13. Recording here because the review confirms it is the plan's spine:
`resolveTools` per turn, `attenuate` as intersection, and `compute` as the never-filterable gate.
StarNet's comment on why the floor is real:

> on the INTERACTIVE surface the floor is REAL — the office starts COMPUTE-ONLY (compute is the
> single freebie, so an agent can ALWAYS think and a brand-new agent works out of the box — never
> a dead wall)

That "never a dead wall" rule is what makes the compute-only baseline correct rather than a hack.

---

## What the 2D floor (pepeclaw) contributes alongside

The two codebases are complementary and neither alone is enough:

| | starnet | pepeclaw |
|---|---|---|
| **Is** | a harness — real agent runs, real cost, capability as placed objects | a renderer — a working 2D isometric office in ~600 lines |
| **Gives us** | the law (never assert unprovable state), capability resolution, permission tiers, spent-run settlement, budget caps, e-stop | the whole visual layer: iso diamond rooms, walkways, camera pan/zoom/select/follow, minimap, per-room agent slots, procedural agent sprites |
| **Language** | Node/vanilla JS, canvas | TypeScript/React + Phaser |
| **License** | MIT (code only; brand reserved) | MIT |
| **Fault** | no visual floor worth reusing (its station art is proprietary and excluded) | no harness, no economy, demo-data fallback we must reject |

**The combination is the point:** pepeclaw tells us *how to draw the office*, starnet tells us
*what the office means*. The plan already reflects this, and the review found no place where they
conflict — the iso renderer consumes positions and states; the capability model consumes room
objects. They meet at exactly one coordinate: **a room is a set of objects, and those objects are
both drawn and resolved.**

That single seam is why the editor (M7) is coherent: placing a prop is simultaneously a rendering
change and a capability change, and `tile_x/tile_y` are deliberately *outside* the resolution so
the two never get confused.

---

## Smaller improvements from the review

| # | Change | Source |
|---|---|---|
| 5 | **Dispatch receipt before every run**, not just a row after it | `ledger.js beginRun` |
| 6 | **E-stop must never throw**, and must mark runs so a deliberate stop is distinguishable from a crash | `halt.js` — *"on a phone silence made a deliberate stop byte-identical to a crashed bot"* |
| 7 | **Budget caps as four keys** — `perRun`, `perAgent`, `perDay`, `global` — with `0` = explicitly no cap and `null` = clear back to default | `budgetcaps.js` |
| 8 | **A cap override must be a finite number ≥ 0 or it is junk** — "a corrupt persisted value can never grant unintended headroom" | `budgetcaps.js cleanOverrides` |
| 9 | **`UNKNOWN_MODEL`** — never write an empty model name; a blank field is worse than `(unknown)` | `spend.js cleanModel` |
| 10 | **Backfill only genuinely unpriced metered usage** — never guess a price for a run that has none | `spend.js effectiveUsd` |
| 11 | **Frozen event contract** — ~60 typed events with schemas validated in **both directions** at the bus boundary | `shared/events.js` |
| 12 | **`agent.run.end` carries a `reason` enum** including `empty` (zero tools, no text — a degraded provider) and `clarifying` (ended by asking one question), so a degraded run never renders as a clean delivery | `shared/events.js` |

Item 12 is worth calling out on its own: **a run that produced nothing must not look like a run
that succeeded.** Our `office_event` has no equivalent, and a flaky provider would silently read
as work done.

---

## What we deliberately do NOT take

| Their thing | Why not |
|---|---|
| Full-Access / host-minted bypass scopes (screen + input control) | Our owner is not on the machine watching; an unattended agent with screen control under a *money-making* directive is an unacceptable risk. Our ceiling is publishing under the owner's name, staged. |
| Tauri desktop shell, station art, brand | Out of scope; brand is not licensed (see `80-RISKS.md`) |
| Their Convex-style / Node sidecar architecture | We already have a Python core with a single listener; D9 forbids a second runtime |
| Demo-data fallback | D14 forbids rendering state we cannot prove |
| Their `LOOP` subsystem's self-reported "am I done" | `MISTAKES.md` documents a full-access loop that ran all night on 98 passes of "verified the files exist", each auto-approved. Our future-cron work must have a **machine-checkable** done signal, never the model's own verdict. |
