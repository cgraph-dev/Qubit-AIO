"""P1-03 catalog + policy snapshot semantics (hermetic)."""
from __future__ import annotations

import pytest

from voicebridge.business import policy as pol
from voicebridge.business.errors import BusinessError
from _phase1_helpers import (
    EXPIRES,
    ISSUED,
    NOW,
    READ_ACTION,
    READ_RESOURCE,
    READ_TOOL,
    SUBJECT,
    WRITE_ACTION,
    WRITE_RESOURCE,
    WRITE_TOOL,
    catalog,
    grant,
    read_grant,
    snapshot,
)


def test_catalog_unknown_capability_is_a_typed_refusal():
    cat = catalog()
    assert {c.capability_id for c in cat.list()} == {READ_TOOL, WRITE_TOOL}
    with pytest.raises(BusinessError) as exc:
        cat.get("etsy.nope")
    assert exc.value.code == pol.CAPABILITY_UNKNOWN_CODE

    empty = pol.CapabilityCatalog()
    with pytest.raises(BusinessError) as exc:
        empty.get(READ_TOOL)
    assert exc.value.code == pol.CAPABILITY_UNKNOWN_CODE


def test_compile_snapshot_is_deterministic_and_carries_versions():
    first = snapshot("ten_1")
    second = snapshot("ten_1")
    assert first.digest == second.digest
    assert first.snapshot_id == second.snapshot_id
    assert first.snapshot_id.startswith("psnap_")
    assert first.tool_versions == {READ_TOOL: "1.2.0", WRITE_TOOL: "3.0.0"}
    assert first.policy_revision == "pol-1"

    # A different grant set changes the digest; ordering does not.
    reordered = snapshot("ten_1", grants=(read_grant(),))
    assert reordered.digest != first.digest
    two = snapshot("ten_1", grants=(read_grant(), read_grant()))
    two_reversed = snapshot("ten_1", grants=(read_grant(), read_grant()))
    assert two.digest == two_reversed.digest


def test_snapshot_digest_excludes_the_random_clock_only_when_pinned():
    a = snapshot("ten_1", issued_at=ISSUED, expires_at=EXPIRES)
    b = snapshot("ten_1", issued_at=ISSUED, expires_at=EXPIRES)
    assert a.digest == b.digest
    c = snapshot("ten_1", issued_at=ISSUED + 1, expires_at=EXPIRES)
    assert c.digest != a.digest


def test_evaluate_is_deny_by_default():
    snap = snapshot("ten_1")  # no grants
    decision = pol.evaluate(
        snap, READ_TOOL, READ_RESOURCE, READ_ACTION, now=NOW
    )
    assert decision.allowed is False
    assert decision.reason == pol.CAPABILITY_DENIED_CODE


def test_evaluate_allows_a_matching_grant_and_reports_approval():
    snap = snapshot("ten_1", grants=(read_grant(),))
    decision = pol.evaluate(
        snap, READ_TOOL, READ_RESOURCE, READ_ACTION, now=NOW
    )
    assert decision.allowed is True
    assert decision.approval_required is False
    assert decision.snapshot_id == snap.snapshot_id
    assert decision.policy_revision == "pol-1"

    write = pol.evaluate(
        snapshot("ten_1", grants=(grant(
            WRITE_TOOL, WRITE_RESOURCE, WRITE_ACTION, approval="required"
        ),)),
        WRITE_TOOL,
        WRITE_RESOURCE,
        WRITE_ACTION,
        now=NOW,
    )
    assert write.allowed is True
    assert write.approval_required is True


def test_deny_wins_over_allow():
    snap = snapshot(
        "ten_1",
        grants=(read_grant(),),
        denials=(
            pol.Denial(
                subject=SUBJECT,
                capability=READ_TOOL,
                resource=READ_RESOURCE,
                action=READ_ACTION,
                reason="owner revoked this resource",
            ),
        ),
    )
    decision = pol.evaluate(
        snap, READ_TOOL, READ_RESOURCE, READ_ACTION, now=NOW
    )
    assert decision.allowed is False
    assert decision.reason == pol.CAPABILITY_DENIED_CODE
    assert "owner revoked" in decision.detail


def test_no_wildcard_entries_are_rejected():
    for bad in ("*", "all", "any"):
        with pytest.raises(BusinessError) as exc:
            pol.Grant(
                subject=bad,
                capability=READ_TOOL,
                resource=READ_RESOURCE,
                action=READ_ACTION,
            )
        assert exc.value.code == pol.POLICY_SNAPSHOT_INVALID_CODE
        with pytest.raises(BusinessError):
            pol.Grant(
                subject=SUBJECT,
                capability=READ_TOOL,
                resource=bad,
                action=READ_ACTION,
            )


def test_grant_and_snapshot_expiry_are_enforced():
    expired_grant = snapshot(
        "ten_1",
        grants=(
            grant(
                READ_TOOL,
                READ_RESOURCE,
                READ_ACTION,
                expires_at=NOW - 1,
            ),
        ),
    )
    assert pol.evaluate(
        expired_grant, READ_TOOL, READ_RESOURCE, READ_ACTION, now=NOW
    ).allowed is False

    live = snapshot("ten_1", grants=(read_grant(),))
    expired_snapshot = snapshot(
        "ten_1", grants=(read_grant(),), issued_at=ISSUED, expires_at=NOW - 1
    )
    assert expired_snapshot.is_expired(NOW) is True
    assert live.is_expired(NOW) is False
    decision = pol.evaluate(
        expired_snapshot, READ_TOOL, READ_RESOURCE, READ_ACTION, now=NOW
    )
    assert decision.allowed is False
    assert decision.reason == pol.POLICY_SNAPSHOT_EXPIRED_CODE


def test_identity_evaluator_seam_can_override_the_builtin_match():
    snap = snapshot("ten_1", grants=(read_grant(),))

    def deny_all(**_kwargs):
        return pol.Decision(
            allowed=False,
            reason=pol.CAPABILITY_DENIED_CODE,
            capability_id=READ_TOOL,
            snapshot_id=snap.snapshot_id,
            policy_revision=snap.policy_revision,
            detail="P1-01 identity says no",
        )

    decision = pol.evaluate(
        snap, READ_TOOL, READ_RESOURCE, READ_ACTION, now=NOW,
        identity_evaluator=deny_all,
    )
    assert decision.allowed is False
    assert decision.detail == "P1-01 identity says no"

    # Returning None falls through to the built-in deterministic evaluator.
    decision = pol.evaluate(
        snap, READ_TOOL, READ_RESOURCE, READ_ACTION, now=NOW,
        identity_evaluator=lambda **_kwargs: None,
    )
    assert decision.allowed is True


def test_compile_snapshot_rejects_unknown_catalog_capabilities():
    rogue = pol.Grant(
        subject=SUBJECT,
        capability="etsy.rogue",
        resource=READ_RESOURCE,
        action=READ_ACTION,
    )
    with pytest.raises(BusinessError) as exc:
        snapshot("ten_1", grants=(rogue,))
    assert exc.value.code == pol.CAPABILITY_UNKNOWN_CODE
