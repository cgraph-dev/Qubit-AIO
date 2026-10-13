# 05 — Amendment Log

The plan changed shape several times during authoring. This file records what changed, so a reviewer
can tell a **deliberate revision** from a **contradiction**, and does not spend their time
re-deriving decisions already made.

Read this before flagging an inconsistency. If the inconsistency is listed here, it was resolved —
say so and move on. If it is **not** listed here, it is a genuine finding.

---

## What changed, in order

| # | Change | Files touched | Reason |
|---|---|---|---|
| 1 | **D7 superseded by D19.** Staging every external action → no prompts by default | `10`, `45`, `50`, `57`, `58`, `75`, `80`, `90`, `95`, `15` | Owner ruling: agents need full permission to move freely |
| 2 | **D11 rewritten.** 3D agents over 2D rooms → 2D isometric Phaser, procedural agents, GLB as a later upgrade | `10`, `20`, `65`, `70`, `90` | Reading pepeclaw's actual source showed its live renderer is 2D; 3D-as-prerequisite made art a blocker on a non-art milestone |
| 3 | **D12 flipped.** "No editor" → editor in scope, staged-gated, as **M7** | `10`, `65`, `75`, `90` | Owner asked for it; and object=capability (from starnet) makes the floor a real control surface, so an editor over it is an admin console, not decoration |
| 4 | **D13 revised.** Floor cap 12 → 60 | `10`, `30`, `45` | D21: dozens of departments. 12 was a cosmetic limit from when the org was imagined as a startup |
| 5 | **D15–D18 added** from reading starnet's code: run receipts + unknown-spend blocking, unmetered ≠ dollars, the four-tier consent ladder, the hardline floor | `10`, `30`, `40`, `45`, `95`, `99` | Each fixes a way the plan would have failed in production; each is taken from code written *because* the failure already happened |
| 6 | **D19–D20 added.** Unattended by default; real shell + execution profiles | `10`, `30`, `45`, `55`, `58`, `95` | Owner ruling: full permission, freedom of movement |
| 7 | **D21–D23 added.** Corporation scale (5 division kinds, 18 templates); review requests; agents create accounts and request owner info | `10`, `12`, `30`, `45`, `59`, `95` | Owner ruling: dozens of departments, review on money + publishing, test accounts from day one, full account creation |
| 8 | **Economy table rescaled** for corporation headcount (target $264/mo → full corp $870/mo) | `45`, `README` | Consequence of D21; the old table described a startup |
| 9 | **`57-BOARDROOM.md` extended** by `59-REVIEW.md` | `57`, `59` | The boardroom originally modeled only blocking requests; reviews and account requests are the other two owner-facing classes |
| 10 | **Economy table recomputed.** ~$20/$57/$210/$870 → **~$105/$215/$630/$1,150** | `45`, `README`, `80` | Directors were missing from the headcount while D32 had put them in the org; the table now follows the file's own price card |
| 11 | **Stale status banners cleared.** `10-DECISIONS.md` and `95-CONTRACT.md` both still said `PROPOSED`; `80-RISKS.md` still listed "No editor (D12)" as a non-goal; `00-IDENTITY.md` still said a floor is a department; both files said "six tables" | `10`, `95`, `80`, `00`, `README`, `30`, `99` | D12 was flipped to M7, D27 split floors from departments, and the model grew past six tables — the banners and non-goals were never re-read after |
| 12 | **Seat vocabulary unified.** `scout` → `scraper`/`analyst`/`researcher`; `18`/`120 agents` corrected to **71 seats** | `13`, `45`, `80`, `README`, `99` | D31 split the old `scout` seat three ways; the economy and handoff still counted it |

## Decisions that were REWRITTEN (do not report the old text as current)

| Was | Now | Where |
|---|---|---|
| "No editor in v1" | Editor in scope, M7 | D12 |
| "Outbound actions human-gated at the money boundary" | No prompts; two mandatory reviews | D7 → D19, D22 |
| "3D agents, 2D rooms" | 2D isometric Phaser, procedural agents | D11 |
| "Floor max 12" | Floor max 60 | D13 |
| "Target org: 25 workers, ~$264/month" | Full corp ~870/month, staged | `45-ECONOMY.md` |

## Owner review — 2026-10-12 (D1–D23 approved; five rulings recorded)

The owner reviewed every ruling. Result: **D1–D23 approved** except two rejected and two modified.

| Ruling | Owner's verdict | Result |
|---|---|---|
| D1, D3, D4, D5, D8, D9, D11, D12, D14, D15, D16, D17, D18, D20, D21, D22, D23 | approved as written | `OWNER-APPROVED` |
| **D2** | approved, **but the CEO must consult and report** | extended by **D24** |
| **D6** | **rejected** — "they need to survive, everything is allowed, they are real businessmen" | **superseded by D25** (reputation is data, not a veto) |
| **D10** | approved, **but the owner exports the assets himself** | extended by **D26** |
| **D13** | approved, **revised: floors hold multiple departments, departments hold multiple rooms** | revised by **D27** |
| **D19** | **rejected** — "make them pop up … think of something good" | **superseded by D28** (loud autonomy, not silent) |
| D7 | already superseded | remains `SUPERSEDED by D19`, itself superseded by D28 |

**New rulings: D24–D29.** See `10-DECISIONS.md` § Owner rulings.

### The three structural consequences a reviewer must check

1. **D27 changes the hierarchy.** Floor → department (2–7) → room → desk. `office_department.floor_number`
   became `floor_id`; `office_room` gained `role_scope`. **The tower directory is now three levels.**
   Check `30-DATA-MODEL.md` and `55-DASHBOARD-PAGE.md` agree.
2. **D25 removes a brake.** R3 (agents optimize into ToS violations) is now defended by D29 item 6
   alone. This is the **thinnest defence in the plan** and the highest-value thing to attack.
3. **D28 reverses D19's silence.** Agents act unprompted but surface everything. Check
   `95-CONTRACT.md` C25 — `notify` is reserved for mandatory reviews and blocking requests only, or
   71 seats will generate noise the owner stops reading.

## Owner review — 2026-10-12 (second pass: the intake pipeline)

The owner identified a structural gap — **the org had no intake** (ideas appeared from nowhere) —
and specified a funnel. Recorded as **D30–D33**.

| New ruling | What it adds |
|---|---|
| **D30** | Three reserved org floors: `executive` (CEO + the boardroom), `director`, `intelligence` (the intake) |
| **D31** | The funnel: scraper → manager → director → CEO → owner, with a full provenance chain per idea |
| **D32** | Two director kinds: **portfolio** (decides, owns P&L) and **functional** (advises, no veto) |
| **D33** | Managers filter ~90%; the funnel is strict with two auditable hatches (bounce, fast-track) |

**New file: `13-PIPELINE.md`.** New table: `office_idea` (with `source_url`, `fetched_at`,
`mechanism` mandatory). New seat types: `director_portfolio`, `director_functional`, `scraper`,
`analyst`, `researcher` (the old `scout` split three ways). `office_floor` gains a `role`.

**The gap this closed:** before D31 a proposal had no provenance, no volume control, and no way to
tell a good researcher from a loud one. Now every idea carries its source, its mechanism, every
bounce, every skip, and every verdict with a reason — which is what makes a dead business
attributable to the gate that let it through.

**Check these when reviewing:** `13-PIPELINE.md` (gate rules coherent?) · `50-INTEGRATION.md` (do
the new seat capability rows match the gates?) · `95-CONTRACT.md` C27–C29 (provenance, director
authority, and funnel metrics — all provable?)

## Owner review — 2026-10-12 (third pass: the corporate structure)

The owner asked for a real corporation — one CEO, a director per business, managers under them,
reviews, and everything else a real company has — and for the six reference repos to be mined again
for anything still unused. Recorded as **D34–D41**, with three new files.

| New ruling | What it adds |
|---|---|
| **D34** | Five levels; **one named supervisor per seat**; span limit 7 |
| **D35** | Each business has **exactly one owning portfolio director**; managers report to it |
| **D36** | Performance is judged **only by the level above**; nothing is self-assessed |
| **D37** | **Budget envelopes per level** — spend inside freely, raise above; ~20% CEO reserve |
| **D38** | Review requests **escalate in the chain**; the owner sees four classes only |
| **D39** | A review requires **artifact evidence**; PIP, promotion-with-a-seat, termination, appeal |
| **D40** | **Independent internal audit**: reconciliation, random spot audit, separation of duties |
| **D41** | Goals with deadlines, profile drift, recipes, the weekly confessional |

**New files: `11-ORG-CHARTER.md`, `19-REVIEWS.md`, `17-AUDIT.md`.** New tables:
`office_envelope`, `office_goal`, `office_audit_finding`, `office_recipe`. New columns:
`office_agent.supervisor_id` / `.profile`, `office_department.director_id`,
`office_review.{level,period,rating,evidence,action,ack}`, `office_report.ledger_diff`,
`office_ledger.recorded_by`. New seat: `auditor`. New contract rules: **C30–C32**.

**The gaps this closed:**
1. **The org had no chain of command.** D32 created two kinds of director but gave no seat a named
   superior, so nothing in the schema could answer "who rates this agent". D34/D36 fix that.
2. **The CEO was a single point of failure.** Every funded idea reached one desk; at 18 departments
   that desk *is* the org's throughput. D37 replaces the approval with an envelope.
3. **The org reported on itself with no control.** D40 adds reconciliation, sampling, and separation
   of duties; without them the plan's central claim (the numbers are real) rests on eight agents
   choosing to be accurate.
4. **There was no path between levels, and no way to fail.** D39 adds ratings, PIP, promotion, and a
   sourced termination path with an appeal.

**The six reference repos, mined again.** What was still unused and is now taken: StarNet's
**Task Brief** (one question with options) formalised as the review format; its **OUTBOX** (finished
work has a front door) as the per-department deliverables shelf; its **Night Shift leash** as the
overnight posture; its **recipes** as the org's reusable business procedures; `aphae`'s **goals that
resolve and bend the personality**, **confessional cam**, and **mole** (recast as a spot audit);
`ai-town`'s **shared-state agent conversation** as the gossip substrate behind `office_message`;
Agent-Quest's **activity→building** walk as the reason a room is a scope, not a skin; and
`agent-monitor`'s **per-session policy + run-now** as the manager's override on a stalled department.
`aphae` remains **all rights reserved** — ideas only, no code, no assets.

**Check these when reviewing:** `11-ORG-CHARTER.md` (does the chain hold at 9 departments without the
CEO becoming a gate again?) · `19-REVIEWS.md` (can every rating be produced from the ledger alone?) ·
`17-AUDIT.md` (does the auditor have enough authority to be believed and too little to be a second
CEO?) · `95-CONTRACT.md` C30–C32.

## Owner review — 2026-10-12 (fourth pass: the R&D floor)

The owner identified a gap the funnel did not cover: **he wants to feed the org himself** — a YouTube
video, an article, a PDF — and have it digested into business ideas. Recorded as **D42–D43**, new
file **`14-RND-FLOOR.md`**.

| New ruling | What it adds |
|---|---|
| **D42** | A fourth reserved floor role, **`rnd`**: owner-fed intake with five task rooms (Intake → Digest → War Room → Red Team Arena → Archive) |
| **D43** | A **hard capacity of 12 open items**, a 72-hour digest deadline, a real kill outcome, and no silent closes |

**Why it is a floor and not a department:** the R&D floor sells nothing — it feeds the businesses,
which is what a reserved floor is for (D30). And it is **not** the intelligence floor: the firehose
optimises volume with no judgment, this optimises depth with no volume, and a single room holding
both gives the worse of each.

**The two-intake rule:** an owner-fed playbook becomes an `office_idea` with
`source_channel='owner_intake'` and enters at **gate 0 with no bypass**. Every funnel metric now
splits by channel, which is the only honest way to answer *"is the owner's taste better than the
firehose?"*

**What was taken from the reference repos this pass** (the owner asked for all seven to be re-read,
not just the two the earlier passes leaned on):

| Taken | From | Where it landed |
|---|---|---|
| **Task rooms map abstract work to visible rooms** (8 named rooms: War Room, Red Team Arena, Dream Chamber, Identity Vault, …) | `BitmapAsset/pepeclaw` | the five rooms of the R&D floor, **by name and function** — "Dream Chamber" is the Digest, "War Room" and "Red Team Arena" are adopted as-is |
| Three-phase reflection: questions → evidence → synthesised insights **with citation pointers** | **Generative Agents**, the paper `a16z-infra/ai-town` implements | the Digest, verbatim in shape. This replaced a plain "read it and write notes" |
| Reflection fires on a **threshold**, not a schedule | same paper / `ai-town` | the no-backlog rule: capacity 12, deadline 72h |
| Conversations summarised into a **scored memory**, retrieved later by relevance | `ai-town` (`rememberConversation`) | the Archive: playbooks scored, and past ones retrieved when a new drop overlaps |
| "Let information move between agents … it creates information asymmetry" | `rsanandres/aphae` PLAN.md Phase 3 | the panel sees the digest and each other, never the source mid-read |
| One living doc, amended not rewritten; **"a stale doc is worse than no doc"** | `aphae` PLAN.md, `starnet` docs/INDEX.md | `05-AMENDMENT-LOG.md` (this file) is now explicitly the plan's living record — see the rule added below |
| Typed single boundary for many integrations (`HookProvider`); five source kinds, one shape | `pixel-agents` | `office_intake_item` accepts video/article/file/voice/note through one table |
| Demo-mode fallback, clearly labelled | `pepeclaw` | an empty floor renders **"no material dropped"**, never an invented busy lab (C7) |
| Plans drift within hours; **verify claims against current code** | `starnet` docs/INDEX.md | the rule added below |

**Two rules this pass added to the plan's own method** (both from the reference repos reading their
own mistakes):

1. **A doc claim about the repo is verified against the repo before it is trusted.** StarNet's index
   states its plan docs "go stale within hours" and tells readers to grep the code and re-prove any
   claim that something is missing or broken. This plan made exactly that class of error once already
   (`15-REVIEW.md`'s M3 blockers, `80-RISKS.md`'s "No editor", the cost table). **Any future claim
   here about what the repo contains gets checked against the repo in the same pass it is written.**
2. **This amendment log is the living record, and the body files defer to it.** A ruling that is
   rewritten in place leaves the old text somewhere else; the log is what tells a reviewer which is
   which. A stale entry here is worse than no entry.

**New tables/columns:** `office_intake_item`; `office_idea.source_channel`,
`office_idea.supersedes_id`; `office_floor.role` gains `rnd`. **New contract rule: C33.**

**Check these when reviewing:** `14-RND-FLOOR.md` (does the digest actually cite the material?) ·
`13-PIPELINE.md` § the second way in (does owner matter still face gate 0?) · `95-CONTRACT.md` C33
(can the capacity, the deadline, and the no-silent-close rules all be enforced by the store?) ·
`30-DATA-MODEL.md` § `office_intake_item`.

## Implementation — 2026-10-12 (M0, the accounting spine)

The owner said "start". The plan-only gate is lifted; the code lives at `voicebridge/office/` and is
tested by five files under `tests/`.

| What landed | Where | Proof |
|---|---|---|
| The append-only store, the two money folds, run receipts, the four caps | `voicebridge/office/{store,ledger,receipts,caps,org,errors}.py` (1,807 lines) | 57 tests, all passing |
| The human surface | `qubit office` — 24 subcommands, confirm-gated, e-stop aware, typed refusals | `tests/test_office_cli.py` |
| Contract proofs C1, C2, C8, C14, C15, C30 | `tests/test_office_{ledger_health,unmetered,budget_admission,chain_of_command}.py` | named by the contract, in the contract's files |

**Deviations from the M0 build list, and why:**

1. **`consent.py` is deferred to M1.** Nothing in M0 asks permission for anything, so the four-tier
   ladder would be untested infrastructure. The repo's own rule rejects that. It lands where a real
   turn can be denied.
2. **The schema is smaller than `30-DATA-MODEL.md` describes.** Tables arrive with the milestone that
   can exercise them; a table nobody reads is dead weight, and the repo rejects speculative
   infrastructure. `office_cap_registry`, `office_room_object`, `office_idea`, `office_envelope`,
   `office_goal`, `office_audit_finding`, `office_recipe` and the rest come with their milestones.
3. **Schema is now `qubit-office/2`**, and `migrate()` carries additive `_ensure_column` upgrades, so
   a store created by schema 1 upgrades in place.

**Three things the build proved that the plan had wrong or missing** (each is now in `90-MILESTONES.md`
or the code, not just here):

- A SQLite `BEFORE` trigger fires **per affected row**, so the append-only check proves nothing on an
  empty ledger. `health()` says `unverified: no rows` instead of claiming enforcement (D14 applied to
  the checker).
- The `admit()`/`begin_run()` refusal indexed a run that may not exist, and **crashed on an unreadable
  ledger** — the one case where it most needed to speak. Fixed and covered.
- `office record` had no `--unmetered`, so the plan's own D16 example (unmetered Perplexity research)
  could not be recorded from the CLI.

## Implementation — 2026-10-12 (M1, one attributed turn)

| What landed | Where | Proof |
|---|---|---|
| The runner: e-stop → consent → admission → receipt → turn → ledger → events → attribution | `voicebridge/office/runner.py` | `tests/test_office_runner.py` (14) |
| The four-tier consent ladder + the hardline floor | `voicebridge/office/consent.py` | `tests/test_office_consent.py` (30) |
| "done" is machine-checkable or it is not done | `runner._completion_is_machine_checkable` | `tests/test_office_done_signal.py` (4) |
| Artifacts attributed by `run_id` | `office_artifact` + `attribute_artifact` | `tests/test_office_runner.py` |
| The surface | `office run/trace/attribute/attribution-check/consent/hardline` | `tests/test_office_cli.py` |

**Suite: 108 tests across 8 files.** `voicebridge/office/` is 2,346 lines across 9 modules.

**Two bugs the tests caught, both of which would have shipped a falsehood:**

1. **The completion check reintroduced the exact failure C17 exists to prevent.** Its first draft
   treated "a non-empty reply came back with a usage figure" as *done* — which is StarNet's
   98 auto-approved passes of *"verified the files exist"*, rebuilt from scratch. The rule is now:
   **no machine-checkable exit condition means nothing is done.** Settlement and completion are
   reported as separate events because they are separate facts.
2. **`trace()` deadlocked the writer.** It asked for `health()` — which runs a write — while its own
   read cursor was still open on the same connection. A read-only surface hung the ledger. It now
   materialises cursors and closes before asking.

**Cost so far: $0.00.** Every part of the runner is proven through an injected `executor`, so the
real accounting path runs while the model call is a stub. The actual provider call has not been made.

### The real turn — 2026-10-12 · a bug no test found

The first real provider call exposed the most serious defect so far: **the provider returns no usage
block, and the runner booked it as 0 cents and settled.** A billed call, recorded as free. Every
unit test passed because every stub *had* a usage block — the stub was more honest than the provider.

Fixed: a **missing** usage block is unknown spend (receipt stays open, admission refuses, owner
settles); a usage block that **says** 0 is a measurement and settles. `trace` now prints the
attestation beside every booking, so the two can never be confused.

Disclosed: the throwaway dev store created during this session (init + 2 caps + 1 seat + 1 capital +
the bogus 0-cent run) was removed and rebuilt, because it contained a 0-cent row I knew to be false
and nothing else. It did not exist before this session.

## Implementation — 2026-10-12 (M2, the org)

| What landed | Where | Proof |
|---|---|---|
| Budget envelopes, allocated top-down, with a reserve | `voicebridge/office/envelope.py` | `tests/test_office_envelopes.py` (13) |
| Raises above an envelope (amount + thesis + return + displacement) | `envelope.raise_above` | same |
| **Two** spend bounds: policy (envelope) and solvency (balance) | `envelope.check_spend` / `spend` | same |
| Escalation onto the **shared** confirmation journal | `voicebridge/office/escalation.py` | `tests/test_office_escalation.py` (10) |
| Survival: runway, floor, the daily tick, termination | `voicebridge/office/survival.py` | `tests/test_office_termination.py` (12) |
| Reports with their claims verified against the ledger | `survival.file_report` | same |
| Reviews, one level only | `voicebridge/office/review.py` | `tests/test_office_review_levels.py` (7) |

**Suite: 152 tests across 12 files.** `voicebridge/office/` is 3,777 lines across 13 modules, the
store 14 tables, `qubit office` 49 subcommands.

**Two bugs the live run found, neither of which any unit test had:**

1. **`ledger.record` let a department's spend be invisible to that department.** `department_id` was
   an optional argument, so a booking that omitted it produced a cost nothing owned — the department
   envelope read `spent 0` immediately after a real spend. Fixed in `ledger.record` itself, because
   the seat already knows its department and a rule kept in one place cannot be forgotten in a
   sixth. This one would have made every department P&L wrong in the same direction: too cheap.
2. **Only the envelope bounded a spend, so a seat spent money it did not have.** The demo ended at
   **net −49,400c** for a worker whose envelope had been raised — and its runway computed as
   `-12.71 days`, which is not a number, it is a symptom. The balance is a second, independent
   bound; the two now report different codes because a raise fixes one and can never fix the other.

**Also fixed while proving it:** `department-create` / `floor-create` / `room-create` did not return
the id they had just created (only `hire` did), which made an envelope impossible to allocate without
going behind the CLI. `roster` no longer lists terminated seats by default — a roster that shows
corpses misreports the org's size.

**Cost: $0.00.**

## Implementation — 2026-10-12 (M3, the first business — scaffolding)

| What landed | Where | Proof |
|---|---|---|
| The business lifecycle, with the ladder enforced | `voicebridge/office/business.py` | `tests/test_office_business.py` (13) |
| The `kill_criteria` gate and the `exposure` gate | `business.advance` | same |
| Owner-only funding; no agent-facing function can promote (C23) | `business.owner_fund` | same, incl. signature inspection |
| Idempotent revenue on `(source, external_ref)` | `business.record_revenue` | same |
| `office_v_business_pnl` + `ledger.pnl(scope='business')` | `voicebridge/office/store.py` | same |
| Reconciliation, findings, separation, spot audit, ladder, auditor seat | `voicebridge/office/audit.py` | `tests/test_office_recon.py` (11) |

**Schema `qubit-office/2` → `qubit-office/3`** (business tables, `office_ledger.business_id`,
`office_agent.auditor_of`, the business view) and a second bump to `/4` in intent — folded into `/3`
before anything persistent consumed it.

**Suite: 178 tests across 14 files.** `voicebridge/office/` is 4,928 lines across 15 modules, the store
18 tables, `qubit office` 63 subcommands.

**Four bugs the live run found — three of them in the controls themselves, which is the point of
running the thing rather than only testing it:**

1. **`migrate()` silently healed the very drift the audit exists to catch.** It dropped and recreated
   the P&L views on every connect. A check that auto-repairs its own input proves nothing, and this one
   would have made `reconcile_all()` a tautology forever. Views are now rebuilt **only on a
   schema-version bump**; otherwise install-if-absent.
2. **The spot audit cried wolf on every healthy business.** Question 3 compared a revenue-side net
   against the whole net (which includes the cost of thinking), so any business that had spent anything
   was reported as failing. Fixed by deriving `revenue_net_cents` in the view and comparing like with
   like.
3. **`check_separation` printed violations and filed nothing.** A violation that exists only while
   somebody is looking is not a control. Violations are now filed, deduplicated against open ones.
4. **"The finding clears" was false, and it was my own claim.** The ledger row is append-only (C1), so
   the violation stays true; what closes is the finding, and a violation still true after a close is
   **re-filed**. A finding closed without fixing anything makes the problem come back.

**Cost: $0.00.**

**⚠ Not done, and cannot be done by an agent:** the milestone's final proof — one business earning its
first **real dollar**. Its own gate is the owner's: the vertical and the payment rail (`80-RISKS.md`
Q3/Q4). The live demo's sale carries a synthetic processor reference and is explicitly not revenue.

## Implementation — 2026-10-12 (M4, the tower)

| What landed | Where | Proof |
|---|---|---|
| Animation state as a pure function of (event rows, clock) | `voicebridge/office/tower.py` (360 lines) | `tests/test_office_tower_no_phantom_motion.py` (17) |
| The procedural floorplan: floors → rooms → seat slots | `tower.floorplan` | same |
| The HUD (headcount, burn, runway, per-floor P&L) on the same folds as `pnl` | `tower.hud` | same |
| `GET /api/office/tower` (+ `?motion=1`, `?now=<epoch>`) | `voicebridge/qubit_compat.py::h_office_tower` | same |
| The sidebar tab and renderer | `frontend/qubit-app/plugins/qubit-office/` | `tests/test_office_tower_plugin.py` (18) |

**Suite: 213 tests across 16 files.** 16 modules / 5,288 lines; `qubit office` 65 subcommands; the
bundle is 301 lines of dependency-free IIFE.

**What the live run caught:**

1. **The tower would have omitted the CEO.** A room requires a department and the reserved floors
   have none, so the CEO and directors had no desk and were simply absent. A tower that hides the CEO
   because the executive floor has no rooms lies by omission. Unseated seats are now surfaced in a
   labelled tray, and never drawn at a fabricated desk.
2. **C4's proof was a `grep` in a document, not a test.** It is now
   `tests/test_office_tower_plugin.py`: no mutating request in the bundle, exactly one timer (the
   poll), no `@keyframes`, no `animation:`, no spinner vocabulary, no `innerHTML`.

**Composition note:** those plugin assertions run on the bundle **with JS comments stripped**, because
its header says "no spinner" and "no innerHTML" while explaining what it refuses to do — a naive text
check fails on its own documentation.

**Deliberately not built, and stated rather than implied:** no isometric projection, no camera rig, no
sprite art (the milestone cannot be blocked on an artist and its proof does not depend on the
projection), and no WebSocket (the page polls a read route; a second transport before the first is
needed is how C3's "no second loop" gets eroded).

**Two spec deviations, recorded rather than silently taken:**

1. `55-DASHBOARD-PAGE.md`'s illustrative manifest shows `"css": "qubit-office.css"`. The tab ships
   `css: null` and styles inline: there is no CSS file, and a manifest referencing a missing file is
   a broken tab rather than a style. The plugin test now refuses a `css` entry that does not exist.
2. The same example shows `"position": "after:qubit-boards"`; the first draft wrote `after:boards`,
   which names no plugin (the short form is used for built-in pages like `cron` and `system`, the
   prefixed form for overlay plugins like `qubit-devices`). The tab would have anchored to nothing.
   The test now asserts the anchor names a real plugin directory.

#### The tab shipped INVISIBLE, and the page shipped BROKEN — found by looking

The owner reported the Office tab missing from the sidebar. Chasing it found **four**
separate faults, none of which any test in this repo could have caught, because all
four live outside the plugin directory:

1. **The sidebar is built from two lists, and the tab was in only one of them.** The SPA
   merges the served SPA's static `<base>/plugins/fleet.json` with `GET /api/dashboard/plugins`.
   An overlay tab delivered only by the API is one API/cache problem away from vanishing.
   `qubit-office` is now in **both** copies of `fleet.json` (the `public/` source and the served
   `qubit-dist/`), which the server reads from disk on every request — so no rebuild was needed.
2. **The position hint could not resolve.** `buildNavItems` (App.tsx) inserts a tab after an
   existing nav item and *appends* it when the anchor is not found. The static fleet is merged
   **before** the API list, so a fleet-delivered tab cannot anchor to another overlay plugin —
   that plugin's entry does not exist yet. `after:qubit-boards` therefore silently placed the
   tab at the end of the group. It is now `"position": "end"`: explicit, and honest about the
   ordering the delivery mechanism actually allows. `qubit-devices` has the same latent fault
   (`after:qubit-runtime`, a sibling delivered later) — noted, not touched.
3. **The icon did not exist.** `building-2` is not in the client's `ICON_MAP`, and
   `resolveIcon` falls back to a generic `Puzzle` **without complaining**. Now `Users`, and a
   test asserts the icon is a real map key.
4. **`/qubit-office` was missing from `SPA_ROUTE_PATHS`** in `webui.py` — "the single source of
   truth for which public paths render the shell". Every other overlay tab is listed; this one
   was not.

And one more, found only once the tab rendered: **the page came up with its error state** —
`NOT_FOUND: no compat route for GET /api/office/tower`. The compat route table is loaded when the
server process starts, so a dashboard started before the route existed cannot serve it. The fix is
a **restart**; the running instance was stale, not wrong.

**Verified in a real browser, not inferred.** Headless Chrome rendering the running dashboard shows
`href="/qubit-office"` present as a genuine sidebar link (`qagent-sidebar__link`, `lucide-users`
icon) — and the page rendered *"The office could not be read"*, which is the correct behaviour for
a page that must never invent an office it cannot read. No screenshot was needed to conclude the
tab was missing; no screenshot is needed to conclude the route is the remaining gap.

**Cost: $0.00.**

## Implementation — 2026-10-12 (M2.5, the work loop; M4 renderer ported; the org deployed)

**Inserted milestone.** The plan had seats (M2) and a business (M3) but no mechanism for handing work
to a seat, so the work loop was built out of order and recorded as **M2.5** in `90-MILESTONES.md` and
`27-WORK-LOOP.md`. Nothing was renumbered.

### Deployed
11 seats: `ceo` → `lead-content`, `lead-quality` (reviewers) → `wren`, `alex`, `mira`, `tobi`,
`sable`, `juno`, `kerr` (workers). Turn caps 2/seat/day (18/day ceiling) set **before** any work.
Capital allocated double-entry with `adjustment`; `transfer` was refused by C1 and `revenue` would
have faked the top line. Org revenue unchanged at 1000¢.

### First real work, end to end
Task #1's exit check is the owner's own command. Seat #4 wrote `tests/test_office_tower_walk.py`
(5,228 bytes); the check returned exit 0 with six tests passing; the task moved to `review` under its
own supervisor. The turn overran its watchdog, leaving an open receipt — `complete: false`, all
admissions refused, which is D15 working — settled at 0¢.

### The M4 renderer was rewritten from four MIT projects
`frontend/qubit-app/plugins/qubit-office/qubit-office.js`, 301 → 838 lines: isometric stacked decks
with cutaway walls, feet-anchored characters with ground shadows and a payload-driven walk bob,
per-seat colour derived from one procedural body, a canvas camera, a minimap, an event feed, and the
P&L charts no reference project has. Provenance with **real commit SHAs** is in
`25-PORTED-CODE.md`; the attribution also travels in the file header.

**`rsanandres/aphae` was NOT ported.** It ships no licence file, which means all rights reserved, so
its design was read and not one line of its GDScript was used. This is the one source of the six the
owner named from which code could not be taken.

### Also fixed
The plugin contract held with a fast poll (400 ms) instead of an animation loop: C7 forbids a timer
that implies work, so walk smoothness comes from asking the backend more often, never from the
renderer interpolating motion the events did not justify.

---

## Known open items (correctly unresolved, not contradictions)

| Item | Status |
|---|---|
| ~~D1–D23 `PROPOSED`~~ | **Resolved 2026-10-12** — all approved; see the owner review section above |
| Four M3 questions | Reclassified as org work ending in a review/request (`57-BOARDROOM.md`), not owner pre-work |
| Tripo Studio vs API | Owner decision, and **narrowed by D26**: the owner exports assets himself, so the org needs no Tripo key. Blocks M6 only |
| Internal auditor seat | New (D40). Needs `office_audit_finding` + `auditor_of`; no owner decision required |
| R&D floor staffing | New (D42). Runs at **zero seats** without breaking — drops queue as `new`. No owner decision required to start; decide the seat count at M2 |
| Drop folder path | `~/Documents/qubit-ideas/` assumed. One line to change if the owner prefers elsewhere |
| Payment rail, legal entity | Owner decisions; Class B blocking requests at M3 |
| Scraping permitted-use record | Raised in review; **now covered by D29 item 6** (acting against published terms is a hardline denial). A per-platform signed permitted-use record remains an unspecced hardening suggestion, not a gap |

## How to review this plan

1. Read `99-HANDOFF.md` first, then `95-CONTRACT.md`. Those define done.
2. For every finding, cite `file:line`. A finding without a location cannot be actioned.
3. Check this log before flagging an inconsistency.
4. The highest-value review targets, in order:
   - **`95-CONTRACT.md`** — is each C-rule provable by the command given? Is any rule unenforceable?
   - **`30-DATA-MODEL.md`** — does the schema actually express the rulings, or can a ruling be
     violated with valid data? (e.g. is there truly no column that can hold a secret?)
   - **`12-DEPARTMENT-CATALOG.md`** — are the unit economics credible? Are the risk models complete
     for each division kind?
   - **`58-AUTONOMY.md`** — with the bypass on, what is the worst thing an agent can do that is not
     stopped by the hardline floor, the per-run cap, or the schema?
   - **`90-MILESTONES.md`** — can each proof actually be run as written?
5. Do **not** propose changing a ruling. File a `PROPOSAL` in the format in `99-HANDOFF.md`.
