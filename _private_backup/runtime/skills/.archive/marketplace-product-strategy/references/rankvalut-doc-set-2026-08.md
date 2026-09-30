# RankValut doc set 2026-08-08 (concrete reusable detail)

The 6-doc handoff pack built for RankValut. Location: `~/Projects/Repos/RankValut/docs/`
(blueprint + decision pack at docs root; research docs + 58 screenshots in
`docs/competitor-auth-research/`).

## Doc inventory (what exists, what each holds)
1. `RankValut Phase 0 Decision Pack (2026-08-08).md` — owner worksheet: provider
   shortlist (Stripe Connect recommended, fallback Paddle+Payoneer), first
   vertical (gift cards/top-ups), launch countries, fee schedule, 27-row
   Decision Record (decision | default proposal | approver | date).
2. `RankValut Full Marketplace Implementation Blueprint (2026-08-08).md` —
   engineering spec: canonical order FSM + transition table, double-entry
   ledger, inventory units + reservation, category contracts, secure code
   storage, durable timers, faceted search, seller API, seller/buyer UI
   parity screens (§17.1 Rank & Fees page, §17.2 buyer Rank & Benefits, §17.3
   marketing copy spec), 7 build phases with gates.
3. `RankValut Marketplace Policy Decision Pack (Draft).md` — protection model,
   evidence/privacy, reserves, dispute flow, enforcement ladder. Its §3 order
   FSM was aligned to the Blueprint's canonical states.
4. `RankValut Company Facts and Payment Provider Readiness.md` — the 10-row
   provider acceptance table (business-model eligibility, funds flow, webhook
   contract, reconciliation, commercial terms...).
5-6. `docs/competitor-auth-research/RankValut {Z2U,G2G} Authenticated Surface
   Research (2026-08-08).md` — captured logged-in surfaces with exact routes.

## Fee ladder (proposed defaults, per category)
| Rank | Discount | Gift cards | Items/currency | Accounts |
|---|---|---|---|---|
| Starter–Legend (1-7) | 0 | 9.5% | 10.5% | 11.0% |
| Bronze (8) | 1pp | 8.5% | 9.5% | 10.0% |
| Silver (9) | 2pp | 7.5% | 8.5% | 9.0% |
| Gold (10) | 3pp | 6.5% | 7.5% | 8.0% |
| Platinum (11) | 4pp | 5.5% | 6.5% | 7.0% |
| Diamond (12) | 5pp | 4.5% | 5.5% | 6.0% |
| Master (13) | 6pp | 3.5% | 4.5% | 5.0% |
| Grandmaster (14) | 7pp | 3.4% (floor) | 3.5% | 4.0% |

Floor ≈ 3.4% = payment ~2.9% + 0.5pp ops margin. Base = competitor benchmark
− 1–2pp (verified from public fee pages, e.g.
help.zeusx.com .../what-are-the-seller-fees,
support.eldorado.gg .../8409025-seller-fees).

## Canonical order FSM (Blueprint §4)
`pending_payment → paid → preparing → delivering → delivered →
awaiting_confirmation → completed`
Terminal: `cancelled`, `partially_fulfilled`, `partially_refunded`, `refunded`,
`disputed`. Transition table has guards + atomic effects; every transition
recorded in `OrderEvent`; retryable commands carry idempotency keys; money ops
in `prisma.$transaction`.

## Existing code hooks (dormant ladder — reuse, don't redesign)
- `apps/frontend/src/lib/progression/activity-level.ts` — `BUYER_LEVELS` +
  `SELLER_LEVELS` (14 each) with `feeDiscountPercent` 0→7, `getSellerActivityLevel`,
  `getBuyerActivityLevel`, `RankLadder` component; `/ranks` page currently says
  benefits are "inactive / provider-neutral policy inputs" (preview copy to
  swap at launch).
- `apps/backend/src/modules/orders/orders.service.ts` — `OrderTransitions`
  currently only `CANCEL` from `pending`; the TRANSITIONS map is the extension
  point for the full FSM.
- `apps/backend/src/modules/wallet/wallet.service.ts` — throws
  `ServiceUnavailableException` until provider + ledger are live.
- `packages/database/prisma/schema.prisma` — `Balance`/`Transaction`/`Dispute`
  are reserved, non-immutable; order only `pending`/`cancelled` actionable;
  `Listing` flips `active→sold` on first buy (no quantity — inventory model
  needed).

## Decision Record rows (27) — renumber when inserting
1 provider (Stripe Connect) · 2 first vertical (gift cards) · 3 second vertical
(items/currency) · 4-5 launch countries · 6 currencies (USD,EUR) · 7 exclusions
· 8 age 18+ · 9-11 base fees (9.5/10.5/11.0) · 12 buyer fee none · 13 rank
discount ladder · 14 fee floor 3.4% · 15 min payout $10 · 16 daily payout · 17
reserve 0% · 18-19 rank perks · 20-27 windows/SLA (15min code, 24h manual, 4h
no-start, 48h dispute, 5d refund, support hours, prohibited list, refund policy).

## Consistency-pass checklist (run after doc edits)
- All local `.md` links resolve relative to each doc's own folder (decode
  %20/%28/%29 first; `../` needed after subfolder moves).
- One canonical FSM across all docs; drafts point to it.
- Decision Record sequential 1..N, no dupes.
- Fee numbers agree across docs; no stale "10% cheaper" claims (only guardrails
  forbidding them); marketing figures compute from approved data.
- Personal-data screenshots never committed without explicit user OK.
