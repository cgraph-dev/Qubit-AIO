# Qubit Business OS foundation — control plane skeleton

**Status:** foundation only. This document describes what the code in
`voicebridge/business/` actually does today and what it deliberately does not.
It is **not** `VERIFIED`, not `COMPLETE`, and not a release claim.

Source of the requirement: `docs/QUBIT-OFFICE-MASTER-PLAN.md` §18.5 (Business
OS), §25 (first-wave execution contract, lane D), and the review rules in §22
(licence/provenance) and §23 (the world is a projection; the Business OS owns
the control plane and integrates systems of record).

## 1. Scope of this lane

Lane D builds a **durable control-plane skeleton**, not an ERP, CRM, accounting
system, payment integration or UI.

Real today:

- Typed business objects with stable ULID IDs and explicit tenant/business
  scope: `Business`, `Contact`, `Task`, `JobRun`, `Approval`, `LedgerEntry`,
  `Artifact`, `AuditEvent`, `ToolInvocation`, `JobEvent`, `Tenant`.
- One SQLite database with forward-only migrations, WAL, `synchronous=FULL`,
  foreign keys and `BEGIN IMMEDIATE` writes.
- Database-enforced append-only ledger, job event log and audit log.
- Idempotency keys with request fingerprints: a replay is one effect, and a
  reused key with a different body is a typed conflict.
- A job state machine with leases, restart recovery and an explicit `unknown`
  outcome.
- An approval state machine that pauses and resumes/terminates a linked job.
- A tool-gateway contract that validates actor, tenant, policy, autonomy,
  approval, budget and idempotency, supports dry-run, records a redacted result,
  and ships **no** external executor.
- Tenant isolation on every read and write path.

Deliberately deferred:

- Temporal/queues, timers, dead-letter queues, leases across multiple hosts.
- Postgres, replication and backup/restore drills.
- Stripe, CRM, email, accounting, document or browser adapters.
- Dashboard/voice surfaces, approval cards, operator notifications.
- Real payouts, emails, messages or any external side effect.
- Actor authentication/authorisation beyond the passed `actor` string.
- Encryption at rest and an external secret store.

## 2. Package layout

| Module | Owns |
|---|---|
| `voicebridge/business/errors.py` | Typed refusals (`BUSINESS_*` codes) |
| `voicebridge/business/ids.py` | ULID-shaped stable IDs, canonical JSON, fingerprints |
| `voicebridge/business/models.py` | Immutable typed records decoded from rows |
| `voicebridge/business/schema.py` | Schema v1, migrations, append-only triggers |
| `voicebridge/business/store.py` | Connections, transactions, tenants, businesses, contacts, tasks, artifacts, audit, budgets |
| `voicebridge/business/jobs.py` | Job creation, claims, leases, transitions, unknown/recovery |
| `voicebridge/business/approvals.py` | Request/decide/expire approvals |
| `voicebridge/business/ledger.py` | Append-only ledger and derived totals |
| `voicebridge/business/tools.py` | `Policy`, `ToolGateway`, `ToolRequest`, dry-run, redaction |

The database lives at `<state_dir>/business/business.db` when opened with
`BusinessStore.from_config(cfg)`; tests and embeddings can pass an explicit
path to `BusinessStore(path)`.

## 3. Schema (v1)

All tables are prefixed `bos_`. Money is integer minor units; there is no float
column and no stored rollup on the ledger.

| Table | Purpose |
|---|---|
| `bos_tenant` | Tenant scope; unique name |
| `bos_business` | Business under a tenant; unique `(tenant, slug)` |
| `bos_contact` | Customer/lead/vendor/partner; unique email or external ref per business |
| `bos_task` | Lightweight human/agent task |
| `bos_job` | Durable work item; unique `(tenant, idempotency_key)` |
| `bos_job_event` | Per-job append-only event log; unique `(job, seq)` |
| `bos_approval` | Human gate; unique `(tenant, idempotency_key)` |
| `bos_ledger_entry` | Append-only money record; unique `(tenant, idempotency_key)` |
| `bos_artifact` | Receipt/proof body with sha256 + size |
| `bos_audit_event` | Append-only tenant audit trail |
| `bos_budget` | Per-business, per-currency, per-period spend cap |
| `bos_tool_invocation` | Tool-gateway reservation/result; unique `(tenant, key, dry_run)` |

Append-only is enforced by SQLite, not by convention:

```sql
CREATE TRIGGER bos_ledger_entry_no_update
BEFORE UPDATE ON bos_ledger_entry
BEGIN SELECT RAISE(ABORT, 'bos_ledger_entry is append-only'); END;
```

The same pair exists for `bos_job_event`, `bos_audit_event` and the ledger
delete path. A correction is a new compensating row. Tool invocations are not
append-only because a reservation is completed from `pending` to
`success`/`failed`/`dry_run`; the immutable record of the call is the audit
event.

## 4. State machines

### Job

```
created ──> queued ──> running ──> completed
   │           │          │ ├────> failed
   │           │          │ ├────> cancelled
   │           │          │ ├────> waiting_approval ──> queued (approved)
   │           │          │ │                        └─> cancelled (rejected/expired)
   │           │          │ └────> unknown ──> completed | failed | cancelled
   │           │          │              └─> queued  (explicit reconciliation only)
   │           └──────────┴──> failed / cancelled
   └──> cancelled
```

- Terminal states (`completed`, `failed`, `cancelled`) have no outgoing edges.
- `unknown` is reached when a worker's lease expires before completion. It is
  never retried automatically; `jobs.requeue_unknown()` is the explicit
  reconciliation decision.
- `jobs.recover()` only converts `running` rows whose lease has expired. A
  valid lease is left alone because another process may still own it.

### Approval

```
pending ──> approved   (linked job waiting_approval -> queued)
        ├─> rejected   (linked job -> cancelled)
        ├─> cancelled  (linked job -> cancelled)
        └─> expired    (linked job -> cancelled)
```

A decided approval cannot be decided again (`BUSINESS_APPROVAL_NOT_PENDING`).
Requesting an approval pauses a `queued`/`running` job and releases its lease.

### Business / task

`draft -> active|paused|archived`, `active -> paused|archived`,
`paused -> active|archived`, `archived` terminal.
`open -> doing|done|cancelled`, `doing -> done|cancelled`, terminal `done` and
`cancelled`.

## 5. Idempotency and IDs

- IDs are `<prefix>_<26-char Crockford base32 ULID>` (`ten_`, `biz_`, `job_`,
  `apr_`, `lgr_`, `tcl_`, `evt_`, `jev_`, `art_`, `con_`, `task_`, `bud_`).
  They sort by creation time and need no coordination.
- Every mutating entry point that can be retried takes an `idempotency_key`.
- The store persists a sha256 **request fingerprint** beside the key. The same
  key with the same body returns the original row (`created=False`); the same
  key with a different body raises `BUSINESS_IDEMPOTENCY_CONFLICT`.
- This is enforced by unique constraints, not by an in-process dictionary.

## 6. Tool gateway contract

`ToolGateway.invoke(...)` is the only door from an agent to an external system.
No module in this package makes an HTTP/socket/provider call.

Order of validation:

1. non-empty `actor`, `tool`, `idempotency_key`, currency and integer cost;
2. the tool is in the policy's `allowed_tools` (`BUSINESS_TOOL_NOT_ALLOWED`);
3. tenant and business exist and belong together (`BUSINESS_NOT_FOUND`);
4. L4/L5 or `approval_required` needs an **approved** approval whose action is
   `tool:<tool>` (`BUSINESS_APPROVAL_REQUIRED`);
5. per-period budget is sufficient (`BUSINESS_BUDGET_EXCEEDED`);
6. an executor exists unless this is a dry run
   (`BUSINESS_TOOL_EXECUTOR_MISSING`);
7. reserve the `(tenant, idempotency_key, dry_run)` row under `BEGIN IMMEDIATE`,
   or replay the terminal row;
8. execute outside the write transaction;
9. record `success`/`failed` with a redacted result and an audit event.

`dry_run=True` records intent and the would-be cost, calls no executor, and
does not consume budget or require an approval. Secrets are redacted before
persistence: sensitive key names (`password`, `secret`, `token`, `api_key`,
`authorization`, `cookie`, `private_key`, `client_secret`, `credential`) and
bearer/provider-key shapes become `***REDACTED***`.

Real integrations must be injected as `executor` callables (see
`Executor = Callable[[ToolRequest], Any]`) and must still be idempotent at the
provider boundary; this gateway prevents duplicate local effects, not duplicate
provider requests after an `unknown` outcome.

The dry-run integration wiring lives in `voicebridge/business/integration_tools.py`.
`IntegrationToolRegistry` is the registered executor: it resolves the injected
adapter for the tool, checks readiness (missing transport/credentials is the
adapter's typed `adapter_unavailable`/`adapter_not_configured` refusal), passes
the request's idempotency key and derived approval token, and returns an
`ExecutorResult`. A dry-run adapter result is recorded as `dry_run`, never as an
execution; a money-moving directive (`_ledger`) is appended once by this gateway
and re-asserted idempotently on replay. `voicebridge/business/etsy_workflow.py`
is the small durable pilot path (draft → owner approval → dry-run publish) built
on `jobs`, `approvals` and this gateway; it refuses publish without a matching,
unexpired approval.

## 7. What is real vs deferred

| Capability | State |
|---|---|
| Typed objects, tenant scope, stable IDs | real |
| SQLite schema + migrations + WAL | real |
| Append-only ledger/event/audit triggers | real |
| Idempotency keys + request fingerprints | real |
| Job state machine + leases + unknown recovery | real |
| Approval pause/resume/reject/expire | real |
| Tool validation, approval gate, budget, dry-run, redaction | real |
| Dry-run Etsy/social/CRM/email executor wiring + Etsy pilot workflow | real |
| Real provider transports/OAuth/credentials, Stripe, accounting | **deferred** |
| Multi-host queue, Temporal, dead-letter, timers | **deferred** |
| Postgres, replication, backup/restore drill | **deferred** |
| UI, voice commands, approval cards | **deferred** |
| Real payments/emails/external writes | **deferred** |
| Actor authentication beyond the `actor` string | **deferred** |
| Encryption at rest / external secret store | **deferred** |

## 8. Next integration steps

1. **Pick the pilot business** and its systems of record (owner decision §26).
2. The dry-run executor wiring for Etsy/social/CRM/email exists
   (`voicebridge/business/integration_tools.py`). The remaining work is a real
   provider transport plus credential store and a provider idempotency key
   (Stripe idempotency key, CRM external ref, SMTP Message-ID), plus a
   reconciliation read for `unknown` jobs.
3. The Etsy pilot workflow calls `jobs.claim` → tool gateway → `jobs.finish`
   (`voicebridge/business/etsy_workflow.py`). Lease heartbeats and
   `jobs.recover()` at process boot are still to wire.
4. Add approval cards and voice commands that call `approvals.request/decide`;
   never let an agent self-approve.
5. Replace the single-file SQLite store with a Postgres-compatible adapter and
   introduce a durable workflow engine (Temporal or equivalent) when more than
   one host runs jobs. Keep the same state names and idempotency keys.
6. Add actor authentication/authorisation, a secret store and encryption at
   rest before any real credential is used.
7. Add restore drills, dead-man's-switch/heartbeat alerting, on-call escalation
   and incident runbooks.
8. Only then enable money/email/CRM tools for the pilot, starting with dry-run
   evidence and capped budgets.

## 9. Verification

Run the focused suite:

```bash
TMPDIR=/tmp PYTHONPATH=. python -m pytest -q tests/test_business_os_foundation.py
```

At the time of writing this reports `13 passed`, covering: typed objects and
scope, config-path resolution, one-effect idempotency, restart → `unknown` → explicit resume, unsafe
job transitions, approval pause/approve/reject/expire, duplicate decision
refusal, ledger append-only enforcement and idempotency, tenant isolation,
tool-gateway validation/dry-run/redaction, budget refusal, missing executor,
and executor failure recording.

Also run:

```bash
python -m py_compile voicebridge/business/*.py
ruff check voicebridge/business tests/test_business_os_foundation.py
git diff --check
```

## 10. Blockers / owner decisions

The skeleton does not need these to be useful, but the pilot does:

- the first business and its legal owner (plan §26.1/§26.2);
- the existing tools of record and who holds their credentials (§26.3);
- spend/refund/email/contract limits and approval thresholds (§26.4);
- hosting and on-call ownership (§26.5);
- whether the online World is in scope (§26.6).

Until those are answered, keep every real tool in `dry_run` and the executor
unset. That is the honest state of this lane.
