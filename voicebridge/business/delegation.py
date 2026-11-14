"""Bounded delegation, a lease-backed work queue, and pinned worktrees.

P4-03. This module composes the records that already exist — it is **not** a
second task engine:

* a delegated subtask is a real :class:`voicebridge.business.runs.WorkTask`
  (``task_class='execution'``);
* every mutation goes through :mod:`runs` (claim, transition, lease, fence) and
  :mod:`sessions` (handoff proposal/acceptance);
* the only new durable record is an append-only delegation event on the child
  task's aggregate, which carries the parent link, depth, wave, attempt
  counters and artifact manifest history. Nothing here is a second store.

Caps are explicit and fail closed:

* ``max_depth`` — a delegation tree stops growing past the ceiling; the
  refused child is never created;
* ``max_wave_size`` — a parent may not fan out more than the wave ceiling;
* ``max_concurrency`` — a principal holds at most this many task leases;
* ``max_attempts`` — attempt events past the cap block the task, never loop;
* ``no_progress_attempts`` — repeated attempts with an identical progress
  fingerprint block the task instead of pretending progress;
* ``max_handoffs`` — a task cannot be handed off indefinitely.

Lease loss stops work: ``complete`` and artifact writes assert the live lease
and fencing token before any effect. A workspace has one mutable writer, and
artifact rows are immutable: the same name with new content appends a new
version rather than overwriting the old one.
"""
from __future__ import annotations

import hashlib
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from . import eventlog, runs, sessions
from .errors import BAD_INPUT_CODE, BAD_STATE_CODE, BusinessError
from .ids import fingerprint
from .util import require_text

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .models import Artifact
    from .store import BusinessStore

DELEGATION_CREATED = "delegation.created"
DELEGATION_ATTEMPT = "delegation.attempt"
DELEGATION_COMPLETED = "delegation.completed"
DELEGATION_ARTIFACT = "delegation.artifact"
DELEGATION_HANDOFF = "delegation.handoff"
DELEGATION_HANDOFF_ACCEPTED = "delegation.handoff_accepted"
DELEGATION_ESCALATED = "delegation.escalated"

DELEGATION_DEPTH_EXCEEDED_CODE = "BUSINESS_DELEGATION_DEPTH_EXCEEDED"
DELEGATION_WAVE_EXCEEDED_CODE = "BUSINESS_DELEGATION_WAVE_EXCEEDED"
DELEGATION_CONCURRENCY_EXCEEDED_CODE = (
    "BUSINESS_DELEGATION_CONCURRENCY_EXCEEDED"
)
DELEGATION_ATTEMPTS_EXCEEDED_CODE = "BUSINESS_DELEGATION_ATTEMPTS_EXCEEDED"
DELEGATION_NO_PROGRESS_CODE = "BUSINESS_DELEGATION_NO_PROGRESS"
DELEGATION_HANDOFF_EXCEEDED_CODE = "BUSINESS_DELEGATION_HANDOFF_EXCEEDED"
DELEGATION_SINGLE_OWNER_CODE = "BUSINESS_DELEGATION_SINGLE_OWNER"
DELEGATION_WORKTREE_CONFLICT_CODE = "BUSINESS_DELEGATION_WORKTREE_CONFLICT"
DELEGATION_WORKSPACE_REQUIRED_CODE = "BUSINESS_DELEGATION_WORKSPACE_REQUIRED"
DELEGATION_CAPABILITY_REQUIRED_CODE = "BUSINESS_DELEGATION_CAPABILITY_REQUIRED"
DELEGATION_TERMINAL_CODE = "BUSINESS_DELEGATION_TERMINAL"


@dataclass(frozen=True, slots=True)
class DelegationCaps:
    max_depth: int = 3
    max_handoffs: int = 3
    max_attempts: int = 3
    max_wave_size: int = 8
    max_concurrency: int = 4
    no_progress_attempts: int = 2
    lease_ttl_seconds: float = 300.0

    def validate(self) -> "DelegationCaps":
        for name in (
            "max_depth",
            "max_handoffs",
            "max_attempts",
            "max_wave_size",
            "max_concurrency",
            "no_progress_attempts",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise BusinessError(
                    BAD_INPUT_CODE, f"{name} must be an integer >= 1"
                )
        if float(self.lease_ttl_seconds) <= 0:
            raise BusinessError(BAD_INPUT_CODE, "lease_ttl_seconds must be positive")
        if self.no_progress_attempts > self.max_attempts:
            raise BusinessError(
                BAD_INPUT_CODE,
                "no_progress_attempts cannot exceed max_attempts",
            )
        return self


@dataclass(frozen=True, slots=True)
class DelegationInfo:
    task_id: str
    parent_task_id: str | None
    root_task_id: str
    depth: int
    wave_id: str | None
    delegation_id: str | None


@dataclass(frozen=True, slots=True)
class DelegationResult:
    task: runs.WorkTask
    created: bool
    delegation_id: str
    parent_task_id: str | None
    root_task_id: str
    depth: int
    wave_id: str | None


@dataclass(frozen=True, slots=True)
class ClaimResult:
    task: runs.WorkTask
    lease: runs.Lease
    root_task_id: str
    wave_id: str | None


@dataclass(frozen=True, slots=True)
class AttemptResult:
    task: runs.WorkTask
    attempt_number: int
    progress_changed: bool
    no_progress_streak: int
    blocked: bool
    code: str | None
    detail: str


@dataclass(frozen=True, slots=True)
class CompletionResult:
    task: runs.WorkTask
    artifacts: tuple[dict[str, Any], ...]
    created: bool


@dataclass(frozen=True, slots=True)
class ArtifactWrite:
    artifact: "Artifact"
    workspace: runs.Workspace
    version: int
    revision: str
    created: bool


@dataclass(frozen=True, slots=True)
class Escalation:
    task: runs.WorkTask
    blocked: bool
    code: str
    detail: str


def _text(value: Any, label: str, *, max_len: int = 4096) -> str:
    try:
        return require_text(value, label, max_len=max_len)
    except ValueError as exc:
        raise BusinessError(BAD_INPUT_CODE, str(exc)) from exc


def _optional_text(value: Any, label: str, *, max_len: int = 4096) -> str | None:
    if value is None:
        return None
    return _text(value, label, max_len=max_len)


def _events(
    store: "BusinessStore", tenant_id: str, task_id: str
) -> list[eventlog.EventEnvelope]:
    return eventlog.read_events(store, tenant_id, task_id)


def delegation_info(
    store: "BusinessStore", tenant_id: str, task_id: str
) -> DelegationInfo:
    """Rebuild the child's parent link/depth from its append-only events."""
    latest: dict[str, Any] | None = None
    for event in _events(store, tenant_id, task_id):
        if event.type == DELEGATION_CREATED:
            latest = event.payload if isinstance(event.payload, Mapping) else {}
    if latest is None:
        return DelegationInfo(
            task_id=task_id,
            parent_task_id=None,
            root_task_id=task_id,
            depth=0,
            wave_id=None,
            delegation_id=None,
        )
    parent = latest.get("parent_task_id")
    return DelegationInfo(
        task_id=task_id,
        parent_task_id=parent,
        root_task_id=str(latest.get("root_task_id") or task_id),
        depth=int(latest.get("depth") or 0),
        wave_id=latest.get("wave_id"),
        delegation_id=latest.get("delegation_id"),
    )


def _children(
    store: "BusinessStore", tenant_id: str, business_id: str,
    parent_task_id: str,
) -> list[runs.WorkTask]:
    found: list[runs.WorkTask] = []
    for task in runs.list_tasks(store, tenant_id, business_id=business_id):
        if task.task_id == parent_task_id:
            continue
        info = delegation_info(store, tenant_id, task.task_id)
        if info.parent_task_id == parent_task_id:
            found.append(task)
    return found


def _artifact_records(
    store: "BusinessStore", tenant_id: str, task_id: str
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for event in _events(store, tenant_id, task_id):
        if event.type == DELEGATION_ARTIFACT and isinstance(event.payload, Mapping):
            records.append(dict(event.payload))
    return records


def _event_payload(
    store: "BusinessStore", tenant_id: str, task_id: str, event_type: str
) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for event in _events(store, tenant_id, task_id):
        if event.type == event_type and isinstance(event.payload, Mapping):
            payloads.append(dict(event.payload))
    return payloads


def delegate(
    store: "BusinessStore",
    tenant_id: str,
    business_id: str,
    *,
    title: str,
    parent_task_id: str | None = None,
    objective: str = "",
    acceptance: Iterable[Any] | None = None,
    budget: Mapping[str, Any] | None = None,
    deadline: str | None = None,
    required_capabilities: Iterable[str] | None = None,
    data_class: str = "internal",
    assignee_principal: str | None = None,
    wave_id: str | None = None,
    idempotency_key: str | None = None,
    caps: DelegationCaps | None = None,
    actor: str = "system",
    now: float | None = None,
) -> DelegationResult:
    """Create one bounded child task with a durable parent link."""
    limits = (caps or DelegationCaps()).validate()
    clean_title = _text(title, "task title", max_len=500)
    parent = _optional_text(parent_task_id, "parent_task_id", max_len=80)
    wave = _optional_text(wave_id, "wave_id", max_len=120)
    parent_info: DelegationInfo | None = None
    if parent is not None:
        runs.get_task(store, tenant_id, parent)
        parent_info = delegation_info(store, tenant_id, parent)
        depth = parent_info.depth + 1
        root = parent_info.root_task_id
        if wave is None:
            wave = parent_info.wave_id
        if depth > limits.max_depth:
            raise BusinessError(
                DELEGATION_DEPTH_EXCEEDED_CODE,
                f"delegation depth {depth} exceeds the ceiling"
                f" {limits.max_depth} (root {root!r})",
            )
        siblings = _children(store, tenant_id, business_id, parent)
        in_wave = [
            child for child in siblings
            if wave is None or delegation_info(
                store, tenant_id, child.task_id
            ).wave_id == wave
        ]
        if len(in_wave) >= limits.max_wave_size:
            raise BusinessError(
                DELEGATION_WAVE_EXCEEDED_CODE,
                f"wave {wave or parent!r} already holds {len(in_wave)} children;"
                f" the ceiling is {limits.max_wave_size}",
            )
    else:
        depth = 0
        root = None  # filled once the task id is known

    task_id: str | None = None
    created = True
    if idempotency_key is not None:
        key = _text(idempotency_key, "idempotency_key", max_len=280)
        task_id = "dlgtask_" + fingerprint(
            {"tenant": tenant_id, "parent": parent, "key": key}
        )[:24]
        existing = None
        try:
            existing = runs.get_task(store, tenant_id, task_id)
        except BusinessError as exc:
            if exc.code != runs.TASK_NOT_FOUND_CODE:
                raise
        if existing is not None:
            info = delegation_info(store, tenant_id, task_id)
            return DelegationResult(
                task=existing,
                created=False,
                delegation_id=info.delegation_id or "",
                parent_task_id=info.parent_task_id,
                root_task_id=info.root_task_id,
                depth=info.depth,
                wave_id=info.wave_id,
            )

    task = runs.create_task(
        store,
        tenant_id,
        business_id,
        clean_title,
        objective=objective,
        assignee_principal=assignee_principal,
        required_capabilities=required_capabilities,
        data_class=data_class,
        budget=budget,
        deadline=deadline,
        acceptance=acceptance,
        actor=actor,
        task_id=task_id,
        now=now,
    )
    delegation_id = "dlg_" + hashlib.sha256(
        f"{tenant_id}:{task.task_id}".encode("utf-8")
    ).hexdigest()[:24]
    if root is None:
        root = task.task_id
    eventlog.append_event(
        store,
        tenant_id,
        task.task_id,
        DELEGATION_CREATED,
        {
            "delegation_id": delegation_id,
            "parent_task_id": parent,
            "root_task_id": root,
            "depth": depth,
            "wave_id": wave,
            "business_id": business_id,
            "required_capabilities": list(required_capabilities or ()),
            "data_class": data_class,
            "deadline": deadline,
            "acceptance": list(acceptance or ()),
        },
        actor=actor,
        idempotency_key=f"delegation.created:{task.task_id}",
        business_id=business_id,
        now=now,
    )
    return DelegationResult(
        task=task,
        created=created,
        delegation_id=delegation_id,
        parent_task_id=parent,
        root_task_id=root,
        depth=depth,
        wave_id=wave,
    )


def _required_caps_ok(
    task: runs.WorkTask,
    principal: str,
    can_hold: Callable[[str, str], bool] | None,
) -> None:
    if not task.required_capabilities:
        return
    if can_hold is None:
        raise BusinessError(
            DELEGATION_CAPABILITY_REQUIRED_CODE,
            "the task requires capabilities; a capability checker is required",
        )
    missing = [
        capability for capability in task.required_capabilities
        if not can_hold(principal, capability)
    ]
    if missing:
        raise BusinessError(
            DELEGATION_CAPABILITY_REQUIRED_CODE,
            f"{principal!r} does not hold: {', '.join(missing)}",
        )


def _active_task_leases(
    store: "BusinessStore", tenant_id: str, holder: str
) -> list[runs.Lease]:
    return [
        lease for lease in runs.list_leases(store, tenant_id, active_only=True)
        if lease.resource_type == "task" and lease.holder == holder
    ]


def claim_next(
    store: "BusinessStore",
    tenant_id: str,
    *,
    principal: str,
    business_id: str | None = None,
    queue: str | None = None,
    can_hold: Callable[[str, str], bool] | None = None,
    caps: DelegationCaps | None = None,
    now: float | None = None,
) -> ClaimResult | None:
    """Atomically lease + claim the next eligible task, or return ``None``."""
    limits = (caps or DelegationCaps()).validate()
    who = _text(principal, "principal", max_len=200)
    if len(_active_task_leases(store, tenant_id, who)) >= limits.max_concurrency:
        raise BusinessError(
            DELEGATION_CONCURRENCY_EXCEEDED_CODE,
            f"{who!r} already holds {limits.max_concurrency} task leases",
        )
    candidates = [
        task
        for task in runs.list_tasks(store, tenant_id, business_id=business_id)
        if task.status in ("draft", "assigned")
        and task.assignee_principal in (None, who)
    ]
    if queue is not None:
        wanted = _text(queue, "queue", max_len=120)
        candidates = [
            task for task in candidates
            if wanted in (
                delegation_info(store, tenant_id, task.task_id).root_task_id,
                delegation_info(store, tenant_id, task.task_id).wave_id,
            )
        ]
    candidates.sort(key=lambda task: (task.deadline or "9999", task.created_at, task.task_id))
    moment = time.time() if now is None else float(now)
    for task in candidates:
        try:
            _required_caps_ok(task, who, can_hold)
        except BusinessError:
            continue
        try:
            lease = runs.acquire_lease(
                store,
                tenant_id,
                "task",
                task.task_id,
                holder=who,
                ttl_seconds=limits.lease_ttl_seconds,
                now=moment,
            )
        except BusinessError as exc:
            if exc.code == runs.LEASE_CONFLICT_CODE:
                continue
            raise
        try:
            claimed = runs.claim_task(
                store, tenant_id, task.task_id, principal=who, now=moment
            )
        except BusinessError:
            try:
                runs.release_lease(
                    store, tenant_id, lease.lease_id, holder=who,
                    fencing_token=lease.fencing_token, now=moment,
                )
            except BusinessError:
                pass
            continue
        info = delegation_info(store, tenant_id, claimed.task_id)
        return ClaimResult(
            task=claimed, lease=lease,
            root_task_id=info.root_task_id, wave_id=info.wave_id,
        )
    return None


def heartbeat(
    store: "BusinessStore",
    tenant_id: str,
    lease_id: str,
    *,
    holder: str,
    fencing_token: str,
    caps: DelegationCaps | None = None,
    now: float | None = None,
) -> runs.Lease:
    """Renew the lease; an expired/released lease is a typed loss."""
    limits = (caps or DelegationCaps()).validate()
    return runs.renew_lease(
        store,
        tenant_id,
        lease_id,
        holder=holder,
        fencing_token=fencing_token,
        ttl_seconds=limits.lease_ttl_seconds,
        now=now,
    )


def _progress_token(
    store: "BusinessStore", tenant_id: str, task: runs.WorkTask
) -> str:
    workspaces = runs.list_workspaces(store, tenant_id, task_id=task.task_id)
    revision = workspaces[0].current_revision if workspaces else None
    artifacts = sorted(
        str(record.get("artifact_id"))
        for record in _artifact_records(store, tenant_id, task.task_id)
    )
    return fingerprint(
        {
            "status": task.status,
            "workspace_revision": revision,
            "artifact_ids": artifacts,
        }
    )


def record_attempt(
    store: "BusinessStore",
    tenant_id: str,
    task_id: str,
    *,
    principal: str,
    progress: Any = None,
    caps: DelegationCaps | None = None,
    now: float | None = None,
) -> AttemptResult:
    """Record one attempt and block the task on a retry/no-progress cap."""
    limits = (caps or DelegationCaps()).validate()
    who = _text(principal, "principal", max_len=200)
    task = runs.get_task(store, tenant_id, task_id)
    if task.status in runs.TERMINAL_WORKFLOW_STATUSES:
        return AttemptResult(
            task=task, attempt_number=0, progress_changed=False,
            no_progress_streak=0, blocked=False,
            code=DELEGATION_TERMINAL_CODE,
            detail=f"task is {task.status!r}",
        )
    previous = _event_payload(store, tenant_id, task_id, DELEGATION_ATTEMPT)
    attempt_number = len(previous) + 1
    token = fingerprint(
        progress if progress is not None
        else {"derived": _progress_token(store, tenant_id, task)}
    )
    prior_streak = int(previous[-1].get("no_progress_streak") or 0) if previous else 0
    changed = not previous or previous[-1].get("progress_digest") != token
    streak = 1 if changed else prior_streak + 1
    moment = time.time() if now is None else float(now)
    eventlog.append_event(
        store,
        tenant_id,
        task_id,
        DELEGATION_ATTEMPT,
        {
            "attempt_number": attempt_number,
            "principal": who,
            "progress_digest": token,
            "progress_changed": changed,
            "no_progress_streak": streak,
        },
        actor=who,
        idempotency_key=f"delegation.attempt:{task_id}:{attempt_number}",
        now=moment,
    )
    code: str | None = None
    detail = ""
    if attempt_number > limits.max_attempts:
        code = DELEGATION_ATTEMPTS_EXCEEDED_CODE
        detail = (
            f"attempt {attempt_number} exceeds the {limits.max_attempts}"
            " attempt ceiling"
        )
    elif streak >= limits.no_progress_attempts:
        code = DELEGATION_NO_PROGRESS_CODE
        detail = (
            f"{streak} consecutive attempts produced no artifact, checkpoint"
            " or status change"
        )
    blocked = False
    if code is not None:
        blocked = _block(store, tenant_id, task_id, who, detail, moment)
    return AttemptResult(
        task=runs.get_task(store, tenant_id, task_id),
        attempt_number=attempt_number,
        progress_changed=changed,
        no_progress_streak=streak,
        blocked=blocked,
        code=code,
        detail=detail,
    )


def _block(
    store: "BusinessStore",
    tenant_id: str,
    task_id: str,
    actor: str,
    reason: str,
    moment: float,
) -> bool:
    task = runs.get_task(store, tenant_id, task_id)
    if task.status == "blocked":
        return True
    try:
        runs.transition_task(
            store, tenant_id, task_id, "blocked",
            actor=actor, note=reason, now=moment,
        )
        return True
    except BusinessError:
        return False


def escalate(
    store: "BusinessStore",
    tenant_id: str,
    task_id: str,
    *,
    principal: str,
    reason: str,
    evidence: Iterable[str] | None = None,
    caps: DelegationCaps | None = None,
    now: float | None = None,
) -> Escalation:
    """Record an escalation and block the task when the state machine allows."""
    (caps or DelegationCaps()).validate()
    who = _text(principal, "principal", max_len=200)
    why = _text(reason, "reason", max_len=2000)
    task = runs.get_task(store, tenant_id, task_id)
    if task.status in runs.TERMINAL_WORKFLOW_STATUSES:
        raise BusinessError(
            BAD_STATE_CODE, f"task {task_id!r} is {task.status!r} and cannot escalate"
        )
    moment = time.time() if now is None else float(now)
    refs = [str(item) for item in (evidence or ())]
    eventlog.append_event(
        store,
        tenant_id,
        task_id,
        DELEGATION_ESCALATED,
        {"principal": who, "reason": why, "evidence": refs},
        actor=who,
        idempotency_key=f"delegation.escalated:{task_id}:{fingerprint(refs + [why])[:16]}",
        now=moment,
    )
    blocked = _block(store, tenant_id, task_id, who, why, moment)
    return Escalation(
        task=runs.get_task(store, tenant_id, task_id),
        blocked=blocked,
        code="ESCALATED_BLOCKED" if blocked else "ESCALATED",
        detail=why,
    )


def _assert_owner(task: runs.WorkTask, principal: str) -> None:
    if task.assignee_principal != principal:
        raise BusinessError(
            DELEGATION_SINGLE_OWNER_CODE,
            f"task {task.task_id!r} is owned by"
            f" {task.assignee_principal!r}, not {principal!r}",
        )


def bind_worktree(
    store: "BusinessStore",
    tenant_id: str,
    task_id: str,
    *,
    session_id: str,
    repository_ref: str,
    worktree_ref: str,
    storage_class: str = "ephemeral",
    access_policy: Mapping[str, Any] | None = None,
    actor: str = "system",
    now: float | None = None,
) -> tuple[runs.Workspace, bool]:
    """Pin one worktree workspace to the task; a different binding conflicts."""
    task = runs.get_task(store, tenant_id, task_id)
    repo = _text(repository_ref, "repository_ref", max_len=1024)
    worktree = _text(worktree_ref, "worktree_ref", max_len=1024)
    existing = runs.list_workspaces(store, tenant_id, task_id=task.task_id)
    for workspace in existing:
        if (
            workspace.repository_ref == repo
            and workspace.worktree_ref == worktree
        ):
            return workspace, False
        if workspace.repository_ref or workspace.worktree_ref:
            raise BusinessError(
                DELEGATION_WORKTREE_CONFLICT_CODE,
                f"task {task.task_id!r} is already pinned to"
                f" {workspace.repository_ref!r}/{workspace.worktree_ref!r}",
            )
    workspace = runs.create_workspace(
        store,
        tenant_id,
        task_id=task.task_id,
        session_id=session_id,
        repository_ref=repo,
        worktree_ref=worktree,
        storage_class=storage_class,
        access_policy=access_policy,
        actor=actor,
        now=now,
    )
    return workspace, True


def _next_revision(current: str) -> str:
    try:
        return str(int(current) + 1)
    except (TypeError, ValueError):
        return f"{current}.1"


def _workspace_lease(
    store: "BusinessStore",
    tenant_id: str,
    workspace_id: str,
    principal: str,
    moment: float,
) -> runs.Lease:
    for lease in runs.list_leases(
        store, tenant_id, resource_type="workspace",
        resource_id=workspace_id, active_only=True,
    ):
        if lease.holder == principal:
            if lease.expires_at <= moment:
                raise BusinessError(runs.LEASE_LOST_CODE, "workspace lease expired")
            return lease
        raise BusinessError(
            DELEGATION_SINGLE_OWNER_CODE,
            f"workspace {workspace_id!r} is leased to {lease.holder!r}",
        )
    return runs.acquire_lease(
        store,
        tenant_id,
        "workspace",
        workspace_id,
        holder=principal,
        ttl_seconds=DelegationCaps().lease_ttl_seconds,
        now=moment,
    )


def _record_artifact(
    store: "BusinessStore",
    tenant_id: str,
    task: runs.WorkTask,
    workspace: runs.Workspace,
    *,
    principal: str,
    name: str,
    kind: str,
    body: str | None,
    media_type: str,
    caps: DelegationCaps,
    moment: float,
) -> ArtifactWrite:
    clean_name = _text(name, "artifact name", max_len=300)
    digest = hashlib.sha256((body or "").encode("utf-8")).hexdigest()
    existing_records = _artifact_records(store, tenant_id, task.task_id)
    same = [
        record for record in existing_records
        if record.get("name") == clean_name and record.get("sha256") == digest
    ]
    if same:
        artifact = store.get_artifact(tenant_id, str(same[-1]["artifact_id"]))
        return ArtifactWrite(
            artifact=artifact, workspace=workspace,
            version=int(same[-1].get("version") or 1),
            revision=workspace.current_revision, created=False,
        )
    version = sum(
        1 for record in existing_records if record.get("name") == clean_name
    ) + 1
    artifact = store.record_artifact(
        tenant_id,
        task.business_id,
        kind=kind,
        name=clean_name,
        body=body,
        media_type=media_type,
    )
    entry = {
        "artifact_id": artifact.artifact_id,
        "name": artifact.name,
        "kind": artifact.kind,
        "sha256": artifact.sha256,
        "version": version,
        "media_type": artifact.media_type,
        "size_bytes": artifact.size_bytes,
    }
    eventlog.append_event(
        store,
        tenant_id,
        task.task_id,
        DELEGATION_ARTIFACT,
        entry,
        actor=principal,
        idempotency_key=(
            f"delegation.artifact:{task.task_id}:{artifact.artifact_id}"
        ),
        business_id=task.business_id,
        now=moment,
    )
    lease = _workspace_lease(
        store, tenant_id, workspace.workspace_id, principal, moment
    )
    manifest = list(workspace.artifact_manifest) + [entry]
    revised = runs.revise_workspace(
        store,
        tenant_id,
        workspace.workspace_id,
        expected_revision=workspace.current_revision,
        new_revision=_next_revision(workspace.current_revision),
        holder=principal,
        fencing_token=lease.fencing_token,
        artifact_manifest=manifest,
        now=moment,
    )
    return ArtifactWrite(
        artifact=artifact, workspace=revised, version=version,
        revision=revised.current_revision, created=True,
    )


def write_artifact(
    store: "BusinessStore",
    tenant_id: str,
    task_id: str,
    *,
    principal: str,
    lease_id: str,
    fencing_token: str,
    name: str,
    kind: str,
    body: str | None,
    media_type: str = "text/plain",
    caps: DelegationCaps | None = None,
    now: float | None = None,
) -> ArtifactWrite:
    """Append an immutable artifact version under the live task lease."""
    limits = (caps or DelegationCaps()).validate()
    who = _text(principal, "principal", max_len=200)
    task = runs.get_task(store, tenant_id, task_id)
    _assert_owner(task, who)
    moment = time.time() if now is None else float(now)
    runs.assert_fence(
        store, tenant_id, lease_id,
        holder=who, fencing_token=fencing_token, now=moment,
    )
    workspaces = runs.list_workspaces(store, tenant_id, task_id=task.task_id)
    if not workspaces:
        raise BusinessError(
            DELEGATION_WORKSPACE_REQUIRED_CODE,
            f"task {task.task_id!r} has no pinned workspace; bind a worktree first",
        )
    return _record_artifact(
        store, tenant_id, task, workspaces[0],
        principal=who, name=name, kind=kind, body=body,
        media_type=media_type, caps=limits, moment=moment,
    )


def complete(
    store: "BusinessStore",
    tenant_id: str,
    task_id: str,
    *,
    principal: str,
    lease_id: str,
    fencing_token: str,
    artifacts: Iterable[Mapping[str, Any]] | None = None,
    checkpoint_id: str | None = None,
    summary: str = "",
    caps: DelegationCaps | None = None,
    now: float | None = None,
) -> CompletionResult:
    """Complete the task under a live lease; artifact versions stay immutable."""
    limits = (caps or DelegationCaps()).validate()
    who = _text(principal, "principal", max_len=200)
    task = runs.get_task(store, tenant_id, task_id)
    existing = _event_payload(store, tenant_id, task_id, DELEGATION_COMPLETED)
    if task.status in runs.TERMINAL_WORKFLOW_STATUSES:
        if task.status == "completed" and task.assignee_principal == who and existing:
            return CompletionResult(
                task=task,
                artifacts=tuple(existing[-1].get("artifacts") or ()),
                created=False,
            )
        raise BusinessError(
            BAD_STATE_CODE,
            f"task {task_id!r} is {task.status!r} and cannot complete",
        )
    _assert_owner(task, who)
    moment = time.time() if now is None else float(now)
    runs.assert_fence(
        store, tenant_id, lease_id,
        holder=who, fencing_token=fencing_token, now=moment,
    )
    manifest: list[dict[str, Any]] = []
    workspaces = runs.list_workspaces(store, tenant_id, task_id=task.task_id)
    for item in artifacts or ():
        if not isinstance(item, Mapping):
            raise BusinessError(BAD_INPUT_CODE, "each artifact must be a mapping")
        name = item.get("name")
        if name is None:
            raise BusinessError(BAD_INPUT_CODE, "an artifact needs a name")
        if not workspaces:
            raise BusinessError(
                DELEGATION_WORKSPACE_REQUIRED_CODE,
                "completing with artifacts requires a pinned workspace",
            )
        written = _record_artifact(
            store, tenant_id, task, workspaces[0],
            principal=who,
            name=str(name),
            kind=str(item.get("kind") or "file"),
            body=item.get("body"),
            media_type=str(item.get("media_type") or "text/plain"),
            caps=limits,
            moment=moment,
        )
        workspaces = [written.workspace]
        manifest.append({
            "artifact_id": written.artifact.artifact_id,
            "name": written.artifact.name,
            "sha256": written.artifact.sha256,
            "version": written.version,
        })
    eventlog.append_event(
        store,
        tenant_id,
        task.task_id,
        DELEGATION_COMPLETED,
        {
            "principal": who,
            "summary": str(summary or "")[:4000],
            "checkpoint_id": _optional_text(checkpoint_id, "checkpoint_id", max_len=80),
            "artifacts": manifest,
        },
        actor=who,
        idempotency_key=f"delegation.completed:{task.task_id}",
        business_id=task.business_id,
        now=moment,
    )
    completed = runs.transition_task(
        store, tenant_id, task.task_id, "completed",
        actor=who, note=str(summary or "")[:2000], now=moment,
    )
    try:
        runs.release_lease(
            store, tenant_id, lease_id,
            holder=who, fencing_token=fencing_token, now=moment,
        )
    except BusinessError:
        pass
    return CompletionResult(
        task=completed, artifacts=tuple(manifest), created=True
    )


def handoff(
    store: "BusinessStore",
    tenant_id: str,
    session_id: str,
    task_id: str,
    *,
    from_principal: str,
    to_principal: str,
    reason: str = "",
    artifact_ids: Iterable[str] | None = None,
    checkpoint_id: str | None = None,
    unresolved_questions: Iterable[str] | None = None,
    required_capabilities: Iterable[str] | None = None,
    acceptance_deadline: Any = None,
    caps: DelegationCaps | None = None,
    actor: str = "system",
    now: float | None = None,
) -> sessions.Handoff:
    """Propose a handoff with artifacts, bounded by the handoff ceiling."""
    limits = (caps or DelegationCaps()).validate()
    current = sessions.list_handoffs(store, tenant_id, task_id=task_id)
    if len(current) >= limits.max_handoffs:
        raise BusinessError(
            DELEGATION_HANDOFF_EXCEEDED_CODE,
            f"task {task_id!r} already has {len(current)} handoffs; the ceiling"
            f" is {limits.max_handoffs}",
        )
    record = sessions.propose_handoff(
        store,
        tenant_id,
        session_id,
        to_principal=to_principal,
        actor=actor,
        task_id=task_id,
        from_principal=from_principal,
        reason=reason,
        checkpoint_id=checkpoint_id,
        artifact_ids=artifact_ids,
        unresolved_questions=unresolved_questions,
        required_capabilities=required_capabilities,
        acceptance_deadline=acceptance_deadline,
        now=now,
    )
    eventlog.append_event(
        store,
        tenant_id,
        task_id,
        DELEGATION_HANDOFF,
        {
            "handoff_id": record.handoff_id,
            "from": from_principal,
            "to": to_principal,
            "artifact_ids": list(record.artifact_ids),
            "checkpoint_id": record.checkpoint_id,
        },
        actor=actor,
        idempotency_key=f"delegation.handoff:{record.handoff_id}",
        business_id=record.business_id,
        now=now,
    )
    return record


def accept_handoff(
    store: "BusinessStore",
    tenant_id: str,
    handoff_id: str,
    *,
    principal: str,
    now: float | None = None,
) -> sessions.Handoff:
    """Accept a proposed handoff; the artifact list is preserved durably."""
    record = sessions.get_handoff(store, tenant_id, handoff_id)
    accepted = sessions.accept_handoff(
        store, tenant_id, handoff_id, principal=principal, now=now
    )
    eventlog.append_event(
        store,
        tenant_id,
        accepted.task_id,
        DELEGATION_HANDOFF_ACCEPTED,
        {
            "handoff_id": accepted.handoff_id,
            "from": accepted.from_principal,
            "to": accepted.to_principal,
            "artifact_ids": list(record.artifact_ids),
            "checkpoint_id": record.checkpoint_id,
        },
        actor=principal,
        idempotency_key=f"delegation.handoff_accepted:{handoff_id}",
        business_id=accepted.business_id,
        now=now,
    )
    return accepted


def reject_handoff(
    store: "BusinessStore",
    tenant_id: str,
    handoff_id: str,
    *,
    principal: str,
    reason: str = "",
    now: float | None = None,
) -> sessions.Handoff:
    """Reject a proposed handoff; the source keeps ownership unchanged."""
    return sessions.reject_handoff(
        store, tenant_id, handoff_id,
        principal=principal, reason=reason, now=now,
    )


__all__ = [
    "DELEGATION_ARTIFACT",
    "DELEGATION_ATTEMPTS_EXCEEDED_CODE",
    "DELEGATION_ATTEMPT",
    "DELEGATION_COMPLETED",
    "DELEGATION_CONCURRENCY_EXCEEDED_CODE",
    "DELEGATION_CREATED",
    "DELEGATION_DEPTH_EXCEEDED_CODE",
    "DELEGATION_ESCALATED",
    "DELEGATION_HANDOFF",
    "DELEGATION_HANDOFF_ACCEPTED",
    "DELEGATION_HANDOFF_EXCEEDED_CODE",
    "DELEGATION_NO_PROGRESS_CODE",
    "DELEGATION_SINGLE_OWNER_CODE",
    "DELEGATION_TERMINAL_CODE",
    "DELEGATION_WAVE_EXCEEDED_CODE",
    "DELEGATION_WORKSPACE_REQUIRED_CODE",
    "DELEGATION_WORKTREE_CONFLICT_CODE",
    "ArtifactWrite",
    "AttemptResult",
    "ClaimResult",
    "CompletionResult",
    "DelegationCaps",
    "DelegationInfo",
    "DelegationResult",
    "Escalation",
    "accept_handoff",
    "bind_worktree",
    "claim_next",
    "complete",
    "delegate",
    "delegation_info",
    "escalate",
    "handoff",
    "heartbeat",
    "record_attempt",
    "reject_handoff",
    "write_artifact",
]
