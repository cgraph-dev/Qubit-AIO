# RankValut Handoff Package — concrete example (2026-08-08)

A working 5-doc handoff set produced for a digital-goods marketplace. Reuse the
anatomy, not the content.

## Package layout

```
docs/
├── RankValut Phase 0 Decision Pack (2026-08-08).md        ← decisions (fill-in)
├── RankValut Full Marketplace Implementation Blueprint (2026-08-08).md  ← build spec
└── competitor-auth-research/
    ├── RankValut Competitor Parity and Completion Plan (2026-08-08).md  ← gap register
    ├── RankValut Z2U Authenticated Surface Research (2026-08-08).md     ← observed pages
    ├── RankValut G2G Authenticated Surface Research (2026-08-08).md     ← observed pages
    └── screenshots/  (58 PNGs, personal data — kept out of public pushes)
```

## Decision Pack anatomy (the fill-in worksheet)

1. What must be decided, in order (provider → vertical → countries → fees → SLA).
2. Provider selection: criteria + candidate shortlist table (fit / watch-out)
   + recommended default + fallback.
3. First vertical spec: category contract (typed fields), delivery states,
   filters, moderation rules, claim/warranty defaults.
4. Launch scope: countries, currencies, exclusions, age rule.
5. Fee schedule (with rank model — see below).
6. Inspection windows + support coverage (honest staffing, no fake "24/7").
7. **Decision Record**: sequential rows `# | Decision | Default proposal |
   Decision | Approver | Date` — the agent can't start money code until
   rows 1–2 are dated.
8. What the agent should do with the pack (provider-neutral work first).

## Fee-advantage + rank fee model (owner's final choice)

Goal translation: "10% cheaper than competitors" → NOT implementable as a
forced price on a marketplace. Owner clarified: **1–2% cheaper + rank
discounts**, while profitable.

- Base fee = verified competitor benchmark − 1–2 percentage points per category
  (gift cards 9.5%, items/currency 10.5%, accounts 11% proposed).
- Rank discount: existing 14-level seller ladder (Starter→Grandmaster), 0–7pp
  (`feeDiscountPercent` already existed in `activity-level.ts` dormant).
- `effective fee = max(base fee − rank discount, fee floor)`
- Fee floor ≈ 3.4% (payment processing + 0.5pp margin) — the profitability
  guardrail.
- Rank evaluated at order time; discount snapshotted on the order; upgrades
  immediate, downgrades never retroactive.
- Zero buyer fee at launch; buyer ranks earn coupons/points/support perks.
- Marketing: "always ~1–2% cheaper", "earn up to 7% off as you rank up" —
  verifiable from published schedule. NEVER a literal "10% cheaper than G2A".

## Blueprint anatomy (the build spec)

1. Current state (verified table: works / preview-only / missing).
2. Phase 0 decisions (links to decision pack).
3. Non-negotiable invariants (integer cents, idempotency, transactions,
   immutable ledger, server-side authz, encrypted secrets, tests).
4. Canonical order FSM + full transition table (from → to → allowed by →
   atomic effects) + OrderEvent history.
5–16. Build specs: payment seam + webhook dedupe, escrow, double-entry ledger,
   delivery contracts + evidence, inventory model, category contracts, fee
   engine, secure code storage, durable timers, chat, regional eligibility,
   faceted search, seller API/bulk import/auto-delivery.
17–19. UI parity screens (seller + buyer dashboards), trust signals (measured).
20. Implementation order: 7 phases with checkboxes + global completion gate.
21. Explicit non-goals.
22. Handoff checklist for the implementing agent.

## Gotchas learned

- The Parity Plan doc initially sat at `docs/` root; when it moved into
  `docs/competitor-auth-research/`, relative links in it and the Blueprint
  broke — grep for stale paths after any move.
- Rewriting the fee section dropped the payout-rules subsection; re-read the
  file after big replacements.
- Decision Record renumbering after inserting rows (was 15 rows → 27).
- Rank ladder numbers must match the code (`activity-level.ts`) exactly —
  the docs reference the code, they don't redefine it.
