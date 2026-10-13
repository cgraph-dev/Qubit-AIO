# 45 — Enhancements the merge plan undersclaims

**Scope correction (owner, 2026-10-13, in-session):** the merge's priority is **LOGIC** —
*how everything works and is linked and what it produces* — over UI. This file ranks the
enhancements **L1–L14 (logic)** first, then **U1–U8 (UI)**. Every claim is verified against
the code with a `file:line` citation; nothing here is estimated.

**This file expands `70-MILESTONES.md`, it does not replace it.** M0–M6 remain the merge's
milestones; §L1–L14 attach to them as named sub-tasks, and §L1 and §L2 are new **milestones
M7/M8** because they are logic the merge plan has no home for at all.

**Two merge-plan claims are wrong and are corrected here first** (§0), because the whole
asset half of the plan builds on them.

---

## 0. What the plan got wrong (verified against the running code)

### 0.1 The asset-serving claim is false for *this* plugin — assets would 404

`40-ASSET-PIPELINE.md` §3, `60-WIRING-TRAPS.md` Trap 3, and `10-DECISIONS.md` M5 all assert
that a PNG dropped under the plugin directory serves **today** via
`resolve_plugin_asset` / `_PLUGIN_ASSET_CONTENT_TYPES`. Measured, that is not how a
`qubit-office` asset is served:

* `/dashboard-plugins/qubit-office/qubit-office.bundle.js` → **200, 188073 b**. It is served
  by the **overlay fast path** in `voicebridge/webui.py:1615-1625`, which reads
  `frontend/qubit-app/plugins/<name>/<file>` and accepts **only** `resolved.suffix in
  (".js", ".json", ".css")` (`webui.py:1620`) and **only** `len(parts) <= 3` (`webui.py:1615`).
* `resolve_plugin_asset` (`voicebridge/qubit_compat.py:10934`) is the **fallback**, and it
  resolves from `plugin["_dir"]`, which `_discover_dashboard_plugins` sets to
  `child / "dashboard"` (`qubit_runtime/qubit_cli/web_server_dashboard.py:604`,
  `:581`). `qubit-office` has **no `dashboard/` directory** — verified:
  `ls frontend/qubit-app/plugins/qubit-office/` → `camera.js control-bar.js iso-engine.js
  manifest.json motion.js qubit-office.bundle.js qubit-office.js sprites.js` (no `dashboard/`).
  So the plugin is **not in `_get_dashboard_plugins()` at all**, and the fallback answers
  `PLUGIN_NOT_FOUND`.
* Measured live, today:
  ```
  $ curl -s -o /dev/null -w '%{http_code}\n' \
      http://127.0.0.1:8890/dashboard-plugins/qubit-office/assets/furniture/chair.png
  404
  $ curl -s -o /dev/null -w '%{http_code}\n' \
      http://127.0.0.1:8890/dashboard-plugins/qubit-office/assets/sprites/x.js
  404
  ```
  (the second has `len(parts) == 4 > 3`, so it falls to the fallback and misses too).

**Consequence the plan does not carry:** adding `".wav": "audio/wav"` to
`_PLUGIN_ASSET_CONTENT_TYPES` (`qubit_runtime/qubit_cli/web_routers/dashboard_ui.py:379`) —
M9's whole mechanism — **changes nothing**, because that dict is only read by the fallback
that this plugin never reaches. The merge must instead either:

* **(a)** extend the overlay fast path (`webui.py:1615-1625`) to accept the image/font/audio
  suffixes AND raise the `len(parts) <= 3` cap, or
* **(b)** create `frontend/qubit-app/plugins/qubit-office/dashboard/manifest.json` so the
  plugin is discovered, and serve assets through the (already image-capable) fallback.

**(b) is the smaller change and the honest one** — it uses the existing image allowlist
unchanged, and it is the same shape every other dashboard plugin already has. But (b) means
the overlay path (parts ≤ 3, `.js/.json/.css`) and the plugin path are *different resolution
roots for the same plugin*, which is a new split-brain surface the plan's Trap 4 does not
name. **Recommendation: (b) + a test asserting both roots resolve to the same bytes**, and
M2's proof line
(`curl .../assets/furniture/chair.png → 200`) must be rewritten against whichever root is
chosen — as written it cannot pass.

### 0.2 Sprite counts and pose names are wrong

`30-SOURCE-INVENTORY.md:21` says starnet poses are `rot_{8 dirs}`, `walk_{dir}_{1-4}`,
`sit_{dir}_4`, `type_{dir}_{1-4}`. Measured on `approved_android`
(`ls starnet/frontend/assets/sprites/approved_android/`):

* `rot_*` — **8** files, correct.
* `walk_{dir}_*` — **6 frames** per direction (`walk_east_1..6.png`), not 4. The renderer's
  `walkFrom()` in `sprites.js:512` produces a **3-frame** cycle (frames 0/1/2). A 6-frame
  authored walk needs a remap, not a 1:1 wire.
* `sit_*` — only **4** directions (`sit_east/south/west/north_4.png`), measured
  `uniq -c` = 1 each.
* `type_*` — only **`type_north`**, 4 frames. There is **no** type pose for east/south/west.
  So "typing at the desk while facing right" is not in the art; the sprite layer must either
  accept a north-only type pose (and not rotate) or reuse `sit` — a decision the plan does
  not make and whose absence would surface as a silently missing pose.

**Byte budget, measured** (the plan says "to be measured at vendoring", `40-ASSET-PIPELINE.md:90`):
`du -sc starnet/frontend/assets/sprites/approved_*` → **22,768 KB ≈ 22.2 MB** across 50 sets,
64 files each. Furniture `316 KB`, sfx `2.0 MB`, font `vt323.woff2`. So the *entire* starnet
asset take is ≈ **24.5 MB**, comfortably inside the 100 MB cap (R7) — **and the sfx are only
2 MB, so R7's "drop sfx first" is a 2 MB saving, not a real lever.**

### 0.3 The "50 crew sets" are mostly licensed celebrity likenesses

`ls -d starnet/frontend/assets/sprites/approved_*` returns, verbatim: `approved_elonmusk`,
`approved_samaltman`, `approved_harrypotter`, `approved_ricksanchez`, `approved_tonymontana`,
`approved_heisenberg`, `approved_masterchief`, `approved_xenomorph`, `approved_freddyfazbear`,
`approved_ghostface`, `approved_catwoman`, `approved_ninjaturtle`, `approved_vaultboy`, … plus
`approved_blank_red/green/blue/amber` (recolourable bases) and `approved_robot`,
`approved_endoskeleton`, `approved_crewmate` (original-looking).

The plan (M6, `30-SOURCE-INVENTORY.md:20`) treats all 50 as interchangeable "crew sets".
M3/M4 correctly remove the *license-file* question for a private project, but the celebrity
sets are not a licensing question — they are a **theme** question: a corporate tower staffed
by Spider-Man and Walter White is a different product. **The plan must name the subset it
uses**, and the obvious subset is the four `approved_blank_*` bases (which is exactly what the
renderer's `seatColour()`/`tint()` recolouring at `qubit-office.js:242,220` already wants) plus
the neutral originals. This is also what makes §L3's per-agent identity readable.

### 0.4 Smaller corrections

* **Line numbers drift by 1–33 lines.** `60-WIRING-TRAPS.md:53` cites
  `resolve_plugin_asset` at `qubit_compat.py:10965`; the function **starts** at `:10934` and
  the `PLUGIN_PATH_BLOCKED` return is at `:10967`. `dashboard_ui.py:379` is correct. The
  `_PLUGIN_ASSET_CONTENT_TYPES` dict in the *installed* runtime
  (`sandbox/.../dashboard_ui.py:272`, `.venv/.../dashboard_ui.py:272`) is a **different copy**
  from the repo one (`qubit_runtime/.../dashboard_ui.py:379`) — Trap 2's "vendored file, pin
  refreshed" caveat is right and should be a hard step, not a footnote.
* **Trap 9's test name is truncated** (`60-WIRING-TRAPS.md:133`): "covering: the every asset
  the renderer loads is listed in the vendoring manifest" — the sentence is broken; the
  sentence says nothing checkable. Whatever the intent, it is not a spec.
* **`tool_call` IS emitted today** (`voicebridge/office/runner.py:217`), a fix recorded in the
  comment at `runner.py:202-216`. Any doc that still says the office emits no walk-driving
  event is stale (`43-OPEN-ITEMS.md` predates this). See §L3 for what is still missing.

---

## 1. LOGIC enhancements (L1–L14), ranked visual-impact × feasibility

The ranking is by **(how much real state this puts on screen) × (how feasible it is given
what already exists)**. Each names: what it is, which repo it comes from, why it makes the
game better, the milestone it attaches to, and a concrete acceptance test.

---

## L1 — The autonomous driver: an idle tick that lets the org notice work for itself

**What it is.** A server-side *idle driver* that, on a cadence, wakes each idle seat, has it
choose the highest-value next action **from its own context**, and runs a real attributed turn
— gated by the same admission/caps/consent path a human-triggered `qubit office run` uses.
This is the missing consumer of `task_proposal.propose()`.

**Verified gap (owner-confirmed, and in `43-OPEN-ITEMS.md` A2:70).**
`grep -rn "TP\.propose" voicebridge/` returns **only** `voicebridge/cli.py:7173` — a CLI verb.
`grep -rn "proposal" voicebridge/office/runner.py voicebridge/office/dispatch.py
voicebridge/office/work.py voicebridge/office/survival.py` returns **nothing**. Every turn
today is a human command. There is **no** loop: `grep -rn 'while True\|threading.Thread\|
asyncio.create_task\|cron' voicebridge/office/*.py` → **NONE**.

**Source.** `agent-monitor` — `src/lib/autowork.ts` (313 lines): a persisted
`AutoworkPolicy {enabled, intervalMs, directive, lastSentAt}`, a server ticker
(`TICK_INTERVAL_MS = 15s`, `MIN_GAP_MS = 30s`, `DEFAULT_INTERVAL_MS = 10min`), and a default
directive that tells the agent to *"continue the highest-impact task for your role … do real
work now … do not stop at a status update."* The tick returns
`{ok, tick, sent[], skipped[], failed[]}` so every tick is auditable. Adapted via
`src/app/api/gateway/autowork/route.ts` (83 lines). **The stronger model is starnet's
`autopilot.js`** — the *"PURE engine for the IDLE SELF-DIRECTION DRIVER"* — which decides
between `EARN` (learn/ask) / `ACT` (draft toward a goal) / `none` **gated by the posture dial
AND by how well it knows the owner**, and `autojobs.js`, which has the agent **propose its own
recurring standing jobs** grounded in what it knows.

**Why it makes the game better.** This is the difference between a diorama and a game. Right
now the tower is a *view of what the owner last commanded*; with a driver it is a *living org
that acts on its own*, which is the entire premise of `00-IDENTITY.md` ("the org must be able
to lose", "an agent that burns its balance without earning is terminated"). It is also the only
way the tower's animation states ever fire without the owner driving them — see §L3.

**Attaches to: new milestone M7** (the merge plan's M0–M6 has no home for it).

**The design, concretely (using what already exists — no new store, no new loop module, C3):**

1. **Cadence.** Reuse the **existing cron seam** — `survival.daily_tick` (`survival.py:118`) is
   already the "wake, evaluate, do work, report" shape and is already invoked as a cron job.
   The driver is a *shorter-period* sibling: `office_tick(cfg, every=N minutes)`.
   **Do not add a thread** (`grep … threading.Thread` must stay empty in `office/`); the tick
   runs as a cron job and exits, exactly like `daily_tick`.
2. **Who chooses work.** A seat proposes from its own context via the **existing**
   `task_proposal.propose(cfg, proposer_id, title, brief, exit_check, rationale)`
   (`task_proposal.py:180`); its approver is **exactly one level above** (`approve_level`,
   `task_proposal.py:142`; C31) and approval delegates to `work.create` (`task_proposal.py:286`).
   The driver does not invent a second approval path — it *drives the one that exists*.
   The CEO's proposal has no seat above it, so it escalates to the owner through the existing
   escalation surface, not silently.
3. **Budget / caps (all already built).** Every tick's turn goes through
   `receipts.admit` (`runner.py:191`), the per-agent `budget_cents_day` and org cap (C8),
   the envelope `headroom_for`/`check_spend` (`envelope.py:240,300`), and the
   `office_turn_budget` (`work.py:329`, checked at `dispatch.py:214` with
   `OFFICE_TURN_BUDGET_EXHAUSTED`). The driver adds **one** new policy field —
   *how many ticks per hour a seat may act on, and on what posture* — and nothing else.
   Posture already gates it: `unattended_full` acts, `supervised` stages a 409, `frozen`
   refuses (C5, `runner.py:180`).
4. **It emits REAL `office_event` rows** so C7 holds: because the tick calls the same
   `run_turn`, every `run_started / tool_call / run_settled / task_done` row (and, once §L3
   lands, `note_written / ledger_write / …`) is written by the existing emitter
   (`runner.py:59` `_event`). The driver produces no animation of its own; it produces
   *events*, and the tower animates them as it always does. **There is no second source of
   motion.**
5. **Honesty rules it must not break.** (a) `frozen` seats are skipped, never woken.
   (b) A seat past its envelope headroom proposes but does **not** spend (the proposal is
   free; the turn is gated). (c) A tick that does nothing writes a `note_written`-class event
   with the *reason* — starnet's `nightreport.js` ("what it DECLINED, and the one plain reason")
   and `floorstats.js` SLAG rule — so a quiet org says *why* it is quiet rather than looking
   dead. (d) The e-stop is checked first, as in `run_turn` (`runner.py:173`).

**Acceptance test.**
```sh
# a real turn is produced with NO owner invocation:
$ .venv/bin/python -m pytest tests/test_office_autonomous_driver.py -q
# asserts, with a stubbed executor (zero spend):
#  1. one tick with 3 idle unattended_full seats produces >=1 `proposed` row and exactly
#     one dispatchable task, and a `run_started`+`run_settled` event pair for the seat;
#  2. the same tick on a `frozen` seat produces NO event and NO proposal;
#  3. a seat whose envelope headroom is 0 proposes but its turn is refused
#     (OFFICE_ENVELOPE_EXCEEDED) and no ledger row appears;
#  4. turn_budget at cap -> OFFICE_TURN_BUDGET_EXHAUSTED, tick reports it in `skipped`;
#  5. freeze the clock and run two ticks 1s apart -> the MIN_GAP policy suppresses the 2nd;
#  6. `grep -c 'threading.Thread\|asyncio.create_task\|while True' voicebridge/office/*.py` == 0
#     (the driver is a cron-shaped job, not a second loop: C3).
```
**Proof the driver is real, not a fixture:** run one live tick against the real store and show
`qubit office activity` gaining rows whose `agent_id` is a seat the owner never commanded.

---

## L2 — Per-agent memory: the seats stop forgetting everything each turn

**What it is.** Each seat keeps a durable, retrievable memory of what it did, learned, and
was told — and *uses it* on its next turn.

**Verified gap (owner-confirmed).** `grep -n 'CREATE TABLE IF NOT EXISTS office_agent' -A 30
voicebridge/office/store.py` (`store.py:106-125`) shows the table has `slug, name, seat_type,
department_id, room_id, supervisor_id, portfolio, status, autonomy, exec_profile, created_at,
created_by, terminated_at, auditor_of` — **no memory, no personality, no trait, no mood
column.** `grep -rn 'personality\|persona\|trait\|mood\|memory\|relationship\|profile'
voicebridge/office/*.py` returns only `consent.py`'s `personal_spend` and `tower.py`'s
`memory_written` *map entry* — i.e. the animation vocabulary exists and **nothing backs it.**

**Source.** `ai-town` — `convex/agent/memory.ts`: every conversation is summarized in first
person, scored for `importance` (`calculateImportance`, asks the model "1–10"), embedded
(`fetchEmbedding`), and stored; **recall** (`searchMemories`, `memory.ts:158`) over-fetches
10× (`MEMORY_OVERFETCH`, `:15`) from a vector search and re-ranks by
**relevance + recency + importance** (`:216`), and `reflectOnMemories` (`:84`) writes
synthesized higher-order memories. `MEMORY_ACCESS_THROTTLE = 300_000` (`:13`).
**The pattern to borrow is the ranking (relevance+recency+importance) and the reflection
step, not Convex.** starnet's `dossier.js` supplies the *personality* half: named dimensions
(identity / stack / goals / style / standing_orders / pain / ambition / people / schedule),
each a human-readable belief with provenance, composed into the system prompt — a durable
belief layer, not a per-turn ephemeral.

**Why it makes the game better.** Three compounding reasons. (1) **The tower looks dead
because the state is thin**: `_ANIMATION` (`tower.py:46-70`) defines `memory_written → glow`
and `note_written → carrying`, but nothing emits them (§L3), and even if they did there is no
memory to write. (2) Memory makes §L1's driver *possible*: "continue the highest-impact task
for your role" (autowork's directive) requires the seat to know what it was doing. (3) Memory
is what makes the org *improvable* — `00-IDENTITY.md`'s success criterion is that the org gets
smarter.

**Attaches to: new milestone M8** (paired with M7; the driver has nothing to drive without it).

**The design, reusing the named seam (C3 forbids a second memory):**

* **The store is not new.** C3 (`95-CONTRACT.md:60,73`) names the memory seam explicitly:
  *"Memory → `voicebridge/context_store.py` (OpenViking local root)"*, and
  `context_store.py` already exposes `add_memory(cfg, text, scope=...)` (`context_store.py:427`)
  and `add_resource` (`:392`) with a Viking URI scheme (`to_viking_uri`, `:144`) and tiers
  (`tier_files`, `:225`). **Memory is written through that, scoped per seat**, not into a new
  `office_memory` table. A thin office-side table may hold only *pointers* (a `office_memory_ref
  (agent_id, memory_id, ts, importance, kind)` — a reference, not a second store), matching
  how `office_artifact` points at real files rather than copying them (C6).
* **Importance + reflection** come from the model turn that already ran: the turn's own
  summary is the memory; `calculateImportance` is one extra cheap call, or a deterministic
  proxy at first (artifact count, ledger magnitude, escalation raised).
* **Personality** is a small, durable, *derived* projection — not a new column to hand-edit.
  starnet's dossier dimensions map onto data the office already has: `standing_orders` from
  the seat's envelope policy, `goals` from its open tasks, `style` from its ratchet entries,
  `people` from the **collaboration graph** (§L4). It is composed into the seat's turn as a
  belief block, exactly as `dossier.composeBlock()` composes into the system prompt.
* **The relationship web** is derived, never authored: for each pair of seats, the count and
  outcome of tasks they shared (a `GROUP BY` over `office_task_event` + `office_task`). No new
  input — a projection, like every money figure (C2).
* **Honesty.** A belief with no evidence does not exist: `dossier.js`'s rule "composeBlock() is
  built ONLY from durable beliefs (never a volatile signal)". A fresh agent has an *empty*
  dossier and says so (`known:false`, mirroring `xp.js`'s CONFIDENCE "calibrating" rule) —
  it never invents a personality.

**Acceptance test.**
```sh
$ .venv/bin/python -m pytest tests/test_office_agent_memory.py -q
#  1. a seat runs turn N; a memory ref exists for it with a provenance pointer;
#  2. turn N+1's composed context contains that memory (assert on the injected block);
#  3. recall ranks by relevance+recency+importance: build 3 memories with identical text and
#     different importance/age; assert the composite order is the predicted one;
#  4. a seat with zero memories composes an EMPTY block and reports known:false (no invention);
#  5. `grep -c 'sqlite3.connect' voicebridge/office/*.py` still == 1 (store.py only) — C3:
#     no second store was added;
#  6. the relationship web for two seats that shared 2 tasks reports 2, and 0 for one that
#     shared none.
```

---

## L3 — Linkage: make work → event → animation a real, complete chain

**What it is.** Exactly which of the tower's animation-producing event kinds are emitted
today, and the turn-path call sites that must emit the rest so the six station walks and the
`glow`/`carrying` states become reachable.

**The verified map** (`_ANIMATION`, `tower.py:46-70`; `_EVENT_STATION`, `tower.py:86-93`;
emit sites, `grep`):

| kind | animation | station (tower.py:86) | emitted today? | emit site |
|---|---|---|---|---|
| `run_started` | working | desk | **YES** | `runner.py:198` |
| `run_settled` | settling | desk | **YES** | `runner.py:253,314` |
| `run_unpriced` | paused | desk | **YES** | `runner.py:286` |
| `run_failed` | stopped | desk | **YES** | `runner.py:228` |
| `task_done` | done | desk | **YES** | `runner.py:258,318` |
| `task_unverified` | working | desk | **YES** | `runner.py:258,318` |
| `consent_denied` | blocked | desk | **YES** | `runner.py:185` |
| `tool_call` | walking | **workbench** | **YES** | `runner.py:217` |
| `skill_used` | working | **workbench** | **NO — never emitted** | — |
| `ledger_write` | settling | **vault** | **NO — never emitted** | — |
| `mcp_call` | walking | **comms** | **NO — never emitted** | — |
| `note_written` | carrying | **shelf** | **NO — never emitted** | — |
| `memory_written` | glow | **shelf** | **NO — never emitted** | — |
| `escalation` | walking_to_manager | supervisor desk | **NO** (only read at `tower.py:323`; raised at `escalation.py:93` but no event row) | — |
| `meeting` | meeting | — | **NO — never emitted** | — |

**So: 8 of 15 kinds fire; 7 never do, including 4 of the 5 station walks and both states that
make a room feel alive (`glow`, `carrying`).** This is *the* reason the office reads as dead:
the tower's most characterful states (`walking` to vault/comms/shelf, `settling` at the vault,
`carrying` to the shelf) are **unreachable**, and `tool_call` is the only walk that ever
fires. This is the single highest visual-impact logic fix and the cheapest.

**Source.** The *pattern* is `tower.py`'s own `_EVENT_STATION` — the map already declares the
intent; only the emitters are missing. starnet's `pipeline.js` states the rule that makes it
safe: *"the box you watch ride to a bay" and "the agent that actually runs" are provably one
plan — they cannot drift.* Here the same law says the event kind must be emitted by the code
that *performs* the thing, never guessed by the renderer.

**Attaches to: M4** (the merge's tiles/furniture milestone) — an event that animates must
exist before art is laid over it, and L3 is a prerequisite for the sprite poses of M6.

**The design — the honest call sites (each is a real action, not a decoration):**
* `ledger_write` → **`ledger.record` / every money posting** (`voicebridge/office/ledger.py`).
  A row was written: that IS a ledger write. Emit with `visible="badge"`.
* `mcp_call` → the office's MCP/tool dispatch when an MCP server is actually called (the
  runtime's `voicebridge/mcp_servers.py` path the turn already reaches), located in the turn's
  tool loop, not in `runner.py`'s outer body.
* `skill_used` → when the turn loads a skill (the skills seam named in C3).
* `note_written` → when a seat writes a note/artifact to the shelf-deliverable — attribute
  through the existing `attribute_artifact` (`runner.py:78`) so a note and its ledger row
  share a `run_id` (C6).
* `memory_written` → emitted by §L2's memory write, so the glow state becomes reachable
  *because a memory was really written* — the two enhancements are one change here.
* `escalation` → `escalation.raise_*` (`escalation.py:93`) writes an ordinary event row
  alongside the journal row it already writes, so the walk-to-manager animation fires.
* `meeting` → only when a real meeting/boardroom event exists (`57-BOARDROOM.md`); until
  then it stays unemitted and **unwired** (the weathering rule, `50-ARCHITECTURE.md` §5.3).

**Acceptance test.**
```sh
$ .venv/bin/python -m pytest tests/test_office_event_animation_linkage.py -q
#  1. for each of the 7 missing kinds, assert a live call site EXISTS:
#     grep the emit call in the production module (not a test), by line;
#  2. the durability test: an event of each kind produces the animation _ANIMATION maps it to
#     AND the station _EVENT_STATION maps it to, asserted end to end through tower.state();
#  3. the inverse (weathering): no kind in _ANIMATION is unreachable — a test walks every
#     station and asserts each is reachable, so a new pose cannot be added without an emitter
#     (this is the plan's §5.3 rule made checkable);
#  4. `meeting` is explicitly asserted UNREACHABLE and marked so, rather than silently dead.
```

---

## L4 — The collaboration / relationship web (derived, no new input)

**What it is.** A derived graph: who works with whom, how often, and with what outcome —
read straight from `office_task` + `office_task_event`.

**Verified feasibility.** `office_task_event(ts, task_id, actor, kind, body, …)` exists
(`store.py:344`), `task_proposal.approve_level` (`task_proposal.py:142`) already resolves the
one-level-up relation, and `org._walk_up` (`org.py:57`) walks the chain. A shared-task graph is
a `GROUP BY`. No new table is required beyond a cached projection, and the money rule (C2:
every total is derived) is the same discipline.

**Source.** slogan-free — this is the office's own data; the *idea* of agents forming
relationships from interaction is ai-town's (`rememberConversation` writes a first-person
relationship memory, `memory.ts:27`).

**Why it makes the game better.** It gives §L2's personality `people`-dimension real content
and gives the tower a second visible layer: two seats that habitually collaborate can be drawn
with a shared walkway highlight or adjacent desks, and an escalation between feuding seats can
read differently. It turns the org chart from a tree into a *social* structure — the thing
`00-IDENTITY.md` calls "an org chart you can walk through".

**Attaches to: M5** (the merge's "layout IS state" wiring milestone) — §L4 is a second
consumer of `room_layout`'s programme and shares its reconciliation work.

**Acceptance test.** `tests/test_office_relationship_web.py`: two seats sharing 3 tasks report
a strength of 3; a seat pair sharing none reports 0; the projection is a `GROUP BY` (assert no
write occurs — `office_ledger` row count unchanged); and it drives the `people` dossier
dimension of §L2.

---

## L5 — The goal loop: a seat pursues a standing objective until a judge says done

**What it is.** A seat can be given a standing goal; after each turn a cheap auxiliary check
asks *"is this satisfied by the last result?"* and, if not, a continuation is auto-queued —
within a continuation budget.

**Source.** starnet `goalloop.js` — the *"PURE GOAL-LOOP state machine"*, ending on judge-done
/ budget-spent / user-preemption / pause. Premise: a one-shot task can't express work that
needs several turns.

**Why it makes the game better.** It is the mechanism by which a seat *finishes* something
substantial instead of stopping at a status update — the exact failure autowork's default
directive warns about. It also gives the tower a real long-running state (`working` sustained
across turns) rather than single-turn flickers.

**Attaches to: M7** (it is the multi-turn form of the L1 driver).

**Acceptance test.** `tests/test_office_goal_loop.py`: a 3-turn goal whose stub checker returns
false/false/true runs exactly 3 turns and stops; budget exhaustion stops it with a typed
reason; a real owner task preempts it; and it never exceeds
`office_turn_budget`/`max_attempts` (`work.py:72`).

---

## L6 — Recurring standing jobs: a seat proposes its own scheduled work

**What it is.** A seat, from what it knows, *proposes* recurring jobs; the owner approves the
ones they want; each becomes a scheduled wake that leaves a draft.

**Source.** starnet `autojobs.js` — *"self-initiation … it PROPOSES a few recurring STANDING
JOBS grounded in what it knows — the Commander approves the ones they want, and each approved
job becomes a scheduled cron routine."* Directly the fusion of §L1 (driver) and §L2 (memory).

**Why it makes the game better.** It closes the loop from *"the org can execute work"* to
*"the org decides what work to do"* — the missing half named in A2. It is also honest by
construction: approval is the owner's, so the org never commits spend to a standing job the
owner did not bless.

**Attaches to: M7.** **Acceptance test.** `tests/test_office_standing_jobs.py`: a proposed job
carries the grounding (which memory/dimension it came from); approval mints a cron routine
through the existing cron seam (not a new scheduler); an unapproved job never wakes; a job
that fires writes a draft and emits its events.

---

## L7 — Failure post-mortems become direction (the maintenance quest)

**What it is.** When the *same* failure cause recurs (≥2 times), the org mints one actionable
maintenance task carrying the diagnosis and the fix — instead of silently failing twice.

**Source.** starnet `maintquests.js` — *"when the SAME cause bites REPEATEDLY, the station mints
ONE maintenance quest carrying the diagnosis + the actionable fix"* — fed by `slaglog.js`'s
per-run `{reason, title, cause, fix}`. Threshold-gated so a one-off blip never nags.

**Why it makes the game better.** `00-IDENTITY.md`'s success criterion is that "the failure is
attributed to the gate that let it through" and the org "gets better". A `run_failed`
(`runner.py:228`) currently produces one `stopped` flicker and nothing else. This turns repeat
failure into visible work — the org healing itself.

**Attaches to: M5.** **Acceptance test.** `tests/test_office_maintenance.py`: two `run_failed`
rows with the same cause mint exactly one maintenance task (one-off → zero);
`grep` proves the task's rationale cites the diagnosis; the mint is idempotent per cause.

---

## L8 — Deliberation / voting: decisions the org makes together

**What it is.** A structured multi-agent decision: phases (proposing → arguing → countering →
voting → decided) and a tally strategy (majority / supermajority / unanimous / weighted /
quadratic).

**Source.** `agent-monitor` — `src/lib/deliberation.ts` (the phase state machine) +
`src/lib/consensus.ts` (`ConsensusStrategy`, `:7-13`). Plus `src/lib/council/`.

**Why it makes the game better.** It is the boardroom (`57-BOARDROOM.md`) made real: a real
decision with a real tally, landing as recorded positions (the plan's own `14-RND-FLOOR.md`
"Red Team as recorded positions"). It gives several seats a *shared* event — and thus a shared
animation moment — which the tower currently never has.

**Attaches to: M5/M7.** **Acceptance test.** `tests/test_office_deliberation.py`: a 3-seat
weighted vote produces the predicted tally; a tie resolves by the declared fallback; the
decision is written as append-only records (no edit); it emits `meeting`-class events so the
tower animates it.

---

## L9 — Achievements / levels as an *honest* projection of real outcomes

**What it is.** Per-seat milestones and level, **derived from real ledger/work rows**, never a
free-standing score.

**Source.** `agent-monitor` `docs/ACHIEVEMENT-SYSTEM.md` + `src/lib/xp.ts`,`leaderboard.ts`;
*and* starnet `xp.js`/`quests.js`/`trophies.js`, whose **law** is the one to adopt:
*"every quest cashes out in a REAL capability or outcome — never a fake XP currency — and the
log NEVER gates anything."* `trophies.js`: *"A trophy is a REAL completion … completedAt is
passed through raw (null when unknown) … no 1969 trophies."*

**Why it makes the game better.** A visible progression the org earned is exactly the tower's
"ten-second read". But it must be the honest version — the office already has the ratchet
(`task_proposal.py:6`) and the ledger; an XP number with no evidence behind it would be the
fake-currency failure the office's whole design refuses (D14).

**Attaches to: M5** (a projection over existing rows). **Acceptance test.**
`tests/test_office_achievements.py`: every achievement maps to a `GROUP BY` over
`office_ledger`/`office_task`; a seat with no rows has no level (never level 1 fabricated);
`completedAt` is null when unknown and renders dateless.

---

## L10 — Health/runway as a first-class per-seat *state*, driving behaviour

**What it is.** A seat's runway/at-risk state is already computed (`survival.runway_days`
`:57`, `at_risk` `:100`) but drives nothing. Make low runway *change what the seat does* —
prefer revenue work, decline optional spend, and escalate before death.

**Source.** `agent-monitor` `src/app/agent/[id]/page.tsx` + `TokenUsage.tsx` (per-agent cost
visibility) and starnet `floorstats.js` (the live economy readout). The **behaviour** rule is
the office's own: `00-IDENTITY.md` "the org must be able to lose".

**Why it makes the game better.** It makes the economy *legible as pressure*: a seat near the
floor visibly changes behaviour before it is terminated, so the tower's survival drama is
observable rather than a sudden disappearance.

**Attaches to: M7.** **Acceptance test.** `tests/test_office_runway_behaviour.py`: a seat below
the survival floor escalates to its manager (reusing `survival.daily_tick`'s path,
`survival.py:118`) and its next driver tick prefers a revenue-tagged task; a seat with healthy
runway is unaffected.

---

## L11 — Autowork-style per-seat policy (the dial the owner actually holds)

**What it is.** A durable per-seat autowork policy (`enabled`, cadence, directive) distinct
from posture: posture says *may it act unattended*, the policy says *how often and toward
what*.

**Source.** `agent-monitor` `src/lib/types.ts:363` (`AutoworkPolicy {enabled, intervalMs,
directive, lastSentAt}`) + `src/lib/autowork.ts` (`toPolicy`, the ticker) +
`AutoworkPanel.tsx`. Treated by that repo as "dashboard-visible auto-work settings"
(`types.ts:370-371`, `AutoworkConfig`) — distinct from autonomy.

**Why it makes the game better.** Gives the owner a single honest control over §L1 without
touching the org's autonomy semantics — the two dials are genuinely different questions
("can it" vs "does it, how often").

**Attaches to: M7.** **Acceptance test.** `tests/test_office_autowork_policy.py`: a disabled
policy suppresses ticks for that seat only; interval and min-gap are enforced; the policy is
durable across a restart.

---

## L12 — Stalled-work watchdog → escalation (close the loop on `stalled`)

**What it is.** `stalled` is computed (`tower.py:266`) and rendered, but nothing *acts* on a
stalled turn. A watchdog escalates a seat whose turn is open past a threshold.

**Source.** `agent-monitor` `src/lib/delivery.ts`/`TaskList` stalled-task semantics; and the
office's own M4 finding (a killed session must be visible, not silent).

**Why it makes the game better.** `01-THE-MACHINE.md` promises a speech bubble when someone is
blocked. A bubble that never resolves is a decoration; a bubble that triggers an escalation is
a system.

**Attaches to: M5.** **Acceptance test.** `tests/test_office_stalled_watchdog.py`: a receipt
open past N minutes produces one escalation to the supervisor (not N), and the escalation
carries the run id.

---

## L13 — The audit / integrity surface as a *game* signal

**What it is.** The already-built audit layer (`audit.py`, recon, separation-of-duties,
penalty ladder) currently surfaces only as CLI/report text. Expose failed controls to the tower
as a visible integrity state (a seat flagged, a floor flagged).

**Source.** the office's own `17-AUDIT.md` + M3's audit controls; the *presentation* idea is
starnet's `pride.js`/`trust.js` (honest counters, never fabricated).

**Why it makes the game better.** `00-IDENTITY.md`'s success criterion is a third party walking
any dollar to its seat. A control that fires but is invisible is the failure this office has
already paid for once (M3's `check_separation` "filed nothing"). Making it visible on the owner
surface the design already built is free honesty.

**Attaches to: M5.** **Acceptance test.** `tests/test_office_audit_surface.py`: an injected
separation-of-duties violation appears as a flagged state in `tower.state()` and clears only
when the underlying violation is fixed (append-only — never by editing a row).

---

## L14 — The owner-intake pipeline as a feedback loop (A1/A3 wiring)

**What it is.** Close A1/A3: wire `room_layout.programme()`/`check()` into the room-creation
path so it becomes the grant source (M11's whole point), and reconcile the two planners.

**Verified gap.** `43-OPEN-ITEMS.md` A1:32 — `grep -rn "room_layout" voicebridge/ frontend/
--include=*.py --include=*.js` → **no output**; it is imported by **tests only**. A3:99 — a
work room has no reserved manager's desk because `tower.floorplan()` assigns desk 0 as "the
first seat" (`tower.py:171-175`) with no `room_layout.check`. The `xfail` at
`tests/test_office_cross_module.py` (`43-OPEN-ITEMS.md:60-66`) is the same root.

**Source.** `agent-quest`'s *"the building an agent walks to IS what it is doing"* — which the
plan already names as the best idea in any source, but which **requires the programme to be
live** to be true. M11 declares the law; L14 is the code that makes it hold.

**Why it makes the game better.** Without it, every visual the merge adds is decor over an
org whose capabilities are not actually granted by the layout. With it, "a placed object IS a
capability grant" becomes testable end to end (C12).

**Attaches to: M5** (already named; L14 lists the exact acceptance it must meet).

**Acceptance test.**
```sh
$ .venv/bin/python -m pytest tests/test_office_cross_module.py -q -rxX
# 26 passed, 0 xfailed   (the xfail is gone because the disagreement is resolved)
$ grep -rn "room_layout" voicebridge/ --include=*.py   # a PRODUCTION consumer now exists
```
plus: a work room without exactly one manager's desk is refused by `room_layout.check` when the
plan is built (`room_layout.py:428,444`), and a test asserts a manager and its leaves share a
room with the manager on a distinct cell.

---

## 2. UI enhancements (U1–U8), ranked below the logic items

**Definition used here:** "adapt the logic" = the server computes real state and the tower
renders it (already done for all UI below — the tower is a pure function of the payload).
"Adapt the UI" = a *view* of that state. Per the owner's ruling, every UI item below is
**worthless until its logic item exists**, so U5 and U6 (which surface L1/L2/L3) are ranked
first within the UI set.

## U1 — The station zone map (rooms get real interiors)
`agent-monitor` `src/office/zones.ts` + `layout.ts`, `agent-quest` `building-layout.ts` +
`road-network.ts`. The office already has `_EVENT_STATION`/`STATIONS` (`tower.py:79-93`) as
*coordinates* but no zone semantics. Zone the room (workbench/vault/comms/shelf/meeting) so the
station walk of §L3 lands *in a drawn zone*, not in empty plate. **Attaches to M4.** Test: each
station maps to a drawn zone rect and a walk terminates inside it.

## U2 — Speech bubbles as real sprites (not just a marker glyph)
`pixel-agents` `office/sprites/bubble-waiting.json`,`bubble-permission.json`,`bubble-pet.json`;
`ai-town` `Character.tsx` status marker. The renderer's `drawMarker()` (`qubit-office.js:630`)
draws a letter in a box. **Attaches to M6.** Test: `blocked`/`stalled` render the bubble sprite,
and the bubble is absent when no event justifies it (C7).

## U3 — Informational detail panels (agent / room / floor)
`agent-monitor` `src/components/agent/{AgentDetail,SessionLog,TaskList,TokenUsage}.tsx`,
`agent-quest` `BuildingInfoPanel.tsx` + `DetailPanel.tsx`. The renderer's click-panel
(`qubit-office.js:1438-1461`) is five lines of text. **Attaches to M4.** Test: selecting an
agent renders identity + last-event + its real ledger rows; selecting a room renders its roster.

## U4 — The activity feed as a timeline, not a list
`agent-monitor` `ActivityTimeline.tsx` (time-bucketed bars) and `ActivityFeed.tsx`. The office's
feed (`qubit-office.js:1478`) is a plain log. **Attaches to M4.** Test: the timeline buckets
match the event rows' own timestamps; a quiet hour is drawn as a quiet hour.

## U5 — An "autonomy / driver" panel (surfaces L1 + L11)
`agent-monitor` `AutoworkPanel.tsx` + `OfficeControls.tsx` (behaviour info). Shows per-seat
policy, posture, last tick, and skipped reasons. **Attaches to M7** — a view of L1/L11, so it
ships *after* them. Test: toggling a seat's policy changes its displayed state and the next
tick's behaviour.

## U6 — A memory / dossier panel per seat (surfaces L2)
starnet `dossier.js` panel + `crewcards.js` (the agent card with a run clock and current step).
**Attaches to M8.** Test: the panel shows only beliefs with provenance; a fresh seat shows an
empty dossier, never invented content.

## U7 — A leaderboard / achievements view (surfaces L9)
`agent-monitor` `achievements/Leaderboard.tsx` + `AchievementList.tsx`; starnet `trophies.js`.
**Attaches to M5.** Test: ranks recompute from real rows; a seat with no completions is absent,
not zero.

## U8 — UI kit chrome from agent-quest (shelf/banners/ribbons)
`agent-quest` `UI/{Buttons,Icons,Pointers,Ribbons,Banners}` (90 files) — the plan only mentions
these as "HUD chrome" (`30-SOURCE-INVENTORY.md:63`). Use them for the towel bar, seat badges,
and the floor rail. **Attaches to M4.** Test: the shipping UI assets are a named subset of the
vendored manifest, and every one the renderer loads is in `assets/MANIFEST.json`.

---

## 3. What this changes in the merge plan

| plan item | change this file forces |
|---|---|
| `70-MILESTONES.md` | add **M7 (autonomous driver + goal loop + standing jobs)** and **M8 (agent memory + personality + relationships)**; L3 is an M4 sub-task |
| `40-ASSET-PIPELINE.md` §3 | the serving analysis is wrong for this plugin (§0.1); the pipeline must pick a serving root and prove it |
| `60-WIRING-TRAPS.md` Trap 2/3 | `.wav` is not the only blocker — the whole asset path is unreachable for this plugin as written |
| `10-DECISIONS.md` M6/M9 | M6's "50 sets" are mostly licensed likenesses (§0.3); M9's one-line fix does nothing without §0.1 |
| `30-SOURCE-INVENTORY.md` §3.1 | pose counts wrong (walk=6, sit=4 dirs, type=north only); byte total measured at ≈24.5 MB |
| `80-RISKS.md` R5/R7 | R5's "39,869 PNGs → gigabytes" fear is unfounded once the allowlist is real: the take is 24.5 MB; R7's sfx-first lever is a 2 MB saving |
| new risk | **R10 — the merge ships a pretty tower with no autonomous org.** The plan adds art and law to a renderer that already works; §L1–L14 are what make it a *game*, and none of them is in M0–M6 |
