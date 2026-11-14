"""Agent-to-agent (A2A) message fabric over an injected event log.

This module owns the *contract* of a typed, append-only message fabric. It does
not own persistence: callers inject an :class:`EventLog` (``append``/``read``)
so the integration layer can bind the authoritative Business OS event log once
that lane lands. :class:`InMemoryEventLog` exists only for hermetic tests and is
**not** a product authority — it forgets everything on process exit and has no
durability, retention or tenancy enforcement of its own.

A message is an immutable envelope plus append-only delivery receipts. Delivery
state (``pending`` -> ``delivered`` -> ``acked``, or ``dead_letter``) is *folded*
from those receipts, never written over the message record. That keeps the log
append-only: wiring the authoritative log must not require an UPDATE path.

Enforced here:

* the recipient (and only the recipient) can receive/ack; the sender can read
  what it wrote, and an injected ``observer_check`` may grant a third party;
* a sender may not spoof another principal (``authenticated_principal`` or
  ``verify_sender``);
* a delegation may not grant a capability the delegator does not hold
  (``can_delegate`` is required before a non-empty subset is accepted);
* a duplicate idempotency key is deduped, a conflicting one is refused;
* a reused ``replay_nonce`` is refused;
* an expired message cannot be sent, received, acked, or used as live work;
* an oversized payload is refused before it reaches the log.
"""
from __future__ import annotations

import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from .errors import (
    A2A_BAD_STATE_CODE,
    A2A_DELEGATION_REFUSED_CODE,
    A2A_IDEMPOTENCY_CONFLICT_CODE,
    A2A_MESSAGE_EXPIRED_CODE,
    A2A_NOT_FOUND_CODE,
    A2A_NOT_RECIPIENT_CODE,
    A2A_PAYLOAD_TOO_LARGE_CODE,
    A2A_REPLAY_REFUSED_CODE,
    A2A_SPOOF_REFUSED_CODE,
    BAD_INPUT_CODE,
    BusinessError,
)
from .ids import fingerprint, new_id
from .util import json_dumps, now_iso

DELIVERY_STATES = ("pending", "delivered", "acked", "dead_letter")
VISIBILITIES = ("direct", "thread", "broadcast")
DEFAULT_MAX_PAYLOAD_BYTES = 65536
MAX_PRINCIPAL_LEN = 200
MAX_KEY_LEN = 300
_STATE_RANK = {"pending": 0, "delivered": 1, "acked": 2, "dead_letter": 3}


class EventLog(Protocol):
    """The seam the authoritative event log must satisfy.

    ``append`` stores one record and returns the stored record with its
    per-thread ``sequence`` assigned by the log (1-based, monotonic). ``read``
    returns records for a tenant (optionally one thread) in append order. The
    implementation must be append-only; delivery state is derived by folding
    receipts, so no in-place UPDATE is required.
    """

    def append(self, record: Mapping[str, Any]) -> Mapping[str, Any]: ...

    def read(
        self, tenant_id: str, *, thread_id: str | None = None
    ) -> Sequence[Mapping[str, Any]]: ...


@dataclass(frozen=True, slots=True)
class A2AMessage:
    message_id: str
    tenant_id: str
    sender_principal: str
    recipient_principal: str
    beneficiary_principal: str | None
    delegator_principal: str | None
    capability_subset: tuple[str, ...]
    task_id: str | None
    correlation_id: str
    causation_id: str | None
    idempotency_key: str
    kind: str
    payload: Any
    visibility: str
    sent_at: str
    expires_at: float | None
    delivery_state: str
    replay_nonce: str | None
    thread_id: str
    sequence: int = 0
    sent_at_epoch: float = 0.0


class InMemoryEventLog:
    """Hermetic event log used by tests. **Not a product authority.**

    It holds records in process memory, assigns a per-tenant/thread monotonic
    ``sequence``, and returns copies. Nothing survives the process, no lock file
    or backup exists, and it performs no tenancy checks beyond the read filter.
    """

    def __init__(self) -> None:
        self._records: list[dict[str, Any]] = []

    def append(self, record: Mapping[str, Any]) -> Mapping[str, Any]:
        stored = dict(record)
        stored.setdefault("event_id", new_id("evt"))
        tenant = stored["tenant_id"]
        thread = stored.get("thread_id")
        highest = 0
        for existing in self._records:
            if existing.get("tenant_id") != tenant:
                continue
            if thread is not None and existing.get("thread_id") != thread:
                continue
            highest = max(highest, int(existing.get("sequence", 0)))
        stored["sequence"] = highest + 1
        stored["appended_at"] = now_iso()
        self._records.append(stored)
        return dict(stored)

    def read(
        self, tenant_id: str, *, thread_id: str | None = None
    ) -> Sequence[Mapping[str, Any]]:
        return [
            dict(record)
            for record in self._records
            if record.get("tenant_id") == tenant_id
            and (thread_id is None or record.get("thread_id") == thread_id)
        ]


def _text(value: Any, label: str, *, max_len: int = MAX_PRINCIPAL_LEN) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BusinessError(BAD_INPUT_CODE, f"{label} must be a non-empty string")
    clean = value.strip()
    if len(clean) > max_len:
        raise BusinessError(
            BAD_INPUT_CODE, f"{label} is longer than {max_len} characters"
        )
    return clean


def _optional_text(value: Any, label: str, *, max_len: int = MAX_PRINCIPAL_LEN) -> str | None:
    if value is None:
        return None
    return _text(value, label, max_len=max_len)


def _kind(value: Any) -> str:
    clean = _text(value, "message kind", max_len=64)
    if not clean[0].isalpha() or not all(
        ch.isalnum() or ch in "_.-" for ch in clean
    ):
        raise BusinessError(
            BAD_INPUT_CODE,
            "message kind must start with a letter and contain only"
            " letters, digits, '_', '.' or '-'",
        )
    return clean


def _capabilities(value: Iterable[str] | None) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        raise BusinessError(
            BAD_INPUT_CODE, "capability_subset must be an iterable of names"
        )
    seen: list[str] = []
    for item in value:
        clean = _text(item, "capability", max_len=200)
        if clean not in seen:
            seen.append(clean)
    return tuple(seen)


def _payload_bytes(payload: Any) -> int:
    try:
        encoded = json_dumps(payload)
    except (TypeError, ValueError) as exc:
        raise BusinessError(BAD_INPUT_CODE, f"payload is not JSON: {exc}") from exc
    return len(encoded.encode("utf-8"))


def _sort(records: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    return sorted(records, key=lambda item: int(item.get("sequence", 0)))


def _fold(records: Iterable[Mapping[str, Any]]) -> list[A2AMessage]:
    folded: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for record in _sort(records):
        record_type = record.get("record_type")
        message_id = record.get("message_id")
        if record_type == "message" and message_id:
            folded[message_id] = {
                "message_id": message_id,
                "tenant_id": record["tenant_id"],
                "sender_principal": record["sender_principal"],
                "recipient_principal": record["recipient_principal"],
                "beneficiary_principal": record.get("beneficiary_principal"),
                "delegator_principal": record.get("delegator_principal"),
                "capability_subset": tuple(record.get("capability_subset") or ()),
                "task_id": record.get("task_id"),
                "correlation_id": record["correlation_id"],
                "causation_id": record.get("causation_id"),
                "idempotency_key": record["idempotency_key"],
                "kind": record["kind"],
                "payload": record.get("payload"),
                "visibility": record.get("visibility", "direct"),
                "sent_at": record["sent_at"],
                "expires_at": record.get("expires_at"),
                "delivery_state": "pending",
                "replay_nonce": record.get("replay_nonce"),
                "thread_id": record["thread_id"],
                "sequence": int(record.get("sequence", 0)),
                "sent_at_epoch": float(record.get("sent_at_epoch", 0.0)),
            }
            order.append(message_id)
        elif record_type == "receipt" and message_id in folded:
            target = folded[message_id]
            state = str(record.get("delivery_state") or "")
            if state in _STATE_RANK and _STATE_RANK[state] >= _STATE_RANK[
                target["delivery_state"]
            ]:
                target["delivery_state"] = state
    return [A2AMessage(**folded[message_id]) for message_id in order]


def _messages(
    log: EventLog, tenant_id: str, *, thread_id: str | None = None
) -> list[A2AMessage]:
    return _fold(log.read(tenant_id, thread_id=thread_id))


def _find(messages: Iterable[A2AMessage], message_id: str) -> A2AMessage:
    for message in messages:
        if message.message_id == message_id:
            return message
    raise BusinessError(
        A2A_NOT_FOUND_CODE, f"no message {message_id!r} in this tenant"
    )


def _can_read(
    message: A2AMessage,
    principal: str,
    observer_check: Callable[[str, A2AMessage], bool] | None,
) -> bool:
    if principal in (message.sender_principal, message.recipient_principal):
        return True
    if message.visibility == "broadcast":
        return True
    if observer_check is not None and observer_check(principal, message):
        return True
    return False


def _expired(message: A2AMessage, moment: float) -> bool:
    return message.expires_at is not None and moment >= float(message.expires_at)


def _same_content(
    message: A2AMessage,
    *,
    sender: str,
    recipient: str,
    beneficiary: str | None,
    delegator: str | None,
    capabilities: tuple[str, ...],
    task_id: str | None,
    kind: str,
    payload: Any,
    visibility: str,
    expires_at: float | None,
) -> bool:
    return (
        message.sender_principal == sender
        and message.recipient_principal == recipient
        and message.beneficiary_principal == beneficiary
        and message.delegator_principal == delegator
        and message.capability_subset == capabilities
        and message.task_id == task_id
        and message.kind == kind
        and fingerprint(message.payload) == fingerprint(payload)
        and message.visibility == visibility
        and message.expires_at == expires_at
    )


def _receipt(
    log: EventLog,
    message: A2AMessage,
    *,
    delivery_state: str,
    actor: str,
    now: float,
    reason: str = "",
) -> A2AMessage:
    log.append(
        {
            "record_type": "receipt",
            "tenant_id": message.tenant_id,
            "thread_id": message.thread_id,
            "message_id": message.message_id,
            "delivery_state": delivery_state,
            "actor": actor,
            "reason": reason,
            "at": now_iso(now),
            "at_epoch": float(now),
        }
    )
    refreshed = _messages(log, message.tenant_id, thread_id=message.thread_id)
    return _find(refreshed, message.message_id)


def send(
    log: EventLog,
    *,
    tenant_id: str,
    sender_principal: str,
    recipient_principal: str,
    payload: Any,
    idempotency_key: str,
    kind: str = "task",
    capability_subset: Iterable[str] | None = None,
    task_id: str | None = None,
    correlation_id: str | None = None,
    causation_id: str | None = None,
    beneficiary_principal: str | None = None,
    delegator_principal: str | None = None,
    visibility: str = "direct",
    sent_at: float | None = None,
    expires_at: float | None = None,
    replay_nonce: str | None = None,
    thread_id: str | None = None,
    authenticated_principal: str | None = None,
    verify_sender: Callable[[str], bool] | None = None,
    can_delegate: Callable[[str, str], bool] | None = None,
    max_payload_bytes: int = DEFAULT_MAX_PAYLOAD_BYTES,
    now: float | None = None,
) -> tuple[A2AMessage, bool]:
    """Append one typed message. Returns ``(message, created)``.

    A replay of the same idempotency key with identical content returns the
    original message and ``created=False``; the same key with different content
    is a typed conflict.
    """
    tenant = _text(tenant_id, "tenant_id")
    sender = _text(sender_principal, "sender_principal")
    recipient = _text(recipient_principal, "recipient_principal")
    if sender == recipient:
        raise BusinessError(
            BAD_INPUT_CODE, "sender and recipient must be different principals"
        )
    key = _text(idempotency_key, "idempotency key", max_len=MAX_KEY_LEN)
    message_kind = _kind(kind)
    capabilities = _capabilities(capability_subset)
    beneficiary = _optional_text(beneficiary_principal, "beneficiary_principal")
    delegator = _optional_text(delegator_principal, "delegator_principal")
    task = _optional_text(task_id, "task_id")
    causation = _optional_text(causation_id, "causation_id")
    nonce = _optional_text(replay_nonce, "replay_nonce", max_len=200)
    if visibility not in VISIBILITIES:
        raise BusinessError(
            BAD_INPUT_CODE, f"visibility must be one of {', '.join(VISIBILITIES)}"
        )
    moment = time.time() if now is None else float(now)
    correlation = _optional_text(correlation_id, "correlation_id") or new_id("cor")
    thread = _optional_text(thread_id, "thread_id") or correlation
    if _payload_bytes(payload) > int(max_payload_bytes):
        raise BusinessError(
            A2A_PAYLOAD_TOO_LARGE_CODE,
            f"payload exceeds the {int(max_payload_bytes)} byte limit",
        )
    if authenticated_principal is not None:
        if _text(authenticated_principal, "authenticated_principal") != sender:
            raise BusinessError(
                A2A_SPOOF_REFUSED_CODE,
                "the authenticated principal does not match the declared sender",
            )
    if verify_sender is not None and not verify_sender(sender):
        raise BusinessError(
            A2A_SPOOF_REFUSED_CODE,
            f"sender {sender!r} could not be authenticated",
        )
    if capabilities:
        if can_delegate is None:
            raise BusinessError(
                A2A_DELEGATION_REFUSED_CODE,
                "a capability subset requires a delegation authority checker",
            )
        for capability in capabilities:
            if not can_delegate(sender, capability):
                raise BusinessError(
                    A2A_DELEGATION_REFUSED_CODE,
                    f"{sender!r} may not delegate capability {capability!r}",
                )
    if expires_at is not None and float(expires_at) <= moment:
        raise BusinessError(
            A2A_MESSAGE_EXPIRED_CODE, "the message expiry is not in the future"
        )

    existing = _messages(log, tenant)
    for message in existing:
        if message.idempotency_key != key:
            continue
        if _same_content(
            message,
            sender=sender,
            recipient=recipient,
            beneficiary=beneficiary,
            delegator=delegator,
            capabilities=capabilities,
            task_id=task,
            kind=message_kind,
            payload=payload,
            visibility=visibility,
            expires_at=expires_at,
        ):
            return message, False
        raise BusinessError(
            A2A_IDEMPOTENCY_CONFLICT_CODE,
            f"idempotency key {key!r} was already used for a different message",
        )
    if nonce is not None and any(
        message.sender_principal == sender and message.replay_nonce == nonce
        for message in existing
    ):
        raise BusinessError(
            A2A_REPLAY_REFUSED_CODE,
            f"replay nonce {nonce!r} was already used by {sender!r}",
        )

    message_id = new_id("msg")
    record = {
        "record_type": "message",
        "message_id": message_id,
        "tenant_id": tenant,
        "thread_id": thread,
        "sender_principal": sender,
        "recipient_principal": recipient,
        "beneficiary_principal": beneficiary,
        "delegator_principal": delegator,
        "capability_subset": list(capabilities),
        "task_id": task,
        "correlation_id": correlation,
        "causation_id": causation,
        "idempotency_key": key,
        "kind": message_kind,
        "payload": payload,
        "visibility": visibility,
        "sent_at": now_iso(moment),
        "sent_at_epoch": moment,
        "expires_at": None if expires_at is None else float(expires_at),
        "replay_nonce": nonce,
    }
    stored = log.append(record)
    folded = _fold([stored])
    if not folded:
        raise BusinessError(
            A2A_BAD_STATE_CODE, "the event log did not return the stored message"
        )
    return folded[0], True


def receive(
    log: EventLog,
    *,
    tenant_id: str,
    principal: str,
    thread_id: str | None = None,
    message_id: str | None = None,
    observer_check: Callable[[str, A2AMessage], bool] | None = None,
    now: float | None = None,
) -> list[A2AMessage] | A2AMessage:
    """Deliver visible pending messages to ``principal``.

    With ``message_id`` one message is returned (typed refusal if invisible or
    expired). Without it, the list of visible pending/delivered messages is
    returned and each pending message is marked ``delivered``.
    """
    tenant = _text(tenant_id, "tenant_id")
    reader = _text(principal, "principal")
    moment = time.time() if now is None else float(now)
    messages = _messages(log, tenant, thread_id=thread_id)
    if message_id is not None:
        target = _find(messages, _text(message_id, "message_id"))
        if not _can_read(target, reader, observer_check):
            raise BusinessError(
                A2A_NOT_RECIPIENT_CODE,
                f"{reader!r} is not a recipient of message {target.message_id!r}",
            )
        if _expired(target, moment):
            raise BusinessError(
                A2A_MESSAGE_EXPIRED_CODE,
                f"message {target.message_id!r} has expired",
            )
        if target.delivery_state == "dead_letter":
            raise BusinessError(
                A2A_BAD_STATE_CODE,
                f"message {target.message_id!r} is dead-lettered",
            )
        if target.delivery_state == "pending":
            target = _receipt(
                log, target,
                delivery_state="delivered", actor=reader, now=moment,
            )
        return target

    delivered: list[A2AMessage] = []
    for message in messages:
        if not _can_read(message, reader, observer_check):
            continue
        if _expired(message, moment):
            continue
        if message.delivery_state not in ("pending", "delivered"):
            continue
        if message.delivery_state == "pending":
            message = _receipt(
                log, message,
                delivery_state="delivered", actor=reader, now=moment,
            )
        delivered.append(message)
    return delivered


def ack(
    log: EventLog,
    *,
    tenant_id: str,
    principal: str,
    message_id: str,
    now: float | None = None,
) -> A2AMessage:
    """Acknowledge a delivered message. Only the recipient may ack; repeat is a no-op."""
    tenant = _text(tenant_id, "tenant_id")
    actor = _text(principal, "principal")
    moment = time.time() if now is None else float(now)
    messages = _messages(log, tenant)
    message = _find(messages, _text(message_id, "message_id"))
    if actor != message.recipient_principal:
        raise BusinessError(
            A2A_NOT_RECIPIENT_CODE,
            f"only the recipient may acknowledge message {message.message_id!r}",
        )
    if message.delivery_state == "dead_letter":
        raise BusinessError(
            A2A_BAD_STATE_CODE,
            f"message {message.message_id!r} is dead-lettered",
        )
    if _expired(message, moment):
        raise BusinessError(
            A2A_MESSAGE_EXPIRED_CODE,
            f"message {message.message_id!r} has expired",
        )
    if message.delivery_state == "acked":
        return message
    return _receipt(
        log, message, delivery_state="acked", actor=actor, now=moment
    )


def list_thread(
    log: EventLog,
    *,
    tenant_id: str,
    principal: str,
    thread_id: str,
    observer_check: Callable[[str, A2AMessage], bool] | None = None,
    include_expired: bool = False,
    now: float | None = None,
) -> list[A2AMessage]:
    """Every message in a thread visible to ``principal``, in sequence order."""
    tenant = _text(tenant_id, "tenant_id")
    reader = _text(principal, "principal")
    thread = _text(thread_id, "thread_id")
    moment = time.time() if now is None else float(now)
    visible = [
        message
        for message in _messages(log, tenant, thread_id=thread)
        if _can_read(message, reader, observer_check)
        and (include_expired or not _expired(message, moment))
    ]
    return visible


def replay(
    log: EventLog,
    *,
    tenant_id: str,
    principal: str,
    thread_id: str | None = None,
    since_sequence: int = 0,
    observer_check: Callable[[str, A2AMessage], bool] | None = None,
    include_expired: bool = True,
    now: float | None = None,
) -> list[A2AMessage]:
    """Rebuild visible history after ``since_sequence`` (authoritative replay)."""
    tenant = _text(tenant_id, "tenant_id")
    reader = _text(principal, "principal")
    moment = time.time() if now is None else float(now)
    messages = _messages(log, tenant, thread_id=thread_id)
    return [
        message
        for message in messages
        if int(since_sequence) < message.sequence
        and _can_read(message, reader, observer_check)
        and (include_expired or not _expired(message, moment))
    ]


def sweep(
    log: EventLog,
    *,
    tenant_id: str,
    now: float | None = None,
    retention_seconds: float | None = None,
) -> dict[str, Any]:
    """Dead-letter expired messages and messages past the retention window.

    Deletion is not represented by removing a log record: the message is
    retained and a ``dead_letter`` receipt records why it is no longer live.
    """
    tenant = _text(tenant_id, "tenant_id")
    moment = time.time() if now is None else float(now)
    expired: list[str] = []
    retained: list[str] = []
    for message in _messages(log, tenant):
        if message.delivery_state == "dead_letter":
            continue
        if _expired(message, moment):
            _receipt(
                log, message,
                delivery_state="dead_letter", actor="system", now=moment,
                reason="expired",
            )
            expired.append(message.message_id)
        elif (
            retention_seconds is not None
            and message.sent_at_epoch
            and moment - message.sent_at_epoch >= float(retention_seconds)
        ):
            _receipt(
                log, message,
                delivery_state="dead_letter", actor="system", now=moment,
                reason="retention",
            )
            retained.append(message.message_id)
    return {
        "tenant_id": tenant,
        "expired": expired,
        "retained": retained,
        "dead_lettered": expired + retained,
    }


__all__ = [
    "A2AMessage",
    "DELIVERY_STATES",
    "DEFAULT_MAX_PAYLOAD_BYTES",
    "EventLog",
    "InMemoryEventLog",
    "VISIBILITIES",
    "ack",
    "list_thread",
    "receive",
    "replay",
    "send",
    "sweep",
]
