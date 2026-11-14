"""P1-03 ToolGateway enforcement over a frozen capability snapshot (hermetic)."""
from __future__ import annotations

import pytest

from voicebridge.business import approvals
from voicebridge.business import policy as pol
from voicebridge.business.errors import BusinessError
from _phase1_helpers import (
    NOW,
    READ_TOOL,
    WRITE_ACTION,
    WRITE_RESOURCE,
    WRITE_TOOL,
    Recorder,
    catalog,
    gateway,
    intent_for,
    invoke,
    read_grant,
    scope,
    snapshot,
    store,
    write_grant,
)


def _approved(
    bos,
    tenant,
    business,
    tool,
    intent,
    *,
    key="apr-1",
    expires_at=NOW + 10_000,
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
    return approvals.decide(
        bos,
        tenant_id=tenant.tenant_id,
        approval_id=approval.approval_id,
        decision="approved",
        decided_by="owner",
    )


def test_unknown_tool_is_denied_even_when_an_executor_exists(tmp_path):
    bos = store(tmp_path)
    tenant, business = scope(bos)
    executor = Recorder()
    snap = snapshot(tenant.tenant_id, grants=(read_grant(),))
    with pytest.raises(BusinessError) as exc:
        invoke(
            gateway(bos, executor),
            tenant,
            business,
            "etsy.rogue.read",
            {},
            snap=snap,
            cat=catalog(),
        )
    assert exc.value.code == pol.CAPABILITY_UNKNOWN_CODE
    assert executor.calls == []


def test_empty_snapshot_allows_nothing(tmp_path):
    bos = store(tmp_path)
    tenant, business = scope(bos)
    executor = Recorder()
    with pytest.raises(BusinessError) as exc:
        invoke(
            gateway(bos, executor),
            tenant,
            business,
            READ_TOOL,
            {},
            snap=snapshot(tenant.tenant_id),
            cat=catalog(),
        )
    assert exc.value.code == "BUSINESS_TOOL_NOT_ALLOWED"
    assert "deny by default" in exc.value.detail
    assert executor.calls == []


def test_capability_absent_from_snapshot_is_denied_even_if_catalogued(tmp_path):
    bos = store(tmp_path)
    tenant, business = scope(bos)
    executor = Recorder()
    snap = snapshot(tenant.tenant_id, grants=(write_grant(),))
    with pytest.raises(BusinessError) as exc:
        invoke(
            gateway(bos, executor), tenant, business, READ_TOOL, {},
            snap=snap, cat=catalog(),
        )
    assert exc.value.code == "BUSINESS_TOOL_NOT_ALLOWED"
    assert executor.calls == []


def test_expired_snapshot_is_refused(tmp_path):
    bos = store(tmp_path)
    tenant, business = scope(bos)
    snap = snapshot(
        tenant.tenant_id,
        grants=(read_grant(),),
        issued_at=NOW - 1_000,
        expires_at=NOW - 1,
    )
    with pytest.raises(BusinessError) as exc:
        invoke(
            gateway(bos, Recorder()), tenant, business, READ_TOOL, {},
            snap=snap, cat=catalog(),
        )
    assert exc.value.code == pol.POLICY_SNAPSHOT_EXPIRED_CODE


def test_cross_tenant_and_cross_subject_snapshots_are_refused(tmp_path):
    bos = store(tmp_path)
    tenant, business = scope(bos)
    other_tenant, _ = scope(bos, name="Globex", slug="globex")
    cat = catalog()
    g = read_grant()

    cross_tenant = snapshot(other_tenant.tenant_id, grants=(g,))
    with pytest.raises(BusinessError) as exc:
        invoke(
            gateway(bos, Recorder()), tenant, business, READ_TOOL, {},
            snap=cross_tenant, cat=cat,
        )
    assert exc.value.code == pol.POLICY_SNAPSHOT_SCOPE_CODE

    cross_subject = snapshot(tenant.tenant_id, grants=(g,), subject="agent:other")
    with pytest.raises(BusinessError) as exc:
        invoke(
            gateway(bos, Recorder()), tenant, business, READ_TOOL, {},
            snap=cross_subject, cat=cat,
        )
    assert exc.value.code == pol.POLICY_SNAPSHOT_SCOPE_CODE


def test_allowed_read_capability_executes(tmp_path):
    bos = store(tmp_path)
    tenant, business = scope(bos)
    executor = Recorder({"shop_id": "shop-1"})
    outcome = invoke(
        gateway(bos, executor),
        tenant,
        business,
        READ_TOOL,
        {"shop_id": "shop-1"},
        snap=snapshot(tenant.tenant_id, grants=(read_grant(),)),
        cat=catalog(),
    )
    assert outcome.executed is True
    assert outcome.invocation.status == "success"
    assert len(executor.calls) == 1


def test_allowed_write_with_bound_approval_executes(tmp_path):
    bos = store(tmp_path)
    tenant, business = scope(bos)
    cat = catalog()
    payload = {"listing_id": "lst-1"}
    intent = intent_for(WRITE_TOOL, WRITE_RESOURCE, WRITE_ACTION, payload)
    approval = _approved(bos, tenant, business, WRITE_TOOL, intent)
    executor = Recorder({"published": True})
    outcome = invoke(
        gateway(bos, executor),
        tenant,
        business,
        WRITE_TOOL,
        payload,
        snap=snapshot(tenant.tenant_id, grants=(write_grant(),)),
        cat=cat,
        approval_id=approval.approval_id,
    )
    assert outcome.executed is True
    assert len(executor.calls) == 1


def test_dry_run_never_executes_even_with_a_snapshot(tmp_path):
    bos = store(tmp_path)
    tenant, business = scope(bos)
    executor = Recorder()
    outcome = invoke(
        gateway(bos, executor),
        tenant,
        business,
        WRITE_TOOL,
        {"listing_id": "lst-1"},
        snap=snapshot(tenant.tenant_id, grants=(write_grant(),)),
        cat=catalog(),
        dry_run=True,
    )
    assert outcome.executed is False
    assert outcome.invocation.status == "dry_run"
    assert outcome.invocation.result["approval_required"] is True
    assert executor.calls == []


def test_resource_or_action_mismatch_is_refused(tmp_path):
    bos = store(tmp_path)
    tenant, business = scope(bos)
    snap = snapshot(tenant.tenant_id, grants=(read_grant(),))
    with pytest.raises(BusinessError) as exc:
        invoke(
            gateway(bos, Recorder()), tenant, business, READ_TOOL, {},
            snap=snap, cat=catalog(), resource="etsy.other",
        )
    assert exc.value.code == pol.CAPABILITY_ARGS_MISMATCH_CODE

    with pytest.raises(BusinessError) as exc:
        invoke(
            gateway(bos, Recorder()), tenant, business, READ_TOOL, {},
            snap=snap, cat=catalog(), action="write",
        )
    assert exc.value.code == pol.CAPABILITY_ARGS_MISMATCH_CODE


def test_audit_records_snapshot_digest_and_decision(tmp_path):
    bos = store(tmp_path)
    tenant, business = scope(bos)
    snap = snapshot(tenant.tenant_id, grants=(read_grant(),))
    invoke(
        gateway(bos, Recorder()), tenant, business, READ_TOOL, {},
        snap=snap, cat=catalog(),
    )
    rows = bos.list_audit(tenant.tenant_id, action="tool.invoke")
    assert len(rows) == 1
    detail = rows[0].detail
    assert detail["policy_snapshot_digest"] == snap.digest
    assert detail["policy_snapshot_id"] == snap.snapshot_id
    assert detail["policy_revision"] == "pol-1"
    assert detail["policy_decision"] == "allow"
    assert detail["policy_capability"] == READ_TOOL
