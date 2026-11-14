"""P2-05 — typed A2A handoff over the shared message fabric.

Hermetic: the in-memory log is a test double; the sessions integration uses a
throwaway SQLite file. No network, no live service. These tests pin spoof,
replay, over-delegation and recipient-only acceptance, and that an accepted
handoff grants no authority beyond the sender's subset.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from voicebridge.business import (
    AgentControlService,
    BusinessStore,
    a2a,
    identity,
    runs,
    sessions,
)
from voicebridge.business.errors import BusinessError

TENANT = "tenant-alpha"
SOURCE = "agent:a"
RECIPIENT = "agent:b"
OTHER = "agent:c"


def _log() -> a2a.InMemoryEventLog:
    return a2a.InMemoryEventLog()


def _send(log, **overrides):
    allowed = overrides.pop(
        "allowed",
        {(SOURCE, "cap:read")},
    )
    args = dict(
        tenant_id=TENANT,
        from_principal=SOURCE,
        to_principal=RECIPIENT,
        task_id="tsk_1",
        idempotency_key="handoff-1",
        capability_subset=["cap:read"],
        can_delegate=lambda principal, capability: (principal, capability)
        in allowed,
        checkpoint_id="ckp_1",
        artifact_ids=["art_1"],
        unresolved_questions=["who signs off?"],
        acceptance_deadline=9_999_999_999,
    )
    args.update(overrides)
    return a2a.send_handoff(log, **args)


def _code(exc: pytest.ExceptionInfo[BusinessError]) -> str:
    return exc.value.code


# -- typed envelope --------------------------------------------------------
def test_handoff_message_round_trips_typed_fields():
    log = _log()
    message, created = _send(log)

    assert created is True
    assert message.kind == "handoff"
    assert message.task_id == "tsk_1"
    assert message.capability_subset == ("cap:read",)

    handoff = a2a.parse_handoff(message)
    assert handoff.task_id == "tsk_1"
    assert handoff.from_principal == SOURCE
    assert handoff.to_principal == RECIPIENT
    assert handoff.checkpoint_id == "ckp_1"
    assert handoff.artifact_ids == ("art_1",)
    assert handoff.unresolved_questions == ("who signs off?",)
    assert handoff.acceptance_deadline
    assert handoff.as_payload()["capability_subset"] == ["cap:read"]


def test_spoofed_sender_and_payload_are_refused():
    log = _log()
    with pytest.raises(BusinessError) as exc:
        _send(log, authenticated_principal=OTHER)
    assert _code(exc) == "A2A_SPOOF_REFUSED"

    message, _ = _send(log)
    tampered = replace(message, sender_principal=OTHER)
    with pytest.raises(BusinessError) as exc:
        a2a.parse_handoff(tampered)
    assert _code(exc) == "A2A_SPOOF_REFUSED"


def test_replayed_nonce_and_duplicate_acceptance_are_refused():
    log = _log()
    _send(log, replay_nonce="nonce-1")
    with pytest.raises(BusinessError) as exc:
        _send(log, idempotency_key="handoff-2", replay_nonce="nonce-1")
    assert _code(exc) == "A2A_REPLAY_REFUSED"

    first, _ = _send(log, idempotency_key="handoff-3")
    a2a.accept_handoff_message(
        log, tenant_id=TENANT, principal=RECIPIENT, message_id=first.message_id
    )
    with pytest.raises(BusinessError) as exc:
        a2a.accept_handoff_message(
            log, tenant_id=TENANT, principal=RECIPIENT, message_id=first.message_id
        )
    assert _code(exc) == "A2A_REPLAY_REFUSED"


def test_over_delegation_is_refused_and_the_subset_never_widens():
    log = _log()
    allowed = {(SOURCE, "cap:read")}
    with pytest.raises(BusinessError) as exc:
        _send(
            log,
            idempotency_key="handoff-over",
            capability_subset=["cap:read", "cap:payout"],
            allowed=allowed,
        )
    assert _code(exc) == "A2A_DELEGATION_REFUSED"

    message, _ = _send(log)
    accepted = a2a.accept_handoff_message(
        log, tenant_id=TENANT, principal=RECIPIENT, message_id=message.message_id
    )
    # Acceptance does not add authority: the message still carries exactly the
    # sender's declared subset, and nothing else is minted.
    assert accepted.capability_subset == ("cap:read",)
    assert accepted.message.delivery_state == "acked"


def test_only_the_recipient_can_accept():
    log = _log()
    message, _ = _send(log)
    with pytest.raises(BusinessError) as exc:
        a2a.accept_handoff_message(
            log, tenant_id=TENANT, principal=OTHER, message_id=message.message_id
        )
    assert _code(exc) == "A2A_NOT_RECIPIENT"
    # The sender cannot accept its own offer either.
    with pytest.raises(BusinessError) as exc:
        a2a.accept_handoff_message(
            log, tenant_id=TENANT, principal=SOURCE, message_id=message.message_id
        )
    assert _code(exc) == "A2A_NOT_RECIPIENT"
    # Unaccepted, the message stays live for the real recipient.
    assert a2a.parse_handoff(message).to_principal == RECIPIENT


def test_expired_handoff_cannot_be_accepted():
    log = _log()
    message, _ = _send(log, acceptance_deadline=1000)
    with pytest.raises(BusinessError) as exc:
        a2a.accept_handoff_message(
            log,
            tenant_id=TENANT,
            principal=RECIPIENT,
            message_id=message.message_id,
            now=2000,
        )
    assert _code(exc) == "A2A_MESSAGE_EXPIRED"


# -- sessions integration --------------------------------------------------
def test_accepting_the_message_freezes_the_source_task(tmp_path):
    store = BusinessStore(tmp_path / "business.db")
    tenant = store.create_tenant("Acme Holdings")
    business = store.create_business(tenant.tenant_id, "Acme", slug="acme")
    owner = identity.create_principal(
        store, tenant.tenant_id, kind="owner", external_ref="owner"
    )
    source = identity.create_principal(
        store, tenant.tenant_id, kind="agent", external_ref="agent-1"
    )
    recipient = identity.create_principal(
        store, tenant.tenant_id, kind="agent", external_ref="agent-2"
    )
    svc = AgentControlService(store)
    agent, _ = svc.create(
        tenant_id=tenant.tenant_id,
        business_id=business.business_id,
        role_id="role.analyst",
        department="Research",
        display_name="Ada",
        human_owner="owner",
        provider="provider-a",
        model="model-a",
        idempotency_key="agent-1",
        security_principal_id=source.principal_id,
    )
    session = sessions.create_session(
        store,
        tenant.tenant_id,
        business.business_id,
        agent.agent_id,
        owner_principal=owner.principal_id,
        actor="owner",
        task_title="Draft the report",
    )
    workspace = runs.get_workspace(store, tenant.tenant_id, session.default_workspace_id)
    offer = sessions.propose_handoff(
        store,
        tenant.tenant_id,
        session.session_id,
        to_principal=recipient.principal_id,
        actor="owner",
        reason="hand off",
    )

    log = _log()
    message, _ = _send(
        log,
        tenant_id=tenant.tenant_id,
        from_principal=source.principal_id,
        to_principal=recipient.principal_id,
        task_id=workspace.task_id,
        handoff_id=offer.handoff_id,
        allowed={(source.principal_id, "cap:read")},
    )
    accepted = a2a.accept_handoff_message(
        log,
        tenant_id=tenant.tenant_id,
        principal=recipient.principal_id,
        message_id=message.message_id,
        on_accept=lambda handoff: sessions.accept_handoff(
            store,
            tenant.tenant_id,
            offer.handoff_id,
            principal=handoff.to_principal,
        ),
    )
    assert accepted.message.delivery_state == "acked"

    task = runs.get_task(store, tenant.tenant_id, workspace.task_id)
    assert task.assignee_principal == recipient.principal_id
    with pytest.raises(BusinessError) as exc:
        runs.transition_task(
            store,
            tenant.tenant_id,
            task.task_id,
            "completed",
            actor=source.principal_id,
        )
    assert _code(exc) == "BUSINESS_TASK_READ_ONLY"
