"""P4-02 — owner/agent and room/agent-to-agent threads over the A2A log.

Hermetic: the in-memory event log is a test double, not a product authority.
These tests pin ordering/read-your-writes, idempotent post/reply, offline
replay, participant ACLs, mention authorization, tenant isolation, bounded
bodies and the no-authority-transfer rule.
"""
from __future__ import annotations

import pytest

from voicebridge.business import a2a, threads
from voicebridge.business.errors import BusinessError

TENANT = "tenant-alpha"
OTHER_TENANT = "tenant-beta"
OWNER = "owner"
AGENT_A = "agent:a"
AGENT_B = "agent:b"
AGENT_C = "agent:c"


def _log() -> a2a.InMemoryEventLog:
    return a2a.InMemoryEventLog()


def _open(log, *, kind="owner_agent", participants=(OWNER, AGENT_A), **overrides):
    args = dict(
        tenant_id=TENANT,
        kind=kind,
        participants=participants,
        owner=OWNER if kind == "owner_agent" else None,
        thread_id="thr-1",
    )
    args.update(overrides)
    return threads.open_thread(log, **args)


def _code(exc: pytest.ExceptionInfo[BusinessError]) -> str:
    return exc.value.code


def test_owner_to_agent_thread_round_trip_and_ack():
    log = _log()
    thread, created = _open(log)
    assert created is True
    assert thread.kind == "owner_agent"
    assert thread.participants == (OWNER, AGENT_A)
    assert thread.visibility == "direct"

    message, made = threads.post(
        log, tenant_id=TENANT, thread_id=thread.thread_id, sender=OWNER,
        body="Please summarise the report.", idempotency_key="m-1", now=100.0,
    )
    assert made is True
    assert message.sequence == 2  # metadata occupies sequence 1
    assert message.recipient == AGENT_A
    assert message.delivery_state == "pending"

    # Read-your-writes on both sides.
    owner_view = threads.read(
        log, tenant_id=TENANT, thread_id=thread.thread_id, principal=OWNER)
    agent_view = threads.read(
        log, tenant_id=TENANT, thread_id=thread.thread_id, principal=AGENT_A)
    assert [item.message_id for item in owner_view] == [message.message_id]
    assert [item.message_id for item in agent_view] == [message.message_id]

    acked = threads.ack(
        log, tenant_id=TENANT, thread_id=thread.thread_id, principal=AGENT_A,
        message_id=message.message_id, now=101.0)
    assert acked.delivery_state == "acked"

    reply, _ = threads.reply(
        log, tenant_id=TENANT, thread_id=thread.thread_id, sender=AGENT_A,
        body="Done — attached.", idempotency_key="m-2", now=102.0)
    assert reply.recipient == OWNER
    assert reply.reply_to == message.message_id
    # the ack receipt is itself a log record, so the reply is sequence 4
    assert reply.sequence == 4


def test_agent_to_agent_direct_thread():
    log = _log()
    _open(log, kind="direct", participants=(AGENT_A, AGENT_B))
    first, _ = threads.post(
        log, tenant_id=TENANT, thread_id="thr-1", sender=AGENT_A,
        body="Can you review this?", idempotency_key="d-1")
    assert first.recipient == AGENT_B
    second, _ = threads.reply(
        log, tenant_id=TENANT, thread_id="thr-1", sender=AGENT_B,
        body="Reviewing now.", idempotency_key="d-2")
    assert second.recipient == AGENT_A
    assert [item.sender for item in threads.read(
        log, tenant_id=TENANT, thread_id="thr-1", principal=AGENT_B
    )] == [AGENT_A, AGENT_B]


def test_room_thread_and_mention_authorization():
    log = _log()
    participants = [OWNER, AGENT_A, AGENT_B]
    _open(log, kind="room", participants=participants)
    allowed = {(OWNER, AGENT_B), (OWNER, threads.ROOM_MENTION)}
    def can_mention(sender, mention):
        return (sender, mention) in allowed

    message, _ = threads.post(
        log, tenant_id=TENANT, thread_id="thr-1", sender=OWNER,
        body="@agent:b please pair on the draft.",
        mentions=["@agent:b", threads.ROOM_MENTION],
        can_mention=can_mention, idempotency_key="r-1")
    assert message.mentions == (AGENT_B, threads.ROOM_MENTION)

    # Agent A is still a participant and can read the room thread.
    assert threads.read(
        log, tenant_id=TENANT, thread_id="thr-1", principal=AGENT_A
    )[0].message_id == message.message_id

    # An unauthorized mention is refused before anything is appended.
    with pytest.raises(BusinessError) as exc:
        threads.post(
            log, tenant_id=TENANT, thread_id="thr-1", sender=OWNER,
            body="@agent:a look", mentions=["@agent:a"],
            can_mention=can_mention, idempotency_key="r-2")
    assert _code(exc) == threads.THREAD_MENTION_REFUSED_CODE

    # A mention of a non-participant is refused too.
    with pytest.raises(BusinessError) as exc:
        threads.post(
            log, tenant_id=TENANT, thread_id="thr-1", sender=OWNER,
            body="@agent:c look", mentions=["@agent:c"],
            can_mention=lambda _s, _m: True, idempotency_key="r-3")
    assert _code(exc) == threads.THREAD_MENTION_REFUSED_CODE

    # A missing mention checker fails closed.
    with pytest.raises(BusinessError) as exc:
        threads.post(
            log, tenant_id=TENANT, thread_id="thr-1", sender=OWNER,
            body="@agent:b look", mentions=["@agent:b"],
            idempotency_key="r-4")
    assert _code(exc) == threads.THREAD_MENTION_REFUSED_CODE


def test_non_participant_cannot_read_or_list():
    log = _log()
    _open(log)
    threads.post(
        log, tenant_id=TENANT, thread_id="thr-1", sender=OWNER,
        body="private", idempotency_key="p-1")
    with pytest.raises(BusinessError) as exc:
        threads.read(
            log, tenant_id=TENANT, thread_id="thr-1", principal=AGENT_C)
    assert _code(exc) == threads.THREAD_NOT_PARTICIPANT_CODE
    assert threads.list_threads(
        log, tenant_id=TENANT, principal=AGENT_C) == []
    assert len(threads.list_threads(
        log, tenant_id=TENANT, principal=AGENT_A)) == 1


def test_ordering_duplicate_and_conflict():
    log = _log()
    _open(log)
    one, created = threads.post(
        log, tenant_id=TENANT, thread_id="thr-1", sender=OWNER,
        body="one", idempotency_key="k-1")
    assert created is True
    again, created = threads.post(
        log, tenant_id=TENANT, thread_id="thr-1", sender=OWNER,
        body="one", idempotency_key="k-1")
    assert created is False and again.message_id == one.message_id

    with pytest.raises(BusinessError) as exc:
        threads.post(
            log, tenant_id=TENANT, thread_id="thr-1", sender=OWNER,
            body="different", idempotency_key="k-1")
    assert _code(exc) == "A2A_IDEMPOTENCY_CONFLICT"

    second, _ = threads.post(
        log, tenant_id=TENANT, thread_id="thr-1", sender=OWNER,
        body="two", idempotency_key="k-2")
    third, _ = threads.reply(
        log, tenant_id=TENANT, thread_id="thr-1", sender=AGENT_A,
        body="three", idempotency_key="k-3")
    order = threads.read(
        log, tenant_id=TENANT, thread_id="thr-1", principal=OWNER)
    assert [item.message_id for item in order] == [
        one.message_id, second.message_id, third.message_id]
    assert [item.sequence for item in order] == [2, 3, 4]
    assert order[-1].delivery_state == "pending"


def test_offline_backlog_replays_in_order():
    log = _log()
    _open(log)
    sent = []
    for index in range(3):
        message, _ = threads.post(
            log, tenant_id=TENANT, thread_id="thr-1", sender=OWNER,
            body=f"queued {index}", idempotency_key=f"b-{index}")
        sent.append(message.message_id)
    # The agent was offline while these were appended; replay returns all.
    backlog = threads.replay(
        log, tenant_id=TENANT, principal=AGENT_A)
    assert [item.message_id for item in backlog] == sent
    assert [item.body for item in backlog] == ["queued 0", "queued 1", "queued 2"]
    # A later replay from the last sequence is empty.
    assert threads.replay(
        log, tenant_id=TENANT, principal=AGENT_A,
        since_sequence=backlog[-1].sequence) == []


def test_tenant_isolation():
    log = _log()
    _open(log)
    threads.post(
        log, tenant_id=TENANT, thread_id="thr-1", sender=OWNER,
        body="alpha only", idempotency_key="t-1")
    with pytest.raises(BusinessError) as exc:
        threads.get_thread(log, tenant_id=OTHER_TENANT, thread_id="thr-1")
    assert _code(exc) == threads.THREAD_NOT_FOUND_CODE
    assert threads.replay(
        log, tenant_id=OTHER_TENANT, principal=AGENT_A) == []


def test_open_thread_is_idempotent_and_conflicts_on_change():
    log = _log()
    first, created = _open(log)
    again, created_again = _open(log)
    assert created is True and created_again is False
    assert first.thread_id == again.thread_id
    with pytest.raises(BusinessError) as exc:
        _open(log, participants=(OWNER, AGENT_B))
    assert _code(exc) == threads.THREAD_EXISTS_CODE


def test_no_authority_transfer_payload_or_kind():
    log = _log()
    _open(log)
    with pytest.raises(BusinessError) as exc:
        threads.post(
            log, tenant_id=TENANT, thread_id="thr-1", sender=OWNER,
            body="you own this now", idempotency_key="a-1",
            payload={"transfer_ownership": AGENT_A})
    assert _code(exc) == threads.THREAD_AUTHORITY_REFUSED_CODE
    with pytest.raises(BusinessError) as exc:
        threads.post(
            log, tenant_id=TENANT, thread_id="thr-1", sender=OWNER,
            body="grant", kind="grant", idempotency_key="a-2")
    assert _code(exc) == threads.THREAD_AUTHORITY_REFUSED_CODE
    # A natural-language message is recorded but never changes participants.
    threads.post(
        log, tenant_id=TENANT, thread_id="thr-1", sender=OWNER,
        body="I transfer ownership to agent:b", idempotency_key="a-3")
    assert threads.get_thread(
        log, tenant_id=TENANT, thread_id="thr-1").participants == (OWNER, AGENT_A)


def test_body_and_participant_bounds():
    log = _log()
    _open(log)
    with pytest.raises(BusinessError) as exc:
        threads.post(
            log, tenant_id=TENANT, thread_id="thr-1", sender=OWNER,
            body="x" * 100, idempotency_key="z-1", max_body_bytes=10)
    assert _code(exc) == threads.THREAD_BODY_TOO_LARGE_CODE
    with pytest.raises(BusinessError) as exc:
        _open(log, thread_id="thr-2",
              participants=[f"agent:{i}" for i in range(threads.MAX_PARTICIPANTS + 1)])
    assert _code(exc) == "BUSINESS_BAD_INPUT"


def test_sweep_dead_letters_expired_messages():
    log = _log()
    _open(log)
    message, _ = threads.post(
        log, tenant_id=TENANT, thread_id="thr-1", sender=OWNER,
        body="short lived", idempotency_key="s-1", expires_at=200.0, now=100.0)
    result = threads.sweep(log, tenant_id=TENANT, now=201.0)
    assert result["dead_lettered"] == [message.message_id]
    assert threads.read(
        log, tenant_id=TENANT, thread_id="thr-1", principal=AGENT_A) == []
