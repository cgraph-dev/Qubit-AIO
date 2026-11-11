"""Typed Business OS records.

These are plain immutable value objects decoded from SQLite rows. They carry
the tenant/business scope explicitly so a caller cannot accidentally treat a
row from one tenant as a row from another.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .util import json_loads


def _row(row: Mapping[str, Any] | Any) -> dict[str, Any]:
    return dict(row)


@dataclass(frozen=True, slots=True)
class Tenant:
    tenant_id: str
    name: str
    created_at: str
    metadata: dict[str, Any]

    @classmethod
    def from_row(cls, row: Any) -> "Tenant":
        data = _row(row)
        return cls(
            tenant_id=data["tenant_id"],
            name=data["name"],
            created_at=data["created_at"],
            metadata=json_loads(data.get("metadata_json"), {}),
        )


@dataclass(frozen=True, slots=True)
class Business:
    business_id: str
    tenant_id: str
    name: str
    slug: str
    status: str
    currency: str
    created_at: str
    updated_at: str
    metadata: dict[str, Any]

    @classmethod
    def from_row(cls, row: Any) -> "Business":
        data = _row(row)
        return cls(
            business_id=data["business_id"],
            tenant_id=data["tenant_id"],
            name=data["name"],
            slug=data["slug"],
            status=data["status"],
            currency=data["currency"],
            created_at=data["created_at"],
            updated_at=data["updated_at"],
            metadata=json_loads(data.get("metadata_json"), {}),
        )


@dataclass(frozen=True, slots=True)
class Contact:
    contact_id: str
    tenant_id: str
    business_id: str
    kind: str
    display_name: str
    email: str | None
    external_ref: str | None
    created_at: str
    metadata: dict[str, Any]

    @classmethod
    def from_row(cls, row: Any) -> "Contact":
        data = _row(row)
        return cls(
            contact_id=data["contact_id"],
            tenant_id=data["tenant_id"],
            business_id=data["business_id"],
            kind=data["kind"],
            display_name=data["display_name"],
            email=data["email"],
            external_ref=data["external_ref"],
            created_at=data["created_at"],
            metadata=json_loads(data.get("metadata_json"), {}),
        )


@dataclass(frozen=True, slots=True)
class Task:
    task_id: str
    tenant_id: str
    business_id: str
    title: str
    status: str
    assigned_to: str | None
    created_at: str
    updated_at: str
    metadata: dict[str, Any]

    @classmethod
    def from_row(cls, row: Any) -> "Task":
        data = _row(row)
        return cls(
            task_id=data["task_id"],
            tenant_id=data["tenant_id"],
            business_id=data["business_id"],
            title=data["title"],
            status=data["status"],
            assigned_to=data["assigned_to"],
            created_at=data["created_at"],
            updated_at=data["updated_at"],
            metadata=json_loads(data.get("metadata_json"), {}),
        )


@dataclass(frozen=True, slots=True)
class JobRun:
    job_id: str
    tenant_id: str
    business_id: str
    kind: str
    status: str
    idempotency_key: str
    request_hash: str
    input: Any
    output: Any
    attempt: int
    lease_owner: str | None
    lease_expires_at: float | None
    last_error_code: str | None
    last_error_detail: str | None
    created_at: str
    updated_at: str
    started_at: str | None
    finished_at: str | None

    @classmethod
    def from_row(cls, row: Any) -> "JobRun":
        data = _row(row)
        return cls(
            job_id=data["job_id"],
            tenant_id=data["tenant_id"],
            business_id=data["business_id"],
            kind=data["kind"],
            status=data["status"],
            idempotency_key=data["idempotency_key"],
            request_hash=data["request_hash"],
            input=json_loads(data.get("input_json"), {}),
            output=json_loads(data.get("output_json"), None),
            attempt=data["attempt"],
            lease_owner=data["lease_owner"],
            lease_expires_at=data["lease_expires_at"],
            last_error_code=data["last_error_code"],
            last_error_detail=data["last_error_detail"],
            created_at=data["created_at"],
            updated_at=data["updated_at"],
            started_at=data["started_at"],
            finished_at=data["finished_at"],
        )


@dataclass(frozen=True, slots=True)
class JobEvent:
    event_id: str
    tenant_id: str
    job_id: str
    seq: int
    kind: str
    actor: str
    detail: Any
    created_at: str

    @classmethod
    def from_row(cls, row: Any) -> "JobEvent":
        data = _row(row)
        return cls(
            event_id=data["event_id"],
            tenant_id=data["tenant_id"],
            job_id=data["job_id"],
            seq=data["seq"],
            kind=data["kind"],
            actor=data["actor"],
            detail=json_loads(data.get("detail_json"), {}),
            created_at=data["created_at"],
        )


@dataclass(frozen=True, slots=True)
class Approval:
    approval_id: str
    tenant_id: str
    business_id: str
    job_id: str | None
    action: str
    status: str
    requested_by: str
    decided_by: str | None
    reason: str
    idempotency_key: str
    request_hash: str
    policy_version: str
    payload: Any
    created_at: str
    decided_at: str | None
    expires_at: float | None

    @classmethod
    def from_row(cls, row: Any) -> "Approval":
        data = _row(row)
        return cls(
            approval_id=data["approval_id"],
            tenant_id=data["tenant_id"],
            business_id=data["business_id"],
            job_id=data["job_id"],
            action=data["action"],
            status=data["status"],
            requested_by=data["requested_by"],
            decided_by=data["decided_by"],
            reason=data["reason"],
            idempotency_key=data["idempotency_key"],
            request_hash=data["request_hash"],
            policy_version=data["policy_version"],
            payload=json_loads(data.get("payload_json"), {}),
            created_at=data["created_at"],
            decided_at=data["decided_at"],
            expires_at=data["expires_at"],
        )


@dataclass(frozen=True, slots=True)
class LedgerEntry:
    entry_id: str
    tenant_id: str
    business_id: str
    job_id: str | None
    account: str
    direction: str
    amount_minor: int
    currency: str
    idempotency_key: str
    request_hash: str
    memo: str
    created_at: str

    @classmethod
    def from_row(cls, row: Any) -> "LedgerEntry":
        data = _row(row)
        return cls(
            entry_id=data["entry_id"],
            tenant_id=data["tenant_id"],
            business_id=data["business_id"],
            job_id=data["job_id"],
            account=data["account"],
            direction=data["direction"],
            amount_minor=data["amount_minor"],
            currency=data["currency"],
            idempotency_key=data["idempotency_key"],
            request_hash=data["request_hash"],
            memo=data["memo"],
            created_at=data["created_at"],
        )


@dataclass(frozen=True, slots=True)
class Artifact:
    artifact_id: str
    tenant_id: str
    business_id: str
    job_id: str | None
    kind: str
    name: str
    media_type: str
    sha256: str
    size_bytes: int
    body: str | None
    created_at: str

    @classmethod
    def from_row(cls, row: Any) -> "Artifact":
        data = _row(row)
        return cls(
            artifact_id=data["artifact_id"],
            tenant_id=data["tenant_id"],
            business_id=data["business_id"],
            job_id=data["job_id"],
            kind=data["kind"],
            name=data["name"],
            media_type=data["media_type"],
            sha256=data["sha256"],
            size_bytes=data["size_bytes"],
            body=data["body"],
            created_at=data["created_at"],
        )


@dataclass(frozen=True, slots=True)
class AuditEvent:
    event_id: str
    tenant_id: str
    business_id: str | None
    actor: str
    action: str
    resource_type: str
    resource_id: str | None
    outcome: str
    detail: Any
    created_at: str

    @classmethod
    def from_row(cls, row: Any) -> "AuditEvent":
        data = _row(row)
        return cls(
            event_id=data["event_id"],
            tenant_id=data["tenant_id"],
            business_id=data["business_id"],
            actor=data["actor"],
            action=data["action"],
            resource_type=data["resource_type"],
            resource_id=data["resource_id"],
            outcome=data["outcome"],
            detail=json_loads(data.get("detail_json"), {}),
            created_at=data["created_at"],
        )


@dataclass(frozen=True, slots=True)
class ToolInvocation:
    invocation_id: str
    tenant_id: str
    business_id: str
    actor: str
    tool: str
    autonomy: str
    policy_version: str
    idempotency_key: str
    request_hash: str
    dry_run: bool
    status: str
    currency: str
    cost_minor: int
    request: Any
    result: Any
    error_code: str | None
    created_at: str

    @classmethod
    def from_row(cls, row: Any) -> "ToolInvocation":
        data = _row(row)
        return cls(
            invocation_id=data["invocation_id"],
            tenant_id=data["tenant_id"],
            business_id=data["business_id"],
            actor=data["actor"],
            tool=data["tool"],
            autonomy=data["autonomy"],
            policy_version=data["policy_version"],
            idempotency_key=data["idempotency_key"],
            request_hash=data["request_hash"],
            dry_run=bool(data["dry_run"]),
            status=data["status"],
            currency=data["currency"],
            cost_minor=data["cost_minor"],
            request=json_loads(data.get("request_json"), {}),
            result=json_loads(data.get("result_json"), None),
            error_code=data["error_code"],
            created_at=data["created_at"],
        )
