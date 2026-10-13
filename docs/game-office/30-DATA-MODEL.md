# 30 — Data Model

One SQLite database, `<state>/office/office.db`, opened with the same connection discipline as
`voicebridge/kanban.py` (WAL, one `_migrate()` chain keyed on `PRAGMA user_version`, `_audit()`
on every mutation). Money is stored as **integer cents** — never floats.

Naming rule: `floor` is a department's physical level, `room` is a team's space inside it, `seat`
is a desk. An agent occupying a seat is an employee. An agent with no seat is terminated (or
unhired).

---

## `office_agent`

The employee record. This is the join between the org and Qubit's existing agent/session world.

| column | type | notes |
|---|---|---|
| `id` | INTEGER PK | |
| `slug` | TEXT UNIQUE | stable handle, e.g. `ceo`, `scout-2`, `builder-3` |
| `display_name` | TEXT | what the tower renders above the head |
| `seat_type` | TEXT | `ceo` \| `director_portfolio` \| `director_functional` \| `manager` \| `scraper` \| `analyst` \| `researcher` \| `worker` \| `support` — fixes the model tier (D3) and the capability set |
| `portfolio` | TEXT | for `director_portfolio`: which division kinds it owns. NULL otherwise |
| `title` | TEXT | "Head of Fulfillment" — free text, rendered on hover |
| `department_id` | INTEGER | FK `office_department`, NULL while in the hiring pool |
| `room_id` | INTEGER | FK `office_room`, NULL at department level |
| `seat_index` | INTEGER | desk slot within the room; unique with `room_id` |
| `status` | TEXT | `active` \| `idle` \| `working` \| `blocked` \| `suspended` \| `terminated` |
| `hired_at` / `terminated_at` | REAL | epoch |
| `balance_cents` | INTEGER | the survival number (D1) |
| `burn_rate_cents_day` | INTEGER | rolling 7-day mean of this agent's inference cost |
| `earned_cents_total` | INTEGER | lifetime gross attributed to this agent |
| `reputation` | INTEGER | 0–100, starts at 50. **Data, not a veto** (D25) — the agent reasons about it, it does not cap profit |
| `persona_path` | TEXT | on-disk persona/instructions file — never a DB blob |
| `ratchet_path` | TEXT | the learner file this agent appends to (D5) |
| `model_provider` / `model_name` | TEXT | resolved at hire from `seat_type`; overridable by approval only |
| `session_id` | TEXT | the Qubit session this agent works in (same store as chat) |
| `budget_cents_day` | INTEGER | hard daily inference allowance; runner refuses past it |
| `autonomy` | TEXT | `unattended_full` \| `supervised` \| `frozen` — the consent posture (D19/D28). Read live, per call |
| `exec_profile` | TEXT | `this_computer` \| `trusted_project` \| `safe_cell` \| `remote_ssh` (D20). Live for the next command |
| `exec_profile_reason` | TEXT | why this profile — recorded when it differs from the seat default |
| `created_by` | TEXT | `owner` \| agent slug that hired it |

Indexes: `(department_id)`, `(status)`, `(balance_cents)` — the last one is what the HUD sorts by.

**Default at hire:** `autonomy='unattended_full'`, `exec_profile='this_computer'`. An agent whose
work involves fetching and executing code defaults to `safe_cell` instead, with the reason
recorded. Both are owner-editable and take effect on the next turn — no restart.

## `office_floor` (D27)

A physical building level. **Holds 2–7 departments** — a floor is a shared level, not a department.

| column | type | notes |
|---|---|---|
| `id` | INTEGER PK | |
| `number` | INTEGER UNIQUE | 1 = ground. D13 caps at 60 |
| `name` | TEXT | "Floor 1", "Trading Floor" |
| `role` | TEXT | `operations` \| `intelligence` \| `director` \| `executive` \| `rnd` — **reserved org-level floors** (D30, D42). `operations` is the default: a normal department floor |
| `theme` | TEXT | optional visual theme for the level |
| `floorplan_path` | TEXT | JSON the renderer loads |

## `office_department`

A **business**, with its own P&L. Many departments share a floor (D27).

| column | type | notes |
|---|---|---|
| `id` | INTEGER PK | |
| `slug` | TEXT UNIQUE | |
| `name` | TEXT | rendered on the floor plate and the elevator directory |
| `floor_id` | INTEGER | FK `office_floor`. **Many departments per floor** (D27) |
| `director_id` | INTEGER | **mandatory, unique** — the `director_portfolio` that owns this business and its P&L (D35). Set once; changing it is a portfolio re-org, recorded |
| `floorplan_path` | TEXT | JSON file the renderer loads (see `60-FLOORPLAN.md`) |
| `division_kind` | TEXT | `product` \| `content` \| `service` \| `trading` \| `intelligence` \| `meta` (D21) |
| `template` | TEXT | which catalog template it was founded from (`12-DEPARTMENT-CATALOG.md`) |
| `charter` | TEXT | the department's mandate, one paragraph, written by whoever founded it |
| `risk_model` | TEXT | JSON — **per division kind** (see below). Never agent-editable |
| `manager_agent_id` | INTEGER | FK `office_agent`; NULL = unmanaged, a real problem state |
| `status` | TEXT | `proposed` \| `active` \| `profitable` \| `bleeding` \| `sunset` \| `dissolved` |
| `founded_at` / `dissolved_at` | REAL | |
| `revenue_cents_total` / `cost_cents_total` | INTEGER | rolled up from the ledger, never hand-written |
| `kill_criteria` | TEXT | the condition on which the manager has committed to shut it down |

Constraint: a `proposed` department has no floor number yet and renders nowhere. Founding it
assigns the next free floor and materializes its rooms.

### Risk model — per division kind (D21)

`risk_model` is a JSON object whose required keys depend on `division_kind`. The store validates on
write; a department cannot reach `active` without a complete risk model.

| Kind | Required keys |
|---|---|
| `product` | `max_inventory_capex_cents`, `max_platforms`, `tos_check_required: true` |
| `content` | `max_production_capex_cents`, `plagiarism_check_required: true`, `ai_disclosure_policy` |
| `service` | `max_open_engagements`, `min_rate_cents_hr`, `max_client_concentration_pct` |
| `trading` | `max_capital_cents`, `per_trade_cap_cents`, `daily_loss_limit_cents`, `max_drawdown_pct`, `paper_weeks_required` |
| `intelligence` | `robots_respected: true` (**must be true**), `rate_limit_rps`, `no_auth_wall_bypass: true` (**must be true**), `pii_forbidden: true` (**must be true**), `permitted_use_check_required: true` |

The `(must be true)` keys are not preferences — the store **refuses to create the department**
without them. That is where the legal exposure of a scraping floor is contained: in a schema
constraint, not in a prompt.

**Trading desks also require `funded: false` until the owner funds them** (D23). The org cannot
promote itself from paper to real.

## `office_review` — the "is this good?" request (D22)

| column | type | notes |
|---|---|---|
| `id` | INTEGER PK | |
| `ts` | REAL | |
| `raised_by` | INTEGER | FK `office_agent` |
| `department_id` / `business_id` | INTEGER | |
| `kind` | TEXT | `creative` \| `publish` \| `spend` \| `strategy` \| `pricing` \| `trade` |
| `mandatory` | INTEGER | 1 for publish / spend-above-allowance / trade-above-cap / account creation |
| `title` | TEXT | one line |
| `artifact_paths` | TEXT | JSON — **mandatory**, non-empty |
| `preview_path` | TEXT | rendered contact sheet / image / PDF — **mandatory** |
| `options` | TEXT | JSON `[{label, consequence}]` |
| `recommendation` | TEXT | **mandatory** — what the team does if nobody answers |
| `deadline_hours` | INTEGER | default 24 |
| `state` | TEXT | `open` \| `answered` \| `auto_applied` \| `withdrawn` |
| `answer` / `answer_choice` | TEXT | |
| `answered_at` | REAL | |

**Store constraints:** a row is refused without `artifact_paths`, `preview_path`, and
`recommendation`. A non-mandatory review may be raised by any seat; a **mandatory** one is raised by
the manager or CEO.

## `office_account_request` — "can I have what only you have?" (D23)

| column | type | notes |
|---|---|---|
| `id` | INTEGER PK | |
| `ts` / `raised_by` / `department_id` | | |
| `platform` | TEXT | |
| `purpose` | TEXT | why, and the expected first-dollar path |
| `test_mode_tried` | TEXT | the test path already used, and why it is insufficient |
| `needs` | TEXT | JSON subset of `legal_name`, `entity`, `address`, `tax_id`, `payout_account`, `email_alias`, `phone_otp`, `api_key` |
| `state` | TEXT | `open` \| `supplied` \| `created` \| `denied` \| `expired` |
| `funded` | INTEGER | 0/1 — has the owner funded this to real money? |
| `created_by` | TEXT | `agent` \| `owner` |

## `office_credential_ref` — handles, never values (D23)

| column | type | notes |
|---|---|---|
| `id` | INTEGER PK | |
| `department_id` | INTEGER | |
| `platform` | TEXT | |
| `handle` | TEXT | `env:ETSY_API_KEY` or `file:/run/secrets/etsy.key` — **a reference, never a secret** |
| `scope` | TEXT | what the platform tool may do with it |
| `state` | TEXT | `active` \| `revoked` |

There is **no column anywhere in this schema that can hold a secret value**, by design. A store
constraint, not a convention: an agent with the bypass on (D19/D28) that could write a key could also
exfiltrate it.

## `office_room`

A team's space. Rooms are carved out of the floor plan.

| column | type | notes |
|---|---|---|
| `id` | INTEGER PK | |
| `department_id` | INTEGER | FK |
| `slug` / `name` | TEXT | |
| `kind` | TEXT | `office` \| `lab` \| `studio` \| `war_room` \| `vault` \| `storefront` \| `lounge` \| `boardroom` \| `review` \| `design_studio` \| `writing_room` \| `editing` \| `cover_studio` \| `publishing` \| `sourcing` \| `fulfillment` \| `trading_floor` \| `risk_office` \| `crawl_room` \| `data_lab` \| `research` \| `analyst` \| `client_room` |
| `capacity` | INTEGER | desks the room physically holds; enforced against seated agents |
| `role_scope` | TEXT | `workers` \| `decision_makers` \| `researchers` \| `mixed` — **a capability boundary**, since a room's objects grant its occupants' tools (C12) |
| `bounds` | TEXT | JSON `{x,y,w,h}` in floor tiles, validated against the floor grid |
| `function` | TEXT | free text; the room's stated job |
| `created_at` | REAL | |

## `office_business`

The actual money-making unit. This is what makes D1 real — a department can hold more than one
business, and a business can be shared across departments.

| column | type | notes |
|---|---|---|
| `id` | INTEGER PK | |
| `slug` / `name` | TEXT | |
| `department_id` | INTEGER | FK, owning department |
| `thesis` | TEXT | why this is expected to make money, written **before** the first dollar is spent |
| `offer` | TEXT | what is actually sold, to whom, at what price |
| `channel` | TEXT | where it sells (`direct`, `marketplace:<name>`, `api`, `service`) |
| `status` | TEXT | `proposed` \| `building` \| `live` \| `profitable` \| `paused` \| `dead` |
| `launched_at` / `killed_at` | REAL | |
| `price_cents` | INTEGER | unit price, NULL for non-unit offers |
| `revenue_cents` / `cost_cents` | INTEGER | ledger rollup |
| `kill_criteria` | TEXT | required before `status = live` |
| `provenance` | TEXT | JSON: which agents proposed/approved, which research backed the thesis |

**Gate:** a business cannot reach `live` without a non-empty `kill_criteria`. That single
constraint is what stops the org from accumulating zombie products.

## `office_ledger`

**Append-only. No UPDATE, no DELETE — enforced by triggers, not by convention.**

| column | type | notes |
|---|---|---|
| `id` | INTEGER PK | |
| `ts` | REAL | |
| `agent_id` | INTEGER | FK, NULL only for owner-level entries (fixed fees, legal) |
| `department_id` / `business_id` | INTEGER | nullable FKs for rollups |
| `kind` | TEXT | `revenue` \| `inference` \| `tool` \| `infra` \| `fee` \| `legal` \| `refund` \| `transfer` \| `trade_pnl` \| `capital_injection` |
| `amount_cents` | INTEGER | **signed**: revenue positive, cost negative |
| `unmetered` | INTEGER | 0/1 — subscription-backed. **Excluded from every metered aggregate** (D16) |
| `attested` | INTEGER | 0/1 — an owner settlement of a previously-unknown run (D15) |
| `attested_as` | TEXT | `entered` \| `limit` — how it was settled; never claim `entered` for a limit booking |
| `model` | TEXT | resolved model name; **`(unknown)` rather than empty** — a blank field is worse than an explicit unknown |
| `currency` | TEXT | ISO 4217; multi-currency is expected once selling starts |
| `source` | TEXT | `stripe` \| `inference_router` \| `manual` \| `cron` … |
| `external_ref` | TEXT | payment id, invoice id, provider request id — UNIQUE per source |
| `artifact_path` | TEXT | the thing this money bought or sold, on disk |
| `run_id` | TEXT | the Qubit run that caused it, for reconciliation |
| `note` | TEXT | |

Indexes: `(ts)`, `(agent_id, ts)`, `(business_id, ts)`, unique `(source, external_ref)` — the
unique index is the idempotency guard that stops a double-counted payment.

Derived (materialized views, recomputed on write, never hand-edited):
`office_v_agent_pnl`, `office_v_department_pnl`, `office_v_business_pnl`.

## `office_idea` (D30)

The intake funnel's unit, carrying its full provenance chain. Schema and the gate rules are in
`13-PIPELINE.md`.

| column | type | notes |
|---|---|---|
| `id` | INTEGER PK | |
| `ts` | REAL | |
| `title` | TEXT | |
| `origin_agent_id` | INTEGER | the scraper/analyst who found it |
| `source_url` | TEXT | **mandatory** |
| `fetched_at` | REAL | **mandatory** |
| `permitted_use_checked` | INTEGER | 1 = the source's terms were checked (gate 0, D29 item 6) |
| `mechanism` | TEXT | **mandatory** — why this makes money |
| `source_channel` | TEXT | `scraper` \| `owner_intake` (D42) — so the two intakes are measurable separately |
| `supersedes_id` | INTEGER | FK `office_idea` — set when a re-digest replaces an earlier idea. **Never an edit** (C1 applied to ideas) |
| `evidence` | TEXT | JSON: cited facts with sources |
| `stage` | TEXT | `raw` \| `manager_review` \| `director_review` \| `ceo_review` \| `funded` \| `building` \| `dead` |
| `bounces` | TEXT | JSON `[{stage, reason, ts}]` — every bounce, kept |
| `skips` | TEXT | JSON `[{stage, justification, ts}]` — every fast-track, kept |
| `manager_verdict` | TEXT | `pass` \| `kill:<reason>` from the fixed vocabulary |
| `director_verdict` | TEXT | `pick:<portfolio>` \| `kill:<reason>` |
| `functional_notes` | TEXT | JSON `[{director, lane, note}]` — **advisory, never a veto** |
| `ceo_verdict` | TEXT | `fund` \| `defer` \| `kill` + capacity/sequence reasoning |
| `business_id` | INTEGER | set when it becomes a business |

**Store constraints:** `source_url`, `fetched_at`, and `mechanism` are mandatory. An idea with no
mechanism is a trend observation, and gate 0 refuses it.

## `office_report` (D24)

The CEO's accountability record. Claims are machine-checked against the ledger (C17) — a report
cannot be optimistic about numbers.

| column | type | notes |
|---|---|---|
| `id` | INTEGER PK | |
| `ts` | REAL | |
| `kind` | TEXT | `daily` \| `weekly_org_review` \| `decision_memo` |
| `author_agent_id` | INTEGER | the CEO, or a department manager for their own floor |
| `body_path` | TEXT | the written report on disk |
| `headline` | TEXT | one line |
| `recommendation` | TEXT | **mandatory** — what the author wants done |
| `reasoning` | TEXT | **mandatory** — why this, why now, what it costs, what it beats |
| `options` | TEXT | JSON `[{label, consequence}]` for structural memos |
| `ledger_snapshot` | TEXT | JSON of the metered totals the report claims, for the C17 check |
| `verified` | INTEGER | 0/1 — did the claimed numbers match the ledger on read? |

**Store constraint:** `recommendation` and `reasoning` are mandatory. A report with neither is
refused — a recommendation without an argument is a coin flip and cannot be reviewed.

## `office_run_receipts`

The dispatch receipts that make interrupted spend findable (D15). Written **before** the first
model call of every turn, removed only when the matching ledger row is durable.

| column | type | notes |
|---|---|---|
| `run_id` | TEXT PK | matches `office_ledger.run_id` |
| `agent_id` | INTEGER | |
| `ts` | REAL | dispatch time — the run's **real day**, kept even if settled much later |
| `provider` | TEXT | |
| `model` | TEXT | `(unknown)` if unresolved |
| `run_cap_cents` | INTEGER | the per-run cap in force when it started — **never** the booked amount |
| `state` | TEXT | `open` \| `settled` |
| `settled_at` | REAL | |

**Health, read by admission:**

```
health.complete = no read error AND no `open` receipt
health.durable  = no write error
```

A receipt with `state='open'` means spend is **unknown**, not zero. Budget admission refuses while
`health.complete` is false (D15) — an incomplete ledger is never headroom.

## `office_event`

The tower's activity feed — what the renderer animates from. Append-only, bounded retention.

| column | type | notes |
|---|---|---|
| `id` | INTEGER PK | monotonic; this is the render cursor |
| `ts` | REAL | |
| `agent_id` | INTEGER | |
| `kind` | TEXT | `state_change` \| `move` \| `tool_call` \| `skill_used` \| `mcp_call` \| `note_written` \| `memory_written` \| `ledger_write` \| `hired` \| `terminated` \| `promoted` \| `escalation` \| `meeting` \| `floor_edited` \| `run_started` \| `run_ended` \| `request_raised` \| `request_answered` \| `review_raised` \| `review_answered` \| `review_auto_applied` \| `account_requested` \| `account_supplied` \| `department_founded` \| `department_sunset` \| `trade_placed` \| `risk_limit_hit` \| `report_filed` \| `report_unverified` \| `attention_requested` \| `idea_found` \| `idea_passed` \| `idea_killed` \| `idea_bounced` \| `idea_fast_tracked` \| `idea_picked` \| `idea_funded` |
| `visible` | TEXT | `bubble` \| `badge` \| `notify` \| `silent` — which surface this event belongs on (D28). Bubbles self-clear; badges aggregate; **`notify` is reserved for mandatory reviews and blocking requests only** |
| `end_reason` | TEXT | for `run_ended`: `done` \| `empty` \| `clarifying` \| `error` \| `budget` \| `halted` — **`empty` must never render as a clean delivery** |
| `room_id` | INTEGER | where it happened, for the camera |
| `detail` | TEXT | JSON, redacted via the shared redactor before it lands |
| `cost_cents` | INTEGER | non-null for anything that burned money |

The renderer consumes `office_event` over the existing `/api/events` WebSocket; it does not poll
the store. A destroyed/rebuilt view replays from its last cursor, exactly like `EventHub`.

---

## Money and survival

```
net_cents(agent)   = sum_metered(ledger.amount_cents where agent_id = ?)   # unmetered EXCLUDED (D16)
daily_burn(agent)  = |sum_metered(inference+tool where ts > now-7d)| / 7
runway_days(agent) = balance_cents / max(daily_burn, 1)
```

**Admission rule (D15):** if `health.complete` is false — any `open` receipt, or an unreadable
ledger — every budget check **refuses**. Unknown spend is never treated as zero, and never as
headroom.

**Termination condition** (evaluated by the daily tick, never by the agent):
`runway_days < SURVIVAL_FLOOR_DAYS` (default 7) **and** the manager has been notified at least
once **and** no human override is on record.

The condition is deliberately conservative. An agent that dies the moment it dips is fragile; an
agent that never dies has no survival pressure. Seven days of runway is enough to ask for capital
and short enough to be a real deadline.

## `office_envelope` (D37)

The spend plan per level, per period. Allocated top-down; never edited, only superseded by the next
period's row.

| column | type | notes |
|---|---|---|
| `id` | INTEGER PK | |
| `period` | TEXT | `2026-11` — one row per scope per month |
| `scope` | TEXT | `org` \| `portfolio` \| `department` |
| `scope_id` | INTEGER | portfolio/department id; NULL for `org` |
| `allocated_cents` | INTEGER | set by the level above |
| `allocated_by` | INTEGER | agent id — the CEO sets portfolios, the director sets departments |
| `carried_cents` | INTEGER | unspent from the previous period, capped at one month |
| `reserve` | INTEGER | 1 = the CEO's unallocated reserve (~20% of the org envelope) |

**Derived, never stored:** spend, remaining, and utilisation are `GROUP BY` over `office_ledger`.
A hand-written utilisation number is refused (C2).

## `office_intake_item` (D42/D43)

What the owner dropped into the R&D floor, and what happened to it. This is the only intake in the
org besides the scraper firehose, and it is deliberately a different table from `office_idea` — an
item is **material**, an idea is **a business hypothesis**, and most material never becomes one.

| column | type | notes |
|---|---|---|
| `id` | INTEGER PK | |
| `ts` | REAL | when it landed |
| `source_kind` | TEXT | `url_video` \| `url_article` \| `file` \| `voice` \| `note` |
| `source_ref` | TEXT | the URL, the file path, or the transcript of what the owner said |
| `owner_note` | TEXT | **the owner's own words about why it caught his eye.** The one field a researcher cannot reconstruct |
| `status` | TEXT | `new` \| `digesting` \| `digested` \| `triaged_out` \| `failed` |
| `priority` | INTEGER | 0 default; the owner may raise one item |
| `via` | TEXT | `folder_watch` \| `voice` \| `dashboard` |
| `deadline_ts` | REAL | **set on creation** — `ts + 72h` (D43). Not extended by an agent |
| `triaged_reason` | TEXT | mandatory when `status` is `triaged_out` |
| `failure_reason` | TEXT | mandatory when `status` is `failed` (paywall, no captions, dead link) |
| `idea_id` | INTEGER | FK `office_idea`, set when the digest produced one |

**Store constraints:**
- **capacity** — the store refuses an insert that would make open items (`new` + `digesting`)
  exceed **12**. Intake refuses politely and tells the owner; it does not queue.
- **deadline** — `triaged_out` requires `triaged_reason`; `failed` requires `failure_reason`. An item
  cannot be closed silently.
- **immutability** — `source_ref`, `owner_note`, `ts`, and `deadline_ts` are write-once. Only
  `status`, `priority` and the outcome fields change.

## `office_goal` (D41)

Every seat works toward a goal with a deadline. This is what makes the tower's people legible
without reading a transcript.

| column | type | notes |
|---|---|---|
| `id` | INTEGER PK | |
| `agent_id` | INTEGER | the seat that holds it |
| `statement` | TEXT | what it wants, in the agent's own words |
| `deadline` | REAL | **mandatory** — a goal without a deadline is a preference |
| `metric` | TEXT | how it is judged: `artifact_count` \| `margin_cents` \| `rating` \| `custom:<name>` |
| `progress` | REAL | updated from artifacts, not typed by the agent |
| `outcome` | TEXT | `open` \| `landed` \| `near_miss` \| `failed` \| `abandoned` |
| `profile_delta` | TEXT | JSON — the trait drift this outcome recorded (D41) |

**Constraint:** `profile_delta` may only be written by the store, and only on a recorded outcome.
An agent cannot edit its own profile; drift is a consequence of a scored result, never a mood.

## `office_audit_finding` (D40)

Append-only, like the ledger. Findings are never edited or deleted; a closed finding keeps its row.

| column | type | notes |
|---|---|---|
| `id` | INTEGER PK | |
| `ts` | REAL | |
| `period` | TEXT | |
| `business_id` | INTEGER | which business the sample landed on |
| `kind` | TEXT | `ghost_work` \| `cost_shifting` \| `sandbagging` \| `report_inflation` \| `ledger_mismatch` |
| `severity` | TEXT | `minor` \| `repeat` \| `intentional` |
| `evidence` | TEXT | JSON — artifact ids, ledger rows, the diff |
| `status` | TEXT | `open` \| `closed` |
| `closed_ts` | REAL | |

See `17-AUDIT.md` for the checks and the penalty ladder.

## `office_recipe` (D41)

A proven multi-step business procedure, promoted from a department that actually ran it. Distinct
from a *skill*: a recipe carries an economics model and a test path, and it is the thing a new
department starts from instead of re-deriving the business.

| column | type | notes |
|---|---|---|
| `id` | INTEGER PK | |
| `name` | TEXT | e.g. `pod-listing-launch` |
| `version` | INTEGER | recipes version; a change is a new version, never an edit |
| `division_kind` | TEXT | which business kind it applies to |
| `steps` | TEXT | JSON — the actual sequence, each naming a tool or a seat type |
| `economics` | TEXT | JSON — the proven unit economics it produced, with the business it came from |
| `proven_by_business_id` | INTEGER | the business that earned the promotion |
| `promoted_by` | INTEGER | the director that promoted it |

## What is deliberately NOT in the schema

- **No `office_asset` table.** Tripo asset manifests are files (`assets/manifest.json`) beside
  the GLB files — they are build inputs, not runtime state.
- **No `office_skill` table.** Skills are the existing `qubit_runtime/skills` tree. The ratchet
  file references skill names; it does not copy them (D9).
- **No `office_message` table.** Agent-to-agent talk is a chat session — the existing session
  store. The tower renders the *event* (`office_event` rows), not a second message log.
- **No P&L columns written by hand anywhere.** Every rollup is derived from `office_ledger`.
  A hand-written total is a lie waiting to happen.
- **No unmetered row in a metered aggregate.** Two folds (`sum_metered`, `sum_reported`) exist and
  are never interchangeable — a phantom expense line would deny real work (D16).
