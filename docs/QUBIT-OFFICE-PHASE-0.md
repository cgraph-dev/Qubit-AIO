# Qubit Office — Phase 0 execution plan

**Status:** ready-to-start design/contract phase. No implementation is claimed.
**Rule:** Phase 1 does not start until every Phase 0 exit gate has evidence.

## 1. Decision: durable execution engine

**Decision:** use **Temporal** as the canonical durable workflow engine for consequential
workflows, with PostgreSQL persistence. Do **not** build a homegrown workflow scheduler and do not
treat LangGraph checkpoints, Airflow DAGs or a SQLite queue as a transaction boundary for external
effects.

- Model/agent turns, tool calls, provider calls, payments, publishing, email, Etsy, CRM writes and
  human approvals are **activities** or signals/waits.
- Workflow code stays deterministic; external effects live in idempotent activities.
- Each activity has an idempotency key, provider receipt/request ID and reconciliation path.
- Human approval is a durable workflow wait/signal, not a chat message.
- Run history is the workflow history; the Business OS stores the business-readable projection.
- Use PostgreSQL for Temporal persistence and the Business OS; SQLite remains acceptable for a
  single-user local projection only if it is not the workflow authority.
- If Temporal/server dependencies are unavailable, consequential workflows **fail closed**; there
  is no silent fallback to a non-durable loop.

**Why not alternatives now**

- LangGraph/LangSmith: good graph state/tracing, not a complete external-effect transaction
  boundary.
- Airflow: scheduled DAGs, not interactive long-lived agent sessions/human waits.
- Homegrown SQLite scheduler: would reimplement timers, retries, history and replay; high risk.
- CrewAI/AutoGen/OpenAI Agents SDK: orchestration primitives, not durable business workflow.

**Later option:** a renderer/workflow adapter may support another engine, but Temporal remains the
reference until a replacement passes the same recovery/reconciliation tests.

## 2. Staging, test and replay environment

Required before Phase 1 implementation:

- Throwaway `QUBIT_HOME`, XDG dirs and `TMPDIR`.
- Synthetic tenant/business/departments; no operator data.
- Fake model provider, fake MCP/tool server, fake Etsy/social/CRM/payment/email transports.
- Recorded provider/tool traces and deterministic seeds/clocks.
- Fault injection: timeout before/after commit, duplicate delivery, lease expiry, provider 5xx,
  revoked capability, stale approval, corrupt memory, conflicting patch, restore failure.
- No live credentials, no real external writes, no paid-art packaging.
- Replay a run from event history and compare artifacts/receipts.

## 3. Phase 0 work items

### P0-01 — Freeze decisions and authority

- Freeze canonical phase order v7.
- Record owner decisions and non-goals.
- Authority: Business OS event log/SQLite/Postgres; Obsidian projection; OpenViking adapter.
- Exit: this document + master plan + decision log are committed.

### P0-02 — Domain/projection contract

- Finalize entities and invariants in `docs/QUBIT-OFFICE-DOMAIN-CONTRACT.md`.
- Generate schema/types after owner review.
- Exit: every field has one owner; projections are rebuildable.

### P0-03 — Asset ledger

- Inventory `/home/trick/Desktop/UI Office`: 1,523 files / 404 MB.
- Record pack, license, paid/local-only, intended room/character/UI/outside use.
- Generate per-file sha256/dimensions ledger in a local artifact; do not commit paid art.
- Exit: no asset enters a room/character/bundle without a ledger row.

### P0-04 — Idea and repo ledgers

- `docs/QUBIT-OFFICE-IDEA-LEDGER.md`: every owner idea + status.
- `docs/QUBIT-OFFICE-REPO-LEDGER.md`: repo, commit, license, take/avoid, status.
- Exit: no “reviewed” repo is treated as approved code; no idea is lost in chat.

### P0-05 — Threat model

- `docs/QUBIT-OFFICE-THREAT-MODEL.md`: assets, actors, boundaries, threats/controls/tests.
- Exit: security gates for external actions are accepted.

### P0-06 — Durable engine prototype

- Bring up Temporal locally with PostgreSQL (Docker Compose or equivalent).
- Prototype: one workflow with an activity that writes a fake external receipt, one human approval
  wait, one timer, one retry/timeout and one unknown-outcome reconciliation.
- Exit: restart during the workflow resumes; duplicate activity is idempotent; approval survives
  restart; unknown outcome reconciles.

### P0-07 — Test/staging/replay harness

- Disposable home/tenant, fake transports, deterministic clock/seed, fault injection.
- Exit: same scenario passes from a clean environment and replays from history.

### P0-08 — Phase-1 backlog

- Break Phase 1 into tickets with acceptance tests and owners.
- Exit: each ticket maps to a domain record, capability, policy and test.

## 4. Phase 0 exit gates

1. Domain contract, asset/idea/repo ledgers, threat model and phase order are committed.
2. Temporal decision is recorded with a working local prototype.
3. Staging/replay environment is reproducible from a command.
4. No real credentials or operator data are used.
5. Phase 1 tickets are bounded and have failure/recovery tests.
6. The tower, character studio, outside world, mature interactions and social integrations remain
   explicitly deferred.

## 5. First Phase 1 tickets (preview)

1. Identity/tenant/owner principal + local auth boundary.
2. Event log/audit skeleton + correlation IDs.
3. Temporal workflow/activity skeletons + idempotency/receipts.
4. Run/task/workspace/checkpoint/lease records.
5. Policy compiler + capability catalog skeleton.
6. ToolGateway stub with deny-by-default and fake transports.
7. Message fabric skeleton.
8. Approval workflow with payload hash/expiry.
9. Staging/replay test harness.
10. Backup/restore skeleton.

## 6. Explicitly not Phase 0

- 100-floor world, room builder, character studio, outside world.
- Mature interactions, social accounts, real Etsy/social/CRM credentials.
- Broad agent autonomy, 283 active agents, multi-department cloning.
- Godot, advanced FX, 1,000-agent load tests.
