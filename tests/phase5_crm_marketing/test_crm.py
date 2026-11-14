"""P5-03 — CRM identity, consent/suppression, pipeline, leases and support.

Hermetic: a throwaway Business OS SQLite file under ``tmp_path``. No network,
no live service, no credentials. The tests pin the acceptance claims:

* dedupe/identity by email/external_ref (and an identity conflict refused);
* per-channel consent and suppression block contact;
* pipeline stage aging;
* a single-owner assignment lease;
* support tickets carry an SLA and an explicit handoff;
* internal notes are never customer-visible, and a customer-visible message
  needs an exact approval plus a brand/legal check;
* tenant isolation.
"""
from __future__ import annotations

import pytest

from voicebridge.business import approvals, crm
from voicebridge.business.errors import BusinessError
from voicebridge.business.runs import LEASE_CONFLICT_CODE
from voicebridge.business.store import BusinessStore

pytestmark = pytest.mark.unit

OWNER = "owner"
NOW = 1_800_000_000.0


@pytest.fixture()
def env(tmp_path):
    store = BusinessStore(tmp_path / "business.db")
    tenant = store.create_tenant("Acme Holdings")
    business = store.create_business(tenant.tenant_id, "Acme", slug="acme")
    other = store.create_tenant("Other Co")
    other_business = store.create_business(
        other.tenant_id, "Other", slug="other"
    )
    return {
        "store": store,
        "tenant": tenant.tenant_id,
        "business": business.business_id,
        "other_tenant": other.tenant_id,
        "other_business": other_business.business_id,
    }


def _contact(env, *, email="ada@example.com", ref="ext-1", kind="lead"):
    contact, _ = crm.upsert_contact(
        env["store"],
        env["tenant"],
        env["business"],
        kind=kind,
        display_name="Ada Lovelace",
        email=email,
        external_ref=ref,
        actor=OWNER,
        idempotency_key=f"contact-{email}-{ref}",
        now=NOW,
    )
    return contact


def _ticket(env, contact, *, sla_seconds=100.0, now=NOW):
    return crm.open_ticket(
        env["store"],
        env["tenant"],
        env["business"],
        contact.contact_id,
        subject="Where is my order?",
        body="It has not arrived.",
        channel="email",
        priority="normal",
        sla_seconds=sla_seconds,
        assigned_to="agent:a",
        actor=OWNER,
        idempotency_key=f"ticket-{contact.contact_id}",
        now=now,
    )


def _approve(store, tenant, approval_id):
    return approvals.decide(
        store,
        tenant_id=tenant,
        approval_id=approval_id,
        decision="approved",
        decided_by=OWNER,
        reason="checked",
    )


# -- identity -------------------------------------------------------------
def test_identity_dedupes_by_email_and_external_ref(env):
    store, tenant, business = env["store"], env["tenant"], env["business"]
    first, created = crm.upsert_contact(
        store, tenant, business,
        kind="lead", display_name="Ada Lovelace", email="Ada@Example.com",
        external_ref="ext-1", actor=OWNER, idempotency_key="c-1", now=NOW,
    )
    assert created is True
    same_email, created_again = crm.upsert_contact(
        store, tenant, business,
        kind="customer", display_name="Ada L.", email="ada@example.com",
        actor=OWNER, idempotency_key="c-2", now=NOW,
    )
    same_ref, created_ref = crm.upsert_contact(
        store, tenant, business,
        kind="customer", display_name="Someone", external_ref="ext-1",
        actor=OWNER, idempotency_key="c-3", now=NOW,
    )
    assert created_again is False and created_ref is False
    assert same_email.contact_id == first.contact_id
    assert same_ref.contact_id == first.contact_id
    assert len(store.list_contacts(tenant, business_id=business)) == 1


def test_identity_requires_a_key_and_refuses_a_split_identity(env):
    store, tenant, business = env["store"], env["tenant"], env["business"]
    with pytest.raises(BusinessError) as missing:
        crm.upsert_contact(
            store, tenant, business,
            kind="lead", display_name="No Key", actor=OWNER,
            idempotency_key="none", now=NOW,
        )
    assert missing.value.code == crm.CRM_IDENTITY_REQUIRED_CODE

    first, _ = crm.upsert_contact(
        store, tenant, business,
        kind="lead", display_name="One", email="one@example.com",
        actor=OWNER, idempotency_key="one", now=NOW,
    )
    second, _ = crm.upsert_contact(
        store, tenant, business,
        kind="lead", display_name="Two", external_ref="ref-two",
        actor=OWNER, idempotency_key="two", now=NOW,
    )
    assert first.contact_id != second.contact_id
    with pytest.raises(BusinessError) as conflict:
        crm.upsert_contact(
            store, tenant, business,
            kind="lead", display_name="Merged", email="one@example.com",
            external_ref="ref-two", actor=OWNER, idempotency_key="merge",
            now=NOW,
        )
    assert conflict.value.code == crm.CRM_IDENTITY_CONFLICT_CODE


# -- consent and suppression ---------------------------------------------
def test_consent_and_suppression_block_contact_per_channel(env):
    store, tenant = env["store"], env["tenant"]
    contact = _contact(env)

    with pytest.raises(BusinessError) as no_consent:
        crm.assert_contactable(store, tenant, contact.contact_id, "email")
    assert no_consent.value.code == crm.CRM_CONSENT_BLOCKED_CODE

    crm.record_consent(
        store, tenant, contact.contact_id,
        channel="email", status="granted", source="checkout",
        actor=OWNER, idempotency_key="consent-1", now=NOW,
    )
    state = crm.assert_contactable(store, tenant, contact.contact_id, "email")
    assert state["consent"] == "granted"
    assert state["marketing_contactable"] is True

    # Consent for email is not consent for SMS.
    with pytest.raises(BusinessError) as sms:
        crm.assert_contactable(store, tenant, contact.contact_id, "sms")
    assert sms.value.code == crm.CRM_CONSENT_BLOCKED_CODE

    crm.record_suppression(
        store, tenant, contact.contact_id,
        channel="all", status="suppressed", reason="do not contact",
        actor=OWNER, idempotency_key="suppress-1", now=NOW,
    )
    with pytest.raises(BusinessError) as suppressed:
        crm.assert_contactable(store, tenant, contact.contact_id, "email")
    assert suppressed.value.code == crm.CRM_SUPPRESSED_CODE

    crm.record_suppression(
        store, tenant, contact.contact_id,
        channel="all", status="cleared", actor=OWNER,
        idempotency_key="suppress-2", now=NOW,
    )
    crm.record_consent(
        store, tenant, contact.contact_id,
        channel="email", status="revoked", actor=OWNER,
        idempotency_key="consent-2", now=NOW,
    )
    with pytest.raises(BusinessError) as revoked:
        crm.assert_contactable(store, tenant, contact.contact_id, "email")
    assert revoked.value.code == crm.CRM_CONSENT_BLOCKED_CODE


# -- pipeline -------------------------------------------------------------
def test_pipeline_stage_and_aging(env):
    store, tenant, business = env["store"], env["tenant"], env["business"]
    contact = _contact(env)
    crm.set_stage(
        store, tenant, contact.contact_id, "qualified",
        actor=OWNER, idempotency_key="stage-1", now=NOW,
    )
    fresh = crm.pipeline(store, tenant, business_id=business, now=NOW + 10)
    assert fresh[0]["stage"] == "qualified"
    assert fresh[0]["aging"] is False

    stale = crm.pipeline(
        store, tenant, business_id=business,
        now=NOW + 8 * 24 * 3600, aging_seconds=7 * 24 * 3600,
    )
    assert stale[0]["aging"] is True

    crm.set_stage(
        store, tenant, contact.contact_id, "won",
        actor=OWNER, idempotency_key="stage-2", now=NOW + 10,
    )
    summary = crm.pipeline_summary(
        crm.pipeline(
            store, tenant, business_id=business,
            now=NOW + 9 * 24 * 3600, aging_seconds=7 * 24 * 3600,
        )
    )
    assert summary["counts"]["won"] == 1
    assert summary["aging"] == 0


# -- assignment leases ----------------------------------------------------
def test_assignment_has_a_single_owner_until_release_or_expiry(env):
    store, tenant = env["store"], env["tenant"]
    contact = _contact(env)
    lease = crm.assign(
        store, tenant, contact.contact_id,
        holder="agent:a", ttl_seconds=50, actor=OWNER,
        idempotency_key="assign-a", now=NOW,
    )
    assert lease["holder"] == "agent:a"

    with pytest.raises(BusinessError) as taken:
        crm.assign(
            store, tenant, contact.contact_id,
            holder="agent:b", ttl_seconds=50, actor=OWNER,
            idempotency_key="assign-b", now=NOW + 10,
        )
    assert taken.value.code == LEASE_CONFLICT_CODE
    assert crm.assignment(store, tenant, contact.contact_id, now=NOW + 10) == {
        "lease_id": lease["lease_id"],
        "holder": "agent:a",
        "fencing_token": lease["fencing_token"],
        "expires_at": lease["expires_at"],
    }

    crm.release_assignment(
        store, tenant, contact.contact_id,
        lease_id=lease["lease_id"], holder="agent:a",
        fencing_token=lease["fencing_token"], actor=OWNER,
        idempotency_key="release-a", now=NOW + 20,
    )
    second = crm.assign(
        store, tenant, contact.contact_id,
        holder="agent:b", ttl_seconds=50, actor=OWNER,
        idempotency_key="assign-b-2", now=NOW + 20,
    )
    assert second["holder"] == "agent:b"


# -- support tickets ------------------------------------------------------
def test_ticket_sla_and_handoff(env):
    store, tenant = env["store"], env["tenant"]
    contact = _contact(env)
    ticket = _ticket(env, contact, sla_seconds=100, now=NOW)
    assert ticket.assignee == "agent:a"
    assert crm.ticket_sla(store, tenant, ticket.ticket_id, now=NOW + 40) == {
        "ticket_id": ticket.ticket_id,
        "status": "open",
        "sla_due_at": ticket.sla_due_at,
        "overdue": False,
        "seconds_remaining": 60.0,
        "breaches": 0,
    }

    handed = crm.handoff_ticket(
        store, tenant, ticket.ticket_id,
        to_assignee="agent:b", reason="needs a specialist", actor=OWNER,
        idempotency_key="handoff-1", now=NOW + 150,
    )
    assert handed.assignee == "agent:b"
    assert handed.overdue is True
    assert len(handed.handoffs) == 1
    assert handed.handoffs[0]["from"] == "agent:a"
    assert handed.handoffs[0]["to"] == "agent:b"
    assert handed.handoffs[0]["sla_breached"] is True


def test_terminal_ticket_cannot_be_handed_off(env):
    store, tenant = env["store"], env["tenant"]
    contact = _contact(env)
    ticket = _ticket(env, contact)
    crm.set_ticket_status(
        store, tenant, ticket.ticket_id, "done", actor=OWNER,
        idempotency_key="done-1", now=NOW,
    )
    with pytest.raises(BusinessError) as terminal:
        crm.handoff_ticket(
            store, tenant, ticket.ticket_id,
            to_assignee="agent:b", actor=OWNER,
            idempotency_key="handoff-done", now=NOW,
        )
    assert terminal.value.code == crm.CRM_TICKET_TERMINAL_CODE


# -- notes vs customer messages -----------------------------------------
def test_internal_note_is_never_customer_visible(env):
    store, tenant = env["store"], env["tenant"]
    contact = _contact(env)
    ticket = _ticket(env, contact)
    note = crm.add_note(
        store, tenant, ticket.ticket_id,
        body="Refund approved internally; do not quote the amount.",
        actor="agent:a", idempotency_key="note-1", now=NOW,
    )
    assert note.visibility == "internal"
    assert [item.message_id for item in crm.list_notes(
        store, tenant, ticket.ticket_id)] == [note.message_id]
    assert crm.list_customer_messages(store, tenant, ticket.ticket_id) == []

    with pytest.raises(BusinessError) as visibility:
        crm.add_note(
            store, tenant, ticket.ticket_id, body="customer visible?",
            actor="agent:a", idempotency_key="note-2", visibility="customer",
            now=NOW,
        )
    assert visibility.value.code == crm.CRM_NOTE_VISIBILITY_CODE


def test_customer_message_requires_exact_approval_and_brand_check(env):
    store, tenant = env["store"], env["tenant"]
    contact = _contact(env)
    ticket = _ticket(env, contact)
    crm.record_consent(
        store, tenant, contact.contact_id,
        channel="email", status="granted", actor=OWNER,
        idempotency_key="consent-msg", now=NOW,
    )

    with pytest.raises(BusinessError) as no_approval:
        crm.send_customer_message(
            store, tenant, ticket.ticket_id,
            body="Thanks for reaching out.", approval_id="",
            actor="agent:a", idempotency_key="msg-0", now=NOW,
        )
    assert no_approval.value.code == crm.CRM_MESSAGE_APPROVAL_CODE

    with pytest.raises(BusinessError) as bad_content:
        crm.request_message_approval(
            store, tenant, ticket.ticket_id,
            body="Buy followers to grow fast.", requested_by=OWNER,
            idempotency_key="apr-bad", now=NOW,
        )
    assert bad_content.value.code == crm.CRM_CONTENT_REFUSED_CODE

    approval = crm.request_message_approval(
        store, tenant, ticket.ticket_id,
        body="Thanks for reaching out. Your order is on the way.",
        requested_by=OWNER, idempotency_key="apr-ok", now=NOW,
    )
    _approve(store, tenant, approval.approval_id)
    message = crm.send_customer_message(
        store, tenant, ticket.ticket_id,
        body="Thanks for reaching out. Your order is on the way.",
        approval_id=approval.approval_id, actor="agent:a",
        idempotency_key="msg-1", now=NOW,
    )
    assert message.visibility == "customer"
    assert [item.message_id for item in crm.list_customer_messages(
        store, tenant, ticket.ticket_id)] == [message.message_id]
    assert crm.list_notes(store, tenant, ticket.ticket_id) == []

    # Approve A, send B: the payload hash no longer matches.
    with pytest.raises(BusinessError) as confused:
        crm.send_customer_message(
            store, tenant, ticket.ticket_id,
            body="A completely different message.",
            approval_id=approval.approval_id, actor="agent:a",
            idempotency_key="msg-2", now=NOW,
        )
    assert confused.value.code == "BUSINESS_APPROVAL_INTENT_MISMATCH"


def test_customer_message_respects_consent_unless_transactional(env):
    store, tenant = env["store"], env["tenant"]
    contact = _contact(env)
    ticket = _ticket(env, contact)
    approval = crm.request_message_approval(
        store, tenant, ticket.ticket_id,
        body="We have received your request.",
        requested_by=OWNER, idempotency_key="apr-consent", now=NOW,
    )
    _approve(store, tenant, approval.approval_id)

    with pytest.raises(BusinessError) as blocked:
        crm.send_customer_message(
            store, tenant, ticket.ticket_id,
            body="We have received your request.",
            approval_id=approval.approval_id, actor="agent:a",
            idempotency_key="msg-consent-1", now=NOW,
        )
    assert blocked.value.code == crm.CRM_CONSENT_BLOCKED_CODE

    message = crm.send_customer_message(
        store, tenant, ticket.ticket_id,
        body="We have received your request.",
        approval_id=approval.approval_id, actor="agent:a",
        idempotency_key="msg-consent-2", consent_required=False, now=NOW,
    )
    assert message.body == "We have received your request."


# -- tenant isolation -----------------------------------------------------
def test_tenant_isolation(env):
    store = env["store"]
    contact = _contact(env)
    ticket = _ticket(env, contact)

    with pytest.raises(BusinessError) as other_contact:
        crm.get_contact(store, env["other_tenant"], contact.contact_id)
    assert other_contact.value.code == "BUSINESS_NOT_FOUND"
    with pytest.raises(BusinessError) as other_ticket:
        crm.get_ticket(store, env["other_tenant"], ticket.ticket_id)
    assert other_ticket.value.code == "BUSINESS_NOT_FOUND"
    with pytest.raises(BusinessError) as other_consent:
        crm.consent_state(store, env["other_tenant"], contact.contact_id, "email")
    assert other_consent.value.code == "BUSINESS_NOT_FOUND"
    assert crm.pipeline(store, env["other_tenant"]) == []
