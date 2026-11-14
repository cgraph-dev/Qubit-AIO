"""P5-08 — multi-entity books and the business factory.

Hermetic: throwaway SQLite under ``tmp_path``. The tests pin the invariant the
Office depends on: identity records are separate, personal books exist first,
cloning copies structure only, and authority/budget/bank/credentials/agents/
treasury are never inherited implicitly.
"""
from __future__ import annotations

import sqlite3

import pytest

from voicebridge.business import business_factory as factory
from voicebridge.business import finance, identity
from voicebridge.business.errors import BusinessError
from voicebridge.business.store import BusinessStore

OWNER = "owner:trick"


def _store(tmp_path) -> BusinessStore:
    return BusinessStore(tmp_path / "business.db")


def _personal(tmp_path):
    store = _store(tmp_path)
    result = factory.create_personal_books(
        store, owner_name="Trick", owner_external_ref="trick"
    )
    return store, result


def _count(
    store: BusinessStore, table: str, where: str = "", params=()
) -> int:
    conn = sqlite3.connect(store.path)
    try:
        sql = f"SELECT COUNT(*) FROM {table}"
        if where:
            sql += f" WHERE {where}"
        return int(conn.execute(sql, params).fetchone()[0])
    finally:
        conn.close()


def _rows(store: BusinessStore, sql: str, params=()) -> list:
    conn = sqlite3.connect(store.path)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def _seed_source_power(store: BusinessStore, tenant_id: str, business_id: str):
    principal = identity.create_principal(
        store, tenant_id, kind="agent", external_ref="agent-1"
    )
    role = identity.create_role(store, tenant_id, "operator")
    identity.grant(
        store,
        tenant_id,
        subject_id=principal.principal_id,
        capability="tool:read",
        resource="shared",
        action="read",
        role_id=role.role_id,
    )
    with store.transaction() as conn:
        conn.execute(
            "INSERT INTO bos_budget(budget_id, tenant_id, business_id,"
            " currency, period, limit_minor, created_at)"
            " VALUES('bgt_source'||?,?,?,?,?,?,?)",
            (
                business_id[-6:],
                tenant_id,
                business_id,
                "USD",
                "2026-11",
                100000,
                "2026-11-01T00:00:00.000Z",
            ),
        )
        conn.execute(
            "INSERT INTO bos_agent(agent_id, tenant_id, business_id, role_id,"
            " department, display_name, human_owner, status, provider, model,"
            " reasoning_effort, max_tokens, schedule, timezone,"
            " max_concurrent_turns, permissions_json, safety_json,"
            " idempotency_key, request_hash, created_at, updated_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "agt_source",
                tenant_id,
                business_id,
                "role_source",
                "finance",
                "Source Agent",
                OWNER,
                "active",
                "test",
                "test-model",
                "low",
                1024,
                "manual",
                "UTC",
                1,
                "{}",
                "{}",
                "agent-source",
                "hash-source",
                "2026-11-01T00:00:00.000Z",
                "2026-11-01T00:00:00.000Z",
            ),
        )
    finance.post(
        store,
        tenant_id=tenant_id,
        business_id=business_id,
        lines=[("cash", "debit", 500), ("revenue", "credit", 500)],
        currency="USD",
        idempotency_key="source-sale",
    )
    store.record_artifact(
        tenant_id,
        business_id,
        kind="provider_receipt",
        name="receipt-source",
        body='{"amount_minor": 500}',
    )


def test_personal_books_create_separate_identity_records(tmp_path):
    store, result = _personal(tmp_path)
    assert result.created["tenant"]
    assert result.created["owner_principal"]
    assert result.created["legal_entity"]
    assert result.created["business"]
    assert result.created["brand"]
    entities = factory.list_legal_entities(store, result.tenant_id)
    assert [(entity.kind, entity.legal_entity_id) for entity in entities] == [
        ("personal", result.created["legal_entity"])
    ]
    principals = identity.list_principals(store, result.tenant_id)
    assert [p.kind for p in principals] == ["owner"]
    business = store.get_business(result.tenant_id, result.business_id)
    assert business.metadata["legal_entity_id"] == result.created["legal_entity"]
    assert result.created["brand"] != result.created["business"]
    assert result.created["brand"] != result.created["legal_entity"]


def test_company_entity_requires_personal_books_first(tmp_path):
    store = _store(tmp_path)
    tenant = store.create_tenant("Bare Holdings")
    with pytest.raises(BusinessError) as exc:
        factory.create_legal_entity(store, tenant.tenant_id, name="Bare LLC")
    assert exc.value.code == factory.FACTORY_PERSONAL_FIRST_CODE
    with pytest.raises(BusinessError) as exc:
        factory.create_business(store, tenant.tenant_id, name="Bare Biz")
    assert exc.value.code == factory.FACTORY_PERSONAL_FIRST_CODE


def test_business_is_bound_to_one_entity_and_has_its_own_brand(tmp_path):
    store, result = _personal(tmp_path)
    entity = factory.create_legal_entity(
        store,
        result.tenant_id,
        name="Acme LLC",
        jurisdiction="RO",
        registration_id="J40/1/2026",
    )
    created = factory.create_business(
        store,
        result.tenant_id,
        name="Acme Store",
        slug="acme-store",
        legal_entity_id=entity.legal_entity_id,
        metadata={"category": "retail"},
    )
    business = store.get_business(result.tenant_id, created.business_id)
    assert business.metadata["legal_entity_id"] == entity.legal_entity_id
    assert business.metadata["category"] == "retail"
    assert factory.business_legal_entity(
        store, result.tenant_id, created.business_id
    ).legal_entity_id == entity.legal_entity_id
    brand = factory.brand_for_business(
        store, result.tenant_id, created.business_id
    )
    assert brand is not None
    assert brand.business_id == created.business_id
    assert brand.brand_id == created.brand_id


def test_clone_copies_structure_only(tmp_path):
    store, result = _personal(tmp_path)
    source = factory.create_business(
        store,
        result.tenant_id,
        name="Source Co",
        slug="source-co",
        metadata={
            "category": "services",
            "timezone": "Europe/Bucharest",
            "description": "source business",
        },
    )
    _seed_source_power(store, result.tenant_id, source.business_id)
    grants_before = _count(store, "bos_grant")
    principals_before = _count(store, "bos_principal")
    budget_before = _count(store, "bos_budget")

    clone = factory.clone_business(
        store,
        result.tenant_id,
        source.business_id,
        name="Clone Co",
        slug="clone-co",
    )
    business = store.get_business(result.tenant_id, clone.business_id)
    assert business.metadata["category"] == "services"
    assert business.metadata["timezone"] == "Europe/Bucharest"
    assert business.metadata["legal_entity_id"] == result.created["legal_entity"]
    assert clone.not_copied
    assert {row["class"] for row in clone.not_copied} >= {
        "authority",
        "budget",
        "bank",
        "credentials",
        "agents",
        "treasury",
    }
    # No authority, budget, agent, ledger or artifact rows moved.
    assert _count(store, "bos_grant") == grants_before
    assert _count(store, "bos_principal") == principals_before
    assert _count(store, "bos_budget") == budget_before
    assert _count(
        store, "bos_budget", "business_id=?", (clone.business_id,)
    ) == 0
    assert _count(
        store, "bos_agent", "business_id=?", (clone.business_id,)
    ) == 0
    assert _count(
        store, "bos_ledger_entry", "business_id=?", (clone.business_id,)
    ) == 0
    assert _count(
        store, "bos_artifact", "business_id=?", (clone.business_id,)
    ) == 0


@pytest.mark.parametrize(
    "flag",
    [
        "inherit_authority",
        "inherit_budget",
        "inherit_credentials",
        "inherit_agents",
        "inherit_treasury",
        "inherit_bank",
    ],
)
def test_clone_refuses_implicit_inheritance_flags(tmp_path, flag):
    store, result = _personal(tmp_path)
    source = factory.create_business(
        store, result.tenant_id, name="Source", slug="source"
    )
    with pytest.raises(BusinessError) as exc:
        factory.clone_business(
            store,
            result.tenant_id,
            source.business_id,
            name="Clone",
            slug="clone",
            **{flag: True},
        )
    assert exc.value.code == factory.FACTORY_INHERIT_REFUSED_CODE
    # The refusal happens before any row is created.
    assert _count(store, "bos_business") == 2


def test_clone_refuses_template_authority_keys(tmp_path):
    store, result = _personal(tmp_path)
    factory.create_business(
        store, result.tenant_id, name="Source", slug="source"
    )
    with pytest.raises(BusinessError) as exc:
        factory.create_from_template(
            store,
            result.tenant_id,
            {"name": "Template", "budget": {"limit_minor": 10}},
            name="From Template",
            slug="from-template",
        )
    assert exc.value.code == factory.FACTORY_INHERIT_REFUSED_CODE
    with pytest.raises(BusinessError) as exc:
        factory.create_business(
            store,
            result.tenant_id,
            name="From Metadata",
            slug="from-metadata",
            metadata={"credentials": {"api_key": "x"}},
        )
    assert exc.value.code == factory.FACTORY_INHERIT_REFUSED_CODE
    assert _count(store, "bos_business") == 2


def test_cross_entity_ledger_isolation(tmp_path):
    store, result = _personal(tmp_path)
    entity_a = factory.create_legal_entity(
        store, result.tenant_id, name="Alpha LLC"
    )
    entity_b = factory.create_legal_entity(
        store, result.tenant_id, name="Beta LLC"
    )
    business_a = factory.create_business(
        store,
        result.tenant_id,
        name="Alpha Books",
        slug="alpha-books",
        legal_entity_id=entity_a.legal_entity_id,
    )
    business_b = factory.create_business(
        store,
        result.tenant_id,
        name="Beta Books",
        slug="beta-books",
        legal_entity_id=entity_b.legal_entity_id,
    )
    books_a = factory.entity_books(
        store, result.tenant_id, business_a.business_id
    )
    books_b = factory.entity_books(
        store, result.tenant_id, business_b.business_id
    )
    books_a.post(
        lines=[("cash", "debit", 300), ("revenue", "credit", 300)],
        currency="USD",
        idempotency_key="alpha-sale",
    )
    books_b.post(
        lines=[("cash", "debit", 900), ("revenue", "credit", 900)],
        currency="USD",
        idempotency_key="beta-sale",
    )
    assert books_a.summary()["entry_count"] == 2
    assert books_b.summary()["entry_count"] == 2
    assert books_a.summary()["debit_minor"] == 300
    assert books_b.summary()["debit_minor"] == 900
    assert books_a.namespace != books_b.namespace
    assert all(
        account.startswith(f"{books_a.namespace}:")
        for account in books_a.summary()["by_account"]
    )
    # A cross-entity read is a typed refusal, not a blended view.
    with pytest.raises(BusinessError) as exc:
        factory.entity_books(
            store,
            result.tenant_id,
            business_a.business_id,
            legal_entity_id=entity_b.legal_entity_id,
        )
    assert exc.value.code == factory.FACTORY_ENTITY_MISMATCH_CODE


def test_personal_and_entity_books_never_mix(tmp_path):
    store, result = _personal(tmp_path)
    entity = factory.create_legal_entity(store, result.tenant_id, name="Acme LLC")
    company = factory.create_business(
        store,
        result.tenant_id,
        name="Acme Books",
        slug="acme-books",
        legal_entity_id=entity.legal_entity_id,
    )
    personal_books = factory.entity_books(
        store, result.tenant_id, result.business_id
    )
    company_books = factory.entity_books(
        store, result.tenant_id, company.business_id
    )
    personal_books.post(
        lines=[("cash", "debit", 100), ("revenue", "credit", 100)],
        currency="USD",
        idempotency_key="personal-sale",
    )
    assert personal_books.summary()["entry_count"] == 2
    assert company_books.summary()["entry_count"] == 0
    assert personal_books.namespace == f"entity:{result.created['legal_entity']}"


def test_clone_across_tenants_uses_the_target_personal_entity(tmp_path):
    store = _store(tmp_path)
    source = factory.create_personal_books(store, owner_name="Source Owner")
    source_biz = factory.create_business(
        store, source.tenant_id, name="Source Biz", slug="source-biz"
    )
    target = factory.create_personal_books(store, owner_name="Target Owner")
    clone = factory.clone_business(
        store,
        source.tenant_id,
        source_biz.business_id,
        name="Target Biz",
        slug="target-biz",
        target_tenant_id=target.tenant_id,
    )
    assert clone.tenant_id == target.tenant_id
    assert clone.legal_entity_id == target.created["legal_entity"]
    assert clone.legal_entity_id != source.created["legal_entity"]
    assert clone.source_business_id == source_biz.business_id
    target_business = store.get_business(target.tenant_id, clone.business_id)
    assert target_business.metadata["legal_entity_id"] == target.created[
        "legal_entity"
    ]


def test_every_creation_emits_an_audit_and_an_event(tmp_path):
    store, result = _personal(tmp_path)
    entity = factory.create_legal_entity(store, result.tenant_id, name="Acme LLC")
    business = factory.create_business(
        store,
        result.tenant_id,
        name="Acme",
        slug="acme",
        legal_entity_id=entity.legal_entity_id,
    )
    factory.clone_business(
        store,
        result.tenant_id,
        business.business_id,
        name="Acme Clone",
        slug="acme-clone",
    )
    event_types = {
        row[0]
        for row in _rows(
            store, "SELECT DISTINCT type FROM bos_event WHERE tenant_id=?",
            (result.tenant_id,),
        )
    }
    assert "finance.legal_entity.created" in event_types
    assert "factory.business.created" in event_types
    assert "factory.brand.created" in event_types
    assert "factory.business.cloned" in event_types
    actions = {
        row[0]
        for row in _rows(
            store,
            "SELECT DISTINCT action FROM bos_audit_event WHERE tenant_id=?",
            (result.tenant_id,),
        )
    }
    assert "factory.entity.create" in actions
    assert "factory.business.create" in actions
    assert "factory.brand.create" in actions
    assert "factory.business.clone" in actions


def test_legal_entity_registry_is_tenant_scoped(tmp_path):
    store, result = _personal(tmp_path)
    other = factory.create_personal_books(store, owner_name="Other Owner")
    factory.create_legal_entity(store, result.tenant_id, name="Acme LLC")
    assert len(factory.list_legal_entities(store, result.tenant_id)) == 2
    assert len(factory.list_legal_entities(store, other.tenant_id)) == 1
    with pytest.raises(BusinessError):
        factory.get_legal_entity(
            store, other.tenant_id, result.created["legal_entity"]
        )


def test_template_without_forbidden_keys_is_structure_only(tmp_path):
    store, result = _personal(tmp_path)
    created = factory.create_from_template(
        store,
        result.tenant_id,
        {
            "template": "service-studio",
            "category": "services",
            "timezone": "Europe/Bucharest",
        },
        name="Studio",
        slug="studio",
    )
    business = store.get_business(result.tenant_id, created.business_id)
    assert business.metadata["category"] == "services"
    assert business.metadata["timezone"] == "Europe/Bucharest"
    assert business.metadata["template"] == "service-studio"
    assert _count(
        store, "bos_grant", "tenant_id=?", (result.tenant_id,)
    ) == 0
