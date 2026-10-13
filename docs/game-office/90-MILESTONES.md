# 90 — Milestones

Each milestone has one **behavioral proof** — a thing that can be observed, not a test count.
A milestone is not done until its proof runs. No milestone claims VERIFIED without it.

Ordering rule: **the ledger comes before the tower.** A pretty tower over a fake economy is
exactly the failure this plan exists to avoid.

---

## M0 — Accounting spine

**Build:** `voicebridge/office/` package: `store.py` (the eight tables incl. `office_cap_registry`,
`office_room_object`, `office_run_receipts`; `_migrate`; append-only triggers), `ledger.py` (signed
cents, `unmetered` + `attested` flags, two folds `sum_metered`/`sum_reported`, idempotency on
`(source, external_ref)`, run receipts + `health()`, materialized P&L views), `org.py`
(departments/rooms/seats/agents CRUD with the hard caps), `caps.py` (the registry + per-turn
resolution), `consent.py` (the four-tier ladder + hardline list).

**Proof:** four quoted observations.
1. Create one department, one room, three agents; write 20 mixed ledger rows; `qubit office pnl`
   prints totals exact to the cent.
2. An `UPDATE` or `DELETE` on a ledger row is **rejected by the database**.
3. Write one metered row (100¢) and one unmetered row (500¢): `sum_metered == 100`,
   `sum_reported == 600`, and the agent balance sees 100.
4. Leave an `open` run receipt: `ledger health` reports `complete: false` and admission **refuses**
   with a typed reason naming the unsettled run.

**Cost:** zero (no models called).

### M0 status — 2026-10-12 · **the accounting half is BUILT and PROVEN**

`voicebridge/office/` (7 modules, 1,807 lines), the `qubit office` CLI family (24 subcommands),
5 test files, **57 tests passing at the time** (the suite is 108 after M1 — see below):

```
.venv/bin/python -m pytest tests/test_office_ledger_health.py \
    tests/test_office_chain_of_command.py tests/test_office_cli.py \
    tests/test_office_unmetered.py tests/test_office_budget_admission.py -q
# 57 passed
```

| M0 proof, as written above | Status |
|---|---|
| 2. `UPDATE`/`DELETE` on a ledger row rejected **by the database** | **PROVEN** — `BEFORE` triggers `RAISE(ABORT)`; asserted as `sqlite3.IntegrityError` |
| 1. mixed ledger rows; `qubit office pnl` exact to the cent | **PROVEN** — derived `GROUP BY` views only; three scopes asserted to agree |
| 1. one department, one room, three agents | **PROVEN** — through the real parser (`test_office_cli.py`), end to end |
| 3. metered 100¢ + unmetered 500¢ → `sum_metered == 100`, `sum_reported == 600`, balance sees 100 | **PROVEN** — `sum_metered`/`sum_reported` are spend folds; balance and P&L are net folds (D16) |
| 4. open run receipt → `health.complete == false`, admission **refuses** typed | **PROVEN** — receipt → refuse → owner attestation (`entered`/`limit`) → resume; an *unreadable* store returns DEGRADED and refuses too |
| `caps.py` — the four keys, resolution rules, corrupt-value rule | **PROVEN** — `0` means "no cap", absent means "default", a corrupt value can never grant headroom |
| chain of command (C30/D34/D35) | **PROVEN** — supervisor mandatory, one CEO, no cycles, span 7, terminates at the CEO |
| `consent.py` — the four-tier ladder + hardline list | **DEFERRED to M1, and delivered there** — see the M1 block below |

**`consent.py` is deferred, on purpose.** It has no consumer until the runner exists: nothing in M0
asks "may I do this?". The four-tier ladder would be untested infrastructure, and this repo rejects
exactly that ("no dead code wired in without E2E proof"; a hook with no concrete consumer is
speculative). It lands in M1, where a real turn can be denied.

**Verified live, not just in tests** (a fresh temp store, the real CLI):

```
$ qubit office record --kind revenue --cents 5000 --agent 1 --confirm
$ qubit office record --kind inference --cents 100 --agent 1 --confirm
$ qubit office record --kind inference --cents 9000 --agent 1 --unmetered --confirm
$ qubit office pnl
  revenue 5000  cost 100  net 4900
  entries 3  metered 2  unmetered 1  reported_net -4100  tokens 1200/300
$ qubit office pnl --scope agent     # the 9000 must not exist here
  balance 4900                       # = 5000 - 100
$ qubit office health
  healthy True complete True append_only {update: True, delete: True} drift 0
```

**Two bugs the build found, both real:**

1. **A degraded ledger crashed instead of refusing.** `admit()` and `begin_run()` indexed
   `health["open_runs"][0]`, but `complete` is false in *two* situations — an unsettled run, and a
   store that cannot be read. The unreadable case has no run to name, so it raised `IndexError` from
   inside the safety gate. Both now route through one `_unknown_spend_detail()` that names a run when
   there is one and says "the ledger could not be read" when there is not.
2. **`--unmetered` did not exist on `office record`.** The D16 fold was unreachable from the CLI, so
   the plan's own example (unmetered Perplexity research) could not actually be recorded. Found by
   running the live loop rather than by reading the code.

**One correction to the plan, found while building:** a SQLite `BEFORE` trigger fires **per affected
row**, so on an empty ledger the forbidden writes succeed trivially. `ledger.health()` therefore
reports `unverified: no rows` rather than claiming enforcement it did not observe — the honesty rule
(D14) applied to the health check itself.

---

## M1 — One agent, one real turn, attributed

**Build:** `runner.py` — the work tick for a single agent: budget admission, real
`run_agent_turn`, real usage from the provider response into the ledger, artifacts attributed by
`run_id`, `office_event` rows for each tool call.

**Proof:** one agent is given one task; `qubit office trace <agent>` shows the receipt written
before the call, the turn, the model used, the exact tokens and cents it cost (matching the
provider's own usage number), the artifacts it produced, and the event rows the renderer would
consume. Then set `budget_cents_day` below the cost and re-run: the second turn is **refused with a
typed response, not silently run**. Finally kill the process mid-turn and restart: the receipt is
`open`, admission refuses until settled, and settling with an owner-stated amount clears it and
records `attested_as`.

**Cost:** ~$0.05.

---

### M1 status — 2026-10-12 · **BUILT and PROVEN at zero cost**

`voicebridge/office/` grows to 9 modules / 2,346 lines, `qubit office` to 30 subcommands, and the
suite to **108 tests** across 8 files **(M1-era counts; 13 modules / 3,777 lines and 152 tests after
M2 — see below)**:

```
.venv/bin/python -m pytest tests/test_office_ledger_health.py \
    tests/test_office_chain_of_command.py tests/test_office_cli.py \
    tests/test_office_unmetered.py tests/test_office_budget_admission.py \
    tests/test_office_consent.py tests/test_office_runner.py \
    tests/test_office_done_signal.py -q
# 108 passed
```

| M1 proof, as written above | Status |
|---|---|
| the receipt is written **before** the call | **PROVEN** — `run_turn` orders e-stop → consent → admission → `begin_run` → turn → `finish_run`; `trace` shows the receipt on every run |
| the turn, the model, the **exact tokens and cents** | **PROVEN** — read from the provider's own `UsageRecord`, booked in integer cents **and** preserved exactly in `provider_cost_micros` |
| the artifacts it produced | **PROVEN** — `office_artifact` bound to `run_id`, refused without a receipt (C6) |
| the event rows the renderer would consume | **PROVEN** — `run_started`, `run_settled`, then `task_done`/`task_unverified` |
| a cap below the cost **refuses** rather than silently running | **PROVEN** — the executor is asserted **never reached** |
| kill mid-turn → receipt `open` → admission refuses → owner attests → clears | **PROVEN** — including the `attested_as` value |
| `consent.py` (deferred from M0) | **PROVEN** — all 6 hardline items deny under all 3 postures, even holding the grant (C16/C19) |

**The whole milestone is provable without spending a cent.** `run_turn` takes an injected
`executor`, so the accounting path runs for real while the model call is a stub.

**A correction the C17 test forced.** The first draft of the completion check treated *"a non-empty
reply came back with a usage figure"* as done. That is **exactly** the StarNet failure — 98 passes of
*"verified the files exist"*, each auto-approved — rebuilt from scratch. The rule is now: **with no
machine-checkable exit condition, nothing is done.** A turn's *settlement* and its *completion* are
reported as two separate events, because they are two different facts.

**A deadlock the `trace` test found.** `trace()` called `health()` — which runs a write (its savepoint
probe) — while its own read cursor was still open on the same database. The read-only surface
hung the writer. It now closes the connection before asking for health, and materialises cursors
before issuing further statements.

**Live, zero-spend receipt:**

```
$ qubit office consent --capability ledger --scope rewrite --grant ledger:rewrite
  allowed False | tier hardline
  refused: 'ledger:rewrite' is on the hardline floor (ledger_tamper)
$ qubit office run --agent 1 --task "say hello"        # no --confirm
  OFFICE_CONFIRM_REQUIRED
$ qubit office trace --agent 1
  runs 0 | complete True | unattributed 0
$ qubit office attribution-check
  {'orphan_artifacts': 0, 'ledger_rows_without_a_receipt': 0, 'unattributed_entries': 0}
```

**The real paid turn has now been made** — see below.

---

#### The real paid turn — run 2026-10-12, and what it found

`qubit office run --agent 1 --task "…" --estimate 5 --cap 20 --confirm` was made against the real
provider. It **found a bug that no test had**: this provider returns **no usage block**, and the
first version of the runner booked that as **0 cents and settled the receipt** — a real, billed call
recorded as free work. That is precisely the failure D15 exists to prevent, and it would have gone
unnoticed until an invoice disagreed.

The fix, now enforced and tested: **a missing usage block is unknown spend, not a zero.** The
receipt stays open, admission refuses, and only the owner closes it. A usage block that *says* 0 is
different — that is a real measurement (a local model) and settles normally.

The honest end state of that turn, on the record:

```
$ qubit office run --agent 1 --task "…" --confirm
  {"state": "UNKNOWN_SPEND", "unknown_spend": true,
   "reason": "the provider reported no usage for this turn"}

$ qubit office health          -> complete: false, healthy: false
$ qubit office admit --agent 1 -> OFFICE_ADMISSION_REFUSED
     "unknown spend: run 'run_f3f4a228cb2b4135' (agent 1) has a receipt and no
      settled ledger row. Unknown spend is not headroom."

$ qubit office settle run_f3f4a228cb2b4135 --cents 20 --attested-as limit --confirm
$ qubit office admit --agent 1 -> OK, balance 980
```

`--attested-as limit` is the honest label here: the charge is booked **at the run's own cap**, and
the record says so, because the provider never stated a figure. `entered` would have been a guess.

**✓ M1 is complete.** All seven proofs pass, and the live turn exercised the unknown-spend path for
real rather than in a stub.

**⚠ Known limitation, for the owner:** this provider (`deepseek-v4.1-flash` via `opencode-go`)
**does not report tokens or cost**, so per-turn *exact* attribution is not currently possible — the
caps still bound the spend (per-run and per-day both hold), but the ledger can only record a
cap-attested booking, not a measured one. Two ways to get exact cents later: use a provider that
returns a usage block, or add a per-model price table and mark those rows `attested_as='limit'` too,
since they are still not the provider's own number. `trace` now shows the attestation on every
booking, so a measured figure and a cap booking can never be confused.

---

## M2 — The org: CEO, directors, managers, one department

**Build:** seat type → model tier + toolset resolution; **the chain of command** — `supervisor_id`,
the span limit, and the review levels (D34/D36/C30/C31); **budget envelopes** allocated top-down
(D37); hire/fire through the store; escalation (the Task Brief pattern) staged through the shared
confirmation journal; the CEO seat with the weekly org review; the daily tick (reflection + survival
evaluation) as cron jobs.

**Proof:** an escalation raised by a worker appears on the existing approvals surface with a real
`confirmation_id`, is approved from the voice dashboard, and the agent resumes and completes the
work — one journal, one settle path. Separately: force an agent's runway below the floor and the
daily tick escalates it to **its manager** (found via `supervisor_id`) instead of silently killing
it; and a manager's spend inside its envelope writes a ledger row with **no** request row, while the
same manager spending above it is refused.

**Cost:** ~$3.45/day of real operation (5 seats).

**Why the chain lands here and not later:** every later milestone attributes numbers to a seat —
"this business lost money", "this agent burned its balance". Without the supervisor edge those
numbers have no owner to escalate to and no one to review them, and the tower becomes a very pretty
org chart of unaccountable peers.

---

### M2 status — 2026-10-12 · **BUILT and PROVEN at zero cost**

`voicebridge/office/` grows to **13 modules / 3,777 lines**, `qubit office` to
**49 subcommands**, the suite to **152 tests** across 12 files (3,037 lines of
test code), and the store schema to 14 tables (envelopes, requests, escalations, reports, reviews).
**(M2-era counts; 15 modules / 4,928 lines and 178 tests after M3 — see below.)**

| M2 proof, as written above | Status |
|---|---|
| an escalation appears on the **existing** approvals surface with a real `confirmation_id` | **PROVEN** — asserted against the real `ApprovalJournal` at `<state_dir>/approvals.jsonl`, the same one the dashboard and the voice bridge read |
| …is approved from the voice dashboard, and the office sees it | **PROVEN** — `escalation.sync()` closes the office row when the **shared** surface settles it; a denial lands as `abandoned`, not `resolved` |
| one journal, one settle path | **PROVEN** — no second queue exists; `journal_for()` opens the shared path |
| force a runway below the floor → the tick escalates to **its manager**, found via `supervisor_id` | **PROVEN** — live: `wk-1 -> supervisor seat 4`, and the worker is still `active` afterwards |
| …instead of silently killing it | **PROVEN** — no code path terminates on the daily tick |
| a manager spending **inside** its envelope writes a ledger row with **no** request row | **PROVEN** — live: 3,000c booked, `open requests: 0` |
| …and above it is refused | **PROVEN** — `OFFICE_ENVELOPE_EXCEEDED`, and nothing is written |
| a seat cannot write its own review; a peer cannot rate a peer | **PROVEN** — `OFFICE_SELF_REVIEW`, `OFFICE_REVIEW_WRONG_LEVEL` (`tests/test_office_review_levels.py`, the file C31 names) |

#### Two bugs the live run found that no unit test had

1. **A department's spend was invisible to the department.** `ledger.record` took `department_id` as
   an optional argument, so a booking that omitted it produced a cost no department P&L owned — the
   department envelope read `spent 0` right after a real spend. Fixed **in `ledger.record`**, not at
   the call sites: the seat already knows its department, and a rule enforced in one place cannot be
   forgotten in a sixth.
2. **The envelope was the only bound, so a seat could spend money it did not have.** The live demo
   ended with a worker at **net −49,400c** — it had spent its way into debt, and its runway went
   *negative*, which reads as nonsense and means "already dead". There are two independent bounds and
   both now apply: the envelope is the **policy** bound, the balance is the **solvency** bound. They
   report different codes (`OFFICE_ENVELOPE_EXCEEDED` vs `OFFICE_INSUFFICIENT_BALANCE`) because a
   raise fixes the first and can never fix the second. A negative balance now reads as *"already out
   of money"* rather than `-12.71 days`.

**Cost: $0.00.** Nothing in M2 calls a provider. The daily tick's survival pass is pure `GROUP BY`
over the ledger, which is exactly why it can run every day forever for free.

**⚠ The one paid piece still unconfigured:** the tick's *reflection* half. With no reflector injected
it reports `NOT_CONFIGURED` with a typed reason rather than pretending an agent reflected — the same
shape as the runner's injected executor. Reflection is a real turn on a real model, so it costs money
and inherits M1's finding: this provider reports no usage, so those turns settle as `UNKNOWN_SPEND`
until the owner attests them.

---

## M3 — The first business, with real money

Reads `11-ORG-CHARTER.md` § budget envelopes and `17-AUDIT.md` before starting: this is the first
milestone where a dollar exists that can be misreported, so the reconciliation check, the
separation-of-duties rule, and the auditor seat land **with** the first revenue row, not after it.

**Build:** `office_business` lifecycle including the `kill_criteria` gate and the exposure field;
the money gate triggers wired to the journal; revenue ingestion (`kind='revenue'`, real
`external_ref` from the payment processor); the P&L report.

**Proof:** one business goes `proposed → building → live` **and earns its first real dollar**,
sourced from a real payment, with the row naming the agent and the artifact that produced it. The
department's P&L turns positive in `qubit office pnl` without any hand-entered number.

**Owner decision needed first:** the business vertical and the payment rail (`80-RISKS.md` Q3/Q4).

**Cost:** real, and this is the milestone that decides whether the whole thing works.

---

### M3 status — 2026-10-12 · **SCAFFOLDING BUILT and PROVEN; the first real dollar is NOT done**

`voicebridge/office/` grows to **15 modules / 4,928 lines**, `qubit office` to **63 subcommands**, the
suite to **178 tests** across 14 files, and the store schema to `qubit-office/3` with 18 tables.
**(M3-era counts; 16 modules / 5,288 lines and 213 tests after M4 — see below.)**

**What this means in one line:** every gate, every ingestion path, and every audit control between an
idea and a real payment now exists and is tested — and the payment itself has not been taken, because
that needs the owner's three answers.

| M3 proof, as written above | Status |
|---|---|
| a business goes `proposed → building → live` | **PROVEN** — with the ladder enforced: no skipping `building`, no `live` from `dead` |
| **the `kill_criteria` gate** | **PROVEN** — refused by name, with *why* it is required ("a business with no declared way to die is not a business") |
| **the `exposure` field** | **PROVEN** — same gate, same refusal; the M3 addition, in the spirit of C22 |
| revenue ingestion with a real `external_ref` | **PROVEN mechanically** — `(source, external_ref)` is the idempotency key; a replayed webhook returns the original row, not a second sale |
| the P&L turns positive with **no hand-entered number** | **PROVEN** — `office_v_business_pnl` is a `GROUP BY`; 500 in − 165 out = 335, derived at org, department, and business level |
| **the money gate is the owner's** (C23) | **PROVEN** — no agent-facing function takes a funding parameter at all (asserted by signature inspection); `owner_fund` demands the owner's own sentence |
| the auditor seat, reconciliation, separation of duties, the spot audit | **PROVEN** — see below |
| **one business earns its first real dollar from a real payment** | **NOT DONE — needs the owner** |

**⚠ The milestone's own gate is still closed.** "Owner decision needed first: the business vertical and
the payment rail." Both are still open, so the last proof has not been attempted. The live demo books a
sale with a **synthetic** processor reference (`pi_demo_1`): it proves the machinery end to end and it is
**not** a real dollar. Saying otherwise would be the exact kind of claim this whole design exists to
refuse.

#### The audit layer, which is where the interesting mistakes were

| Control | What it does | Proof |
|---|---|---|
| A. Reconciliation | every rollup recomputed client-side and diffed against the view | `tests/test_office_recon.py` |
| A. **A diff is never silently repaired** | it is filed as a finding, and the view is corrected *from the ledger* | the drifted-view test drops a view, tampers with it, and asserts it is caught, filed, and left alone |
| B. Spot audit | one business, drawn by seed, three questions, refund rate included | same |
| C. Separation of duties | checkable because `office_ledger.recorded_by` exists | same |
| D. Penalty ladder | minor → repeat → terminate; only `terminate` wakes the owner | same |

#### Four bugs the live run found, three of them in the *controls themselves*

1. **`migrate()` silently healed drifted views.** It dropped and recreated the P&L views on **every
   connect** — which quietly repaired exactly the drift the reconciliation control exists to catch. A
   check that auto-fixes its own input proves nothing. Views are now rebuilt **only on a schema-version
   bump**; otherwise they are installed-if-absent, so drift survives to be found.
2. **The spot audit's third question was a false alarm on every healthy business.** It compared a
   **revenue-side** net (gross − fees − refunds) against the **whole** net (which also carries the cost
   of thinking), so it reported a disagreement on any business that had spent anything. A control that
   always fires is a control nobody reads. The view now exposes `revenue_net_cents` and that is what is
   compared.
3. **`check_separation` detected violations and filed nothing.** A violation that exists only while
   someone is looking at it will be missed every time it matters. Violations are now filed as findings,
   deduplicated against open ones.
4. **"The finding clears" was wrong — and it was my error in the demo.** A finding cannot clear: the
   ledger row that caused it is append-only (C1). What closes is the *finding*, with the fix recorded
   as evidence. And if the violation is still true, the control files a **fresh** finding — so a finding
   closed without actually fixing anything makes the problem come back rather than go away.

**Cost: $0.00.** No provider call, no payment processor, no network.

---

## M4 — The tower (visual) — the "Qubit Office" page

**Build:** the SPA overlay plugin (`frontend/qubit-app/plugins/qubit-office/`, sidebar tab per
`55-DASHBOARD-PAGE.md`), the floorplan JSON format, the 2D isometric room layer with procedural
agents, the camera and floor stack, the HUD (headcount, burn rate, runway, floor P&L), and the
read/WS wiring. **Carries no external asset dependency** — phase-1 agents are drawn by code, so
this milestone cannot be blocked on art.

**Proof:** with the real org from M2/M3 running, the page renders every agent at its real seat,
animates **only** from `office_event`, and the HUD's burn-rate and runway numbers match
`qubit office pnl` to the cent. Kill an agent's session mid-turn and its character stops
animating — the tower never shows work that is not happening. And: an org with no rows renders the
empty state, never a demo office.

**Cost:** $0. No art required.

---

## M6 — Tripo characters (visual upgrade)

**Build:** the GLB loader path, the character swap at existing tile coordinates, the asset
manifest, and the license gate (C9).

**Proof:** drop a rigged GLB with `idle`/`walk`/`work`/`talk` into
`assets/characters/<slug>.glb`, add its manifest entry, and that agent renders as the GLB at the
same position and with the same event-driven state it had as a procedural sprite — with no change
to the office, store, or event code. Then point the license gate at a non-commercial-tier manifest
entry and the asset build refuses it by name.

**Cost:** the owner's existing Tripo credits (~$0.78–$1.18 per animated character).

**Ordering note:** M6 is deliberately last. It is a renderer swap behind a stable seam, so it can
land at any time after M4 without touching anything else — which is exactly why it must not be
allowed to block M4.

---

### M4 status — 2026-10-12 · **BUILT and PROVEN, with no browser and no art**

`voicebridge/office/` grows to **16 modules / 5,288 lines**, `qubit office` to **65 subcommands**,
the suite to **213 tests** across 16 files, and the sidebar to a new **Office** tab.

The design decision that makes this milestone provable: **the animation state is computed in
Python**. "The tower renders only from events" (C7) is a claim about a renderer, and a claim that
lives only in a browser is a claim nobody can test. So `voicebridge/office/tower.py` (360 lines) turns
`office_event` rows into per-character animation as a **pure function of (rows, clock)**, and the
plugin is handed the answer. The browser then has nothing to animate from but those numbers.

| M4 proof, as written above | Status |
|---|---|
| the page renders every agent at its real seat | **PROVEN** — and an agent with no room is **reported as unseated, never dropped** (see below) |
| animates **only** from `office_event` | **PROVEN** — `tests/test_office_tower_no_phantom_motion.py`: freeze the stream, advance the clock, `moving 3 of 5 → 0 of 5`, and positions unchanged after an hour of silence |
| the HUD's burn and runway match `qubit office pnl` to the cent | **PROVEN** — live: `cli revenue 12345 cost 2345 net 10000` vs `HUD revenue 12345 cost 2345 net 10000`; both exclude the unmetered row (D16) |
| kill a session mid-turn → the character stops animating | **PROVEN** — it goes `working → idle`, and the dead turn is still visible as `stalled: True`. No phantom motion, and no silent disappearance either |
| an org with no rows renders the empty state, never a demo office | **PROVEN** — `empty: True`, `has not started`; the plugin has no data to draw a demo one from |
| carries no external asset dependency | **PROVEN** — agents are a rounded rect and a state-coloured band, drawn in code; `manifest.json` has `css: null` |

#### Two things the live run caught

1. **The tower would have omitted the CEO.** A room requires a department, and reserved floors
   (executive/director/intelligence) have none — so the CEO and the directors had no desk and were
   simply absent from the plan. A tower that hides the CEO because the executive floor has no rooms
   **lies by omission**. Unseated seats are now surfaced in a labelled tray, and the plan never draws
   them at a fabricated desk.
2. **The checkable half of the plugin's contract was only checkable by `grep`.** C4's proof in the
   contract is a shell pipeline. A grep in a document is a habit; `tests/test_office_tower_plugin.py`
   makes it a test — no `POST/PUT/PATCH/DELETE` anywhere in the bundle, exactly **one** timer (the
   poll, and it only re-reads), no `@keyframes`, no `animation:`, no spinner/skeleton/shimmer word,
   no `innerHTML`. The day someone adds a quick mutation or a busy spinner to the tower, the suite
   fails instead of shipping it.

**Composition note:** those plugin assertions run on the file **with JS comments stripped**. The
header of that bundle says "no spinner" and "no innerHTML" — explaining what it deliberately refuses
to do — and a naive text check fails on its own documentation.

#### What is deliberately *not* here

- **No isometric projection, no camera rig, no sprite art.** The plan's build list names them; phase 1
  is a flat plan view with code-drawn agents. Nothing in the milestone's proof depends on the
  projection, and a milestone that cannot be blocked on an artist should not be blocked on one. The
  floor stack is a selector, not a 3D camera, and `40-VISUAL-SPEC.md` remains the target for a later
  pass.
- **No WebSocket.** The page polls a read route. A push channel is an optimisation, not a proof, and
  adding a second transport before the first one is needed is how a "no second loop" rule (C3) gets
  eroded.

**Cost: $0.00.** No provider call, no network. (Chrome was used locally to read the rendered
dashboard — no screenshot, no remote asset.)

#### Addendum — the tab shipped invisible, and how that was found

The owner reported no Office entry in the sidebar. Chasing it found **four** faults outside the
plugin directory, none reachable by this repo's tests: the tab was missing from the served SPA's
static `fleet.json`; its `after:` anchor could not resolve (the static fleet is merged *before* the
API list, so a fleet-delivered tab cannot anchor to another overlay plugin) and it silently landed
at the end; its icon (`building-2`) is not in the client's `ICON_MAP` and fell back to a generic
`Puzzle` without complaining; and `/qubit-office` was missing from `SPA_ROUTE_PATHS` in `webui.py`.
Then, with the tab rendering, the page showed **`NOT_FOUND: no compat route for GET
/api/office/tower`** — that route table is loaded when the server process starts, so a dashboard
started earlier cannot serve it. **Restart required; the running instance was stale, not wrong.**

Verified in headless Chrome against the live dashboard: `href="/qubit-office"` is present as a real
sidebar link with the `Users` icon. The page rendering *"The office could not be read"* is correct
behaviour for a page that must never invent an office — and it is what makes the stale route
visible rather than silent.

Five new assertions (`tests/test_office_tower_plugin.py`) now hold the whole chain: the icon is a
real map key, the position can resolve, `fleet.json` lists the tab in **both** copies and they
agree, the path is a shell route, and the route is registered and answers with the payload the tab
reads.

---

## M5 — The ratchet

**Build:** the baseline/ratchet files on disk, the daily reflection turn with the capped
single-output rule, entry scoring from artifact-attributed ledger rows, weekly cross-pollination,
and entry deactivation.

**Proof:** an agent produces a ratchet entry with a stated source artifact; the artifact's ledger
row shows the outcome; the entry's score changes in the predicted direction over the evaluation
window; and an entry that scores at or below zero across a full window is deactivated with a
recorded reason. Nothing in the ratchet is active without a score.

**Cost:** the daily reflection budget from `45-ECONOMY.md`.

---

## M7 — Floorplan editor

**Build:** the diegetic editor (`75-EDITOR.md`): `office_cap_registry` + `office_room_object`
tables, `office.validate_edit()`, the per-turn grant resolution, the editor overlay on the tower
page, and the staged write path.

**Proof:** six quoted observations — the ghost refuses a cap violation by name; placing a
`cabinet` makes file tools appear in the next turn's resolved list with no restart; deleting it
makes them disappear; a non-grantable prop is refused by the store; an unconfirmed edit answers
`409` and leaves the floorplan on disk unchanged; and an undo appends a second `floor_edited`
event rather than rewriting history.

**Cost:** $0.

**Ordering note:** last of all. An editor over a tower that can lie lets the owner author the lie
deliberately.

---

## M8 — The R&D floor (`14-RND-FLOOR.md`)

**Build:** `office_intake_item` with the capacity/deadline/no-silent-close constraints (C33); the
folder watcher on `~/Documents/qubit-ideas/`; the transcript path (`yt-dlp` + the existing
`transcription_common.py`); the three-phase digest writing `office_idea` rows with
`source_channel='owner_intake'`; the War Room panel and the Red Team turn as recorded positions.

**Proof:** drop one real YouTube link and one real article into the folder. Within the digest window
each produces an `office_idea` row whose `mechanism` cites the material, whose `evidence` pointers
resolve back into the transcript text, and which then appears at **gate 0** like any scraped signal.
Separately: fill the intake to 12 and assert the 13th drop is **refused with a typed reason**; force
an item past its deadline and assert it renders expired with a written reason; and drop a dead link
and assert it lands in `failed` with the reason, **not** in a fabricated playbook.

**Cost:** 2–4 best-tier seats. Runs at **zero seats** without breaking (drops queue as `new`).

**Why after M4/M5 and not with M0:** the floor's entire output is `office_idea` rows, so the pipeline
and the manager gate must exist for a digest to have anywhere to land. Building the lab before the
funnel gives the owner a beautiful reading room attached to nothing.

**Why it is not on the critical path:** it produces no revenue by itself. It is the highest-value
*marginal* milestone for the owner's own workflow — which is exactly why it is M8 and not M2.

## M2.5 — The work loop (inserted, see `27-WORK-LOOP.md`)

**Not in the original M0–M8 numbering.** The plan gave the org seats (M2) and a business (M3) but
nothing that hands work to a seat. Without it, eleven hired seats are eleven spectators. It is
recorded as an insertion rather than renumbering the owner's milestones.

**Build:** `work.py` (queue, reviewer assignment, attempt cap, turn budget), `dispatch.py` (the
loop), `office_task`, `office_task_event`, `office_turn_budget`, and 10 CLI subcommands.

**The five rules, each enforced in code:** a task without a machine-checkable exit condition cannot
be created (C17); a rejection must carry the instruction and goes back to its own author; the
reviewer is exactly one level above and nobody accepts their own work (C31); an unparseable verdict
accepts nothing; `running` stays eligible so a refused turn cannot strand a task.

**Proven:** `tests/test_office_work_loop.py`, 20 tests against a stubbed executor.

### M2.5 status — 2026-10-12 · **BUILT and PROVEN, and it has produced real work**

```
252 passed   # the full office suite
```

**The live proof, in full:** 11 seats deployed (CEO + 2 reviewer leads + 7 workers), turn caps set
to 2/seat/day = an 18-turn/day ceiling *before* any work ran, and 900¢ of owner capital allocated
double-entry via `adjustment` (not `transfer` — C1 reserves that for compensating entries; not
`revenue` — that would fake the top line). Org revenue stayed exactly 1000¢.

Then task #1, *"Pin the tower walk model with tests"*, whose exit check is the owner's own command.
Seat #4 (`wren`) ran a real provider turn and wrote `tests/test_office_tower_walk.py` — 5,228 bytes.
The owner's check returned **exit 0: six tests pass**. The task moved to `review`, reviewer seat #2,
its supervisor. The turn overran its watchdog and was killed, leaving an open receipt
(`complete: false`, every admission refused — D15 working); it was settled at 0¢ attested, because
opencode-go is a flat-rate gateway.

Eight bugs were found building this, seven of them in code written the same day. The two worth
naming: the dispatcher picked **managers** to do the work, i.e. a manager reviewing its own output
(the exact thing C31 exists to prevent); and a **failed** turn was counted as *satisfied* because the
flat-rate branch never required the run to have succeeded.

**Not done:** the reviewer's verdict turn; and the org cannot find work for itself — tasks are still
created by the owner. That is M5's ratchet, not this.

---

## What is deliberately not a milestone

- **Autonomous growth** (hiring its own new departments without the owner). The org may *propose* a
  department from M2 onward; auto-founding waits until the ledger has proven the loop works.
- **Any auto-publish path.** Publishing is a mandatory review under every posture (D22). If it ever
  feels too slow, the fix is better proposals, not a weaker gate.
- **An auto-digest queue.** The R&D floor does not process material on a schedule; it processes what
  the owner dropped, with a deadline and a refusal at capacity (D43). A scheduled digest would be a
  second loop (C3) and would re-create the backlog the capacity rule exists to prevent.
- **Multi-user, cloud, mobile.**

## Critical path

```
M0 ledger ──► M1 one real turn ──► M2 org + escalation ──► M3 first real dollar ──► M4 tower
                        │                                          │                    │
                        └──────────────► M5 ratchet ◄──────────────┘                    ▼
                                                                                  M6 Tripo chars
```

M4 (the tower) can be built in parallel with M5 by a second worker once M1's event schema is
frozen — the renderer only needs `office_event` and the store reads, both of which exist after M1.
