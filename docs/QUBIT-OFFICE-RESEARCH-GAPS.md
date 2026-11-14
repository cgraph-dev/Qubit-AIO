# Qubit Office — external research, gaps and forgotten requirements

**Status:** research synthesis. Not a release verdict, not `VERIFIED`, and not evidence that any
item is implemented. This file records what external reviews added to the plan and what the plan
was missing.

**Method:** three Perplexity reviews (agent platform patterns, security/privacy/ops, business
operations) plus direct inspection of the reference repos. Product/company statements are
patterns to evaluate, not verified feature claims. Verify before porting code.

---

## 1. Verdict

The plan has strong intent and a credible control-plane skeleton, but it is missing foundation
requirements that established agent/workflow platforms treat as mandatory:

- durable execution and reconciliation
- real identity, tenancy and authorization
- sandbox, secrets and egress control
- leases/checkpoints and a message fabric
- approval durability and anti-confusion
- observability, evaluation, cost attribution and disaster recovery
- business systems of record, not only an office renderer

The first build must not be the tower, the outside world, the character studio or mature
interactions. It must be **one durable department with one real agent lane and one real workflow**.

---

## 2. Company/platform patterns

| Source | Pattern to take | Avoid |
|---|---|---|
| Temporal | Deterministic durable workflows, activities for external effects, timers, signals, heartbeats, retries, event history | Model/network/database calls inside replayed workflow code; assuming retries equal exactly-once |
| LangGraph / LangSmith | Explicit graph state, checkpoints, interrupts, human-in-the-loop, tracing/evaluation | Treating a graph checkpoint as a full external transaction boundary |
| OpenAI Agents SDK | Instructions, tools, handoffs, guardrails, tracing | Confusing an SDK run/trace with durable business state or approval authority |
| Anthropic | Lead agent + delegated subagents, parallel research, synthesis | Unrestricted inherited authority, secrets or budgets |
| Microsoft Agent Framework / AutoGen | Workflow/agent abstractions, identity/governance/observability alignment | Unconstrained group chat for consequential operations |
| CrewAI | Role/task/crew modeling and simple delegation prototypes | Assuming role prompts provide security or durability |
| Google ADK | Sessions, tools, subagents, callbacks, deployment integration | Provider-specific session state as the only source of truth |
| Letta | Stateful agents with persistent memory | Memory as an ungoverned global pool |
| Mem0 | Memory extraction/storage/retrieval with user/app scopes | Treating extracted memory as unquestioned truth |
| OpenViking | Hierarchical/filesystem-like context and scoped retrieval | Inventing writable filesystem paths or bypassing authenticated namespaces |
| Airflow-style workflows | DAGs, schedules, retries, task dependencies, logs, monitoring | Using a DAG scheduler as an agent runtime with no leases/approvals |
| Enterprise agent platforms | Identity, tools, connectors, policy, approvals, budgets, audit, tenancy, observability | A control plane that is only a dashboard |
| SoWork / Roam | Office as presence/collaboration layer over real work | Rebuilding collaboration tools instead of integrating |
| Dynamics 365 / Salesboom | CRM/ERP/support workflows are the business, not the office | A standalone office with no systems of record |
| Fluid AI / Logic Mount | Orchestration across CRM/ERP/legacy systems with human handoff | Chatbot-only architecture |
| Azhi Flow | Durable orchestration + tools + knowledge + approvals + audit + cost | Self-hosted claims without operational contracts |

---

## 3. What the plan already has

- Office as projection/control room, not business truth.
- Departments/floors/rooms/desks and capability-bearing rooms as a design goal.
- Per-agent records, controls, previews, budget/autonomy fields.
- War Room, approvals, Etsy pilot/connect, world projection, occupancy.
- Sessions/workspaces/checkpoints/message fabric/handoff as design.
- Obsidian/OpenViking memory design.
- Character Studio, outside world, panels, capability catalog and asset ledger as design.

The gap is not vocabulary. The gap is foundation and enforcement.

---

## 4. Must-have additions

### P0 — before a second department or broad UI

| Area | What we need | Why |
|---|---|---|
| Durable execution | Temporal or equivalent: workflows, activities, timers, signals, retries, event history, replay | Agent runs and human waits must survive restarts and provider failures |
| Run state machine | queued, leased, executing, waiting, approval, checkpointed, completed, failed, cancelled, unknown | Prevents false completion and duplicate effects |
| Identity | Human IdP/SSO/MFA; stable human, agent, service, tool, workflow identities; workload tokens | Desk/room/role cannot be the security principal |
| Authorization | RBAC + ABAC; tenant/business/department/resource/action/data/expiry; deny wins | Per-agent permissions need resource scope |
| Tenancy | Isolation in DB, storage, queues, caches, vector indexes; tenant-scoped keys | Personal now, entities later; avoid cross-tenant leaks |
| Capability gateway | One execution boundary for model/tools/MCP/terminal/browser/files/messages/Etsy/memory | A catalog or permission list is not enforcement |
| Policy compiler | Effective grants, inherited vs explicit, denials, expiry, explainable decision | UI must show what the agent can actually do |
| Capability snapshot | Freeze policy/model/tool/server/skill versions and budget per run | Revocation and replay must be deterministic |
| Sessions | Durable owner↔agent lane, participants, visibility, default memory/workspace | A global chat loses ownership and recovery |
| Runs/tasks | Bounded execution and durable work with acceptance criteria, budget, deadline, artifacts | Work must move and recover |
| Leases/fencing | task/run/workspace/mutable-resource leases, heartbeat, expiry, fencing token | Concurrency otherwise duplicates writes |
| Workspaces | task-pinned workspace, immutable artifacts, worktree/sandbox, retention | Agent-owned mutable workspace contaminates tasks |
| Checkpoints | state, tool receipts, pending approvals, memory writes, workspace revision, safe resume | Chat summaries are not recovery points |
| Message fabric | append-only events, topics, ACLs, threading, delivery/ack, correlation, replay, dead letters | Owner/agent/agent chat needs one substrate |
| Handoff | checkpoint, artifacts, unresolved questions, capabilities, acceptance, source read-only | Handoffs otherwise lose context/authority |
| Memory policy | event log authoritative; Obsidian projection; OpenViking adapter; provenance/retention/deletion | Two memory stores with no authority diverge |
| Approvals | durable approval tasks with exact payload hash, evidence, risk, cost, reversibility, expiry, delegate/escalate | Chat approvals and mutable drafts are unsafe |
| Reconciliation | provider request IDs, idempotency keys, unknown outcomes, retry/query/rollback | Timeouts are not failures |
| Sandbox/egress | ephemeral unprivileged execution, read-only base, resource limits, no host sockets, deny egress | Terminal/browser/MCP/file tools are high-risk |
| Secrets | external manager, short-lived per-tool credentials, injection at gateway, scanning | Workspaces/prompts/memory are durable leak surfaces |
| Prompt injection | untrusted content isolation, provenance/trust labels, action validation outside model, red teaming | Files/web/MCP/messages can contain instructions |
| Audit/observability | tamper-evident event chain, traces, run timelines, policy decisions, cost, anomalies | Unit tests do not prove operations |
| Evaluation/release | golden sets for security/privacy/correctness/cost; canary, version pinning, rollback | Agents degrade after model/prompt/API changes |
| Backup/DR | encrypted immutable backups, RPO/RTO, tenant restore, failover/restore drills | 24/7 business cannot rely on one PC |
| Cost/fairness | per-tenant/agent budgets, reservations, quotas, backpressure, queue age, cost attribution | Runaway agents and starvation |
| Systems of record | CRM, support, marketing, finance/accounting, payments, contracts, HR, procurement, analytics | The office is not the business |
| Etsy/digital ops | listing, digital asset versions, malware scan, entitlements, refunds, payouts, disputes, reconciliation | A listing draft is not a business |
| Multi-entity | owner/tenant/legal entity/brand/books/bank/tax separation | Personal now, Estonia/HK/US later |
| Self-hosting | setup, migrations, health, import/export, backup/restore, provider substitution, telemetry policy | Avoid hidden SaaS and lock-in |
| Exit/manual ops | pause/close/export/cancel/revoke agents, manual fallback SOPs | Automation failure must not end the business |

### P1 — after one department works

- Character Studio (Habblaud + LimeZu) with canonical profile and projections.
- Asset/license ledger and build/runtime leakage scans.
- Renderer-neutral map schema, exterior, one department map.
- Room/department builder with validation/versioning.
- Full panel inventory and effective-state UI.
- Mature interactions/FX with bounded authority and consequences.
- 100-floor performance budgets and active/dormant scheduler.
- Godot parity contract only after web slice.

---

## 5. What we forgot

1. **Exactly-once is usually observation, not execution.** Retries can duplicate effects; use idempotency and reconciliation.
2. **Unknown outcome is a first-class state.** Payments, emails, listings and CRM writes can time out after committing.
3. **Cancellation is distributed.** Stop must revoke leases/tokens, interrupt calls, handle committed effects and surface unknown outcomes.
4. **Schema/prompt migrations.** Old checkpoints, tasks and artifacts outlive code and model versions.
5. **Backpressure/fairness.** A durable platform can still starve users or exhaust provider quota.
6. **Secrets lifecycle.** Vault refs, short-lived per-tool credentials, rotation/revocation, scanning.
7. **Read-path permissions.** Retrieval, caches, traces, previews and summaries can leak data even when a tool is safe.
8. **Approval bottleneck and anti-confusion.** Approval needs ownership, escalation, expiry, batching, payload hash and evidence.
9. **Agent identity ≠ visual identity.** Desk/avatar/department must not grant access.
10. **Renderer is not truth.** Business must run with browser closed and rooms dormant.
11. **Dead letters need operators.** Classification, replay, dedupe, ownership, incident links.
12. **Provider fallback changes semantics.** Tool calling, safety, context, structured output, latency, residency.
13. **Memory deletion/correction.** Retention, legal deletion, user correction, stale-fact invalidation.
14. **Data freshness.** Prices, inventory, tax, permissions and customer status go stale.
15. **Clock/timezone/calendar.** SLAs, schedules, tax periods, recurring obligations.
16. **Outbound communication controls.** Published/sent messages are irreversible.
17. **Consent and communication compliance.** Opt-outs, spam, claims, regulated communications.
18. **Tax/marketplace obligations.** VAT/sales tax/payout fees even for digital goods.
19. **Asset/IP provenance.** AI art, fonts, templates, stock and customer files have different rights.
20. **Separation of duties.** One agent must not create a vendor, approve spend and pay.
21. **Cost allocation.** One model bill hides which department/business is unprofitable.
22. **Post-deploy evals.** Availability can hide quality/safety regression.
23. **Human availability.** An approval gate is useless without an assigned, reachable, authorized human.
24. **Manual fallback.** How to run the business when automation is down.
25. **Shutdown/exit.** Pause, close, export, cancel subscriptions, revoke agents.
26. **Office UX overstates autonomy.** Animated desks/chat do not prove committed outcomes.
27. **Self-hosting dependencies.** Storage, queue, DB, object store, secrets, email, telemetry, assets.
28. **Model/prompt version governance.** Untracked changes make incidents irreproducible.
29. **Data poisoning.** Retrieved docs, memory and knowledge can steer agents.
30. **Tool output limits.** Oversized/adversarial outputs cause context flooding or hidden instructions.

---

## 6. Prioritized additions to the execution order

1. Canonical domain/projection contract + event log.
2. Durable workflow engine decision and run state machine.
3. Identity, tenancy, authorization, session revocation.
4. Capability gateway + policy compiler + capability snapshots.
5. Sessions, runs, tasks, workspaces, leases, checkpoints, handoffs.
6. Message fabric.
7. Approvals + reconciliation + unknown outcomes.
8. Sandbox, secrets, egress, prompt-injection defenses.
9. Memory policy/adapter; Obsidian projection; OpenViking scoped adapter.
10. Audit, observability, evals, cost/fairness.
11. One department floor + one real agent lane + one real workflow.
12. Business systems of record (CRM/support/marketing/finance/contracts/HR/procurement/analytics).
13. Etsy digital operations end-to-end with approvals/reconciliation.
14. Backups/DR/self-hosting/exit/manual SOPs.
15. Character Studio, asset ledger, room builder, maps/exterior.
16. Interactions/FX/scale/Godot only after the above.

---

## 7. Acceptance gates for the first real slice

- One agent has a durable session, task queue, workspace, lease and checkpoint.
- Owner↔agent and agent↔agent messages persist and replay.
- One capability call is denied without a grant and allowed with one.
- One approval is bound to exact payload hash and expiry.
- One external draft returns a provider receipt; timeout produces `unknown` and reconciles.
- Restart after a side effect does not duplicate it.
- Revocation interrupts an active call.
- Memory write is scoped and visible in Obsidian; retrieval respects ACL.
- Cost and trace IDs correlate the run end-to-end.
- Backup/restore of the department works.

**If these gates are not met, the tower, character studio, outside world, mature interactions and
extra departments are decoration, not product.**

---

## 8. Additional 20 findings (not duplicates of §4–§5)

### 10 must-have additions

1. **Isolated test, staging and replay environments** — disposable tenants, fake external systems, deterministic seeds, recorded traces, fault injection, production-like policy/data fixtures. Without this, agent changes are tested only in production.
2. **Data lineage and provenance graph** — field-level source, retrieval, transformation, prompt, model, handoff and output lineage. Without it, wrong outputs cannot be investigated, corrected or defended.
3. **Schema and policy migration framework** — version schemas/policies/prompts/tools/checkpoints, compatibility adapters, dry-run migrations, dual-read/write, rollback and ownership. Old runs outlive new code.
4. **Periodic identity and access recertification** — scheduled owner/manager reviews, dormant-access flags, expiring temporary grants, explicit agent renewal. Agent sprawl otherwise accumulates permanent access.
5. **Notification, escalation and on-call policy engine** — severity/tenant/workflow/business-hours routing, acknowledgements, paging, suppression, escalation tiers and handoff history. Observability without ownership is noise.
6. **Jurisdiction-aware legal/regulatory rule engine** — runtime evaluation of jurisdiction, residency, sector, consent, retention, disclosure and action restrictions, with versioned effective dates. One global policy is not enough.
7. **Content moderation and asset-use enforcement** — scan text/images/audio/video/uploads, enforce blocked categories, audience/age rules, provenance labels, takedown and appeal. An asset ledger does not prevent misuse.
8. **Verifiable action and decision receipts** — signed, redacted receipts containing inputs, sources, policy checks, approvals, tool calls, outcomes, uncertainty and reversibility. Audit logs alone are not customer-understandable evidence.
9. **Feature flags and controlled experimentation** — tenant/cohort flags, holdouts, kill switches, experiment metrics, eligibility and automatic rollback. Prompt/model/tool changes need controlled comparison.
10. **Backup restoration verification** — automated restore drills into isolated environments, referential-integrity checks, workflow replay, RPO/RTO measurement and unrestorable-object reporting. A backup is not recovery until restored.

### 10 nice-to-have additions

1. **Agent performance scorecards** — outcome quality, groundedness, intervention rate, tool-error rate, latency, cost, drift and business KPI impact per agent.
2. **Synthetic end-to-end monitoring** — scheduled synthetic users through login, retrieval, handoff, approval, tool execution, notification and export across representative tenants.
3. **Trace review and incident learning workspace** — label traces, compare expected vs actual, assign root causes, redact examples, promote incidents into regression tests.
4. **Knowledge-base lifecycle and ownership** — owners, freshness SLAs, review queues, supersession, archival, approval states, authoritative-passage links.
5. **Semantic search, deduplication and entity resolution** — lexical+vector search, duplicate detection, canonicalization, aliases, conflict display, merge/split.
6. **Customer trust center and self-service controls** — data flows, model routing, subprocessors, retention, export/delete, incident notices and trust documentation.
7. **Vendor/model supply-chain risk registry** — versions, regions, data handling, licenses, SBOMs, security posture, concentration risk, exit plans and change notices.
8. **Deployment rings and progressive release** — internal, synthetic, pilot, low-risk and general rings with promotion gates, opt-outs, region-aware scheduling and rollback.
9. **Disaster exercises and operational game days** — provider outage, corrupted index, compromised agent, lost keys, region loss, notification failure and partial restore.
10. **Local-first synchronization and conflict resolution** — append-only local queues, encrypted caches, causal metadata, merge screens and safe replay after reconnect.
