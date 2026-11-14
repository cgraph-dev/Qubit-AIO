"""Read-only operator timeline for P4-07 (replayable operator UI).

This module is a **projection**, not a second store and not a second execution
path. It composes the durable records the Business OS already owns into one
causally ordered timeline:

* ``bos_event`` — the append-only aggregate log (``eventlog.list_events``);
* ``bos_job`` and ``bos_job_event`` — the run state machine and its trail;
* ``bos_checkpoint`` — recovery points (``durable.list_checkpoints``);
* ``bos_approval`` — the durable approval journal;
* ``bos_artifact`` — immutable completion evidence;
* ``bos_ledger_entry`` — committed receipts and their cost;
* ``bos_tool_invocation`` — tool calls behind a run;
* ``bos_task`` — the durable work item a run serves.

Truth rules (``docs/QUBIT-OFFICE-DOMAIN-CONTRACT.md`` §3):

* every run status is derived from the durable job row, never from a seat,
  sprite, chat message or this projection;
* ``completed`` is emitted only when an artifact or a provider receipt is
  linked; a job that *claims* completion without evidence is projected as
  ``unknown`` with the claim preserved in ``claimed_status``;
* ``unknown`` stays ``unknown`` until reconciliation; pending work is never
  rendered completed;
* a source that cannot be read is a typed ``unavailable`` state, not ``[]``;
* the projection writes nothing, calls no model/provider and touches no
  network.

Replay mode (``replay=True``) returns the same ordering as a labelled
simulation: every item carries ``replay=True`` and ``effect="stubbed"``. It
performs no real call — replay is never a second execution path.
"""
from __future__ import annotations

import heapq
from typing import Any, Callable, Iterable, Mapping

from . import approvals as APPROVALS
from . import durable as DURABLE
from . import eventlog as EVENTLOG
from . import jobs as JOBS
from . import ledger as LEDGER
from . import models
from . import runs as RUNS
from .errors import BusinessError
from .store import BusinessStore
from .util import json_dumps, now_iso, redact

SCHEMA = "qubit-office-operator-timeline/1"
DEFAULT_LIMIT = 100
MAX_LIMIT = 500

#: Canonical causal stages: a caused record sorts after its cause when two
#: records share a timestamp. The order follows the contract chain
#: run -> task -> tool -> approval -> artifact/receipt -> reconciliation.
STAGES = ("run", "task", "tool", "event", "approval", "checkpoint", "artifact",
          "receipt", "reconciliation")
_STAGE_RANK = {kind: index for index, kind in enumerate(STAGES)}

KINDS = (
    "run", "task", "tool", "event", "approval", "checkpoint", "artifact",
    "receipt", "reconciliation",
)

#: Completion claims that require an artifact or provider receipt before the
#: projection may call a job completed.
RECEIPT_KEYS = (
    "receipt_id",
    "provider_request_id",
    "provider_receipt",
    "external_id",
    "message_id",
    "ticket_id",
    "order_id",
    "transaction_id",
)

PENDING_STATUSES = frozenset(
    {"queued", "leased", "waiting", "approval", "checkpointed", "pending"}
)
UNKNOWN_STATUSES = frozenset({"unknown"})
COMPLETED_STATUSES = frozenset({"completed"})


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _bounded_limit(value: Any) -> int:
    if value is None or value == "":
        return DEFAULT_LIMIT
    try:
        limit = int(value)
    except (TypeError, ValueError) as exc:
        raise BusinessError(
            "BUSINESS_BAD_INPUT", "limit must be an integer"
        ) from exc
    if limit < 1 or limit > MAX_LIMIT:
        raise BusinessError(
            "BUSINESS_BAD_INPUT",
            f"limit must be between 1 and {MAX_LIMIT}",
        )
    return limit


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _bounded_detail(value: Any, limit: int = 1200) -> dict[str, Any]:
    """Return a redacted mapping, truncating instead of serving a huge blob."""
    safe = redact(_mapping(value))
    text = json_dumps(safe)
    if len(text) <= limit:
        return safe
    return {"_truncated": True, "keys": sorted(safe)[:20]}


def _tokens(value: Any) -> dict[str, int] | None:
    """Extract token usage from a state/summary mapping, or ``None``.

    Only fields that are actually present are summed; a record without usage
    stays ``None`` and is rendered unknown, never zero-invented.
    """
    scopes: list[Mapping[str, Any]] = []
    if isinstance(value, Mapping):
        scopes.append(value)
        for key in ("usage", "token_usage", "metrics"):
            nested = value.get(key)
            if isinstance(nested, Mapping):
                scopes.append(nested)
    prompt = completion = total = 0
    found = False
    for scope in scopes:
        p = _int(scope.get("prompt_tokens", scope.get("input_tokens")))
        c = _int(scope.get("completion_tokens", scope.get("output_tokens")))
        t = _int(scope.get("total_tokens"))
        if p or c or t:
            found = True
            prompt += p
            completion += c
            total += t or (p + c)
    if not found:
        return None
    if not total:
        total = prompt + completion
    return {"prompt": prompt, "completion": completion, "total": total}


def _source(
    fn: Callable[[], list[Any]], label: str
) -> tuple[list[Any], dict[str, Any]]:
    """Run one source read; an error is a typed unavailable state, not []."""
    try:
        rows = fn()
    except Exception as exc:  # noqa: BLE001 - each source degrades alone
        code = getattr(exc, "code", type(exc).__name__)
        detail = getattr(exc, "detail", str(exc))
        return [], {
            "state": "unavailable",
            "count": 0,
            "code": str(code)[:120],
            "reason": str(detail)[:300],
        }
    return rows, {
        "state": "ok" if rows else "empty",
        "count": len(rows),
        "code": None,
        "reason": None,
    }


def _at(item: Mapping[str, Any]) -> str:
    return _clean(item.get("at"))


def _stage(kind: str) -> int:
    return _STAGE_RANK.get(kind, len(STAGES))


def _causal_sort(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Stable chronological sort, with causation always before its effect.

    A pure timestamp sort can invert a cause/effect pair that shares a clock
    tick (common in one transaction). The DAG pass keeps the timestamp order
    unless it contradicts an explicit ``causation_id``. A cycle (malformed
    history) falls back to the plain sort instead of dropping records.
    """
    ordered = sorted(
        items,
        key=lambda it: (
            _at(it),
            _stage(it["kind"]),
            _int(it.get("sequence")),
            it["item_id"],
        ),
    )
    if not ordered:
        return ordered
    by_id = {it["item_id"]: it for it in ordered}
    position = {it["item_id"]: index for index, it in enumerate(ordered)}
    children: dict[str, list[str]] = {}
    indegree = {it["item_id"]: 0 for it in ordered}
    for item in ordered:
        cause = _clean(item.get("causation_id"))
        if cause and cause in by_id and cause != item["item_id"]:
            children.setdefault(cause, []).append(item["item_id"])
            indegree[item["item_id"]] += 1
    heap = [
        (position[item_id], item_id)
        for item_id, degree in indegree.items() if degree == 0
    ]
    heapq.heapify(heap)
    result: list[dict[str, Any]] = []
    while heap:
        _, item_id = heapq.heappop(heap)
        result.append(by_id[item_id])
        for child in children.get(item_id, ()):
            indegree[child] -= 1
            if indegree[child] == 0:
                heapq.heappush(heap, (position[child], child))
    if len(result) != len(ordered):  # a cycle; keep every record
        return ordered
    return result


def _ref(kind: str, ref_id: str) -> dict[str, Any]:
    return {"kind": kind, "id": ref_id, "item_id": f"{kind}:{ref_id}"}


def _receipt_in_output(output: Any) -> bool:
    if not isinstance(output, Mapping):
        return False
    return any(_clean(output.get(key)) for key in RECEIPT_KEYS)


def _status_truth(
    claimed: str, *, evidence: bool
) -> tuple[str, bool, bool]:
    """Map a claimed status to (verified status, unknown, completed)."""
    status = _clean(claimed).lower() or "unknown"
    if status in COMPLETED_STATUSES:
        if evidence:
            return "completed", False, True
        # A completion claim without an artifact/receipt is not truth.
        return "unknown", True, False
    if status in UNKNOWN_STATUSES:
        return "unknown", True, False
    return status, False, False


def _item(
    *,
    item_id: str,
    kind: str,
    at: str,
    status: str,
    title: str,
    summary: str,
    actor: str,
    source: str,
    run_id: str | None = None,
    task_id: str | None = None,
    agent_id: str | None = None,
    session_id: str | None = None,
    approval_id: str | None = None,
    artifact_id: str | None = None,
    ledger_id: str | None = None,
    checkpoint_id: str | None = None,
    correlation_id: str | None = None,
    causation_id: str | None = None,
    claimed_status: str | None = None,
    evidence_refs: Iterable[Mapping[str, Any]] = (),
    cost: Mapping[str, Any] | None = None,
    tokens: Mapping[str, Any] | None = None,
    detail: Mapping[str, Any] | None = None,
    sequence: int | None = None,
    reason: str | None = None,
    evidence: bool | None = None,
) -> dict[str, Any]:
    refs = [dict(ref) for ref in evidence_refs]
    has_evidence = bool(refs) if evidence is None else bool(evidence)
    verified, unknown, completed = _status_truth(status, evidence=has_evidence)
    pending = verified in PENDING_STATUSES or (
        kind == "reconciliation" and unknown
    )
    return {
        "item_id": item_id,
        "kind": kind,
        "at": at,
        "status": verified,
        "claimed_status": claimed_status or _clean(status) or None,
        "title": title,
        "summary": summary,
        "actor": actor or "system",
        "source": source,
        "run_id": run_id or None,
        "task_id": task_id or None,
        "agent_id": agent_id or None,
        "session_id": session_id or None,
        "approval_id": approval_id or None,
        "artifact_id": artifact_id or None,
        "ledger_id": ledger_id or None,
        "checkpoint_id": checkpoint_id or None,
        "correlation_id": correlation_id or None,
        "causation_id": causation_id or None,
        "sequence": int(sequence) if sequence is not None else None,
        "evidence": has_evidence,
        "evidence_refs": refs,
        "completed": completed,
        "unknown": unknown,
        "pending": pending,
        "reason": reason,
        "cost": dict(cost) if cost else None,
        "tokens": dict(tokens) if tokens else None,
        "detail": dict(detail) if detail else {},
    }


def _event_suffix(event_type: str) -> str:
    return _clean(event_type).rsplit(".", 1)[-1].lower()


def _event_status(event_type: str, evidence: bool) -> str:
    suffix = _event_suffix(event_type)
    if suffix in ("failed", "failure", "error"):
        return "failed"
    if suffix in ("cancelled", "canceled"):
        return "cancelled"
    if suffix in ("unknown",):
        return "unknown"
    if suffix in ("completed", "succeeded", "success", "done", "finished"):
        return "completed" if evidence else "unknown"
    if suffix in ("approved",):
        return "approved"
    if suffix in ("rejected",):
        return "rejected"
    if suffix in ("expired",):
        return "expired"
    if suffix in ("pending", "requested", "waiting", "approval"):
        return "pending"
    return "recorded"


def _event_links(payload: Any, aggregate_id: str) -> dict[str, str]:
    body = _mapping(payload)
    links: dict[str, str] = {}
    for key in (
        "run_id", "job_id", "task_id", "agent_id", "session_id",
        "approval_id", "artifact_id", "ledger_id", "checkpoint_id",
    ):
        value = _clean(body.get(key))
        if value:
            links[key] = value
    if "run_id" not in links and "job_id" in links:
        links["run_id"] = links["job_id"]
    # The aggregate id names some record; keep it only as a fallback run link
    # when the type is job-scoped and the payload did not name one.
    if "run_id" not in links and _clean(aggregate_id).startswith("job_"):
        links["run_id"] = _clean(aggregate_id)
    return links


def operator_timeline(
    store: BusinessStore,
    tenant_id: str,
    *,
    run: Any = None,
    task: Any = None,
    agent: Any = None,
    session: Any = None,
    limit: Any = None,
    replay: Any = False,
    now: str | None = None,
) -> dict[str, Any]:
    """Compose the causal operator timeline for one tenant.

    Raises the store's tenant refusal for an unknown/cross-tenant id. Every
    other source degrades independently to a typed ``unavailable`` state.
    """
    store.require_tenant(tenant_id)
    bounded = _bounded_limit(limit)
    replay_mode = _as_bool(replay)
    run_filter = _clean(run)
    task_filter = _clean(task)
    agent_filter = _clean(agent)
    session_filter = _clean(session)
    has_filter = any((run_filter, task_filter, agent_filter, session_filter))

    events, events_state = _source(
        lambda: EVENTLOG.list_events(store, tenant_id, limit=MAX_LIMIT),
        "events",
    )
    jobs, jobs_state = _source(
        lambda: JOBS.list_jobs(store, tenant_id), "jobs",
    )
    tasks, tasks_state = _source(
        lambda: store.list_tasks(tenant_id), "tasks",
    )
    approvals, approvals_state = _source(
        lambda: APPROVALS.list_approvals(store, tenant_id), "approvals",
    )
    artifacts, artifacts_state = _source(
        lambda: store.list_artifacts(tenant_id), "artifacts",
    )
    ledger_rows, ledger_state = _source(
        lambda: LEDGER.list_entries(store, tenant_id, limit=MAX_LIMIT),
        "ledger",
    )
    tool_rows, tools_state = _source(
        lambda: _list_tool_invocations(store, tenant_id, MAX_LIMIT),
        "tools",
    )

    # Jobs are the run authority. A run filter narrows to one job; a task
    # filter resolves through the job input, which is the only durable link.
    selected_jobs = _select_jobs(jobs, run_filter, task_filter)
    checkpoints: list[Any] = []
    checkpoint_rows: dict[str, list[Any]] = {}
    checkpoint_once: dict[str, Any] = {
        "state": "empty", "count": 0, "code": None, "reason": None}
    if selected_jobs:
        for job in selected_jobs:
            rows, state = _source(
                lambda j=job: DURABLE.list_checkpoints(store, tenant_id, j.job_id),
                "checkpoints",
            )
            if state["state"] == "unavailable":
                checkpoint_once = state
            checkpoint_rows[job.job_id] = rows
            checkpoints.extend(rows)
    if selected_jobs:
        checkpoint_state = {
            "state": "ok" if checkpoints else "empty",
            "count": len(checkpoints),
            "code": checkpoint_once.get("code") if not checkpoints else None,
            "reason": checkpoint_once.get("reason") if not checkpoints else None,
        }
    else:
        checkpoint_state = checkpoint_once

    # Per-run evidence: an artifact, a ledger receipt, or a receipt-shaped job
    # output. This is the ONLY route to a `completed` projection.
    artifacts_by_job: dict[str, list[Any]] = {}
    for artifact in artifacts:
        if artifact.job_id:
            artifacts_by_job.setdefault(artifact.job_id, []).append(artifact)
    ledger_by_job: dict[str, list[Any]] = {}
    for entry in ledger_rows:
        if entry.job_id:
            ledger_by_job.setdefault(entry.job_id, []).append(entry)
    job_events_by_job: dict[str, list[Any]] = {}
    job_events_state: dict[str, Any] = {
        "state": "empty", "count": 0, "code": None, "reason": None}
    for job in selected_jobs:
        rows, state = _source(
            lambda j=job: JOBS.list_events(store, tenant_id, j.job_id),
            "job-events",
        )
        if state["state"] == "unavailable":
            job_events_state = state
        job_events_by_job[job.job_id] = rows
    if selected_jobs and job_events_state["state"] != "unavailable":
        total_events = sum(len(rows) for rows in job_events_by_job.values())
        job_events_state = {
            "state": "ok" if total_events else "empty",
            "count": total_events,
            "code": None,
            "reason": None,
        }

    evidence_refs: dict[str, list[dict[str, Any]]] = {}
    for job in selected_jobs:
        refs: list[dict[str, Any]] = []
        for artifact in artifacts_by_job.get(job.job_id, []):
            refs.append(_ref("artifact", artifact.artifact_id))
        for entry in ledger_by_job.get(job.job_id, []):
            refs.append(_ref("receipt", entry.entry_id))
        if _receipt_in_output(job.output):
            refs.append({"kind": "receipt", "id": f"{job.job_id}:output",
                         "item_id": f"run:{job.job_id}"})
        evidence_refs[job.job_id] = refs

    session_by_task = _sessions_for_tasks(store, tenant_id, tasks)

    items: list[dict[str, Any]] = []

    # -- tasks -----------------------------------------------------------
    for task_row in tasks:
        task_id = task_row.task_id
        task_jobs = {
            job.job_id for job in selected_jobs
            if _clean(_mapping(job.input).get("task_id")) == task_id
        }
        tasks_artifacts = [
            artifact for artifact in artifacts if artifact.job_id in task_jobs
        ]
        tasks_receipts = [
            entry for entry in ledger_rows if entry.job_id in task_jobs
        ]
        refs = [_ref("artifact", a.artifact_id) for a in tasks_artifacts]
        refs += [_ref("receipt", e.entry_id) for e in tasks_receipts]
        claimed = _clean(task_row.status)
        verified, _unknown, _completed = _status_truth(
            claimed, evidence=bool(refs)
        )
        if claimed == "done" and not refs:
            # A workflow "done" without a linked artifact is NOT completion.
            verified = "done-unverified"
        reason = None
        if claimed == "done" and not refs:
            reason = ("the task is marked done but no artifact or receipt is"
                      " linked; completion is unverified")
        items.append(_item(
            item_id=f"task:{task_id}",
            kind="task",
            at=_clean(task_row.updated_at or task_row.created_at),
            status=verified,
            claimed_status=claimed,
            title=_clean(task_row.title) or task_id,
            summary=f"{claimed} task · {task_id}",
            actor=_clean(task_row.assigned_to) or "system",
            source="bos_task",
            task_id=task_id,
            agent_id=_clean(task_row.assigned_to) or None,
            session_id=session_by_task.get(task_id),
            evidence_refs=refs,
            reason=reason,
            sequence=0,
        ))

    # -- runs ------------------------------------------------------------
    for job in selected_jobs:
        refs = evidence_refs.get(job.job_id, [])
        claimed = RUNS.run_status_for_job(job)
        verified, _unknown, _completed = _status_truth(
            claimed, evidence=bool(refs)
        )
        if claimed == "unknown":
            verified = "unknown"
        tokens = _job_tokens(job, job_events_by_job.get(job.job_id, []),
                             checkpoint_rows.get(job.job_id, []))
        reason = None
        if claimed == "completed" and not refs:
            reason = ("the job reports completed but no artifact or provider"
                      " receipt is linked; outcome stays unknown")
        input_map = _mapping(job.input)
        items.append(_item(
            item_id=f"run:{job.job_id}",
            kind="run",
            at=_clean(job.finished_at or job.updated_at or job.created_at),
            status=verified,
            claimed_status=claimed,
            title=f"Run {job.job_id}",
            summary=f"{job.kind} · {claimed}",
            actor=_clean(job.lease_owner) or "system",
            source="bos_job",
            run_id=job.job_id,
            task_id=_clean(input_map.get("task_id")) or None,
            agent_id=_clean(input_map.get("agent_id")) or None,
            session_id=_clean(input_map.get("session_id")) or None,
            evidence_refs=refs,
            tokens=tokens,
            reason=reason,
            sequence=_int(job.attempt),
        ))

    # -- job events (the low-level run trail) ----------------------------
    for job in selected_jobs:
        refs = evidence_refs.get(job.job_id, [])
        for job_event in job_events_by_job.get(job.job_id, []):
            detail = _mapping(job_event.detail)
            kind_hint = _clean(job_event.kind).rsplit(".", 1)[-1].lower()
            status = {
                "failed": "failed",
                "unknown": "unknown",
                "completed": "completed" if refs else "unknown",
                "cancelled": "cancelled",
                "created": "queued",
                "queued": "queued",
                "claimed": "executing",
                "running": "executing",
                "finished": "completed" if refs else "unknown",
            }.get(kind_hint, "recorded")
            items.append(_item(
                item_id=f"event:{job_event.event_id}",
                kind="event",
                at=_clean(job_event.created_at),
                status=status,
                title=_clean(job_event.kind) or "job event",
                summary=f"run {job.job_id} · seq {job_event.seq}",
                actor=_clean(job_event.actor) or "system",
                source="bos_job_event",
                run_id=job.job_id,
                evidence_refs=refs if kind_hint in (
                    "completed", "finished", "succeeded") else (),
                detail=_bounded_detail(detail),
                tokens=_tokens(detail),
                sequence=_int(job_event.seq),
            ))

    # -- aggregate events ------------------------------------------------
    for event in events:
        links = _event_links(event.payload, event.aggregate_id)
        run_id = links.get("run_id")
        refs = evidence_refs.get(run_id, []) if run_id else []
        status = _event_status(event.type, bool(refs))
        items.append(_item(
            item_id=f"event:{event.event_id}",
            kind="event",
            at=_clean(event.occurred_at),
            status=status,
            title=_clean(event.type) or "event",
            summary=f"{event.actor} · {event.aggregate_id}",
            actor=_clean(event.actor) or "system",
            source="bos_event",
            run_id=run_id,
            task_id=links.get("task_id"),
            agent_id=links.get("agent_id"),
            session_id=links.get("session_id"),
            approval_id=links.get("approval_id"),
            artifact_id=links.get("artifact_id"),
            correlation_id=_clean(event.correlation_id) or None,
            causation_id=_clean(event.causation_id) or None,
            evidence_refs=refs if status == "completed" else (),
            detail=_bounded_detail(event.payload),
            tokens=_tokens(event.payload),
            sequence=_int(event.sequence),
        ))

    # -- approvals -------------------------------------------------------
    for approval in approvals:
        refs: list[dict[str, Any]] = []
        if approval.job_id:
            refs = evidence_refs.get(approval.job_id, [])
        items.append(_item(
            item_id=f"approval:{approval.approval_id}",
            kind="approval",
            at=_clean(approval.decided_at or approval.created_at),
            status=_clean(approval.status) or "pending",
            title=_clean(approval.action) or "approval",
            summary=f"{approval.requested_by} · {approval.status}",
            actor=_clean(approval.decided_by or approval.requested_by)
            or "system",
            source="bos_approval",
            run_id=approval.job_id,
            agent_id=_clean(approval.requested_by) or None,
            approval_id=approval.approval_id,
            evidence_refs=refs if approval.status == "approved" else (),
            detail=_bounded_detail(approval.payload),
            sequence=0,
        ))

    # -- artifacts (completion evidence) ---------------------------------
    for artifact in artifacts:
        items.append(_item(
            item_id=f"artifact:{artifact.artifact_id}",
            kind="artifact",
            at=_clean(artifact.created_at),
            status="completed",
            title=_clean(artifact.name) or artifact.artifact_id,
            summary=f"{artifact.kind} · {artifact.size_bytes} bytes",
            actor="system",
            source="bos_artifact",
            run_id=artifact.job_id,
            artifact_id=artifact.artifact_id,
            evidence=True,
            detail={"sha256": artifact.sha256,
                    "media_type": artifact.media_type,
                    "kind": artifact.kind, "bytes": artifact.size_bytes},
            sequence=0,
        ))

    # -- receipts / ledger cost ------------------------------------------
    for entry in ledger_rows:
        items.append(_item(
            item_id=f"receipt:{entry.entry_id}",
            kind="receipt",
            at=_clean(entry.created_at),
            status="committed",
            title=f"Ledger {entry.direction} {entry.amount_minor}",
            summary=f"{entry.account} · {entry.amount_minor} {entry.currency}",
            actor="system",
            source="bos_ledger_entry",
            run_id=entry.job_id,
            ledger_id=entry.entry_id,
            evidence=True,
            cost={"amount_minor": int(entry.amount_minor),
                  "currency": entry.currency,
                  "direction": entry.direction},
            detail={"memo": entry.memo[:300]},
            sequence=0,
        ))

    # -- checkpoints -----------------------------------------------------
    for checkpoint in checkpoints:
        refs = [
            _ref("artifact", artifact_id)
            for artifact_id in (checkpoint.artifact_ids or [])
            if isinstance(artifact_id, str)
        ]
        items.append(_item(
            item_id=f"checkpoint:{checkpoint.checkpoint_id}",
            kind="checkpoint",
            at=_clean(checkpoint.created_at),
            status="checkpointed",
            title=f"Checkpoint {checkpoint.sequence}",
            summary=f"run {checkpoint.run_id} · seq {checkpoint.sequence}",
            actor="system",
            source="bos_checkpoint",
            run_id=checkpoint.run_id,
            checkpoint_id=checkpoint.checkpoint_id,
            evidence_refs=refs,
            tokens=_tokens(checkpoint.state),
            detail=_bounded_detail(checkpoint.state),
            sequence=_int(checkpoint.sequence),
        ))

    # -- tool invocations ------------------------------------------------
    for tool_row in tool_rows:
        # A tool row is a durable call receipt, but its `success` is not an
        # artifact or provider receipt, so it never renders as `completed`.
        status = {
            "success": "success",
            "failed": "failed",
            "dry_run": "simulated",
            "pending": "pending",
        }.get(_clean(tool_row.status), _clean(tool_row.status) or "unknown")
        items.append(_item(
            item_id=f"tool:{tool_row.invocation_id}",
            kind="tool",
            at=_clean(tool_row.created_at),
            status=status,
            title=_clean(tool_row.tool) or "tool call",
            summary=f"{tool_row.actor} · {tool_row.status}"
            + (" · dry run" if tool_row.dry_run else ""),
            actor=_clean(tool_row.actor) or "system",
            source="bos_tool_invocation",
            agent_id=_clean(tool_row.actor) or None,
            evidence=False,
            evidence_refs=(),
            cost={"amount_minor": int(tool_row.cost_minor or 0),
                  "currency": tool_row.currency or "USD"}
            if tool_row.cost_minor else None,
            detail={"status": tool_row.status, "dry_run": tool_row.dry_run,
                    "error_code": tool_row.error_code},
            sequence=0,
        ))

    # -- reconciliation items for unknown outcomes -----------------------
    for job in selected_jobs:
        if RUNS.run_status_for_job(job) != "unknown":
            continue
        items.append(_item(
            item_id=f"reconciliation:{job.job_id}",
            kind="reconciliation",
            at=_clean(job.updated_at or job.created_at),
            status="unknown",
            title=f"Reconcile {job.job_id}",
            summary="outcome unknown; reconciliation required before retry",
            actor="operator",
            source="bos_job",
            run_id=job.job_id,
            evidence_refs=(),
            reason=("unknown is a terminal-until-reconciled outcome; this"
                    " projection never resolves it"),
            sequence=_int(job.attempt),
        ))

    filtered = [
        item for item in items
        if _matches(
            item,
            run=run_filter,
            task=task_filter,
            agent=agent_filter,
            session=session_filter,
        )
    ]
    ordered = _causal_sort(filtered)
    total_matched = len(ordered)
    page = ordered[:bounded]
    if replay_mode:
        for item in page:
            item["replay"] = True
            item["effect"] = "stubbed"
            item["simulated"] = True
    else:
        for item in page:
            item["replay"] = False
            item["effect"] = None
            item["simulated"] = False

    counts = _counts(page)
    counts["matched"] = total_matched
    counts["returned"] = len(page)
    counts["truncated"] = total_matched > len(page)
    cost = _cost(page)
    tokens = _token_total(page)
    unavailable = [
        label for label, state in (
            ("events", events_state), ("jobs", jobs_state),
            ("tasks", tasks_state), ("approvals", approvals_state),
            ("artifacts", artifacts_state), ("ledger", ledger_state),
            ("tools", tools_state), ("checkpoints", checkpoint_state),
            ("job_events", job_events_state),
        ) if state["state"] == "unavailable"
    ]
    if not page:
        state = "empty"
    elif unavailable:
        state = "partial"
    else:
        state = "ok"

    payload: dict[str, Any] = {
        "schema": SCHEMA,
        "tenant_id": tenant_id,
        "generated_at": now or now_iso(),
        "mode": "replay" if replay_mode else "live",
        "state": state,
        "query": {
            "run": run_filter or None,
            "task": task_filter or None,
            "agent": agent_filter or None,
            "session": session_filter or None,
            "limit": bounded,
            "replay": replay_mode,
            "filtered": has_filter,
        },
        "counts": counts,
        "cost": cost,
        "tokens": tokens,
        "items": page,
        "sources": {
            "events": events_state,
            "jobs": jobs_state,
            "tasks": tasks_state,
            "approvals": approvals_state,
            "artifacts": artifacts_state,
            "ledger": ledger_state,
            "tools": tools_state,
            "checkpoints": checkpoint_state,
            "job_events": job_events_state,
        },
        "replay": {
            "enabled": replay_mode,
            "effects": "stubbed" if replay_mode else "none",
            "real_calls": False,
            "label": (
                "Replay (simulated): ordering only, effects stubbed, no real"
                " action performed"
                if replay_mode else "Live projection of durable records"
            ),
            "note": (
                "replay is a viewer; it never executes, resumes, approves or"
                " spends"
            ),
        },
        "unavailable_sources": unavailable,
    }
    return payload


def _list_tool_invocations(
    store: BusinessStore, tenant_id: str, limit: int
) -> list[models.ToolInvocation]:
    with store.read() as conn:
        store._require_tenant(conn, tenant_id)
        rows = conn.execute(
            "SELECT * FROM bos_tool_invocation WHERE tenant_id=?"
            " ORDER BY created_at DESC, invocation_id DESC LIMIT ?",
            (tenant_id, max(1, int(limit))),
        ).fetchall()
    return [models.ToolInvocation.from_row(row) for row in rows]


def _select_jobs(
    jobs: Iterable[Any], run_filter: str, task_filter: str
) -> list[Any]:
    selected: list[Any] = []
    for job in jobs:
        input_map = _mapping(job.input)
        if run_filter and job.job_id != run_filter:
            continue
        if task_filter and _clean(input_map.get("task_id")) != task_filter:
            continue
        selected.append(job)
    return selected


def _sessions_for_tasks(
    store: BusinessStore, tenant_id: str, tasks: Iterable[Any]
) -> dict[str, str]:
    """Map task_id -> session_id without inventing a link.

    Only a durable ``bos_session.task_id`` provides this relationship; a
    failure to read sessions leaves the map empty (the timeline still works).
    """
    try:
        from . import sessions as SESSIONS

        rows = SESSIONS.list_sessions(store, tenant_id)
    except Exception:  # noqa: BLE001 - the link is optional enrichment
        return {}
    mapping: dict[str, str] = {}
    for row in rows:
        task_id = _clean(getattr(row, "task_id", ""))
        if task_id:
            mapping.setdefault(task_id, row.session_id)
    return mapping


def _job_tokens(
    job: Any, job_events: Iterable[Any], checkpoints: Iterable[Any]
) -> dict[str, int] | None:
    prompt = completion = total = 0
    found = False
    for event in job_events:
        usage = _tokens(_mapping(event.detail))
        if usage:
            found = True
            prompt += usage["prompt"]
            completion += usage["completion"]
            total += usage["total"]
    for checkpoint in checkpoints:
        usage = _tokens(checkpoint.state)
        if usage:
            found = True
            prompt += usage["prompt"]
            completion += usage["completion"]
            total += usage["total"]
    if not found:
        return None
    return {"prompt": prompt, "completion": completion,
            "total": total or prompt + completion}


def _matches(
    item: Mapping[str, Any], *, run: str, task: str, agent: str, session: str
) -> bool:
    if run and item.get("run_id") != run:
        return False
    if task and item.get("task_id") != task:
        return False
    if agent and item.get("agent_id") != agent:
        return False
    if session and item.get("session_id") != session:
        return False
    return True


def _counts(items: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    counts = {
        "total": 0,
        "runs": 0,
        "events": 0,
        "tasks": 0,
        "tools": 0,
        "approvals": 0,
        "artifacts": 0,
        "receipts": 0,
        "checkpoints": 0,
        "reconciliations": 0,
        "completed": 0,
        "completed_with_evidence": 0,
        "pending": 0,
        "unknown": 0,
        "replay": 0,
    }
    for item in items:
        counts["total"] += 1
        kind = str(item.get("kind") or "")
        if kind == "run":
            counts["runs"] += 1
        elif kind == "event":
            counts["events"] += 1
        elif kind == "task":
            counts["tasks"] += 1
        elif kind == "tool":
            counts["tools"] += 1
        elif kind == "approval":
            counts["approvals"] += 1
        elif kind == "artifact":
            counts["artifacts"] += 1
        elif kind == "receipt":
            counts["receipts"] += 1
        elif kind == "checkpoint":
            counts["checkpoints"] += 1
        elif kind == "reconciliation":
            counts["reconciliations"] += 1
        if item.get("status") == "completed":
            counts["completed"] += 1
        if item.get("completed"):
            counts["completed_with_evidence"] += 1
        if item.get("pending"):
            counts["pending"] += 1
        if item.get("unknown"):
            counts["unknown"] += 1
        if item.get("replay"):
            counts["replay"] += 1
    return counts


def _cost(items: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    debit = credit = 0
    entries = 0
    by_currency: dict[str, dict[str, int]] = {}
    for item in items:
        cost = item.get("cost")
        if not isinstance(cost, Mapping):
            continue
        amount = _int(cost.get("amount_minor"))
        currency = _clean(cost.get("currency")) or "USD"
        direction = _clean(cost.get("direction")).lower()
        entries += 1
        bucket = by_currency.setdefault(
            currency, {"debit_minor": 0, "credit_minor": 0, "entries": 0})
        bucket["entries"] += 1
        if direction == "credit":
            credit += amount
            bucket["credit_minor"] += amount
        elif direction == "debit":
            debit += amount
            bucket["debit_minor"] += amount
        else:
            # A tool cost is spend; it is not a ledger double-entry row.
            debit += amount
            bucket["debit_minor"] += amount
    return {
        "debit_minor": debit,
        "credit_minor": credit,
        "net_minor": credit - debit,
        "entries": entries,
        "by_currency": dict(sorted(by_currency.items())),
        "source": "bos_ledger_entry + bos_tool_invocation",
    }


def _token_total(items: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    """Total usage from one aggregate per run (never double-counted).

    A run item already sums its own job events and checkpoints, so counting the
    checkpoint/event items again would double the number. Only the run items
    contribute to the headline total.
    """
    prompt = completion = total = runs = 0
    for item in items:
        if item.get("kind") != "run":
            continue
        tokens = item.get("tokens")
        if not isinstance(tokens, Mapping):
            continue
        prompt += _int(tokens.get("prompt"))
        completion += _int(tokens.get("completion"))
        total += _int(tokens.get("total"))
        runs += 1
    return {"prompt": prompt, "completion": completion, "total": total,
            "runs": runs}


__all__ = [
    "DEFAULT_LIMIT",
    "KINDS",
    "MAX_LIMIT",
    "SCHEMA",
    "STAGES",
    "operator_timeline",
]
