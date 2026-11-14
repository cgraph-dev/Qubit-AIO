"""P1-06 execution-time approval binding inside the ToolGateway (hermetic)."""
from __future__ import annotations

import pytest

from voicebridge.business import approvals
from voicebridge.business import policy as pol
from voicebridge.business.errors import BusinessError
from voicebridge.business.ids import new_id
from voicebridge.business.util import json_dumps, now_iso
from _phase1_helpers import (
    NOW,
    READ_TOOL,
    SUBJECT,
    WRITE_ACTION,
    WRITE_RESOURCE,
    WRITE_TOOL,
    Recorder,
    catalog,
    gateway,
    intent_for,
    invoke,
    scope,
    snapshot,
    store,
    write_grant,
)


def _request(
    bos,
    tenant,
    business,
    tool,
    intent,
    *,
    key="apr-1",
    expires_at=NOW + 10_000,
    decide=True,
):
    approval, _ = approvals.request(
        bos,
        tenant_id=tenant.tenant_id,
        business_id=business.business_id,
        action=f"tool:{tool}",
        requested_by="owner",
        idempotency_key=key,
        payload=intent,
        expires_at=expires_at,
    )
    if decide:
        return approvals.decide(
            bos,
            tenant_id=tenant.tenant_id,
            approval_id=approval.approval_id,
            decision="approved",
            decided_by="owner",
        )
    return approval


def _write_snapshot(tenant_id):
    return snapshot(tenant_id, grants=(write_grant(),))


def _write_intent(payload, cost_minor=0):
    return intent_for(
        WRITE_TOOL, WRITE_RESOURCE, WRITE_ACTION, payload, cost_minor=cost_minor
    )


def test_approval_for_payload_a_cannot_execute_payload_b(tmp_path):
    bos = store(tmp_path)
    tenant, business = scope(bos)
    approved_payload = {"listing_id": "lst-1"}
    execution_payload = {"listing_id": "lst-2"}
    approval = _request(
        bos, tenant, business, WRITE_TOOL, _write_intent(approved_payload)
    )
    executor = Recorder()
    with pytest.raises(BusinessError) as exc:
        invoke(
            gateway(bos, executor),
            tenant,
            business,
            WRITE_TOOL,
            execution_payload,
            snap=_write_snapshot(tenant.tenant_id),
            cat=catalog(),
            approval_id=approval.approval_id,
        )
    assert exc.value.code == "BUSINESS_APPROVAL_REQUIRED"
    assert "different payload" in exc.value.detail
    assert executor.calls == []


def test_cost_change_breaks_the_intent_binding(tmp_path):
    bos = store(tmp_path)
    tenant, business = scope(bos)
    payload = {"listing_id": "lst-1"}
    approval = _request(
        bos, tenant, business, WRITE_TOOL, _write_intent(payload, cost_minor=500)
    )
    with pytest.raises(BusinessError) as exc:
        invoke(
            gateway(bos, Recorder()), tenant, business, WRITE_TOOL, payload,
            snap=_write_snapshot(tenant.tenant_id), cat=catalog(),
            approval_id=approval.approval_id, cost_minor=900,
        )
    assert exc.value.code == "BUSINESS_APPROVAL_REQUIRED"


def test_expired_approval_is_refused(tmp_path):
    bos = store(tmp_path)
    tenant, business = scope(bos)
    payload = {"listing_id": "lst-1"}
    approval = _request(
        bos, tenant, business, WRITE_TOOL, _write_intent(payload),
        expires_at=NOW - 1,
    )
    with pytest.raises(BusinessError) as exc:
        invoke(
            gateway(bos, Recorder()), tenant, business, WRITE_TOOL, payload,
            snap=_write_snapshot(tenant.tenant_id), cat=catalog(),
            approval_id=approval.approval_id,
        )
    assert exc.value.code == "BUSINESS_APPROVAL_EXPIRED"


def test_pending_or_missing_approval_fails_closed(tmp_path):
    bos = store(tmp_path)
    tenant, business = scope(bos)
    payload = {"listing_id": "lst-1"}
    pending = _request(
        bos, tenant, business, WRITE_TOOL, _write_intent(payload), decide=False
    )
    with pytest.raises(BusinessError) as exc:
        invoke(
            gateway(bos, Recorder()), tenant, business, WRITE_TOOL, payload,
            snap=_write_snapshot(tenant.tenant_id), cat=catalog(),
            approval_id=pending.approval_id,
        )
    assert exc.value.code == "BUSINESS_APPROVAL_REQUIRED"
    assert "not 'approved'" in exc.value.detail

    with pytest.raises(BusinessError) as exc:
        invoke(
            gateway(bos, Recorder()), tenant, business, WRITE_TOOL, payload,
            snap=_write_snapshot(tenant.tenant_id), cat=catalog(),
        )
    assert exc.value.code == "BUSINESS_APPROVAL_REQUIRED"
    assert "no matching approval" in exc.value.detail


def test_wrong_action_approval_is_refused(tmp_path):
    bos = store(tmp_path)
    tenant, business = scope(bos)
    payload = {"listing_id": "lst-1"}
    # The approval is bound to a different tool action.
    approval = _request(
        bos, tenant, business, READ_TOOL, _write_intent(payload)
    )
    with pytest.raises(BusinessError) as exc:
        invoke(
            gateway(bos, Recorder()), tenant, business, WRITE_TOOL, payload,
            snap=_write_snapshot(tenant.tenant_id), cat=catalog(),
            approval_id=approval.approval_id,
        )
    assert exc.value.code == "BUSINESS_APPROVAL_REQUIRED"
    assert "does not bind" in exc.value.detail


def test_denied_capability_fails_even_with_a_valid_approval(tmp_path):
    bos = store(tmp_path)
    tenant, business = scope(bos)
    payload = {"listing_id": "lst-1"}
    approval = _request(
        bos, tenant, business, WRITE_TOOL, _write_intent(payload)
    )
    snap = snapshot(
        tenant.tenant_id,
        grants=(write_grant(),),
        denials=(
            pol.Denial(
                subject=SUBJECT,
                capability=WRITE_TOOL,
                resource=WRITE_RESOURCE,
                action=WRITE_ACTION,
                reason="publishing revoked",
            ),
        ),
    )
    executor = Recorder()
    with pytest.raises(BusinessError) as exc:
        invoke(
            gateway(bos, executor), tenant, business, WRITE_TOOL, payload,
            snap=snap, cat=catalog(), approval_id=approval.approval_id,
        )
    assert exc.value.code == "BUSINESS_TOOL_NOT_ALLOWED"
    assert "publishing revoked" in exc.value.detail
    assert executor.calls == []


def test_approval_from_another_tenant_cannot_execute(tmp_path):
    bos = store(tmp_path)
    tenant, business = scope(bos)
    other_tenant, other_business = scope(bos, name="Globex", slug="globex")
    payload = {"listing_id": "lst-1"}
    foreign = _request(
        bos, other_tenant, other_business, WRITE_TOOL, _write_intent(payload)
    )
    with pytest.raises(BusinessError) as exc:
        invoke(
            gateway(bos, Recorder()), tenant, business, WRITE_TOOL, payload,
            snap=_write_snapshot(tenant.tenant_id), cat=catalog(),
            approval_id=foreign.approval_id,
        )
    assert exc.value.code == "BUSINESS_APPROVAL_REQUIRED"


def test_a_direct_intent_digest_approval_row_binds_exactly(tmp_path):
    """The literal ``request_hash == sha256(intent)`` shape also binds."""
    bos = store(tmp_path)
    tenant, business = scope(bos)
    payload = {"listing_id": "lst-1"}
    intent = _write_intent(payload)
    approval_id = new_id("apr")
    with bos.transaction() as conn:
        conn.execute(
            "INSERT INTO bos_approval(approval_id, tenant_id, business_id,"
            " job_id, action, status, requested_by, reason, idempotency_key,"
            " request_hash, policy_version, payload_json, created_at,"
            " expires_at) VALUES(?,?,?,NULL,?,'approved',?,'',?,?,?,?,?,?)",
            (
                approval_id,
                tenant.tenant_id,
                business.business_id,
                f"tool:{WRITE_TOOL}",
                "owner",
                "direct-intent",
                pol.intent_digest(intent),
                "pol-1",
                json_dumps(intent),
                now_iso(),
                NOW + 10_000,
            ),
        )
    outcome = invoke(
        gateway(bos, Recorder({"ok": True})),
        tenant,
        business,
        WRITE_TOOL,
        payload,
        snap=_write_snapshot(tenant.tenant_id),
        cat=catalog(),
        approval_id=approval_id,
    )
    assert outcome.executed is True
