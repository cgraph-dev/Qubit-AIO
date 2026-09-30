---
name: agent-handoff-docs
description: >
  Author implementation-ready documentation packages that another AI agent can
  execute from without re-browsing or re-deciding: research/inventory docs,
  implementation blueprints with phase gates, and owner decision packs with
  fill-in decision records. Use whenever the user says "create docs so I can
  give to another AI agent to start work/make", or when converting competitor
  research / gap audits into buildable specs. Covers the 3-layer doc package,
  [OWNER DECISION] marking, decision-record tables, phase ordering, completion
  gates, and clean-room handoff discipline.
platforms: [linux, macos, windows]
version: 1.0.0
author: Hermes
license: MIT
category: software-development
metadata:
  hermes:
    tags: [documentation, handoff, ai-agents, specifications, planning, clean-room]
---

# Agent Handoff Documentation

## When to use

- User says "create a doc(s) so I can give to another AI agent to start work"
  or "so the agent can implement it".
- Converting competitor research, page inventories, or gap audits into
  buildable specs (feature parity, marketplace build-out, migration plans).
- Any docs-driven project where a separate agent (or human) will implement
  from the docs without re-doing research.

## The 3-layer doc package

Field-tested shape for marketplace/product build-outs (RankValut 2026-08):

1. **Research/inventory doc** — what EXISTS (observed, exact routes, state
   machines, table columns, form fields, filters). No decisions, no invented
   numbers. Screenshots with personal data stay OUT of the repo.
2. **Implementation Blueprint** — how to BUILD it: canonical enums/FSM with
   transition tables, schema/db hints, module breakdown, build order with
   phases + completion gates, explicit non-goals.
3. **Decision Pack** — what the OWNER must decide before money/legal/provider
   code: fill-in decision record table (default proposal | decision |
   approver | date), provider shortlist, fee schedules, SLA windows.

Link the three layers to each other with relative paths; keep a shared
"Related documentation" block in each so the agent can navigate.

## Structural rules that make handoff docs work

- **Mark owner decisions inline as `[OWNER DECISION]`** wherever policy,
  legal, provider, or money decisions gate code. The agent builds everything
  unmarked and reports marked blockers instead of stalling.
- **Default proposals everywhere.** Every open question gets a proposed
  default ("verify at decision time" where numbers depend on external
  sources) so work never blocks on a blank. Nothing is enforceable until the
  decision record is filled + dated.
- **State machines as transition tables**, not prose: enum + table of
  from → to → allowed by → atomic effects. Include exception states
  (cancelled/refunded/disputed/partial).
- **Phase order with completion gates**: a critical path first (e.g. money +
  FSM core), parallelizable work after; a global "do not call production-ready
  until ALL true" checklist.
- **Explicit non-goals section**: no fake routes for parity, no copied
  assets/code/policy text, no fabricated metrics, no provider-specific
  behavior before provider selection.
- **Handoff checklist for the implementing agent**: numbered reads (repo
  AGENTS.md, this blueprint, research docs, schema, migrations) + "implement
  Phase 1 first; report blockers on [OWNER DECISION] items".
- **Money in integer cents, idempotency, atomic transactions, server-side
  authz** — state as non-negotiable invariants when money/inventory is in
  scope.

## Pitfalls (hit in the field)

- **Big section replacements can silently drop adjacent subsections.** After
  replacing a whole section (e.g. rewriting a fee schedule), re-read the file
  to confirm payout/related subsections survived.
- **Renumber after inserting decision-record rows** — adding rows in the
  middle leaves duplicate numbers.
- **Moving a doc into a subfolder breaks relative links** in every doc that
  references it. After any move, grep the docs for the old path and fix
  links (this bit us when the parity plan moved into a research folder).
- **Don't let the doc overclaim parity.** A route in the research doc is
  "observed behavior", not "implemented". Keep the gap status honest
  (Present/Partial/Missing/Blocked).
- **Competitor numbers must be verified, not copied from memory.** Fee
  benchmarks and policies change; write "verify at decision time from public
  fee pages" instead of hardcoding figures, and link the source page.

## Clean-room rules (this user's standard)

- Learn behavior, state transitions, information architecture — do NOT copy
  competitor code, assets, screenshots-as-assets, listings, prices, seller
  data, or policy wording.
- Never fabricate counts, metrics, delivery times, protection claims, or
  seller data in handoff docs.
- Translate aspirational claims ("10% cheaper than competitors") into
  verifiable mechanisms (fee advantage, rank discounts) — see the
  one-three-one-rule skill for the feasibility-first pattern.
- Personal data captured during research (screenshots, dumps) never enters
  the repo; docs describe structure only.

## References

- `references/rankvalut-handoff-example.md` — concrete example: the 5-doc
  RankValut handoff package (decision pack → blueprint → parity plan →
  Z2U/G2G research), section anatomy, and the fee-advantage + rank fee model.
