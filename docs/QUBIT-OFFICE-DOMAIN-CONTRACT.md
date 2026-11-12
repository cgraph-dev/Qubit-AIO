# Qubit Office — canonical domain and projection contract

**Status:** Phase 0 design contract. No schema is implemented yet.
**Rule:** one field has one authoritative owner. Every other surface is a rebuildable projection.

## 1. Authority

| Domain | Authority | Projection surfaces |
|---|---|---|
| Business, department, room, agent, session, run, task, workspace, checkpoint, artifact, message, approval, ledger, policy, capability call, audit, FX event | Business OS event log/SQLite (later Postgres) | Office, tower, maps, panels, Obsidian |
| Obsidian notes/graph | Derived projection | Human graph/search |
| OpenViking memory | Qubit memory adapter over authenticated namespaces | Retrieval, Memory Room |
| CharacterProfile | Business OS, versioned | Habblaud character state, LimeZu portrait projection |
| World/map layout | Versioned map schema | Web renderer, optional Godot |
| FX | Event consumer over authoritative events | Animations/particles |

Business truth must be rebuildable with the browser closed and all rooms dormant.

## 2. Core records

### Owner

- `owner_id`, human identity, auth subject, display name, status, created_at, retired_at.
- Authority: identity provider + owner record.
- Invariant: owner is a human; agents are separate principals.

### Tenant

- `tenant_id`, owner_id, name, status, data_residency, retention_policy, created_at.
- Authority: tenant registry.
- Invariant: every business record carries tenant_id; no cross-tenant reads by default.

### LegalEntity

- `legal_entity_id`, tenant_id, name, jurisdiction, registration/tax IDs, currency, status.
- Authority: entity registry.
- Invariant: personal and future company books never mix.

### Business

- `business_id`, tenant_id, legal_entity_id, name, slug, status, currency, metadata.
- Authority: Business OS.
- Invariant: business is not derived from room or seat presence.

### Department

- `department_id`, tenant_id, business_ids, name, slug, wing/floor assignment, status.
- Authority: Business OS/org registry.
- Invariant: department is an org unit, not a security boundary.

### Room

- `room_id`, department_id, name, archetype, capacity, privacy_class, status,
  capability_manifest_id, memory_scope_id, map_cell_ids.
- Authority: map/room registry.
- Invariant: every room maps to at least one workflow/capability; a furnished dormant room is
  allowed only when its business records still exist.

### Cell / Desk / Seat

- `cell_id`, room_id, origin, cols=16, rows=12, desk IDs; `desk_id`, `seat_id`,
  `roomId:cellId:localSeatId`.
- Authority: map/seat registry.
- Invariant: never stretch a cell; a wider room is a cell cluster.

### Agent

- `agent_id`, owner_id, tenant_id, business_ids, department_id, room_id, desk_id, role_id,
  security_principal_id, display_name, status, policy_revision, autonomy_ceiling.
- Authority: Business OS agent registry.
- Invariant: desk/room/role are presentation/org metadata, never authorization.

### Session

- `session_id`, agent_id, owner_id, participants, visibility, default_memory_scopes,
  default_workspace_id, status, last_checkpoint_id, current_policy_revision.
- Authority: session store.
- Invariant: durable owner↔agent lane; archive creates a handoff if work remains.

### Run / Turn

- `run_id`, session_id, task_id, initiator, parent_run_id, model/provider, prompt_revision,
  capability_snapshot_id, budget_snapshot, lease_id, status, timestamps.
- Statuses: queued, leased, executing, waiting, approval, checkpointed, completed, failed,
  cancelled, unknown.
- Invariant: exactly one terminal state; unknown requires reconciliation.

### Task

- `task_id`, objective, acceptance_criteria, priority, owner, assignee, dependencies,
  required_capabilities, data_classification, budget, deadline, status, artifact_ids.
- Invariant: task movement is append-only; handoff is explicit.

### Workspace

- `workspace_id`, owner_scope, task_id, session_id, repository_ref, worktree_ref, storage_class,
  access_policy, current_revision, artifact_manifest, retention_policy.
- Invariant: task-pinned; immutable artifacts; single writer for mutable worktrees.

### Checkpoint

- `checkpoint_id`, run_id, sequence, state_summary, pending_actions, tool_receipts,
  artifact_ids, memory_writes, approvals, workspace_revision, policy_revision, safe_resume_point.
- Invariant: checkpoints are recovery points, not chat summaries.

### Artifact

- `artifact_id`, task_id, run_id, kind, sha256, bytes, storage_ref, provenance, access_policy,
  immutable/versioned.
- Invariant: an agent cannot claim completion without an artifact or provider receipt.

### Message / Event

- `event_id`, conversation_id, sender, recipients, correlation_id, causation_id, type,
  payload_ref, visibility, classification, idempotency_key, delivery_state, timestamp.
- Invariant: append-only; ACL checked per recipient; replayable.

### Handoff

- `handoff_id`, task_id, from_agent, to_agent, reason, checkpoint_id, artifact_ids,
  unresolved_questions, required_capabilities, acceptance_deadline, status.
- Invariant: recipient acceptance required; source becomes read-only.

### Approval

- `approval_id`, run_id, action, canonical_payload_hash, evidence, risk, cost, reversibility,
  policy_revision, approver_scope, expiry, status, resume_token.
- Invariant: approval is durable workflow state, not a chat message; any payload change invalidates.

### LedgerEntry / ProviderReceipt

- `entry_id`, tenant/business/entity, kind, amount minor units, currency, provider_request_id,
  idempotency_key, status (pending/committed/unknown), artifact/receipt refs.
- Invariant: append-only; reconciliation resolves unknown; narrative never equals financial truth.

### Capability / Grant / Call

- Capability: id, category, resource/action, risk, required scopes, approval policy,
  budget/rate, health, version.
- Grant: subject, capability, resource, action, data_class, environment, expiry, approval.
- Call: run/task, capability, arguments hash, policy decision, approval, result/receipt, cost.
- Invariant: deny-by-default; no wildcard; gateway is the only execution path.

### PolicyRevision / Budget / Lease

- PolicyRevision: version, grants, denials, model/tool/server/skill revisions, effective_at.
- Budget: subject, period, limit, reservation, spend, reset.
- Lease: resource, holder, expiry, heartbeat, fencing token.
- Invariant: capability snapshot pinned per run; lease loss stops execution.

### MemoryRecord

- `memory_id`, scope (agent/session/task/department/office/tenant), source, provenance,
  confidence, visibility, retention, created/updated, supersedes.
- Invariant: event log authoritative; Obsidian is projection; OpenViking via adapter;
  deletion/correction are first-class.

### FXEvent

- `fx_event_id`, event_id, room/agent, animation, seed, priority, cancellation, reduced_motion.
- Invariant: FX consumes authoritative events; FX never creates business truth.

### AuditEvent

- `audit_id`, actor, tenant, agent, run, policy revision, action, target, decision,
  before/after hashes, timestamp, chain hash.
- Invariant: tamper-evident; restricted deletion; exportable.

### MetricContract

- `metric_id`, name, formula, source tables, timezone, attribution window, owner, refresh cadence,
  acceptable staleness, target, anomaly threshold.
- Invariant: every KPI drills through to source records; derived estimates are labelled.

## 3. Cross-record invariants

1. Every record carries tenant/business/entity scope where applicable.
2. Every agent action has an audit chain from owner → session → run → task → capability call →
   artifact/receipt.
3. No external side effect is committed outside ToolGateway.
4. No “completed” without artifact, system-of-record row, provider receipt, message ID or ticket.
5. No approval without exact payload hash, evidence and expiry.
6. No business truth in the renderer, Obsidian note text or vector memory alone.
7. No paid art or raw secret leaves its approved local boundary.
8. Every long-running record has a retention and deletion policy.
9. Every unknown outcome has a reconciliation owner and deadline.
10. Every projection can be rebuilt from authoritative records.
