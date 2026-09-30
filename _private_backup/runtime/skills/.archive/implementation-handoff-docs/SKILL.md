---
name: implementation-handoff-docs
description: Write implementation-ready planning documents (gap/parity plan, engineering blueprint, Phase-0 decision pack) that another AI agent can implement from without re-browsing or re-asking. Includes the code-grounding review technique for answering "will this make us production-ready?" honestly. Use when the user says "make a doc so I can give it to another AI agent to start work" or asks whether a plan/schema/codebase is ready to become a full product.
---

# Implementation Handoff Docs (planning docs another agent can build from)

## When to use
- User asks for docs "so I can give to another AI agent to start work" / "to make".
- User asks "will we be ready to become a full X after implementing everything there?" — needs a code-grounded readiness review, not cheerleading.
- Any doc-driven development flow: research capture → gap/parity plan → engineering blueprint → decision pack (this user runs this pipeline repeatedly across projects like RankValut).

## Pipeline (the shape that works)
1. **Research docs** — what competitors/requirements expose (exact routes, states, filters, forms, A→Z flows). Capture-side tooling lives in the `authenticated-web-research` skill.
2. **Parity/gap plan** — capability matrix with statuses: Present / Partial / Missing / Blocked, each with required evidence. Never claim parity from a UI route alone.
3. **Engineering blueprint** — the build spec: state machines, ledger/inventory schema, contracts, phases with checkbox gates, explicit non-goals, agent handoff checklist.
4. **Phase-0 decision pack** — everything that blocks real work, each with a **default proposal** + fill-in Decision Record table (decision | approver | date). Unblock the agent: provider-neutral work is startable immediately; provider-specific work starts after rows 1–2 are dated.

Cross-link all four docs in a "Related documentation" section; fix relative links whenever docs are moved between folders.

## Code-grounding readiness review (the core technique)
Never answer "are we ready?" from the docs alone. Verify against the actual code:
- Read the Prisma/schema file first — it is the source of truth for what's implemented vs reserved.
- Grep services for hard blockers: `ServiceUnavailableException`, `throw new ...("unavailable")`, "reserved", "not implemented", "preview", transition tables with only one allowed transition.
- Check the money layer specifically: is there a payment module? immutable ledger (append-only, idempotency keys)? escrow/payout/webhook code? Or only stubs?
- Compare the plan's claimed statuses to what the schema/services actually allow (e.g. "order lifecycle" doc says 7 states; code shows `pending → cancelled` only → the doc is aspirational).
- State the honest verdict with a table: layer | what docs say | what code has. "Surface parity ≠ operational marketplace" is the usual finding.

## Doc structure rules (this user's standard)
- **Exact routes and enums** in every spec — another agent must not need to re-browse.
- **A→Z flows** for every core journey (buyer purchase, seller onboarding, dispute, payout) with state transitions and guards.
- **Mapping table** competitor-surface → existing route → notes (build new vs extend).
- **Data-model hints**: status enums, money units (integer cents), ledger shape, verification ladders.
- Mark every policy/fee/window as `[OWNER DECISION]` with a proposal default — never let an agent invent policy text or numbers.
- Include **explicit non-goals** for the implementing agent (don't copy competitor assets, don't fabricate metrics, don't add parity routes without real behavior).
- Include a **handoff checklist** (which AGENTS.md files and docs to read first, what to implement first, how to report blockers).

## Pitfalls
- **Stub ≠ feature**: a UI route with a throwing service is not "present". Say "route exists, backend unavailable" explicitly.
- **Don't fabricate provider specifics** before provider selection — keep the payment seam provider-neutral; the adapter comes after the decision.
- **Money in integer minor units**; floats are a hard no in specs.
- **Idempotency + atomic transactions** must appear in every money/inventory/state-transition spec, or the agent will skip them.
- **Screenshots with personal data** (orders, balances, phone/address/DOB) stay OUT of git; committed docs are structural only.
- **Completion gates**: define the "production-ready" gate list explicitly (payment+escrow+delivery+refund+dispute+payout states, idempotency, concurrency tests, observability, legal approval) — the gate is what stops "done" claims.
- After moving any doc, grep for stale relative links (`grep -rn "old/path" docs/`) and fix them.

## Support files
- None bundled yet. Session-specific captured surfaces (z2u/g2g route maps, order FSM, seller flows) live under the `authenticated-web-research` skill's references; a RankValut example of the full 4-doc pipeline is in `~/Projects/Repos/RankValut/docs/` (2026-08-08 set).
