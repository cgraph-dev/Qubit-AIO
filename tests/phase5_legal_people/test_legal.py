"""P5-06 contract/obligation/renewal and jurisdiction-rule contracts."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from voicebridge.business import approvals
from voicebridge.business import legal
from voicebridge.business.errors import BusinessError


def _entity(store, tenant_id, *, kind="personal", jurisdiction="RO", name="Owner"):
    return legal.create_entity(
        store,
        tenant_id,
        name=name,
        jurisdiction=jurisdiction,
        kind=kind,
        tax_id="RO-TAX-1",
        currency="RON",
    )


def _ensure_rules(store, tenant_id, jurisdiction="RO"):
    if jurisdiction != "RO" or jurisdiction in legal.known_jurisdictions(
        store, tenant_id
    ):
        return
    legal.register_rule(
        store,
        tenant_id,
        jurisdiction=jurisdiction,
        rule_key="vat_rate",
        version=1,
        effective_from="2020-01-01T00:00:00Z",
        payload={"rate_percent": 19},
    )


def _contract(store, tenant, business, entity):
    _ensure_rules(store, tenant.tenant_id, entity["jurisdiction"])
    return legal.create_contract(
        store,
        tenant.tenant_id,
        business_id=business.business_id,
        entity_id=entity["entity_id"],
        title="Office lease",
        counterparty="Landlord SRL",
    )


def _clauses():
    return [
        {"clause_id": "term", "title": "Term", "body": "twelve months"},
        {"clause_id": "rent", "title": "Rent", "body": "1000 RON monthly"},
    ]


def _request_and_approve(store, tenant, contract, version, *, approver="boss"):
    request = legal.request_signing(
        store,
        tenant.tenant_id,
        contract["contract_id"],
        version["version_id"],
        requested_by="alice",
        idempotency_key="sign-1",
    )
    approvals.decide(
        store,
        tenant_id=tenant.tenant_id,
        approval_id=request["approval_id"],
        decision="approved",
        decided_by=approver,
    )
    return request


def test_contract_versions_and_clause_diff(store, scope):
    tenant, business = scope
    entity = _entity(store, tenant.tenant_id)
    contract = _contract(store, tenant, business, entity)

    first = legal.add_version(
        store, tenant.tenant_id, contract["contract_id"], clauses=_clauses()
    )
    second_clauses = [
        {"clause_id": "term", "title": "Term", "body": "twenty-four months"},
        {"clause_id": "notice", "title": "Notice", "body": "30 days"},
    ]
    second = legal.add_version(
        store,
        tenant.tenant_id,
        contract["contract_id"],
        clauses=second_clauses,
        change_note="extension",
    )
    assert (first["version"], second["version"]) == (1, 2)
    assert first["content_sha256"] != second["content_sha256"]
    assert len(legal.list_versions(store, tenant.tenant_id, contract["contract_id"])) == 2

    diff = legal.diff_versions(
        store, tenant.tenant_id, contract["contract_id"], 1, 2
    )
    assert [item["clause_id"] for item in diff["added"]] == ["notice"]
    assert [item["clause_id"] for item in diff["removed"]] == ["rent"]
    assert [item["clause_id"] for item in diff["changed"]] == ["term"]
    assert diff["changed"][0]["before_sha256"] != diff["changed"][0]["after_sha256"]

    with pytest.raises(BusinessError) as exc:
        legal.add_version(
            store,
            tenant.tenant_id,
            contract["contract_id"],
            clauses=[
                {"clause_id": "x", "title": "X", "body": "1"},
                {"clause_id": "x", "title": "X2", "body": "2"},
            ],
        )
    assert exc.value.code == legal.LEGAL_DUPLICATE_CLAUSE_CODE


def test_signing_without_approval_is_refused(store, scope):
    tenant, business = scope
    entity = _entity(store, tenant.tenant_id)
    contract = _contract(store, tenant, business, entity)
    version = legal.add_version(
        store, tenant.tenant_id, contract["contract_id"], clauses=_clauses()
    )

    with pytest.raises(BusinessError) as exc:
        legal.record_signing(
            store,
            tenant.tenant_id,
            contract["contract_id"],
            "apr_missing",
            signed_by="alice",
        )
    assert exc.value.code == legal.LEGAL_SIGNING_APPROVAL_REQUIRED_CODE

    request = legal.request_signing(
        store,
        tenant.tenant_id,
        contract["contract_id"],
        version["version_id"],
        requested_by="alice",
        idempotency_key="sign-pending",
    )
    with pytest.raises(BusinessError) as exc:
        legal.record_signing(
            store,
            tenant.tenant_id,
            contract["contract_id"],
            request["approval_id"],
            signed_by="alice",
        )
    assert exc.value.code == legal.LEGAL_SIGNING_APPROVAL_REQUIRED_CODE

    approvals.decide(
        store,
        tenant_id=tenant.tenant_id,
        approval_id=request["approval_id"],
        decision="rejected",
        decided_by="boss",
    )
    with pytest.raises(BusinessError) as exc:
        legal.record_signing(
            store,
            tenant.tenant_id,
            contract["contract_id"],
            request["approval_id"],
            signed_by="alice",
        )
    assert exc.value.code == legal.LEGAL_SIGNING_APPROVAL_REQUIRED_CODE
    assert legal.get_contract(store, tenant.tenant_id, contract["contract_id"])["status"] == "pending_signature"


def test_signing_binds_exact_payload_and_tamper_is_refused(store, scope):
    tenant, business = scope
    entity = _entity(store, tenant.tenant_id)
    contract = _contract(store, tenant, business, entity)
    version = legal.add_version(
        store, tenant.tenant_id, contract["contract_id"], clauses=_clauses()
    )
    request = _request_and_approve(store, tenant, contract, version)

    expected = legal.signing_payload(
        store, tenant.tenant_id, contract["contract_id"], version["version_id"]
    )
    assert request["payload_hash"] == approvals.intent_hash(expected)
    assert request["payload"] == expected

    # Tampering with the stored signing binding is caught before a signature.
    conn = sqlite3.connect(store.path)
    try:
        conn.execute(
            "UPDATE bos_legal_signing SET payload_hash='deadbeef'"
            " WHERE tenant_id=? AND approval_id=?",
            (tenant.tenant_id, request["approval_id"]),
        )
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(BusinessError) as exc:
        legal.record_signing(
            store,
            tenant.tenant_id,
            contract["contract_id"],
            request["approval_id"],
            signed_by="clerk",
        )
    assert exc.value.code == legal.LEGAL_SIGNING_APPROVAL_MISMATCH_CODE
    assert legal.get_contract(store, tenant.tenant_id, contract["contract_id"])["status"] == "pending_signature"


def test_signed_contract_requires_separation_of_duties(store, scope):
    tenant, business = scope
    entity = _entity(store, tenant.tenant_id)
    contract = _contract(store, tenant, business, entity)
    version = legal.add_version(
        store, tenant.tenant_id, contract["contract_id"], clauses=_clauses()
    )
    request = legal.request_signing(
        store,
        tenant.tenant_id,
        contract["contract_id"],
        version["version_id"],
        requested_by="alice",
        idempotency_key="sod",
    )
    # A human requester can decide in the generic approvals API, but the legal
    # module refuses to execute a self-approved commitment.
    approvals.decide(
        store,
        tenant_id=tenant.tenant_id,
        approval_id=request["approval_id"],
        decision="approved",
        decided_by="alice",
    )
    with pytest.raises(BusinessError) as exc:
        legal.record_signing(
            store,
            tenant.tenant_id,
            contract["contract_id"],
            request["approval_id"],
            signed_by="clerk",
        )
    assert exc.value.code == legal.LEGAL_SIGNING_SELF_APPROVAL_CODE

    # A second contract approved by a different human signs cleanly.
    second = _contract(store, tenant, business, entity)
    second_version = legal.add_version(
        store, tenant.tenant_id, second["contract_id"], clauses=_clauses()
    )
    fresh = legal.request_signing(
        store,
        tenant.tenant_id,
        second["contract_id"],
        second_version["version_id"],
        requested_by="alice",
        idempotency_key="sod-fresh",
    )
    approvals.decide(
        store,
        tenant_id=tenant.tenant_id,
        approval_id=fresh["approval_id"],
        decision="approved",
        decided_by="boss",
    )
    signed = legal.record_signing(
        store,
        tenant.tenant_id,
        second["contract_id"],
        fresh["approval_id"],
        signed_by="clerk",
        accepted_by="owner",
    )
    assert signed["status"] == "signed"
    assert signed["real_signature"] is False
    assert signed["legal_advice"] is False
    stored = legal.get_contract(store, tenant.tenant_id, second["contract_id"])
    assert stored["status"] == "signed"
    assert stored["signed_version_id"] == second_version["version_id"]


def test_obligations_and_renewals_are_due_from_rows(store, scope):
    tenant, business = scope
    entity = _entity(store, tenant.tenant_id)
    contract = _contract(store, tenant, business, entity)
    version = legal.add_version(
        store, tenant.tenant_id, contract["contract_id"], clauses=_clauses()
    )
    request = _request_and_approve(store, tenant, contract, version)
    legal.record_signing(
        store,
        tenant.tenant_id,
        contract["contract_id"],
        request["approval_id"],
        signed_by="clerk",
    )
    renewal = legal.add_obligation(
        store,
        tenant.tenant_id,
        contract["contract_id"],
        kind="renewal",
        description="renew the lease",
        due_at="2020-01-01T00:00:00Z",
    )
    legal.add_obligation(
        store,
        tenant.tenant_id,
        contract["contract_id"],
        kind="payment",
        description="future rent",
        due_at="2999-01-01T00:00:00Z",
    )
    due = legal.due_obligations(store, tenant.tenant_id)
    assert [item["obligation_id"] for item in due] == [renewal["obligation_id"]]
    assert due[0]["overdue"] is True
    assert [item["kind"] for item in legal.due_renewals(store, tenant.tenant_id)] == [
        "renewal"
    ]

    renewed = legal.renew_contract(
        store,
        tenant.tenant_id,
        contract["contract_id"],
        effective_at="2030-01-01T00:00:00Z",
    )
    assert renewed["version"] == 2
    assert legal.get_contract(store, tenant.tenant_id, contract["contract_id"])["status"] == "draft"
    done = legal.complete_obligation(
        store, tenant.tenant_id, renewal["obligation_id"], completed_by="alice"
    )
    assert done["status"] == "done"
    assert legal.due_renewals(store, tenant.tenant_id) == []


def test_jurisdiction_effective_dates_and_unknown_refused(store, scope):
    tenant, _business = scope
    legal.register_rule(
        store,
        tenant.tenant_id,
        jurisdiction="RO",
        rule_key="vat_rate",
        version=1,
        effective_from="2020-01-01T00:00:00Z",
        payload={"rate_percent": 19},
    )
    legal.register_rule(
        store,
        tenant.tenant_id,
        jurisdiction="RO",
        rule_key="vat_rate",
        version=2,
        effective_from="2030-01-01T00:00:00Z",
        payload={"rate_percent": 21},
    )
    now = legal.evaluate_rule(
        store, tenant.tenant_id, jurisdiction="ro", rule_key="vat_rate"
    )
    future = legal.evaluate_rule(
        store,
        tenant.tenant_id,
        jurisdiction="RO",
        rule_key="vat_rate",
        as_of=datetime(2031, 1, 1, tzinfo=timezone.utc).timestamp(),
    )
    assert now["version"] == 1 and now["payload"]["rate_percent"] == 19
    assert future["version"] == 2 and future["payload"]["rate_percent"] == 21
    assert now["disclaimer"] == legal.DISCLAIMER

    with pytest.raises(BusinessError) as exc:
        legal.evaluate_rule(
            store, tenant.tenant_id, jurisdiction="XX", rule_key="vat_rate"
        )
    assert exc.value.code == legal.LEGAL_JURISDICTION_UNKNOWN_CODE

    with pytest.raises(BusinessError) as exc:
        legal.evaluate_rule(
            store, tenant.tenant_id, jurisdiction="RO", rule_key="missing"
        )
    assert exc.value.code == legal.LEGAL_RULE_UNKNOWN_CODE

    # An unknown jurisdiction also blocks a signing request before an approval
    # can be created.
    entity = _entity(store, tenant.tenant_id, jurisdiction="ZZ")
    contract = _contract(store, tenant, _business, entity)
    version = legal.add_version(
        store, tenant.tenant_id, contract["contract_id"], clauses=_clauses()
    )
    with pytest.raises(BusinessError) as exc:
        legal.request_signing(
            store,
            tenant.tenant_id,
            contract["contract_id"],
            version["version_id"],
            requested_by="alice",
            idempotency_key="unknown-jurisdiction",
        )
    assert exc.value.code == legal.LEGAL_JURISDICTION_UNKNOWN_CODE


def test_tenant_and_entity_isolation(store, scope):
    tenant, business = scope
    personal = _entity(store, tenant.tenant_id, kind="personal", name="Owner")
    company = _entity(store, tenant.tenant_id, kind="company", name="Acme SRL")
    personal_contract = _contract(store, tenant, business, personal)
    company_contract = _contract(store, tenant, business, company)
    assert [
        item["contract_id"]
        for item in legal.entity_contracts(store, tenant.tenant_id, personal["entity_id"])
    ] == [personal_contract["contract_id"]]
    assert [
        item["contract_id"]
        for item in legal.entity_contracts(store, tenant.tenant_id, company["entity_id"])
    ] == [company_contract["contract_id"]]

    other = store.create_tenant("Other")
    with pytest.raises(BusinessError):
        legal.get_contract(store, other.tenant_id, personal_contract["contract_id"])
    with pytest.raises(BusinessError):
        legal.get_entity(store, other.tenant_id, personal["entity_id"])
    other_business = store.create_business(other.tenant_id, "Other Biz", slug="other")
    with pytest.raises(BusinessError):
        legal.create_contract(
            store,
            other.tenant_id,
            business_id=other_business.business_id,
            entity_id=personal["entity_id"],
            title="cross tenant",
            counterparty="x",
        )
