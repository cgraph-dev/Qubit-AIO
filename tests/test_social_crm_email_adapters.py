"""Representative social, CRM and email adapters — same gated contract."""
from __future__ import annotations

import pytest

from _integration_stubs import FakeClock, RecordingTransport
from voicebridge.integrations.base import (
    ADAPTER_APPROVAL_REQUIRED_CODE,
    ADAPTER_BAD_INPUT_CODE,
    ADAPTER_CONSENT_REQUIRED_CODE,
    ADAPTER_IDEMPOTENCY_CONFLICT_CODE,
    ADAPTER_NOT_CONFIGURED_CODE,
    ADAPTER_POLICY_FORBIDDEN_CODE,
    ADAPTER_RATE_LIMITED_CODE,
    ADAPTER_UNAVAILABLE_CODE,
    AdapterError,
    ApprovalToken,
)
from voicebridge.integrations.crm import APPROVAL_CREATE_LEAD, CrmAdapter
from voicebridge.integrations.email import APPROVAL_SEND, EmailAdapter
from voicebridge.integrations.social import SocialAdapter

SOCIAL_CREDS = {"access_token": "token-1"}
CRM_CREDS = {"api_key": "key-1"}
EMAIL_CREDS = {"api_key": "key-1"}


def _social(platform="x", **kwargs):
    transport = kwargs.pop("transport", None)
    credentials = kwargs.pop("credentials", SOCIAL_CREDS)
    return SocialAdapter(
        platform,
        transport=transport if transport is not None else RecordingTransport(),
        credentials=credentials,
        **kwargs,
    )


def _crm(**kwargs):
    transport = kwargs.pop("transport", None)
    credentials = kwargs.pop("credentials", CRM_CREDS)
    return CrmAdapter(
        transport=transport if transport is not None else RecordingTransport(),
        credentials=credentials,
        **kwargs,
    )


def _email(**kwargs):
    transport = kwargs.pop("transport", None)
    credentials = kwargs.pop("credentials", EMAIL_CREDS)
    return EmailAdapter(
        transport=transport if transport is not None else RecordingTransport(),
        credentials=credentials,
        **kwargs,
    )


# -- social -------------------------------------------------------------
def test_social_missing_transport_and_credentials_are_typed():
    adapter_no_transport = SocialAdapter("x", credentials=SOCIAL_CREDS)
    with pytest.raises(AdapterError) as exc:
        adapter_no_transport.fetch_metrics()
    assert exc.value.code == ADAPTER_UNAVAILABLE_CODE

    adapter = _social(credentials={})
    with pytest.raises(AdapterError) as exc:
        adapter.fetch_metrics()
    assert exc.value.code == ADAPTER_NOT_CONFIGURED_CODE


def test_x_automated_publish_is_allowed_without_approval():
    adapter = _social("x")
    result = adapter.publish_post(text="hello", idempotency_key="x-1")
    assert result.executed is False
    assert result.dry_run is True
    assert result.approval_id is None


def test_first_publish_requires_approval_even_where_automation_is_allowed():
    adapter = _social("x")
    with pytest.raises(AdapterError) as exc:
        adapter.publish_post(
            text="hello", idempotency_key="x-1", first_publish=True
        )
    assert exc.value.code == ADAPTER_APPROVAL_REQUIRED_CODE

    token = ApprovalToken("apr-1", "x.post.publish")
    result = adapter.publish_post(
        text="hello",
        idempotency_key="x-1",
        first_publish=True,
        approval=token,
    )
    assert result.approval_id == "apr-1"
    assert result.executed is False


def test_limited_platform_publish_always_requires_approval():
    adapter = _social("linkedin")
    with pytest.raises(AdapterError) as exc:
        adapter.publish_post(text="hello", idempotency_key="li-1")
    assert exc.value.code == ADAPTER_APPROVAL_REQUIRED_CODE

    token = ApprovalToken("apr-1", "linkedin.post.publish")
    result = adapter.publish_post(
        text="hello", idempotency_key="li-1", approval=token
    )
    assert result.executed is False


def test_forbidden_publish_platform_is_refused_before_anything_else():
    adapter = _social("web")
    with pytest.raises(AdapterError) as exc:
        adapter.publish_post(text="hello", idempotency_key="web-1")
    assert exc.value.code == ADAPTER_POLICY_FORBIDDEN_CODE


def test_reply_requires_approval():
    adapter = _social("x")
    with pytest.raises(AdapterError) as exc:
        adapter.reply("post-1", text="thanks", idempotency_key="r-1")
    assert exc.value.code == ADAPTER_APPROVAL_REQUIRED_CODE

    token = ApprovalToken("apr-1", "x.post.reply")
    result = adapter.reply(
        "post-1", text="thanks", idempotency_key="r-1", approval=token
    )
    assert result.executed is False


def test_direct_message_requires_opt_in_and_approval():
    adapter = _social("x")
    with pytest.raises(AdapterError) as exc:
        adapter.send_direct_message(
            "person-1", text="hi", idempotency_key="dm-1"
        )
    assert exc.value.code == ADAPTER_CONSENT_REQUIRED_CODE

    with pytest.raises(AdapterError) as exc:
        adapter.send_direct_message(
            "person-1", text="hi", idempotency_key="dm-1", opt_in=True
        )
    assert exc.value.code == ADAPTER_APPROVAL_REQUIRED_CODE

    token = ApprovalToken("apr-1", "x.dm.send")
    result = adapter.send_direct_message(
        "person-1",
        text="hi",
        idempotency_key="dm-1",
        opt_in=True,
        approval=token,
    )
    assert result.executed is False


def test_bulk_dm_is_refused_as_a_policy_violation():
    adapter = _social("x")
    with pytest.raises(AdapterError) as exc:
        adapter.send_direct_message(
            "person-1",
            text="hi",
            idempotency_key="dm-1",
            opt_in=True,
            bulk=True,
        )
    assert exc.value.code == ADAPTER_POLICY_FORBIDDEN_CODE


def test_social_idempotency_and_rate_limit():
    transport = RecordingTransport({"ok": True})
    clock = FakeClock()
    adapter = _social(
        "x",
        transport=transport,
        dry_run=False,
        rate_limit=1,
        rate_window_seconds=60,
        clock=clock,
    )
    first = adapter.publish_post(text="hello", idempotency_key="x-1")
    second = adapter.publish_post(text="hello", idempotency_key="x-1")
    assert first.executed is True
    assert second.replayed is True
    assert len(transport.calls) == 1

    with pytest.raises(AdapterError) as exc:
        adapter.fetch_metrics()
    assert exc.value.code == ADAPTER_RATE_LIMITED_CODE

    clock.advance(61)
    adapter.fetch_metrics()
    assert len(transport.calls) == 2


def test_social_idempotency_conflict():
    adapter = _social("x")
    adapter.publish_post(text="hello", idempotency_key="x-1")
    with pytest.raises(AdapterError) as exc:
        adapter.publish_post(text="different", idempotency_key="x-1")
    assert exc.value.code == ADAPTER_IDEMPOTENCY_CONFLICT_CODE


# -- CRM ----------------------------------------------------------------
def test_crm_missing_transport_and_credentials_are_typed():
    adapter_no_transport = CrmAdapter(credentials=CRM_CREDS)
    with pytest.raises(AdapterError) as exc:
        adapter_no_transport.get_contact("c-1")
    assert exc.value.code == ADAPTER_UNAVAILABLE_CODE

    adapter = _crm(credentials={})
    with pytest.raises(AdapterError) as exc:
        adapter.get_contact("c-1")
    assert exc.value.code == ADAPTER_NOT_CONFIGURED_CODE


def test_crm_upsert_is_idempotent_without_approval():
    transport = RecordingTransport({"ok": True})
    adapter = _crm(transport=transport, dry_run=False)
    first = adapter.upsert_contact(
        {"external_id": "c-1", "name": "Ada"}, idempotency_key="crm-1"
    )
    second = adapter.upsert_contact(
        {"external_id": "c-1", "name": "Ada"}, idempotency_key="crm-1"
    )
    assert first.executed is True
    assert second.replayed is True
    assert len(transport.calls) == 1

    with pytest.raises(AdapterError) as exc:
        adapter.upsert_contact(
            {"external_id": "c-1", "name": "Different"},
            idempotency_key="crm-1",
        )
    assert exc.value.code == ADAPTER_IDEMPOTENCY_CONFLICT_CODE


def test_crm_create_lead_requires_approval():
    adapter = _crm()
    with pytest.raises(AdapterError) as exc:
        adapter.create_lead({"name": "New lead"}, idempotency_key="lead-1")
    assert exc.value.code == ADAPTER_APPROVAL_REQUIRED_CODE

    token = ApprovalToken("apr-1", APPROVAL_CREATE_LEAD)
    result = adapter.create_lead(
        {"name": "New lead"}, idempotency_key="lead-1", approval=token
    )
    assert result.executed is False


def test_crm_bad_input_is_typed():
    adapter = _crm()
    with pytest.raises(AdapterError) as exc:
        adapter.upsert_contact({"name": "No id"}, idempotency_key="crm-2")
    assert exc.value.code == ADAPTER_BAD_INPUT_CODE


# -- email --------------------------------------------------------------
def test_email_missing_transport_and_credentials_are_typed():
    adapter_no_transport = EmailAdapter(credentials=EMAIL_CREDS)
    with pytest.raises(AdapterError) as exc:
        adapter_no_transport.send_email(
            to="a@example.com",
            subject="Hi",
            body="Body",
            idempotency_key="m-1",
        )
    assert exc.value.code == ADAPTER_UNAVAILABLE_CODE

    adapter = _email(credentials={})
    with pytest.raises(AdapterError) as exc:
        adapter.send_email(
            to="a@example.com",
            subject="Hi",
            body="Body",
            idempotency_key="m-1",
        )
    assert exc.value.code == ADAPTER_NOT_CONFIGURED_CODE


def test_email_send_requires_approval():
    adapter = _email()
    with pytest.raises(AdapterError) as exc:
        adapter.send_email(
            to="a@example.com",
            subject="Hi",
            body="Body",
            idempotency_key="m-1",
        )
    assert exc.value.code == ADAPTER_APPROVAL_REQUIRED_CODE

    token = ApprovalToken("apr-1", APPROVAL_SEND)
    result = adapter.send_email(
        to="a@example.com",
        subject="Hi",
        body="Body",
        idempotency_key="m-1",
        approval=token,
    )
    assert result.executed is False


def test_marketing_email_requires_opt_in():
    token = ApprovalToken("apr-1", APPROVAL_SEND)
    adapter = _email()
    with pytest.raises(AdapterError) as exc:
        adapter.send_email(
            to="a@example.com",
            subject="Sale",
            body="Buy now",
            idempotency_key="m-1",
            approval=token,
            marketing=True,
            opted_in=False,
        )
    assert exc.value.code == ADAPTER_CONSENT_REQUIRED_CODE

    result = adapter.send_email(
        to="a@example.com",
        subject="Sale",
        body="Buy now",
        idempotency_key="m-1",
        approval=token,
        marketing=True,
        opted_in=True,
    )
    assert result.executed is False


def test_email_idempotency_and_rate_limit():
    transport = RecordingTransport({"ok": True})
    clock = FakeClock()
    token = ApprovalToken("apr-1", APPROVAL_SEND)
    adapter = _email(
        transport=transport,
        dry_run=False,
        rate_limit=1,
        rate_window_seconds=60,
        clock=clock,
    )
    first = adapter.send_email(
        to="a@example.com",
        subject="Hi",
        body="Body",
        idempotency_key="m-1",
        approval=token,
    )
    second = adapter.send_email(
        to="a@example.com",
        subject="Hi",
        body="Body",
        idempotency_key="m-1",
        approval=token,
    )
    assert first.executed is True
    assert second.replayed is True
    assert len(transport.calls) == 1

    with pytest.raises(AdapterError) as exc:
        adapter.send_email(
            to="b@example.com",
            subject="Hi",
            body="Body",
            idempotency_key="m-2",
            approval=token,
        )
    assert exc.value.code == ADAPTER_RATE_LIMITED_CODE

    clock.advance(61)
    adapter.send_email(
        to="b@example.com",
        subject="Hi",
        body="Body",
        idempotency_key="m-2",
        approval=token,
    )
    assert len(transport.calls) == 2
