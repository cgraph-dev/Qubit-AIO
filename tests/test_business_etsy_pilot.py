"""Etsy digital-shop pilot bootstrap: one pending, owner-first War Room session.

Hermetic: a throwaway ``business.db`` under ``tmp_path`` and a network
tripwire. The bootstrap records the shipped playbook, labelled evidence and
experiments, and must never approve, create tasks, publish, spend or contact
anyone.
"""
from __future__ import annotations

import http.client
import socket
import sqlite3
import urllib.request

import pytest

from voicebridge.business import BusinessStore, warroom
from voicebridge.business.errors import (
    RESEARCH_NOT_CONFIGURED_CODE,
    BusinessError,
)
from voicebridge.business.pilot import (
    PILOT_TITLE,
    PLAYBOOK_SOURCE_LOCATOR,
    bootstrap_etsy_pilot,
    render_playbook_text,
)
from voicebridge.integrations import etsy_playbook


@pytest.fixture()
def no_network(monkeypatch):
    def tripwire(*_args, **_kwargs):
        raise AssertionError("the Etsy pilot attempted network I/O")

    monkeypatch.setattr(socket, "socket", tripwire)
    monkeypatch.setattr(socket, "create_connection", tripwire)
    monkeypatch.setattr(socket, "create_server", tripwire)
    monkeypatch.setattr(http.client, "HTTPConnection", tripwire)
    monkeypatch.setattr(http.client, "HTTPSConnection", tripwire)
    monkeypatch.setattr(urllib.request, "urlopen", tripwire)


def _store(tmp_path) -> BusinessStore:
    return BusinessStore(tmp_path / "business.db")


def _scope(store: BusinessStore):
    tenant = store.create_tenant("Personal")
    business = store.create_business(tenant.tenant_id, "Personal", slug="personal")
    return tenant, business


def _count(store: BusinessStore, table: str) -> int:
    conn = sqlite3.connect(store.path)
    try:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    finally:
        conn.close()


def test_render_playbook_text_is_deterministic_and_complete():
    text = render_playbook_text()
    assert text == render_playbook_text()
    assert etsy_playbook.PILOT_NAME in text
    assert "approval_points:" in text
    for section in etsy_playbook.SECTIONS:
        assert section.title in text


def test_pilot_creates_one_pending_session_with_labelled_evidence(
        tmp_path, no_network):
    store = _store(tmp_path)
    tenant, business = _scope(store)

    result = bootstrap_etsy_pilot(
        store, tenant_id=tenant.tenant_id,
        business_id=business.business_id,
    )

    assert result.created is True
    assert result.session.title == PILOT_TITLE
    assert result.session.state == "owner_decision"
    assert result.session.business_id == business.business_id
    assert result.research_provider == "local-deterministic"

    sources = warroom.list_sources(store, tenant.tenant_id, result.session.session_id)
    assert len(sources) == 1
    source = sources[0]
    assert source.locator == PLAYBOOK_SOURCE_LOCATOR
    assert source.status == "extracted"
    assert source.text and etsy_playbook.PILOT_NAME in source.text

    evidence = warroom.list_evidence(store, tenant.tenant_id, result.session.session_id)
    labels = {item.label for item in evidence}
    assert labels == {"FACT", "INFERENCE", "ASSUMPTION", "UNKNOWN"}
    facts = [item for item in evidence if item.label == "FACT"]
    assert facts and all(item.source_id == source.source_id for item in facts)

    experiments = warroom.list_experiments(
        store, tenant.tenant_id, result.session.session_id)
    assert {item.name for item in experiments} == {
        "Niche demand check",
        "First listing conversion test",
        "Licence audit",
    }

    # Pending owner decision: no decision, no tasks, no approvals, no spend.
    assert warroom.list_decisions(store, tenant.tenant_id,
                                  result.session.session_id) == []
    assert _count(store, "bos_warroom_decision") == 0
    assert _count(store, "bos_task") == 0
    assert _count(store, "bos_approval") == 0
    assert _count(store, "bos_warroom_session") == 1


def test_pilot_is_idempotent(tmp_path, no_network):
    store = _store(tmp_path)
    tenant, business = _scope(store)

    first = bootstrap_etsy_pilot(
        store, tenant_id=tenant.tenant_id, business_id=business.business_id)
    second = bootstrap_etsy_pilot(
        store, tenant_id=tenant.tenant_id, business_id=business.business_id)

    assert first.created is True
    assert second.created is False
    assert second.session.session_id == first.session.session_id
    assert _count(store, "bos_warroom_session") == 1
    assert _count(store, "bos_warroom_source") == 1
    assert _count(store, "bos_warroom_evidence") == len(first.evidence_ids)
    assert _count(store, "bos_warroom_experiment") == 3


def test_real_provider_is_typed_not_configured_and_creates_nothing(
        tmp_path, no_network):
    store = _store(tmp_path)
    tenant, business = _scope(store)

    with pytest.raises(BusinessError) as excinfo:
        bootstrap_etsy_pilot(
            store, tenant_id=tenant.tenant_id,
            business_id=business.business_id, provider="remote")
    assert excinfo.value.code == RESEARCH_NOT_CONFIGURED_CODE
    assert _count(store, "bos_warroom_session") == 0


def test_brief_records_plan_and_risks_without_creating_work(
        tmp_path, no_network):
    store = _store(tmp_path)
    tenant, business = _scope(store)
    result = bootstrap_etsy_pilot(
        store, tenant_id=tenant.tenant_id, business_id=business.business_id)

    session = warroom.get_session(store, tenant.tenant_id,
                                  result.session.session_id)
    assert session.brief["risks"]
    assert session.brief["plan_30_60_90"]
    assert session.brief["tasks"]
    assert session.score["total"] == pytest.approx(4.8)
    assert _count(store, "bos_task") == 0
