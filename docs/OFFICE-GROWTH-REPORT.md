# Office growth report — can this grow into an agent-run business?

**Author:** Qubit Agent (growth-report subagent). **Date:** 2026-10-15.
**Worktree:** `/home/trick/Projects/Repos/qubit-voicebridge`, HEAD `629c8de0f`.
**Method:** real execution + `file:line`, no opinion. Live store read at
`~/.local/state/qubit-voicebridge/office/office.db`. No source, test, or other
doc was edited. Nothing written to the vendored habblaud tree.

Every claim below is either a quoted command output from this session or a
`file:line` citation. Anything I could not determine is listed, with why, at
the end.

---

## Bottom line

- **A seat's turn is the real agent.** An office seat reaches the *same*
  `run_agent.AIAgent` the terminal drives, with the same toolsets, skills,
  plugins and MCP servers. Proven below. There is **no seat-level capability
  gate** — nothing says "this seat may use tool X and not Y".
- **Rooms are DATA; geometry is CODE.** Adding a room is a DB insert
  (`room-create`), but the *drawing* of that room is fixed by constants in
  `tower.py`. You can add rooms without touching Python; you cannot change the
  building's shape without it.
- **Memory is empty for one reason:** the only writer of a `memory_written`
  event is `autowork.tick`, and the autonomous driver is **off by default and
  has never run** (`enabled: false`, `last_tick_at: null`). It is not "not
  enough turns" and not a broken commit path — the commit path is never called.
- **The revenue loop is half-built.** Ledger, envelopes, caps, P&L, runway,
  business lifecycle and a revenue *ingestion* verb all exist. There is **no
  customer, order, invoice, or checkout concept anywhere**, and no code path
  that *goes and earns* — revenue only arrives if something outside calls
  `office revenue` with a processor reference.
- **What StarNet has that Qubit's office lacks** (briefly): capability derived
  from *what is placed in a room*, monotonic attenuation for delegated workers,
  a compute gate, and trust/decay memory feedback. Three of those map directly
  onto the four broken office invariants another agent is fixing.

---

## 1. Capability inheritance — a seat gets the terminal agent's tool surface

**The seam.** `voicebridge/office/runner.py:374-394` `_default_executor`:

```python
def _default_executor(cfg, agent_id, task_text):
    from .. import agent as _agent
    seat = _seat(cfg, agent_id)
    session = seat.get("slug") or f"office-{agent_id}"
    result = _agent.run_agent_turn(
        cfg, task_text, session=session,
        estop_engaged=lambda: store.estop_engaged(cfg))
    if result is None:
        raise OfficeError(RUN_REFUSED_CODE, "no tool-capable runtime/model ...")
    return result
```

So a seat turn calls `voicebridge/agent.py:840` `run_agent_turn`, which calls
`_run_vendored_session` (`agent.py:411`). That function — quoted at
`agent.py:419-424` — is documented as *"the terminal /chat agent
(`run_agent.AIAgent`): it resolves the same tool set (skills, plugins, MCP)"*.

**What a seat actually gets** (`agent.py:451-575`):

| Facet | How it is resolved | Line |
|---|---|---|
| Runtime | `from run_agent import AIAgent` — the vendored agent | `agent.py:456` |
| Model/provider | `resolve_runtime_with_fallback` via `qubit_cli.oneshot` | `agent.py:457-513` |
| Toolsets | `sorted(_get_platform_tools(vcfg, "cli"))` → `enabled_toolsets=` | `agent.py:515`, `564` |
| MCP servers | `ensure_mcp_discovery_before_agent_build(...)` runs before the agent is built, *deliberately*, so `mcp__*` tools register | `agent.py:525-538` |
| Skills/plugins | Resolved inside the vendored `AIAgent` from the same config — the platform is `"cli"`, identical to terminal | `agent.py:567` |
| Fallback chain | `get_fallback_chain(vcfg)` | `agent.py:551-555` |
| Governance | *"The agent's own broker/policy/confirmation/e-stop chain governs each tool (the terminal path's authority)"* | `agent.py:447-449` |
| E-stop | Honored before and after the run | `agent.py:467-469`, `590-592` |

The turn is a genuine tool-executing agent: `agent.run_conversation(text)`
(`agent.py:576`), and the receipt carries the tool names it used
(`agent.py:594` `_vendor_tool_names(result)` → `receipt["tools"]`).

**CAN reach** (same as terminal, `platform="cli"`): terminal, browser,
file/shell tools, skills (`skill_view`/`skill_manage`/`skills_list`), plugins,
every configured `mcp__<server>__<tool>`, and the confirm-gate/broker chain.

**CANNOT reach / caveats:**
- If no tool-capable model is configured, the turn **refuses** with
  `RUN_REFUSED_CODE` rather than degrade (`runner.py:389-393`). Live config:
  `agent.model: None`, `agent.enabled: True` — so a model must resolve through
  the vendored config or the seat turn is a typed refusal, not a weaker turn.
- The seat runs under the *same env and permissions as the terminal agent* —
  there is no sandbox a seat gets that the terminal doesn't, and vice versa.

**Is there a seat-level capability gate?** **No.** Searches for
`tool_allow|allowed_tools|capability_grant|grant.*seat|deny_tool|tool_deny`
across `voicebridge/` return only:
- `voicebridge/mcp_client.py:131` `allowed_tools(record)` and
  `mcp_servers.py:341` — these gate which tools an **MCP server record** may
  expose, not which tools a **seat** may call.
- `voicebridge/office/consent.py:140` — the consent broker's *danger-class*
  grant ("this danger class is granted to the seat"), which is a per-call
  consent ladder, not a tool allowlist.

`enabled_toolsets` is passed identically for every seat; nothing narrows it per
seat, per room, or per `seat_type`. **Every seat has full reach, always.**
This is the single largest gap versus StarNet (§5).

---

## 2. Adding rooms / seats / tasks — what exists, and data vs code

### 2.1 The real CLI verbs

`python -m voicebridge.cli office --help` lists the growth verbs. Their real
arguments (from `office <verb> --help`):

| Verb | Arguments | Effect |
|---|---|---|
| `floor-create` | `--number` (req), `--name`, `--role {operations,intelligence,director,executive,rnd}`, `--confirm` | one floor row |
| `department-create` | `--slug` (req), `--name`, `--floor`, `--division-kind`, `--director`, `--kill-criteria`, `--confirm` | one business/department |
| `room-create` | `--department` (req), `--slug` (req), `--kind` (default `work`), `--capacity`, `--confirm` | one room row |
| `hire` | `--slug` (req), `--name`, `--seat-type`, `--department`, `--room` (desk's room), `--supervisor`, `--portfolio`, `--confirm` | one seat |
| `seat-move` | `agent_id`, `--room`, `--department`, `--confirm` | seat the agent (only way to seat a hire made before rooms existed) |
| `programme` | `--headcount` (req), `--departments`, `--confirm` | materialise a whole room programme (boardroom + work rooms + review) |
| `task-create` | `--title` (req), `--brief`, `--exit-check` (a shell command; **required in practice**, C17), `--department`, `--attempts`, `--confirm` | one task |
| `task-claim` | task id, `--agent-id` (assign; reviewer becomes supervisor) | assign |
| `dispatch` | run worker turns against the open queue, `--confirm` | work the queue |
| `escalate` / `raise` | raise to supervisor / above envelope | escalation |

There is also the **programme** verb (`room_layout.programme`, wired at
`cli.py:1695-1699`): given a headcount it derives *one work room per manager +
a boardroom + a review room* using the org rules (span limit 7,
`room_layout.py:121-162`).

### 2.2 Room geometry: DATA or CODE? — **both, at different layers**

**Room *existence* and identity are DATA.** `office_room` is a plain table
(`store.py:97-104`): `id, department_id, slug, kind, role_scope, capacity`.
`room-create` inserts one row. A seat's room is `office_agent.room_id`
(`store.py:114`). Nothing about *which* rooms exist is hard-coded.

**Room *placement and geometry* are CODE.** The floorplan is built
**procedurally** from fixed constants in `voicebridge/office/tower.py`:

- `floorplan()` (`tower.py:128-191`) reads `office_floor` / `office_room` /
  `office_agent`, then lays rooms on a grid with **no DB-stored coordinates**:
  `col, row = i % ROOM_COLS, (i // ROOM_COLS) % ROOM_ROWS` (`tower.py:169`),
  `"x": col * (ROOM_W + 1), "y": row * (ROOM_H + 1)` (`tower.py:179`).
- The grid size is hard-coded: `ROOM_COLS, ROOM_ROWS = 3, 2`
  (`tower.py:115`) → **6 rooms per floor, maximum**.
- Room size is hard-coded: `ROOM_W, ROOM_H = 12, 7` (`tower.py:116`).
- Desk slots: `SEATS_PER_DESK_ROW = 3`, `DESK_ROWS = 3`,
  `SEAT_SLOTS_PER_ROOM = 9` (`tower.py:120-124`), positions from
  `room_slot_x`/`room_slot_y`/`room_slot_offset` (`tower.py:194-227`).
- Named stations inside a room are hard-coded offsets:
  `STATIONS = {workbench:(1,2), vault:(4,1), comms:(4,2), shelf:(2,3)}`
  (`tower.py:79-84`).

**So the answer is: adding a room is a DATA insert, but only within the fixed
6-rooms-per-floor grid.** Go past 6 rooms on a floor and the layout reuses
cells (rooms overlap) — the geometry does not expand. To grow the *building*
you must change code:

- `tower.py:115` `ROOM_COLS, ROOM_ROWS` — rooms per floor (explicitly frozen by
  `docs/game-office/24-FLOORPLAN-SCALE.md` §7: *"Do not change
  `ROOM_COLS/ROOM_ROWS`, and do not enlarge the building"*, quoted at
  `room_layout.py:156-160`).
- `tower.py:116` `ROOM_W, ROOM_H` — room size in tiles.
- `room_layout.py:161` `MAX_ROOM_COLS, MAX_ROOM_ROWS = 3, 2` mirrors it and
  `check()` **reports** (never silently enlarges) a programme that does not fit
  (`room_layout.py:484-490`).

The capacity maths is centralised in `seating_plan.py` (`desks_per_room`,
`rooms_needed`, `floors_needed`), and `floorplan_fit.py` is the checker that
*measures* the divergence between `tower.py`'s hard-coded constants and the
recommended geometry by **parsing tower.py's source** (`floorplan_fit.py:315-467`
`assert_matches_tower`). That checker exists precisely because the geometry is
code, not data.

**Net:** more *seats* → data (`hire`, `seat-move`, up to 9 slots/room).
More *rooms* → data, up to 6/floor; beyond that → new floor (data) or code
change to the grid. Different *building shape* → code.

---

## 3. Why memory is empty — the writer never runs

**Live evidence** (`office.db`, this session):

```
events_total         7
memory_events        0     # zero memory_written / note_written rows, ever
agents               10
event kinds: run_started 3, tool_call 1, task_unverified 1, run_unpriced 1, run_settled 1
newest event: id 7, kind tool_call, agent 4, age 2.65 days
tower: every seat  memory {'count': 0, 'known': False},  animation idle
```

**The commit path.** A memory exists only when `agent_memory.remember()` runs
(`agent_memory.py:164`), which is what inserts the `memory_written` /
`note_written` event the dossier and tower read (`agent_memory.py:221`,
`_rows_for` at `:135-145`, `dossier` at `:349-397`).

**Who calls `remember()`?** A repo-wide search for `.remember(` returns exactly
**one** production caller: `autowork.py:747`, inside `tick()`:

```python
_remember(cfg, agent_id,
          text=(f"tick {…}: ran a self-directed turn ({outcome}); {…}")[:800],
          meta={"run_id": …, "outcome": …, "state": …})
```

Everything else that imports `agent_memory` only **emits a display event** —
it never records a durable memory:
- `runner.py:118-125` emits `note_written` for an **artifact**, via
  `_mem.emit_event`, not `remember` (an artifact is not a memory).
- `receipts.py:142-150` emits `ledger_write` via `_mem.emit_event`.
- `escalation.py:409` emits an escalation event.
- `tower.py:638` only reads `dossiers()`.

`dispatch.py` (the manual work loop) calls `run_turn` (`dispatch.py:269`,
`:378`) but **never `remember`**. The manual `office run` verb likewise runs a
turn and settles a receipt but writes no memory — the `run_started` /
`run_settled` pairs present in the live DB (agents 1, 2, 4) left **zero**
memories.

**And the tick is off.** `autowork` status, live:

```json
{ "state": "OK", "enabled": false,
  "config_key": "office.autowork.enabled",
  "every_seconds": 900, "last_tick_at": null, "last_tick_date": null }
```

Config default is also off: `cfg.get('office','autowork','enabled', default=False)`
→ `False` (`autowork.py:110-112`, `:172-187`). The gate is explicit
(`autowork.py:638-644`): when disabled the tick *"is a genuine no-op — nothing
is read from the store and nothing is written."*

**Verdict: (a) the only writer never fires — because the driver it lives in is
off and has never ticked.** It is **not** (b) a broken commit path (the path is
correct and tested but simply never invoked), and **not** (c) a provider
problem (a provider clearly ran — agents 1, 2, 4 have real run receipts and
ledger rows). The moment `autowork.enabled` is true **and** a seat turn
completes, `_remember` writes the first `memory_written` row. Until that flag
flips, every dossier is correctly, honestly empty (`known: False`).

**Why this matters most:** a seat composes its turn from recalled memories only
when a caller passes them. `autowork._recall` (`autowork.py:257-276`) is the
only composition path, and it, too, runs only inside the disabled tick. So
today no seat has a "what I did last time" context at all.

---

## 4. What "maintaining a business" would need — EXISTS vs MISSING

### What EXISTS (all citation-backed)

| Capability | Where | Live state |
|---|---|---|
| Append-only double-entry ledger (`inference`/`revenue`/`fees`/`transfer`/`capex`/`adjustment`) | `store.py:130-175`, triggers | 24 rows |
| P&L derived by `GROUP BY` (never stored) | `ledger.py`, views `office_v_org_pnl`/`_agent_`/`_department_`/`_business_` `store.py:467-716` | org net `+980` cents |
| Budget **envelopes** (monthly, per level) | `envelope.py` (org/portfolio/department) | present |
| Four **caps** (perRun/perAgent/perDay/global) | `caps.py:34-43` — defaults $0.50/$50/$50/$5,000 | defaults in force |
| **Runway / survival floor** | `survival.py` `SURVIVAL_FLOOR_DAYS=7.0`, `BURN_WINDOW_DAYS=14.0` | runway `686.0` days |
| **Business lifecycle** with live-gates (`kill_criteria` + `exposure` required before `live`) | `business.py:154-215` | **0 businesses** |
| **Revenue ingestion** — `(source, external_ref)` idempotency key | `business.py:256-309`, CLI `office revenue` | **1 revenue row** (1000¢) |
| Owner-only **funding** gate (`funded=1` only via `owner_fund`) | `business.py:218-253` | no business funded |
| Audit findings, separation-of-duties, spot-audit, penalty ladder | `audit.py`, `store.py:294-310` | — |
| Task queue with **machine-checkable exit condition** (C17) | `work.py`, `store.py:316-353` | 2 tasks |
| Escalation, raise/approve, reviews | `escalation.py`, `review.py` | — |
| Autonomous driver (`tick`) that composes recalled memory, proposes, approves, runs a real turn | `autowork.py` | **OFF** (§3) |

### What is MISSING for a loop that actually earns

1. **No customer, order, invoice, checkout, or payment-account concept.**
   A repo-wide search for `customer|order|invoice|checkout|buyer|stripe`
   returns only unrelated hits (`learning.py`, `config.py`, `git_scope.py`,
   etc.). Revenue is *ingested*, never *generated*: `record_revenue`
   (`business.py:256`) requires a caller to supply
   `--source stripe --external-ref <processor id>`. **Nothing in the codebase
   creates that call** — there is no webhook receiver, no storefront, no
   outbound sale path. Money can only be *booked after* something outside the
   office sold it.
2. **No path from "a live business" to "a first sale."** The lifecycle
   (`proposed→building→live→profitable`) is fully modelled
   (`business.py:39-51`), but the transition to `profitable` is a manual
   status advance; nothing ties it to a sales process. There is no fulfillment,
   no listing/publish action, no price-to-order link.
3. **The autonomous loop is off, so nothing runs unattended at all** (§3).
   Even the proposal→approve→dispatch ratchet only fires inside `tick`
   (`autowork.py:759` `_approve_pending`).
4. **Envelope enforcement is not wired onto a real turn** — this is
   `OFFICE-INVARIANT-AUDIT.md` FINDING #1: no live path calls
   `envelope.spend()`; the live turn gates on `receipts.admit` (dollar caps +
   balance) only. So "above the envelope it is refused" is a documented rule
   with no live enforcer.
5. **The revenue is a demo, not a business.** Live: `1` revenue row of `1000`
   cents against **0** businesses — it was booked as an org-level row, not
   through a live+funded business (`business.py:287-299` would refuse one that
   wasn't). `pnl` reports `revenue_cents: 1000` but no business exists to own
   it.

**What a real loop would need, concretely:** (i) at least one *sales surface*
(the office has none — even a single MCP connector to a marketplace would run
through a seat turn, which is possible since seats get MCP, §1); (ii)
`autowork.enabled = true` so seats run unattended; (iii) a webhook/ingest
caller that turns a processor event into `office revenue`; (iv) a business
promoted to `live`+`funded` by the owner; (v) the envelope fix so spend is
actually bounded. (i) is the only genuinely *new* code; the rest is
configuration plus the invariant fixes already in flight.

---

## 5. StarNet comparison — governance ideas Qubit's office LACKS

**Source:** `/home/trick/Projects/starnet-live` (MIT). Read: `CODE_MAP.md`,
`sidecar/capability/{resolve,capGate}.js`, `sidecar/permissions.js`,
`sidecar/memcore.js`. This is a **comparison only — no code was copied.**

StarNet's claim is real in its code: *"a room is a capability-scoped team, a
placed object is a real capability grant"* —
`sidecar/capability/resolve.js:1-4`:

> `resolveTools: the PURE projection of placed objects onto the tools an agent
> may use THIS turn. Capability follows the agent's ASSIGNED room (the desk) …
> `room.capabilities` is derived, never hand-stored. Called fresh every turn so
> placing/reclaiming an object changes the agent's reach on the very next model
> call.`

| StarNet governance idea | Where | Qubit's office today |
|---|---|---|
| **Capability is derived from placed room objects** — the tool allowlist is the union of what's *in the room* | `capability/resolve.js:35-63` | **Absent.** Every seat gets the full `platform="cli"` toolset with no room/seat narrowing (§1). Rooms are pure rendering. |
| **Monotonic attenuation for delegated workers** — a subagent's tools = `resolved ∩ subset`, never a union; and `deferred` is filtered by the *same* subset so a worker can't be *told* it may call a tool the gate will deny | `capability/capGate.js:21-31` | **Absent.** No delegation narrowing; `enabled_toolsets` is identical everywhere. |
| **Compute gate** — `canRun()` is false unless a computer object is in the room ("no compute capability — place a computer in the room") | `capGate.js:33-44` | **Absent.** A seat's ability to start a turn depends only on a model being configured (`runner.py:389`), not on any placed resource. |
| **Toolset kill-switch** — a disabled family's grants are dropped *before* the gate (`compute` exempt) | `resolve.js:13-16, 48` | Partial analogue only at MCP-server level (`mcp_client.allowed_tools`); no per-seat/toolset family switch. |
| **Memory trust + decay + dedup** — signed feedback nudges a `[0,1]` trust that **decays** (30d half-life) unless re-endorsed; near-duplicate beliefs are detected by Jaccard, not exact text | `memcore.js:15-67, 102-124` | **Partially present.** `agent_memory.recall` ranks on relevance+recency+importance with an exponential half-life (`agent_memory.py:38-44, 290-317`) — but there is **no endorsement/trust signal, no decay-from-last-reinforcement, and no near-duplicate detection**. A memory's worth never depends on whether it proved useful. |
| **Consent broker with an unconditional hardline floor, anti-retry suffix, default-deny under autonomous surfaces ("silence is not consent"), jail-scoped write grants, per-routine terminal/connector grants** | `permissions.js:15-29, 39-41, 82-121` | **Partially present and already strong.** `office/consent.py` implements the four-tier ladder and a hardline floor (`consent.py:140`; `OFFICE-INVARIANT-AUDIT.md` "D17/D18 hardline" HELD). Qubit lacks the *jail-scoped write* and *per-routine* grants, but the core ladder is not the gap. |

### Which of these would fix the four broken invariants?

`docs/OFFICE-INVARIANT-AUDIT.md` names four BROKE invariants. Mapping:

- **#2 (reversing an unmetered row manufactures metered money)** — `ledger.reverse()`
  does not propagate `unmetered`. StarNet's *monotonic attenuation* discipline
  (a derived action can never widen authority) is the **governing principle**
  that would name this class; a StarNet-style derived-property rule would
  propagate the flag. **Directly relevant as a principle; not itself the fix.**
- **#4 (a running task silently re-claimed by a peer)** — StarNet's
  *assignee-scoped capability* (authority follows the assigned room/agent and
  is re-resolved each turn) would make "who owns this task" a checked property
  rather than an overwrite. **Directly relevant.**
- **#1 (a 1-cent approved raise unlocks unlimited spend)** — StarNet's
  `approvalRules` per tool (`resolve.js:58`) tie an approval to a **specific
  tool+scope+danger-class**, not a bare status flag. Applying that shape
  (approve an *amount+period*, not a *status*) is the fix. **Directly
  relevant.**
- **#3 (task-review with no reviewer identity accepts any task)** — StarNet's
  `dangerKey = capability + ':' + scope` and *"silence is not consent"*
  (`permissions.js:41,47-50`) mean an action with **no** resolved authority
  **default-denies**. That is the exact inversion Qubit needs (an absent
  `--by` should deny, not pass). **Directly relevant.**

So: the ideas Qubit's office most lacks that would **fix the broken
invariants** are (1) *authority is derived and re-resolved per action, never a
stored/absent flag* — fixes #1, #3, #4; and (2) *derived properties
attenuate, they never widen* — the #2 class. StarNet's room-object capability
model is the cleanest expression of idea (1); its `deferred`-filtering comment
(`capGate.js:22-30`) is the honest warning about getting "what may I find"
out of sync with "what may I run".

**One-line honest take:** Qubit's office has *more* money machinery than StarNet
(ledger, envelopes, caps, P&L, runway, business gates) and *less* authority
machinery — StarNet makes capability a derived, per-turn, room-scoped fact,
while Qubit's office gives every seat everything and enforces only money. The
four invariant breaks are all instances of that second gap.

---

## What I could NOT determine, and why

1. **No live provider turn was run.** I did not call `office run` (it makes a
   real paid provider call). So capability inheritance is proven by reading the
   call chain (`runner._default_executor` → `agent.run_agent_turn` →
   `_run_vendored_session` → `AIAgent(enabled_toolsets=...)`), **not** by
   observing a seat emit an `mcp__*` or `skill_used` event. The one live
   `tool_call` event (agent 4, 2.65 days old) is consistent with a tool-
   executing seat but does not itself name the tool.
2. **The exact set of `mcp__*` servers a seat reaches right now is unverified.**
   `_get_platform_tools(vcfg, "cli")` and MCP discovery run inside the vendored
   runtime; I did not load the vendored config to enumerate the live tool
   names. The mechanism is proven (MCP discovery is called before build,
   `agent.py:533-538`); the live inventory is not.
3. **`autowork` was never ticked, so its memory-writing path is proven by
   code, not by a live `memory_written` row.** `last_tick_at: null`; enabling
   it would spend real inference money, which I did not do without asking.
4. **No webhook/ingest caller was found, but I cannot prove none exists
   outside this repo.** I searched `voicebridge/`; an external service (a cron
   job, a Stripe dashboard webhook, a script) could in principle call
   `office revenue`. Within the tree there is no such caller.
5. **StarNet's runtime enforcement was read, not executed.** I did not boot
   the StarNet sidecar; the capability model is quoted from its own source
   comments and functions, which are unusually explicit, but I did not observe
   a live `resolveTools` call.
6. **Whether `enabled_toolsets=None` (the `except` branch at `agent.py:514-517`)
   means "all toolsets" or "default toolsets"** depends on the vendored
   `AIAgent`'s handling of `None`; I did not open the vendored runtime to
   confirm. The happy path passes an explicit sorted list.

---

## Files

- Created: `docs/OFFICE-GROWTH-REPORT.md` (this file).
- No source, test, or other doc was modified. No vendored habblaud file was
  touched.
