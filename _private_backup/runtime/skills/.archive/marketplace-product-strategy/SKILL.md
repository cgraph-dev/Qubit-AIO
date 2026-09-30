---
name: marketplace-product-strategy
description: >
  Design and document marketplace/e-commerce product strategy for this user:
  fee-advantage pricing (undercut competitors honestly), rank/loyalty-based fee
  discounts, order state machines, and multi-doc handoff packs (blueprint +
  decision record + research docs) that another AI agent can implement from
  without re-asking. Use when the user says "so I can give to another AI agent
  to make", asks to be cheaper/better than competitors, or wants marketplace
  parity/fee/pricing/roadmap docs.
---

# Marketplace Product Strategy & Implementation Docs

## When to use
- User building a marketplace / two-sided platform (e.g. RankValut, a
  digital-goods marketplace competing with z2u, g2g, g2a, zeusx, eldorado.gg,
  playerauctions) and asks for pricing, parity, or implementation docs.
- User says "so I can give to another AI agent to start work" / "make a doc for
  this" — deliverable must be SELF-CONTAINED and implementable by a fresh agent
  with no conversation context.
- Any pricing/positioning question ("we must be cheaper than X", "take less
  from sellers/buyers but still profit").

## Deliverable format for handoff docs (this user's standard)
A handoff pack, not a single doc. Cross-link every doc; every link must resolve
relative to its own folder. Each doc should be:
- **Decision pack** (owner worksheet): decision → default proposal → blank
  Approver/Date columns. Every number is a PROPOSAL with a confirm box; nothing
  is enforceable until recorded + dated.
- **Implementation blueprint**: state machines (FSM transition tables with
  guards + atomic effects), schema hints, build phases with checkbox gates,
  non-goals, agent handoff checklist.
- **Research docs**: page inventory with EXACT routes, UI/UX organization,
  filters/categories, forms/fields, A→Z flows, mapping table to existing
  routes, data-model hints (enums/statuses). Structural only — never copied
  competitor copy/assets/listings/prices/personal data.
- **Marketing copy spec**: taglines, which pages change, guardrails. Figures
  must COMPUTE from approved data at render time, never hardcoded.

## Fee-advantage pricing (the corrected mental model)
User instinct is often "be 10% cheaper than competitors". That is NOT
mechanically implementable on a marketplace and produces unverifiable claims:
- Sellers set prices; the platform cannot force them 10% below competitors.
- "10% cheaper than G2A" requires scraped live competitor price data — banned
  by the user's own research runbook, and unverifiable.
- Forcing a discount requires subsidy; margin math collapses
  (take rate − payment cost − ops cost < 0).

The honest version that works (and that this user accepted):
`effective fee = max(base fee − rank discount, fee floor)`
1. **Base fee = verified competitor benchmark − 1–2 percentage points**
   (read competitor PUBLIC fee pages — allowed; scraping prices is not).
   Every seller is cheaper from day one; buyers pay less as a consequence.
2. **Rank discount** — wire the platform's EXISTING rank ladder to money
   (this user's codebase already had a dormant 14-level ladder with
   `feeDiscountPercent` 0→7 reserved for exactly this). Bigger rank = less
   fees = retention loop.
3. **Fee floor** = payment processing cost + small ops margin (e.g. ~3.4%).
   "Still make profit" guardrail: no rank/category runs at structural loss.
4. Marketing: "always 1–2% cheaper than the big marketplaces", "earn up to X%
   off as you rank up" — verifiable from published fee schedule. NEVER name
   competitors in claims ("cheaper than X" → "below market average" + method
   link), never publish literal "10% cheaper than [competitor]".

## Rank-perks design (the "nice" part)
Fees alone feel like a coupon; add a status layer:
- Seller: Bronze+ earned badge + premium search placement (parity with
  ZeusX Premium Seller — earned by rank, never purchasable); Silver+ faster
  payout / lower reserve; Diamond+ featured storefront; Grandmaster dedicated
  support queue. All reversible on rank downgrade, never retroactive.
- Buyer: Bronze+ coupon eligibility + points multiplier; Silver+ priority
  support + longer inspection window; higher tiers more multiplier.
- Rule: rank evaluated at ORDER time; upgrades apply to next order
  immediately; downgrades never retroactive; fee+rank+discount snapshotted on
  the order for audit.

## Order FSM authoring
- One canonical enum across backend/frontend/DB, e.g.
  `pending_payment → paid → preparing → delivering → delivered →
  awaiting_confirmation → completed` + terminal `cancelled /
  partially_fulfilled / partially_refunded / refunded / disputed`.
- Write a transition TABLE (from → to → allowed by → atomic effects), guards
  enforced server-side, `OrderEvent` history for audit, idempotency keys on
  every retryable command.
- When multiple docs exist, one is the authoritative FSM — older draft docs
  must be aligned to it, or you get state-name drift that confuses agents.

## Pitfalls
- **Doc moves break links**: moving a doc into a subfolder breaks relative
  links to sibling-folder files — they need `../`. After any move, verify all
  local `.md` links resolve (decode %20/%28/%29 before checking existence).
- **FSM naming drift**: older draft docs use older state names
  (`created → awaiting_delivery → inspection`). Align drafts to the newest
  authoritative spec with a pointer comment.
- **Decision Record renumbering**: when inserting rows, renumber the whole
  table or you get duplicate row numbers that confuse the implementer.
- **Check for dormant policy inputs before designing new systems**: grep the
  codebase for reserved fields/comments ("fee benefit reserved", "policy
  input") — this user's stack often has the ladder/schema already built and
  waiting to be wired. Reusing it beats designing a parallel system.
- **Scope discipline**: the user enforces exact scope (do ONLY what was
  asked). When a pricing ask is impossible as stated, push back with the
  honest reframing ONCE (1-3-1: problem, 3 options, recommendation), then
  implement what they pick — do not silently substitute your own strategy.
- **Clean-room rules are non-negotiable**: no copied competitor copy, assets,
  prices, seller data, or fabricated metrics in docs. Competitor fee pages may
  be read and cited; dynamic price data may not be scraped.

## References
- `references/rankvalut-doc-set-2026-08.md` — the concrete 6-doc handoff pack
  built for RankValut (decision pack, blueprint, policy pack, research docs)
  with the exact fee ladder, FSM, and decision-record rows to reuse/extend.
