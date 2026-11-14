"""P1-05 — A2A / message fabric over an injected event log.

Hermetic: the in-memory log is a test double, not a product authority. No
network, no database, no live service. These tests pin the contract the
platform lane will wire to the authoritative event log: typed envelopes,
per-thread ordering, read-your-writes, delivery/ack, recipient ACL, spoof and
replay refusal, delegation subset, payload bounds and dead-letter retention.
"""
from __future__ import annotations

import pytest

from voicebridge.business import a2a
from voicebridge.business.errors import BusinessError

TENANT = "tenant-alpha"
OWNER = "owner"
AGENT_A = "agent:a"
AGENT_B = "agent:b"
AGENT_C = "agent:c"


def _log() -> a2a.InMemoryEventLog:
    return a2a.InMemoryEventLog()


def _send(log, **overrides):
    args = dict(
        tenant_id=TENANT,
        sender_principal=OWNER,
        recipient_principal=AGENT_A,
        payload={"task": "summarise"},
        idempotency_key="msg-1",
    )
    args.update(overrides)
    return a2a.send(log, **args)


def _code(exc: pytest.ExceptionInfo[BusinessError]) -> str:
    return exc.value.code


# --- delivery lifecycle ---------------------------------------------------
def test_owner_to_agent_round_trip_and_ack():
    log = _log()
    message, created = _send(log, correlation_id="thread-1", now=100.0)

    assert created is True
    assert message.delivery_state == "pending"
    assert message.sequence == 1
    assert message.thread_id == "thread-1"
    assert message.sent_at.endswith("Z")

    # Read-your-writes: the sender can always read what it sent.
    own = a2a.list_thread(log, tenant_id=TENANT, principal=OWNER, thread_id="thread-1")
    assert [item.message_id for item in own] == [message.message_id]

    delivered = a2a.receive(log, tenant_id=TENANT, principal=AGENT_A, now=101.0)
    assert [item.message_id for item in delivered] == [message.message_id]
    assert delivered[0].delivery_state == "delivered"

    acked = a2a.ack(
        log, tenant_id=TENANT, principal=AGENT_A,
        message_id=message.message_id, now=102.0,
    )
    assert acked.delivery_state == "acked"

    # A repeat ack is a no-op, not a second effect.
    assert a2a.ack(
        log, tenant_id=TENANT, principal=AGENT_A,
        message_id=message.message_id, now=103.0,
    ).delivery_state == "acked"


def test_agent_to_agent_delegation_subset_enforced():
    log = _log()
    allowed = {("agent:a", "tool:email.send")}

    def can_delegate(principal: str, capability: str) -> bool:
        return (principal, capability) in allowed

    message, _ = a2a.send(
        log,
        tenant_id=TENANT,
        sender_principal=AGENT_A,
        recipient_principal=AGENT_B,
        payload={"task": "draft reply"},
        idempotency_key="delegate-1",
        kind="handoff",
        capability_subset=["tool:email.send"],
        can_delegate=can_delegate,
    )
    assert message.capability_subset == ("tool:email.send",)
    assert message.beneficiary_principal is None

    with pytest.raises(BusinessError) as exc:
        a2a.send(
            log,
            tenant_id=TENANT,
            sender_principal=AGENT_A,
            recipient_principal=AGENT_B,
            payload={"task": "pay"},
            idempotency_key="delegate-2",
            capability_subset=["tool:money.payout"],
            can_delegate=can_delegate,
        )
    assert _code(exc) == "A2A_DELEGATION_REFUSED"

    # A non-empty subset with no authority checker fails closed.
    with pytest.raises(BusinessError) as exc:
        a2a.send(
            log,
            tenant_id=TENANT,
            sender_principal=AGENT_A,
            recipient_principal=AGENT_B,
            payload={"task": "draft reply"},
            idempotency_key="delegate-3",
            capability_subset=["tool:email.send"],
        )
    assert _code(exc) == "A2A_DELEGATION_REFUSED"


def test_ordering_replay_and_read_your_writes():
    log = _log()
    first, _ = _send(log, idempotency_key="k1", correlation_id="thread", payload={"n": 1})
    second, _ = _send(log, idempotency_key="k2", correlation_id="thread", payload={"n": 2})
    third, _ = _send(log, idempotency_key="k3", correlation_id="thread", payload={"n": 3})

    assert [first.sequence, second.sequence, third.sequence] == [1, 2, 3]
    thread = a2a.list_thread(
        log, tenant_id=TENANT, principal=OWNER, thread_id="thread"
    )
    assert [item.message_id for item in thread] == [
        first.message_id, second.message_id, third.message_id,
    ]

    replayed = a2a.replay(
        log,
        tenant_id=TENANT,
        principal=AGENT_A,
        thread_id="thread",
        since_sequence=first.sequence,
    )
    assert [item.message_id for item in replayed] == [
        second.message_id, third.message_id,
    ]


# --- hostile input --------------------------------------------------------
def test_spoofed_sender_refused():
    log = _log()
    with pytest.raises(BusinessError) as exc:
        _send(log, sender_principal=AGENT_B, authenticated_principal=AGENT_A)
    assert _code(exc) == "A2A_SPOOF_REFUSED"

    with pytest.raises(BusinessError) as exc:
        _send(log, sender_principal=AGENT_B, verify_sender=lambda principal: False)
    assert _code(exc) == "A2A_SPOOF_REFUSED"

    assert a2a.list_thread(
        log, tenant_id=TENANT, principal=OWNER, thread_id="thread"
    ) == []


def test_duplicate_idempotency_key_deduped_and_conflict_refused():
    log = _log()
    first, created = _send(log, idempotency_key="dup", payload={"n": 1})
    assert created is True

    replay, created_again = _send(log, idempotency_key="dup", payload={"n": 1})
    assert created_again is False
    assert replay.message_id == first.message_id

    with pytest.raises(BusinessError) as exc:
        _send(log, idempotency_key="dup", payload={"n": 2})
    assert _code(exc) == "A2A_IDEMPOTENCY_CONFLICT"


def test_replayed_nonce_refused():
    log = _log()
    _send(log, idempotency_key="nonce-1", replay_nonce="nonce-abc")
    with pytest.raises(BusinessError) as exc:
        _send(log, idempotency_key="nonce-2", replay_nonce="nonce-abc")
    assert _code(exc) == "A2A_REPLAY_REFUSED"

    # A different sender may use the same nonce: nonces are per principal.
    a2a.send(
        log,
        tenant_id=TENANT,
        sender_principal=AGENT_B,
        recipient_principal=OWNER,
        payload={"reply": True},
        idempotency_key="nonce-3",
        replay_nonce="nonce-abc",
    )


def test_expired_message_refused_and_dead_lettered():
    log = _log()
    with pytest.raises(BusinessError) as exc:
        _send(log, idempotency_key="past", expires_at=50.0, now=100.0)
    assert _code(exc) == "A2A_MESSAGE_EXPIRED"

    message, _ = _send(log, idempotency_key="live", expires_at=200.0, now=100.0)
    with pytest.raises(BusinessError) as exc:
        a2a.ack(
            log, tenant_id=TENANT, principal=AGENT_A,
            message_id=message.message_id, now=201.0,
        )
    assert _code(exc) == "A2A_MESSAGE_EXPIRED"

    swept = a2a.sweep(log, tenant_id=TENANT, now=201.0)
    assert message.message_id in swept["expired"]
    assert swept["dead_lettered"] == [message.message_id]

    assert a2a.receive(log, tenant_id=TENANT, principal=AGENT_A, now=201.0) == []
    with pytest.raises(BusinessError) as exc:
        a2a.ack(
            log, tenant_id=TENANT, principal=AGENT_A,
            message_id=message.message_id, now=201.0,
        )
    assert _code(exc) == "A2A_BAD_STATE"


def test_oversized_payload_refused():
    log = _log()
    with pytest.raises(BusinessError) as exc:
        _send(log, payload={"blob": "x" * 70000})
    assert _code(exc) == "A2A_PAYLOAD_TOO_LARGE"

    # The bound is explicit and overridable.
    a2a.send(
        log,
        tenant_id=TENANT,
        sender_principal=OWNER,
        recipient_principal=AGENT_A,
        payload={"blob": "x" * 1000},
        idempotency_key="small",
        max_payload_bytes=2048,
    )


# --- access control -------------------------------------------------------
def test_recipient_acl_and_authorized_observer():
    log = _log()
    message, _ = _send(log, correlation_id="thread", idempotency_key="acl")

    assert a2a.list_thread(
        log, tenant_id=TENANT, principal=AGENT_C, thread_id="thread"
    ) == []
    with pytest.raises(BusinessError) as exc:
        a2a.receive(
            log, tenant_id=TENANT, principal=AGENT_C,
            message_id=message.message_id,
        )
    assert _code(exc) == "A2A_NOT_RECIPIENT"

    with pytest.raises(BusinessError) as exc:
        a2a.ack(
            log, tenant_id=TENANT, principal=OWNER,
            message_id=message.message_id,
        )
    assert _code(exc) == "A2A_NOT_RECIPIENT"

    observer = "agent:observer"
    seen = a2a.receive(
        log,
        tenant_id=TENANT,
        principal=observer,
        message_id=message.message_id,
        observer_check=lambda principal, _message: principal == observer,
    )
    assert isinstance(seen, a2a.A2AMessage)
    assert seen.message_id == message.message_id


def test_retention_dead_letters_old_messages():
    log = _log()
    message, _ = _send(log, idempotency_key="old", now=100.0)

    swept = a2a.sweep(log, tenant_id=TENANT, now=110.0, retention_seconds=5.0)
    assert message.message_id in swept["retained"]

    history = a2a.replay(
        log,
        tenant_id=TENANT,
        principal=AGENT_A,
        include_expired=True,
    )
    assert history[0].delivery_state == "dead_letter"


def test_broadcast_visibility_is_tenant_readable():
    log = _log()
    message, _ = _send(log, idempotency_key="bcast", visibility="broadcast")
    seen = a2a.list_thread(
        log, tenant_id=TENANT, principal=AGENT_C, thread_id=message.thread_id
    )
    assert [item.message_id for item in seen] == [message.message_id]


def test_unknown_message_is_typed_not_found():
    log = _log()
    with pytest.raises(BusinessError) as exc:
        a2a.receive(
            log, tenant_id=TENANT, principal=AGENT_A, message_id="msg_missing"
        )
    assert _code(exc) == "A2A_NOT_FOUND"
