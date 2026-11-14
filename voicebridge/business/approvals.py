"""Human approval gates.

An approval is a durable pause, not a prompt suggestion. Requesting one moves
the linked job to ``waiting_approval`` and releases its worker lease, so no
worker can keep mutating things while a human decides. Approving returns the
job to ``queued`` (it still has to be claimed); rejecting or cancelling ends
it. A second decision on a decided approval is a typed refusal.
"""
from __future__ import annotations

import sqlite3
import time
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from . import models
from .errors import (
    APPROVAL_AUTOMATION_REFUSED_CODE,
    APPROVAL_DELEGATION_WIDENED_CODE,
    APPROVAL_EXPIRED_CODE,
    APPROVAL_INTENT_MISMATCH_CODE,
    APPROVAL_NOT_PENDING_CODE,
    APPROVAL_POLICY_STALE_CODE,
    APPROVAL_REQUIRED_CODE,
    APPROVAL_REVOKED_CODE,
    BAD_INPUT_CODE,
    BAD_STATE_CODE,
    IDEMPOTENCY_CONFLICT_CODE,
    NOT_FOUND_CODE,
    BusinessError,
)
from .ids import fingerprint, new_id
from .util import json_dumps, now_iso, redact

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .store import BusinessStore

APPROVAL_STATUSES = ("pending", "approved", "rejected", "expired", "cancelled")
DECISIONS = (
    "approved",
    "rejected",
    "cancelled",
    "expired",
    "edit",
    "delegate",
)
INTENT_FIELDS = (
    "action",
    "target",
    "parameters",
    "currency",
    "amount_minor",
    "policy_revision",
)


def _text(value: Any, label: str, *, max_len: int = 4096) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BusinessError(BAD_INPUT_CODE, f"{label} must be a non-empty string")
    clean = value.strip()
    if len(clean) > max_len:
        raise BusinessError(
            BAD_INPUT_CODE, f"{label} is longer than {max_len} characters"
        )
    return clean


def _is_normalized_intent(value: Any) -> bool:
    return (
        isinstance(value, Mapping)
        and all(field in value for field in INTENT_FIELDS)
        and isinstance(value.get("parameters"), Mapping)
    )


def normalized_intent(
    action: str,
    target: str,
    parameters: Mapping[str, Any] | None,
    currency: str,
    amount_minor: int,
    policy_revision: str,
    *,
    business_id: str | None = None,
) -> dict[str, Any]:
    """Build the canonical intent an approval is bound to.

    ``parameters`` must not contain credentials: the intent is persisted in the
    approval payload and its hash is the execution-time binding. ``business_id``
    is carried on the intent when provided so business scope can be re-checked.
    """
    action_clean = _text(action, "intent action", max_len=200)
    target_clean = _text(target, "intent target", max_len=500)
    if parameters is None:
        params: dict[str, Any] = {}
    elif isinstance(parameters, Mapping):
        params = dict(parameters)
    else:
        raise BusinessError(BAD_INPUT_CODE, "intent parameters must be a mapping")
    currency_clean = _text(currency, "intent currency", max_len=8).upper()
    if len(currency_clean) != 3 or not currency_clean.isalpha():
        raise BusinessError(BAD_INPUT_CODE, "intent currency must be a 3-letter code")
    if not isinstance(amount_minor, int) or isinstance(amount_minor, bool):
        raise BusinessError(BAD_INPUT_CODE, "intent amount_minor must be an integer")
    if amount_minor < 0:
        raise BusinessError(BAD_INPUT_CODE, "intent amount_minor must be non-negative")
    revision = _text(policy_revision, "intent policy revision", max_len=80)
    intent: dict[str, Any] = {
        "action": action_clean,
        "target": target_clean,
        "parameters": params,
        "currency": currency_clean,
        "amount_minor": amount_minor,
        "policy_revision": revision,
    }
    if business_id is not None:
        intent["business_id"] = _text(business_id, "intent business id", max_len=200)
    return intent


def intent_hash(intent: Mapping[str, Any]) -> str:
    """Canonical hash of the exact intent (order-insensitive, full content)."""
    if not isinstance(intent, Mapping):
        raise BusinessError(BAD_INPUT_CODE, "intent must be a mapping")
    return fingerprint(dict(intent))


def _intent_business(intent: Mapping[str, Any]) -> str | None:
    direct = intent.get("business_id")
    if direct is not None:
        return str(direct)
    params = intent.get("parameters")
    if isinstance(params, Mapping) and params.get("business_id") is not None:
        return str(params["business_id"])
    return None


def _intent_capabilities(intent: Mapping[str, Any]) -> tuple[str, ...]:
    raw = intent.get("capability_subset")
    if raw is None:
        params = intent.get("parameters")
        if isinstance(params, Mapping):
            raw = params.get("capability_subset")
    if raw is None:
        return ()
    if isinstance(raw, str):
        return (raw,)
    return tuple(str(item) for item in raw)


def _automation_actor(actor: str) -> bool:
    return actor.lower().startswith(("model:", "llm:", "ai:"))


def _insert_pending(
    conn: sqlite3.Connection,
    *,
    approval_id: str,
    tenant_id: str,
    business_id: str,
    job_id: str | None,
    action: str,
    requested_by: str,
    reason: str,
    idempotency_key: str,
    request_hash: str,
    policy_version: str,
    payload: Mapping[str, Any],
    created_at: str,
    expires_at: float | None,
) -> None:
    conn.execute(
        "INSERT INTO bos_approval(approval_id, tenant_id, business_id,"
        " job_id, action, status, requested_by, reason, idempotency_key,"
        " request_hash, policy_version, payload_json, created_at,"
        " expires_at) VALUES(?,?,?,?,?,'pending',?,?,?,?,?,?,?,?)",
        (
            approval_id,
            tenant_id,
            business_id,
            job_id,
            action,
            requested_by,
            reason,
            idempotency_key,
            request_hash,
            policy_version,
            json_dumps(redact(dict(payload))),
            created_at,
            expires_at,
        ),
    )


def _row(
    conn: sqlite3.Connection, tenant_id: str, approval_id: str
) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM bos_approval WHERE tenant_id=? AND approval_id=?",
        (tenant_id, approval_id),
    ).fetchone()
    if row is None:
        raise BusinessError(
            NOT_FOUND_CODE,
            f"no approval {approval_id!r} in tenant {tenant_id!r}",
        )
    return row


def request(
    store: "BusinessStore",
    *,
    tenant_id: str,
    business_id: str,
    action: str,
    requested_by: str,
    idempotency_key: str,
    job_id: str | None = None,
    payload: Any = None,
    policy_version: str = "v1",
    expires_at: float | None = None,
) -> tuple[models.Approval, bool]:
    """Create a pending approval; a replayed key returns the original.

    When ``payload`` is a full :func:`normalized_intent`, the stored
    ``request_hash`` is the canonical intent hash and both ``action`` and
    ``policy_version`` must agree with it. Legacy ad-hoc payloads keep their
    historical ``action``/``job_id``/payload fingerprint.
    """
    action_clean = _text(action, "approval action", max_len=200)
    actor = _text(requested_by, "requested_by", max_len=200)
    key = _text(idempotency_key, "idempotency key", max_len=300)
    policy = _text(policy_version, "policy version", max_len=80)
    if payload is None:
        body: dict[str, Any] = {}
    elif isinstance(payload, Mapping):
        body = dict(payload)
    else:
        raise BusinessError(BAD_INPUT_CODE, "approval payload must be a mapping")
    normalized = _is_normalized_intent(body)
    if normalized:
        if str(body["action"]) != action_clean:
            raise BusinessError(
                BAD_INPUT_CODE,
                "approval action disagrees with the normalized intent action",
            )
        policy = _text(body["policy_revision"], "policy version", max_len=80)
    stored_body = redact(body)
    if normalized:
        request_hash = intent_hash(stored_body)
    else:
        request_hash = fingerprint(
            {"action": action_clean, "job_id": job_id, "payload": stored_body}
        )
    approval_id = new_id("apr")
    created = now_iso()
    with store.transaction() as conn:
        store._require_business(conn, tenant_id, business_id)
        existing = conn.execute(
            "SELECT * FROM bos_approval WHERE tenant_id=? AND idempotency_key=?",
            (tenant_id, key),
        ).fetchone()
        if existing is not None:
            if (
                existing["action"] != action_clean
                or existing["request_hash"] != request_hash
            ):
                raise BusinessError(
                    IDEMPOTENCY_CONFLICT_CODE,
                    f"idempotency key {key!r} was already used for a different"
                    " approval request",
                )
            return models.Approval.from_row(existing), False
        if job_id is not None:
            job = conn.execute(
                "SELECT * FROM bos_job WHERE tenant_id=? AND business_id=?"
                " AND job_id=?",
                (tenant_id, business_id, job_id),
            ).fetchone()
            if job is None:
                raise BusinessError(
                    NOT_FOUND_CODE,
                    f"no job {job_id!r} for this tenant/business",
                )
            if job["status"] not in ("queued", "running"):
                raise BusinessError(
                    BAD_STATE_CODE,
                    "an approval can only pause a queued or running job"
                    f" (status={job['status']!r})",
                )
        try:
            _insert_pending(
                conn,
                approval_id=approval_id,
                tenant_id=tenant_id,
                business_id=business_id,
                job_id=job_id,
                action=action_clean,
                requested_by=actor,
                reason="",
                idempotency_key=key,
                request_hash=request_hash,
                policy_version=policy,
                payload=stored_body,
                created_at=created,
                expires_at=expires_at,
            )
        except sqlite3.IntegrityError as exc:  # concurrent identical request
            raise BusinessError(
                IDEMPOTENCY_CONFLICT_CODE,
                f"idempotency key {key!r} is already in use",
            ) from exc
        if job_id is not None:
            from . import jobs

            jobs._transition(
                conn,
                store,
                tenant_id,
                job_id,
                "waiting_approval",
                actor=actor,
                note=f"approval {approval_id} requested",
            )
        store._audit(
            conn, tenant_id, business_id, actor, "approval.request", "approval",
            approval_id, "ok",
            {"action": action_clean, "job_id": job_id, "policy_version": policy},
        )
    return get(store, tenant_id, approval_id), True


def get(
    store: "BusinessStore", tenant_id: str, approval_id: str
) -> models.Approval:
    with store.read() as conn:
        return models.Approval.from_row(_row(conn, tenant_id, approval_id))


def list_approvals(
    store: "BusinessStore",
    tenant_id: str,
    *,
    business_id: str | None = None,
    status: str | None = None,
) -> list[models.Approval]:
    if status is not None and status not in APPROVAL_STATUSES:
        raise BusinessError(BAD_INPUT_CODE, f"unknown approval status {status!r}")
    query = "SELECT * FROM bos_approval WHERE tenant_id=?"
    params: list[Any] = [tenant_id]
    if business_id is not None:
        query += " AND business_id=?"
        params.append(business_id)
    if status is not None:
        query += " AND status=?"
        params.append(status)
    query += " ORDER BY created_at, approval_id"
    with store.read() as conn:
        rows = conn.execute(query, params).fetchall()
    return [models.Approval.from_row(row) for row in rows]


def decide(
    store: "BusinessStore",
    *,
    tenant_id: str,
    approval_id: str,
    decision: str,
    decided_by: str,
    reason: str = "",
    now: float | None = None,
    edited_intent: Mapping[str, Any] | None = None,
    delegate_to: str | None = None,
    delegated_capabilities: Any = None,
    idempotency_key: str | None = None,
) -> models.Approval:
    """Decide a pending approval and resume/terminate the linked job.

    ``approved``/``rejected``/``cancelled``/``expired`` move the row; ``edit``
    invalidates it and opens a new pending approval (a fresh decision is
    required); ``delegate`` opens a pending approval for another approver
    without widening the intent. A model actor may never approve.
    """
    target = _text(decision, "decision", max_len=20)
    if target not in DECISIONS:
        raise BusinessError(
            BAD_INPUT_CODE,
            f"decision must be one of {', '.join(DECISIONS)}",
        )
    actor = _text(decided_by, "decided_by", max_len=200)
    note = str(reason or "").strip()[:2000]
    if target == "edit":
        if edited_intent is None:
            raise BusinessError(BAD_INPUT_CODE, "an edit requires the new intent")
        return edit(
            store,
            tenant_id=tenant_id,
            approval_id=approval_id,
            edited_by=actor,
            intent=edited_intent,
            reason=note,
            idempotency_key=idempotency_key,
            now=now,
        )
    if target == "delegate":
        if delegate_to is None:
            raise BusinessError(BAD_INPUT_CODE, "a delegation requires delegate_to")
        return delegate(
            store,
            tenant_id=tenant_id,
            approval_id=approval_id,
            decided_by=actor,
            delegate_to=delegate_to,
            capabilities=delegated_capabilities,
            reason=note,
            idempotency_key=idempotency_key,
            now=now,
        )
    if target == "approved" and _automation_actor(actor):
        raise BusinessError(
            APPROVAL_AUTOMATION_REFUSED_CODE,
            "a model principal cannot approve its own action; a human must decide",
        )
    moment = time.time() if now is None else float(now)
    status_target = {
        "approved": "approved",
        "rejected": "rejected",
        "cancelled": "cancelled",
        "expired": "expired",
    }[target]
    with store.transaction() as conn:
        row = _row(conn, tenant_id, approval_id)
        if row["status"] != "pending":
            raise BusinessError(
                APPROVAL_NOT_PENDING_CODE,
                f"approval {approval_id!r} is already {row['status']!r}",
            )
        job_id = row["job_id"]
        if job_id is not None:
            job = conn.execute(
                "SELECT * FROM bos_job WHERE tenant_id=? AND job_id=?",
                (tenant_id, job_id),
            ).fetchone()
            if job is None or job["status"] != "waiting_approval":
                raise BusinessError(
                    BAD_STATE_CODE,
                    "the linked job is no longer waiting for this approval"
                    f" (job status={None if job is None else job['status']!r})",
                )
        decided = now_iso(moment)
        conn.execute(
            "UPDATE bos_approval SET status=?, decided_by=?, reason=?,"
            " decided_at=? WHERE tenant_id=? AND approval_id=?",
            (status_target, actor, note, decided, tenant_id, approval_id),
        )
        if job_id is not None:
            from . import jobs

            jobs._transition(
                conn,
                store,
                tenant_id,
                job_id,
                "queued" if status_target == "approved" else "cancelled",
                actor=actor,
                note=f"approval {approval_id} {status_target}: {note}".strip(),
            )
        store._audit(
            conn, tenant_id, row["business_id"], actor, "approval.decide",
            "approval", approval_id, "ok",
            {"decision": status_target, "job_id": job_id, "reason": note},
        )
    return get(store, tenant_id, approval_id)


def edit(
    store: "BusinessStore",
    *,
    tenant_id: str,
    approval_id: str,
    edited_by: str,
    intent: Mapping[str, Any],
    reason: str = "",
    idempotency_key: str | None = None,
    now: float | None = None,
) -> models.Approval:
    """Invalidate an approval and open a new pending one for the edited intent.

    The old row becomes ``cancelled`` (the exact-intent binding is broken by
    design); a new approval must be decided. The linked job keeps waiting for
    the replacement approval rather than being cancelled.
    """
    actor = _text(edited_by, "edited_by", max_len=200)
    if not _is_normalized_intent(intent):
        raise BusinessError(
            BAD_INPUT_CODE, "an edited approval requires the normalized intent"
        )
    note = str(reason or "").strip()[:2000]
    moment = time.time() if now is None else float(now)
    new_payload = redact(dict(intent))
    new_hash = intent_hash(new_payload)
    new_action = _text(intent["action"], "approval action", max_len=200)
    new_policy = _text(intent["policy_revision"], "policy version", max_len=80)
    with store.transaction() as conn:
        row = _row(conn, tenant_id, approval_id)
        if row["status"] != "pending":
            raise BusinessError(
                APPROVAL_NOT_PENDING_CODE,
                f"approval {approval_id!r} is already {row['status']!r}",
            )
        if new_hash == row["request_hash"]:
            raise BusinessError(
                BAD_INPUT_CODE, "an edit must change the approval intent"
            )
        key = (
            _text(idempotency_key, "idempotency key", max_len=300)
            if idempotency_key is not None
            else f"{row['idempotency_key']}:edit:{new_hash[:16]}"
        )
        existing = conn.execute(
            "SELECT * FROM bos_approval WHERE tenant_id=? AND idempotency_key=?",
            (tenant_id, key),
        ).fetchone()
        if existing is not None:
            if existing["request_hash"] != new_hash:
                raise BusinessError(
                    IDEMPOTENCY_CONFLICT_CODE,
                    f"idempotency key {key!r} was already used for a different"
                    " edited intent",
                )
            return models.Approval.from_row(existing)
        decided = now_iso(moment)
        conn.execute(
            "UPDATE bos_approval SET status='cancelled', decided_by=?,"
            " reason=?, decided_at=? WHERE tenant_id=? AND approval_id=?",
            (
                actor,
                f"superseded by edit: {note}".strip(),
                decided,
                tenant_id,
                approval_id,
            ),
        )
        new_id_value = new_id("apr")
        _insert_pending(
            conn,
            approval_id=new_id_value,
            tenant_id=tenant_id,
            business_id=row["business_id"],
            job_id=row["job_id"],
            action=new_action,
            requested_by=row["requested_by"],
            reason=f"edited from {approval_id}: {note}".strip(),
            idempotency_key=key,
            request_hash=new_hash,
            policy_version=new_policy,
            payload=new_payload,
            created_at=decided,
            expires_at=row["expires_at"],
        )
        store._audit(
            conn, tenant_id, row["business_id"], actor, "approval.edit",
            "approval", approval_id, "ok",
            {"replacement_id": new_id_value, "job_id": row["job_id"]},
        )
    return get(store, tenant_id, new_id_value)


def delegate(
    store: "BusinessStore",
    *,
    tenant_id: str,
    approval_id: str,
    decided_by: str,
    delegate_to: str,
    intent: Mapping[str, Any] | None = None,
    capabilities: Any = None,
    reason: str = "",
    idempotency_key: str | None = None,
    now: float | None = None,
) -> models.Approval:
    """Delegate a pending approval without widening its scope.

    The replacement approval carries the same exact intent. A changed intent or
    a capability that the original approval did not carry is a typed refusal.
    The original row is closed; the linked job keeps waiting for the delegate.
    """
    actor = _text(decided_by, "decided_by", max_len=200)
    target_actor = _text(delegate_to, "delegate_to", max_len=200)
    if target_actor == actor:
        raise BusinessError(BAD_INPUT_CODE, "cannot delegate an approval to yourself")
    note = str(reason or "").strip()[:2000]
    moment = time.time() if now is None else float(now)
    with store.transaction() as conn:
        row = _row(conn, tenant_id, approval_id)
        if row["status"] != "pending":
            raise BusinessError(
                APPROVAL_NOT_PENDING_CODE,
                f"approval {approval_id!r} is already {row['status']!r}",
            )
        original = models.Approval.from_row(row)
        base_payload = (
            redact(dict(intent)) if intent is not None else dict(original.payload)
        )
        base_hash = intent_hash(base_payload)
        normalized_original = _is_normalized_intent(original.payload)
        original_hash = (
            row["request_hash"]
            if normalized_original
            else intent_hash(original.payload)
        )
        if base_hash != original_hash:
            raise BusinessError(
                APPROVAL_DELEGATION_WIDENED_CODE,
                "a delegated approval cannot change the approved intent",
            )
        original_caps = _intent_capabilities(original.payload)
        new_caps = (
            tuple(str(item) for item in capabilities)
            if capabilities is not None
            else original_caps
        )
        if not set(new_caps).issubset(set(original_caps)):
            raise BusinessError(
                APPROVAL_DELEGATION_WIDENED_CODE,
                "a delegated approval cannot add capabilities",
            )
        key = (
            _text(idempotency_key, "idempotency key", max_len=300)
            if idempotency_key is not None
            else f"{row['idempotency_key']}:delegate:{fingerprint([target_actor, base_hash])[:16]}"
        )
        existing = conn.execute(
            "SELECT * FROM bos_approval WHERE tenant_id=? AND idempotency_key=?",
            (tenant_id, key),
        ).fetchone()
        if existing is not None:
            if existing["request_hash"] != original_hash:
                raise BusinessError(
                    IDEMPOTENCY_CONFLICT_CODE,
                    f"idempotency key {key!r} was already used for a different"
                    " delegation",
                )
            return models.Approval.from_row(existing)
        decided = now_iso(moment)
        conn.execute(
            "UPDATE bos_approval SET status='cancelled', decided_by=?,"
            " reason=?, decided_at=? WHERE tenant_id=? AND approval_id=?",
            (
                actor,
                f"delegated to {target_actor}: {note}".strip(),
                decided,
                tenant_id,
                approval_id,
            ),
        )
        new_id_value = new_id("apr")
        _insert_pending(
            conn,
            approval_id=new_id_value,
            tenant_id=tenant_id,
            business_id=row["business_id"],
            job_id=row["job_id"],
            action=row["action"],
            requested_by=row["requested_by"],
            reason=f"delegated from {approval_id} to {target_actor}: {note}".strip(),
            idempotency_key=key,
            request_hash=original_hash,
            policy_version=row["policy_version"],
            payload=base_payload,
            created_at=decided,
            expires_at=row["expires_at"],
        )
        store._audit(
            conn, tenant_id, row["business_id"], actor, "approval.delegate",
            "approval", approval_id, "ok",
            {
                "replacement_id": new_id_value,
                "delegate_to": target_actor,
                "job_id": row["job_id"],
            },
        )
    return get(store, tenant_id, new_id_value)


def verify_execution(
    store: "BusinessStore",
    tenant_id: str,
    approval_id: str,
    intent: Mapping[str, Any],
    *,
    now: float | None = None,
    current_policy_version: str | None = None,
) -> models.Approval:
    """Re-authorize one execution against the approval that was granted.

    Refuses unless the approval is approved, unexpired, in the same tenant and
    business, for the same action and exact intent hash, under the same policy
    revision, and not revoked. The approval row is returned on success.
    """
    if not _is_normalized_intent(intent):
        raise BusinessError(
            BAD_INPUT_CODE,
            "execution requires the same normalized intent that was approved",
        )
    moment = time.time() if now is None else float(now)
    with store.read() as conn:
        row = _row(conn, _text(tenant_id, "tenant_id"), _text(approval_id, "approval_id"))
    status = row["status"]
    if status == "pending":
        raise BusinessError(
            APPROVAL_REQUIRED_CODE, f"approval {approval_id!r} is still pending"
        )
    if status == "expired":
        raise BusinessError(
            APPROVAL_EXPIRED_CODE, f"approval {approval_id!r} has expired"
        )
    if status in ("cancelled", "rejected"):
        raise BusinessError(
            APPROVAL_REVOKED_CODE,
            f"approval {approval_id!r} is {status!r} and cannot authorize execution",
        )
    if status != "approved":
        raise BusinessError(
            APPROVAL_NOT_PENDING_CODE,
            f"approval {approval_id!r} is {status!r}",
        )
    deadline = row["expires_at"]
    if deadline is not None and moment >= float(deadline):
        raise BusinessError(
            APPROVAL_EXPIRED_CODE, f"approval {approval_id!r} has expired"
        )
    business = _intent_business(intent)
    if business is None:
        raise BusinessError(
            APPROVAL_INTENT_MISMATCH_CODE,
            "the execution intent does not name a business",
        )
    if business != row["business_id"]:
        raise BusinessError(
            APPROVAL_INTENT_MISMATCH_CODE,
            "the execution intent names a different business",
        )
    if str(intent["action"]) != row["action"]:
        raise BusinessError(
            APPROVAL_INTENT_MISMATCH_CODE,
            "the execution intent names a different action",
        )
    if str(intent["policy_revision"]) != row["policy_version"]:
        raise BusinessError(
            APPROVAL_INTENT_MISMATCH_CODE,
            "the execution intent names a different policy revision",
        )
    if (
        current_policy_version is not None
        and str(current_policy_version) != row["policy_version"]
    ):
        raise BusinessError(
            APPROVAL_POLICY_STALE_CODE,
            f"policy revision is now {current_policy_version!r}; the approval was"
            f" granted under {row['policy_version']!r}",
        )
    if intent_hash(redact(dict(intent))) != row["request_hash"]:
        raise BusinessError(
            APPROVAL_INTENT_MISMATCH_CODE,
            "the execution intent does not match the approved intent hash",
        )
    return models.Approval.from_row(row)


def execution_authorized(
    store: "BusinessStore",
    tenant_id: str,
    approval_id: str,
    *,
    intent: Mapping[str, Any],
    policy_version: str,
    now: float | None = None,
) -> models.Approval:
    """Stable execution-time seam: re-authorize or raise a typed refusal."""
    return verify_execution(
        store,
        tenant_id,
        approval_id,
        intent,
        now=now,
        current_policy_version=_text(
            policy_version, "policy_version", max_len=80
        ),
    )


def expire(
    store: "BusinessStore",
    *,
    now: float | None = None,
    tenant_id: str | None = None,
) -> list[models.Approval]:
    """Expire pending approvals whose deadline has passed.

    An expired approval cancels its linked job: the window for the action
    closed, so continuing would be a decision nobody made.
    """
    moment = time.time() if now is None else float(now)
    ids: list[tuple[str, str]] = []
    with store.read() as conn:
        query = (
            "SELECT tenant_id, approval_id FROM bos_approval"
            " WHERE status='pending' AND expires_at IS NOT NULL AND expires_at <= ?"
        )
        params: list[Any] = [moment]
        if tenant_id is not None:
            query += " AND tenant_id=?"
            params.append(tenant_id)
        query += " ORDER BY created_at, approval_id"
        ids = [(row["tenant_id"], row["approval_id"]) for row in conn.execute(query, params)]
    expired: list[models.Approval] = []
    for scope, approval_id in ids:
        with store.transaction() as conn:
            row = _row(conn, scope, approval_id)
            if row["status"] != "pending":
                continue
            decided = now_iso()
            conn.execute(
                "UPDATE bos_approval SET status='expired', decided_by='system',"
                " reason=?, decided_at=? WHERE tenant_id=? AND approval_id=?",
                ("approval window expired", decided, scope, approval_id),
            )
            job_id = row["job_id"]
            if job_id is not None:
                from . import jobs

                job = conn.execute(
                    "SELECT * FROM bos_job WHERE tenant_id=? AND job_id=?",
                    (scope, job_id),
                ).fetchone()
                if job is not None and job["status"] == "waiting_approval":
                    jobs._transition(
                        conn, store, scope, job_id, "cancelled",
                        actor="system", note=f"approval {approval_id} expired",
                    )
            store._audit(
                conn, scope, row["business_id"], "system", "approval.expire",
                "approval", approval_id, "ok", {"job_id": job_id},
            )
        expired.append(get(store, scope, approval_id))
    return expired
