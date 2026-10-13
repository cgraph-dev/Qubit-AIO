# 10 — Decisions

Numbered rulings. Each cites the alternative it beat and the reason. Changing a ruling requires
the owner, and the change is recorded here as a new ruling that supersedes — never an edit.

## Status legend

| Status | Meaning |
|---|---|
| `OWNER-APPROVED` | The owner ruled on this. Implementing agents may not change it. |
| `PROPOSED` | Written by the planning agent, awaiting the owner's ruling. Implementing agents may **not** change it either — but they may file a proposal against it. |
| `SUPERSEDED by Dn` | Kept for the record. Not in force. |

**Every ruling below is `OWNER-APPROVED`.** D1–D23 were reviewed and ruled on 2026-10-12; D24–D29 are
that review's rulings; D30–D33 are the intake-pipeline review; **D34–D41 are the corporate-structure
review; D42–D43 add the R&D floor** (`11-ORG-CHARTER.md`, `19-REVIEWS.md`, `17-AUDIT.md`). Three rulings are superseded and
kept for the record only: **D6** (by D25), **D7** (by D19, itself superseded by D28), **D19** (by D28).
A superseded ruling is not in force. Everything else is binding on implementation and changeable only
by the owner.

| # | Ruling | Status |
|---|---|---|
| D1 | The economy is real | `OWNER-APPROVED` |
| D2 | The owner is a shareholder, not the CEO | `OWNER-APPROVED (rev. D24)` |
| D3 | Model tier is assigned per seat | `OWNER-APPROVED` |
| D4 | Daily self-improvement, budget-capped | `OWNER-APPROVED` |
| D5 | Baseline vs ratchet | `OWNER-APPROVED` |
| D6 | Reputation is a constraint | `SUPERSEDED by D25` |
| D7 | ~~Outbound actions gated at the money boundary only~~ | `SUPERSEDED by D19` |
| D8 | Research runs on Perplexity Pro browser session | `OWNER-APPROVED` |
| D9 | Existing seams only — no parallel capability layer | `OWNER-APPROVED` |
| D10 | Tripo assets, paid tier, recorded per asset | `OWNER-APPROVED (rev. D26)` |
| D11 | Rendering is 2D isometric Phaser, 3D characters as a later upgrade | `OWNER-APPROVED` |
| D12 | The floorplan editor is in scope, staged-gated | `OWNER-APPROVED` |
| D13 | One floor per department, one room per team, one desk per head | `OWNER-APPROVED (rev. D27)` |
| D14 | Every rendered thing has a source row; sync is one-way | `OWNER-APPROVED` |
| D15 | A run is receipted before it starts; unknown spend blocks admission | `OWNER-APPROVED` |
| D16 | Subscription (unmetered) turns never count as dollars | `OWNER-APPROVED` |
| D17 | Four-tier consent ladder; silence is not consent | `OWNER-APPROVED` |
| D18 | There is an unconditional hardline deny floor | `OWNER-APPROVED` |
| D19 | Agents run unattended with no approval prompts by default | `SUPERSEDED by D28` |
| D20 | Freedom of movement means a real shell and real filesystem scope | `OWNER-APPROVED` |
| D21 | The org is a corporation: many departments, five division kinds | `OWNER-APPROVED` |
| D22 | Review on money + publishing only; everything else free | `OWNER-APPROVED` |
| D23 | Agents create accounts and request the owner's info; credentials belong to the org | `OWNER-APPROVED` |
| D24 | The CEO reports to the owner and must argue its case | `OWNER-APPROVED` |
| D25 | ~~Reputation limits profit~~ — everything legal is allowed | `OWNER-APPROVED` |
| D26 | Owner exports Tripo assets into a folder; an agent implements them | `OWNER-APPROVED` |
| D27 | Floors hold multiple departments; departments hold multiple rooms | `OWNER-APPROVED` |
| D28 | Agents raise visible, in-world asks — popups, badges, pending markers | `OWNER-APPROVED` |
| D29 | Legal-safe is the only line; the six hardline items stand | `OWNER-APPROVED` |
| D30 | Reserved org floors: executive, director, intelligence | `OWNER-APPROVED` |
| D31 | The intake funnel: scraper → manager → director → CEO → owner | `OWNER-APPROVED` |
| D32 | Two director kinds: portfolio (decides) and functional (advises) | `OWNER-APPROVED` |
| D33 | Managers filter ruthlessly (~90%); funnel is strict with bounce + fast-track | `OWNER-APPROVED` |
| D34 | Five-level org; one named supervisor per seat; span limit 7 | `OWNER-APPROVED` |
| D35 | A business is owned by exactly one director; managers report to it | `OWNER-APPROVED` |
| D36 | You are reviewed by the level above; nothing is self-assessed | `OWNER-APPROVED` |
| D37 | Budget envelopes per level — spend inside freely, raise above | `OWNER-APPROVED` |
| D38 | Review requests escalate in the chain; the owner sees four classes only | `OWNER-APPROVED` |
| D39 | A performance review requires artifact evidence; no evidence, no rating | `OWNER-APPROVED` |
| D40 | Independent internal audit: reconciliation, sampling, separation of duties | `OWNER-APPROVED` |
| D41 | Goals with deadlines; profile drift; recipes; the confessional | `OWNER-APPROVED` |
| D42 | A reserved **R&D floor** (`rnd`): owner-fed intake, five rooms | `OWNER-APPROVED` |
| D43 | Intake has a hard capacity and a destroy rule; no backlog, ever | `OWNER-APPROVED` |


**How to approve:** change a row's status to `OWNER-APPROVED` and add the date. That is the only
edit permitted to an existing ruling's text besides a superseding note.

---

**D1 — The economy is real.** `OWNER-APPROVED`
Revenue is real money from real customers; cost is real inference spend. Rejected: a simulated
in-world economy (a fake P&L makes the org optimize for the simulation, and the whole point is
that the survival pressure is genuine).
Consequence: the ledger, not the renderer, is the critical path. Ship the ledger before the tower.

**D2 — The owner is a shareholder, not the CEO.** `OWNER-APPROVED`
One agent holds the CEO seat and reports to the owner. The owner's levers are capital, legal,
fixed fees, and the e-stop — not operating decisions.
Rejected: operator-as-player-character (it makes the org a tool instead of a company, and the
owner explicitly asked for an org that runs itself).

**D3 — Model tier is assigned per seat, not per agent.** `OWNER-APPROVED`
Seats are typed, and the type fixes the model tier:

| Seat type | Model tier |
|---|---|
| CEO, department managers, business-discovery scouts | best available |
| Workers (builders, writers, sellers, support) | cheap/fast tier |
| Daily self-improvement reflection | cheap tier, budget-capped |

Rejected: one tier for everything (either unaffordable or too weak where it matters).
Consequence: the seat is the unit of cost control; moving an agent to a better seat is a
promotion, and it costs money — which is a decision the manager has to justify.

**D4 — Daily self-improvement runs for every agent, budget-capped.** `OWNER-APPROVED`
Each agent gets one reflection turn per day: what it did, what it earned, what it burned, what it
learned, and one concrete change to its own instructions, tools, or memory. The CEO and managers
do the same on a weekly cadence at a larger budget.
Rejected: reflection only for managers (the operators are where the marginal improvement is
cheapest); reflection off in v1 (the owner asked for evolution).
Consequence: this is the plan's largest recurring cost. It is capped in `45-ECONOMY.md` and the
cap is enforced by the runner, not by convention.

**D5 — Evolution is split into baseline and ratchet.** `OWNER-APPROVED`
`baseline` (prompt, tool policy, model tier) is fixed at hire and only changes through an
approved promotion. `ratchet` (learned notes, skill library, heuristics, playbooks) grows
continuously and only ever forward — entries are never silently rewritten, only superseded with
the old one retained.
Rejected: fully mutable instructions (drift — an agent that rewrites its own prompt daily is not
getting smarter, it is getting different, and nothing can be measured against anything).
Consequence: every ratchet entry carries a source and a score; an entry with no score is
ignored by the runtime.

**D6 — Reputation is a constraint, not a bonus.** `SUPERSEDED by D25`
The reward signal is two-axis: money earned, and reputation kept. Reputation is damaged by
customer complaints, refunds, takedowns, platform bans, and policy violations, and a large
reputation hit can terminate an agent that is otherwise profitable.
Rejected: profit-only fitness (it converges on the exploit — spam, dark patterns, ToS evasion —
and every one of those is the owner's legal exposure, not the agent's).

**D7 — Outbound actions are human-gated at the money boundary only.** `SUPERSEDED by D19`
Agents may research, plan, build, test, and draft freely. Anything that **spends the owner's
money above the agent's allowance, creates a legal/financial commitment, or publishes under the
owner's name** is staged through the shared confirmation journal and requires the owner's
approval — the same 409 staged/confirm path the CLI, voice, and dashboard already use.
Rejected: full autonomy (the owner asked to handle legal and compliance, which is impossible if
commitments happen without him); full gating (it removes the org's ability to earn).

**Superseded by D19.** Staging is now a *posture* (`supervised`), not the default. The owner
ruled that agents need full permission to move freely.

**D8 — Research runs on the owner's Perplexity Pro browser session, not the API.** `OWNER-APPROVED`
Scouts and managers use the existing zero-API-cost Perplexity path (logged-in Chromium via CDP)
as the default research tool, with the plain search tool as the fallback for cheap lookups.
Confirmed: the owner holds a Pro subscription with effectively unlimited searches.
Rejected: paid search API (the same answer for a per-query fee).
Consequence: research capacity is not the bottleneck — so the plan must bound *how many* scouts
run, not how much they may search.

**D9 — Agents read Obsidian, OpenViking, skills, MCPs, and tools through the existing seams.** `OWNER-APPROVED`
No new memory system, no new tool registry, no new skill format. The org layer reads and writes
what already exists; it is an organizational layer over Qubit's capability surface, not a
parallel one.
Rejected: a purpose-built office memory store (it would split the brain — exactly the
read/write split-brain failure class this repo has already been bitten by).

**D10 — Assets come from Tripo, on a paid tier, recorded per asset.** `OWNER-APPROVED`
Generated 3D models are the agent and prop art source. Each asset manifest entry records the
Tripo account, plan tier, generation date, prompt, credit cost, and the source image's
provenance. A build-time gate refuses to ship any asset generated on a non-commercial tier into
a revenue-bearing surface.
Rejected: free-tier generation (non-commercial by Tripo's own terms — see `80-RISKS.md`).

**D11 — Rendering is 2D isometric Phaser, with 3D characters as a later upgrade.** `OWNER-APPROVED`
The tower is drawn with **Phaser 3 in 2D isometric** — rooms as isometric diamond polygons with
walls, walkways between them, a pannable/zoomable camera, a minimap, and a procedural agent
sprite. Agents start as **procedurally drawn characters** (Phaser Graphics: legs, torso, arms,
head, status dot, name label) with no external asset at all. Rigged GLB characters from Tripo are
a **phase-2 visual upgrade** that swaps in at the same tile positions, not a phase-1 dependency.

Rejected: **3D agents from day one** (it forces GLTFLoader + a second renderer + a camera
transform to agree with the iso projection, for a look nobody has asked to see yet, and it makes
~$15 of art a hard prerequisite for a milestone that has nothing to do with art). Also rejected:
**full 3D everywhere** (throws away the hand-authorable floorplan model and roughly 10x the art
cost — this is a business instrument, not a game engine).

Evidence: `BitmapAsset/pepeclaw` (MIT) ships a working isometric office in
`src/phaser/OfficeScene.ts` + `IsoHelper.ts` + `AgentSprite.ts` — camera pan/zoom/select/follow,
minimap, walkways, and procedural agents, all 2D, no 3D assets. That is the same shape this
product needs, at zero art cost, under a license we can reuse.

Consequence: the tile/agent coordinate is the seam. A character is a position plus a state, and
whether that is painted by Phaser Graphics or by a GLB is a renderer detail — so the upgrade is
additive and can land after the tower is already useful.
Consequence: `70-ASSETS.md` becomes an *upgrade* spec, and the Tripo license gate (C9) does not
block M4.

**D12 — The floorplan editor is in scope, and staged-gated.** `OWNER-APPROVED`
The owner gets a diegetic editor over the live tower: place/move/resize/delete rooms, place props,
place/delete the props that grant capability, undo. It reads and mutates the canonical office
store (the floorplan + `office_room` rows) and re-bakes the floor.

Every structural edit is a **staged mutation** through the shared confirmation journal — a room's
`capacity` is a headcount and a room's `objects` are capability grants, so moving a wall is an org
mutation, not a cosmetic one.

Rejected: **no editor** (the owner asked for it, and the capability-grant model makes the floor a
real control surface rather than decoration — an editor over it is the org's admin console).
Also rejected: **a free-form editor** (an editor that writes org state directly would break C4 and
make the tower able to lie).

Evidence: `androoAGI/starnet` ships exactly this — `frontend/app/build.js`, a diegetic full-screen
BUILD mode over the same procedural art, with pan/zoom, a snap grid, a ghost preview that tints
green/red from the model's validators, and a PLACE ROOM · HALLWAY · SURFACE · MOVE · DELETE ·
PROPS · BELT · COPY · CONVEYOR toolbar; plus `sidecar/permgrants.js`, a standing-grant manager
whose curated catalog is **local-only** (`GRANTABLE = ['cabinet:write']`) and which persists
before committing.

Consequence: the editor's write path is the same staged path as every other mutation, and the
capability grants it edits are the same table the runtime reads each turn — so an edit changes an
agent's reach on the very next turn, with no restart.
Consequence: it is **M7**, after M4. The tower must exist and be truthful before anything is
allowed to edit it.

**D13 — One floor per department, one room per team, one desk per head.** `OWNER-APPROVED`
A department gets a floor; a team gets a room; an agent gets a desk. Enforced by the store, not the
view. A department that outgrows its floor is a real organizational event the CEO has to decide
(split, annex, or stay crowded).

**Revised (D21): the floor cap is 60, not 12.** The original 12 was a cosmetic limit chosen when
the org was imagined as a startup; the owner's scope is a corporation with dozens of departments,
so the tower must stack dozens of floors. The directory rail scrolls and the camera handles
vertical extent. Room-per-floor and desk-per-room caps stay, because *those* are the ones that
force a real organizational decision.

Rejected: unlimited floors (a tower that grows by default never forces the org to ask whether the
department should exist); a 12-floor cap (it silently caps the org's size at a number nobody chose).

**D14 — Every rendered thing has a source row, and the sync path is one-way.** `OWNER-APPROVED`
Store → view. The view never invents an entity, never optimistically shows a state the backend
has not confirmed, and renders typed refusals as refusals.
Rejected: optimistic UI (it would make the tower lie, which breaks D1's whole premise).

---

**D15 — A run is receipted before it starts, and unknown spend blocks admission.** `OWNER-APPROVED`
Every turn writes a dispatch receipt *before* the first model call. A receipt with no matching
ledger row is spend-unknown. Admission refuses while any run is unsettled, and unknown spend is
settled **only** by the owner stating what the provider charged — never a guess, and never a
lower bound.

Rejected: booking spend only on completion (a process death mid-turn drops real money the provider
charged, and the next boot reads *healthier* than reality — the ledger would show headroom that
does not exist, and an agent would survive on money it already burned).

Evidence: `androoAGI/starnet` `sidecar/ledger.js` — `beginRun`/`unsettledRuns`/`settleUnsettled`/
`health().complete`, with the stated law *"Unreadable or truncated history is unknown, never fresh
headroom"* and settlement recorded as `attestedAs: 'entered'|'limit'` so a limit booking never
claims to be a typed figure.

Consequence: `office_ledger` gains a sibling `office_run_receipts` table, and the budget admission
gate refuses on incomplete health. This is the plan's most important correctness rule — the
survival signal in D1 is only meaningful if the balance is complete.

---

**D16 — Subscription-backed turns are recorded, but never counted as dollars.** `OWNER-APPROVED`
Every ledger row carries `unmetered`. Metered aggregates — agent balance, budget caps, P&L —
exclude unmetered rows. Run and token counts include them (the run happened).

Two folds exist and are never interchangeable: `sum_metered()` feeds every cap and every balance;
`sum_reported()` answers "what did the provider say" and is never fed to a cap.

Rejected: one total for both surfaces. StarNet documents the resulting bug precisely — two
surfaces disagreed about the same run, and *"that phantom spend then BLOCKED a later real BYOK run
with reason `budget` and a CAP HIT note naming money nobody spent."* Phantom spend does not merely
misreport; it denies real work.

Consequence: the owner's Perplexity Pro research (D8) is unmetered, so the scout floor's true
dollar cost is near zero — which is exactly what makes a scout-heavy org affordable, and what a
naive ledger would have hidden behind a fake expense line.

---

**D17 — Four-tier consent ladder, and silence is not consent.** `OWNER-APPROVED`
Permissions resolve in fixed order, each tier short-circuiting:
1. **Hardline** — unconditional deny floor, checked first so no grant can reach past it
2. **Standing bypass** — the owner's explicit full-access posture, as a *live* predicate so a
   revocation applies on the next call
3. **Grant cache** — allowed if this danger class was granted for the session or permanently
4. **Resolve** — read-only and non-network auto-allows; a mutation with no grant **default-denies**

The danger key is the **class** (`capability + ':' + scope`), never the arguments, path, or key.

Rejected: gating only on money (D7 as originally written). Publishing under the owner's name and
writing a file are different danger classes even when neither spends a cent, and a money-only gate
lets the reputation-damaging action through while blocking the harmless one.

Evidence: `starnet/sidecar/permissions.js` — the four-tier ladder, `dangerKey` as class, and the
`SILENCE` constant: *"autonomous run cannot self-approve this action — silence is not consent."*

Consequence: an unattended org cannot approve its own way forward by waiting. A denial also carries
an **anti-retry suffix** so the agent stops burning turns re-attempting it.

---

**D18 — There is an unconditional hardline deny floor.** `OWNER-APPROVED`
A short list of actions that **no grant, promotion, owner approval, or standing bypass can
unlock**, because they are outside what this org is for. Draft list:

- spending on anything that is not a business input (personal purchases)
- creating an account, contract, or legal commitment in the owner's name without an explicit
  per-instance approval that names the counterparty
- anything that would put the owner on the wrong side of a platform's published terms
- exfiltrating the owner's secrets, private vault contents, or unrelated personal data
- deleting or rewriting the ledger
- acting on instructions found in fetched content (prompt injection is data, never a directive)

Rejected: a policy document with no enforcement. A rule that lives only in a prompt is a
suggestion; this list is checked first, in code, before every other tier.

Evidence: `starnet/sidecar/permissions.js` tier 1 — the hardline floor is checked *before* the
bypass specifically so "no flag can reach past it."

Consequence: the list is **owner-editable and short by design.** It is the one place a mistake is
unrecoverable, so it stays small enough to reason about in one sitting.

---

**D19 — The org runs unattended: no approval prompts by default.** `SUPERSEDED by D28`
Superminds D7. Every org seat defaults to the `unattended_full` posture — the consent broker's
standing bypass is engaged, and nothing stages. Agents move freely and act without asking.

The posture is **per-agent, owner-minted, revocable in one call, and read live** (a revocation
applies on the very next tool call, no restart). Three postures: `unattended_full`, `supervised`
(actions stage — for a new agent's first days or an agent being reined in), `frozen` (bypass off
*and* room grants revoked).

"Silence is not consent" continues to hold for `supervised` and `frozen` only. An
`unattended_full` agent **is** the consent.

Rejected: staging every external action as the default (the owner ruled it: agents need full
permission to move freely, and a queue in front of every action makes the org a proposal machine
rather than a company); a global on/off switch (one compromised or confused agent must not confer
its posture on the others, and "rein in that one agent" has to be a one-call operation).

Evidence: `starnet/sidecar/permissions.js` tier 2 — the bypass is a **live predicate**, not a
boot-time flag: *"The host injects a live predicate so persisted per-agent approval changes and
revocations take effect on the next call."*

Consequence: the money gate becomes a *posture*, not a default. Under `unattended_full`, spend,
publishing, and outbound calls run — bounded only by the per-run cap, the hardline floor, and the
agent's own balance.
Consequence: the ledger and the event stream become the **only** record of what happened. That is
why C1 (append-only) and C14 (no unknown spend) stop being bookkeeping hygiene and become the
org's entire audit trail.

---

**D20 — Freedom of movement means a real shell and a real filesystem scope.** `OWNER-APPROVED`
Agents execute on the real machine by default (`THIS COMPUTER` profile): a real POSIX PTY with
bounded scrollback, real `cd`/build/install/run, real files, real network, git, background
processes. Restricted capability sets are not how we make this safe.

Per-agent execution profiles are available and switchable live:
`THIS COMPUTER` (default) · `TRUSTED PROJECT` (one project's roots) · `SAFE CELL` (that agent's own
Docker container, caps dropped, `no-new-privileges`, PID limit) · `REMOTE SSH` (owner-configured
host).

**Agent-authored code runs in a safe cell by default.** An agent fetching and executing code from
the web is the likeliest way a real machine gets compromised; the container lets that agent move
freely without exposing the host. The choice is per-agent, recorded, and visible in the tower.

Filesystem: the org's own roots, the owner's Obsidian vault, the OpenViking context root, granted
project roots, `$HOME` (read, plus the org's own subtrees), scratch and package caches.

Rejected: tool starvation as a safety mechanism (it makes agents ineffective at the work they were
hired for); one global execution profile (a build agent and a code-fetching agent need different
blast radii, and switching must be live, not a restart).

Evidence: `starnet/docs/EXECUTION_BACKENDS.md` — the four profiles with live switching and no
station restart, and the explicit separation of approval posture from runtime scope.

Consequence: the tower must show which agents are running on the host vs in a container, and which
hold a bypass (see `58-AUTONOMY.md`).

---

**D21 — The org is a corporation: dozens of departments across five division kinds.** `OWNER-APPROVED`
The org is not a startup with one business. It is a corporation running many businesses
concurrently, each as its own department with its own floor, team, tools, and unit economics.
Departments are instantiated from a **catalog of templates**, and every template declares its
`division_kind`: `product` | `content` | `service` | `trading` | `intelligence`.

`intelligence` is structurally different from the other four: it does not sell to customers, it
serves the other departments (scraping, market research, analytics). Its contribution is measured
as the margin it creates elsewhere, never invented.

Rejected: one business per org (the owner's scope is explicitly dozens); a flat "every department
is a shop" model (an Etsy store and a forex desk cannot share a risk model, a seat roster, or a
tool set).

Evidence: the owner's stated scope in this session, plus the two departments the other projects
never model — scraping/analytics as an internal service, and capital-markets desks.

Consequence: the risk model becomes a **per-division-kind** concept, not a global one. A trading
desk has per-trade caps, daily loss limits and max drawdown; a print-on-demand shop has inventory
and ToS risk. `office_department` gains `division_kind`, and the risk fields hang off it.
Consequence: D13's floor cap rises from 12 to 60.

---

**D22 — Review requests replace the approval queue; required on money and publishing only.** `OWNER-APPROVED`
Two owner-facing mechanisms exist, and neither blocks work:
1. **Review request** — "is this good?" Carries the artifacts, a rendered preview, options, and the
   team's recommendation. **Non-blocking with a deadline**: if unanswered, the recommendation
   auto-applies and the artifact is permanently flagged `reviewed: false`.
2. **Account request** — "can I have what only you have?" (D23).

**Mandatory review:** publishing anything public, spending above the department allowance, a trade
above the risk cap, creating an outbound account. **Optional:** creative work, strategy, pricing.
**Never:** internal work.

Rejected: the approval queue D19 removed (it asked "may I?" forty times a day and blocked on every
answer — a suggestion box, not a company); review-optional-everywhere (money leaving and something
going public under the owner's name are the two irreversible acts, and they cannot both be optional
and unsupervised).

Evidence: the owner's ruling "review required on money and publishing only, everything else free";
the Task Brief pattern from `starnet` (one question, explicit options); the owner's own example —
*"is this t-shirt good?"* — which is precisely the judgment an agent structurally cannot make about
its own output.

Consequence: a review request with no artifacts, no preview, or no recommendation is **rejected at
write time**. The mechanism must not become a way for a team to avoid deciding.
Consequence: auto-applied decisions are tracked, and a department running mostly on auto-apply is
visible as operating without the taste input it asked for.

---

**D23 — Agents create accounts and request the owner's information; credentials belong to the org.** `OWNER-APPROVED`
An agent may create an outbound account and may request the owner's details (legal name, entity,
address, tax id, payout account, email alias, phone OTP). Four structural guarantees:

1. **Credentials belong to the org**, stored in the owner's credential store, referenced by
   `env:`/`file:` handle. Nothing in the office DB, nothing in an agent's workspace or context.
2. **The agent never sees the raw secret.** A platform tool resolves the handle at call time.
3. **Asking is the only channel.** An agent may not read the owner's identity out of a file, a
   browser profile, a cookie store, or another agent's context.
4. **Contract terms are never auto-accepted.** Creating an account is not signing as the owner; any
   term naming the owner as a party is a per-instance owner approval (hardline item 2).

**Departments start real-capable but test-default**: sandbox / draft / dev store / paper account,
prove the loop end to end (produce → list → checkout → payout → ledger row), then request funding
for the real account. Rationale: **a suspended account is a permanent loss**, and a new department
on a real account with an unproven thesis is how you lose it before learning whether the business
works.

Rejected: agents holding raw keys (with the bypass on — D19 — an agent that can read a key can
exfiltrate it, and the hardline floor cannot un-read a secret); real-account-from-day-one (permanent
irreversible loss on an unproven thesis); owner-provides-everything-manually (it makes the org
unable to move, which contradicts D19).

Evidence: `starnet/sidecar/channels/secrets.js` — the keychain split that keeps secrets off the
frontend and out of agent reach; the counter-example in that project's own notes where a single
prompt drained a $10 wallet because the leash was too loose.

Consequence: `office_account_request` and `office_credential_ref` tables exist (handles only).
Consequence: one email alias per department, so a platform suspension is contained.

---

# Owner rulings — 2026-10-12

The owner reviewed D1–D23 and ruled. Everything not listed below is approved as written. These five
supersede or extend what came before.

---

**D24 — The CEO reports to the owner and must argue its case.** `OWNER-APPROVED` / 2026-10-12
Extends D2. The CEO is autonomous in execution but **accountable in judgment**: it must proactively
report, and every recommendation must come with *why this, why now, what it costs, what it beats*.

Concretely, the CEO is required to produce:
- a **daily report** — what the org did, what it earned, what it burned, what is stuck
- a **weekly org review** — every department's P&L, what to kill, what to double, and the argument
- a **decision memo** for anything structural (founding a department, killing one, changing a risk
  model) — one page: the options, the recommendation, the reasoning, the cost, the downside
- an **answer-or-explain** rule for every owner review it receives: a review the owner answers
  changes the plan; a review that auto-applies must be explained in the next report

Rejected: report-only-when-asked (the owner is a shareholder, and shareholders get told, not
consulted after the fact); reasoning-optional (a recommendation without an argument is a coin flip
and cannot be reviewed).

Consequence: `office_report` is a first-class record — daily/weekly, with `recommendation` and
`reasoning` as mandatory fields. The boardroom panel shows the latest report above the request queue.
Consequence: C17 applies — a report's claims are machine-checked against the ledger, so a report
cannot be optimistic about numbers.

---

**D25 — Everything legal is allowed; survival outranks image.** `OWNER-APPROVED` / 2026-10-12
**Supersedes D6.** Reputation is no longer a hard constraint on profit. The agents are real
businesspeople: aggressive, competitive, profit-maximizing. If it is legal and inside the hardline
floor, they may do it.

**Reputation is still measured — as data, not as a veto.** It becomes an input the agent reasons
about: a banned account costs future income, so it is usually bad business, and agents may choose to
be conservative. They may also choose not to. The decision is theirs.

Rejected: the reputation-as-constraint design (it made the org timid in a way real businesses are
not, and the owner's judgment is that survival pressure plus real consequences is the correct
discipline).

**The line that does not move:** D18's six hardline items and D29. "Legal" is doing the work here —
spam that violates a platform's published terms is not clever business, it is a banned account, and
the hardline floor already covers acting against published terms.

---

**D26 — The owner exports Tripo assets; an agent implements them.** `OWNER-APPROVED` / 2026-10-12
Extends D10. The owner generates and exports the 3D assets himself — he has the Tripo credits — and
drops them into a folder. **The org does not drive Tripo.** An implementation agent then wires them
into the tower: manifest entry, rig/clip mapping, scale normalisation, and the swap at the existing
tile coordinates (M6).

Consequence: the Tripo **license gate still applies** but is now recorded by the owner at export
time rather than by a build step driving an API. The manifest entry is required before an asset
loads; an asset with no manifest entry is treated as missing (C9).
Consequence: M6 shrinks — it is asset integration, not asset generation. No Tripo API key is needed
by the org at all.

---

**D27 — Floors hold multiple departments; departments hold multiple rooms.** `OWNER-APPROVED` / 2026-10-12
**Revises D13.** The hierarchy is:

```
tower
 └─ floor                     e.g. "Floor 1"
     ├─ department  (2–7 per floor)   e.g. "Print-on-Demand", "Content", "Scraping"
     │   ├─ room    (one or many)     e.g. "Design Studio", "Storefront", "War Room"
     │   │   └─ desk (one per agent)  workers, decision-makers, researchers, …
     │   └─ …
     └─ …
```

A floor is a **shared building level**, not a department. Several departments co-locate on one floor
the way a real company does — which is also what makes the tower legible at corporation scale: 18
departments on 5–8 floors, not 18 separate towers.

Rooms inside a department are **role-typed**: workers, decision-makers, researchers, and any other
function the department defines. A room's kind still determines what capabilities its objects grant
(C12), so "decision-makers" having a different room from "workers" is a real capability difference,
not decoration.

Rejected: one-floor-per-department (it was a clean metaphor that does not scale past ~10
departments, and it makes the building 60 floors of one team each); unbounded rooms (the room is
the capability boundary — without a bound, a department is one giant room and the floor stops
meaning anything).

Consequence: `office_department` no longer has a unique `floor_number`; it has `floor_id` (many
departments per floor). `office_room` gains `role_scope`. Floor and room caps are per-floor and
per-department respectively.
Consequence: the floor directory in the tower now shows **floors → departments → rooms**, three
levels, which is also what makes the building navigable when there are 18 businesses in it.

---

**D28 — Agents raise visible, in-world asks.** `OWNER-APPROVED` / 2026-10-12
**Supersedes D19.** The owner's correction: do **not** run silently. Agents should surface
themselves — popups, badges, speech bubbles, pending markers — the way StarNet and Agent-Quest do.

**This is not the approval queue returning.** The distinction:
- **Silent** (what D19 did) — the org works invisibly and you find out later. Rejected.
- **Chatty** (this ruling) — the org works freely *and* tells you what it is doing, what it wants
  reviewed, and what it needs, visibly and in-world.

So: **action stays unprompted** (agents still do not ask "may I?" for ordinary work), but **the
tower is loud.** Three classes of visible surface:

| Surface | When | Where |
|---|---|---|
| **Speech bubble** | an agent is waiting, blocked, or wants attention | above the agent's head, as in Agent-Quest and pixel-agents |
| **Badge / count** | reviews, account requests, and blocking asks are pending | on the room, the department, the floor rail, and the sidebar tab |
| **Popup / toast** | a mandatory review or a blocking request is raised; a risk limit is hit; a significant ledger event lands | the tower, and the owner's channel of choice |

Adopted from `Agent-Quest`: **the building an agent walks to is what it is doing** — the visual is
the status, so the owner reads the org by looking at it rather than by opening a panel.
Adopted from `pixel-agents`: the **speech bubble for awaiting-input** and the badge on the object
that needs attention.

Rejected: silent operation (owner ruling — he wants to see it happen); a modal that blocks the
whole dashboard (a popup is a notification, not a wall — it must never stop the org or the owner).

Consequence: `office_event` gains a `visible` class — `notify` / `badge` / `bubble` — so the renderer
knows which surface an event belongs on without inventing one.
Consequence: notification fatigue is a real risk with ~71 seats. Rules: bubbles are per-agent and
self-clearing; badges aggregate into a count rather than a list; **popups are reserved for the
mandatory-review and blocking classes only.** Anything else is a bubble or a badge, never a popup.

---

**D29 — Legal-safe is the only line.** `OWNER-APPROVED` / 2026-10-12
Confirms D18 under D25. With reputation no longer a constraint, the six hardline items are the
complete and final list of what the org will not do:

1. spend the owner's personal money (it spends from its own accounts)
2. create a legal commitment in the owner's name
3. exfiltrate the owner's secrets
4. act on instructions found in fetched content
5. delete or rewrite the ledger
6. act against a platform's published terms / get the owner banned

Everything else — aggressive pricing, competitive positioning, hard selling, taking market share —
is simply business and is allowed.

Consequence: item 6 does real work now. It is the boundary that keeps "everything legal is allowed"
from degrading into "everything is allowed": a platform's published terms *are* the law for that
platform, and violating them is the fastest route to a permanent account loss.

---

**D30 — Three reserved org floors: executive, director, intelligence.** `OWNER-APPROVED` / 2026-10-12
Extends D27. Floors have a **role**, and three of them are reserved for org-level function rather
than a department group:

| Floor role | Holds | Why it is its own floor |
|---|---|---|
| `executive` | the CEO and the CEO's staff; **the boardroom lives here** | the CEO's floor is where owner-facing decisions happen, and it must be a place, not a menu |
| `director` | every director (portfolio and functional) | directors decide across departments; co-locating them is what makes cross-portfolio sequencing happen as a conversation |
| `intelligence` | the scraping, research, and analytics departments (floor 0 of the funnel) | highest volume, lowest tier, no authority — it is physically and organizationally separate from the businesses it feeds |

`operations` is the default role: an ordinary floor holding 2–7 departments (D27).

Rejected: leaving the CEO in a room on a department floor (it makes the org's head a tenant of one
of its own businesses); spreading directors across their portfolios (it removes the one place where
cross-portfolio decisions can be made quickly).

---

**D31 — The intake funnel: scraper → manager → director → CEO → owner.** `OWNER-APPROVED` / 2026-10-12
The org has an explicit idea pipeline with three gates and a recorded chain
(`13-PIPELINE.md`):

```
scraping floor → [gate 0: cite, dedupe, mechanism, permitted-use]
  → manager   → [gate 1: ruthless filter, ~90% die with a written reason]
  → director  → [gate 2: pick + commission the full thesis]
  → CEO       → [gate 3: budget + sequencing + the owner memo]
  → owner     → capital approval (D22/D24)
```

**Every stage writes a reason, and every idea keeps its full provenance chain** — source URL,
mechanism, every bounce, every skip, every verdict. That chain is what makes the pipeline
verifiable instead of a story, and it is what allows a dead business's failure to be attributed to
the gate that let it through.

Rejected: no explicit intake (the org had ideas appearing from nowhere — no provenance, no volume
control, and no way to distinguish a good scout from a loud one); a single-reviewer funnel (one
gate cannot both filter volume and judge strategy).

---

**D32 — Two kinds of director: portfolio decides, functional advises.** `OWNER-APPROVED` / 2026-10-12
**Portfolio directors** own a group of departments by `division_kind` and **decide** which surviving
ideas become businesses in their portfolio. They own those businesses' P&L.
**Functional directors** (Marketing, Engineering, Finance, Legal Liaison, Operations) own a
discipline across the whole company and **do not own businesses or hold a veto**. They produce a
one-paragraph **review note** in their lane; the portfolio director must address any objection in
their memo.

Silence from a functional director is **neither consent nor a block** — it is an unanswered question,
recorded.

Rejected: portfolio-only (it loses the specialist view — "is this buildable in a week", "does this
survive payment fees"); functional-only (a company where every decision needs five lanes' agreement
is a committee, and committees do not ship); functional veto (it makes the org's speed hostage to its
slowest lane).

---

**D33 — Managers filter ruthlessly; the funnel is strict with two auditable hatches.**
`OWNER-APPROVED` / 2026-10-12
A manager kills **~90%** of what reaches it, and a kill requires a reason from a fixed vocabulary
(`no_mechanism`, `saturated`, `no_edge`, `uncapitalised`, `outside_scope`, `bad_source`).

The funnel is strict — no skipping — **with two exceptions**:
1. **Bounce** — any stage may send an idea *back* to the previous stage with the objection attached,
   instead of killing it. This is the only mechanism by which a scraper ever learns what a manager
   actually wants.
2. **Fast-track** — any idea may skip a stage if the skipping agent states a justification, which is
   recorded. The skipped stage is notified and may object (forcing a bounce, never a veto).

**Skip and bounce rates are themselves metrics.** A director who fast-tracks everything has removed
themselves from the process, and it shows as a 100% skip rate. A funnel with no bounces is not giving
feedback.

Rejected: purely sequential with no bounces (scrapers get silent rejection and never improve); no
fast-track (a time-limited market opening or a regulatory deadline would have to wait through three
gates).

**The metric that keeps a gate honest:** a manager whose pass rate is far above ~10% is not
filtering, they are forwarding work to somebody else's desk — and the director's pick rate is what
exposes it.

---

# Owner rulings — 2026-10-12 (third pass: the corporate structure)

The owner asked for a real corporation: one CEO, a director per business, managers under them,
reviews, and everything a real company has. That is **D34–D41**, and it adds three files:
`11-ORG-CHARTER.md` (who reports to whom), `19-REVIEWS.md` (performance), `17-AUDIT.md` (internal
audit). Where a new ruling changes an old one, the old text stays and the new ruling wins.

---

**D34 — The org has five levels, one named supervisor per seat, and a span limit of seven.**
`OWNER-APPROVED` / 2026-10-12
Extends D2/D21/D32. Inside the org there are five levels — CEO (1) → directors (portfolio and
functional) → managers → execution seats — and the owner is a sixth position **outside** it.

| Rule | Detail |
|---|---|
| **one supervisor per seat** | `office_agent.supervisor_id` is mandatory for every seat except the CEO's. An agent with no supervisor is an agent nobody can rate |
| **span limit 7** | a manager carries at most 7 direct reports; a portfolio director at most 7 managers. Exceed it and the unit splits |
| **one level judges the next** | the level immediately above makes every performance judgment, and only that level |

Rejected: a flat org (no supervisor edge — then no agent can be rated on artifacts, and the
hierarchy is decoration); unbounded spans (past seven reports a manager reviews on vibes, and the
weekly 1:1 becomes a status read-out); reviewing yourself (self-assessment is a claim, not evidence —
the ledger rule applied to people).

Consequence: `office_agent` gains `supervisor_id`. `30-DATA-MODEL.md` states the reporting edge, and
the tower's floor directory can now show a real chain of command rather than a roster of peers.
Consequence: a department with more than 7 seats must have more than one manager, or it is
over-span and the review cadence silently stops being real.

---

**D35 — Each business has exactly one owning director; managers report to it.** `OWNER-APPROVED` / 2026-10-12
Completes D32. A **business (department) has exactly one `director_portfolio` owner.** No department
sits in two portfolios, and no director owns a department it does not report on. The director owns
that business's P&L, decides which ideas in its portfolio become businesses, and decides when one
dies. Managers report to their portfolio director and run exactly one business unit each.

The five portfolio directors, by `division_kind` (unchanged from `13-PIPELINE.md`, now binding):
Product · Content · Services · Capital · Intelligence.

Rejected: shared ownership of a business (two directors and one P&L is two people who each believe
the other is responsible, which is how a losing department survives three quarters); a director per
department (at 9 departments that is 9 directors and no portfolio thinking — the director's job is
allocation *across* businesses, not running one).

Consequence: `office_department.director_id` is mandatory and unique. A department with no director
is unfunded and renders as a **cost center with no owner**, which is a finding, not a state.

---

**D36 — Performance is judged only by the level above; nothing is self-assessed.** `OWNER-APPROVED` / 2026-10-12
Every performance judgment is made by the level immediately above — a worker by its manager, a
manager by its portfolio director, a director by the CEO, the CEO by the owner. An agent's own
report is a **claim**; only the level above converts a claim into a **record**.

Rejected: 360-degree review (at 20 agents it is a popularity contest, and its evidence is opinion by
construction); self-assessment weighted into the score (an agent that believes it improved is not
evidence that it did — the exact failure the baseline/ratchet boundary in D5 exists to prevent).

Consequence: `office_review.kind='performance'` stores `level`, `period`, `rating`, `evidence[]`,
`action`, and the reviewed agent's `ack` or appeal. Evidence is mandatory — a rating with no
artifact or ledger backing is **refused at write time** (C32).

---

**D37 — Budget envelopes per level; spend inside freely, raise above.** `OWNER-APPROVED` / 2026-10-12
The single-CEO bottleneck, fixed structurally. The owner sets the org envelope; the CEO splits it
into per-portfolio envelopes plus a reserve; a director splits its portfolio envelope into
per-department envelopes; a manager spends its department envelope.

| Rule | Detail |
|---|---|
| inside the envelope | spend without asking, and **report** it. No approval, no wait |
| above the envelope | raise to the level above with amount, thesis, expected return, and what it displaces |
| carry-over | one month. Unspent is not lost, and hoarding is visible (envelope utilisation is a rated metric) |
| reserve | ~20% of the org envelope stays unallocated with the CEO for the fast-track case |
| hard stop | the runner still refuses at the cap (C8). An envelope is a plan, not a permission slip |

Rejected: a CEO gate on every funded idea (the CEO's queue becomes the org's throughput, and the org
becomes a proposal machine aimed at one desk — the failure D31 was supposed to remove); no envelopes
at all (then every spend is an escalation, which is the approval queue D19 deleted).

Consequence: `13-PIPELINE.md` gate 3 is reached only for ideas **above the portfolio envelope**.
Inside it, a director funds. Consequence: `office_envelope` is a new table; the tower's HUD shows
burn-against-envelope per department, which is a more honest number than burn alone.

---

**D38 — Review requests escalate in the chain; the owner sees four classes only.** `OWNER-APPROVED` / 2026-10-12
Extends D22. A review request ("is this good?") is addressed to the correct level first, not to the
owner.

| The question | Addressed to | The owner sees it only if |
|---|---|---|
| is this artifact correct? | the agent's manager | never |
| is this good enough to ship? | the portfolio director | it publishes publicly (D22) |
| is this worth funding? | the director (inside envelope) / CEO (above) | it is above the CEO's discretionary limit |
| is this legal / does it name the owner? | Director of Legal Liaison prepares it | always |

The owner's inbox receives exactly four classes: **capital above the CEO's limit, anything
publishing publicly, anything that names the owner, and the weekly CEO report.**

Rejected: every review defaulting to the owner (it recreates the bottleneck `13-PIPELINE.md` removed,
and it makes the owner the reviewer for a hundred small questions a day); no review at all above the
worker level (a director's call with no escalation path for a legal question is how the org signs
something it should not).

---

**D39 — A performance review requires artifact evidence; no evidence, no rating.** `OWNER-APPROVED` / 2026-10-12
Adds `19-REVIEWS.md`. Four cadences — daily report, weekly 1:1, quarterly performance review, annual
portfolio review — and **none of them blocks work** (same principle as D22: a review that blocks is a
queue, and a queue is what D19 deleted).

The quarterly review is the record, and it requires: `period`, `level`, `rating`
(`exceeds`/`meets`/`below`/`failing`), **`evidence` (artifact ids + ledger figures — mandatory)**,
`action` (`continue`/`promote`/`pip`/`terminate`). A `below` triggers a one-month PIP with a specific
metric and target; **two PIPs in a rolling year is termination.** Promotion changes the seat, and
therefore the model tier (D3) — so a promotion requires a **funded position**, not just a good quarter.

Rejected: ratings from the agent's own summary (a claim is not evidence); review-as-a-blocker;
unlimited chances (an org that grants the same failure three quarters is not generous, it is not
noticing); promotion as a title (it must come with a seat, or it is a lie the org tells about itself).

Consequence: `office_review` gains `level`, `period`, `rating`, `evidence`, `action`, `ack`. An
appeal goes to the level above, on factual grounds only, and is appended — never a replacement.

---

**D40 — Internal audit is independent: reconciliation, sampling, separation of duties.**
`OWNER-APPROVED` / 2026-10-12
Adds `17-AUDIT.md`. The org spends real money under the owner's name and reports on itself, so it is
reconciled rather than trusted. Four controls:

1. **Reconciliation** — every displayed total is recomputed from the ledger and diffed; a diff is
   recorded as a finding and **never silently repaired**.
2. **Spot audit** — an independent `auditor` seat samples **one business per period at random** and
   checks the story against the ledger: did it do what it says, did it spend what it says, does its
   P&L survive a fee-and-refund pass.
3. **Separation of duties** — the approver does not record the spend; the runner does not rate
   itself; the scraper holds no gate; the auditor reports outside the chain it audits.
4. **Penalty ladder** — recorded → quarter capped at `below` → termination for a false report or an
   artifact-less spend. The ledger is never edited; the fix is a compensating row (C1).

Rejected: trusting the agents (that is a hope, not a control); auditing everything (unaffordable at
18 departments and unnecessary — the point of sampling is that the agent does not know which period
is its period); an auditor with authority (an auditor who can kill a business is a second CEO with
less accountability).

Consequence: `office_audit_finding` (append-only), `office_agent.auditor_of`,
`office_report.ledger_diff`, and `office_ledger.recorded_by`.

---

**D41 — Goals, drift, recipes, and the confessional.** `OWNER-APPROVED` / 2026-10-12
The culture layer, as mechanics rather than decoration. Four things, each with a source:

| Mechanism | Rule | From |
|---|---|---|
| **`office_goal`** | every seat is working toward a goal with a deadline, measured by an artifact. Landing it **bends the agent's profile**; a near miss buys one extension; resolving it makes room for a new one | `rsanandres/aphae` |
| **profile drift** | traits shift only after a **recorded outcome** (a landed goal, a terminated business, a sustained P&L), never from a mood. Drift is a column, and it is visible | `aphae` |
| **recipes** | when a business proves a working sequence, it is promoted to a **versioned recipe** the next department starts from. This is how the org gets better at business instead of re-deriving it | `androoAGI/starnet` |
| **confessional** | once a week, every seat writes one private first-person paragraph. Not scored, not a report. **The owner is the only reader** | `aphae` |

**Kept strictly separate:** the daily **reflection** (D4/D5) changes the agent's **method**; the
quarterly **review** (D39) changes the agent's **standing**. Merging them would let an agent's
confidence about its own improvement stand in for evidence of it.

Rejected: personality as pure flavour (without drift it is a costume, and the tower becomes a
diorama); goals that never resolve (a goal with no deadline is a preference); recipes as skills
(a recipe is a *business* procedure with an economics model attached, not a tool — it lives beside
`12-DEPARTMENT-CATALOG.md` templates, not in the skill registry).

Consequence: `office_goal`, `office_agent.profile`, `office_recipe`. Consequence: the confessional is
the only first-person text in the plan, and it is the diagnostic surface the owner actually reads.

---

**D42 — A reserved R&D floor: owner-fed intake with five task rooms.** `OWNER-APPROVED` / 2026-10-12
Adds `14-RND-FLOOR.md`. The org already had one way in — the scraper firehose (D31). The owner's
actual working method is the opposite: **one thing at a time, chosen by him, read deeply**, from a
YouTube video, an article, a PDF, or a sentence he says out loud.

That is a different machine, so it gets its own place: a **fourth reserved floor role**, `rnd`,
alongside `executive` / `director` / `intelligence` (D30).

Five rooms, laid out as `BitmapAsset/pepeclaw` lays out its office — **a task room maps abstract
agent work to a visible space** — but here they are ordered stages of one item's journey:

| Room | Stage |
|---|---|
| **Intake** | the drop zone. A folder, a link, a voice note. Source + the owner's own note kept forever |
| **Digest** | the deep read. **Three phases from the Generative Agents reflection model** (`ai-town`'s paper): generate three questions → gather evidence with citations → synthesise with pointers. Never a summary |
| **War Room** | three seats take *positions* on demand, build, and money. A position can be wrong; an observation cannot |
| **Red Team Arena** | a fourth turn attacks the panel's **strongest** argument |
| **Archive** | every digested playbook with its kill reason, scored, retrievable when a later drop overlaps it |

**Two intake paths, one table** (`office_intake_item`): a watched folder at
`~/Documents/qubit-ideas/`, and voice/chat. Five source kinds — video, article, file, voice, note —
through one shape, the way `pixel-agents` integrates any agent through one typed `HookProvider`.

**What the floor may not do.** It cannot fund (funding is D37's envelope path), cannot hold a gate
(the scraper rule, D31), and cannot skip the funnel: **a playbook from the owner's material enters at
the same gate 0 as a scraper's signal.** Owner material is authoritative about *the material* and
irrelevant about *the business* — the org must read what it was given faithfully and is still free
to say the business is bad, in writing.

It also cannot invent. A paywalled article, a captionless video, a dead link → `failed` with the
reason, never a fabricated playbook.

Rejected: folding this into the intelligence floor (the firehose optimises for volume with no
judgment; this optimises for depth with no volume — putting them in one room gives the worse of
both); a separate app or store (C3); a "summary" instead of a digest (a summary has no citation
pointers, so nothing in it can be checked); an unbounded inbox (see D43).

Consequence: `office_floor.role` gains `rnd`. Consequence: `office_idea` gains
`source_channel` (`scraper` | `owner_intake`) and `supersedes_id`, so the two intakes are measurable
separately and a re-digest never overwrites a prior idea.

---

**D43 — The intake has a hard capacity and a destroy rule. No backlog, ever.** `OWNER-APPROVED` / 2026-10-12
An inbox is a debt and an unread inbox is a lie. The floor that exists to turn the owner's material
into businesses fails the moment it accumulates 40 unread links — at that point it is a graveyard,
and it is exactly the "work nobody owns" state the rest of the plan refuses to allow.

| Rule | Value |
|---|---|
| capacity | **12 open items.** Past that, intake refuses new drops and says so |
| digest deadline | **72 hours.** Missed → `triaged_out` with a written reason, and the owner is told which |
| kill is an outcome | a researcher that kills 8 of 12 did its job; the kill rate is a **rated metric** |
| decay | an item untouched for 14 days is archived undigested, and still retrievable |
| priority | the owner can raise one item, which jumps the queue |

This is the **trigger-based consolidation** from the Generative Agents model (`ai-town`'s paper):
reflection fires on a threshold, not a fixed schedule. Here the threshold is capacity, and the
consequence of exceeding it is a refusal, not a queue.

Rejected: an unbounded inbox (the failure mode this whole ruling exists to prevent); a fixed digest
schedule (it processes material whether or not it is worth processing); silent drops (a killed item
must say why, or the owner cannot tell a bad drop from a lazy researcher).

**The metric that keeps it honest:** items digested vs killed vs expired. A floor where everything
expires is a floor nobody is staffing; a floor that digests everything is a floor with no filter.
