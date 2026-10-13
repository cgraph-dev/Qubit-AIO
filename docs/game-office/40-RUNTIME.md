# 40 — Runtime (how an agent actually does a turn)

The office layer is a **scheduler and an org chart over the existing agent core**. It does not
implement an agent loop, a tool registry, or a memory system (D9). It decides *who works, when,
on what, under which budget*, and turns the result into ledger rows and events.

## The turn

```
office_tick(now)
  for each department (by floor):
     manager = department.manager_agent_id
     for each active agent on the floor:
        if agent.budget_remaining_today <= 0:      -> skip, record `budget_exhausted`
        if agent.runway_days < SURVIVAL_FLOOR_DAYS -> -> escalation to manager, skip
        task = next_task_for(agent)                # from the department's backlog
        if task is None:                            -> record `idle`, skip
        if not ledger_health().complete:             -> refuse: unknown spend (D15)
        receipt = begin_run(agent, run_cap)          # BEFORE the first model call (D15)
        result = run_agent_turn(                    # the EXISTING core turn
            agent.session_id, task, seat_toolset(agent.seat_type),
            model=resolve_model(agent), budget=agent.budget_remaining_today)
        record_ledger(result.usage, unmetered=result.subscription_backed)
        finish_run(receipt)                          # only after the row is durable
        record_events(result.events)                # office_event rows for the renderer
        attribute(result.artifacts, agent, task)    # artifact -> run_id -> ledger
        if result.needs_approval: stage(result)     # the shared confirmation journal
```

Everything in that loop already exists in some form: `voicebridge.agent.run_agent_turn` runs the
turn, the session store is the real one, the confirmation journal is the real one, the e-stop is
the real one. The office module adds the *ordering*, the *budget*, and the *attribution*.

## Cadence — three clocks, deliberately separate

| Clock | Interval | Does | Cost |
|---|---|---|---|
| **Work tick** | continuous, per-agent, bounded concurrency | one agent turn each | variable, budget-capped |
| **Daily tick** | 24h | reflection turns (D4), payroll/survival evaluation, P&L rollup, runway alerts | ~$0.01–$0.05 per agent |
| **Weekly tick** | 7d | cross-pollination, CEO org review, department P&L report to the owner | manager/CEO tier, bounded |

Implemented as Qubit cron jobs on the existing scheduler (`qubit_runtime/cron/scheduler.py`), one
job per clock, each with a declared toolset and model. This reuses the scheduler's existing
failure handling, incident records, and delivery — the office does not grow its own watchdog.

**Concurrency is capped, not unlimited.** `OFFICE_MAX_CONCURRENT_TURNS` (default 4). Twenty agents
"working at once" is twenty parallel model calls; the cap is what keeps a busy day from becoming
an unbounded bill. Queued agents render as `idle` with a clock overlay — which is honest, and also
visible (the tower shows a backlog).

## Task backlog — where work comes from

Four sources, in priority order:

1. **Escalation response** — the owner or a manager answered something; the agent resumes.
2. **Business work item** — from the business's own plan (a checklist the business owns).
3. **Manager assignment** — a manager assigned a task to this agent or this room.
4. **Self-directed improvement** — the agent's own backlog (from its last reflection turn).

A department with an empty backlog and no live business is a department that is not doing
anything. That is a real state (`status = bleeding` if it is costing money), and the CEO sees it.

## Session isolation

Each agent works in its **own Qubit session** (`office_agent.session_id`), in its own department's
profile scope. Agents do not share a conversation — an office where every agent appends to one
chat is not an org, it is a single confused assistant. Cross-agent communication is explicit
(an escalation, a handoff, or a written note), which is also what makes it renderable and
auditable.

## Model resolution

```
model = agent.model_override              # only set by an approved promotion
     or SEAT_TIER[agent.seat_type]         # D3
     or department default
     or org default
```

The resolved model is written to the ledger row, so a cost surprise is always traceable to the
agent and the seat that caused it. There is no implicit fallback chain inside a turn — a turn
either runs on the seat's model or it does not run.

## Budget enforcement — where it actually bites

Budget is checked **before** the turn (admission) and **after** (recording real usage). The
before-check is an estimate from the agent's rolling mean; the after-check is the truth. An agent
that overshoots its estimate has the overshoot debited and its next-day allowance reduced by the
same amount — so a bad estimate is self-correcting rather than free.

The org-wide cap (`OFFICE_DAILY_BUDGET_CENTS`) is checked at the same admission point. When it is
hit, in-flight turns finish, new turns do not start, and the org records the event. It does not
quietly keep going.

## Failure and termination

| Situation | Response |
|---|---|
| Turn ends with `empty` (zero tools, no text) | Recorded as `run_ended` with `end_reason='empty'` — **never renders as a clean delivery**. Retried once, then flagged to the manager (a degraded provider is not a successful turn) |
| Turn error | Retry once, then record `blocked` with the real error; the agent stays employed |
| Repeated failure (N turns) | Manager notified; the seat is flagged for review |
| Budget exhausted | Agent idles until tomorrow; if this repeats, the manager sees a trend |
| Runway below floor | Escalation to manager → then to the owner. Never an automatic silent death |
| Owner e-stop | Everything stops at the admission point. Runs are marked **halted** (not just aborted) so a deliberate stop is distinguishable from a crash, and the mark is applied before the abort so no stale partial is delivered. The e-stop path must never itself throw |
| Agent terminated | Seat freed, session archived, ledger preserved. The agent's ratchet is kept on disk — an agent that learned something useful and then died still left the org smarter |

Termination is **not** deletion. The row stays, `status = terminated`, and its history stays in
the ledger and the events. An org that erases its failures cannot learn from them.
