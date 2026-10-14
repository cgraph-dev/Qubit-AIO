# The Tower — Qubit's agent corporation

A real company, staffed by real agents, rendered as a game. The owner supplies capital and
absorbs legal and compliance; the agents decide what to build and sell, earn real money, pay
their own inference bills, and are terminated when they cannot.

**Read `01-THE-MACHINE.md` first — one page, the whole thing.** Everything below is detail.

Read in this order:

| File | What it settles |
|---|---|
| `01-THE-MACHINE.md` | **Start here.** The whole machine on one page: the six stages, the game layer, what each reference repo became, and where it stands |
| `00-IDENTITY.md` | What this is, the product law, and what "working" means in five checkable sentences |
| `05-AMENDMENT-LOG.md` | **Read this before flagging an inconsistency.** What changed and why |
| `10-DECISIONS.md` | **D1–D43, all `OWNER-APPROVED` 2026-10-12.** D24–D33 review + pipeline; D34–D41 corporate structure; D42–D43 the R&D floor. |
| `11-ORG-CHARTER.md` | **Who reports to whom**: five levels, supervisor edges, span limits, budget envelopes, the review chain, culture mechanics |
| `12-DEPARTMENT-CATALOG.md` | ~18 department templates across 5 division kinds, with risk models and test paths |
| `13-PIPELINE.md` | The intake funnel: scraper → manager → director → CEO → owner, with provenance |
| `14-RND-FLOOR.md` | **The R&D floor**: owner-fed intake, the five task rooms, the three-phase digest, the panel, the no-backlog rule |
| `15-REVIEW.md` | Review against both codebases: four material findings, and what we reject |
| `17-AUDIT.md` | **Internal audit**: the four ways an agent's numbers lie, the spot audit, separation of duties, the penalty ladder |
| `19-REVIEWS.md` | **Performance**: the four cadences, ratings, PIP, promotion, termination, appeal |
| `20-ARCHITECTURE.md` | Layers, where each piece lives in this repo, the overlay-plugin wiring |
| `25-PORTED-CODE.md` | **Provenance**: which MIT source informs which local function, and what was deliberately not used |
| `26-OCCLUSION-AND-DEPTH.md` | **The renderer's depth fix**: why the iso view smeared, the painter's-algorithm sort key, and the proven `paintOrder()` |
| `30-DATA-MODEL.md` | The tables (agents, floors, departments, rooms, ideas, reviews, audit), append-only ledger, the survival formula |
| `40-RUNTIME.md` | The turn, three clocks, task backlog, budget enforcement, termination |
| `45-ECONOMY.md` | Real unit costs, daily run cost by headcount, revenue plan, the money gate |
| `50-INTEGRATION.md` | Obsidian / OpenViking / skills / MCP / tools per seat type; the event map |
| `55-DASHBOARD-PAGE.md` | The "Qubit Office" sidebar tab: manifest, wiring points, layout, honest states |
| `57-BOARDROOM.md` | Decisions, the boardroom/review rooms, and blocking capability requests to the owner |
| `58-AUTONOMY.md` | Full access: the four axes, per-agent postures, real shell + exec profiles, the remaining floors |
| `59-REVIEW.md` | Review requests ("is this good?") and account requests ("can I have your info?") |
| `60-EVOLUTION.md` | baseline vs ratchet, the daily reflection turn, scoring, cross-pollination |
| `65-REFERENCES.md` | What each of the seven source repos contributed and what was discarded |
| `70-ASSETS.md` | Visuals: the Phaser iso model, procedural agents, the Tripo upgrade + license gate |
| `75-EDITOR.md` | M7 floorplan editor: object=capability, derived grants, staged writes, undo |
| `80-RISKS.md` | License matrix, R1–R14, non-goals, four open owner questions |
| `90-MILESTONES.md` | M0–M7 with one behavioral proof each, and the critical path |
| `95-CONTRACT.md` | C1–C33 machine-checkable invariants. **Read before writing code.** |
| `99-HANDOFF.md` | **Start here if you are implementing.** Which milestone can start now. |
| `merge/00-INDEX.md` | **The `/refs` merge** — folding the five MIT reference repos' art, laws and techniques into the tower. Licence gate, source inventory, asset pipeline, milestones M0–M6. |

## The rulings that matter most

1. **The economy is real, not simulated** (D1). Real revenue, real inference cost, real survival.
2. **The owner is a shareholder, not the CEO** (D2). One agent holds the CEO seat and reports up.
3. **Model tier is per seat, not per agent** (D3). Cheap tier by default; best tier only where a
   seat has to make a judgment call.
4. **Baseline is immutable, the ratchet only grows** (D5). An agent cannot rewrite its own
   instructions; improvement has to be measurable against a fixed reference.
5. **The tower never asserts state it cannot prove** (D14). No event, no animation.
6. **The org moves freely — and loudly** (D28 + D22). No permission prompts; but publishing and
   above-allowance spend always get a review, and the tower surfaces everything via bubbles,
   badges, and reserved popups.
7. **The floor is the org chart** (C12). A room's objects are the team's capabilities, resolved
   fresh every turn — so editing the floor changes what agents can do.
8. **Everything legal is allowed** (D25 + D29). Survival outranks image; the six hardline items are
   the only line.
9. **The CEO argues its case** (D24). Reports are mandatory, reasoning is mandatory, and the
   numbers are checked against the ledger.
10. **You are reviewed by the level above** (D34/D36). One supervisor per seat, span limit seven, and
    nobody's self-assessment counts as evidence.
11. **Every level holds a budget envelope** (D37). It spends inside freely and raises above — which
    is how one CEO runs nine businesses without becoming the bottleneck.
12. **The org reconciles rather than trusts** (D40). An independent auditor samples one business a
    period, and a report that the ledger contradicts is a finding, not a rounding error.
13. **There are two ways in, and neither gets a shortcut** (D42). The scraper firehose is volume with
    no judgment; the R&D floor is depth with no volume — and the owner's own material still faces
    gate 0, because a business is good or it is not, whoever suggested it.

## The honest numbers

| Item | Amount |
|---|---|
| Bootstrap (1 CEO, 1 manager, 3 workers) | **~$105/month** |
| First department proved (1 CEO, 1 director, 2 managers, 7 workers) | ~$215/month |
| Small corp (5 departments, 30 seats) | ~$630/month |
| Full corp (9 businesses, 71 seats) | ~$1,150/month |
| Daily self-improvement tax at 71 seats | ~$27/month |
| One-time art capex (12 Tripo characters + props) | ~$15 (M6 upgrade, optional) |

Recomputed 2026-10-12 from `45-ECONOMY.md`'s own price card ($0.15 best-tier turn, $0.01 cheap turn,
15 worker turns/day, 10 management turns/day). The earlier figures (~$20 / $57 / $210 / $870) were
*slightly* low because they left directors out of the headcount while D32 put them in the org.

Every figure in this table is computed from the price card in `45-ECONOMY.md` (see the note there on
what was wrong with the previous totals).

The org has to earn **~$1,150/month at full size** before it is anything but a cost center. Reach
that in stages, not at once — the constraint is account risk, not money (`45-ECONOMY.md`).

## The one-line pitch

Every other project draws agents working. This one runs a company and draws it running.

## The page

The tower is a **page in the dashboard sidebar** — the "Qubit Office" tab — not a standalone app
(`55-DASHBOARD-PAGE.md`). A tab cannot drift from the bridge it is drawn in; a separate app would
need its own auth, store, event stream, and confirmation path, which is this repo's most expensive
recurring bug class.

## Full-game frontend audit and proposed redesign

Read [`frontend-game/README.md`](frontend-game/README.md) for the current live
frontend audit and the sequential Japanese/anime ARPG-style game plan: building
entry/exit, lobby/elevator/stair travel, floors/rooms, character conversations,
settings, panels and the backend handoff. This is **audit and planning only**;
no game redesign has been implemented. The milestone claims below are historical
snapshots, not the new audit's current capability verdict.

## Status

**M0–M4 are built and proven** (2026-10-12). The append-only ledger, the two money folds, the
chain of command, owner-attested receipts, the budget caps, the consent ladder, the attributed turn,
top-down envelopes with raises, escalation on the **shared** confirmation journal, survival and the
daily tick, one-level reviews, the business lifecycle with its gates, idempotent revenue ingestion,
the audit layer (reconciliation, findings, separation of duties, spot audit, penalty ladder, auditor
seat), and **the Office page** — a tower whose characters animate only from `office_event`.
**16 modules / 5,288 lines**, `qubit office` with **65 subcommands**, **221 tests passing**
(`90-MILESTONES.md` § M0–M4 status carry the commands and the per-proof tables).

**The Office tab** (`frontend/qubit-app/plugins/qubit-office/`, tab `/qubit-office`) reads
`GET /api/office/tower` and nothing else — no mutation, no optimistic state (C4). Its animation
state is computed in `voicebridge/office/tower.py` from event rows as a pure function of
(rows, clock), which is what makes C7 testable: freeze the stream and every character goes `idle`.

**⚠ Limitation:** `deepseek-v4.1-flash` via `opencode-go` reports no tokens or cost, so per-turn
*exact* attribution is not possible with this provider — the caps bound the spend, but bookings are
cap-attested rather than measured.

No tower page yet — the visuals are **M4**. The store is real and queryable now.

Open before M3: the first business vertical, the payment rail, the legal entity, and which Tripo
product line (Studio vs API — separately billed) — see `80-RISKS.md`.
